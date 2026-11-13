"""Yahoo answers (made up, in Yahoo's real JSON shape) and the price caches built from them.

The caches are made the way refresh() makes them: weekly fetches, each answer read with
the copied parse_chart, stored_rows (clean_rows rule 1) and merge_rows, then save_cache.
"""

import datetime
import json
import os

from .common import cache_file, merge_rows, parse_chart, save_cache, stored_rows, ts_to_utc
from .util import D, H, M5, f32, iso, rnd, rng, ts

STEP = {"5m": 300, "1h": 3600, "1d": 86400}
VALID_RANGES = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
QUOTE_NAMES = ("open", "high", "low", "close", "volume")
FUT_META = {   # root: (exchangeName, fullExchangeName, firstTradeDate, the shortName in the sample)
    "GC": ("CMX", "COMEX", 967608000, "Gold Aug 26"),
    "SI": ("CMX", "COMEX", 967608000, "Silver Sep 26"),
    "PL": ("NYM", "NY Mercantile", 969163200, "Platinum Oct 26"),
    "PA": ("NYM", "NY Mercantile", 1009861200, "Palladium Sep 26"),
    "HG": ("CMX", "COMEX", 967608000, "Copper Sep 26"),
    "CL": ("NYM", "NY Mercantile", 967003200, "Crude Oil Sep 26"),
    "BZ": ("NYM", "NY Mercantile", 1185163200, "Brent Crude Oil Last Day Financ"),
    "NG": ("NYM", "NY Mercantile", 967003200, "Natural Gas Sep 26"),
}
DEC_NAME = "Gold Dec 26"


def _zone(sym):
    """(timezone, exchangeTimezoneName, gmtoffset) in July: BST for FX, EDT for CME."""
    if sym["data_hours"] == "cme":
        return "EDT", "America/New_York", -14400
    return "BST", "Europe/London", 3600


def _period(sym, day_start):
    """One trading period dict of the day that starts at day_start (UTC)."""
    tz, _, off = _zone(sym)
    end = day_start + (datetime.timedelta(hours=23) if sym["data_hours"] == "cme" else datetime.timedelta(hours=24))
    return {"timezone": tz, "start": ts(day_start), "end": ts(end) - 60, "gmtoffset": off}


def _day_start(sym, t):
    """The UTC start of Yahoo's trading day that holds t (22:00 UTC for CME, 23:00 for FX)."""
    hour = 22 if sym["data_hours"] == "cme" else 23
    s = t.replace(hour=hour, minute=0, second=0)
    return s if s <= t else s - datetime.timedelta(days=1)


def meta(sym, interval, rows, market_time, price, range_, short_name, prev_close, day_rows, chart_prev=None):
    """The chart meta of one answer (06 5.3 fields; made-up values)."""
    tz, zone, off = _zone(sym)
    fx = not sym["futures"]
    root = sym["yahoo"][:2]
    m = {"currency": "USD" if not fx else sym["key"][3:], "symbol": sym["yahoo"]}
    if fx:
        m.update(exchangeName="CCY", fullExchangeName="CCY", instrumentType="CURRENCY", firstTradeDate=1070236800)
    else:
        ex, full, first, _ = FUT_META[root]
        m.update(exchangeName=ex, fullExchangeName=full, instrumentType="FUTURE", firstTradeDate=first)
    closes = [r[4] for r in rows if r[4] is not None]
    highs = [r[2] for r in day_rows if r[2] is not None] or [price]
    lows = [r[3] for r in day_rows if r[3] is not None] or [price]
    p = sym["places"]
    m.update(regularMarketTime=market_time, hasPrePostMarketData=False, gmtoffset=off, timezone=tz,
             exchangeTimezoneName=zone, regularMarketPrice=price,
             fiftyTwoWeekHigh=rnd(max(closes or [price]) * 1.08, p), fiftyTwoWeekLow=rnd(min(closes or [price]) * 0.93, p),
             regularMarketDayHigh=rnd(max(highs), p), regularMarketDayLow=rnd(min(lows), p),
             regularMarketVolume=0 if fx else sum(r[5] or 0 for r in day_rows))
    if fx:
        m["longName"] = "%s/%s" % (sym["key"][:3], sym["key"][3:])
    m["shortName"] = short_name
    m["chartPreviousClose"] = chart_prev if chart_prev is not None else prev_close
    if interval != "1d":
        m["previousClose"] = prev_close
    m.update(scale=3 if not fx else 4, priceHint=2 if not fx else 4)
    day0 = _day_start(sym, ts_to_utc(market_time))
    reg = _period(sym, day0)
    m["currentTradingPeriod"] = {"pre": {"timezone": tz, "start": reg["start"], "end": reg["start"], "gmtoffset": off},
                                 "regular": reg,
                                 "post": {"timezone": tz, "start": reg["end"], "end": reg["end"], "gmtoffset": off}}
    if interval != "1d" and rows:
        starts = sorted(set(_day_start(sym, ts_to_utc(r[0])) for r in rows))
        m["tradingPeriods"] = [[_period(sym, s)] for s in starts]
    m.update(dataGranularity=interval, range=range_ or "", validRanges=list(VALID_RANGES))
    return m


