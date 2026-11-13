"""Tests for fx-risk/check.py on the made-up sample market. Offline only.

Run from the pack root: python3 -m unittest discover -s tests -p "test_risk.py"
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import json
import shutil
import subprocess
import tempfile
import unittest

import _load
import fx_core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET = os.path.join(ROOT, "tests", "fixtures", "fx-market")
SCRIPT = os.path.join(ROOT, "fx-risk", "check.py")

# The two sample orders, as fx-budget writes them (fx-budget spec section 5). They are
# written here by hand so this file does not need the other tools (check).
GOLD = {"created_by": "fx-budget", "created_at": "2026-07-17T04:07:00Z", "orders": [{
    "symbol": "XAUUSD", "side": "buy", "intent": "open", "lots": 0.02, "type": "market",
    "entry": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0, "expiry": "gtc", "style": "swing",
    "broker_symbol": "XAUUSD", "position_id": None, "rolls_applied": {},
    "plan": {"setup": "breakout_20d", "fx_day": "2026-07-17", "chart_entry": 2433.4, "chart_stop": 2400.0,
             "risk_per_unit": 33.49, "target_r": 1.69, "flat_by": None, "flat_by_utc": None,
             "exit_by": "2026-08-13T21:00:00Z", "nights_est": 28, "max_loss_usd": 97.81,
             "costs_usd": 30.83, "margin_usd": 48.67, "usd_per_point_per_lot": 1.0},
    "decided_by": "risk_pct",
    "why": "1.0% of $10,000.00 is $100.00. 0.02 lots lose $97.81 at the stop, costs in."}]}
EUR = {"created_by": "fx-budget", "created_at": "2026-07-23T07:43:00Z", "orders": [{
    "symbol": "EURUSD", "side": "sell", "intent": "open", "lots": 0.86, "type": "market",
    "entry": 1.08452, "stop_loss": 1.08561, "take_profit": 1.08234, "expiry": "2026-07-23T18:30:00Z",
    "style": "day", "broker_symbol": "EURUSD", "position_id": None, "rolls_applied": {},
    "plan": {"setup": "or_breakdown", "fx_day": "2026-07-23", "chart_entry": 1.08452, "chart_stop": 1.08561,
             "risk_per_unit": 0.00109, "target_r": 2.0, "flat_by": "23:30",
             "flat_by_utc": "2026-07-23T18:30:00Z", "exit_by": None, "nights_est": 0,
             "max_loss_usd": 98.9, "costs_usd": 5.16, "margin_usd": 932.69, "usd_per_point_per_lot": 1.0},
    "decided_by": "risk_pct", "why": "1.0% of $10,000.00 is $100.00. 0.86 lots lose $98.90 at the stop, costs in."}]}
CLOSE = {"created_by": "fx-broker", "created_at": "2026-07-23T10:21:00Z", "orders": [{
    "symbol": "EURUSD", "side": "sell", "intent": "close", "lots": 0.86, "type": "market",
    "entry": None, "stop_loss": None, "take_profit": None, "expiry": None, "style": "day",
    "broker_symbol": "EURUSD", "position_id": "EURUSD-1", "rolls_applied": {},
    "plan": {}, "decided_by": "close", "why": "Close EURUSD-1."}]}


def position(pid, symbol, side, lots, entry, stop, max_loss, style="swing", fp="0000000000000000"):
    """One portfolio.json position (skeleton 4.7)."""
    return {"id": pid, "symbol": symbol, "side": side, "style": style, "lots": lots, "fingerprint": fp,
            "paper": True, "opened_at": "2026-07-17T04:11:00Z", "fx_day_opened": "2026-07-17",
            "entry": entry, "fill_price": entry, "broker_fill_price": None, "stop_loss": stop,
            "take_profit": None, "stop_at_open": stop, "broker_stop_loss": None,
            "broker_take_profit": None, "max_loss_usd": max_loss, "flat_by_utc": None,
            "exit_by": None, "rolls_applied": {}, "swap_nights": 0, "swap_usd": 0.0}


def portfolio(*positions):
    return {"updated_at": "2026-07-17T04:11:00Z",
            "account": {"currency": "USD", "balance": 10000.0, "start_balance": 10000.0,
                        "realized_usd": 0.0, "paper": True},
            "positions": list(positions), "closed": []}


def record(status, fp, fx_day, max_loss, symbol="XAUUSD", side="buy"):
    """One state/broker.json record (skeleton 4.10)."""
    return {"at": "2026-07-17T04:10:00Z", "fx_day": fx_day, "status": status, "index": 1,
            "fingerprint": fp, "symbol": symbol, "side": side, "intent": "open", "lots": 0.02,
            "style": "swing", "max_loss_usd": max_loss, "reply": "yes" if status == "yes" else None,
            "price": None, "broker_price": None, "result_usd": None, "position_id": None,
            "roll_id": None, "plain": "", "paper": True}


class RiskBase(unittest.TestCase):
    """A temp folder with fx/data = a copy of the sample market's data (shared by the
    tests: fx-risk only reads it offline) and a fresh fx/ for each run."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fx-risk-test-")
        cls.data = os.path.join(cls.tmp, "data")
        shutil.copytree(os.path.join(MARKET, "data"), cls.data)
        with open(os.path.join(MARKET, "config.json"), encoding="utf-8") as fh:
            cls.config = json.load(fh)
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_check(self, now, orders=None, port=None, ledger=None, cfg=None, words=False, extra=()):
        """Write a fresh fx/ folder and run check.py --offline --now T. Returns
        (code, stdout, stderr, json doc or None, the work folder)."""
        type(self).runs += 1
        work = os.path.join(self.tmp, "run%d" % self.runs)
        fx = os.path.join(work, "fx")
        os.makedirs(os.path.join(fx, "state"))
        os.symlink(self.data, os.path.join(fx, "data"))
        files = {"config.json": cfg or self.config, "orders.json": orders, "portfolio.json": port}
        for name, doc in files.items():
            if doc is not None:
                with open(os.path.join(fx, name), "w", encoding="utf-8") as fh:
                    json.dump(doc, fh)
        if ledger is not None:
            with open(os.path.join(fx, "state", "broker.json"), "w", encoding="utf-8") as fh:
                json.dump(ledger, fh)
        cmd = [sys.executable, SCRIPT, "--offline", "--now", now] + ([] if words else ["--json"]) + list(extra)
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=120)
        doc = None
        if not words and p.returncode in (0, 1):
            doc = json.loads(p.stdout)
        return p.returncode, p.stdout, p.stderr, doc, work

    def cfg_with(self, **parts):
        """A copy of the sample config with some keys changed: risk={...}, account={...}, top=value."""
        cfg = copy.deepcopy(self.config)
        for key, value in parts.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
        return cfg

    def reasons(self, doc, i=0):
        return doc["risk"]["verdicts"][i]["reasons"]

    def warnings(self, doc, i=0):
        return doc["risk"]["verdicts"][i]["warnings"]

    def has_reason(self, doc, rid, text=None, i=0):
        hits = [r for r in self.reasons(doc, i) if r.startswith(rid + " ")]
        self.assertTrue(hits, "no %s in %s" % (rid, self.reasons(doc, i)))
        if text is not None:
            self.assertTrue(any(text in r for r in hits), "%r not in %s" % (text, hits))

    def no_reason(self, doc, rid, i=0):
        self.assertFalse([r for r in self.reasons(doc, i) if r.startswith(rid + " ")], self.reasons(doc, i))


