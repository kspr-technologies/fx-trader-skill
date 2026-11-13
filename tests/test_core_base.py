"""Tests of fx_core: files and config (not load_config), ORDER_FIELDS, fingerprint, words."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

import fx_core


def old_pack_fingerprint(order):
    """The earlier pack's fingerprint rule, copied here to compare."""
    core = {k: order[k] for k in fx_core.ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


GOLD_ORDER = {"symbol": "XAUUSD", "side": "buy", "intent": "open", "lots": 0.01, "type": "market",
              "entry": 4131.59, "stop_loss": 3994.7, "take_profit": 4405.37, "expiry": "gtc",
              "style": "swing", "broker_symbol": "XAUUSD", "position_id": None,
              "decided_by": "risk_pct"}
EUR_ORDER = {"symbol": "EURUSD", "side": "sell", "intent": "open", "lots": 2.43, "type": "market",
             "entry": 1.12395, "stop_loss": 1.12471, "take_profit": 1.12243,
             "expiry": "2026-11-11T18:30:00Z", "style": "day", "broker_symbol": "EURUSD",
             "position_id": None}


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def path(self, name, text=None):
        p = os.path.join(self.dir, name)
        if text is not None:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
        return p

    def test_order_fields(self):
        self.assertEqual(fx_core.ORDER_FIELDS, ("symbol", "side", "intent", "lots", "type", "entry",
                                                "stop_loss", "take_profit", "expiry", "style"))
        self.assertEqual(fx_core.FRESH_CHECK_MINUTES, 5)

    def test_fingerprint_spec_values(self):
        self.assertEqual(fx_core.fingerprint(GOLD_ORDER), "e24b8aab2ed53715")
        self.assertEqual(fx_core.fingerprint(EUR_ORDER), "6ee0d21e4ae596a2")

    def test_fingerprint_matches_psx(self):
        for order in (GOLD_ORDER, EUR_ORDER, {"symbol": "X", "lots": None}, {}):
            self.assertEqual(fx_core.fingerprint(order), old_pack_fingerprint(order))

    def test_fingerprint_skips_null_and_extra_fields(self):
        a = dict(GOLD_ORDER, why="other words", plan={"x": 1})
        self.assertEqual(fx_core.fingerprint(a), "e24b8aab2ed53715")
        b = dict(GOLD_ORDER)
        del b["broker_symbol"]
        self.assertEqual(fx_core.fingerprint(b), "e24b8aab2ed53715")
        c = dict(GOLD_ORDER, lots=0.02)
        self.assertNotEqual(fx_core.fingerprint(c), "e24b8aab2ed53715")

    def test_load_json(self):
        self.assertIsNone(fx_core.load_json(self.path("none.json")))
        self.assertEqual(fx_core.load_json(self.path("none.json"), default={}), {})
        self.assertEqual(fx_core.load_json(self.path("a.json", '{"a": "é"}')), {"a": "é"})

    def test_load_json_empty(self):
        p = self.path("e.json", "")
        with self.assertRaises(ValueError) as cm:
            fx_core.load_json(p)
        self.assertEqual(str(cm.exception), "%s is empty" % p)

    def test_load_json_broken(self):
        p = self.path("b.json", "{oops")
        with self.assertRaises(ValueError) as cm:
            fx_core.load_json(p)
        self.assertTrue(str(cm.exception).startswith("%s is not valid JSON: " % p))
        p2 = self.path("c.json", " ")
        with self.assertRaises(ValueError) as cm:
            fx_core.load_json(p2)
        self.assertEqual(str(cm.exception),
                         "%s is not valid JSON: Expecting value: line 1 column 2 (char 1)" % p2)

    def test_save_json_atomic(self):
        p = os.path.join(self.dir, "state", "deep", "x.json")
        doc = {"b": 1, "a": [1.5, "é"]}
        fx_core.save_json_atomic(p, doc)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.endswith("}\n"))
        self.assertIn("é", text)
        self.assertLess(text.index('"b"'), text.index('"a"'))
        self.assertEqual(json.loads(text), doc)
        self.assertFalse(os.path.exists(p + ".tmp"))
        self.assertEqual(os.listdir(os.path.dirname(p)), ["x.json"])

    def test_save_json_atomic_overwrites_old_tmp(self):
        p = self.path("y.json")
        self.path("y.json.tmp", "junk from a crash")
        fx_core.save_json_atomic(p, {"ok": True})
        self.assertEqual(fx_core.load_json(p), {"ok": True})
        self.assertFalse(os.path.exists(p + ".tmp"))

    def test_save_json_atomic_error(self):
        blocker = self.path("file", "x")
        p = os.path.join(blocker, "x.json")
        with self.assertRaises(ValueError) as cm:
            fx_core.save_json_atomic(p, {})
        self.assertTrue(str(cm.exception).startswith("could not save %s: " % p))

    def test_fx_paths(self):
        j = os.path.join
        p = fx_core.fx_paths("fx")
        self.assertEqual(p["config"], j("fx", "config.json"))
        for key in ("alerts", "watchlist", "entry", "plan", "timemachine", "orders", "prices",
                    "portfolio"):
            self.assertEqual(p[key], j("fx", key + ".json"))
        self.assertEqual(p["risk_state"], j("fx", "state", "risk.json"))
        self.assertEqual(p["broker_state"], j("fx", "state", "broker.json"))
        self.assertEqual(p["alerts_state"], j("fx", "state", "alerts.json"))
        self.assertEqual(p["news_state"], j("fx", "state", "news.json"))
        self.assertEqual(p["cot_state"], j("fx", "state", "cot.json"))
        self.assertEqual(p["data"], j("fx", "data"))
        self.assertEqual(p["yahoo"], j("fx", "data", "yahoo"))
        self.assertEqual(p["rolls"], j("fx", "data", "rolls.json"))
        self.assertEqual(p["calendar"], j("fx", "data", "calendar"))
        self.assertEqual(p["cot"], j("fx", "data", "cot"))
        self.assertEqual(p["news"], j("fx", "data", "news"))
        self.assertEqual(len(p), 20)

    def test_fx_paths_data_and_config(self):
        p = fx_core.fx_paths("fx", data_dir="m/data", config="m/config.json")
        self.assertEqual(p["config"], "m/config.json")
        self.assertEqual(p["data"], "m/data")
        self.assertEqual(p["yahoo"], os.path.join("m/data", "yahoo"))
        self.assertEqual(p["plan"], os.path.join("fx", "plan.json"))


