"""Tests for fx-calendar/events.py (the skill spec section 10). Offline only: every run
passes --offline or --now, on a temp copy of the made-up sample market."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest

import _load
import fx_core

events = _load.load("fx-calendar", "events")

ROOT = _load.ROOT
MARKET = os.path.join(_load.FIX, "fx-market")
SCRIPT = os.path.join(ROOT, "fx-calendar", "events.py")
CHAIN_NOW = "2026-07-17 09:01"
ECB_NOW = "2026-07-23 17:00"

# The values marked (m) in the skill spec, measured on the first green build (frozen).
M_WEEK_0712_HEAD = "Week of Sun 12 Jul (New York): 3 High and 2 Medium events."
M_WEEK_0719_HEAD = "Week of Sun 19 Jul (New York): 4 High and 7 Medium events, and 1 holiday."
M_SAVED_0712 = "Offline: the saved calendar from Mon 13 Jul is used."
M_SAVED_0719 = "Offline: the saved calendar from Mon 20 Jul is used."
M_CPI_FORECAST = "    forecast 0.2%, last 0.3% (past)"
M_ECB_FORECAST = "    forecast 2.15%, last 2.15%"
M_0719_EVENTS = 24
M_0719_AGE_MIN = 5220

WATCHLIST = {"mode": "day", "session": "london",
             "hits": [{"symbol": "EURUSD", "side": "sell", "level": 1.0848}]}
PLAN = {"symbol": "EURUSD", "side": "sell", "style": "day", "entry": 1.0847,
        "entry_at": "2026-07-23T07:40:00Z", "flat_by_utc": "2026-07-23T18:30:00Z", "exit_by": None}
GOLD = {"id": "XAUUSD-1", "symbol": "XAUUSD", "side": "buy", "style": "swing", "entry": 2433.49,
        "opened_at": "2026-07-17T04:10:00Z", "flat_by_utc": None, "exit_by": "2026-08-13T21:00:00Z"}

def flat(text):
    """The text with every run of spaces and line breaks made one space (unwraps lines)."""
    return " ".join(text.split())


ALL_OUTPUT = []   # every words printout of these tests (test_no_price_words reads it too)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fxcal-")
        self.fx = os.path.join(self.tmp, "fx")
        os.makedirs(os.path.join(self.fx, "data"))
        shutil.copytree(os.path.join(MARKET, "data", "calendar"),
                        os.path.join(self.fx, "data", "calendar"))
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(self.fx, "config.json"))
        self.config = os.path.join(self.fx, "config.json")
        self.data = os.path.join(self.fx, "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, doc):
        with open(os.path.join(self.fx, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def run_cal(self, *args):
        """events.main with the temp paths: (code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        argv = list(args) + ["--config", self.config, "--data", self.data]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = events.main(argv)
        ALL_OUTPUT.append(out.getvalue())
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args):
        code, out, err = self.run_cal(*args)
        self.assertEqual(code, 0, err)
        return out

    def json_of(self, *args):
        return json.loads(self.ok(*(list(args) + ["--json"])))

    def chain_files(self, plan=True, gold=True):
        self.put("watchlist.json", WATCHLIST)
        if plan:
            self.put("plan.json", PLAN)
        self.put("portfolio.json", {"account": {"currency": "USD"},
                                    "positions": [GOLD] if gold else []})


