"""Tests for fx-alerts/alerts.py, offline, on the made-up sample market (spec 06 5).
Every command line passes --offline --now; the network helpers are swapped for fakes
that fail the test if they are ever called."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import datetime
import json
import shutil
import subprocess
import tempfile
import time
import unittest

import _load

ROOT = _load.ROOT
SCRIPT = os.path.join(ROOT, "fx-alerts", "alerts.py")
MARKET = os.path.join(_load.FIX, "fx-market")
A = _load.load("fx-alerts", "alerts")
D = datetime.datetime


def no_network(*args, **kwargs):
    raise AssertionError("a test must never use the network")


A.yahoo_get = no_network
A.calendar_get = no_network
A.SLEEP = no_network

GOLD_PLAN = {
    "symbol": "XAUUSD", "side": "buy", "style": "swing", "setup": "breakout_20d",
    "entry": 2433.49, "entry_type": "market", "stop_loss": 2400.0, "take_profit": 2490.0,
    "stop_rule": "price", "stop_label": "your price 2,400.00", "target_rule": "price",
    "target_label": "your price 2,490.00", "risk_points": 3349, "flat_by": None,
    "flat_by_utc": None, "max_hold_days": 20, "exit_by": "2026-08-13T21:00:00Z",
    "rolls_applied": {}}
EUR_PLAN = {
    "symbol": "EURUSD", "side": "sell", "style": "day", "setup": "or_breakdown",
    "entry": 1.08452, "entry_type": "market", "stop_loss": 1.08561, "take_profit": 1.08234,
    "stop_rule": "or-mid", "stop_label": "opening-range middle", "target_rule": "2R",
    "target_label": "2R", "risk_points": 109, "flat_by": "23:30",
    "flat_by_utc": "2026-07-23T18:30:00Z", "max_hold_days": None, "exit_by": None,
    "rolls_applied": {}}
ACCOUNT = {"currency": "USD", "balance": 10000.0, "start_balance": 10000.0,
           "realized_usd": 0.0, "paper": True}


def position(pid, plan, lots, opened):
    """A portfolio position as fx-broker writes it after a paper fill."""
    return {"id": pid, "symbol": plan["symbol"], "side": plan["side"], "style": plan["style"],
            "lots": lots, "fingerprint": "0123456789abcdef", "paper": True,
            "opened_at": opened, "fx_day_opened": opened[:10], "entry": plan["entry"],
            "fill_price": plan["entry"], "broker_fill_price": None,
            "stop_loss": plan["stop_loss"], "take_profit": plan["take_profit"],
            "stop_at_open": plan["stop_loss"], "broker_stop_loss": None,
            "broker_take_profit": None, "max_loss_usd": 97.81,
            "flat_by_utc": plan["flat_by_utc"], "exit_by": plan["exit_by"],
            "rolls_applied": {}, "swap_nights": 0, "swap_usd": 0.0}


GOLD_POS = position("XAUUSD-1", GOLD_PLAN, 0.02, "2026-07-17T04:11:00Z")
EUR_POS = position("EURUSD-1", EUR_PLAN, 0.86, "2026-07-23T07:45:00Z")


class Project(object):
    """A temp folder whose fx/ holds the sample data and config (as test_chain)."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="fx-alerts-test-")
        fx = os.path.join(self.dir, "fx")
        os.makedirs(fx)
        shutil.copytree(os.path.join(MARKET, "data"), os.path.join(fx, "data"))
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(fx, "config.json"))

    def path(self, name):
        return os.path.join(self.dir, "fx", name)

    def write(self, name, doc):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)

    def read(self, name):
        with open(self.path(name), encoding="utf-8") as fh:
            return json.load(fh)

    def portfolio(self, *positions):
        self.write("portfolio.json", {"created_by": "fx-broker", "updated_at": "2026-07-17T04:11:00Z",
                                      "account": dict(ACCOUNT), "positions": [copy.deepcopy(p) for p in positions],
                                      "closed": []})

    def run(self, now, *args):
        """Run alerts.py in the folder; returns (code, stdout, stderr, seconds)."""
        cmd = [sys.executable, SCRIPT] + list(args) + ["--offline", "--now", now]
        t0 = time.time()
        p = subprocess.run(cmd, cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=300)
        return p.returncode, p.stdout, p.stderr, time.time() - t0

    def rules(self):
        return self.read("alerts.json")["rules"]

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Chain(unittest.TestCase):
    """The README story through the real command lines, in time order (06 6.2).
    setUpClass runs every step once; each test checks one step."""

    @classmethod
    def setUpClass(cls):
        p = cls.p = Project()
        cls.out = {}
        cls.snap = {}
        p.write("plan.json", GOLD_PLAN)
        p.portfolio(GOLD_POS)
        cls.out["add_gold"] = p.run("2026-07-17 09:12", "add", "--from-plan")
        cls.snap["rules_gold"] = p.rules()
        cls.out["quote"] = p.run("2026-07-17 09:12", "quote", "XAUUSD", "EURUSD")
        cls.snap["prices"] = p.read("prices.json")
        cls.out["broker"] = p.run("2026-07-17 09:12", "quote", "XAUUSD", "--broker-price", "2429.15")
        cls.snap["prices_broker"] = p.read("prices.json")
        cls.out["stale"] = p.run("2026-07-21 15:00", "check")
        cls.snap["rules_stale"] = p.rules()
        p.write("plan.json", EUR_PLAN)
        p.portfolio(GOLD_POS, EUR_POS)
        cls.out["add_eur"] = p.run("2026-07-23 12:45", "add", "--from-plan")
        cls.snap["rules_eur"] = p.rules()
        cls.out["clock"] = p.run("2026-07-23 12:41", "clock")
        cls.out["quiet"] = p.run("2026-07-23 14:00", "check")
        cls.out["target"] = p.run("2026-07-23 15:20", "check")
        cls.snap["state_target"] = p.read("state/alerts.json")
        cls.out["once"] = p.run("2026-07-23 15:25", "check")
        p.portfolio(GOLD_POS)        # fx-broker closed EURUSD-1 at 15:22
        cls.out["closed"] = p.run("2026-07-23 23:20", "check")
        cls.snap["rules_closed"] = p.rules()
        cls.out["weekend"] = p.run("2026-07-25 12:00", "clock")
        cls.out["roll"] = p.run("2026-07-29 13:15", "check")
        cls.snap["rules_roll"] = p.rules()
        cls.snap["portfolio_roll"] = p.read("portfolio.json")
        cls.snap["broker_roll"] = p.read("state/broker.json")
        cls.snap["state_roll"] = p.read("state/alerts.json")
        cls.out["roll_once"] = p.run("2026-07-29 13:20", "check")
        cls.snap["broker_once"] = p.read("state/broker.json")
        cls.out["rebased"] = p.run("2026-07-30 19:45", "check")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def by_kind(self, rules, symbol):
        return {r["kind"]: r for r in rules if r["symbol"] == symbol}

    def test_from_plan_swing(self):
        code, out, err, _ = self.out["add_gold"]
        self.assertEqual(code, 0, err)
        rules = self.snap["rules_gold"]
        self.assertEqual([r["kind"] for r in rules], ["stop", "target", "exit_by", "friday_close"])
        k = self.by_kind(rules, "XAUUSD")
        self.assertEqual(k["stop"]["level"], 2400.0)
        self.assertEqual(k["target"]["level"], 2490.0)
        self.assertEqual(k["exit_by"]["at_utc"], "2026-08-13T21:00:00Z")
        self.assertEqual(k["exit_by"]["minutes_before"], 60)
        self.assertEqual(k["friday_close"]["at_utc"], "2026-07-17T16:00:00Z")
        for r in rules:
            self.assertEqual(r["added_at"], "2026-07-17T04:12:00Z")
            self.assertEqual(r["position_id"], "XAUUSD-1")
            self.assertEqual(r["side"], "buy")
            self.assertEqual(r["from"], "plan")
            self.assertEqual(set(A.RULE_KEYS), set(r))
        self.assertIn("Added a1: XAUUSD stop 2,400.00 for your BUY XAUUSD-1 (it fires on the bid).", out)
        self.assertIn("Added a3: XAUUSD exit by Fri 14 Aug 2:00 AM PKT (5:00 PM New York), 60 min early.", out)
        self.assertIn("Added a4: XAUUSD Friday check at Fri 17 Jul 9:00 PM PKT (12:00 PM New York).", out)
        self.assertIn("These are Yahoo prices (gold futures), not your broker's.", out)

    def test_from_plan_day(self):
        code, out, err, _ = self.out["add_eur"]
        self.assertEqual(code, 0, err)
        k = self.by_kind(self.snap["rules_eur"], "EURUSD")
        self.assertEqual(sorted(k), ["flat_by", "stop", "target"])
        self.assertEqual(k["stop"]["level"], 1.08561)
        self.assertEqual(k["target"]["level"], 1.08234)
        self.assertEqual(k["flat_by"]["at_utc"], "2026-07-23T18:30:00Z")
        self.assertEqual(k["flat_by"]["minutes_before"], 15)
        self.assertEqual([r["id"] for r in self.snap["rules_eur"]][4:], ["a5", "a6", "a7"])
        self.assertIn("(it fires on the ask: the Yahoo bid plus the spread)", out)

    def test_stale(self):
        code, out, err, _ = self.out["stale"]
        self.assertEqual(code, 1, err)
        self.assertTrue(out.startswith("Nothing hit. 4 rules watched at Tue 3:00 PM PKT (6:00 AM New York)."))
        self.assertIn("stale", out)
        self.assertIn("8 h old", out)
        self.assertIn("Missed: the Friday 12:00 PM New York check (Fri 07-17).", out)
        self.assertNotIn("ALERT", out)
        # (m) measured on the first green build of the sample market
        self.assertIn("  XAUUSD  2,451.00  Yahoo price (gold futures), not your broker's price, 8 h old", out)
        self.assertIn("Note: the gold price is stale: no new Yahoo price since Tue 7:00 AM PKT (8 h old).", out)
        fri = self.by_kind(self.snap["rules_stale"], "XAUUSD")["friday_close"]
        self.assertEqual(fri["at_utc"], "2026-07-24T16:00:00Z")

    def test_quiet(self):
        code, out, err, _ = self.out["quiet"]
        self.assertEqual(code, 1, err)
        self.assertTrue(out.startswith("Nothing hit."))

    def test_sell_target_on_the_ask(self):
        code, out, err, _ = self.out["target"]
        self.assertEqual(code, 0, err)
        self.assertIn("ALERT EURUSD target 1.08234: the ask fell to 1.08222 in the 3:15 PM PKT 5-minute bar.", out)
        self.assertIn("Your SELL's take profit fires on the ask (the Yahoo bid plus the 0.1 pip spread).", out)
        self.assertIn("  EURUSD 1.08262 now (Yahoo price, close to your broker's price, live)", out)   # (m)
        self.assertIn("python3 fx-broker/ticket.py close --position EURUSD-1", out)
        self.assertIn("An alert is information, not an order. Nothing was closed.", out)
        rang = self.snap["state_target"]["rang"]["a6"]
        self.assertEqual(rang, {"at": "2026-07-23T10:15:00Z", "price": 1.08222, "fx_day": "2026-07-23"})

    def test_once(self):
        code, out, err, _ = self.out["once"]
        self.assertEqual(code, 1, err)
        self.assertNotIn("ALERT", out)

    def test_closed_position(self):
        code, out, err, _ = self.out["closed"]
        self.assertEqual(code, 1, err)
        self.assertIn("Note: alerts a5, a6, a7 are off: EURUSD-1 is closed.", out)
        self.assertNotIn("flat by", out)
        k = self.by_kind(self.snap["rules_closed"], "EURUSD")
        self.assertEqual([k[x]["active"] for x in ("stop", "target", "flat_by")], [False, False, False])

    def test_roll(self):
        code, out, err, _ = self.out["roll"]
        self.assertEqual(code, 1, err)
        self.assertIn("Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same amount. "
                      "Your broker's XAUUSD did not jump: keep the SL and TP you set in MetaTrader.", out)
        self.assertIn("Nothing hit.", out)
        self.assertIn("Missed: the Friday 12:00 PM New York check (Fri 07-24).", out)
        self.assertNotIn("ALERT", out)
        k = self.by_kind(self.snap["rules_roll"], "XAUUSD")
        self.assertEqual(k["target"]["level"], 2512.41)
        self.assertEqual(k["stop"]["level"], 2421.59)
        self.assertEqual(k["friday_close"]["at_utc"], "2026-07-31T16:00:00Z")
        pos = self.snap["portfolio_roll"]["positions"][0]
        self.assertEqual(pos["stop_loss"], 2421.59)
        self.assertEqual(pos["take_profit"], 2512.41)
        self.assertEqual(list(pos["rolls_applied"]), ["GC-2026-07-29"])
        self.assertAlmostEqual(pos["rolls_applied"]["GC-2026-07-29"], 2500.3 / 2478.0, places=9)
        self.assertEqual(pos["broker_stop_loss"], None)
        recs = self.snap["broker_roll"]["records"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "rebase")
        self.assertIsNone(recs[0]["fingerprint"])
        self.assertEqual(recs[0]["roll_id"], "GC-2026-07-29")
        self.assertEqual(recs[0]["price"], 2421.59)
        self.assertEqual(recs[0]["plain"], "Gold rolled on Yahoo (+0.90%). XAUUSD-1 moved: stop 2,421.59, target 2,512.41.")
        rang = self.snap["state_roll"]["rang"]
        self.assertEqual(rang["a4@2026-07-17"], {"at": "2026-07-21T10:00:00Z", "price": None,
                                                 "fx_day": "2026-07-17", "missed": True})
        self.assertEqual(rang["a4@2026-07-24"], {"at": "2026-07-29T08:15:00Z", "price": None,
                                                 "fx_day": "2026-07-24", "missed": True})
        self.assertEqual(self.snap["state_roll"]["seen"]["XAUUSD"], "2026-07-29T08:15:00Z")
        self.assertEqual(self.snap["state_roll"]["checked_at"], "2026-07-29T08:15:00Z")

    def test_roll_bar_does_not_ring(self):
        # the raw 07:00 UTC bar crosses the old target 2,490.00; with no rebase it would ring
        inst = A.instrument(A.load_config(self.p.path("config.json")), "XAUUSD")
        cfg = A.load_config(self.p.path("config.json"))
        raw = A.load_bars(self.p.path("data"), "GC=F", "1h", A.parse_now("2026-07-29 13:15"), inst=inst, cfg=cfg)
        bar = [b for b in raw if b["start"] == D(2026, 7, 29, 7)][0]
        self.assertGreaterEqual(bar["high"], 2490.0)

    def test_roll_once(self):
        code, out, err, _ = self.out["roll_once"]
        self.assertEqual(code, 1, err)
        self.assertNotIn("rolled", out)
        self.assertEqual(len(self.snap["broker_once"]["records"]), 1)

    def test_rebased_target(self):
        code, out, err, _ = self.out["rebased"]
        self.assertEqual(code, 0, err)
        self.assertIn("ALERT XAUUSD target 2,512.41: the bid reached 2,513.00 in the 7:35 PM PKT 5-minute bar.", out)
        self.assertIn("Your BUY's take profit fires on the bid.", out)

    def test_quote(self):
        code, out, err, _ = self.out["quote"]
        self.assertEqual(code, 0, err)
        q = self.snap["prices"]["quotes"]
        self.assertEqual(q["XAUUSD"]["bid"], 2433.4)
        self.assertEqual(q["XAUUSD"]["source"], "chart")
        self.assertEqual(q["XAUUSD"]["yahoo"], "GC=F")
        self.assertEqual(q["XAUUSD"]["contract"], "Gold Aug 26")
        self.assertIsNotNone(q["XAUUSD"]["age_min"])
        self.assertEqual(q["EURUSD"]["bid"], 1.08499)                       # (m)
        self.assertEqual(self.snap["prices"]["created_by"], "fx-alerts")
        self.assertEqual(self.snap["prices"]["updated_at"], "2026-07-17T04:12:00Z")
        self.assertIn("Quotes at Fri 9:12 AM PKT (12:12 AM New York):", out)
        self.assertIn("  XAUUSD  2,433.40  Yahoo price (gold futures), not your broker's price, 2 min old. "
                      "Contract: Gold Aug 26.", out)                          # (m)
        self.assertIn("Saved to fx/prices.json.", out)

    def test_broker_price(self):
        code, out, err, _ = self.out["broker"]
        self.assertEqual(code, 0, err)
        b = self.snap["prices_broker"]["broker"]["XAUUSD"]
        self.assertEqual(b["k"], 0.998253)
        self.assertEqual(b["bid"], 2429.15)
        self.assertEqual(b["yahoo_bid"], 2433.4)
        self.assertEqual(b["at"], "2026-07-17T04:12:00Z")
        self.assertIn("EURUSD", self.snap["prices_broker"]["quotes"])     # the other key is kept
        self.assertIn("Your last MetaTrader price for XAUUSD: 2,429.15 (your broker's price (you typed it), live).", out)

    def test_clock(self):
        code, out, err, _ = self.out["clock"]
        self.assertEqual(code, 0, err)
        for text in ("PKT", "New York", "London", "Main Refinancing Rate",
                     "Clock: Thu 23 Jul 12:41 PM PKT (3:41 AM New York). FX day Thu 2026-07-23.",
                     "  FX (EURUSD, GBPUSD ... 12 pairs): open. The day ends at 2:00 AM PKT (5:00 PM New York).",
                     "  CME metals (XAUUSD, XAGUSD, COPPER): open. Daily break 2:00 AM to 3:00 AM PKT.",
                     "New York 5:00 PM to 2:00 AM PKT (8:00 AM to 5:00 PM New York, opens in 4 h 19 min)"):
            self.assertIn(text, out)

    def test_clock_weekend(self):
        code, out, err, _ = self.out["weekend"]
        self.assertEqual(code, 0, err)
        self.assertIn("Closed for the weekend.", out)

    def test_speed(self):
        for name, (code, out, err, secs) in self.out.items():
            self.assertLess(secs, 60, name)


