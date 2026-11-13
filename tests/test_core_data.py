"""Tests for the data groups of fx_core: yahoo client, cache, bars, rolls, broker prices.
Offline only: SLEEP and the opener are swapped for fakes. All prices are made up."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datetime
import gzip
import io
import json
import math
import shutil
import tempfile
import unittest
import urllib.error

import fx_core as fx

D = datetime.datetime
H = datetime.timedelta(hours=1)
M = datetime.timedelta(minutes=1)


def utc(text):
    """ "2026-11-11 06:30" -> naive datetime (test shorthand)."""
    return D.strptime(text, "%Y-%m-%d %H:%M")


def ts(text):
    return fx.utc_to_ts(utc(text))


def chart_doc(t_list, closes, meta=None, order=("volume", "low", "open", "close", "high")):
    """A made-up chart answer in Yahoo's real shape (the lists in a shuffled key order)."""
    q = {}
    for name in order:
        if name == "volume":
            q[name] = [0 if c is not None else None for c in closes]
        elif name == "high":
            q[name] = [None if c is None else c + 1 for c in closes]
        elif name == "low":
            q[name] = [None if c is None else c - 1 for c in closes]
        else:
            q[name] = list(closes)
    m = {"currency": "USD", "symbol": "GC=F", "regularMarketTime": t_list[-1] if t_list else 0,
         "regularMarketPrice": closes[-1] if closes else None, "shortName": "Gold Dec 26",
         "gmtoffset": -18000, "exchangeTimezoneName": "America/New_York", "previousClose": 2400.0}
    m.update(meta or {})
    res = {"meta": m, "indicators": {"quote": [q]}}
    if t_list:
        res["timestamp"] = list(t_list)
    return {"chart": {"result": [res], "error": None}}


class FakeResp(object):
    def __init__(self, status, body):
        self.status, self._body, self.headers = status, body, {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet(object):
    """Serves a list of (status, body bytes) answers in order and records each URL."""

    def __init__(self, answers):
        self.answers, self.urls, self.headers = list(answers), [], []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        self.headers.append(dict(req.header_items()))
        status, body = self.answers.pop(0)
        if status is None:
            raise urllib.error.URLError("no answer")
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(body))
        return FakeResp(status, body)


class NetCase(unittest.TestCase):
    """Swaps SLEEP and urlopen; self.sleeps records each wait."""

    def setUp(self):
        self.sleeps = []
        self._sleep, self._open = fx.SLEEP, fx.urllib.request.urlopen
        fx.SLEEP = self.sleeps.append
        fx._last_request = None

    def tearDown(self):
        fx.SLEEP = self._sleep
        fx.urllib.request.urlopen = self._open
        fx._last_request = None

    def net(self, answers):
        n = FakeNet(answers)
        fx.urllib.request.urlopen = n
        return n


OK = json.dumps(chart_doc([100], [1.0])).encode()
CUT = OK[:len(OK) // 2]


class Yahoo(NetCase):
    def test_chart_url(self):
        u = fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", range_="730d")
        self.assertEqual(u, "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF"
                            "?interval=1h&range=730d&includePrePost=false")
        u = fx.chart_url(fx.YAHOO_HOSTS[0], "EURUSD=X", "5m", period1=10, period2=20)
        self.assertIn("period1=10&period2=20", u)
        with self.assertRaisesRegex(ValueError, "^chart_url needs a range or a period1 and period2$"):
            fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h")
        with self.assertRaisesRegex(ValueError, "^chart_url needs a range"):
            fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", range_="1d", period1=1, period2=2)
        with self.assertRaisesRegex(ValueError, "^Interval 60m is not used by this pack$"):
            fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "60m", range_="1d")

    def test_ok_and_headers(self):
        n = self.net([(200, OK)])
        self.assertEqual(fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d")), OK)
        h = {k.lower(): v for k, v in n.headers[0].items()}
        self.assertEqual(h["user-agent"], "Mozilla/5.0 (compatible; fx-trader-skill/1.0)")
        self.assertEqual(h["accept"], "application/json")
        self.assertNotIn("cookie", h)

    def test_pace(self):
        self.net([(200, OK), (200, OK)])
        url = fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d")
        fx.yahoo_get(url)
        fx.yahoo_get(url)
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreater(self.sleeps[0], 1.9)
        self.assertLessEqual(self.sleeps[0], 2.0)

    def test_429_then_ok_uses_query2_once(self):
        n = self.net([(429, b"Too Many Requests"), (200, OK), (200, OK)])
        url = fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d")
        self.assertEqual(fx.yahoo_get(url), OK)
        self.assertTrue(n.urls[0].startswith("https://query1."))
        self.assertTrue(n.urls[1].startswith("https://query2."))
        self.assertIn(5, self.sleeps)
        self.assertEqual(fx._last_host, "query2")
        fx.yahoo_get(url)
        self.assertTrue(n.urls[2].startswith("https://query1."))   # the next call starts on query1

    def test_429_twice(self):
        self.net([(429, b""), (429, b"")])
        with self.assertRaisesRegex(ValueError, r"^Yahoo is busy \(too many requests\)\. Wait a few minutes, "
                                                r"then try again\. Or use --offline\.$"):
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d"))

    def test_5xx_and_no_answer(self):
        n = self.net([(503, b""), (None, b"")])
        with self.assertRaisesRegex(ValueError, r"^Yahoo did not answer for GC=F\. Try again later, or use --offline\.$"):
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d"))
        self.assertEqual(len(n.urls), 2)
        self.assertIn(3, self.sleeps)
        self.net([(500, b""), (200, OK)])
        self.assertEqual(fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d")), OK)

    def test_cut_short_json(self):
        n = self.net([(200, CUT), (200, CUT)])
        with self.assertRaisesRegex(ValueError, r"^Yahoo sent a broken answer for EURUSD=X\. Try again in a minute\.$"):
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "EURUSD=X", "5m", "1d"))
        self.assertEqual(len(n.urls), 2)
        self.assertIn(2, self.sleeps)
        self.net([(200, CUT), (200, OK)])
        self.assertEqual(fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d")), OK)

    def test_gzip_bodies(self):
        def opener(answers):
            def call(req, timeout=None):
                status, body = answers.pop(0)
                head = {"Content-Encoding": "gzip"}
                if status != 200:
                    raise urllib.error.HTTPError(req.full_url, status, "x", head, io.BytesIO(body))
                r = FakeResp(status, body)
                r.headers = head
                return r
            return call
        url = fx.chart_url(fx.YAHOO_HOSTS[0], "EURUSD=X", "5m", "1d")
        fx.urllib.request.urlopen = opener([(200, gzip.compress(OK))])
        self.assertEqual(fx.yahoo_get(url), OK)
        cut = gzip.compress(OK)[:20]
        fx.urllib.request.urlopen = opener([(200, cut), (200, cut)])
        with self.assertRaisesRegex(ValueError, r"^Yahoo sent a broken answer for EURUSD=X\. "):
            fx.yahoo_get(url)
        err = json.dumps({"chart": {"result": None, "error": {"code": "Bad Request",
                                                              "description": "Invalid input"}}}).encode()
        fx.urllib.request.urlopen = opener([(400, gzip.compress(err))])
        with self.assertRaisesRegex(ValueError, "Invalid input"):
            fx.yahoo_get(url)

    def test_404_422_400_401_no_retry(self):
        b404 = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found"}}}'
        n = self.net([(404, b404)])
        with self.assertRaises(fx.NoData) as cm:
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "XAUUSD=X", "1h", "5d"))
        self.assertEqual(str(cm.exception), "Yahoo has no data for XAUUSD=X. Check the yahoo symbol in your config.")
        self.assertEqual(len(n.urls), 1)
        self.net([(404, b404)])
        with self.assertRaises(fx.NoData) as cm:
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GCQ26.CMX", "1h", "5d"))
        self.assertEqual(str(cm.exception), "Yahoo has no data for GCQ26.CMX. Old contracts vanish from Yahoo.")
        self.net([(422, b"{}")])
        with self.assertRaises(ValueError) as cm:
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "EURUSD=X", "5m", "61d"))
        self.assertEqual(str(cm.exception), "Yahoo keeps 5-minute bars for 60 days and 1-hour bars for 730 days. "
                                            "This asked for older data (EURUSD=X, 5m).")
        self.assertNotIsInstance(cm.exception, fx.NoData)
        b400 = b'{"chart":{"result":null,"error":{"code":"Bad Request","description":"Invalid input"}}}'
        self.net([(400, b400)])
        with self.assertRaisesRegex(ValueError, "^Yahoo refused the request for GC=F: Invalid input$"):
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d"))
        self.net([(401, b"")])
        with self.assertRaisesRegex(ValueError, r"^Yahoo refused this kind of request\.$"):
            fx.yahoo_get(fx.chart_url(fx.YAHOO_HOSTS[0], "GC=F", "1h", "5d"))
        self.assertFalse([w for w in self.sleeps if w in (2, 3, 5)])   # pace waits only, no retry wait

    def test_fetch_and_parse(self):
        t = [ts("2026-07-23 10:00"), ts("2026-07-23 11:00"), ts("2026-07-23 11:00") + 77]
        self.net([(200, json.dumps(chart_doc(t, [2400.10009765625, None, 2401.0])).encode())])
        res = fx.fetch_chart("GC=F", "1h", period1=1, period2=2)
        meta, rows = fx.parse_chart(res)
        self.assertEqual(meta["shortName"], "Gold Dec 26")
        self.assertEqual(rows[0], [t[0], 2400.10009765625, 2401.10009765625, 2399.10009765625, 2400.10009765625, 0])
        self.assertEqual(rows[1], [t[1], None, None, None, None, None])
        # the same lists in another key order give the same rows
        doc2 = chart_doc(t, [2400.10009765625, None, 2401.0], order=("high", "close", "open", "volume", "low"))
        self.assertEqual(fx.parse_chart(doc2["chart"]["result"][0])[1], rows)

    def test_parse_edge(self):
        meta, rows = fx.parse_chart(chart_doc([], [])["chart"]["result"][0])   # a Saturday: no timestamp
        self.assertEqual(rows, [])
        res = chart_doc([1, 2], [1.0, 2.0])["chart"]["result"][0]
        del res["indicators"]["quote"][0]["volume"]
        self.assertEqual(fx.parse_chart(res)[1][0][5], None)
        res["indicators"]["quote"][0]["close"] = [1.0]
        with self.assertRaisesRegex(ValueError, r"^Yahoo sent a broken chart for GC=F \(lists of different length\)\.$"):
            fx.parse_chart(res, "GC=F")

    def test_fetch_errors(self):
        self.net([(200, b'{"chart":{"result":[],"error":null}}')])
        with self.assertRaisesRegex(fx.NoData, r"^Yahoo sent no data for GC=F\.$"):
            fx.fetch_chart("GC=F", "1h", range_="5d")
        self.net([(200, b'{"chart":{"result":null,"error":{"code":"Not Found","description":"x"}}}')])
        with self.assertRaises(fx.NoData):
            fx.fetch_chart("GCQ26.CMX", "1h", range_="5d")

    def test_spark(self):
        def item(sym, closes):
            res = chart_doc([100, 400], closes, meta={"symbol": "JPY=X" if sym == "USDJPY=X" else sym,
                                                     "regularMarketTime": 450, "shortName": sym})
            return {"symbol": sym, "response": res["chart"]["result"]}
        body = {"spark": {"result": [item("USDJPY=X", [150.1, 150.2]), item("EURUSD=X", [1.08, None])],
                          "error": None}}
        n = self.net([(200, json.dumps(body).encode())])
        out = fx.spark(["EURUSD=X", "USDJPY=X", "NOPE=X"])
        self.assertEqual(out["USDJPY=X"]["close"], 150.2)
        self.assertEqual(out["EURUSD=X"]["close"], 1.08)
        self.assertEqual(out["EURUSD=X"]["at"], fx.ts_to_utc(450))
        self.assertNotIn("NOPE=X", out)
        self.assertIn("/v7/finance/spark?symbols=EURUSD%3DX,USDJPY%3DX,NOPE%3DX&range=1d&interval=5m", n.urls[0])
        empty = json.dumps({"spark": {"result": [], "error": None}}).encode()
        n = self.net([(200, empty), (200, empty)])
        fx.spark(["S%d=X" % i for i in range(21)])
        self.assertEqual(len(n.urls), 2)