class Week(Base):
    def test_week_chain_step(self):
        out = self.ok("week", "--offline", "--now", CHAIN_NOW)
        lines = out.split("\n")
        self.assertEqual(lines[0], M_WEEK_0712_HEAD)
        self.assertEqual(lines[1], M_SAVED_0712)
        self.assertEqual(lines[2], "Shown: High and Medium, and holidays. Times: Karachi, then New York.")
        for text in ("CPI m/m", "PKT", "New York", "(past)", "Offline: the saved calendar from",
                     events.NO_RESULTS, "Next: python3 fx-calendar/events.py next",
                     "Market days: no holiday or early close this week."):
            self.assertIn(text, out)
        i = lines.index("Tue 14 Jul 5:30 PM PKT (8:30 AM New York)  USD  High  CPI m/m")
        self.assertEqual(lines[i + 1], M_CPI_FORECAST)
        self.assertIn("Wed 15 Jul 7:30 PM PKT (10:30 AM New York)  USD  High for oil  Crude Oil\n"
                      "    Inventories (built-in table) (past)", out)
        self.assertIn("Thu 16 Jul 7:30 PM PKT (10:30 AM New York)  USD  High for gas  Natural Gas\n"
                      "    Storage (built-in table) (past)", out)
        self.assertNotIn("COT report", out)          # Low: only with --impact all
        self.assertTrue(all(len(x) <= 78 for x in lines), out)

    def test_week_eia_counts_high_for_oil(self):
        out = self.ok("week", "--offline", "--now", "2026-07-23 12:41")
        self.assertEqual(out.split("\n")[0], M_WEEK_0719_HEAD)
        # the feed row (Medium in the feed) is shown, not the built-in one
        self.assertIn("Wed 22 Jul 7:30 PM PKT (10:30 AM New York)  USD  High for oil  Crude Oil\n"
                      "    Inventories  forecast -1.2M, last 2.4M (past)", out)
        self.assertEqual(out.count("Crude Oil"), 1)
        self.assertIn("Mon 20 Jul  JPY  Holiday: Bank Holiday. Few traders. (past)", out)

    def test_week_impact_all_shows_cot(self):
        out = self.ok("week", "--impact", "all", "--offline", "--now", CHAIN_NOW)
        self.assertIn("COT report (CFTC)", out)
        self.assertIn("(built-in table)", out)
        self.assertIn("Sat 18 Jul 12:30 AM PKT (3:30 PM New York)  USD  Low  COT report (CFTC)", out)
        self.assertIn("Shown: High, Medium and Low, and holidays.", out)
        self.assertIn("German WPI m/m", out)

    def test_week_impact_any_case(self):
        out = self.ok("week", "--impact", "hIGH", "--offline", "--now", CHAIN_NOW)
        self.assertIn("CPI m/m", out)
        self.assertNotIn("Core CPI m/m", out)
        self.assertIn("Shown: High, and holidays.", out)

    def test_week_symbols_filter(self):
        out = self.ok("week", "--symbols", "eurgbp", "--offline", "--now", CHAIN_NOW)
        self.assertNotIn("CPI m/m", out)
        self.assertNotIn("Crude Oil", out)
        self.assertIn("ZEW Economic Sentiment", out)
        self.assertIn("Only events that touch EURGBP.", out)
        oil = self.ok("week", "--symbols", "USOIL", "--offline", "--now", CHAIN_NOW)
        self.assertIn("High for oil", oil)
        self.assertNotIn("Natural Gas", oil)

    def test_week_holiday_notes(self):
        out = self.ok("week", "--offline", "--now", "2026-07-01 12:00")
        self.assertIn("Offline: there is no saved calendar for this week\n"
                      "(fx/data/calendar/2026-06-28.json).", out)
        days = out.split("Market days:\n")[1]
        self.assertIn("Fri 3 Jul", days)
        self.assertIn("stops early", days)
        self.assertIn("Your broker's own notice wins", days)
        doc = self.json_of("week", "--offline", "--now", "2026-07-01 12:00")
        notes = [d["note"] for d in doc["market_days"]]
        self.assertTrue(all(d["date"] == "2026-07-03" for d in doc["market_days"]))
        tails = [n for n in notes if n.endswith(events.OWN_NOTICE)]
        self.assertEqual(len(tails), 1)                # the table day (gold and oil)
        self.assertIn("Gold and oil stops early", tails[0])
        self.assertTrue(any("Early close (your config)" in n and events.OWN_NOTICE not in n
                            for n in notes))

    def test_past_friday_note(self):
        out = self.ok("week", "--offline", "--now", "2026-07-18 12:00")
        self.assertIn("The calendar only covers this week. Next week's news comes on Sunday.", out)
        after = out.split("Next week's news comes on Sunday.")[1]
        self.assertIn("Wed 22 Jul 7:30 PM PKT (10:30 AM New York)  USD  High for oil", after)
        self.assertNotIn("Fed rate decision", out)     # 07-29 is two weeks out
        self.assertNotIn("COT report", after)
        every = self.ok("week", "--impact", "all", "--offline", "--now", "2026-07-18 12:00")
        after = every.split("Next week's news comes on Sunday.")[1]
        self.assertIn("Sat 25 Jul 12:30 AM PKT (3:30 PM New York)  USD  Low  COT report (CFTC)", after)
        doc = self.json_of("week", "--offline", "--now", "2026-07-18 12:00")
        self.assertEqual(doc["week"], "2026-07-12")
        self.assertTrue(doc["next_week"])
        self.assertTrue(all(e["source"] == "built-in" for e in doc["next_week"]))
        friday = self.json_of("week", "--offline", "--now", CHAIN_NOW)
        self.assertEqual(friday["next_week"], [])

    def test_feed_off(self):
        with open(self.config) as fh:
            cfg = json.load(fh)
        cfg["calendar_url"] = ""
        self.put("config.json", cfg)
        out = self.ok("week", "--offline", "--now", CHAIN_NOW)
        self.assertIn("Calendar feed is off (calendar_url is empty). Only built-in dates are shown.", out)
        self.assertNotIn("CPI m/m", out)
        doc = self.json_of("week", "--offline", "--now", CHAIN_NOW)
        self.assertTrue(doc["feed_off"])
        self.assertTrue(doc["events"])
        self.assertTrue(all(e["source"] == "built-in" for e in doc["events"]))

    def test_no_saved_file(self):
        os.remove(os.path.join(self.data, "calendar", "2026-07-19.json"))
        out = self.ok("week", "--offline", "--now", "2026-07-23 12:41")
        self.assertIn("Offline: there is no saved calendar for this week", out)
        self.assertIn(events.BUILT_IN_ONLY, flat(out))
        self.assertNotIn("Main Refinancing Rate", out)
        doc = self.json_of("week", "--offline", "--now", "2026-07-23 12:41")
        self.assertTrue(all(e["source"] == "built-in" for e in doc["events"]))
        self.assertIsNone(doc["calendar"]["fetched_at"])

    def test_week_quiet_flag(self):
        out = self.ok("week", "--offline", "--now", CHAIN_NOW, "--quiet")
        self.assertEqual(out.split("\n")[0], M_WEEK_0712_HEAD)
        self.assertIn("CPI m/m", out)
        for text in ("Offline:", "Shown:", events.NO_RESULTS, "Next:", "Market days"):
            self.assertNotIn(text, out)