def raw_rows(bars):
    """Story bars [datetime, o, h, l, c, v] -> Yahoo rows [t, o, h, l, c, v] as float32."""
    out = []
    for b in bars:
        if b[4] is None:
            out.append([ts(b[0]), None, None, None, None, None])
        else:
            out.append([ts(b[0])] + [f32(x) for x in b[1:5]] + [b[5]])
    return out


def answer(sym, interval, rows, market_time, price, short_name, range_, prev_close, chart_prev, day_rows, live=True):
    """One chart answer {"chart": {"result": [...], "error": null}}: the rows, then the
    live tick (intraday), the quote lists in a seeded shuffled key order."""
    rows = list(rows)
    if live and interval != "1d" and rows:
        p = f32(price)
        rows.append([market_time, p, p, p, p, 0])
    result = {"meta": meta(sym, interval, rows, market_time, price, range_, short_name, prev_close, day_rows,
                          chart_prev)}
    names = list(QUOTE_NAMES)
    rng("order", sym["yahoo"], interval, market_time).shuffle(names)
    if rows:
        result["timestamp"] = [r[0] for r in rows]
        q = dict((n, [r[1 + QUOTE_NAMES.index(n)] for r in rows]) for n in names)
    else:
        q = {}
    result["indicators"] = {"quote": [q]}
    if interval == "1d" and rows:
        result["indicators"]["adjclose"] = [{"adjclose": [r[4] for r in rows]}]
    return {"chart": {"result": [result], "error": None}}


def dumps(doc):
    """Yahoo's compact JSON (keys in the answer's own order)."""
    return json.dumps(doc, separators=(",", ":"))


def market_time_of(sym, at):
    """The data clock of a fetch: Friday's last tick after the close (FX 21:00:00, CME
    20:59:58 UTC), else 4 seconds before the fetch."""
    if at.weekday() == 4 and at.time() >= datetime.time(21, 0):
        last = at.replace(hour=21, minute=0, second=0)
        return ts(last) - (2 if sym["futures"] else 0)
    return ts(at) - 4


def short_name(sym, interval, at):
    """The meta shortName at a fetch: FX "EUR/USD"; GC=F "Gold Aug 26" until the roll
    is seen (1h: the fetch of 07-29 08:12), then "Gold Dec 26"."""
    if not sym["futures"]:
        return "%s/%s" % (sym["key"][:3], sym["key"][3:])
    if sym["yahoo"].startswith("GCZ"):
        return DEC_NAME
    if sym["yahoo"] == "GC=F" and at >= D(2026, 7, 29, 8, 12):
        return DEC_NAME
    return FUT_META[sym["yahoo"][:2]][3]


