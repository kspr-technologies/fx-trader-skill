# group: yahoo client, cache, bars, rolls, broker prices

# ---- yahoo client (part 02 section 1)

UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"
YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
PACE_SECONDS = 2.0     # at least this long between any two Yahoo requests (both hosts)
TIMEOUT = 25           # socket timeout in seconds
SPARK_MAX = 20         # spark takes at most 20 symbols (21 gives HTTP 400)
LOOKBACK = {"5m": 60, "1h": 730, "1d": 3650}           # days Yahoo keeps (1d: we cap at 10 years)
FIRST_RANGE = {"5m": "60d", "1h": "730d", "1d": "2y"}  # the range of a first fill
MIN_REFRESH_S = {"5m": 60, "1h": 600, "1d": 21600}     # skip the network if the newest fetch is younger
STEP_S = {"5m": 300, "1h": 3600, "1d": 86400}          # bar length in seconds
SLEEP = time.sleep     # tests replace it
RETRY_WAIT_S = {"broken": 2, "busy": 5, "down": 3}     # wait before the one retry on the other host
_last_request = None   # time.monotonic() of the last Yahoo request of this run
_last_host = "query1"  # the host of the last answer (for the fetch record)


class NoData(ValueError):
    """Yahoo has no data for that symbol (a 404, or an answer with no result)."""


def chart_url(host, symbol, interval, range_=None, period1=None, period2=None):
    """The v8 chart URL for one symbol: a range, or a period1 and period2 (Unix seconds)."""
    if interval not in ("5m", "1h", "1d"):
        raise ValueError("Interval %s is not used by this pack" % interval)
    by_range = range_ is not None
    by_period = period1 is not None and period2 is not None
    if by_range == by_period:
        raise ValueError("chart_url needs a range or a period1 and period2")
    q = "interval=%s&" % interval
    if by_range:
        q += "range=%s" % range_
    else:
        q += "period1=%d&period2=%d" % (int(period1), int(period2))
    return host + "/v8/finance/chart/" + urllib.parse.quote(symbol, safe="") + "?" + q + "&includePrePost=false"


def _url_symbol(path):
    """The symbol a Yahoo URL asks for (the chart path, or the spark symbols list)."""
    parts = urllib.parse.urlsplit(path)
    if "/v8/finance/chart/" in parts.path:
        return urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    q = urllib.parse.parse_qs(parts.query)
    return ",".join(q.get("symbols", [""]))


def _url_interval(path):
    """The interval a Yahoo URL asks for, or "" when it has none."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    return q.get("interval", [""])[0]


def is_contract_symbol(symbol):
    """True for a single futures contract like GCZ26.CMX or CLF27.NYM (not GC=F)."""
    return re.match(r"^[A-Z]{1,3}[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM)$", str(symbol)) is not None


def _yahoo_error(status, symbol, interval, description=""):
    """The plain error for a Yahoo HTTP status (part 02 1.3), or None for 200."""
    if status == 404:
        if is_contract_symbol(symbol):
            return NoData("Yahoo has no data for %s. Old contracts vanish from Yahoo." % symbol)
        return NoData("Yahoo has no data for %s. Check the yahoo symbol in your config." % symbol)
    if status == 422:
        return ValueError("Yahoo keeps 5-minute bars for 60 days and 1-hour bars for 730 days. "
                          "This asked for older data (%s, %s)." % (symbol, interval))
    if status == 401:
        return ValueError("Yahoo refused this kind of request.")
    if status == 429:
        return ValueError("Yahoo is busy (too many requests). Wait a few minutes, then try again. Or use --offline.")
    if status == "broken":
        return ValueError("Yahoo sent a broken answer for %s. Try again in a minute." % symbol)
    if status is None or 500 <= status <= 599:
        return ValueError("Yahoo did not answer for %s. Try again later, or use --offline." % symbol)
    if status == 200:
        return None
    return ValueError("Yahoo refused the request for %s: %s" % (symbol, description or "HTTP %s" % status))


def _error_description(body):
    """chart.error.description (or spark.error.description) from an error body, or ""."""
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
        for key in ("chart", "spark", "finance"):
            err = (doc.get(key) or {}).get("error") or {}
            if err.get("description"):
                return str(err["description"])
    except Exception:
        pass
    return ""


def _pace():
    """Wait until PACE_SECONDS have passed since the last Yahoo request, then mark now."""
    global _last_request
    if _last_request is not None:
        wait = PACE_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            SLEEP(wait)
    _last_request = time.monotonic()


def _gunzip(body, headers):
    """body unzipped when the headers say Content-Encoding gzip. A gzip body that was
    cut short gives b"" (so the caller sees a broken answer, not no answer)."""
    if ((headers.get("Content-Encoding") if headers else "") or "").lower() != "gzip":
        return body
    try:
        return gzip.decompress(body)
    except Exception:
        return b""


def _one_get(url, timeout):
    """One raw GET: (status or None when nothing came back, body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw, headers = getattr(resp, "status", 200), resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() or b""
        except Exception:
            body = b""
        return exc.code, _gunzip(body, exc.headers)
    except Exception:
        return None, b""
    return status, _gunzip(raw, headers)


def yahoo_get(path, timeout=TIMEOUT):
    """One paced GET on query1 with the retry rules of part 02 1.3 (one retry on query2
    for a broken answer, a 429, a 5xx or no answer); returns the body or raises."""
    global _last_host
    symbol, interval = _url_symbol(path), _url_interval(path)
    url = path
    for attempt in (0, 1):
        _pace()
        status, body = _one_get(url, timeout)
        _last_host = "query2" if url.startswith(YAHOO_HOSTS[1]) else "query1"
        if status == 200:
            try:
                json.loads(body.decode("utf-8"))
                return body
            except Exception:
                kind = "broken"
        elif status == 429:
            kind = "busy"
        elif status is None or 500 <= status <= 599:
            kind = "down"
        else:
            raise _yahoo_error(status, symbol, interval, _error_description(body))
        if attempt == 1:
            raise _yahoo_error(None if kind == "down" else (429 if kind == "busy" else "broken"),
                               symbol, interval)
        SLEEP(RETRY_WAIT_S[kind])
        if url.startswith(YAHOO_HOSTS[0]):
            url = YAHOO_HOSTS[1] + url[len(YAHOO_HOSTS[0]):]


_ERROR_CODES = {"Not Found": 404, "Unprocessable Entity": 422, "Bad Request": 400,
                "Unauthorized": 401, "Too Many Requests": 429}


def fetch_chart(symbol, interval, range_=None, period1=None, period2=None):
    """GET one chart answer and return chart.result[0] (meta, maybe timestamp, indicators).
    An error in the body raises the part 02 1.3 message; no result raises NoData."""
    body = yahoo_get(chart_url(YAHOO_HOSTS[0], symbol, interval, range_, period1, period2))
    doc = json.loads(body.decode("utf-8"))
    chart = doc.get("chart") or {}
    err = chart.get("error")
    if err:
        code = _ERROR_CODES.get(str(err.get("code")), 400)
        raise _yahoo_error(code, symbol, interval, str(err.get("description") or ""))
    result = chart.get("result")
    if not result:
        raise NoData("Yahoo sent no data for %s." % symbol)
    return result[0]


def parse_chart(result, symbol=""):
    """A chart result -> (meta, rows); rows are [t, o, h, l, c, v], raw, nulls kept.
    The quote lists are read by name; a missing list counts as all nulls."""
    meta = result.get("meta") or {}
    ts = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = []
    for name in ("open", "high", "low", "close", "volume"):
        col = q.get(name)
        if col is None:
            col = [None] * len(ts)
        if len(col) != len(ts):
            raise ValueError("Yahoo sent a broken chart for %s (lists of different length)."
                             % (symbol or meta.get("symbol") or "?"))
        cols.append(col)
    rows = [[ts[i]] + [c[i] for c in cols] for i in range(len(ts))]
    return meta, rows


def spark(symbols):
    """Quick closes for a printout, never for levels: {asked symbol: {close, at, short_name}},
    in calls of at most SPARK_MAX symbols; unknown symbols are just missing."""
    out = {}
    symbols = list(symbols)
    for i in range(0, len(symbols), SPARK_MAX):
        part = symbols[i:i + SPARK_MAX]
        url = (YAHOO_HOSTS[0] + "/v7/finance/spark?symbols="
               + ",".join(urllib.parse.quote(s, safe="") for s in part) + "&range=1d&interval=5m")
        doc = json.loads(yahoo_get(url).decode("utf-8"))
        for item in ((doc.get("spark") or {}).get("result") or []):
            sym = item.get("symbol")
            resp = (item.get("response") or [None])[0]
            if sym not in part or not resp:
                continue
            meta, rows = parse_chart(resp, sym)
            closes = [r[4] for r in rows if r[4] is not None]
            at = meta.get("regularMarketTime")
            out[sym] = {"close": closes[-1] if closes else meta.get("regularMarketPrice"),
                        "at": ts_to_utc(at) if at is not None else None,
                        "short_name": meta.get("shortName")}
    return out


# ---- cache (part 02 section 2)

FETCH_LOG_MAX = 400    # fetch records kept per cache file (newest last)


def cache_file(data_dir, symbol, interval):
    """The cache path data_dir/yahoo/<file>/<interval>.json; <file> is the symbol with
    "=" and "." turned into "_" (GC=F -> GC_F). A strange symbol is refused."""
    if not re.match(r"^[A-Za-z0-9=.^-]+$", str(symbol)):
        raise ValueError("Bad Yahoo symbol: %s" % symbol)
    name = str(symbol).replace("=", "_").replace(".", "_")
    return os.path.join(data_dir, "yahoo", name, "%s.json" % interval)


def load_cache(data_dir, symbol, interval):
    """The cache doc of one symbol and interval; an empty doc when the file is missing."""
    path = cache_file(data_dir, symbol, interval)
    empty = {"symbol": symbol, "interval": interval, "bars": [], "fetches": []}
    if not os.path.exists(path):
        return empty
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("bars"), list):
            raise ValueError(path)
    except (OSError, ValueError):
        raise ValueError("The price cache %s is broken. Delete it and run again with the network on." % path)
    doc.setdefault("fetches", [])
    return doc


