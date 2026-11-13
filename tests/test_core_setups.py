"""Tests for the indicators and setups groups of fx_core (part 05 sections 1, 2, 3, 5.5,
5.6). Offline only. Every bar list here is small and made up by hand."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import datetime
import unittest

import fx_core as fx

D = datetime.datetime
H = datetime.timedelta(hours=1)
M5 = datetime.timedelta(minutes=5)

if not hasattr(fx, "COARSE"):   # part 02's constant; set here until 40_data is joined
    fx.COARSE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}


def utc(text):
    """ "2026-11-11 06:30" -> naive datetime (test shorthand)."""
    return D.strptime(text, "%Y-%m-%d %H:%M")


def bar(start, o, h, l, c, flags=None, **kw):
    """One bar dict as part 02 makes them."""
    b = {"start": start, "open": o, "high": h, "low": l, "close": c, "volume": 0}
    if flags is not None:
        b["flags"] = list(flags)
    b.update(kw)
    return b


# Spec 05 1.7: six EURUSD daily bars (high, low, close).
HLC = [(1.1000, 1.0950, 1.0980), (1.1010, 1.0960, 1.0990), (1.1030, 1.0970, 1.1020),
       (1.1025, 1.0990, 1.1000), (1.1060, 1.1000, 1.1050), (1.1055, 1.1015, 1.1030)]


def six_days():
    start = utc("2026-11-02 22:00")
    return [bar(start + i * 24 * H, c, h, l, c, flags=[]) for i, (h, l, c) in enumerate(HLC)]


class Usable(unittest.TestCase):
    def test_drop_and_gap(self):
        bars = six_days()
        bars[2]["flags"] = ["roll"]
        bars[4]["flags"] = ["late_start", "early_close"]
        u = fx.usable_bars(bars)
        self.assertEqual(len(u), 5)
        self.assertEqual([b["after_gap"] for b in u], [True, False, True, False, False])
        self.assertNotIn("after_gap", bars[0])           # the input is not changed
        self.assertIsNot(u[0], bars[0])

    def test_each_skip_flag(self):
        for flag in ("roll", "outage", "partial", "holiday", "forming"):
            bars = six_days()
            bars[5]["flags"] = [flag]
            self.assertEqual(len(fx.usable_bars(bars)), 5, flag)

    def test_outage_end_and_no_flags_key(self):
        start = utc("2026-11-11 08:00")
        bars = [bar(start + i * M5, 1, 1.1, 0.9, 1) for i in range(4)]   # 5m: no flags key
        u = fx.usable_bars(bars, [(start, start + 2 * M5)])
        self.assertEqual([b["after_gap"] for b in u], [True, False, True, False])

    def test_constants(self):
        self.assertEqual(fx.SKIP_FLAGS, ("roll", "outage", "partial", "holiday", "forming"))
        self.assertEqual(fx.ATR_WINDOW, {"1d": 250, "1h": 120, "5m": 288})


class Indicators(unittest.TestCase):
    def test_true_range(self):
        b = {"high": 1.1010, "low": 1.0960, "close": 1.0990}
        self.assertAlmostEqual(fx.true_range(b, 1.0980), 0.0050, places=10)
        self.assertAlmostEqual(fx.true_range(b, 1.1100), 0.0140, places=10)
        self.assertAlmostEqual(fx.true_range(b, None), 0.0050, places=10)
        b["after_gap"] = True
        self.assertAlmostEqual(fx.true_range(b, 1.1100), 0.0050, places=10)

    def test_worked_example(self):
        u = fx.usable_bars(six_days())
        closes = [b["close"] for b in u]
        self.assertAlmostEqual(fx.atr_wilder(u[:4], 3), 0.0048333, places=7)
        self.assertAlmostEqual(fx.atr_wilder(u[:5], 3), 0.0052222, places=7)
        self.assertAlmostEqual(fx.atr_wilder(u, 3), 0.0048148, places=7)
        self.assertAlmostEqual(fx.sma(closes, 3), 1.1026667, places=7)
        self.assertAlmostEqual(fx.ema(closes, 3), 1.1027083, places=7)
        self.assertEqual(round(fx.rsi_wilder(closes, 3), 2), 63.89)
        self.assertAlmostEqual(fx.highest(u, 3), 1.1060, places=10)
        self.assertAlmostEqual(fx.lowest(u, 3), 1.0990, places=10)
        self.assertAlmostEqual(fx.highest(u, 2, "close"), 1.1050, places=10)

    def test_atr_gap_and_warmup(self):
        u = fx.usable_bars(six_days())
        self.assertIsNone(fx.atr_wilder(u[:3], 3))       # needs n + 1 bars
        u[1]["after_gap"] = True                          # TR of bar 2 is just high - low
        self.assertAlmostEqual(fx.atr_wilder(u[:4], 3), (0.0050 + 0.0060 + 0.0035) / 3, places=10)
        flat = [bar(utc("2026-11-02 22:00") + i * H, 1, 1, 1, 1) for i in range(5)]
        self.assertIsNone(fx.atr_wilder(fx.usable_bars(flat), 3))   # ATR 0 is None

    def test_warmups(self):
        closes = [b["close"] for b in six_days()]
        self.assertIsNone(fx.sma(closes, 7))
        self.assertIsNone(fx.ema(closes[:5], 3))          # EMA needs 2 x n values
        self.assertIsNone(fx.rsi_wilder(closes[:3], 3))   # RSI needs n + 1 closes
        self.assertIsNone(fx.highest(closes and six_days()[:2], 3))
        self.assertIsNone(fx.ema([], 3))
        self.assertIsNone(fx.rsi_wilder([], 3))

    def test_series_match(self):
        closes = [1 + 0.01 * ((i * 7) % 5) - 0.002 * i for i in range(30)]
        es = fx.ema_series(closes, 4)
        rs = fx.rsi_series(closes, 4)
        self.assertEqual(len(es), 30)
        self.assertEqual(es[:7], [None] * 7)
        self.assertEqual(rs[:4], [None] * 4)
        for i in range(30):
            self.assertEqual(es[i], fx.ema(closes[:i + 1], 4))
            self.assertEqual(rs[i], fx.rsi_wilder(closes[:i + 1], 4))

    def test_rsi_edges(self):
        self.assertEqual(fx.rsi_wilder([1, 2, 3, 4], 3), 100.0)
        self.assertEqual(fx.rsi_wilder([1, 1, 1, 1], 3), 50.0)
        self.assertEqual(fx.rsi_wilder([4, 3, 2, 1], 3), 0.0)



GOLD = {"key": "XAUUSD", "name": "Gold", "kind": "metal", "base": "XAU", "quote": "USD",
        "digits": 2, "point": 0.01, "tick_size": 0.01, "spread_points": 9}
EURUSD = {"key": "EURUSD", "name": "Euro / US dollar", "kind": "fx", "base": "EUR",
          "quote": "USD", "digits": 5, "point": 0.00001, "tick_size": 0.00001,
          "spread_points": 1}


def days(hlc, last_day="2026-11-10", flags=None):
    """Daily bars (high, low, close) on weekdays, the last one on last_day."""
    d = datetime.date.fromisoformat(last_day)
    dates = []
    while len(dates) < len(hlc):
        if d.weekday() < 5:
            dates.append(d)
        d -= datetime.timedelta(days=1)
    dates.reverse()
    out = []
    for i, ((h, l, c), dd) in enumerate(zip(hlc, dates)):
        start = D(dd.year, dd.month, dd.day) - datetime.timedelta(hours=2)
        out.append({"fx_day": dd.isoformat(), "start": start, "end": start + 24 * H,
                    "open": c, "high": h, "low": l, "close": c, "hours": 23,
                    "flags": list((flags or {}).get(i, []))})
    return out


def gold_breakout():
    """Spec 05 2.6: the 20 highs before Tue top out at 4,120.0, Tue closes 4,131.5,
    and every true range is 68.4, so A = 68.4."""
    hlc = [(4114.2, 4045.8, 4080.0)] * 69
    hlc[60] = (4120.0, 4051.6, 4085.8)
    hlc.append((4140.0, 4071.6, 4131.5))
    return days(hlc)


def by_setup(hits):
    return dict((h["setup"], h) for h in hits)


class FindSetups(unittest.TestCase):
    def test_worked_example(self):
        hits = by_setup(fx.find_setups(gold_breakout(), GOLD, {}, ("buy", "sell")))
        h = hits["breakout_20d"]
        self.assertEqual(h["level"], 4120.0)
        self.assertEqual(h["last"], 4131.5)
        self.assertEqual(h["atr"], 68.4)
        self.assertEqual(h["distance_atr"], -0.17)
        self.assertEqual(h["notes"], [])
        self.assertEqual((h["symbol"], h["name"], h["side"], h["style"]),
                         ("XAUUSD", "Gold", "buy", "swing"))
        self.assertEqual((h["atr_interval"], h["opening_range"], h["data_age_min"]),
                         ("1d", None, None))
        self.assertEqual(h["fx_day"], "2026-11-10")
        self.assertIn("breakout_55d", hits)
        self.assertNotIn("breakdown_20d", hits)           # 0.8 ATR away: not near

    def test_sides(self):
        hits = fx.find_setups(gold_breakout(), GOLD, {}, ("buy",))
        self.assertTrue(hits)
        self.assertEqual(set(h["side"] for h in hits), {"buy"})
        self.assertEqual(fx.find_setups(gold_breakout(), GOLD, {}, ()), [])

    def test_order_follows_setups(self):
        hits = fx.find_setups(gold_breakout(), GOLD, {}, ("buy", "sell"))
        names = [s["name"] for s in fx.SETUPS]
        idx = [names.index(h["setup"]) for h in hits]
        self.assertEqual(idx, sorted(idx))

    def test_chase_note(self):
        bars = gold_breakout()
        bars[-1].update(high=4220.0, low=4151.6, close=4210.0)   # far past the level
        h = by_setup(fx.find_setups(bars, GOLD, {}, ("buy",)))["breakout_20d"]
        self.assertLess(h["distance_atr"], -1.0)
        self.assertEqual(h["notes"][0], "The close is already %.2f ATR past the level. "
                         "A late entry is a different trade." % -h["distance_atr"])

    def test_breakdown_sell(self):
        hlc = [(4114.2, 4045.8, 4080.0)] * 69
        hlc[60] = (4108.4, 4040.0, 4074.2)
        hlc.append((4088.4, 4020.0, 4028.5))
        h = by_setup(fx.find_setups(days(hlc), GOLD, {}, ("buy", "sell")))["breakdown_20d"]
        self.assertEqual((h["side"], h["level"], h["last"]), ("sell", 4040.0, 4028.5))
        self.assertEqual(h["distance_atr"], round((4028.5 - 4040.0) / 68.4, 2))

    def test_roll_bar_left_out(self):
        bars = gold_breakout()
        bars[65].update(high=4400.0, flags=["roll"])          # a jump that is a roll
        bars[66]["flags"] = ["outage"]
        h = by_setup(fx.find_setups(bars, GOLD, {}, ("buy",)))["breakout_20d"]
        self.assertEqual(h["level"], 4120.0)

    def test_errors(self):
        with self.assertRaises(ValueError) as e:
            fx.find_setups([], GOLD, {}, ("buy",))
        self.assertEqual(str(e.exception), "No daily prices for Gold yet.")
        bars = gold_breakout()
        bars[-1]["flags"] = ["outage"]
        with self.assertRaises(ValueError) as e:
            fx.find_setups(bars, GOLD, {}, ("buy",))
        self.assertEqual(str(e.exception), "The newest Gold day (Tue 2026-11-10) is not usable "
                         "on Yahoo (outage). No setups today.")
        with self.assertRaises(ValueError) as e:
            fx.find_setups(gold_breakout()[-41:], GOLD, {}, ("buy",))
        self.assertEqual(str(e.exception), "Only 41 usable daily bars for Gold (need 60).")
        flat = days([(1.0, 1.0, 1.0)] * 70)
        with self.assertRaises(ValueError) as e:
            fx.find_setups(flat, GOLD, {}, ("buy",))
        self.assertEqual(str(e.exception), "Not enough bars for the ATR of Gold.")

    def test_skipped_bars_count(self):
        bars = gold_breakout()[-62:]
        bars[3]["flags"] = ["partial"]
        bars[4]["flags"] = ["roll"]
        bars[5]["flags"] = ["holiday"]
        with self.assertRaises(ValueError) as e:
            fx.find_setups(bars, GOLD, {}, ("buy",))
        self.assertIn("Only 59 usable", str(e.exception))
        bars[5]["flags"] = ["late_start", "early_close"]     # kept
        fx.find_setups(bars, GOLD, {}, ("buy",))

    def test_forming_bar_dropped(self):
        bars = gold_breakout()
        extra = dict(bars[-1], fx_day="2026-11-11", flags=["forming"], close=1.0)
        hits = by_setup(fx.find_setups(bars + [extra], GOLD, {}, ("buy",)))
        self.assertEqual(hits["breakout_20d"]["fx_day"], "2026-11-10")


def trend(n, start, step, half=0.5):
    """n bars whose close moves by step each day, range 2 x half."""
    return [(start + i * step + half, start + i * step - half, start + i * step) for i in range(n)]


def mirror(hlc, top=400.0):
    """The same bars upside down (a buy pattern becomes its sell twin)."""
    return [(top - l, top - h, top - c) for h, l, c in hlc]


def flag_bars():
    """55 flat days, an 11-day pole up 1.0 a day, then a 5-day tight flag."""
    hlc = [(100.5, 99.5, 100.0)] * 55 + trend(11, 101.0, 1.0)
    hlc += [(111.0, 110.0, 110.5)] * 5
    return hlc


class Patterns(unittest.TestCase):
    def test_bull_and_bear_flag(self):
        h = by_setup(fx.find_setups(days(flag_bars()), GOLD, {}, ("buy", "sell")))["bull_flag"]
        self.assertEqual((h["side"], h["level"], h["last"]), ("buy", 111.0, 110.5))
        h = by_setup(fx.find_setups(days(mirror(flag_bars())), GOLD, {}, ("sell",)))["bear_flag"]
        self.assertEqual((h["side"], h["level"]), ("sell", 289.0))

    def test_flag_too_wide(self):
        hlc = flag_bars()
        hlc[-3] = (113.0, 110.0, 110.5)                    # flag span 3.0 > 1.5 ATR
        self.assertNotIn("bull_flag", by_setup(fx.find_setups(days(hlc), GOLD, {}, ("buy",))))

    def test_inside_day_both_sides(self):
        hlc = [(101.0, 99.0, 100.0)] * 69 + [(100.5, 99.8, 100.1)]
        hits = by_setup(fx.find_setups(days(hlc), GOLD, {}, ("buy", "sell")))
        self.assertEqual(hits["inside_break_up"]["level"], 100.5)
        self.assertEqual(hits["inside_break_down"]["level"], 99.8)
        hlc[-5] = (100.4, 99.9, 100.1)                     # a narrower bar in the last 7
        hits = by_setup(fx.find_setups(days(hlc), GOLD, {}, ("buy", "sell")))
        self.assertNotIn("inside_break_up", hits)

    def test_oversold_and_overbought(self):
        hlc = trend(69, 200.0, -1.0) + [(133.0, 132.0, 132.5)]
        h = by_setup(fx.find_setups(days(hlc), GOLD, {}, ("buy",)))["oversold_bounce"]
        self.assertEqual(h["level"], 133.0)
        self.assertIn("This trades against the trend.", h["notes"])
        h = by_setup(fx.find_setups(days(mirror(hlc)), GOLD, {}, ("sell",)))["overbought_drop"]
        self.assertEqual(h["level"], 267.0)
        self.assertIn("This trades against the trend.", h["notes"])

    def test_reclaim_and_lose_ma20(self):
        hlc = [(100.5 + (i % 2), 99.5 - (i % 2), 100.0 - (i % 2) * 0.5) for i in range(69)]
        hlc[-1] = (100.0, 98.0, 99.0)
        hlc.append((101.5, 99.5, 101.0))
        bars = days(hlc)
        closes = [b["close"] for b in bars]
        h = by_setup(fx.find_setups(bars, GOLD, {}, ("buy",)))["reclaim_ma20"]
        self.assertEqual(h["level"], round(fx.sma(closes, 20), 2))
        h = by_setup(fx.find_setups(days(mirror(hlc)), GOLD, {}, ("sell",)))["lose_ma20"]
        self.assertEqual(h["level"], round(400.0 - fx.sma(closes, 20), 2))

    def test_pullback_and_rally(self):
        hlc = trend(109, 100.0, 0.5, half=1.0)
        c = hlc[-1][2]
        hlc.append((c + 1.0, c - 8.0, c))
        closes = [x[2] for x in hlc]
        e20 = fx.ema(closes, 20)
        self.assertTrue(c - 8.0 <= e20 < c)
        h = by_setup(fx.find_setups(days(hlc), GOLD, {}, ("buy",)))["pullback_ema20"]
        self.assertEqual(h["level"], round(c + 1.0, 2))
        h = by_setup(fx.find_setups(days(mirror(hlc)), GOLD, {}, ("sell",)))["rally_ema20"]
        self.assertEqual(h["level"], round(400.0 - c - 1.0, 2))
        short = days(hlc[-99:])                             # under 100 bars: not tested
        self.assertNotIn("pullback_ema20", by_setup(fx.find_setups(short, GOLD, {}, ("buy",))))

    def test_coarse_note(self):
        hlc = [(1.1000 + 0.0002 * (i % 3), 1.0950, 1.0975) for i in range(69)]
        hlc.append((1.1010, 1.0960, 1.1003))
        hits = fx.find_setups(days(hlc), EURUSD, {}, ("buy", "sell"))
        self.assertTrue(hits)
        for h in hits:
            self.assertIn("Coarse data: Yahoo EURUSD moves in steps of about 1.25 pips.", h["notes"])
        self.assertEqual(fx.coarse_words({"key": "GBPUSD"}),
                         "Coarse data: Yahoo GBPUSD shows about one price a minute.")
        self.assertIsNone(fx.coarse_words({"key": "USDJPY"}))
        self.assertIsNone(fx.coarse_words(GOLD))

    def test_setups_table(self):
        names = [s["name"] for s in fx.SETUPS]
        self.assertEqual(len(names), 16)
        self.assertEqual(names[:2], ["breakout_20d", "breakdown_20d"])
        self.assertEqual(names[-2:], ["or_breakout", "or_breakdown"])
        for s in fx.SETUPS:
            self.assertEqual(set(s), {"name", "side", "style", "words", "min_bars", "listed", "stop"})
            self.assertIn(s["listed"], ("near", "pattern"))
        near = [s["name"] for s in fx.SETUPS if s["listed"] == "near" and s["style"] == "swing"]
        self.assertEqual(near, ["breakout_20d", "breakdown_20d", "breakout_55d", "breakdown_55d"])
        self.assertEqual(fx.MIN_STOP_ATR, {"swing": 0.5, "day": 1.0})
        self.assertEqual((fx.NEAR_ATR, fx.CHASE_ATR, fx.MIN_DAILY_BARS, fx.SWING_VALID_DAYS),
                         (0.5, 1.0, 60, 3))
        self.assertIn("recommend", fx.SIGNAL_BANNED)


def five_min(start, rows, flags=None):
    """5m bars from (high, low, close) rows, the first at start."""
    out = []
    for i, (h, l, c) in enumerate(rows):
        b = bar(start + i * M5, c, h, l, c)
        if flags and i in flags:
            b["flags"] = list(flags[i])
        out.append(b)
    return out


def eurusd_day():
    """Spec 05 3.5: Wed 2026-11-11, London range 08:00 to 08:15 UTC, then the 08:35 bar
    closes 1.12395 under the range low 1.12410."""
    rows = [(1.12460, 1.12440, 1.12450)] * 6                     # 07:30 to 07:55
    rows += [(1.12530, 1.12460, 1.12500), (1.12510, 1.12410, 1.12430),
             (1.12480, 1.12420, 1.12470)]                         # 08:00 to 08:10: the range
    rows += [(1.12480, 1.12415, 1.12425)] * 4                     # 08:15 to 08:30: inside
    rows += [(1.12430, 1.12390, 1.12395), (1.12400, 1.12350, 1.12360)]   # 08:35, 08:40
    return five_min(utc("2026-11-11 07:30"), rows)


class OpeningRange(unittest.TestCase):
    def test_worked_example(self):
        b5 = fx.usable_bars(eurusd_day())
        or_ = fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD)
        self.assertEqual(or_, {"session": "london", "start": "2026-11-11T08:00:00Z",
                               "end": "2026-11-11T08:15:00Z", "high": 1.1253, "low": 1.1241,
                               "mid": 1.1247, "bars": 3, "of": 3})
        t = fx.or_trigger(b5, or_, "sell", utc("2026-11-11 08:15"), utc("2026-11-11 09:00"))
        self.assertEqual((t["start"], t["close"]), (utc("2026-11-11 08:35"), 1.12395))
        self.assertEqual(t["start"] + M5, utc("2026-11-11 08:40"))   # triggered_at
        self.assertIsNone(fx.or_trigger(b5, or_, "buy", None, utc("2026-11-11 09:00")))
        self.assertEqual(round((1.12395 - 1.12410) / 0.00028, 2), -0.54)

    def test_mid_ties(self):
        b5 = five_min(utc("2026-11-11 08:00"), [(1.12531, 1.12410, 1.125)] * 3)
        self.assertEqual(fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD)["mid"], 1.12471)
        # Yahoo gold shows 1 decimal: the mid still goes to the 0.01 tick (05 3.1)
        g = five_min(utc("2026-11-11 08:00"), [(4126.2, 4120.1, 4125.0)] * 3)
        self.assertEqual(fx.opening_range(g, "london", "2026-11-11", 15, GOLD)["mid"], 4123.15)
        with self.assertRaises(TypeError):
            fx.opening_range(g, "london", "2026-11-11", 15)   # inst is required
        g = five_min(utc("2026-11-11 08:00"), [(4134.25, 4134.10, 4134.2)])
        self.assertEqual(fx.opening_range(g, "london", "2026-11-11", 5, GOLD)["mid"], 4134.18)

    def test_until_and_after(self):
        b5 = eurusd_day()
        or_ = fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD)
        # the 08:35 bar ends 08:40: not done by 08:39, so no trigger yet
        self.assertIsNone(fx.or_trigger(b5, or_, "sell", None, utc("2026-11-11 08:39")))
        t = fx.or_trigger(b5, or_, "sell", None, utc("2026-11-11 08:40"))
        self.assertEqual(t["start"], utc("2026-11-11 08:35"))
        t = fx.or_trigger(b5, or_, "sell", utc("2026-11-11 08:40"), utc("2026-11-11 09:00"))
        self.assertEqual(t["start"], utc("2026-11-11 08:40"))   # after_utc moves the start
        t = fx.or_trigger(list(reversed(b5)), or_, "sell", None, utc("2026-11-11 09:00"))
        self.assertEqual(t["start"], utc("2026-11-11 08:35"))   # time order, any input order

    def test_equal_close_is_not_beyond(self):
        b5 = eurusd_day()
        b5[13].update(close=1.12410)
        b5[14].update(close=1.12410)
        or_ = fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD)
        self.assertIsNone(fx.or_trigger(b5, or_, "sell", None, utc("2026-11-11 09:00")))

    def test_roll_and_outage_bars(self):
        b5 = eurusd_day()
        b5[13]["flags"] = ["roll"]                      # the 08:35 bar holds a roll
        or_ = fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD)
        t = fx.or_trigger(b5, or_, "sell", None, utc("2026-11-11 09:00"))
        self.assertEqual(t["start"], utc("2026-11-11 08:40"))
        b5 = eurusd_day()
        u = fx.usable_bars(b5, [(utc("2026-11-11 08:30"), utc("2026-11-11 08:35"))])
        t = fx.or_trigger(u, or_, "sell", None, utc("2026-11-11 09:00"))
        self.assertEqual(t["start"], utc("2026-11-11 08:40"))   # first price after a hole
        u = fx.usable_bars([dict(b, flags=["roll"]) if i == 13 else b for i, b in enumerate(b5)])
        self.assertTrue(u[13]["after_gap"])
        self.assertEqual(fx.or_trigger(u, or_, "sell", None, utc("2026-11-11 09:00")), None)

    def test_incomplete_range(self):
        b5 = eurusd_day()
        b5[7]["flags"] = ["roll"]                        # a roll bar does not count
        or_ = fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD)
        self.assertEqual((or_["bars"], or_["of"], or_["high"], or_["low"]), (2, 3, 1.1253, 1.1242))
        with self.assertRaises(ValueError) as e:
            fx.or_trigger(b5, or_, "sell", None, utc("2026-11-11 09:00"))
        self.assertEqual(str(e.exception), "The opening range is not complete.")
        del b5[6:9]
        self.assertIsNone(fx.opening_range(b5, "london", "2026-11-11", 15, EURUSD))

    def test_minutes(self):
        with self.assertRaises(ValueError) as e:
            fx.opening_range(eurusd_day(), "london", "2026-11-11", 17, EURUSD)
        self.assertEqual(str(e.exception), "The opening range must be a whole number "
                         "of 5-minute bars (got 17 minutes).")

    def test_across_dst(self):
        # UK clocks go forward on Sun 2026-03-29: 08:00 London is 08:00 UTC on Fri,
        # 07:00 UTC on Mon. Bars at both hours; each day takes only its own.
        for day, hour in (("2026-03-27", "08:00"), ("2026-03-30", "07:00")):
            other = "07:00" if hour == "08:00" else "08:00"
            b5 = five_min(utc(day + " " + other), [(2.0, 1.9, 1.95)] * 3)
            b5 += five_min(utc(day + " " + hour), [(1.5, 1.4, 1.45)] * 3)
            b5.sort(key=lambda b: b["start"])
            or_ = fx.opening_range(b5, "london", day, 15, EURUSD)
            self.assertEqual(or_["start"], day + "T" + hour + ":00Z")
            self.assertEqual((or_["high"], or_["low"], or_["bars"]), (1.5, 1.4, 3))
        # New York: US clocks go forward on Sun 2026-03-08 (08:00 NY = 13:00, then 12:00 UTC)
        for day, hour in (("2026-03-06", "13:00"), ("2026-03-09", "12:00")):
            b5 = five_min(utc(day + " " + hour), [(1.5, 1.4, 1.45)] * 3)
            self.assertEqual(fx.opening_range(b5, "new_york", day, 15, EURUSD)["start"],
                             day + "T" + hour + ":00Z")


class GroupsAndLegs(unittest.TestCase):
    def test_group_of(self):
        cfg = {"instruments": {"EURUSD": {}, "XAUUSD": {"group": "gold_only"}, "XYZ": {}}}
        self.assertEqual(fx.group_of("EURUSD", cfg), "usd_majors")
        self.assertEqual(fx.group_of("XAUUSD", cfg), "gold_only")   # the column wins
        self.assertEqual(fx.group_of("XYZ", cfg), "XYZ")             # a group of one
        self.assertEqual(fx.group_of("USOIL", {}), "energy")
        self.assertEqual(fx.group_of("COPPER", None), "industrial")
        keys = [k for ks in fx.CORRELATION_GROUPS.values() for k in ks]
        self.assertEqual(len(keys), 20)
        self.assertEqual(len(set(keys)), 20)

    def test_legs(self):
        usdjpy = {"kind": "fx", "base": "USD", "quote": "JPY"}
        oil = {"kind": "energy", "base": "WTI", "quote": "USD"}
        self.assertEqual(fx.currency_legs("EURUSD", "buy", EURUSD), {"EUR": 1, "USD": -1})
        self.assertEqual(fx.currency_legs("USDJPY", "sell", usdjpy), {"USD": -1, "JPY": 1})
        self.assertEqual(fx.currency_legs("XAUUSD", "buy", GOLD), {"XAU": 1, "USD": -1})
        self.assertEqual(fx.currency_legs("XAUUSD", "sell", GOLD), {"XAU": -1, "USD": 1})
        self.assertEqual(fx.currency_legs("USOIL", "buy", oil), {"WTI": 1})
        with self.assertRaises(ValueError):
            fx.currency_legs("EURUSD", "long", EURUSD)
        with self.assertRaises(ValueError):
            fx.currency_legs("X", "buy", {"kind": "bond", "base": "X"})

    def test_r19_example(self):
        # 05 5.6: open buy EURUSD ($100) and buy XAUUSD ($90), new buy GBPUSD ($95)
        gbp = {"kind": "fx", "base": "GBP", "quote": "USD"}
        trades = [("EURUSD", EURUSD, 100.0), ("XAUUSD", GOLD, 90.0), ("GBPUSD", gbp, 95.0)]
        short_usd = sum(m for k, i, m in trades if fx.currency_legs(k, "buy", i).get("USD") == -1)
        self.assertEqual(short_usd, 285.0)
        cfg = {"instruments": {}}
        group = sum(m for k, i, m in trades if fx.group_of(k, cfg) == "usd_majors")
        self.assertEqual(group, 195.0)


if __name__ == "__main__":
    unittest.main()