def _prev_close(rows, market_time):
    """The close of the last bar before the FX day (21:00 UTC cut in July) of market_time."""
    mt = ts_to_utc(market_time - 1)
    cut = mt.replace(hour=21, minute=0, second=0)
    if cut > mt:
        cut -= datetime.timedelta(days=1)
    before = [r for r in rows if r[4] is not None and r[0] < ts(cut)]
    return (before[-1][4], cut) if before else (None, cut)


def fetch_answer(sym, interval, all_rows, at, period1=None, range_=None, extra=None, mt=None):
    """(answer doc, market_time) of one fetch at `at`: the rows the data clock has seen,
    inside [period1, at + 60 s) (or the whole range), plus `extra` rows (a forming bar)."""
    mt = market_time_of(sym, at) if mt is None else mt
    lo = period1 if period1 is not None else -1
    rows = [r for r in all_rows if lo <= r[0] <= mt and r[0] < ts(at) + 60]
    if extra:
        rows = [r for r in rows if r[0] not in set(e[0] for e in extra)] + list(extra)
        rows.sort(key=lambda r: r[0])
    done = [r for r in all_rows if r[0] <= mt and r[4] is not None] + [e for e in (extra or []) if e[4] is not None]
    price = rnd(sorted(done, key=lambda r: r[0])[-1][4], sym["places"]) if done else None
    pc, cut = _prev_close(done, mt)
    pc = rnd(pc, sym["places"]) if pc is not None else None
    first = rows[0][0] if rows else None
    older = [r for r in all_rows if first is not None and r[0] < first and r[4] is not None]
    chart_prev = rnd(older[-1][4], sym["places"]) if older else pc
    day_rows = [r for r in done if r[0] >= ts(cut)]
    doc = answer(sym, interval, rows, mt, price, short_name(sym, interval, at), range_,
                 None if interval == "1d" else pc, chart_prev, day_rows, live=interval != "1d")
    return doc, mt


FIRST_RANGE = {"5m": "60d", "1h": "730d", "1d": "2y"}
ROLL_SEEN = D(2026, 7, 29, 8, 12)        # m: the fetch that first says "Gold Dec 26"


def fetch_times(sym, interval):
    """The fetch times of one cache file: every Friday at 21:30 UTC (5m from 06-26), and
    for GC=F 1h also 07-29 08:00 (still "Gold Aug 26") and 08:12 ("Gold Dec 26")."""
    from .util import fridays, FIRST_DAY, FIVE_MIN_DAY, LAST_DAY
    first = FIVE_MIN_DAY if interval == "5m" else FIRST_DAY
    out = fridays(first, LAST_DAY)
    if sym["yahoo"] == "GC=F" and interval == "1h":
        out += [D(2026, 7, 29, 8, 0), ROLL_SEEN]
    return sorted(out)


def forming(bars5, mt):
    """The forming 1h row at data time mt, from the 5m bars that started by then."""
    hour = ts_to_utc(mt).replace(minute=0, second=0)
    part = [b for b in bars5 if b[4] is not None and hour <= b[0] and ts(b[0]) <= mt]
    if not part:
        return []
    return raw_rows([[hour, part[0][1], max(b[2] for b in part), min(b[3] for b in part), part[-1][4],
                      sum(b[5] for b in part)]])


def answer_at(sym, interval, rows_all, at, period1=None, range_=None, bars5=None, mt=None):
    """(answer, market_time) of a fetch at `at`. A 1h bar that is still forming at the data
    time is built from the 5m bars seen so far (or left out when there are none)."""
    mt = market_time_of(sym, at) if mt is None else mt
    rows, extra = rows_all, None
    if interval == "1h":
        step = STEP[interval]
        rows = [r for r in rows_all if not (r[0] <= mt < r[0] + step - 2)]
        extra = forming(bars5, mt) if bars5 else None
    return fetch_answer(sym, interval, rows, at, period1, range_, extra, mt)


