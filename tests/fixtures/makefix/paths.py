"""The price paths of the sample market: one seeded random walk per symbol (made up).

1h bars from 2026-04-06 directly; for the 4 day symbols 5m bars from 2026-06-22 on (the
1h bars of that part are built from them in story.py). Only bars inside open data hours
are made. The designed story levels (story.PINS, OPENS, BOUNDS) steer the walk; the
designed bars themselves are pasted over it by story.apply.
"""

import datetime
import math

from . import story
from .common import city_to_utc, fx_day, at_time
from .util import D, FIVE_MIN_DAY, H, M5, rnd, rng, slots

# key, yahoo, data hours, yahoo decimals, start price, made-up daily ATR, futures, day symbol
SYMBOLS = (
    ("EURUSD", "EURUSD=X", "yahoo_fx", 5, 1.0850, 0.0060, False, True),
    ("GBPUSD", "GBPUSD=X", "yahoo_fx", 5, 1.2700, 0.0080, False, True),
    ("USDJPY", "USDJPY=X", "yahoo_fx", 3, 142.00, 0.95, False, True),
    ("USDCHF", "USDCHF=X", "yahoo_fx", 5, 0.8900, 0.0055, False, False),
    ("AUDUSD", "AUDUSD=X", "yahoo_fx", 5, 0.6200, 0.0055, False, False),
    ("USDCAD", "USDCAD=X", "yahoo_fx", 5, 1.3200, 0.0070, False, False),
    ("NZDUSD", "NZDUSD=X", "yahoo_fx", 5, 0.6000, 0.0050, False, False),
    ("EURJPY", "EURJPY=X", "yahoo_fx", 3, 154.00, 1.10, False, False),
    ("GBPJPY", "GBPJPY=X", "yahoo_fx", 3, 180.00, 1.40, False, False),
    ("EURGBP", "EURGBP=X", "yahoo_fx", 5, 0.7900, 0.0035, False, False),
    ("AUDJPY", "AUDJPY=X", "yahoo_fx", 3, 94.00, 0.85, False, False),
    ("EURCHF", "EURCHF=X", "yahoo_fx", 5, 1.0300, 0.0040, False, False),
    ("XAUUSD", "GC=F", "cme", 1, 2385.0, 28.0, True, True),
    ("XAGUSD", "SI=F", "cme", 3, 28.50, 0.60, True, False),
    ("XPTUSD", "PL=F", "cme", 1, 960.0, 18.0, True, False),
    ("XPDUSD", "PA=F", "cme", 1, 1000.0, 30.0, True, False),
    ("COPPER", "HG=F", "cme", 4, 4.2000, 0.0800, True, False),
    ("USOIL", "CL=F", "cme", 2, 78.00, 1.60, True, False),
    ("UKOIL", "BZ=F", "cme", 2, 82.00, 1.55, True, False),
    ("NATGAS", "NG=F", "cme", 3, 2.600, 0.110, True, False),
)
FIELDS = ("key", "yahoo", "data_hours", "places", "start", "atr", "futures", "day")
GAP_CAP = 0.008      # a weekend or break gap of the walk is at most 0.8% (story b is bigger)
# an FX pair stays inside its made-up band: about 3% around the start, so it never
# reaches real levels (06 5); EURUSD has its own band (real: 1.11 to 1.12 in 2026-11)
BAND_PCT = 0.03
BANDS = {"EURUSD": (1.0600, 1.1000)}


def symbols():
    """The 20 symbol rows as dicts, in table order."""
    return [dict(zip(FIELDS, row)) for row in SYMBOLS]


def band(sym):
    """(lo, hi) an FX pair's prices stay inside, or None (metals and energy: no band)."""
    if sym["futures"]:
        return None
    return BANDS.get(sym["key"], (sym["start"] * (1 - BAND_PCT), sym["start"] * (1 + BAND_PCT)))


def _reflect(x, wall):
    """x mirrored back inside wall (lo, hi); the walk keeps its own steps inside."""
    if wall is None:
        return x
    lo, hi = wall
    if x > hi:
        x = 2 * hi - x
    if x < lo:
        x = 2 * lo - x
    return min(max(x, lo), hi)


def _bound_of(t, bounds):
    """The (lo, hi) of the narrowest bound window that holds t, or None."""
    boxes = [(hi - lo, lo, hi) for t0, t1, lo, hi in bounds if t0 <= t <= t1]
    return min(boxes)[1:] if boxes else None


