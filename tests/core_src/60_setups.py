# group: indicators, setups

# ---- indicators (part 05 section 1)

# A daily bar with one of these flags is used by no indicator and no setup.
SKIP_FLAGS = ("roll", "outage", "partial", "holiday", "forming")
# The ATR window: this many usable bars, ending at the bar the ATR is for.
ATR_WINDOW = {"1d": 250, "1h": 120, "5m": 288}


def usable_bars(bars, outages=None):
    """New copies of the bars without the SKIP_FLAGS ones; each gets "after_gap": True
    on the first bar, after a dropped bar, or at the end of an outage, else False."""
    ends = set(to for _frm, to in (outages or []))
    out = []
    dropped = True
    for bar in bars:
        if any(f in SKIP_FLAGS for f in (bar.get("flags") or ())):
            dropped = True
            continue
        b = dict(bar)
        b["after_gap"] = bool(dropped or b.get("start") in ends)
        dropped = False
        out.append(b)
    return out


def true_range(bar, prev_close):
    """The true range of one bar: high - low, or the widest move from prev_close
    (prev_close None, or the bar after a gap: just high - low). Not rounded."""
    hl = bar["high"] - bar["low"]
    if prev_close is None or bar.get("after_gap"):
        return hl
    return max(hl, abs(bar["high"] - prev_close), abs(bar["low"] - prev_close))


def atr_wilder(bars, n=14):
    """Wilder ATR(n) of usable_bars output (the caller cuts the ATR_WINDOW first): the
    mean of the first n true ranges, then Wilder smoothing. None if under n + 1 bars or 0."""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        prev = None if bars[i].get("after_gap") else bars[i - 1]["close"]
        trs.append(true_range(bars[i], prev))
    atr = sum(trs[:n]) / float(n)
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr if atr > 0 else None


def sma(values, n):
    """The mean of the last n values; None when there are fewer than n."""
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / float(n)


def ema_series(values, n):
    """The EMA(n) after each value (seed: the SMA of the first n, k = 2 / (n + 1));
    None for the first 2 x n - 1 values, while the EMA still leans on its seed."""
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / float(n)
    for i in range(n - 1, len(values)):
        if i >= n:
            e = (values[i] - e) * k + e
        if i >= 2 * n - 1:
            out[i] = e
    return out


def ema(values, n):
    """The EMA(n) after the last value; None when there are fewer than 2 x n values."""
    if not values:
        return None
    return ema_series(values, n)[-1]


def rsi_series(closes, n=14):
    """Wilder RSI(n) after each close (100 with no losses, 50 when flat); None for the
    first n closes."""
    out = [None] * len(closes)
    if n <= 0 or len(closes) < n + 1:
        return out
    d = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(x, 0) for x in d[:n]) / float(n)
    loss = sum(max(-x, 0) for x in d[:n]) / float(n)
    for i in range(n, len(closes)):
        if i > n:
            x = d[i - 1]
            gain = (gain * (n - 1) + max(x, 0)) / n
            loss = (loss * (n - 1) + max(-x, 0)) / n
        if loss == 0:
            out[i] = 50.0 if gain == 0 else 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def rsi_wilder(closes, n=14):
    """Wilder RSI(n) after the last close; None when there are fewer than n + 1 closes."""
    if not closes:
        return None
    return rsi_series(closes, n)[-1]


def highest(bars, n, field="high"):
    """The max of field over the last n bars; None when there are fewer than n."""
    if n <= 0 or len(bars) < n:
        return None
    return max(b[field] for b in bars[-n:])


def lowest(bars, n, field="low"):
    """The min of field over the last n bars; None when there are fewer than n."""
    if n <= 0 or len(bars) < n:
        return None
    return min(b[field] for b in bars[-n:])


# ---- setups (part 05 sections 2 and 3)

