"""Tests for fx-timemachine (timemachine.py), spec section 10. Offline only, on the
made-up sample market (tests/fixtures/fx-market). The script never uses the network;
a test checks that it has no network code at all. All prices are made up."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import json
import random
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal

import _load
import fx_core

TM = _load.load("fx-timemachine", "timemachine")
ROOT = _load.ROOT
FIX = os.path.join(_load.FIX, "fx-market")
SCRIPT = os.path.join(ROOT, "fx-timemachine", "timemachine.py")
SWING_NOW = "2026-07-17 09:06"
DAY_NOW = "2026-07-23 12:42"

# The sample swing BUY of gold (the fx-exit spec, section 5).
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
    "gap": {"note": "made up", "loss_per_lot_usd": 5600.0},
    "rolls_applied": {}, "data_age_min": 0.0, "warnings": []}

# The sample day SELL of EURUSD (the fx-exit spec, section 5).
DAY_PLAN = dict(GOLD_PLAN, **{
    "created_at": "2026-07-23T07:42:00Z", "symbol": "EURUSD", "side": "sell", "style": "day",
    "setup": "or_breakdown", "fx_day": "2026-07-23", "chart_entry": 1.08452,
    "chart_stop": 1.0856, "entry": 1.08452, "entry_type": "market",
    "entry_at": "2026-07-23T07:40:00Z", "entry_rule": "sell at the market (bid 1.08452)",
    "stop_loss": 1.08561, "stop_rule": "or-mid", "take_profit": 1.08234, "target_rule": "2R",
    "target_r": 2.0, "risk_per_unit": 0.00109, "risk_points": 109, "reward_risk": 2.0,
    "reward_risk_net": 1.84, "spread_points": 1, "atr": 0.0003, "atr_interval": "5m",
    "opening_range": {"session": "london", "start": "2026-07-23T07:00:00Z",
                      "end": "2026-07-23T07:15:00Z", "high": 1.0864, "low": 1.0848,
                      "mid": 1.0856, "bars": 3, "of": 3},
    "flat_by": "23:30", "flat_by_utc": "2026-07-23T18:30:00Z", "max_hold_days": None,
    "exit_by": None,
    "costs": {"commission_round_trip": 6.0, "spread": 1.0, "swap_per_night": 3.1,
              "nights_est": 0, "swap_est": 0.0, "swap_free": False, "fee_est": 0.0},
    "data_age_min": 2.0})

PRINTED = []   # every printout, for test_no_advice_words


def make_project(plan=None):
    """A temp project: fx/data copied from the sample, fx/config.json, fx/plan.json."""
    tmp = tempfile.mkdtemp(prefix="fxtm-")
    shutil.copytree(os.path.join(FIX, "data"), os.path.join(tmp, "fx", "data"))
    shutil.copy(os.path.join(FIX, "config.json"), os.path.join(tmp, "fx", "config.json"))
    if plan is not None:
        write_plan(tmp, plan)
    return tmp


def write_plan(tmp, plan, name="plan.json"):
    with open(os.path.join(tmp, "fx", name), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)


def run(tmp, *args):
    """Run timemachine.py in tmp: (code, stdout, stderr)."""
    p = subprocess.run([sys.executable, SCRIPT] + list(args), cwd=tmp, capture_output=True,
                       text=True, timeout=300)
    PRINTED.append(p.stdout + p.stderr)
    return p.returncode, p.stdout, p.stderr


def read_doc(tmp):
    with open(os.path.join(tmp, "fx", "timemachine.json"), encoding="utf-8") as fh:
        return json.load(fh)


def cfg_sample():
    return TM.load_config(os.path.join(FIX, "config.json"), required=True)


# (m) values measured on the first green build of the sample market (seed 19), frozen.
SWING_M = {
    "history": {"from": "2026-04-27", "to": "2026-07-16", "bars": 58, "interval": "1d"},
    "odds": {"target_first": 0.1067, "stop_first": 0.7275, "neither": 0.1658},
    "r": {"p5": -0.989, "p25": -0.776, "median": -0.708, "p75": -0.411, "p95": 0.975, "mean": -0.475},
    "usd_per_lot": {"p5": -4835.68, "median": -3464.68, "p95": 4767.56},
    "gap_through_stop": 0.0622, "green": 0.2375, "verdict_key": "strong_stop",
    "histogram": [0, 0, 108, 356, 3840, 3139, 254, 410, 456, 280, 90, 641, 426] + [0] * 11,
    "last_band": {"step": 20, "p5": 2272.18, "p25": 2348.52, "p50": 2400.63, "p75": 2439.35,
                  "p95": 2477.56},
}
DAY_M = {
    "history": {"from": "2026-06-22", "to": "2026-07-22", "bars": 21, "interval": "5m"},
    "odds": {"target_first": 0.1809, "stop_first": 0.8189, "neither": 0.0002},
    "green": 0.1809, "verdict_key": "strong_stop", "bands": 44, "seed7_target": 0.1854,
}


class TestSample(unittest.TestCase):
    """The sample plans through the real command line."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_project(GOLD_PLAN)
        cls.code, cls.out, cls.err = run(cls.tmp, "run", "--write", "--offline", "--now", SWING_NOW)
        cls.doc = read_doc(cls.tmp)
        with open(os.path.join(cls.tmp, "fx", "timemachine.json"), "rb") as fh:
            cls.doc_bytes = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_swing_sample_runs(self):
        self.assertEqual(self.code, 0, self.err)
        for text in ("10,000", "seed 19", "Mode: swing", "120 is the normal minimum",
                     "Only 73 usable daily bars", "Drift removed", "Wrote fx/timemachine.json.",
                     "Costs: $1,541.52 a lot ($6.00 commission, $1,535.52 swap for 28 nights) = 0.315R.",
                     "1R = $4,890.52 a lot: +0.84R at the target, -1.00R at the stop",
                     "Fri 14 Aug 2:00 AM PKT (5:00 PM New York)",
                     "Same seed, same numbers: repeat with --seed 19.",
                     "A simulation built from Gold's own past 58 days."):
            self.assertIn(text, self.out)
        d = self.doc
        self.assertEqual((d["runs"], d["seed"], d["mode"], d["block"]), (10000, 19, "swing", 5))
        self.assertTrue(d["drift_removed"])
        self.assertEqual(d["created_by"], "fx-timemachine")
        self.assertEqual(d["created_at"], "2026-07-17T04:06:00Z")
        self.assertEqual(d["plan"], {"symbol": "XAUUSD", "side": "buy", "style": "swing",
                                     "entry": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0,
                                     "fx_day": "2026-07-17"})
        self.assertAlmostEqual(sum(d["odds"].values()), 1.0, delta=0.0001)
        self.assertEqual(len(d["bands"]), 20)
        self.assertEqual([b["step"] for b in d["bands"]], list(range(1, 21)))
        self.assertEqual(sum(d["histogram"]["counts"]), 10000)
        self.assertEqual((d["histogram"]["from"], d["histogram"]["width"]), (-2.0, 0.25))
        self.assertEqual(len(d["histogram"]["counts"]), 24)
        self.assertEqual(d["costs_r"], 0.315)
        self.assertEqual(d["net_target_r"], 0.84)
        self.assertEqual(d["max_loss_per_lot"], 4890.52)
        self.assertEqual(d["until"], "2026-08-13T21:00:00Z")
        self.assertEqual(d["history"]["to"], "2026-07-16")
        self.assertTrue(d["verdict"].startswith("Strongly toward the stop: at 1.69R after costs"))

    def test_swing_measured_values(self):
        d = self.doc
        for key in ("history", "odds", "r", "usd_per_lot", "gap_through_stop", "green", "verdict_key"):
            self.assertEqual(d[key], SWING_M[key], key)
        self.assertEqual(d["histogram"]["counts"], SWING_M["histogram"])
        self.assertEqual(d["bands"][-1], SWING_M["last_band"])

    def test_same_seed_same_numbers(self):
        code, out, err = run(self.tmp, "run", "--write", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.tmp, "fx", "timemachine.json"), "rb") as fh:
            again = fh.read()
        self.assertEqual(again, self.doc_bytes)   # created_at is the --now time, so all equal
        code, out, err = run(self.tmp, "run", "--json", "--seed", "7", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        seven = json.loads(out)["result"]
        self.assertEqual(seven["seed"], 7)
        self.assertNotEqual(seven["odds"], self.doc["odds"])

    def test_raw_keeps_drift(self):
        code, out, err = run(self.tmp, "run", "--raw", "--json", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        res = json.loads(out)["result"]
        self.assertFalse(res["drift_removed"])
        code, out, err = run(self.tmp, "run", "--raw", "--fan", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("Drift kept (--raw)", out)
        self.assertNotIn("Drift removed", out)
        self.assertIn("Fan (Yahoo bid): step, low 5 %, middle, high 5 %", out)
        self.assertIn("  day 20 ", out)
        self.assertIn("Not saved. Run again with --write to save fx/timemachine.json.", out)

    def test_quiet(self):
        code, out, err = run(self.tmp, "run", "--quiet", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        lines = out.strip().split("\n")
        self.assertTrue(lines[0].startswith("XAUUSD BUY swing plan: entry 2,433.49"))
        self.assertTrue(lines[1].startswith("  target first"))
        self.assertTrue(lines[5].startswith("Strongly toward the stop"))
        self.assertNotIn("Mode:", out)
        self.assertNotIn("Next:", out)


class TestDayAndData(unittest.TestCase):
    """The day plan, the roll in memory and no peeking."""

    def setUp(self):
        self.tmp = make_project(GOLD_PLAN)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_day_sample_runs(self):
        write_plan(self.tmp, DAY_PLAN)
        code, out, err = run(self.tmp, "run", "--json", "--offline", "--now", DAY_NOW)
        self.assertEqual(code, 0, err)
        j = json.loads(out)
        self.assertEqual((j["v"], j["tool"], j["cmd"], j["ok"]), (1, "fx-timemachine", "run", True))
        self.assertEqual(j["now"], "2026-07-23T07:42:00Z")
        self.assertIsNone(j["doc"])
        self.assertFalse(j["written"])
        r = j["result"]
        self.assertEqual((r["mode"], r["block"], r["history"]["interval"]), ("day", 3, "5m"))
        self.assertGreaterEqual(r["history"]["bars"], 20)
        self.assertLessEqual(len(r["bands"]), 60)
        self.assertEqual(r["bands"][-1]["step"], 130)
        self.assertFalse(r["drift_removed"])
        self.assertEqual((r["costs_r"], r["net_target_r"], r["max_loss_per_lot"]), (0.052, 1.843, 115.0))
        self.assertEqual(r["until"], "2026-07-23T18:30:00Z")
        for key in ("history", "odds", "green", "verdict_key"):
            self.assertEqual(r[key], DAY_M[key], key)
        self.assertEqual(len(r["bands"]), DAY_M["bands"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "fx", "timemachine.json")))
        code, out, err = run(self.tmp, "run", "--now", DAY_NOW)
        self.assertEqual(code, 0, err)
        for text in ("seed 19", "flat-by", "fire on the ask", "130 five-minute bars",
                     "Thu 12:40 PM PKT", "2 min old when planned (Thu 12:40 PM PKT)",
                     "Thu 11:30 PM PKT (2:30 PM New York)", "London session open",
                     "The pool: 21 full past days, 2026-06-22 to 2026-07-22.",
                     "A stop counts as hit 2 pips early.",
                     "Costs: $6.00 a lot (commission) = 0.052R.",
                     "1R = $115.00 a lot: +1.84R at the target, -1.00R at the stop",
                     "still open at 11:30 PM PKT", "under the entry at 11:30 PM PKT",
                     "own past 21 days of 5-minute bars"):
            self.assertIn(text, out)
        code, out, err = run(self.tmp, "run", "--json", "--seed", "7", "--now", DAY_NOW)
        self.assertEqual(json.loads(out)["result"]["odds"]["target_first"], DAY_M["seed7_target"])

    def test_old_day_plan_note(self):
        write_plan(self.tmp, DAY_PLAN)
        code, out, err = run(self.tmp, "run", "--runs", "100", "--now", "2026-07-24 09:00")
        self.assertEqual(code, 0, err)
        self.assertIn("Note: This plan's flat-by time (Thu 11:30 PM PKT) has passed. "
                      "The replays still run to it.", out)

    def test_no_peeking(self):
        code, out, err = run(self.tmp, "run", "--json", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        before = json.loads(out)["result"]
        path = os.path.join(self.tmp, "fx", "data", "yahoo", "GC_F", "1h.json")
        with open(path, encoding="utf-8") as fh:
            cache = json.load(fh)
        cut = fx_core.utc_to_ts(fx_core.parse_utc("2026-07-16T21:00:00Z"))
        for row in cache["bars"]:
            if row[0] >= cut:   # the plan's own FX day and later: made wild
                row[1:5] = [x * 1.08 for x in row[1:5]]
        last = cache["bars"][-1][0]
        for k in range(1, 30):   # and extra fake bars after the end
            cache["bars"].append([last + 3600 * k, 3000.0, 3100.0, 2900.0, 3050.0, 0])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        code, out, err = run(self.tmp, "run", "--json", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        after = json.loads(out)["result"]
        self.assertEqual(after, before)
        self.assertEqual(after["history"]["to"], "2026-07-16")

    def test_roll_rebase_in_memory(self):
        plan_path = os.path.join(self.tmp, "fx", "plan.json")
        with open(plan_path, "rb") as fh:
            plan_bytes = fh.read()
        code, out, err = run(self.tmp, "run", "--write", "--now", "2026-07-29 13:15")
        self.assertEqual(code, 0, err)
        self.assertIn("Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same amount. "
                      "Your broker's XAUUSD did not jump: keep the SL and TP you set in MetaTrader.", out)
        d = read_doc(self.tmp)
        self.assertEqual((d["plan"]["entry"], d["plan"]["stop_loss"], d["plan"]["take_profit"]),
                         (2455.39, 2421.59, 2512.41))
        self.assertIn("entry 2,455.39, stop 2,421.59, target 2,512.41", out)
        with open(plan_path, "rb") as fh:
            self.assertEqual(fh.read(), plan_bytes)
        self.assertEqual(d["history"]["to"], "2026-07-16")


def _tables(inst, cfg, plan, steps, nights):
    l1 = TM.max_loss_core(inst, plan["side"], plan["entry"], plan["stop_loss"],
                          plan["costs"]["nights_est"], {}, cfg)
    return {"steps": steps, "nights": nights, "band_steps": list(range(steps)), "rates": {},
            "l1": l1, "margin_points": 0}


class TestUnits(unittest.TestCase):
    """The money cores, the hit rules and the replay on made pools."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = cfg_sample()
        cls.gold = TM.instrument(cls.cfg, "XAUUSD")
        cls.eur = TM.instrument(cls.cfg, "EURUSD")

    def test_stop_hit_is_minus_one_r(self):
        g, cfg = self.gold, self.cfg
        l1 = TM.max_loss_core(g, "buy", 2433.49, 2400.0, 28, {}, cfg)
        self.assertEqual(l1, Decimal("4890.52"))
        res = TM.result_core(g, "buy", 2433.49, 2400.0, 28, {}, cfg)
        self.assertEqual(res / l1, Decimal(-1))
        net = TM.net_core(g, "buy", 2433.49, 2400.0, 2490.0, 28, {}, cfg)
        self.assertEqual(TM.result_core(g, "buy", 2433.49, 2490.0, 28, {}, cfg) / l1, net)

    def test_fast_hits_matches_bar_hits(self):
        rng = random.Random(5)
        checked = 0
        for inst, mid in ((self.eur, 1.0845), (self.gold, 2433.4)):
            point = inst["point"]
            for margin in (0, 20):
                for side in ("buy", "sell"):
                    for n in range(2000):
                        width = 300 * point
                        if side == "buy":
                            sl, tp = mid - rng.randint(1, 200) * point, mid + rng.randint(1, 200) * point
                        else:
                            sl, tp = mid + rng.randint(1, 200) * point, mid - rng.randint(1, 200) * point
                        vals = [mid + rng.uniform(-width, width) for _ in range(3)]
                        if n % 2:   # half on the tick grid, so ties happen
                            vals = [round(v / point) * point for v in vals]
                            sl, tp = round(sl / point) * point, round(tp / point) * point
                        o = vals[0]
                        h, l = max(vals), min(vals)
                        bar = {"open": o, "high": h, "low": l}
                        lines = TM.hit_lines(side, sl, tp, inst, margin)
                        self.assertEqual(TM.fast_hits(side, o, h, l, lines),
                                         TM.bar_hits(side, bar, sl, tp, inst, margin_points=margin),
                                         (side, margin, bar, sl, tp))
                        checked += 1
        self.assertEqual(checked, 16000)

    def test_sell_fires_on_ask(self):
        e = self.eur
        bar = {"open": 1.08500, "high": 1.08560, "low": 1.08480}   # high < SL <= high + spread
        self.assertEqual(TM.bar_hits("sell", bar, 1.08561, 1.08234, e), "stop")
        lines = TM.hit_lines("sell", 1.08561, 1.08234, e)
        self.assertEqual(TM.fast_hits("sell", 1.085, 1.0856, 1.0848, lines), "stop")
        self.assertIsNone(TM.fast_hits("sell", 1.085, 1.08559, 1.0848, lines))

    def test_both_counts_as_stop(self):
        g, cfg, plan = self.gold, self.cfg, dict(GOLD_PLAN)
        bar = {"open": 2433.4, "high": 2500.0, "low": 2390.0}
        self.assertEqual(TM.bar_hits("buy", bar, 2400.0, 2490.0, g), "both")
        sessions = [("2026-06-01", {0: (1.0, 2500.0 / 2433.4, 2390.0 / 2433.4, 1.0)})]
        raw = TM.replay("day", plan, g, cfg, sessions, {0: (1, [(0, 0)])}, random.Random(1), 100,
                        _tables(g, cfg, plan, 1, [0, 0]))
        self.assertEqual((raw["counts"]["stop"], raw["counts"]["target"]), (100, 0))

    def test_gap_past_stop(self):
        g, cfg, plan = self.gold, self.cfg, dict(GOLD_PLAN)
        day1 = (1.0, 1.001, 0.999, 1.0)
        o2 = (2400.0 - 3 * 28.0) / 2433.4   # day 2 opens 3 ATR past the stop
        pool = [{"f": day1}, {"f": (o2, o2 * 1.001, o2 * 0.999, o2)}]
        raw = TM.replay("swing", plan, g, cfg, pool, {0: (2, [0])}, random.Random(1), 200,
                        _tables(g, cfg, plan, 2, [0, 1, 1]))
        s = TM.summarize(raw, _tables(g, cfg, plan, 2, [0])["l1"], plan, g)
        self.assertEqual(s["gap_through_stop"], 1.0)
        self.assertEqual(s["odds"]["stop_first"], 1.0)
        self.assertTrue(all(r < -1 for r in raw["r"]))

    def test_pending_line_and_never_fills(self):
        g, cfg = self.gold, self.cfg
        plan = dict(GOLD_PLAN, entry_type="stop", entry=3000.0, take_profit=3100.0)
        pool = [{"f": (1.0, 1.001, 0.999, 1.0)}] * 5
        raw = TM.replay("swing", plan, g, cfg, pool, {0: (1, [0, 1, 2])}, random.Random(1), 100,
                        _tables(g, cfg, plan, 1, [0, 0]))
        self.assertEqual(raw["counts"]["neither"], 100)
        self.assertEqual(raw["counts"]["filled"], 0)
        self.assertEqual(set(raw["r"]), {0.0})

    def test_band_steps(self):
        self.assertEqual(TM.band_steps(20), list(range(20)))
        steps = TM.band_steps(130)
        self.assertEqual(steps[:3], [2, 5, 8])
        self.assertEqual(steps[-1], 129)
        self.assertLessEqual(len(steps), 60)

    def test_pct(self):
        self.assertEqual(TM.pct([5, 1, 3, 2, 4], 0.5), 3)
        self.assertEqual(TM.pct(list(range(101)), 0.05), 5)
        self.assertEqual(TM.pct([1.0, 2.0], 0.95), 2.0)


def _summary(tf, sf, nei, net, costs="0", l1="4890.52", mean=0.1, gap=0.0, mode="swing", bars=58):
    return {"_share": {"target": tf, "stop": sf, "neither": nei, "gap": gap, "green": tf},
            "_mean": mean, "_net": Decimal(str(net)), "_costs": Decimal(costs),
            "_l1": Decimal(l1), "mode": mode, "history": {"bars": bars}, "seed": 19}


class TestVerdict(unittest.TestCase):
    """05 6.7 word for word."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = cfg_sample()
        cls.gold = TM.instrument(cls.cfg, "XAUUSD")
        cls.eur = TM.instrument(cls.cfg, "EURUSD")

    def lines(self, summary, plan=None, inst=None):
        return TM.verdict_lines(summary, plan or dict(GOLD_PLAN, target_r=2.0), inst or self.gold, self.cfg)

    def test_costs_eat(self):
        net = (Decimal("1400.00") - Decimal("1541.52")) / Decimal("4890.52")
        key, lines = self.lines(_summary(0.3, 0.4, 0.3, net, costs="1541.52"))
        self.assertEqual(key, "costs_eat")
        self.assertEqual(lines[0], "Costs eat the target: they are $1,541.52 a lot, and the target "
                                   "pays $1,400.00 a lot before costs.")

    def test_too_few(self):
        key, lines = self.lines(_summary(0.041, 0.040, 0.919, 1.5))
        self.assertEqual(key, "too_few")
        self.assertEqual(lines[0], "Too few replays hit the stop or the target to judge (8.1 %).")
        self.assertEqual(lines[1], "91.9 % of the replays hit neither by Fri 14 Aug 2:00 AM PKT; "
                                   "the price then decides those.")

    def test_coin_flip(self):
        key, lines = self.lines(_summary(0.26, 0.24, 0.5, 1.0), dict(GOLD_PLAN, target_r=1.0))
        self.assertEqual((key, lines[0]), ("coin_flip", "Close to a coin flip."))

    def test_break_even(self):
        net = 1 / 0.34 - 1   # be = 34 %
        key, lines = self.lines(_summary(0.36 * 0.8, 0.64 * 0.8, 0.2, net, costs="6"))
        self.assertEqual(key, "break_even")
        self.assertEqual(lines[0], "Close to break-even: at 2R after costs the target has to come "
                                   "first in 34 % of the replays that hit one side; it did in 36 %.")
        self.assertNotIn("The average result", " ".join(lines))

    def test_leans_stop_with_mean_line(self):
        net = 1 / 0.34 - 1
        key, lines = self.lines(_summary(0.25, 0.75, 0.0, net, costs="6", mean=0.05))
        self.assertEqual(key, "leans_stop")
        self.assertTrue(lines[0].startswith("Leans toward the stop: at 2R after costs"))
        self.assertIn("The average result is 0.050R, costs included.", lines)

    def test_strong_target_and_lines_under(self):
        net = 1 / 0.34 - 1
        key, lines = self.lines(_summary(0.55 * 0.55, 0.45 * 0.55, 0.451, net, costs="6",
                                         mean=-0.226, gap=0.019))
        self.assertEqual(key, "strong_target")
        self.assertEqual(lines[0], "Strongly toward the target: at 2R after costs the target has to "
                                   "come first in 34 % of the replays that hit one side; it did in 55 %.")
        self.assertEqual(lines[1:4], [
            "45.1 % of the replays hit neither by Fri 14 Aug 2:00 AM PKT; the price then decides those.",
            "The average result is -0.226R, costs included.",
            "1.9 % of the replays gapped past the stop: the loss was bigger than 1R."])
        self.assertEqual(lines[-2], "A simulation built from Gold's own past 58 days. It is not a "
                                    "prediction: news, rolls and gaps can do things those days never did.")
        self.assertEqual(lines[-1], "Same seed, same numbers: repeat with --seed 19.")

    def test_day_words(self):
        net = 1 / 0.34 - 1
        key, lines = self.lines(_summary(0.05, 0.45, 0.5, net, costs="6", mode="day", bars=21),
                                dict(DAY_PLAN), self.eur)
        self.assertEqual(key, "strong_stop")
        self.assertIn("50.0 % of the replays hit neither by Thu 11:30 PM PKT; the price then "
                      "decides those.", lines)
        self.assertIn("A simulation built from EURUSD's own past 21 days of 5-minute bars. It is "
                      "not a prediction: news, rolls and gaps can do things those days never did.", lines)


class TestRefusals(unittest.TestCase):
    """Every refusal and error: one line on stderr, code 2, nothing written."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_project()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def refused(self, plan, now, message, *extra):
        if plan is not None:
            write_plan(self.tmp, plan)
        out_file = os.path.join(self.tmp, "fx", "timemachine.json")
        if os.path.exists(out_file):
            os.remove(out_file)
        code, out, err = run(self.tmp, "run", "--write", "--now", now, *extra)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "timemachine.py: " + message)
        self.assertEqual(len(err.strip().split("\n")), 1)
        self.assertFalse(os.path.exists(out_file))

    def test_no_plan(self):
        path = os.path.join(self.tmp, "fx", "plan.json")
        if os.path.exists(path):
            os.remove(path)
        self.refused(None, SWING_NOW, "no plan at fx/plan.json. Run fx-exit first.")

    def test_flag_checks(self):
        self.refused(GOLD_PLAN, SWING_NOW, "--runs must be 100 to 100,000, not 50.", "--runs", "50")
        self.refused(GOLD_PLAN, SWING_NOW, "--runs must be 100 to 100,000, not 200000.", "--runs", "200000")
        self.refused(GOLD_PLAN, SWING_NOW, "--seed must be a whole number from 0 to 2,147,483,647.",
                     "--seed", "-1")
        self.refused(GOLD_PLAN, SWING_NOW, "--seed must be a whole number from 0 to 2,147,483,647.",
                     "--seed", "2147483648")
        self.refused(DAY_PLAN, DAY_NOW, "--raw is for swing plans. A day plan keeps the drift anyway.",
                     "--raw")
        self.refused(GOLD_PLAN, "tomorrow", '--now wants a time like "2026-07-23 12:40" (Karachi '
                     'time) or ...Z (UTC)')

    def test_broken_plans(self):
        self.refused(dict(GOLD_PLAN, stop_loss=2440.0), SWING_NOW, "fx/plan.json: the stop or the "
                     "target is on the wrong side of the entry. Run fx-exit again.")
        self.refused(dict(DAY_PLAN, take_profit=1.09), DAY_NOW, "fx/plan.json: the stop or the "
                     "target is on the wrong side of the entry. Run fx-exit again.")
        self.refused(dict(GOLD_PLAN, risk_points=3000), SWING_NOW,
                     "fx/plan.json does not add up (risk_points). Run fx-exit again.")
        self.refused(dict(GOLD_PLAN, exit_by=None), SWING_NOW, 'fx/plan.json: a swing plan needs "exit_by"')
        self.refused(dict(DAY_PLAN, flat_by_utc=None), DAY_NOW,
                     'fx/plan.json: a day plan needs "flat_by_utc"')
        no_entry_at = dict(GOLD_PLAN)
        del no_entry_at["entry_at"]
        self.refused(no_entry_at, SWING_NOW, 'fx/plan.json has no "entry_at". Run fx-exit again.')
        self.refused(dict(GOLD_PLAN, side="long"), SWING_NOW,
                     'fx/plan.json: side must be "buy" or "sell", not "long"')
        self.refused(dict(GOLD_PLAN, symbol="GOLD"), SWING_NOW, 'fx/plan.json: "GOLD" is not in your '
                     'instrument list (fx/config.json "instruments")')
        self.refused(dict(GOLD_PLAN, entry_at="today"), SWING_NOW,
                     'fx/plan.json: "entry_at" is not a UTC time like 2026-11-11T08:40:00Z')
        with open(os.path.join(self.tmp, "fx", "plan.json"), "w") as fh:
            fh.write("[1, 2]")
        self.refused(None, SWING_NOW, "fx/plan.json must be a JSON object: {...}")

    def test_short_swing_pool_refused(self):
        plan = dict(GOLD_PLAN, fx_day="2026-05-11", entry_at="2026-05-10T21:00:00Z",
                    created_at="2026-05-11T04:00:00Z", exit_by="2026-06-05T21:00:00Z")
        write_plan(self.tmp, plan)
        code, out, err = run(self.tmp, "run", "--now", "2026-05-11 09:00")
        self.assertEqual(code, 2)
        self.assertIn("(need 40)", err)
        self.assertRegex(err.strip(), r"^timemachine.py: Only \d+ usable daily bars before this plan "
                                      r"\(need 40\)\. Too few for a replay\.$")

    def test_short_day_pool_refused(self):
        plan = dict(DAY_PLAN, fx_day="2026-07-08", entry_at="2026-07-08T07:40:00Z",
                    created_at="2026-07-08T07:42:00Z", flat_by_utc="2026-07-08T18:30:00Z")
        write_plan(self.tmp, plan)
        code, out, err = run(self.tmp, "run", "--now", "2026-07-08 12:42")
        self.assertEqual(code, 2)
        self.assertRegex(err.strip(), r"^timemachine.py: Only \d+ full past days of 5-minute bars "
                                      r"for these hours \(need 20\)\.$")

    def test_nothing_to_replay(self):
        self.refused(dict(GOLD_PLAN, exit_by="2026-07-16T21:00:00Z"), SWING_NOW,
                     "The exit-by time is at or before the entry. There is nothing to replay.")
        self.refused(dict(DAY_PLAN, flat_by_utc="2026-07-23T07:40:00Z"), DAY_NOW,
                     "The flat-by time is at or before the entry. There is nothing to replay.")

    def test_missing_config(self):
        self.refused(GOLD_PLAN, SWING_NOW, "no config file at fx/nope.json (copy config.example.json "
                     "from the fx-budget folder)", "--config", "fx/nope.json")


def _daily(days, flat=False):
    """Made-up daily bars for the patched tests: one per FX day given."""
    out = []
    for n, d in enumerate(days):
        start, end = fx_core.fx_day_bounds(d, "17:00 New York")
        c = 2400.0 if flat else 2400.0 + (n % 7)
        hi, lo = (c, c) if flat else (c + 15.0, c - 15.0)
        out.append({"fx_day": d, "start": start, "end": end, "open": c, "high": hi, "low": lo,
                    "close": c, "hours": 23, "flags": []})
    return out


class TestDataErrors(unittest.TestCase):
    """Missing bars, a missing USD rate, no ATR, no run of days."""

    def setUp(self):
        self.tmp = make_project(GOLD_PLAN)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refused(self, now, message):
        code, out, err = run(self.tmp, "run", "--now", now)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(err.strip(), "timemachine.py: " + message)

    def test_no_hourly_bars(self):
        os.remove(os.path.join(self.tmp, "fx", "data", "yahoo", "GC_F", "1h.json"))
        self.refused(SWING_NOW, "No hourly prices for XAUUSD yet (fx/data/yahoo/GC_F/1h.json). "
                                "Run without --offline once to get them.")

    def test_no_5m_bars(self):
        write_plan(self.tmp, DAY_PLAN)
        os.remove(os.path.join(self.tmp, "fx", "data", "yahoo", "EURUSD_X", "5m.json"))
        self.refused(DAY_NOW, "No 5-minute prices for EURUSD yet (fx/data/yahoo/EURUSD_X/5m.json). "
                              "The day time machine needs them.")

    def test_rate_missing(self):
        shutil.rmtree(os.path.join(self.tmp, "fx", "data", "yahoo", "USDJPY_X"))
        plan = dict(GOLD_PLAN, symbol="EURJPY", chart_entry=160.0, entry=160.005,
                    stop_loss=158.5, take_profit=163.0, risk_points=1505)
        write_plan(self.tmp, plan)
        self.refused(SWING_NOW, "No USD rate for JPY. It comes from USDJPY. Keep USDJPY in your config "
                                "and run once with Yahoo on (not --offline).")

    def patched(self, days, flat=False):
        cfg = cfg_sample()
        inst = TM.instrument(cfg, "XAUUSD")
        old = TM.daily_from_hourly
        TM.daily_from_hourly = lambda *a, **k: _daily(days, flat)
        try:
            TM._swing_setup(dict(GOLD_PLAN), inst, cfg, os.path.join(self.tmp, "fx", "data"),
                            TM.parse_now(SWING_NOW), [], False, [])
        finally:
            TM.daily_from_hourly = old

    def test_no_atr(self):
        days = ["2026-05-%02d" % d for d in range(1, 32)] + ["2026-06-%02d" % d for d in range(1, 31)]
        days = [d for d in days if fx_core.parse_fx_date(d, weekend_ok=True).weekday() < 5]
        with self.assertRaises(ValueError) as cm:
            self.patched(days, flat=True)
        self.assertEqual(str(cm.exception), "Too few bars for an ATR of XAUUSD (need 15 usable bars).")

    def test_no_run_of_days(self):
        import datetime
        monday = datetime.date(2025, 6, 2)
        days = [(monday + datetime.timedelta(weeks=k)).isoformat() for k in range(50)]
        with self.assertRaises(ValueError) as cm:
            self.patched(days)
        self.assertEqual(str(cm.exception),
                         "The past days have no run of 5 days in a row. There is nothing to replay.")


class TestOtherLines(unittest.TestCase):
    """The pending entry, the swap lines, the help, no network, no advice words."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_project()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pending_entry_never_fills(self):
        plan = dict(GOLD_PLAN, entry_type="stop", entry=3000.0, take_profit=3100.0,
                    risk_points=60000, target_r=0.17)
        write_plan(self.tmp, plan)
        code, out, err = run(self.tmp, "run", "--json", "--runs", "500", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        r = json.loads(out)["result"]
        self.assertEqual(r["odds"], {"target_first": 0.0, "stop_first": 0.0, "neither": 1.0})
        self.assertEqual(set(r["r"].values()), {0.0})
        self.assertEqual(r["green"], 0.0)
        self.assertEqual(r["verdict_key"], "too_few")
        code, out, err = run(self.tmp, "run", "--runs", "500", "--now", SWING_NOW)
        self.assertIn("The entry is a buy stop at 3,000.00. A replay that never fills counts as "
                      "neither, at 0R.", out)

    def test_swap_paid_to_you(self):
        plan = dict(GOLD_PLAN, side="sell", entry=2433.4, stop_loss=2470.0, take_profit=2380.0,
                    risk_points=3660, target_r=1.46)
        write_plan(self.tmp, plan)
        code, out, err = run(self.tmp, "run", "--runs", "500", "--now", SWING_NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("XAUUSD SELL swing plan: entry 2,433.40, stop 2,470.00, target 2,380.00", out)
        self.assertIn("  Swap +$38.52 a night is paid to you; it counts in each R.", out)
        self.assertIn("  Costs: $6.00 a lot (commission) = 0.002R.", out)
        self.assertIn("under the entry at the end", out)

    def test_swap_free_fee(self):
        with open(os.path.join(FIX, "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["account"]["swap_free"] = True
        with open(os.path.join(self.tmp, "fx", "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        try:
            write_plan(self.tmp, GOLD_PLAN)
            code, out, err = run(self.tmp, "run", "--json", "--runs", "500", "--now", SWING_NOW)
            self.assertEqual(json.loads(out)["result"]["max_loss_per_lot"], 4345.0)
            code, out, err = run(self.tmp, "run", "--runs", "500", "--now", SWING_NOW)
            self.assertEqual(code, 0, err)
            self.assertIn("  Costs: $6.00 commission and a $990.00 swap-free fee for 28 nights a lot "
                          "= 0.229R.", out)
        finally:
            shutil.copy(os.path.join(FIX, "config.json"), os.path.join(self.tmp, "fx", "config.json"))

    def test_no_subcommand_prints_help(self):
        code, out, err = run(self.tmp)
        self.assertEqual(code, 2)
        self.assertIn("run", out)

    def test_no_network_code(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        for word in ("urllib", "socket", "http.client", "yahoo_get(", "refresh(", "check_roll_forward("):
            self.assertNotIn(word, src)
        for name in ("yahoo_get", "refresh", "check_roll_forward", "fetch_chart"):
            self.assertFalse(hasattr(TM, name), name)

    def test_no_advice_words(self):
        with open(os.path.join(ROOT, "fx-timemachine", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read().replace("not a recommendation", "")
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        write_plan(self.tmp, GOLD_PLAN)
        run(self.tmp, "run", "--fan", "--now", SWING_NOW)
        write_plan(self.tmp, DAY_PLAN)
        run(self.tmp, "run", "--fan", "--now", DAY_NOW)
        texts = PRINTED + [skill, src]
        for text in texts:
            low = text.lower()
            for word in fx_core.SIGNAL_BANNED:
                self.assertNotIn(word, low)
            self.assertNotIn(chr(0x2014), text)