def save_cache(path, doc):
    """Write a cache doc as compact JSON (path + ".tmp", then one move), making the folder."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ValueError("could not save %s: %s" % (path, exc.strerror or exc))


def last_fetch(doc):
    """The newest fetch record of a cache doc, or None."""
    fetches = (doc or {}).get("fetches") or []
    return fetches[-1] if fetches else None


def merge_rows(old, new, window, now, interval):
    """Old rows inside the new answer's window (first_t, last_t) go; the new rows come in
    (a new row wins on the same t); sorted by t; rows older than LOOKBACK + 30 days are cut."""
    keep = list(old)
    if window and window[0] is not None and window[1] is not None:
        keep = [r for r in keep if not (window[0] <= r[0] <= window[1])]
    by_t = {}
    for r in keep:
        by_t[r[0]] = list(r)
    for r in new:
        by_t[r[0]] = list(r)
    cut = utc_to_ts(now) - (LOOKBACK[interval] + 30) * 86400
    return [by_t[t] for t in sorted(by_t) if t > cut]


def stored_rows(rows, market_time):
    """The raw rows a cache stores: a non-null close, and not the live tick (the last row
    at market_time, or any row off the whole minute)."""
    out = []
    for i, r in enumerate(rows):
        if r[4] is None or r[0] % 60 != 0:
            continue
        if i == len(rows) - 1 and market_time is not None and r[0] == market_time:
            continue
        out.append(list(r))
    return out


def refresh(data_dir, symbol, interval, now, cfg, offline):
    """Bring one cache file up to date: no network when offline or when the newest fetch
    is younger than MIN_REFRESH_S; else a first fill (a range) or a small incremental
    fetch. Records the fetch and saves. A client ValueError is raised to the caller."""
    doc = load_cache(data_dir, symbol, interval)
    if offline:
        return doc
    last = last_fetch(doc)
    at = parse_utc((last or {}).get("at"))
    if at is not None and (now - at).total_seconds() < MIN_REFRESH_S[interval]:
        return doc
    step = STEP_S[interval]
    bars = doc["bars"]
    range_ = period1 = period2 = None
    old_edge = utc_to_ts(now) - (LOOKBACK[interval] - 5) * 86400
    if not bars or bars[-1][0] < old_edge:
        range_ = FIRST_RANGE[interval]
    else:
        period1 = bars[-1][0] - 2 * step
        period2 = utc_to_ts(now) + 60
    return fetch_into(data_dir, doc, symbol, interval, now, range_, period1, period2)


def fetch_into(data_dir, doc, symbol, interval, now, range_=None, period1=None, period2=None):
    """One chart fetch merged into a cache doc (live tick dropped, fetch recorded), saved."""
    result = fetch_chart(symbol, interval, range_=range_, period1=period1, period2=period2)
    meta, rows = parse_chart(result, symbol)
    kept = stored_rows(rows, meta.get("regularMarketTime"))
    window = (kept[0][0], kept[-1][0]) if kept else None
    doc["bars"] = merge_rows(doc["bars"], kept, window, now, interval)
    doc["fetches"].append({
        "at": utc_iso(now), "host": _last_host, "range": range_, "period1": period1,
        "period2": period2, "first": kept[0][0] if kept else None,
        "last": kept[-1][0] if kept else None, "rows": len(kept),
        "market_time": meta.get("regularMarketTime"), "price": meta.get("regularMarketPrice"),
        "short_name": meta.get("shortName"), "currency": meta.get("currency"),
        "tz": meta.get("exchangeTimezoneName"), "gmtoffset": meta.get("gmtoffset"),
        "previous_close": meta.get("previousClose")})
    doc["fetches"] = doc["fetches"][-FETCH_LOG_MAX:]
    save_cache(cache_file(data_dir, symbol, interval), doc)
    return doc


# ---- bars (part 02 sections 2.4, 2.5, 3, 4, 6 and 7)

DONE_MARGIN_MIN = 15          # a span's last bar is done this long after the close
OUTAGE_MIN_SLOTS = {"5m": 6, "1h": 3, "1d": 1}   # an outage misses this many steps of open time
COARSE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}
COARSE_MIN_STOP_PIPS = 10
COARSE_MARGIN_PIPS = 2
STALE_MINUTES = {"spot_fx": 15, "futures": 30}
YAHOO_DECIMALS = {"GC": 1, "SI": 3, "PL": 1, "PA": 1, "HG": 4, "CL": 2, "BZ": 2, "NG": 3}
FUTURES_KIND = {"GC": "metal", "SI": "metal", "PL": "metal", "PA": "metal", "HG": "metal",
                "CL": "energy", "BZ": "energy", "NG": "energy"}


def symbol_root(symbol):
    """The futures root of a Yahoo symbol (GC=F and GCZ26.CMX -> "GC"), else None."""
    m = re.match(r"^([A-Z]{1,3})=F$", str(symbol)) or \
        re.match(r"^([A-Z]{1,3})[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM)$", str(symbol))
    return m.group(1) if m else None


def yahoo_inst(symbol):
    """A small stand-in instrument row made from a Yahoo symbol alone (data hours, root,
    kind, quote, yahoo_to_broker 1.0), for callers that have no config row."""
    root = symbol_root(symbol)
    if root:
        return {"key": symbol, "yahoo": symbol, "kind": FUTURES_KIND.get(root, "energy"),
                "price_source": "futures", "hours": "comex", "data_hours": "cme",
                "quote": "USD", "digits": YAHOO_DECIMALS.get(root, 2), "yahoo_to_broker": 1.0,
                "futures": {"root": root}}
    pair = str(symbol).split("=")[0]
    return {"key": pair, "yahoo": symbol, "kind": "fx", "price_source": "spot_fx", "hours": "fx",
            "data_hours": "yahoo_fx", "base": pair[:3], "quote": pair[3:6],
            "digits": 3 if pair[3:6] == "JPY" else 5, "yahoo_to_broker": 1.0, "futures": None}


def inst_root(inst):
    """The futures root of an instrument row (inst["futures"]["root"]), else None."""
    fut = inst.get("futures") or {}
    return fut.get("root") or (symbol_root(inst.get("yahoo")) if inst.get("price_source") == "futures" else None)


def yahoo_decimals(inst):
    """The decimals Yahoo sends for the instrument's symbol: FX quoted in JPY 3, other
    FX 5; GC 1, SI 3, PL 1, PA 1, HG 4, CL 2, BZ 2, NG 3 (else the row's digits)."""
    root = inst_root(inst)
    if root in YAHOO_DECIMALS:
        return YAHOO_DECIMALS[root]
    if inst.get("kind") == "fx":
        return 3 if inst.get("quote") == "JPY" else 5
    return int(inst.get("digits", 5))


def _round_dec(x, places):
    """A float rounded half up to places decimals (through its shortest text)."""
    return float(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def clean_rows(rows, meta, interval, inst):
    """Raw rows -> bars {start, open, high, low, close, volume}: drops the live tick, rows
    with a null price and (FX and metals) prices of 0 or less; rounds to yahoo_decimals;
    fixes high and low; one bar per start, sorted. meta has regularMarketTime or market_time."""
    meta = meta or {}
    mt = meta.get("regularMarketTime", meta.get("market_time"))
    places = yahoo_decimals(inst)
    by_start = {}
    for i, r in enumerate(rows):
        t = r[0]
        if t % 60 != 0 or (i == len(rows) - 1 and mt is not None and t == mt):
            continue
        if any(x is None for x in r[1:5]):
            continue
        o, h, l, c = [_round_dec(x, places) for x in r[1:5]]
        if inst.get("kind") in ("fx", "metal") and min(o, h, l, c) <= 0:
            continue
        by_start[t] = {"start": ts_to_utc(t), "open": o, "high": max(h, o, c), "low": min(l, o, c),
                       "close": c, "volume": 0 if inst.get("kind") == "fx" else (r[5] or 0)}
    return [by_start[t] for t in sorted(by_start)]


def _span_of(template, utc, cfg, tables=True):
    """The open span (open_utc, close_utc) of a template that holds utc, or None."""
    d = fx_day(utc, "17:00 New York")
    if d is None:
        return None
    for a, b in open_spans(template, d, cfg, tables=tables):
        if a <= utc < b:
            return (a, b)
    return None


def bar_done(start_utc, interval, market_time_utc, now, inst, cfg):
    """True when the bar is over: the data clock passed its end, or it is the last bar
    of its open span (break, weekend, holiday, early close) and now is at least
    DONE_MARGIN_MIN minutes after that close."""
    step = datetime.timedelta(seconds=STEP_S[interval])
    end = start_utc + step
    if market_time_utc is not None and end <= market_time_utc:
        return True
    if now is None or end > now:
        return False
    span = _span_of(inst["data_hours"], start_utc, cfg)
    if span is None:
        return False
    close = span[1]
    return end > close - step and now >= close + datetime.timedelta(minutes=DONE_MARGIN_MIN)


def load_bars(data_dir, symbol, interval, now, rolls=None, include_forming=False, inst=None, cfg=None):
    """Cleaned bars of one cache file: stray bars outside the data hours dropped,
    back-adjusted for rolls (futures), on the plan scale (x yahoo_to_broker), and only
    bars done by now (include_forming adds the forming one). inst (the config row, for
    yahoo_to_broker) and cfg (holidays, early closes) are required."""
    if inst is None or cfg is None:
        raise ValueError("load_bars needs inst= and cfg=")
    doc = load_cache(data_dir, symbol, interval)
    if not doc["bars"]:
        return []
    fetch = last_fetch(doc) or {}
    mt = fetch.get("market_time")
    mt = ts_to_utc(mt) if mt is not None else None
    bars = clean_rows(doc["bars"], {"market_time": fetch.get("market_time")}, interval, inst)
    bars = [b for b in bars if _span_of(inst["data_hours"], b["start"], cfg, tables=False)]
    root = inst_root(inst)
    if root and rolls and str(symbol).endswith("=F"):
        mine = [r for r in rolls if parse_utc(r.get("found_at")) is None or parse_utc(r["found_at"]) <= now]
        bars = adjust_rows(bars, mine, root, interval)
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    if u != 1:
        q = Decimal(1).scaleb(-int(inst["digits"]))
        for b in bars:
            for k in ("open", "high", "low", "close"):
                b[k] = float((_dec_clean(b[k]) * u).quantize(q, rounding=ROUND_HALF_UP))
    at = parse_utc(fetch.get("at"))
    wall = now if at is None else min(at, now)
    step = datetime.timedelta(seconds=STEP_S[interval])
    out = []
    for b in bars:
        if b["start"] + step <= now and bar_done(b["start"], interval, mt, wall, inst, cfg):
            out.append(b)
        elif include_forming and b["start"] <= now:
            b["forming"] = True
            out.append(b)
    return out


def _overlap(a0, a1, b0, b1):
    """The length of the overlap of [a0, a1) and [b0, b1) as a timedelta (0 or more)."""
    return max(datetime.timedelta(0), min(a1, b1) - max(a0, b0))


def _touched_fx_days(start, end):
    """The 17:00-New-York FX days (date strings) whose bounds overlap [start, end)."""
    out = []
    d = to_ny(start).date() - datetime.timedelta(days=1)
    last = to_ny(end).date() + datetime.timedelta(days=1)
    while d <= last:
        if d.weekday() < 5:
            a, b = fx_day_bounds(d, "17:00 New York")
            if a < end and b > start:
                out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def open_time_in(template, start, end, cfg):
    """How much open time a template has inside [start, end), as a timedelta: the overlap
    with open_spans of each FX day it touches (tables used)."""
    total = datetime.timedelta(0)
    for e in _touched_fx_days(start, end):
        for a, b in open_spans(template, e, cfg):
            total += _overlap(start, end, a, b)
    return total


def find_outages(bars, inst, interval, cfg):
    """The holes between done bars that miss at least OUTAGE_MIN_SLOTS steps of open data
    time, as (from_utc, to_utc); a hole touching a Yahoo no-data day is never one."""
    step = datetime.timedelta(seconds=STEP_S[interval])
    need = step * OUTAGE_MIN_SLOTS[interval]
    template = inst["data_hours"]
    out = []
    for prev, nxt in zip(bars, bars[1:]):
        h0, h1 = prev["start"] + step, nxt["start"]
        if h1 - h0 < need:
            continue
        if hours_template(template)["group"] == "cme":
            cuts = [c for e in _touched_fx_days(h0, h1) for c in yahoo_no_data_cuts(e)]
            if any(a < h1 and b > h0 for a, b in cuts):
                continue
        if open_time_in(template, h0, h1, cfg) >= need:
            out.append((h0, h1))
    return out


def _daily_flags(day, bars, inst, cfg, outages):
    """The flags of one daily bar (part 02 4.2): partial, holiday, early_close, outage,
    roll, late_start."""
    start, end = day["start"], day["end"]
    expected = open_time_in(inst["data_hours"], start, end, cfg).total_seconds() / 3600.0
    flags = []
    if expected == 0:
        flags.append("holiday")
    elif day["hours"] < 0.75 * expected:
        flags.append("partial")
    group = hours_template(inst["data_hours"])["group"]
    touched = _touched_fx_days(start, end)
    if any(early_close_utc(e, group, cfg) is not None for e in touched):
        flags.append("early_close")
    if any(a < end and b > start for a, b in outages):
        flags.append("outage")
    if any("roll" in (b.get("flags") or []) for b in bars):
        flags.append("roll")
    opens = [max(a, start) for e in touched for a, b in open_spans(inst["hours"], e, cfg)
             if a < end and b > start]
    if opens and bars[0]["start"] > min(opens) + datetime.timedelta(minutes=60):
        flags.append("late_start")
    return flags


def daily_from_hourly(bars_1h, day_end, inst, cfg, include_forming=False, now=None):
    """Daily bars {fx_day, start, end, open, high, low, close, hours, flags} from done 1h
    bars, cut at day_end. The newest FX day is left out while it is forming (it holds now,
    or, with no now, its last bar is a forming bar or ends before its last data close)."""
    check_day_end(day_end)
    groups = {}
    for b in bars_1h:
        d = fx_day(b["start"], day_end)
        if d is not None:
            groups.setdefault(d, []).append(b)
    outages = find_outages([b for b in bars_1h if not b.get("forming")], inst, "1h", cfg)
    out = []
    for d in sorted(groups):
        bars = sorted(groups[d], key=lambda b: b["start"])
        start, end = fx_day_bounds(d, day_end)
        day = {"fx_day": d, "start": start, "end": end, "open": bars[0]["open"],
               "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars),
               "close": bars[-1]["close"], "hours": len(bars), "flags": []}
        day["flags"] = _daily_flags(day, bars, inst, cfg, outages)
        out.append(day)
    if out and _day_forming(out[-1], groups[out[-1]["fx_day"]], inst, cfg, now):
        if include_forming:
            out[-1]["flags"].append("forming")
        else:
            out.pop()
    return out


def _day_forming(day, bars, inst, cfg, now):
    """True when a daily bar is not over yet: now is before its end; with no now, its
    last 1h bar is forming or ends before the last data close inside the day."""
    if now is not None:
        return now < day["end"]
    last = max(bars, key=lambda b: b["start"])
    if last.get("forming"):
        return True
    closes = [min(b, day["end"]) for e in _touched_fx_days(day["start"], day["end"])
              for a, b in open_spans(inst["data_hours"], e, cfg) if a < day["end"] and b > day["start"]]
    return bool(closes) and last["start"] + datetime.timedelta(hours=1) < max(closes)


def data_age_min(at_utc, now):
    """How many minutes old a price is (at least 0)."""
    return max(0.0, (now - at_utc).total_seconds() / 60.0)


def _plan_bid(raw, inst):
    """A raw Yahoo price -> (plan-scale bid, Yahoo price rounded to yahoo_decimals)."""
    y = _round_dec(raw, yahoo_decimals(inst))
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    if u == 1:
        return y, y
    q = Decimal(1).scaleb(-int(inst["digits"]))
    return float((_dec_clean(y) * u).quantize(q, rounding=ROUND_HALF_UP)), y


def latest_price(data_dir, inst, now, cfg):
    """The newest Yahoo price of the instrument: {bid (plan scale), yahoo, at, age_min,
    source, contract} from the newest 5m or 1h fetch; in a replay (the cache is newer than
    now) the close of the last bar done by now, at its end. None when there is no data."""
    best = None
    for interval in ("5m", "1h"):
        f = last_fetch(load_cache(data_dir, inst["yahoo"], interval))
        if f and f.get("market_time") is not None and f.get("price") is not None:
            if best is None or f["market_time"] > best[1]["market_time"]:
                best = (interval, f)
    futures = inst.get("price_source") == "futures"
    if best is not None:
        at = ts_to_utc(best[1]["market_time"])
        fetched = parse_utc(best[1].get("at"))
        if at <= now and (fetched is None or fetched <= now):
            bid, y = _plan_bid(best[1]["price"], inst)
            return {"bid": bid, "yahoo": y, "at": at, "age_min": data_age_min(at, now),
                    "source": "yahoo " + best[0], "contract": best[1].get("short_name") if futures else None}
    last = None
    for interval in ("5m", "1h"):
        bars = load_bars(data_dir, inst["yahoo"], interval, now, inst=inst, cfg=cfg)
        if bars:
            end = bars[-1]["start"] + datetime.timedelta(seconds=STEP_S[interval])
            if last is None or end > last[1]:
                last = (interval, end, bars[-1])
    if last is None:
        return None
    raw = last[2]["close"]
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    y = float(_dec_clean(raw) / u) if u != 1 else raw
    contract = None
    if futures:
        newest = None
        for interval in ("5m", "1h"):
            for f in load_cache(data_dir, inst["yahoo"], interval)["fetches"]:
                t = parse_utc(f.get("at"))
                if t is not None and t <= now and (newest is None or t > newest):
                    newest, contract = t, f.get("short_name")
    return {"bid": raw, "yahoo": _round_dec(y, yahoo_decimals(inst)), "at": last[1],
            "age_min": data_age_min(last[1], now), "source": "yahoo " + last[0], "contract": contract}


def coarse_note(inst, stop_points):
    """The warning for a tight stop on a pair with rough Yahoo data (COARSE), else None."""
    if inst.get("key") not in COARSE:
        return None
    pip = _dec_clean(inst.get("pip") or 0.0001)
    point = _dec_clean(inst.get("point") or Decimal(1).scaleb(-int(inst.get("digits", 5))))
    if abs(_dec_clean(stop_points)) * point < COARSE_MIN_STOP_PIPS * pip:
        return ("Yahoo prices for %s are rough (about one price a minute). A stop under %d pips "
                "can be hit without Yahoo showing it." % (inst["key"], COARSE_MIN_STOP_PIPS))
    return None


# ---- rolls (part 02 section 5)

MONTH_CODES = {"Jan": "F", "Feb": "G", "Mar": "H", "Apr": "J", "May": "K", "Jun": "M",
               "Jul": "N", "Aug": "Q", "Sep": "U", "Oct": "V", "Nov": "X", "Dec": "Z"}
ROLL_MATCH_BARS = 3          # BZ: the old contract still matches on the newest 3 shared bars
ROLL_CHECK_HOURS = 6         # BZ: the match test at most once every 6 hours (not every_run)
ROLL_SAME_DAYS = 3           # a roll of the root within 3 days is the same roll


def rolls_path(data_dir):
    """The path of data/rolls.json."""
    return os.path.join(data_dir, "rolls.json")


def load_rolls_doc(data_dir):
    """The whole rolls.json doc {rolls, checked, updated_at}; an empty one when missing."""
    doc = load_json(rolls_path(data_dir), None) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("rolls", []), list):
        raise ValueError("%s is broken. Delete it: the rolls are found again." % rolls_path(data_dir))
    doc.setdefault("rolls", [])
    doc.setdefault("checked", {})
    return doc


def load_rolls(data_dir):
    """The list of rolls in rolls.json, sorted by first_bar_new ([] when missing)."""
    return sorted(load_rolls_doc(data_dir)["rolls"], key=lambda r: r.get("first_bar_new") or "")


def save_rolls(data_dir, rolls, checked=None, now=None):
    """Write rolls.json: the rolls sorted by first_bar_new; checked kept from the file
    unless given; updated_at = now (the real clock when None)."""
    doc = load_rolls_doc(data_dir)
    if checked is None:
        checked = doc.get("checked") or {}
    out = {"updated_at": utc_iso(now or utc_now()),
           "rolls": sorted(rolls, key=lambda r: r.get("first_bar_new") or ""), "checked": checked}
    save_json_atomic(rolls_path(data_dir), out)


def contract_from_name(short_name, root, suffix):
    """The single contract named by a Yahoo shortName: "Gold Dec 26" -> "GCZ26.CMX";
    None when the name has no month (BZ=F) or is null."""
    if not short_name:
        return None
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\s*$", str(short_name))
    if not m:
        return None
    return "%s%s%s%s" % (root, MONTH_CODES[m.group(1)], m.group(2), suffix)


def cme_business_day(d):
    """True for Monday to Friday when HOLIDAYS does not shut the CME all day."""
    return d.weekday() < 5 and "cme" not in HOLIDAYS.get(d.isoformat(), {}).get("closed", [])


def add_business_days(d, n):
    """The CME business day n business days after d (n below 0: before it)."""
    step = datetime.timedelta(days=1 if n >= 0 else -1)
    left = abs(n)
    while left:
        d += step
        if cme_business_day(d):
            left -= 1
    return d


def _month_add(year, month, n):
    """(year, month) moved by n months."""
    k = year * 12 + (month - 1) + n
    return k // 12, k % 12 + 1


def _last_business_day(year, month):
    """The last CME business day of a month."""
    y, m = _month_add(year, month, 1)
    d = datetime.date(y, m, 1) - datetime.timedelta(days=1)
    while not cme_business_day(d):
        d -= datetime.timedelta(days=1)
    return d


def roll_dates(rule, year, month):
    """For the contract that delivers in (year, month): (window_start, window_end, Y) as
    dates, by its roll rule (part 02 5.2). Y is the day Yahoo's daily series switches."""
    py, pm = _month_add(year, month, -1)
    if rule in ("gc_fnd", "si_hg"):
        y_day = _last_business_day(py, pm)
        return add_business_days(y_day, -10), add_business_days(y_day, 1), y_day
    if rule == "cl_ltd":
        d25 = datetime.date(py, pm, 25)
        last = add_business_days(d25, -3 if cme_business_day(d25) else -4)
        back = 8
    elif rule == "bz_ltd":
        last = _last_business_day(*_month_add(year, month, -2))
        back = 10
    elif rule == "ng_ltd":
        last = add_business_days(datetime.date(year, month, 1), -3)
        back = 8
    else:
        raise ValueError('Unknown roll_rule "%s". Use gc_fnd, si_hg, cl_ltd, bz_ltd or ng_ltd.' % rule)
    return add_business_days(last, -back), add_business_days(last, 1), add_business_days(last, 1)


def _cycle_contracts(inst, first_year, last_year):
    """[(year, month, symbol)] of the cycle contracts in those years, in order."""
    fut = inst["futures"]
    code_month = dict((c, i + 1) for i, c in enumerate("FGHJKMNQUVXZ"))
    months = sorted(code_month[c] for c in fut["cycle"])
    out = []
    for y in range(first_year, last_year + 1):
        for m in months:
            out.append((y, m, "%s%s%02d%s" % (fut["root"], "FGHJKMNQUVXZ"[m - 1], y % 100, fut["suffix"])))
    return out


def _windows(inst, first_year, last_year):
    """[(old, new, window_start_utc, window_end_utc, Y date)] for each cycle contract."""
    cs = _cycle_contracts(inst, first_year, last_year + 1)
    out = []
    for (y, m, old), (_, _, new) in zip(cs, cs[1:]):
        a, b, y_day = roll_dates(inst["futures"]["roll_rule"], y, m)
        out.append((old, new, ny_to_utc(at_time(a - datetime.timedelta(days=1), "17:00")),
                    ny_to_utc(at_time(b, "17:00")), y_day))
    return out


def expected_front(root, utc, inst):
    """The cycle contract the calendar rule expects as Yahoo's front at utc: the one whose
    search window END is the first one after utc ("GCZ26.CMX" on 2026-11-11)."""
    for old, new, a, b, y_day in _windows(inst, utc.year - 1, utc.year + 1):
        if b > utc:
            return old
    raise ValueError("No %s contract found after %s." % (root, utc_iso(utc)))


def expected_rolls(root, inst, from_utc, to_utc):
    """Each expected roll (old, new, window_start, window_end, Y) whose search window
    overlaps [from_utc, to_utc]. Y is the date the daily series switches."""
    return [w for w in _windows(inst, from_utc.year - 1, to_utc.year + 1)
            if w[2] <= to_utc and w[3] >= from_utc]


def _roll_bar(row, places):
    """A raw [t, o, h, l, c, v] row -> a bar dict rounded to places decimals."""
    o, h, l, c = [_round_dec(x, places) for x in row[1:5]]
    return {"start": ts_to_utc(row[0]), "open": o, "high": max(h, o, c), "low": min(l, o, c),
            "close": c, "volume": row[5] or 0}


def adjust_rows(rows, rolls, root, interval="1h"):
    """Back-adjusted copies of cleaned bars (dicts): each bar times the ratios of the rolls
    of root that start later. The 1h bar at a roll is its replacement, or keeps the flag
    "roll"; 5m bars in that hour get the flag. The cache is never edited."""
    places = YAHOO_DECIMALS.get(root, 2)
    mine = []
    for r in rolls or []:
        if r.get("root") == root and parse_utc(r.get("first_bar_new")) is not None:
            mine.append((parse_utc(r["first_bar_new"]), r))
    out = []
    for bar in rows:
        b = dict(bar)
        start = b["start"]
        hit = None
        for at, r in mine:
            if (interval == "1h" and at == start) or (interval == "5m" and at <= start < at + datetime.timedelta(hours=1)):
                hit = r
        if hit is not None and interval == "1h" and (hit.get("replacement") or {}).get("1h"):
            b = _roll_bar(hit["replacement"]["1h"], places)
        elif hit is not None and interval in ("1h", "5m"):
            b["flags"] = sorted(set((b.get("flags") or []) + ["roll"]))
        factor = Decimal(1)
        for at, r in mine:
            if at > start:
                factor *= Decimal(repr(float(r["ratio"])))
        if factor != 1:
            q = Decimal(1).scaleb(-places)
            for k in ("open", "high", "low", "close"):
                b[k] = float((Decimal(repr(float(b[k]))) * factor).quantize(q, rounding=ROUND_HALF_UP))
        out.append(b)
    return out


ROLL_AWAY = {"stop_loss": "entry", "take_profit": "entry", "stop_at_open": "entry", "chart_stop": "chart_entry"}


def _get_path(item, path):
    """item["a"]["b"] for the path "a.b", or None."""
    cur = item
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(item, path, value):
    """Set item["a"]["b"] = value for the path "a.b" (the parents must exist)."""
    parts = path.split(".")
    cur = item
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value


def rebase(item, rolls, fields, inst=None):
    """Move an open item's plan-scale fields (dotted paths) by each roll it has not had:
    by ratio for a new roll id, by ratio / ratio_applied for a changed one. inst is the
    item's instrument row: only the rolls of its root count (all of load_rolls may be
    given), and the moved levels are rounded to its tick. Returns (new item, ids that
    changed something)."""
    if inst is None:
        raise ValueError("rebase needs the instrument row")
    new = copy.deepcopy(item)
    applied = dict(new.get("rolls_applied") or {})
    root = inst_root(inst)
    if not root:
        new["rolls_applied"] = applied
        return new, []
    mine = [r for r in (rolls or []) if r.get("root") == root]
    mult = Decimal(1)
    ids = []
    for r in sorted(mine, key=lambda r: r.get("first_bar_new") or ""):
        ratio = float(r["ratio"])
        done = applied.get(r["id"])
        if done is None:
            mult *= Decimal(repr(ratio))
        elif float(done) != ratio:
            mult *= Decimal(repr(ratio)) / Decimal(repr(float(done)))
        else:
            continue
        applied[r["id"]] = ratio
        ids.append(r["id"])
    new["rolls_applied"] = applied
    if not ids:
        return new, ids
    plain = [f for f in fields if f.split(".")[-1] not in ROLL_AWAY]
    away = [f for f in fields if f.split(".")[-1] in ROLL_AWAY]
    for f in plain + away:
        v = _get_path(new, f)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        x = _dec_clean(v) * mult
        last = f.split(".")[-1]
        entry_path = ".".join(f.split(".")[:-1] + [ROLL_AWAY[last]]) if last in ROLL_AWAY else None
        entry = _get_path(new, entry_path) if entry_path else None
        if entry is not None and float(x) != float(entry):
            _set_path(new, f, round_away(float(x), entry, inst))
        else:
            _set_path(new, f, round_price(float(x), inst))
    return new, ids


ROLL_WORDS = {"GC": "gold", "SI": "silver", "PL": "platinum", "PA": "palladium", "HG": "copper",
              "CL": "oil", "BZ": "Brent", "NG": "gas"}


def roll_tolerance(inst):
    """Two Yahoo closes match when they differ by at most half a Yahoo step (GC: 0.05)."""
    return 0.5 * 10 ** -yahoo_decimals(inst) + 1e-9


def _contract_bars(data_dir, symbol, inst, now, w0, w1):
    """The 1h bars of a single contract in [w0, w1], fetched into its own cache file;
    None when Yahoo has no data for it (a 404, or no bars in the window)."""
    doc = load_cache(data_dir, symbol, "1h")
    try:
        doc = fetch_into(data_dir, doc, symbol, "1h", now, None, utc_to_ts(w0), utc_to_ts(w1) + 60)
    except NoData:
        return None
    bars = [b for b in clean_rows(doc["bars"], {}, "1h", inst) if w0 <= b["start"] <= w1]
    return bars or None


def _switch_bar(cont, old_by, new_by, tol):
    """(index of the switch bar S in cont, mixed) or None: S is the first bar on the new
    contract after the last bar on the old one (old_by None: a bar not on new stands in)."""
    def on(b, by):
        x = by.get(b["start"]) if by else None
        return x is not None and abs(b["close"] - x["close"]) <= tol
    marks = []
    for b in cont:
        is_new = on(b, new_by)
        is_old = (not is_new) if old_by is None else on(b, old_by)
        marks.append("both" if is_new and is_old else ("new" if is_new else ("old" if is_old else "")))
    olds = [i for i, m in enumerate(marks) if m == "old"]
    start = olds[-1] + 1 if olds else 0
    news = [i for i in range(start, len(cont)) if marks[i] == "new"]
    if not news:
        return None
    if olds and start < len(cont):
        b = cont[start]
        o, n = (old_by or {}).get(b["start"]), new_by.get(b["start"])
        if old_by is not None and o is not None:
            # open on old, close off old (on new, or new has no bar there): the switch is inside
            if abs(b["open"] - o["open"]) <= tol and abs(b["close"] - o["close"]) > tol \
                    and (n is None or abs(b["close"] - n["close"]) <= tol):
                return start, True
        elif old_by is None and n is not None and marks[start] == "new":
            return start, abs(b["open"] - n["open"]) > tol
    return news[0], False


def _roll_ratio(cont, s, old_by, new_by):
    """(ratio, old_price, new_price, prices_at, exact) for a switch at cont[s] (part 02
    5.5 step 5); old_by None: the continuous bars before S stand in for old."""
    at = cont[s]["start"]
    def pair(b):
        o = (old_by or {}).get(b["start"]) if old_by is not None else (b if b["start"] < at else None)
        n = new_by.get(b["start"])
        return (o["close"], n["close"]) if o is not None and n is not None else None
    before = [b for b in cont[:s] if b["start"] >= at - datetime.timedelta(hours=12) and pair(b)]
    if before:
        b = before[-1]
    else:
        after = [b for b in cont[s:] if pair(b)]
        b = after[0] if after else None
    if b is not None:
        o, n = pair(b)
        return n / o, o, n, b["start"], old_by is not None
    if s == 0:
        return None
    o, n = cont[s - 1]["close"], cont[s]["close"]
    return n / o, o, n, cont[s]["start"], False


def _newest_fetch(data_dir, symbol):
    """The newest fetch record (by "at") of the 1h and 5m caches of a symbol, or None."""
    best = None
    for interval in ("1h", "5m"):
        f = last_fetch(load_cache(data_dir, symbol, interval))
        if f and (best is None or (f.get("at") or "") > (best.get("at") or "")):
            best = f
    return best


def _name_change(data_dir, symbol):
    """The shortNames of the two newest fetches (1h and 5m caches, by "at") that have one,
    as (older, newer), when they differ. None on a first run (one name only) or no change:
    a forward roll is only a change between two of our own fetches."""
    rows = []
    for interval in ("1h", "5m"):
        for f in load_cache(data_dir, symbol, interval)["fetches"]:
            if f.get("short_name"):
                rows.append((f.get("at") or "", f["short_name"]))
    rows.sort()
    if len(rows) < 2 or rows[-2][1] == rows[-1][1]:
        return None
    return rows[-2][1], rows[-1][1]


def _bz_still_old(data_dir, inst, old, cont, now):
    """BZ: True when the old contract's 1h closes match BZ=F on the newest shared bars
    (no roll yet); False when old is gone or does not match."""
    bars = _contract_bars(data_dir, old, inst, now, now - datetime.timedelta(hours=24), now)
    if not bars:
        return False
    by = dict((b["start"], b) for b in bars)
    shared = [b for b in cont if b["start"] in by][-ROLL_MATCH_BARS:]
    tol = roll_tolerance(inst)
    return bool(shared) and all(abs(b["close"] - by[b["start"]]["close"]) <= tol for b in shared)


def check_roll_forward(data_dir, inst, cfg, now, offline, every_run=False):
    """After a refresh of a futures symbol: find a roll that just happened (a shortName
    change, or for BZ a failed price match inside a roll window), measure it with the old
    and new contracts' own bars, save it in rolls.json and return the record (else None)."""
    if offline:
        return None
    fut = inst["futures"]
    root, yahoo = fut["root"], inst["yahoo"]
    word = ROLL_WORDS.get(root, inst.get("name", root).lower())
    rdoc = load_rolls_doc(data_dir)
    mine = sorted([r for r in rdoc["rolls"] if r.get("root") == root], key=lambda r: r.get("first_bar_new") or "")
    raw = load_cache(data_dir, yahoo, "1h")["bars"]
    cont = clean_rows(raw, {}, "1h", inst)
    first = cont[0]["start"] if cont else now
    old_name = new_name = None
    if fut.get("shortname_month", True):
        pair = _name_change(data_dir, yahoo)
        if pair is None:
            return None
        old_name, new_name = pair
        old = contract_from_name(old_name, root, fut["suffix"])
        cur = contract_from_name(new_name, root, fut["suffix"])
        if old is None or cur is None or old == cur or any(r.get("new") == cur for r in mine):
            return None
        new, found_by = cur, "shortname"
    else:
        wins = [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]]
        if not wins:
            return None
        checked = rdoc["checked"].setdefault(root, {"history_from": None, "history_to": None, "last_forward": None})
        last = parse_utc(checked.get("last_forward"))
        if not every_run and last is not None and now - last < datetime.timedelta(hours=ROLL_CHECK_HOURS):
            return None
        checked["last_forward"] = utc_iso(now)
        old, new = wins[0][0], wins[0][1]
        if any(r.get("new") == new for r in mine) or _bz_still_old(data_dir, inst, old, cont, now):
            save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
            return None
        found_by = "match"
    starts = [w[2] for w in expected_rolls(root, inst, now - datetime.timedelta(days=400), now) if w[1] == new]
    w0 = starts[-1] if starts else ny_to_utc(at_time(add_business_days(to_ny(now).date(), -10), "00:00"))
    w0 = max(w0, first)
    old_bars = _contract_bars(data_dir, old, inst, now, w0, now)
    new_bars = _contract_bars(data_dir, new, inst, now, w0, now)
    if new_bars is None:
        if found_by == "match":
            save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
        raise NoData("Yahoo %s moved to a new contract, but Yahoo has no data for it. "
                     "The roll size will come from daily prices." % word)
    old_by = dict((b["start"], b) for b in old_bars) if old_bars else None
    new_by = dict((b["start"], b) for b in new_bars)
    cw = [b for b in cont if w0 <= b["start"] <= now]
    hit = _switch_bar(cw, old_by, new_by, roll_tolerance(inst))
    if hit is None:
        return None
    s, mixed = hit
    measured = _roll_ratio(cw, s, old_by, new_by)
    if measured is None:
        return None
    ratio, op, np_, at, exact = measured
    S = cw[s]
    rep = None
    if mixed:
        rep = [r for r in load_cache(data_dir, new, "1h")["bars"] if r[0] == utc_to_ts(S["start"])]
        rep = rep[0] if rep else None
    note = "" if old_bars else "The old %s contract is gone from Yahoo. The roll size is an estimate." % word
    rec = {"id": "%s-%s" % (root, S["start"].date().isoformat()), "root": root, "yahoo": yahoo,
           "old": old, "new": new, "old_name": old_name, "new_name": new_name,
           "first_bar_new": utc_iso(S["start"]), "ratio": ratio, "old_price": op, "new_price": np_,
           "prices_at": utc_iso(at), "found_by": found_by,
           "confidence": "exact" if exact else "estimated", "replaced_bar": rep is not None,
           "found_at": utc_iso(now), "note": note, "replacement": {"1h": rep}}
    if mixed and rep is None:
        rec["note"] = (rec["note"] + " The switch bar is flagged roll.").strip()
    return _keep_roll(data_dir, rdoc, rec, now)


