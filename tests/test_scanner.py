"""fx-scanner (scan.py) on the made-up sample market. Offline only: every command line
passes --offline or --now, and the in-process network tests swap the opener, the clock
and the calendar download for fakes. All prices are made up."""

import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402
import fx_core  # noqa: E402

ROOT = _load.ROOT
SCRIPT = os.path.join(ROOT, "fx-scanner", "scan.py")
MARKET = os.path.join(_load.FIX, "fx-market")
RAW = os.path.join(MARKET, "raw")
scan = _load.load("fx-scanner", "scan")

SWING_T = "2026-07-17 09:00"
DAY_T = "2026-07-23 12:41"

# Measured on the first green build of the sample market (BUILD-BRIEF decision 6).
SWING_HITS = 10                 # (m) hits of the 07-17 swing scan
XAU_ATR = 28.054                # (m) the XAUUSD hit's daily ATR
XAU_DISTANCE = -0.47            # (m) its distance_atr
XAU_RANK = 5                    # (m) its rank
DAY_ATR = 0.00067               # (m) the EURUSD 5-minute ATR of the day hit
DAY_DISTANCE = -0.42            # (m) its distance_atr
DAY_AGE = 1.0                   # (m) its data_age_min


def flat(text):
    """The words of a printout with the line wraps taken out."""
    return " ".join(text.split())