NEAR_ATR = 0.5            # a breakout level is listed when the close is this near (in ATRs)
CHASE_ATR = 1.0           # more than this past the level: the chase note
MIN_DAILY_BARS = 60       # fewer usable daily bars: no swing setups at all
SWING_BUFFER_ATR = 0.25   # swing-low, swing-high, flag-far and bar-far stops: this far out
SWING_VALID_DAYS = 3      # FX days a swing hit waits for its close
OR_MAX_DAILY_ATR = 0.6    # an opening range wider than this x the daily ATR is skipped
OR_MIN_SPREADS = 8        # an opening range narrower than this x the spread is skipped
NEAR_ATR_5M = 1.0         # a day hit with no trigger: the close this near (in 5m ATRs)
MIN_STOP_ATR = {"swing": 0.5, "day": 1.0}
MIN_DAY_MINUTES_LEFT = 15
# Words no tool prints (test_repo checks the scripts and SKILL.md files).
SIGNAL_BANNED = ("strong buy", "strong sell", "buy now", "sell now", "you should buy",
                 "you should sell", "sure thing", "guaranteed", "can't lose", "cannot lose",
                 "winning trade", "easy money", "recommend")


def _setup(name, side, style, words, min_bars, listed, stop):
    """One row of SETUPS as a dict."""
    return {"name": name, "side": side, "style": style, "words": words,
            "min_bars": min_bars, "listed": listed, "stop": stop}


# Each setup: name, side, style, words, min_bars, listed ("near" or "pattern") and the
# stop the rule "setup" gives. In this order (the scanner ranks ties by it).
SETUPS = (
    _setup("breakout_20d", "buy", "swing", "20-day high breakout", 60, "near", "atr:2"),
    _setup("breakdown_20d", "sell", "swing", "20-day low breakdown", 60, "near", "atr:2"),
    _setup("breakout_55d", "buy", "swing", "55-day high breakout", 60, "near", "atr:2"),
    _setup("breakdown_55d", "sell", "swing", "55-day low breakdown", 60, "near", "atr:2"),
    _setup("pullback_ema20", "buy", "swing", "pullback to a rising 20-day EMA", 100,
           "pattern", "swing-low"),
    _setup("rally_ema20", "sell", "swing", "rally to a falling 20-day EMA", 100,
           "pattern", "swing-high"),
    _setup("bull_flag", "buy", "swing", "bull flag (ATR rule)", 60, "pattern", "flag-far"),
    _setup("bear_flag", "sell", "swing", "bear flag (ATR rule)", 60, "pattern", "flag-far"),
    _setup("reclaim_ma20", "buy", "swing", "back over the 20-day average", 60,
           "pattern", "swing-low"),
    _setup("lose_ma20", "sell", "swing", "back under the 20-day average", 60,
           "pattern", "swing-high"),
    _setup("oversold_bounce", "buy", "swing", "oversold bounce (RSI 14 was under 30)", 60,
           "pattern", "swing-low"),
    _setup("overbought_drop", "sell", "swing", "overbought drop (RSI 14 was over 70)", 60,
           "pattern", "swing-high"),
    _setup("inside_break_up", "buy", "swing", "inside day and NR7, upper side", 60,
           "pattern", "bar-far"),
    _setup("inside_break_down", "sell", "swing", "inside day and NR7, lower side", 60,
           "pattern", "bar-far"),
    _setup("or_breakout", "buy", "day",
           "opening-range breakout (a close above the range high)", 0, "near", "or-mid"),
    _setup("or_breakdown", "sell", "day",
           "opening-range breakdown (a close below the range low)", 0, "near", "or-mid"),
)


def _flag_level(B, A, side):
    """The bull (buy) or bear (sell) flag test on usable bars B (05 2.2): the flag top
    (buy) or bottom (sell) of B[-5:], or None when the pattern does not hold."""
    if len(B) < 16:
        return None
    pole_end = B[-6]["close"]
    flag = B[-5:]
    top = max(b["high"] for b in flag)
    bottom = min(b["low"] for b in flag)
    if top - bottom > 1.5 * A:
        return None
    if side == "buy":
        pole_low = lowest(B[-16:-6], 10, "low")
        if pole_end - pole_low >= 3 * A and bottom > (pole_low + pole_end) / 2.0:
            return top
        return None
    pole_high = highest(B[-16:-6], 10, "high")
    if pole_high - pole_end >= 3 * A and top < (pole_high + pole_end) / 2.0:
        return bottom
    return None