def _keep_roll(data_dir, rdoc, rec, now):
    """Add a roll record to rolls.json: a roll of the root within 3 days is replaced only
    by an exact record over an estimated one (keeping its id). Returns the saved record or None."""
    at = parse_utc(rec["first_bar_new"])
    rolls = list(rdoc["rolls"])
    for i, r in enumerate(rolls):
        other = parse_utc(r.get("first_bar_new"))
        if r.get("root") == rec["root"] and other is not None and abs(other - at) <= datetime.timedelta(days=ROLL_SAME_DAYS):
            if rec["confidence"] == "exact" and r.get("confidence") == "estimated":
                rec = dict(rec, id=r["id"])
                rolls[i] = rec
                save_rolls(data_dir, rolls, rdoc["checked"], now)
                return rec
            return None
    rolls.append(rec)
    save_rolls(data_dir, rolls, rdoc["checked"], now)
    return rec


HISTORY_PRE_DAYS = 20        # business days before a window that give the noise
HISTORY_MIN_SIGMA = 0.0005
HISTORY_MIN_TAU = 0.0025


def _median(xs):
    """The median of a non-empty list of numbers."""
    s = sorted(xs)
    k = len(s) // 2
    return s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2.0


def _settle_utc(inst, d):
    """The settle time of the futures row on date d, as naive UTC."""
    city, hhmm = inst["futures"]["settle"]
    return city_to_utc(city, at_time(d, hhmm))


