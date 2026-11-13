"""Tests for fx-entry/entry.py, offline, on the made-up sample market (spec fx-entry 10).

Each command runs in a temp project folder whose fx/data is a copy of
tests/fixtures/fx-market/data and whose fx/config.json is the sample config. Every run
passes --offline --now, so nothing touches the network. Values marked (m) were measured
on the first green build and are frozen here.
"""

import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _load  # noqa: E402

ROOT = _load.ROOT
ENTRY = os.path.join(ROOT, "fx-entry", "entry.py")
SCAN = os.path.join(ROOT, "fx-scanner", "scan.py")
MARKET = os.path.join(ROOT, "tests", "fixtures", "fx-market")
E = _load.load("fx-entry", "entry")

GOLD_HIT = {"symbol": "XAUUSD", "name": "Gold", "side": "buy", "style": "swing",
            "setup": "breakout_20d", "rank": 1, "level": 2418.4, "last": 2431.6,
            "opening_range": None, "rolls_applied": {}}


def make_project():
    """A temp folder with fx/data (a copy of the sample data) and fx/config.json."""
    tmp = tempfile.mkdtemp(prefix="fx-entry-test-")
    os.makedirs(os.path.join(tmp, "fx"))
    shutil.copytree(os.path.join(MARKET, "data"), os.path.join(tmp, "fx", "data"))
    shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(tmp, "fx", "config.json"))
    return tmp


def flat(text):
    """The text with every run of spaces and newlines made one space (lines wrap)."""
    return " ".join(text.split())


def hand_watchlist(folder, hits, fx_day, created_at, mode="swing", session=None):
    """Write a hand-made fx/watchlist.json (the 00 4.1 shape, only the fields used)."""
    doc = {"created_by": "fx-scanner", "created_at": created_at, "mode": mode,
           "fx_day": fx_day, "session": session, "hits": hits}
    with open(os.path.join(folder, "fx", "watchlist.json"), "w") as fh:
        json.dump(doc, fh)


