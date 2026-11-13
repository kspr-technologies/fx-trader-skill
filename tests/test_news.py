"""Tests for fx-news/news.py. Offline only: the opener, http_get, SLEEP and CLOCK are
swapped for fakes, and the data is a temp copy of the made-up sample market."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib
import datetime
import glob
import gzip
import io
import json
import shutil
import subprocess
import tempfile
import unittest

import _load

news = _load.load("fx-news", "news")
MARKET = os.path.join(_load.FIX, "fx-market")
SCRIPT = os.path.join(_load.ROOT, "fx-news", "news.py")
GOLD = "Gold climbs to a three-week high as the dollar slips"
EURUSD_ITEM = "EUR/USD slips before the ECB decision"
ECB_ITEM = "Monetary policy decisions"
FEEDS = ["fxstreet", "investinglive", "investing-commodities", "investing-forex", "oilprice",
         "cnbc-energy", "fed-monetary", "ecb-press", "boe-news", "boj-news"]


def utc(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")


def rss(items, title="Test feed"):
    """Feed bytes from [(title, link, pubDate, description)]."""
    parts = ["<item><title>%s</title><link>%s</link><pubDate>%s</pubDate>"
             "<description>%s</description></item>" % it for it in items]
    return ('<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>%s'
            "</title>%s</channel></rss>" % (title, "".join(parts))).encode("utf-8")


class NewsCase(unittest.TestCase):
    """A temp project: fx/config.json and fx/data from the sample market."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fxnews-")
        self.fx = os.path.join(self.tmp, "fx")
        os.makedirs(self.fx)
        shutil.copytree(os.path.join(MARKET, "data"), os.path.join(self.fx, "data"))
        shutil.copy(os.path.join(MARKET, "config.json"), os.path.join(self.fx, "config.json"))
        self.config = os.path.join(self.fx, "config.json")
        self.data = os.path.join(self.fx, "data")
        self.state = os.path.join(self.fx, "state", "news.json")
        self.swapped = (news.http_get, news.SLEEP, news.CLOCK, news.OPEN_URL)
        self.calls = []
        news.http_get = self.no_network
        news.OPEN_URL = self.no_network
        news._last_get[0] = None

    def tearDown(self):
        news.http_get, news.SLEEP, news.CLOCK, news.OPEN_URL = self.swapped
        news._last_get[0] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def no_network(self, *args, **kw):
        self.calls.append(args)
        raise AssertionError("a test tried the network")

    def run_news(self, *args):
        """(code, stdout, stderr) of news.main with --config and --data of the temp copy."""
        argv = ["--config", self.config, "--data", self.data] + list(args)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = news.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write(self, name, doc):
        path = os.path.join(self.fx, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc))
        return path

    def cfg(self):
        return news.load_config(self.config, required=True)

    def item(self, title, text="", feed="investinglive", source="investingLive"):
        return {"key": "k:" + title, "links": ["k:" + title], "title": title, "link": "",
                "text": (title + " " + text).strip(), "at": utc("2026-07-23 06:00"),
                "source": source, "feed": feed}

    def about(self, title, symbols=None, **kw):
        cfg = self.cfg()
        it = news.tag_items([self.item(title, **kw)], symbols or list(cfg["universe"]), cfg)[0]
        return {a["symbol"]: a["how"] for a in it["about"]}, it


