"""The README example, tool by tool, through the real command lines of all 11 tools.

Two trades on the made-up sample market (tests/fixtures/fx-market): a swing BUY of gold
(XAUUSD) and a day SELL of EURUSD. Each step runs a skill's script the way a user would, in
a temp project folder. Its fx/data is a copy of the sample data, and its fx/config.json is
the sample config. Each tool reads the files the one before it wrote. So a change to a
shared file (watchlist.json, entry.json, plan.json, timemachine.json, orders.json,
state/risk.json, state/broker.json, portfolio.json, prices.json, alerts.json) that breaks
the next reader fails here.

Offline, with --now inside the sample market (Karachi time). Time only goes forward, so one
paper portfolio holds both trades. The plan is spec 06 section 6.2. One step is added: a
PASS older than 5 minutes is refused, and fx-risk must run again (at the EURUSD close).
Each step must end in under 60 seconds (the lead's decision 7).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import resource
import shutil
import subprocess
import tempfile
import time
import unittest

import _load
import fx_core

ROOT = _load.ROOT
MARKET = os.path.join(_load.FIX, "fx-market")
STEP_LIMIT = 60.0           # seconds, for each step
ROLL_ID = "GC-2026-07-29"
ROLL_RATIO = 2500.3 / 2478.0  # the Dec contract over the Aug one at the roll (story k)
MT = "In MetaTrader:"       # the line to type into MetaTrader: only after a real yes


def flat(text):
    """Text with every run of spaces and line breaks made one space (long lines wrap)."""
    return " ".join(text.split())


def child_cpu():
    """CPU seconds (user + system) used so far by finished child processes."""
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def run(cwd, script, args):
    """Run one tool as a user would. Gives (exit code, all printed text, CPU seconds).
    CPU time, not the wall clock: a Mac that sleeps or pauses the test must not fail it."""
    start = child_cpu()
    p = subprocess.run([sys.executable, os.path.join(ROOT, script)] + list(args), cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=1800)
    return p.returncode, p.stdout, child_cpu() - start


class Chain(unittest.TestCase):
    """One temp project for the whole story. The steps run in time order."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fx-chain-")
        os.makedirs(os.path.join(cls.tmp, "fx"))
        shutil.copytree(os.path.join(MARKET, "data"), os.path.join(cls.tmp, "fx", "data"))
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(cls.tmp, "fx", "config.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- small helpers

    def step(self, now, script, *args, **kw):
        """Run script with args plus --offline --now NOW. Check the exit code (code=, 0 by
        default), the texts it must print (has=) and must not print (hasnt=), and the time."""
        args = list(args) + ["--offline", "--now", now]
        code, out, secs = run(self.tmp, script, args)
        said = "%s %s (exit %s, %.1f s):\n%s" % (script, " ".join(args), code, secs, out)
        self.assertEqual(code, kw.get("code", 0), said)
        for text in kw.get("has", ()):
            self.assertTrue(text in out or flat(text) in flat(out), "missing %r in %s" % (text, said))
        for text in kw.get("hasnt", ()):
            self.assertNotIn(flat(text), flat(out), said)
        self.assertLess(secs, STEP_LIMIT, "too slow: " + said)
        return out

    def path(self, name):
        return os.path.join(self.tmp, "fx", name)

    def doc(self, name):
        """One JSON file of the project, for example doc("plan.json")."""
        with open(self.path(name), encoding="utf-8") as fh:
            return json.load(fh)

    def files(self):
        """Every file under fx/ except the copied data, with its bytes (to see what changed)."""
        found = {}
        base = os.path.join(self.tmp, "fx")
        for top, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if os.path.join(top, d) != os.path.join(base, "data")]
            for name in names:
                full = os.path.join(top, name)
                with open(full, "rb") as fh:
                    found[os.path.relpath(full, base)] = fh.read()
        return found

    def pick(self, row, keys):
        """The values of keys in row, as a dict (for one compact assertEqual)."""
        return dict((k, row.get(k)) for k in keys)

    # ---------------------------------------------------------------- the story

    def test_the_readme_example_end_to_end(self):
        self.gold_open()
        self.gold_outage()
        self.day_sell()
        self.day_target_and_close()
        self.gold_roll()
        self.gold_target_and_close()

    # ---------------------------------------------------------------- 1. gold: scan to plan

    def gold_open(self):
        # the swing scan before the London open: gold closed over its 20-day high
        self.step("2026-07-17 09:00", "fx-scanner/scan.py", "swing",
                  has=["10 hits. Swing scan, Fri 2026-07-17 9:00 AM PKT.", "XAUUSD", "2,418.4",
                       "XAUUSD  buy side  20-day high breakout: level 2,418.40, last 2,431.60,",
                       "Wrote fx/watchlist.json (swing, 10 hits)."])
        wl = self.doc("watchlist.json")
        self.assertEqual(self.pick(wl, ["mode", "fx_day", "session", "failed"]),
                         {"mode": "swing", "fx_day": "2026-07-16", "session": None, "failed": []})
        hits = [h for h in wl["hits"] if h["symbol"] == "XAUUSD"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(self.pick(hits[0], ["side", "style", "setup", "level", "last", "atr_interval",
                                             "rolls_applied"]),
                         {"side": "buy", "style": "swing", "setup": "breakout_20d", "level": 2418.4,
                          "last": 2431.6, "atr_interval": "1d", "rolls_applied": {}})

        # the reading tools: news (a dry run marks nothing), the calendar, the COT report
        before = self.files()
        self.step("2026-07-17 09:01", "fx-news/news.py", "--symbols", "XAUUSD", "--dry-run",
                  has=["1 new headline.", "Gold climbs to a three-week high as the dollar slips",
                       "Dry run: nothing was marked as seen."])
        self.assertFalse(os.path.exists(self.path("state/news.json")))
        self.step("2026-07-17 09:01", "fx-calendar/events.py", "week",
                  has=["CPI m/m", "PKT", "New York",
                       "Tue 14 Jul 5:30 PM PKT (8:30 AM New York)  USD  High  CPI m/m"])
        self.assertEqual(self.files(), before, "news --dry-run and calendar week write no file")
        # the 07-14 report is out only at 12:30 AM PKT on Sat 07-18, so a replay at 09:01 shows 07-07
        self.step("2026-07-17 09:01", "fx-cot/cot.py", "show", "--symbols", "XAUUSD",
                  has=["2026-07-07", "net long 98,000 contracts, +6,200 in the week"],
                  hasnt=["2026-07-14"])
        self.assertEqual(self.doc("state/cot.json")["seen"], {"72hh-3qpy-088691": "2026-07-07"})

        # the entry timer reads the watchlist: the daily close is over the level
        self.step("2026-07-17 09:02", "fx-entry/entry.py", "check", "XAUUSD", "--write",
                  has=["TRIGGERED: the daily bar of Thu 2026-07-16 closed at 2,431.60, above 2,418.40.",
                       "Nothing was placed.", "Wrote fx/entry.json (triggered)."])
        entry = self.doc("entry.json")
        self.assertEqual(self.pick(entry, ["symbol", "side", "style", "setup", "fx_day", "status", "level",
                                           "price", "triggered_at", "atr", "atr_interval", "rolls_applied"]),
                         {"symbol": "XAUUSD", "side": "buy", "style": "swing", "setup": "breakout_20d",
                          "fx_day": "2026-07-16", "status": "triggered", "level": 2418.4, "price": 2431.6,
                          "triggered_at": "2026-07-16T21:00:00Z", "atr": 28.054, "atr_interval": "1d",
                          "rolls_applied": {}})

        # the exit plan reads entry.json: buy at the ask, a stop and a target by hand
        self.step("2026-07-17 09:05", "fx-exit/exit.py", "plan", "--stop", "price:2400", "--target",
                  "price:2490", "--write",
                  has=["PLAN  XAUUSD BUY  swing  20-day high breakout",
                       "Entry   2,433.49  buy at the market (ask = bid 2,433.40 + spread 0.09)",
                       "33.49 (3,349 points) under the entry", "56.51 (5,651 points) over the entry",
                       "Reward:risk  1.69 before costs, 0.84 after costs", "Wrote fx/plan.json."])
        plan = self.doc("plan.json")
        self.assertEqual(self.pick(plan, ["symbol", "side", "style", "setup", "fx_day", "entry", "entry_type",
                                          "entry_at", "stop_loss", "take_profit", "risk_points", "reward_risk",
                                          "reward_risk_net", "exit_by", "flat_by_utc", "rolls_applied"]),
                         {"symbol": "XAUUSD", "side": "buy", "style": "swing", "setup": "breakout_20d",
                          "fx_day": "2026-07-17", "entry": 2433.49, "entry_type": "market",
                          "entry_at": "2026-07-16T21:00:00Z", "stop_loss": 2400.0, "take_profit": 2490.0,
                          "risk_points": 3349, "reward_risk": 1.69, "reward_risk_net": 0.84,
                          "exit_by": "2026-08-13T21:00:00Z", "flat_by_utc": None, "rolls_applied": {}})
        self.assertEqual((plan["costs"]["nights_est"], plan["costs"]["swap_est"]), (28, -1535.52))
        self.gold_size_and_yes(plan)

    # ---------------------------------------------------------------- 2. gold: replay to fill

    def gold_size_and_yes(self, plan):
        # the time machine reads plan.json: 10,000 replays of up to 20 FX days, seed 19
        self.step("2026-07-17 09:06", "fx-timemachine/timemachine.py", "run", "--write",
                  has=["Mode: swing. 10,000 replays of up to 20 FX days", "seed 19",
                       "target first   10.7 %", "stop first     72.8 %", "Wrote fx/timemachine.json."])
        tm = self.doc("timemachine.json")
        self.assertEqual(self.pick(tm, ["mode", "runs", "seed", "until", "verdict_key"]),
                         {"mode": "swing", "runs": 10000, "seed": 19, "until": "2026-08-13T21:00:00Z",
                          "verdict_key": "strong_stop"})
        self.assertEqual(tm["plan"], self.pick(plan, ["symbol", "side", "style", "entry", "stop_loss",
                                                      "take_profit", "fx_day"]))
        # measured on the first green build (seed 19), then frozen
        self.assertEqual(tm["odds"], {"target_first": 0.1067, "stop_first": 0.7275, "neither": 0.1658})

        # the budget reads plan.json and timemachine.json: 0.02 lots, $97.81 at the stop
        self.step("2026-07-17 09:07", "fx-budget/size.py", "--write",
                  has=["SIZED: 0.02 lots of XAUUSD (BUY, swing).", "0.02 lots",
                       "Max loss at the stop: $97.81, costs included.",
                       "price and spread  0.02 lots x 33.49 x 100 = $66.98",
                       "commission        $0.12 (both sides)",
                       "swap              $30.71 if held all 28 nights",
                       "in 10,000 replays the target came first 11%, the stop 73%, neither 17%.",
                       "Wrote 1 order to fx/orders.json."])
        orders = self.doc("orders.json")["orders"]
        self.assertEqual(len(orders), 1)
        gold = orders[0]
        self.assertEqual(self.pick(gold, ["symbol", "side", "intent", "lots", "type", "entry", "stop_loss",
                                          "take_profit", "expiry", "style", "position_id", "decided_by"]),
                         {"symbol": "XAUUSD", "side": "buy", "intent": "open", "lots": 0.02, "type": "market",
                          "entry": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0, "expiry": "gtc",
                          "style": "swing", "position_id": None, "decided_by": "risk_pct"})
        self.assertEqual(self.pick(gold["plan"], ["nights_est", "max_loss_usd", "costs_usd", "margin_usd",
                                                  "exit_by"]),
                         {"nights_est": 28, "max_loss_usd": 97.81, "costs_usd": 30.83, "margin_usd": 48.67,
                          "exit_by": "2026-08-13T21:00:00Z"})

        # fx-risk reads orders.json: PASS, for this exact order (its fingerprint)
        self.step("2026-07-17 09:08", "fx-risk/check.py",
                  has=["PASS #1 buy XAUUSD 0.02 lots: max loss $97.81 (limit $100.00), costs included.",
                       "A PASS is a rules check, not a yes."])
        self.check_verdict(gold, "PASS", 97.81, open_trades=0)

        # the ticket and the yes rules: the MetaTrader line comes only after a real yes
        self.step("2026-07-17 09:09", "fx-broker/ticket.py", "show",
                  has=["READY: fx-risk PASS for this exact order (checked 9:08 AM PKT). Waiting for your yes.",
                       "Type yes to approve this exact order. Anything else is a no."], hasnt=[MT])
        self.step("2026-07-17 09:10", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "maybe",
                  code=1, has=['Not recorded: only a reply that starts with "yes" counts'], hasnt=[MT])
        self.step("2026-07-17 09:10", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes, sell it",
                  code=1, has=["Not recorded: the reply says sell, but this order is a buy."], hasnt=[MT])
        # gold is a futures price on Yahoo: a yes needs the MetaTrader price in the same call
        self.step("2026-07-17 09:10", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes",
                  code=1, has=["Gold needs your MetaTrader price", "Nothing was recorded."], hasnt=[MT])
        self.assertFalse(os.path.exists(self.path("state/broker.json")), "a refused reply writes no record")
        self.assertFalse(os.path.exists(self.path("prices.json")), "a refused reply saves no broker price")
        self.step("2026-07-17 09:10", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes",
                  "--broker-price", "2429.15",
                  has=[MT, "Buy by Market", "Stop Loss 2395.75 > Take Profit 2485.75",
                       "Broker levels: entry 2,429.24, SL 2,395.75, TP 2,485.75",
                       "2,395.75", "2,485.75", "your broker's price"])
        records = self.doc("state/broker.json")["records"]
        self.assertEqual([r["status"] for r in records], ["yes"])
        self.assertEqual(self.pick(records[0], ["fingerprint", "reply", "symbol", "side", "lots",
                                                "broker_price", "paper"]),
                         {"fingerprint": fx_core.fingerprint(gold), "reply": "yes", "symbol": "XAUUSD",
                          "side": "buy", "lots": 0.02, "broker_price": 2429.24, "paper": True})
        broker = self.doc("prices.json")["broker"]["XAUUSD"]
        self.assertEqual(self.pick(broker, ["bid", "yahoo_bid", "k"]),
                         {"bid": 2429.15, "yahoo_bid": 2433.4, "k": 0.998253})
        self.gold_fill()

    def check_verdict(self, order, verdict, max_loss, open_trades):
        """state/risk.json holds one verdict, for this exact order (fx-broker matches the
        fingerprint), and a summary with the paper flag."""
        risk = self.doc("state/risk.json")
        self.assertEqual(len(risk["verdicts"]), 1)
        got = risk["verdicts"][0]
        self.assertEqual(self.pick(got, ["index", "fingerprint", "verdict", "symbol", "side", "intent", "lots",
                                         "max_loss_usd", "reasons"]),
                         {"index": 1, "fingerprint": fx_core.fingerprint(order), "verdict": verdict,
                          "symbol": order["symbol"], "side": order["side"], "intent": order["intent"],
                          "lots": order["lots"], "max_loss_usd": max_loss, "reasons": []})
        self.assertEqual(risk["checked_at"], risk["created_at"])
        self.assertEqual(self.pick(risk["summary"], ["passed", "blocked", "open_trades", "paper"]),
                         {"passed": 1, "blocked": 0, "open_trades": open_trades, "paper": True})

    def gold_fill(self):
        # the paper fill at the plan's ask: position XAUUSD-1, with the broker's SL and TP
        self.step("2026-07-17 09:11", "fx-broker/ticket.py", "result", "--index", "1", "--status", "filled",
                  has=["Paper fill: BUY 0.02 lots XAUUSD at 2,433.49. Position XAUUSD-1."])
        pf = self.doc("portfolio.json")
        self.assertEqual(self.pick(pf["account"], ["currency", "balance", "realized_usd", "paper"]),
                         {"currency": "USD", "balance": 10000.0, "realized_usd": 0.0, "paper": True})
        self.assertEqual([p["id"] for p in pf["positions"]], ["XAUUSD-1"])
        self.assertEqual(self.pick(pf["positions"][0], [
            "symbol", "side", "style", "lots", "paper", "entry", "fill_price", "stop_loss", "take_profit",
            "broker_stop_loss", "broker_take_profit", "max_loss_usd", "exit_by", "rolls_applied"]),
            {"symbol": "XAUUSD", "side": "buy", "style": "swing", "lots": 0.02, "paper": True, "entry": 2433.49,
             "fill_price": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0, "broker_stop_loss": 2395.75,
             "broker_take_profit": 2485.75, "max_loss_usd": 97.81, "exit_by": "2026-08-13T21:00:00Z",
             "rolls_applied": {}})
        self.assertEqual([r["status"] for r in self.doc("state/broker.json")["records"]], ["yes", "filled"])

        # the alerts read plan.json and portfolio.json: four rules for the open position
        self.step("2026-07-17 09:12", "fx-alerts/alerts.py", "add", "--from-plan",
                  has=["Added a1: XAUUSD stop 2,400.00 for your BUY XAUUSD-1 (it fires on the bid).",
                       "Added a2: XAUUSD target 2,490.00", "Added a3: XAUUSD exit by", "Added a4: XAUUSD Friday check"])
        rules = self.doc("alerts.json")["rules"]
        self.assertEqual([(r["id"], r["kind"], r["level"], r["position_id"]) for r in rules],
                         [("a1", "stop", 2400.0, "XAUUSD-1"), ("a2", "target", 2490.0, "XAUUSD-1"),
                          ("a3", "exit_by", None, "XAUUSD-1"), ("a4", "friday_close", None, "XAUUSD-1")])
        self.assertEqual((rules[2]["at_utc"], rules[3]["at_utc"]), ("2026-08-13T21:00:00Z", "2026-07-17T16:00:00Z"))

    # ---------------------------------------------------------------- 3. the Yahoo outage of Tue 07-21

    def gold_outage(self):
        # no Yahoo row from 02:00 to 12:00 UTC: the price is stale, so nothing can ring
        self.step("2026-07-21 15:00", "fx-alerts/alerts.py", "check", code=1,
                  has=["Nothing hit.", "stale", "8 h old",
                       "Missed: the Friday 12:00 PM New York check (Fri 07-17)."],
                  hasnt=["ALERT"])
        rules = dict((r["id"], r) for r in self.doc("alerts.json")["rules"])
        self.assertEqual(rules["a4"]["at_utc"], "2026-07-24T16:00:00Z", "the Friday check moves to the next Friday")
        self.assertEqual((rules["a1"]["level"], rules["a2"]["level"]), (2400.0, 2490.0))
        rang = self.doc("state/alerts.json")["rang"]
        self.assertEqual(sorted(rang), ["a4@2026-07-17"])
        self.assertTrue(rang["a4@2026-07-17"]["missed"])

    # ---------------------------------------------------------------- 4. EURUSD: the day SELL

    def day_sell(self):
        # the day scan after the London range: EURUSD closed under the range low
        self.step("2026-07-23 12:41", "fx-scanner/scan.py", "day",
                  has=["1 hit. Day scan, Thu 2026-07-23 12:41 PM PKT", "EURUSD", "1.08480",
                       "EURUSD  sell side  opening-range breakdown (a close below the range low):",
                       "Wrote fx/watchlist.json (day, 1 hit)."])
        wl = self.doc("watchlist.json")
        self.assertEqual(self.pick(wl, ["mode", "session", "fx_day", "failed"]),
                         {"mode": "day", "session": "london", "fx_day": "2026-07-23", "failed": []})
        self.assertEqual([(h["symbol"], h["side"], h["setup"], h["level"]) for h in wl["hits"]],
                         [("EURUSD", "sell", "or_breakdown", 1.0848)])
        rng = wl["hits"][0]["opening_range"]
        self.assertEqual(self.pick(rng, ["high", "low", "mid", "bars", "of"]),
                         {"high": 1.0864, "low": 1.0848, "mid": 1.0856, "bars": 3, "of": 3})

        self.step("2026-07-23 12:41", "fx-entry/entry.py", "check", "EURUSD", "--write",
                  has=["TRIGGERED 12:40 PM PKT at 1.08452", "Wrote fx/entry.json (triggered)."])
        entry = self.doc("entry.json")
        self.assertEqual(self.pick(entry, ["symbol", "side", "style", "setup", "status", "level", "price",
                                           "triggered_at", "atr_interval"]),
                         {"symbol": "EURUSD", "side": "sell", "style": "day", "setup": "or_breakdown",
                          "status": "triggered", "level": 1.0848, "price": 1.08452,
                          "triggered_at": "2026-07-23T07:40:00Z", "atr_interval": "5m"})
        self.assertEqual(entry["opening_range"], rng)

        # the plan: the stop is the range middle plus the spread, the target 2R
        self.step("2026-07-23 12:42", "fx-exit/exit.py", "plan", "--write",
                  has=["PLAN  EURUSD SELL  day", "opening-range middle 1.08560 + spread: 10.9 pips",
                       "Target  1.08234  2R", "Flat by Thu 11:30 PM PKT (2:30 PM New York)",
                       "A sell's stop and target fire on the ask"])
        plan = self.doc("plan.json")
        self.assertEqual(self.pick(plan, ["symbol", "side", "style", "entry", "stop_loss", "take_profit",
                                          "risk_points", "flat_by", "flat_by_utc", "exit_by", "entry_at"]),
                         {"symbol": "EURUSD", "side": "sell", "style": "day", "entry": 1.08452,
                          "stop_loss": 1.08561, "take_profit": 1.08234, "risk_points": 109, "flat_by": "23:30",
                          "flat_by_utc": "2026-07-23T18:30:00Z", "exit_by": None,
                          "entry_at": "2026-07-23T07:40:00Z"})
        self.assertEqual(plan["costs"]["nights_est"], 0)

        # a replay to the flat-by time, not saved: the gold replay stays in timemachine.json
        saved = self.doc("timemachine.json")
        self.step("2026-07-23 12:42", "fx-timemachine/timemachine.py", "run",
                  has=["seed", "flat-by", "Mode: day.", "130 five-minute bars, seed 19",
                       "Not saved."])
        self.assertEqual(self.doc("timemachine.json"), saved)

        # the budget: 0.86 lots; it sees the time machine file is for another plan
        self.step("2026-07-23 12:43", "fx-budget/size.py", "--write",
                  has=["SIZED: 0.86 lots of EURUSD (SELL, day).", "0.86 lots",
                       "Max loss at the stop: $98.90, costs included.",
                       "one more step (0.87 lots) would lose $100.05: over your $100.00",
                       "The time machine ran on an older plan. Not shown."])
        euro = self.doc("orders.json")["orders"]
        self.assertEqual(len(euro), 1)
        euro = euro[0]
        self.assertEqual(self.pick(euro, ["symbol", "side", "intent", "lots", "type", "entry", "stop_loss",
                                          "take_profit", "expiry", "style"]),
                         {"symbol": "EURUSD", "side": "sell", "intent": "open", "lots": 0.86, "type": "market",
                          "entry": 1.08452, "stop_loss": 1.08561, "take_profit": 1.08234,
                          "expiry": "2026-07-23T18:30:00Z", "style": "day"})
        self.assertEqual(self.pick(euro["plan"], ["max_loss_usd", "costs_usd", "margin_usd", "nights_est"]),
                         {"max_loss_usd": 98.9, "costs_usd": 5.16, "margin_usd": 932.69, "nights_est": 0})

        # fx-risk counts the open gold trade; the ticket, the yes, the fill
        self.step("2026-07-23 12:43", "fx-risk/check.py",
                  has=["PASS #1 sell EURUSD 0.86 lots: max loss $98.90 (limit $100.00), costs included."])
        self.check_verdict(euro, "PASS", 98.9, open_trades=1)
        self.step("2026-07-23 12:44", "fx-broker/ticket.py", "show", has=["READY"], hasnt=[MT])
        self.step("2026-07-23 12:44", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes",
                  has=[MT, "Sell by Market", "Stop Loss 1.08561 > Take Profit 1.08234", "1.08561", "1.08234"])
        self.step("2026-07-23 12:45", "fx-broker/ticket.py", "result", "--index", "1", "--status", "filled",
                  has=["Paper fill: SELL 0.86 lots EURUSD at 1.08452. Position EURUSD-1."])
        pf = self.doc("portfolio.json")
        held = dict((p["id"], p) for p in pf["positions"])
        self.assertEqual(sorted(held), ["EURUSD-1", "XAUUSD-1"])
        self.assertEqual(self.pick(held["EURUSD-1"], ["side", "style", "lots", "entry", "stop_loss",
                                                      "take_profit", "flat_by_utc", "paper", "fingerprint"]),
                         {"side": "sell", "style": "day", "lots": 0.86, "entry": 1.08452, "stop_loss": 1.08561,
                          "take_profit": 1.08234, "flat_by_utc": "2026-07-23T18:30:00Z", "paper": True,
                          "fingerprint": fx_core.fingerprint(euro)})

        self.step("2026-07-23 12:45", "fx-alerts/alerts.py", "add", "--from-plan",
                  has=["Added a5: EURUSD stop 1.08561 for your SELL EURUSD-1 (it fires on the ask"])
        rules = [r for r in self.doc("alerts.json")["rules"] if r["symbol"] == "EURUSD"]
        self.assertEqual([(r["id"], r["kind"], r["level"], r["position_id"]) for r in rules],
                         [("a5", "stop", 1.08561, "EURUSD-1"), ("a6", "target", 1.08234, "EURUSD-1"),
                          ("a7", "flat_by", None, "EURUSD-1")])

    # ---------------------------------------------------------------- 5. EURUSD: the target and the close

    def day_target_and_close(self):
        self.step("2026-07-23 14:00", "fx-alerts/alerts.py", "check", code=1,
                  has=["Nothing hit. 7 rules watched"], hasnt=["ALERT"])
        # a sell's target fires on the ask: the 10:15 UTC bar's low 1.08221 plus the spread
        self.step("2026-07-23 15:20", "fx-alerts/alerts.py", "check",
                  has=["ALERT EURUSD", "1.08234", "target",
                       "ALERT EURUSD target 1.08234: the ask fell to 1.08222 in the 3:15 PM PKT 5-minute bar."],
                  hasnt=["ALERT XAUUSD", "ALERT EURUSD stop"])
        rang = self.doc("state/alerts.json")["rang"]
        self.assertEqual(rang["a6"], {"at": "2026-07-23T10:15:00Z", "price": 1.08222, "fx_day": "2026-07-23"})
        self.assertNotIn("a5", rang)

        # the close order: the position's side (a SELL), a market order
        self.step("2026-07-23 15:21", "fx-broker/ticket.py", "close", "--position", "EURUSD-1",
                  has=["Close order for EURUSD-1 written to fx/orders.json"])
        orders = self.doc("orders.json")["orders"]
        self.assertEqual(len(orders), 1)
        close = orders[0]
        self.assertEqual(self.pick(close, ["symbol", "side", "intent", "lots", "type", "decided_by", "position_id"]),
                         {"symbol": "EURUSD", "side": "sell", "intent": "close", "lots": 0.86, "type": "market",
                          "decided_by": "close", "position_id": "EURUSD-1"})
        self.step("2026-07-23 15:21", "fx-risk/check.py",
                  has=["PASS #1 close EURUSD 0.86 lots", "closes your SELL position EURUSD-1."])
        self.check_verdict(close, "PASS", 0.0, open_trades=2)  # a close adds no risk

        # a PASS counts for 5 minutes: at 6 minutes the yes is refused, and fx-risk runs again
        self.step("2026-07-23 15:27", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes", code=1,
                  has=["The fx-risk check is 6 min old. A PASS counts for 5 minutes. Run fx-risk again."],
                  hasnt=[MT])
        self.assertEqual([r["status"] for r in self.doc("state/broker.json")["records"]][-1], "filled")
        self.step("2026-07-23 15:27", "fx-risk/check.py", has=["PASS #1 close EURUSD 0.86 lots"])
        self.step("2026-07-23 15:27", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes",
                  has=["Close", "Recorded your yes for order 1: close EURUSD-1 (your SELL of EURUSD, 0.86 lots).",
                       MT + " Toolbox > Trade > right click the EURUSD row > Close Position."])
        self.step("2026-07-23 15:27", "fx-broker/ticket.py", "result", "--index", "1", "--status", "filled",
                  "--fill", "1.08234",
                  has=["Paper close: EURUSD-1 SELL 0.86 lots at 1.08234 (target). Result +$182.32 after all costs.",
                       "Balance now $10,182.32 (paper)."])
        pf = self.doc("portfolio.json")
        self.assertEqual([p["id"] for p in pf["positions"]], ["XAUUSD-1"])
        self.assertEqual(len(pf["closed"]), 1)
        # $187.48 from the price (21.8 pips on 0.86 lots) less $5.16 commission
        self.assertEqual(self.pick(pf["closed"][0], ["id", "side", "entry", "exit_price", "why", "result_usd",
                                                     "costs_usd", "swap_usd", "paper"]),
                         {"id": "EURUSD-1", "side": "sell", "entry": 1.08452, "exit_price": 1.08234, "why": "target",
                          "result_usd": 182.32, "costs_usd": 5.16, "swap_usd": 0.0, "paper": True})
        self.assertEqual((pf["account"]["balance"], pf["account"]["realized_usd"]), (10182.32, 182.32))

    # ---------------------------------------------------------------- 6. the gold roll of Wed 07-29

    def gold_roll(self):
        # Yahoo's gold moves to the Dec contract: the plan levels move by the ratio, nothing rings
        self.step("2026-07-29 13:15", "fx-alerts/alerts.py", "check", code=1,
                  has=["Gold rolled on Yahoo (+0.90%)", "Nothing hit.", "(Fri 07-24).",
                       "keep the SL and TP you set in MetaTrader",
                       "Note: alerts a5, a6, a7 are off: EURUSD-1 is closed."],
                  hasnt=["ALERT"])
        rules = dict((r["id"], r) for r in self.doc("alerts.json")["rules"])
        self.assertEqual((rules["a1"]["level"], rules["a2"]["level"]), (2421.59, 2512.41))
        self.assertEqual([rules[k]["active"] for k in ("a5", "a6", "a7")], [False, False, False])
        self.assertAlmostEqual(rules["a2"]["rolls_applied"][ROLL_ID], ROLL_RATIO, places=9)
        gold = self.doc("portfolio.json")["positions"][0]
        self.assertEqual(self.pick(gold, ["id", "entry", "fill_price", "stop_loss", "take_profit",
                                          "broker_stop_loss", "broker_take_profit"]),
                         {"id": "XAUUSD-1", "entry": 2455.39, "fill_price": 2455.39, "stop_loss": 2421.59,
                          "take_profit": 2512.41, "broker_stop_loss": 2395.75, "broker_take_profit": 2485.75})
        self.assertEqual(sorted(gold["rolls_applied"]), [ROLL_ID])
        self.assertAlmostEqual(gold["rolls_applied"][ROLL_ID], ROLL_RATIO, places=9)
        # the ledger gets one rebase record; it is not a yes, so it has no fingerprint
        rebase = [r for r in self.doc("state/broker.json")["records"] if r["status"] == "rebase"]
        self.assertEqual(len(rebase), 1)
        self.assertEqual(self.pick(rebase[0], ["roll_id", "position_id", "fingerprint", "price"]),
                         {"roll_id": ROLL_ID, "position_id": "XAUUSD-1", "fingerprint": None, "price": 2421.59})

    # ---------------------------------------------------------------- 7. gold: the target and the close

    def gold_target_and_close(self):
        self.step("2026-07-30 19:45", "fx-alerts/alerts.py", "check",
                  has=["ALERT XAUUSD", "2,512.41",
                       "ALERT XAUUSD target 2,512.41: the bid reached 2,513.00 in the 7:35 PM PKT 5-minute bar."],
                  hasnt=["ALERT XAUUSD stop"])
        rang = self.doc("state/alerts.json")["rang"]
        self.assertEqual((rang["a2"]["at"], rang["a2"]["price"]), ("2026-07-30T14:35:00Z", 2513.0))

        self.step("2026-07-30 19:46", "fx-broker/ticket.py", "close", "--position", "XAUUSD-1",
                  has=["Close order for XAUUSD-1 written to fx/orders.json"],
                  hasnt=["Gold rolled on Yahoo"])  # fx-alerts already moved the position
        orders = self.doc("orders.json")["orders"]
        self.assertEqual(len(orders), 1)
        close = orders[0]
        self.assertEqual(self.pick(close, ["symbol", "side", "intent", "lots", "type", "decided_by", "position_id"]),
                         {"symbol": "XAUUSD", "side": "buy", "intent": "close", "lots": 0.02, "type": "market",
                          "decided_by": "close", "position_id": "XAUUSD-1"})
        self.step("2026-07-30 19:46", "fx-risk/check.py",
                  has=["PASS #1 close XAUUSD 0.02 lots", "closes your BUY position XAUUSD-1."])
        self.check_verdict(close, "PASS", 0.0, open_trades=1)
        # a close takes yes or "yes, close"; a yes that names a side is refused
        self.step("2026-07-30 19:47", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes, sell it",
                  code=1, has=["This order closes your BUY of XAUUSD. Reply yes or yes, close."], hasnt=[MT])
        self.step("2026-07-30 19:47", "fx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes",
                  has=["Close", "Recorded your yes for order 1: close XAUUSD-1 (your BUY of XAUUSD, 0.02 lots).",
                       MT + " Toolbox > Trade > right click the XAUUSD row > Close Position."])
        self.step("2026-07-30 19:47", "fx-broker/ticket.py", "result", "--index", "1", "--status", "filled",
                  "--fill", "2512.41",
                  has=["Paper close: XAUUSD-1 BUY 0.02 lots at 2,512.41 (target). Result +$99.66 after all costs.",
                       "Balance now $10,281.98 (paper)."])
        pf = self.doc("portfolio.json")
        self.assertEqual(pf["positions"], [])
        self.assertEqual([c["id"] for c in pf["closed"]], ["EURUSD-1", "XAUUSD-1"])
        gold = pf["closed"][1]
        # from the rebased entry: 57.02 x 0.02 lots x 100 = $114.04 before costs;
        # costs $14.38 = $0.12 commission + $14.26 swap (13 nights, Wednesdays count 3 times)
        self.assertEqual(self.pick(gold, ["side", "entry", "exit_price", "why", "result_usd", "costs_usd",
                                          "swap_usd", "paper"]),
                         {"side": "buy", "entry": 2455.39, "exit_price": 2512.41, "why": "target",
                          "result_usd": 99.66, "costs_usd": 14.38, "swap_usd": -14.26, "paper": True})
        self.assertAlmostEqual(gold["result_usd"] + gold["costs_usd"], 114.04, places=2)
        # the final paper balance: $10,000 + $182.32 + $99.66
        self.assertEqual(self.pick(pf["account"], ["balance", "start_balance", "realized_usd", "paper"]),
                         {"balance": 10281.98, "start_balance": 10000.0, "realized_usd": 281.98, "paper": True})

        # the log: each yes, each fill, each close, and the rebase, in time order
        out = self.step("2026-07-30 19:48", "fx-broker/ticket.py", "log",
                        has=["Fri 17 Jul 9:10 AM PKT  yes  You said yes to BUY 0.02 lots XAUUSD.",
                             "Fri 17 Jul 9:11 AM PKT  filled  Paper fill: BUY 0.02 lots XAUUSD at 2,433.49.",
                             "Thu 23 Jul 12:44 PM PKT  yes  You said yes to SELL 0.86 lots EURUSD.",
                             "Thu 23 Jul 12:45 PM PKT  filled  Paper fill: SELL 0.86 lots EURUSD at 1.08452.",
                             "closed  Paper close: EURUSD-1 SELL 0.86 lots at 1.08234.",
                             "Wed 29 Jul 1:15 PM PKT  rebase  Gold rolled on Yahoo (+0.90%).",
                             "Thu 30 Jul 7:47 PM PKT  closed  Paper close: XAUUSD-1 BUY 0.02 lots at 2,512.41."])
        words = [line.split("  ")[1] for line in out.splitlines() if line.count("  ") >= 2]
        self.assertEqual(words, ["yes", "filled", "yes", "filled", "yes", "closed", "rebase", "yes", "closed"])
        statuses = [r["status"] for r in self.doc("state/broker.json")["records"]]
        self.assertEqual(statuses, words)
        self.assertTrue(all(r["paper"] for r in self.doc("state/broker.json")["records"]))


if __name__ == "__main__":
    unittest.main()