class Base(unittest.TestCase):
    """One sample project for the class; helpers to run entry.py in it."""

    saved = {}

    @classmethod
    def setUpClass(cls):
        cls.dir = make_project()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.dir, "fx", *parts)

    def setUp(self):
        for name in ("entry.json", "watchlist.json"):
            if os.path.exists(self.path(name)):
                os.remove(self.path(name))

    def run_entry(self, args, now=None, folder=None):
        """Run entry.py in a subprocess: (code, stdout, stderr)."""
        cmd = [sys.executable, ENTRY] + list(args)
        if now is not None:
            cmd += ["--offline", "--now", now]
        p = subprocess.run(cmd, cwd=folder or self.dir, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def call(self, args, now=None, folder=None):
        """Run entry.main in this process (faster): (code, stdout, stderr)."""
        argv = list(args) + (["--offline", "--now", now] if now is not None else [])
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(folder or self.dir)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = E.main(argv)
        finally:
            os.chdir(old)
        return code, out.getvalue(), err.getvalue()

    def entry_json(self):
        with open(self.path("entry.json")) as fh:
            return json.load(fh)

    def from_scanner(self, mode, now):
        """Run fx-scanner once per mode and test run; later calls copy the saved file.
        True when fx/watchlist.json now holds the scanner's file."""
        key = (mode, now)
        if key not in Base.saved:
            Base.saved[key] = None
            if os.path.exists(SCAN):
                p = subprocess.run([sys.executable, SCAN, mode, "--offline", "--now", now],
                                   cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True, timeout=120)
                if p.returncode == 0 and os.path.exists(self.path("watchlist.json")):
                    with open(self.path("watchlist.json")) as fh:
                        Base.saved[key] = fh.read()
        if Base.saved[key] is None:
            return False
        with open(self.path("watchlist.json"), "w") as fh:
            fh.write(Base.saved[key])
        return True

    def swing_watchlist(self):
        """The 07-17 swing watchlist: from fx-scanner when it runs, else by hand."""
        if self.from_scanner("swing", "2026-07-17 09:00"):
            return
        hand_watchlist(self.dir, [dict(GOLD_HIT)], "2026-07-16", "2026-07-17T04:00:00Z")

    def day_watchlist(self):
        """The 07-23 day watchlist: from fx-scanner when it runs, else by hand."""
        if self.from_scanner("day", "2026-07-23 12:41"):
            return
        rng = {"session": "london", "start": "2026-07-23T07:00:00Z", "end": "2026-07-23T07:15:00Z",
               "high": 1.0864, "low": 1.0848, "mid": 1.0856, "bars": 3, "of": 3}
        hit = {"symbol": "EURUSD", "name": "Euro / US dollar", "side": "sell", "style": "day",
               "setup": "or_breakdown", "rank": 1, "level": 1.0848, "last": 1.08452,
               "opening_range": rng, "rolls_applied": {}}
        hand_watchlist(self.dir, [hit], "2026-07-23", "2026-07-23T07:41:00Z", "day", "london")


class TestSwing(Base):
    """Cases 1 to 6, 17, 19 and 22: swing checks on the sample gold breakout."""

    def test_01_swing_chain(self):
        self.swing_watchlist()
        code, out, err = self.run_entry(["check", "XAUUSD", "--write"], "2026-07-17 09:02")
        self.assertEqual(code, 0, err)
        self.assertIn("TRIGGERED: the daily bar of Thu 2026-07-16 closed at 2,431.60, above 2,418.40.", out)
        self.assertIn("Nothing was placed.", out)
        self.assertIn("not your broker's price", out)
        self.assertIn("Wrote fx/entry.json (triggered).", out)
        self.assertIn("python3 fx-exit/exit.py plan --write", out)
        doc = self.entry_json()
        self.assertEqual(doc["status"], "triggered")
        self.assertEqual(doc["price"], 2431.6)
        self.assertEqual(doc["triggered_at"], "2026-07-16T21:00:00Z")
        self.assertEqual(doc["level"], 2418.4)
        self.assertEqual(doc["side"], "buy")
        self.assertEqual(doc["setup"], "breakout_20d")
        self.assertEqual(doc["fx_day"], "2026-07-16")
        self.assertEqual(doc["rolls_applied"], {})
        self.assertEqual(doc["created_by"], "fx-entry")
        self.assertEqual(doc["created_at"], "2026-07-17T04:02:00Z")
        self.assertIsNone(doc["distance"])
        self.assertIsNone(doc["opening_range"])
        # (m) measured on the first green build
        self.assertEqual(doc["bar"], {"start": "2026-07-15T21:00:00Z", "interval": "1d", "open": 2409.4,
                                      "high": 2441.2, "low": 2401.8, "close": 2431.6})
        self.assertEqual(doc["atr"], 28.054)
        self.assertEqual(doc["atr_interval"], "1d")
        self.assertEqual(doc["data_age_min"], 2.0)
        self.assertIn("Newest 2,433.40, 2 min", out)
        self.assertEqual(doc["note"], "The daily bar of Thu 2026-07-16 closed at 2,431.60, above 2,418.40.")
        self.assertEqual(list(doc), ["created_by", "created_at", "symbol", "side", "style", "setup",
                                     "fx_day", "status", "level", "opening_range", "bar", "price",
                                     "triggered_at", "distance", "atr", "atr_interval",
                                     "data_age_min", "rolls_applied", "note"])

    def test_02_no_write_and_json(self):
        self.swing_watchlist()
        code, out, err = self.call(["check", "XAUUSD"], "2026-07-17 09:02")
        self.assertEqual(code, 0, err)
        self.assertIn("Not written.", out)
        self.assertIn("Next: python3 fx-entry/entry.py check XAUUSD --write", out)
        self.assertFalse(os.path.exists(self.path("entry.json")))
        code, out, err = self.call(["check", "XAUUSD", "--json"], "2026-07-17 09:02")
        doc = json.loads(out)
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["ok"]), (1, "fx-entry", "check", True))
        self.assertIsNone(doc["doc"])
        self.assertEqual(doc["now"], "2026-07-17T04:02:00Z")
        self.assertTrue(any(x.startswith("TRIGGERED") for x in doc["notes"]))
        code, out, err = self.call(["check", "XAUUSD", "--json", "--write"], "2026-07-17 09:02")
        self.assertEqual(json.loads(out)["doc"]["status"], "triggered")
        self.assertEqual(json.loads(out)["doc"], self.entry_json())

    def test_03_swing_expired_old(self):
        self.swing_watchlist()
        code, out, err = self.call(["check", "XAUUSD"], "2026-07-18 09:00")
        self.assertEqual(code, 1, err)
        self.assertIn("EXPIRED: The close above 2,418.40 came on Thu. Fri has closed since.", out)
        self.assertIn("A late entry is a different trade. Scan again.", out)
        self.assertIn("Note: The market is closed now. It opens", out)

    def test_04_own_level_waiting(self):
        code, out, err = self.call(["check", "XAUUSD", "--level", "2500", "--side", "buy", "--write"],
                                   "2026-07-17 09:02")
        self.assertEqual(code, 1, err)
        self.assertIn("WAITING: last close 2,431.60, 68.40 under 2,500.00", out)
        self.assertIn("a close beyond your level", out)
        self.assertIn("A finished daily close beyond the level is needed.", out)
        doc = self.entry_json()
        self.assertEqual(doc["setup"], "manual")
        self.assertEqual(doc["status"], "waiting")
        self.assertEqual(doc["distance"], 68.4)
        self.assertEqual(doc["fx_day"], "2026-07-16")

    def test_05_sell_waiting(self):
        code, out, err = self.call(["check", "XAUUSD", "--level", "2400", "--side", "sell", "--write"],
                                   "2026-07-17 09:02")
        self.assertEqual(code, 1, err)
        self.assertIn("over 2,400.00", out)
        self.assertIn("sell side (waits for a close below)", out)
        doc = self.entry_json()
        self.assertEqual(doc["side"], "sell")
        self.assertEqual(doc["distance"], 31.6)

    def test_06_three_day_expiry(self):
        hit = dict(GOLD_HIT, level=2600.0)
        hand_watchlist(self.dir, [hit], "2026-07-13", "2026-07-14T04:00:00Z")
        code, out, err = self.call(["check", "XAUUSD"], "2026-07-17 09:02")
        self.assertEqual(code, 1, err)
        self.assertIn("No daily close above 2,600.00 in 3 days. This setup expired.", out)

    def test_17_rebased_level(self):
        hit = dict(GOLD_HIT, level=2480.0)
        hand_watchlist(self.dir, [hit], "2026-07-28", "2026-07-28T22:00:00Z")
        code, out, err = self.call(["check", "XAUUSD", "--write"], "2026-07-29 13:15")
        self.assertIn(code, (0, 1), err)
        doc = self.entry_json()
        self.assertEqual(doc["level"], 2502.32)
        self.assertEqual(list(doc["rolls_applied"]), ["GC-2026-07-29"])
        self.assertAlmostEqual(doc["rolls_applied"]["GC-2026-07-29"], 2500.3 / 2478.0, places=9)
        self.assertIn("The level moved from 2,480.00 to 2,502.32", flat(out))
        self.assertIn("Gold rolled on Yahoo after the scan (+0.90%).", flat(out))

    def test_17b_roll_not_known_before_found_at(self):
        hit = dict(GOLD_HIT, level=2480.0)
        hand_watchlist(self.dir, [hit], "2026-07-28", "2026-07-28T22:00:00Z")
        code, out, err = self.call(["check", "XAUUSD", "--write"], "2026-07-29 13:10")
        doc = self.entry_json()
        self.assertEqual(doc["level"], 2480.0)
        self.assertEqual(doc["rolls_applied"], {})

    def test_19_not_on_watchlist(self):
        self.swing_watchlist()
        code, out, err = self.call(["check", "AUDUSD"], "2026-07-17 09:02")
        self.assertEqual(code, 2)
        self.assertIn("entry.py: AUDUSD is not on fx/watchlist.json. Run fx-scanner first, or give "
                      "--level PRICE --side buy|sell.", err)

    def test_22_watchlist(self):
        self.swing_watchlist()
        code, out, err = self.run_entry(["watchlist"], "2026-07-17 09:02")
        self.assertEqual(code, 0, err)
        self.assertIn(" of ", out)
        self.assertIn("triggered.", out)
        self.assertIn("Nothing was placed.", out)
        self.assertRegex(out, r"XAUUSD\s+buy side\s+20-day high breakout\s+TRIGGERED  close 2,431.60 \(Thu\)")
        self.assertIn("Watchlist of Fri 2026-07-17 9:00 AM PKT (swing,", out)
        self.assertFalse(os.path.exists(self.path("entry.json")))