class Cache(NetCase):
    def setUp(self):
        NetCase.setUp(self)
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        NetCase.tearDown(self)
        shutil.rmtree(self.dir)

    def test_cache_file(self):
        self.assertEqual(fx.cache_file("d", "GC=F", "1h"), os.path.join("d", "yahoo", "GC_F", "1h.json"))
        self.assertTrue(fx.cache_file("d", "GCZ26.CMX", "5m").endswith(os.path.join("GCZ26_CMX", "5m.json")))
        self.assertTrue(fx.cache_file("d", "EURUSD=X", "1d").endswith(os.path.join("EURUSD_X", "1d.json")))
        with self.assertRaisesRegex(ValueError, r"^Bad Yahoo symbol: \.\./x$"):
            fx.cache_file("d", "../x", "1h")

    def test_load_cache(self):
        doc = fx.load_cache(self.dir, "GC=F", "1h")
        self.assertEqual(doc, {"symbol": "GC=F", "interval": "1h", "bars": [], "fetches": []})
        self.assertIsNone(fx.last_fetch(doc))
        path = fx.cache_file(self.dir, "GC=F", "1h")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            fh.write('{"bars": [[1, 2')
        with self.assertRaisesRegex(ValueError, r"^The price cache .*1h\.json is broken\. Delete it and run again "
                                                r"with the network on\.$"):
            fx.load_cache(self.dir, "GC=F", "1h")

    def test_merge_rows(self):
        now = utc("2026-07-23 12:00")
        t0 = ts("2026-07-23 06:00")
        old = [[t0, 1, 1, 1, 1, 0], [t0 + 3600, 2, 2, 2, 2, 0], [t0 + 7200, 3, 3, 3, 3, 0]]
        new = [[t0 + 3600, 9, 9, 9, 9, 0], [t0 + 3 * 3600, 4, 4, 4, 4, 0]]
        # the old 08:00 row is inside the window but missing from the new answer: it goes
        got = fx.merge_rows(old, new, (t0 + 3600, t0 + 3 * 3600), now, "1h")
        self.assertEqual([r[0] for r in got], [t0, t0 + 3600, t0 + 3 * 3600])
        self.assertEqual(got[1][1], 9)
        # rows older than LOOKBACK + 30 days are cut
        far = [[ts("2024-06-01 00:00"), 1, 1, 1, 1, 0]]
        self.assertEqual(fx.merge_rows(far + old, [], None, now, "1h"), old)
        self.assertEqual(fx.merge_rows(old, [], None, now, "5m"), old)
        self.assertEqual(fx.merge_rows([[ts("2026-04-01 00:00"), 1, 1, 1, 1, 0]], [], None, now, "5m"), [])

    def test_refresh_offline_no_call(self):
        n = self.net([])
        doc = fx.refresh(self.dir, "GC=F", "1h", utc("2026-07-23 12:00"), {"yahoo": True}, True)
        self.assertEqual(doc["bars"], [])
        self.assertEqual(n.urls, [])

    def test_refresh_first_then_incremental(self):
        now = utc("2026-07-23 12:10")
        t = [ts("2026-07-23 09:00"), ts("2026-07-23 10:00"), ts("2026-07-23 11:00"), ts("2026-07-23 12:00") - 2]
        body = json.dumps(chart_doc(t, [2400.0, None, 2401.5, 2402.0])).encode()
        n = self.net([(200, body)])
        doc = fx.refresh(self.dir, "GC=F", "1h", now, {"yahoo": True}, False)
        self.assertIn("range=730d", n.urls[0])
        self.assertEqual([r[0] for r in doc["bars"]], [t[0], t[2]])     # null close and live tick not stored
        rec = doc["fetches"][-1]
        self.assertEqual((rec["at"], rec["host"], rec["rows"], rec["first"], rec["last"]),
                         ("2026-07-23T12:10:00Z", "query1", 2, t[0], t[2]))
        self.assertEqual((rec["market_time"], rec["short_name"], rec["previous_close"]), (t[3], "Gold Dec 26", 2400.0))
        with open(fx.cache_file(self.dir, "GC=F", "1h")) as fh:
            self.assertNotIn("\n ", fh.read())     # compact JSON
        # 5 minutes later: younger than MIN_REFRESH_S, no call
        fx.refresh(self.dir, "GC=F", "1h", now + 5 * M, {"yahoo": True}, False)
        self.assertEqual(len(n.urls), 1)
        # 20 minutes later: an incremental fetch from the newest bar - 2 steps
        t2 = [ts("2026-07-23 11:00"), ts("2026-07-23 12:00")]
        n2 = self.net([(200, json.dumps(chart_doc(t2, [2401.6, 2403.0], meta={"regularMarketTime": 0})).encode())])
        later = now + 20 * M
        doc = fx.refresh(self.dir, "GC=F", "1h", later, {"yahoo": True}, False)
        self.assertIn("period1=%d&period2=%d" % (t[2] - 7200, fx.utc_to_ts(later) + 60), n2.urls[0])
        self.assertEqual([r[0] for r in doc["bars"]], [t[0], t[2], t2[1]])
        self.assertEqual(doc["bars"][1][4], 2401.6)
        self.assertEqual(len(doc["fetches"]), 2)


GOLD = {"key": "XAUUSD", "yahoo": "GC=F", "kind": "metal", "price_source": "futures", "hours": "comex",
        "data_hours": "cme", "quote": "USD", "digits": 2, "yahoo_to_broker": 1.0, "tick_size": 0.01,
        "futures": {"root": "GC", "suffix": ".CMX", "cycle": "GJMQZ", "roll_rule": "gc_fnd",
                    "settle": ["ny", "13:30"], "shortname_month": True, "history_check": True}}
EUR = {"key": "EURUSD", "yahoo": "EURUSD=X", "kind": "fx", "price_source": "spot_fx", "hours": "fx",
       "data_hours": "yahoo_fx", "base": "EUR", "quote": "USD", "digits": 5, "yahoo_to_broker": 1.0,
       "tick_size": 0.00001, "futures": None}
