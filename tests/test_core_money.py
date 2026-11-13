"""Tests of fx_core: money (part 04 sections 2 to 8), with the worked examples E1 to E9."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copy
import datetime
import json
import shutil
import tempfile
import unittest
from decimal import Decimal

import fx_core

D = Decimal
UTC = datetime.datetime


def fx_row(base, quote, digits, spread, swap_long, swap_short, fee=None, grace=0, **kw):
    row = {"kind": "fx", "price_source": "spot_fx", "yahoo": base + quote + "=X", "base": base, "quote": quote,
           "profit_currency": quote, "margin_currency": base, "contract_size": 100000,
           "digits": digits, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
           "calc_mode": "forex", "spread_points": spread, "commission_per_lot_side": 3.0,
           "swap_mode": "points", "swap_long": swap_long, "swap_short": swap_short,
           "swap_triple_day": "wednesday", "swap_free_fee_per_lot": fee,
           "swap_free_grace_nights": grace}
    row.update(kw)
    return row


def cmd_row(base, contract, digits, spread, swap_long, swap_short, **kw):
    row = {"kind": "metal", "price_source": "futures", "base": base, "quote": "USD",
           "profit_currency": "USD", "margin_currency": "USD", "contract_size": contract,
           "digits": digits, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
           "calc_mode": "cfd_leverage", "spread_points": spread, "commission_per_lot_side": 3.0,
           "swap_mode": "points", "swap_long": swap_long, "swap_short": swap_short,
           "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
           "swap_free_grace_nights": 0}
    row.update(kw)
    return row


ROWS = {
    "EURUSD": fx_row("EUR", "USD", 5, 1, -6.23, 3.1),
    "GBPUSD": fx_row("GBP", "USD", 5, 3, -1.63, -1.37),
    "USDJPY": fx_row("USD", "JPY", 3, 1, 6.9, -13.93, fee=10, grace=6),
    "USDCHF": fx_row("USD", "CHF", 5, 4, 6.23, -10.07),
    "GBPJPY": fx_row("GBP", "JPY", 3, 10, 9.07, -21.8, fee=10, grace=6),
    "XAUUSD": cmd_row("XAU", 100, 2, 9, -54.84, 38.52, swap_free_fee_per_lot=45,
                      swap_free_grace_nights=6),
    "XPTUSD": cmd_row("XPT", 100, 2, 420, -4.78, 3.36, swap_mode="interest"),
    "USOIL": cmd_row("WTI", 1, 2, 4, 0.78, -1.39, kind="energy", min_lot=1, max_lot=10000,
                     lot_step=1, commission_per_lot_side=0.0, swap_triple_day="friday"),
    "COPPER": cmd_row("XCU", 1, 2, 0, 0, 0, commission_per_lot_side=0.0, yahoo_to_broker=2204.62),
    "COPPERLB": cmd_row("XCU", 2204.62, 4, 0, 0, 0, commission_per_lot_side=0.0),
}

CFG = {"instruments": ROWS, "fx_rates_max_age_hours": 24,
       "account": {"balance": 10000, "leverage": 100, "margin_call_pct": 100,
                   "stop_out_pct": 50, "swap_free": False},
       "risk": {"max_risk_per_trade_pct": 1.0, "max_risk_per_trade_usd": None,
                "max_margin_used_pct": 30}}


def cfg(**account):
    c = copy.deepcopy(CFG)
    c["account"].update(account)
    return c


def inst(key, c=None, **kw):
    c = copy.deepcopy(c or CFG)
    c["instruments"][key].update(kw)
    return fx_core.instrument(c, key)


def rates(age=0.0):
    out = {"USD": {"usd": 1.0, "pair": None, "price": None, "at": None, "age_h": 0.0,
                   "_usd": D(1)}}
    for ccy, price in (("EUR", 1.12490), ("GBP", 1.3455), ("JPY", 157.955)):
        out[ccy] = fx_core.rate_entry(ccy, price, "2026-11-10T08:45:28Z", age)
    return out


R = rates()
WED = UTC(2026, 11, 11, 9, 0)
EXIT_BY = UTC(2026, 12, 8, 22, 0)


class Rates(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(len(fx_core.RATE_PAIRS), 7)
        self.assertEqual(fx_core.RATE_PAIRS["JPY"], ("USDJPY", "div"))
        self.assertEqual(fx_core.MIN_STOP_SPREADS, 3)
        self.assertEqual(fx_core.GAP_ATR, {"fx": 1.5, "metal": 2.0, "energy": 3.0})
        self.assertEqual(fx_core.GAP_ATR_DAY, 3.0)
        self.assertTrue(issubclass(fx_core.TooSmall, ValueError))

    def test_rate_entry(self):
        e = fx_core.rate_entry("JPY", 157.955, "2026-11-10T08:45:28Z", 0.3)
        self.assertEqual(e["_usd"], D(1) / D("157.955"))
        self.assertEqual(e["pair"], "USDJPY")
        self.assertAlmostEqual(e["usd"], 0.00633091703, places=12)
        self.assertEqual(fx_core.rate_entry("EUR", 1.1249)["_usd"], D("1.1249"))

    def test_usd_per_unit(self):
        self.assertEqual(fx_core.usd_per_unit("USD", {}), D(1))
        self.assertEqual(fx_core.usd_per_unit("JPY", R, CFG), D(1) / D("157.955"))
        with self.assertRaises(ValueError) as cm:
            fx_core.usd_per_unit("CHF", R)
        self.assertEqual(str(cm.exception), "No USD rate for CHF. It comes from USDCHF. Keep "
                         "USDCHF in your config and run once with Yahoo on (not --offline).")
        with self.assertRaises(ValueError) as cm:
            fx_core.usd_per_unit("XAU", R)
        self.assertEqual(str(cm.exception), "XAU is not a currency. Set profit_currency and "
                         "margin_currency to USD or a currency code.")

    def test_rate_age_limit(self):
        old = rates(30.5)
        self.assertEqual(fx_core.usd_per_unit("JPY", old), D(1) / D("157.955"))   # no cfg: no check
        with self.assertRaises(ValueError) as cm:
            fx_core.usd_per_unit("JPY", old, CFG)
        self.assertEqual(str(cm.exception), "The USD rate for JPY is 30.5 hours old (your limit "
                         "is 24). Run once with Yahoo on to refresh it.")

    def test_rate_from_file_row(self):
        row = json.loads(json.dumps({"JPY": {k: v for k, v in R["JPY"].items() if k != "_usd"}}))
        self.assertEqual(fx_core.usd_per_unit("JPY", row), D(1) / D("157.955"))

    def test_rate_age_h(self):
        self.assertEqual(fx_core.rate_age_h(UTC(2026, 11, 13, 21, 59), UTC(2026, 11, 14, 10, 0)), 0.0)
        self.assertEqual(fx_core.rate_age_h(UTC(2026, 11, 10, 8, 45), UTC(2026, 11, 10, 9, 3)), 0.3)
        # Friday 20:00 UTC to Monday 01:00 UTC: 2 h before the weekend, 3 h after it
        self.assertEqual(fx_core.rate_age_h(UTC(2026, 11, 13, 20, 0), UTC(2026, 11, 16, 1, 0)), 5.0)
        self.assertEqual(fx_core.rate_age_h(UTC(2026, 11, 10, 9, 0), UTC(2026, 11, 10, 8, 0)), 0.0)

    def test_loss_rate(self):
        jpy = D(1) / D("157.955")
        uj = inst("USDJPY")
        self.assertEqual(fx_core.loss_usd_per_unit(uj, 155.000, R), D(1) / D("155.000"))
        self.assertEqual(fx_core.loss_usd_per_unit(uj, 159.868, R), jpy)   # a sell's stop
        loss = D("2.956") * 100000 * fx_core.loss_usd_per_unit(uj, 155.000, R)
        self.assertEqual(loss.quantize(D("0.01")), D("1907.10"))
        gj = fx_core.loss_usd_per_unit(inst("GBPJPY"), 211.850, R)
        self.assertEqual(gj, D("1.3455") / D("211.850"))
        self.assertEqual(gj.quantize(D("0.0000001")), D("0.0063512"))
        self.assertEqual(fx_core.loss_usd_per_unit(inst("XAUUSD"), 3951.78, R), D(1))
        self.assertEqual(fx_core.reward_usd_per_unit(uj, 154.129, R), jpy)
        self.assertEqual(fx_core.reward_usd_per_unit(uj, 160.0, R), D(1) / D("160.0"))

    def test_rate_symbols(self):
        self.assertEqual(fx_core.rate_symbols(inst("EURUSD"), CFG), ["EURUSD"])
        self.assertEqual(fx_core.rate_symbols(inst("USDJPY"), CFG), ["USDJPY"])
        self.assertEqual(fx_core.rate_symbols(inst("GBPJPY"), CFG), ["USDJPY", "GBPUSD"])
        self.assertEqual(fx_core.rate_symbols(inst("XAUUSD"), CFG), [])
        self.assertEqual(fx_core.rate_symbols(inst("XAUUSD", margin_currency="XAU", calc_mode="forex"),
                                              CFG), [])

    def test_usd_per_point_per_lot(self):
        f = fx_core.usd_per_point_per_lot
        self.assertEqual(f(inst("EURUSD"), R), 1.0)
        self.assertEqual(f(inst("USDJPY"), R), 0.6331)
        self.assertEqual(f(inst("GBPJPY"), R, CFG), 0.6331)
        self.assertEqual(f(inst("XAUUSD"), R), 1.0)
        self.assertEqual(f(inst("XAUUSD", digits=3), R), 0.1)
        self.assertEqual(f(inst("USOIL", contract_size=1000), R), 10.0)
        self.assertEqual(f(inst("USOIL", contract_size=100), R), 1.0)


class Costs(unittest.TestCase):
    def test_commission_and_spread(self):
        self.assertEqual(fx_core.commission_usd(inst("EURUSD"), 0.68), 4.08)
        self.assertEqual(fx_core.commission_usd(inst("XAUUSD"), 0.01), 0.06)
        self.assertEqual(fx_core.commission_usd(inst("USOIL"), 13), 0.0)
        self.assertEqual(fx_core.spread_usd(inst("EURUSD"), 0.68, R), 0.68)
        self.assertEqual(fx_core.spread_usd(inst("XAUUSD"), 0.01, R), 0.09)

    def test_swap_nights_table(self):
        n = fx_core.swap_nights
        eu, fri = inst("EURUSD"), inst("EURUSD", swap_triple_day="friday")
        none = inst("EURUSD", swap_triple_day="none")
        cases = [((UTC(2026, 11, 11, 8), UTC(2026, 11, 11, 18, 30)), 0, 0),
                 ((UTC(2026, 11, 10, 10), UTC(2026, 11, 12, 10)), 4, 2),
                 ((UTC(2026, 11, 11, 8), UTC(2026, 11, 20, 12)), 11, 9),
                 ((WED, EXIT_BY), 28, 28)]
        for (a, b), wed, fr in cases:
            self.assertEqual(n(a, b, eu, CFG), wed)
            self.assertEqual(n(a, b, fri, CFG), fr)
        self.assertEqual(n(WED, EXIT_BY, none, CFG), 20)
        self.assertEqual(n(EXIT_BY, WED, eu, CFG), 0)
        # a close exactly at the rollover counts (22:00 UTC in US winter)
        self.assertEqual(n(UTC(2026, 11, 10, 10), UTC(2026, 11, 10, 22), eu, CFG), 1)
        self.assertEqual(n(UTC(2026, 11, 10, 10), UTC(2026, 11, 10, 21, 59), eu, CFG), 0)
        # US summer: the rollover is 21:00 UTC
        self.assertEqual(n(UTC(2026, 7, 14, 10), UTC(2026, 7, 14, 21), eu, CFG), 1)
        # Saturday and Sunday have no rollover
        self.assertEqual(n(UTC(2026, 11, 13, 23), UTC(2026, 11, 15, 23), eu, CFG), 0)

    def test_swap_table(self):
        s = fx_core.swap_usd
        self.assertEqual(s(inst("EURUSD"), "buy", 1, 1, R, CFG), -6.23)
        self.assertEqual(s(inst("EURUSD"), "buy", 1, 3, R, CFG), -18.69)
        self.assertEqual(s(inst("EURUSD"), "sell", 1, 1, R, CFG), 3.1)
        self.assertEqual(s(inst("USDJPY"), "sell", 1, 1, R, CFG), -8.82)
        self.assertEqual(s(inst("XAUUSD"), "buy", 1, 1, R, CFG), -54.84)
        self.assertEqual(s(inst("XAUUSD", digits=3), "buy", 1, 1, R, CFG), -5.48)
        self.assertEqual(s(inst("XAUUSD"), "sell", 1, 1, R, CFG), 38.52)
        self.assertEqual(s(inst("USOIL", contract_size=1000), "buy", 1, 1, R, CFG), 7.8)
        self.assertEqual(s(inst("XAUUSD"), "buy", 0.01, 3, R, CFG), -1.65)

    def test_swap_modes(self):
        s = fx_core.swap_usd
        x = inst("XPTUSD")
        self.assertEqual(s(x, "buy", 1, 1, R, CFG, price=1580.00), -20.98)
        with self.assertRaises(ValueError) as cm:
            s(x, "buy", 1, 1, R, CFG)
        self.assertEqual(str(cm.exception),
                         "XPTUSD swaps are a yearly percent. The tool needs a price to count them.")
        self.assertEqual(s(inst("EURUSD", swap_mode="deposit"), "buy", 1, 2, R, CFG), -12.46)
        self.assertEqual(s(inst("USDJPY", swap_mode="profit", swap_short=-1393), "sell", 1, 1, R,
                           CFG), -8.82)
        self.assertEqual(s(inst("EURUSD", swap_mode="base", swap_long=-10), "buy", 1, 1, R, CFG),
                         -11.25)
        self.assertEqual(s(inst("XAUUSD", swap_mode="base", swap_long=-0.01), "buy", 1, 1, R, CFG,
                           price=4000), -40.0)
        self.assertEqual(s(inst("GBPJPY", swap_mode="margin", swap_long=-10), "buy", 1, 1, R, CFG),
                         -13.46)
        self.assertEqual(s(inst("EURUSD", swap_mode="disabled"), "buy", 1, 5, R, CFG), 0.0)
        with self.assertRaises(ValueError):
            s(inst("EURUSD", swap_mode="reopen"), "buy", 1, 1, R, CFG)
        with self.assertRaises(ValueError):
            s(inst("EURUSD"), "long", 1, 1, R, CFG)

    def test_e7_triple_swap_and_swap_free(self):
        x = inst("XAUUSD")
        free = cfg(swap_free=True)
        for (a, b), buy, sell, fee in (((UTC(2026, 11, 10, 10), UTC(2026, 11, 12, 10)), -2.19, 1.54, 0.0),
                                       ((UTC(2026, 11, 11, 8), UTC(2026, 11, 20, 12)), -6.03, 4.24, 2.25)):
            n = fx_core.swap_nights(a, b, x, CFG)
            self.assertEqual(fx_core.swap_usd(x, "buy", 0.01, n, R, CFG), buy)
            self.assertEqual(fx_core.swap_usd(x, "sell", 0.01, n, R, CFG), sell)
            self.assertEqual(fx_core.swap_usd(x, "buy", 0.01, n, R, free), 0.0)
            self.assertEqual(fx_core.swap_free_fee_usd(x, 0.01, n, free), fee)
            self.assertEqual(fx_core.swap_free_fee_usd(x, 0.01, n, CFG), 0.0)
        self.assertEqual(fx_core.swap_free_fee_usd(inst("EURUSD"), 1, 20, free), 0.0)

    def test_trade_costs_e1(self):
        c = fx_core.trade_costs(inst("EURUSD"), "buy", 0.68, 0, R, CFG, price=1.12491)
        self.assertEqual(c, {"spread": 0.68, "commission": 4.08, "swap": 0.0, "fee": 0.0,
                             "total": 4.76})
        c = fx_core.trade_costs(inst("XAUUSD"), "sell", 0.01, 28, R, CFG, price=4126.20)
        self.assertEqual(c["swap"], 10.79)
        self.assertEqual(c["total"], round(0.09 + 0.06 - 10.7856, 2))


class Margin(unittest.TestCase):
    def test_examples(self):
        m = fx_core.margin_usd
        gold = inst("XAUUSD")
        self.assertEqual(m(inst("EURUSD"), 0.68, 1.12491, "buy", CFG, R), 764.94)
        self.assertEqual(m(inst("USDJPY", margin_currency="USD"), 0.06, 157.955, "sell", CFG, R), 60.0)
        self.assertEqual(m(inst("GBPJPY"), 0.47, 212.169, "buy", CFG, R), 632.39)
        self.assertEqual(m(gold, 0.01, 4126.29, "buy", CFG, R), 41.26)
        self.assertEqual(m(gold, 0.01, 4126.29, "buy", cfg(leverage=500), R), 8.25)
        self.assertEqual(m(inst("XAUUSD", margin_currency="XAU", calc_mode="forex"), 0.01, 4126.29,
                           "buy", CFG, R), 41.26)
        self.assertEqual(m(gold, 0.01, 4126.20, "sell", cfg(leverage=20), R), 206.31)
        self.assertEqual(m(inst("USOIL", contract_size=1000, min_lot=0.01, lot_step=0.01), 0.01,
                           84.61, "buy", CFG, R), 8.46)
        self.assertEqual(m(inst("USOIL", contract_size=100, calc_mode="cfd", margin_rate=0.01), 1,
                           84.60, "buy", CFG, R), 84.6)

    def test_modes_and_errors(self):
        m = fx_core.margin_usd
        self.assertEqual(m(inst("EURUSD", calc_mode="forex_no_leverage"), 0.01, 1.1, "buy", CFG, R),
                         1100.0)
        self.assertEqual(m(inst("USOIL", calc_mode="futures", initial_margin=5000), 2, 84.6, "buy",
                           CFG, R), 10000.0)
        self.assertEqual(m(inst("XAUUSD", leverage=20), 0.01, 4126.20, "sell", CFG, R), 206.31)
        with self.assertRaises(ValueError) as cm:
            m(inst("USOIL", calc_mode="futures"), 1, 84.6, "buy", CFG, R)
        self.assertEqual(str(cm.exception), "USOIL uses calc_mode futures. Copy its Initial margin "
                         "from MetaTrader into initial_margin.")
        with self.assertRaises(ValueError) as cm:
            m(inst("XAUUSD", calc_mode="cfdx"), 0.01, 4126.2, "buy", CFG, R)
        self.assertEqual(str(cm.exception), 'XAUUSD: calc_mode "cfdx" is not known. Use forex, '
                         'forex_no_leverage, cfd, cfd_leverage or futures.')
        with self.assertRaises(ValueError) as cm:
            m(inst("XAUUSD"), 0, 4126.2, "buy", CFG, R)
        self.assertEqual(str(cm.exception), "Lots must be above 0.")

    def test_level_and_state(self):
        self.assertIsNone(fx_core.margin_level_pct(10000, 0))
        self.assertEqual(fx_core.margin_level_pct(10000, 764.94), 1307.29)
        self.assertEqual(fx_core.margin_level_pct(10000, 806.20), 1240.39)
        self.assertEqual(fx_core.margin_state(10000, 764.94, CFG),
                         {"level_pct": 1307.29, "call_equity": 764.94, "stop_out_equity": 382.47,
                          "free_margin": 9235.06, "state": "ok"})
        self.assertEqual(fx_core.margin_state(764.94, 764.94, CFG)["state"], "call")
        self.assertEqual(fx_core.margin_state(382.47, 764.94, CFG)["state"], "stop_out")
        self.assertEqual(fx_core.margin_state(500, 0, CFG)["state"], "ok")


def extra(key, side, nights, entry, c=None):
    i = inst(key)
    return fx_core._costs(i, side, 1, nights, R, c or CFG, entry)


class Examples(unittest.TestCase):
    """The worked examples E1 to E9 of part 04 section 8, the same numbers."""

    def test_e1_eurusd_buy_day(self):
        eu = inst("EURUSD")
        self.assertEqual(fx_core.ask_of(1.12490, eu), 1.12491)
        self.assertEqual(fx_core.pips_text(1.12491 - 1.12350, eu), "14.1 pips (141 points)")
        s = fx_core.size_order(eu, "buy", 1.12491, 1.12350, 0, 10000, 0, R, CFG)
        self.assertEqual(s, {"lots": D("0.68"), "decided_by": "risk_pct", "risk_limit_usd": 100.0,
                             "max_loss_usd": 99.96, "loss_per_lot_usd": 147.0,
                             "min_lot_loss_usd": 1.47, "margin_usd": 764.94,
                             "margin_per_lot_usd": 1124.91,
                             "limits": {"risk_pct": "0.68", "risk_usd": None, "max_lot": "100",
                                        "margin_cap": "2.66", "free_margin": "8.88"}})
        self.assertEqual(fx_core.max_loss_usd(eu, "buy", s["lots"], 1.12491, 1.12350, 0, R, CFG), 99.96)
        self.assertEqual(fx_core.max_loss_core(eu, "buy", 1.12491, 1.12350, 0, R, CFG), D("147"))
        self.assertEqual(fx_core.reward_risk_net(eu, "buy", 1.12491, 1.12350, 1.12773, 0, R, CFG), 1.88)
        self.assertEqual(fx_core.lots_for_risk(100, 1.12491, 1.12350, eu, R,
                                               extra("EURUSD", "buy", 0, 1.12491)), D("0.68"))

    def test_e2_usdjpy_sell_swing(self):
        uj = inst("USDJPY")
        self.assertEqual(fx_core.round_away(157.955 + 2 * 0.9558 + 0.001, 157.955, uj), 159.868)
        n = fx_core.swap_nights(WED, EXIT_BY, uj, CFG)
        self.assertEqual(n, 28)
        self.assertEqual(fx_core.pips_text(1.913, uj), "191.3 pips (1,913 points)")
        s = fx_core.size_order(uj, "sell", 157.955, 159.868, n, 10000, 0, R, CFG)
        self.assertEqual((s["lots"], s["max_loss_usd"], s["loss_per_lot_usd"]), (D("0.06"), 87.84, 1464.04))
        self.assertEqual(s["margin_usd"], 60.0)
        self.assertEqual(fx_core.max_loss_usd(uj, "sell", 0.06, 157.955, 159.868, n, R, CFG), 87.84)
        self.assertEqual(fx_core.costs_usd(uj, "sell", 0.06, n, R, CFG, 157.955), 15.18)
        self.assertEqual(fx_core.reward_risk_net(uj, "sell", 157.955, 159.868, 154.129, n, R, CFG), 1.48)
        core = fx_core.net_core(uj, "sell", 157.955, 159.868, 154.129, n, R, CFG)
        self.assertTrue(D("1.47") < core < D("1.5"))

    def test_e3_gbpjpy_buy_day(self):
        gj = inst("GBPJPY")
        self.assertEqual(fx_core.ask_of(212.159, gj), 212.169)
        self.assertEqual(fx_core.pips_text(212.169 - 211.850, gj), "31.9 pips (319 points)")
        s = fx_core.size_order(gj, "buy", 212.169, 211.850, 0, 10000, 0, R, CFG)
        self.assertEqual((s["lots"], s["max_loss_usd"], s["loss_per_lot_usd"]), (D("0.47"), 98.04, 208.6))
        self.assertEqual(s["margin_usd"], 632.39)
        self.assertEqual(fx_core.usd_per_point_per_lot(gj, R), 0.6331)

    def test_e4_gold_min_lot_trap(self):
        g = inst("XAUUSD")
        self.assertEqual(fx_core.pips_text(4126.29 - 3951.78, g), "174.51 (17,451 points)")
        with self.assertRaises(fx_core.TooSmall) as cm:
            fx_core.size_order(g, "buy", 4126.29, 3951.78, 28, 10000, 0, R, CFG)
        e = cm.exception
        self.assertEqual(str(e), "0.01 lot is the smallest order for XAUUSD. At this stop it loses "
                         "$189.93. Your max loss is $100.00. So this trade is too big for your "
                         "account. Do not move the stop closer to make it fit. At 1.0% a trade you "
                         "need about $18,992.52 for this trade.")
        self.assertEqual((e.min_lot, e.min_lot_loss_usd, e.risk_limit_usd, e.decided_by),
                         (0.01, 189.93, 100.0, "risk_pct"))
        with self.assertRaises(fx_core.TooSmall) as cm:
            fx_core.lots_for_risk(100, 4126.29, 3951.78, g, R, extra("XAUUSD", "buy", 28, 4126.29))
        self.assertTrue(str(cm.exception).endswith("Do not move the stop closer to make it fit."))
        s = fx_core.size_order(g, "buy", 4126.29, 3951.78, 28, 20000, 0, R, CFG)
        self.assertEqual((s["lots"], s["max_loss_usd"], s["margin_usd"]), (D("0.01"), 189.93, 41.26))
        self.assertEqual(s["loss_per_lot_usd"], 18992.52)
        self.assertEqual(fx_core.reward_risk_net(g, "buy", 4126.29, 3951.78, 4475.31, 28, R, CFG), 1.76)
        gap = D(str(fx_core.GAP_ATR["metal"])) * D("87.21") * 100
        self.assertEqual(gap, D("17442.000"))

    def test_e5_gold_sell(self):
        g = inst("XAUUSD")
        s = fx_core.size_order(g, "sell", 4126.20, 4300.71, 28, 20000, 0, R, CFG)
        self.assertEqual((s["lots"], s["max_loss_usd"], s["margin_usd"]), (D("0.01"), 174.57, 41.26))
        self.assertEqual(s["loss_per_lot_usd"], 17457.0)
        self.assertEqual(fx_core.reward_risk_net(g, "sell", 4126.20, 4300.71, 3777.18, 28, R, CFG), 2.0)
        self.assertEqual(fx_core.swap_usd(g, "sell", 0.01, 28, R, CFG, price=4126.20), 10.79)
        self.assertEqual(fx_core.bar_hits("sell", {"open": 4290, "high": 4300.65, "low": 4280,
                                                   "close": 4290}, 4300.71, 3777.18, g), "stop")


    def test_e6_oil_three_contract_sizes(self):
        o = inst("USOIL")
        self.assertEqual(fx_core.ask_of(84.57, o), 84.61)
        self.assertEqual(fx_core.round_away(84.57 - 1.5 * 4.949, 84.61, o), 77.14)
        rows = [(dict(), D("13"), 97.11, 11.0, 7.47),
                (dict(contract_size=1000, min_lot=0.01, lot_step=0.01), D("0.01"), 74.7, 8.46, 7470.0),
                (dict(contract_size=100, min_lot=0.01, lot_step=0.01), D("0.13"), 97.11, 11.0, 747.0)]
        for kw, lots, loss, margin, per_lot in rows:
            i = inst("USOIL", **kw)
            s = fx_core.size_order(i, "buy", 84.61, 77.14, 28, 10000, 0, R, CFG)
            self.assertEqual((s["lots"], s["max_loss_usd"], s["margin_usd"], s["loss_per_lot_usd"]),
                             (lots, loss, margin, per_lot))
            self.assertEqual(s["decided_by"], "risk_pct")
        self.assertEqual(fx_core.size_order(o, "buy", 84.61, 77.14, 28, 10000, 0, R, CFG)["limits"]["risk_pct"], "13")

    def test_e8_platinum_interest_swap(self):
        x = inst("XPTUSD")
        self.assertEqual(fx_core.ask_of(1580.00, x), 1584.20)
        per_night = fx_core._swap(x, "buy", 1, 1, R, CFG, 1584.20)
        self.assertEqual(per_night.quantize(D("0.0001")), D("-21.0347"))
        self.assertEqual(fx_core.max_loss_core(x, "buy", 1584.20, 1500.00, 28, R, CFG).quantize(D("0.01")),
                         D("9014.97"))
        s = fx_core.size_order(x, "buy", 1584.20, 1500.00, 28, 10000, 0, R, CFG)
        self.assertEqual((s["lots"], s["max_loss_usd"], s["margin_usd"]), (D("0.01"), 90.15, 15.84))
        self.assertEqual(fx_core.max_loss_usd(x, "buy", 0.01, 1584.20, 1500.00, 28, R, CFG), 90.15)
        c = fx_core.trade_costs(x, "buy", 0.01, 28, R, CFG, price=1584.20)
        self.assertEqual(c["swap"], -5.89)
        self.assertEqual(fx_core.swap_usd(x, "buy", 1, 1, R, CFG, price=1584.20), -21.03)

    def test_e9_copper_two_scales(self):
        a, b = inst("COPPER"), inst("COPPERLB")
        u = D("2204.62")
        entry = fx_core.round_price(D("6.3000") * u, a)
        stop = fx_core.round_away(D("6.1000") * u, entry, a)
        self.assertEqual((entry, stop), (13889.11, 13448.18))
        la = fx_core.lots_for_risk(100, entry, stop, a, R)
        lb = fx_core.lots_for_risk(100, 6.3000, 6.1000, b, R)
        self.assertEqual((la, lb), (D("0.22"), D("0.22")))
        self.assertEqual(fx_core.max_loss_usd(a, "buy", la, entry, stop, 0, R, CFG), 97.0)
        self.assertEqual(fx_core.max_loss_usd(b, "buy", lb, 6.3, 6.1, 0, R, CFG), 97.0)

    def test_max_loss_never_above_the_limit(self):
        cases = [("EURUSD", "buy", 1.12491, 1.12350, 0, 10000), ("USDJPY", "sell", 157.955, 159.868, 28, 10000),
                 ("GBPJPY", "buy", 212.169, 211.850, 0, 10000), ("XAUUSD", "buy", 4126.29, 3951.78, 28, 20000),
                 ("XAUUSD", "sell", 4126.20, 4300.71, 28, 20000), ("USOIL", "buy", 84.61, 77.14, 28, 10000),
                 ("XPTUSD", "buy", 1584.20, 1500.00, 28, 10000)]
        for key, side, e, st, n, bal in cases:
            i = inst(key)
            limit = D(bal) / 100
            lots = fx_core.lots_for_risk(limit, e, st, i, R, fx_core._costs(i, side, 1, n, R, CFG, e), CFG)
            self.assertLessEqual(lots * fx_core.max_loss_core(i, side, e, st, n, R, CFG), limit, key)
            self.assertLessEqual(fx_core.max_loss_usd(i, side, lots, e, st, n, R, CFG), float(limit), key)
            self.assertEqual(lots, fx_core.size_order(i, side, e, st, n, bal, 0, R, CFG)["lots"], key)


class Sizing(unittest.TestCase):
    def test_errors(self):
        eu = inst("EURUSD")
        with self.assertRaises(ValueError) as cm:
            fx_core.lots_for_risk(0, 1.12491, 1.12350, eu, R)
        self.assertEqual(str(cm.exception), "The max loss must be above $0.")
        with self.assertRaises(ValueError) as cm:
            fx_core.lots_for_risk(100, 1.12491, 1.12491, eu, R)
        self.assertEqual(str(cm.exception), "The stop is at the entry price. There is no risk to size.")
        with self.assertRaises(ValueError) as cm:
            fx_core.max_loss_usd(eu, "buy", 1, 1.12491, 1.13, 0, R, CFG)
        self.assertEqual(str(cm.exception), "A buy's stop must be below the entry.")
        with self.assertRaises(ValueError) as cm:
            fx_core.max_loss_usd(eu, "sell", 1, 1.12491, 1.12, 0, R, CFG)
        self.assertEqual(str(cm.exception), "A sell's stop must be above the entry.")
        with self.assertRaises(ValueError) as cm:
            fx_core.reward_risk_net(eu, "buy", 1.12491, 1.12350, 1.12, 0, R, CFG)
        self.assertEqual(str(cm.exception), "A buy's target must be above the entry.")
        with self.assertRaises(ValueError) as cm:
            fx_core.reward_risk_net(eu, "sell", 1.12491, 1.13, 1.13, 0, R, CFG)
        self.assertEqual(str(cm.exception), "A sell's target must be below the entry.")
        with self.assertRaises(ValueError):
            fx_core.lots_for_risk(100, float("nan"), 1.1, eu, R)
        with self.assertRaises(ValueError) as cm:
            fx_core.lots_for_risk(100, 0, 1.1, eu, R)
        self.assertEqual(str(cm.exception), "A price must be above 0.")

    def test_flags_only_lower(self):
        eu = inst("EURUSD")
        args = (eu, "buy", 1.12491, 1.12350, 0, 10000, 0, R)
        with self.assertRaises(ValueError) as cm:
            fx_core.size_order(*args, CFG, risk_pct=2)
        self.assertEqual(str(cm.exception), "Your config allows at most 1.0% a trade. To go higher, "
                         "change risk.max_risk_per_trade_pct in fx/config.json.")
        c = copy.deepcopy(CFG)
        c["risk"]["max_risk_per_trade_usd"] = 50
        with self.assertRaises(ValueError) as cm:
            fx_core.size_order(*args, c, risk_usd=60)
        self.assertEqual(str(cm.exception), "Your config allows at most $50.00 a trade. To go higher, "
                         "change risk.max_risk_per_trade_usd in fx/config.json.")
        s = fx_core.size_order(*args, c)
        self.assertEqual((s["lots"], s["decided_by"], s["limits"]["risk_usd"]), (D("0.34"), "risk_usd", "0.34"))
        s = fx_core.size_order(*args, CFG, risk_pct=0.5)
        self.assertEqual((s["lots"], s["decided_by"], s["risk_limit_usd"]), (D("0.34"), "risk_pct", 50.0))
        s = fx_core.size_order(*args, CFG, risk_usd=30)
        self.assertEqual((s["lots"], s["decided_by"]), (D("0.2"), "risk_usd"))
        c["risk"]["max_risk_per_trade_usd"] = 100   # a tie goes to risk_pct
        self.assertEqual(fx_core.size_order(*args, c)["decided_by"], "risk_pct")

    def test_other_limits(self):
        eu = inst("EURUSD", max_lot=0.5)
        self.assertEqual(fx_core.size_order(eu, "buy", 1.12491, 1.12350, 0, 10000, 0, R, CFG)["decided_by"],
                         "max_lot")
        eu = inst("EURUSD")
        s = fx_core.size_order(eu, "buy", 1.12491, 1.12350, 0, 10000, 2500, R, CFG)
        self.assertEqual((s["lots"], s["decided_by"]), (D("0.44"), "margin_cap"))
        c = copy.deepcopy(CFG)
        c["risk"]["max_margin_used_pct"] = 500
        s = fx_core.size_order(eu, "buy", 1.12491, 1.12350, 0, 10000, 9500, R, c, equity=10000)
        self.assertEqual((s["lots"], s["decided_by"]), (D("0.44"), "free_margin"))

    def test_margin_refusal(self):
        g = inst("XAUUSD")
        with self.assertRaises(fx_core.TooSmall) as cm:
            fx_core.size_order(g, "buy", 4126.29, 3951.78, 0, 100000, 29975, R, CFG)
        self.assertEqual(str(cm.exception), "0.01 lot of XAUUSD needs $41.26 margin. You have room "
                         "for $25.00 (30% of your balance, minus margin in use). So this trade does not fit.")
        self.assertEqual(cm.exception.decided_by, "margin_cap")



class Results(unittest.TestCase):
    def test_stop_out_is_minus_one_r(self):
        g = inst("XAUUSD")
        res = fx_core.result_core(g, "buy", 4126.29, 3951.78, 28, R, CFG)
        self.assertEqual(res / fx_core.max_loss_core(g, "buy", 4126.29, 3951.78, 28, R, CFG), D(-1))
        eu = inst("EURUSD")
        r = fx_core.result_usd(eu, "buy", 0.68, 1.12491, 1.12350, 0, R, CFG)
        self.assertEqual(r, -99.96)
        self.assertEqual(fx_core.r_multiple(r, 99.96), -1.0)
        uj = inst("USDJPY")
        self.assertEqual(fx_core.result_core(uj, "buy", 157.956, 155.0, 0, R, CFG),
                         -fx_core.max_loss_core(uj, "buy", 157.956, 155.0, 0, R, CFG))
        self.assertEqual(fx_core.result_usd(uj, "buy", 1, 157.956, 155.0, 0, R, CFG), -1913.1)

    def test_result_and_r(self):
        eu = inst("EURUSD")
        self.assertEqual(fx_core.result_usd(eu, "buy", 0.68, 1.12491, 1.12773, 0, R, CFG), 187.68)
        self.assertEqual(fx_core.result_usd(inst("XAUUSD"), "sell", 0.01, 4126.20, 3777.18, 28, R, CFG),
                         359.75)   # 349.02 + 10.7856 swap - 0.06
        self.assertEqual(fx_core.r_multiple(187.68, 99.96), 1.88)
        self.assertIsNone(fx_core.r_multiple(10, 0))
        self.assertEqual(fx_core.r_multiple(0, 50), 0.0)

    def test_position_pl(self):
        pos = {"side": "buy", "lots": 0.68, "entry": 1.12491, "swap_nights": 0}
        self.assertEqual(fx_core.position_pl_usd(pos, 1.12600, inst("EURUSD"), R, CFG), 74.12)
        pos = {"side": "sell", "lots": 0.01, "entry": 4126.20, "swap_nights": 2}
        self.assertEqual(fx_core.position_pl_usd(pos, 4100.00, inst("XAUUSD"), R, CFG), 26.88)

    def test_gap_examples(self):
        g, eu = inst("XAUUSD"), inst("EURUSD")
        fill = fx_core.gap_fill("sell", 4310.00, 4300.71, g)
        self.assertEqual(fill, 4310.09)
        extra = abs(D(str(fill)) - D("4300.71")) * 100 * D("0.01") * fx_core.loss_usd_per_unit(g, fill, R)
        self.assertEqual(extra, D("9.3800"))
        fill = fx_core.gap_fill("buy", 1.12200, 1.12350, eu)
        self.assertEqual(fill, 1.122)
        extra = abs(D(str(fill)) - D("1.12350")) * 100000 * D("0.68")
        self.assertEqual(extra.quantize(D("0.01")), D("102.00"))
        self.assertIsNone(fx_core.gap_fill("buy", 1.12400, 1.12350, eu))
        self.assertIsNone(fx_core.gap_fill("sell", 4300.61, 4300.71, g))
        self.assertEqual(fx_core.gap_fill("sell", 4300.62, 4300.71, g), 4300.71)


class StopsAndFills(unittest.TestCase):
    def test_stops_level(self):
        g = inst("XAUUSD", stops_level_points=30)
        p = fx_core.stops_problems("buy", "market", None, 4126.08, 4126.32, 4126.20, g)
        self.assertEqual(p, ["Your stop is too close. MetaTrader needs it at least 30 points (0.30) "
                             "from the price. It is 12 points away.",
                             "Your target is too close. MetaTrader needs it at least 30 points (0.30) "
                             "from the price. It is 12 points away."])
        self.assertEqual(fx_core.stops_problems("buy", "market", None, 3951.78, 4475.31, 4126.20, g), [])
        self.assertEqual(fx_core.stops_problems("sell", "market", None, 4300.71, 3777.18, 4126.20, g), [])
        self.assertEqual(fx_core.stops_problems("buy", "market", None, 4126.30, None, 4126.20, g),
                         ["Your stop is on the wrong side of the price now."])
        # a sell's stop is measured from the ask (4126.29): 4126.50 is 21 points away
        self.assertEqual(fx_core.stops_problems("sell", "market", None, 4126.50, None, 4126.20, g),
                         ["Your stop is too close. MetaTrader needs it at least 30 points (0.30) "
                          "from the price. It is 21 points away."])

    def test_pending_price_rules(self):
        eu = inst("EURUSD")
        sp = fx_core.stops_problems
        self.assertEqual(sp("buy", "stop", 1.12400, 1.12300, None, 1.12490, eu),
                         ["A buy stop order must be above the price now (ask 1.12491)."])
        self.assertEqual(sp("buy", "limit", 1.12600, 1.12300, None, 1.12490, eu),
                         ["A buy limit order must be below the price now (ask 1.12491)."])
        self.assertEqual(sp("sell", "stop", 1.12600, 1.12700, None, 1.12490, eu),
                         ["A sell stop order must be below the price now (bid 1.12490)."])
        self.assertEqual(sp("sell", "limit", 1.12400, 1.12500, None, 1.12490, eu),
                         ["A sell limit order must be above the price now (bid 1.12490)."])
        self.assertEqual(sp("buy", "stop", 1.12600, 1.12300, 1.12900, 1.12490, eu), [])
        self.assertEqual(sp("buy", "limit", 1.12400, 1.12450, None, 1.12490, eu),
                         ["Your stop is on the wrong side of the price now."])
        with self.assertRaises(ValueError):
            sp("buy", "stop_limit", 1.126, 1.123, None, 1.1249, eu)

    def test_bar_hits(self):
        eu = inst("EURUSD")
        bar = {"open": 1.12500, "high": 1.12800, "low": 1.12340, "close": 1.12600}
        self.assertEqual(fx_core.bar_hits("buy", bar, 1.12350, 1.12773, eu), "both")
        self.assertEqual(fx_core.bar_hits("buy", bar, 1.12350, None, eu), "stop")
        self.assertEqual(fx_core.bar_hits("buy", bar, None, 1.12773, eu), "target")
        self.assertIsNone(fx_core.bar_hits("buy", bar, 1.12300, 1.12900, eu))
        self.assertEqual(fx_core.bar_hits("buy", bar, 1.12320, 1.12900, eu, margin_points=20), "stop")
        self.assertEqual(fx_core.bar_hits("buy", dict(bar, open=1.12340), 1.12350, 1.12773, eu), "stop")
        self.assertEqual(fx_core.bar_hits("buy", dict(bar, open=1.12780), 1.12350, 1.12773, eu), "target")
        self.assertIsNone(fx_core.bar_hits("buy", bar, None, None, eu))
        g = inst("XAUUSD")
        sb = {"open": 3800.0, "high": 3810.0, "low": 3777.08, "close": 3790.0}
        self.assertEqual(fx_core.bar_hits("sell", sb, 4300.71, 3777.18, g), "target")
        self.assertIsNone(fx_core.bar_hits("sell", dict(sb, low=3777.10), 4300.71, 3777.18, g))
        self.assertEqual(fx_core.bar_hits("sell", {"open": 4310.0, "high": 4312.0, "low": 4200.0,
                                                   "close": 4300.0}, 4300.71, 3777.18, g), "stop")

    def test_pending_fill(self):
        eu = inst("EURUSD")
        bar = {"open": 1.12480, "high": 1.12520, "low": 1.12440, "close": 1.12500}
        f = fx_core.pending_fill
        self.assertEqual(f("buy", "stop", bar, 1.12500, eu), 1.125)
        self.assertEqual(f("buy", "stop", dict(bar, open=1.12510), 1.12500, eu), 1.12511)
        self.assertIsNone(f("buy", "stop", bar, 1.12600, eu))
        self.assertEqual(f("buy", "limit", bar, 1.12460, eu), 1.1246)
        self.assertEqual(f("buy", "limit", bar, 1.12490, eu), 1.12481)
        self.assertIsNone(f("buy", "limit", bar, 1.12440, eu))
        self.assertEqual(f("sell", "stop", bar, 1.12450, eu), 1.1245)
        self.assertEqual(f("sell", "stop", bar, 1.12490, eu), 1.1248)
        self.assertEqual(f("sell", "limit", bar, 1.12510, eu), 1.1251)
        self.assertEqual(f("sell", "limit", bar, 1.12470, eu), 1.1248)
        self.assertIsNone(f("sell", "limit", bar, 1.12600, eu))
        self.assertEqual(f("buy", "market", bar, None, eu), 1.12481)
        self.assertEqual(f("sell", "market", bar, None, eu), 1.1248)



class FxRates(unittest.TestCase):
    """fx_rates reads the cache only, through latest_price (part 02 section 7)."""

    NOW = UTC(2026, 11, 10, 9, 3)
    AT = UTC(2026, 11, 10, 8, 45, 28)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fx_money_")
        self.real = fx_core.latest_price

    def tearDown(self):
        fx_core.latest_price = self.real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, r):
        self.assertEqual(set(r), {"USD", "EUR", "GBP", "JPY"})   # no cached CHF, CAD ...; others not in cfg
        self.assertEqual(r["USD"]["usd"], 1.0)
        j = r["JPY"]
        self.assertEqual((j["pair"], j["price"], j["at"], j["age_h"]),
                         ("USDJPY", 157.955, "2026-11-10T08:45:28Z", 0.3))
        self.assertEqual(j["_usd"], D(1) / D("157.955"))
        self.assertEqual(r["EUR"]["_usd"], D("1.1249"))
        self.assertEqual(fx_core.usd_per_unit("JPY", r, CFG), D(1) / D("157.955"))
        with self.assertRaises(ValueError):
            fx_core.usd_per_unit("CHF", r, CFG)
        self.assertEqual(fx_core.max_loss_usd(inst("USDJPY"), "sell", 0.06, 157.955, 159.868, 28, r, CFG),
                         87.84)

    def test_with_a_swapped_reader(self):
        path = os.path.join(self.tmp, "prices.json")
        with open(path, "w") as fh:
            json.dump({"USDJPY": 157.955, "EURUSD": 1.12490, "GBPUSD": 1.3455}, fh)

        def fake(data_dir, i, now, c):
            with open(os.path.join(data_dir, "prices.json")) as fh:
                p = json.load(fh).get(i["key"])
            return None if p is None else {"bid": p, "yahoo": p, "at": self.AT, "age_min": 17.5,
                                           "source": "yahoo 1h", "contract": None}
        fx_core.latest_price = fake
        self.check(fx_core.fx_rates(self.tmp, self.NOW, CFG))

    def test_with_a_tiny_cache(self):
        if not hasattr(fx_core, "cache_file"):
            self.skipTest("the cache helpers of part 02 are not in fx_core yet")
        ts = int(fx_core.utc_to_ts(self.AT))
        for sym, price in (("USDJPY=X", 157.955), ("EURUSD=X", 1.12490), ("GBPUSD=X", 1.3455)):
            doc = {"symbol": sym, "interval": "1h", "bars": [],
                   "fetches": [{"at": "2026-11-10T08:46:00Z", "market_time": ts, "price": price}]}
            fx_core.save_cache(fx_core.cache_file(self.tmp, sym, "1h"), doc)
        self.check(fx_core.fx_rates(self.tmp, self.NOW, CFG))


if __name__ == "__main__":
    unittest.main()