def make_project(config=None, data=True):
    """A temp folder with fx/config.json (the sample config, changed by config) and
    fx/data (a copy of the sample data, or empty)."""
    tmp = tempfile.mkdtemp(prefix="fxscan-")
    fx = os.path.join(tmp, "fx")
    os.makedirs(fx)
    if data:
        shutil.copytree(os.path.join(MARKET, "data"), os.path.join(fx, "data"))
    else:
        os.makedirs(os.path.join(fx, "data"))
    with open(os.path.join(MARKET, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    for path, value in (config or {}).items():
        node = cfg
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    with open(os.path.join(fx, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return tmp


class Case(unittest.TestCase):
    config = None

    def setUp(self):
        self.tmp = make_project(self.config)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_scan(self, *args, **kw):
        """Run scan.py in the temp folder; check the exit code; return (out, err)."""
        code = kw.get("code", 0)
        p = subprocess.run([sys.executable, SCRIPT] + list(args), cwd=self.tmp,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=120)
        self.assertEqual(p.returncode, code, "exit %s\n%s\n%s" % (p.returncode, p.stdout, p.stderr))
        return p.stdout, p.stderr

    def watchlist_path(self):
        return os.path.join(self.tmp, "fx", "watchlist.json")

    def watchlist(self):
        with open(self.watchlist_path(), encoding="utf-8") as fh:
            return json.load(fh)

    def hit(self, doc, symbol, setup=None):
        got = [h for h in doc["hits"] if h["symbol"] == symbol and (setup is None or h["setup"] == setup)]
        self.assertTrue(got, "no %s hit in %s" % (symbol, [h["symbol"] for h in doc["hits"]]))
        return got[0]


class Swing(Case):
    def test_01_swing_chain_values(self):
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T)
        for text in ("XAUUSD", "2,418.40", "buy side", "20-day high breakout",
                     "A scan is a list of charts", "Daily bars to Thu 2026-07-16."):
            self.assertIn(text, out)
        doc = self.watchlist()
        self.assertEqual((doc["mode"], doc["fx_day"], doc["as_of"], doc["session"]),
                         ("swing", "2026-07-16", "2026-07-16T21:00:00Z", None))
        self.assertEqual((doc["created_by"], doc["created_at"]), ("fx-scanner", "2026-07-17T04:00:00Z"))
        self.assertEqual(doc["failed"], [])
        self.assertEqual((len(doc["universe"]), doc["scanned"]), (20, 20))
        h = self.hit(doc, "XAUUSD", "breakout_20d")
        self.assertEqual((h["side"], h["style"], h["level"], h["last"], h["rolls_applied"]),
                         ("buy", "swing", 2418.4, 2431.6, {}))
        self.assertEqual((h["atr"], h["distance_atr"], h["rank"], h["atr_interval"]),
                         (XAU_ATR, XAU_DISTANCE, XAU_RANK, "1d"))
        self.assertEqual(h["data_age_min"], 0.0)
        self.assertIsNone(h["opening_range"])
        self.assertEqual(len(doc["hits"]), SWING_HITS)
        self.assertEqual(list(h.keys()), list(scan.HIT_KEYS))
        # ranks 1..n in the 05 2.4 sort order
        self.assertEqual([x["rank"] for x in doc["hits"]], list(range(1, len(doc["hits"]) + 1)))
        names = [s["name"] for s in scan.SETUPS]
        keys = [(x["distance_atr"] > 0, abs(x["distance_atr"]), doc["universe"].index(x["symbol"]),
                 names.index(x["setup"])) for x in doc["hits"]]
        self.assertEqual(keys, sorted(keys))
        self.assertIn("%d hits. Swing scan, Fri 2026-07-17 9:00 AM PKT." % SWING_HITS, out.splitlines()[0])
        self.assertIn("Wrote fx/watchlist.json (swing, %d hits)." % SWING_HITS, out)
        self.assertIn("Next: /fx-news and /fx-calendar, then python3 fx-entry/entry.py check SYMBOL", out)
        self.assertIn("Yahoo price (gold futures), not your broker's price. Newest 2,433.40,", out)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_02_no_write(self):
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T, "--no-write")
        self.assertFalse(os.path.exists(self.watchlist_path()))
        self.assertIn("Not written (--no-write).", out)
        self.assertIn(scan.FOOTER, out)

    def test_03_json(self):
        out, _ = self.run_scan("swing", "--now", SWING_T, "--json")
        doc = json.loads(out)
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["ok"], doc["now"]),
                         (1, "fx-scanner", "swing", True, "2026-07-17T04:00:00Z"))
        self.assertEqual(doc["doc"]["mode"], "swing")
        self.assertEqual(doc["doc"], self.watchlist())
        self.assertIn(scan.FOOTER, doc["notes"])
        self.assertIn("Offline: saved prices only.", doc["notes"])
        self.assertFalse(any("ATR past it" in n for n in doc["notes"]))   # hit blocks stay out
        out, _ = self.run_scan("swing", "--now", SWING_T, "--json", "--no-write")
        self.assertIsNone(json.loads(out)["doc"])

    def test_04_same_bytes(self):
        self.run_scan("swing", "--offline", "--now", SWING_T)
        with open(self.watchlist_path(), "rb") as fh:
            first = fh.read()
        self.run_scan("swing", "--offline", "--now", SWING_T)
        with open(self.watchlist_path(), "rb") as fh:
            self.assertEqual(fh.read(), first)

    def test_05_outage_day(self):
        out, _ = self.run_scan("swing", "--offline", "--now", "2026-07-22 09:00")
        self.assertTrue(out.startswith("No hits."))
        self.assertIn("a Yahoo outage", flat(out))
        self.assertIn("from Tue 21 Jul 7:00 AM PKT to Tue 21 Jul 5:00 PM PKT", flat(out))
        doc = self.watchlist()
        self.assertEqual((len(doc["failed"]), doc["hits"], doc["fx_day"]), (20, [], "2026-07-21"))
        for f in doc["failed"]:
            self.assertIn("(outage)", f["why"])
            self.assertIn("Tue 2026-07-21", f["why"])
        self.assertEqual(flat(out).count("a Yahoo outage"), 1)   # one line for the shared hole

    def test_06_early_close_kept(self):
        self.run_scan("swing", "--offline", "--now", "2026-07-06 09:00")
        doc = self.watchlist()
        self.assertEqual(doc["fx_day"], "2026-07-03")
        cme = ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "COPPER", "USOIL", "NATGAS")
        self.assertEqual([f for f in doc["failed"] if f["symbol"] in cme], [])

    def test_10_blocked(self):
        cfg = os.path.join(self.tmp, "fx", "config.json")
        with open(cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["risk"]["blocked"] = ["XAUUSD"]
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T)
        doc = self.watchlist()
        self.assertIn({"symbol": "XAUUSD", "why": "on your blocked list"}, doc["failed"])
        self.assertEqual([h for h in doc["hits"] if h["symbol"] == "XAUUSD"], [])
        self.assertEqual(doc["scanned"], 19)
        self.assertIn("Not scanned (1):", out)
        self.assertIn("  XAUUSD: on your blocked list", out)

    def test_11_sells_found(self):
        # (check) the spec's EURUSD,XAUUSD give no sell hit on 07-17, so two more keys
        self.run_scan("swing", "--offline", "--now", SWING_T, "--side", "sell",
                      "--symbols", "eurusd,XAUUSD,USDCAD,USDCHF")
        doc = self.watchlist()
        self.assertEqual(doc["universe"], ["EURUSD", "XAUUSD", "USDCAD", "USDCHF"])
        self.assertTrue(doc["hits"])
        for h in doc["hits"]:
            self.assertEqual(h["side"], "sell")
            self.assertAlmostEqual(h["distance_atr"], round((h["last"] - h["level"]) / h["atr"], 2), delta=0.01)

    def test_23_no_banned_words(self):
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T, "--no-write")
        low = out.lower()
        for word in fx_core.SIGNAL_BANNED:
            self.assertNotIn(word, low)

    def test_24_quiet(self):
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T, "--quiet")
        lines = out.splitlines()
        self.assertFalse([l for l in lines if l.startswith("    Note:")])
        self.assertFalse([l for l in lines if "Newest" in l])
        self.assertIn(scan.FOOTER, lines)
        self.assertTrue(lines[0].endswith("Swing scan, Fri 2026-07-17 9:00 AM PKT."))
        self.assertIn("  XAUUSD  buy side  20-day high breakout: level 2,418.40, last 2,431.60,", lines)

    def test_show_limits_the_printout(self):
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T, "--show", "2")
        self.assertIn("  ... and %d more in fx/watchlist.json." % (SWING_HITS - 2), out)
        self.assertEqual(len(self.watchlist()["hits"]), SWING_HITS)   # all are written

    def test_default_is_swing(self):
        out, _ = self.run_scan("--offline", "--now", SWING_T, "--no-write")
        self.assertIn("Swing scan", out.splitlines()[0])