def _settle_value(by_end, inst, d, cfg):
    """s(d): the mean close of the 1h bars that end just before and just after the settle
    time (Friday: the last close before 17:00 New York); None on an early-close day or
    when a bar is missing. by_end maps each bar's end to its close."""
    if early_close_utc(d, "cme", cfg) is not None:
        return None
    hour = datetime.timedelta(hours=1)
    if d.weekday() == 4:
        return by_end.get(ny_to_utc(at_time(d, "17:00")))
    t0 = _settle_utc(inst, d).replace(minute=0, second=0, microsecond=0)
    before, after = by_end.get(t0), by_end.get(t0 + hour)
    if before is None or after is None:
        return None
    return (before + after) / 2.0


def _daily_closes(data_dir, inst, now, cfg, offline):
    """D: {New York date: Yahoo 1d close} from the continuous 1d cache (refreshed when
    online; a failed refresh keeps the cache)."""
    try:
        doc = refresh(data_dir, inst["yahoo"], "1d", now, cfg, offline)
    except ValueError:
        doc = load_cache(data_dir, inst["yahoo"], "1d")
    out = {}
    for b in clean_rows(doc["bars"], {}, "1d", inst):
        out[to_ny(b["start"]).date()] = b["close"]
    return out


def _gap_run(days, g, tau):
    """The longest run of days (in order, data days only) with abs(g) > tau, all of one
    sign: (first index, last index) into days, or None."""
    best, i = None, 0
    while i < len(days):
        if abs(g[days[i]]) > tau:
            j = i
            while j + 1 < len(days) and abs(g[days[j + 1]]) > tau and (g[days[j + 1]] > 0) == (g[days[i]] > 0):
                j += 1
            if best is None or j - i > best[1] - best[0]:
                best = (i, j)
            i = j + 1
        else:
            i += 1
    return best