class AddAndRefusals(unittest.TestCase):
    """add forms, list, and every refusal and error message (section 7)."""

    def setUp(self):
        self.p = Project()
        self.p.write("plan.json", GOLD_PLAN)
        self.p.portfolio(GOLD_POS)

    def tearDown(self):
        self.p.close()

    def err(self, now, *args):
        code, out, err, _ = self.p.run(now, *args)
        self.assertEqual(code, 2, out + err)
        self.assertTrue(err.startswith("alerts.py: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        return err.strip()

    def test_replace(self):
        self.p.run("2026-07-17 09:12", "add", "--from-plan")
        code, out, err, _ = self.p.run("2026-07-17 09:20", "add", "--from-plan")
        self.assertEqual(code, 0, err)
        self.assertIn("Replaced 4 earlier plan alerts for XAUUSD.", out)
        rules = self.p.rules()
        self.assertEqual(len([r for r in rules if r["from"] == "plan"]), 4)

    def test_no_position_yet(self):
        self.p.portfolio()
        code, out, err, _ = self.p.run("2026-07-17 09:06", "add", "--from-plan")
        self.assertEqual(code, 0, err)
        self.assertIn("Note: no open XAUUSD trade yet. The alerts watch the plan's levels.", out)
        self.assertEqual({r["position_id"] for r in self.p.rules()}, {None})
        # after the fill the plan rules are replaced by the position's
        self.p.portfolio(GOLD_POS)
        code, out, err, _ = self.p.run("2026-07-17 09:12", "add", "--from-plan")
        self.assertIn("Replaced 4 earlier plan alerts for XAUUSD.", out)
        self.assertEqual({r["position_id"] for r in self.p.rules()}, {"XAUUSD-1"})

    def test_user_rule(self):
        code, out, err, _ = self.p.run("2026-07-17 09:12", "add", "USDJPY", "above", "158.5")
        self.assertEqual(code, 0, err)
        r = self.p.rules()[0]
        self.assertEqual((r["kind"], r["from"], r["level"], r["symbol"], r["side"]),
                         ("above", "user", 158.5, "USDJPY", None))
        self.assertIn("Added a1: USDJPY above 158.500.", out)
        code, out, err, _ = self.p.run("2026-07-17 09:13", "add", "usdjpy", "above", "158.5")
        self.assertEqual(code, 0, err)
        self.assertIn("Already there: a1.", out)
        self.assertEqual(len(self.p.rules()), 1)

    def test_user_stop_with_side(self):
        code, out, err, _ = self.p.run("2026-07-17 09:12", "add", "XAUUSD", "stop", "2400", "--side", "buy")
        self.assertEqual(code, 0, err)
        r = self.p.rules()[0]
        self.assertEqual((r["kind"], r["side"], r["position_id"], r["level"]), ("stop", "buy", "XAUUSD-1", 2400.0))

    def test_already_past_note(self):
        code, out, err, _ = self.p.run("2026-07-17 09:12", "add", "XAUUSD", "above", "2400")
        self.assertIn("Note: XAUUSD is already above 2,400.00 (2,433.40, 2 min old). It rings on the next check.", out)

    def test_clock_rules(self):
        for args in (["--session", "london"], ["--rollover"], ["--news", "xauusd"], ["--flat-by", "23:00", "EURUSD"]):
            code, out, err, _ = self.p.run("2026-07-23 12:00", "add", *args)
            self.assertEqual(code, 0, err)
        k = {r["kind"]: r for r in self.p.rules()}
        self.assertEqual(k["session_open"]["minutes_before"], 5)
        self.assertEqual(k["rollover"]["minutes_before"], 30)
        self.assertEqual(k["news"]["minutes_before"], 30)
        self.assertEqual(k["news"]["symbol"], "XAUUSD")
        self.assertEqual(k["flat_by"]["at_utc"], "2026-07-23T18:00:00Z")
        self.assertEqual(k["flat_by"]["minutes_before"], 15)

    def test_side_needed(self):
        e = self.err("2026-07-17 09:12", "add", "XAUUSD", "stop", "2400")
        self.assertEqual(e, "alerts.py: A stop or target alert needs --side buy or --side sell "
                            "(a sell's stop fires on the ask).")
        self.assertIn("needs --side", e)

    def test_refusals(self):
        now = "2026-07-17 09:12"
        say = "alerts.py: Say what to watch, for example: add XAUUSD above 2450."
        self.assertEqual(self.err(now, "add"), say)
        self.assertEqual(self.err(now, "add", "--rollover", "--session", "london"), say)
        self.assertEqual(self.err(now, "add", "XAUUSD", "near", "2400"), say)
        self.assertEqual(self.err(now, "add", "XAUUSD", "above"),
                         "alerts.py: add XAUUSD above needs a price, for example: add XAUUSD above 2450.")
        self.assertEqual(self.err(now, "add", "XAUUSD", "above", "-5"), "alerts.py: The price must be above 0, not -5.")
        self.assertEqual(self.err(now, "add", "XAUSD", "above", "2450"),
                         "alerts.py: XAUSD is not in your instruments. Did you mean XAUUSD?")
        self.assertEqual(self.err(now, "add", "--rollover", "--minutes-before", "2000"),
                         "alerts.py: --minutes-before must be 0 to 1440, not 2000.")
        self.assertEqual(self.err("2026-07-25 12:00", "add", "--flat-by", "23:00"),
                         "alerts.py: It is the weekend. There is no FX day for --flat-by.")
        self.assertEqual(self.err(now, "add", "--flat-by", "25:00"), "alerts.py: --flat-by wants a time like 23:30")
        self.assertEqual(self.err(now, "quote", "XAUUSD", "EURUSD", "--broker-price", "2429.15"),
                         "alerts.py: --broker-price is for one instrument. Quote one symbol.")
        self.assertEqual(self.err(now, "quote", "XAUUSD", "--fast"), "alerts.py: --fast needs the network.")
        self.assertIn("is far from Yahoo's 2433.40 (more than 3%)",
                      self.err(now, "quote", "XAUUSD", "--broker-price", "2000"))
        self.assertTrue(self.err("2026/07/17", "check").startswith('alerts.py: --now wants a time like'))
        os.remove(self.p.path("plan.json"))
        self.assertEqual(self.err(now, "add", "--from-plan"), "alerts.py: no plan at fx/plan.json. Run fx-exit first.")

    def test_quote_needs_symbols(self):
        self.p.portfolio()
        os.remove(self.p.path("plan.json"))
        self.assertEqual(self.err("2026-07-17 09:12", "quote"),
                         "alerts.py: Say which instruments, for example: quote XAUUSD EURUSD.")

    def test_quote_defaults(self):
        self.p.run("2026-07-17 09:12", "add", "USDJPY", "above", "158.5")
        code, out, err, _ = self.p.run("2026-07-17 09:12", "quote")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.p.read("prices.json")["quotes"]), ["USDJPY", "XAUUSD"])

    def test_quote_no_data(self):
        shutil.rmtree(self.p.path("data/yahoo/NG_F"))
        e = self.err("2026-07-17 09:12", "quote", "NATGAS")
        self.assertEqual(e, "alerts.py: No Yahoo price for NATGAS yet (fx/data/yahoo/NG_F/1h.json). "
                            "Run without --offline once to get them.")

    def test_list_off_and_reset(self):
        self.p.run("2026-07-17 09:12", "add", "XAUUSD", "above", "2400")
        self.p.run("2026-07-17 09:12", "add", "XAUUSD", "above", "2410")
        code, out, err, _ = self.p.run("2026-07-17 09:13", "list", "--off", "a1")
        self.assertEqual(code, 0, err)
        self.assertIn("a1   off  XAUUSD above 2,400.00  not rung yet", out)
        code, out, err, _ = self.p.run("2026-07-17 09:20", "check")
        self.assertEqual(code, 0, err)
        self.assertIn("ALERT XAUUSD above 2,410.00", out)
        self.assertNotIn("ALERT XAUUSD above 2,400.00", out)
        rang = self.p.read("state/alerts.json")["rang"]
        self.assertNotIn("a1", rang)
        self.assertIn("a2", rang)
        code, out, err, _ = self.p.run("2026-07-17 09:21", "list")
        self.assertTrue(out.splitlines()[0].startswith("a2   on   XAUUSD above 2,410.00  rang Fri "), out)
        self.p.run("2026-07-17 09:22", "list", "--reset", "a2")
        self.assertNotIn("a2", self.p.read("state/alerts.json")["rang"])
        self.assertEqual(self.err("2026-07-17 09:22", "list", "--on", "a9"),
                         "alerts.py: There is no rule a9. Rules: a1 to a2.")

    def test_bad_rule(self):
        self.p.write("alerts.json", {"rules": [{"id": "a9", "active": True, "kind": "price",
                                                "symbol": "XAUUSD", "level": 2400}]})
        e = self.err("2026-07-17 09:12", "check")
        self.assertTrue(e.startswith('alerts.py: fx/alerts.json: rule "a9": unknown kind "price"'), e)

    def test_bad_rules_other(self):
        cases = [
            ([{"id": "a1", "kind": "stop", "symbol": "XAUUSD", "level": 2400}], 'rule "a1": a stop rule needs "side"'),
            ([{"id": "a1", "kind": "exit_by", "symbol": "XAUUSD"}], 'rule "a1": a time rule needs "at_utc"'),
            ([{"id": "a1", "kind": "rollover"}, {"id": "a1", "kind": "rollover"}], 'rule "a1": the id is used twice'),
        ]
        for rules, text in cases:
            self.p.write("alerts.json", {"rules": rules})
            self.assertIn(text, self.err("2026-07-17 09:12", "check"))

    def test_no_rules(self):
        code, out, err, _ = self.p.run("2026-07-17 09:12", "check")
        self.assertEqual(code, 1, err)
        self.assertIn("No alert rules yet. Add one: python3 fx-alerts/alerts.py add --from-plan", out)

    def test_broken_state(self):
        self.p.run("2026-07-17 09:12", "add", "--from-plan")
        os.makedirs(self.p.path("state"), exist_ok=True)
        with open(self.p.path("state/alerts.json"), "w") as fh:
            fh.write("{not json")
        code, out, err, _ = self.p.run("2026-07-17 09:20", "check")
        self.assertEqual(code, 1, err)
        self.assertIn("fx/state/alerts.json was damaged. Started a new one.", out)
        names = os.listdir(self.p.path("state"))
        self.assertIn("alerts.broken-20260717T042000Z.json", names)
        self.assertEqual(self.p.read("state/alerts.json")["checked_at"], "2026-07-17T04:20:00Z")

    def test_broken_portfolio(self):
        self.p.run("2026-07-17 09:12", "add", "--from-plan")
        self.p.write("portfolio.json", {"positions": [GOLD_POS, GOLD_POS]})
        self.assertEqual(self.err("2026-07-17 09:20", "check"),
                         "alerts.py: fx/portfolio.json: two positions have the id XAUUSD-1. "
                         "Fix it by hand or restore a backup.")

    def test_hand_written_rule_gets_added_at(self):
        shutil.copy(os.path.join(ROOT, "fx-alerts", "alerts.example.json"), self.p.path("alerts.json"))
        code, out, err, _ = self.p.run("2026-07-23 12:56", "check")
        self.assertEqual(code, 1, err)
        for r in self.p.rules():
            self.assertEqual(r["added_at"], "2026-07-23T06:56:00Z")

    def test_clock_rules_ring(self):
        shutil.copy(os.path.join(ROOT, "fx-alerts", "alerts.example.json"), self.p.path("alerts.json"))
        code, out, err, _ = self.p.run("2026-07-23 11:56", "check")
        self.assertEqual(code, 0, err)
        self.assertIn("ALERT London opens at 12:00 PM PKT (8:00 AM London) in 4 min.", out)
        self.assertIn("An alert is information, not an order.", out)
        self.assertNotIn("Nothing was closed", out)
        code, out, err, _ = self.p.run("2026-07-23 12:56", "check")
        self.assertNotIn("London opens", out)          # rung once a day, and 30 min late is quiet
        code, out, err, _ = self.p.run("2026-07-24 01:35", "check")
        self.assertIn("ALERT Rollover at 2:00 AM PKT (5:00 PM New York) in 25 min. Swap is booked then.", out)
        self.assertIn("a1@2026-07-23", self.p.read("state/alerts.json")["rang"])

    def test_news_rule_rings(self):
        self.p.run("2026-07-14 16:00", "add", "--news", "XAUUSD")
        code, out, err, _ = self.p.run("2026-07-14 17:05", "check")
        self.assertEqual(code, 0, err)
        self.assertIn("ALERT news for XAUUSD in 25 min: CPI m/m (USD) at 5:30 PM PKT (8:30 AM New York), High impact.", out)
        code, out, err, _ = self.p.run("2026-07-14 17:10", "check")
        self.assertEqual(code, 1, err)

    def test_flat_by_rings(self):
        self.p.write("plan.json", EUR_PLAN)
        self.p.portfolio(GOLD_POS, EUR_POS)
        self.p.run("2026-07-23 12:45", "add", "--from-plan")
        code, out, err, _ = self.p.run("2026-07-23 23:16", "check", "--symbols", "EURUSD")
        self.assertEqual(code, 0, err)
        self.assertIn("ALERT EURUSD flat by Thu 11:30 PM PKT (2:30 PM New York): 14 min left. "
                      "Your day trade EURUSD-1 is still open.", out)
        self.assertIn("Next: python3 fx-broker/ticket.py flatten", out)
        self.assertNotIn("XAUUSD", out.split("\n")[0])

    def test_json(self):
        self.p.run("2026-07-17 09:12", "add", "--from-plan")
        p = subprocess.run([sys.executable, SCRIPT, "--json", "--offline", "--now", "2026-07-21 15:00"],
                           cwd=self.p.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        doc = json.loads(p.stdout)
        self.assertEqual((doc["tool"], doc["cmd"], doc["v"], doc["doc"]), ("fx-alerts", "check", 1, None))
        self.assertEqual(doc["rang"], [])
        self.assertEqual(doc["failed"], [])
        self.assertTrue(any("Missed" in n for n in doc["notes"]))
        self.assertEqual(p.returncode, 1)

    def test_example_file(self):
        with open(os.path.join(ROOT, "fx-alerts", "alerts.example.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        cfg = A.load_config(self.p.path("config.json"))
        A.check_rules(doc, "alerts.example.json", cfg)
        for r in doc["rules"]:
            self.assertEqual(set(A.RULE_KEYS) - {"added_at"}, set(r))


class Units(unittest.TestCase):
    """The own helpers on made-up bars (no files unless said)."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = A.load_config(os.path.join(MARKET, "config.json"))
        cls.eur = A.instrument(cls.cfg, "EURUSD")
        cls.gold = A.instrument(cls.cfg, "XAUUSD")

    def bar(self, high, low, start=D(2026, 7, 23, 8, 0), interval="5m"):
        return {"start": start, "open": low, "high": high, "low": low, "close": low, "interval": interval}

    def test_sell_stop_on_the_ask(self):
        rule = {"kind": "stop", "level": 1.08561, "side": "sell", "symbol": "EURUSD"}
        hit = A.price_hit(rule, [self.bar(1.08560, 1.08500)], None, self.eur)
        self.assertEqual(hit["price"], 1.08561)
        self.assertIsNone(A.price_hit(rule, [self.bar(1.08559, 1.08500)], None, self.eur))
        line = A.alert_line(rule, hit, self.eur, D(2026, 7, 23, 8, 10))
        self.assertEqual(line, "ALERT EURUSD stop 1.08561: the ask rose to 1.08561 in the 1:00 PM PKT 5-minute bar.")

    def test_buy_rules_on_the_bid(self):
        stop = {"kind": "stop", "level": 2421.59, "side": "buy", "symbol": "XAUUSD"}
        target = {"kind": "target", "level": 2512.41, "side": "buy", "symbol": "XAUUSD"}
        self.assertIsNone(A.price_hit(stop, [self.bar(2450.0, 2421.6)], None, self.gold))
        self.assertEqual(A.price_hit(stop, [self.bar(2450.0, 2421.59)], None, self.gold)["price"], 2421.59)
        self.assertIsNone(A.price_hit(target, [self.bar(2512.4, 2500.0)], None, self.gold))
        live = {"start": D(2026, 7, 30, 14, 41), "open": 2513.0, "high": 2513.0, "low": 2513.0,
                "close": 2513.0, "interval": "live"}
        hit = A.price_hit(target, [], live, self.gold)
        self.assertEqual(A.alert_line(target, hit, self.gold, D(2026, 7, 30, 14, 42)),
                         "ALERT XAUUSD target 2,512.41: the bid reached 2,513.00 at 7:41 PM PKT (live price).")

    def test_sell_target_needs_the_ask(self):
        rule = {"kind": "target", "level": 1.08234, "side": "sell", "symbol": "EURUSD"}
        self.assertIsNone(A.price_hit(rule, [self.bar(1.08300, 1.08234)], None, self.eur))
        self.assertEqual(A.price_hit(rule, [self.bar(1.08300, 1.08233)], None, self.eur)["price"], 1.08234)

    def test_past(self):
        self.assertTrue(A.past({"kind": "stop", "level": 1.08561, "side": "sell"}, 1.08560, self.eur))
        self.assertFalse(A.past({"kind": "stop", "level": 1.08561, "side": "sell"}, 1.08559, self.eur))
        self.assertTrue(A.past({"kind": "target", "level": 2512.41, "side": "buy"}, 2512.41, self.gold))
        self.assertTrue(A.past({"kind": "below", "level": 157.0, "side": None}, 156.98, self.eur))
        self.assertFalse(A.past({"kind": "above", "level": 158.5, "side": None}, 158.49, self.eur))

    def test_next_rule_id(self):
        self.assertEqual(A.next_rule_id([]), "a1")
        self.assertEqual(A.next_rule_id([{"id": "a3"}, {"id": "x9"}, {"id": "a12"}]), "a13")

    def test_friday_late_and_exit_by(self):
        rule = {"id": "a4", "kind": "friday_close", "at_utc": "2026-07-17T16:00:00Z", "active": True}
        rang = {}
        lines = A.move_friday(rule, D(2026, 7, 29, 8, 15), self.cfg, {"exit_by": "2026-08-13T21:00:00Z"}, rang)
        self.assertEqual(lines, ["Missed: the Friday 12:00 PM New York check (Fri 07-17).",
                                 "Missed: the Friday 12:00 PM New York check (Fri 07-24)."])
        self.assertEqual(rule["at_utc"], "2026-07-31T16:00:00Z")
        self.assertTrue(rule["active"])
        lines = A.move_friday(rule, D(2026, 8, 10, 8, 0), self.cfg, {"exit_by": "2026-08-13T21:00:00Z"}, rang)
        self.assertEqual(len(lines), 2)
        self.assertFalse(rule["active"])     # the next Friday 08-14 cutoff is after the exit-by
        self.assertEqual(sorted(rang), ["a4@2026-07-17", "a4@2026-07-24", "a4@2026-07-31", "a4@2026-08-07"])

    def test_friday_rings_in_window(self):
        rule = {"id": "a4", "kind": "friday_close", "at_utc": "2026-07-24T16:00:00Z", "minutes_before": None,
                "symbol": "XAUUSD", "active": True}
        now = D(2026, 7, 24, 16, 5)
        self.assertEqual(A.time_hit(rule, now, "2026-07-24", self.cfg, {}, []), "a4@2026-07-24")
        line = A.time_alert_line(rule, now, self.cfg, {"id": "XAUUSD-1"})
        self.assertEqual(line, "ALERT XAUUSD Friday check: it is Fri 9:05 PM PKT (12:05 PM New York). The market "
                               "shuts for the weekend at Sat 2:00 AM PKT (5:00 PM New York). XAUUSD-1 is still open. "
                               "On Monday the price can open past your stop.")
        self.assertIsNone(A.time_hit(rule, D(2026, 7, 24, 15, 59), "2026-07-24", self.cfg, {}, []))
        self.assertIsNone(A.time_hit(rule, D(2026, 7, 24, 21, 0), None, self.cfg, {}, []))

    def test_exit_by_line(self):
        rule = {"id": "a3", "kind": "exit_by", "at_utc": "2026-08-13T21:00:00Z", "minutes_before": 60,
                "symbol": "XAUUSD"}
        self.assertIsNone(A.time_hit(rule, D(2026, 8, 13, 19, 59), "2026-08-13", self.cfg, {}, []))
        self.assertEqual(A.time_hit(rule, D(2026, 8, 13, 20, 0), "2026-08-13", self.cfg, {}, []), "a3")
        self.assertEqual(A.time_alert_line(rule, D(2026, 8, 13, 20, 0), self.cfg, {"id": "XAUUSD-1"}),
                         "ALERT XAUUSD exit by Fri 14 Aug 2:00 AM PKT (5:00 PM New York): the last day of "
                         "your 20-day hold. XAUUSD-1 is still open.")

    def test_session_not_on_a_holiday(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["holidays"] = {"2026-07-23": "made-up broker holiday"}
        rule = {"id": "a1", "kind": "session_open", "session": "london", "minutes_before": 5}
        self.assertIsNone(A.time_hit(rule, D(2026, 7, 23, 6, 57), "2026-07-23", cfg, {}, []))
        self.assertEqual(rule["_holiday"], "Thu 23 Jul: your broker is shut (made-up broker holiday).")
        rule = {"id": "a1", "kind": "session_open", "session": "london", "minutes_before": 5}
        self.assertEqual(A.time_hit(rule, D(2026, 7, 23, 6, 57), "2026-07-23", self.cfg, {}, []), "a1@2026-07-23")
        self.assertIsNone(A.time_hit(rule, D(2026, 7, 23, 7, 31), "2026-07-23", self.cfg, {}, []))


class Patched(unittest.TestCase):
    """Paths through check that need a fake bar feed or a failing Yahoo."""

    def setUp(self):
        self.saved = {n: getattr(A, n) for n in ("load_bars", "latest_price", "refresh", "check_roll_forward")}
        self.cfg = A.load_config(os.path.join(MARKET, "config.json"))
        self.gold = A.instrument(self.cfg, "XAUUSD")

    def tearDown(self):
        for n, f in self.saved.items():
            setattr(A, n, f)

    def gold_window_now(self):
        w = [w for w in A.expected_rolls("GC", self.gold, D(2026, 7, 29), D(2026, 7, 29))
             if w[2] <= D(2026, 7, 29) <= w[3]][0]
        return w[2] + datetime.timedelta(hours=48)

    def test_roll_guard_holds_then_rings(self):
        now = self.gold_window_now()
        start = now - datetime.timedelta(hours=40)
        bars = []
        for i in range(39):
            t = start + datetime.timedelta(hours=i)
            bars.append({"start": t, "open": 2500.0, "high": 2501.0, "low": 2499.0, "close": 2500.0, "volume": 1})
        t = start + datetime.timedelta(hours=39)
        bars.append({"start": t, "open": 2500.0, "high": 2521.0, "low": 2500.0, "close": 2520.0, "volume": 1})
        A.load_bars = lambda data, sym, interval, now, rolls=None, include_forming=False, inst=None, cfg=None: (
            [dict(b) for b in bars] if interval == "1h" else [])
        lp = {"bid": 2520.0, "yahoo": 2520.0, "at": t + datetime.timedelta(hours=1), "age_min": 0.0,
              "source": "yahoo 1h", "contract": None}
        A.latest_price = lambda data, inst, now, cfg: dict(lp)
        rule = {"id": "a2", "active": True, "kind": "target", "symbol": "XAUUSD", "level": 2510.0,
                "side": "buy", "added_at": A.utc_iso(start - datetime.timedelta(hours=1))}
        c = {"cfg": self.cfg, "now": now, "paths": {"data": "/no/such/folder"}}
        state = {"rang": {}, "seen": {}, "held": {}}
        rings, notes, prices, no_data = A.check_prices([rule], c, state, [], [])
        self.assertEqual(rings, [])
        self.assertIn("a2", state["held"])
        self.assertIn("Note: Yahoo gold may have rolled. Check your MetaTrader price.", notes)
        # next run, no roll yet: still held, no ring
        rings, notes, _, _ = A.check_prices([rule], c, state, [], [])
        self.assertEqual(rings, [])
        self.assertIn("a2", state["held"])
        # a roll is found and the price is still past the level: it rings, marked held
        roll = {"id": "GC-made-up", "root": "GC", "ratio": 1.0, "first_bar_new": A.utc_iso(t),
                "found_at": A.utc_iso(now)}
        rings, notes, _, _ = A.check_prices([rule], c, state, [roll], [])
        self.assertEqual([r["id"] for r, hit, inst in rings], ["a2"])
        self.assertEqual(state["held"], {})
        line = A.price_alert_lines(rule, rings[0][1], self.gold, now, None)[0]
        self.assertIn("(held at", line)
        self.assertIn("for a possible roll)", line)
        # the same, but the price came back under the level: the hold is dropped, no ring
        state = {"rang": {}, "seen": {}, "held": {"a2": {"at": A.utc_iso(t), "line": "x"}}}
        lp["bid"] = 2505.0
        rings, notes, _, _ = A.check_prices([rule], c, state, [roll], [])
        self.assertEqual(rings, [])
        self.assertEqual(state["held"], {})

    def run_main(self, project, argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        here = os.getcwd()
        os.chdir(project.dir)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = A.main(argv)
        finally:
            os.chdir(here)
        return code, out.getvalue(), err.getvalue()

    def test_yahoo_failed_everywhere(self):
        p = Project()
        try:
            p.portfolio()
            p.write("alerts.json", {"rules": [dict(A.new_rule("above", symbol="XAUUSD", level=2500.0,
                                                              now=D(2026, 11, 11, 8, 0)), id="a1")]})
            shutil.rmtree(p.path("data/yahoo/GC_F"))

            def boom(*args, **kwargs):
                raise ValueError("Yahoo is busy (HTTP 429). Try again in a minute.")
            A.refresh = boom
            A.check_roll_forward = lambda *a, **k: None
            code, out, err = self.run_main(p, ["check"])
            self.assertEqual(code, 2, out)
            self.assertEqual(err.strip(), "alerts.py: Yahoo did not answer, and there are no saved prices "
                                          "for XAUUSD. Try again later.")
        finally:
            p.close()

    def test_one_symbol_failed(self):
        p = Project()
        try:
            p.portfolio()
            rules = [dict(A.new_rule("above", symbol="XAUUSD", level=9000.0, now=D(2026, 7, 23, 7, 0)), id="a1"),
                     dict(A.new_rule("above", symbol="EURUSD", level=1.5, now=D(2026, 7, 23, 7, 0)), id="a2")]
            p.write("alerts.json", {"rules": rules})

            def gold_fails(data, symbol, interval, now, cfg, offline):
                if symbol == "GC=F":
                    raise ValueError("Yahoo is busy (HTTP 429). Try again in a minute.")
                return {}
            A.refresh = gold_fails
            A.check_roll_forward = lambda *a, **k: None
            code, out, err = self.run_main(p, ["check"])
            self.assertEqual(code, 1, err)
            self.assertIn("Note: Yahoo failed for XAUUSD (Yahoo is busy (HTTP 429). Try again in a minute.). "
                          "The saved prices were used.", out)
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