class Next(Base):
    def test_next_from_files(self):
        self.chain_files()
        out = self.ok("next", "--offline", "--now", "2026-07-23 12:42")
        lines = out.split("\n")
        self.assertEqual(lines[0], "Next High events for EURUSD, XAUUSD (from your watchlist, plan and open")
        self.assertEqual(lines[1], "trades): 3 found.")
        self.assertEqual(lines[2], M_SAVED_0719)
        self.assertIn("Thu 23 Jul 5:15 PM PKT (8:15 AM New York)  EUR  High  Main Refinancing Rate\n"
                      + M_ECB_FORECAST + "\n    for EURUSD\n", out)
        self.assertIn("Thu 23 Jul 5:45 PM PKT (8:45 AM New York)  EUR  High  ECB Press Conference\n"
                      "    for EURUSD\n", out)
        self.assertIn("Wed 29 Jul 11:00 PM PKT (2:00 PM New York)  USD  High  Fed rate decision\n"
                      "    (FOMC) (built-in table)\n    for EURUSD, XAUUSD\n", out)
        self.assertIn("Inside your open trade XAUUSD-1 (BUY XAUUSD, swing, to Fri 14 Aug 2:00 AM PKT\n"
                      "(5:00 PM New York)): 1 High event.\n"
                      "  Wed 29 Jul 11:00 PM PKT (2:00 PM New York)  USD  High  Fed rate decision\n"
                      "    (FOMC) (built-in table)\n", out)
        self.assertIn("Only this week (to Sat 25 Jul) comes from the calendar site.", out)
        self.assertIn("Inside your plan (SELL EURUSD, day, to 11:30 PM PKT (2:30 PM New York) today):\n"
                      "2 High events.\n"
                      "  Thu 23 Jul 5:15 PM PKT (8:15 AM New York)  EUR  High  Main Refinancing Rate\n"
                      "  Thu 23 Jul 5:45 PM PKT (8:45 AM New York)  EUR  High  ECB Press Conference\n", out)
        self.assertEqual(flat(out).count("Only this week"), 1)     # the day plan ends this week
        self.assertTrue(out.rstrip().endswith("Next: python3 fx-calendar/events.py quiet EURUSD"))
        self.assertTrue(all(len(x) <= 78 for x in lines), out)
        doc = self.json_of("next", "--offline", "--now", "2026-07-23 12:42")
        self.assertEqual(doc["symbols"], ["EURUSD", "XAUUSD"])
        self.assertEqual(doc["symbols_from"], "files")
        self.assertEqual(len(doc["events"]), 3)
        self.assertEqual(doc["events"][2]["for"], ["EURUSD", "XAUUSD"])
        self.assertEqual([t["what"] for t in doc["trades"]], ["position", "plan"])
        self.assertEqual(doc["trades"][0]["id"], "XAUUSD-1")
        self.assertIsNone(doc["trades"][1]["id"])
        self.assertEqual(doc["trades"][1]["from"], "2026-07-23T07:42:00Z")   # never before now
        self.assertEqual(doc["trades"][1]["to"], "2026-07-23T18:30:00Z")

    def test_next_skips_the_plan_of_an_open_trade(self):
        self.chain_files()
        twin = dict(PLAN, id="EURUSD-1", opened_at="2026-07-23T07:41:00Z")
        self.put("portfolio.json", {"positions": [GOLD, twin]})
        out = self.ok("next", "--offline", "--now", "2026-07-23 12:42")
        self.assertNotIn("Inside your plan", out)
        self.assertIn("Inside your open trade EURUSD-1 (SELL EURUSD, day, to 11:30 PM PKT", out)
        self.assertEqual(out.count("Inside your"), 2)
        doc = self.json_of("next", "--offline", "--now", "2026-07-23 12:42")
        self.assertEqual([t["what"] for t in doc["trades"]], ["position", "position"])

    def test_next_past_trade_gets_no_block(self):
        self.chain_files(gold=False)
        out = self.ok("next", "--offline", "--now", "2026-07-24 00:00")   # after flat-by 23:30 PKT
        self.assertNotIn("Inside your", out)

    def test_next_universe_fallback(self):
        out = self.ok("next", "--offline", "--now", "2026-07-23 12:42")
        self.assertIn("No watchlist, plan or open trades. Showing events for your whole universe "
                      "(20 instruments).", flat(out))
        self.assertTrue(out.startswith("Next High events for your universe (20 instruments): "))
        doc = self.json_of("next", "--offline", "--now", "2026-07-23 12:42")
        self.assertEqual(doc["symbols_from"], "universe")
        self.assertEqual(doc["trades"], [])

    def test_next_flag_count_and_impact(self):
        out = self.ok("next", "--symbols", "XAUUSD", "--impact", "High,Medium", "--count", "2",
                      "--offline", "--now", "2026-07-23 12:42")
        self.assertTrue(out.startswith("Next High or Medium events for XAUUSD: 2 found."), out)
        self.assertIn("Unemployment Claims", out)
        doc = self.json_of("next", "--symbols", "XAUUSD", "--count", "50", "--offline",
                           "--now", "2026-07-23 12:42")
        self.assertEqual(doc["symbols_from"], "flag")
        self.assertEqual(doc["impacts"], ["High"])
        self.assertEqual([e["title"] for e in doc["events"]], ["Fed rate decision (FOMC)"])

    def test_next_oil_counts_eia(self):
        out = self.ok("next", "--symbols", "USOIL", "--offline", "--now", "2026-07-22 12:00")
        self.assertIn("High for oil  Crude Oil", out)