def _history_switch_bar(cont, inst, first_day, m):
    """The 1h bar where the series jumped by about m, searched from the settle time of
    the business day before first_day to the settle time of first_day: (bar, mixed) or None."""
    a = _settle_utc(inst, add_business_days(first_day, -1))
    b = _settle_utc(inst, first_day)
    moves = []
    for prev, bar in zip(cont, cont[1:]):
        if a <= bar["start"] < b and prev["close"] > 0 and bar["close"] > 0:
            moves.append((math.log(bar["close"] / prev["close"]), bar))
    if not moves:
        return None
    near = min(moves, key=lambda x: abs(x[0] - m))
    if abs(near[0]) < 0.5 * abs(m):
        near = max(moves, key=lambda x: x[0] if m > 0 else -x[0])
    bar = near[1]
    mixed = bar["open"] > 0 and abs(math.log(bar["close"] / bar["open"])) >= 0.5 * abs(m)
    return bar, mixed


def _history_window(cont, by_end, D, inst, cfg, w):
    """Search one expected roll window w for a daily gap run (part 02 5.6): a roll
    record, or None for a miss."""
    a_day = to_ny(w[2]).date() + datetime.timedelta(days=1)
    b_day = to_ny(w[3]).date()
    pre_from = add_business_days(a_day, -HISTORY_PRE_DAYS)
    g = {}
    d = pre_from
    last_d = max(D) if D else b_day
    while d <= last_d:
        if cme_business_day(d) and d in D and D[d] > 0:
            s = _settle_value(by_end, inst, d, cfg)
            if s is not None and s > 0:
                g[d] = math.log(s / D[d])
        d += datetime.timedelta(days=1)
    pre = [abs(g[x]) for x in g if x < a_day]
    sigma = max(_median(pre) if pre else 0.0, HISTORY_MIN_SIGMA)
    tau = max(5 * sigma, HISTORY_MIN_TAU)
    inside = sorted(x for x in g if a_day <= x <= b_day)
    run = _gap_run(inside, g, tau)
    if run is None:
        return None
    days = inside[run[0]:run[1] + 1]
    later = sorted(x for x in g if x > days[-1])
    if not later or abs(g[later[0]]) > tau:
        return None
    y, y_prev = later[0], days[-1]
    m = _median([g[x] for x in days])
    s_y = math.exp(g[y]) * D[y]
    s_prev = math.exp(g[y_prev]) * D[y_prev]
    if abs(math.log(D[y] / D[y_prev]) - math.log(s_y / s_prev) - m) > tau:
        return None
    hit = _history_switch_bar(cont, inst, days[0], m)
    if hit is None:
        return None
    bar, mixed = hit
    root = inst["futures"]["root"]
    s0 = math.exp(g[days[0]]) * D[days[0]]
    return {"id": "%s-%s" % (root, bar["start"].date().isoformat()), "root": root, "yahoo": inst["yahoo"],
            "old": w[0], "new": w[1], "old_name": None, "new_name": None,
            "first_bar_new": utc_iso(bar["start"]), "ratio": math.exp(m), "old_price": D[days[0]],
            "new_price": s0, "prices_at": utc_iso(_settle_utc(inst, days[0])), "found_by": "daily_gap",
            "confidence": "estimated", "replaced_bar": False, "found_at": None,
            "note": "The switch bar is flagged roll." if mixed else "", "replacement": {"1h": None}}