OIL = {"key": "USOIL", "yahoo": "CL=F", "kind": "energy", "price_source": "futures", "hours": "nymex",
       "data_hours": "cme", "quote": "USD", "digits": 2, "yahoo_to_broker": 1.0, "tick_size": 0.01,
       "futures": {"root": "CL", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ", "roll_rule": "cl_ltd",
                   "settle": ["ny", "14:30"], "shortname_month": True, "history_check": True}}
CFG = {"day_end": "17:00 New York", "holidays": {}, "early_closes": {}, "yahoo": True}


class Bars(unittest.TestCase):
    def test_yahoo_decimals(self):
        self.assertEqual(fx.yahoo_decimals(GOLD), 1)
        self.assertEqual(fx.yahoo_decimals(EUR), 5)
        self.assertEqual(fx.yahoo_decimals(dict(EUR, quote="JPY")), 3)
        self.assertEqual(fx.yahoo_decimals(OIL), 2)
        self.assertEqual(fx.yahoo_decimals(fx.yahoo_inst("NG=F")), 3)
        self.assertEqual(fx.yahoo_decimals(fx.yahoo_inst("HGZ26.CMX")), 4)
        self.assertEqual(fx.yahoo_decimals(fx.yahoo_inst("USDJPY=X")), 3)

    def test_clean_rows(self):
        t0 = ts("2026-07-23 10:00")
        rows = [[t0 + 3600, 2433.39990234375, 2434.0, 2433.0, 2433.60009765625, 12],
                [t0, 2430.0, 2431.0, 2429.0, 2430.5, None],
                [t0 + 7200, None, None, None, None, None],                 # all-null grid filler
                [t0 + 9000, 2433.0, None, 2432.0, 2433.5, 3],                # a null high
                [t0 + 3600, 2433.39990234375, 2435.0, 2433.0, 2434.0, 15],   # duplicate: the last wins
                [t0 + 10800, 2434.0, 2434.5, 2433.5, 2434.2, 4],
                [t0 + 10877, 2434.2, 2434.2, 2434.2, 2434.2, 0]]             # the live tick
        bars = fx.clean_rows(rows, {"regularMarketTime": t0 + 10877}, "1h", GOLD)
        self.assertEqual([b["start"] for b in bars], [utc("2026-07-23 10:00"), utc("2026-07-23 11:00"),
                                                      utc("2026-07-23 13:00")])
        self.assertEqual(bars[0]["volume"], 0)
        self.assertEqual(bars[1], {"start": utc("2026-07-23 11:00"), "open": 2433.4, "high": 2435.0,
                                   "low": 2433.0, "close": 2434.0, "volume": 15})
        # the live tick is dropped by market_time even on the whole minute (cache rows)
        live = [[t0, 1.0, 1.0, 1.0, 1.0, 0], [t0 + 300, 1.1, 1.1, 1.1, 1.1, 0]]
        self.assertEqual(len(fx.clean_rows(live, {"market_time": t0 + 300}, "5m", EUR)), 1)

    def test_clean_rows_fix_and_zero(self):
        t0 = ts("2026-07-23 10:00")
        rows = [[t0, 1.0850000429153442, 1.0849, 1.0851, 1.0852000474929810, 900],
                [t0 + 300, 0.0, 0.0, 0.0, 0.0, 0]]
        bars = fx.clean_rows(rows, {}, "5m", EUR)
        self.assertEqual(len(bars), 1)
        self.assertEqual((bars[0]["open"], bars[0]["high"], bars[0]["low"], bars[0]["close"]),
                         (1.085, 1.0852, 1.085, 1.0852))   # an impossible bar fixed
        self.assertEqual(bars[0]["volume"], 0)        # FX volume is always 0
        neg = [[t0, -5.0, -4.0, -6.0, -4.5, 7]]
        self.assertEqual(fx.clean_rows(neg, {}, "1h", OIL)[0]["close"], -4.5)   # oil may go below 0
        self.assertEqual(fx.clean_rows(neg, {}, "1h", GOLD), [])

    def test_bar_done_friday(self):
        # GC=F 1h, fetched Sat 2026-11-14 10:00 UTC, market_time Fri 16:59:58 New York
        mt = utc("2026-11-13 21:59") + datetime.timedelta(seconds=58)
        now = utc("2026-11-14 10:00")
        self.assertTrue(fx.bar_done(utc("2026-11-13 21:00"), "1h", mt, now, GOLD, CFG))    # rule 2
        self.assertTrue(fx.bar_done(utc("2026-11-13 20:00"), "1h", mt, now, GOLD, CFG))    # rule 1
        # 10 minutes after the close is not enough (futures are about 10 minutes late)
        self.assertFalse(fx.bar_done(utc("2026-11-13 21:00"), "1h", mt, utc("2026-11-13 22:10"), GOLD, CFG))
        self.assertTrue(fx.bar_done(utc("2026-11-13 21:00"), "1h", mt, utc("2026-11-13 22:15"), GOLD, CFG))
        # a mid-day bar waits for the data clock
        self.assertFalse(fx.bar_done(utc("2026-11-12 15:00"), "1h", utc("2026-11-12 15:59"), now, GOLD, CFG))
        # the 5m bar 16:55 New York before the daily break (Thursday)
        mt5 = utc("2026-11-12 21:59")
        self.assertTrue(fx.bar_done(utc("2026-11-12 21:55"), "5m", mt5, utc("2026-11-12 22:16"), GOLD, CFG))
        self.assertFalse(fx.bar_done(utc("2026-11-12 21:50"), "5m", utc("2026-11-12 21:54"),
                                     utc("2026-11-12 22:16"), GOLD, CFG))

    def test_bar_done_summer_gold(self):
        mt = utc("2026-07-24 20:59") + datetime.timedelta(seconds=58)    # Fri 16:59:58 New York
        now = utc("2026-07-25 06:00")
        self.assertTrue(fx.bar_done(utc("2026-07-24 20:00"), "1h", mt, now, GOLD, CFG))
        self.assertTrue(fx.bar_done(utc("2026-07-24 19:00"), "1h", mt, now, GOLD, CFG))
        # Fri 2026-07-03: Yahoo has no futures bars from 00:00 New York, so the 23:00 New
        # York bar (03:00 UTC) is the last of its span: done 15 minutes after 04:00 UTC
        self.assertTrue(fx.bar_done(utc("2026-07-03 03:00"), "1h", utc("2026-07-03 03:10"),
                                    utc("2026-07-03 04:15"), GOLD, CFG))
        self.assertFalse(fx.bar_done(utc("2026-07-03 03:00"), "1h", utc("2026-07-03 03:10"),
                                     utc("2026-07-03 04:10"), GOLD, CFG))

    def test_bar_done_summer_fx(self):
        # EURUSD 1h: the bar 16:00 New York on Fri 2026-07-24 (20:00 UTC, US summer)
        mt = utc("2026-07-24 20:59")
        self.assertTrue(fx.bar_done(utc("2026-07-24 20:00"), "1h", mt, utc("2026-07-25 08:00"), EUR, CFG))
        self.assertFalse(fx.bar_done(utc("2026-07-24 20:00"), "1h", mt, utc("2026-07-24 21:05"), EUR, CFG))


def hourly_rows(template, first, last, base, cfg=CFG, skip=()):
    """Made-up raw 1h rows on every open hour of the data template, FX days first..last.
    The close climbs by a small step each hour, so every bar is different."""
    rows = []
    d = datetime.date(*map(int, first.split("-")))
    end = datetime.date(*map(int, last.split("-")))
    while d <= end:
        if d.weekday() < 5:
            for a, b in fx.open_spans(template, d, cfg):
                t = a
                while t < b:
                    if t not in skip:
                        c = base + len(rows) * 0.1
                        rows.append([fx.utc_to_ts(t), c - 0.05, c + 0.2, c - 0.2, c, 5])
                    t += H
        d += datetime.timedelta(days=1)
    return rows


def write_cache(data_dir, symbol, interval, rows, at, market_time):
    doc = {"symbol": symbol, "interval": interval, "bars": rows,
           "fetches": [{"at": fx.utc_iso(at), "market_time": fx.utc_to_ts(market_time)}]}
    fx.save_cache(fx.cache_file(data_dir, symbol, interval), doc)