class ReaderChecks(unittest.TestCase):
    P = "fx/plan.json"

    def msg(self, fn, *args):
        with self.assertRaises(ValueError) as cm:
            fn(*args)
        return str(cm.exception)

    def test_load_doc(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            self.assertEqual(self.msg(fx_core.load_doc, p, "plan", "fx-exit"),
                             "no plan at %s. Run fx-exit first." % p)
            self.assertIsNone(fx_core.load_doc(p, "plan", "fx-exit", required=False))
            for text in ("[1, 2]", '"x"', "3"):
                with open(p, "w") as fh:
                    fh.write(text)
                self.assertEqual(self.msg(fx_core.load_doc, p, "plan", "fx-exit"),
                                 "%s must be a JSON object: {...}" % p)
            with open(p, "w") as fh:
                fh.write('{"a": 1}')
            self.assertEqual(fx_core.load_doc(p, "plan", "fx-exit"), {"a": 1})
        finally:
            shutil.rmtree(d)

    def test_check_objects(self):
        self.assertEqual(fx_core.check_objects({"orders": [{}]}, "orders", self.P), [{}])
        for doc in ({}, {"orders": {}}, {"orders": [1]}):
            self.assertEqual(self.msg(fx_core.check_objects, doc, "orders", self.P),
                             'fx/plan.json: "orders" must be a list of JSON objects: [{...}]')

    def test_check_symbol(self):
        cfg = {"instruments": {"XAUUSD": {}}}
        self.assertEqual(fx_core.check_symbol("XAUUSD", cfg, self.P), "XAUUSD")
        self.assertEqual(self.msg(fx_core.check_symbol, "XAUUSDm", cfg, self.P),
                         'fx/plan.json: "XAUUSDm" is not in your instrument list '
                         '(fx/config.json "instruments")')

    def test_side_and_style(self):
        self.assertEqual(fx_core.check_side("sell", self.P), "sell")
        self.assertEqual(self.msg(fx_core.check_side, "long", self.P),
                         'fx/plan.json: side must be "buy" or "sell", not "long"')
        self.assertEqual(fx_core.check_style("day", self.P), "day")
        self.assertEqual(self.msg(fx_core.check_style, "scalp", self.P),
                         'fx/plan.json: style must be "swing" or "day", not "scalp"')

    def test_check_utc(self):
        self.assertEqual(fx_core.check_utc({"at": "2026-11-11T08:40:00Z"}, "at", self.P),
                         fx_core.datetime.datetime(2026, 11, 11, 8, 40))
        for v in ("2026-11-11 08:40", None, 5, "2026-11-11T08:40:00"):
            self.assertEqual(self.msg(fx_core.check_utc, {"at": v}, "at", self.P),
                             'fx/plan.json: "at" is not a UTC time like 2026-11-11T08:40:00Z')

    def test_check_price_and_lots(self):
        self.assertEqual(fx_core.check_price({"entry": 1.1}, "entry", self.P), 1.1)
        for v in (0, -1.0, "1.1", None, True, float("nan")):
            self.assertIn('fx/plan.json: "entry" must be a price above 0, not ',
                          self.msg(fx_core.check_price, {"entry": v}, "entry", self.P))
        self.assertEqual(self.msg(fx_core.check_price, {"entry": -2}, "entry", self.P),
                         'fx/plan.json: "entry" must be a price above 0, not -2')
        self.assertEqual(fx_core.check_lots(0.01, self.P), 0.01)
        self.assertEqual(self.msg(fx_core.check_lots, 0, self.P),
                         "fx/plan.json: lots must be a number above 0, not 0")

    def test_check_close_order(self):
        good = {"symbol": "XAUUSD", "side": "buy", "intent": "close", "lots": 0.01,
                "type": "market", "entry": None, "stop_loss": None, "take_profit": None,
                "expiry": None, "style": "swing", "position_id": "XAUUSD-1"}
        self.assertIs(fx_core.check_close_order(good, 1, "fx/orders.json"), good)
        want = ("fx/orders.json: order 2 closes a position. Leave entry, stop_loss, "
                "take_profit and expiry empty (null). Use type market. Set position_id.")
        for bad in (dict(good, entry=4131.59), dict(good, type="limit"),
                    dict(good, position_id=None), dict(good, expiry="gtc")):
            self.assertEqual(self.msg(fx_core.check_close_order, bad, 2, "fx/orders.json"), want)


GOLD = {"key": "XAUUSD", "name": "Gold", "digits": 2, "price_source": "futures"}
EURUSD = {"key": "EURUSD", "name": "Euro / US dollar", "digits": 5, "price_source": "spot_fx"}
USDJPY = {"key": "USDJPY", "digits": 3, "price_source": "spot_fx"}
USOIL = {"key": "USOIL", "name": "WTI crude oil", "digits": 2, "price_source": "futures"}


class Words(unittest.TestCase):
    def test_money_table(self):
        for x, want in ((1234.564, "$1,234.56"), (1234.565, "$1,234.57"), (-12.3, "-$12.30"),
                        (0, "$0.00"), (1000000, "$1,000,000.00"), (-0.004, "$0.00"),
                        (Decimal("2.005"), "$2.01")):
            self.assertEqual(fx_core.money(x), want)

    def test_money_bad(self):
        for x in (None, float("nan"), "12", True, float("inf")):
            with self.assertRaises(ValueError) as cm:
                fx_core.money(x)
            self.assertEqual(str(cm.exception), "Not an amount of money: %s" % (x,))

    def test_signed_money(self):
        self.assertEqual(fx_core.signed_money(12.3), "+$12.30")
        self.assertEqual(fx_core.signed_money(-12.3), "-$12.30")
        self.assertEqual(fx_core.signed_money(0), "$0.00")
        self.assertEqual(fx_core.signed_money(0.004), "$0.00")
        self.assertEqual(fx_core.signed_money(1234.565), "+$1,234.57")

    def test_price_text(self):
        self.assertEqual(fx_core.price_text(2433.4, GOLD), "2,433.40")
        self.assertEqual(fx_core.price_text(1.0845, EURUSD), "1.08450")
        self.assertEqual(fx_core.price_text(157.2, USDJPY), "157.200")
        self.assertEqual(fx_core.price_text(-3.5, USOIL), "-3.50")
        self.assertEqual(fx_core.price_text(1.124905, EURUSD), "1.12491")
        self.assertRaises(ValueError, fx_core.price_text, None, GOLD)

    def test_lots_text(self):
        for x, want in ((1, "1.00 lot"), (0.86, "0.86 lots"), (0.02, "0.02 lots"),
                        (12.5, "12.50 lots"), (0.005, "0.005 lots"), (Decimal("1.000"), "1.00 lot"),
                        (100, "100.00 lots"), (0, "0.00 lots")):
            self.assertEqual(fx_core.lots_text(x), want)
        with self.assertRaises(ValueError) as cm:
            fx_core.lots_text(-0.01)
        self.assertEqual(str(cm.exception), "Lots cannot be below 0.")

    def test_side_word(self):
        self.assertEqual(fx_core.side_word("buy"), "BUY")
        self.assertEqual(fx_core.side_word("sell"), "SELL")
        with self.assertRaises(ValueError) as cm:
            fx_core.side_word("long")
        self.assertEqual(str(cm.exception), 'Side must be "buy" or "sell", not long.')

    def test_age_text(self):
        for m, want in ((0.4, "live"), (11.7, "11 min old"), (125, "2 h 5 min old"),
                        (180, "3 h old"), (2900, "2 days old"), (None, "age unknown"),
                        (-5, "live"), (1, "1 min old"), (59.9, "59 min old"), (60, "1 h old"),
                        (1439.9, "23 h 59 min old"), (1440, "1 day old")):
            self.assertEqual(fx_core.age_text(m), want)

    def test_scale_note(self):
        self.assertEqual(fx_core.scale_note(EURUSD), "Yahoo price, close to your broker's price")
        self.assertEqual(fx_core.scale_note(GOLD),
                         "Yahoo price (gold futures), not your broker's price")
        self.assertEqual(fx_core.scale_note(dict(USOIL, name="WTI oil")),
                         "Yahoo price (WTI oil futures), not your broker's price")
        self.assertRaises(ValueError, fx_core.scale_note, {"price_source": "stock"})

    def test_broker_note(self):
        self.assertEqual(fx_core.broker_note(), "your broker's price (you typed it)")

    @unittest.skipUnless(hasattr(fx_core, "pkt_label") and hasattr(fx_core, "ny_label"),
                         "the clock group is not built yet")
    def test_both_times(self):
        utc = fx_core.datetime.datetime(2026, 7, 23, 7, 40)
        self.assertEqual(fx_core.both_times(utc), "Thu 12:40 PM PKT (3:40 AM New York)")

    def test_add_common(self):
        import argparse
        p = fx_core.add_common(argparse.ArgumentParser())
        a = p.parse_args([])
        self.assertEqual((a.config, a.data, a.offline, a.now, a.json, a.quiet),
                         (None, None, False, None, False, False))
        self.assertFalse(hasattr(a, "write"))
        a = fx_core.add_common(argparse.ArgumentParser(), write=True).parse_args(
            ["--config", "c.json", "--data", "d", "--offline", "--now", "2026-07-23 12:40",
             "--json", "--quiet", "--write"])
        self.assertEqual((a.config, a.data, a.offline, a.now, a.json, a.quiet, a.write),
                         ("c.json", "d", True, "2026-07-23 12:40", True, True, True))

    def test_fail(self):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = fx_core.fail(ValueError("no plan at fx/plan.json.\nRun fx-exit first."), "size.py")
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "size.py: no plan at fx/plan.json. Run fx-exit first.\n")
        self.assertEqual(out.getvalue(), "")

    def test_emit_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            fx_core.emit_json({"v": fx_core.JSON_VERSION, "b": 1, "a": "é"})
        text = out.getvalue()
        self.assertEqual(json.loads(text), {"v": 1, "b": 1, "a": "é"})
        self.assertLess(text.index('"a"'), text.index('"b"'))
        self.assertIn("é", text)
        self.assertEqual(fx_core.JSON_VERSION, 1)