def find_rolls_history(data_dir, inst, cfg, now, offline):
    """Search the expected roll windows of the 1h cache that rolls.json has not checked
    yet for a gap between 1h and Yahoo 1d closes (part 02 5.6). Saves and returns the
    new (estimated) roll records; a window whose Y is not in the 1d cache waits."""
    fut = inst.get("futures") or {}
    if not fut.get("history_check"):
        return []
    root = fut["root"]
    cont = clean_rows(load_cache(data_dir, inst["yahoo"], "1h")["bars"], {}, "1h", inst)
    if not cont:
        return []
    D = _daily_closes(data_dir, inst, now, cfg, offline)
    by_end = dict((b["start"] + datetime.timedelta(hours=1), b["close"]) for b in cont)
    rdoc = load_rolls_doc(data_dir)
    checked = rdoc["checked"].setdefault(root, {"history_from": None, "history_to": None, "last_forward": None})
    done_to = checked.get("history_to")
    found = []
    for w in expected_rolls(root, inst, cont[0]["start"], min(now, cont[-1]["start"])):
        if done_to and w[4].isoformat() <= done_to:
            continue
        if not D or max(D) < w[4]:
            break
        rec = _history_window(cont, by_end, D, inst, cfg, w)
        if rec is not None:
            rec["found_at"] = utc_iso(now)
            kept = _keep_roll(data_dir, rdoc, rec, now)
            rdoc = load_rolls_doc(data_dir)
            checked = rdoc["checked"].setdefault(root, checked)
            if kept is not None:
                found.append(kept)
        else:
            checked.setdefault("missed", []).append(w[4].isoformat())
        checked["history_to"] = w[4].isoformat()
        checked["history_from"] = checked.get("history_from") or cont[0]["start"].date().isoformat()
    rdoc["checked"][root] = checked
    save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
    return found


