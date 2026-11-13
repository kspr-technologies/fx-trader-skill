"""The designed events of the sample market (06 5.4, rows a to o). All made up.

paths.build reads PINS, OPENS and BOUNDS (through steer) so the walk lands on the
designed levels; apply then pastes the designed bars, the futures volumes, the seeded
null rows, the 1h bars built from 5m, the stray CME-break bar and the outage.
"""

import datetime
import math

from .util import D, H, M5, rnd, rng


def T(text):
    """ "2026-07-17 03:50" -> naive UTC datetime."""
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")


def _flat(p):
    """A bar with open = high = low = close."""
    return (p, p, p, p)


# closes pinned at a bar start (the walk is bridged to hit them exactly)
PINS = {
    ("GC=F", "1h"): {T("2026-05-12 19:00"): 2466.0, T("2026-06-12 20:00"): 2452.0},
    ("GC=F", "5m"): {T("2026-07-08 14:25"): 2414.0, T("2026-07-08 14:30"): 2415.2,
                     T("2026-07-15 20:55"): 2409.0, T("2026-07-16 20:55"): 2431.6,
                     T("2026-07-17 03:50"): 2433.4, T("2026-07-17 04:10"): 2433.4,
                     T("2026-07-17 20:55"): 2441.0, T("2026-07-29 06:55"): 2478.0,
                     T("2026-07-29 07:55"): 2500.6, T("2026-07-30 14:30"): 2509.6,
                     T("2026-07-30 14:35"): 2511.8, T("2026-07-23 06:55"): 2450.0,
                     T("2026-07-23 07:10"): 2450.2, T("2026-07-23 07:35"): 2450.2},
    ("GBPUSD=X", "5m"): {T("2026-07-23 06:55"): 1.24220, T("2026-07-23 07:10"): 1.24230,
                         T("2026-07-23 07:35"): 1.24230},
    ("USDJPY=X", "5m"): {T("2026-07-23 06:55"): 144.380, T("2026-07-23 07:10"): 144.390,
                         T("2026-07-23 07:35"): 144.390},
    ("EURUSD=X", "5m"): {T("2026-07-23 06:55"): 1.08520, T("2026-07-23 07:35"): 1.08452,
                         T("2026-07-23 10:10"): 1.08295, T("2026-07-23 10:15"): 1.08262,
                         T("2026-07-23 12:10"): 1.08300, T("2026-07-23 12:15"): 1.08950},
}

# designed opens after a weekend (story b and e)
OPENS = {
    ("GC=F", "1h"): {T("2026-06-14 22:00"): 2412.8},     # 1.6% under Friday's 2,452.0
    ("GC=F", "5m"): {T("2026-07-19 22:00"): 2458.0},     # +0.70% over Friday's 2,441.0
}

# (from, to, low, high): every price of the bars that start in [from, to] stays inside
BOUNDS = {
    ("GC=F", "1h"): [(T("2026-06-14 22:00"), T("2026-06-21 21:00"), 2362.0, 2416.5)],
    ("GC=F", "5m"): [(T("2026-06-21 22:00"), T("2026-07-15 20:55"), 2362.0, 2416.5),
                     (T("2026-07-15 21:00"), T("2026-07-16 20:55"), 2398.0, 2440.0),
                     (T("2026-07-16 21:00"), T("2026-07-17 03:50"), 2418.0, 2448.0),
                     (T("2026-07-17 04:15"), T("2026-07-29 06:55"), 2405.0, 2485.0),
                     (T("2026-07-29 08:00"), T("2026-07-30 14:30"), 2425.0, 2511.0),
                     (T("2026-07-30 14:40"), T("2026-07-31 21:00"), 2425.0, 2570.0)],
    ("EURUSD=X", "5m"): [(T("2026-07-23 07:40"), T("2026-07-23 10:10"), 1.08245, 1.08540)],
    # the other day symbols on 07-23: closes stay deep inside the London range (no day hit)
    ("GBPUSD=X", "5m"): [(T("2026-07-23 07:15"), T("2026-07-23 07:35"), 1.24170, 1.24290)],
    ("USDJPY=X", "5m"): [(T("2026-07-23 07:15"), T("2026-07-23 07:35"), 144.340, 144.430)],
}
BOUNDS[("GC=F", "5m")].append((T("2026-07-23 07:15"), T("2026-07-23 07:35"), 2447.6, 2452.8))