def gold(**changes):
    doc = copy.deepcopy(GOLD)
    for k, v in changes.items():
        if k == "created_at":
            doc["created_at"] = v
        else:
            doc["orders"][0][k] = v
    return doc


def eur(**changes):
    doc = copy.deepcopy(EUR)
    for k, v in changes.items():
        if k == "created_at":
            doc["created_at"] = v
        else:
            doc["orders"][0][k] = v
    return doc


def close_order(**changes):
    doc = copy.deepcopy(CLOSE)
    doc["orders"][0].update(changes)
    return doc


class TestOpens(RiskBase):

    def test_gold_pass(self):
        code, out, err, doc, work = self.run_check("2026-07-17 09:08", gold())
        self.assertEqual(code, 0, out + err)
        v = doc["risk"]["verdicts"][0]
        self.assertEqual(v["fingerprint"], fx_core.fingerprint(GOLD["orders"][0]))
        self.assertEqual(v["fingerprint"], "c63ced9cb166dab1")
        self.assertEqual(doc["risk"]["checked_at"], "2026-07-17T04:08:00Z")
        self.assertEqual(v["max_loss_usd"], 97.81)
        self.assertEqual(v["margin_usd"], 48.67)
        w = self.warnings(doc)
        self.assertTrue(any(x.startswith("W-net ") and "0.84" in x for x in w), w)
        self.assertTrue(any(x.startswith("W-weekend ") for x in w))
        self.assertTrue(any(x.startswith("W-scale ") for x in w))
        # (m) the expected roll date: the Y date of the GC window (Aug to Dec)
        self.assertIn("W-roll Gold rolls on Yahoo about 2026-07-31. Plan levels move then; your "
                      "broker's price does not.", w)
        s = doc["risk"]["summary"]
        self.assertEqual(s["daily_loss_used_usd"], 97.81)
        self.assertEqual(s["daily_loss_limit_usd"], 300.0)
        self.assertEqual(s["open_trades"], 0)
        self.assertEqual(s["equity_usd"], 10000.0)
        self.assertEqual(s["margin_used_usd"], 48.67)
        self.assertTrue(s["paper"])
        code, out, err, _, _ = self.run_check("2026-07-17 09:08", gold(), words=True)
        self.assertEqual(code, 0)
        self.assertIn("PASS #1 buy XAUUSD 0.02 lots: max loss $97.81 (limit $100.00), costs included.", out)
        # (m) the price line: the sample's 5m close and its age
        self.assertIn("gold 2,433.40 (Yahoo price (gold futures), not your broker's price, 3 min old)", out)
        self.assertIn("Market order. Measured from 2,433.49 (the ask now; the plan said 2,433.49).", out)
        self.assertIn("Stop 2,400.00, 33.49 (3,349 points) away. Target 2,490.00. Reward:risk 1.69 (min 1.50).", out)
        self.assertIn("Today before this: 0 of 3 trades, $0.00 of $300.00 daily loss used.", out)
        self.assertIn("Margin after: $48.67 (0.5% of your balance, limit 30%). Free margin $10,000.00.", out)
        self.assertIn("1 passed, 0 blocked. Checked at Fri 9:08 AM PKT. Paper account.", out)
        self.assertIn("Balance $10,000.00. Daily loss used: $97.81 of $300.00, with the passed orders.", out)
        self.assertIn("A PASS is a rules check, not a yes. fx-broker still asks you.", out)
        self.assertIn("a PASS counts for 5 minutes.", out)
        self.assertIn("Next: python3 fx-broker/ticket.py show", out)

    def test_day_sample_with_gold_open(self):
        port = portfolio(position("XAUUSD-1", "XAUUSD", "buy", 0.02, 2433.49, 2400.0, 97.81,
                                  fp="c63ced9cb166dab1"))
        code, out, err, doc, _ = self.run_check("2026-07-23 12:43", eur(), port=port)
        self.assertEqual(code, 0, out + err)
        v = doc["risk"]["verdicts"][0]
        self.assertEqual(v["fingerprint"], "2e2a875b3e30fdc4")
        self.assertEqual(v["max_loss_usd"], 98.9)
        self.assertEqual(v["margin_usd"], 932.69)
        s = doc["risk"]["summary"]
        self.assertEqual(s["daily_loss_used_usd"], 98.9)
        self.assertEqual(s["open_trades"], 1)
        self.assertTrue(any(x.startswith("W-margin ") for x in v["warnings"]))
        self.assertTrue(any(x.startswith("W-news Main Refinancing Rate (EUR)") for x in v["warnings"]))
        code, out, _, _, _ = self.run_check("2026-07-23 12:43", eur(), port=port, words=True)
        self.assertIn("PASS #1 sell EURUSD 0.86 lots", out)
        self.assertIn("EURUSD 1.08452 (Yahoo price, close to your broker's price, 3 min old)", out)
        self.assertIn("Flat by Thu 11:30 PM PKT (2:30 PM New York). No new day trades after Thu 9:00 PM PKT.", out)

    def test_one_step_too_big(self):
        code, out, err, doc, _ = self.run_check("2026-07-23 12:43", eur(lots=0.87))
        self.assertEqual(code, 1, out + err)
        self.has_reason(doc, "R10", "Max loss $100.05 is over your limit of $100.00 a trade "
                                    "(1.0% of $10,000.00), costs included.")
        code, out, _, _, _ = self.run_check("2026-07-23 12:43", eur(lots=0.87), words=True)
        self.assertIn("BLOCK #1 sell EURUSD 0.87 lots:", out)
        self.assertIn("A BLOCK is final for that order.", out)
        self.assertNotIn("Next: python3 fx-broker/ticket.py show", out)

    def test_ecb_quiet_window(self):
        code, out, err, doc, _ = self.run_check("2026-07-23 17:00", eur(created_at="2026-07-23T11:50:00Z"))
        self.assertEqual(code, 1, out + err)
        self.has_reason(doc, "R24", "Main Refinancing Rate (EUR) at 5:15 PM PKT. High impact. No new "
                                    "trades from 30 min before to 30 min after it.")

    def test_sells_off(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 12:43", eur(), cfg=self.cfg_with(allow_short=False))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R4", "Sells are off (allow_short is false).")

    def test_blocked(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(), cfg=self.cfg_with(risk={"blocked": ["XAUUSD"]}))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R3", "XAUUSD is on your blocked list.")

    def test_duplicate_today(self):
        ledger = {"records": [record("filled", "c63ced9cb166dab1", "2026-07-17", 97.81)]}
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(), ledger=ledger)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R16", "This exact order was already taken today.")

    def test_daily_loss(self):
        ledger = {"records": [record("yes", "aaaaaaaaaaaaaaaa", "2026-07-17", 60.0)]}
        cfg = self.cfg_with(risk={"max_daily_loss_usd": 150})
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(), ledger=ledger, cfg=cfg)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R12", "Today's loss room: $60.00 used of $150.00. This trade can lose $97.81, "
                                    "so it does not fit.")
        self.assertEqual(doc["risk"]["summary"]["daily_loss_limit_usd"], 150.0)

    def test_group_and_currency(self):
        port = portfolio(position("XAGUSD-1", "XAGUSD", "buy", 0.1, 29.0, 28.0, 150.0))
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(), port=port)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R18", "Metals risk would be $247.81 (2.48% of your balance). Your limit is 2.00%. "
                                    "Gold, silver, platinum and palladium move together.")
        self.has_reason(doc, "R19", "Short USD risk would be $247.81")
        self.has_reason(doc, "R19", "These trades all bet against the US dollar: buy XAGUSD, buy XAUUSD.")

    def test_currency_only(self):
        port = portfolio(position("EURUSD-1", "EURUSD", "buy", 0.5, 1.08, 1.078, 100.0),
                         position("GBPUSD-1", "GBPUSD", "buy", 0.3, 1.29, 1.288, 50.0))
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(), port=port)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R19", "buy EURUSD, buy GBPUSD, buy XAUUSD")
        self.no_reason(doc, "R18")

    def test_cme_break(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 02:30", gold(created_at="2026-07-16T21:20:00Z"))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R22", "Gold is closed now (CME daily break). It opens at Fri 3:00 AM PKT.")
        self.no_reason(doc, "R28")

    def test_friday_cutoff(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 21:30", gold(created_at="2026-07-17T16:00:00Z"))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R25", "It is past 12:00 PM New York on Friday. No new swing trades before the "
                                    "weekend: a gap can jump past the stop.")

    def test_day_window(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 21:05", eur())
        self.assertEqual(code, 1)
        self.has_reason(doc, "R26", "No new day trades after 9:00 PM PKT.")

    def test_flat_by_close(self):
        order = eur()
        order["orders"][0]["plan"]["flat_by_utc"] = "2026-07-23T15:30:00Z"
        code, _, _, doc, _ = self.run_check("2026-07-23 20:20", order)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R26", "Less than 15 minutes to your flat-by time (8:30 PM PKT).")
        self.assertEqual(len([r for r in self.reasons(doc) if r.startswith("R26 ")]), 1)

    def test_stale_price_outage(self):
        code, _, _, doc, _ = self.run_check("2026-07-21 15:00", gold(created_at="2026-07-21T09:00:00Z"))
        self.assertEqual(code, 1)
        # (m) the newest gold bar ends at 02:00 UTC: 8 hours before 15:00 PKT
        self.has_reason(doc, "R23", "The newest gold price is 8 h old. A new trade needs one under 30 min old. "
                                    "Run /fx-alerts quote XAUUSD.")

    def test_roll_not_applied(self):
        code, _, _, doc, _ = self.run_check("2026-07-29 13:15", gold(created_at="2026-07-29T08:13:00Z",
                                                                     rolls_applied={}))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R29", "Gold rolled on Yahoo after this order was made (+0.90%). Its prices are "
                                    "on the old scale. Make it again: /fx-exit, then /fx-budget.")
        ok = gold(created_at="2026-07-29T08:13:00Z", rolls_applied={"GC-2026-07-29": 1.008999192897498})
        code, _, _, doc, _ = self.run_check("2026-07-29 13:15", ok)
        self.no_reason(doc, "R29")
        old = gold(created_at="2026-07-29T08:13:00Z", rolls_applied={"GC-2026-07-29": 1.005})
        code, _, _, doc, _ = self.run_check("2026-07-29 13:15", old)
        self.has_reason(doc, "R29", "(+0.40%)")

    def test_old_orders(self):
        code, _, _, doc, _ = self.run_check("2026-07-20 10:00", gold())
        self.assertEqual(code, 1)
        self.has_reason(doc, "R28", "These orders were made on Fri 2026-07-17. Make a new plan today.")

    def test_batch_counts_passed(self):
        two = gold()
        two["orders"].append(copy.deepcopy(two["orders"][0]))
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", two)
        self.assertEqual(code, 1)
        vs = doc["risk"]["verdicts"]
        self.assertEqual(vs[0]["verdict"], "PASS")
        self.assertEqual(vs[1]["verdict"], "BLOCK")
        self.has_reason(doc, "R16", "Two buys of XAUUSD in one batch.", i=1)
        self.assertEqual(doc["risk"]["summary"]["daily_loss_used_usd"], 97.81)
        self.assertEqual(doc["risk"]["summary"]["passed"], 1)
        self.assertEqual(doc["risk"]["summary"]["blocked"], 1)

    def test_margin_level(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(),
                                            cfg=self.cfg_with(risk={"min_margin_level_pct": 30000}))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R21", "Margin level would be 20,546%. Your minimum is 30000%.")

    def test_margin_used(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 12:43", eur(),
                                            cfg=self.cfg_with(risk={"max_margin_used_pct": 5}))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R20", "Margin would be $932.69, 9.3% of your balance. Your limit is 5%.")

    def test_netting(self):
        port = portfolio(position("EURUSD-1", "EURUSD", "buy", 0.5, 1.08, 1.078, 100.0))
        cfg = self.cfg_with(account={"hedging": False})
        code, _, _, doc, _ = self.run_check("2026-07-23 12:43", eur(), port=port, cfg=cfg)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R17", "Your account is netting: this sell would close or flip your open buy of "
                                    "EURUSD. Close it first (/fx-broker close).")

    def test_reward_risk_gross(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(take_profit=2440.0))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R11", "Reward:risk 0.19 is under your minimum 1.50.")

    def test_pending_side(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(type="stop", entry=2430.0))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R7", "A buy stop must be above the price now (ask 2,433.49). Use a market order, "
                                   "or plan again.")
        self.no_reason(doc, "R8")

    def test_bad_field_blocks_one(self):
        two = eur()
        bad = copy.deepcopy(GOLD["orders"][0])
        bad["lots"] = -1
        two["orders"].insert(0, bad)
        code, _, _, doc, _ = self.run_check("2026-07-23 12:43", two)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R1", "order 1: lots must be a number above 0, not -1")
        self.assertEqual(len(self.reasons(doc)), 1)
        self.assertEqual(doc["risk"]["verdicts"][1]["verdict"], "PASS")
        self.assertIsNone(doc["risk"]["verdicts"][0]["max_loss_usd"])

    def test_no_stop_allowed(self):
        cfg = self.cfg_with(risk={"require_stop": False})
        code, out, _, doc, _ = self.run_check("2026-07-17 09:08", gold(stop_loss=None), cfg=cfg)
        self.assertIn("require_stop is off: an order with no stop can pass.", doc["notes"])
        self.assertTrue(any(x.startswith("W-nostop No stop.") for x in self.warnings(doc)))
        self.assertEqual(doc["risk"]["verdicts"][0]["max_loss_usd"], 100.0)
        self.no_reason(doc, "R5")
        code, out, _, _, _ = self.run_check("2026-07-17 09:08", gold(stop_loss=None), cfg=cfg, words=True)
        self.assertIn("Note: require_stop is off", out)

    def test_no_stop_required(self):
        code, _, _, doc, _ = self.run_check("2026-07-17 09:08", gold(stop_loss=None, take_profit=None))
        self.assertEqual(code, 1)
        self.has_reason(doc, "R5", "No stop loss. Every trade needs one.")
        self.has_reason(doc, "R5", "No take profit. Every ticket has a stop and a target.")