def _swing_level(name, B, A):
    """The level of the swing setup `name` on usable daily bars B (B[-1] is the newest
    bar), or None when its rule does not hold (05 2.2)."""
    last, prev = B[-1], B[-2]
    before = B[:-1]
    closes = [b["close"] for b in B]
    if name in ("breakout_20d", "breakout_55d"):
        return highest(before, 20 if name == "breakout_20d" else 55, "high")
    if name in ("breakdown_20d", "breakdown_55d"):
        return lowest(before, 20 if name == "breakdown_20d" else 55, "low")
    if name in ("pullback_ema20", "rally_ema20"):
        e20s, e50 = ema_series(closes, 20), ema(closes, 50)
        e20, e20_5 = e20s[-1], e20s[-6]
        if None in (e20, e20_5, e50):
            return None
        if name == "pullback_ema20":
            ok = e20 > e50 and e20 > e20_5 and last["low"] <= e20 < last["close"]
            return last["high"] if ok else None
        ok = e20 < e50 and e20 < e20_5 and last["high"] >= e20 > last["close"]
        return last["low"] if ok else None
    if name in ("bull_flag", "bear_flag"):
        return _flag_level(B, A, "buy" if name == "bull_flag" else "sell")
    if name in ("reclaim_ma20", "lose_ma20"):
        s20p, s20, s50 = sma(closes[:-1], 20), sma(closes, 20), sma(closes, 50)
        if None in (s20p, s20, s50):
            return None
        if name == "reclaim_ma20":
            ok = prev["close"] < s20p and last["close"] > s20 and last["close"] > s50
        else:
            ok = prev["close"] > s20p and last["close"] < s20 and last["close"] < s50
        return s20 if ok else None
    if name in ("oversold_bounce", "overbought_drop"):
        r = rsi_series(closes, 14)[-2]
        if r is None:
            return None
        if name == "oversold_bounce":
            return last["high"] if r < 30 and last["close"] > prev["close"] else None
        return last["low"] if r > 70 and last["close"] < prev["close"] else None
    if name in ("inside_break_up", "inside_break_down"):
        if len(B) < 7:
            return None
        inside = last["high"] < prev["high"] and last["low"] > prev["low"]
        rng = last["high"] - last["low"]
        nr7 = all(rng <= b["high"] - b["low"] for b in B[-7:])
        if not (inside and nr7):
            return None
        return last["high"] if name == "inside_break_up" else last["low"]
    return None


def coarse_words(inst):
    """The coarse-data note for a key in COARSE (02 6), or None: EURUSD moves in steps
    of about 1.25 pips; GBPUSD, AUDUSD and NZDUSD show about one price a minute."""
    key = inst.get("key")
    if key not in COARSE:
        return None
    if key == "EURUSD":
        return "Coarse data: Yahoo EURUSD moves in steps of about 1.25 pips."
    return "Coarse data: Yahoo %s shows about one price a minute." % key


def find_setups(daily_bars, inst, cfg, sides):
    """The swing hits on the newest daily bar (05 2.3), in SETUPS order: each with its
    level, last close, daily ATR, distance_atr and notes. ValueError when it cannot scan."""
    name = inst.get("name") or inst["key"]
    bars = [b for b in daily_bars if "forming" not in (b.get("flags") or ())]   # (check)
    if not bars:
        raise ValueError("No daily prices for %s yet." % name)
    bad = [f for f in SKIP_FLAGS if f in (bars[-1].get("flags") or ())]
    if bad:
        raise ValueError("The newest %s day (%s) is not usable on Yahoo (%s). No setups today."
                         % (name, day_label(parse_fx_date(bars[-1]["fx_day"])), bad[0]))
    B = usable_bars(bars)
    if len(B) < MIN_DAILY_BARS:
        raise ValueError("Only %d usable daily bars for %s (need %d)."
                         % (len(B), name, MIN_DAILY_BARS))
    A = atr_wilder(B[-ATR_WINDOW["1d"]:], 14)
    if A is None:
        raise ValueError("Not enough bars for the ATR of %s." % name)
    last = B[-1]
    hits = []
    for s in SETUPS:
        if s["style"] != "swing" or s["side"] not in sides or len(B) < s["min_bars"]:
            continue
        raw = _swing_level(s["name"], B, A)
        if raw is None:
            continue
        level = round_price(raw, inst)
        gap = level - last["close"] if s["side"] == "buy" else last["close"] - level
        dist = round(gap / A, 2)
        if s["listed"] == "near" and dist > NEAR_ATR:
            continue
        notes = []
        if dist < -CHASE_ATR:
            notes.append("The close is already %.2f ATR past the level. "
                         "A late entry is a different trade." % -dist)
        if s["name"] in ("oversold_bounce", "overbought_drop"):
            notes.append("This trades against the trend.")
        coarse = coarse_words(inst)
        if coarse:
            notes.append(coarse)
        hits.append({"symbol": inst["key"], "name": name, "side": s["side"],
                     "style": "swing", "setup": s["name"], "level": level,
                     "last": last["close"], "atr": round(A, int(inst["digits"]) + 1),
                     "atr_interval": "1d", "distance_atr": dist, "opening_range": None,
                     "data_age_min": None, "notes": notes, "fx_day": last["fx_day"]})
    return hits


