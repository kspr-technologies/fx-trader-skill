"""Tests of fx_core: config.json (part 01 sections 2 to 4 and 6): DEFAULTS, merge_config,
check_config, load_config, and the shipped example files."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib
import io
import json
import shutil
import tempfile
import unittest

import fx_core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "fx-budget", "config.example.json")
ALERTS = os.path.join(ROOT, "fx-alerts", "alerts.example.json")
KEYS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "GBPJPY",
        "EURGBP", "AUDJPY", "EURCHF", "XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "COPPER", "USOIL",
        "UKOIL", "NATGAS"]
TOP_KEYS = ["account", "paper", "yahoo", "allow_short", "style", "day_end", "universe",
            "day_universe", "risk", "swing", "day", "news", "calendar_url", "holidays",
            "early_closes", "fx_rates_max_age_hours", "instruments"]


def checked(doc):
    """merge_config then check_config, with the default path in the messages."""
    return fx_core.check_config(fx_core.merge_config(doc))


class ExampleFiles(unittest.TestCase):
    def test_config_example_is_safe(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            ex = json.load(fh)
        self.assertEqual(list(ex), TOP_KEYS)
        self.assertIs(ex["paper"], True)
        self.assertIs(ex["yahoo"], True)
        self.assertIs(ex["allow_short"], True)
        self.assertEqual(ex["account"]["currency"], "USD")
        self.assertEqual(ex["account"]["balance"], 1000)
        self.assertIs(ex["risk"]["require_stop"], True)
        self.assertEqual(ex["risk"]["max_risk_per_trade_pct"], 1.0)
        self.assertEqual(ex["style"], "swing")
        self.assertEqual(ex["day_end"], "17:00 New York")
        self.assertEqual(list(ex["instruments"]), KEYS)
        self.assertIsNone(fx_core._find_secret(ex))
        cfg = fx_core.load_config(EXAMPLE)
        self.assertEqual(list(cfg["instruments"]), KEYS)

    def test_config_example_is_the_defaults(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            ex = json.load(fh)
        for key in TOP_KEYS[:-1]:
            self.assertEqual(ex[key], fx_core.DEFAULTS[key], key)
        for sym, row in ex["instruments"].items():
            for col, value in row.items():
                self.assertEqual(fx_core.DEFAULTS["instruments"][sym][col], value, (sym, col))

    def test_config_example_has_only_copy_columns(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            ex = json.load(fh)
        fixed = {"name", "kind", "yahoo", "price_source", "base", "quote", "pip", "point",
                 "hours", "data_hours", "futures", "group"}
        for row in ex["instruments"].values():
            self.assertFalse(fixed & set(row))

    def test_alerts_example(self):
        with open(ALERTS, encoding="utf-8") as fh:
            doc = json.load(fh)
        rules = doc["rules"]
        self.assertEqual([r["id"] for r in rules], ["a1", "a2", "a3", "a4", "a5"])
        self.assertEqual([r["kind"] for r in rules],
                         ["session_open", "session_open", "rollover", "news", "above"])
        self.assertEqual([r["active"] for r in rules], [True, True, True, True, False])
        self.assertEqual(rules[0]["session"], "london")
        self.assertEqual(rules[1]["session"], "new_york")
        self.assertEqual(rules[3]["symbol"], "XAUUSD")
        self.assertEqual(rules[4]["level"], 1.13)
        for r in rules:
            self.assertIsNone(r["at_utc"])
            self.assertEqual(r["rolls_applied"], {})

    def test_example_files_end_with_a_newline(self):
        for path in (EXAMPLE, ALERTS):
            with open(path, "rb") as fh:
                self.assertTrue(fh.read().endswith(b"}\n"))


class BuiltInRows(unittest.TestCase):
    def test_twenty_rows_in_table_order(self):
        rows = fx_core.DEFAULTS["instruments"]
        self.assertEqual(list(rows), KEYS)
        self.assertEqual(fx_core.DEFAULTS["universe"], KEYS)
        self.assertEqual(fx_core.DEFAULTS["day_universe"], ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])

    def test_yahoo_symbols_and_sources(self):
        rows = fx_core.DEFAULTS["instruments"]
        for sym in KEYS[:12]:
            self.assertEqual(rows[sym]["yahoo"], sym + "=X")
            self.assertEqual(rows[sym]["price_source"], "spot_fx")
            self.assertEqual((rows[sym]["hours"], rows[sym]["data_hours"]), ("fx", "yahoo_fx"))
            self.assertIsNone(rows[sym]["futures"])
            self.assertEqual(rows[sym]["profit_currency"], rows[sym]["quote"])
        futs = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "XPTUSD": "PL=F", "XPDUSD": "PA=F",
                "COPPER": "HG=F", "USOIL": "CL=F", "UKOIL": "BZ=F", "NATGAS": "NG=F"}
        for sym, yahoo in futs.items():
            self.assertEqual(rows[sym]["yahoo"], yahoo)
            self.assertEqual(rows[sym]["price_source"], "futures")
            self.assertEqual(rows[sym]["futures"]["root"], yahoo[:2])
            self.assertEqual(rows[sym]["data_hours"], "cme")
        self.assertEqual(rows["UKOIL"]["hours"], "ice_brent")
        self.assertEqual(rows["UKOIL"]["futures"]["settle"], ["london", "19:30"])
        self.assertIs(rows["UKOIL"]["futures"]["shortname_month"], False)
        self.assertIs(rows["XAGUSD"]["futures"]["history_check"], False)
        self.assertIs(rows["COPPER"]["futures"]["history_check"], False)
        self.assertEqual(rows["XPTUSD"]["futures"]["suffix"], ".NYM")
        self.assertEqual(rows["XAUUSD"]["futures"]["cycle"], "GJMQZ")

    def test_pip_and_groups(self):
        rows = fx_core.DEFAULTS["instruments"]
        for sym in ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"):
            self.assertEqual(rows[sym]["pip"], 0.01)
            self.assertEqual(rows[sym]["group"], "jpy")
        self.assertEqual(rows["EURUSD"]["pip"], 0.0001)
        self.assertIsNone(rows["XAUUSD"]["pip"])
        self.assertEqual(rows["COPPER"]["group"], "industrial")
        self.assertEqual(rows["NATGAS"]["group"], "natgas")
        self.assertEqual(rows["EURGBP"]["group"], "eur_crosses")

    def test_defaults_pass_and_fill_derived(self):
        cfg = checked({})
        gold = cfg["instruments"]["XAUUSD"]
        self.assertEqual((gold["point"], gold["tick_size"], gold["broker_symbol"]), (0.01, 0.01, "XAUUSD"))
        self.assertEqual(cfg["instruments"]["EURUSD"]["point"], 0.00001)
        inst = fx_core.instrument(cfg, "USDJPY")
        self.assertEqual((inst["point"], inst["pip"]), (0.001, 0.01))


NEW_ROW = {"name": "US dollar / Swedish krona", "kind": "fx", "yahoo": "USDSEK=X",
           "price_source": "spot_fx", "base": "USD", "quote": "SEK", "profit_currency": "USD",
           "margin_currency": "USD", "contract_size": 100000, "digits": 5, "min_lot": 0.01,
           "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "spread_points": 30,
           "swap_mode": "points", "swap_long": 1.0, "swap_short": -2.0,
           "swap_triple_day": "wednesday", "hours": "fx", "data_hours": "yahoo_fx",
           "group": "usd_majors"}


class Merge(unittest.TestCase):
    def test_user_row_changes_only_its_columns(self):
        cfg = checked({"instruments": {"XAUUSD": {"spread_points": 25}}})
        gold = cfg["instruments"]["XAUUSD"]
        self.assertEqual(gold["spread_points"], 25)
        self.assertEqual(gold["contract_size"], 100)
        self.assertEqual(gold["yahoo"], "GC=F")
        self.assertEqual(list(cfg["instruments"]), KEYS)
        self.assertEqual(fx_core.DEFAULTS["instruments"]["XAUUSD"]["spread_points"], 9)

    def test_broker_symbol_and_digits_override(self):
        cfg = checked({"instruments": {"XAUUSD": {"broker_symbol": "GOLD", "digits": 3,
                                                  "spread_points": 90}}})
        gold = cfg["instruments"]["XAUUSD"]
        self.assertEqual((gold["broker_symbol"], gold["point"], gold["tick_size"]), ("GOLD", 0.001, 0.001))

    def test_objects_merge_one_level(self):
        cfg = checked({"risk": {"max_open_trades": 5}, "account": {"balance": 20000}})
        self.assertEqual(cfg["risk"]["max_open_trades"], 5)
        self.assertEqual(cfg["risk"]["max_daily_loss_pct"], 3.0)
        self.assertEqual(cfg["account"]["balance"], 20000)
        self.assertEqual(cfg["account"]["leverage"], 100)

    def test_lists_and_tables_replaced_whole(self):
        cfg = checked({"universe": ["XAUUSD"], "holidays": {"2026-12-25": "Christmas"}})
        self.assertEqual(cfg["universe"], ["XAUUSD"])
        self.assertEqual(cfg["holidays"], {"2026-12-25": "Christmas"})
        cfg = checked({"news": {"feeds": [{"name": "mine", "url": "https://example.com/rss"}]}})
        self.assertEqual(len(cfg["news"]["feeds"]), 1)
        self.assertEqual(cfg["news"]["impacts"], ["High"])

    def test_new_instrument_gets_column_defaults(self):
        cfg = checked({"instruments": {"USDSEK": dict(NEW_ROW)}})
        row = cfg["instruments"]["USDSEK"]
        self.assertEqual(list(cfg["instruments"])[-1], "USDSEK")
        self.assertEqual((row["broker_symbol"], row["point"], row["margin_rate"]), ("USDSEK", 0.00001, 1.0))
        self.assertIsNone(row["futures"])
        self.assertEqual(row["commission_per_lot_side"], 0)

    def test_new_instrument_needs_every_column(self):
        row = dict(NEW_ROW)
        del row["yahoo"]
        with self.assertRaises(ValueError) as cm:
            checked({"instruments": {"USDSEK": row}})
        self.assertEqual(str(cm.exception), 'fx/config.json: instrument "USDSEK": a new instrument '
                         'needs "yahoo". Copy it from MetaTrader (the built-in rows show each column).')
        row = dict(NEW_ROW, price_source="futures", kind="metal")
        with self.assertRaises(ValueError) as cm:
            checked({"instruments": {"XCUUSD": row}})
        self.assertIn('needs "futures"', str(cm.exception))

    def test_whole_numbers_stored_as_ints(self):
        cfg = checked({"day": {"or_minutes": 15.0}, "risk": {"max_open_trades": 4.0}})
        self.assertIs(type(cfg["day"]["or_minutes"]), int)
        self.assertIs(type(cfg["risk"]["max_open_trades"]), int)

    def test_unknown_keys_kept(self):
        cfg = checked({"alow_short": False})
        self.assertIs(cfg["alow_short"], False)
        self.assertIs(cfg["allow_short"], True)


class LoadConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def load(self, doc):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = fx_core.load_config(self.path)
        return cfg, err.getvalue()

    def refused(self, doc):
        with self.assertRaises(ValueError) as cm:
            self.load(doc)
        return str(cm.exception)

    def test_missing_file_gives_defaults_and_the_note(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = fx_core.load_config(os.path.join(self.dir, "none.json"))
        self.assertEqual(err.getvalue(), fx_core.CONFIG_NOTE + "\n")
        self.assertEqual(fx_core.CONFIG_NOTE,
                         "No fx/config.json yet. Using the example values. Copy your own from MetaTrader.")
        self.assertEqual(list(cfg["instruments"]), KEYS)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = fx_core.load_config(os.path.join(self.dir, "none.json"), required=False)
        self.assertEqual(cfg["account"]["balance"], 1000)

    def test_typed_config_that_is_missing_is_an_error(self):
        typo = os.path.join(self.dir, "typo.json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(ValueError) as cm:
                fx_core.load_config(typo, required=True)
        self.assertEqual(str(cm.exception), "no config file at %s (copy config.example.json "
                         "from the fx-budget folder)" % typo)
        self.assertEqual(err.getvalue(), "")   # no note: the error says it all
        with open(typo, "w", encoding="utf-8") as fh:
            fh.write("{}")
        with contextlib.redirect_stderr(err):
            self.assertEqual(list(fx_core.load_config(typo, required=True)["instruments"]), KEYS)

    def test_secrets_refused_anywhere(self):
        tail = ". Never put MetaTrader logins or passwords in the pack's files."
        self.assertEqual(self.refused({"password": "x"}), '%s: remove "password"%s' % (self.path, tail))
        self.assertEqual(self.refused({"account": {"Login": 123}}), '%s: remove "Login"%s' % (self.path, tail))
        self.assertEqual(self.refused({"news": {"feeds": [{"name": "a", "url": "https://a",
                                                           "account_number": "9"}]}}),
                         '%s: remove "account_number"%s' % (self.path, tail))
        for key in ("investor_password", "MASTER_PASSWORD"):
            self.assertIn('remove "%s"' % key, self.refused({"instruments": {"EURUSD": {key: "x"}}}))

    def test_not_an_object(self):
        self.assertEqual(self.refused([1, 2]), "%s must be a JSON object: {...}" % self.path)
        self.assertIn("is not valid JSON", self.refused("{oops"))
        self.assertEqual(self.refused(""), "%s is empty" % self.path)

    def test_messages_use_the_path_given(self):
        self.assertEqual(self.refused({"paper": "true"}),
                         '%s: "paper" must be true or false (you have "true")' % self.path)

    def test_unknown_key_warns(self):
        cfg, err = self.load({"alow_short": False})
        self.assertEqual(err, '%s: unknown key "alow_short" (skipped). Check the spelling.\n' % self.path)
        self.assertIs(cfg["allow_short"], True)

    def test_utc0_server_warns_on_default_day_end(self):
        cfg, err = self.load({"account": {"server_time": "utc+0"}})
        self.assertEqual(err, 'Your server clock is UTC+0. Such brokers often end the day at 00:00 UTC. '
                         'Check "day_end".\n')
        cfg, err = self.load({"account": {"server_time": "utc+0"}, "day_end": "00:00 UTC"})
        self.assertEqual(err, "")
        cfg, err = self.load({})
        self.assertEqual(err, "")


class KeyMessages(unittest.TestCase):
    def bad(self, doc, msg):
        with self.assertRaises(ValueError) as cm:
            checked(doc)
        self.assertEqual(str(cm.exception), "fx/config.json: " + msg)

    def test_generic_messages(self):
        self.bad({"paper": "true"}, '"paper" must be true or false (you have "true")')
        self.bad({"yahoo": 1}, '"yahoo" must be true or false (you have 1)')
        self.bad({"account": {"balance": 0}},
                 '"account.balance" must be a number from 1 to 100,000,000 (you have 0)')
        self.bad({"account": {"leverage": True}},
                 '"account.leverage" must be a number from 1 to 5,000 (you have true)')
        self.bad({"risk": {"max_open_trades": 2.5}},
                 '"risk.max_open_trades" must be a whole number from 1 to 50 (you have 2.5)')
        self.bad({"style": "scalp"}, '"style" must be one of swing, day (you have "scalp")')
        self.bad({"day": {"flat_by": "9:30"}}, '"day.flat_by" must be a time like 23:30 (you have "9:30")')
        self.bad({"universe": ["XAUSD"]}, '"universe": "XAUSD" is not in your instrument list')
        self.bad({"risk": {"blocked": ["GOLD"]}}, '"risk.blocked": "GOLD" is not in your instrument list')

    def test_account(self):
        self.bad({"account": {"currency": "EUR"}},
                 '"account.currency" must be "USD". The pack counts money in US dollars only.')
        self.bad({"account": {"stop_out_pct": 120}},
                 '"account.stop_out_pct" must not be above "account.margin_call_pct"')
        self.bad({"account": {"server_time": "gmt+2"}},
                 '"account.server_time" must be "ny+7" (GMT+2 in winter, GMT+3 in summer) or like "utc+0"')
        self.bad({"account": {"server_time": "utc+15"}},
                 '"account.server_time" must be "ny+7" (GMT+2 in winter, GMT+3 in summer) or like "utc+0"')
        self.bad({"account": {"platform": "mt4", "hedging": False}},
                 'MT4 accounts always hedge. Set "account.hedging" to true.')
        checked({"account": {"platform": "mt5", "hedging": False, "server_time": "utc-3"}})

    def test_lists(self):
        self.bad({"universe": ["EURUSD", "XAUUSD", "EURUSD"]}, '"universe" lists "EURUSD" twice')
        self.bad({"universe": []},
                 '"universe" must be a list of instrument keys, at least 1 (you have [])')
        self.bad({"day_universe": "EURUSD"},
                 '"day_universe" must be a list of instrument keys, at least 1 (you have "EURUSD")')
        self.bad({"day_end": "17:00 UTC"},
                 '"day_end" must be one of 17:00 New York, 00:00 UTC (you have "17:00 UTC")')

    def test_risk(self):
        self.bad({"risk": {"max_daily_loss_pct": 0.5}},
                 '"risk.max_daily_loss_pct" is below the risk of one trade. No trade could pass.')
        self.bad({"risk": {"max_risk_per_trade_usd": 100, "max_daily_loss_usd": 50}},
                 '"risk.max_daily_loss_usd" is below the risk of one trade. No trade could pass.')
        self.bad({"risk": {"max_risk_per_trade_usd": 0.5}},
                 '"risk.max_risk_per_trade_usd" must be a number from 1 to 1,000,000 (you have 0.5)')
        self.bad({"risk": {"max_orders_per_day": 2}},
                 '"risk.max_orders_per_day" must be at least "risk.max_trades_per_day"')
        self.bad({"risk": {"min_margin_level_pct": 50}},
                 '"risk.min_margin_level_pct" must be at least the margin call level')
        self.bad({"risk": {"max_risk_per_trade_pct": 0.001}},
                 '"risk.max_risk_per_trade_pct" must be a number from 0.01 to 10 (you have 0.001)')
        self.bad({"risk": {"require_stop": "yes"}}, '"risk.require_stop" must be true or false (you have "yes")')
        cfg = checked({"risk": {"max_risk_per_trade_usd": 50, "max_daily_loss_usd": 150}})
        self.assertEqual(cfg["risk"]["max_daily_loss_usd"], 150)

    def test_swing(self):
        self.bad({"swing": {"stop": "price:4000"}},
                 '"swing.stop" cannot be a fixed price. Give a price with --stop on the command line.')
        self.bad({"swing": {"stop": "atr:20"}}, '"swing.stop" must be atr:K (K from 0.5 to 10) or one of '
                 'swing-low, swing-high, flag-far, bar-far, setup (you have "atr:20")')
        self.bad({"swing": {"target": "2"}}, '"swing.target" must be like 2R, from 0.5R to 10R (you have "2")')
        self.bad({"swing": {"entry": "stop"}}, '"swing.entry" must be one of market, limit (you have "stop")')
        self.bad({"swing": {"max_hold_days": 0}},
                 '"swing.max_hold_days" must be a whole number from 1 to 120 (you have 0)')
        self.bad({"swing": {"friday_cutoff_ny": "17:00"}},
                 '"swing.friday_cutoff_ny" must be before 17:00 New York (the week ends then)')
        for rule in ("swing-low", "swing-high", "flag-far", "bar-far", "setup", "atr:0.5", "atr:2.5"):
            checked({"swing": {"stop": rule, "target": "1.5R", "friday_cutoff_ny": "16:59"}})

    def test_day_keys(self):
        self.bad({"day": {"or_minutes": 7}}, '"day.or_minutes" must be a multiple of 5 (the bars are 5 minutes)')
        self.bad({"day": {"or_minutes": 125}},
                 '"day.or_minutes" must be a whole number from 5 to 120 (you have 125)')
        self.bad({"day": {"or_session": "sydney"}},
                 '"day.or_session" must be one of london, new_york, tokyo (you have "sydney")')
        self.bad({"day": {"stop": "swing-low"}},
                 '"day.stop" must be atr:K (K from 0.5 to 10) or one of or-mid, or-far, setup (you have "swing-low")')
        self.bad({"day": {"target": "11R"}}, '"day.target" must be like 2R, from 0.5R to 10R (you have "11R")')
        for rule in ("or-mid", "or-far", "setup", "atr:1"):
            checked({"day": {"stop": rule}})

    def test_news_and_calendar(self):
        self.bad({"news": {"quiet_before_min": 300}},
                 '"news.quiet_before_min" must be a whole number from 0 to 240 (you have 300)')
        self.bad({"news": {"impacts": []}}, '"news.impacts" must be some of High, Medium, Low (you have [])')
        self.bad({"news": {"impacts": ["high"]}},
                 '"news.impacts" must be some of High, Medium, Low (you have ["high"])')
        feeds = '"news.feeds": each feed needs a "name" (letters, digits, -) and a "url"'
        self.bad({"news": {"feeds": [{"name": "My Feed", "url": "https://a.b/rss"}]}}, feeds)
        self.bad({"news": {"feeds": [{"name": "a", "url": "ftp://a.b/rss"}]}}, feeds)
        self.bad({"news": {"feeds": [{"name": "a"}]}}, feeds)
        self.bad({"news": {"feeds": ["https://a.b/rss"]}}, feeds)
        self.bad({"news": {"feeds": [{"name": "a", "url": "https://a"}, {"name": "a", "url": "http://b"}]}},
                 '"news.feeds" lists "a" twice')
        self.bad({"calendar_url": "http://nfs.faireconomy.media/ff_calendar_thisweek.json"},
                 '"calendar_url" must start with https:// or be "" (off)')
        self.assertEqual(checked({"calendar_url": "", "yahoo": False})["calendar_url"], "")

    def test_dates(self):
        self.bad({"holidays": {"25-12-2026": "Christmas"}}, '"holidays" wants {"2026-12-25": "Christmas"}')
        self.bad({"holidays": ["2026-12-25"]}, '"holidays" wants {"2026-12-25": "Christmas"}')
        self.bad({"holidays": {"2026-02-30": "x"}}, '"holidays" wants {"2026-12-25": "Christmas"}')
        self.bad({"early_closes": {"2026-11-27": "1:45 PM"}},
                 '"early_closes" wants {"2026-11-27": "13:45"} (New York time)')
        self.bad({"early_closes": {"2026-11-27": "17:00"}},
                 '"early_closes" 2026-11-27 17:00 must be before 17:00 New York (the day ends then)')
        self.bad({"fx_rates_max_age_hours": 200},
                 '"fx_rates_max_age_hours" must be a number from 1 to 168 (you have 200)')
        cfg = checked({"holidays": {"2026-12-25": "Christmas"}, "early_closes": {"2026-11-27": "13:45"}})
        self.assertEqual(cfg["early_closes"], {"2026-11-27": "13:45"})

    def test_not_objects(self):
        self.bad({"instruments": []}, '"instruments" must be a JSON object: {...}')
        self.bad({"risk": None}, '"risk" must be a JSON object: {...}')
        self.bad({"instruments": {"XAUUSD": 5}}, 'instrument "XAUUSD": must be a JSON object: {...}')
        with self.assertRaises(ValueError) as cm:
            fx_core.check_config([])
        self.assertEqual(str(cm.exception), "fx/config.json must be a JSON object: {...}")


class DayTimes(unittest.TestCase):
    """The D15 check of 01 3.3 on the US winter day 2026-01-14 and the US summer day 2026-07-15."""

    def bad(self, day, msg, day_end="17:00 New York"):
        with self.assertRaises(ValueError) as cm:
            checked({"day": day, "day_end": day_end})
        self.assertEqual(str(cm.exception), "fx/config.json: " + msg)

    def ok(self, day, day_end="17:00 New York"):
        return checked({"day": day, "day_end": day_end})

    def test_worked_numbers(self):
        # FX day start in PKT and the 23:30 offset (the table of 01 3.3)
        rows = [("17:00 New York", "2026-01-14", "03:00", 20.5), ("17:00 New York", "2026-07-15", "02:00", 21.5),
                ("00:00 UTC", "2026-01-14", "05:00", 18.5), ("00:00 UTC", "2026-07-15", "05:00", 18.5)]
        for day_end, t, start, hours in rows:
            s = fx_core.fx_day_bounds(t, day_end)[0]
            f = fx_core.pkt_time_on_fx_day("23:30", t, day_end)
            self.assertEqual(fx_core._pkt_hhmm(s), start + " PKT")
            self.assertEqual((f - s).total_seconds() / 3600, hours)

    def test_defaults_pass_both_day_ends(self):
        self.ok({})
        self.ok({}, "00:00 UTC")

    def test_latest_flat_by_all_year_is_0145(self):
        for day_end in ("17:00 New York", "00:00 UTC"):
            self.ok({"flat_by": "01:45"}, day_end)
            self.bad({"flat_by": "01:50"}, '"day.flat_by" 01:50 is too close to the 17:00 New York '
                     'rollover in US summer (02:00 PKT). Pick 01:45 or earlier.', day_end)

    def test_rule_1_first_hour(self):
        self.bad({"flat_by": "02:30"}, '"day.flat_by" 02:30 falls in the first hour of the FX day in '
                 'US summer (that hour starts at 02:00 PKT). Pick a time like 23:30.')
        self.bad({"flat_by": "05:30", "no_entries_after": "05:00"}, '"day.flat_by" 05:30 falls in the '
                 'first hour of the FX day in US winter (that hour starts at 05:00 PKT). Pick a time '
                 'like 23:30.', "00:00 UTC")

    def test_rule_2_rollover(self):
        self.bad({"flat_by": "02:55"}, '"day.flat_by" 02:55 is too close to the 17:00 New York '
                 'rollover in US winter (03:00 PKT). Pick 01:45 or earlier.')
        self.bad({"flat_by": "02:50"}, '"day.flat_by" 02:50 is too close to the 17:00 New York '
                 'rollover in US winter (03:00 PKT). Pick 01:45 or earlier.', "00:00 UTC")

    def test_rule_3_entries_before_flat_by(self):
        self.bad({"no_entries_after": "23:20"},
                 '"day.no_entries_after" 23:20 must be at least 15 minutes before "day.flat_by" 23:30')
        self.ok({"no_entries_after": "23:15"})
        self.bad({"flat_by": "04:30"},
                 '"day.no_entries_after" 21:00 must be at least 15 minutes before "day.flat_by" 04:30')

    def test_rule_4_after_the_opening_range(self):
        self.bad({"no_entries_after": "13:15"}, '"day.no_entries_after" 13:15 is before the London '
                 'opening range ends (13:15 PKT in US winter). No day trade could start.')
        self.ok({"no_entries_after": "13:20"})
        self.bad({"no_entries_after": "18:10", "or_session": "new_york", "or_minutes": 15},
                 '"day.no_entries_after" 18:10 is before the New York opening range ends '
                 '(18:15 PKT in US winter). No day trade could start.')


class InstrumentMessages(unittest.TestCase):
    def bad(self, key, row, msg):
        with self.assertRaises(ValueError) as cm:
            checked({"instruments": {key: row}})
        self.assertEqual(str(cm.exception), 'fx/config.json: instrument "%s": %s' % (key, msg))

    def test_kind_digits_point_tick(self):
        self.bad("XAUUSD", {"kind": "stock"}, '"kind" must be one of fx, metal, energy (you have "stock")')
        self.bad("XAUUSD", {"price_source": "spot"},
                 '"price_source" must be one of spot_fx, futures (you have "spot")')
        self.bad("XAUUSD", {"digits": 9}, '"digits" must be a whole number from 0 to 8 (you have 9)')
        self.bad("XAUUSD", {"digits": 3, "point": 0.01}, '"point" 0.01 does not match "digits" 3 (that gives 0.001)')
        self.bad("EURUSD", {"point": 0.0001}, '"point" 0.0001 does not match "digits" 5 (that gives 0.00001)')
        self.bad("XAUUSD", {"tick_size": 0.015}, '"tick_size" must be a whole number of points')
        self.bad("XAUUSD", {"tick_size": 20}, '"tick_size" must be a whole number of points')
        cfg = checked({"instruments": {"XAUUSD": {"tick_size": 0.05}, "EURUSD": {"point": 0.00001}}})
        self.assertEqual(cfg["instruments"]["XAUUSD"]["tick_size"], 0.05)

    def test_sizes_and_lots(self):
        self.bad("USOIL", {"contract_size": 0},
                 '"contract_size" must be above 0. Copy "Contract size" from MetaTrader.')
        self.bad("XAUUSD", {"min_lot": 0.015}, '"min_lot" 0.015 is not a whole number of "lot_step" 0.01')
        self.bad("XAUUSD", {"min_lot": 0}, '"min_lot" must be a number above 0 (you have 0)')
        self.bad("XAUUSD", {"lot_step": -1}, '"lot_step" must be a number above 0 (you have -1)')
        self.bad("XAUUSD", {"min_lot": 200}, '"min_lot" must not be above "max_lot"')
        checked({"instruments": {"USOIL": {"min_lot": 0.5, "lot_step": 0.1, "contract_size": 100}}})

    def test_modes_and_days(self):
        self.bad("XAUUSD", {"calc_mode": "cfd-leverage"}, '"calc_mode" must be one of forex, '
                 'forex_no_leverage, cfd, cfd_leverage, futures (you have "cfd-leverage")')
        self.bad("XAUUSD", {"swap_mode": "money"},
                 '"swap_mode" must be one of points, deposit, base, margin, profit, interest, disabled')
        self.bad("XAUUSD", {"swap_triple_day": "saturday"}, '"swap_triple_day" must be monday to friday, '
                 'or none. MetaTrader charges no swap on Saturday or Sunday nights.')
        self.bad("XAUUSD", {"hours": "cme"}, '"hours" must be one of fx, comex, nymex, ice_brent (you have "cme")')
        self.bad("XAUUSD", {"data_hours": "comex"},
                 '"data_hours" must be one of yahoo_fx, cme (you have "comex")')
        checked({"instruments": {"USOIL": {"swap_triple_day": "none"}}})

    def test_money_columns(self):
        self.bad("XAUUSD", {"spread_points": 0.09},
                 '"spread_points" is in points, not price. 0.09 for gold looks like a price: use 9.')
        self.bad("XAUUSD", {"spread_points": -1},
                 '"spread_points" must be a number from 0 to 100,000 (you have -1)')
        self.bad("XAUUSD", {"commission_per_lot_side": 2000},
                 '"commission_per_lot_side" must be a number from 0 to 1,000 (you have 2000)')
        self.bad("XAUUSD", {"swap_long": "-54.84"}, '"swap_long" must be a number, like -6.23')
        self.bad("XAUUSD", {"swap_short": None}, '"swap_short" must be a number, like -6.23')
        self.bad("XAUUSD", {"swap_free_fee_per_lot": -5},
                 '"swap_free_fee_per_lot" must be a number from 0 to 1,000 (you have -5)')
        self.bad("XAUUSD", {"swap_free_grace_nights": 61},
                 '"swap_free_grace_nights" must be a whole number from 0 to 60 (you have 61)')
        self.bad("XAUUSD", {"leverage": 0.5}, '"leverage" must be a number from 1 to 5,000 (you have 0.5)')
        self.bad("XAUUSD", {"initial_margin": 0}, '"initial_margin" must be a number above 0 (you have 0)')
        self.bad("XAUUSD", {"margin_rate": 0}, '"margin_rate" must be a number above 0, up to 100 (you have 0)')
        self.bad("COPPER", {"yahoo_to_broker": 0}, '"yahoo_to_broker" must be a number above 0 (you have 0)')
        cfg = checked({"instruments": {"COPPER": {"yahoo_to_broker": 2204.62, "contract_size": 1,
                                                  "spread_points": 0, "leverage": None}}})
        self.assertEqual(cfg["instruments"]["COPPER"]["yahoo_to_broker"], 2204.62)

    def test_futures_object(self):
        need = '"futures" needs root, suffix, cycle, roll_rule, settle, shortname_month, history_check'
        self.bad("XAUUSD", {"futures": None}, need)
        self.bad("XAUUSD", {"futures": {"root": "GC"}}, need)
        gold = dict(fx_core.DEFAULTS["instruments"]["XAUUSD"]["futures"])
        self.bad("XAUUSD", {"futures": dict(gold, suffix=".NYMEX")},
                 '"futures.suffix" must be one of .CMX, .NYM (you have ".NYMEX")')
        self.bad("XAUUSD", {"futures": dict(gold, cycle="GJMQZA")},
                 '"futures.cycle" must be month letters from FGHJKMNQUVXZ (you have "GJMQZA")')
        self.bad("XAUUSD", {"futures": dict(gold, settle=["chicago", "13:30"])},
                 '"futures.settle" must be like ["ny", "13:30"] (you have ["chicago", "13:30"])')
        self.bad("XAUUSD", {"futures": dict(gold, roll_rule="gc")},
                 '"futures.roll_rule" must be one of gc_fnd, si_hg, cl_ltd, bz_ltd, ng_ltd (you have "gc")')
        checked({"instruments": {"EURUSD": {"futures": None}}})

    def test_futures_lists_match_the_roll_code(self):
        self.assertEqual(fx_core.FUTURES_MONTH_LETTERS, "FGHJKMNQUVXZ")
        self.assertEqual(fx_core.FUTURES_MONTH_LETTERS, "".join(fx_core.MONTH_CODES.values()))
        for rule in fx_core.ROLL_RULES:
            fx_core.roll_dates(rule, 2026, 12)   # each rule the check takes, the roll code knows
        with self.assertRaises(ValueError) as cm:
            fx_core.roll_dates("gc", 2026, 12)
        names = ", ".join(fx_core.ROLL_RULES[:-1]) + " or " + fx_core.ROLL_RULES[-1]
        self.assertEqual(str(cm.exception), 'Unknown roll_rule "gc". Use %s.' % names)

    def test_profit_currency(self):
        row = dict(NEW_ROW, profit_currency="SEK")
        self.bad("USDSEK", row, '"profit_currency" "SEK" has no Yahoo rate in this pack')

    def test_margin_currency(self):
        self.bad("XAUUSD", {"margin_currency": "XAG"},
                 '"margin_currency" "XAG" must be USD, a currency of this pack or the base XAU')
        self.bad("EURUSD", {"margin_currency": "eur"},
                 '"margin_currency" "eur" must be USD, a currency of this pack or the base EUR')
        checked({"instruments": {"XAUUSD": {"margin_currency": "XAU"}}})   # the base is fine
        checked({"instruments": {"EURUSD": {"margin_currency": "USD"}}})

    def test_new_instrument_checked_too(self):
        self.bad("USDSEK", dict(NEW_ROW, digits=4, point=0.001),
                 '"point" 0.001 does not match "digits" 4 (that gives 0.0001)')


if __name__ == "__main__":
    unittest.main()