class Quiet(Base):
    def test_quiet_inside(self):
        out = self.ok("quiet", "EURUSD", "--offline", "--now", ECB_NOW)
        lines = out.split("\n")
        self.assertTrue(lines[0].startswith("QUIET: EURUSD"))
        self.assertEqual(flat("\n".join(lines[:2])), "QUIET: EURUSD is in a no-new-trade window now, to "
                         "5:45 PM PKT (8:45 AM New York): Main Refinancing Rate (EUR) at 5:15 PM PKT.")
        self.assertEqual(lines[2], M_SAVED_0719)
        wins = [x for x in lines if "No new trades from" in x]
        self.assertEqual(wins, [
            "Thu 23 Jul  No new trades from 4:45 PM to 5:45 PM PKT (7:45 AM to 8:45 AM New",
            "Thu 23 Jul  No new trades from 5:15 PM to 6:15 PM PKT (8:15 AM to 9:15 AM New",
            "Wed 29 Jul  No new trades from 10:30 PM to Thu 12:00 AM PKT (1:30 PM to 3:00"])
        names = [x.strip() for x in lines if x.startswith("    ")]
        self.assertEqual(names[:3], ["Main Refinancing Rate (EUR) at 5:15 PM PKT.",
                                     "ECB Press Conference (EUR) at 5:45 PM PKT.",
                                     "Fed rate decision (FOMC) at 11:00 PM PKT."])
        self.assertIn(flat(events.QUIET_RULE), flat(out))
        self.assertIn("Note: only this week (to Sat 25 Jul) comes from the calendar site.", flat(out))
        self.assertTrue(all(len(x) <= 78 for x in lines), out)

    def test_quiet_outside(self):
        out = self.ok("quiet", "eurusd", "--offline", "--now", "2026-07-23 12:41")
        self.assertEqual(flat(out.split("Offline:")[0]),
                         "Not quiet: EURUSD has no news window now. The next one starts "
                         "Thu 23 Jul 4:45 PM PKT (7:45 AM New York).")

    def test_quiet_none_in_days(self):
        out = self.ok("quiet", "EURUSD", "--days", "1", "--offline", "--now", "2026-07-24 12:00")
        self.assertTrue(out.startswith("Not quiet: EURUSD has no news window now, and none in the "
                                       "next 1 day."), out)
        self.assertNotIn("Windows for", out)
        self.assertNotIn("Only this week", flat(out))

    def test_quiet_oil_on_eia(self):
        out = self.ok("quiet", "USOIL", "--offline", "--now", "2026-07-22 19:30")
        self.assertTrue(out.startswith("QUIET: USOIL"), out)
        self.assertIn("Wed 22 Jul  No new trades from 7:00 PM to 8:00 PM PKT", out)
        self.assertIn("    Crude Oil Inventories (USD) at 7:30 PM PKT.", out)

    def test_quiet_none_for_eurgbp_on_cpi(self):
        out = self.ok("quiet", "EURGBP", "--offline", "--now", "2026-07-14 17:30")
        self.assertTrue(out.startswith("Not quiet"), out)
        usd = self.ok("quiet", "EURUSD", "--offline", "--now", "2026-07-14 17:30")
        self.assertTrue(usd.startswith("QUIET: EURUSD"), usd)
        self.assertIn("CPI m/m (USD) at 5:30 PM PKT.", flat(usd))

    def test_quiet_holiday_note(self):
        out = self.ok("quiet", "USDJPY", "--offline", "--now", "2026-07-19 12:00")
        self.assertIn("Note: Mon 20 Jul: holiday: Bank Holiday (JPY). Few traders.", flat(out))
        doc = self.json_of("quiet", "USDJPY", "--offline", "--now", "2026-07-19 12:00")
        self.assertEqual([e["title"] for e in doc["holidays"]], ["Bank Holiday"])

    def test_quiet_flag(self):
        out = self.ok("quiet", "EURUSD", "--offline", "--now", ECB_NOW, "--quiet")
        self.assertNotIn("Note:", out)
        self.assertNotIn("Offline:", out)
        self.assertIn("No new trades from", out)


ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
EVENT_KEYS = {"title", "country", "at", "end", "kind", "impact", "forecast", "previous", "source",
              "symbols", "all_day", "provisional", "past", "impact_word"}