class TestCloses(RiskBase):

    def port(self):
        return portfolio(position("EURUSD-1", "EURUSD", "sell", 0.86, 1.08452, 1.08561, 98.9, style="day"))

    def test_close_pass(self):
        code, out, err, doc, _ = self.run_check("2026-07-23 15:21", close_order(), port=self.port())
        self.assertEqual(code, 0, out + err)
        v = doc["risk"]["verdicts"][0]
        self.assertEqual(v["max_loss_usd"], 0.0)
        self.assertEqual(v["margin_usd"], 0.0)
        self.assertEqual(v["intent"], "close")
        code, out, _, _, _ = self.run_check("2026-07-23 15:21", close_order(), port=self.port(), words=True)
        self.assertIn("PASS #1 close EURUSD 0.86 lots: closes your SELL position EURUSD-1.", out)

    def test_close_never_blocked_by_limits(self):
        cfg = self.cfg_with(risk={"blocked": ["EURUSD"], "max_open_trades": 1}, allow_short=False)
        code, _, _, doc, _ = self.run_check("2026-07-25 10:00", close_order(created_at="2026-07-17T00:00:00Z"),
                                            port=self.port(), cfg=cfg)
        self.assertEqual(code, 0)

    def test_close_unknown_position(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 15:21", close_order(position_id="EURUSD-9"), port=self.port())
        self.assertEqual(code, 1)
        self.has_reason(doc, "C2", "There is no open position EURUSD-9.")

    def test_close_too_many_lots(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 15:21", close_order(lots=0.9), port=self.port())
        self.assertEqual(code, 1)
        self.has_reason(doc, "C3", "The position has 0.86 lots; this close says 0.90 lots.")

    def test_close_wrong_side(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 15:21", close_order(side="buy"), port=self.port())
        self.assertEqual(code, 1)
        self.has_reason(doc, "C3", "The position is a sell of 0.86 lots; this close says buy.")

    def test_close_leaves_dust(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 15:21", close_order(lots=0.855), port=self.port())
        self.assertEqual(code, 1)
        self.has_reason(doc, "C3", "0.855 lots is not a whole number of the lot step 0.01.")

    def test_close_with_prices(self):
        code, _, _, doc, _ = self.run_check("2026-07-23 15:21", close_order(stop_loss=1.09), port=self.port())
        self.assertEqual(code, 1)
        self.has_reason(doc, "C1", "order 1 closes a position. Leave entry, stop_loss, take_profit and expiry empty")

    def test_close_when_closed(self):
        code, _, _, doc, _ = self.run_check("2026-07-25 10:00", close_order(), port=self.port())
        self.assertEqual(code, 0)
        self.assertIn("W-closed The market is closed. MetaTrader cannot close it until Mon 2:05 AM PKT.",
                      self.warnings(doc))

    def test_live_warning(self):
        cfg = self.cfg_with(paper=False)
        code, out, _, _, _ = self.run_check("2026-07-23 15:21", close_order(), port=self.port(), cfg=cfg, words=True)
        self.assertEqual(code, 0)
        self.assertIn("Note: LIVE account: this is real money.", out)
        self.assertIn("1 passed, 0 blocked. Checked at Thu 3:21 PM PKT. LIVE account: this is real money.", out)


class TestErrors(RiskBase):

    def test_no_orders_file(self):
        code, out, err, _, work = self.run_check("2026-07-17 09:08", None)
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "check.py: no orders at fx/orders.json. Run fx-budget first.")
        self.assertFalse(os.path.exists(os.path.join(work, "fx", "state", "risk.json")))

    def test_empty_orders(self):
        code, _, err, _, _ = self.run_check("2026-07-17 09:08", {"created_at": "2026-07-17T04:07:00Z", "orders": []})
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "check.py: fx/orders.json has no orders. Run fx-budget with --write first.")

    def test_orders_not_a_list(self):
        code, _, err, _, _ = self.run_check("2026-07-17 09:08", {"created_at": "2026-07-17T04:07:00Z", "orders": {}})
        self.assertEqual(code, 2)
        self.assertIn('fx/orders.json: "orders" must be a list of JSON objects: [{...}]', err)

    def test_orders_not_an_object(self):
        code, _, err, _, _ = self.run_check("2026-07-17 09:08", [1, 2])
        self.assertEqual(code, 2)
        self.assertIn("fx/orders.json must be a JSON object: {...}", err)

    def test_bad_created_at(self):
        code, _, err, _, _ = self.run_check("2026-07-17 09:08", gold(created_at="yesterday"))
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), 'check.py: fx/orders.json: "created_at" is not a UTC time like '
                                      '2026-11-11T08:40:00Z')

    def test_broken_ledger(self):
        code, _, err, _, work = self.run_check("2026-07-17 09:08", gold(), ledger={"records": [{"status": "yes"}]})
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "check.py: fx/state/broker.json is damaged at record 1. Nothing was changed.")
        self.assertFalse(os.path.exists(os.path.join(work, "fx", "state", "risk.json")))

    def test_broken_portfolio(self):
        a = position("XAUUSD-1", "XAUUSD", "buy", 0.02, 2433.49, 2400.0, 97.81)
        code, _, err, _, _ = self.run_check("2026-07-17 09:08", gold(), port=portfolio(a, dict(a)))
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "check.py: fx/portfolio.json: two positions have the id XAUUSD-1. "
                                      "Fix it by hand or restore a backup.")

    def test_bad_now(self):
        code, _, err, _, _ = self.run_check("soon", gold())
        self.assertEqual(code, 2)
        self.assertIn('--now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)', err)

    def test_missing_typed_config(self):
        code, _, err, _, _ = self.run_check("2026-07-17 09:08", gold(), extra=("--config", "fx2/config.json"))
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "check.py: no config file at fx2/config.json (copy config.example.json "
                                      "from the fx-budget folder)")