def walk(sym, interval, grid, first_open, pins, opens, bounds):
    """Bars [t, o, h, l, c, v] on the slot grid [(t, after_break)]: a seeded walk with a
    weak pull to the start price, a stronger pull inside BOUNDS windows, bridged so each
    pinned close is exact, gaps only after a break (or as OPENS say)."""
    g = rng("path", sym["yahoo"], interval)
    per_day = 24.0 if interval == "1h" else 276.0
    sd = sym["atr"] / math.sqrt(per_day) * 0.9
    wall = band(sym)
    if wall is not None:
        wall = (wall[0] + 3 * sd, wall[1] - 3 * sd)
    theta_free = sd * sd / (2.0 * (4.0 * sym["atr"]) ** 2)
    n = len(grid)
    closes, gaps = [0.0] * n, [0.0] * n
    pin_idx = sorted(i for i, (t, _) in enumerate(grid) if t in pins)
    prev, seg_start = first_open, 0
    for end in pin_idx + [None]:
        last = (n - 1) if end is None else end
        x0 = prev
        target = pins[grid[end][0]] if end is not None else None
        for i in range(seg_start, last + 1):
            t, brk = grid[i]
            gap = 0.0
            if t in opens:
                gap = opens[t] - prev
            elif brk and i > 0:
                gap = max(-GAP_CAP, min(GAP_CAP, g.gauss(0, 0.002))) * prev
            frac = (i - seg_start + 1) / float(last - seg_start + 1)
            base = x0 + (target - x0) * frac if target is not None else sym["start"]
            box = _bound_of(t, bounds)
            if box:
                width = (box[1] - box[0]) / 8.0
                theta = min(0.5, sd * sd / (2.0 * width * width))
                if target is None:
                    base = (box[0] + box[1]) / 2.0
            else:
                theta = theta_free if target is None else theta_free * 4
            x = _reflect(prev + gap + g.gauss(0, sd) - theta * (prev + gap - base), wall)
            gaps[i], closes[i] = gap, x
            prev = x
        if target is not None:
            miss = target - closes[last]
            for i in range(seg_start, last + 1):
                closes[i] = _reflect(closes[i] + miss * (i - seg_start + 1) / float(last - seg_start + 1), wall)
            closes[last] = target
            prev = target
        seg_start = last + 1
    return _bars(sym, grid, closes, gaps, first_open, bounds, sd, g)


def _bars(sym, grid, closes, gaps, first_open, bounds, sd, g):
    """OHLC bars from closes and gaps: wicks seeded, prices kept inside bound windows and
    over 0, rounded to the Yahoo decimals. Volume 0 (story.py fills futures volume)."""
    p = sym["places"]
    tick = 10 ** -p
    wall = band(sym)
    out = []
    prev = first_open
    for i, (t, _) in enumerate(grid):
        o, c = prev + gaps[i], closes[i]
        box = _bound_of(t, bounds)
        if box:
            lo, hi = box[0] + 2 * tick, box[1] - 2 * tick
            o, c = min(max(o, lo), hi), min(max(c, lo), hi)
        h = max(o, c) + abs(g.gauss(0, 0.6 * sd))
        l = min(o, c) - abs(g.gauss(0, 0.6 * sd))
        if box:
            h, l = min(h, box[1] - tick), max(l, box[0] + tick)
        if wall:
            o, c = min(max(o, wall[0]), wall[1]), min(max(c, wall[0]), wall[1])
            h, l = min(h, wall[1]), max(l, wall[0])
        l = max(l, tick)
        o, h, l, c = rnd(o, p), rnd(h, p), rnd(l, p), rnd(c, p)
        out.append([t, o, max(h, o, c), min(l, o, c), c, 0])
        prev = c
    return out


def build():
    """{yahoo symbol: {"sym": row, "1h": bars, "5m": bars or None}}: the walks, before
    the story (story.apply pastes the designed bars, the outage and the volumes)."""
    out = {}
    for sym in symbols():
        y = sym["yahoo"]
        step_1h = slots(sym["data_hours"], H)
        if sym["day"]:
            cut = [s for s in slots(sym["data_hours"], M5, FIVE_MIN_DAY)]
            grid1 = [s for s in step_1h if s[0] < cut[0][0]]
        else:
            cut, grid1 = None, step_1h
        pins, opens, bounds = story.steer(y, "1h")
        bars1 = walk(sym, "1h", grid1, sym["start"], pins, opens, bounds)
        bars5 = None
        if cut:
            pins, opens, bounds = story.steer(y, "5m")
            bars5 = walk(sym, "5m", cut, bars1[-1][4], pins, opens, bounds)
        out[y] = {"sym": sym, "1h": bars1, "5m": bars5}
    return out