ROLL_GUARD_ATR = 3           # a 1h move above 3 x the 1h ATR inside a roll window may be a roll


def roll_guard(inst, bars_1h, rolls, now, cfg):
    """fx-alerts check: inside an expected roll window, a last 1h bar that moved more
    than 3 x the 1h ATR with no roll found in the last 24 hours may be a roll. Returns
    the line that holds a stop or target ring, else None."""
    root = inst_root(inst)
    if not root or not inst.get("futures") or len(bars_1h) < 2:
        return None
    if not [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]]:
        return None
    done = [b for b in bars_1h if not b.get("forming")]
    if len(done) < 2:
        return None
    a = atr_wilder(usable_bars(done)[-ATR_WINDOW["1h"]:], 14)
    if not a:
        return None
    last, prev = done[-1], done[-2]
    if max(abs(last["close"] - last["open"]), abs(last["open"] - prev["close"])) <= ROLL_GUARD_ATR * a:
        return None
    for r in rolls or []:
        at = parse_utc(r.get("first_bar_new"))
        if r.get("root") == root and at is not None and now - datetime.timedelta(hours=24) <= at <= now:
            return None
    return "Yahoo %s may have rolled. Check your MetaTrader price." % ROLL_WORDS.get(root, root)


# ---- broker prices (part 02 section 8)