class TestOutput(RiskBase):

    def test_json_shape(self):
        code, out, _, doc, work = self.run_check("2026-07-17 09:08", gold())
        self.assertEqual(code, 0)
        self.assertEqual(sorted(doc), sorted(["v", "tool", "cmd", "now", "ok", "notes", "risk"]))
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["now"], doc["ok"]),
                         (1, "fx-risk", None, "2026-07-17T04:08:00Z", True))
        with open(os.path.join(work, "fx", "state", "risk.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(doc["risk"], saved)
        self.assertEqual(saved["created_by"], "fx-risk")
        self.assertEqual(saved["orders_created_at"], "2026-07-17T04:07:00Z")
        keys = ["index", "fingerprint", "verdict", "symbol", "side", "intent", "lots", "max_loss_usd",
                "margin_usd", "reasons", "warnings"]
        self.assertEqual(sorted(saved["verdicts"][0]), sorted(keys))

    def test_state_written_on_block(self):
        code, _, _, doc, work = self.run_check("2026-07-23 12:43", eur(lots=0.87))
        self.assertEqual(code, 1)
        self.assertFalse(doc["ok"])
        with open(os.path.join(work, "fx", "state", "risk.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["summary"]["blocked"], 1)

    def test_words_have_no_ids(self):
        code, out, _, _, _ = self.run_check("2026-07-23 12:43", eur(lots=0.87), words=True)
        self.assertEqual(code, 1)
        self.assertNotIn("R10 ", out)
        self.assertNotIn("W-", out)
        self.assertIn("- Max loss $100.05", out)

    def test_quiet(self):
        code, out, _, _, _ = self.run_check("2026-07-17 09:08", gold(), words=True, extra=("--quiet",))
        self.assertEqual(code, 0)
        lines = [x for x in out.splitlines() if x.strip()]
        self.assertEqual(lines[0], "PASS #1 buy XAUUSD 0.02 lots: max loss $97.81 (limit $100.00), costs included.")
        self.assertEqual(lines[-1], "1 passed, 0 blocked. Checked at Fri 9:08 AM PKT. Paper account.")
        self.assertEqual(len(lines), 2)

    def test_no_banned_words(self):
        for now, orders in (("2026-07-17 09:08", gold()), ("2026-07-23 12:43", eur(lots=0.87))):
            code, out, _, _, _ = self.run_check(now, orders, words=True)
            low = out.lower()
            for word in fx_core.SIGNAL_BANNED:
                self.assertNotIn(word, low)
            self.assertNotIn(chr(0x2014), out)
            self.assertNotIn("!", out)

    def test_early_close_cutoff(self):
        order = gold(created_at="2026-07-03T16:00:00Z", style="day", expiry="2026-07-03T17:00:00Z")
        order["orders"][0]["plan"].update(flat_by_utc="2026-07-03T16:45:00Z", nights_est=0, exit_by=None)
        cfg = self.cfg_with(day={"no_entries_after": "23:00"})
        code, _, _, doc, _ = self.run_check("2026-07-03 21:40", order, cfg=cfg)
        self.assertEqual(code, 1)
        self.has_reason(doc, "R26", "Gold stops early today, at 1:00 PM New York (10:00 PM PKT). "
                                    "No new day trades after 9:30 PM PKT.")

    def test_roll_note_for_a_position(self):
        pos = position("XAUUSD-1", "XAUUSD", "buy", 0.02, 2433.49, 2400.0, 97.81, fp="c63ced9cb166dab1")
        order = close_order(symbol="XAUUSD", side="buy", lots=0.02, position_id="XAUUSD-1", style="swing")
        order["created_at"] = "2026-07-29T08:14:00Z"
        code, out, _, _, _ = self.run_check("2026-07-29 13:15", order, port=portfolio(pos), words=True)
        self.assertEqual(code, 0)
        self.assertIn("Note: Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same", out)
        self.assertIn("XAUUSD did not jump: keep the SL and TP you set in", out)
        self.assertIn("PASS #1 close XAUUSD 0.02 lots", out)


class TestHelpers(unittest.TestCase):
    """Unit tests of check.py's own helpers (no subprocess)."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load.load("fx-risk", "check")
        cls.cfg = fx_core.load_config(os.path.join(MARKET, "config.json"))

    def test_pct_text(self):
        self.assertEqual([self.mod.pct_text(x) for x in (1, 0.5, 0.25, 2.0, 30)],
                         ["1.0%", "0.5%", "0.25%", "2.0%", "30.0%"])

    def test_signed_pct(self):
        self.assertEqual(self.mod.signed_pct(1.008999192897498), "+0.90%")
        self.assertEqual(self.mod.signed_pct(0.99), "-1.00%")

    def test_name_and_closed_words(self):
        gold = fx_core.instrument(self.cfg, "XAUUSD")
        eurusd = fx_core.instrument(self.cfg, "EURUSD")
        brent = fx_core.instrument(self.cfg, "UKOIL")
        self.assertEqual((self.mod.name_word(gold), self.mod.name_word(gold, True)), ("gold", "Gold"))
        self.assertEqual(self.mod.name_word(eurusd, True), "EURUSD")
        self.assertEqual(self.mod.closed_words({"state": "break"}, gold), "CME daily break")
        self.assertEqual(self.mod.closed_words({"state": "break"}, brent), "Brent daily break")
        self.assertEqual(self.mod.closed_words({"state": "break"}, eurusd), "the 5:00 PM New York rollover")
        self.assertEqual(self.mod.closed_words({"state": "weekend"}, eurusd), "weekend")

    def test_daily_loss_example(self):
        # 05 5.4: one open taken today (max loss 95, still open) and a swing trade from
        # Monday closed today at -120: used 215.
        recs = [dict(record("yes", "f1", "2026-11-11", 95.0)), dict(record("filled", "f1", "2026-11-11", 95.0))]
        port = {"positions": [{"fingerprint": "f1"}],
                "closed": [{"id": "XAUUSD-3", "opened_at": "2026-11-09T08:00:00Z", "closed_at": "2026-11-11T08:00:00Z",
                            "fx_day_closed": "2026-11-11", "result_usd": -120.0},
                           {"id": "XAUUSD-4", "opened_at": "2026-11-09T08:00:00Z", "fx_day_closed": "2026-11-11",
                            "result_usd": 300.0}]}
        lt = self.mod.ledger_today(recs, port, "2026-11-11", "17:00 New York")
        self.assertEqual(lt["used_today"], fx_core.Decimal("215.0"))
        self.assertEqual(list(lt["taken_today"]), ["f1"])
        self.assertEqual(lt["yes_today"], {"f1"})
        self.assertEqual(lt["pending"], set())

    def test_gap_loss_counts_bigger(self):
        filled = dict(record("filled", "f2", "2026-11-11", 95.0), position_id="EURUSD-2")
        port = {"positions": [], "closed": [{"id": "EURUSD-2", "opened_at": "2026-11-11T08:00:00Z",
                                             "fx_day_closed": "2026-11-11", "result_usd": -140.0}]}
        recs = [record("yes", "f2", "2026-11-11", 95.0), filled, record("closed", "f2", "2026-11-11", None)]
        lt = self.mod.ledger_today(recs, port, "2026-11-11", "17:00 New York")
        self.assertEqual(lt["used_today"], fx_core.Decimal("140.0"))
        placed = [record("placed", "f3", "2026-11-10", 50.0)]
        lt = self.mod.ledger_today(placed, {"positions": [], "closed": []}, "2026-11-11", "17:00 New York")
        self.assertEqual(lt["pending"], {"f3"})
        self.assertEqual(lt["taken_today"], {})

    def test_rate_reasons(self):
        usdjpy = fx_core.instrument(self.cfg, "USDJPY")
        m = {"rate_err": None}
        old = {"USD": {"age_h": 0.0}, "JPY": {"age_h": 30.4, "usd": 0.0066}}
        out = self.mod.rate_reasons(usdjpy, m, {"cfg": self.cfg, "rates": old})
        self.assertEqual(out, ["R30 The JPY rate is 30 hours old. Run once with Yahoo on to refresh it."])
        out = self.mod.rate_reasons(usdjpy, m, {"cfg": self.cfg, "rates": {"USD": {}}})
        self.assertEqual(out, ["R30 No USD rate for JPY. It comes from USDJPY. Keep USDJPY in your config "
                               "and run once with Yahoo on (not --offline)."])

    def test_fetch_all_with_fakes(self):
        calls, rolls = [], []

        def fake_refresh(data_dir, symbol, interval, now, cfg, offline):
            calls.append((symbol, interval, offline))
            if symbol == "GC=F":
                raise ValueError("Yahoo is down")
            return {}
        saved = (self.mod.refresh, self.mod.check_roll_forward)
        self.mod.refresh = fake_refresh
        self.mod.check_roll_forward = lambda data_dir, inst, cfg, now, offline, every_run=False: rolls.append(inst["key"])
        try:
            orders = [GOLD["orders"][0], EUR["orders"][0], CLOSE["orders"][0]]
            port = {"positions": [{"symbol": "XAUUSD"}, {"symbol": "GBPUSD"}], "closed": []}
            now = fx_core.parse_now("2026-07-23 12:43")
            failed = self.mod.fetch_all(orders, port, self.cfg, os.path.join(MARKET, "data"), now, False)
        finally:
            self.mod.refresh, self.mod.check_roll_forward = saved
        # rates first (EURUSD, with 5m too: it is also a day order), then orders, then positions
        self.assertEqual(calls, [("EURUSD=X", "1h", False), ("EURUSD=X", "5m", False), ("GC=F", "1h", False),
                                 ("GBPUSD=X", "1h", False)])
        self.assertEqual(rolls, ["XAUUSD"])
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].startswith("Yahoo could not be read for GC=F. Using the saved prices ("), failed)

    def test_fresh_quote_prices_json(self):
        tmp = tempfile.mkdtemp(prefix="fx-risk-q-")
        try:
            paths = fx_core.fx_paths(tmp, os.path.join(MARKET, "data"))
            gold = fx_core.instrument(self.cfg, "XAUUSD")
            now = fx_core.parse_now("2026-07-17 09:08")
            doc = {"updated_at": "2026-07-17T04:07:00Z",
                   "quotes": {"XAUUSD": {"yahoo": "GC=F", "bid": 2435.0, "at": "2026-07-17T04:07:00Z"}}}
            with open(paths["prices"], "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            q = self.mod.fresh_quote(paths["data"], paths, gold, now, self.cfg, False)
            self.assertEqual((q["bid"], q["source"], q["age_min"]), (2435.0, "prices.json", 1.0))
            q = self.mod.fresh_quote(paths["data"], paths, gold, now, self.cfg, True)
            self.assertEqual((q["bid"], q["source"]), (2433.4, "yahoo 5m"))
            doc["quotes"]["XAUUSD"]["at"] = "2026-07-17T04:00:00Z"
            with open(paths["prices"], "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            q = self.mod.fresh_quote(paths["data"], paths, gold, now, self.cfg, False)
            self.assertEqual(q["source"], "yahoo 5m")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fresh_check_minutes(self):
        self.assertEqual(self.mod.FRESH_CHECK_MINUTES, 5)


if __name__ == "__main__":
    unittest.main()
