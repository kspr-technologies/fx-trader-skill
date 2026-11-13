"""Tests for fx-exit/exit.py on the made-up sample market (offline, no network).

Run from the pack root: python3 -m unittest discover -s tests -p "test_exit.py"
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import datetime
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _load

ROOT = _load.ROOT
FIX = os.path.join(ROOT, "tests", "fixtures", "fx-market")
SCRIPT = os.path.join(ROOT, "fx-exit", "exit.py")
EX = _load.load("fx-exit", "exit")

GOLD_T = "2026-07-17 09:05"
EUR_T = "2026-07-23 12:42"
# (m) measured on the first green build of the sample market, then frozen
GOLD_ATR = 28.054          # the daily ATR of gold at the 07-16 trigger bar
GOLD_GAP = 5610.8          # 2 x 28.054 x 100
EUR_ATR = 0.00067          # the 5-minute ATR of EURUSD at the 07:35 trigger bar
EUR_GAP = 201.0            # 3 x 0.00067 x 100,000
EUR_SWAP = 3.1             # EURUSD swap_short a night, 1 lot (paid to you)
ROLL_DATE = "2026-07-17"   # the roll-ahead line (window start 07-16, never a past date)
BANNED = ("strong buy", "strong sell", "buy now", "sell now", "you should buy",
          "you should sell", "sure thing", "guaranteed", "can't lose", "cannot lose",
          "winning trade", "easy money", "recommend")


def sample_cfg():
    return EX.load_config(os.path.join(FIX, "config.json"))


def gold_entry(data):
    """entry.json for the gold swing buy, as fx-entry makes it at 07-17 09:02 PKT."""
    cfg = sample_cfg()
    inst = EX.instrument(cfg, "XAUUSD")
    now = EX.parse_now("2026-07-17 09:02")
    rolls = [r for r in EX.load_rolls(data) if EX.parse_utc(r["found_at"]) <= now]
    bars = EX.load_bars(data, "GC=F", "1h", now, rolls=rolls, inst=inst, cfg=cfg)
    daily = EX.daily_from_hourly(bars, cfg["day_end"], inst, cfg, now=now)
    hit = [h for h in EX.find_setups(daily, inst, cfg, ("buy",)) if h["setup"] == "breakout_20d"][0]
    d = daily[-1]
    return {"created_by": "fx-entry", "created_at": "2026-07-17T04:02:00Z", "symbol": "XAUUSD",
            "side": "buy", "style": "swing", "setup": "breakout_20d", "fx_day": d["fx_day"],
            "status": "triggered", "level": hit["level"], "opening_range": None,
            "bar": {"start": EX.utc_iso(d["start"]), "interval": "1d", "open": d["open"],
                    "high": d["high"], "low": d["low"], "close": d["close"]},
            "price": d["close"], "triggered_at": EX.utc_iso(d["end"]), "distance": None,
            "atr": hit["atr"], "atr_interval": "1d", "data_age_min": 2.0, "rolls_applied": {},
            "note": "made by the test"}


def eur_entry(data):
    """entry.json for the EURUSD day sell, as fx-entry makes it at 07-23 12:41 PKT."""
    cfg = sample_cfg()
    inst = EX.instrument(cfg, "EURUSD")
    now = EX.parse_now("2026-07-23 12:41")
    bars = EX.load_bars(data, "EURUSD=X", "5m", now, inst=inst, cfg=cfg)
    B5 = EX.usable_bars(bars, EX.find_outages(bars, inst, "5m", cfg))
    or_ = EX.opening_range(bars, "london", "2026-07-23", 15, inst)
    trig = [b for b in B5 if b["start"] == datetime.datetime(2026, 7, 23, 7, 35)][0]
    atr = EX.atr_wilder([b for b in B5 if b["start"] <= trig["start"]][-288:])
    return {"created_by": "fx-entry", "created_at": "2026-07-23T07:41:00Z", "symbol": "EURUSD",
            "side": "sell", "style": "day", "setup": "or_breakdown", "fx_day": "2026-07-23",
            "status": "triggered", "level": or_["low"], "opening_range": or_,
            "bar": {"start": EX.utc_iso(trig["start"]), "interval": "5m", "open": trig["open"],
                    "high": trig["high"], "low": trig["low"], "close": trig["close"]},
            "price": trig["close"], "triggered_at": "2026-07-23T07:40:00Z", "distance": None,
            "atr": round(atr, 6), "atr_interval": "5m", "data_age_min": 1.0, "rolls_applied": {},
            "note": "made by the test"}


class ExitCase(unittest.TestCase):
    """A temp project: fx/data is a copy of the sample market, fx/config.json its config."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fx-exit-")
        self.fx = os.path.join(self.dir, "fx")
        os.makedirs(self.fx)
        shutil.copytree(os.path.join(FIX, "data"), os.path.join(self.fx, "data"))
        shutil.copy(os.path.join(FIX, "config.json"), os.path.join(self.fx, "config.json"))
        self.data = os.path.join(self.fx, "data")
        self.outputs = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, name, doc):
        with open(os.path.join(self.fx, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def edit_config(self, fn):
        path = os.path.join(self.fx, "config.json")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        fn(doc)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def plan_file(self):
        path = os.path.join(self.fx, "plan.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def run_exit(self, now, *args, code=0):
        cmd = [sys.executable, SCRIPT, "plan"] + list(args) + ["--offline", "--now", now]
        p = subprocess.run(cmd, cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=120)
        self.outputs.append(p.stdout + p.stderr)
        self.assertEqual(p.returncode, code, "exit %s\n%s\n%s" % (p.returncode, p.stdout, p.stderr))
        return p.stdout, p.stderr


class TestSamplePlans(ExitCase):

    def test_swing_buy_sample(self):
        self.put("entry.json", gold_entry(self.data))
        out, _ = self.run_exit(GOLD_T, "--stop", "price:2400", "--target", "price:2490", "--write")
        p = self.plan_file()
        want = {"symbol": "XAUUSD", "side": "buy", "style": "swing", "setup": "breakout_20d",
                "fx_day": "2026-07-17", "chart_entry": 2433.4, "chart_stop": 2400.0,
                "entry": 2433.49, "entry_type": "market", "entry_at": "2026-07-16T21:00:00Z",
                "stop_loss": 2400.0, "stop_rule": "price", "take_profit": 2490.0,
                "target_rule": "price", "target_r": 1.69, "risk_per_unit": 33.49,
                "risk_points": 3349, "reward_risk": 1.69, "reward_risk_net": 0.84,
                "spread_points": 9, "atr": GOLD_ATR, "atr_interval": "1d", "opening_range": None,
                "flat_by": None, "flat_by_utc": None, "max_hold_days": 20,
                "exit_by": "2026-08-13T21:00:00Z", "usd_per_point_per_lot": 1.0,
                "rolls_applied": {}, "created_by": "fx-exit", "created_at": "2026-07-17T04:05:00Z",
                "entry_rule": "buy at the market (ask = bid 2,433.40 + spread 0.09)",
                "stop_label": "your price (2,400.00)", "target_label": "your price (2,490.00)"}
        for k, v in want.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["costs"], {"commission_round_trip": 6.0, "spread": 9.0,
                                      "swap_per_night": -54.84, "nights_est": 28,
                                      "swap_est": -1535.52, "swap_free": False, "fee_est": 0.0})
        self.assertEqual(p["gap"]["loss_per_lot_usd"], GOLD_GAP)
        self.assertEqual(p["warnings"], [
            "Gold rolls about %s. Yahoo prices jump then; your broker's price does not." % ROLL_DATE,
            "Yahoo gold is a futures price, about 10 minutes late. Your broker's gold price is a little different.",
            "After costs this trade wins 0.84 times what it risks. Your minimum is 1.5.",
            "The market closes for the weekend. On Monday the price can open past your stop."])
        self.assertTrue(out.startswith("PLAN  XAUUSD BUY  swing  20-day high breakout\n"))
        for text in ("Reward:risk  1.69 before costs, 0.84 after costs (1 lot, held all 20 days)",
                     "out by Fri 14 Aug 2:00 AM PKT (5:00 PM New York)",
                     "swap -$54.84 a night (you pay), -$1,535.52 for 28 nights.",
                     "Set before the entry. A stop is a plan, not a guarantee",
                     "Wrote fx/plan.json.", "Nothing was placed."):
            self.assertIn(text, out)
        self.assertEqual(set(p), set(EXPECTED_KEYS))

    def test_day_sell_sample(self):
        self.put("entry.json", eur_entry(self.data))
        out, _ = self.run_exit(EUR_T, "--write")
        p = self.plan_file()
        want = {"symbol": "EURUSD", "side": "sell", "style": "day", "setup": "or_breakdown",
                "fx_day": "2026-07-23", "chart_entry": 1.08452, "chart_stop": 1.0856,
                "entry": 1.08452, "entry_at": "2026-07-23T07:40:00Z", "stop_loss": 1.08561,
                "stop_rule": "or-mid", "stop_label": "opening-range middle 1.08560 + spread",
                "take_profit": 1.08234, "target_rule": "2R", "target_r": 2.0,
                "target_label": "2R (two times the risk)", "risk_per_unit": 0.00109,
                "risk_points": 109, "reward_risk": 2.0, "reward_risk_net": 1.84,
                "atr": EUR_ATR, "atr_interval": "5m", "flat_by": "23:30",
                "flat_by_utc": "2026-07-23T18:30:00Z", "max_hold_days": None, "exit_by": None,
                "data_age_min": 2.0, "warnings": [], "entry_rule": "sell at the market (bid 1.08452)"}
        for k, v in want.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["costs"]["swap_per_night"], EUR_SWAP)
        self.assertEqual(p["costs"]["nights_est"], 0)
        self.assertEqual(p["gap"]["loss_per_lot_usd"], EUR_GAP)
        self.assertEqual(p["opening_range"]["bars"], 3)
        for text in ("2:30 PM New York", "fire on the ask",
                     "Range   London opening range from Thu 12:00 PM PKT (3:00 AM New York), "
                     "15 minutes: 1.08480 to 1.08640",
                     "Stop    1.08561  opening-range middle 1.08560 + spread: 10.9 pips (109 points)",
                     "(Yahoo price, close to your broker's price, 2 min old)"):
            self.assertIn(text, out)

    def test_setup_rule_resolves(self):
        self.put("entry.json", gold_entry(self.data))
        out, _ = self.run_exit(GOLD_T, "--stop", "setup", "--json")
        p = json.loads(out)["plan"]
        self.assertEqual(p["stop_rule"], "atr:2")
        cfg = sample_cfg()
        self.assertEqual(p["stop_loss"], EX.round_away(2433.4 - 2 * GOLD_ATR, 2433.4, EX.instrument(cfg, "XAUUSD")))
        self.assertEqual(p["stop_loss"], 2377.29)
        self.assertEqual(p["stop_label"], "2 x daily ATR (28.05) under the entry")
        self.assertEqual(p["take_profit"], 2545.89)

    def test_sell_swing_by_hand(self):
        cfg = sample_cfg()
        inst = EX.instrument(cfg, "USDJPY")
        bid = EX.latest_price(self.data, inst, EX.parse_now(GOLD_T), cfg)["bid"]
        out, _ = self.run_exit(GOLD_T, "--symbol", "usdjpy", "--side", "sell", "--entry", str(bid),
                               "--style", "swing", "--stop", "atr:2", "--json")
        p = json.loads(out)["plan"]
        self.assertEqual(p["setup"], "manual")
        self.assertEqual(p["chart_entry"], bid)
        raw = bid + 2 * p["atr"] + 0.001 * inst["spread_points"]
        self.assertAlmostEqual(p["stop_loss"], EX.round_away(raw, bid, inst), places=6)
        self.assertGreaterEqual(p["stop_loss"], raw - 1e-9)
        self.assertLess(p["take_profit"], p["entry"])
        self.assertEqual(p["entry_at"], "2026-07-17T04:05:00Z")
        self.assertEqual(p["rolls_applied"], {})


EXPECTED_KEYS = ("created_by", "created_at", "symbol", "side", "style", "setup", "fx_day",
                 "chart_entry", "chart_stop", "entry", "entry_type", "entry_at", "entry_rule",
                 "stop_loss", "stop_rule", "stop_label", "take_profit", "target_rule",
                 "target_r", "target_label", "risk_per_unit", "risk_points", "reward_risk",
                 "reward_risk_net", "spread_points", "atr", "atr_interval", "opening_range",
                 "flat_by", "flat_by_utc", "max_hold_days", "exit_by", "usd_per_point_per_lot",
                 "costs", "gap", "rolls_applied", "data_age_min", "warnings")


class TestRefusals(ExitCase):

    def test_wrong_side_refused(self):
        self.put("entry.json", gold_entry(self.data))
        out, _ = self.run_exit(GOLD_T, "--stop", "price:2440", "--write", code=1)
        self.assertTrue(out.startswith("REFUSED  XAUUSD BUY  swing plan:\n"))
        self.assertIn("- A buy needs the stop under the entry and the target above it.", out)
        self.assertIn("Nothing was written. fx/plan.json was not changed.", out)
        self.assertIsNone(self.plan_file())

    def test_refused_keeps_old_plan(self):
        self.put("entry.json", gold_entry(self.data))
        self.put("plan.json", {"old": True})
        self.run_exit(GOLD_T, "--stop", "price:2440", "--write", code=1)
        self.assertEqual(self.plan_file(), {"old": True})

    def test_stop_too_close_day(self):
        self.put("entry.json", eur_entry(self.data))
        out, _ = self.run_exit(EUR_T, "--stop", "price:1.08460", code=1)
        self.assertIn("The stop is too close", out)
        self.assertIn("- The stop is too close: 0.8 pips from the entry. It must be at least 6.7 pips "
                      "(1 x the 5-minute ATR). Use a wider stop, or skip the trade.", out)
        self.assertNotIn("3 x the spread", out)

    def test_stop_inside_spread_floor(self):
        self.put("entry.json", eur_entry(self.data))
        out, _ = self.run_exit(EUR_T, "--stop", "price:1.08453", code=1)
        self.assertIn("- The stop is too close: 0.1 pips from the entry. It must be at least 0.3 pips "
                      "(3 x the spread).", out)

    def test_stops_level(self):
        def big(doc):
            doc["instruments"]["XAUUSD"]["stops_level_points"] = 4000
        self.edit_config(big)
        self.put("entry.json", gold_entry(self.data))
        out, _ = self.run_exit(GOLD_T, "--stop", "price:2400", "--target", "price:2490", "--write", code=1)
        self.assertIn("Your stop is too close. MetaTrader needs it at least 4,000 points (40.00) "
                      "from the price. It is 3,340 points away.", out)
        self.assertIsNone(self.plan_file())

    def test_not_triggered(self):
        e = gold_entry(self.data)
        e.update(status="waiting", fx_day="2026-07-15", price=None, triggered_at=None, bar=None)
        self.put("entry.json", e)
        _, err = self.run_exit("2026-07-16 09:00", code=2)
        self.assertEqual(err.strip(), "exit.py: XAUUSD has not triggered yet (status: waiting). Nothing to plan.")

    def test_plan_ahead(self):
        e = gold_entry(self.data)
        e.update(status="waiting", fx_day="2026-07-15", price=None, triggered_at=None, bar=None)
        self.put("entry.json", e)
        out, _ = self.run_exit("2026-07-16 09:00", "--entry", "2418.4", "--entry-type", "stop", "--write")
        p = self.plan_file()
        self.assertEqual(p["entry_type"], "stop")
        self.assertEqual(p["entry"], 2418.49)
        self.assertEqual(p["entry_at"], "2026-07-16T04:00:00Z")
        self.assertEqual(p["entry_rule"], "buy stop at 2,418.49 (ask, when the bid reaches 2,418.40)")
        self.assertIn("This plans ahead of the trigger, at 2,418.40. fx-entry has not seen the close yet.", out)

    def test_pending_entry_side(self):
        out, _ = self.run_exit(GOLD_T, "--symbol", "XAUUSD", "--side", "buy", "--entry", "2433.4",
                               "--entry-type", "limit", "--json", code=1)
        j = json.loads(out)
        self.assertEqual(j["refused"], ["A buy limit order must be below the price now (ask 2,433.49)."])
        self.assertFalse(j["ok"])
        self.assertIsNone(j["plan"])
        out, _ = self.run_exit(GOLD_T, "--symbol", "XAUUSD", "--side", "buy", "--entry", "2420",
                               "--entry-type", "limit", "--json")
        p = json.loads(out)["plan"]
        self.assertEqual((p["entry_type"], p["entry"]), ("limit", 2420.09))
        self.assertEqual(p["entry_rule"], "buy limit at 2,420.09 (ask, when the bid reaches 2,420.00)")

    def test_stale_swing_entry(self):
        self.put("entry.json", gold_entry(self.data))
        out, _ = self.run_exit("2026-07-20 09:00", code=1)
        self.assertIn("- The close above 2,418.40 came on Thu 2026-07-16. Fri 2026-07-17 has closed "
                      "since. A late entry is a different trade. Run fx-entry again.", out)
        self.assertIn("has closed since", out)

    def test_day_entry_of_another_day(self):
        self.put("entry.json", eur_entry(self.data))
        out, _ = self.run_exit("2026-07-24 12:42", code=1)
        self.assertIn("- This day entry is from Thu 2026-07-23. A day trade is planned on its own day. "
                      "Run fx-entry again.", out)

    def test_sells_off(self):
        self.edit_config(lambda d: d.update(allow_short=False))
        self.put("entry.json", eur_entry(self.data))
        _, err = self.run_exit(EUR_T, code=2)
        self.assertEqual(err.strip(), 'exit.py: Short trades are off ("allow_short": false).')
        os.remove(os.path.join(self.fx, "entry.json"))
        _, err = self.run_exit(EUR_T, "--symbol", "EURUSD", "--side", "sell", "--entry", "1.08452",
                               "--style", "day", code=2)
        self.assertEqual(err.strip(), "exit.py: Sells are off in your config (allow_short is false).")


def no_network(*a, **k):
    raise AssertionError("the network was used in a test")


class InProcess(ExitCase):
    """Runs EX.main in this process (fast), with every network function swapped for a fake
    that fails the test."""

    def setUp(self):
        ExitCase.setUp(self)
        self.saved = {n: getattr(EX, n) for n in ("yahoo_get", "_one_get", "fetch_chart", "SLEEP")}
        for n in ("yahoo_get", "_one_get", "fetch_chart", "SLEEP"):
            setattr(EX, n, no_network)

    def tearDown(self):
        for n, f in self.saved.items():
            setattr(EX, n, f)
        ExitCase.tearDown(self)

    def call(self, now, *args, code=None, net=False):
        argv = ["plan"] + list(args) + ["--config", os.path.join(self.fx, "config.json"),
                                        "--data", self.data, "--now", now]
        if not net:
            argv.append("--offline")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = EX.main(argv)
        self.outputs.append(out.getvalue() + err.getvalue())
        if code is not None:
            self.assertEqual(got, code, out.getvalue() + err.getvalue())
        return got, out.getvalue(), err.getvalue()

    def error(self, now, *args):
        """Run, expect exit 2 and one stderr line; return the message after "exit.py: "."""
        got, out, err = self.call(now, *args, code=2)
        self.assertEqual(out, "")
        lines = err.strip().split("\n")
        self.assertTrue(lines[-1].startswith("exit.py: "), err)
        return lines[-1][len("exit.py: "):]


class TestErrors(InProcess):

    def test_no_subcommand(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(EX.main([]), 2)
        self.assertIn("plan", out.getvalue())

    def test_no_entry_file(self):
        msg = self.error(GOLD_T)
        self.assertTrue(msg.startswith("no entry at "), msg)
        self.assertTrue(msg.endswith("entry.json. Run fx-entry first. Or give --entry PRICE --side buy|sell."))

    def test_hand_mode_needs(self):
        self.assertEqual(self.error(GOLD_T, "--entry", "2433.4"),
                         "--entry needs --symbol (no entry.json to read it from).")
        self.assertEqual(self.error(GOLD_T, "--entry", "2433.4", "--symbol", "XAUUSD"),
                         "--entry needs --side buy or --side sell (no entry.json to read it from).")
        self.assertEqual(self.error(GOLD_T, "--entry", "-5", "--symbol", "XAUUSD", "--side", "buy"),
                         "--entry must be a price above 0, not -5.")
        self.assertEqual(self.error(GOLD_T, "--entry", "0", "--symbol", "XAUUSD", "--side", "buy"),
                         "--entry must be a price above 0, not 0.")
        self.assertEqual(self.error(GOLD_T, "--entry", "2433.4", "--symbol", "XAUSD", "--side", "buy"),
                         "XAUSD is not in your instruments. Did you mean XAUUSD?")

    def test_entry_file_checks(self):
        self.put("entry.json", gold_entry(self.data))
        msg = self.error(GOLD_T, "--symbol", "USDJPY")
        self.assertIn("--symbol USDJPY is not the symbol in ", msg)
        self.assertIn("(XAUUSD). Give --entry PRICE --side buy|sell to plan it by hand.", msg)
        self.assertIn("is a buy. Leave out --side, or plan by hand with --symbol, --entry and --side.",
                      self.error(GOLD_T, "--side", "sell"))
        self.assertIn("is a swing trade. Leave out --style, or plan by hand",
                      self.error(GOLD_T, "--style", "day"))
        for key, bad, words in (("side", "long", 'side must be "buy" or "sell", not "long"'),
                                ("style", "week", 'style must be "swing" or "day", not "week"'),
                                ("triggered_at", "07-16", '"triggered_at" is not a UTC time'),
                                ("level", -1, '"level" must be a price above 0, not -1'),
                                ("symbol", "XAUXAU", '"XAUXAU" is not in your instrument list')):
            e = gold_entry(self.data)
            e[key] = bad
            self.put("entry.json", e)
            self.assertIn(words, self.error(GOLD_T))
        with open(os.path.join(self.fx, "entry.json"), "w") as fh:
            fh.write("{broken")
        self.assertIn("is not valid JSON", self.error(GOLD_T))
        with open(os.path.join(self.fx, "entry.json"), "w") as fh:
            fh.write("[1, 2]")
        self.assertIn("must be a JSON object: {...}", self.error(GOLD_T))

    def test_rule_errors(self):
        self.put("entry.json", gold_entry(self.data))
        cases = (("--stop", "pct:1", '--stop wants atr:K, or-mid, or-far, swing-low, swing-high, flag-far, '
                  'bar-far, setup or price:X, not "pct:1".'),
                 ("--stop", "atr:12", "--stop atr:K needs K from 0.5 to 10, not 12."),
                 ("--target", "3", '--target wants KR (like 2R or 1.5R) or price:X, not "3".'),
                 ("--target", "0.2R", "--target KR needs K from 0.5 to 10, not 0.2."),
                 ("--stop", "or-mid", "or-mid is a day-trade stop. For a swing trade use atr:K, "
                  "swing-low, swing-high or price:X."),
                 ("--stop", "swing-high", "swing-high is a stop over the price, for a sell."),
                 ("--stop", "flag-far", "flag-far is the stop for a bull or bear flag. This setup is "
                  "a 20-day high breakout. Use atr:K, swing-low or price:X."),
                 ("--stop", "bar-far", "bar-far is the stop for an inside day. This setup is a "
                  "20-day high breakout. Use atr:K, swing-low or price:X."),
                 ("--flat-by", "22:00", "--flat-by is for day trades. A swing trade ends by its "
                  "hold days (--hold-days)."),
                 ("--hold-days", "0", "--hold-days must be 1 to 120, not 0."),
                 ("--hold-days", "121", "--hold-days must be 1 to 120, not 121."))
        for flag, value, msg in cases:
            self.assertEqual(self.error(GOLD_T, flag, value), msg)

    def test_day_rule_errors(self):
        self.put("entry.json", eur_entry(self.data))
        self.assertEqual(self.error(EUR_T, "--stop", "swing-low"),
                         "swing-low is a swing stop. For a day trade use or-mid, or-far, atr:K or price:X.")
        self.assertEqual(self.error(EUR_T, "--hold-days", "3"),
                         "--hold-days is for swing trades. A day trade ends at its flat-by time (--flat-by).")
        self.assertEqual(self.error(EUR_T, "--flat-by", "25:00"), "--flat-by wants a time like 23:30")

    def test_hand_plan_rules(self):
        args = ("--symbol", "XAUUSD", "--side", "sell", "--entry", "2433.4")
        self.assertEqual(self.error(GOLD_T, *(args + ("--stop", "setup"))),
                         "This plan has no setup (it was made by hand). Name a stop: atr:K or price:X.")
        self.assertEqual(self.error(GOLD_T, *(args + ("--stop", "swing-low"))),
                         "swing-low is a stop under the price, for a buy.")
        self.assertEqual(self.error(GOLD_T, *(args + ("--stop", "bar-far"))),
                         "bar-far is the stop for an inside day. This plan was made by hand. "
                         "Use atr:K, swing-high or price:X.")
        msg = self.error("2026-07-23 12:05", "--symbol", "EURUSD", "--side", "sell", "--entry",
                         "1.0845", "--style", "day")
        self.assertEqual(msg, "This entry has no full opening range. Use atr:K or price:X.")


class TestDayTimes(InProcess):

    def test_early_close_day(self):
        # (check) comex is open that morning: the early close is at 13:00 New York
        cfg = sample_cfg()
        bid = EX.latest_price(self.data, EX.instrument(cfg, "XAUUSD"), EX.parse_now("2026-07-03 14:00"), cfg)["bid"]
        got, out, _ = self.call("2026-07-03 14:00", "--symbol", "XAUUSD", "--side", "buy", "--entry",
                                str(bid), "--style", "day", "--stop", "atr:2", "--json", code=0)
        j = json.loads(out)
        self.assertEqual(j["plan"]["flat_by_utc"], "2026-07-03T16:45:00Z")
        self.assertIn("Gold stops early today, at 1:00 PM New York (10:00 PM PKT). Flat by 9:45 PM PKT.",
                      j["notes"])
        self.assertIn("The newest Yahoo price is 5 h old and the market is open. The entry may be off.",
                      j["plan"]["warnings"])

    def test_past_cutoff(self):
        got, out, _ = self.call("2026-07-03 22:00", "--symbol", "XAUUSD", "--side", "buy", "--entry",
                                "2416.3", "--style", "day", "--stop", "atr:2", code=1)
        self.assertIn("- It is past 9:00 PM PKT, the last time for a new day trade today. No day plan.", out)

    def test_too_little_time(self):
        got, out, _ = self.call("2026-07-23 20:50", "--symbol", "EURUSD", "--side", "sell", "--entry",
                                "1.085", "--entry-type", "stop", "--style", "day", "--stop", "price:1.0875",
                                "--flat-by", "21:00", code=1)
        self.assertIn("- Only 10 minutes are left before the flat-by time (9:00 PM PKT). No day plan.", out)

    def test_market_not_open(self):
        got, out, _ = self.call("2026-07-18 12:00", "--symbol", "EURUSD", "--side", "sell", "--entry",
                                "1.0845", "--style", "day", "--stop", "atr:3", code=1)
        self.assertIn("- The market is not open now (Closed for the weekend). No day plan for a market entry.", out)

    def test_closed_all_day(self):
        cfg = sample_cfg()
        inst = EX.instrument(cfg, "XAUUSD")
        t = datetime.datetime(2026, 4, 3, 5, 0)   # Good Friday: CME shut, FX open
        with self.assertRaises(EX.Refused) as ctx:
            EX.day_times(inst, "2026-04-03", "23:30", t, t, cfg, "market", False)
        self.assertEqual(ctx.exception.lines, ["Gold is closed all day today. No day trade."])
        out = EX.day_times(EX.instrument(cfg, "EURUSD"), "2026-04-03", "23:30", t, t, cfg, "stop", True)
        self.assertEqual(out["flat_by_utc"], datetime.datetime(2026, 4, 3, 18, 30))

    def test_flat_by_flag_checks(self):
        self.put("entry.json", eur_entry(self.data))
        self.assertEqual(self.error(EUR_T, "--flat-by", "02:55"),
                         "--flat-by 02:55 falls in the first hour of the FX day (that hour starts at "
                         "2:00 AM PKT). Pick a time like 23:30.")
        self.assertEqual(self.error(EUR_T, "--flat-by", "01:50"),
                         "--flat-by 01:50 is too close to the 17:00 New York rollover today "
                         "(2:00 AM PKT). Pick 01:45 or earlier.")
        got, out, _ = self.call(EUR_T, "--flat-by", "22:00", "--json", code=0)
        self.assertEqual(json.loads(out)["plan"]["flat_by_utc"], "2026-07-23T17:00:00Z")
        self.put("entry.json", gold_entry(self.data))
        self.assertEqual(self.error(GOLD_T, "--flat-by", "22:00"),
                         "--flat-by is for day trades. A swing trade ends by its hold days (--hold-days).")

    def test_late_day_entry(self):
        self.put("entry.json", eur_entry(self.data))
        got, out, _ = self.call("2026-07-23 14:10", "--json", code=0)
        p = json.loads(out)["plan"]
        self.assertEqual(p["entry_at"], "2026-07-23T09:10:00Z")
        self.assertIn("The trigger came at 12:40 PM PKT. The newest price is from 2:10 PM PKT, so the plan "
                      "starts there. A late entry is a different trade.", p["warnings"])


class TestMore(InProcess):

    def test_coarse_note(self):
        self.put("entry.json", eur_entry(self.data))
        got, out, _ = self.call(EUR_T, "--stop", "atr:1", "--json", code=0)
        p = json.loads(out)["plan"]
        note = ("Yahoo prices for EURUSD are rough (about one price a minute). A stop under 10 pips "
                "can be hit without Yahoo showing it.")
        if p["risk_points"] < 100:
            self.assertIn(note, p["warnings"])
        else:
            self.skipTest("the 1 x ATR stop is %d points, not under 100" % p["risk_points"])
        self.assertEqual(p["risk_points"], 68)   # (m) 1 x 0.00067 + the spread

    def test_roll_rebase_in_memory(self):
        cfg = sample_cfg()
        inst = EX.instrument(cfg, "XAUUSD")
        now = EX.parse_now("2026-07-29 13:15")
        bars = EX.load_bars(self.data, "GC=F", "1h", now, rolls=[], inst=inst, cfg=cfg)
        d = EX.daily_from_hourly(bars, cfg["day_end"], inst, cfg, now=now)[-1]
        e = gold_entry(self.data)
        e.update(fx_day=d["fx_day"], triggered_at=EX.utc_iso(d["end"]), price=d["close"],
                 level=round(d["close"] - 5, 1), rolls_applied={})
        e["bar"].update(start=EX.utc_iso(d["start"]), open=d["open"], high=d["high"], low=d["low"],
                        close=d["close"])
        self.put("entry.json", e)
        path = os.path.join(self.fx, "entry.json")
        with open(path, "rb") as fh:
            before = fh.read()
        got, out, _ = self.call("2026-07-29 13:15", "--stop", "atr:2", "--write", code=0)
        self.assertIn("Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same amount. "
                      "Your broker's XAUUSD did not jump: keep the SL and TP you set in MetaTrader.", out)
        p = self.plan_file()
        self.assertEqual(list(p["rolls_applied"]), ["GC-2026-07-29"])
        self.assertAlmostEqual(p["rolls_applied"]["GC-2026-07-29"], 2500.3 / 2478.0, places=9)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_json_shape(self):
        self.put("entry.json", gold_entry(self.data))
        got, out, err = self.call(GOLD_T, "--stop", "price:2400", "--target", "price:2490", "--json", code=0)
        j = json.loads(out)
        self.assertEqual(set(j), set("v tool cmd now ok notes doc plan written refused".split()))
        self.assertEqual((j["v"], j["tool"], j["cmd"], j["now"], j["ok"]), (1, "fx-exit", "plan", "2026-07-17T04:05:00Z", True))
        self.assertIsNone(j["doc"])
        self.assertFalse(j["written"])
        self.assertEqual(j["refused"], [])
        self.assertEqual(j["plan"]["entry"], 2433.49)
        self.assertIn("After costs this trade wins 0.84 times what it risks. Your minimum is 1.5.", j["notes"])
        self.assertIsNone(self.plan_file())
        got, out, err = self.call(GOLD_T, "--stop", "price:2400", "--target", "price:2490", "--json",
                                  "--write", code=0)
        j = json.loads(out)
        self.assertTrue(j["written"])
        self.assertEqual(j["doc"], self.plan_file())

    def test_not_saved_and_quiet(self):
        self.put("entry.json", gold_entry(self.data))
        got, out, _ = self.call(GOLD_T, "--stop", "price:2400", code=0)
        self.assertIn("Not saved. Run again with --write to save ", out)
        self.assertIn("Next: python3 fx-exit/exit.py plan --stop price:2400 ", out)
        self.assertTrue(out.rstrip().endswith("--write"))
        got, out, _ = self.call(GOLD_T, "--stop", "price:2400", "--quiet", code=0)
        lines = out.strip().split("\n")
        self.assertEqual(len(lines), 6, out)
        self.assertTrue(lines[0].startswith("PLAN  XAUUSD BUY"))
        self.assertTrue(lines[4].startswith("  Hold    up to 20 FX days"))

    def test_no_bars(self):
        shutil.rmtree(self.data)
        os.makedirs(self.data)
        self.put("entry.json", gold_entry(os.path.join(FIX, "data")))
        msg = self.error(GOLD_T)
        self.assertTrue(msg.startswith("No hourly prices for XAUUSD yet ("), msg)
        self.assertTrue(msg.endswith("GC_F/1h.json). Run without --offline once to get them."))
        self.put("entry.json", eur_entry(os.path.join(FIX, "data")))
        self.assertIn("No 5-minute prices for EURUSD yet (", self.error(EUR_T))

    def test_no_price_and_no_atr(self):
        cache = os.path.join(self.data, "yahoo", "USDJPY_X")
        shutil.rmtree(cache)
        self.assertIn("No hourly prices for USDJPY yet",
                      self.error(GOLD_T, "--symbol", "USDJPY", "--side", "buy", "--entry", "145"))
        os.makedirs(cache)
        src = os.path.join(FIX, "data", "yahoo", "USDJPY_X", "1h.json")
        with open(src) as fh:
            doc = json.load(fh)
        cut = EX.utc_to_ts(EX.parse_now(GOLD_T)) - 3 * 86400
        doc["bars"] = [b for b in doc["bars"] if b[0] > cut]
        doc["fetches"] = []
        with open(os.path.join(cache, "1h.json"), "w") as fh:
            json.dump(doc, fh)
        self.assertEqual(self.error(GOLD_T, "--symbol", "USDJPY", "--side", "buy", "--entry", "145"),
                         "Too few bars for an ATR of USDJPY (need 15 usable bars).")
        saved = EX.latest_price
        EX.latest_price = lambda *a, **k: None
        try:
            msg = self.error(GOLD_T, "--symbol", "XAUUSD", "--side", "buy", "--entry", "2433.4")
        finally:
            EX.latest_price = saved
        self.assertTrue(msg.startswith("No Yahoo price for XAUUSD in "), msg)
        self.assertTrue(msg.endswith("yahoo/. Run without --offline once to get it."), msg)

    def test_missing_rate(self):
        shutil.rmtree(os.path.join(self.data, "yahoo", "USDJPY_X"))
        cfg = sample_cfg()
        inst = EX.instrument(cfg, "EURJPY")
        msg = self.error(GOLD_T, "--symbol", "EURJPY", "--side", "buy", "--entry",
                         str(EX.latest_price(self.data, inst, EX.parse_now(GOLD_T), cfg)["bid"]))
        self.assertTrue(msg.startswith("No USD rate for JPY. It comes from USDJPY."), msg)


def made_up_days(n_flat, tail):
    """Made-up daily bars: n_flat quiet days (100 +- 1, a slow wobble), then the tail
    (open, high, low, close) rows. Monday to Friday from Mon 2026-01-05."""
    rows = []
    for i in range(n_flat):
        c = 100.0 + (0.3 if i % 2 else -0.3)
        rows.append((100.0, 101.0, 99.0, c))
    rows += list(tail)
    out, d = [], datetime.date(2026, 1, 5)
    for o, h, l, c in rows:
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        s, e = EX.fx_day_bounds(d.isoformat(), "17:00 New York")
        out.append({"fx_day": d.isoformat(), "start": s, "end": e, "open": o, "high": h,
                    "low": l, "close": c, "hours": 23, "flags": []})
        d += datetime.timedelta(days=1)
    return out


class TestSetupBar(unittest.TestCase):

    def test_bar_far_finds_the_inside_day(self):
        cfg = sample_cfg()
        inst = EX.instrument(cfg, "USDJPY")
        days = made_up_days(70, [(100.0, 103.0, 97.0, 100.2),     # the mother bar
                                 (100.2, 100.5, 99.8, 100.1),     # inside day and NR7
                                 (100.1, 101.2, 100.0, 101.0)])   # the close above 100.5
        hits = EX.find_setups(days[:-1], inst, cfg, ("buy",))
        self.assertIn(("inside_break_up", 100.5), [(h["setup"], h["level"]) for h in hits])
        sb = EX.find_setup_bar(days, "inside_break_up", "buy", 100.5, days[-1]["fx_day"], inst, cfg)
        self.assertEqual(sb["bar"]["fx_day"], days[-2]["fx_day"])
        raw, label = EX.chart_stop_for("bar-far", None, "buy", 101.0, 0.8, [], None, sb, inst)
        self.assertAlmostEqual(raw, 99.8 - 0.25 * 0.8)
        self.assertEqual(label, "inside day low 99.800 - 0.25 ATR")
        self.assertIsNone(EX.find_setup_bar(days, "inside_break_up", "buy", 100.7, days[-1]["fx_day"], inst, cfg))

    def test_flag_far_and_swing_words(self):
        cfg = sample_cfg()
        inst = EX.instrument(cfg, "XAUUSD")
        sb = {"bar": {"low": 2390.0, "high": 2410.0}, "flag_low": 2388.2, "flag_high": 2412.0}
        raw, label = EX.chart_stop_for("flag-far", None, "buy", 2420.0, 20.0, [], None, sb, inst)
        self.assertEqual((raw, label), (2388.2 - 5.0, "flag low 2,388.20 - 0.25 ATR"))
        raw, label = EX.chart_stop_for("flag-far", None, "sell", 2380.0, 20.0, [], None, sb, inst)
        self.assertEqual((raw, label), (2412.0 + 5.0, "flag high 2,412.00 + 0.25 ATR"))
        with self.assertRaises(ValueError) as ctx:
            EX.chart_stop_for("flag-far", None, "buy", 2420.0, 20.0, [], None, None, inst)
        self.assertEqual(str(ctx.exception), "The bars behind this flag are not in the saved prices. "
                                             "Use atr:K, swing-low or price:X.")
        B = [{"low": 2401.1 + i, "high": 2420.0 + i} for i in range(5)]
        raw, label = EX.chart_stop_for("swing-low", None, "buy", 2433.4, 28.0, B, None, None, inst)
        self.assertEqual(label, "lowest low of 5 days (2,401.10) - 0.25 ATR")
        self.assertAlmostEqual(raw, 2401.1 - 7.0)


class TestNetwork(unittest.TestCase):
    """fetch_fresh with fakes: never the real network."""

    def test_fetch_order_and_failed_note(self):
        cfg = sample_cfg()
        calls = []

        def fake_refresh(data, symbol, interval, now, cfg_, offline):
            calls.append((symbol, interval, offline))
            if symbol == "GC=F":
                raise ValueError("Yahoo said 503")
            return {}

        saved = (EX.refresh, EX.check_roll_forward)
        EX.refresh = fake_refresh
        EX.check_roll_forward = lambda *a, **k: calls.append(("roll",)) or None
        try:
            notes = EX.fetch_fresh({"data": "nowhere"}, EX.instrument(cfg, "XAUUSD"), cfg,
                                   datetime.datetime(2026, 7, 17, 4, 5), "swing")
            self.assertEqual(calls, [("GC=F", "1h", False)])
            self.assertEqual(notes, ["Yahoo did not answer for GC=F (Yahoo said 503). Using the saved prices."])
            del calls[:]
            EX.fetch_fresh({"data": "nowhere"}, EX.instrument(cfg, "EURJPY"), cfg,
                           datetime.datetime(2026, 7, 23, 7, 42), "day")
            self.assertEqual(calls, [("EURJPY=X", "5m", False), ("USDJPY=X", "1h", False),
                                     ("EURUSD=X", "1h", False)])
        finally:
            EX.refresh, EX.check_roll_forward = saved


class TestWords(ExitCase):

    def test_no_advice_words(self):
        self.put("entry.json", gold_entry(self.data))
        texts = [self.run_exit(GOLD_T, "--stop", "price:2400", "--target", "price:2490")[0],
                 self.run_exit(GOLD_T, "--stop", "price:2440", code=1)[0],
                 self.run_exit("2026-07-20 09:00", code=1)[0]]
        self.put("entry.json", eur_entry(self.data))
        texts += [self.run_exit(EUR_T)[0], self.run_exit(EUR_T, "--stop", "price:1.08460", code=1)[0],
                  self.run_exit(EUR_T, "--json")[0], self.run_exit(EUR_T, "--quiet")[0]]
        for text in texts:
            self.assertNotIn("!", text)
        for name in ("exit.py", "SKILL.md"):
            with open(os.path.join(ROOT, "fx-exit", name), encoding="utf-8") as fh:
                texts.append(fh.read())
        for text in texts:
            low = text.lower().replace("not a recommendation", "")
            for word in BANNED:
                self.assertNotIn(word, low)
            self.assertNotIn(chr(0x2014), text)

    def test_skill_md(self):
        with open(os.path.join(ROOT, "fx-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: fx-exit\n"))
        head = text.split("---")[1]
        desc = head.split("description: >-", 1)[1].split("argument-hint:")[0]
        self.assertGreater(len(" ".join(desc.split())), 200)
        heads = [l for l in text.split("\n") if l.startswith("## ")]
        self.assertEqual(heads[-2:], ["## What it never does", "## Safety"])
        spec_path = os.path.join(ROOT, "..", "fx-work", "spec", "06-cli-safety-tests.md")
        if not os.path.exists(spec_path):
            self.skipTest("the spec is not next to the pack")
        with open(spec_path, encoding="utf-8") as fh:
            spec = fh.read()
        block = spec.split("```markdown\n", 1)[1].split("```", 1)[0]
        self.assertEqual(text[text.index("## Safety"):].strip(), block.strip())


class TestSwingExtras(InProcess):

    def test_hold_days_and_closed_note(self):
        got, out, _ = self.call("2026-07-18 12:00", "--symbol", "EURUSD", "--side", "buy", "--entry",
                                "1.0845", "--hold-days", "5", "--json", code=0)
        p = json.loads(out)["plan"]
        self.assertEqual((p["fx_day"], p["max_hold_days"], p["exit_by"]),
                         ("2026-07-20", 5, "2026-07-24T21:00:00Z"))
        self.assertEqual(p["costs"]["nights_est"], 7)
        self.assertEqual(p["warnings"], ["The market is closed now. It opens Mon 2:05 AM PKT. "
                                         "The price then can be far from this plan."])

    def test_swap_free_words(self):
        self.edit_config(lambda d: d["account"].update(swap_free=True))
        self.put("entry.json", gold_entry(self.data))
        got, out, _ = self.call(GOLD_T, "--stop", "price:2400", "--target", "price:2490", code=0)
        flat = " ".join(out.split())
        self.assertIn("No swap (swap-free account). Swap-free fee $45.00 a night after 6 free nights: "
                      "$990.00 for 28 nights.", flat)
