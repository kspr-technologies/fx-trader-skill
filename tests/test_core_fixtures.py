"""Tests for the made-up sample market (tests/fixtures/fx-market) and its generator.
Offline only. All prices are made up. The story rows a to o are spec 06 5.4."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ast
import contextlib
import io
import datetime
import email.utils
import importlib.machinery
import importlib.util
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

import fx_core as fx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
MARKET = os.path.join(FIXTURES, "fx-market")
DATA = os.path.join(MARKET, "data")
RAW = os.path.join(MARKET, "raw")
COMMON = os.path.join(FIXTURES, "makefix", "common.py")
EXAMPLE = os.path.join(ROOT, "fx-budget", "config.example.json")
D = datetime.datetime
H = datetime.timedelta(hours=1)
END = D(2026, 8, 1, 0, 0)     # after the last bar: every bar of the sample is done


def T(text):
    """ "2026-07-17 03:50" -> naive UTC datetime (test shorthand)."""
    return D.strptime(text, "%Y-%m-%d %H:%M")


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def all_files(folder):
    """Every file under folder, as sorted relative paths."""
    out = []
    for base, _, names in os.walk(folder):
        for n in names:
            out.append(os.path.relpath(os.path.join(base, n), folder))
    return sorted(out)


def load_generator():
    """tests/fixtures/make_fixtures.py as a module."""
    spec = importlib.util.spec_from_file_location("fx_make_fixtures", os.path.join(FIXTURES, "make_fixtures.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def top_level(path):
    """{name: source text} of a file's top-level functions, classes and simple assignments."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()
    out = {}
    for node in ast.parse(src).body:
        text = "\n".join(lines[node.lineno - 1:node.end_lineno])
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out[node.name] = text
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = text
    return out


def sample_cfg():
    return fx.load_config(os.path.join(MARKET, "config.json"))