class Json(Base):
    def check_event(self, e):
        self.assertTrue(EVENT_KEYS <= set(e), e)
        self.assertRegex(e["at"], ISO_Z)
        if e["end"] is not None:
            self.assertRegex(e["end"], ISO_Z)

    def common(self, doc, cmd):
        for key in ("v", "tool", "cmd", "now", "ok", "notes", "doc", "calendar", "offline", "feed_off"):
            self.assertIn(key, doc)
        self.assertEqual(doc["v"], 1)
        self.assertEqual(doc["tool"], "fx-calendar")
        self.assertEqual(doc["cmd"], cmd)
        self.assertIsNone(doc["doc"])
        self.assertTrue(doc["ok"])
        self.assertTrue(doc["offline"])
        self.assertRegex(doc["now"], ISO_Z)
        self.assertEqual(set(doc["calendar"]), {"week", "ok", "fetched_at", "tried_at", "age_min",
                                                "error", "events"})
        self.assertTrue(all(isinstance(n, str) for n in doc["notes"]))

    def test_json_shapes(self):
        doc = self.json_of("week", "--offline", "--now", CHAIN_NOW)
        self.common(doc, "week")
        self.assertEqual(doc["week"], "2026-07-12")
        self.assertEqual(doc["span"], ["2026-07-12T04:00:00Z", "2026-07-19T04:00:00Z"])
        self.assertEqual(doc["impacts"], ["High", "Medium"])
        self.assertIsNone(doc["symbols"])
        self.assertEqual(doc["market_days"], [])
        for e in doc["events"]:
            self.check_event(e)
        self.assertEqual(doc["notes"][0], M_WEEK_0712_HEAD)
        self.assertIn(events.NO_RESULTS, doc["notes"])
        self.assertFalse(any("CPI m/m" in n for n in doc["notes"]))   # event lines are not notes

        self.chain_files()
        doc = self.json_of("next", "--offline", "--now", "2026-07-23 12:42")
        self.common(doc, "next")
        for t in doc["trades"]:
            self.assertEqual(set(t), {"what", "id", "symbol", "side", "style", "from", "to", "events"})
            self.assertRegex(t["from"], ISO_Z)
            self.assertRegex(t["to"], ISO_Z)
            for e in t["events"]:
                self.check_event(e)
        for e in doc["events"]:
            self.check_event(e)
            self.assertIn("for", e)

        doc = self.json_of("quiet", "EURUSD", "--offline", "--now", ECB_NOW)
        self.common(doc, "quiet")
        self.assertEqual(doc["now"], "2026-07-23T12:00:00Z")
        self.assertEqual(doc["symbol"], "EURUSD")
        self.assertEqual(doc["days"], 7)
        self.assertEqual(doc["calendar"]["week"], "2026-07-19")
        self.assertEqual(doc["calendar"]["fetched_at"], "2026-07-19T21:00:00Z")
        self.assertEqual(doc["calendar"]["events"], M_0719_EVENTS)
        self.assertEqual(doc["calendar"]["age_min"], M_0719_AGE_MIN)
        q = doc["quiet_now"]
        self.assertEqual((q["start"], q["end"]), ("2026-07-23T11:45:00Z", "2026-07-23T12:45:00Z"))
        self.assertEqual(q["event"]["title"], "Main Refinancing Rate")
        self.assertEqual(q["event"]["symbols"], ["EURUSD", "EURJPY", "EURGBP", "EURCHF"])
        self.check_event(q["event"])
        self.assertEqual([(w["start"], w["end"]) for w in doc["windows"]], [
            ("2026-07-23T11:45:00Z", "2026-07-23T12:45:00Z"),
            ("2026-07-23T12:15:00Z", "2026-07-23T13:15:00Z"),
            ("2026-07-29T17:30:00Z", "2026-07-29T19:00:00Z")])
        self.assertEqual(doc["holidays"], [])
        out = self.json_of("quiet", "EURUSD", "--offline", "--now", "2026-07-23 12:41")
        self.assertIsNone(out["quiet_now"])