class TestDay(Base):
    """Cases 7 to 16, 20 and 21: the EURUSD day sell of Thu 2026-07-23 and friends."""

    def test_07_08_day_chain(self):
        self.day_watchlist()
        code, out, err = self.run_entry(["check", "EURUSD", "--write"], "2026-07-23 12:41")
        self.assertEqual(code, 0, err)
        self.assertIn("TRIGGERED 12:40 PM PKT at 1.08452: the 12:35 PM bar closed below the range "
                      "low 1.08480.\n", out)
        self.assertIn("This is the moment the rule names. Nothing was placed.", out)
        self.assertIn("London range Thu 12:00 PM PKT (3:00 AM New York) to 12:15 PM PKT", flat(out))
        self.assertIn("No new day trades after 9:00 PM PKT (12:00 PM New York).", out)
        self.assertIn("Yahoo price, close to your broker's price. Newest 1.08452, 1 min old.", out)
        # case 8: the news note
        self.assertIn("Main Refinancing Rate", out)
        self.assertIn("before the flat-by time", flat(out))
        doc = self.entry_json()
        self.assertEqual((doc["side"], doc["setup"], doc["style"]), ("sell", "or_breakdown", "day"))
        self.assertEqual(doc["level"], 1.0848)
        self.assertEqual(doc["opening_range"], {
            "session": "london", "start": "2026-07-23T07:00:00Z", "end": "2026-07-23T07:15:00Z",
            "high": 1.0864, "low": 1.0848, "mid": 1.0856, "bars": 3, "of": 3})
        self.assertEqual(doc["bar"]["start"], "2026-07-23T07:35:00Z")
        self.assertEqual(doc["triggered_at"], "2026-07-23T07:40:00Z")
        self.assertEqual(doc["price"], 1.08452)
        self.assertEqual(doc["fx_day"], "2026-07-23")
        self.assertIsNone(doc["distance"])
        self.assertEqual(doc["note"], "The 12:35 PM bar closed below the range low 1.08480.")
        # (m) measured on the first green build
        self.assertEqual(doc["bar"], {"start": "2026-07-23T07:35:00Z", "interval": "5m", "open": 1.08485,
                                      "high": 1.08495, "low": 1.0844, "close": 1.08452})
        self.assertEqual(doc["atr"], 0.00067)
        self.assertEqual(doc["atr_interval"], "5m")
        self.assertEqual(doc["data_age_min"], 1.0)

    def test_09_one_bar_ago(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day"], "2026-07-23 12:45")
        self.assertEqual(code, 0, err)
        self.assertIn("The close came 1 bar ago, at 12:40 PM PKT. A late entry is a different trade.", flat(out))
        self.assertIn("EURUSD is not on the watchlist. Using today's opening range.", flat(out))

    def test_10_day_waiting(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day", "--side", "sell", "--write"],
                                   "2026-07-23 12:31")
        self.assertEqual(code, 1, err)
        self.assertIn("WAITING  last close", out)
        self.assertIn("above the range low 1.08480", out)
        doc = self.entry_json()
        self.assertEqual(doc["status"], "waiting")
        self.assertGreaterEqual(doc["distance"], 0)
        self.assertEqual(doc["distance"], 0.0001)

    def test_11_forming_and_ahead(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day", "--write"], "2026-07-23 12:10")
        self.assertEqual(code, 1, err)
        self.assertIn("The range is forming (2 of 3 bars so far).", out)
        doc = self.entry_json()
        self.assertIsNone(doc["opening_range"])
        self.assertIsNone(doc["distance"])
        code, out, err = self.call(["check", "EURUSD", "--style", "day"], "2026-07-23 09:00")
        self.assertEqual(code, 1, err)
        self.assertIn("The London range starts Thu 12:00 PM PKT (3:00 AM New York).", out)

    def test_12_late_wins(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day", "--write"], "2026-07-23 21:05")
        self.assertEqual(code, 1, err)
        self.assertIn("LATE  it is past 9:00 PM PKT.", out)
        self.assertIn("The close below 1.08480 came at 12:40 PM PKT.", out)
        doc = self.entry_json()
        self.assertEqual(doc["status"], "late")
        self.assertIsNone(doc["triggered_at"])

    def test_13_quiet_window(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day", "--write"], "2026-07-23 17:00")
        self.assertEqual(code, 0, err)
        self.assertIn("Quiet time for news now", flat(out))
        self.assertIn("4:45 PM to 5:45 PM PKT", flat(out))
        self.assertEqual(self.entry_json()["status"], "triggered")

    def test_14_other_side(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day", "--side", "buy"], "2026-07-23 12:41")
        self.assertEqual(code, 1, err)
        self.assertIn("EXPIRED  The other side triggered first: a close below 1.08480 at 12:40 PM PKT.", out)

    def test_15_weekend(self):
        code, out, err = self.call(["check", "EURUSD", "--style", "day"], "2026-07-25 12:00")
        self.assertEqual(code, 1, err)
        self.assertIn("CLOSED  Closed for the weekend.", out)

    def test_16_cme_no_data_day(self):
        code, out, err = self.call(["check", "XAUUSD", "--style", "day"], "2026-07-03 13:00")
        self.assertEqual(code, 1, err)
        self.assertIn("CLOSED", out)
        self.assertIn("No Yahoo futures prices on US holidays.", out)

    def test_20_watch_replay(self):
        calls = []
        old = E.SLEEP
        E.SLEEP = lambda s: calls.append(s)
        try:
            code, out, err = self.call(["watch", "EURUSD", "--style", "day", "--write"], "2026-07-23 12:20")
        finally:
            E.SLEEP = old
        self.assertEqual(code, 0, err)
        self.assertIn("EURUSD  sell side  opening-range breakdown. Watching from Thu 12:20 PM PKT.", out)
        self.assertIn("12:25 PM PKT  the 12:20 PM bar closed at 1.08500. Waiting.", out)
        self.assertIn("the 12:20 PM bar closed", out)
        tail = out[out.index("TRIGGERED"):]
        self.assertTrue(tail.startswith("TRIGGERED 12:40 PM PKT at 1.08452"))
        self.assertNotIn("the 12:35 PM bar closed at", out)
        self.assertEqual(self.entry_json()["status"], "triggered")
        self.assertEqual(calls, [])

    def test_21_watch_until(self):
        code, out, err = self.run_entry(["watch", "EURUSD", "--style", "day", "--side", "buy",
                                         "--until", "12:30"], "2026-07-23 12:20")
        self.assertEqual(code, 1, err)
        self.assertIn("Stopped at --until 12:30 PM PKT without a trigger.", out)
        self.assertFalse(os.path.exists(self.path("entry.json")))

    def test_watch_json_and_late(self):
        code, out, err = self.call(["watch", "EURUSD", "--style", "day", "--json"], "2026-07-23 21:05")
        self.assertEqual(code, 1, err)
        doc = json.loads(out)
        self.assertEqual((doc["cmd"], doc["ok"], doc["doc"]), ("watch", False, None))
        self.assertTrue(any(x.startswith("LATE") for x in doc["notes"]))

    def test_watch_swing_replay(self):
        code, out, err = self.call(["watch", "XAUUSD", "--level", "2440", "--side", "buy", "--write"],
                                   "2026-07-17 09:02")
        self.assertEqual(code, 0, err)
        self.assertIn("Sat 2:16 AM PKT  the daily bar of Fri 2026-07-17 closed at 2,441.00. Triggered.", out)
        self.assertEqual(self.entry_json()["triggered_at"], "2026-07-17T21:00:00Z")


class TestRefusals(Base):
    """Case 18 and every section 7 message: one stderr line, exit 2."""

    def refuse(self, args, text, now="2026-07-17 09:02", folder=None):
        code, out, err = self.call(args, now, folder)
        self.assertEqual(code, 2, out + err)
        self.assertIn("entry.py: " + text, err)
        self.assertEqual(len(err.strip().split("\n")), 1, err)
        return err

    def config_with(self, **changes):
        """A second config folder (other/config.json) that reads fx/data."""
        with open(self.path("config.json")) as fh:
            cfg = json.load(fh)
        for k, v in changes.items():
            if "." in k:
                a, b = k.split(".")
                cfg[a][b] = v
            else:
                cfg[k] = v
        folder = os.path.join(self.dir, "other")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "config.json")
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        return ["--config", path, "--data", self.path("data")]

    def test_18_sells_off(self):
        flags = self.config_with(allow_short=False)
        self.refuse(["check", "EURUSD", "--side", "sell"] + flags,
                    "Sells are off in your config (allow_short is false).")
        self.refuse(["check", "EURUSD", "--side", "both"] + flags,
                    "Sells are off in your config (allow_short is false).")
        wl = os.path.join(self.dir, "other", "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_at": "2026-07-17T04:00:00Z", "mode": "swing", "fx_day": "2026-07-16",
                       "hits": [dict(GOLD_HIT, side="sell", setup="breakdown_20d")]}, fh)
        self.refuse(["check", "XAUUSD"] + flags, "Sells are off in your config (allow_short is false).")

    def test_unknown_key(self):
        self.refuse(["check", "XAUSD"], "XAUSD is not in your instruments. Did you mean XAUUSD?")
        self.refuse(["check", "QQQQQQ"], "QQQQQQ is not in your instruments.")

    def test_level_refusals(self):
        self.refuse(["check", "XAUUSD", "--level", "2500"], "--level needs --side buy or --side sell.")
        self.refuse(["check", "XAUUSD", "--level", "2500", "--side", "both"],
                    "--level needs --side buy or --side sell.")
        self.refuse(["check", "XAUUSD", "--level", "2500", "--side", "buy", "--style", "day"],
                    "--level is for swing setups. A day setup uses the opening range.")
        self.refuse(["check", "XAUUSD", "--level", "2500", "--side", "buy", "--setup", "or_breakout"],
                    "--level is for swing setups. A day setup uses the opening range.")
        self.refuse(["check", "XAUUSD", "--level", "-5", "--side", "buy"],
                    "--level must be a price above 0, not -5.")
        self.refuse(["check", "XAUUSD", "--level", "abc", "--side", "buy"],
                    "--level must be a price above 0, not abc.")

    def test_setup_name(self):
        err = self.refuse(["check", "XAUUSD", "--setup", "breakout"], '"breakout" is not a setup name. Names: ')
        self.assertIn("breakout_20d", err)
        self.assertIn("or_breakdown", err)

    def test_watchlist_missing(self):
        self.refuse(["check", "XAUUSD"], "no watchlist at fx/watchlist.json. Run fx-scanner first. "
                    "Or give --level PRICE --side buy|sell.")
        self.refuse(["watchlist"], "no watchlist at fx/watchlist.json. Run fx-scanner first.")

    def test_bad_watchlist(self):
        hand_watchlist(self.dir, [dict(GOLD_HIT)], "2026-07-16", "2026-07-17T04:00:00Z", mode="week")
        self.refuse(["check", "XAUUSD"], 'fx/watchlist.json: "mode" must be "swing" or "day"')
        hits = [dict(GOLD_HIT, symbol="EURUSD", rank=1), dict(GOLD_HIT, rank=2, level=None)]
        hand_watchlist(self.dir, hits, "2026-07-16", "2026-07-17T04:00:00Z")
        self.refuse(["watchlist"], "fx/watchlist.json: hit 2 (XAUUSD) has no level. Run fx-scanner again.")
        hand_watchlist(self.dir, [dict(GOLD_HIT, side="long")], "2026-07-16", "2026-07-17T04:00:00Z")
        self.refuse(["check", "XAUUSD"], 'fx/watchlist.json: side must be "buy" or "sell", not "long"')
        with open(self.path("watchlist.json"), "w") as fh:
            fh.write("{not json")
        self.refuse(["check", "XAUUSD"], "fx/watchlist.json is not valid JSON")

    def test_flag_values(self):
        self.refuse(["watch", "XAUUSD", "--every", "5"],
                    "--every must be 60 to 3600 seconds (Yahoo asks for a polite pace).")
        self.refuse(["watch", "XAUUSD", "--until", "25:00"], "--until wants a time like 23:30")
        code, out, err = self.call(["check", "XAUUSD", "--now", "soon"])
        self.assertEqual(code, 2)
        self.assertIn('entry.py: --now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)', err)

    def test_no_subcommand(self):
        code, out, err = self.run_entry([])
        self.assertEqual(code, 2)
        self.assertIn("usage: entry.py", out + err)


class TestDataErrors(Base):
    """Empty, broken and missing data files (section 7). Its own project copy."""

    def put(self, rel, text):
        path = self.path("data", *rel.split("/"))
        with open(path, "w") as fh:
            fh.write(text)

    def test_empty_caches(self):
        self.put("yahoo/GC_F/1h.json", json.dumps({"symbol": "GC=F", "interval": "1h", "bars": [], "fetches": []}))
        code, out, err = self.call(["check", "XAUUSD", "--level", "2500", "--side", "buy"], "2026-07-17 09:02")
        self.assertEqual(code, 2)
        self.assertIn("entry.py: No hourly prices for XAUUSD yet (fx/data/yahoo/GC_F/1h.json). Run without "
                      "--offline once to get them.", err)
        os.remove(self.path("data", "yahoo", "EURUSD_X", "5m.json"))
        code, out, err = self.call(["check", "EURUSD", "--style", "day"], "2026-07-23 12:41")
        self.assertEqual(code, 2)
        self.assertIn("entry.py: No 5-minute prices for EURUSD yet (fx/data/yahoo/EURUSD_X/5m.json). Run "
                      "without --offline once to get them.", err)

    def test_broken_cache(self):
        self.put("yahoo/USDCHF_X/1h.json", "{broken")
        code, out, err = self.call(["check", "USDCHF", "--level", "0.9", "--side", "buy"], "2026-07-17 09:02")
        self.assertEqual(code, 2)
        self.assertIn("entry.py: The price cache fx/data/yahoo/USDCHF_X/1h.json is broken.", err)

    def test_bad_rolls(self):
        with open(self.path("data", "rolls.json")) as fh:
            doc = json.load(fh)
        good = json.dumps(doc)
        doc["rolls"][0]["ratio"] = 5
        self.put("rolls.json", json.dumps(doc))
        try:
            code, out, err = self.call(["check", "XAUUSD", "--level", "2500", "--side", "buy"], "2026-07-17 09:02")
        finally:
            self.put("rolls.json", good)
        self.assertEqual(code, 2)
        self.assertIn("entry.py: fx/data/rolls.json: roll GC-2026-07-29 has a ratio that cannot be right (5). "
                      "Fix or delete that roll.", err)

    def test_or_minutes(self):
        cfg_path = self.path("config.json")
        with open(cfg_path) as fh:
            good = fh.read()
        cfg = json.loads(good)
        cfg["day"]["or_minutes"] = 12
        with open(cfg_path, "w") as fh:
            json.dump(cfg, fh)
        try:
            code, out, err = self.call(["check", "EURUSD", "--style", "day"], "2026-07-23 12:41")
        finally:
            with open(cfg_path, "w") as fh:
                fh.write(good)
        self.assertEqual(code, 2)
        self.assertIn("multiple of 5", err)


class FakeNet(object):
    """Swaps the network functions of entry.py for fakes that record each call."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def yahoo_get(self, path, timeout=None):
        self.calls.append(path)
        raise ValueError(self.fail or "no network in tests")

    def calendar_get(self, url, headers, timeout=None):
        self.calls.append(url)
        return None, b"", {}   # the real helper never raises: None is a network error

    def __enter__(self):
        self.old = (E.yahoo_get, E.calendar_get, E.SLEEP)
        E.yahoo_get, E.calendar_get, E.SLEEP = self.yahoo_get, self.calendar_get, lambda s: None
        return self

    def __exit__(self, *exc):
        E.yahoo_get, E.calendar_get, E.SLEEP = self.old
        return False


class TestNetwork(Base):
    """Case 25 and the Yahoo error paths, with fakes only."""

    def test_25_no_network_no_banned_words(self):
        outs = []
        with FakeNet() as net:
            self.swing_watchlist()
            outs.append(self.call(["check", "XAUUSD", "--write"], "2026-07-17 09:02")[1])
            self.day_watchlist()
            outs.append(self.call(["check", "EURUSD", "--write"], "2026-07-23 12:41")[1])
            outs.append(self.call(["watchlist"], "2026-07-23 12:41")[1])
        self.assertEqual(net.calls, [])
        banned = E.SIGNAL_BANNED if hasattr(E, "SIGNAL_BANNED") else (
            "strong buy", "strong sell", "buy now", "sell now", "you should buy", "you should sell",
            "sure thing", "guaranteed", "can't lose", "cannot lose", "winning trade", "easy money",
            "recommend")
        for out in outs:
            self.assertTrue(out)
            for word in banned:
                self.assertNotIn(word, out.lower())
            self.assertNotIn(chr(0x2014), out)

    def test_yahoo_failed_empty_cache(self):
        self.put_empty = os.path.join(self.dir, "fx", "data", "yahoo", "USDCAD_X", "1h.json")
        with open(self.put_empty) as fh:
            good = fh.read()
        with open(self.put_empty, "w") as fh:
            json.dump({"symbol": "USDCAD=X", "interval": "1h", "bars": [], "fetches": []}, fh)
        msg = "Yahoo did not answer for USDCAD=X. Try again later, or use --offline."
        try:
            with FakeNet(fail=msg) as net:
                code, out, err = self.call(["check", "USDCAD", "--level", "1.3", "--side", "buy"])
        finally:
            with open(self.put_empty, "w") as fh:
                fh.write(good)
        self.assertEqual(code, 2)
        self.assertIn("entry.py: " + msg, err)
        self.assertTrue(any("USDCAD" in c for c in net.calls))

    def test_yahoo_failed_saved_prices(self):
        with FakeNet(fail="Yahoo did not answer for EURGBP=X.") as net:
            code, out, err = self.call(["check", "EURGBP", "--level", "0.9", "--side", "buy"])
        self.assertIn(code, (0, 1), err)
        self.assertIn("Yahoo did not answer just now. Using the saved prices.", flat(out))
        self.assertTrue(net.calls)

    def test_yahoo_off_in_config(self):
        flags = TestRefusals.config_with(self, yahoo=False, calendar_url="")
        with FakeNet() as net:
            code, out, err = self.call(["check", "EURGBP", "--level", "0.9", "--side", "buy"] + flags)
        self.assertIn(code, (0, 1), err)
        self.assertEqual(net.calls, [])

    def test_offline_note(self):
        with FakeNet() as net:
            code, out, err = self.call(["check", "EURGBP", "--level", "0.9", "--side", "buy", "--offline"])
        self.assertEqual(net.calls, [])
        self.assertIn("Note: Offline: saved prices only.", out)


def hand_days(closes, first="2026-06-01"):
    """Usable daily bars on weekdays from first: high = close + 2, low = close - 2."""
    d = datetime.date.fromisoformat(first)
    out = []
    for c in closes:
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        start = datetime.datetime.combine(d, datetime.time(0))
        out.append({"fx_day": d.isoformat(), "start": start, "end": start + datetime.timedelta(days=1),
                    "open": c, "high": c + 2.0, "low": c - 2.0, "close": c, "flags": [],
                    "after_gap": not out})
        d += datetime.timedelta(days=1)
    return out


class TestPure(unittest.TestCase):
    """Cases 23 and 24, and the watch loop with fakes."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = E.load_config(os.path.join(MARKET, "config.json"))
        cls.gold = E.instrument(cls.cfg, "XAUUSD")

    def target(self, **kw):
        t = dict(GOLD_HIT, fx_day="2026-06-25", level=110.0)   # the newest hand day
        t.update(kw)
        return t

    def test_24_close_equal_waits(self):
        days = hand_days([100.0] * 18 + [110.0])
        res = E.swing_status(days, self.target(), days[-1]["fx_day"], self.gold, self.cfg)
        self.assertEqual(res["status"], "waiting")
        self.assertEqual(res["distance"], 0.0)

    def test_24_wick_is_not_a_trigger(self):
        days = hand_days([100.0] * 18 + [109.0])   # high 111 is above 110, the close is not
        res = E.swing_status(days, self.target(), days[-1]["fx_day"], self.gold, self.cfg)
        self.assertEqual(res["status"], "waiting")
        self.assertEqual(res["distance"], 1.0)
        self.assertTrue(res["head"].startswith("WAITING: last close 109.00, 1.00 under 110.00 ("))

    def test_24_trigger_and_sell(self):
        days = hand_days([100.0] * 18 + [110.01])
        res = E.swing_status(days, self.target(), days[-1]["fx_day"], self.gold, self.cfg)
        self.assertEqual(res["status"], "triggered")
        res = E.swing_status(days, self.target(side="sell", level=111.0), days[-1]["fx_day"], self.gold, self.cfg)
        self.assertEqual(res["status"], "triggered")
        self.assertIn("below 111.00", res["note"])

    def test_24_inside_pair(self):
        days = hand_days([100.0] * 17 + [96.0, 105.0])
        up = self.target(setup="inside_break_up", level=104.0, fx_day=days[-3]["fx_day"])
        down = self.target(setup="inside_break_down", side="sell", level=97.0, fx_day=days[-3]["fx_day"])
        res = E.swing_status(days, up, days[-1]["fx_day"], self.gold, self.cfg, partner=down)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(res["note"], "The other side triggered first.")
        res = E.swing_status(days, down, days[-1]["fx_day"], self.gold, self.cfg, partner=up)
        self.assertNotEqual(res["note"], "The other side triggered first.")

    def test_24_no_days(self):
        res = E.swing_status([], self.target(), None, self.gold, self.cfg)
        self.assertEqual(res["status"], "closed")
        self.assertEqual(res["head"], "CLOSED: No usable daily bars for Gold.")

    def test_23_helpers_match_scan(self):
        if not os.path.exists(SCAN):
            self.skipTest("fx-scanner/scan.py is not built yet")
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        import check_copies
        mine = check_copies.top_nodes(check_copies.read(ENTRY), ENTRY)
        theirs = check_copies.top_nodes(check_copies.read(SCAN), SCAN)
        for name in ("rolls_known", "last_done_fx_day", "entry_cutoff", "roll_notes"):
            self.assertIn(name, theirs, name)
            self.assertEqual(mine[name][1], theirs[name][1], name)

    def test_rolls_known(self):
        rolls = [{"id": "a", "found_at": "2026-07-29T08:12:00Z"}, {"id": "b"}]
        now = datetime.datetime(2026, 7, 29, 8, 0)
        self.assertEqual([r["id"] for r in E.rolls_known(rolls, now)], ["b"])
        self.assertEqual(len(E.rolls_known(rolls, now + datetime.timedelta(minutes=12))), 2)

    def test_day_filter_words(self):
        eur = E.instrument(self.cfg, "EURUSD")
        wide = {"high": 1.0900, "low": 1.0855}
        self.assertEqual(E.day_filter(wide, 0.00665, eur),
                         "Opening range 45.0 pips is wider than 0.6 daily ATR (39.9 pips). Skipped today.")
        thin = {"high": 1.08486, "low": 1.0848}
        self.assertEqual(E.day_filter(thin, 0.00665, eur),
                         "Opening range 0.6 pips is too small next to the spread (0.1 pips). Skipped today.")
        self.assertIsNone(E.day_filter({"high": 1.0864, "low": 1.0848}, 0.00665, eur))

    def test_early_close_cutoff_words(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["early_closes"] = {"2026-07-03": "11:00"}   # 15:00 UTC: the cut-off is 7:30 PM PKT
        now = E.parse_now("2026-07-03 20:00")
        res = E.new_result({"symbol": "XAUUSD", "side": "buy"}, "day")
        res["note"] = "x"
        cut = E.entry_cutoff(self.gold, "2026-07-03", now, cfg)
        self.assertEqual(cut, datetime.datetime(2026, 7, 3, 14, 30))
        E.day_late(res, None, {}, self.gold, cfg, "2026-07-03", cut)
        self.assertEqual(res["note"], "It is past 7:30 PM PKT (the market stops early today). "
                                      "No new day trades now.")
        self.assertEqual(res["status"], "late")
        self.assertIn("(the market stops early today)", res["note"])


class TestWatchLoop(Base):
    """The watch loop with fakes: failed looks, Ctrl+C, the simulated clock."""

    def test_five_failed_looks(self):
        clock = E.SimClock(datetime.datetime(2026, 7, 23, 7, 20))
        lines = []

        def look(now):
            raise ValueError("Yahoo did not answer for EURUSD=X.")

        code, res = E.watch_loop(look, clock.now, clock.sleep, lines.append, 60, None)
        self.assertEqual(code, 2)
        self.assertIsNone(res)
        self.assertEqual(lines[-1], "Stopping after 5 failed reads in a row.")
        self.assertEqual(len([x for x in lines if "could not read Yahoo" in x]), 5)
        self.assertEqual(lines[0], "12:20 PM PKT  could not read Yahoo: Yahoo did not answer for EURUSD=X.")
        self.assertEqual(clock.now(), datetime.datetime(2026, 7, 23, 7, 24))

    def test_watch_failed_looks_exit_2(self):
        old = E.check_one

        def broken(*a, **k):
            raise ValueError("Yahoo did not answer for EURUSD=X.")

        E.check_one = broken
        try:
            code, out, err = self.call(["watch", "EURUSD", "--style", "day"], "2026-07-23 12:20")
        finally:
            E.check_one = old
        self.assertEqual(code, 2)
        self.assertIn("Stopping after 5 failed reads in a row.", out)

    def test_ctrl_c(self):
        old = E.check_one

        def stop(*a, **k):
            raise KeyboardInterrupt()

        E.check_one = stop
        try:
            code, out, err = self.call(["watch", "EURUSD", "--style", "day"], "2026-07-23 12:20")
        finally:
            E.check_one = old
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().split("\n")[-1], "Stopped.")

    def test_sim_clock(self):
        c = E.SimClock(datetime.datetime(2026, 7, 23, 7, 0))
        c.sleep(90)
        self.assertEqual(c.now(), datetime.datetime(2026, 7, 23, 7, 1, 30))

    def test_until_parse(self):
        now = datetime.datetime(2026, 7, 23, 7, 20)   # 12:20 PM PKT
        self.assertEqual(E.parse_until("12:30", now), datetime.datetime(2026, 7, 23, 7, 30))
        self.assertEqual(E.parse_until("12:00", now), datetime.datetime(2026, 7, 24, 7, 0))
        self.assertEqual(E.parse_until("08:00Z", now), datetime.datetime(2026, 7, 23, 8, 0))
        self.assertIsNone(E.parse_until(None, now))