def opening_range(bars_5m, session, fx_date, minutes, inst):
    """The opening range of a session on fx_date (05 3.1): high, low, mid (to the tick of
    inst, half up) and how many of the expected 5m bars were there (roll bars left out).
    None when no range bar."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes % 5:
        raise ValueError("The opening range must be a whole number of 5-minute bars "
                         "(got %s minutes)." % (minutes,))
    start, end = opening_range_window(session, fx_date, minutes)
    rng = [b for b in bars_5m if start <= b["start"] < end
           and "roll" not in (b.get("flags") or ())]
    if not rng:
        return None   # no range bar at all (a range of roll bars only: None too, check)
    high = max(b["high"] for b in rng)
    low = min(b["low"] for b in rng)
    mid = round_price((high + low) / 2.0, inst)
    return {"session": session, "start": utc_iso(start), "end": utc_iso(end),
            "high": high, "low": low, "mid": mid, "bars": len(rng), "of": minutes // 5}


def or_trigger(bars_5m, or_, side, after_utc, until_utc):
    """The first finished 5m bar after the range (and after after_utc) whose close is
    beyond it: buy above the high, sell below the low; done by until_utc. Or None."""
    if or_["bars"] < or_["of"]:
        raise ValueError("The opening range is not complete.")
    if side not in ("buy", "sell"):
        raise ValueError('A side is "buy" or "sell", not "%s".' % (side,))
    frm = parse_utc(or_["end"])
    if after_utc is not None and after_utc > frm:
        frm = after_utc
    step = datetime.timedelta(minutes=5)
    for b in sorted(bars_5m, key=lambda x: x["start"]):
        if b["start"] < frm or b["start"] + step > until_utc:
            continue
        if "roll" in (b.get("flags") or ()) or b.get("after_gap"):   # every after_gap: the safe side
            continue
        if side == "buy" and b["close"] > or_["high"]:
            return b
        if side == "sell" and b["close"] < or_["low"]:
            return b
    return None


# ---- correlation groups and currency legs (part 05 sections 5.5 and 5.6)

# group name -> the keys in it (part 01's first cut, kept). A key in no group is a group
# of one. The instrument's own "group" column wins over this table.
CORRELATION_GROUPS = {
    "usd_majors": ("EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"),
    "jpy": ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"),
    "eur_crosses": ("EURGBP", "EURCHF"),
    "metals": ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"),
    "industrial": ("COPPER",),
    "energy": ("USOIL", "UKOIL"),   # WTI and Brent: one oil market (check: not measured)
    "natgas": ("NATGAS",),
}


def group_of(symbol, cfg):
    """The correlation group of a key: its "group" column when set, else the group of
    CORRELATION_GROUPS that holds it, else the key itself (a group of one)."""
    row = ((cfg or {}).get("instruments") or {}).get(symbol) or {}
    if row.get("group"):
        return row["group"]
    for name, keys in CORRELATION_GROUPS.items():
        if symbol in keys:
            return name
    return symbol


def currency_legs(symbol, side, inst):
    """The currency bets of one trade, +1 long and -1 short: fx {base: s, quote: -s},
    metal {base: s, "USD": -s}, energy {base: s} only; s = 1 for a buy, -1 for a sell."""
    if side not in ("buy", "sell"):
        raise ValueError('A side is "buy" or "sell", not "%s".' % (side,))
    s = 1 if side == "buy" else -1
    kind = inst.get("kind")
    if kind == "fx":
        return {inst["base"]: s, inst["quote"]: -s}
    if kind == "metal":
        return {inst["base"]: s, "USD": -s}
    if kind == "energy":
        return {inst["base"]: s}
    raise ValueError('%s: kind must be "fx", "metal" or "energy", not "%s".' % (symbol, kind))