class Errors(Base):
    def bad(self, *args):
        code, out, err = self.run_cal(*args)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "")
        return err.strip()

    def test_errors(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = events.main([])
        self.assertEqual(code, 2)
        self.assertIn("week", out.getvalue())
        self.assertIn("quiet", out.getvalue())
        now = ["--offline", "--now", CHAIN_NOW]
        self.assertEqual(self.bad("week", "--impact", "big", *now),
                         'events.py: --impact wants High, Medium, Low or all (commas between), not "big".')
        self.assertEqual(self.bad("next", "--count", "0", *now),
                         "events.py: --count wants a whole number from 1 to 50, not 0.")
        self.assertEqual(self.bad("next", "--count", "51", *now),
                         "events.py: --count wants a whole number from 1 to 50, not 51.")
        self.assertEqual(self.bad("quiet", "EURUSD", "--days", "99", *now),
                         "events.py: --days wants a whole number from 1 to 35, not 99.")
        self.assertEqual(self.bad("quiet", "XAUSD", *now),
                         "events.py: XAUSD is not in your instruments. Did you mean XAUUSD?")
        self.assertEqual(self.bad("quiet", "EURUSD,GBPUSD", *now),
                         "events.py: quiet takes one symbol, like: quiet EURUSD")
        self.assertEqual(self.bad("week", "--symbols", ",", *now), "events.py: --symbols is empty.")
        self.assertEqual(self.bad("week", "--symbols", "QQQQQQ", *now),
                         "events.py: QQQQQQ is not in your instruments.")
        self.assertEqual(self.bad("week", "--now", "soon"),
                         'events.py: --now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)')

    def test_argparse_errors(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                events.main(["next", "--count", "many"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("invalid int value", err.getvalue())

    def test_bad_config(self):
        with open(self.config, "w") as fh:
            fh.write("{not json")
        self.assertIn("is not valid JSON", self.bad("week", "--offline"))
        os.remove(self.config)
        self.assertIn("no config file at", self.bad("week", "--offline"))

    def test_broken_chain_files(self):
        now = ["--offline", "--now", "2026-07-23 12:42"]
        self.put("watchlist.json", {"mode": "fast", "hits": []})
        self.assertTrue(self.bad("next", *now).endswith('watchlist.json: "mode" must be "swing" or "day"'))
        self.put("watchlist.json", {"mode": "day", "hits": {"symbol": "EURUSD"}})
        self.assertIn('"hits" must be a list of JSON objects', self.bad("next", *now))
        self.put("watchlist.json", {"mode": "day", "hits": [{"symbol": "EURUSX"}]})
        self.assertIn('"EURUSX" is not in your instrument list', self.bad("next", *now))
        self.put("watchlist.json", WATCHLIST)
        self.put("plan.json", dict(PLAN, side="short"))
        self.assertIn('side must be "buy" or "sell", not "short"', self.bad("next", *now))
        self.put("plan.json", dict(PLAN, style="scalp"))
        self.assertIn('style must be "swing" or "day", not "scalp"', self.bad("next", *now))
        self.put("plan.json", dict(PLAN, flat_by_utc=None))
        self.assertIn('plan.json: a day plan needs "flat_by_utc"', self.bad("next", *now))
        self.put("plan.json", dict(PLAN, entry_at="23 July"))
        self.assertIn('"entry_at" is not a UTC time like 2026-11-11T08:40:00Z', self.bad("next", *now))
        self.put("plan.json", [PLAN])
        self.assertIn("must be a JSON object", self.bad("next", *now))
        self.put("plan.json", PLAN)
        self.put("portfolio.json", {"positions": [GOLD, GOLD]})
        self.assertIn("two positions have the id XAUUSD-1. Fix it by hand or restore a backup.",
                      self.bad("next", *now))
        with open(os.path.join(self.fx, "portfolio.json"), "w") as fh:
            fh.write("")
        self.assertIn("portfolio.json is empty", self.bad("next", *now))

    def test_broken_calendar_file_is_read_as_missing(self):
        with open(os.path.join(self.data, "calendar", "2026-07-19.json"), "w") as fh:
            fh.write("{broken")
        code, out, err = self.run_cal("week", "--offline", "--now", "2026-07-23 12:41")
        self.assertEqual(code, 0, err)
        self.assertIn("Offline: there is no saved calendar for this week", out)


class Rules(Base):
    def test_now_means_offline(self):
        seen = []
        real = events.load_calendar

        def fake(data_dir, now, cfg, offline):
            seen.append(offline)
            return real(data_dir, now, cfg, offline)

        events.load_calendar = fake
        try:
            for argv in (["week"], ["next"], ["quiet", "EURUSD"]):
                code, out, err = self.run_cal(*(argv + ["--now", CHAIN_NOW]))
                self.assertEqual(code, 0, err)
        finally:
            events.load_calendar = real
        self.assertEqual(seen, [True, True, True])

    def test_skill_md_example_is_real_output(self):
        with open(os.path.join(ROOT, "fx-calendar", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        block = re.search(r"## Example\n.*?\n```\n(.*?)```", text, re.S).group(1)
        self.assertEqual(block, self.ok("quiet", "EURUSD", "--offline", "--now", ECB_NOW))

    def test_skill_md_shape(self):
        with open(os.path.join(ROOT, "fx-calendar", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        head = text.split("---")[1]
        self.assertIn("name: fx-calendar", head)
        desc = head.split("description: >-")[1].split("argument-hint")[0]
        self.assertGreater(len(" ".join(desc.split())), 200)
        self.assertNotIn("## Safety", text)
        self.assertEqual(text.rstrip().split("\n")[-1],
                         "Not financial advice. Nothing in this pack places an order.")
        heads = [x for x in text.split("\n") if x.startswith("## ")]
        self.assertEqual(heads, ["## What it reads and writes", "## The rules", "## Steps",
                                 "## Example", "## What it never does"])

    def test_no_price_words(self):
        # runs every command once more, then checks every printout of this file so far
        self.chain_files()
        for argv in (["week", "--impact", "all"], ["next"], ["quiet", "EURUSD"], ["quiet", "XAUUSD"]):
            for now in (CHAIN_NOW, ECB_NOW, "2026-07-18 12:00"):
                self.ok(*(argv + ["--offline", "--now", now]))
        self.assertGreater(len(ALL_OUTPUT), 12)
        for out in ALL_OUTPUT:
            low = out.lower()
            self.assertNotIn("$", out)
            self.assertNotIn("lots", low)
            self.assertNotIn("\u2014", out)
            for word in fx_core.SIGNAL_BANNED:
                self.assertNotIn(word, low)
        with open(SCRIPT, encoding="utf-8") as fh:
            self.assertNotIn("\u2014", fh.read())

    def test_status_lines(self):
        cfg = {"calendar_url": "https://example.invalid/x.json"}
        now = fx_core.parse_utc("2026-07-23T12:00:00Z")
        info = {"week": "2026-07-19", "ok": True, "fetched_at": "2026-07-23T11:46:00Z",
                "tried_at": "2026-07-23T11:46:00Z", "age_min": 14, "error": None, "events": 24}
        self.assertEqual(events.status_line(info, cfg, False, now), "Calendar: this week, updated 14 min ago.")
        self.assertEqual(events.status_line(dict(info, age_min=0), cfg, False, now),
                         "Calendar: this week, updated just now.")
        self.assertEqual(events.status_line(dict(info, ok=False, error="old week"), cfg, False, now),
                         "Calendar not updated. The calendar site still shows last week.")
        self.assertEqual(events.status_line(dict(info, ok=False, error="http 403", age_min=180), cfg, False, now),
                         "Calendar not updated (the site said no). Using the copy from 3 h ago.")
        self.assertEqual(events.status_line(dict(info, ok=False, error="network", events=0), cfg, False, now),
                         "Calendar site could not be read. " + events.BUILT_IN_ONLY)
        self.assertEqual(events.status_line(info, {"calendar_url": ""}, False, now),
                         "Calendar feed is off (calendar_url is empty). Only built-in dates are shown.")
        self.assertEqual(events.status_line(info, cfg, True, now),
                         "Offline: the saved calendar from Thu 23 Jul is used.")

    def test_small_helpers(self):
        self.assertEqual(events.parse_impacts("all"), ("High", "Medium", "Low"))
        self.assertEqual(events.parse_impacts("low,HIGH"), ("High", "Low"))
        self.assertIsNone(events.parse_impacts(None))
        self.assertEqual(events.day_month(fx_core.parse_utc("2026-11-11T00:00:00Z")), "Wed 11 Nov")
        self.assertEqual(events.when_text(fx_core.parse_utc("2026-11-12T17:00:00Z")),
                         "Thu 12 Nov 10:00 PM PKT (12:00 PM New York)")
        self.assertEqual(events.week_span("2026-11-08"), (fx_core.parse_utc("2026-11-08T05:00:00Z"),
                                                          fx_core.parse_utc("2026-11-15T05:00:00Z")))
        self.assertTrue(events.past_friday(fx_core.parse_utc("2026-07-17T21:00:00Z")))
        self.assertFalse(events.past_friday(fx_core.parse_utc("2026-07-17T20:59:00Z")))
        self.assertTrue(events.past_friday(fx_core.parse_utc("2026-07-19T03:59:00Z")))
        self.assertFalse(events.past_friday(fx_core.parse_utc("2026-07-19T04:00:00Z")))
        at = fx_core.parse_utc("2026-12-09T19:00:00Z")
        ev = fx_core.make_event("Fed rate decision (FOMC)", "USD", at, "cb", "High", provisional=True)
        line = events.event_line(ev, at, None)
        self.assertEqual(line, ["Thu 10 Dec 12:00 AM PKT (2:00 PM New York)  USD  High  Fed rate decision",
                                "    (FOMC) (expected date) (built-in table)"])
        self.assertEqual(events.event_name(ev), "Fed rate decision (FOMC)")
        self.assertEqual(events.forecast_part({"forecast": None, "previous": "0.6%"}), "last 0.6%")
        self.assertEqual(events.forecast_part({"forecast": "0.3%", "previous": None}), "forecast 0.3%")


class Chain(unittest.TestCase):
    def test_chain_step_command_line(self):
        tmp = tempfile.mkdtemp(prefix="fxcal-chain-")
        try:
            os.makedirs(os.path.join(tmp, "fx"))
            shutil.copytree(os.path.join(MARKET, "data", "calendar"), os.path.join(tmp, "fx", "data", "calendar"))
            shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(tmp, "fx", "config.json"))
            before = sorted(os.listdir(os.path.join(tmp, "fx", "data", "calendar")))
            res = subprocess.run([sys.executable, SCRIPT, "week", "--offline", "--now", CHAIN_NOW],
                                 cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
            for text in ("CPI m/m", "PKT", "New York"):
                self.assertIn(text, res.stdout)
            self.assertEqual(res.stdout.split("\n")[1], M_SAVED_0712)
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "fx", "data", "calendar"))), before)
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "fx"))), ["config.json", "data"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