class Imports(unittest.TestCase):
    def test_every_import_is_used(self):
        import ast
        with open(fx_core.__file__.replace(".pyc", ".py"), encoding="utf-8") as f:
            text = f.read()
        tree = ast.parse(text)
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                parts = []
                while isinstance(node, ast.Attribute):
                    parts.append(node.attr)
                    node = node.value
                if isinstance(node, ast.Name):
                    parts.append(node.id)
                    parts.reverse()
                    for i in range(1, len(parts) + 1):
                        used.add(".".join(parts[:i]))
            elif isinstance(node, ast.Name):
                used.add(node.id)
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertIn(a.name, used, "import %s is not used" % a.name)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    self.assertIn(a.asname or a.name, used, "%s is not used" % a.name)


class CopyChecker(unittest.TestCase):
    """tests/check_copies.py compares whole lines: decorators and end comments count."""

    def setUp(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_copies.py")
        spec = importlib.util.spec_from_file_location("check_copies", path)
        self.cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cc)
        self.core = self.cc.top_nodes(self.cc.read(self.cc.CORE), self.cc.CORE)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def script(self, text):
        path = os.path.join(self.tmp, "s.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_whole_lines(self):
        line = self.core["KARACHI_HOURS"][1]
        self.assertIn("#", line)
        self.assertEqual(self.cc.check(self.script(line + "\n"), self.core), ([], 1))
        changed = self.script(line.split("#")[0] + "# my own note\n")
        self.assertEqual(len(self.cc.check(changed, self.core)[0]), 1)
        self.assertEqual(self.cc.sync(changed, self.core), 1)
        self.assertEqual(self.cc.check(changed, self.core), ([], 1))
        deco = self.script("import functools\n\n\n@functools.lru_cache()\n" + self.core["load_json"][1] + "\n")
        self.assertEqual(len(self.cc.check(deco, self.core)[0]), 1)
        self.assertEqual(self.cc.sync(deco, self.core), 1)
        with open(deco, encoding="utf-8") as fh:
            self.assertNotIn("lru_cache()", fh.read())


if __name__ == "__main__":
    unittest.main()