class SellsOff(Case):
    config = {"allow_short": False}

    def test_07_sells_off(self):
        out, _ = self.run_scan("swing", "--offline", "--now", SWING_T)
        self.assertIn("Sells are off (allow_short is false).", out)
        self.assertEqual(out.count("Sells are off"), 1)
        doc = self.watchlist()
        self.assertTrue(doc["hits"])
        self.assertEqual([h for h in doc["hits"] if h["side"] == "sell"], [])

    def test_08_sell_refused(self):
        for side in ("sell", "both"):
            out, err = self.run_scan("swing", "--offline", "--now", SWING_T, "--side", side, code=2)
            self.assertEqual(err.strip(), "scan.py: Sells are off in your config (allow_short is false).")
            self.assertEqual(out, "")
        self.run_scan("swing", "--offline", "--now", SWING_T, "--side", "buy", "--no-write")


class Day(Case):
    def test_12_13_23_day_chain_values(self):
        out, _ = self.run_scan("day", "--offline", "--now", DAY_T)
        for text in ("EURUSD", "1.08480", "sell side", "opening-range breakdown", "New York",
                     "London opening range: Thu 12:00 PM PKT (3:00 AM New York) to 12:15 PM PKT.",
                     "No new day trades after 9:00 PM PKT (12:00 PM New York).",
                     "Range 1.08480 to 1.08640 (3 of 3 bars).",
                     "Next: /fx-news and /fx-calendar, then python3 fx-entry/entry.py check EURUSD"):
            self.assertIn(text, out)
        self.assertTrue(out.startswith("1 hit. Day scan, Thu 2026-07-23 12:41 PM PKT (3:41 AM New York)."))
        doc = self.watchlist()
        self.assertEqual((doc["mode"], doc["session"], doc["fx_day"], doc["as_of"]),
                         ("day", "london", "2026-07-23", "2026-07-23T07:40:00Z"))
        self.assertEqual(doc["universe"], ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])
        self.assertEqual((doc["scanned"], doc["failed"]), (4, []))
        h = self.hit(doc, "EURUSD")
        self.assertEqual((h["name"], h["side"], h["style"], h["setup"], h["level"], h["last"]),
                         ("Euro / US dollar", "sell", "day", "or_breakdown", 1.0848, 1.08452))
        self.assertEqual(h["opening_range"], {"session": "london", "start": "2026-07-23T07:00:00Z",
                                              "end": "2026-07-23T07:15:00Z", "high": 1.0864,
                                              "low": 1.0848, "mid": 1.0856, "bars": 3, "of": 3})
        self.assertEqual((h["atr"], h["atr_interval"], h["distance_atr"], h["data_age_min"], h["rank"]),
                         (DAY_ATR, "5m", DAY_DISTANCE, DAY_AGE, 1))
        self.assertEqual(h["rolls_applied"], {})
        self.assertEqual(h["notes"][0], "Closed below the range low at 12:40 PM PKT.")
        self.assertTrue(any("12:40 PM PKT" in n for n in h["notes"]))
        # 13: the ECB news note (two High EUR events that day)
        news = [n for n in h["notes"] if "Main Refinancing Rate" in n]
        self.assertEqual(news, ["Main Refinancing Rate (EUR) at Thu 5:15 PM PKT (8:15 AM New York): "
                                "high-impact news soon (and 1 more)."])
        self.assertIn("Coarse data: Yahoo EURUSD moves in steps of about 1.25 pips.", h["notes"])
        self.assertEqual(len(doc["hits"]), 1)
        # 23: no banned words
        low = out.lower()
        for word in fx_core.SIGNAL_BANNED:
            self.assertNotIn(word, low)

    def test_14_range_not_done(self):
        out, _ = self.run_scan("day", "--offline", "--now", "2026-07-23 12:10")
        self.assertEqual(flat(out), "The London opening range is Thu 12:00 PM PKT (3:00 AM New York) "
                                      "to 12:15 PM PKT. It is not complete yet. Scan again after 12:15 PM PKT.")
        self.assertFalse(os.path.exists(self.watchlist_path()))

    def test_15_past_the_cutoff(self):
        out, _ = self.run_scan("day", "--offline", "--now", "2026-07-23 21:30")
        self.assertEqual(out.strip(), "It is past 9:00 PM PKT (12:00 PM New York). No new day trades today.")
        self.assertFalse(os.path.exists(self.watchlist_path()))

    def test_16_weekend(self):
        out, _ = self.run_scan("day", "--offline", "--now", "2026-07-25 12:00")
        self.assertIn("Closed for the weekend.", out)
        self.assertEqual(out.strip(), "Closed for the weekend. Opens Mon 27 Jul 2:05 AM PKT.")
        self.assertFalse(os.path.exists(self.watchlist_path()))
        out, _ = self.run_scan("day", "--now", "2026-07-25 12:00", "--json")
        doc = json.loads(out)
        self.assertEqual((doc["cmd"], doc["doc"], doc["notes"]),
                         ("day", None, ["Closed for the weekend. Opens Mon 27 Jul 2:05 AM PKT."]))

    def test_17_cme_no_data_day(self):
        self.run_scan("day", "--offline", "--now", "2026-07-03 13:00", "--symbols", "XAUUSD,EURUSD")
        doc = self.watchlist()
        self.assertIn({"symbol": "XAUUSD", "why": "No Yahoo futures prices on US holidays."}, doc["failed"])
        self.assertEqual([f for f in doc["failed"] if f["symbol"] == "EURUSD"], [])
        self.assertEqual(doc["scanned"], 2)

    def test_day_sells_off_finds_no_sell(self):
        cfg = os.path.join(self.tmp, "fx", "config.json")
        with open(cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["allow_short"] = False
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        out, _ = self.run_scan("day", "--offline", "--now", DAY_T)
        self.assertIn("Sells are off (allow_short is false).", out)
        self.assertEqual([h for h in self.watchlist()["hits"] if h["side"] == "sell"], [])


class Errors(Case):
    def err(self, *args):
        out, err = self.run_scan(*args, code=2)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(err.startswith("scan.py: "), err)
        return err.strip()

    def test_09_unknown_key(self):
        e = self.err("swing", "--offline", "--now", SWING_T, "--symbols", "XAUSD")
        self.assertEqual(e, "scan.py: XAUSD is not in your instruments. Did you mean XAUUSD?")
        e = self.err("day", "--offline", "--now", DAY_T, "--symbols", "QQQQQQ")
        self.assertEqual(e, "scan.py: QQQQQQ is not in your instruments.")
        e = self.err("swing", "--offline", "--now", SWING_T, "--symbols", ",")
        self.assertEqual(e, "scan.py: --symbols wants instrument keys like XAUUSD,EURUSD.")

    def test_18_empty_cache(self):
        empty = os.path.join(self.tmp, "fx", "data")
        shutil.rmtree(empty)
        os.makedirs(empty)
        e = self.err("swing", "--offline", "--symbols", "XAUUSD", "--data", "fx/data")
        self.assertIn("No hourly prices for XAUUSD yet (fx/data/yahoo/GC_F/1h.json)", e)
        self.assertTrue(e.endswith("Run without --offline once to get them."))
        e = self.err("swing", "--now", SWING_T)
        self.assertEqual(e, "scan.py: No hourly prices yet for 20 instruments "
                            "(fx/data/yahoo/EURUSD_X/1h.json and 19 more). Run without --offline once to get them.")
        e = self.err("day", "--now", DAY_T, "--symbols", "EURUSD")
        self.assertEqual(e, "scan.py: No 5-minute prices for EURUSD yet (fx/data/yahoo/EURUSD_X/5m.json). "
                            "Run without --offline once to get them.")

    def test_19_refresh_offline(self):
        e = self.err("refresh", "--offline")
        self.assertEqual(e, "scan.py: refresh needs the network. Run it without --offline and --now.")
        e = self.err("refresh", "--now", SWING_T)
        self.assertIn("refresh needs the network", e)

    def test_refresh_yahoo_off(self):
        cfg = os.path.join(self.tmp, "fx", "config.json")
        with open(cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["yahoo"] = False
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        e = self.err("refresh")
        self.assertEqual(e, 'scan.py: Yahoo is off in your config ("yahoo": false). Nothing to refresh.')

    def test_show_out_of_range(self):
        for bad in ("0", "201", "abc"):
            e = self.err("swing", "--offline", "--now", SWING_T, "--show", bad)
            self.assertEqual(e, "scan.py: --show must be a whole number from 1 to 200.")

    def test_bad_now(self):
        e = self.err("swing", "--now", "yesterday")
        self.assertEqual(e, 'scan.py: --now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)')

    def test_refresh_refuses_scan_flags(self):
        for flag in ("--side", "--show"):
            out, err = self.run_scan("refresh", flag, "buy", code=2)
            self.assertIn("unrecognized arguments", err)
        out, err = self.run_scan("refresh", "--no-write", code=2)
        self.assertIn("unrecognized arguments", err)

    def test_bad_config(self):
        cfg = os.path.join(self.tmp, "fx", "config.json")
        with open(cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["universe"] = []
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        e = self.err("swing", "--offline", "--now", SWING_T)
        self.assertIn("universe", e)
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        e = self.err("swing", "--offline", "--now", SWING_T)
        self.assertIn("is not valid JSON", e)
        e = self.err("swing", "--offline", "--now", SWING_T, "--config", "fx/nothere.json")
        self.assertIn("no config file at fx/nothere.json", e)

    def test_bad_rolls_file(self):
        with open(os.path.join(self.tmp, "fx", "data", "rolls.json"), "w", encoding="utf-8") as fh:
            fh.write("[1, 2]")
        e = self.err("swing", "--offline", "--now", SWING_T)
        self.assertIn("rolls.json is broken", e)

    def test_bad_or_minutes(self):
        cfg = os.path.join(self.tmp, "fx", "config.json")
        with open(cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["day"]["or_minutes"] = 17
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        e = self.err("day", "--offline", "--now", DAY_T)
        self.assertIn("or_minutes", e)

    def test_broken_cache_is_one_failed_key(self):
        path = os.path.join(self.tmp, "fx", "data", "yahoo", "GC_F", "1h.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{oops")
        self.run_scan("swing", "--offline", "--now", SWING_T)
        doc = self.watchlist()
        why = [f["why"] for f in doc["failed"] if f["symbol"] == "XAUUSD"]
        self.assertEqual(len(why), 1)
        self.assertIn("is broken. Delete it and run again with the network on.", why[0])
        self.assertEqual(doc["scanned"], 20)
        e = self.err("swing", "--offline", "--now", SWING_T, "--symbols", "XAUUSD")
        self.assertIn("The price cache", e)
        self.assertIn("is broken", e)


def utc(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")


class FakeClock(object):
    """Stands in for the time module inside scan.py: SLEEP moves this clock."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeResp(object):
    def __init__(self, body):
        self.status, self.body, self.headers = 200, body, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


NOT_FOUND = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found"}}}'


class InProcess(unittest.TestCase):
    """main() run in this process, with the network swapped for fakes."""

    def setUp(self):
        self.tmp = make_project()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.saved = (scan.yahoo_get, scan.calendar_get, scan.urllib.request.urlopen,
                      scan.time, scan.SLEEP, scan._last_request)
        self.cal_calls = []
        scan.calendar_get = lambda url, headers, timeout=None: (self.cal_calls.append(url) or (None, b"", {}))
        scan.urllib.request.urlopen = self.no_network
        scan._last_request = None

    def tearDown(self):
        (scan.yahoo_get, scan.calendar_get, scan.urllib.request.urlopen,
         scan.time, scan.SLEEP, scan._last_request) = self.saved
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def no_network(self, req, timeout=None):
        raise AssertionError("a test tried the network: %s" % getattr(req, "full_url", req))

    def quiet_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = scan.main(argv)
        finally:
            sys.stdout, sys.stderr = old
        return code, out.getvalue(), err.getvalue()

    def test_20_no_network(self):
        calls = []
        scan.yahoo_get = lambda *a, **k: calls.append(a)
        self.assertEqual(self.quiet_main(["swing", "--now", SWING_T])[0], 0)
        self.assertEqual(self.quiet_main(["swing", "--offline"])[0], 0)
        self.assertEqual(self.quiet_main(["day", "--now", DAY_T])[0], 0)
        self.assertEqual(self.cal_calls, [])          # --now and --offline: no calendar either
        cfg = os.path.join("fx", "config.json")
        with open(cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["yahoo"] = False
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        code, out, _ = self.quiet_main(["swing"])
        self.assertEqual(code, 0)
        self.assertIn('Yahoo is off in your config ("yahoo": false). Saved prices only.', out)
        self.assertEqual(calls, [])
        self.assertEqual(len(self.cal_calls), 1)      # the calendar does not depend on "yahoo"

    def test_21_fake_refresh(self):
        rolls_file = os.path.join("fx", "data", "rolls.json")
        with open(rolls_file, encoding="utf-8") as fh:
            rdoc = json.load(fh)
        rdoc["rolls"] = [r for r in rdoc["rolls"] if r["root"] != "GC"]
        with open(rolls_file, "w", encoding="utf-8") as fh:
            json.dump(rdoc, fh)
        clock, seen = FakeClock(), []
        scan.time, scan.SLEEP = clock, clock.sleep

        def fake(req, timeout=None):
            url = req.full_url
            seen.append((url, req.get_header("User-agent"), clock.t))
            if "GCZ26" in url:
                name = "GCZ26_CMX-1h.json"
            elif ".CMX" in url:
                name = "GCQ26_CMX-1h.json"
            elif "GC%3DF" in url and "interval=1h" in url:
                name = "GC_F-1h-incr.json"
            elif "GC%3DF" in url and "interval=5m" in url:
                name = "GC_F-5m-saturday.json"
            else:
                raise urllib.error.HTTPError(url, 404, "x", {}, io.BytesIO(NOT_FOUND))
            with open(os.path.join(RAW, name), "rb") as fh:
                return FakeResp(fh.read())
        scan.urllib.request.urlopen = fake
        code, out, err = self.quiet_main(["refresh", "--symbols", "XAUUSD"])
        self.assertEqual(code, 0, out + err)
        self.assertTrue(seen)
        for _url, ua, _t in seen:
            self.assertEqual(ua, "Mozilla/5.0 (compatible; fx-trader-skill/1.0)")
        times = [t for _u, _a, t in seen]
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(b - a, 2.0)
        with open(rolls_file, encoding="utf-8") as fh:
            ids = [r["id"] for r in json.load(fh)["rolls"]]
        self.assertIn("GC-2026-07-29", ids)
        self.assertIn("Refreshed 1 instrument from Yahoo (2 seconds between requests).", out)
        self.assertIn("Gold futures on Yahoo moved from", flat(out))
        self.assertEqual(len(self.cal_calls), 1)

    def test_refresh_all_fail(self):
        clock = FakeClock()
        scan.time, scan.SLEEP = clock, clock.sleep
        scan.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
            urllib.error.URLError("no answer"))
        code, out, err = self.quiet_main(["refresh", "--symbols", "EURUSD,GBPUSD"])
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "scan.py: Yahoo did not answer for any instrument. Try again later.")

    def test_swing_fetch_fails_but_cache_works(self):
        clock = FakeClock()
        scan.time, scan.SLEEP = clock, clock.sleep
        scan.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
            urllib.error.URLError("no answer"))
        code, out, err = self.quiet_main(["swing", "--symbols", "EURUSD", "--no-write", "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertIsNone(doc["doc"])
        empty = make_project(data=False)
        try:
            os.chdir(empty)
            code, out, err = self.quiet_main(["swing", "--symbols", "EURUSD,GBPUSD"])
            self.assertEqual(code, 2)
            self.assertEqual(err.strip(), "scan.py: Yahoo did not answer for any instrument. "
                                          "Try again later, or use --offline.")
        finally:
            os.chdir(self.tmp)
            shutil.rmtree(empty, ignore_errors=True)


class Helpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = os.path.join(MARKET, "data")
        cls.cfg = scan.load_config(os.path.join(MARKET, "config.json"), required=True)

    def test_22_roll_notes(self):
        gold = scan.instrument(self.cfg, "XAUUSD")
        now = utc("2026-07-27 04:00")
        rolls = scan.rolls_known(scan.load_rolls(self.data), now)
        self.assertEqual(rolls, [])                    # found_at 07-29: not known yet
        notes = scan.roll_notes(gold, rolls, now, scan.last_fx_day(now, self.cfg), self.cfg, self.data)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Gold rolls about"), notes)
        self.assertIn("(Aug to Dec): Yahoo prices jump then.", notes[0])
        now = utc("2026-07-30 04:00")
        rolls = scan.rolls_known(scan.load_rolls(self.data), now)
        notes = scan.roll_notes(gold, rolls, now, scan.last_fx_day(now, self.cfg), self.cfg, self.data)
        self.assertEqual(notes, ["Gold rolled on Yahoo on Wed 2026-07-29 (+0.90%). "
                                 "Older prices are adjusted to match."])
        self.assertEqual(scan.roll_notes(scan.instrument(self.cfg, "EURUSD"), rolls, now,
                                         "2026-07-29", self.cfg, self.data), [])
        self.assertEqual(scan.rolls_applied(gold, rolls), {"GC-2026-07-29": 1.008999192897498})

    def test_parse_symbols_and_sides(self):
        self.assertEqual(scan.parse_symbols(None, self.cfg, ["A"]), ["A"])
        self.assertEqual(scan.parse_symbols(" xauusd , EURUSD,XAUUSD", self.cfg, []), ["XAUUSD", "EURUSD"])
        with self.assertRaisesRegex(ValueError, "^XAUSD is not in your instruments. Did you mean XAUUSD\\?$"):
            scan.parse_symbols("XAUSD", self.cfg, [])
        self.assertEqual(scan.side_list(None, self.cfg), ("buy", "sell"))
        self.assertEqual(scan.side_list("sell", self.cfg), ("sell",))
        off = dict(self.cfg, allow_short=False)
        self.assertEqual(scan.side_list(None, off), ("buy",))
        self.assertEqual(scan.side_list("buy", off), ("buy",))
        with self.assertRaisesRegex(ValueError, "^Sells are off in your config"):
            scan.side_list("both", off)

    def test_fetch_order_and_refresh_keys(self):
        self.assertEqual(scan.fetch_order(["XAUUSD", "EURJPY", "USDCAD", "EURUSD"]),
                         ["USDCAD", "EURUSD", "XAUUSD", "EURJPY"])
        keys = scan.refresh_keys(self.cfg)
        self.assertEqual(keys[:7], ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCHF", "USDCAD"])
        self.assertEqual(len(keys), 20)
        self.assertEqual(len(set(keys)), 20)

    def test_first_run_line(self):
        empty = tempfile.mkdtemp()
        try:
            insts = [scan.instrument(self.cfg, k) for k in self.cfg["universe"]]
            self.assertEqual(scan.first_run_line(insts, empty),
                             "First run: getting 2 years of hourly prices for 20 instruments from "
                             "Yahoo. This takes about 2 minutes.")
            self.assertIn("for 1 instrument from Yahoo. This takes about 1 minute.",
                          scan.first_run_line(insts[:1], empty))
            self.assertIsNone(scan.first_run_line(insts, self.data))
        finally:
            shutil.rmtree(empty)

    def test_rank_hits(self):
        def h(sym, setup, d):
            return {"symbol": sym, "setup": setup, "distance_atr": d, "notes": []}
        got = scan.rank_hits([h("B", "breakout_20d", 0.3), h("A", "lose_ma20", -0.2),
                              h("A", "breakout_20d", -0.2), h("B", "breakdown_20d", 0.0)], ["A", "B"])
        self.assertEqual([(x["rank"], x["symbol"], x["setup"]) for x in got],
                         [(1, "B", "breakdown_20d"), (2, "A", "breakout_20d"), (3, "A", "lose_ma20"),
                          (4, "B", "breakout_20d")])

    def test_words(self):
        self.assertEqual(scan.distance_words(-0.47), "0.47 ATR past it.")
        self.assertEqual(scan.distance_words(0.3), "0.30 ATR away.")
        self.assertEqual(scan.distance_words(0), "at the level.")
        self.assertEqual(scan.hits_word(0), "No hits.")
        self.assertEqual(scan.hits_word(1), "1 hit.")
        lines = scan.wrap_line("x" * 50 + ", " + "y" * 40, "  ", "    ", comma=True)
        self.assertEqual(lines, ["  " + "x" * 50 + ",", "    " + "y" * 40])
        self.assertEqual(scan.pkt_clock(utc("2026-07-23 07:15")), "12:15 PM PKT")

    def test_outage_lines_share_a_hole(self):
        hole = (utc("2026-07-21 02:00"), utc("2026-07-21 12:00"))
        lines = scan.outage_lines({"XAUUSD": [hole], "XAGUSD": [hole], "USOIL": [hole]}, self.cfg)
        self.assertEqual(lines, ["Note: Yahoo has no prices for gold, silver and 1 more from Tue 21 Jul "
                                 "7:00 AM PKT to Tue 21 Jul 5:00 PM PKT (a Yahoo outage). Daily bars on "
                                 "those days are partial."])

    def test_saved_prices_and_news_notes(self):
        gold = scan.instrument(self.cfg, "XAUUSD")
        now = utc("2026-07-17 04:00")
        hit = {"style": "swing", "fx_day": "2026-07-16", "notes": [], "data_age_min": 125.0,
               "_fetch_failed": True}
        events = [{"title": "CPI m/m", "country": "USD", "at": utc("2026-07-17 12:30"),
                   "impact": "High", "symbols": ["XAUUSD", "EURUSD"]}]
        notes = scan.hit_notes(gold, hit, now, self.cfg, [], events, self.data)
        self.assertIn("CPI m/m (USD) at Fri 5:30 PM PKT (8:30 AM New York): high-impact news soon.", notes)
        self.assertEqual(notes[-1], "Yahoo did not answer just now. These are saved prices, 2 h 5 min old.")

    def test_day_hits_trigger_and_near(self):
        eur = scan.instrument(self.cfg, "EURUSD")
        or_ = {"session": "london", "start": "2026-07-23T07:00:00Z", "end": "2026-07-23T07:15:00Z",
               "high": 1.0864, "low": 1.0848, "mid": 1.0856, "bars": 3, "of": 3}

        def bar(hhmm, close):
            return {"start": utc("2026-07-23 " + hhmm), "open": close, "high": close, "low": close,
                    "close": close, "after_gap": False}
        b5 = [bar("07:15", 1.0850), bar("07:20", 1.0847), bar("07:25", 1.0866)]
        now, cut = utc("2026-07-23 07:31"), utc("2026-07-23 16:00")
        got = scan.day_hits(eur, b5, 0.0100, 0.0004, or_, now, cut, ("buy", "sell"))
        self.assertEqual([(h["side"], h["setup"]) for h in got], [("sell", "or_breakdown")])
        self.assertEqual(got[0]["notes"], ["Closed below the range low at 12:25 PM PKT."])
        near = scan.day_hits(eur, b5[:1], 0.0100, 0.0004, or_, now, cut, ("buy", "sell"))
        self.assertEqual([h["side"] for h in near], ["sell"])        # 0.5 ATR from the low
        with self.assertRaisesRegex(ValueError, "^Opening range 16.0 pips is wider than 0.6 daily ATR"):
            scan.day_hits(eur, b5, 0.0020, 0.0004, or_, now, cut, ("buy", "sell"))
        tiny = dict(or_, high=1.08485)
        with self.assertRaisesRegex(ValueError, "is too small next to the spread"):
            scan.day_hits(eur, b5, 0.0100, 0.0004, tiny, now, cut, ("buy", "sell"))


class Shape(unittest.TestCase):
    def test_help_and_python_38(self):
        for args in (["--help"], ["swing", "--help"], ["day", "--help"], ["refresh", "--help"]):
            p = subprocess.run([sys.executable, SCRIPT] + args, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
        import ast
        with open(SCRIPT, encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 8))

    def test_skill_md(self):
        with open(os.path.join(ROOT, "fx-scanner", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: fx-scanner\n"))
        self.assertGreater(len(text.splitlines()), 60)
        heads = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(heads[-2:], ["## What it never does", "## Safety"])
        self.assertIn("74% to 89%", text)