class TestParse(NewsCase):

    def test_sample_feeds_parse(self):
        counts = {}
        for path in sorted(glob.glob(os.path.join(self.data, "news", "*.xml"))):
            name = os.path.basename(path)[:-4]
            with open(path, "rb") as fh:
                items, bad = news.parse_feed(fh.read(), {"name": name})
            counts[name] = len(items)
            self.assertEqual(bad, 0)
            for it in items:
                self.assertTrue(it["title"] and it["key"])
                self.assertIsInstance(it["at"], datetime.datetime)
                self.assertIsNone(it["at"].tzinfo)
        self.assertEqual(sorted(counts), sorted(FEEDS))
        self.assertEqual(counts["boj-news"], 0)
        for name in FEEDS:
            if name != "boj-news":
                self.assertTrue(3 <= counts[name] <= 6, name)
        # (m) measured on the sample market, then frozen
        self.assertEqual(counts, {"fxstreet": 4, "investinglive": 3, "investing-commodities": 4,
                                  "investing-forex": 3, "oilprice": 3, "cnbc-energy": 3,
                                  "fed-monetary": 3, "ecb-press": 3, "boe-news": 3,
                                  "boj-news": 0})

    def test_ecb_title_is_stripped_and_link_kept(self):
        with open(os.path.join(self.data, "news", "ecb-press.xml"), "rb") as fh:
            items, _ = news.parse_feed(fh.read(), {"name": "ecb-press"})
        titles = [it["title"] for it in items]
        self.assertIn(ECB_ITEM, titles)
        self.assertTrue(all(not t.startswith(" ") for t in titles))
        self.assertEqual(items[0]["source"], "ECB")

    def test_date_formats(self):
        f = news.parse_feed_time
        self.assertEqual(f("Thu, 23 Jul 2026 14:15:00 +0200"), utc("2026-07-23 12:15"))
        self.assertEqual(f("Wed, 22 Jul 2026 11:05:00 -0400"), utc("2026-07-22 15:05"))
        self.assertEqual(f("Tue, 10 Nov 2026 00:45:00 -0600"), utc("2026-11-10 06:45"))
        self.assertEqual(f("Thu, 16 Jul 2026 21:40:00 GMT"), utc("2026-07-16 21:40"))
        self.assertEqual(f("Wed, 22 Jul 2026 13:00:00 Z"), utc("2026-07-22 13:00"))
        self.assertEqual(f("Tue, 10 Nov 2026  00:45:00 GMT"), utc("2026-11-10 00:45"))
        self.assertEqual(f("2026-11-10 06:38:55"), datetime.datetime(2026, 11, 10, 6, 38, 55))
        self.assertEqual(f("Nov 09, 2026 15:51 GMT"), utc("2026-11-09 15:51"))
        self.assertEqual(f("2026-11-09T09:00:00Z"), utc("2026-11-09 09:00"))
        self.assertEqual(f("2026-11-05T16:00:19+00:00"), datetime.datetime(2026, 11, 5, 16, 0, 19))
        self.assertEqual(f("2026-11-05T21:00:00+05:00"), utc("2026-11-05 16:00"))
        self.assertIsNone(f("Fri, 09 Oct  09:00:00 EST"))
        self.assertIsNone(f("################### EST"))
        self.assertIsNone(f(""))
        self.assertIsNone(f(None))

    def test_atom_feed(self):
        body = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>'
                b'<entry><title>Gold &amp; silver rise</title><link rel="alternate" '
                b'href="https://example.com/a/1"/><updated>2026-07-23T06:00:00Z</updated>'
                b'<summary>Both metals gain.</summary></entry></feed>')
        items, bad = news.parse_feed(body, {"name": "atom-test"})
        self.assertEqual((len(items), bad), (1, 0))
        self.assertEqual(items[0]["title"], "Gold & silver rise")
        self.assertEqual(items[0]["key"], "https://example.com/a/1")
        self.assertEqual(items[0]["source"], "A")

    def test_not_a_feed_raises(self):
        for body in (b"<html><body>Request Denied</body></html>", b"not xml", b""):
            with self.assertRaises(news.FeedError) as ctx:
                news.parse_feed(body, {"name": "x"})
            self.assertEqual(ctx.exception.error, "not a feed")
            with self.assertRaises(news.FeedError):
                news.check_feed(body)
        news.check_feed(b"\xef\xbb\xbf  <rss><channel/></rss>")

    def test_future_date_is_a_bad_date(self):
        body = rss([("Gold one", "https://e.x/1", "Thu, 23 Jul 2026 06:00:00 GMT", ""),
                    ("Gold two", "https://e.x/2", "Mon, 27 Jul 2026 06:00:00 GMT", "")])
        items, bad = news.parse_feed(body, {"name": "x"}, utc("2026-07-23 07:00"))
        self.assertEqual(([it["title"] for it in items], bad), (["Gold one"], 1))
        items, bad = news.parse_feed(body, {"name": "x"})
        self.assertEqual((len(items), bad), (2, 0))

    def test_bad_date_is_left_out(self):
        body = rss([("Gold rises in Asia", "https://example.com/t/1", "################### EST", ""),
                    ("Gold dips in London", "https://example.com/t/2",
                     "Thu, 16 Jul 2026 20:00:00 GMT", "")])
        with open(os.path.join(self.data, "news", "fxstreet.xml"), "wb") as fh:
            fh.write(body)
        code, out, err = self.run_news("--symbols", "XAUUSD", "--feeds", "fxstreet", "--dry-run",
                                       "--now", "2026-07-17 09:01")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Gold rises in Asia", out)
        self.assertIn("Gold dips in London", out)
        self.assertIn("Note: 1 headline had a date this tool cannot read. It is left out.", out)
        self.assertIn("(1 of 1 found)", out)

    def test_clean_and_lead(self):
        self.assertEqual(news.clean(" <b>Gold</b>&amp;\u00adsil\u200bver \u2014 up "), "Gold &silver - up")
        self.assertEqual(news.lead("word " * 100, 20), "word word word word")
        self.assertEqual(news.lead("short"), "short")