def build_cache(sym, interval, bars, times, bars5=None, first_period1=None):
    """A cache doc made by the fetches at `times` (the first one a range fill, unless
    first_period1 is given), each merged as refresh() does. Returns the doc."""
    rows_all = raw_rows(bars)
    step = STEP[interval]
    doc = {"symbol": sym["yahoo"], "interval": interval, "bars": [], "fetches": []}
    for at in times:
        range_ = period1 = period2 = None
        if not doc["bars"] and first_period1 is None:
            range_ = FIRST_RANGE[interval]
        else:
            period1 = first_period1 if not doc["bars"] else doc["bars"][-1][0] - 2 * step
            period2 = ts(at) + 60
        ans, mt = answer_at(sym, interval, rows_all, at, period1, range_, bars5)
        meta, got = parse_chart(ans["chart"]["result"][0], sym["yahoo"])
        kept = stored_rows(got, meta.get("regularMarketTime"))
        window = (kept[0][0], kept[-1][0]) if kept else None
        doc["bars"] = merge_rows(doc["bars"], kept, window, at, interval)
        doc["fetches"].append({
            "at": iso(at), "host": "query1", "range": range_, "period1": period1, "period2": period2,
            "first": kept[0][0] if kept else None, "last": kept[-1][0] if kept else None, "rows": len(kept),
            "market_time": meta.get("regularMarketTime"), "price": meta.get("regularMarketPrice"),
            "short_name": meta.get("shortName"), "currency": meta.get("currency"),
            "tz": meta.get("exchangeTimezoneName"), "gmtoffset": meta.get("gmtoffset"),
            "previous_close": meta.get("previousClose")})
    return doc


def write_caches(out, paths, contracts, daily):
    """Every cache file of fx-market/data/yahoo/. Returns the paths written."""
    from .util import LAST_DAY
    data = os.path.join(out, "data")
    written = []
    for yahoo, p in sorted(paths.items()):
        sym = p["sym"]
        jobs = [("1h", p["1h"]), ("5m", p["5m"])] + ([("1d", daily[yahoo])] if yahoo in daily else [])
        for interval, bars in jobs:
            if not bars:
                continue
            doc = build_cache(sym, interval, bars, fetch_times(sym, interval), p["5m"])
            path = cache_file(data, yahoo, interval)
            save_cache(path, doc)
            written.append(path)
    gold = paths["GC=F"]["sym"]
    for symbol, bars in sorted(contracts.items()):
        sym = dict(gold, yahoo=symbol)
        times = [ROLL_SEEN, D(LAST_DAY.year, LAST_DAY.month, LAST_DAY.day, 21, 30)]
        doc = build_cache(sym, "1h", bars, times, None, ts(bars[0][0]))
        path = cache_file(data, symbol, "1h")
        save_cache(path, doc)
        written.append(path)
    return written


ERR_404 = '{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found, symbol may be delisted"}}}'
ERR_422 = ('{"chart":{"result":null,"error":{"code":"Unprocessable Entity","description":"5m data not available '
           'for startTime=1777870800 and endTime=1785531600. The requested range must be within the last 60 days."}}}')
CUT_AT = 4840                       # a real answer was once cut short at this byte


def _write(out, name, text):
    """Write one raw answer file and return its path."""
    from .util import write_text
    path = os.path.join(out, "raw", name)
    write_text(path, text)
    return path


def _spark(paths, at):
    """A spark answer (range 1d, interval 5m) for the 4 day symbols at `at`, closes rounded
    as spark sends them (4 decimals, 3 for JPY pairs, gold 1), results in a shuffled order."""
    items = []
    for y in ("EURUSD=X", "GBPUSD=X", "USDJPY=X", "GC=F"):
        sym = paths[y]["sym"]
        mt = ts(at) - 4
        start = _day_start(sym, at)
        rows = [r for r in raw_rows(paths[y]["5m"]) if ts(start) <= r[0] <= mt]
        places = 1 if y == "GC=F" else (3 if y.startswith("USDJPY") else 4)
        done = [r for r in rows if r[4] is not None]
        price = rnd(done[-1][4], sym["places"])
        m = meta(sym, "5m", rows, mt, price, "1d", short_name(sym, "5m", at), None, rows)
        m.pop("tradingPeriods", None)
        resp = {"meta": m, "timestamp": [r[0] for r in rows],
                "indicators": {"quote": [{"close": [None if r[4] is None else rnd(r[4], places) for r in rows]}]}}
        items.append({"symbol": y, "response": [resp]})
    rng("spark", iso(at)).shuffle(items)
    return {"spark": {"result": items, "error": None}}