# the designed bars (open, high, low, close), pasted over the walk
BARS = {
    ("GC=F", "5m"): {
        T("2026-07-08 14:30"): (2414.0, 2418.4, 2413.6, 2415.2),     # c: the 20-day high
        T("2026-07-17 03:55"): _flat(2433.4), T("2026-07-17 04:00"): _flat(2433.4),
        T("2026-07-17 04:05"): _flat(2433.4), T("2026-07-17 04:10"): _flat(2433.4),   # d
        # l: the mixed hour of the roll (Aug to Dec inside the 07:00 bar)
        T("2026-07-29 07:00"): (2478.2, 2478.9, 2477.9, 2478.6),
        T("2026-07-29 07:05"): (2478.6, 2479.3, 2478.1, 2478.9),
        T("2026-07-29 07:10"): (2478.9, 2479.4, 2478.3, 2479.1),
        T("2026-07-29 07:15"): (2479.1, 2479.6, 2478.5, 2478.8),
        T("2026-07-29 07:20"): (2478.8, 2500.9, 2478.6, 2500.2),
        T("2026-07-29 07:25"): (2500.2, 2501.4, 2499.6, 2501.0),
        T("2026-07-29 07:30"): (2501.0, 2502.2, 2500.4, 2501.8),
        T("2026-07-29 07:35"): (2501.8, 2503.0, 2501.1, 2502.4),
        T("2026-07-29 07:40"): (2502.4, 2502.8, 2501.3, 2501.6),
        T("2026-07-29 07:45"): (2501.6, 2502.1, 2500.5, 2500.9),
        T("2026-07-29 07:50"): (2500.9, 2501.5, 2499.9, 2501.2),
        T("2026-07-29 07:55"): (2501.2, 2501.6, 2500.3, 2500.6),
        T("2026-07-30 14:35"): (2509.6, 2513.0, 2508.9, 2511.8),     # n: the rebased target
        T("2026-07-16 15:40"): (2434.3, 2441.2, 2433.0, 2433.3),     # c: the breakout day's high (ATR 28.0)
        T("2026-07-23 07:00"): (2450.0, 2455.8, 2449.2, 2453.1),     # a wide London range, no day hit
        T("2026-07-23 07:05"): (2453.1, 2453.9, 2444.6, 2446.0),
        T("2026-07-23 07:10"): (2446.0, 2450.9, 2445.3, 2450.2),
    },
    ("GBPUSD=X", "5m"): {
        T("2026-07-23 07:00"): (1.24220, 1.24380, 1.24190, 1.24300),
        T("2026-07-23 07:05"): (1.24300, 1.24330, 1.24080, 1.24150),
        T("2026-07-23 07:10"): (1.24150, 1.24260, 1.24120, 1.24230),
    },
    ("USDJPY=X", "5m"): {
        T("2026-07-23 07:00"): (144.380, 144.560, 144.350, 144.470),
        T("2026-07-23 07:05"): (144.470, 144.500, 144.210, 144.300),
        T("2026-07-23 07:10"): (144.300, 144.420, 144.260, 144.390),
    },
    ("EURUSD=X", "5m"): {
        T("2026-07-23 07:00"): (1.08520, 1.08640, 1.08500, 1.08610),  # g: the London range
        T("2026-07-23 07:05"): (1.08610, 1.08630, 1.08480, 1.08510),
        T("2026-07-23 07:10"): (1.08510, 1.08580, 1.08490, 1.08550),
        T("2026-07-23 07:15"): (1.08550, 1.08590, 1.08510, 1.08530),  # h: closes in the range
        T("2026-07-23 07:20"): (1.08530, 1.08545, 1.08488, 1.08500),
        T("2026-07-23 07:25"): (1.08500, 1.08520, 1.08483, 1.08490),
        T("2026-07-23 07:30"): (1.08490, 1.08505, 1.08481, 1.08485),
        T("2026-07-23 07:35"): (1.08485, 1.08495, 1.08440, 1.08452),  # h: the breakdown close
        T("2026-07-23 10:15"): (1.08295, 1.08310, 1.08221, 1.08262),  # i: the sell's target bar
        T("2026-07-23 12:15"): (1.08300, 1.08990, 1.08290, 1.08950),  # j: the ECB bar
    },
}

OUTAGE = (T("2026-07-21 02:00"), T("2026-07-21 12:00"))   # f: no row at all, every symbol
STRAY = T("2026-07-13 21:00")                             # o: a GC_F 1h bar in the CME break
ROLL_BAR = T("2026-07-29 07:00")                          # l: the first bar on the Dec contract
RATIO = 2500.3 / 2478.0                                   # k: the exact roll ratio
NULL_EVERY = 200                                          # about 1 all-null row in 200

