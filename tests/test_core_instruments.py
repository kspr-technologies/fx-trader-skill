"""Tests of fx_core: instruments and rounding (part 04 section 1), on a small made-up cfg."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import unittest

import fx_core


def row(**kw):
    base = {"name": "Euro / US dollar", "kind": "fx", "price_source": "spot_fx", "base": "EUR",
            "quote": "USD", "contract_size": 100000, "digits": 5, "min_lot": 0.01,
            "max_lot": 100, "lot_step": 0.01, "spread_points": 1}
    base.update(kw)
    return base


CFG = {"instruments": {
    "EURUSD": row(),
    "USDJPY": row(name="US dollar / Japanese yen", base="USD", quote="JPY", digits=3),
    "GBPJPY": row(name="British pound / Japanese yen", base="GBP", quote="JPY", digits=3,
                  spread_points=10),
    "XAUUSD": row(name="Gold", kind="metal", price_source="futures", base="XAU", contract_size=100,
                  digits=2, spread_points=9, broker_symbol="XAUUSD.m"),
    "USOIL": row(name="WTI crude oil", kind="energy", price_source="futures", base="WTI",
                 contract_size=1000, digits=2, spread_points=4, broker_symbol=""),
}}


def inst(key, **kw):
    cfg = copy.deepcopy(CFG)
    cfg["instruments"][key].update(kw)
    return fx_core.instrument(cfg, key)


class Instruments(unittest.TestCase):
    def msg(self, fn, *args, **kw):
        with self.assertRaises(ValueError) as cm:
            fn(*args, **kw)
        return str(cm.exception)

    def test_defaults(self):
        e = fx_core.instrument(CFG, "EURUSD")
        self.assertEqual(e["key"], "EURUSD")
        self.assertEqual(e["broker_symbol"], "EURUSD")
        self.assertEqual(e["point"], 0.00001)
        self.assertEqual(e["tick_size"], 0.00001)
        self.assertEqual(e["pip"], 0.0001)
        for k in ("leverage", "initial_margin", "swap_free_fee_per_lot", "futures"):
            self.assertIsNone(e[k])
        self.assertEqual((e["margin_rate"], e["yahoo_to_broker"]), (1.0, 1.0))
        for k in ("swap_free_grace_nights", "commission_per_lot_side", "stops_level_points"):
            self.assertEqual(e[k], 0)
        self.assertEqual(fx_core.instrument(CFG, "USDJPY")["pip"], 0.01)
        g = fx_core.instrument(CFG, "XAUUSD")
        self.assertEqual((g["point"], g["pip"], g["broker_symbol"]), (0.01, None, "XAUUSD.m"))
        self.assertEqual(fx_core.instrument(CFG, "USOIL")["broker_symbol"], "USOIL")

    def test_new_dict(self):
        e = fx_core.instrument(CFG, "EURUSD")
        e["spread_points"] = 99
        self.assertNotIn("key", CFG["instruments"]["EURUSD"])
        self.assertEqual(CFG["instruments"]["EURUSD"]["spread_points"], 1)
        self.assertIsNot(fx_core.instrument(CFG, "EURUSD"), fx_core.instrument(CFG, "EURUSD"))

    def test_given_values_kept(self):
        g = inst("XAUUSD", tick_size=0.05, point=0.01, margin_rate=0.5, commission_per_lot_side=3,
                 yahoo_to_broker=2204.62)
        self.assertEqual((g["tick_size"], g["margin_rate"], g["commission_per_lot_side"],
                          g["yahoo_to_broker"]), (0.05, 0.5, 3, 2204.62))

    def test_unknown_key(self):
        self.assertEqual(self.msg(fx_core.instrument, CFG, "XAUUSDm"),
                         'Unknown instrument "XAUUSDm". Use a key from your config, such as XAUUSD. '
                         'Put your broker\'s name in "broker_symbol".')
        self.assertIn("such as USOIL.", self.msg(fx_core.instrument, CFG, "USOILL"))

    def test_lot_checks(self):
        want = "XAUUSD: contract_size, min_lot, max_lot and lot_step must be above 0."
        for kw in ({"contract_size": 0}, {"lot_step": 0}, {"min_lot": -0.01}, {"max_lot": 0},
                   {"min_lot": 2, "max_lot": 1}):
            self.assertEqual(self.msg(inst, "XAUUSD", **kw), want)
        self.assertEqual(self.msg(inst, "XAUUSD", min_lot=0.015),
                         "XAUUSD: min_lot 0.015 is not a whole number of lot_step 0.01.")
        self.assertEqual(inst("XAUUSD", min_lot=0.1, lot_step=0.01)["min_lot"], 0.1)

    def test_tick_and_point_checks(self):
        self.assertEqual(self.msg(inst, "XAUUSD", tick_size=0.005),
                         "XAUUSD: tick_size 0.005 does not fit digits 2.")
        self.assertEqual(self.msg(inst, "XAUUSD", point=0.001),
                         "XAUUSD: point 0.001 does not match digits 2. Copy both from MetaTrader.")


class Rounding(unittest.TestCase):
    def setUp(self):
        self.eur = fx_core.instrument(CFG, "EURUSD")
        self.jpy = fx_core.instrument(CFG, "USDJPY")
        self.gj = fx_core.instrument(CFG, "GBPJPY")
        self.gold = fx_core.instrument(CFG, "XAUUSD")
        self.oil = fx_core.instrument(CFG, "USOIL")

    def test_round_price(self):
        self.assertEqual(fx_core.round_price(1.124905, self.eur), 1.12491)
        self.assertEqual(fx_core.round_price(4134.175, self.gold), 4134.18)
        self.assertEqual(fx_core.round_price(1.1234999999999, self.eur), 1.1235)
        g5 = inst("XAUUSD", tick_size=0.05)
        self.assertEqual(fx_core.round_price(4134.175, g5), 4134.2)
        self.assertEqual(fx_core.round_price(4134.124, g5), 4134.1)

    def test_bad_prices(self):
        for bad in (0, -1.5):
            with self.assertRaises(ValueError) as cm:
                fx_core.round_price(bad, self.gold)
            self.assertEqual(str(cm.exception), "A price must be above 0.")
        for bad in (None, "x", float("nan"), float("inf"), True):
            with self.assertRaises(ValueError) as cm:
                fx_core.round_price(bad, self.gold)
            self.assertEqual(str(cm.exception), "not a number: %r" % (bad,))

    def test_round_away_table(self):
        self.assertEqual(fx_core.round_away(1.112735465, 1.12271, self.eur), 1.11273)
        self.assertEqual(fx_core.round_away(159.8676, 157.955, self.jpy), 159.868)
        self.assertEqual(fx_core.round_away(84.57 - 1.5 * 4.949, 84.61, self.oil), 77.14)
        self.assertEqual(fx_core.round_away(77.1465, 84.61, self.oil), 77.14)
        self.assertEqual(fx_core.round_away(4369.102, 4134.18, self.gold), 4369.11)

    def test_round_away_both_sides(self):
        # a buy: stop down, target up; a sell: stop up, target down
        self.assertEqual(fx_core.round_away(1.084515, 1.0855, self.eur), 1.08451)
        self.assertEqual(fx_core.round_away(1.086511, 1.0855, self.eur), 1.08652)
        self.assertEqual(fx_core.round_away(1.086511, 1.0875, self.eur), 1.08651)
        self.assertEqual(fx_core.round_away(1.084515, 1.0835, self.eur), 1.08452)
        # already on the tick: unchanged (float noise cleaned first)
        self.assertEqual(fx_core.round_away(1.1 + 0.00001 * 3, 1.2, self.eur), 1.10003)

    def test_round_away_at_entry(self):
        with self.assertRaises(ValueError) as cm:
            fx_core.round_away(1.0855, 1.0855000000001, self.eur)
        self.assertEqual(str(cm.exception), "A stop or a target cannot be at the entry price.")

    def test_points(self):
        self.assertEqual(fx_core.to_points(-0.00141, self.eur), -141)
        self.assertEqual(fx_core.to_points(0.00141, self.eur), 141)
        self.assertEqual(fx_core.to_points(1.913, self.jpy), 1913)
        self.assertEqual(fx_core.to_points(174.51, self.gold), 17451)
        self.assertEqual(fx_core.to_points(0.000015, self.eur), 2)
        self.assertIsInstance(fx_core.to_points(0.09, self.gold), int)
        self.assertEqual(fx_core.from_points(9, self.gold), 0.09)
        self.assertEqual(fx_core.from_points(141, self.eur), 0.00141)
        self.assertEqual(fx_core.from_points(-747, self.oil), -7.47)

    def test_pips_text(self):
        self.assertEqual(fx_core.pips_text(0.00141, self.eur), "14.1 pips (141 points)")
        self.assertEqual(fx_core.pips_text(-0.00141, self.eur), "14.1 pips (141 points)")
        self.assertEqual(fx_core.pips_text(1.913, self.jpy), "191.3 pips (1,913 points)")
        self.assertEqual(fx_core.pips_text(0.0001, self.eur), "1.0 pip (10 points)")
        self.assertEqual(fx_core.pips_text(174.51, self.gold), "174.51 (17,451 points)")
        self.assertEqual(fx_core.pips_text(7.47, self.oil), "7.47 (747 points)")
        self.assertEqual(fx_core.pips_text(-7.47, self.oil), "7.47 (747 points)")
        self.assertEqual(fx_core.pips_text(0.00001, self.eur), "0.1 pips (1 point)")
        self.assertEqual(fx_core.pips_text(1234.5, self.gold), "1,234.50 (123,450 points)")

    def test_spread_and_ask(self):
        self.assertEqual(fx_core.spread_price(self.eur), 0.00001)
        self.assertEqual(fx_core.spread_price(self.gold), 0.09)
        self.assertEqual(fx_core.spread_price(self.gj), 0.010)
        self.assertEqual(fx_core.spread_price(self.oil), 0.04)
        self.assertEqual(fx_core.spread_price(inst("EURUSD", spread_points=0)), 0.0)
        self.assertEqual(fx_core.ask_of(1.12490, self.eur), 1.12491)
        self.assertEqual(fx_core.ask_of(4126.20, self.gold), 4126.29)
        self.assertEqual(fx_core.ask_of(157.955, self.gj), 157.965)
        self.assertRaises(ValueError, fx_core.ask_of, 0, self.gold)


if __name__ == "__main__":
    unittest.main()