def write_raw(out, paths, contracts):
    """The single answers of fx-market/raw/ (06 5.3) for the fake network of test_core."""
    gold = paths["GC=F"]
    gsym = gold["sym"]
    at = D(2026, 7, 29, 12, 0)
    p1 = ts(D(2026, 7, 28, 0, 0))
    written = []
    doc, _ = answer_at(gsym, "1h", raw_rows(gold["1h"]), at, p1, None, gold["5m"])
    written.append(_write(out, "GC_F-1h-incr.json", dumps(doc)))
    for symbol, bars in sorted(contracts.items()):
        doc, _ = answer_at(dict(gsym, yahoo=symbol), "1h", raw_rows(bars), at, p1)
        written.append(_write(out, "%s-1h.json" % symbol.replace(".", "_"), dumps(doc)))
    eur = paths["EURUSD=X"]
    doc, _ = answer_at(eur["sym"], "5m", raw_rows(eur["5m"]), D(2026, 7, 23, 14, 0), ts(D(2026, 7, 23, 0, 0)))
    written.append(_write(out, "EURUSD_X-5m-day.json", dumps(doc)))
    sat = D(2026, 7, 25, 10, 0)
    friday_tick = ts(D(2026, 7, 24, 21, 0)) - 2
    doc, _ = answer_at(gsym, "5m", raw_rows(gold["5m"]), sat, ts(D(2026, 7, 25, 0, 0)), None, None, friday_tick)
    written.append(_write(out, "GC_F-5m-saturday.json", dumps(doc)))
    week = D(2026, 7, 24, 21, 30)
    doc, _ = answer_at(gsym, "1h", raw_rows(gold["1h"]), week, ts(D(2026, 7, 17, 18, 0)), None, gold["5m"])
    written.append(_write(out, "GC_F-1h-cut.json", dumps(doc)[:CUT_AT]))
    written.append(_write(out, "err-404.json", ERR_404))
    written.append(_write(out, "err-422.json", ERR_422))
    written.append(_write(out, "spark-4.json", dumps(_spark(paths, D(2026, 7, 23, 7, 41)))))
    return written


def roll_record(contracts):
    """The gold roll GC-2026-07-29 as check_roll_forward records it at 07-29 08:12 (5.4 k to m)."""
    from .story import ROLL_BAR
    rep = [r for r in raw_rows(contracts["GCZ26.CMX"]) if r[0] == ts(ROLL_BAR)][0]
    return {"id": "GC-2026-07-29", "root": "GC", "yahoo": "GC=F", "old": "GCQ26.CMX", "new": "GCZ26.CMX",
            "old_name": "Gold Aug 26", "new_name": DEC_NAME, "first_bar_new": iso(ROLL_BAR), "ratio": 2500.3 / 2478.0,
            "old_price": 2478.0, "new_price": 2500.3, "prices_at": iso(ROLL_BAR - H), "found_by": "shortname",
            "confidence": "exact", "replaced_bar": True, "found_at": iso(ROLL_SEEN), "note": "",
            "replacement": {"1h": rep}}


def write_rolls(out, contracts):
    """fx-market/data/rolls.json: the gold roll, and `checked` for the 8 roots from 2026-04-06."""
    from .util import write_json
    checked = {}
    for root in sorted(FUT_META):
        checked[root] = {"history_from": "2026-04-06", "history_to": "2026-07-31",
                         "last_forward": iso(ROLL_SEEN) if root == "GC" else "2026-07-31T21:30:00Z"}
    path = os.path.join(out, "data", "rolls.json")
    write_json(path, {"updated_at": "2026-07-31T21:30:00Z", "rolls": [roll_record(contracts)], "checked": checked})
    return path