SETTLE = {"GC=F": ("ny", "13:30"), "SI=F": ("ny", "13:25"), "PL=F": ("ny", "13:05"), "PA=F": ("ny", "13:00"),
          "HG=F": ("ny", "13:00"), "CL=F": ("ny", "14:30"), "BZ=F": ("london", "19:30"), "NG=F": ("ny", "14:30")}
CONTRACT_FROM = D(2026, 7, 24, 0, 0)    # the two gold contracts are in the sample from here
CONTRACT_TO = D(2026, 7, 31, 20, 0)     # ... to the last bar of the week


def _scaled(b, k, g, places):
    """A bar times k, rounded, with a little own noise (another contract's own trades)."""
    x = [rnd(v * k + g.choice((-0.2, -0.1, 0.0, 0.1, 0.2)), places) for v in b[1:5]]
    return [b[0], x[0], max(x), min(x), x[3], max(1, b[5] // 4)]


def contracts(paths):
    """{"GCQ26.CMX": bars, "GCZ26.CMX": bars} 1h, 07-24 00:00 to 07-31 20:00: the Aug
    contract is GC=F before the roll bar, the Dec contract is GC=F after it (story k, l)."""
    from .story import RATIO, ROLL_BAR, STRAY
    g = rng("contracts")
    aug, dec = [], []
    for b in paths["GC=F"]["1h"]:
        if not (CONTRACT_FROM <= b[0] <= CONTRACT_TO) or b[0] == STRAY:
            continue
        if b[0] < ROLL_BAR:
            aug.append(list(b))
            dec.append(_scaled(b, RATIO, g, 1))
        elif b[0] == ROLL_BAR:
            aug.append([b[0], 2478.2, 2480.1, 2477.9, 2478.3, b[5] // 4])
            dec.append([b[0], 2500.4, 2503.0, 2498.9, 2500.6, b[5]])
        else:
            aug.append(_scaled(b, 1 / RATIO, g, 1))
            dec.append(list(b))
    six = [x for x in dec if x[0] == ROLL_BAR - H][0]
    six[4] = 2500.3
    six[2], six[3] = max(six[1:5]), min(six[1:5])
    return {"GCQ26.CMX": aug, "GCZ26.CMX": dec}


def _settle(by_end, yahoo, d):
    """The made-up settlement of trade date d from 1h closes (by bar end): Friday the
    last close before 17:00 New York, else the mean of the two closes around the settle
    time; None when a bar is missing."""
    if d.weekday() == 4:
        return by_end.get(city_to_utc("ny", at_time(d, "17:00")))
    city, hhmm = SETTLE[yahoo]
    t0 = city_to_utc(city, at_time(d, hhmm)).replace(minute=0)
    a, b = by_end.get(t0), by_end.get(t0 + H)
    return None if a is None or b is None else (a + b) / 2.0


def daily(paths, gold_contracts):
    """{yahoo: 1d bars} for the 8 futures: one bar per CME trade date, stamped 00:00 New
    York; close = the settlement times 1 + gauss(0, 0.0001). Gold's 07-29 and 07-30 bars
    stay on the Aug contract (Yahoo's daily series switches on 07-31)."""
    from .story import STRAY
    out = {}
    for yahoo, p in sorted(paths.items()):
        sym = p["sym"]
        if not sym["futures"]:
            continue
        g = rng("daily", yahoo)
        groups = {}
        for b in p["1h"]:
            if b[4] is not None and b[0] != STRAY:
                groups.setdefault(fx_day(b[0], "17:00 New York"), []).append(b)
        if yahoo == "GC=F":
            for b in gold_contracts["GCQ26.CMX"]:
                d = fx_day(b[0], "17:00 New York")
                if d in ("2026-07-29", "2026-07-30"):
                    groups[d] = [x for x in groups[d] if x[0] != b[0]] + [b]
        rows = []
        for d in sorted(groups):
            bars = sorted(groups[d], key=lambda x: x[0])
            day = datetime.date.fromisoformat(d)
            by_end = dict((x[0] + H, x[4]) for x in bars)
            s = _settle(by_end, yahoo, day)
            close = rnd((s if s is not None else bars[-1][4]) * (1 + g.gauss(0, 0.0001)), sym["places"])
            hi = max([x[2] for x in bars] + [close])
            lo = min([x[3] for x in bars] + [close])
            rows.append([city_to_utc("ny", at_time(day, "00:00")), bars[0][1], hi, lo, close,
                         sum(x[5] for x in bars)])
        out[yahoo] = rows
    return out