class TestTags(NewsCase):

    def test_pairs_with_and_without_slash(self):
        for title, word in (("XAUUSD drifts in a narrow range", "XAUUSD"),
                            ("XAU/USD: gold eases in quiet Asian trade", "XAU/USD")):
            found, it = self.about(title)
            self.assertEqual(found, {"XAUUSD": "named"}, title)
            self.assertEqual(it["about"][0]["word"], word)
        found, _ = self.about("EUR/USD slips", symbols=["EURUSD", "GBPUSD"])
        self.assertEqual(found, {"EURUSD": "named"})
        found, _ = self.about("Copper: COPPER and USOIL move", symbols=["COPPER", "USOIL", "XAUUSD"])
        self.assertEqual(found, {"COPPER": "named", "USOIL": "named"})
        found, _ = self.about("THEXAUUSDX is not a code", symbols=["XAUUSD"])
        self.assertEqual(found, {})

    def test_commodity_and_currency_words(self):
        found, it = self.about("Oil holds steady as US stocks build",
                               text="Crude prices change little after a build in US crude stocks.")
        self.assertEqual(found, {"USOIL": "named", "UKOIL": "named"})
        self.assertEqual(it["tags"], ["data"])
        found, it = self.about("Dutch inflation rises")
        self.assertEqual(found, dict.fromkeys(["EURUSD", "EURJPY", "EURGBP", "EURCHF"], "currency"))
        self.assertEqual(it["about"][0]["word"], "EUR word dutch")
        found, _ = self.about("Yen slides as BoJ holds")
        self.assertEqual(found, dict.fromkeys(["USDJPY", "EURJPY", "GBPJPY", "AUDJPY"], "currency"))
        found, _ = self.about("Natural gas falls after a mild weather outlook")
        self.assertEqual(found, {"NATGAS": "named"})
        found, _ = self.about("Silver gains with platinum on supply worries")
        self.assertEqual(found, {"XAGUSD": "named", "XPTUSD": "named"})

    def test_usd_touches_metals_and_named_wins(self):
        found, it = self.about(GOLD, text="Gold extends its gains as the US dollar loses ground.")
        self.assertEqual(found["XAUUSD"], "named")
        for key in ("XAGUSD", "XPTUSD", "XPDUSD", "EURUSD", "USDJPY", "USDCAD"):
            self.assertEqual(found[key], "currency", key)
        self.assertNotIn("EURGBP", found)
        self.assertNotIn("USOIL", found)
        words = {a["symbol"]: a["word"] for a in it["about"]}
        self.assertEqual((words["XAUUSD"], words["EURUSD"]), ("gold", "USD word dollar"))
        found, _ = self.about("China data lifts the yuan")
        self.assertEqual(found, dict.fromkeys(["COPPER", "AUDUSD", "NZDUSD", "AUDJPY"], "currency"))

    def test_people_and_codes(self):
        found, it = self.about("Powell speaks on jobs", symbols=["EURUSD", "XAUUSD"])
        self.assertEqual(found, {"EURUSD": "currency", "XAUUSD": "currency"})
        self.assertEqual(it["about"][0]["word"], "USD word Powell")
        found, _ = self.about("powell is a word here", symbols=["EURUSD"])
        self.assertEqual(found, {})
        found, _ = self.about("AUD/NZD edges up in thin trade", symbols=["AUDUSD", "NZDUSD", "EURUSD"])
        self.assertEqual(found, {"AUDUSD": "currency", "NZDUSD": "currency"})
        found, _ = self.about("Lagarde and Vuj\u010di\u0107 speak", symbols=["EURUSD"])
        self.assertEqual(found, {"EURUSD": "currency"})

    def test_traps(self):
        found, _ = self.about("A pound of copper costs more")
        self.assertEqual(found, {"COPPER": "named"})
        found, _ = self.about("Kiwi fruit exports reach a record")
        self.assertEqual(found, {})
        found, _ = self.about("Gas prices at the pump ease before the summer peak")
        self.assertEqual(found, {})
        found, it = self.about("US-Iran talks weigh on crude",
                               text="Traders watch the talks for supply news.")
        self.assertEqual(found, {"USOIL": "named", "UKOIL": "named"})
        found, _ = self.about("US and U.S. leaders meet", symbols=["EURUSD", "XAUUSD"])
        self.assertEqual(found, {})
        found, _ = self.about("Pound rises as BoE holds", symbols=["GBPUSD"])
        self.assertEqual(found, {"GBPUSD": "currency"})
        found, _ = self.about("Kiwi rises as RBNZ holds", symbols=["NZDUSD"])
        self.assertEqual(found, {"NZDUSD": "currency"})

    def test_feed_currency(self):
        found, it = self.about(ECB_ITEM, feed="ecb-press", source="ECB")
        self.assertEqual(found, dict.fromkeys(["EURUSD", "EURJPY", "EURGBP", "EURCHF"], "feed"))
        self.assertEqual(it["about"][0]["word"], "ECB feed")
        self.assertTrue(it["important"])
        self.assertEqual(it["tags"], ["central bank"])
        found, it = self.about("Speech by a member", feed="boe-news", source="Bank of England",
                               symbols=["GBPUSD", "XAUUSD"])
        self.assertEqual(found, {"GBPUSD": "feed"})
        self.assertFalse(it["important"])

    def test_important_reads_the_title_only(self):
        _, it = self.about("Gold edges up", text="Traders wait for the rate decision and CPI.")
        self.assertFalse(it["important"])
        self.assertEqual(it["tags"], ["central bank", "data"])
        _, it = self.about("US CPI rises 0.3% in June")
        self.assertTrue(it["important"])

    def test_dedupe_and_about_text(self):
        cfg = self.cfg()
        a = self.item("Gold rises!", feed="fxstreet", source="FXStreet")
        b = self.item("GOLD rises", text="The dollar slips.", feed="investinglive")
        b["key"], b["links"] = "https://e.x/b", ["https://e.x/b"]
        items = news.tag_items([a, b], ["XAUUSD", "EURUSD"], cfg)
        out = news.dedupe(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["feed"], "fxstreet")
        self.assertEqual(out[0]["links"], ["k:Gold rises!", "https://e.x/b"])
        self.assertEqual(sorted(x["symbol"] for x in out[0]["about"]), ["EURUSD", "XAUUSD"])
        about = [{"symbol": "EURUSD", "how": "currency", "word": "USD word dollar"},
                 {"symbol": "XAUUSD", "how": "named", "word": "gold"},
                 {"symbol": "GBPUSD", "how": "currency", "word": "USD word dollar"},
                 {"symbol": "EURGBP", "how": "feed", "word": "Bank of England feed"}]
        self.assertEqual(news.about_text(about), "XAUUSD (gold); EURUSD, GBPUSD (USD word dollar); "
                                                 "EURGBP (Bank of England feed)")