class Daily(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def bars(self, inst, rows, at, mt=None, now=None, **kw):
        write_cache(self.dir, inst["yahoo"], "1h", rows, at, mt or at)
        return fx.load_bars(self.dir, inst["yahoo"], "1h", now or at, inst=inst, cfg=CFG, **kw)

    def test_winter_new_york(self):
        at = utc("2026-11-14 10:00")
        eur = fx.daily_from_hourly(self.bars(EUR, hourly_rows("yahoo_fx", "2026-11-09", "2026-11-13", 1.1), at),
                                   "17:00 New York", EUR, CFG)
        day = [b for b in eur if b["fx_day"] == "2026-11-11"][0]
        self.assertEqual((day["start"], day["end"], day["hours"]), (utc("2026-11-10 22:00"), utc("2026-11-11 22:00"), 24))
        self.assertEqual(fx.to_pkt(day["start"]), utc("2026-11-11 03:00"))
        self.assertEqual(day["flags"], [])
        gold = fx.daily_from_hourly(self.bars(GOLD, hourly_rows("cme", "2026-11-09", "2026-11-13", 4100.0), at),
                                    "17:00 New York", GOLD, CFG)
        g = [b for b in gold if b["fx_day"] == "2026-11-11"][0]
        self.assertEqual(g["hours"], 23)
        self.assertEqual(g["flags"], [])
        self.assertEqual([b["fx_day"] for b in gold][-1], "2026-11-13")   # Friday's bar is done all weekend
        # the Yahoo FX Monday starts at 00:00 London: late_start; gold Mondays are not late
        self.assertIn("late_start", eur[0]["flags"])
        self.assertEqual(eur[0]["fx_day"], "2026-11-09")
        self.assertNotIn("late_start", gold[0]["flags"])

    def test_close_is_last_bar(self):
        at = utc("2026-11-14 10:00")
        rows = hourly_rows("yahoo_fx", "2026-11-11", "2026-11-11", 1.1)
        bars = self.bars(EUR, rows, at)
        day = fx.daily_from_hourly(bars, "17:00 New York", EUR, CFG)[0]
        self.assertEqual(bars[-1]["start"], utc("2026-11-11 21:00"))
        self.assertEqual(day["close"], bars[-1]["close"])
        self.assertEqual(day["open"], bars[0]["open"])
        self.assertEqual(day["high"], max(b["high"] for b in bars))

    def test_utc_day_end(self):
        at = utc("2026-11-14 10:00")
        bars = self.bars(EUR, hourly_rows("yahoo_fx", "2026-11-10", "2026-11-12", 1.1), at)
        days = fx.daily_from_hourly(bars, "00:00 UTC", EUR, CFG)
        day = [b for b in days if b["fx_day"] == "2026-11-11"][0]
        self.assertEqual((day["start"], day["end"], day["hours"]), (utc("2026-11-11 00:00"), utc("2026-11-12 00:00"), 24))
        self.assertEqual(fx.to_pkt(day["start"]), utc("2026-11-11 05:00"))
        self.assertEqual(day["flags"], [])

    def test_summer(self):
        at = utc("2026-08-01 10:00")
        bars = self.bars(GOLD, hourly_rows("cme", "2026-07-28", "2026-07-31", 2400.0), at)
        days = fx.daily_from_hourly(bars, "17:00 New York", GOLD, CFG)
        day = [b for b in days if b["fx_day"] == "2026-07-29"][0]
        self.assertEqual((day["start"], day["end"]), (utc("2026-07-28 21:00"), utc("2026-07-29 21:00")))
        self.assertEqual(fx.to_pkt(day["start"]), utc("2026-07-29 02:00"))
        self.assertEqual(day["hours"], 23)
        u = fx.daily_from_hourly(bars, "00:00 UTC", GOLD, CFG)
        uday = [b for b in u if b["fx_day"] == "2026-07-29"][0]
        self.assertEqual((uday["start"], uday["end"]), (utc("2026-07-29 00:00"), utc("2026-07-30 00:00")))
        self.assertEqual(uday["hours"], 23)      # 21:00 to 22:00 UTC is the CME break

    def test_early_close_and_expected(self):
        at = utc("2026-07-06 10:00")
        bars = self.bars(GOLD, hourly_rows("cme", "2026-07-01", "2026-07-03", 2400.0), at)
        day = [b for b in fx.daily_from_hourly(bars, "17:00 New York", GOLD, CFG) if b["fx_day"] == "2026-07-03"][0]
        self.assertEqual(day["hours"], 6)
        self.assertIn("early_close", day["flags"])
        self.assertNotIn("partial", day["flags"])
        self.assertEqual(fx.open_time_in("yahoo_fx", utc("2026-12-24 00:00"), utc("2026-12-25 00:00"), CFG), 22 * H)
        a, b = fx.fx_day_bounds("2026-09-08", "17:00 New York")
        self.assertEqual(fx.open_time_in("cme", a, b, CFG), 17 * H)

    def test_labor_day_late_start(self):
        at = utc("2026-09-09 10:00")
        rows = hourly_rows("cme", "2026-09-08", "2026-09-08", 2500.0)
        self.assertEqual(len(rows), 17)
        day = fx.daily_from_hourly(self.bars(GOLD, rows, at), "17:00 New York", GOLD, CFG)[0]
        self.assertEqual(day["hours"], 17)
        self.assertEqual(day["flags"], ["late_start"])

    def test_stray_bar_dropped(self):
        at = utc("2026-11-14 10:00")
        rows = hourly_rows("cme", "2026-11-09", "2026-11-10", 4100.0)
        stray = [fx.utc_to_ts(utc("2026-11-09 22:00")), 4149.5, 4149.5, 4149.5, 4149.5, 1]   # Mon 17:00 New York
        rows = sorted(rows + [stray])
        bars = self.bars(GOLD, rows, at)
        self.assertNotIn(utc("2026-11-09 22:00"), [b["start"] for b in bars])
        tue = [b for b in fx.daily_from_hourly(bars, "17:00 New York", GOLD, CFG) if b["fx_day"] == "2026-11-10"][0]
        first = [b for b in bars if b["start"] == utc("2026-11-09 23:00")][0]
        self.assertEqual((tue["open"], tue["hours"]), (first["open"], 23))

    def test_cut_at_now_and_forming(self):
        at = utc("2026-11-14 10:00")
        rows = hourly_rows("yahoo_fx", "2026-11-10", "2026-11-12", 1.1)
        now = utc("2026-11-11 14:30")
        write_cache(self.dir, "EURUSD=X", "1h", rows, at, at)
        bars = fx.load_bars(self.dir, "EURUSD=X", "1h", now, inst=EUR, cfg=CFG)
        self.assertEqual(bars[-1]["start"], utc("2026-11-11 13:00"))
        withf = fx.load_bars(self.dir, "EURUSD=X", "1h", now, include_forming=True, inst=EUR, cfg=CFG)
        self.assertEqual((withf[-1]["start"], withf[-1]["forming"]), (utc("2026-11-11 14:00"), True))
        days = fx.daily_from_hourly(bars, "17:00 New York", EUR, CFG)
        self.assertEqual(days[-1]["fx_day"], "2026-11-10")              # 11-11 is forming
        days = fx.daily_from_hourly(withf, "17:00 New York", EUR, CFG, include_forming=True)
        self.assertEqual((days[-1]["fx_day"], days[-1]["flags"][-1]), ("2026-11-11", "forming"))
        days = fx.daily_from_hourly(bars, "17:00 New York", EUR, CFG, now=now)
        self.assertEqual(days[-1]["fx_day"], "2026-11-10")
        # the data clock: a fetch at 14:40 with market_time 13:59 leaves the 13:00 bar done only
        write_cache(self.dir, "EURUSD=X", "1h", rows, utc("2026-11-11 14:40"), utc("2026-11-11 13:59"))
        bars = fx.load_bars(self.dir, "EURUSD=X", "1h", utc("2026-11-11 16:00"), inst=EUR, cfg=CFG)
        self.assertEqual(bars[-1]["start"], utc("2026-11-11 12:00"))
        self.assertEqual(fx.load_bars(self.dir, "NONE=X", "1h", now, inst=EUR, cfg=CFG), [])
        for kw in ({}, {"inst": EUR}, {"cfg": CFG}):
            with self.assertRaises(ValueError) as e:
                fx.load_bars(self.dir, "EURUSD=X", "1h", now, **kw)
            self.assertEqual(str(e.exception), "load_bars needs inst= and cfg=")

    def test_plan_scale(self):
        cu = {"key": "COPPER", "yahoo": "HG=F", "kind": "metal", "price_source": "futures", "hours": "comex",
              "data_hours": "cme", "quote": "USD", "digits": 2, "yahoo_to_broker": 2204.62,
              "futures": {"root": "HG"}}
        at = utc("2026-11-14 10:00")
        rows = [[fx.utc_to_ts(utc("2026-11-11 15:00")), 6.3, 6.31, 6.29, 6.3000000953674316, 10]]
        bars = self.bars(cu, rows, at)
        self.assertEqual(bars[0]["close"], 13889.11)
        self.assertEqual(bars[0]["volume"], 10)


class Outages(unittest.TestCase):
    def bars(self, inst, template, first, last, skip=()):
        rows = hourly_rows(template, first, last, 2400.0 if inst is GOLD else 1.08, skip=skip)
        return fx.clean_rows(rows, {}, "1h", inst)

    def test_outage(self):
        skip = set(utc("2026-07-21 02:00") + i * H for i in range(10))
        bars = self.bars(EUR, "yahoo_fx", "2026-07-20", "2026-07-22", skip)
        self.assertEqual(fx.find_outages(bars, EUR, "1h", CFG), [(utc("2026-07-21 02:00"), utc("2026-07-21 12:00"))])
        days = fx.daily_from_hourly(bars, "17:00 New York", EUR, CFG)
        self.assertEqual([d["fx_day"] for d in days if "outage" in d["flags"]], ["2026-07-21"])
        self.assertIn("partial", [d for d in days if d["fx_day"] == "2026-07-21"][0]["flags"])

    def test_short_hole_and_weekend(self):
        skip = set(utc("2026-07-21 02:00") + i * H for i in range(2))
        bars = self.bars(EUR, "yahoo_fx", "2026-07-20", "2026-07-21", skip)
        self.assertEqual(fx.find_outages(bars, EUR, "1h", CFG), [])
        # Friday 17:00 New York to Monday 00:00 London is closed time: no outage
        bars = self.bars(EUR, "yahoo_fx", "2026-07-24", "2026-07-27")
        self.assertEqual(fx.find_outages(bars, EUR, "1h", CFG), [])
        # the CME daily break is not a hole either
        bars = self.bars(GOLD, "cme", "2026-07-20", "2026-07-23")
        self.assertEqual(fx.find_outages(bars, GOLD, "1h", CFG), [])

    def test_no_data_day(self):
        # around Labor Day (Mon 2026-09-07) Yahoo has no futures bars: never an outage
        bars = self.bars(GOLD, "cme", "2026-09-04", "2026-09-09")
        self.assertEqual(fx.find_outages(bars, GOLD, "1h", CFG), [])
        bars = [b for b in bars if not (utc("2026-09-08 04:00") <= b["start"] < utc("2026-09-08 10:00"))]
        self.assertEqual(fx.find_outages(bars, GOLD, "1h", CFG), [])   # touches the no-data cut
        bars = self.bars(GOLD, "cme", "2026-09-09", "2026-09-10")
        bars = [b for b in bars if not (utc("2026-09-09 04:00") <= b["start"] < utc("2026-09-09 10:00"))]
        self.assertEqual(fx.find_outages(bars, GOLD, "1h", CFG), [(utc("2026-09-09 04:00"), utc("2026-09-09 10:00"))])


class Latest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_live_and_replay(self):
        self.assertIsNone(fx.latest_price(self.dir, GOLD, utc("2026-11-11 08:46"), CFG))
        rows = hourly_rows("cme", "2026-11-10", "2026-11-11", 4100.0)
        mt = utc("2026-11-11 08:35") + datetime.timedelta(seconds=30)
        doc = {"symbol": "GC=F", "interval": "1h", "bars": rows, "fetches": [
            {"at": "2026-11-11T08:45:40Z", "market_time": fx.utc_to_ts(mt), "price": 4126.2000000001,
             "short_name": "Gold Dec 26"}]}
        fx.save_cache(fx.cache_file(self.dir, "GC=F", "1h"), doc)
        five = {"symbol": "GC=F", "interval": "5m", "bars": [], "fetches": [
            {"at": "2026-11-11T08:30:00Z", "market_time": fx.utc_to_ts(utc("2026-11-11 08:20")), "price": 4120.0}]}
        fx.save_cache(fx.cache_file(self.dir, "GC=F", "5m"), five)
        lp = fx.latest_price(self.dir, GOLD, utc("2026-11-11 08:46") + datetime.timedelta(seconds=10), CFG)
        self.assertEqual((lp["bid"], lp["yahoo"], lp["at"], lp["source"], lp["contract"]),
                         (4126.2, 4126.2, mt, "yahoo 1h", "Gold Dec 26"))
        self.assertAlmostEqual(lp["age_min"], 10 + 40 / 60.0)
        # a replay at 06:20: the close of the last 1h bar done by then, at its end
        lp = fx.latest_price(self.dir, GOLD, utc("2026-11-11 06:20"), CFG)
        bar = [r for r in rows if r[0] == ts("2026-11-11 05:00")][0]
        self.assertEqual((lp["bid"], lp["at"], lp["source"]), (round(bar[4], 1), utc("2026-11-11 06:00"), "yahoo 1h"))
        self.assertAlmostEqual(lp["age_min"], 20.0)
        self.assertIsNone(lp["contract"])           # no fetch was made by then

    def test_replay_contract_newest_fetch(self):
        rows = hourly_rows("cme", "2026-11-10", "2026-11-11", 4100.0)
        doc = {"symbol": "GC=F", "interval": "1h", "bars": rows, "fetches": [
            {"at": "2026-11-10T10:00:00Z", "market_time": ts("2026-11-10 09:59"), "price": 4100.0,
             "short_name": "Gold Oct 26"},
            {"at": "2026-11-11T08:45:40Z", "market_time": ts("2026-11-11 08:35"), "price": 4126.2,
             "short_name": "Gold Dec 26"}]}
        fx.save_cache(fx.cache_file(self.dir, "GC=F", "1h"), doc)
        five = {"symbol": "GC=F", "interval": "5m", "bars": [], "fetches": [
            {"at": "2026-11-11T05:30:00Z", "market_time": ts("2026-11-11 05:25"), "price": 4120.0,
             "short_name": "Gold Dec 26"}]}
        fx.save_cache(fx.cache_file(self.dir, "GC=F", "5m"), five)
        lp = fx.latest_price(self.dir, GOLD, utc("2026-11-11 06:20"), CFG)
        self.assertEqual((lp["source"], lp["contract"]), ("yahoo 1h", "Gold Dec 26"))

    def test_plan_scale_and_fx(self):
        cu = dict(GOLD, key="COPPER", yahoo="HG=F", yahoo_to_broker=2204.62, futures={"root": "HG"})
        doc = {"symbol": "HG=F", "interval": "1h", "bars": [], "fetches": [
            {"at": "2026-11-11T08:45:40Z", "market_time": ts("2026-11-11 08:35"), "price": 6.300000190734863}]}
        fx.save_cache(fx.cache_file(self.dir, "HG=F", "1h"), doc)
        lp = fx.latest_price(self.dir, cu, utc("2026-11-11 09:00"), CFG)
        self.assertEqual((lp["bid"], lp["yahoo"]), (13889.11, 6.3))
        doc = {"symbol": "EURUSD=X", "interval": "5m", "bars": [], "fetches": [
            {"at": "2026-11-11T08:45:44Z", "market_time": ts("2026-11-11 08:45"), "price": 1.1239099740982056,
             "short_name": "EUR/USD"}]}
        fx.save_cache(fx.cache_file(self.dir, "EURUSD=X", "5m"), doc)
        lp = fx.latest_price(self.dir, EUR, utc("2026-11-11 08:46"), CFG)
        self.assertEqual((lp["bid"], lp["source"], lp["contract"]), (1.12391, "yahoo 5m", None))

    def test_age(self):
        self.assertEqual(fx.data_age_min(utc("2026-11-11 08:35"), utc("2026-11-11 08:46")), 11.0)
        self.assertEqual(fx.data_age_min(utc("2026-11-11 08:47"), utc("2026-11-11 08:46")), 0.0)
        self.assertEqual(fx.STALE_MINUTES, {"spot_fx": 15, "futures": 30})

    def test_coarse_note(self):
        e = dict(EUR, pip=0.0001, point=0.00001)
        self.assertEqual(fx.coarse_note(e, 99), "Yahoo prices for EURUSD are rough (about one price a minute). "
                                                "A stop under 10 pips can be hit without Yahoo showing it.")
        self.assertIsNone(fx.coarse_note(e, 100))
        self.assertIsNone(fx.coarse_note(dict(e, key="USDJPY"), 50))
        self.assertIsNotNone(fx.coarse_note(e, -60))


BRENT = dict(OIL, key="UKOIL", yahoo="BZ=F", hours="ice_brent",
             futures={"root": "BZ", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ", "roll_rule": "bz_ltd",
                      "settle": ["london", "19:30"], "shortname_month": False, "history_check": True})
GAS = dict(OIL, key="NATGAS", yahoo="NG=F",
           futures={"root": "NG", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ", "roll_rule": "ng_ltd",
                    "settle": ["ny", "14:30"], "shortname_month": True, "history_check": True})


class RollCalendar(unittest.TestCase):
    def test_contract_from_name(self):
        self.assertEqual(fx.contract_from_name("Gold Dec 26", "GC", ".CMX"), "GCZ26.CMX")
        self.assertEqual(fx.contract_from_name("Crude Oil Jan 27 ", "CL", ".NYM"), "CLF27.NYM")
        self.assertIsNone(fx.contract_from_name("Brent Crude Oil Last Day Financ", "BZ", ".NYM"))
        self.assertIsNone(fx.contract_from_name(None, "GC", ".CMX"))

    def test_rule_dates(self):
        d = datetime.date
        self.assertEqual(fx.roll_dates("cl_ltd", 2026, 11)[2], d(2026, 10, 21))      # CLX26: last day 10-20
        self.assertEqual(fx.add_business_days(fx.roll_dates("cl_ltd", 2026, 11)[2], -1), d(2026, 10, 20))
        self.assertEqual(fx.roll_dates("cl_ltd", 2026, 12)[2], d(2026, 11, 23))      # CLZ26: last day 11-20
        self.assertEqual(fx.roll_dates("bz_ltd", 2026, 12)[2], d(2026, 11, 2))       # BZZ26: last day 10-30
        self.assertEqual(fx.roll_dates("ng_ltd", 2026, 11)[2], d(2026, 10, 29))      # NGX26: last day 10-28
        a, b, y = fx.roll_dates("gc_fnd", 2026, 12)
        self.assertEqual((y, a, b), (d(2026, 11, 30), d(2026, 11, 16), d(2026, 12, 1)))
        with self.assertRaisesRegex(ValueError, '^Unknown roll_rule "x"'):
            fx.roll_dates("x", 2026, 12)

    def test_expected_front_and_rolls(self):
        self.assertEqual(fx.expected_front("GC", utc("2026-11-11 00:00"), GOLD), "GCZ26.CMX")
        self.assertEqual(fx.expected_front("GC", utc("2026-07-01 00:00"), GOLD), "GCQ26.CMX")
        self.assertEqual(fx.expected_front("CL", utc("2026-11-11 00:00"), OIL), "CLZ26.NYM")
        rolls = fx.expected_rolls("GC", GOLD, utc("2026-07-01 00:00"), utc("2026-08-31 00:00"))
        self.assertEqual([(r[0], r[1], r[4]) for r in rolls], [("GCQ26.CMX", "GCZ26.CMX", datetime.date(2026, 7, 31))])
        self.assertTrue(rolls[0][2] <= utc("2026-07-29 07:00") < rolls[0][3])
        bz = fx.expected_rolls("BZ", BRENT, utc("2026-10-25 00:00"), utc("2026-10-31 00:00"))
        self.assertIn(("BZZ26.NYM", "BZF27.NYM"), [(r[0], r[1]) for r in bz])


SAMPLE_RATIO = 2500.3 / 2478.0
ROLL = {"id": "GC-2026-07-29", "root": "GC", "yahoo": "GC=F", "old": "GCQ26.CMX", "new": "GCZ26.CMX",
        "first_bar_new": "2026-07-29T07:00:00Z", "ratio": SAMPLE_RATIO, "old_price": 2478.0,
        "new_price": 2500.3, "found_by": "shortname", "confidence": "exact", "replaced_bar": True,
        "found_at": "2026-07-29T12:00:00Z",
        "replacement": {"1h": [ts("2026-07-29 07:00"), 2499.89990234375, 2503.0, 2498.0, 2501.0, 40]}}
GOLD2 = dict(GOLD, tick_size=0.01, digits=2)


def bar(text, o, h, l, c, v=1):
    return {"start": utc(text), "open": o, "high": h, "low": l, "close": c, "volume": v}


class Rolls(unittest.TestCase):
    def test_adjust_rows(self):
        bars = [bar("2026-07-29 06:00", 2477.0, 2479.0, 2476.0, 2478.0), bar("2026-07-29 07:00", 2478.0, 2502.0, 2477.0, 2500.9),
                bar("2026-07-29 08:00", 2501.0, 2502.0, 2500.0, 2501.5)]
        got = fx.adjust_rows(bars, [ROLL], "GC")
        self.assertEqual(got[2], bars[2])                      # after the roll: real Yahoo prices
        self.assertEqual(got[0]["close"], 2500.3)              # 2478.0 x ratio
        self.assertEqual(got[0]["open"], round(2477.0 * SAMPLE_RATIO, 1))
        self.assertEqual((got[1]["open"], got[1]["close"], got[1]["volume"]), (2499.9, 2501.0, 40))
        self.assertNotIn("flags", got[1])
        self.assertEqual(bars[0]["close"], 2478.0)             # the input is not changed
        no_rep = dict(ROLL, replacement={"1h": None}, replaced_bar=False)
        got = fx.adjust_rows(bars, [no_rep], "GC")
        self.assertEqual((got[1]["close"], got[1]["flags"]), (2500.9, ["roll"]))
        self.assertEqual(fx.adjust_rows(bars, [no_rep], "CL"), bars)   # other roots: untouched
        five = [bar("2026-07-29 06:55", 2478.0, 2478.0, 2478.0, 2478.0), bar("2026-07-29 07:30", 2490.0, 2490.0, 2490.0, 2490.0),
                bar("2026-07-29 08:00", 2501.0, 2501.0, 2501.0, 2501.0)]
        got = fx.adjust_rows(five, [ROLL], "GC", "5m")
        self.assertEqual([b.get("flags") for b in got], [None, ["roll"], None])
        self.assertEqual(got[0]["close"], 2500.3)

    def test_rebase(self):
        plan = {"symbol": "XAUUSD", "side": "buy", "entry": 2433.49, "stop_loss": 2400.0, "take_profit": 2490.0,
                "chart_entry": 2433.4, "chart_stop": 2400.0, "rolls_applied": {}}
        fields = ["chart_entry", "chart_stop", "entry", "stop_loss", "take_profit"]
        new, ids = fx.rebase(plan, [ROLL], fields, GOLD2)
        self.assertEqual((new["entry"], new["stop_loss"], new["take_profit"]), (2455.39, 2421.59, 2512.41))
        self.assertEqual(new["rolls_applied"], {"GC-2026-07-29": SAMPLE_RATIO})
        self.assertAlmostEqual(SAMPLE_RATIO, 1.0089992, places=7)
        self.assertEqual(ids, ["GC-2026-07-29"])
        self.assertEqual(plan["entry"], 2433.49)
        again, ids = fx.rebase(new, [ROLL], fields, GOLD2)
        self.assertEqual((again, ids), (new, []))
        # an exact record replaces the estimate (same id): moved by ratio / ratio_applied
        est = dict(plan, rolls_applied={"GC-2026-07-29": 1.0})
        moved, ids = fx.rebase(est, [ROLL], fields, GOLD2)
        self.assertEqual((moved["entry"], ids), (2455.39, ["GC-2026-07-29"]))
        # a sell: the stop above the entry rounds up
        sell = {"entry": 2433.49, "stop_loss": 2460.0, "rolls_applied": {}}
        new, _ = fx.rebase(sell, [ROLL], ["entry", "stop_loss"], GOLD2)
        self.assertEqual(new["stop_loss"], math.ceil(2460.0 * SAMPLE_RATIO * 100) / 100)

    def test_rebase_paths(self):
        order = {"entry": 2433.49, "plan": {"chart_entry": 2433.4, "chart_stop": 2400.0}, "rolls_applied": {}}
        new, ids = fx.rebase(order, [ROLL], ["entry", "plan.chart_entry", "plan.chart_stop"], GOLD2)
        self.assertEqual(new["plan"]["chart_stop"], 2421.59)
        self.assertEqual(new["plan"]["chart_entry"], 2455.3)
        self.assertEqual(fx.rebase({"level": 1.1, "rolls_applied": {}}, [], ["level"], GOLD2)[1], [])

    def test_rebase_only_own_root(self):
        cl = {"id": "CL-2026-07-20", "root": "CL", "ratio": 0.961, "first_bar_new": "2026-07-20T07:00:00Z"}
        plan = {"entry": 2433.49, "stop_loss": 2400.0, "rolls_applied": {}}
        new, ids = fx.rebase(plan, [ROLL, cl], ["entry", "stop_loss"], GOLD2)
        self.assertEqual(ids, ["GC-2026-07-29"])
        self.assertEqual((new["entry"], new["stop_loss"]), (2455.39, 2421.59))
        self.assertEqual(new["rolls_applied"], {"GC-2026-07-29": SAMPLE_RATIO})
        eur = {"level": 1.1, "rolls_applied": {}}
        moved, ids = fx.rebase(eur, [ROLL, cl], ["level"], EUR)
        self.assertEqual((moved, ids), (eur, []))
        with self.assertRaises(ValueError) as e:
            fx.rebase(plan, [ROLL], ["entry"])
        self.assertEqual(str(e.exception), "rebase needs the instrument row")


def answer(rows, meta=None):
    """A made-up chart answer (bytes) from full raw rows, lists in a shuffled order."""
    q = {"close": [r[4] for r in rows], "low": [r[3] for r in rows], "volume": [r[5] for r in rows],
         "open": [r[1] for r in rows], "high": [r[2] for r in rows]}
    m = {"regularMarketTime": rows[-1][0] + 600 if rows else 0, "regularMarketPrice": rows[-1][4] if rows else None}
    m.update(meta or {})
    res = {"meta": m, "indicators": {"quote": [q]}}
    if rows:
        res["timestamp"] = [r[0] for r in rows]
    return json.dumps({"chart": {"result": [res], "error": None}}).encode()


def roll_market(now=utc("2026-07-29 12:30")):
    """Made-up GC=F 1h bars around a roll at 07:00 UTC on 2026-07-29, and the two contracts."""
    old, new, cont = [], [], []
    S = ts("2026-07-29 07:00")
    for i, r in enumerate(hourly_rows("cme", "2026-07-27", "2026-07-29", 2470.0)):
        t = r[0]
        if fx.ts_to_utc(t) > now - H:
            break
        oc = 2478.0 if t == S - 3600 else round(2460.0 + 0.1 * i, 1)
        nc = 2500.3 if t == S - 3600 else round(oc * 1.009, 1)
        o = [t, oc - 0.5, oc + 1, oc - 1, oc, 10]
        n = [t, nc - 0.5, nc + 1, nc - 1, nc, 5]
        old.append(o)
        new.append(n)
        if t < S:
            cont.append(list(o))
        elif t == S:
            cont.append([t, o[1], n[2], o[3], n[4], 12])
        else:
            cont.append(list(n))
    return old, new, cont


class RollForward(NetCase):
    def setUp(self):
        NetCase.setUp(self)
        self.dir = tempfile.mkdtemp()
        self.now = utc("2026-07-29 12:30")
        self.old, self.new, cont = roll_market(self.now)
        doc = {"symbol": "GC=F", "interval": "1h", "bars": cont,
               "fetches": [{"at": "2026-07-29T06:20:00Z", "market_time": ts("2026-07-29 06:10"),
                            "price": 2478.0, "short_name": "Gold Aug 26"},
                           {"at": "2026-07-29T12:20:00Z", "market_time": ts("2026-07-29 12:10"),
                            "price": 2510.0, "short_name": "Gold Dec 26"}]}
        fx.save_cache(fx.cache_file(self.dir, "GC=F", "1h"), doc)

    def test_first_run_is_never_a_roll(self):
        # live bug of 2026-11-13: one fetch only (a first fill) must not record a roll
        doc = fx.load_cache(self.dir, "GC=F", "1h")
        doc["fetches"] = doc["fetches"][-1:]
        fx.save_cache(fx.cache_file(self.dir, "GC=F", "1h"), doc)
        n = self.net([])
        self.assertIsNone(fx.check_roll_forward(self.dir, GOLD, CFG, self.now, False))
        self.assertEqual(n.urls, [])
        self.assertEqual(fx.load_rolls(self.dir), [])

    def tearDown(self):
        NetCase.tearDown(self)
        shutil.rmtree(self.dir)

    def test_exact_in_bar_roll(self):
        n = self.net([(200, answer(self.old)), (200, answer(self.new))])
        rec = fx.check_roll_forward(self.dir, GOLD, CFG, self.now, False)
        self.assertIn("GCQ26.CMX", n.urls[0])
        self.assertIn("GCZ26.CMX", n.urls[1])
        self.assertEqual((rec["id"], rec["first_bar_new"], rec["old"], rec["new"]),
                         ("GC-2026-07-29", "2026-07-29T07:00:00Z", "GCQ26.CMX", "GCZ26.CMX"))
        self.assertAlmostEqual(rec["ratio"], 1.0089992, places=7)
        self.assertEqual(rec["ratio"], 2500.3 / 2478.0)            # not rounded
        self.assertEqual((rec["old_price"], rec["new_price"], rec["prices_at"]), (2478.0, 2500.3, "2026-07-29T06:00:00Z"))
        self.assertEqual((rec["confidence"], rec["found_by"], rec["replaced_bar"]), ("exact", "shortname", True))
        self.assertEqual(rec["replacement"]["1h"][0], ts("2026-07-29 07:00"))
        self.assertEqual(fx.load_rolls(self.dir)[0]["id"], "GC-2026-07-29")
        self.assertTrue(os.path.exists(fx.cache_file(self.dir, "GCZ26.CMX", "1h")))
        # the next run: rolls.json knows GCZ26, so no change and no request
        n = self.net([])
        self.assertIsNone(fx.check_roll_forward(self.dir, GOLD, CFG, self.now + H, False))
        self.assertEqual(n.urls, [])
        self.assertIsNone(fx.check_roll_forward(self.dir, GOLD, CFG, self.now, True))   # offline

    def test_no_replacement_bar(self):
        new = [r for r in self.new if r[0] != ts("2026-07-29 07:00")]
        self.net([(200, answer(self.old)), (200, answer(new))])
        rec = fx.check_roll_forward(self.dir, GOLD, CFG, self.now, False)
        self.assertEqual((rec["replaced_bar"], rec["replacement"], rec["confidence"]), (False, {"1h": None}, "exact"))
        bars = fx.load_bars(self.dir, "GC=F", "1h", self.now, fx.load_rolls(self.dir), inst=GOLD, cfg=CFG)
        hit = [b for b in bars if b["start"] == utc("2026-07-29 07:00")][0]
        self.assertEqual(hit["flags"], ["roll"])

    def test_old_gone(self):
        b404 = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found"}}}'
        self.net([(404, b404), (200, answer(self.new))])
        rec = fx.check_roll_forward(self.dir, GOLD, CFG, self.now, False)
        self.assertEqual(rec["confidence"], "estimated")
        self.assertEqual(rec["note"], "The old gold contract is gone from Yahoo. The roll size is an estimate.")
        self.assertEqual(rec["first_bar_new"], "2026-07-29T07:00:00Z")
        self.assertAlmostEqual(rec["ratio"], 2500.3 / 2478.0)
        # an exact record later replaces the estimate and keeps its id
        exact = dict(rec, id="GC-2026-07-30", confidence="exact", first_bar_new="2026-07-29T08:00:00Z")
        got = fx._keep_roll(self.dir, fx.load_rolls_doc(self.dir), exact, self.now)
        self.assertEqual((got["id"], got["confidence"]), ("GC-2026-07-29", "exact"))
        self.assertEqual(len(fx.load_rolls(self.dir)), 1)
        again = dict(rec, confidence="estimated")
        self.assertIsNone(fx._keep_roll(self.dir, fx.load_rolls_doc(self.dir), again, self.now))

    def test_new_gone_too(self):
        b404 = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"x"}}}'
        self.net([(404, b404), (404, b404)])
        with self.assertRaises(fx.NoData) as cm:
            fx.check_roll_forward(self.dir, GOLD, CFG, self.now, False)
        self.assertEqual(str(cm.exception), "Yahoo gold moved to a new contract, but Yahoo has no data for it. "
                                            "The roll size will come from daily prices.")
        self.assertEqual(fx.load_rolls(self.dir), [])

    def test_brent_match(self):
        now = utc("2026-10-28 12:30")
        rows = [r for r in hourly_rows("cme", "2026-10-27", "2026-10-28", 80.0) if fx.ts_to_utc(r[0]) < now - H]
        fx.save_cache(fx.cache_file(self.dir, "BZ=F", "1h"), {"symbol": "BZ=F", "interval": "1h", "bars": rows,
                      "fetches": [{"at": "2026-10-28T12:20:00Z", "market_time": rows[-1][0] + 3000,
                                   "price": 80.0, "short_name": "Brent Crude Oil Last Day Financ"}]})
        n = self.net([(200, answer(rows[-24:]))])
        self.assertIsNone(fx.check_roll_forward(self.dir, BRENT, CFG, now, False))   # BZZ26 still matches
        self.assertIn("BZZ26.NYM", n.urls[0])
        self.assertEqual(fx.load_rolls_doc(self.dir)["checked"]["BZ"]["last_forward"], "2026-10-28T12:30:00Z")
        n = self.net([])
        self.assertIsNone(fx.check_roll_forward(self.dir, BRENT, CFG, now + 2 * H, False))   # 6-hour limit
        self.assertEqual(n.urls, [])
        n = self.net([(200, answer(rows[-24:]))])
        self.assertIsNone(fx.check_roll_forward(self.dir, BRENT, CFG, now + 2 * H, False, every_run=True))
        self.assertEqual(len(n.urls), 1)
        self.assertIsNone(fx.check_roll_forward(self.dir, BRENT, CFG, utc("2026-12-10 12:00"), False))  # no window


