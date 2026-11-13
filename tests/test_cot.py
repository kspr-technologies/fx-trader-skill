"""Tests for fx-cot/cot.py (the COT Reader). Offline only: the CFTC is never asked.
cftc_get, SLEEP and COT_CLOCK are fakes; the CLI runs on the made-up sample market."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib
import csv
import datetime
import gzip
import io
import json
import shutil
import subprocess
import tempfile
import unittest

import _load

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MARKET = os.path.join(HERE, "fixtures", "fx-market")
SCRIPT = os.path.join(ROOT, "fx-cot", "cot.py")
cot = _load.load("fx-cot", "cot")
D = datetime.datetime
GOLD = "72hh-3qpy-088691"
EURO = "gpe5-46if-099741"

# (m) values measured on the sample market (first green build, 2026-11-11), then frozen
M_GOLD_0707_PCT = "23.5"       # net % of open interest, 07-07 report
M_GOLD_0707_INDEX = "14.8"     # COT index over 156 reports, 07-07 report
M_GOLD_0714 = ("97,200", "-800", "22.3", "13.2")    # net, change, %, index of 07-14
M_EURO_0714 = ("42,000", "-6,000", "6.8", "0.0")    # net short, change, %, index of 07-14


class FakeNet(object):
    """A stand-in for cftc_get, SLEEP and COT_CLOCK: answers from a list, records calls."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []
        self.sleeps = []
        self.t = 1000.0

    def get(self, url, accept):
        self.calls.append((url, accept))
        if self.answers:
            return self.answers.pop(0)
        return 0, {}, b""

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def clock(self):
        return self.t