# no null row here (the designed days and their edges stay whole)
PROTECT = [(T(a), T(b)) for a, b in (
    ("2026-06-12 18:00", "2026-06-15 02:00"), ("2026-07-02 18:00", "2026-07-06 02:00"),
    ("2026-07-07 20:00", "2026-07-09 00:00"), ("2026-07-13 18:00", "2026-07-14 02:00"),
    ("2026-07-15 18:00", "2026-07-17 08:00"), ("2026-07-17 18:00", "2026-07-20 00:00"),
    ("2026-07-20 22:00", "2026-07-21 14:00"), ("2026-07-23 05:00", "2026-07-23 14:00"),
    ("2026-07-29 04:00", "2026-07-29 09:00"), ("2026-07-30 12:00", "2026-07-30 16:00"),
    ("2026-07-31 18:00", "2026-07-31 21:00"))]

# futures volume per 1h bar (a seeded log-normal count around this)
VOLUME = {"GC=F": 9000, "SI=F": 3000, "PL=F": 800, "PA=F": 300, "HG=F": 2500,
          "CL=F": 15000, "BZ=F": 6000, "NG=F": 7000}


def steer(yahoo, interval):
    """(pins, opens, bounds) of one series for paths.walk."""
    key = (yahoo, interval)
    return PINS.get(key, {}), OPENS.get(key, {}), BOUNDS.get(key, [])


def _paste(bars, designed):
    """Write the designed (o, h, l, c) over the bars with the same start."""
    for b in bars:
        if b[0] in designed:
            b[1:5] = list(designed[b[0]])


def _protected(t):
    """True when a null row must not be put at t."""
    return any(a <= t < b for a, b in PROTECT)


def _volumes(series, yahoo, interval, after_break):
    """Seeded log-normal futures volume; 0 on the first bar after a break (02 2.4 rule 6)."""
    g = rng("volume", yahoo, interval)
    base = VOLUME[yahoo] / (12.0 if interval == "5m" else 1.0)
    for b in series:
        v = int(round(math.exp(g.gauss(math.log(base), 0.45))))
        b[5] = 0 if b[0] in after_break else max(1, v)


def _nulls(series, yahoo, interval):
    """About 1 bar in NULL_EVERY becomes an all-null row (Yahoo's no-trade slots)."""
    g = rng("null", yahoo, interval)
    for b in series:
        if g.randrange(NULL_EVERY) == 0 and not _protected(b[0]):
            b[1:6] = [None] * 5


def hourly_from_5m(bars5):
    """1h bars from 5m bars: open, max high, min low, close, sum of volume (null rows skipped)."""
    groups = {}
    for b in bars5:
        if b[4] is None:
            continue
        hour = b[0].replace(minute=0)
        groups.setdefault(hour, []).append(b)
    out = []
    for hour in sorted(groups):
        g = groups[hour]
        out.append([hour, g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4],
                    sum(x[5] for x in g)])
    return out


def apply(paths):
    """Paste the story over the walks, in place: designed bars, volumes, null rows, the
    1h bars of the day symbols built from 5m, the stray GC_F bar (o) and the outage (f)."""
    from .util import slots
    for yahoo, p in sorted(paths.items()):
        sym = p["sym"]
        for interval in ("1h", "5m"):
            if p[interval]:
                _paste(p[interval], BARS.get((yahoo, interval), {}))
        if sym["futures"]:
            for interval, step in (("1h", H), ("5m", M5)):
                if p[interval]:
                    brk = set(t for t, after in slots(sym["data_hours"], step) if after)
                    _volumes(p[interval], yahoo, interval, brk)
        for interval in ("1h", "5m"):
            if p[interval]:
                _nulls(p[interval], yahoo, interval)
        if p["5m"]:
            p["1h"] = p["1h"] + hourly_from_5m(p["5m"])
        if yahoo == "GC=F":
            before = [b for b in p["1h"] if b[0] == STRAY - H][0]
            p["1h"].append([STRAY] + [before[4]] * 4 + [1])
            p["1h"].sort(key=lambda b: b[0])
        for interval in ("1h", "5m"):
            if p[interval]:
                p[interval] = [b for b in p[interval] if not (OUTAGE[0] <= b[0] < OUTAGE[1])]
    return paths
