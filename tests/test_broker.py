"""fx-broker (ticket.py): offline tests on the made-up sample market (spec fx-broker 10)."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib
import io
import json
import shutil
import socket
import subprocess
import tempfile
import unittest

import _load
import fx_core

ROOT = _load.ROOT
SCRIPT = os.path.join(ROOT, "fx-broker", "ticket.py")
MARKET = os.path.join(ROOT, "tests", "fixtures", "fx-market")
T = _load.load("fx-broker", "ticket")

GOLD = {"symbol": "XAUUSD", "side": "buy", "intent": "open", "lots": 0.02, "type": "market",
        "entry": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0, "expiry": "gtc", "style": "swing",
        "broker_symbol": "XAUUSD", "position_id": None, "rolls_applied": {},
        "plan": {"setup": "breakout_20d", "fx_day": "2026-07-17", "chart_entry": 2431.6, "chart_stop": 2400.0,
                 "risk_per_unit": 33.49, "target_r": 1.7, "flat_by": None, "flat_by_utc": None,
                 "exit_by": "2026-08-13T21:00:00Z", "nights_est": 28, "max_loss_usd": 97.81,
                 "costs_usd": 30.83, "margin_usd": 48.67, "usd_per_point_per_lot": 1.0},
        "decided_by": "risk_pct", "why": "made up for the test"}
EURO = {"symbol": "EURUSD", "side": "sell", "intent": "open", "lots": 0.86, "type": "market",
        "entry": 1.08452, "stop_loss": 1.08561, "take_profit": 1.08234, "expiry": "2026-07-23T18:30:00Z",
        "style": "day", "broker_symbol": "EURUSD", "position_id": None, "rolls_applied": {},
        "plan": {"setup": "or_breakdown", "fx_day": "2026-07-23", "chart_entry": 1.08452, "chart_stop": 1.0856,
                 "risk_per_unit": 0.00109, "target_r": 2.0, "flat_by": "23:30",
                 "flat_by_utc": "2026-07-23T18:30:00Z", "exit_by": None, "nights_est": 0,
                 "max_loss_usd": 98.9, "costs_usd": 5.16, "margin_usd": 932.69, "usd_per_point_per_lot": 1.0},
        "decided_by": "risk_pct", "why": "made up for the test"}
NOT_A_YES = ["ok", "yes?", "yes to all", "yes but wait", "yes, sell it", "yes, close it", "yes 0.05",
             "yes EURUSD", "yes double", "yes, limit", "maybe", ""]
YES_GOLD = ["yes", "YES", "y", "yes, buy 0.02 lots XAUUSD", "yes, buy 0.02 XAUUSD, SL 2400, TP 2490"]


def flat(text):
    """Text with every run of spaces and line breaks made one space (for wrapped notes)."""
    return " ".join(text.split())


def utc(pkt):
    """Karachi time text -> the UTC text files use."""
    return fx_core.utc_iso(fx_core.parse_now(pkt))


class Base(unittest.TestCase):
    """A temp project: fx/data is a copy of the sample market, fx/config.json its config."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="fxbroker-base-")
        shutil.copytree(os.path.join(MARKET, "data"), os.path.join(cls.base, "data"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fxbroker-")
        self.fx = os.path.join(self.dir, "fx")
        os.makedirs(os.path.join(self.fx, "state"))
        shutil.copytree(os.path.join(self.base, "data"), os.path.join(self.fx, "data"))
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(self.fx, "config.json"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- files
    def path(self, name):
        return os.path.join(self.fx, name)

    def write(self, name, doc):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)

    def read(self, name, default=None):
        if not os.path.exists(self.path(name)):
            return default
        with open(self.path(name), encoding="utf-8") as fh:
            return json.load(fh)

    def config(self, **top):
        cfg = self.read("config.json")
        for k, v in top.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        self.write("config.json", cfg)

    def orders(self, *orders, created="2026-07-17 09:07"):
        self.write("orders.json", {"created_by": "fx-budget", "created_at": utc(created),
                                   "orders": [dict(o) for o in orders]})

    def passed(self, at, verdict="PASS", warnings=None):
        """state/risk.json with a verdict for each order now in orders.json."""
        orders = self.read("orders.json")["orders"]
        vs = []
        for n, o in enumerate(orders, 1):
            plan = o.get("plan") or {}
            vs.append({"index": n, "fingerprint": fx_core.fingerprint(o), "verdict": verdict,
                       "symbol": o["symbol"], "side": o["side"], "intent": o["intent"], "lots": o["lots"],
                       "max_loss_usd": plan.get("max_loss_usd"), "margin_usd": plan.get("margin_usd"),
                       "reasons": [], "warnings": list(warnings or [])})
        self.write("state/risk.json", {"checked_at": utc(at), "orders_created_at": None,
                                       "verdicts": vs, "summary": {}})

    def records(self):
        return (self.read("state/broker.json") or {"records": []})["records"]

    def run_t(self, *args, now=None, code=None):
        """Run ticket.py in the temp folder; returns (exit code, stdout, stderr)."""
        cmd = [sys.executable, SCRIPT] + list(args) + ["--offline"]
        if now:
            cmd += ["--now", now]
        p = subprocess.run(cmd, cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=120)
        if code is not None:
            self.assertEqual(p.returncode, code, "%s\nstdout:\n%s\nstderr:\n%s" % (args, p.stdout, p.stderr))
        return p.returncode, p.stdout, p.stderr

    # ---- the sample trades, step by step
    def gold_filled(self):
        """The gold BUY approved with the broker price and filled on paper (07-17)."""
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        self.run_t("approve", "--index", "1", "--reply", "yes", "--broker-price", "2429.15",
                   now="2026-07-17 09:10", code=0)
        self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-17 09:11", code=0)

    def euro_filled(self):
        """The EURUSD day SELL approved and filled on paper (07-23 12:44)."""
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-23 12:44", code=0)
        self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-23 12:45", code=0)


class TestShow(Base):
    def test_no_pass(self):
        self.orders(GOLD)
        code, out, _ = self.run_t("show", now="2026-07-17 09:09", code=1)
        self.assertIn("NOT READY: No risk check found. Run fx-risk first.", out)
        self.assertNotIn("In MetaTrader:", out)

    def test_old_pass(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        code, out, _ = self.run_t("show", now="2026-07-17 09:14", code=1)
        self.assertIn("The fx-risk check is 6 min old. A PASS counts for 5 minutes", out)

    def test_check_from_later(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        code, out, _ = self.run_t("show", now="2026-07-17 09:07", code=1)
        self.assertIn("from after the time now", out)

    def test_ready_no_line(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        code, out, _ = self.run_t("show", now="2026-07-17 09:09", code=0)
        self.assertTrue(out.startswith("READY: fx-risk PASS for this exact order (checked 9:08 AM PKT)."))
        self.assertNotIn("In MetaTrader:", out)
        for text in ("BUY XAUUSD, market, 0.02 lots", "about 33.49 below the price (3,349 points)",
                     "Plan levels       entry 2,433.49, SL 2,400.00, TP 2,490.00 (Yahoo's scale",
                     "Type your MetaTrader price with --broker-price to get exact levels.",
                     "good till cancelled (swing: up to 20 FX days, to Fri 14 Aug 2:00 AM PKT)",
                     "Max loss at the stop: $97.81, costs included ($66.98 price and spread,",
                     "$0.12 commission, $30.71 swap for 28 nights).", "Margin used: about $48.67.",
                     "Note: a buy opens at the ask and its stop fires on the bid.",
                     "Swap is booked each night at 5:00 PM New York (2:00 AM PKT).",
                     "On Monday the price can open past your stop.",
                     "Type yes to approve this exact order. Anything else is a no."):
            self.assertIn(flat(text), flat(out))

    def test_block_and_unknown_fingerprint(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08", verdict="BLOCK")
        self.assertIn("fx-risk said BLOCK for this order.", self.run_t("show", now="2026-07-17 09:09", code=1)[1])
        changed = dict(GOLD, lots=0.03)
        self.orders(changed)
        self.write("state/risk.json", dict(self.read("state/risk.json"), checked_at=utc("2026-07-17 09:08")))
        out = self.run_t("show", now="2026-07-17 09:09", code=1)[1]
        self.assertIn("fx-risk has not checked this exact order. Run fx-risk again.", out)

    def test_broker_price_levels(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        out = self.run_t("show", "--index", "1", "--broker-price", "2429.15", now="2026-07-17 09:09", code=0)[1]
        for text in ("Stop loss (SL)    2,395.75   33.49 below the price (3,349 points)",
                     "Take profit (TP)  2,485.75   56.51 above the price (5,651 points)",
                     "Price now         2,433.40   Yahoo price (gold futures), not your broker's price, 4 min old",
                     "Your price        2,429.15   your broker's price (you typed it), live",
                     "Broker levels     entry 2,429.24 (Yahoo plan: entry 2,433.49, SL 2,400.00, TP 2,490.00)",
                     "Yahoo's price was 4 min old, so these levels may be off by about $1.68."):
            self.assertIn(text, out)
        self.assertNotIn("In MetaTrader:", out)
        rec = self.read("prices.json")["broker"]["XAUUSD"]
        self.assertEqual(rec["bid"], 2429.15)
        self.assertEqual(rec["yahoo_bid"], 2433.4)
        self.assertEqual(rec["k"], 0.998253)

    def test_k_too_far(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        code, out, err = self.run_t("show", "--index", "1", "--broker-price", "2000", now="2026-07-17 09:09", code=2)
        self.assertIn("ticket.py: Your broker price 2000 for XAUUSD is far from Yahoo's 2433.40 (more than 3%).", err)
        self.assertIsNone(self.read("prices.json"))

    def test_spot_day_sell(self):
        self.orders(EURO, created="2026-07-23 12:41")
        self.passed("2026-07-23 12:41")
        out = self.run_t("show", now="2026-07-23 12:42", code=0)[1]
        for text in ("SELL EURUSD, market, 0.86 lots", "Stop loss (SL)    1.08561   10.9 pips above the price (109 points)",
                     "Take profit (TP)  1.08234   21.8 pips below the price (218 points)",
                     "Yahoo price, close to your broker's price",
                     "Expiry            close by 11:30 PM PKT (2:30 PM New York) today (flat-by)",
                     "Max loss at the stop: $98.90, costs included ($93.74 price and spread,",
                     "Margin used: about $932.69.", "Note: a sell opens at the bid and its stop fires on the ask.",
                     "high-impact event at Thu 5:15 PM PKT (8:15 AM New York):"):
            self.assertIn(text, out)
        self.assertNotIn("swing trade", out)

    def test_market_shut(self):
        self.orders(GOLD)
        self.passed("2026-07-22 02:29")
        out = self.run_t("show", now="2026-07-22 02:30", code=1)[1]
        self.assertIn("NOT READY: The gold market is shut now (Daily break from 5:00 PM to 6:00 PM New York). "
                      "It opens Wed 3:00 AM PKT.", out)

    def test_warnings_without_id(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08", warnings=["W-net After costs the reward is small."])
        out = self.run_t("show", now="2026-07-17 09:09", code=0)[1]
        self.assertIn("Note: After costs the reward is small.", out)
        self.assertNotIn("W-net", out)
        quiet = self.run_t("show", "--quiet", now="2026-07-17 09:09", code=0)[1]
        self.assertNotIn("Note:", quiet)

    def test_json(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        out = self.run_t("show", "--json", "--broker-price", "2429.15", now="2026-07-17 09:09", code=0)[1]
        doc = json.loads(out)
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["ok"]), (1, "fx-broker", "show", True))
        t = doc["tickets"][0]
        self.assertEqual(t["fingerprint"], fx_core.fingerprint(GOLD))
        self.assertTrue(t["ready"])
        self.assertEqual(t["expiry_server"], "gtc")
        self.assertEqual(t["broker"]["entry"], 2429.24)
        self.assertEqual((t["stop_distance"], t["target_distance"]), (33.49, 56.51))
        self.assertEqual(t["max_loss_usd"], 97.81)
        self.assertTrue(any("a buy opens at the ask" in n for n in doc["notes"]))


class TestApprove(Base):
    def test_not_a_yes(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        for reply in NOT_A_YES:
            code, out, _ = self.run_t("approve", "--index", "1", "--reply", reply, "--broker-price", "2429.15",
                                      now="2026-07-17 09:10")
            self.assertEqual(code, 1, reply)
            self.assertTrue(out.startswith("Not recorded: "), out)
            self.assertIn("Ask again with the ticket for order 1.", out)
            self.assertNotIn("In MetaTrader:", out)
            self.assertIsNone(self.read("state/broker.json"), reply)
            self.assertIsNone(self.read("prices.json"), reply)

    def test_yes_passes(self):
        for reply in YES_GOLD:
            if os.path.exists(self.path("state/broker.json")):
                os.remove(self.path("state/broker.json"))
            self.orders(GOLD)
            self.passed("2026-07-17 09:08")
            code, out, err = self.run_t("approve", "--index", "1", "--reply", reply, "--broker-price", "2429.15",
                                        now="2026-07-17 09:10")
            self.assertEqual(code, 0, reply + out + err)
            recs = self.records()
            self.assertEqual([r["status"] for r in recs], ["yes"])
            self.assertEqual(recs[0]["reply"], reply)
            self.assertEqual(sorted(recs[0]), sorted(T.RECORD_KEYS))

    def test_reply_kept_exactly(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        self.run_t("approve", "--index", "1", "--reply", "  Yes  ", "--broker-price", "2429.15",
                   now="2026-07-17 09:10", code=0)
        self.assertEqual(self.records()[0]["reply"], "  Yes  ")

    def test_futures_needs_price(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        out = self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-17 09:10", code=1)[1]
        self.assertIn("Gold needs your MetaTrader price for exact SL and TP levels. Ask for the XAUUSD bid "
                      "in MetaTrader, then run approve again with --broker-price P.", out)
        self.assertIsNone(self.read("state/broker.json"))
        out = self.run_t("approve", "--index", "1", "--reply", "yes, sell it", now="2026-07-17 09:10", code=1)[1]
        self.assertIn("the reply says sell, but this order is a buy", out)

    def test_broker_levels(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        out = self.run_t("approve", "--index", "1", "--reply", "yes", "--broker-price", "2429.15",
                         now="2026-07-17 09:10", code=0)[1]
        self.assertIn("Recorded your yes for order 1: BUY XAUUSD, market, 0.02 lots.", out)
        self.assertIn("Broker levels: entry 2,429.24, SL 2,395.75, TP 2,485.75 (from your broker's price "
                      "(you typed it), 2,429.15).", out)
        self.assertIn("In MetaTrader: New order > Symbol XAUUSD > Market Execution > Volume 0.02 > Stop Loss "
                      "2395.75 > Take Profit 2485.75 > Buy by Market.", out)
        self.assertIn("Paper trade: do not place it in MetaTrader.", out)
        self.assertEqual(self.read("prices.json")["broker"]["XAUUSD"]["bid"], 2429.15)
        rec = self.records()[0]
        self.assertEqual((rec["broker_price"], rec["max_loss_usd"], rec["paper"]), (2429.24, 97.81, True))
        self.assertEqual(rec["plain"], "You said yes to BUY 0.02 lots XAUUSD.")

    def test_saved_price_is_used(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        self.run_t("show", "--broker-price", "2429.15", now="2026-07-17 09:09", code=0)
        out = self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-17 09:10", code=0)[1]
        self.assertIn("SL 2,395.75, TP 2,485.75 (from your broker's price (you typed it), 2,429.15, 1 min old)", out)

    def test_answered_once(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        args = ("approve", "--index", "1", "--reply", "yes", "--broker-price", "2429.15")
        self.run_t(*args, now="2026-07-17 09:10", code=0)
        out = self.run_t(*args, now="2026-07-17 09:11", code=1)[1]
        self.assertIn("NOT READY: You already answered order 1 today. Run fx-budget and fx-risk for a new "
                      "order. Nothing was recorded.", out)
        self.assertEqual(len(self.records()), 1)
        out = self.run_t("decline", "--index", "1", now="2026-07-17 09:11", code=1)[1]
        self.assertIn("You already answered order 1 today.", out)

    def test_decline(self):
        self.orders(EURO, created="2026-07-23 12:43")
        out = self.run_t("decline", "--index", "1", "--reply", "no thanks", now="2026-07-23 12:44", code=0)[1]
        self.assertIn("Recorded your no for order 1. Nothing goes to MetaTrader.", out)
        rec = self.records()[0]
        self.assertEqual((rec["status"], rec["reply"], rec["plain"]),
                         ("no", "no thanks", "You said no to SELL 0.86 lots EURUSD."))

    def test_spot_needs_no_price(self):
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        out = self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-23 12:44", code=0)[1]
        self.assertIn("In MetaTrader: New order > Symbol EURUSD > Market Execution > Volume 0.86 > Stop Loss "
                      "1.08561 > Take Profit 1.08234 > Sell by Market.", out)
        self.assertIn("spot FX: Yahoo's price is close to your broker's", out)

    def test_json_line(self):
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        doc = json.loads(self.run_t("approve", "--index", "1", "--reply", "yes", "--json",
                                    now="2026-07-23 12:44", code=0)[1])
        self.assertTrue(doc["line"].startswith("In MetaTrader: "))
        self.assertEqual(doc["record"]["status"], "yes")
        doc = json.loads(self.run_t("approve", "--index", "1", "--reply", "yes", "--json",
                                    now="2026-07-23 12:44", code=1)[1])
        self.assertIsNone(doc["line"])
        self.assertFalse(doc["ok"])


class TestResult(Base):
    def test_paper_fill(self):
        self.gold_filled()
        pos = self.read("portfolio.json")["positions"]
        self.assertEqual(len(pos), 1)
        p = pos[0]
        self.assertEqual((p["id"], p["lots"], p["paper"], p["fill_price"], p["entry"]),
                         ("XAUUSD-1", 0.02, True, 2433.49, 2433.49))
        self.assertEqual((p["broker_stop_loss"], p["broker_take_profit"], p["max_loss_usd"]), (2395.75, 2485.75, 97.81))
        self.assertEqual((p["opened_at"], p["fx_day_opened"], p["exit_by"], p["stop_at_open"]),
                         ("2026-07-17T04:11:00Z", "2026-07-17", "2026-08-13T21:00:00Z", 2400.0))
        self.assertEqual((p["swap_nights"], p["swap_usd"], p["rolls_applied"]), (0, 0.0, {}))
        self.assertEqual(p["fingerprint"], fx_core.fingerprint(GOLD))
        acc = self.read("portfolio.json")["account"]
        self.assertEqual(acc, {"currency": "USD", "balance": 10000.0, "start_balance": 10000.0,
                               "realized_usd": 0.0, "paper": True})
        rec = self.records()[-1]
        self.assertEqual((rec["status"], rec["price"], rec["position_id"]), ("filled", 2433.49, "XAUUSD-1"))
        self.assertEqual(rec["plain"], "Paper fill: BUY 0.02 lots XAUUSD at 2,433.49.")
        out = self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-17 09:12", code=1)[1]
        self.assertIn("Order 1 is already filled (XAUUSD-1).", out)

    def test_no_yes_yet(self):
        self.orders(GOLD)
        out = self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-17 09:11", code=1)[1]
        self.assertIn("No yes is recorded for order 1 today. Run approve first.", out)

    def test_failed_then_closed(self):
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-23 12:44", code=0)
        out = self.run_t("result", "--index", "1", "--status", "failed", "--note", "MetaTrader said no money",
                         now="2026-07-23 12:45", code=0)[1]
        self.assertIn("Recorded order 1 as failed.", out)
        self.assertEqual(self.records()[-1]["plain"],
                         "Order 1 (SELL 0.86 lots EURUSD) failed: MetaTrader said no money.")
        out = self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-23 12:46", code=1)[1]
        self.assertIn("Order 1 is closed in the ledger (status: failed). It needs a new fx-risk check and a "
                      "new yes.", out)
        self.assertIsNone(self.read("portfolio.json"))

    def test_pending_paper_waits(self):
        order = dict(GOLD, type="stop", entry=2440.0)
        self.orders(order)
        self.passed("2026-07-17 09:08")
        out = self.run_t("approve", "--index", "1", "--reply", "yes, buy stop", "--broker-price", "2429.15",
                         now="2026-07-17 09:10", code=0)[1]
        self.assertIn("> Pending Order > Buy Stop > Volume 0.02 > Price ", out)
        out = self.run_t("result", "--index", "1", "--status", "placed", now="2026-07-17 09:11", code=0)[1]
        self.assertIn("Paper: the order waits at 2,440.00. When the price gets there, record it: python3 "
                      "fx-broker/ticket.py result --index 1 --status filled", out)
        self.assertEqual(self.records()[-1]["plain"], "Paper order waits: BUY STOP 0.02 lots XAUUSD at 2,440.00.")
        self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-17 09:30", code=0)
        p = self.read("portfolio.json")["positions"][0]
        self.assertEqual(p["fill_price"], 2440.0)

    def test_market_placed_is_filled_on_paper(self):
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-23 12:44", code=0)
        self.run_t("result", "--index", "1", "--status", "placed", now="2026-07-23 12:45", code=0)
        self.assertEqual([r["status"] for r in self.records()], ["yes", "placed", "filled"])
        self.assertEqual(self.read("portfolio.json")["positions"][0]["id"], "EURUSD-1")

    def test_live_fill(self):
        self.config(paper=False)
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        out = self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-23 12:44", code=0)[1]
        self.assertIn("Place it in your own MetaTrader. Then record it:", out)
        err = self.run_t("result", "--index", "1", "--status", "filled", now="2026-07-23 12:45", code=2)[2]
        self.assertIn("ticket.py: A live fill needs --fill P: the price MetaTrader shows for the fill.", err)
        self.run_t("result", "--index", "1", "--status", "filled", "--fill", "1.08450",
                   now="2026-07-23 12:45", code=0)
        p = self.read("portfolio.json")["positions"][0]
        self.assertEqual((p["broker_fill_price"], p["fill_price"], p["paper"]), (1.0845, 1.0845, False))
        self.assertEqual(self.records()[-1]["plain"], "Filled in MetaTrader: SELL 0.86 lots EURUSD at 1.08450.")

    def test_close_result(self):
        self.gold_filled()
        self.euro_filled()
        self.run_t("close", "--position", "EURUSD-1", now="2026-07-23 15:21", code=0)
        self.passed("2026-07-23 15:21")
        out = self.run_t("approve", "--index", "1", "--reply", "yes", now="2026-07-23 15:22", code=0)[1]
        self.assertIn("In MetaTrader: Toolbox > Trade > right click the EURUSD row > Close Position.", out)
        self.assertIn("Do not place a BUY order to close it: on a hedging account that opens a second trade.", out)
        out = self.run_t("result", "--index", "1", "--status", "filled", "--fill", "1.08234",
                         now="2026-07-23 15:22", code=0)[1]
        self.assertIn("Paper close: EURUSD-1 SELL 0.86 lots at 1.08234 (target). Result +$182.32 after all costs.", out)
        self.assertIn("Balance now $10,182.32 (paper).", out)
        pf = self.read("portfolio.json")
        self.assertEqual([p["id"] for p in pf["positions"]], ["XAUUSD-1"])
        c = pf["closed"][0]
        self.assertEqual((c["id"], c["why"], c["result_usd"], c["costs_usd"], c["swap_usd"], c["exit_price"]),
                         ("EURUSD-1", "target", 182.32, 5.16, 0.0, 1.08234))
        self.assertEqual(pf["account"]["balance"], 10000.0 + c["result_usd"])
        self.assertEqual(pf["account"]["realized_usd"], 182.32)
        rec = self.records()[-1]
        self.assertEqual((rec["status"], rec["intent"], rec["result_usd"], rec["max_loss_usd"]),
                         ("closed", "close", 182.32, None))


class TestClose(Base):
    def test_close_order_and_words(self):
        self.gold_filled()
        out = self.run_t("close", "--position", "XAUUSD-1", now="2026-07-17 10:00", code=0)[1]
        self.assertIn("Close order for XAUUSD-1 written to fx/orders.json (it replaced 1 earlier order).", out)
        o = self.read("orders.json")["orders"]
        self.assertEqual(len(o), 1)
        o = o[0]
        self.assertEqual((o["intent"], o["side"], o["type"], o["decided_by"], o["position_id"], o["lots"]),
                         ("close", "buy", "market", "close", "XAUUSD-1", 0.02))
        self.assertEqual([o[k] for k in ("entry", "stop_loss", "take_profit", "expiry")], [None] * 4)
        self.assertEqual(o["why"], "Close XAUUSD-1: your BUY of XAUUSD, 0.02 lots, opened Fri 17 Jul 9:11 AM PKT.")
        self.assertEqual(o["plan"]["max_loss_usd"], 0.0)
        self.assertEqual(self.read("orders.json")["created_by"], "fx-broker")
        self.passed("2026-07-17 10:00")
        show = self.run_t("show", now="2026-07-17 10:01", code=0)[1]
        self.assertIn("Close XAUUSD-1: your BUY of XAUUSD, 0.02 lots, opened Fri 9:11 AM PKT", show)
        self.assertIn("Use the position's Close button in MetaTrader. Not a new SELL order.", show)
        out = self.run_t("approve", "--index", "1", "--reply", "yes, sell it", now="2026-07-17 10:01", code=1)[1]
        self.assertIn("This order closes your BUY of XAUUSD. Reply yes or yes, close.", out)
        self.assertNotIn("In MetaTrader:", out)
        for reply in ("yes, buy", "yes, close it", "yes 0.05"):
            self.assertEqual(self.run_t("approve", "--index", "1", "--reply", reply, now="2026-07-17 10:01")[0], 1)
        self.assertEqual([r["status"] for r in self.records()], ["yes", "filled"])
        self.run_t("approve", "--index", "1", "--reply", "yes, close 0.02 lots XAUUSD", now="2026-07-17 10:01", code=0)
        self.assertEqual(self.records()[-1]["plain"], "You said yes to close XAUUSD-1.")

    def test_unknown_position(self):
        self.gold_filled()
        err = self.run_t("close", "--position", "EURUSD-2", now="2026-07-17 10:00", code=2)[2]
        self.assertIn("ticket.py: There is no open position EURUSD-2. Open positions: XAUUSD-1.", err)
        err = self.run_t("stop", "--position", "EURUSD-2", "--to", "1.1", now="2026-07-17 10:00", code=2)[2]
        self.assertIn("There is no open position EURUSD-2.", err)

    def test_no_positions(self):
        err = self.run_t("close", "--position", "XAUUSD-1", now="2026-07-17 10:00", code=2)[2]
        self.assertIn("ticket.py: There is no open position XAUUSD-1. You have no open positions.", err)

    def test_fill_needs_already(self):
        self.gold_filled()
        err = self.run_t("close", "--position", "XAUUSD-1", "--fill", "2420", now="2026-07-17 10:00", code=2)[2]
        self.assertIn("ticket.py: --fill and --why go with --already.", err)
        err = self.run_t("close", "--position", "XAUUSD-1", "--already", now="2026-07-17 10:00", code=2)[2]
        self.assertIn("close --already needs --fill P", err)

    def test_already_closed_in_metatrader(self):
        self.gold_filled()
        out = self.run_t("close", "--position", "XAUUSD-1", "--already", "--fill", "2395.10",
                         now="2026-07-20 09:00", code=0)[1]
        self.assertIn("(stop)", out)
        self.assertIn("Your stop was at 2,400.00. The price opened past it, at 2,395.10. The stop filled there.", out)
        rec = self.records()[-1]
        self.assertEqual((rec["status"], rec["fingerprint"], rec["index"]), ("closed", None, None))
        self.assertTrue(rec["plain"].endswith("(recorded after MetaTrader closed it)"))
        c = self.read("portfolio.json")["closed"][0]
        self.assertEqual((c["why"], c["exit_price"]), ("stop", 2395.1))
        self.assertEqual(self.read("portfolio.json")["positions"], [])

    def test_flatten(self):
        self.gold_filled()
        self.euro_filled()
        out = self.run_t("flatten", now="2026-07-23 14:00", code=0)[1]
        self.assertIn("Be flat by Thu 11:30 PM PKT (2:30 PM New York).", out)
        o = self.read("orders.json")["orders"]
        self.assertEqual(len(o), 1)
        self.assertEqual((o[0]["position_id"], o[0]["side"], o[0]["decided_by"], o[0]["intent"]),
                         ("EURUSD-1", "sell", "close", "close"))
        self.run_t("flatten", "--all", now="2026-07-23 14:00", code=0)
        self.assertEqual([x["position_id"] for x in self.read("orders.json")["orders"]], ["XAUUSD-1", "EURUSD-1"])

    def test_flatten_none(self):
        self.gold_filled()
        self.orders(EURO, created="2026-07-23 12:43")
        out = self.run_t("flatten", now="2026-07-23 14:00", code=1)[1]
        self.assertIn("No open day trades. Nothing to close.", out)
        self.assertEqual(self.read("orders.json")["orders"][0]["symbol"], "EURUSD")
        out = self.run_t("flatten", "--all", now="2026-07-23 14:00", code=0)[1]
        os.remove(self.path("portfolio.json"))
        out = self.run_t("flatten", "--all", now="2026-07-23 14:00", code=1)[1]
        self.assertIn("You have no open positions.", out)

    def test_ready_needs_the_position(self):
        self.gold_filled()
        self.run_t("close", "--position", "XAUUSD-1", now="2026-07-17 10:00", code=0)
        self.run_t("close", "--position", "XAUUSD-1", "--already", "--fill", "2420", now="2026-07-17 10:00", code=0)
        self.passed("2026-07-17 10:00")
        out = self.run_t("show", now="2026-07-17 10:01", code=1)[1]
        self.assertIn("NOT READY: There is no open position XAUUSD-1 to close.", out)


class TestRollStopLog(Base):
    def test_roll(self):
        self.gold_filled()
        self.orders(GOLD)
        self.passed("2026-07-29 13:14")
        out = self.run_t("show", now="2026-07-29 13:15", code=1)[1]
        self.assertIn("NOT READY: Gold rolled. Run fx-budget again.", out)
        self.assertIn("Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same amount. Your broker's "
                      "XAUUSD did not jump: keep the SL and TP you set in MetaTrader.", out)
        p = self.read("portfolio.json")["positions"][0]
        self.assertEqual((p["entry"], p["fill_price"], p["stop_loss"], p["take_profit"], p["stop_at_open"]),
                         (2455.39, 2455.39, 2421.59, 2512.41, 2421.59))
        self.assertEqual((p["broker_stop_loss"], p["broker_take_profit"]), (2395.75, 2485.75))
        self.assertAlmostEqual(p["rolls_applied"]["GC-2026-07-29"], 2500.3 / 2478.0, places=9)
        self.assertGreater(p["swap_nights"], 0)
        recs = [r for r in self.records() if r["status"] == "rebase"]
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["fingerprint"], recs[0]["roll_id"], recs[0]["price"]),
                         (None, "GC-2026-07-29", 2421.59))
        self.assertEqual(recs[0]["plain"], "Gold rolled on Yahoo (+0.90%). XAUUSD-1 moved: stop 2,421.59, "
                                           "target 2,512.41.")
        out = self.run_t("show", now="2026-07-29 13:16", code=1)[1]
        self.assertNotIn("rolled on Yahoo", out)
        self.assertEqual(len([r for r in self.records() if r["status"] == "rebase"]), 1)

    def test_roll_not_seen_before_found(self):
        self.gold_filled()
        self.run_t("log", now="2026-07-29 11:00", code=0)
        self.run_t("stop", "--position", "XAUUSD-1", "--to", "2410", now="2026-07-29 11:00", code=0)
        self.assertEqual(self.read("portfolio.json")["positions"][0]["take_profit"], 2490.0)

    def test_stop(self):
        self.gold_filled()
        out = self.run_t("stop", "--position", "XAUUSD-1", "--to", "2390", now="2026-07-17 10:00", code=1)[1]
        self.assertIn("Not moved: 2,390.00 is further from your profit side than 2,400.00. Use --wider if you "
                      "mean it.", out)
        out = self.run_t("stop", "--position", "XAUUSD-1", "--to", "2400", now="2026-07-17 10:00", code=1)[1]
        self.assertIn("Not moved: the stop is already at 2,400.00.", out)
        out = self.run_t("stop", "--position", "XAUUSD-1", "--to", "2410", now="2026-07-17 10:00", code=0)[1]
        self.assertIn("Stop of XAUUSD-1 moved from 2,400.00 to 2,410.00 (Yahoo plan scale). Max loss at the stop "
                      "now $77.81.", out)
        self.assertIn("In MetaTrader: Toolbox > Trade > double click the XAUUSD row > Stop Loss 2405.75 > Modify.", out)
        p = self.read("portfolio.json")["positions"][0]
        self.assertEqual((p["stop_loss"], p["broker_stop_loss"], p["stop_at_open"], p["max_loss_usd"]),
                         (2410.0, 2405.75, 2400.0, 77.81))
        rec = self.records()[-1]
        self.assertEqual((rec["status"], rec["fingerprint"], rec["price"], rec["broker_price"], rec["max_loss_usd"]),
                         ("stop_moved", None, 2410.0, 2405.75, None))
        out = self.run_t("stop", "--position", "XAUUSD-1", "--to", "2390", "--wider", now="2026-07-17 10:05", code=0)[1]
        self.assertIn("Note: this stop is further away. Your max loss at the stop grows from $77.81 to $117.81.",
                      flat(out))

    def test_stop_too_close(self):
        self.gold_filled()
        out = self.run_t("stop", "--position", "XAUUSD-1", "--to", "2440", now="2026-07-17 10:00", code=1)[1]
        self.assertIn("Your stop is on the wrong side of the price now.", out)
        self.assertEqual(self.read("portfolio.json")["positions"][0]["stop_loss"], 2400.0)

    def test_stop_mt4_and_spot(self):
        self.config(account={"platform": "mt4"})
        self.euro_filled()
        out = self.run_t("stop", "--position", "EURUSD-1", "--to", "1.0855", now="2026-07-23 12:50", code=0)[1]
        self.assertIn("In MetaTrader: Terminal > Trade > double click the EURUSD row > Stop Loss 1.08550 > Modify.", out)

    def test_log(self):
        out = self.run_t("log", now="2026-07-17 09:00", code=0)[1]
        self.assertIn("No records in the last 30 days.", out)
        self.gold_filled()
        out = self.run_t("log", now="2026-07-17 09:12", code=0)[1]
        self.assertIn("Fri 17 Jul 9:10 AM PKT  yes  You said yes to BUY 0.02 lots XAUUSD.", out)
        self.assertIn("Fri 17 Jul 9:11 AM PKT  filled  Paper fill: BUY 0.02 lots XAUUSD at 2,433.49.", out)
        self.assertIn("No records today.", self.run_t("log", "--today", now="2026-07-20 09:00", code=0)[1])
        self.assertIn("No records in the last 1 days.", self.run_t("log", "--days", "1", now="2026-07-20 09:00", code=0)[1])
        doc = json.loads(self.run_t("log", "--json", now="2026-07-17 09:12", code=0)[1])
        self.assertEqual([r["status"] for r in doc["records"]], ["yes", "filled"])
        err = self.run_t("log", "--days", "0", now="2026-07-17 09:12", code=2)[2]
        self.assertIn("--days must be a whole number from 1 to 3650", err)


class TestErrors(Base):
    def err(self, *args, now="2026-07-17 09:09"):
        code, out, err = self.run_t(*args, now=now)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        return err.strip()

    def test_no_orders_file(self):
        self.assertEqual(self.err("show"), "ticket.py: no orders at fx/orders.json. Run fx-budget first.")

    def test_empty_orders(self):
        self.write("orders.json", {"created_by": "fx-budget", "created_at": utc("2026-07-17 09:07"), "orders": []})
        self.assertEqual(self.err("show"), "ticket.py: fx/orders.json has no orders. Run fx-budget with --write first.")

    def test_no_order_n(self):
        self.orders(GOLD)
        self.assertEqual(self.err("approve", "--index", "3", "--reply", "yes"),
                         "ticket.py: There is no order 3. fx/orders.json has 1 order.")
        self.assertEqual(self.err("show", "--index", "2"), "ticket.py: There is no order 2. fx/orders.json has 1 order.")

    def test_reader_checks(self):
        self.orders(dict(GOLD, side="long"))
        self.assertEqual(self.err("show"), 'ticket.py: fx/orders.json: side must be "buy" or "sell", not "long"')
        self.orders(dict(GOLD, symbol="XAUSD"))
        self.assertIn('"XAUSD" is not in your instrument list', self.err("show"))
        self.orders(dict(GOLD, type="trailing"))
        self.assertEqual(self.err("show"), 'ticket.py: fx/orders.json: order 1: type must be "market", "stop" or '
                                           '"limit", not "trailing"')
        self.orders(dict(GOLD, lots=0.025))
        self.assertIn("order 1: lots 0.025 is not a whole number of lot_step 0.01", self.err("show"))
        self.orders(dict(GOLD, stop_loss=None))
        self.assertIn("order 1: it has no stop_loss", self.err("show"))
        self.orders(dict(GOLD, intent="close", position_id="XAUUSD-1"))
        self.assertIn("order 1 closes a position. Leave entry, stop_loss, take_profit and expiry empty", self.err("show"))
        self.orders(dict(GOLD, expiry="next week"))
        self.assertIn('"expiry" is not a UTC time like 2026-11-11T08:40:00Z', self.err("show"))
        with open(self.path("orders.json"), "w") as fh:
            fh.write("{broken")
        self.assertIn("fx/orders.json is not valid JSON", self.err("show"))

    def test_damaged_ledger(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        self.write("state/broker.json", {"records": [{"at": "2026-07-17T04:00:00Z", "status": "no"}, {"at": "x"}]})
        self.assertEqual(self.err("approve", "--index", "1", "--reply", "yes"),
                         "ticket.py: fx/state/broker.json is damaged at record 2. Nothing was changed.")
        self.assertEqual(self.err("log"), "ticket.py: fx/state/broker.json is damaged at record 2. Nothing was changed.")
        self.write("state/broker.json", {"records": {}})
        self.assertIn("is damaged at record 1", self.err("log"))

    def test_damaged_portfolio(self):
        self.orders(GOLD)
        self.write("portfolio.json", {"account": {"currency": "EUR"}, "positions": [], "closed": []})
        self.assertEqual(self.err("show"), 'ticket.py: fx/portfolio.json: "account.currency" must be "USD". Fix it '
                                           'by hand or restore a backup.')
        pos = {"id": "XAUUSD-1", "symbol": "XAUUSD"}
        self.write("portfolio.json", {"account": {"currency": "USD"}, "positions": [pos, pos], "closed": []})
        self.assertIn("two positions have the id XAUUSD-1", self.err("show"))

    def test_broker_price_needs_index(self):
        self.orders(GOLD, EURO)
        self.assertEqual(self.err("show", "--broker-price", "2429.15"),
                         "ticket.py: --broker-price is for one order. Add --index N.")

    def test_bad_numbers(self):
        self.orders(GOLD)
        self.gold_filled()
        self.assertEqual(self.err("result", "--index", "1", "--status", "filled", "--fill", "abc"),
                         "ticket.py: --fill must be a price above 0, not abc.")
        self.assertEqual(self.err("stop", "--position", "XAUUSD-1", "--to=0"),
                         "ticket.py: --to must be a price above 0, not 0.")
        self.assertEqual(self.err("show", "--broker-price", "x"), "ticket.py: --broker-price must be a price above 0, not x.")

    def test_bad_now_and_config(self):
        self.orders(GOLD)
        self.assertEqual(self.err("show", now="tomorrow"),
                         'ticket.py: --now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)')
        self.assertIn("no config file at fx/none.json", self.err("show", "--config", "fx/none.json"))

    def test_no_subcommand(self):
        p = subprocess.run([sys.executable, SCRIPT], cwd=self.dir, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("show", p.stdout)

    def test_bad_status(self):
        self.orders(GOLD)
        p = self.run_t("result", "--index", "1", "--status", "done", now="2026-07-17 09:09")
        self.assertEqual(p[0], 2)


class TestNoNetwork(Base):
    def test_no_network_code(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        for word in ("urllib", "urlopen", "http.client", "socket", "yahoo_get", "calendar_get", "def refresh(",
                     "check_roll_forward"):
            self.assertNotIn(word, src)
        for name in ("yahoo_get", "fetch_chart", "spark", "UA", "calendar_refresh", "load_calendar"):
            self.assertFalse(hasattr(T, name), name)

    def test_no_call_made(self):
        def boom(*a, **k):
            raise AssertionError("network used")
        saved = (socket.socket, socket.create_connection, fx_core.yahoo_get, fx_core.calendar_get)
        socket.socket = socket.create_connection = fx_core.yahoo_get = fx_core.calendar_get = boom
        here = os.getcwd()
        try:
            os.chdir(self.dir)
            self.orders(GOLD)
            self.passed("2026-07-17 09:08")
            runs = [["show", "--broker-price", "2429.15"], ["approve", "--index", "1", "--reply", "yes"],
                    ["result", "--index", "1", "--status", "filled"], ["stop", "--position", "XAUUSD-1", "--to", "2410"],
                    ["close", "--position", "XAUUSD-1"], ["flatten", "--all"], ["log"]]
            for args in runs:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    code = T.main(args + ["--now", "2026-07-17 09:10"])
                self.assertEqual(code, 0, (args, buf.getvalue()))
        finally:
            os.chdir(here)
            socket.socket, socket.create_connection, fx_core.yahoo_get, fx_core.calendar_get = saved


class TestHelpers(unittest.TestCase):
    """Unit tests of the own helpers (no files)."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = fx_core.load_config(os.path.join(MARKET, "config.json"), required=True)
        cls.gold = fx_core.instrument(cls.cfg, "XAUUSD")
        cls.euro = fx_core.instrument(cls.cfg, "EURUSD")

    def yes(self, reply, order=GOLD, **extra):
        x = dict(order, _digits=int(fx_core.instrument(self.cfg, order["symbol"])["digits"]), **extra)
        return T.parse_yes(reply, x, {"EURUSD"}, T.known_symbols(self.cfg), {"max_loss_usd": 97.81})

    def test_parse_yes_words(self):
        self.assertEqual(self.yes("yes"), (True, "explicit yes for BUY 0.02 lots XAUUSD"))
        self.assertTrue(self.yes("yes, buy XAUUSD, max loss $97.81")[0])
        self.assertTrue(self.yes("yes 2,433.49")[0])
        self.assertTrue(self.yes("yes 2395.75", _broker={"entry": 2429.24, "stop_loss": 2395.75, "take_profit": 2485.75})[0])
        self.assertEqual(self.yes("yes 2395.75")[1], "the reply says 2395.75, but this order is BUY 0.02 lots "
                                                     "XAUUSD, SL 2,400.00, TP 2,490.00")
        self.assertEqual(self.yes("yes GOLD")[1], "the reply names GOLD, but this order is for XAUUSD")
        self.assertEqual(self.yes("yes eurusd")[1], "the reply names EURUSD, but this order is for XAUUSD")
        self.assertEqual(self.yes("yes x2")[1], "the reply asks for a change (x2); a changed order needs a new "
                                               "fx-risk check and a new yes")
        self.assertEqual(self.yes("yes as soon as it dips")[1], "the reply has a condition or a doubt in it; ask again")
        self.assertEqual(self.yes("yes, go short")[1], "the reply says short, but this order is a buy")
        self.assertEqual(self.yes("yes, exit")[1], "the reply says exit, but this order opens a new trade")
        self.assertEqual(self.yes("yes, stop")[1], "the reply has a condition or a doubt in it; ask again")
        self.assertEqual(self.yes("yes, buy it")[0], True)
        self.assertEqual(self.yes("yes", EURO)[0], True)
        self.assertEqual(self.yes("yes, buy", EURO)[1], "the reply says buy, but this order is a sell")
        self.assertEqual(self.yes("sure"), (False, 'only a reply that starts with "yes" counts (not ok, sure, go, '
                                                   'do it, haan)'))

    def test_parse_yes_types(self):
        stop = dict(GOLD, type="stop", entry=2440.0)
        self.assertTrue(self.yes("yes, buy stop 2440", stop)[0])
        self.assertEqual(self.yes("yes, stop", stop)[1], "the reply has a condition or a doubt in it; ask again")
        self.assertEqual(self.yes("yes, market", stop)[1], "the reply says market, but this is a stop order")
        limit = dict(GOLD, type="limit", entry=2420.0)
        self.assertEqual(self.yes("yes at any price", limit)[1], 'the reply says "any price", but this is a limit order')
        self.assertTrue(self.yes("YES LIMIT", limit)[0])

    def test_parse_yes_close(self):
        close = dict(T.close_order({"id": "XAUUSD-1", "symbol": "XAUUSD", "side": "buy", "style": "swing",
                                    "lots": 0.02, "opened_at": "2026-07-17T04:11:00Z"}, "2026-07-30",
                                   None, self.cfg))
        self.assertEqual(self.yes("YES, CLOSE", close), (True, "explicit yes to close XAUUSD-1"))
        self.assertEqual(self.yes("yes, close", close, _position_lots=0.02)[0], True)
        self.assertEqual(self.yes("yes, sell", close)[1], "This order closes your BUY of XAUUSD. Reply yes or yes, close.")
        self.assertEqual(self.yes("yes, exit", close)[1], "a close needs only yes or yes, close")
        self.assertEqual(self.yes("yes 0.03", close)[1], "the reply says 0.03, but this close is for 0.02 lots")
        self.assertEqual(self.yes("yes, all", close)[1], "a yes covers one order at a time, not a batch")

    def test_mt_lines(self):
        pend = dict(EURO, type="stop", entry=1.08400)
        line = T.mt_line(pend, None, self.cfg, self.euro, "2026-07-23")
        self.assertEqual(line, "In MetaTrader: New order > Symbol EURUSD > Pending Order > Sell Stop > Volume 0.86 > "
                               "Price 1.08400 > Stop Loss 1.08561 > Take Profit 1.08234 > Expiration 2026-07-23 "
                               "21:30 > Place.")
        cfg4 = dict(self.cfg, account=dict(self.cfg["account"], platform="mt4"))
        line = T.mt_line(pend, None, cfg4, self.euro, "2026-07-23")
        self.assertIn("> Sell Stop > Volume 0.86 > At price 1.08400 > Stop Loss", line)
        self.assertTrue(line.endswith("> Expiry 2026.07.23 21:30 > Place."))
        self.assertEqual(T.mt_line(dict(GOLD, type="limit", entry=2420.0), None, self.cfg, self.gold, "2026-07-17"),
                         "In MetaTrader: New order > Symbol XAUUSD > Pending Order > Buy Limit > Volume 0.02 > "
                         "Price 2420.00 > Stop Loss 2400.00 > Take Profit 2490.00 > Place.")
        self.assertEqual(T.mt_line({"intent": "close"}, None, cfg4, self.gold),
                         "In MetaTrader: Terminal > Trade > right click the XAUUSD row > Close Order.")
        self.assertEqual(T.mt_price(2395.75, self.gold), "2395.75")
        self.assertEqual(T.mt_price(12345.5, self.gold), "12345.50")
        netting = dict(self.cfg, account=dict(self.cfg["account"], hedging=False))
        self.assertEqual(T.close_note(netting, "buy"), "Your account is netting: the Close button is still the safe way.")

    def test_expiry_words(self):
        now = fx_core.parse_now("2026-07-23 12:42")
        self.assertEqual(T.expiry_words(EURO, self.cfg, "2026-07-23", now),
                         "close by 11:30 PM PKT (2:30 PM New York) today (flat-by)")
        self.assertEqual(T.expiry_words(dict(EURO, type="stop"), self.cfg, "2026-07-23", now),
                         "cancel at 11:30 PM PKT (2:30 PM New York): 2026-07-23 21:30 server time")
        self.assertEqual(T.expiry_words(dict(EURO, expiry="today"), self.cfg, "2026-07-23", now),
                         "until the FX day ends, 2:00 AM PKT (5:00 PM New York)")
        self.assertEqual(T.expiry_server(dict(EURO, expiry="today"), "2026-07-23", self.cfg), "2026-07-24 00:00")

    def test_small_helpers(self):
        pos = {"side": "buy", "stop_loss": 2400.0, "take_profit": 2490.0, "flat_by_utc": None,
               "exit_by": "2026-08-13T21:00:00Z"}
        now = fx_core.parse_now("2026-07-20 10:00")
        self.assertEqual(T.close_why(pos, 2490.0, now, self.gold), "target")
        self.assertEqual(T.close_why(pos, 2400.0, now, self.gold), "stop")
        self.assertEqual(T.close_why(pos, 2395.1, now, self.gold), "stop")
        self.assertEqual(T.close_why(pos, 2450.0, now, self.gold), "manual")
        self.assertEqual(T.close_why(pos, 2450.0, fx_core.parse_now("2026-08-14 01:30"), self.gold), "exit_by")
        day = dict(pos, side="sell", stop_loss=1.08561, take_profit=1.08234, exit_by=None,
                   flat_by_utc="2026-07-23T18:30:00Z")
        self.assertEqual(T.close_why(day, 1.0840, fx_core.parse_now("2026-07-23 23:20"), self.euro), "flat_by")
        self.assertEqual(T.close_why(day, 1.0860, fx_core.parse_now("2026-07-23 23:20"), self.euro), "stop")
        pf = {"positions": [{"id": "XAUUSD-2"}], "closed": [{"id": "XAUUSD-1"}, {"id": "EURUSD-7"}]}
        self.assertEqual(T.next_position_id("XAUUSD", pf, {"records": [{"position_id": "XAUUSD-4"}]}), "XAUUSD-5")
        self.assertEqual(T.next_position_id("EURUSD", None, {"records": []}), "EURUSD-1")
        self.assertEqual(T.roll_pct(2500.3 / 2478.0), "+0.90%")
        self.assertEqual(T.roll_pct(0.99), "-1.00%")
        self.assertEqual(T.ORDER_FIELDS, fx_core.ORDER_FIELDS)
        self.assertEqual(T.FRESH_CHECK_MINUTES, 5)
        rec = T.new_record("no", EURO, now, "2026-07-20", self.cfg)
        self.assertEqual(sorted(rec), sorted(T.RECORD_KEYS))


class TestChainRows(Base):
    """The 17 fx-broker rows of test_chain (06 6.2), with hand-made fx-budget and fx-risk files."""

    def step(self, now, *args, code=0, has=(), hasnt=()):
        import time
        start = time.time()
        got, out, err = self.run_t(*args, now=now)
        self.assertLess(time.time() - start, 60.0, args)
        self.assertEqual(got, code, "%s %s\n%s%s" % (now, args, out, err))
        for text in has:
            self.assertIn(text, out, (now, args))
        for text in hasnt:
            self.assertNotIn(text, out, (now, args))
        return out

    def test_chain_rows(self):
        self.orders(GOLD)
        self.passed("2026-07-17 09:08")
        self.step("2026-07-17 09:09", "show", has=("READY",), hasnt=("In MetaTrader:",))
        self.step("2026-07-17 09:10", "approve", "--index", "1", "--reply", "maybe", code=1, hasnt=("In MetaTrader:",))
        self.step("2026-07-17 09:10", "approve", "--index", "1", "--reply", "yes, sell it", code=1,
                  hasnt=("In MetaTrader:",))
        self.step("2026-07-17 09:10", "approve", "--index", "1", "--reply", "yes", "--broker-price", "2429.15",
                  has=("In MetaTrader:", "Buy by Market", "2,395.75", "2,485.75", "your broker's price"))
        self.step("2026-07-17 09:11", "result", "--index", "1", "--status", "filled")
        p = self.read("portfolio.json")["positions"][0]
        self.assertEqual((p["id"], p["lots"], p["paper"], p["fill_price"]), ("XAUUSD-1", 0.02, True, 2433.49))
        self.orders(EURO, created="2026-07-23 12:43")
        self.passed("2026-07-23 12:43")
        self.step("2026-07-23 12:44", "approve", "--index", "1", "--reply", "yes",
                  has=("Sell by Market", "1.08561", "1.08234"))
        self.step("2026-07-23 12:45", "result", "--index", "1", "--status", "filled")
        self.assertEqual([x["id"] for x in self.read("portfolio.json")["positions"]], ["XAUUSD-1", "EURUSD-1"])
        self.step("2026-07-23 15:21", "close", "--position", "EURUSD-1")
        o = self.read("orders.json")["orders"]
        self.assertEqual((len(o), o[0]["intent"], o[0]["side"], o[0]["type"], o[0]["decided_by"]),
                         (1, "close", "sell", "market", "close"))
        self.passed("2026-07-23 15:21")
        self.step("2026-07-23 15:22", "approve", "--index", "1", "--reply", "yes", has=("Close",))
        self.step("2026-07-23 15:22", "result", "--index", "1", "--status", "filled", "--fill", "1.08234")
        c = self.read("portfolio.json")["closed"][0]
        self.assertEqual((c["symbol"], c["why"], c["result_usd"]), ("EURUSD", "target", 182.32))
        self.step("2026-07-30 19:46", "close", "--position", "XAUUSD-1", has=("Gold rolled on Yahoo (+0.90%)",))
        o = self.read("orders.json")["orders"][0]
        self.assertEqual((o["intent"], o["side"]), ("close", "buy"))
        self.passed("2026-07-30 19:46")
        self.step("2026-07-30 19:47", "approve", "--index", "1", "--reply", "yes, sell it", code=1,
                  has=("This order closes your BUY of XAUUSD. Reply yes or yes, close.",), hasnt=("In MetaTrader:",))
        self.step("2026-07-30 19:47", "approve", "--index", "1", "--reply", "yes", has=("Close",))
        self.step("2026-07-30 19:47", "result", "--index", "1", "--status", "filled", "--fill", "2512.41",
                  has=("(target)", "Result +$99.66 after all costs.", "Balance now $10,281.98 (paper)."))
        pf = self.read("portfolio.json")
        self.assertEqual(pf["positions"], [])
        self.assertEqual([x["id"] for x in pf["closed"]], ["EURUSD-1", "XAUUSD-1"])
        gold = pf["closed"][1]
        self.assertEqual((gold["entry"], gold["exit_price"], gold["why"], gold["result_usd"]),
                         (2455.39, 2512.41, "target", 99.66))
        before_costs = round((2512.41 - 2455.39) * 0.02 * 100, 2)
        self.assertEqual(before_costs, 114.04)
        self.assertEqual(round(gold["result_usd"] + gold["costs_usd"], 2), before_costs)
        out = self.step("2026-07-30 19:48", "log")
        for sym in ("XAUUSD", "EURUSD"):
            for status in ("yes", "filled", "closed"):
                self.assertTrue(any(("  %s  " % status) in line and sym in line for line in out.splitlines()),
                                (sym, status))


class TestSkillFile(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "fx-broker", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: fx-broker\n"))
        self.assertIn('argument-hint: "[order number]"', text)
        heads = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(heads[-2:], ["## What it never does", "## Safety"])
        safety = text[text.index("## Safety"):]
        low = " ".join(safety.lower().split())
        for word in ("not financial advice", "yes", "nothing in this pack places an order", "74% to 89%", "paper"):
            self.assertIn(word, low)
        for path in ("fx-broker/SKILL.md", "fx-broker/ticket.py", "tests/test_broker.py"):
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                self.assertNotIn(chr(0x2014), fh.read(), path)