K_LIMITS = {"fx": 0.01, "metal": 0.03, "energy": 0.20}   # how far k may be from 1, by kind
BROKER_PRICE_MINUTES = 60    # a typed broker price counts as today's this long (D23)


def broker_k(inst, broker_bid, yahoo_bid):
    """k = broker_bid / (yahoo_bid x yahoo_to_broker), not rounded; ValueError when the
    broker price is too far from Yahoo's (K_LIMITS by kind)."""
    b, y = _price_dec(broker_bid), _price_dec(yahoo_bid)
    plan = y * _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    k = float(b) / float(plan)
    limit = K_LIMITS[inst["kind"]]
    if abs(k - 1) > limit:
        q = Decimal(1).scaleb(-int(inst["digits"]))
        raise ValueError("Your broker price %s for %s is far from Yahoo's %s (more than %s%%). "
                         "Check the symbol and the price you typed."
                         % ("{:f}".format(b.normalize()), inst["key"],
                            plan.quantize(q, rounding=ROUND_HALF_UP), format(limit * 100, "g")))
    return k


def to_broker(level, k, inst):
    """A plan-scale entry or plain level on the broker scale: level x k, to the nearest tick."""
    return round_price(float(_dec_clean(level) * Decimal(repr(float(k)))), inst)


def to_broker_away(level, entry, k, inst, entry_broker=None):
    """A stop or target on the broker scale: the broker entry plus or minus the plan
    distance (entry - level), rounded away from the broker entry (D3)."""
    if entry_broker is None:
        entry_broker = to_broker(entry, k, inst)
    x = _dec_clean(entry_broker) - (_dec_clean(entry) - _dec_clean(level))
    return round_away(float(x), entry_broker, inst)


def saved_broker_price(prices, symbol, now, cfg, rolls):
    """The prices.json broker record of symbol when it is at most 60 minutes old, on the
    FX day of now, and no roll of its root came after its yahoo_at (D23); else None."""
    rec = ((prices or {}).get("broker") or {}).get(symbol)
    if not rec:
        return None
    at = parse_utc(rec.get("at"))
    if at is None or at > now or now - at > datetime.timedelta(minutes=BROKER_PRICE_MINUTES):
        return None
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = fx_day(now, day_end)
    if d is None or fx_day(at, day_end) != d:
        return None
    row = ((cfg or {}).get("instruments") or {}).get(symbol) or {}
    root = (row.get("futures") or {}).get("root")
    y_at = parse_utc(rec.get("yahoo_at")) or at
    for r in rolls or []:
        fbn = parse_utc(r.get("first_bar_new"))
        if root and r.get("root") == root and fbn is not None and fbn > y_at:
            return None
    return rec


def delay_error(inst, age_min, bars_5m, daily_atr=None):
    """About how far the price may have moved since Yahoo's price: the 5m ATR(14) x
    sqrt(age_min / 5), or with no 5m bars daily_atr x sqrt(age_min / 1440); 0 under a
    minute (or with no ATR). Rounded to the instrument's digits."""
    if age_min is None or age_min < 1:
        return 0.0
    a = atr_wilder(usable_bars(bars_5m)[-ATR_WINDOW["5m"]:], 14) if bars_5m else None
    if a:
        x = a * math.sqrt(age_min / 5.0)
    elif daily_atr:
        x = daily_atr * math.sqrt(age_min / 1440.0)
    else:
        return 0.0
    return _round_dec(x, int(inst.get("digits", 2)))