class CotCase(unittest.TestCase):
    """A temp project: fx/data is a copy of the sample data, fx/config.json the sample config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fxcot-")
        self.fx = os.path.join(self.tmp, "fx")
        shutil.copytree(os.path.join(MARKET, "data"), os.path.join(self.fx, "data"))
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(self.fx, "config.json"))
        self.net = FakeNet()
        self._saved = (cot.cftc_get, cot.SLEEP, cot.COT_CLOCK, cot._cot_last_request)
        cot.cftc_get, cot.SLEEP, cot.COT_CLOCK = self.net.get, self.net.sleep, self.net.clock
        cot._cot_last_request = None

    def tearDown(self):
        cot.cftc_get, cot.SLEEP, cot.COT_CLOCK, cot._cot_last_request = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def has(self, part, whole, msg=None):
        """assertIn; for text, spaces and line breaks do not count (long lines are wrapped)."""
        if isinstance(part, str) and isinstance(whole, str):
            self.assertIn(" ".join(part.split()), " ".join(whole.split()), msg or whole)
        else:
            self.assertIn(part, whole, msg)

    def path(self, *parts):
        return os.path.join(self.fx, *parts)

    def write(self, rel, doc):
        p = self.path(*rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc))
        return p

    def read_bytes(self, rel):
        with open(self.path(*rel.split("/")), "rb") as fh:
            return fh.read()

    def state(self):
        with open(self.path("state", "cot.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def cli(self, *args):
        """Run cot.py as a subprocess in the temp project: (code, stdout, stderr)."""
        p = subprocess.run([sys.executable, SCRIPT] + list(args), cwd=self.tmp,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def main(self, *args):
        """Run cot.main in this process (the fakes are in place): (code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(self.tmp)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cot.main(list(args))
        finally:
            os.chdir(old)
        return code, out.getvalue(), err.getvalue()


class TestCli(CotCase):

    def test_chain_case_hides_the_unreleased_report(self):
        code, out, err = self.cli("show", "--symbols", "XAUUSD", "--now", "2026-07-17 09:01")
        self.assertEqual(code, 0, err)
        for text in ("2026-07-07", "98,000", "+6,200", "managed money", "buy side of XAUUSD",
                     "Sat 18 Jul 12:30 AM PKT", "COT for 1 instrument (from --symbols).",
                     "Out Sat 11 Jul 12:30 AM PKT (Fri 10 Jul 3:30 PM New York).",
                     "That is %s%% of all open contracts." % M_GOLD_0707_PCT,
                     "COT index %s of 100 over 156 reports" % M_GOLD_0707_INDEX,
                     "The report is 10 days old.",
                     "Replay at Fri 17 Jul 9:01 AM PKT: reports that came out later are not shown.",
                     "Source: CFTC Commitments of Traders (saved in fx/data/cot)."):
            self.has(text, out)
        self.assertNotIn("2026-07-14", out)
        self.assertNotIn("Expected date", out)

    def test_after_the_release(self):
        code, out, err = self.cli("show", "--symbols", "XAUUSD,EURUSD", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        for text in ("2026-07-14", "net short 42,000", "leveraged funds", "sell side of EURUSD",
                     "net long %s contracts, %s in the week." % M_GOLD_0714[:2],
                     "That is %s%%" % M_GOLD_0714[2], "COT index %s of 100" % M_GOLD_0714[3],
                     "net short %s contracts, %s in the week." % M_EURO_0714[:2],
                     "That is %s%%" % M_EURO_0714[2], "COT index %s of 100" % M_EURO_0714[3],
                     "The next COT report is due Sat 25 Jul 12:30 AM PKT (Fri 24 Jul 3:30 PM New York)."):
            self.has(text, out)

    def test_sign_turned_for_usdjpy(self):
        row = {"report_date_as_yyyy_mm_dd": "2026-07-14T00:00:00.000",
               "cftc_contract_market_code": "097741", "open_interest_all": "200000",
               "lev_money_positions_long": "50000", "lev_money_positions_short": "20000",
               "change_in_lev_money_long": "1000", "change_in_lev_money_short": "0"}
        self.write("data/cot/gpe5-46if-097741.json",
                   {"fetched_at": "2026-07-17T20:05:00Z", "dataset": "gpe5-46if", "code": "097741",
                    "market": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE", "rows": [row]})
        code, out, err = self.cli("show", "--symbols", "USDJPY", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        self.has("net long 30,000", out)
        self.has("Net long yen futures is the sell side of USDJPY.", out)
        self.has("The sign is turned: these futures are priced in dollars per yen.", out)
        self.has("Not enough reports for a COT index (1 of 26 needed).", out)

    def test_cross_has_two_legs(self):
        code, out, err = self.cli("show", "--symbols", "EURJPY", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        self.has("euro futures (CME), CFTC code 099741", out)
        self.has("Net short euro futures is the sell side of EURJPY.", out)
        self.has("Note: no COT data for yen futures yet (fx/data/cot/gpe5-46if-097741.json). "
                      "Run once without --offline or --now to get it.", out)
        self.has("A cross has no COT market of its own. Each currency is shown on its own.", out)

    def test_new_once_then_nothing(self):
        args = ("new", "--symbols", "XAUUSD", "--now", "2026-07-18 01:00")
        code, out, err = self.cli(*args)
        self.assertEqual(code, 0, err)
        self.has("NEW: gold futures (COMEX)", out)
        self.has("First run: showing the newest report of each market.", out)
        self.has("1 new COT report.", out)
        code, out, err = self.cli(*args)
        self.assertEqual(code, 1, err)
        self.has("Nothing new since Sat 18 Jul 1:00 AM PKT.", out)
        self.assertNotIn("NEW:", out)
        self.assertEqual(self.state()["seen"], {GOLD: "2026-07-14"})
        self.assertEqual(self.state()["last_check"], "2026-07-17T20:00:00Z")

    def test_new_two_markets_after_seen(self):
        self.write("state/cot.json", {"seen": {GOLD: "2026-07-07", EURO: "2026-07-07"},
                                      "last_check": "2026-07-17T04:01:00Z"})
        code, out, err = self.cli("new", "--symbols", "XAUUSD,EURUSD", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("2 new COT reports."), out)
        self.assertNotIn("First run", out)
        self.has("NEW: euro futures (CME), CFTC code 099741", out)

    def test_dry_run_keeps_state(self):
        before = self.write("state/cot.json", {"seen": {GOLD: "2026-07-07"},
                                               "last_check": "2026-07-17T04:01:00Z"})
        raw = self.read_bytes("state/cot.json")
        code, out, err = self.cli("new", "--symbols", "XAUUSD", "--now", "2026-07-18 01:00", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read_bytes("state/cot.json"), raw)
        os.remove(before)
        self.cli("show", "--symbols", "XAUUSD", "--now", "2026-07-18 01:00", "--dry-run")
        self.assertFalse(os.path.exists(before))

    def test_seen_never_goes_back(self):
        self.cli("new", "--symbols", "XAUUSD", "--now", "2026-07-18 01:00")
        code, out, err = self.cli("show", "--symbols", "XAUUSD", "--now", "2026-07-17 09:01")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["seen"][GOLD], "2026-07-14")
        code, out, err = self.cli("new", "--symbols", "XAUUSD", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 1, out)


def gold_rows(nets, start="2026-07-14", step=7, changes=True, oi=400000):
    """Made-up managed money rows, newest first: one row a net, step days apart."""
    d0 = datetime.date.fromisoformat(start)
    rows = []
    for i, net in enumerate(nets):
        row = {"report_date_as_yyyy_mm_dd": (d0 - datetime.timedelta(days=step * i)).isoformat()
               + "T00:00:00.000", "cftc_contract_market_code": "088691",
               "open_interest_all": str(oi), "m_money_positions_long_all": str(100000 + net),
               "m_money_positions_short_all": "100000"}
        if changes:
            row["change_in_m_money_long_all"] = "500"
            row["change_in_m_money_short_all"] = "200"
        rows.append(row)
    return rows


class TestHelpers(unittest.TestCase):

    def test_numbers(self):
        now = D(2026, 7, 18, 3, 0)
        info = cot.cot_numbers(gold_rows([1000, 900]), "72hh-3qpy", 156, now)
        self.assertEqual((info["net"], info["change"], info["long"]), (1000, 300, 101000))
        info = cot.cot_numbers(gold_rows([1000, 900], changes=False), "72hh-3qpy", 156, now)
        self.assertEqual(info["change"], 100)                     # from the row 7 days before
        info = cot.cot_numbers(gold_rows([1000, 900], step=10, changes=False), "72hh-3qpy", 156, now)
        self.assertIsNone(info["change"])                         # 10 days: not known
        info = cot.cot_numbers(gold_rows([1000], oi=8000), "72hh-3qpy", 156, now)
        self.assertEqual(info["net_pct_oi"], 12.5)
        info = cot.cot_numbers(gold_rows([1], oi=40), "72hh-3qpy", 156, now)
        self.assertEqual(info["net_pct_oi"], 2.5)                 # 2.5 exact; half up below
        info = cot.cot_numbers(gold_rows([1], oi=1600), "72hh-3qpy", 156, now)
        self.assertEqual(info["net_pct_oi"], 0.1)                 # 0.0625 -> 0.1
        info = cot.cot_numbers(gold_rows([1], oi=16), "72hh-3qpy", 156, now)
        self.assertEqual(info["net_pct_oi"], 6.3)                 # 6.25 -> 6.3 (half up)
        info = cot.cot_numbers(gold_rows([5] * 30), "72hh-3qpy", 156, now)
        self.assertEqual(info["cot_index"], 50.0)                 # flat: 50
        info = cot.cot_numbers(gold_rows([5] * 25), "72hh-3qpy", 156, now)
        self.assertIsNone(info["cot_index"])                      # under 26 reports
        self.assertEqual(info["index_reports"], 25)
        nets = [10] + [0] * 51 + [-1000] * 10                     # the low is outside 52 reports
        info = cot.cot_numbers(gold_rows(nets), "72hh-3qpy", 52, now)
        self.assertEqual((info["index_reports"], info["cot_index"], info["extreme"]), (52, 100.0, "high"))
        info = cot.cot_numbers(gold_rows(nets), "72hh-3qpy", 156, now)
        self.assertEqual(info["index_reports"], 62)
        self.assertEqual(info["cot_index"], 100.0)
        info = cot.cot_numbers(gold_rows([0] + [100] * 30), "72hh-3qpy", 156, now)
        self.assertEqual((info["cot_index"], info["extreme"]), (0.0, "low"))
        info = cot.cot_numbers(gold_rows([30] + [0] * 29 + [100]), "72hh-3qpy", 156, now)
        self.assertEqual((info["cot_index"], info["extreme"]), (30.0, None))
        self.assertIsNone(cot.cot_numbers([{"report_date_as_yyyy_mm_dd": "2026-07-14T00:00:00.000",
                                            "m_money_positions_long_all": "."}], "72hh-3qpy", 156, now))
        self.assertEqual(cot.num({"a": " 1,234 "}, "a"), 1234)
        self.assertIsNone(cot.num({"a": ""}, "a"))
        self.assertIsNone(cot.num({}, "a"))
        self.assertEqual(cot.count_text(6200, signed=True), "+6,200")
        self.assertEqual(cot.count_text(-1948, signed=True), "-1,948")
        self.assertEqual(cot.count_text(0, signed=True), "0")
        self.assertEqual(cot.count_text(98000), "98,000")

    def test_release_of(self):
        self.assertEqual(cot.release_of("2026-07-14"), (D(2026, 7, 17, 19, 30), False, True))
        self.assertEqual(cot.pkt_label(cot.release_of("2026-07-14")[0], date=True),
                         "Sat 18 Jul 12:30 AM PKT")
        self.assertEqual(cot.release_of("2026-06-30"), (D(2026, 7, 6, 19, 30), False, True))
        self.assertEqual(cot.release_of("2026-11-10"), (D(2026, 11, 16, 20, 30), False, True))
        self.assertEqual(cot.pkt_label(cot.release_of("2026-11-10")[0], date=True),
                         "Tue 17 Nov 1:30 AM PKT")
        self.assertEqual(cot.release_of("2027-11-09"), (D(2027, 11, 15, 20, 30), True, True))
        self.assertEqual(cot.release_of("2023-06-27"), (D(2023, 6, 30, 19, 30), True, False))
        self.assertEqual(cot.release_of("2028-02-01"), (D(2028, 2, 4, 20, 30), True, False))
        self.assertEqual(cot.next_release(D(2026, 7, 17, 4, 1)), (D(2026, 7, 17, 19, 30), False, True))
        self.assertEqual(cot.next_release(D(2028, 1, 5, 4, 1)), (D(2028, 1, 7, 20, 30), True, False))
        rows = gold_rows([1, 2, 3])
        self.assertEqual(len(cot.visible_rows(rows, D(2026, 7, 17, 4, 1), True)), 2)
        self.assertEqual(len(cot.visible_rows(rows, D(2026, 7, 17, 19, 30), True)), 3)
        self.assertEqual(len(cot.visible_rows(rows, D(2026, 7, 1), False)), 3)

    def test_legs_and_sides(self):
        cfg = cot.load_config(os.path.join(MARKET, "config.json"))
        legs = {k: cot.cot_legs(cot.instrument(cfg, k)) for k in cfg["universe"]}
        self.assertEqual(legs["XAUUSD"], [("XAU", 1)])
        self.assertEqual(legs["EURUSD"], [("EUR", 1)])
        for k in ("USDJPY", "USDCHF", "USDCAD"):
            self.assertEqual(len(legs[k]), 1)
            self.assertEqual(legs[k][0][1], -1)
        self.assertEqual(legs["EURJPY"], [("EUR", 1), ("JPY", -1)])
        self.assertEqual(legs["UKOIL"], [("BRENT", 1)])
        self.assertEqual(cot.cot_legs({"kind": "fx", "base": "USD", "quote": "SEK"}), [])
        self.assertEqual(cot.pair_side(5, 1), "buy")
        self.assertEqual(cot.pair_side(5, -1), "sell")
        self.assertEqual(cot.pair_side(-5, -1), "buy")
        self.assertIsNone(cot.pair_side(0, 1))


def text_line(dataset, date, code, values):
    """A made-up weekly text file line: '.' in every cell, then the given positions."""
    n = cot.TEXT_COLUMNS[dataset]["fields"]
    cells = ["."] * n
    cells[0] = "GOLD - COMMODITY EXCHANGE INC."
    cells[2] = date
    cells[3] = code
    for pos, v in values.items():
        cells[pos - 1] = v
    buf = io.StringIO()
    csv.writer(buf).writerow(cells)
    return buf.getvalue()


class TestFetch(CotCase):

    def cot_dir(self):
        return self.path("data", "cot")

    def api_answer(self, rows, gz=False):
        body = json.dumps(rows).encode("utf-8")
        heads = {"Last-Modified": "Fri, 17 Jul 2026 19:30:41 GMT"}
        if gz:
            body = gzip.compress(body)
            heads["Content-Encoding"] = "gzip"
        return 200, heads, body

    def test_fetch_rules(self):
        with open(os.path.join(self.cot_dir(), GOLD + ".json"), encoding="utf-8") as fh:
            old = json.load(fh)
        os.remove(os.path.join(self.cot_dir(), GOLD + ".json"))
        os.remove(os.path.join(self.cot_dir(), EURO + ".json"))
        euro = [dict(r, cftc_contract_market_code="099741") for r in gold_rows([1, 2])]
        self.net.answers = [self.api_answer(old["rows"], gz=True), self.api_answer(euro)]
        now = D(2026, 7, 18, 3, 0)
        markets = [("72hh-3qpy", "088691"), ("gpe5-46if", "099741")]
        got = cot.refresh_markets(self.cot_dir(), markets, now, False)
        self.assertEqual(len(self.net.calls), 2)                   # one call per dataset
        url, accept = self.net.calls[0]
        self.has("72hh-3qpy.json?", url)
        self.has("in%28%27088691%27%29", url)                 # in('088691')
        self.has("%24order=cftc_contract_market_code%2Creport_date_as_yyyy_mm_dd+DESC", url)
        self.has("%24limit=170", url)
        self.has("swap__positions_short_all", url)
        self.assertEqual(accept, "application/json")
        self.assertEqual(self.net.sleeps, [2.0])                   # the pace between the two
        doc = got[markets[0]][0]
        self.assertEqual((doc["source"], doc["error"], len(doc["rows"])), ("api", None, 160))
        self.assertEqual(doc["fetched_at"], "2026-07-18T03:00:00Z")
        self.assertEqual(doc["last_modified"], "Fri, 17 Jul 2026 19:30:41 GMT")
        self.assertEqual(doc["market"], "GOLD - COMMODITY EXCHANGE INC.")
        cot.refresh_markets(self.cot_dir(), markets, now + datetime.timedelta(minutes=30), False)
        self.assertEqual(len(self.net.calls), 2)                   # inside the hour: no call
        later = D(2026, 7, 25, 3, 0)                               # the 07-21 report is out
        self.net.answers = [(503, {}, b""), (0, {}, b"")]
        got = cot.refresh_markets(self.cot_dir(), markets[:1], later, False)
        self.assertEqual(len(self.net.calls), 4)                   # the API, then the text file once
        self.has("f_disagg.txt", self.net.calls[3][0])
        self.assertEqual(self.net.calls[3][1], "text/plain")
        doc, notes, why = got[markets[0]]
        self.assertEqual(len(doc["rows"]), 160)                    # old rows kept
        self.assertEqual((doc["tried_at"], doc["error"]), ("2026-07-25T03:00:00Z", "http 503"))
        self.assertEqual(doc["fetched_at"], "2026-07-18T03:00:00Z")
        self.assertEqual(notes, ["Note: the CFTC site did not answer (http 503). Showing the "
                                 "saved data (fetched Sat 18 Jul 8:00 AM PKT)."])
        got = cot.refresh_markets(self.cot_dir(), markets[:1], later + datetime.timedelta(minutes=10), False)
        self.assertEqual(len(self.net.calls), 4)                   # 1-hour rule after a failure
        self.assertEqual(got[markets[0]][1], ["Note: the CFTC site failed less than 1 hour ago. "
                                              "Showing the saved data."])

    def test_text_file_backup(self):
        line = text_line("72hh-3qpy", "2026-07-21", "088691", {8: "420000", 14: "130000", 15: "30000",
                                                               62: "1000", 63: "200"})
        old_line = text_line("72hh-3qpy", "2026-07-07", "088691", {14: "1", 15: "2"})
        self.net.answers = [(0, {}, b""), (200, {}, (line + old_line).encode("utf-8"))]
        now = D(2026, 7, 25, 3, 0)
        got = cot.refresh_markets(self.cot_dir(), [("72hh-3qpy", "088691")], now, False)
        doc, notes, why = got[("72hh-3qpy", "088691")]
        self.assertEqual(doc["rows"][0]["report_date_as_yyyy_mm_dd"], "2026-07-21T00:00:00.000")
        self.assertEqual(doc["rows"][1]["report_date_as_yyyy_mm_dd"], "2026-07-14T00:00:00.000")
        self.assertEqual(len(doc["rows"]), 160)
        self.assertEqual((doc["source"], doc["error"]), ("text", "network"))
        self.assertEqual(doc["fetched_at"], "2026-07-17T20:05:00Z")   # unchanged
        self.assertEqual(notes, ["Note: the CFTC data site did not answer (network). The newest "
                                 "week came from the CFTC text file."])

    def test_both_fail_with_no_file(self):
        os.remove(os.path.join(self.cot_dir(), GOLD + ".json"))
        self.net.answers = [(500, {}, b""), (500, {}, b"")]
        code, out, err = self.main("show", "--symbols", "XAUUSD")
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "cot.py: The CFTC site did not answer (http 500) and no COT "
                                      "data is saved yet. Try again later.")
        with open(os.path.join(self.cot_dir(), GOLD + ".json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["rows"], doc["error"]), ([], "http 500"))
        code, out, err = self.main("show", "--symbols", "XAUUSD")
        self.assertEqual(len(self.net.calls), 2)                   # no call inside the hour

    def test_cftc_get_headers_and_gzip(self):
        seen = {}

        class Resp(object):
            status = 200
            headers = {"content-encoding": "gzip", "last-modified": "X"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return gzip.compress(b"[]")

        def fake_open(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            seen["accept"] = req.get_header("Accept")
            seen["enc"] = req.get_header("Accept-encoding")
            seen["timeout"] = timeout
            return Resp()

        def broken_open(req, timeout=None):
            raise OSError("no network in tests")

        real = cot.urllib.request.urlopen
        cot.cftc_get = self._saved[0]
        try:
            cot.urllib.request.urlopen = fake_open
            status, heads, body = cot.cftc_get("https://example.com/x.json", "application/json")
            cot.urllib.request.urlopen = broken_open
            self.assertEqual(cot.cftc_get("https://example.com/x.json", "text/plain"), (0, {}, b""))
        finally:
            cot.urllib.request.urlopen = real
        self.assertEqual(seen, {"ua": "Mozilla/5.0 (compatible; fx-trader-skill/1.0)",
                                "accept": "application/json", "enc": "gzip", "timeout": 25})
        self.assertEqual(status, 200)
        self.assertEqual(cot._gunzip(body, heads), b"[]")
        self.assertEqual(heads["Last-Modified"], "X")

    def test_offline_and_now_never_fetch(self):
        os.remove(os.path.join(self.cot_dir(), GOLD + ".json"))
        code, out, err = self.main("new", "--all", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        code, out, err = self.main("show", "--symbols", "XAUUSD,EURUSD", "--offline")
        self.assertEqual(code, 0, err)
        self.has("Note: no COT data for gold futures yet", out)
        self.has("Source: CFTC Commitments of Traders (fetched Sat 18 Jul 1:05 AM PKT).", out)
        self.assertEqual(self.net.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.cot_dir(), GOLD + ".json")))

    def test_text_line(self):
        cells = next(csv.reader([text_line("72hh-3qpy", "2026-11-03", "088691",
                                           {8: "399169", 14: "122283", 15: "18669", 186: "(CONTRACTS)",
                                            191: "FutOnly"})]))
        row = cot.parse_text_line(cells, "72hh-3qpy")
        self.assertEqual(row["report_date_as_yyyy_mm_dd"], "2026-11-03T00:00:00.000")
        self.assertEqual(row["m_money_positions_long_all"], "122283")
        self.assertEqual(row["open_interest_all"], "399169")
        self.assertEqual(row["fx_from"], "weekly text file")
        self.assertEqual(row["futonly_or_combined"], "FutOnly")
        self.assertNotIn("change_in_m_money_long_all", row)       # "." left out
        cells = next(csv.reader([text_line("gpe5-46if", "2026-11-03", "099741",
                                           {15: " 71296 ", 16: "113296", 50: "18.5"})]))
        row = cot.parse_text_line(cells, "gpe5-46if")
        self.assertEqual((row["lev_money_positions_long"], row["pct_of_oi_lev_money_short"]),
                         ("71296", "18.5"))
        self.assertEqual(row["cftc_contract_market_code"], "099741")
        with self.assertRaises(ValueError) as ctx:
            cot.parse_text_line(cells[:80], "gpe5-46if")
        self.assertEqual(str(ctx.exception), "text layout")
        self.net.answers = [(0, {}, b""), (200, {}, b"a,b,c\n")]
        got = cot.refresh_markets(self.cot_dir(), [("gpe5-46if", "099741")], D(2026, 7, 25, 3, 0), False)
        self.assertEqual(got[("gpe5-46if", "099741")][0]["source"], "api")   # nothing newer
        self.assertEqual(cot.fetch_text("gpe5-46if", ["099741"]), (None, "network"))


class TestRefusals(CotCase):

    def test_missing_and_damaged(self):
        code, out, err = self.cli("show", "--symbols", "USOIL", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "cot.py: No COT data for USOIL yet (fx/data/cot/72hh-3qpy-067651.json). "
                                      "Run once without --offline or --now to get it.")
        self.write("data/cot/%s.json" % GOLD, {"rows": 5})
        self.write("state/cot.json", "{")
        code, out, err = self.cli("show", "--symbols", "XAUUSD,EURUSD", "--offline")
        self.assertEqual(code, 0, err)
        self.has("Note: fx/data/cot/72hh-3qpy-088691.json is damaged. Run once without "
                      "--offline or --now to get it again.", out)
        self.assertTrue(out.startswith("fx/state/cot.json was damaged. Started a new one."), out)
        broken = [n for n in os.listdir(self.path("state")) if n.startswith("cot.broken-")]
        self.assertEqual(len(broken), 1)
        self.assertRegex(broken[0], r"^cot\.broken-\d{8}T\d{6}Z\.json$")
        self.assertEqual(self.state()["seen"], {EURO: "2026-07-14"})
        self.write("data/cot/%s.json" % EURO, "not json")
        code, out, err = self.cli("show", "--symbols", "XAUUSD,EURUSD", "--offline")
        self.assertEqual(code, 2)
        self.has("cot.py: No COT data for XAUUSD yet (fx/data/cot/72hh-3qpy-088691.json).", err)

    def test_no_rows_and_no_market(self):
        self.write("data/cot/%s.json" % GOLD, {"rows": [{"report_date_as_yyyy_mm_dd": "2026-07-14T00:00:00.000"}]})
        code, out, err = self.cli("show", "--symbols", "XAUUSD,EURUSD", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        self.has("Note: the gold futures data has no big speculator numbers. Nothing to show.", out)
        with open(self.path("config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        row = dict(cot.BUILTIN_INSTRUMENTS["EURUSD"], base="SEK", name="Swedish krona / US dollar")
        cfg["instruments"]["SEKUSD"] = row
        cfg["universe"] = cfg["universe"] + ["SEKUSD"]
        self.write("config.json", cfg)
        code, out, err = self.cli("show", "--symbols", "SEKUSD", "--offline")
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "cot.py: There is no COT market for Swedish krona / US dollar in this "
                                      "pack. COT covers gold, silver, platinum, palladium, copper, oil, gas "
                                      "and the main currencies.")
        code, out, err = self.cli("show", "--symbols", "SEKUSD,EURUSD", "--offline")
        self.assertEqual(code, 0, err)
        self.has("Note: there is no COT market for Swedish krona / US dollar in this pack.", out)

    def test_errors_exit_2(self):
        cases = [
            (("show", "--symbols", "XAUSD"), "cot.py: XAUSD is not in your instruments. Did you mean XAUUSD?"),
            (("show", "--symbols", "QQQQQQ"), "cot.py: QQQQQQ is not in your instruments."),
            (("show", "--symbols", "XAUUSD", "--all"), "cot.py: Use --symbols or --all, not both."),
            (("show", "--weeks", "10"), "cot.py: --weeks must be a whole number from 26 to 156."),
            (("new", "--weeks", "157"), "cot.py: --weeks must be a whole number from 26 to 156."),
            (("show", "--now", "soon"),
             'cot.py: --now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)'),
            (("show", "--config", "fx/none.json", "--offline"),
             "cot.py: no config file at fx/none.json (copy config.example.json from the fx-budget folder)"),
        ]
        for args, message in cases:
            code, out, err = self.cli(*(args + ("--offline",)))
            self.assertEqual((code, err.strip()), (2, message), args)
            self.assertEqual(out, "")
        code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.has("usage: cot.py", out)
        code, out, err = self.cli("show", "--weeks", "many")
        self.assertEqual(code, 2)
        self.assertEqual(self.net.calls, [])

    def test_reader_refusals(self):
        self.write("watchlist.json", {"mode": "week", "hits": []})
        code, out, err = self.cli("show", "--offline")
        self.assertEqual((code, err.strip()), (2, 'cot.py: fx/watchlist.json: "mode" must be "swing" or "day"'))
        self.write("watchlist.json", {"mode": "swing", "hits": [{"symbol": "XAUUSD"}]})
        code, out, err = self.cli("show", "--offline")
        self.assertEqual(err.strip(), "cot.py: fx/watchlist.json: hit 1 (XAUUSD) has no level. Run fx-scanner again.")
        os.remove(self.path("watchlist.json"))
        self.write("plan.json", {"symbol": "XAUUSD", "side": "long", "style": "swing"})
        code, out, err = self.cli("show", "--offline")
        self.assertEqual(err.strip(), 'cot.py: fx/plan.json: side must be "buy" or "sell", not "long"')
        self.write("plan.json", "{")
        code, out, err = self.cli("show", "--symbols", "XAUUSD", "--offline")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("cot.py: fx/plan.json is not valid JSON"), err)
        os.remove(self.path("plan.json"))
        self.write("portfolio.json", {"account": {"currency": "EUR"}, "positions": []})
        code, out, err = self.cli("show", "--offline")
        self.assertEqual(err.strip(), "cot.py: fx/portfolio.json: the account currency is EUR, not USD. "
                                      "Fix it by hand or restore a backup.")
        pos = {"id": "XAUUSD-1", "symbol": "XAUUSD", "side": "buy", "style": "swing", "lots": 0.01}
        self.write("portfolio.json", {"account": {"currency": "USD"}, "positions": [pos, pos]})
        code, out, err = self.cli("show", "--offline")
        self.assertEqual(err.strip(), 'cot.py: fx/portfolio.json: two positions have the id "XAUUSD-1". '
                                      "Fix it by hand or restore a backup.")
        self.write("portfolio.json", {"account": {"currency": "USD"}, "positions": [dict(pos, symbol="XAUUSX")]})
        code, out, err = self.cli("show", "--offline")
        self.assertEqual(err.strip(), 'cot.py: fx/portfolio.json: position 1 ("XAUUSX") is not in your '
                                      "instruments. Fix it by hand or restore a backup.")


class TestOutput(CotCase):

    def test_json(self):
        args = ("show", "--symbols", "XAUUSD", "--now", "2026-07-17 09:01")
        code, out, err = self.cli(*(args + ("--json",)))
        self.assertEqual(code, 0, err)
        doc = json.loads(out)                                      # one object, nothing else
        for key in ("v", "tool", "cmd", "now", "ok", "notes", "doc", "replay", "weeks",
                    "first_run", "next_release", "symbols"):
            self.has(key, doc)
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["now"], doc["ok"], doc["doc"]),
                         (1, "fx-cot", "show", "2026-07-17T04:01:00Z", True, None))
        self.assertEqual((doc["replay"], doc["weeks"], doc["first_run"]), (True, 156, None))
        self.assertEqual(doc["next_release"], {"at": "2026-07-17T19:30:00Z", "expected": False})
        leg = doc["symbols"][0]["legs"][0]
        self.assertEqual((leg["net"], leg["change"], leg["report_date"], leg["released_at"]),
                         (98000, 6200, "2026-07-07", "2026-07-10T19:30:00Z"))
        self.assertEqual((leg["long"], leg["short"], leg["open_interest"]), (117882, 19882, 416941))
        self.assertEqual((leg["net_pct_oi"], leg["cot_index"], leg["index_reports"], leg["extreme"]),
                         (float(M_GOLD_0707_PCT), float(M_GOLD_0707_INDEX), 156, "low"))
        self.assertEqual((leg["pair_side"], leg["sign"], leg["group"], leg["age_days"], leg["new"]),
                         ("buy", 1, "managed_money", 10, True))
        self.assertEqual(doc["symbols"][0]["missing"], [])
        self.has("COT for 1 instrument (from --symbols). Positions from Tuesdays, out on Fridays.",
                      doc["notes"])
        code, out, err = self.cli("new", "--symbols", "XAUUSD", "--now", "2026-07-17 09:01", "--json")
        doc = json.loads(out)
        self.assertEqual((code, doc["ok"], doc["first_run"], doc["cmd"]), (1, False, False, "new"))
        code, out, err = self.cli("show", "--symbols", "XAUSD", "--json", "--offline")
        self.assertEqual((code, out), (2, ""))

    def test_your_trade_and_day_note(self):
        self.write("portfolio.json", {"account": {"currency": "USD"}, "positions": [
            {"id": "XAUUSD-1", "symbol": "XAUUSD", "side": "sell", "style": "day", "lots": 0.02,
             "paper": True}]})
        code, out, err = self.cli("show", "--now", "2026-07-17 09:01")
        self.assertEqual(code, 0, err)
        self.has("COT for 1 instrument (from your watchlist, plan and trades).", out)
        self.has("  Your open trade: SELL 0.02 lots (paper), day.", out)
        self.has("  Big speculators are on the other side.", out)
        self.has("  Note: COT is weekly. It says little about one day.", out)
        self.write("plan.json", {"symbol": "EURUSD", "side": "sell", "style": "swing"})
        code, out, err = self.cli("show", "--now", "2026-07-18 01:00", "--quiet")
        self.has("COT for 2 instruments (from your watchlist, plan and trades).", out)
        self.assertLess(out.index("EURUSD (Euro"), out.index("XAUUSD (Gold)"))   # the plan first
        self.has("  Your plan: SELL, swing.\n  Big speculators are on the same side.", out)
        self.assertNotIn("Note:", out)
        self.assertNotIn("It is context", out)
        os.remove(self.path("plan.json"))
        os.remove(self.path("portfolio.json"))
        code, out, err = self.cli("show", "--now", "2026-07-18 01:00")
        self.assertEqual(code, 0, err)
        self.has("COT for 20 instruments (from your universe (no watchlist, plan or trades)).", out)
        self.has(cot.BRENT_NOTE, out)
        code, out, err = self.cli("show", "--all", "--weeks", "52", "--now", "2026-07-18 01:00")
        self.has("COT for 20 instruments (from --all).", out)
        self.has("over 52 reports", out)

    def test_old_report_note(self):
        code, out, err = self.cli("show", "--symbols", "XAUUSD", "--now", "2026-08-05 12:00")
        self.assertEqual(code, 0, err)
        self.has("The report is 22 days old.", out)
        self.has("Note: no newer report yet. The CFTC can be late, for example in a US "
                      "government shutdown.", out)

    def test_guessed_dates(self):
        rows = gold_rows([1000] * 30, start="2027-11-09")
        self.write("data/cot/%s.json" % GOLD, {"fetched_at": "2027-11-16T01:00:00Z", "rows": rows})
        code, out, err = self.cli("show", "--symbols", "XAUUSD", "--now", "2027-11-17 09:00")
        self.assertEqual(code, 0, err)
        self.has("Out Tue 16 Nov 1:30 AM PKT (Mon 15 Nov 3:30 PM New York). Expected date.", out)
        self.assertEqual(out.count("Note: the 2027 dates are a guess from the 2026 rule. Check the "
                                   "CFTC release page."), 1)
        self.write("data/cot/%s.json" % GOLD, {"rows": gold_rows([1000] * 30, start="2028-02-01")})
        code, out, err = self.cli("show", "--symbols", "XAUUSD", "--now", "2028-02-05 09:00")
        self.has("Note: the CFTC dates in this pack end on 2028-01-03. This time is a guess: "
                      "Friday 3:30 PM New York.", out)

    def test_words(self):
        runs = [("show", "--symbols", "XAUUSD", "--now", "2026-07-17 09:01"),
                ("show", "--symbols", "XAUUSD,EURUSD,EURJPY,UKOIL", "--now", "2026-07-18 01:00"),
                ("new", "--all", "--now", "2026-07-18 01:00"),
                ("new", "--all", "--now", "2026-07-18 01:00"),
                ("show", "--symbols", "XAUUSD", "--now", "2026-08-05 12:00")]
        for args in runs:
            code, out, err = self.cli(*args)
            self.has(code, (0, 1), err)
            self.has("It is context, not a timing signal.", out)
            self.has("Source: CFTC", out)
            low = out.lower()
            for word in cot.SIGNAL_BANNED:
                self.assertNotIn(word, low)
            self.assertNotIn("\u2014", out)
            self.assertNotIn("!", out)
            for line in out.splitlines():
                self.assertLessEqual(len(line), cot.WRAP_WIDTH, line)


class TestSkillFile(unittest.TestCase):

    def test_skill_md(self):
        with open(os.path.join(ROOT, "fx-cot", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: fx-cot\n"))
        self.assertNotIn("PSX", text)
        self.assertNotIn("\u2014", text)
        heads = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(heads, ["## What it reads and writes", "## How to read it", "## Step 1: show",
                                 "## Step 2: new", "## Example", "## What it never does", "## Safety"])
        with open(os.path.join(os.path.dirname(ROOT), "fx-work", "spec", "06-cli-safety-tests.md"),
                  encoding="utf-8") as fh:
            spec = fh.read()
        start = spec.index("```markdown\n## Safety") + len("```markdown\n")
        block = spec[start:spec.index("\n```", start)]
        self.assertEqual(text[text.index("## Safety"):].strip(), block.strip())