EXAMPLE_A = ["--symbols", "XAUUSD", "--dry-run", "--offline", "--now", "2026-07-17 09:01"]


class TestRuns(NewsCase):

    def test_chain_step(self):
        """The test_chain step, through the real command line in the project folder."""
        p = subprocess.run([sys.executable, SCRIPT, "--symbols", "XAUUSD", "--dry-run",
                            "--offline", "--now", "2026-07-17 09:01"], cwd=self.tmp,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        for text in (GOLD, "PKT", "New York", "1 new headline."):
            self.assertIn(text, p.stdout)
        self.assertFalse(os.path.exists(self.state))
        self.assertFalse(os.path.exists(os.path.join(self.fx, "state")))

    def test_example_a_words(self):
        code, out, err = self.run_news(*EXAMPLE_A)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "1 new headline.")   # (m)
        self.assertEqual(lines[1], "For: XAUUSD (from --symbols).")
        self.assertEqual(lines[2], "From Wed 15 Jul 9:01 AM PKT to now (the last 48 hours).")
        self.assertEqual(lines[4], "Fri 17 Jul 2:40 AM PKT (5:40 PM New York)  FXStreet")
        self.assertEqual(lines[5], "    " + GOLD)
        self.assertEqual(lines[6], "    About: XAUUSD (gold).")
        self.assertIn("Offline: the saved feeds in fx/data/news/ are used (10 of 10 found).", out)
        self.assertIn("Dry run: nothing was marked as seen.", out)
        self.assertTrue(all(len(x) <= 78 for x in lines))

    def test_new_once_newest_first(self):
        args = ["--offline", "--now", "2026-07-23 12:41", "--symbols", "EURUSD,XAUUSD"]
        code, out, err = self.run_news(*args)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("1 new headline.\n"))   # (m)
        self.assertIn(EURUSD_ITEM, out)
        self.assertIn("About: EURUSD (EUR/USD). Tags: central bank.", out)
        with open(self.state, encoding="utf-8") as fh:
            st = json.load(fh)
        link = "https://example.com/investinglive/eur-usd-slips-before-the-ecb-decision"
        self.assertEqual(st["seen"], {link: "2026-07-23T07:41:00Z"})
        self.assertEqual(st["last_check"], "2026-07-23T07:41:00Z")
        code, out, err = self.run_news(*args)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("Nothing new.\n"))
        code, out, err = self.run_news(*(args + ["--all"]))
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("1 headline (0 new).\n"))
        self.assertIn(EURUSD_ITEM + " (seen before)", out)
        code, out, err = self.run_news("--offline", "--now", "2026-07-23 18:00", "--all",
                                       "--symbols", "EURUSD,XAUUSD")
        self.assertTrue(out.startswith("2 headlines (1 new).\n"))
        self.assertLess(out.index(ECB_ITEM), out.index(EURUSD_ITEM))   # newest first

    def test_now_cuts_later_items(self):
        code, out, _ = self.run_news("--symbols", "EURUSD", "--dry-run", "--now", "2026-07-23 12:41")
        self.assertIn(EURUSD_ITEM, out)
        self.assertNotIn(ECB_ITEM, out)
        code, out, _ = self.run_news("--symbols", "EURUSD", "--dry-run", "--now", "2026-07-23 18:00")
        self.assertEqual(code, 0)
        self.assertIn("Thu 23 Jul 5:15 PM PKT (8:15 AM New York)  ECB\n"
                      "  ! Monetary policy decisions\n"
                      "    About: EURUSD (ECB feed). Tags: central bank.\n"
                      "    https://example.com/ecb-press/monetary-policy-decisions\n", out)
        self.assertIn("This tool shows no prices. For a price, use /fx-alerts quote.", out)
        # 10 minutes late still counts; 11 do not
        code, out, _ = self.run_news("--symbols", "EURUSD", "--dry-run", "--now", "2026-07-23 17:05")
        self.assertIn(ECB_ITEM, out)
        code, out, _ = self.run_news("--symbols", "EURUSD", "--dry-run", "--now", "2026-07-23 17:04")
        self.assertNotIn(ECB_ITEM, out)

    def test_symbols_from_files(self):
        self.write("watchlist.json", {"mode": "day", "hits": [
            {"symbol": "EURUSD", "name": "Euro / US dollar", "side": "sell", "style": "day"}]})
        self.write("plan.json", {"symbol": "XAUUSD", "side": "buy", "style": "swing"})
        self.write("portfolio.json", {"positions": [{"symbol": "XAUUSD", "side": "buy",
                                                     "style": "swing"}]})
        code, out, err = self.run_news("--offline", "--now", "2026-07-23 12:41")
        self.assertEqual(code, 0, err)
        self.assertIn("For: EURUSD, XAUUSD (from your watchlist, plan and open trades).", out)
        self.assertTrue(out.startswith("1 new headline.\n"))   # (m) example B
        self.assertIn(EURUSD_ITEM, out)
        self.assertNotIn("Oil holds steady", out)
        other = self.write("other-watch.json", {"mode": "swing", "hits": [{"symbol": "USOIL"}]})
        os.remove(os.path.join(self.fx, "watchlist.json"))
        code, out, err = self.run_news("--offline", "--now", "2026-07-23 12:41", "--watchlist",
                                       other, "--dry-run")
        self.assertIn("For: USOIL, XAUUSD (from your watchlist, plan and open trades).", out)
        self.assertIn("Oil holds steady as US stocks build", out)

    def test_universe_when_no_files(self):
        code, out, err = self.run_news("--offline", "--now", "2026-07-23 12:41", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("No watchlist, plan or open trades. Showing news for your whole universe "
                      "(20\ninstruments).", out)
        self.assertIn("(your whole universe: no watchlist, plan or open trades).", out.replace("\n", " "))
        self.assertTrue(out.startswith("3 new headlines.\n"))   # (m)

    def test_quiet_and_max(self):
        code, out, _ = self.run_news("--offline", "--now", "2026-07-23 12:41", "--dry-run", "--quiet")
        self.assertNotIn("For:", out)
        self.assertNotIn("Checked", out)
        self.assertNotIn("Note", out)
        code, out, _ = self.run_news("--offline", "--now", "2026-07-23 12:41", "--dry-run", "--max", "2")
        self.assertTrue(out.startswith("2 new headlines.\n"))
        self.assertIn("Note: 1 more headline is not shown. Use --max 4 to see it.", out)
        code, out, _ = self.run_news("--offline", "--now", "2026-07-23 12:41", "--dry-run", "--max", "2",
                                     "--hours", "720")
        self.assertIn("Note: 14 more headlines are not shown. Use --max 4 to see them.", out)   # (m)
        code, out, _ = self.run_news("--offline", "--now", "2026-07-23 12:41", "--dry-run", "--max", "300",
                                     "--hours", "720")
        self.assertTrue(out.startswith("16 new headlines.\n"))   # (m)
        code, out, _ = self.run_news("--offline", "--now", "2026-07-23 12:41", "--dry-run", "--hours", "1")
        self.assertIn("(the last 1 hour).", out)
        self.assertTrue(out.startswith("1 new headline.\n"))

    def test_skill_md_example_is_real_output(self):
        with open(os.path.join(_load.ROOT, "fx-news", "SKILL.md"), encoding="utf-8") as fh:
            md = fh.read()
        part = md.split("## Example", 1)[1]
        block = part.split("```\n", 2)[1].split("\n```", 1)[0]
        code, out, err = self.run_news(*EXAMPLE_A)
        self.assertEqual(code, 0, err)
        self.assertEqual(block.strip(), out.strip())
        self.assertTrue(md.rstrip().endswith("Not financial advice. Nothing in this pack places an order."))
        self.assertNotIn("## Safety", md)


class TestState(NewsCase):

    def test_seen_kept_30_days(self):
        self.write("state/news.json", {"seen": {"https://old.example/31": "2026-06-22T07:00:00Z",
                                                "https://old.example/29": "2026-06-24T07:00:00Z"},
                                       "last_check": "2026-06-24T07:00:00Z",
                                       "feeds": {"gone-feed": {"tried_at": "2026-06-24T07:00:00Z",
                                                               "fetched_at": None, "error": None}}})
        code, out, err = self.run_news("--symbols", "EURUSD", "--now", "2026-07-23 12:41")
        self.assertEqual(code, 0, err)
        with open(self.state, encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertNotIn("https://old.example/31", st["seen"])
        self.assertIn("https://old.example/29", st["seen"])
        self.assertEqual(st["feeds"], {})   # a feed no longer in news.feeds is dropped
        self.assertEqual(sorted(st), ["feeds", "last_check", "seen"])

    def test_broken_state(self):
        self.write("state/news.json", "{")
        code, out, err = self.run_news("--symbols", "EURUSD", "--now", "2026-07-23 12:41")
        self.assertEqual(code, 0, err)
        self.assertIn("fx/state/news.json was damaged. Started a new one.", out)
        broken = glob.glob(os.path.join(self.fx, "state", "news.broken-*.json"))
        self.assertEqual([os.path.basename(b) for b in broken], ["news.broken-20260723T074100Z.json"])
        with open(broken[0], encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{")
        with open(self.state, encoding="utf-8") as fh:
            self.assertIn("eur-usd-slips", fh.read())

    def test_broken_state_dry_run_touches_nothing(self):
        self.write("state/news.json", "[1, 2]")
        code, out, err = self.run_news("--symbols", "EURUSD", "--now", "2026-07-23 12:41", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("was damaged", out)
        self.assertEqual(os.listdir(os.path.join(self.fx, "state")), ["news.json"])

    def test_dry_run_leaves_the_state_alone(self):
        path = self.write("state/news.json", {"seen": {}, "last_check": None, "feeds": {}})
        with open(path, "rb") as fh:
            before = fh.read()
        self.run_news("--symbols", "EURUSD", "--now", "2026-07-23 12:41", "--dry-run")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_save_bytes_atomic(self):
        path = os.path.join(self.tmp, "a", "b.xml")
        news.save_bytes_atomic(path, b"<rss/>")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"<rss/>")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["b.xml"])
        blocker = os.path.join(self.tmp, "file")
        with open(blocker, "w") as fh:
            fh.write("x")
        with self.assertRaises(ValueError) as ctx:
            news.save_bytes_atomic(os.path.join(blocker, "x.xml"), b"")
        self.assertTrue(str(ctx.exception).startswith("could not save "))


class FakeNet:
    """A fake http_get with a fake clock: answers[name] is bytes or a FeedError."""

    def __init__(self, case, answers):
        self.case, self.answers, self.calls, self.clock = case, answers, [], [100.0]
        self.urls = {f["url"]: f["name"] for f in case.cfg()["news"]["feeds"]}
        news.http_get = self
        news.CLOCK = lambda: self.clock[0]
        news.SLEEP = self.sleep

    def sleep(self, seconds):
        self.clock[0] += seconds

    def __call__(self, url, timeout=news.NEWS_TIMEOUT):
        name = self.urls[url]
        self.calls.append((name, self.clock[0]))
        self.clock[0] += 0.2     # the request takes a little time
        answer = self.answers[name]
        if isinstance(answer, Exception):
            raise answer
        return answer


def fresh_body(name):
    when = news.utc_now() - datetime.timedelta(hours=1)
    return rss([("Gold news from %s" % name, "https://example.com/fake/%s" % name,
                 when.strftime("%a, %d %b %Y %H:%M:%S GMT"), "Gold moves.")])


class TestOnline(NewsCase):

    def saved(self, name):
        with open(os.path.join(self.data, "news", name + ".xml"), "rb") as fh:
            return fh.read()

    def test_online_fetch_saves_and_paces(self):
        boj = self.saved("boj-news")
        answers = {n: fresh_body(n) for n in FEEDS}
        answers["boj-news"] = news.FeedError("http 403")
        net = FakeNet(self, answers)
        code, out, err = self.run_news("--symbols", "XAUUSD")
        self.assertEqual(code, 0, err)
        self.assertEqual([c[0] for c in net.calls], FEEDS)
        gaps = [b[1] - a[1] for a, b in zip(net.calls, net.calls[1:])]
        self.assertTrue(all(g >= news.NEWS_PACE_S - 1e-9 for g in gaps), gaps)
        for name in FEEDS[:-1]:
            self.assertEqual(self.saved(name), answers[name], name)
        self.assertEqual(self.saved("boj-news"), boj)
        self.assertIn("Feeds: 9 of 10 read now, 1 from a saved copy.", out)
        self.assertIn("Note: Bank of Japan could not be read now (http 403). Using its saved copy",
                      out.replace("\n", " "))
        self.assertTrue(out.startswith("9 new headlines.\n"))
        with open(self.state, encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertEqual(sorted(st["feeds"]), sorted(FEEDS))
        self.assertEqual(st["feeds"]["boj-news"]["error"], "http 403")
        self.assertIsNone(st["feeds"]["boj-news"]["fetched_at"])
        row = st["feeds"]["fxstreet"]
        self.assertEqual((row["error"], row["fetched_at"]), (None, row["tried_at"]))
        self.assertEqual(len(st["seen"]), 9)

    def test_fifteen_minute_rule(self):
        tried = news.utc_iso(news.utc_now() - datetime.timedelta(minutes=5))
        self.write("state/news.json", {"seen": {}, "last_check": tried, "feeds": {
            n: {"tried_at": tried, "fetched_at": tried, "error": None} for n in FEEDS}})
        net = FakeNet(self, {n: fresh_body(n) for n in FEEDS})
        code, out, err = self.run_news("--symbols", "XAUUSD", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(net.calls, [])
        self.assertIn("Feeds: 0 of 10 read now, 10 from a saved copy.", out)
        # 16 minutes ago: asked again
        tried = news.utc_iso(news.utc_now() - datetime.timedelta(minutes=16))
        self.write("state/news.json", {"seen": {}, "feeds": {
            n: {"tried_at": tried, "fetched_at": tried, "error": None} for n in FEEDS}})
        code, out, err = self.run_news("--symbols", "XAUUSD", "--dry-run", "--feeds", "fxstreet")
        self.assertEqual([c[0] for c in net.calls], ["fxstreet"])

    def test_not_a_feed(self):
        before = self.saved("fxstreet")
        FakeNet(self, {"fxstreet": b"<html><body>Request Denied</body></html>"})
        code, out, err = self.run_news("--feeds", "fxstreet", "--symbols", "XAUUSD", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["feeds"][0]["error"], "not a feed")
        self.assertEqual(doc["feeds"][0]["used"], "saved")
        self.assertEqual(self.saved("fxstreet"), before)
        self.assertTrue(any(n.startswith("Note: FXStreet could not be read now (not a feed). "
                                         "Using its saved copy from ") for n in doc["notes"]))

    def test_broken_saved_copy_is_a_note(self):
        with open(os.path.join(self.data, "news", "fxstreet.xml"), "wb") as fh:
            fh.write(b"<html>no</html>")
        code, out, err = self.run_news("--offline", "--symbols", "XAUUSD", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("Note: the saved copy of FXStreet is not a feed. It is left out.", out)

    def test_a_feed_with_no_copy(self):
        os.remove(os.path.join(self.data, "news", "oilprice.xml"))
        code, out, err = self.run_news("--offline", "--symbols", "USOIL", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("(9 of 10 found)", out)
        self.assertIn("Note: no saved copy of OilPrice.com (fx/data/news/oilprice.xml).", out)
        answers = {n: fresh_body(n) for n in FEEDS}
        answers["oilprice"] = news.FeedError("network")
        FakeNet(self, answers)
        code, out, err = self.run_news("--symbols", "USOIL", "--dry-run")
        self.assertIn("Note: OilPrice.com could not be read now (network). It has no saved copy, "
                      "so it is left out.", out.replace("\n", " "))

    def test_offline_makes_no_call(self):
        for args in (["--offline"], ["--now", "2026-07-23 12:41"]):
            code, out, err = self.run_news(*(args + ["--symbols", "XAUUSD"]))
            self.assertEqual(code, 0, err)
        self.assertEqual(self.calls, [])


class TestErrors(NewsCase):

    def assertError(self, args, message):
        code, out, err = self.run_news(*args)
        self.assertEqual((code, out), (2, ""), err)
        self.assertEqual(err, "news.py: %s\n" % message)

    def test_errors(self):
        now = ["--now", "2026-07-23 12:41"]
        self.assertError(now + ["--symbols", "XAUSD"],
                         "XAUSD is not in your instruments. Did you mean XAUUSD?")
        self.assertError(now + ["--symbols", "zzzz"], "ZZZZ is not in your instruments.")
        self.assertError(now + ["--symbols", ","], "--symbols is empty.")
        self.assertError(now + ["--feeds", "fxstret"],
                         "fxstret is not one of your news feeds. Did you mean fxstreet?")
        self.assertError(now + ["--feeds", "zzz"],
                         "zzz is not one of your news feeds. Your feeds are: fxstreet, investinglive, "
                         "investing-commodities, investing-forex, oilprice, cnbc-energy, "
                         "fed-monetary, ecb-press, boe-news, boj-news.")
        self.assertError(now + ["--feeds", " , "], "--feeds is empty.")
        self.assertError(now + ["--hours", "0"], "--hours wants a number from 1 to 720, not 0.")
        self.assertError(now + ["--hours", "721"], "--hours wants a number from 1 to 720, not 721.")
        self.assertError(now + ["--hours", "x"], "--hours wants a number from 1 to 720, not x.")
        self.assertError(now + ["--max", "0"], "--max wants a whole number from 1 to 500, not 0.")
        self.assertError(now + ["--max", "1.5"], "--max wants a whole number from 1 to 500, not 1.5.")
        self.assertError(["--now", "July"],
                         '--now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)')
        shutil.rmtree(os.path.join(self.data, "news"))
        self.assertError(now, "No saved feeds in fx/data/news/. Run without --offline once to get them.")
        self.assertError(["--offline"],
                         "No saved feeds in fx/data/news/. Run without --offline once to get them.")
        FakeNet(self, {n: news.FeedError("network") for n in FEEDS})
        self.assertError([], "No news feed could be read, and there is no saved copy in fx/data/news/. "
                             "Check your internet and run again.")
        self.assertFalse(os.path.exists(self.state))

    def test_empty_feeds_and_bad_config(self):
        with open(self.config, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["news"]["feeds"] = []
        self.write("config.json", doc)
        self.assertError(["--offline"],
                         'Your config has no news feeds ("news.feeds" is empty in fx/config.json).')
        missing = os.path.join(self.tmp, "nope.json")
        argv = ["--config", missing, "--offline"]
        e = io.StringIO()
        with contextlib.redirect_stderr(e), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(news.main(argv), 2)
        self.assertIn("no config file at %s" % missing, e.getvalue())

    def test_broken_chain_files(self):
        now = ["--now", "2026-07-23 12:41"]
        self.write("watchlist.json", {"mode": "week", "hits": []})
        self.assertError(now, 'fx/watchlist.json: "mode" must be "swing" or "day"'.replace(
            "fx/watchlist.json", os.path.join(self.fx, "watchlist.json")))
        self.write("watchlist.json", {"mode": "day", "hits": [{"symbol": "XAUSD"}]})
        self.assertError(now, '%s: "XAUSD" is not in your instrument list (fx/config.json '
                              '"instruments")' % os.path.join(self.fx, "watchlist.json"))
        self.write("watchlist.json", {"mode": "day", "hits": {}})
        self.assertError(now, '%s: "hits" must be a list of JSON objects: [{...}]'
                         % os.path.join(self.fx, "watchlist.json"))
        os.remove(os.path.join(self.fx, "watchlist.json"))
        self.write("plan.json", {"symbol": "XAUUSD", "side": "long"})
        self.assertError(now, '%s: side must be "buy" or "sell", not "long"'
                         % os.path.join(self.fx, "plan.json"))
        self.write("plan.json", "[]")
        self.assertError(now, "%s must be a JSON object: {...}" % os.path.join(self.fx, "plan.json"))
        os.remove(os.path.join(self.fx, "plan.json"))
        self.write("portfolio.json", {"positions": [{"symbol": "XAUUSD", "style": "scalp"}]})
        self.assertError(now, '%s: style must be "swing" or "day", not "scalp"'
                         % os.path.join(self.fx, "portfolio.json"))
        self.write("portfolio.json", "")
        self.assertError(now, "%s is empty" % os.path.join(self.fx, "portfolio.json"))
        # --symbols skips the files
        code, out, err = self.run_news(*(now + ["--symbols", "EURUSD", "--dry-run"]))
        self.assertEqual(code, 0, err)

    def test_help(self):
        p = subprocess.run([sys.executable, SCRIPT, "--help"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--symbols", p.stdout)


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestJsonAndNetwork(NewsCase):

    def test_json_shape(self):
        code, out, err = self.run_news(*(EXAMPLE_A + ["--json"]))
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        for key in ("v", "tool", "cmd", "now", "ok", "doc", "notes", "symbols", "symbols_from",
                    "hours", "dry_run", "items", "left_out", "bad_dates", "feeds"):
            self.assertIn(key, doc)
        self.assertEqual((doc["v"], doc["tool"], doc["cmd"], doc["ok"], doc["doc"]),
                         (1, "fx-news", None, True, None))
        self.assertEqual(doc["now"], "2026-07-17T04:01:00Z")
        self.assertEqual(doc["notes"], ["Offline: the saved feeds in fx/data/news/ are used (10 of 10 found).",
                                        "Dry run: nothing was marked as seen."])
        self.assertEqual((doc["symbols"], doc["symbols_from"], doc["hours"], doc["dry_run"]),
                         (["XAUUSD"], "flag", 48, True))
        self.assertEqual((doc["left_out"], doc["bad_dates"]), (0, 0))
        link = "https://example.com/fxstreet/gold-climbs-to-a-three-week-high"
        self.assertEqual(doc["items"], [{
            "key": link, "links": [link], "link": link, "title": GOLD, "source": "FXStreet",
            "feed": "fxstreet", "at": "2026-07-16T21:40:00Z",
            "about": [{"symbol": "XAUUSD", "how": "named", "word": "gold"}],
            "tags": [], "important": False, "new": True}])
        self.assertEqual(doc["feeds"][0], {"name": "fxstreet", "source": "FXStreet", "used": "saved",
                                           "error": None, "fetched_at": None, "items": 4})   # (m)
        self.assertEqual([r["items"] for r in doc["feeds"]], [4, 3, 4, 3, 3, 3, 3, 3, 3, 0])   # (m)
        self.assertNotIn('"summary"', out)
        self.assertNotIn('"text"', out)

    def test_no_text_republished(self):
        descriptions = ["Gold extends its gains late on Thursday", "The euro eases in early European",
                        "Crude prices change little", "The Committee keeps Bank Rate"]
        runs = [EXAMPLE_A, EXAMPLE_A + ["--json"],
                ["--dry-run", "--now", "2026-07-23 18:00", "--hours", "720", "--all"],
                ["--dry-run", "--now", "2026-07-23 18:00", "--hours", "720", "--json"]]
        for args in runs:
            code, out, err = self.run_news(*args)
            self.assertEqual(code, 0, err)
            for text in descriptions:
                self.assertNotIn(text, out)
        self.run_news("--now", "2026-07-23 18:00", "--hours", "720")
        with open(self.state, encoding="utf-8") as fh:
            st = fh.read()
        for text in descriptions:
            self.assertNotIn(text, st)

    def test_http_get_with_a_fake_opener(self):
        real_get = self.swapped[0]   # the real http_get, with a fake opener
        seen = []

        def opener(answer):
            def fake(req, timeout=None):
                seen.append((req.full_url, req.get_header("User-agent"),
                             req.get_header("Accept-encoding"), timeout))
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return fake

        news.OPEN_URL = opener(FakeResponse(b"<rss/>"))
        self.assertEqual(real_get("https://example.com/feed"), b"<rss/>")
        self.assertEqual(seen[0], ("https://example.com/feed",
                                   "Mozilla/5.0 (compatible; fx-trader-skill/1.0)", "gzip", 20))
        news.OPEN_URL = opener(FakeResponse(gzip.compress(b"<rss>z</rss>"),
                                            headers={"Content-Encoding": "gzip"}))
        self.assertEqual(real_get("https://example.com/feed"), b"<rss>z</rss>")
        cases = [(news.urllib.error.HTTPError("https://e.x", 404, "Not Found", {}, None), "http 404"),
                 (news.urllib.error.URLError("no answer"), "network"),
                 (OSError("reset"), "network"),
                 (FakeResponse(b"x", status=204), "http 204"),
                 (FakeResponse(b"\x1f\x8bbroken", headers={"Content-Encoding": "gzip"}), "network"),
                 (FakeResponse(b"x" * (news.NEWS_MAX_BYTES + 5)), "too big"),
                 (FakeResponse(gzip.compress(b"x" * (news.NEWS_MAX_BYTES + 5)),
                               headers={"Content-Encoding": "gzip"}), "too big")]
        for answer, error in cases:
            news.OPEN_URL = opener(answer)
            with self.assertRaises(news.FeedError) as ctx:
                real_get("https://example.com/feed")
            self.assertEqual(ctx.exception.error, error)

    def test_pace_waits_between_requests(self):
        clock, waits = [50.0], []
        news.CLOCK = lambda: clock[0]
        news.SLEEP = waits.append
        news._last_get[0] = None
        news.news_pace()
        news._last_get[0] = 50.0
        clock[0] = 50.5
        news.news_pace()
        self.assertEqual(waits, [1.0])
        clock[0] = 60.0
        news.news_pace()
        self.assertEqual(waits, [1.0])


class TestNoConfig(unittest.TestCase):

    def test_missing_default_config_gives_the_note(self):
        tmp = tempfile.mkdtemp(prefix="fxnews-")
        try:
            p = subprocess.run([sys.executable, SCRIPT, "--offline"], cwd=tmp, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 2)
            self.assertIn("No fx/config.json yet. Using the example values.", p.stderr)
            self.assertIn("news.py: No saved feeds in fx/data/news/.", p.stderr)
            self.assertEqual(os.listdir(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