class Generator(unittest.TestCase):
    def test_same_bytes_on_every_run(self):
        # the generator run into a temp folder gives exactly the files of fx-market/
        tmp = tempfile.mkdtemp()
        try:
            gen = load_generator()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(gen.main(["--out", tmp]), 0)
            self.assertIn("wrote 61 files to", out.getvalue())
            self.assertIn("total 6.9 MB", out.getvalue())
            self.assertEqual(all_files(tmp), all_files(MARKET))
            for rel in all_files(MARKET):
                self.assertEqual(read_bytes(os.path.join(tmp, rel)), read_bytes(os.path.join(MARKET, rel)), rel)
        finally:
            shutil.rmtree(tmp)

    def test_under_the_size_limit(self):
        total = sum(os.path.getsize(os.path.join(MARKET, f)) for f in all_files(MARKET))
        self.assertLess(total, 7.5e6)
        self.assertLess(total, 8 * 1024 * 1024)

    def test_the_file_list(self):
        files = all_files(MARKET)
        for rel in ("README.md", "config.json", "data/rolls.json", "data/calendar/2026-07-12.json",
                    "data/calendar/2026-07-19.json", "data/calendar/2026-07-26.json",
                    "data/cot/72hh-3qpy-088691.json", "data/cot/gpe5-46if-099741.json",
                    "data/yahoo/GCQ26_CMX/1h.json", "data/yahoo/GCZ26_CMX/1h.json"):
            self.assertIn(rel, files)
        yahoo = sorted(f for f in files if f.startswith("data/yahoo/"))
        self.assertEqual(len([f for f in yahoo if f.endswith("/1h.json")]), 22)
        self.assertEqual(sorted(f.split("/")[2] for f in yahoo if f.endswith("/5m.json")),
                         ["EURUSD_X", "GBPUSD_X", "GC_F", "USDJPY_X"])
        self.assertEqual(sorted(f.split("/")[2] for f in yahoo if f.endswith("/1d.json")),
                         ["BZ_F", "CL_F", "GC_F", "HG_F", "NG_F", "PA_F", "PL_F", "SI_F"])
        self.assertEqual(len([f for f in files if f.startswith("data/news/")]), 10)
        self.assertEqual(sorted(f[4:] for f in files if f.startswith("raw/")),
                         ["EURUSD_X-5m-day.json", "GCQ26_CMX-1h.json", "GCZ26_CMX-1h.json", "GC_F-1h-cut.json",
                          "GC_F-1h-incr.json", "GC_F-5m-saturday.json", "err-404.json", "err-422.json",
                          "spark-4.json"])

    def test_readme_says_made_up(self):
        with open(os.path.join(MARKET, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# The sample FX market (made up)\n"))
        self.assertIn("All prices in this folder are made up.", text)
        self.assertIn("python3 make_fixtures.py", text)
        self.assertIn("2,512.41", text)
        self.assertNotIn(chr(0x2014), text)

    def test_sample_config_differs_only_where_told(self):
        mine, example = read_json(os.path.join(MARKET, "config.json")), read_json(EXAMPLE)
        self.assertEqual(mine["account"]["balance"], 10000)
        self.assertEqual(mine["early_closes"], {"2026-07-03": "13:00"})
        mine["account"]["balance"] = example["account"]["balance"]
        mine["early_closes"] = example["early_closes"]
        self.assertEqual(mine, example)
        self.assertTrue(sample_cfg()["paper"])

    def test_no_generator_module_hides_a_python_module(self):
        # a makefix/calendar.py once hid Python's calendar, so email.utils broke there
        folder = os.path.join(FIXTURES, "makefix")
        names = sorted(n[:-3] for n in os.listdir(folder) if n.endswith(".py"))
        self.assertIn("econ", names)
        others = [p for p in sys.path if os.path.abspath(p or ".") != folder]
        for name in names:
            if name == "__init__":
                continue
            self.assertNotIn(name, sys.builtin_module_names, name)
            self.assertIsNone(importlib.machinery.PathFinder.find_spec(name, others), name)
        self.assertNotIn("calendar", names)

    def test_fx_prices_stay_away_from_real_levels(self):
        # 06 5: the prices are made up and kept clear of real ones. Real 5-day closes,
        # 2026-11 (research/samples/yahoo/spark_20_symbols_5d_1d.json), rounded outward.
        real = {"EURUSD": (1.114, 1.124), "GBPUSD": (1.335, 1.348), "USDJPY": (157.4, 158.5),
                "USDCHF": (0.815, 0.820), "AUDUSD": (0.686, 0.701), "USDCAD": (1.397, 1.412),
                "NZDUSD": (0.555, 0.565), "EURJPY": (176.4, 178.0), "GBPJPY": (211.4, 213.5),
                "EURGBP": (0.833, 0.835), "AUDJPY": (108.7, 111.0), "EURCHF": (0.913, 0.919)}
        made = {"EURUSD": (1.0600, 1.1000), "AUDUSD": (0.6014, 0.6386), "USDCAD": (1.2804, 1.3596),
                "EURGBP": (0.7663, 0.8137), "EURCHF": (0.9991, 1.0609)}
        for key, (rlo, rhi) in real.items():
            sym = key + "=X"
            closes = []
            for name in os.listdir(os.path.join(DATA, "yahoo", key + "_X")):
                doc = read_json(os.path.join(DATA, "yahoo", key + "_X", name))
                closes += [b[4] for b in doc["bars"] if b[4] is not None]
            lo, hi = min(closes), max(closes)
            self.assertTrue(hi < rlo * 0.99 or lo > rhi * 1.01, (sym, lo, hi))
            if key in made:
                self.assertTrue(made[key][0] <= lo and hi <= made[key][1], (sym, lo, hi))

    def test_copied_helpers_are_verbatim(self):
        canon = top_level(os.path.join(HERE, "fx_core.py"))
        mine = top_level(COMMON)
        need = ("parse_chart", "clean_rows", "merge_rows", "cache_file", "save_json_atomic", "yahoo_decimals",
                "utc_iso", "ts_to_utc", "utc_to_ts", "nth_weekday", "last_weekday", "us_dst", "uk_dst", "to_ny",
                "ny_to_utc", "to_london", "london_to_utc", "fx_day", "fx_day_bounds", "HOURS", "market_state",
                "is_open", "save_cache", "stored_rows")
        for name in need:
            self.assertIn(name, mine)
        for name, src in mine.items():
            self.assertIn(name, canon, name)
            self.assertEqual(src, canon[name], "%s is not a verbatim copy of fx_core.py" % name)


def chart_result(name):
    return read_json(os.path.join(RAW, name))["chart"]["result"][0]


class RawAnswers(unittest.TestCase):
    INTRADAY = ("GC_F-1h-incr.json", "GCQ26_CMX-1h.json", "GCZ26_CMX-1h.json", "EURUSD_X-5m-day.json")

    def test_the_yahoo_shape(self):
        orders = set()
        for name in self.INTRADAY:
            r = chart_result(name)
            self.assertEqual(sorted(r), ["indicators", "meta", "timestamp"])
            q = r["indicators"]["quote"][0]
            self.assertEqual(sorted(q), ["close", "high", "low", "open", "volume"])
            orders.add(tuple(q))
            for col in q.values():
                self.assertEqual(len(col), len(r["timestamp"]), name)
            m = r["meta"]
            for key in ("currency", "symbol", "exchangeName", "instrumentType", "regularMarketTime", "gmtoffset",
                        "exchangeTimezoneName", "regularMarketPrice", "shortName", "previousClose",
                        "tradingPeriods", "dataGranularity", "validRanges"):
                self.assertIn(key, m, name)
            # the live tick: the last row is at regularMarketTime, off the minute grid
            self.assertEqual(r["timestamp"][-1], m["regularMarketTime"])
            self.assertNotEqual(r["timestamp"][-1] % 60, 0)
            self.assertFalse(m["hasPrePostMarketData"])
        self.assertGreater(len(orders), 1)    # the list order changes between answers
        meta, rows = fx.parse_chart(chart_result("EURUSD_X-5m-day.json"))
        self.assertEqual(meta["longName"], "EUR/USD")
        self.assertEqual(meta["exchangeName"], "CCY")
        self.assertEqual(chart_result("GC_F-1h-incr.json")["meta"]["shortName"], "Gold Dec 26")
        self.assertEqual(chart_result("GC_F-1h-incr.json")["meta"]["exchangeName"], "CMX")

    def test_the_saturday_answer_has_no_timestamp(self):
        r = chart_result("GC_F-5m-saturday.json")
        self.assertNotIn("timestamp", r)
        meta, rows = fx.parse_chart(r)
        self.assertEqual(rows, [])
        self.assertEqual(fx.ts_to_utc(meta["regularMarketTime"]), D(2026, 7, 24, 20, 59, 58))

    def test_the_cut_answer_does_not_parse(self):
        raw = read_bytes(os.path.join(RAW, "GC_F-1h-cut.json"))
        self.assertEqual(len(raw), 4840)
        self.assertTrue(raw.startswith(b'{"chart":{"result":[{"meta":'))
        with self.assertRaises(ValueError):
            json.loads(raw.decode("utf-8"))

    def test_the_error_bodies(self):
        e404 = read_json(os.path.join(RAW, "err-404.json"))["chart"]
        self.assertIsNone(e404["result"])
        self.assertEqual(e404["error"], {"code": "Not Found", "description": "No data found, symbol may be delisted"})
        e422 = read_json(os.path.join(RAW, "err-422.json"))["chart"]["error"]
        self.assertEqual(e422["code"], "Unprocessable Entity")
        self.assertIn("within the last 60 days", e422["description"])

    def test_the_spark_answer(self):
        doc = read_json(os.path.join(RAW, "spark-4.json"))
        items = doc["spark"]["result"]
        self.assertIsNone(doc["spark"]["error"])
        self.assertEqual(sorted(i["symbol"] for i in items), ["EURUSD=X", "GBPUSD=X", "GC=F", "USDJPY=X"])
        self.assertNotEqual([i["symbol"] for i in items], ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GC=F"])
        for i in items:
            meta, rows = fx.parse_chart(i["response"][0], i["symbol"])
            self.assertTrue(rows)
            closes = [r[4] for r in rows if r[4] is not None]
            places = 3 if i["symbol"] == "USDJPY=X" else (1 if i["symbol"] == "GC=F" else 4)
            self.assertTrue(all(round(c, places) == c for c in closes), i["symbol"])

    def test_the_raw_rows_are_float32(self):
        meta, rows = fx.parse_chart(chart_result("GC_F-1h-incr.json"))
        six = [r for r in rows if r[0] == fx.utc_to_ts(T("2026-07-29 06:00"))][0]
        self.assertEqual(six[4], 2478.0)
        seven = [r for r in rows if r[0] == fx.utc_to_ts(T("2026-07-29 07:00"))][0]
        self.assertEqual(seven[1:5], [2478.199951171875, 2503.0, 2477.89990234375, 2500.60009765625])
        g = fx.instrument(sample_cfg(), "XAUUSD")
        bars = fx.clean_rows(rows, meta, "1h", g)
        self.assertEqual([bars[-1]["start"]], [T("2026-07-29 11:00")])    # the forming bar; the tick is gone
        self.assertEqual([b for b in bars if b["start"] == T("2026-07-29 07:00")][0]["high"], 2503.0)


class Caches(unittest.TestCase):
    def test_the_cache_format(self):
        for rel in all_files(os.path.join(DATA, "yahoo")):
            path = os.path.join(DATA, "yahoo", rel)
            raw = read_bytes(path)
            self.assertTrue(raw.startswith(b'{"symbol":"') and raw.endswith(b"]}\n"), rel)
            doc = json.loads(raw.decode("utf-8"))
            self.assertEqual(list(doc), ["symbol", "interval", "bars", "fetches"])
            self.assertEqual(fx.cache_file(DATA, doc["symbol"], doc["interval"]), path)
            ts = [b[0] for b in doc["bars"]]
            self.assertEqual(ts, sorted(set(ts)), rel)
            self.assertTrue(all(len(b) == 6 and b[4] is not None and b[0] % 60 == 0 for b in doc["bars"]), rel)
            self.assertLessEqual(len(doc["fetches"]), 400)
            for f in doc["fetches"]:
                for key in ("at", "market_time", "price", "short_name", "first", "last", "rows"):
                    self.assertIn(key, f)

    def test_the_newest_fetch_ends_the_week(self):
        cfg = sample_cfg()
        for key in cfg["universe"]:
            inst = fx.instrument(cfg, key)
            f = fx.last_fetch(fx.load_cache(DATA, inst["yahoo"], "1h"))
            want = D(2026, 7, 31, 20, 59, 58) if inst["price_source"] == "futures" else D(2026, 7, 31, 21, 0)
            self.assertEqual(fx.ts_to_utc(f["market_time"]), want, key)
            self.assertEqual(f["at"], "2026-07-31T21:30:00Z")

    def test_gold_short_names_switch_at_the_roll(self):
        fetches = fx.load_cache(DATA, "GC=F", "1h")["fetches"]
        names = dict((f["at"], f["short_name"]) for f in fetches)
        self.assertEqual(names["2026-07-29T08:00:00Z"], "Gold Aug 26")
        self.assertEqual(names["2026-07-29T08:12:00Z"], "Gold Dec 26")
        self.assertEqual(set(f["short_name"] for f in fetches if f["at"] < "2026-07-29T08:12:00Z"), {"Gold Aug 26"})
        self.assertEqual(set(f["short_name"] for f in fetches if f["at"] >= "2026-07-29T08:12:00Z"), {"Gold Dec 26"})
        self.assertEqual(fetches[0]["range"], "730d")
        self.assertIsNotNone(fetches[1]["period1"])

    def test_the_raw_cache_rows_are_float32(self):
        bars = fx.load_cache(DATA, "GC=F", "1h")["bars"]
        three = [b for b in bars if b[0] == fx.utc_to_ts(T("2026-07-17 03:00"))][0]
        self.assertEqual(three[4], 2433.39990234375)
        g = fx.instrument(sample_cfg(), "XAUUSD")
        self.assertEqual(fx.clean_rows([three], {}, "1h", g)[0]["close"], 2433.4)

    def test_the_daily_series_of_the_futures(self):
        cfg = sample_cfg()
        rows = fx.load_cache(DATA, "GC=F", "1d")["bars"]
        self.assertEqual(len(rows), 84)    # 85 FX days less Memorial Day (no Yahoo data)
        for r in rows:
            self.assertEqual(fx.to_ny(fx.ts_to_utc(r[0])).time(), datetime.time(0, 0))
        g = fx.instrument(cfg, "XAUUSD")
        days = fx.clean_rows(rows, {}, "1d", g)
        by = dict((fx.to_ny(b["start"]).date().isoformat(), b["close"]) for b in days)
        # the daily series stays on the Aug contract on 07-29 and 07-30 and switches on 07-31
        hourly = fx.load_bars(DATA, "GC=F", "1h", END, [], inst=g, cfg=cfg)
        last = dict((fx.fx_day(b["start"], "17:00 New York"), b["close"]) for b in hourly)
        self.assertGreater(last["2026-07-29"] / by["2026-07-29"], 1.005)
        self.assertLess(abs(last["2026-07-31"] / by["2026-07-31"] - 1), 0.001)
        self.assertLess(abs(last["2026-07-28"] / by["2026-07-28"] - 1), 0.003)

    def test_latest_price_is_flat_at_the_gold_entry(self):
        # story d: every now from 07-17 04:05 to 04:12 UTC gives the bid 2,433.4
        cfg = sample_cfg()
        g = fx.instrument(cfg, "XAUUSD")
        for minute in range(5, 13):
            p = fx.latest_price(DATA, g, D(2026, 7, 17, 4, minute), cfg)
            self.assertEqual(p["bid"], 2433.4, minute)
            self.assertEqual(p["contract"], "Gold Aug 26")


class Story(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = sample_cfg()
        cls.gold = fx.instrument(cls.cfg, "XAUUSD")
        cls.eur = fx.instrument(cls.cfg, "EURUSD")
        cls.g1 = fx.load_bars(DATA, "GC=F", "1h", END, [], inst=cls.gold, cfg=cls.cfg)
        cls.g5 = fx.load_bars(DATA, "GC=F", "5m", END, [], inst=cls.gold, cfg=cls.cfg)
        cls.e5 = fx.load_bars(DATA, "EURUSD=X", "5m", END, [], inst=cls.eur, cfg=cls.cfg)

    def bar(self, bars, text):
        return [b for b in bars if b["start"] == T(text)][0]

    def span(self, bars, a, b):
        return [x for x in bars if T(a) <= x["start"] <= T(b)]

    def test_a_the_july_3_early_close(self):
        days = dict((d["fx_day"], d) for d in fx.daily_from_hourly(self.g1, "17:00 New York", self.gold, self.cfg))
        self.assertEqual(days["2026-07-03"]["flags"], ["early_close"])
        self.assertEqual(days["2026-07-03"]["hours"], 6)
        self.assertEqual(self.span(self.g1, "2026-07-03 04:00", "2026-07-05 21:00"), [])
        cl = fx.load_bars(DATA, "CL=F", "1h", END, [], inst=fx.instrument(self.cfg, "USOIL"), cfg=self.cfg)
        self.assertEqual(self.span(cl, "2026-07-03 04:00", "2026-07-05 21:00"), [])
        e1 = fx.load_bars(DATA, "EURUSD=X", "1h", END, [], inst=self.eur, cfg=self.cfg)
        self.assertEqual(e1[[b["start"] for b in e1].index(T("2026-07-03 20:00"))]["start"], T("2026-07-03 20:00"))
        self.assertEqual(fx.day_close_utc(self.gold, "2026-07-03", self.cfg), T("2026-07-03 17:00"))
        for d in ("2026-05-25", "2026-06-19"):
            self.assertNotIn(d, [x for x in days if not days[x]["hours"]])

    def test_b_the_biggest_weekend_gap(self):
        gaps = []
        for a, b in zip(self.g1, self.g1[1:]):
            if b["start"] - a["start"] > datetime.timedelta(hours=30):
                gaps.append((b["open"] / a["close"] - 1, b["start"]))
        worst = max(gaps, key=lambda x: abs(x[0]))
        self.assertEqual(worst[1], T("2026-06-14 22:00"))
        self.assertAlmostEqual(worst[0], -0.016, places=4)

    def test_c_the_swing_breakout(self):
        now = T("2026-07-17 04:00")
        bars = fx.load_bars(DATA, "GC=F", "1h", now, [], inst=self.gold, cfg=self.cfg)
        days = fx.daily_from_hourly(bars, "17:00 New York", self.gold, self.cfg, now=now)
        self.assertEqual(days[-1]["fx_day"], "2026-07-16")
        self.assertEqual(days[-1]["close"], 2431.6)
        self.assertEqual(self.bar(bars, "2026-07-16 20:00")["close"], 2431.6)
        before = fx.usable_bars(days[:-1])[-20:]
        self.assertEqual(max(d["high"] for d in before), 2418.4)
        self.assertEqual([d["fx_day"] for d in before if d["high"] == 2418.4], ["2026-07-08"])
        hits = fx.find_setups(days, self.gold, self.cfg, ("buy", "sell"))
        self.assertEqual([(h["setup"], h["side"], h["level"], h["last"]) for h in hits],
                         [("breakout_20d", "buy", 2418.4, 2431.6)])
        self.assertEqual(hits[0]["distance_atr"], -0.47)    # 13.2 / about 28.0 (the made-up gold ATR)
        self.assertGreaterEqual(len(days), 73)

    def test_c_every_instrument_scans_on_07_17(self):
        # the swing scan at 07-17 09:00 PKT: 20 scanned, none failed (06 6.2 first step)
        now = T("2026-07-17 04:00")
        for key in self.cfg["universe"]:
            inst = fx.instrument(self.cfg, key)
            bars = fx.load_bars(DATA, inst["yahoo"], "1h", now, [], inst=inst, cfg=self.cfg)
            days = fx.daily_from_hourly(bars, "17:00 New York", inst, self.cfg, now=now)
            self.assertEqual(days[-1]["fx_day"], "2026-07-16", key)
            fx.find_setups(days, inst, self.cfg, ("buy", "sell"))

    def test_g_the_other_day_symbols_have_no_hit_on_07_23(self):
        # the day scan at 07-23 12:41 PKT: 4 scanned, 0 failed, 1 hit (EURUSD)
        now = T("2026-07-23 07:41")
        for key in ("GBPUSD", "USDJPY", "XAUUSD"):
            inst = fx.instrument(self.cfg, key)
            b5 = fx.load_bars(DATA, inst["yahoo"], "5m", now, [], inst=inst, cfg=self.cfg)
            b1 = fx.load_bars(DATA, inst["yahoo"], "1h", now, [], inst=inst, cfg=self.cfg)
            orr = fx.opening_range(b5, "london", "2026-07-23", 15, inst=inst)
            daily_atr = fx.atr_wilder(fx.usable_bars(fx.daily_from_hourly(b1, "17:00 New York", inst, self.cfg, now=now))[-250:])
            atr5 = fx.atr_wilder(fx.usable_bars(b5)[-288:])
            self.assertEqual(orr["bars"], 3)
            self.assertLessEqual(orr["high"] - orr["low"], 0.6 * daily_atr, key)
            self.assertGreaterEqual(orr["high"] - orr["low"], 8 * fx.spread_price(inst), key)
            for b in self.span(b5, "2026-07-23 07:15", "2026-07-23 07:35"):
                self.assertTrue(orr["low"] < b["close"] < orr["high"], key)
            last = b5[-1]["close"]
            self.assertGreater(min(orr["high"] - last, last - orr["low"]), atr5, key)

    def test_d_the_flat_entry_price(self):
        self.assertEqual(self.bar(self.g1, "2026-07-17 03:00")["close"], 2433.4)
        self.assertEqual(self.bar(self.g1, "2026-07-17 04:00")["open"], 2433.4)
        for b in self.span(self.g5, "2026-07-17 03:55", "2026-07-17 04:10"):
            self.assertEqual((b["open"], b["high"], b["low"], b["close"]), (2433.4,) * 4)
        hold = self.span(self.g1, "2026-07-17 04:00", "2026-07-29 06:00")
        self.assertGreaterEqual(min(b["low"] for b in hold), 2405.0)
        self.assertLessEqual(max(b["high"] for b in hold), 2485.0)

    def test_e_the_weekend_gap_up(self):
        fri, sun = self.bar(self.g1, "2026-07-17 20:00"), self.bar(self.g1, "2026-07-19 22:00")
        self.assertEqual((fri["close"], sun["open"]), (2441.0, 2458.0))
        self.assertEqual(round(100 * (sun["open"] / fri["close"] - 1), 2), 0.70)

    def test_f_the_outage(self):
        self.assertIn((T("2026-07-21 02:00"), T("2026-07-21 12:00")), fx.find_outages(self.g1, self.gold, "1h", self.cfg))
        for rel in all_files(os.path.join(DATA, "yahoo")):
            if rel.endswith("1d.json"):
                continue    # the outage is in the 1h and 5m data only
            bars = read_json(os.path.join(DATA, "yahoo", rel))["bars"]
            hole = [b for b in bars if fx.utc_to_ts(T("2026-07-21 02:00")) <= b[0] < fx.utc_to_ts(T("2026-07-21 12:00"))]
            self.assertEqual(hole, [], rel)
        days = dict((d["fx_day"], d) for d in fx.daily_from_hourly(self.g1, "17:00 New York", self.gold, self.cfg))
        self.assertIn("outage", days["2026-07-21"]["flags"])

    def test_g_h_the_london_range_and_the_breakdown(self):
        orr = fx.opening_range(self.e5, "london", "2026-07-23", 15, inst=self.eur)
        self.assertEqual((orr["high"], orr["low"], orr["mid"], orr["bars"], orr["of"]), (1.0864, 1.0848, 1.0856, 3, 3))
        for b in self.span(self.e5, "2026-07-23 07:15", "2026-07-23 07:30"):
            self.assertTrue(1.0848 <= b["close"] <= 1.0864)
        usable = fx.usable_bars(self.e5, fx.find_outages(self.e5, self.eur, "5m", self.cfg))
        until = T("2026-07-23 07:41")
        sell = fx.or_trigger(usable, orr, "sell", T("2026-07-23 07:00"), until)
        self.assertEqual((sell["start"], sell["close"]), (T("2026-07-23 07:35"), 1.08452))
        self.assertIsNone(fx.or_trigger(usable, orr, "buy", T("2026-07-23 07:00"), until))

    def test_i_the_sell_target_bar(self):
        after = self.span(self.e5, "2026-07-23 07:40", "2026-07-23 10:10")
        self.assertLessEqual(max(b["high"] for b in after), 1.0854)
        self.assertGreater(min(b["low"] for b in after), 1.08233)
        self.assertEqual(self.bar(self.e5, "2026-07-23 10:15")["low"], 1.08221)

    def test_j_the_ecb_bar(self):
        b = self.bar(self.e5, "2026-07-23 12:15")
        self.assertEqual((b["open"], b["high"], b["low"], b["close"]), (1.083, 1.0899, 1.0829, 1.0895))

    def test_k_l_the_roll_bars(self):
        six, seven = self.bar(self.g1, "2026-07-29 06:00"), self.bar(self.g1, "2026-07-29 07:00")
        self.assertEqual(six["close"], 2478.0)
        self.assertEqual((seven["open"], seven["high"], seven["low"], seven["close"]), (2478.2, 2503.0, 2477.9, 2500.6))
        q = fx.load_bars(DATA, "GCQ26.CMX", "1h", END, None, inst=self.gold, cfg=self.cfg)
        z = fx.load_bars(DATA, "GCZ26.CMX", "1h", END, None, inst=self.gold, cfg=self.cfg)
        self.assertEqual((q[0]["start"], z[0]["start"], q[-1]["start"]), (T("2026-07-24 00:00"),) * 2 + (T("2026-07-31 20:00"),))
        self.assertEqual((self.bar(q, "2026-07-29 06:00")["close"], self.bar(z, "2026-07-29 06:00")["close"]), (2478.0, 2500.3))
        zb = self.bar(z, "2026-07-29 07:00")
        self.assertEqual((zb["open"], zb["high"], zb["low"], zb["close"]), (2500.4, 2503.0, 2498.9, 2500.6))
        self.assertAlmostEqual(2500.3 / 2478.0, 1.0089992, places=7)
        for b in self.span(self.g1, "2026-07-24 00:00", "2026-07-29 06:00"):
            self.assertEqual(self.bar(q, b["start"].strftime("%Y-%m-%d %H:%M"))["close"], b["close"])
        for b in self.span(self.g1, "2026-07-29 08:00", "2026-07-31 20:00"):
            self.assertEqual(self.bar(z, b["start"].strftime("%Y-%m-%d %H:%M"))["close"], b["close"])

    def test_m_the_roll_in_rolls_json(self):
        doc = read_json(os.path.join(DATA, "rolls.json"))
        self.assertEqual(sorted(doc["checked"]), ["BZ", "CL", "GC", "HG", "NG", "PA", "PL", "SI"])
        for root, c in doc["checked"].items():
            self.assertEqual(c["history_from"], "2026-04-06", root)
        (r,) = doc["rolls"]
        self.assertEqual((r["id"], r["found_by"], r["confidence"], r["found_at"]),
                         ("GC-2026-07-29", "shortname", "exact", "2026-07-29T08:12:00Z"))
        self.assertEqual((r["first_bar_new"], r["old"], r["new"], r["replaced_bar"]),
                         ("2026-07-29T07:00:00Z", "GCQ26.CMX", "GCZ26.CMX", True))
        self.assertEqual(r["ratio"], 2500.3 / 2478.0)
        z = fx.load_cache(DATA, "GCZ26.CMX", "1h")["bars"]
        self.assertIn(r["replacement"]["1h"], z)
        rolls = fx.load_rolls(DATA)
        adjusted = fx.load_bars(DATA, "GC=F", "1h", END, rolls, inst=self.gold, cfg=self.cfg)
        self.assertEqual(self.bar(adjusted, "2026-07-29 07:00")["close"], 2500.6)
        self.assertEqual(self.bar(adjusted, "2026-07-29 07:00")["open"], 2500.4)
        self.assertEqual(self.bar(adjusted, "2026-07-29 06:00")["close"], 2500.3)
        replay = fx.load_bars(DATA, "GC=F", "1h", T("2026-07-29 08:00"), rolls, inst=self.gold, cfg=self.cfg)
        self.assertEqual(self.bar(replay, "2026-07-29 06:00")["close"], 2478.0)    # found only at 08:12

    def test_n_the_rebased_target_rings_once(self):
        hold = self.span(self.g1, "2026-07-29 08:00", "2026-07-30 13:00")
        self.assertLessEqual(max(b["high"] for b in hold), 2511.0)
        self.assertGreater(min(b["low"] for b in hold), 2425.0)
        first = [b for b in self.g5 if b["start"] >= T("2026-07-29 08:00") and b["high"] >= 2512.41][0]
        self.assertEqual((first["start"], first["high"]), (T("2026-07-30 14:35"), 2513.0))
        self.assertGreaterEqual(self.bar(self.g1, "2026-07-30 14:00")["high"], 2513.0)
        rest = self.span(self.g1, "2026-07-29 08:00", "2026-07-31 20:00")
        self.assertGreater(min(b["low"] for b in rest), 2421.59)

    def test_o_the_stray_bar_in_the_cme_break(self):
        raw = fx.load_cache(DATA, "GC=F", "1h")["bars"]
        stray = [b for b in raw if b[0] == fx.utc_to_ts(T("2026-07-13 21:00"))][0]
        before = [b for b in raw if b[0] == fx.utc_to_ts(T("2026-07-13 20:00"))][0]
        self.assertEqual(stray[1:5], [before[4]] * 4)
        self.assertEqual(stray[5], 1)
        self.assertEqual([b for b in self.g1 if b["start"] == T("2026-07-13 21:00")], [])
        days = dict((d["fx_day"], d) for d in fx.daily_from_hourly(self.g1, "17:00 New York", self.gold, self.cfg))
        self.assertEqual(days["2026-07-14"]["open"], self.bar(self.g1, "2026-07-13 22:00")["open"])
        self.assertEqual(days["2026-07-14"]["hours"], 23)

    def test_volume(self):
        # FX volume is 0; futures volume is 0 only on the first bar after a break (02 2.4 rule 6)
        e1 = fx.load_cache(DATA, "EURUSD=X", "1h")["bars"]
        self.assertEqual(set(b[5] for b in e1), {0})
        g5 = fx.load_cache(DATA, "GC=F", "5m")["bars"]
        after_break = [b for b in g5 if fx.ts_to_utc(b[0]).time() == datetime.time(22, 0)]
        self.assertTrue(after_break and all(b[5] == 0 for b in after_break))
        self.assertTrue(all(b[5] > 0 for b in g5 if b not in after_break))
        g1 = fx.load_cache(DATA, "GC=F", "1h")["bars"]
        early = [b for b in g1 if b[0] < fx.utc_to_ts(T("2026-06-20 00:00"))]
        opens = [b for b in early if fx.ts_to_utc(b[0]).time() == datetime.time(22, 0)
                 or fx.ts_to_utc(b[0]) == T("2026-05-26 04:00")]    # the reopen after Memorial Day
        self.assertEqual([b for b in early if b[5] == 0], opens)


class RollsFromTheRawAnswers(unittest.TestCase):
    """The raw answers on a fake network give the same roll as rolls.json (02 5.5, 5.6)."""

    FILES = {"GC%3DF": "GC_F-1h-incr.json", "GCQ26.CMX": "GCQ26_CMX-1h.json", "GCZ26.CMX": "GCZ26_CMX-1h.json"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (fx.yahoo_get, fx.SLEEP)
        self.calls = []

        def fake_get(path, timeout=None):
            self.calls.append(path)
            for key, name in self.FILES.items():
                if "/chart/%s?" % key in path:
                    return read_bytes(os.path.join(RAW, name))
            raise fx.NoData("Yahoo has no data for this symbol.")
        fx.yahoo_get = fake_get
        fx.SLEEP = lambda s: None

    def tearDown(self):
        fx.yahoo_get, fx.SLEEP = self.saved
        shutil.rmtree(self.tmp)

    def test_check_roll_forward_finds_the_gold_roll(self):
        cfg = sample_cfg()
        g = fx.instrument(cfg, "XAUUSD")
        now = T("2026-07-29 12:00")
        # an earlier fetch of our own, still on the Aug contract (a first run is never a roll)
        doc = fx.load_cache(self.tmp, "GC=F", "1h")
        doc["fetches"] = [{"at": "2026-07-29T08:00:00Z", "short_name": "Gold Aug 26"}]
        fx.save_cache(fx.cache_file(self.tmp, "GC=F", "1h"), doc)
        fx.fetch_into(self.tmp, fx.load_cache(self.tmp, "GC=F", "1h"), "GC=F", "1h", now, None,
                      fx.utc_to_ts(T("2026-07-28 00:00")), fx.utc_to_ts(now) + 60)
        rec = fx.check_roll_forward(self.tmp, g, cfg, now, False)
        want = dict(read_json(os.path.join(DATA, "rolls.json"))["rolls"][0], found_at="2026-07-29T12:00:00Z")
        self.assertEqual(rec, want)
        self.assertAlmostEqual(rec["ratio"], 1.0089992, places=7)
        self.assertEqual(len(self.calls), 3)

    def test_find_rolls_history_finds_it_as_an_estimate(self):
        data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(data, "yahoo"))
        for name in ("GC_F",):
            shutil.copytree(os.path.join(DATA, "yahoo", name), os.path.join(data, "yahoo", name))
        cfg = sample_cfg()
        found = fx.find_rolls_history(data, fx.instrument(cfg, "XAUUSD"), cfg, END, True)
        self.assertEqual([(r["id"], r["confidence"], r["first_bar_new"]) for r in found],
                         [("GC-2026-07-29", "estimated", "2026-07-29T07:00:00Z")])
        self.assertLess(abs(found[0]["ratio"] / (2500.3 / 2478.0) - 1), 0.001)
        self.assertEqual(self.calls, [])


class CalendarCotNews(unittest.TestCase):
    def test_calendar_files(self):
        counts = {}
        for week in ("2026-07-12", "2026-07-19", "2026-07-26"):
            doc = read_json(os.path.join(DATA, "calendar", week + ".json"))
            self.assertEqual((doc["week"], doc["ok"], doc["error"]), (week, True, None))
            self.assertEqual(doc["fetched_at"], week + "T21:00:00Z")
            for e in doc["events"]:
                self.assertEqual(sorted(e), ["country", "date", "forecast", "impact", "previous", "title"])
                self.assertTrue(e["date"].endswith("-04:00"))
                self.assertIn(e["impact"], ("High", "Medium", "Low", "Holiday"))
            counts[week] = (len(doc["events"]), [e["title"] for e in doc["events"] if e["impact"] == "High"])
        self.assertEqual(counts["2026-07-12"], (15, ["CPI m/m"]))
        self.assertEqual(counts["2026-07-19"], (24, ["Main Refinancing Rate", "ECB Press Conference"]))
        self.assertEqual(counts["2026-07-26"], (15, ["Federal Funds Rate"]))

    def test_the_ecb_quiet_window(self):
        cfg = sample_cfg()
        events = fx.parse_ff(read_json(os.path.join(DATA, "calendar", "2026-07-19.json")))
        self.assertEqual(len(events), 24)
        mrr = [e for e in events if e["title"] == "Main Refinancing Rate"][0]
        self.assertEqual(mrr["at"], T("2026-07-23 12:15"))
        oil = [e for e in events if e["title"] == "Crude Oil Inventories"][0]
        self.assertEqual((oil["at"], oil["impact"]), (T("2026-07-22 14:30"), "Medium"))
        self.assertTrue(fx.in_quiet_window(events, "EURUSD", T("2026-07-23 12:00"), cfg))
        self.assertFalse(fx.in_quiet_window(events, "EURUSD", T("2026-07-23 07:43"), cfg))
        # the High "ECB Press Conference" at 12:45 adds a second window: the block runs
        # 11:45 to 13:15 UTC (4:45 to 6:15 PM PKT), not 11:45 to 12:45 (check with the lead)
        wins = [(a, b, e["title"]) for a, b, e in fx.quiet_windows(events, "EURUSD", cfg)
                if a.date() == datetime.date(2026, 7, 23)]
        self.assertEqual(wins, [(T("2026-07-23 11:45"), T("2026-07-23 12:45"), "Main Refinancing Rate"),
                                (T("2026-07-23 12:15"), T("2026-07-23 13:15"), "ECB Press Conference")])
        self.assertFalse(fx.in_quiet_window(events, "EURUSD", T("2026-07-23 11:44"), cfg))
        self.assertTrue(fx.in_quiet_window(events, "EURUSD", T("2026-07-23 13:00"), cfg))
        self.assertFalse(fx.in_quiet_window(events, "EURUSD", T("2026-07-23 13:15"), cfg))
        self.assertFalse(fx.in_quiet_window(events, "EURUSD", T("2026-07-23 13:16"), cfg))

    def test_cot_files(self):
        for name, dataset, code, long_, short, net, change in (
                ("gold", "72hh-3qpy", "088691", "m_money_positions_long_all", "m_money_positions_short_all", 98000, 6200),
                ("euro", "gpe5-46if", "099741", "lev_money_positions_long", "lev_money_positions_short", -42000, None)):
            doc = read_json(os.path.join(DATA, "cot", "%s-%s.json" % (dataset, code)))
            self.assertEqual((doc["dataset"], doc["code"], doc["source"]), (dataset, code, "api"))
            rows = doc["rows"]
            self.assertEqual(len(rows), 160)
            self.assertEqual(rows[0]["report_date_as_yyyy_mm_dd"], "2026-07-14T00:00:00.000")
            self.assertTrue(all(isinstance(v, str) for r in rows for v in r.values()))
            dates = [r["report_date_as_yyyy_mm_dd"] for r in rows]
            self.assertEqual(dates, sorted(dates, reverse=True))
            pick = rows[1] if name == "gold" else rows[0]
            self.assertEqual(int(pick[long_]) - int(pick[short]), net, name)
            if change is not None:
                chg = int(pick["change_in_m_money_long_all"]) - int(pick["change_in_m_money_short_all"])
                self.assertEqual((pick["report_date_as_yyyy_mm_dd"][:10], chg), ("2026-07-07", change))

    def test_news_files(self):
        found = {}
        for name in sorted(os.listdir(os.path.join(DATA, "news"))):
            root = ET.fromstring(read_bytes(os.path.join(DATA, "news", name)))
            items = root.iter("item")
            found[name[:-4]] = [(i.findtext("title").strip(), i.findtext("link"), i.findtext("pubDate")) for i in items]
        self.assertEqual(len(found), 10)
        self.assertEqual(found["boj-news"], [])
        for name, items in found.items():
            if name != "boj-news":
                self.assertTrue(3 <= len(items) <= 6, name)
            for title, link, date in items:
                self.assertTrue(link.startswith("https://example.com/%s/" % name))
                if name.startswith("investing-"):
                    D.strptime(date, "%Y-%m-%d %H:%M:%S")
                else:
                    self.assertIsNotNone(email.utils.parsedate_to_datetime(date))
        titles = [t for items in found.values() for t, _, _ in items]
        for t in ("Gold climbs to a three-week high as the dollar slips", "EUR/USD slips before the ECB decision",
                  "Monetary policy decisions", "Oil holds steady as US stocks build"):
            self.assertIn(t, titles)
        self.assertEqual(found["ecb-press"][1][2], "Thu, 23 Jul 2026 14:15:00 +0200")
        self.assertTrue(any(d.endswith("-0400") for _, _, d in found["oilprice"]))
        self.assertIn(("Gold climbs to a three-week high as the dollar slips",
                       "https://example.com/fxstreet/gold-climbs-to-a-three-week-high", "Thu, 16 Jul 2026 21:40:00 GMT"),
                      found["fxstreet"])
        self.assertTrue(any("XAUUSD" in t for t in titles) and any("XAU/USD" in t for t in titles))