def history_market(ratio=1.009):
    """Made-up GC=F 1h and 1d caches, 2026-06-15 to 2026-08-07. The 1h series moves to
    the new contract (old x ratio) at 07:00 UTC on 07-29 (an in-bar switch); the 1d
    series stays on the old contract up to Thu 07-30 and is on the new one from 07-31."""
    S = utc("2026-07-29 07:00")
    old = lambda t: 2400.0 + 5.0 * math.sin((t - utc("2026-06-01 00:00")).total_seconds() / 3600.0 / 7.0)
    rows = []
    for r in hourly_rows("cme", "2026-06-15", "2026-08-07", 2400.0):
        t = fx.ts_to_utc(r[0])
        o, c = old(t), old(t + H)
        f_o = ratio if t > S else 1.0
        f_c = ratio if t >= S else 1.0
        o, c = o * f_o, c * f_c
        rows.append([r[0], o, max(o, c) + 0.3, min(o, c) - 0.3, c, 7])
    inst = dict(GOLD)
    by_end = dict((fx.ts_to_utc(r[0]) + H, round(r[4], 1)) for r in rows)
    daily = []
    d = datetime.date(2026, 6, 15)
    flip = 1
    while d <= datetime.date(2026, 8, 7):
        s = fx._settle_value(by_end, inst, d, CFG) if d.weekday() < 5 else None
        if s is not None:
            if d >= datetime.date(2026, 7, 29) and d < datetime.date(2026, 7, 31):
                s = s / ratio                       # the 1d series is still on the old contract
            flip = -flip
            stamp = fx.ny_to_utc(fx.at_time(d, "00:00"))
            daily.append([fx.utc_to_ts(stamp), s, s, s, s * (1 + flip * 0.0002), 100])
        d += datetime.timedelta(days=1)
    return rows, daily


class RollHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        rows, daily = history_market()
        now = utc("2026-08-08 10:00")
        for interval, bars in (("1h", rows), ("1d", daily)):
            fx.save_cache(fx.cache_file(self.dir, "GC=F", interval), {"symbol": "GC=F", "interval": interval,
                          "bars": bars, "fetches": [{"at": fx.utc_iso(now), "market_time": bars[-1][0] + 3000}]})
        self.now = now

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_finds_the_roll(self):
        got = fx.find_rolls_history(self.dir, GOLD, CFG, self.now, True)
        self.assertEqual(len(got), 1)
        rec = got[0]
        self.assertEqual((rec["id"], rec["first_bar_new"], rec["found_by"], rec["confidence"]),
                         ("GC-2026-07-29", "2026-07-29T07:00:00Z", "daily_gap", "estimated"))
        self.assertLess(abs(rec["ratio"] / 1.009 - 1), 0.001)
        self.assertEqual((rec["old"], rec["new"]), ("GCQ26.CMX", "GCZ26.CMX"))
        self.assertEqual(rec["note"], "The switch bar is flagged roll.")
        doc = fx.load_rolls_doc(self.dir)
        self.assertEqual(doc["checked"]["GC"]["history_to"], "2026-07-31")
        self.assertEqual(fx.find_rolls_history(self.dir, GOLD, CFG, self.now, True), [])   # checked: not again
        self.assertEqual(len(fx.load_rolls(self.dir)), 1)

    def test_exact_roll_is_kept(self):
        exact = dict(ROLL, ratio=1.00899, first_bar_new="2026-07-29T07:00:00Z")
        fx.save_rolls(self.dir, [exact], {}, self.now)
        self.assertEqual(fx.find_rolls_history(self.dir, GOLD, CFG, self.now, True), [])
        self.assertEqual(fx.load_rolls(self.dir)[0]["confidence"], "exact")

    def test_no_history_for_silver(self):
        silver = dict(GOLD, futures=dict(GOLD["futures"], root="SI", history_check=False))
        self.assertEqual(fx.find_rolls_history(self.dir, silver, CFG, self.now, True), [])

    def test_a_miss(self):
        rows, daily = history_market(ratio=1.0)          # no gap: the window is a miss
        for interval, bars in (("1h", rows), ("1d", daily)):
            fx.save_cache(fx.cache_file(self.dir, "GC=F", interval), {"symbol": "GC=F", "interval": interval,
                                                                      "bars": bars, "fetches": []})
        self.assertEqual(fx.find_rolls_history(self.dir, GOLD, CFG, self.now, True), [])
        self.assertEqual(fx.load_rolls_doc(self.dir)["checked"]["GC"]["missed"], ["2026-07-31"])


