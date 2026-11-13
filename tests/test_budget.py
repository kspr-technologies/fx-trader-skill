"""Tests for fx-budget/size.py. Offline, on the made-up sample market (tests/fixtures/fx-market).

The plans come from the real fx-entry and fx-exit tools when they work, as test_chain
does. When they do not (yet), the tests use hand-made plans with the same numbers as
the fx-exit spec (section 5 of fx-exit.md). Either way, every run is offline with --now.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _load  # noqa: E402
import fx_core  # noqa: E402

ROOT = _load.ROOT
MARKET = os.path.join(_load.FIX, "fx-market")
SIZE = os.path.join(ROOT, "fx-budget", "size.py")
GOLD_T = "2026-07-17 09:07"
EUR_T = "2026-07-23 12:43"

GOLD_PLAN = {
    "created_by": "fx-exit", "created_at": "2026-07-17T04:05:00Z",
    "symbol": "XAUUSD", "side": "buy", "style": "swing", "setup": "breakout_20d",
    "fx_day": "2026-07-17", "chart_entry": 2433.4, "chart_stop": 2400.0, "entry": 2433.49,
    "entry_type": "market", "entry_at": "2026-07-16T21:00:00Z",
    "entry_rule": "buy at the market (ask = bid 2,433.40 + spread 0.09)",
    "stop_loss": 2400.0, "stop_rule": "price", "stop_label": "your price (2,400.00)",
    "take_profit": 2490.0, "target_rule": "price", "target_r": 1.69,
    "target_label": "your price (2,490.00)", "risk_per_unit": 33.49, "risk_points": 3349,
    "reward_risk": 1.69, "reward_risk_net": 0.84, "spread_points": 9, "atr": 28.0,
    "atr_interval": "1d", "opening_range": None, "flat_by": None, "flat_by_utc": None,
    "max_hold_days": 20, "exit_by": "2026-08-13T21:00:00Z", "usd_per_point_per_lot": 1.0,
    "costs": {"commission_round_trip": 6.0, "spread": 9.0, "swap_per_night": -54.84,
              "nights_est": 28, "swap_est": -1535.52, "swap_free": False, "fee_est": 0.0},
    "gap": {"note": "If the price jumps past your stop, you lose more. A jump of 2 ATR (56.00) "
                    "past the stop costs $5,600.00 more a lot.", "loss_per_lot_usd": 5600.0},
    "rolls_applied": {}, "data_age_min": 0.0,
    "warnings": ["After costs this trade wins 0.84 times what it risks. Your minimum is 1.5."]}

EUR_PLAN = {
    "created_by": "fx-exit", "created_at": "2026-07-23T07:42:00Z",
    "symbol": "EURUSD", "side": "sell", "style": "day", "setup": "or_breakdown",
    "fx_day": "2026-07-23", "chart_entry": 1.08452, "chart_stop": 1.0856, "entry": 1.08452,
    "entry_type": "market", "entry_at": "2026-07-23T07:40:00Z",
    "entry_rule": "sell at the market (bid 1.08452)", "stop_loss": 1.08561, "stop_rule": "or-mid",
    "stop_label": "opening-range middle 1.08560 + spread", "take_profit": 1.08234,
    "target_rule": "2R", "target_r": 2.0, "target_label": "2R (two times the risk)",
    "risk_per_unit": 0.00109, "risk_points": 109, "reward_risk": 2.0, "reward_risk_net": 1.84,
    "spread_points": 1, "atr": 0.0002, "atr_interval": "5m",
    "opening_range": {"session": "london", "start": "2026-07-23T07:00:00Z",
                      "end": "2026-07-23T07:15:00Z", "high": 1.0864, "low": 1.0848,
                      "mid": 1.0856, "bars": 3, "of": 3},
    "flat_by": "23:30", "flat_by_utc": "2026-07-23T18:30:00Z", "max_hold_days": None,
    "exit_by": None, "usd_per_point_per_lot": 1.0,
    "costs": {"commission_round_trip": 6.0, "spread": 1.0, "swap_per_night": 3.1,
              "nights_est": 0, "swap_est": 0.0, "swap_free": False, "fee_est": 0.0},
    "gap": {"note": "If the price jumps past your stop, you lose more. A jump of 3 ATR (0.00060) "
                    "past the stop costs $60.00 more a lot.", "loss_per_lot_usd": 60.0},
    "rolls_applied": {}, "data_age_min": 2.0, "warnings": []}


def flat(text):
    """Squeeze the white space, so a wrapped line still matches."""
    return " ".join(text.split())


def run_py(cwd, script, args, timeout=300):
    """Run python3 script args in cwd; (code, stdout, stderr)."""
    p = subprocess.run([sys.executable, script] + list(args), cwd=cwd, timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def save(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)


def real_plan(base, steps):
    """Run the real fx-entry and fx-exit steps in base; the plan they wrote, or None."""
    plan_path = os.path.join(base, "fx", "plan.json")
    if os.path.exists(plan_path):
        os.remove(plan_path)
    for script, args, t in steps:
        path = os.path.join(ROOT, script)
        if not os.path.exists(path):
            return None
        try:
            code, _out, _err = run_py(base, path, args + ["--offline", "--now", t], timeout=120)
        except subprocess.TimeoutExpired:
            return None
        if code != 0:
            return None
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError):
        return None
    return plan if isinstance(plan, dict) and plan.get("created_by") == "fx-exit" else None


class Budget(unittest.TestCase):
    """Each test gets its own fx/ folder; fx/data points at one shared copy (size.py
    never writes the data folder offline)."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="fx-budget-")
        os.makedirs(os.path.join(cls.base, "fx"))
        cls.data = os.path.join(cls.base, "fx", "data")
        shutil.copytree(os.path.join(MARKET, "data"), cls.data)
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(cls.base, "fx", "config.json"))
        gold = real_plan(cls.base, [
            ("fx-scanner/scan.py", ["swing"], "2026-07-17 09:00"),
            ("fx-entry/entry.py", ["check", "XAUUSD", "--write"], "2026-07-17 09:02"),
            ("fx-exit/exit.py", ["plan", "--stop", "price:2400", "--target", "price:2490", "--write"],
             "2026-07-17 09:05")])
        eur = real_plan(cls.base, [
            ("fx-scanner/scan.py", ["day"], "2026-07-23 12:41"),
            ("fx-entry/entry.py", ["check", "EURUSD", "--write"], "2026-07-23 12:41"),
            ("fx-exit/exit.py", ["plan", "--write"], "2026-07-23 12:42")])
        cls.real = (gold is not None, eur is not None)
        cls.gold = gold or copy.deepcopy(GOLD_PLAN)
        cls.eur = eur or copy.deepcopy(EUR_PLAN)
        with open(os.path.join(MARKET, "config.json"), encoding="utf-8") as fh:
            cls.cfg = json.load(fh)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def folder(self, cfg_change=None, data=True):
        """A new project folder: fx/config.json, gold-plan.json and eur-plan.json in fx/."""
        Budget.n += 1
        d = os.path.join(self.base, "t%d" % Budget.n)
        os.makedirs(os.path.join(d, "fx"))
        if data:
            try:
                os.symlink(self.data, os.path.join(d, "fx", "data"))
            except OSError:
                shutil.copytree(self.data, os.path.join(d, "fx", "data"))
        cfg = copy.deepcopy(self.cfg)
        if cfg_change:
            cfg_change(cfg)
        save(os.path.join(d, "fx", "config.json"), cfg)
        save(os.path.join(d, "gold-plan.json"), self.gold)
        save(os.path.join(d, "eur-plan.json"), self.eur)
        return d

    def size(self, d, args, t):
        return run_py(d, SIZE, list(args) + ["--offline", "--now", t])

    def orders(self, d):
        with open(os.path.join(d, "fx", "orders.json"), encoding="utf-8") as fh:
            return json.load(fh)

    # ---- the sized orders

    def test_gold_swing_buy(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--write"], GOLD_T)
        self.assertEqual(code, 0, err)
        for text in ("SIZED: 0.02 lots of XAUUSD (BUY, swing).", "0.02 lots", "$97.81", "$66.98",
                     "$0.12", "$30.71", "<- decides", "$4,890.52",
                     "Entry 2,433.49 = Yahoo bid 2,433.40 + spread 0.09.",
                     "gold 2,433.40 (Yahoo price (gold futures), not your broker's price, 2 min old)",
                     "Stop 2,400.00: 33.49 (3,349 points) under the entry.",
                     "Target 2,490.00: 56.51 (5,651 points) over the entry. Reward:risk 1.69.",
                     "Exit by Fri 2:00 AM PKT (5:00 PM New York), 2026-08-14.",
                     "Margin: $48.67 (0.5% of your balance).", "Good till cancelled.",
                     "Wrote 1 order to fx/orders.json.", "Next: python3 fx-risk/check.py",
                     "After costs this trade wins 0.84 times what it risks.",
                     "Costs are about 46% of the price loss"):
            self.assertIn(text, flat(out))
        self.assertEqual(flat(out).count("After costs this trade wins"), 1)
        doc = self.orders(d)
        self.assertEqual(doc["created_by"], "fx-budget")
        self.assertEqual(doc["created_at"], "2026-07-17T04:07:00Z")
        self.assertEqual(len(doc["orders"]), 1)
        o = doc["orders"][0]
        self.assertEqual((o["lots"], o["type"], o["intent"], o["expiry"]), (0.02, "market", "open", "gtc"))
        self.assertEqual((o["stop_loss"], o["take_profit"], o["entry"]), (2400.0, 2490.0, 2433.49))
        self.assertEqual((o["side"], o["style"], o["broker_symbol"], o["position_id"]),
                         ("buy", "swing", "XAUUSD", None))
        self.assertEqual(o["rolls_applied"], {})
        self.assertEqual(o["decided_by"], "risk_pct")
        self.assertEqual(o["why"], "1.0% of $10,000.00 is $100.00. 0.02 lots lose $97.81 at the "
                                   "stop, costs in.")
        p = o["plan"]
        self.assertEqual((p["nights_est"], p["margin_usd"], p["max_loss_usd"], p["costs_usd"]),
                         (28, 48.67, 97.81, 30.83))
        self.assertEqual((p["risk_per_unit"], p["usd_per_point_per_lot"]), (33.49, 1.0))
        self.assertEqual(p["exit_by"], "2026-08-13T21:00:00Z")
        self.assertEqual(p["setup"], "breakout_20d")
        self.assertEqual(list(o)[:10], list(fx_core.ORDER_FIELDS))

    def test_gap_line_is_measured(self):
        # (m) the gap money for 0.02 lots: plan gap.loss_per_lot_usd x 0.02
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)
        self.assertEqual(code, 0, err)
        per_lot = self.gold["gap"]["loss_per_lot_usd"]
        want = fx_core.money(round(per_lot * 0.02 + 1e-9, 2))
        self.assertIn("For 0.02 lots that is %s more." % want, flat(out))

    def test_measured_values_with_the_real_plan(self):
        # (m) measured on the first green build with the real fx-exit and fx-timemachine
        if not self.real[0]:
            self.skipTest("fx-exit did not make the gold plan here")
        d = self.folder()
        self.tm_file(d)
        code, out, err = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("For 0.02 lots that is $112.22 more.", flat(out))
        if os.path.exists(os.path.join(ROOT, "fx-timemachine", "timemachine.py")):
            self.assertIn("in 10,000 replays the target came first 11%, the stop 73%, neither 17%.",
                          flat(out))

    def test_one_more_step_line(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("one more step (0.03 lots) would lose $146.72: over your $100.00", flat(out))
        self.assertIn("Not saved. Add --write to save it to fx/orders.json.", out)
        self.assertFalse(os.path.exists(os.path.join(d, "fx", "orders.json")))

    def test_risk_usd_cap(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--risk-usd", "50", "--write"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("SIZED: 0.01 lots of XAUUSD", out)
        self.assertIn("$48.91", out)
        self.assertIn("your cap $50.00, costs in", out)
        o = self.orders(d)["orders"][0]
        self.assertEqual((o["lots"], o["decided_by"]), (0.01, "risk_usd"))
        self.assertEqual(o["why"], "Your cap is $50.00 a trade. 0.01 lots lose $48.91 at the stop, costs in.")

    def test_eurusd_day_sell(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "eur-plan.json", "--write"], EUR_T)
        self.assertEqual(code, 0, err)
        for text in ("SIZED: 0.86 lots of EURUSD (SELL, day).", "0.86 lots", "$98.90", "0.87 lots",
                     "$100.05", "11:30 PM PKT", "2:30 PM New York", "Entry 1.08452 = the Yahoo bid.",
                     "EURUSD 1.08452 (Yahoo price, close to your broker's price, 3 min old)",
                     "Stop 1.08561: 10.9 pips (109 points) over the entry.",
                     "Target 1.08234: 21.8 pips (218 points) under the entry. Reward:risk 2.00.",
                     "price and spread 0.86 lots x 0.00109 x 100,000 = $93.74",
                     "commission $5.16 (both sides)", "Value: $0.86 a point, $8.60 a pip.",
                     "Ends at 11:30 PM PKT (2:30 PM New York)."):
            self.assertIn(text, flat(out))
        self.assertNotIn("swap ", out.lower().replace("swap-free", ""))
        o = self.orders(d)["orders"][0]
        self.assertEqual((o["side"], o["lots"], o["type"], o["entry"]), ("sell", 0.86, "market", 1.08452))
        self.assertEqual((o["stop_loss"], o["take_profit"]), (1.08561, 1.08234))
        self.assertEqual((o["expiry"], o["style"]), ("2026-07-23T18:30:00Z", "day"))
        p = o["plan"]
        self.assertEqual((p["nights_est"], p["max_loss_usd"], p["costs_usd"], p["margin_usd"]),
                         (0, 98.9, 5.16, 932.69))

    def test_margin_cap_decides(self):
        def tight(cfg):
            cfg["risk"]["max_margin_used_pct"] = 1
        d = self.folder(tight)
        code, out, err = self.size(d, ["--plan", "eur-plan.json", "--write"], EUR_T)
        self.assertEqual(code, 0, err)
        o = self.orders(d)["orders"][0]
        self.assertEqual((o["lots"], o["decided_by"]), (0.09, "margin_cap"))
        self.assertEqual(o["why"], "Your margin limit is 1% of $10,000.00. 0.09 lots need $97.61 margin.")
        self.assertNotIn("one more step", out)

    # ---- refusals (exit 1)

    def refused(self, d, args, t, *texts):
        code, out, err = self.size(d, args, t)
        self.assertEqual(code, 1, out + err)
        self.assertTrue(out.startswith("NO ORDER for "), out)
        for text in texts:
            self.assertIn(text, flat(out))
        self.assertIn("Nothing was written.", out)
        self.assertFalse(os.path.exists(os.path.join(d, "fx", "orders.json")))
        return out

    def test_min_lot_trap_on_1000(self):
        def small(cfg):
            cfg["account"]["balance"] = 1000
        d = self.folder(small)
        out = self.refused(d, ["--plan", "gold-plan.json", "--write"], GOLD_T,
                           "NO ORDER for XAUUSD (BUY, swing).", "$48.91", "$10.00", "$4,890.52",
                           "Do not move the stop closer to make it fit.",
                           "0.01 lot is the smallest order for XAUUSD.",
                           "At 1.0% a trade you need about $4,890.52 for this trade.")
        self.assertNotIn("SIZED", out)

    def test_old_orders_file_stays(self):
        def small(cfg):
            cfg["account"]["balance"] = 1000
        d = self.folder(small)
        path = os.path.join(d, "fx", "orders.json")
        save(path, {"created_by": "fx-budget", "orders": []})
        with open(path, "rb") as fh:
            before = fh.read()
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--write"], GOLD_T)
        self.assertEqual(code, 1)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_margin_too_small(self):
        def tiny(cfg):
            cfg["account"]["balance"] = 1000
            cfg["risk"]["max_margin_used_pct"] = 1
        d = self.folder(tiny)
        self.refused(d, ["--plan", "eur-plan.json"], EUR_T,
                     "0.01 lot of EURUSD needs $10.85 margin. You have room for $10.00 (1% of "
                     "your balance, minus margin in use). So this trade does not fit.")
        code, out, err = self.size(d, ["--plan", "eur-plan.json", "--json"], EUR_T)
        doc = json.loads(out)
        self.assertEqual((code, doc["decided_by"], doc["refused"]["min_lot_loss_usd"]),
                         (1, "margin_cap", 1.15))

    def test_day_plan_too_late(self):
        d = self.folder()
        self.refused(d, ["--plan", "eur-plan.json"], "2026-07-23 23:20",
                     "Less than 15 minutes to your flat-by time (11:30 PM PKT (2:30 PM New York)).")

    def test_swing_plan_last_day_passed(self):
        d = self.folder()
        plan = dict(self.gold, created_at="2026-08-14T08:00:00Z", fx_day="2026-08-14")
        save(os.path.join(d, "late.json"), plan)
        self.refused(d, ["--plan", "late.json"], "2026-08-14 13:00",
                     "This plan's last day has passed. Make a new plan: /fx-exit.")

    def test_plan_of_another_day(self):
        d = self.folder()
        self.refused(d, ["--plan", "eur-plan.json"], "2026-07-24 10:00",
                     "This plan was made on Thu 2026-07-23. Make a new plan today: /fx-exit.")

    def test_sells_off(self):
        def no_short(cfg):
            cfg["allow_short"] = False
        d = self.folder(no_short)
        self.refused(d, ["--plan", "eur-plan.json"], EUR_T,
                     "NO ORDER for EURUSD (SELL, day).",
                     "Sells are off in your config (allow_short is false).")

    def test_blocked(self):
        def block(cfg):
            cfg["risk"]["blocked"] = ["XAUUSD"]
        d = self.folder(block)
        self.refused(d, ["--plan", "gold-plan.json"], GOLD_T, "XAUUSD is on your blocked list.")

    def test_price_already_at_the_stop(self):
        d = self.folder()
        plan = dict(self.gold, stop_loss=2433.4, risk_points=9, entry=2433.49)
        save(os.path.join(d, "p.json"), plan)
        self.refused(d, ["--plan", "p.json"], GOLD_T,
                     "The price is already at the stop (bid 2,433.40, stop 2,433.40). Make a new "
                     "plan: /fx-exit.")

    def test_price_already_at_the_target(self):
        d = self.folder()
        plan = dict(self.gold, take_profit=2433.4)
        plan["entry"], plan["entry_type"] = 2433.0, "limit"
        plan["risk_points"] = 3300
        save(os.path.join(d, "p.json"), plan)
        self.refused(d, ["--plan", "p.json"], GOLD_T,
                     "The price is already at the target. The move it planned for has happened.")

    def test_reward_risk_under_the_minimum(self):
        d = self.folder()
        plan = dict(self.gold, take_profit=2443.0)
        save(os.path.join(d, "p.json"), plan)
        self.refused(d, ["--plan", "p.json"], GOLD_T,
                     "Reward:risk 0.28 (target 2,443.00, entry 2,433.49, stop 2,400.00) is under "
                     "your minimum 1.50, so fx-risk would block it. Skip it, or make a new plan "
                     "with /fx-exit.")

    # ---- errors (exit 2, one line on stderr)

    def error(self, d, args, t, *texts):
        code, out, err = self.size(d, args, t)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(err.startswith("size.py: "), err)
        for text in texts:
            self.assertIn(text, err)
        return err

    def test_flag_cannot_raise_limit(self):
        d = self.folder()
        self.error(d, ["--plan", "gold-plan.json", "--risk-pct", "2"], GOLD_T,
                   "size.py: Your config allows at most 1.0% a trade. To go higher, change "
                   "risk.max_risk_per_trade_pct in fx/config.json.")

    def test_flag_cannot_raise_usd_cap(self):
        def cap(cfg):
            cfg["risk"]["max_risk_per_trade_usd"] = 50
        d = self.folder(cap)
        self.error(d, ["--plan", "gold-plan.json", "--risk-usd", "60"], GOLD_T,
                   "size.py: Your config allows at most $50.00 a trade. To go higher, change "
                   "risk.max_risk_per_trade_usd in fx/config.json.")

    def test_flag_at_or_below_zero(self):
        d = self.folder()
        self.error(d, ["--plan", "gold-plan.json", "--risk-pct", "0"], GOLD_T,
                   "size.py: --risk-pct must be a number above 0.")
        self.error(d, ["--plan", "gold-plan.json", "--risk-usd", "-5"], GOLD_T,
                   "size.py: --risk-usd must be a number above 0.")

    def test_no_plan(self):
        d = self.folder()
        self.error(d, [], GOLD_T, "size.py: no plan at fx/plan.json. Run fx-exit first.")

    def test_broken_plan(self):
        d = self.folder()
        with open(os.path.join(d, "fx", "plan.json"), "w") as fh:
            fh.write("{broken")
        self.error(d, [], GOLD_T, "size.py: fx/plan.json is not valid JSON:")
        save(os.path.join(d, "fx", "plan.json"), [1, 2])
        self.error(d, [], GOLD_T, "size.py: fx/plan.json must be a JSON object: {...}")

    def test_plan_checks(self):
        d = self.folder()
        cases = [
            (dict(self.gold, stop_loss=2500.0),
             "fx/plan.json: the stop or the target is on the wrong side of the entry. Run fx-exit again."),
            (dict(self.gold, risk_points=3000), "fx/plan.json does not add up (risk_points). Run fx-exit again."),
            (dict(self.gold, exit_by=None), 'fx/plan.json: a swing plan needs "exit_by"'),
            (dict(self.eur, flat_by_utc=None), 'fx/plan.json: a day plan needs "flat_by_utc"'),
            (dict(self.gold, side="long"), 'fx/plan.json: side must be "buy" or "sell", not "long"'),
            (dict(self.gold, style="scalp"), 'fx/plan.json: style must be "swing" or "day", not "scalp"'),
            (dict(self.gold, symbol="XAUSD"), '"XAUSD" is not in your instrument list'),
            (dict(self.gold, created_at="today"), '"created_at" is not a UTC time like 2026-11-11T08:40:00Z'),
            (dict(self.gold, entry=-1), '"entry" must be a price above 0, not -1'),
            (dict(self.gold, entry_type="fok"), 'entry_type must be "market", "stop" or "limit"'),
        ]
        for plan, text in cases:
            save(os.path.join(d, "fx", "plan.json"), plan)
            self.error(d, [], GOLD_T, text)

    def test_config_file_missing(self):
        d = self.folder()
        self.error(d, ["--config", "fx/nothing.json"], GOLD_T,
                   "size.py: no config file at fx/nothing.json (copy config.example.json from the "
                   "fx-budget folder)")

    def test_bad_config(self):
        def bad(cfg):
            cfg["risk"]["max_margin_used_pct"] = 0.01
        d = self.folder(bad)
        self.error(d, ["--plan", "gold-plan.json"], GOLD_T, '"risk.max_margin_used_pct" must be')

    def test_bad_now(self):
        d = self.folder()
        self.error(d, ["--plan", "gold-plan.json"], "yesterday",
                   '--now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)')

    def test_no_prices_offline(self):
        d = self.folder(data=False)
        os.makedirs(os.path.join(d, "fx", "data"))
        self.error(d, ["--plan", "gold-plan.json"], GOLD_T,
                   "size.py: No hourly prices for XAUUSD yet (fx/data/yahoo/GC_F/1h.json). Run "
                   "without --offline once to get them.")

    def test_bad_portfolio(self):
        d = self.folder()
        save(os.path.join(d, "fx", "portfolio.json"),
             {"account": {"currency": "EUR", "balance": 10000}, "positions": []})
        self.error(d, ["--plan", "gold-plan.json"], GOLD_T,
                   "fx/portfolio.json: account.currency must be USD. Fix it by hand or restore a backup.")
        pos = {"id": "X-1", "symbol": "XAUUSD", "side": "buy", "lots": 0.01, "entry": 2400.0}
        save(os.path.join(d, "fx", "portfolio.json"),
             {"account": {"currency": "USD", "balance": 10000}, "positions": [pos, dict(pos)]})
        self.error(d, ["--plan", "gold-plan.json"], GOLD_T, 'two positions have the id "X-1"')

    def test_save_fails(self):
        d = self.folder()
        os.makedirs(os.path.join(d, "fx", "orders.json"))
        self.error(d, ["--plan", "gold-plan.json", "--write"], GOLD_T, "could not save fx/orders.json")

    def test_no_usd_rate(self):
        d = self.folder(data=False)
        shutil.copytree(self.data, os.path.join(d, "fx", "data"))
        shutil.rmtree(os.path.join(d, "fx", "data", "yahoo", "GBPUSD_X"))   # no GBP rate
        cfg = fx_core.load_config(os.path.join(d, "fx", "config.json"))
        inst = fx_core.instrument(cfg, "EURGBP")
        bid = fx_core.latest_price(os.path.join(d, "fx", "data"), inst, fx_core.parse_now(EUR_T), cfg)["bid"]
        stop, target = round(bid + 0.001, 5), round(bid - 0.003, 5)
        plan = dict(self.eur, symbol="EURGBP", entry=bid, chart_entry=bid, stop_loss=stop,
                    take_profit=target, risk_points=fx_core.to_points(stop - bid, inst))
        save(os.path.join(d, "fx", "plan.json"), plan)
        self.error(d, [], EUR_T, "No USD rate for GBP. It comes from GBPUSD.")

    # ---- JSON, the time machine line, rolls, help

    def test_json_shape(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--json", "--write"], GOLD_T)
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(set(doc), set("v tool cmd now ok notes order decided_by limits written".split()))
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["now"], doc["ok"]),
                         (1, "fx-budget", None, "2026-07-17T04:07:00Z", True))
        self.assertEqual(doc["limits"], {"risk_usd": 100.0, "margin_free_usd": 10000.0, "max_lot": 100.0})
        self.assertEqual(doc["decided_by"], "risk_pct")
        self.assertTrue(doc["written"])
        self.assertEqual(doc["order"], self.orders(d)["orders"][0])
        self.assertTrue(any(n.startswith("After costs this trade wins 0.84") for n in doc["notes"]))
        self.assertFalse(any(n.startswith("Note: ") for n in doc["notes"]))

    def test_json_without_write(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--json"], GOLD_T)
        doc = json.loads(out)
        self.assertEqual((code, doc["written"], doc["order"]["lots"]), (0, False, 0.02))
        self.assertFalse(os.path.exists(os.path.join(d, "fx", "orders.json")))

    def test_json_refused(self):
        def small(cfg):
            cfg["account"]["balance"] = 1000
        d = self.folder(small)
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--json", "--write"], GOLD_T)
        self.assertEqual(code, 1, err)
        doc = json.loads(out)
        self.assertFalse(doc["ok"])
        self.assertIsNone(doc["order"])
        self.assertFalse(doc["written"])
        self.assertEqual(doc["decided_by"], "risk_pct")
        self.assertEqual(doc["refused"]["min_lot_loss_usd"], 48.91)
        self.assertIn("Do not move the stop closer", doc["refused"]["reason"])
        self.assertNotIn("\n", doc["refused"]["reason"])
        self.assertEqual(doc["limits"]["risk_usd"], 10.0)
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--json"], "2026-07-20 10:00Z")
        doc = json.loads(out)
        self.assertEqual((code, doc["decided_by"], doc["refused"]["min_lot_loss_usd"]), (1, None, None))

    def tm_file(self, d):
        """fx/timemachine.json from the real tool, or a made-up one for the gold plan."""
        shutil.copy(os.path.join(d, "gold-plan.json"), os.path.join(d, "fx", "plan.json"))
        path = os.path.join(d, "fx", "timemachine.json")
        script = os.path.join(ROOT, "fx-timemachine", "timemachine.py")
        if os.path.exists(script):
            try:
                run_py(d, script, ["run", "--write", "--offline", "--now", "2026-07-17 09:06"], timeout=120)
            except subprocess.TimeoutExpired:
                pass
        if not os.path.exists(path):
            p = self.gold
            save(path, {"created_by": "fx-timemachine", "created_at": "2026-07-17T04:06:00Z",
                        "mode": "swing", "runs": 10000, "seed": 19,
                        "plan": {k: p[k] for k in ("symbol", "side", "style", "entry", "stop_loss",
                                                   "take_profit", "fx_day")},
                        "odds": {"target_first": 0.31, "stop_first": 0.42, "neither": 0.27},
                        "verdict": "In past moves like these, the stop came first a bit more often "
                                   "than the target."})
        return path

    def test_time_machine_line(self):
        d = self.folder()
        path = self.tm_file(d)
        code, out, err = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as fh:
            tm = json.load(fh)
        odds = tm["odds"]
        want = ("Time machine (a simulation, not a prediction): in %s replays the target came first "
                "%d%%, the stop %d%%, neither %d%%." % (format(tm["runs"], ","), round(odds["target_first"] * 100),
                                                       round(odds["stop_first"] * 100), round(odds["neither"] * 100)))
        self.assertIn(want, flat(out))
        if tm.get("verdict"):
            self.assertIn(flat(tm["verdict"]), flat(out))
        tm["plan"]["entry"] = 2400.5
        save(path, tm)
        code, out, err = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)
        self.assertIn("The time machine ran on an older plan. Not shown.", flat(out))
        self.assertNotIn("a simulation, not a prediction", out)

    def test_roll_rebase_in_memory(self):
        d = self.folder()
        plan = dict(self.gold, created_at="2026-07-29T08:00:00Z", rolls_applied={})
        path = os.path.join(d, "roll-plan.json")
        save(path, plan)
        with open(path, "rb") as fh:
            before = fh.read()
        code, out, err = self.size(d, ["--plan", "roll-plan.json"], "2026-07-29 13:15")
        self.assertIn(code, (0, 1), err)
        self.assertIn("Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same amount. "
                      "Your broker's XAUUSD did not jump: keep the SL and TP you set in MetaTrader.",
                      flat(out))
        self.assertEqual(flat(out).count("Gold rolled on Yahoo"), 1)
        self.assertIn("stop 2,421.59", out.replace("\n", " "))   # the moved stop (2400 x 1.009)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        code, out, err = self.size(d, ["--plan", "roll-plan.json", "--json"], "2026-07-29 13:15")
        doc = json.loads(out)
        self.assertIn(code, (0, 1))
        self.assertTrue(doc["order"] is not None or doc["refused"] is not None)
        self.assertTrue(any("Gold rolled on Yahoo (+0.90%)" in n for n in doc["notes"]))

    def test_roll_found_later_is_dropped(self):
        d = self.folder()
        code, out, err = self.size(d, ["--plan", "gold-plan.json", "--write"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertNotIn("rolled", out)
        self.assertEqual(self.orders(d)["orders"][0]["rolls_applied"], {})

    def test_help_lists_the_traps(self):
        code, out, err = run_py(ROOT, SIZE, ["--help"])
        self.assertEqual(code, 0, err)
        for text in ("Oil lot size", "Silver lot size", "Gold digits", "Swap in points or money",
                     "Triple-swap day", "Min lot", "--risk-pct", "--risk-usd", "--plan", "--write"):
            self.assertIn(text, out)

    def test_no_banned_words(self):
        d = self.folder()
        _c, out1, _e = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)

        def small(cfg):
            cfg["account"]["balance"] = 1000
        d2 = self.folder(small)
        _c, out2, _e = self.size(d2, ["--plan", "gold-plan.json"], GOLD_T)
        for word in fx_core.SIGNAL_BANNED:
            self.assertNotIn(word, (out1 + out2).lower())
        self.assertNotIn(chr(0x2014), out1 + out2)

    # ---- the balance, open trades, prices.json, other plan shapes

    def test_portfolio_balance_and_open_trade(self):
        d = self.folder()
        pos = {"id": "XAUUSD-1", "symbol": "XAUUSD", "side": "buy", "style": "swing", "lots": 0.02,
               "entry": 2433.49, "fill_price": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0,
               "stop_at_open": 2400.0, "max_loss_usd": 97.81, "rolls_applied": {}, "swap_nights": 0}
        save(os.path.join(d, "fx", "portfolio.json"),
             {"account": {"currency": "USD", "balance": 9000.0}, "positions": [pos]})
        code, out, err = self.size(d, ["--plan", "eur-plan.json", "--json"], EUR_T)
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["limits"]["risk_usd"], 90.0)
        self.assertEqual(doc["order"]["lots"], 0.78)
        self.assertLess(doc["limits"]["margin_free_usd"], 9000.0)
        code, out, err = self.size(d, ["--plan", "eur-plan.json"], EUR_T)
        self.assertIn("1.0% of $9,000.00 = $90.00", out)
        self.assertNotIn("minus $0.00 in use", out)

    def test_swing_sell_gets_a_swap_credit(self):
        d = self.folder()
        plan = dict(self.gold, side="sell", entry=2433.4, chart_entry=2433.4, stop_loss=2466.9,
                    chart_stop=2466.9, take_profit=2370.0, risk_points=3350, setup="breakdown_20d")
        save(os.path.join(d, "p.json"), plan)
        code, out, err = self.size(d, ["--plan", "p.json"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("Setup: 20-day low breakdown. Market order: you sell at the bid.", flat(out))
        self.assertIn("paid to you over 28 nights (not counted: a broker can change it)", flat(out))
        self.assertIn("Entry 2,433.40 = the Yahoo bid.", out)

    def test_swap_free_account(self):
        def free(cfg):
            cfg["account"]["swap_free"] = True
        d = self.folder(free)
        code, out, err = self.size(d, ["--plan", "gold-plan.json"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("$0.00 (swap-free account)", flat(out))
        self.assertIn("after 6 free nights", flat(out))
        self.assertIn("swap-free fee", flat(out))

    def test_pending_order_and_config_entry_note(self):
        d = self.folder()
        plan = dict(self.gold, entry=2440.0, entry_type="stop", risk_points=4000, take_profit=2500.0)
        save(os.path.join(d, "p.json"), plan)
        code, out, err = self.size(d, ["--plan", "p.json", "--write"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("Buy stop order at 2,440.00: it fills if the ask rises to it.", flat(out))
        self.assertIn("Your config says swing entries are market; this plan uses a stop order. "
                      "The plan wins.", flat(out))
        o = self.orders(d)["orders"][0]
        self.assertEqual((o["type"], o["entry"]), ("stop", 2440.0))

    def test_price_moved_since_the_plan(self):
        d = self.folder()
        plan = dict(self.gold, entry=2430.0, chart_entry=2429.91, risk_points=3000)
        save(os.path.join(d, "p.json"), plan)
        code, out, err = self.size(d, ["--plan", "p.json", "--write"], GOLD_T)
        self.assertEqual(code, 0, err)
        self.assertIn("The price moved since the plan. Sized at 2,433.49, not 2,430.00. A worse "
                      "entry means a bigger loss at the same stop.", flat(out))
        o = self.orders(d)["orders"][0]
        self.assertEqual((o["entry"], o["plan"]["risk_per_unit"]), (2433.49, 33.49))

    def test_no_fresh_price(self):
        # Tue 07-21: the sample's Yahoo outage (no gold bar since 02:00 UTC)
        d = self.folder()
        plan = dict(self.gold, created_at="2026-07-21T05:00:00Z", fx_day="2026-07-21")
        save(os.path.join(d, "p.json"), plan)
        code, out, err = self.size(d, ["--plan", "p.json"], "2026-07-21 15:00")
        self.assertIn(code, (0, 1), err)
        self.assertIn("No fresh gold price (the newest is 8 h old). Sized at the plan's entry. "
                      "fx-risk needs a price under 30 min old.", flat(out))


class OwnHelpers(unittest.TestCase):
    """size.py's own helpers, called directly."""

    @classmethod
    def setUpClass(cls):
        cls.m = _load.load("fx-budget", "size")
        cls.cfg = cls.m.load_config(os.path.join(MARKET, "config.json"))
        cls.data = os.path.join(MARKET, "data")

    def test_pct_text(self):
        self.assertEqual([self.m.pct_text(x) for x in (1, 1.0, 0.5, 0.25, 2.125, 30)],
                         ["1.0%", "1.0%", "0.5%", "0.25%", "2.13%", "30.0%"])

    def test_fresh_quote_prices_json(self):
        inst = self.m.instrument(self.cfg, "XAUUSD")
        now = self.m.parse_now(GOLD_T)
        d = tempfile.mkdtemp(prefix="fx-budget-q-")
        try:
            paths = self.m.fx_paths(d)
            base = self.m.fresh_quote(self.data, paths, inst, now, self.cfg, False)
            self.assertEqual((base["bid"], base["source"]), (2433.4, "yahoo 5m"))
            save(paths["prices"], {"quotes": {"XAUUSD": {"yahoo": "GC=F", "bid": 2434.1,
                                                         "at": "2026-07-17T04:06:30Z"}}})
            q = self.m.fresh_quote(self.data, paths, inst, now, self.cfg, False)
            self.assertEqual((q["bid"], q["source"], q["age_min"]), (2434.1, "prices.json", 0.5))
            q = self.m.fresh_quote(self.data, paths, inst, now, self.cfg, True)   # a replay
            self.assertEqual(q["source"], "yahoo 5m")
            save(paths["prices"], {"quotes": {"XAUUSD": {"yahoo": "GC=F", "bid": 2434.1,
                                                         "at": "2026-07-17T04:09:00Z"}}})
            q = self.m.fresh_quote(self.data, paths, inst, now, self.cfg, False)   # after now
            self.assertEqual(q["source"], "yahoo 5m")
            save(paths["prices"], {"quotes": {"XAUUSD": {"yahoo": "SI=F", "bid": 30.0,
                                                         "at": "2026-07-17T04:06:30Z"}}})
            q = self.m.fresh_quote(self.data, paths, inst, now, self.cfg, False)   # other symbol
            self.assertEqual(q["source"], "yahoo 5m")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_roll_line_down(self):
        inst = self.m.instrument(self.cfg, "USOIL")
        line = self.m.roll_line(inst, {"ratio": 0.98, "id": "CL-1"})
        self.assertTrue(line.startswith("Oil rolled on Yahoo (-2.00%). Your plan levels moved down"), line)

    def test_lose_and_need_words(self):
        from decimal import Decimal
        self.assertEqual(self.m.lose_word(Decimal("1")), "1.00 lot loses")
        self.assertEqual(self.m.lose_word(Decimal("0.02")), "0.02 lots lose")
        self.assertEqual(self.m.need_word(Decimal("0.09")), "0.09 lots need")


if __name__ == "__main__":
    unittest.main()