class FakeAtr(object):
    """Swaps atr_wilder and usable_bars for fakes: the ATR is a fixed number."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.saved = (fx.atr_wilder, fx.usable_bars)
        fx.atr_wilder = lambda bars, n=14: self.value if len(bars) > n else None
        fx.usable_bars = lambda bars, outages=None: [dict(b, after_gap=False) for b in bars]
        return self

    def __exit__(self, *a):
        fx.atr_wilder, fx.usable_bars = self.saved
        return False


GOLDB = dict(GOLD2, kind="metal")
OILB = dict(OIL, tick_size=0.01, digits=2)
EURB = dict(EUR, tick_size=0.00001, digits=5)


class BrokerPrices(unittest.TestCase):
    def test_gold_example(self):
        k = fx.broker_k(GOLDB, 4121.9, 4130.2)
        self.assertEqual(k, 4121.9 / 4130.2)
        self.assertAlmostEqual(k, 0.99799, places=5)
        e = fx.to_broker(4142.5, k, GOLDB)
        self.assertEqual(e, 4134.18)
        self.assertEqual(fx.to_broker_away(4024.8, 4142.5, k, GOLDB, e), 4016.48)
        self.assertEqual(fx.to_broker_away(4377.9, 4142.5, k, GOLDB, e), 4369.58)
        self.assertEqual(fx.to_broker_away(4024.8, 4142.5, k, GOLDB), 4016.48)
        with FakeAtr(3.2):
            bars = [bar("2026-11-11 08:%02d" % (i * 3), 1, 1, 1, 1) for i in range(20)]
            self.assertEqual(fx.delay_error(GOLDB, 11, bars), 4.75)
            self.assertEqual(fx.delay_error(GOLDB, 0.5, bars), 0.0)
            self.assertEqual(fx.delay_error(GOLDB, 1440, [], daily_atr=30.0), 30.0)
            self.assertEqual(fx.delay_error(GOLDB, 11, []), 0.0)

    def test_sample_market(self):
        k = fx.broker_k(GOLDB, 2429.15, 2433.4)
        e = fx.to_broker(2433.49, k, GOLDB)
        self.assertEqual(e, 2429.24)
        self.assertEqual(fx.to_broker_away(2400.0, 2433.49, k, GOLDB, e), 2395.75)
        self.assertEqual(fx.to_broker_away(2490.0, 2433.49, k, GOLDB, e), 2485.75)

    def test_eurusd_and_oil(self):
        e = fx.to_broker(1.08452, 1.0, EURB)
        self.assertEqual((e, fx.to_broker_away(1.0859, 1.08452, 1.0, EURB, e),
                          fx.to_broker_away(1.08176, 1.08452, 1.0, EURB, e)), (1.08452, 1.0859, 1.08176))
        k = fx.broker_k(OILB, 86.41, 86.85)
        self.assertAlmostEqual(k, 0.99493, places=5)
        e = fx.to_broker(87.40, k, OILB)
        self.assertEqual(e, 86.96)
        self.assertEqual(fx.to_broker_away(85.20, 87.40, k, OILB, e), 84.76)     # not 84.75 (float noise)
        self.assertEqual(fx.to_broker_away(91.80, 87.40, k, OILB, e), 91.36)

    def test_k_limits(self):
        with self.assertRaises(ValueError) as cm:
            fx.broker_k(OILB, 8.64, 86.85)
        self.assertEqual(str(cm.exception), "Your broker price 8.64 for USOIL is far from Yahoo's 86.85 "
                                            "(more than 20%). Check the symbol and the price you typed.")
        with self.assertRaisesRegex(ValueError, r"\(more than 3%\)"):
            fx.broker_k(GOLDB, 4000.0, 4130.2)
        with self.assertRaisesRegex(ValueError, r"\(more than 1%\)"):
            fx.broker_k(EURB, 1.1, 1.08)
        with self.assertRaisesRegex(ValueError, r"^A price must be above 0\.$"):
            fx.broker_k(GOLDB, 0, 4130.2)
        with self.assertRaisesRegex(ValueError, r"^A price must be above 0\.$"):
            fx.broker_k(GOLDB, 4100.0, -1)
        cu = dict(GOLDB, yahoo_to_broker=2204.62)
        self.assertAlmostEqual(fx.broker_k(cu, 13889.11, 6.3), 13889.11 / (6.3 * 2204.62))

    def test_saved_broker_price(self):
        cfg = dict(CFG, instruments={"XAUUSD": {"futures": {"root": "GC"}}, "EURUSD": {"futures": None}})
        rec = {"bid": 4118.35, "at": "2026-11-11T08:46:10Z", "yahoo_bid": 4126.2,
               "yahoo_at": "2026-11-11T08:35:30Z", "k": 0.998098}
        prices = {"broker": {"XAUUSD": rec}}
        self.assertEqual(fx.saved_broker_price(prices, "XAUUSD", utc("2026-11-11 09:30"), cfg, []), rec)
        self.assertEqual(fx.saved_broker_price(prices, "XAUUSD", utc("2026-11-11 09:46"), cfg, []), rec)
        self.assertIsNone(fx.saved_broker_price(prices, "XAUUSD", utc("2026-11-11 09:47"), cfg, []))   # 61 min
        self.assertIsNone(fx.saved_broker_price(prices, "EURUSD", utc("2026-11-11 09:30"), cfg, []))
        late = {"broker": {"XAUUSD": dict(rec, at="2026-11-11T21:40:00Z")}}
        self.assertIsNotNone(fx.saved_broker_price(late, "XAUUSD", utc("2026-11-11 21:59"), cfg, []))
        self.assertIsNone(fx.saved_broker_price(late, "XAUUSD", utc("2026-11-11 22:05"), cfg, []))  # a new FX day
        roll = {"root": "GC", "first_bar_new": "2026-11-11T09:00:00Z"}
        self.assertIsNone(fx.saved_broker_price(prices, "XAUUSD", utc("2026-11-11 09:30"), cfg, [roll]))
        old_roll = {"root": "GC", "first_bar_new": "2026-11-11T08:00:00Z"}
        self.assertEqual(fx.saved_broker_price(prices, "XAUUSD", utc("2026-11-11 09:30"), cfg, [old_roll]), rec)
        self.assertEqual(fx.saved_broker_price(prices, "XAUUSD", utc("2026-11-11 09:30"), cfg,
                                               [dict(roll, root="CL")]), rec)


class RollGuard(unittest.TestCase):
    def test_guard(self):
        bz_now = utc("2026-10-28 12:30")
        bars = [dict(bar("2026-10-27 16:00", 80.0, 80.2, 79.8, 80.0), start=utc("2026-10-27 16:00") + i * H)
                for i in range(20)]
        self.assertEqual(bars[-1]["start"], utc("2026-10-28 11:00"))
        jump = bars[:-1] + [bar("2026-10-28 11:00", 80.0, 80.1, 76.0, 76.0)]
        with FakeAtr(0.3):
            self.assertIsNone(fx.roll_guard(BRENT, bars, [], bz_now, CFG))
            self.assertEqual(fx.roll_guard(BRENT, jump, [], bz_now, CFG),
                             "Yahoo Brent may have rolled. Check your MetaTrader price.")
            gap = bars[:-1] + [bar("2026-10-28 11:00", 77.0, 77.1, 76.9, 77.0)]
            self.assertIsNotNone(fx.roll_guard(BRENT, gap, [], bz_now, CFG))
            found = [{"root": "BZ", "first_bar_new": "2026-10-28T11:00:00Z"}]
            self.assertIsNone(fx.roll_guard(BRENT, jump, found, bz_now, CFG))
            self.assertIsNone(fx.roll_guard(BRENT, jump, [], utc("2026-12-10 12:00"), CFG))   # no window
            self.assertIsNone(fx.roll_guard(EUR, jump, [], bz_now, CFG))
