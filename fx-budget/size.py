#!/usr/bin/env python3
"""Lot sizing for fx-budget. Standard library only, Python 3.8.

It sizes ONE trade in lots from fx/plan.json (fx-exit writes it), to your max loss
per trade in US dollars, with the spread, the commission and the swap counted. With
--write it saves ONE order to fx/orders.json for fx-risk. It never places, approves
or sends an order. It never moves a stop or a target, and never raises a limit.

    python3 fx-budget/size.py                         # show the size, write nothing
    python3 fx-budget/size.py --write                 # write fx/orders.json
    python3 fx-budget/size.py --risk-usd 50 --write   # at most $50 at the stop
    python3 fx-budget/size.py --risk-pct 0.5 --json   # half a percent, JSON answer
    python3 fx-budget/size.py --plan old-plan.json --offline
    python3 fx-budget/size.py --offline --now "2026-07-17 09:07" --write

The lots are the smallest of four limits, rounded DOWN to lot_step:
  max loss     the lots that lose at most your max loss at the stop, costs in
               (the smaller of max_risk_per_trade_pct of the balance and
               max_risk_per_trade_usd)
  max lot      the broker's largest order (max_lot)
  margin cap   max_margin_used_pct of the balance, minus the margin in use
  free margin  equity minus the margin in use
Below min_lot it says no (exit 1). It never rounds up.

Exit codes: 0 sized, 1 refused (a plain no), 2 error (one line on stderr).
"""

import argparse
import copy
import datetime
import difflib
import gzip
import hashlib
import json
import math
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

TOOL = "size.py"
SKILL = "fx-budget"


# ---------------------------------------------------------------- copy together
# Each line is a copied helper and the names from tests/fx_core.py it uses.
# "-" means it uses nothing else from fx_core. Made from the code itself.
#   load_json: -
#   save_json_atomic: -
#   fx_paths: -
#   load_doc: load_json
#   check_objects: -
#   check_symbol: -
#   check_side: -
#   check_style: -
#   check_utc: -
#   _positive_number: -
#   check_price: _positive_number
#   check_lots: _positive_number
#   JSON_VERSION: -
#   _words_dec: -
#   money: _words_dec
#   signed_money: _words_dec, money
#   price_text: _words_dec
#   lots_text: _words_dec
#   side_word: -
#   age_text: _words_dec
#   scale_note: -
#   both_times: ny_label, pkt_label
#   add_common: -
#   fail: -
#   emit_json: -
#   _dec_clean: -
#   _price_dec: _dec_clean
#   instrument: _dec_clean
#   _to_tick: _dec_clean
#   round_price: _price_dec, _to_tick
#   round_away: _price_dec, _to_tick
#   to_points: _dec_clean
#   pips_text: _dec_clean, to_points
#   spread_price: _dec_clean
#   ask_of: _price_dec, round_price, spread_price
#   KARACHI_HOURS: -
#   DAY_NAMES: -
#   MONTH_NAMES: -
#   utc_now: -
#   to_pkt: KARACHI_HOURS
#   pkt_to_utc: KARACHI_HOURS
#   parse_now: pkt_to_utc, utc_now
#   utc_iso: -
#   parse_utc: -
#   ts_to_utc: -
#   utc_to_ts: -
#   clock_label: -
#   day_label: DAY_NAMES
#   short_date: DAY_NAMES, MONTH_NAMES
#   pkt_label: DAY_NAMES, clock_label, short_date, to_pkt
#   ny_label: clock_label, short_date, to_ny
#   parse_hhmm: -
#   nth_weekday: -
#   last_weekday: -
#   us_dst: nth_weekday
#   uk_dst: last_weekday
#   sydney_dst: nth_weekday
#   to_ny: us_dst
#   ny_to_utc: us_dst
#   london_to_utc: uk_dst
#   sydney_to_utc: sydney_dst
#   tokyo_to_utc: -
#   city_to_utc: london_to_utc, ny_to_utc, sydney_to_utc, tokyo_to_utc
#   SERVER_TIME_BAD: -
#   server_offset: SERVER_TIME_BAD
#   DAY_ENDS: -
#   FULL_DAY_NAMES: -
#   OR_WINDOWS: -
#   check_day_end: DAY_ENDS
#   parse_fx_date: FULL_DAY_NAMES
#   at_time: parse_hhmm
#   fx_day: check_day_end, to_ny
#   fx_day_bounds: check_day_end, ny_to_utc, parse_fx_date
#   last_fx_day: fx_day, to_ny
#   pkt_time_on_fx_day: fx_day_bounds, parse_hhmm, pkt_to_utc, to_pkt
#   opening_range_window: OR_WINDOWS, at_time, city_to_utc, parse_fx_date
#   HOLIDAYS: -
#   EARLY_CLOSES: -
#   HOURS: -
#   BROKER_TEMPLATES: -
#   DATA_TEMPLATES: -
#   hours_template: HOURS
#   group_closed: HOLIDAYS, parse_fx_date
#   early_close_utc: EARLY_CLOSES, at_time, ny_to_utc, parse_fx_date
#   yahoo_no_data_cuts: HOLIDAYS, at_time, ny_to_utc, parse_fx_date
#   open_spans: DATA_TEMPLATES, at_time, city_to_utc, early_close_utc, group_closed, hours_template, ny_to_utc, parse_fx_date, yahoo_no_data_cuts
#   UA: -
#   YAHOO_HOSTS: -
#   PACE_SECONDS: -
#   TIMEOUT: -
#   LOOKBACK: -
#   FIRST_RANGE: -
#   MIN_REFRESH_S: -
#   STEP_S: -
#   SLEEP: -
#   RETRY_WAIT_S: -
#   _last_request: -
#   _last_host: -
#   NoData: -
#   chart_url: -
#   _url_symbol: -
#   _url_interval: -
#   is_contract_symbol: -
#   _yahoo_error: NoData, is_contract_symbol
#   _error_description: -
#   _pace: PACE_SECONDS, SLEEP, _last_request
#   _gunzip: -
#   _one_get: UA, _gunzip
#   yahoo_get: RETRY_WAIT_S, SLEEP, TIMEOUT, YAHOO_HOSTS, _error_description, _last_host, _one_get, _pace, _url_interval, _url_symbol, _yahoo_error
#   _ERROR_CODES: -
#   fetch_chart: NoData, YAHOO_HOSTS, _ERROR_CODES, _yahoo_error, chart_url, yahoo_get
#   parse_chart: -
#   FETCH_LOG_MAX: -
#   cache_file: -
#   load_cache: cache_file
#   save_cache: -
#   last_fetch: -
#   merge_rows: LOOKBACK, utc_to_ts
#   stored_rows: -
#   refresh: FIRST_RANGE, LOOKBACK, MIN_REFRESH_S, STEP_S, fetch_into, last_fetch, load_cache, parse_utc, utc_to_ts
#   fetch_into: FETCH_LOG_MAX, _last_host, cache_file, fetch_chart, merge_rows, parse_chart, save_cache, stored_rows, utc_iso
#   DONE_MARGIN_MIN: -
#   STALE_MINUTES: -
#   YAHOO_DECIMALS: -
#   symbol_root: -
#   inst_root: symbol_root
#   yahoo_decimals: YAHOO_DECIMALS, inst_root
#   _round_dec: -
#   clean_rows: _round_dec, ts_to_utc, yahoo_decimals
#   _span_of: fx_day, open_spans
#   bar_done: DONE_MARGIN_MIN, STEP_S, _span_of
#   load_bars: STEP_S, _dec_clean, _span_of, adjust_rows, bar_done, clean_rows, inst_root, last_fetch, load_cache, parse_utc, ts_to_utc
#   data_age_min: -
#   _plan_bid: _dec_clean, _round_dec, yahoo_decimals
#   latest_price: STEP_S, _dec_clean, _plan_bid, _round_dec, data_age_min, last_fetch, load_bars, load_cache, parse_utc, ts_to_utc, yahoo_decimals
#   MONTH_CODES: -
#   ROLL_MATCH_BARS: -
#   ROLL_CHECK_HOURS: -
#   ROLL_SAME_DAYS: -
#   rolls_path: -
#   load_rolls_doc: load_json, rolls_path
#   load_rolls: load_rolls_doc
#   save_rolls: load_rolls_doc, rolls_path, save_json_atomic, utc_iso, utc_now
#   contract_from_name: MONTH_CODES
#   cme_business_day: HOLIDAYS
#   add_business_days: cme_business_day
#   _month_add: -
#   _last_business_day: _month_add, cme_business_day
#   roll_dates: _last_business_day, _month_add, add_business_days, cme_business_day
#   _cycle_contracts: -
#   _windows: _cycle_contracts, at_time, ny_to_utc, roll_dates
#   expected_front: _windows, utc_iso
#   expected_rolls: _windows
#   _roll_bar: _round_dec, ts_to_utc
#   adjust_rows: YAHOO_DECIMALS, _roll_bar, parse_utc
#   ROLL_AWAY: -
#   _get_path: -
#   _set_path: -
#   rebase: ROLL_AWAY, _dec_clean, _get_path, _set_path, inst_root, round_away, round_price
#   ROLL_WORDS: -
#   roll_tolerance: yahoo_decimals
#   _contract_bars: NoData, clean_rows, fetch_into, load_cache, utc_to_ts
#   _switch_bar: -
#   _roll_ratio: -
#   _newest_fetch: last_fetch, load_cache
#   _bz_still_old: ROLL_MATCH_BARS, _contract_bars, roll_tolerance
#   check_roll_forward: NoData, ROLL_CHECK_HOURS, ROLL_WORDS, _bz_still_old, _contract_bars, _keep_roll, _newest_fetch, _roll_ratio, _switch_bar, add_business_days, at_time, clean_rows, contract_from_name, expected_front, expected_rolls, load_cache, load_rolls_doc, ny_to_utc, parse_utc, roll_tolerance, save_rolls, to_ny, utc_iso, utc_to_ts
#   _keep_roll: ROLL_SAME_DAYS, parse_utc, save_rolls
#   RATE_PAIRS: -
#   TooSmall: -
#   _cents: -
#   _num_word: _dec_clean
#   _pct_word: _num_word
#   _pccy: -
#   _is_currency: RATE_PAIRS
#   _rate_from_price: RATE_PAIRS, _price_dec
#   rate_entry: RATE_PAIRS, _price_dec, _rate_from_price
#   rate_age_h: ny_to_utc, to_ny
#   fx_rates: RATE_PAIRS, instrument, latest_price, parse_utc, rate_age_h, rate_entry, utc_iso
#   usd_per_unit: RATE_PAIRS, _dec_clean, _is_currency, _num_word, _rate_from_price
#   _base_has_rate: _is_currency, _pccy
#   loss_usd_per_unit: _base_has_rate, _pccy, _price_dec, usd_per_unit
#   reward_usd_per_unit: _base_has_rate, _pccy, _price_dec, usd_per_unit
#   rate_symbols: RATE_PAIRS, _pccy
#   usd_per_point_per_lot: _dec_clean, _pccy, usd_per_unit
#   _swap_free: -
#   _margin_ccy_usd: RATE_PAIRS, _is_currency, _pccy, _price_dec, _rate_from_price, usd_per_unit
#   _commission: _dec_clean
#   _spread: _dec_clean, _pccy, usd_per_unit
#   _swap: _dec_clean, _is_currency, _margin_ccy_usd, _pccy, _price_dec, _swap_free, side_word, usd_per_unit
#   _fee: _dec_clean, _swap_free
#   swap_nights: FULL_DAY_NAMES, ny_to_utc, to_ny
#   _costs: _commission, _fee, _swap
#   costs_usd: _cents, _costs
#   trade_costs: _cents, _commission, _fee, _spread, _swap
#   _lots_dec: _dec_clean
#   _check_stop_side: _price_dec, side_word
#   max_loss_core: _check_stop_side, _costs, _dec_clean, loss_usd_per_unit
#   max_loss_usd: _cents, _lots_dec, max_loss_core
#   net_core: _check_stop_side, _costs, _dec_clean, _price_dec, max_loss_core, reward_usd_per_unit
#   reward_risk_net: net_core
#   _pl_core: _dec_clean, _fee, _pccy, _price_dec, _swap, side_word, usd_per_unit
#   position_pl_usd: _cents, _lots_dec, _pl_core, ask_of
#   _margin: _dec_clean, _lots_dec, _margin_ccy_usd, _price_dec, side_word
#   margin_usd: _cents, _margin
#   _floor_lots: _dec_clean
#   _lot_word: _dec_clean
#   _risk_words: _lot_word, money
#   _loss_per_lot: _dec_clean, _price_dec, loss_usd_per_unit
#   SIZE_LIMITS: -
#   _risk_money: _dec_clean, _pct_word, money
#   size_order: SIZE_LIMITS, TooSmall, _cents, _check_stop_side, _costs, _dec_clean, _floor_lots, _loss_per_lot, _lot_word, _margin, _num_word, _pct_word, _risk_money, _risk_words, money
#   MIN_DAY_MINUTES_LEFT: -
#   _setup: -
#   SETUPS: _setup
#   CONFIG_NOTE: -
#   SECRET_KEYS: -
#   MERGED_KEYS: -
#   D15_DAYS: -
#   KINDS: -
#   PRICE_SOURCES: -
#   CALC_MODES: -
#   SWAP_MODES: -
#   TRIPLE_DAYS: -
#   PROFIT_CURRENCIES: -
#   FUTURES_KEYS: -
#   FUTURES_SUFFIXES: -
#   FUTURES_MONTH_LETTERS: MONTH_CODES
#   ROLL_RULES: -
#   SWING_STOP_RULES: -
#   DAY_STOP_RULES: -
#   SESSION_WORDS: -
#   NEEDED_COLUMNS: -
#   COLUMN_DEFAULTS: -
#   BUILTIN_INSTRUMENTS: -
#   DEFAULTS: BUILTIN_INSTRUMENTS
#   _cfg_get: -
#   _cfg_put: -
#   _is_number: -
#   _num_text: -
#   _is_hhmm: -
#   _cfg_bad: -
#   _cfg_bool: _cfg_bad, _cfg_get
#   _cfg_number: _cfg_bad, _cfg_get, _is_number, _num_text
#   _cfg_int: _cfg_bad, _cfg_get, _cfg_put, _is_number, _num_text
#   _cfg_choice: _cfg_bad, _cfg_get
#   _cfg_hhmm: _cfg_get, _is_hhmm
#   _cfg_keys: _cfg_bad, _cfg_get
#   _rule_k: -
#   _is_date: -
#   _check_account: SERVER_TIME_BAD, _cfg_bool, _cfg_choice, _cfg_get, _cfg_number, server_offset
#   _check_top: DAY_ENDS, _cfg_bool, _cfg_choice, _cfg_keys
#   _check_risk: _cfg_bool, _cfg_int, _cfg_keys, _cfg_number
#   _cfg_target: _cfg_bad, _cfg_get, _rule_k
#   _cfg_stop: _cfg_bad, _cfg_get, _rule_k
#   _check_swing: SWING_STOP_RULES, _cfg_choice, _cfg_get, _cfg_hhmm, _cfg_int, _cfg_stop, _cfg_target
#   _check_day: DAY_STOP_RULES, _cfg_choice, _cfg_get, _cfg_hhmm, _cfg_int, _cfg_stop, _cfg_target, _check_day_times, _is_number
#   _pkt_hhmm: to_pkt
#   _check_day_times: D15_DAYS, SESSION_WORDS, _pkt_hhmm, at_time, fx_day_bounds, ny_to_utc, opening_range_window, parse_fx_date, pkt_time_on_fx_day
#   _check_news: _cfg_bad, _cfg_get, _cfg_int
#   _check_dates: _cfg_number, _is_date, _is_hhmm
#   _plain: _is_number
#   _row_bad: _plain
#   _row_number: _is_number, _num_text, _row_bad
#   _row_int: _is_number, _num_text, _row_bad
#   _row_choice: _row_bad
#   _inst_size: KINDS, PRICE_SOURCES, _dec_clean, _is_number, _plain, _row_choice, _row_int, _row_number
#   _inst_money: BROKER_TEMPLATES, CALC_MODES, DATA_TEMPLATES, SWAP_MODES, TRIPLE_DAYS, _is_number, _row_choice, _row_int, _row_number
#   _inst_futures: FUTURES_KEYS, FUTURES_MONTH_LETTERS, FUTURES_SUFFIXES, ROLL_RULES, _is_hhmm, _row_bad
#   _check_instrument: BUILTIN_INSTRUMENTS, NEEDED_COLUMNS, PROFIT_CURRENCIES, _inst_futures, _inst_money, _inst_size
#   check_config: MERGED_KEYS, _check_account, _check_dates, _check_day, _check_instrument, _check_news, _check_risk, _check_swing, _check_top
#   _find_secret: SECRET_KEYS
#   _fill_derived: _is_number
#   merge_config: COLUMN_DEFAULTS, DEFAULTS, MERGED_KEYS, _fill_derived
#   config_warnings: DEFAULTS, server_offset
#   load_config: CONFIG_NOTE, _find_secret, check_config, config_warnings, load_json, merge_config


# ---- copied from tests/fx_core.py: do not edit (tests/check_copies.py checks it)

def load_json(path, default=None):
    """Read a UTF-8 JSON file. A missing file gives default. An empty or broken file
    is an error (never repaired), so a broken ledger never looks like "no trades"."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("could not read %s: %s" % (path, exc.strerror or exc))
    if not raw:
        raise ValueError("%s is empty" % path)
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("%s is not valid JSON: %s" % (path, exc))
    except ValueError as exc:
        raise ValueError("%s is not valid JSON: %s" % (path, exc))


def save_json_atomic(path, doc):
    """Write doc as JSON (indent 2, a final newline) to path + ".tmp", then move it
    onto path in one step. Makes the folder first. No .tmp file is left on success."""
    tmp = path + ".tmp"
    try:
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ValueError("could not save %s: %s" % (path, exc.strerror or exc))


def fx_paths(work_dir, data_dir=None, config=None):
    """Every path of the fx/ folder as a dict. work_dir is the fx/ folder itself;
    data_dir defaults to work_dir/data and config to work_dir/config.json."""
    w = work_dir
    d = data_dir or os.path.join(w, "data")
    paths = {"config": config or os.path.join(w, "config.json")}
    for key in ("alerts", "watchlist", "entry", "plan", "timemachine", "orders",
                "prices", "portfolio"):
        paths[key] = os.path.join(w, key + ".json")
    for key in ("risk", "broker", "alerts", "news", "cot"):
        paths[key + "_state"] = os.path.join(w, "state", key + ".json")
    paths["data"] = d
    paths["yahoo"] = os.path.join(d, "yahoo")
    paths["rolls"] = os.path.join(d, "rolls.json")
    for key in ("calendar", "cot", "news"):
        paths[key] = os.path.join(d, key)
    return paths


def load_doc(path, what, skill, required=True):
    """Read one fx/ JSON file with the common checks (part 01 1.3): it must exist
    (when required; else None) and be a JSON object. what = "plan", skill = "fx-exit"."""
    doc = load_json(path)
    if doc is None:
        if required:
            raise ValueError("no %s at %s. Run %s first." % (what, path, skill))
        return None
    if not isinstance(doc, dict):
        raise ValueError("%s must be a JSON object: {...}" % path)
    return doc


def check_objects(doc, key, path):
    """The list under doc[key], which must be a list of JSON objects."""
    items = doc.get(key)
    if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
        raise ValueError('%s: "%s" must be a list of JSON objects: [{...}]' % (path, key))
    return items


def check_symbol(symbol, cfg, path):
    """The symbol, which must be a key of cfg["instruments"]."""
    if not isinstance(symbol, str) or symbol not in cfg.get("instruments", {}):
        raise ValueError('%s: "%s" is not in your instrument list (fx/config.json "instruments")'
                         % (path, symbol))
    return symbol


def check_side(side, path):
    """The side, which must be "buy" or "sell"."""
    if side not in ("buy", "sell"):
        raise ValueError('%s: side must be "buy" or "sell", not "%s"' % (path, side))
    return side


def check_style(style, path):
    """The style, which must be "swing" or "day"."""
    if style not in ("swing", "day"):
        raise ValueError('%s: style must be "swing" or "day", not "%s"' % (path, style))
    return style


def check_utc(doc, key, path):
    """doc[key] as a naive UTC datetime; it must be text like 2026-11-11T08:40:00Z."""
    text = doc.get(key)
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        raise ValueError('%s: "%s" is not a UTC time like 2026-11-11T08:40:00Z' % (path, key))


def _positive_number(x):
    """True when x is a finite int or float above 0 (never a bool or text)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and x > 0


def check_price(doc, key, path):
    """doc[key], which must be a price (a number above 0)."""
    x = doc.get(key)
    if not _positive_number(x):
        raise ValueError('%s: "%s" must be a price above 0, not %s' % (path, key, x))
    return x


def check_lots(lots, path):
    """The lots, which must be a number above 0."""
    if not _positive_number(lots):
        raise ValueError("%s: lots must be a number above 0, not %s" % (path, lots))
    return lots


JSON_VERSION = 1


def _words_dec(x, what):
    """x as a Decimal for the words helpers; ValueError "Not <what>: X" for None,
    text, a bool, NaN or infinity."""
    if isinstance(x, bool) or not isinstance(x, (int, float, Decimal)):
        raise ValueError("Not %s: %s" % (what, x))
    d = Decimal(str(x))
    if not d.is_finite():
        raise ValueError("Not %s: %s" % (what, x))
    return d


def money(usd):
    """US dollars for people: "$1,234.56", "-$12.30". Always 2 decimals, half up;
    -0.004 gives "$0.00" (never "-$0.00")."""
    v = _words_dec(usd, "an amount of money").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "%s$%s" % ("-" if v < 0 else "", format(abs(v), ",f"))


def signed_money(usd):
    """As money, with "+" above 0 after rounding: "+$12.30", "-$12.30", "$0.00"."""
    v = _words_dec(usd, "an amount of money").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("+" if v > 0 else "") + money(v)


def price_text(price, inst):
    """A price with all the instrument's digits kept and thousands commas:
    "2,433.40" for gold, "1.08450" for EURUSD, "-3.50" for oil below zero."""
    d = _words_dec(price, "a price")
    q = d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP)
    return "%s%s" % ("-" if q < 0 else "", format(abs(q), ",f"))


def lots_text(lots):
    """Lots for people: at least 2 decimals, more only when needed, then the word:
    "1.00 lot", "0.86 lots", "0.005 lots"."""
    d = _words_dec(lots, "a number of lots")
    if d < 0:
        raise ValueError("Lots cannot be below 0.")
    d = d.normalize()
    if d.as_tuple().exponent > -2:
        d = d.quantize(Decimal("0.01"))
    return "%s %s" % (format(d, "f"), "lot" if d == 1 else "lots")


def side_word(side):
    """"buy" -> "BUY", "sell" -> "SELL" (the words on the MetaTrader buttons)."""
    if side == "buy":
        return "BUY"
    if side == "sell":
        return "SELL"
    raise ValueError('Side must be "buy" or "sell", not %s.' % (side,))


def age_text(minutes):
    """How old a price is: "live" under 1 minute, "11 min old", "2 h 5 min old",
    "3 h old", "2 days old". None gives "age unknown"; below 0 counts as 0."""
    if minutes is None:
        return "age unknown"
    m = float(_words_dec(minutes, "a number of minutes"))
    m = max(m, 0.0)
    if m < 1:
        return "live"
    if m < 60:
        return "%d min old" % int(m)
    if m < 1440:
        h, mins = divmod(int(m), 60)
        return "%d h old" % h if mins == 0 else "%d h %d min old" % (h, mins)
    days = int(m // 1440)
    return "1 day old" if days == 1 else "%d days old" % days


def scale_note(inst):
    """Whose price it is: "Yahoo price, close to your broker's price" for spot FX,
    "Yahoo price (gold futures), not your broker's price" for a futures row."""
    source = inst.get("price_source")
    if source == "spot_fx":
        return "Yahoo price, close to your broker's price"
    if source == "futures":
        name = str(inst.get("name") or inst.get("key") or "")
        if name and not (len(name) > 1 and name[1].isupper()):
            name = name[0].lower() + name[1:]
        return "Yahoo price (%s futures), not your broker's price" % name
    raise ValueError('price_source must be "spot_fx" or "futures", not %s.' % (source,))


def both_times(utc):
    """Karachi and New York time in one line: "Thu 12:40 PM PKT (3:40 AM New York)"."""
    return "%s (%s)" % (pkt_label(utc), ny_label(utc))


def add_common(parser, write=False):
    """Add the common flags (part 06 2.1) to one argparse parser; write=True also adds
    --write. Path defaults are None: fx_paths then uses fx/config.json and fx/data."""
    parser.add_argument("--config", metavar="FILE", default=None,
                        help="your config (default fx/config.json)")
    parser.add_argument("--data", metavar="DIR", default=None,
                        help="the data folder (default fx/data)")
    parser.add_argument("--offline", action="store_true",
                        help="never use the network; read only the data folder")
    parser.add_argument("--now", metavar='"YYYY-MM-DD HH:MM"', default=None,
                        help="pretend it is this Karachi time (or ...Z for UTC); also means --offline")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("--quiet", action="store_true", help="print only the result lines")
    if write:
        parser.add_argument("--write", action="store_true", help="also write the output file")
    return parser


def fail(err, tool):
    """Print "<tool>: <message>" on stderr as one line and return 2 (an error)."""
    text = " ".join(str(err).split())
    print("%s: %s" % (tool, text), file=sys.stderr)
    return 2


def emit_json(doc):
    """Print doc as one JSON object (indent 2, sorted keys)."""
    print(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False))


def _dec_clean(x):
    """x as a Decimal with float noise under a billionth cleaned (half up), so
    1.1234999999999 counts as 1.1235. ValueError "not a number: x" when not finite."""
    if isinstance(x, bool) or x is None:
        raise ValueError("not a number: %r" % (x,))
    try:
        d = Decimal(str(x))
        if not d.is_finite():
            raise ValueError("not a number: %r" % (x,))
        return d.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError("not a number: %r" % (x,))


def _price_dec(x):
    """A price as a cleaned Decimal; ValueError when it is not above 0."""
    d = _dec_clean(x)
    if d <= 0:
        raise ValueError("A price must be above 0.")
    return d


def instrument(cfg, key):
    """The instrument row cfg["instruments"][key] as a NEW dict, with the defaults
    filled (key, broker_symbol, point, tick_size, pip ...) and its lot and tick checks."""
    rows = cfg.get("instruments") or {}
    if key not in rows:
        near = difflib.get_close_matches(str(key), list(rows), n=1, cutoff=0.6)
        such = near[0] if near else ("XAUUSD" if "XAUUSD" in rows or not rows else sorted(rows)[0])
        raise ValueError('Unknown instrument "%s". Use a key from your config, such as %s. '
                         'Put your broker\'s name in "broker_symbol".' % (key, such))
    inst = copy.deepcopy(rows[key])
    inst["key"] = key
    if not inst.get("broker_symbol"):
        inst["broker_symbol"] = key
    digits = int(inst["digits"])
    point = Decimal(1).scaleb(-digits)
    if inst.get("point") is not None and _dec_clean(inst["point"]) != point:
        raise ValueError("%s: point %s does not match digits %s. Copy both from MetaTrader."
                         % (key, inst["point"], digits))
    inst["point"] = float(point)
    if inst.get("tick_size") is None:
        inst["tick_size"] = float(point)
    if inst.get("pip") is None:
        if inst.get("kind") == "fx":
            inst["pip"] = 0.01 if inst.get("quote") == "JPY" else 0.0001
        else:
            inst["pip"] = None
    for name in ("leverage", "initial_margin", "swap_free_fee_per_lot", "futures"):
        inst.setdefault(name, None)
    for name in ("margin_rate", "yahoo_to_broker"):
        if inst.get(name) is None:
            inst[name] = 1.0
    for name in ("swap_free_grace_nights", "commission_per_lot_side", "stops_level_points"):
        if inst.get(name) is None:
            inst[name] = 0
    sizes = [_dec_clean(inst[n]) for n in ("contract_size", "lot_step", "min_lot", "max_lot")]
    if min(sizes) <= 0 or sizes[2] > sizes[3]:
        raise ValueError("%s: contract_size, min_lot, max_lot and lot_step must be above 0." % key)
    if sizes[2] % sizes[1] != 0:
        raise ValueError("%s: min_lot %s is not a whole number of lot_step %s."
                         % (key, inst["min_lot"], inst["lot_step"]))
    tick = _dec_clean(inst["tick_size"])
    if tick <= 0 or tick % point != 0:
        raise ValueError("%s: tick_size %s does not fit digits %s." % (key, inst["tick_size"], digits))
    return inst


def _to_tick(d, inst, rounding):
    """A cleaned Decimal price to a whole number of ticks (the given rounding), then
    quantized to the instrument's digits, as a float."""
    tick = _dec_clean(inst["tick_size"])
    q = (d / tick).to_integral_value(rounding=rounding) * tick
    return float(q.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP))


def round_price(price, inst):
    """A price to the nearest tick, half up (entries, fills, plain levels):
    EURUSD 1.124905 -> 1.12491, XAUUSD 4134.175 -> 4134.18."""
    return _to_tick(_price_dec(price), inst, ROUND_HALF_UP)


def round_away(price, entry, inst):
    """A stop or a target to the tick AWAY from the entry, so the risk never shrinks
    and the target never comes closer: below the entry down, above it up."""
    p = _price_dec(price)
    e = _price_dec(entry)
    if p == e:
        raise ValueError("A stop or a target cannot be at the entry price.")
    return _to_tick(p, inst, ROUND_FLOOR if p < e else ROUND_CEILING)


def to_points(distance, inst):
    """A price distance in whole points, half up, the sign kept:
    to_points(-0.00141, EURUSD) = -141."""
    d = _dec_clean(distance) / _dec_clean(inst["point"])
    return int(d.to_integral_value(rounding=ROUND_HALF_UP))


def pips_text(distance, inst):
    """A distance in plain words, sign dropped: "14.1 pips (141 points)" for FX,
    "174.51 (17,451 points)" for gold and the other non-FX rows (no pip word)."""
    d = abs(_dec_clean(distance))
    pts = abs(to_points(d, inst))
    pts_word = "%s %s" % (format(pts, ","), "point" if pts == 1 else "points")
    if inst.get("pip"):
        pips = (d / _dec_clean(inst["pip"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return "%s %s (%s)" % (format(pips, ",f"), "pip" if pips == 1 else "pips", pts_word)
    q = d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP)
    return "%s (%s)" % (format(q, ",f"), pts_word)


def spread_price(inst):
    """The table spread as a price distance: spread_points x point, exact (not rounded
    to the tick), on the plan scale. EURUSD 1 point = 0.00001, XAUUSD 9 points = 0.09."""
    return float(_dec_clean(inst["spread_points"]) * _dec_clean(inst["point"]))


def ask_of(bid, inst):
    """The ask from a Yahoo bid (D5): bid + the table spread, to the nearest tick."""
    return round_price(_price_dec(bid) + Decimal(str(spread_price(inst))), inst)


KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving


DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def utc_now():
    """The real clock as naive UTC, without microseconds. The only place that reads it."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def to_pkt(utc):
    """UTC -> naive Karachi time (UTC+5). An aware datetime is turned to UTC first."""
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc + datetime.timedelta(hours=KARACHI_HOURS)


def pkt_to_utc(pkt):
    """Naive Karachi time -> naive UTC. An aware datetime keeps its own offset."""
    if pkt.tzinfo is not None:
        return (pkt - pkt.utcoffset()).replace(tzinfo=None)
    return pkt - datetime.timedelta(hours=KARACHI_HOURS)


def parse_now(text):
    """--now text -> naive UTC. Empty gives the real clock. No offset means Karachi time
    (a " PKT" at the end is fine); "...Z" is UTC; an offset like "+05:00" is honoured."""
    if not text:
        return utc_now()
    bad = '--now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)'   # spec 06 2.1 (check)
    t = str(text).strip()
    if t.upper().endswith("PKT"):
        t = t[:-3].strip()
    if t[-1:] in ("Z", "z"):
        t = t[:-1] + "+00:00"
    if not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", t):
        raise ValueError(bad)
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ValueError(bad)
    return pkt_to_utc(dt)


def utc_iso(utc):
    """naive UTC -> "2026-11-11T06:30:00Z" (how files store times). None stays None."""
    if utc is None:
        return None
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text):
    """ISO time -> naive UTC. No offset means UTC; "Z" and offsets are honoured.
    Empty or bad text gives None (never raises)."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    if t[-1:] in ("Z", "z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def ts_to_utc(seconds):
    """Unix seconds -> naive UTC. Never uses the machine's time zone."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(seconds))


def utc_to_ts(utc):
    """naive UTC -> Unix seconds (int)."""
    return int((utc - datetime.datetime(1970, 1, 1)).total_seconds())


def clock_label(t):
    """datetime or time -> "3:05 PM" ("12:00 AM" is midnight)."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    """date or datetime -> "Wed 2026-11-11" (English day names always)."""
    if isinstance(d, datetime.datetime):
        d = d.date()
    return "%s %s" % (DAY_NAMES[d.weekday()], d.isoformat())


def short_date(d):
    """date or datetime -> "Wed 11 Nov" (no leading zero on the day; English names)."""
    return "%s %d %s" % (DAY_NAMES[d.weekday()], d.day, MONTH_NAMES[d.month - 1])


def pkt_label(utc, date=False):
    """naive UTC -> "Wed 3:05 PM PKT" (the Karachi day); date=True: "Wed 11 Nov 3:05 PM PKT"."""
    p = to_pkt(utc)
    day = short_date(p) if date else DAY_NAMES[p.weekday()]
    return "%s %s PKT" % (day, clock_label(p))


def ny_label(utc, date=False):
    """naive UTC -> "5:05 AM New York"; date=True: "Wed 11 Nov 5:05 AM New York"."""
    n = to_ny(utc)
    if date:
        return "%s %s New York" % (short_date(n), clock_label(n))
    return "%s New York" % clock_label(n)


def parse_hhmm(text, flag):
    """"23:30" or "9:05" -> datetime.time. Bad text: ValueError that names the flag or key."""
    if isinstance(text, datetime.time):
        return text
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("%s wants a time like 23:30" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def nth_weekday(year, month, weekday, n):
    """The n-th weekday (0 = Monday ... 6 = Sunday, n from 1) of a month, as a date."""
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(weekday - d.weekday()) % 7)
    d += datetime.timedelta(weeks=n - 1)
    if d.month != month:
        raise ValueError("there is no weekday %d number %d in %d-%02d" % (weekday, n, year, month))
    return d


def last_weekday(year, month, weekday):
    """The last weekday (0 = Monday ... 6 = Sunday) of a month, as a date."""
    nxt = datetime.date(year + month // 12, month % 12 + 1, 1)
    d = nxt - datetime.timedelta(days=1)
    return d - datetime.timedelta(days=(d.weekday() - weekday) % 7)


def us_dst(utc):
    """True when New York is on summer time (EDT): 2nd Sunday of March 07:00 UTC to
    1st Sunday of November 06:00 UTC."""
    y = utc.year
    start = datetime.datetime.combine(nth_weekday(y, 3, 6, 2), datetime.time(7))
    end = datetime.datetime.combine(nth_weekday(y, 11, 6, 1), datetime.time(6))
    return start <= utc < end


def uk_dst(utc):
    """True when London is on summer time (BST): last Sunday of March 01:00 UTC to
    last Sunday of October 01:00 UTC."""
    y = utc.year
    start = datetime.datetime.combine(last_weekday(y, 3, 6), datetime.time(1))
    end = datetime.datetime.combine(last_weekday(y, 10, 6), datetime.time(1))
    return start <= utc < end


def sydney_dst(utc):
    """True when Sydney is on summer time (AEDT). It ends on the 1st Sunday of April and
    starts on the 1st Sunday of October, both at Saturday 16:00 UTC."""
    y = utc.year
    eight = datetime.timedelta(hours=8)
    april_end = datetime.datetime.combine(nth_weekday(y, 4, 6, 1), datetime.time(0)) - eight
    october_start = datetime.datetime.combine(nth_weekday(y, 10, 6, 1), datetime.time(0)) - eight
    return not (april_end <= utc < october_start)


def to_ny(utc):
    """naive UTC -> New York wall clock."""
    return utc - datetime.timedelta(hours=4 if us_dst(utc) else 5)


def ny_to_utc(local):
    """New York wall clock -> naive UTC. A doubled hour gives the first (summer) one;
    a missing hour is read with the winter offset."""
    a = local + datetime.timedelta(hours=4)
    return a if us_dst(a) else local + datetime.timedelta(hours=5)


def london_to_utc(local):
    """London wall clock -> naive UTC (a doubled hour gives the summer one)."""
    a = local - datetime.timedelta(hours=1)
    return a if uk_dst(a) else local


def sydney_to_utc(local):
    """Sydney wall clock -> naive UTC (a doubled hour gives the summer one)."""
    a = local - datetime.timedelta(hours=11)
    return a if sydney_dst(a) else local - datetime.timedelta(hours=10)


def tokyo_to_utc(local):
    """Tokyo wall clock -> naive UTC."""
    return local - datetime.timedelta(hours=9)


def city_to_utc(city, local):
    """A wall clock time in "ny", "london", "sydney" or "tokyo" -> naive UTC."""
    if city == "ny":
        return ny_to_utc(local)
    if city == "london":
        return london_to_utc(local)
    if city == "sydney":
        return sydney_to_utc(local)
    if city == "tokyo":
        return tokyo_to_utc(local)
    raise ValueError('A city is "ny", "london", "sydney" or "tokyo", not "%s"' % city)


SERVER_TIME_BAD = ('"account.server_time" must be "ny+7" (GMT+2 in winter, GMT+3 in summer) '
                   'or like "utc+0"')


def server_offset(server_cfg):
    """"ny+7" -> None; "utc+3" -> 3; "utc-2" -> -2. Anything else: ValueError."""
    m = re.match(r"^(ny\+7|utc([+-])(\d{1,2}))$", str(server_cfg or "").strip().lower())
    if not m:
        raise ValueError(SERVER_TIME_BAD)
    if m.group(1) == "ny+7":
        return None
    n = int(m.group(3))
    if n > 14:
        raise ValueError(SERVER_TIME_BAD)
    return n if m.group(2) == "+" else -n


DAY_ENDS = ("17:00 New York", "00:00 UTC")


FULL_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


OR_WINDOWS = {"london": ("london", "08:00"), "new_york": ("ny", "08:00"), "tokyo": ("tokyo", "09:00")}


def check_day_end(day_end):
    """Return day_end when it is "17:00 New York" or "00:00 UTC"; else ValueError."""
    if day_end not in DAY_ENDS:
        raise ValueError('"day_end" must be "17:00 New York" or "00:00 UTC"')
    return day_end


def parse_fx_date(fx_date, weekend_ok=False):
    """"2026-11-11" or a date -> datetime.date. A Saturday or Sunday is refused unless
    weekend_ok. Bad text: ValueError."""
    if isinstance(fx_date, datetime.datetime):
        d = fx_date.date()
    elif isinstance(fx_date, datetime.date):
        d = fx_date
    else:
        text = str(fx_date)
        try:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                raise ValueError(text)
            d = datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))
        except ValueError:
            raise ValueError('An FX day wants a date like "2026-11-11", not "%s"' % text)
    if not weekend_ok and d.weekday() >= 5:
        raise ValueError("%s is a %s. FX days are Monday to Friday." % (d.isoformat(), FULL_DAY_NAMES[d.weekday()]))
    return d


def at_time(d, hhmm):
    """A date and "HH:MM" (or a time) -> a naive datetime on that date."""
    return datetime.datetime.combine(d, parse_hhmm(hhmm, '"%s"' % hhmm))


def fx_day(utc, day_end):
    """The FX day (the New York date it ends on, or the UTC date) that holds this moment,
    as "YYYY-MM-DD"; None on a weekend."""
    check_day_end(day_end)
    n = to_ny(utc)
    if day_end == "17:00 New York":
        d = n.date() + datetime.timedelta(days=1 if n.hour >= 17 else 0)
        return None if d.weekday() >= 5 else d.isoformat()
    d = utc.date()
    wd = d.weekday()
    if wd == 5:
        return None
    if wd == 6:
        return (d + datetime.timedelta(days=1)).isoformat() if n.hour >= 17 and n.date() == d else None
    if wd == 4 and n.date() == d and n.hour >= 17:
        return None
    return d.isoformat()


def fx_day_bounds(fx_date, day_end):
    """(start_utc, end_utc) of an FX day. "00:00 UTC" days are clipped to the FX week
    (Monday starts Sunday 17:00 New York, Friday ends 17:00 New York)."""
    check_day_end(day_end)
    d = parse_fx_date(fx_date)
    day = datetime.timedelta(days=1)
    five = datetime.time(17)
    if day_end == "17:00 New York":
        return (ny_to_utc(datetime.datetime.combine(d - day, five)),
                ny_to_utc(datetime.datetime.combine(d, five)))
    start = datetime.datetime.combine(d, datetime.time(0))
    end = start + day
    if d.weekday() == 0:
        start = ny_to_utc(datetime.datetime.combine(d - day, five))
    if d.weekday() == 4:
        end = ny_to_utc(datetime.datetime.combine(d, five))
    return start, end


def last_fx_day(now, cfg):
    """The FX day of now; on a weekend, the Friday before."""
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = fx_day(now, day_end)
    if d is not None:
        return d
    n = to_ny(now).date()
    back = (n.weekday() - 4) % 7
    if back == 0 and n.weekday() != 4:
        back = 7
    return (n - datetime.timedelta(days=back)).isoformat()


def pkt_time_on_fx_day(hhmm, fx_date, day_end):
    """The first moment at or after the FX day's start whose Karachi clock shows hhmm,
    as naive UTC (D15). It never raises past the day's end: the caller compares."""
    t = parse_hhmm(hhmm, '"%s"' % (hhmm,))
    start = fx_day_bounds(fx_date, day_end)[0]
    p = to_pkt(start)
    cand = datetime.datetime.combine(p.date(), t)
    if cand < p:
        cand += datetime.timedelta(days=1)
    return pkt_to_utc(cand)


def opening_range_window(session, fx_date, minutes):
    """(start_utc, end_utc) of the opening range: the session's local start on the local
    date fx_date, plus minutes. Bars that START inside it make the range."""
    if session not in OR_WINDOWS:
        raise ValueError('"day.or_session" must be "london", "new_york" or "tokyo"')
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes % 5 or not 5 <= minutes <= 120:
        raise ValueError('"day.or_minutes" must be a multiple of 5 (the bars are 5 minutes)')
    d = parse_fx_date(fx_date)
    city, hhmm = OR_WINDOWS[session]
    start = city_to_utc(city, at_time(d, hhmm))
    return start, start + datetime.timedelta(minutes=minutes)


HOLIDAYS = {
    # past rows (for replays over the 1h cache)
    "2024-11-28": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2024-11-29": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2024-12-24": {"name": "Christmas Eve", "thin": ["fx"]},
    "2024-12-25": {"name": "Christmas", "closed": ["fx", "cme", "ice"]},
    "2025-01-01": {"name": "New Year's Day", "closed": ["fx", "cme", "ice"]},
    "2025-01-20": {"name": "Martin Luther King Jr. Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-02-17": {"name": "Presidents' Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-04-18": {"name": "Good Friday", "closed": ["cme", "ice"], "thin": ["fx"]},
    "2025-05-26": {"name": "Memorial Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-06-19": {"name": "Juneteenth (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-07-04": {"name": "Independence Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-09-01": {"name": "Labor Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-11-27": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2025-11-28": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2025-12-24": {"name": "Christmas Eve", "thin": ["fx"]},
    "2025-12-25": {"name": "Christmas", "closed": ["fx", "cme", "ice"]},
    "2026-01-01": {"name": "New Year's Day", "closed": ["fx", "cme", "ice"]},
    "2026-01-19": {"name": "Martin Luther King Jr. Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-02-16": {"name": "Presidents' Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-04-03": {"name": "Good Friday", "closed": ["cme", "ice"], "thin": ["fx"]},
    "2026-05-25": {"name": "Memorial Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-06-19": {"name": "Juneteenth (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-07-03": {"name": "Independence Day (US, observed)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-09-07": {"name": "Labor Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    # from 2026-11-11 on
    "2026-11-11": {"name": "Veterans Day (US)"},
    "2026-11-26": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-11-27": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2026-12-24": {"name": "Christmas Eve", "thin": ["fx"]},
    "2026-12-25": {"name": "Christmas", "closed": ["fx", "cme", "ice"], "provisional": ["fx"]},
    "2026-12-28": {"name": "Boxing Day (UK)", "thin": ["fx"]},
    "2026-12-31": {"name": "New Year's Eve", "thin": ["fx"]},
    "2027-01-01": {"name": "New Year's Day", "closed": ["fx", "cme", "ice"], "provisional": ["fx"]},
    "2027-01-18": {"name": "Martin Luther King Jr. Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-02-15": {"name": "Presidents' Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-03-26": {"name": "Good Friday", "closed": ["cme", "ice"], "thin": ["fx"], "provisional": ["cme", "fx"]},
    "2027-03-29": {"name": "Easter Monday (UK)", "thin": ["fx"]},
    "2027-05-03": {"name": "Early May bank holiday (UK)", "thin": ["fx"]},
    "2027-05-31": {"name": "Memorial Day (US) and Spring bank holiday (UK)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-06-18": {"name": "Juneteenth (US, observed)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-07-05": {"name": "Independence Day (US, observed)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-08-30": {"name": "Summer bank holiday (UK)", "thin": ["fx"]},
    "2027-09-06": {"name": "Labor Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-10-11": {"name": "Columbus Day (US)"},
    "2027-11-11": {"name": "Veterans Day (US)"},
    "2027-11-25": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-11-26": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2027-12-24": {"name": "Christmas (US, observed)", "closed": ["cme"], "thin": ["fx"], "provisional": ["cme"]},
    "2027-12-27": {"name": "Christmas (UK, in lieu)", "thin": ["fx"]},
    "2027-12-28": {"name": "Boxing Day (UK, in lieu)", "thin": ["fx"]},
    "2027-12-31": {"name": "New Year (US, in lieu)", "thin": ["fx"], "provisional": ["cme"]},
}


EARLY_CLOSES = {
    # past rows (broker hours for replays; cme only, all provisional (check))
    "2024-11-28": {"name": "Thanksgiving (US)", "cme": "14:30", "provisional": ["cme"]},
    "2024-11-29": {"name": "Day after Thanksgiving (half day)", "cme": "14:45", "provisional": ["cme"]},
    "2024-12-24": {"name": "Christmas Eve (half day)", "cme": "13:45", "provisional": ["cme"]},
    "2025-01-20": {"name": "Martin Luther King Jr. Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-02-17": {"name": "Presidents' Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-05-26": {"name": "Memorial Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-06-19": {"name": "Juneteenth", "cme": "13:00", "provisional": ["cme"]},
    "2025-07-03": {"name": "Day before Independence Day (half day)", "cme": "13:00", "provisional": ["cme"]},
    "2025-07-04": {"name": "Independence Day", "cme": "13:00", "provisional": ["cme"]},
    "2025-09-01": {"name": "Labor Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-11-27": {"name": "Thanksgiving (US)", "cme": "14:30", "provisional": ["cme"]},
    "2025-11-28": {"name": "Day after Thanksgiving (half day)", "cme": "14:45", "provisional": ["cme"]},
    "2025-12-24": {"name": "Christmas Eve (half day)", "cme": "13:45", "provisional": ["cme"]},
    "2026-01-19": {"name": "Martin Luther King Jr. Day", "cme": "14:30", "provisional": ["cme"]},
    "2026-02-16": {"name": "Presidents' Day", "cme": "14:30", "provisional": ["cme"]},
    "2026-05-25": {"name": "Memorial Day", "cme": "14:30", "provisional": ["cme"]},
    "2026-06-19": {"name": "Juneteenth", "cme": "13:00", "provisional": ["cme"]},
    "2026-07-03": {"name": "Independence Day (observed)", "cme": "13:00", "provisional": ["cme"]},
    "2026-09-07": {"name": "Labor Day", "cme": "14:30", "provisional": ["cme"]},
    # from 2026-11-11 on
    "2026-11-26": {"name": "Thanksgiving (US)", "cme": "14:30", "ice": "13:30"},
    "2026-11-27": {"name": "Day after Thanksgiving", "cme": "14:45", "ice": "15:00"},
    "2026-12-24": {"name": "Christmas Eve", "cme": "13:45", "ice": "14:00", "provisional": ["cme"]},
    "2026-12-31": {"name": "New Year's Eve", "ice": "15:00"},
    "2027-01-18": {"name": "Martin Luther King Jr. Day", "cme": "14:30", "provisional": ["cme"]},
    "2027-02-15": {"name": "Presidents' Day", "cme": "14:30", "provisional": ["cme"]},
    "2027-05-31": {"name": "Memorial Day", "cme": "14:30", "ice": "13:30", "provisional": ["cme"]},
    "2027-06-18": {"name": "Juneteenth (observed)", "cme": "13:00", "ice": "13:30", "provisional": ["cme"]},
    "2027-07-05": {"name": "Independence Day (observed)", "cme": "13:00", "ice": "13:30", "provisional": ["cme"]},
    "2027-09-06": {"name": "Labor Day", "cme": "14:30", "ice": "13:30", "provisional": ["cme"]},
    "2027-11-25": {"name": "Thanksgiving", "cme": "14:30", "ice": "14:30", "provisional": ["cme"]},
    "2027-11-26": {"name": "Day after Thanksgiving", "cme": "14:45", "ice": "15:00", "provisional": ["cme"]},
    "2027-12-24": {"name": "Christmas (observed)", "ice": "08:00"},
    "2027-12-31": {"name": "New Year (in lieu)", "ice": "15:00"},
}


HOURS = {
    # broker trading hours (instrument column "hours")
    "fx":        {"group": "fx",  "open": ("ny", "17:05", -1),     "monday_open": ("ny", "17:05", -1)},
    "comex":     {"group": "cme", "open": ("ny", "18:00", -1),     "monday_open": ("ny", "18:00", -1)},
    "nymex":     {"group": "cme", "open": ("ny", "18:00", -1),     "monday_open": ("ny", "18:00", -1)},
    "ice_brent": {"group": "ice", "open": ("london", "01:00", 0),  "monday_open": ("london", "23:00", -1)},
    # Yahoo data hours (instrument column "data_hours")
    "yahoo_fx":  {"group": "fx",  "open": ("ny", "17:00", -1),     "monday_open": ("london", "00:00", 0)},
    "cme":       {"group": "cme", "open": ("ny", "18:00", -1),     "monday_open": ("ny", "18:00", -1)},
}


BROKER_TEMPLATES = ("fx", "comex", "nymex", "ice_brent")


DATA_TEMPLATES = ("yahoo_fx", "cme")


def hours_template(name):
    """The HOURS row of a template name; an unknown name: ValueError."""
    if name not in HOURS:
        raise ValueError('Unknown hours template "%s". Use one of: fx, comex, nymex, ice_brent, yahoo_fx, cme.' % name)
    return HOURS[name]


def group_closed(fx_date, group, cfg):
    """True when the whole FX day is shut for a holiday group (config "holidays" shut
    every group; HOLIDAYS shuts the groups in its "closed" list)."""
    iso = parse_fx_date(fx_date, weekend_ok=True).isoformat()
    if iso in ((cfg or {}).get("holidays") or {}):
        return True
    return group in HOLIDAYS.get(iso, {}).get("closed", [])


def early_close_utc(fx_date, group, cfg):
    """The early close of a group on that date as naive UTC (config "early_closes" for all
    groups, EARLY_CLOSES per group; the earlier wins), or None."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    times = []
    mine = ((cfg or {}).get("early_closes") or {}).get(d.isoformat())
    if mine:
        times.append(mine)
    table = EARLY_CLOSES.get(d.isoformat(), {}).get(group)
    if table:
        times.append(table)
    if not times:
        return None
    return min(ny_to_utc(at_time(d, t)) for t in times)


def yahoo_no_data_cuts(fx_date):
    """The (start_utc, end_utc) cuts where Yahoo has no futures bars, for an FX day: each
    New York date of fx_date - 1 and fx_date that is a US holiday for "cme" (closed or
    no_data); a Monday also loses the Sunday evening from 18:00."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    day = datetime.timedelta(days=1)
    cuts = []
    for c in (d - day, d):
        row = HOLIDAYS.get(c.isoformat(), {})
        if "cme" in row.get("closed", []) or "cme" in row.get("no_data", []):
            start = ny_to_utc(at_time(c, "00:00"))
            if c.weekday() == 0:
                start = ny_to_utc(at_time(c - day, "18:00"))
            cuts.append((start, ny_to_utc(at_time(c + day, "00:00"))))
    return cuts


def open_spans(template, fx_date, cfg, tables=True):
    """The open time of a template on one FX day: [] or [(open_utc, close_utc)].
    tables=False skips holidays, early closes and the Yahoo no-data cut."""
    row = hours_template(template)
    d = parse_fx_date(fx_date)
    group = row["group"]
    if tables and group_closed(d, group, cfg):
        return []
    city, hhmm, shift = row["monday_open"] if d.weekday() == 0 else row["open"]
    start = city_to_utc(city, at_time(d + datetime.timedelta(days=shift), hhmm))
    close = ny_to_utc(at_time(d, "17:00"))
    if tables:
        early = early_close_utc(d, group, cfg)
        if early is not None:
            close = min(close, early)
        if template in DATA_TEMPLATES and group == "cme":
            for a, b in yahoo_no_data_cuts(d):
                if a <= start:
                    start = max(start, b)
                elif a < close:
                    close = a
    return [(start, close)] if start < close else []


UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"


YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")


PACE_SECONDS = 2.0     # at least this long between any two Yahoo requests (both hosts)


TIMEOUT = 25           # socket timeout in seconds


LOOKBACK = {"5m": 60, "1h": 730, "1d": 3650}           # days Yahoo keeps (1d: we cap at 10 years)


FIRST_RANGE = {"5m": "60d", "1h": "730d", "1d": "2y"}  # the range of a first fill


MIN_REFRESH_S = {"5m": 60, "1h": 600, "1d": 21600}     # skip the network if the newest fetch is younger


STEP_S = {"5m": 300, "1h": 3600, "1d": 86400}          # bar length in seconds


SLEEP = time.sleep     # tests replace it


RETRY_WAIT_S = {"broken": 2, "busy": 5, "down": 3}     # wait before the one retry on the other host


_last_request = None   # time.monotonic() of the last Yahoo request of this run


_last_host = "query1"  # the host of the last answer (for the fetch record)


class NoData(ValueError):
    """Yahoo has no data for that symbol (a 404, or an answer with no result)."""


def chart_url(host, symbol, interval, range_=None, period1=None, period2=None):
    """The v8 chart URL for one symbol: a range, or a period1 and period2 (Unix seconds)."""
    if interval not in ("5m", "1h", "1d"):
        raise ValueError("Interval %s is not used by this pack" % interval)
    by_range = range_ is not None
    by_period = period1 is not None and period2 is not None
    if by_range == by_period:
        raise ValueError("chart_url needs a range or a period1 and period2")
    q = "interval=%s&" % interval
    if by_range:
        q += "range=%s" % range_
    else:
        q += "period1=%d&period2=%d" % (int(period1), int(period2))
    return host + "/v8/finance/chart/" + urllib.parse.quote(symbol, safe="") + "?" + q + "&includePrePost=false"


def _url_symbol(path):
    """The symbol a Yahoo URL asks for (the chart path, or the spark symbols list)."""
    parts = urllib.parse.urlsplit(path)
    if "/v8/finance/chart/" in parts.path:
        return urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    q = urllib.parse.parse_qs(parts.query)
    return ",".join(q.get("symbols", [""]))


def _url_interval(path):
    """The interval a Yahoo URL asks for, or "" when it has none."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    return q.get("interval", [""])[0]


def is_contract_symbol(symbol):
    """True for a single futures contract like GCZ26.CMX or CLF27.NYM (not GC=F)."""
    return re.match(r"^[A-Z]{1,3}[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM)$", str(symbol)) is not None


def _yahoo_error(status, symbol, interval, description=""):
    """The plain error for a Yahoo HTTP status (part 02 1.3), or None for 200."""
    if status == 404:
        if is_contract_symbol(symbol):
            return NoData("Yahoo has no data for %s. Old contracts vanish from Yahoo." % symbol)
        return NoData("Yahoo has no data for %s. Check the yahoo symbol in your config." % symbol)
    if status == 422:
        return ValueError("Yahoo keeps 5-minute bars for 60 days and 1-hour bars for 730 days. "
                          "This asked for older data (%s, %s)." % (symbol, interval))
    if status == 401:
        return ValueError("Yahoo refused this kind of request.")
    if status == 429:
        return ValueError("Yahoo is busy (too many requests). Wait a few minutes, then try again. Or use --offline.")
    if status == "broken":
        return ValueError("Yahoo sent a broken answer for %s. Try again in a minute." % symbol)
    if status is None or 500 <= status <= 599:
        return ValueError("Yahoo did not answer for %s. Try again later, or use --offline." % symbol)
    if status == 200:
        return None
    return ValueError("Yahoo refused the request for %s: %s" % (symbol, description or "HTTP %s" % status))


def _error_description(body):
    """chart.error.description (or spark.error.description) from an error body, or ""."""
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
        for key in ("chart", "spark", "finance"):
            err = (doc.get(key) or {}).get("error") or {}
            if err.get("description"):
                return str(err["description"])
    except Exception:
        pass
    return ""


def _pace():
    """Wait until PACE_SECONDS have passed since the last Yahoo request, then mark now."""
    global _last_request
    if _last_request is not None:
        wait = PACE_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            SLEEP(wait)
    _last_request = time.monotonic()


def _gunzip(body, headers):
    """body unzipped when the headers say Content-Encoding gzip. A gzip body that was
    cut short gives b"" (so the caller sees a broken answer, not no answer)."""
    if ((headers.get("Content-Encoding") if headers else "") or "").lower() != "gzip":
        return body
    try:
        return gzip.decompress(body)
    except Exception:
        return b""


def _one_get(url, timeout):
    """One raw GET: (status or None when nothing came back, body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw, headers = getattr(resp, "status", 200), resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() or b""
        except Exception:
            body = b""
        return exc.code, _gunzip(body, exc.headers)
    except Exception:
        return None, b""
    return status, _gunzip(raw, headers)


def yahoo_get(path, timeout=TIMEOUT):
    """One paced GET on query1 with the retry rules of part 02 1.3 (one retry on query2
    for a broken answer, a 429, a 5xx or no answer); returns the body or raises."""
    global _last_host
    symbol, interval = _url_symbol(path), _url_interval(path)
    url = path
    for attempt in (0, 1):
        _pace()
        status, body = _one_get(url, timeout)
        _last_host = "query2" if url.startswith(YAHOO_HOSTS[1]) else "query1"
        if status == 200:
            try:
                json.loads(body.decode("utf-8"))
                return body
            except Exception:
                kind = "broken"
        elif status == 429:
            kind = "busy"
        elif status is None or 500 <= status <= 599:
            kind = "down"
        else:
            raise _yahoo_error(status, symbol, interval, _error_description(body))
        if attempt == 1:
            raise _yahoo_error(None if kind == "down" else (429 if kind == "busy" else "broken"),
                               symbol, interval)
        SLEEP(RETRY_WAIT_S[kind])
        if url.startswith(YAHOO_HOSTS[0]):
            url = YAHOO_HOSTS[1] + url[len(YAHOO_HOSTS[0]):]


_ERROR_CODES = {"Not Found": 404, "Unprocessable Entity": 422, "Bad Request": 400,
                "Unauthorized": 401, "Too Many Requests": 429}


def fetch_chart(symbol, interval, range_=None, period1=None, period2=None):
    """GET one chart answer and return chart.result[0] (meta, maybe timestamp, indicators).
    An error in the body raises the part 02 1.3 message; no result raises NoData."""
    body = yahoo_get(chart_url(YAHOO_HOSTS[0], symbol, interval, range_, period1, period2))
    doc = json.loads(body.decode("utf-8"))
    chart = doc.get("chart") or {}
    err = chart.get("error")
    if err:
        code = _ERROR_CODES.get(str(err.get("code")), 400)
        raise _yahoo_error(code, symbol, interval, str(err.get("description") or ""))
    result = chart.get("result")
    if not result:
        raise NoData("Yahoo sent no data for %s." % symbol)
    return result[0]


def parse_chart(result, symbol=""):
    """A chart result -> (meta, rows); rows are [t, o, h, l, c, v], raw, nulls kept.
    The quote lists are read by name; a missing list counts as all nulls."""
    meta = result.get("meta") or {}
    ts = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = []
    for name in ("open", "high", "low", "close", "volume"):
        col = q.get(name)
        if col is None:
            col = [None] * len(ts)
        if len(col) != len(ts):
            raise ValueError("Yahoo sent a broken chart for %s (lists of different length)."
                             % (symbol or meta.get("symbol") or "?"))
        cols.append(col)
    rows = [[ts[i]] + [c[i] for c in cols] for i in range(len(ts))]
    return meta, rows


FETCH_LOG_MAX = 400    # fetch records kept per cache file (newest last)


def cache_file(data_dir, symbol, interval):
    """The cache path data_dir/yahoo/<file>/<interval>.json; <file> is the symbol with
    "=" and "." turned into "_" (GC=F -> GC_F). A strange symbol is refused."""
    if not re.match(r"^[A-Za-z0-9=.^-]+$", str(symbol)):
        raise ValueError("Bad Yahoo symbol: %s" % symbol)
    name = str(symbol).replace("=", "_").replace(".", "_")
    return os.path.join(data_dir, "yahoo", name, "%s.json" % interval)


def load_cache(data_dir, symbol, interval):
    """The cache doc of one symbol and interval; an empty doc when the file is missing."""
    path = cache_file(data_dir, symbol, interval)
    empty = {"symbol": symbol, "interval": interval, "bars": [], "fetches": []}
    if not os.path.exists(path):
        return empty
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("bars"), list):
            raise ValueError(path)
    except (OSError, ValueError):
        raise ValueError("The price cache %s is broken. Delete it and run again with the network on." % path)
    doc.setdefault("fetches", [])
    return doc


def save_cache(path, doc):
    """Write a cache doc as compact JSON (path + ".tmp", then one move), making the folder."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ValueError("could not save %s: %s" % (path, exc.strerror or exc))


def last_fetch(doc):
    """The newest fetch record of a cache doc, or None."""
    fetches = (doc or {}).get("fetches") or []
    return fetches[-1] if fetches else None


def merge_rows(old, new, window, now, interval):
    """Old rows inside the new answer's window (first_t, last_t) go; the new rows come in
    (a new row wins on the same t); sorted by t; rows older than LOOKBACK + 30 days are cut."""
    keep = list(old)
    if window and window[0] is not None and window[1] is not None:
        keep = [r for r in keep if not (window[0] <= r[0] <= window[1])]
    by_t = {}
    for r in keep:
        by_t[r[0]] = list(r)
    for r in new:
        by_t[r[0]] = list(r)
    cut = utc_to_ts(now) - (LOOKBACK[interval] + 30) * 86400
    return [by_t[t] for t in sorted(by_t) if t > cut]


def stored_rows(rows, market_time):
    """The raw rows a cache stores: a non-null close, and not the live tick (the last row
    at market_time, or any row off the whole minute)."""
    out = []
    for i, r in enumerate(rows):
        if r[4] is None or r[0] % 60 != 0:
            continue
        if i == len(rows) - 1 and market_time is not None and r[0] == market_time:
            continue
        out.append(list(r))
    return out


def refresh(data_dir, symbol, interval, now, cfg, offline):
    """Bring one cache file up to date: no network when offline or when the newest fetch
    is younger than MIN_REFRESH_S; else a first fill (a range) or a small incremental
    fetch. Records the fetch and saves. A client ValueError is raised to the caller."""
    doc = load_cache(data_dir, symbol, interval)
    if offline:
        return doc
    last = last_fetch(doc)
    at = parse_utc((last or {}).get("at"))
    if at is not None and (now - at).total_seconds() < MIN_REFRESH_S[interval]:
        return doc
    step = STEP_S[interval]
    bars = doc["bars"]
    range_ = period1 = period2 = None
    old_edge = utc_to_ts(now) - (LOOKBACK[interval] - 5) * 86400
    if not bars or bars[-1][0] < old_edge:
        range_ = FIRST_RANGE[interval]
    else:
        period1 = bars[-1][0] - 2 * step
        period2 = utc_to_ts(now) + 60
    return fetch_into(data_dir, doc, symbol, interval, now, range_, period1, period2)


def fetch_into(data_dir, doc, symbol, interval, now, range_=None, period1=None, period2=None):
    """One chart fetch merged into a cache doc (live tick dropped, fetch recorded), saved."""
    result = fetch_chart(symbol, interval, range_=range_, period1=period1, period2=period2)
    meta, rows = parse_chart(result, symbol)
    kept = stored_rows(rows, meta.get("regularMarketTime"))
    window = (kept[0][0], kept[-1][0]) if kept else None
    doc["bars"] = merge_rows(doc["bars"], kept, window, now, interval)
    doc["fetches"].append({
        "at": utc_iso(now), "host": _last_host, "range": range_, "period1": period1,
        "period2": period2, "first": kept[0][0] if kept else None,
        "last": kept[-1][0] if kept else None, "rows": len(kept),
        "market_time": meta.get("regularMarketTime"), "price": meta.get("regularMarketPrice"),
        "short_name": meta.get("shortName"), "currency": meta.get("currency"),
        "tz": meta.get("exchangeTimezoneName"), "gmtoffset": meta.get("gmtoffset"),
        "previous_close": meta.get("previousClose")})
    doc["fetches"] = doc["fetches"][-FETCH_LOG_MAX:]
    save_cache(cache_file(data_dir, symbol, interval), doc)
    return doc


DONE_MARGIN_MIN = 15          # a span's last bar is done this long after the close


STALE_MINUTES = {"spot_fx": 15, "futures": 30}


YAHOO_DECIMALS = {"GC": 1, "SI": 3, "PL": 1, "PA": 1, "HG": 4, "CL": 2, "BZ": 2, "NG": 3}


def symbol_root(symbol):
    """The futures root of a Yahoo symbol (GC=F and GCZ26.CMX -> "GC"), else None."""
    m = re.match(r"^([A-Z]{1,3})=F$", str(symbol)) or \
        re.match(r"^([A-Z]{1,3})[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM)$", str(symbol))
    return m.group(1) if m else None


def inst_root(inst):
    """The futures root of an instrument row (inst["futures"]["root"]), else None."""
    fut = inst.get("futures") or {}
    return fut.get("root") or (symbol_root(inst.get("yahoo")) if inst.get("price_source") == "futures" else None)


def yahoo_decimals(inst):
    """The decimals Yahoo sends for the instrument's symbol: FX quoted in JPY 3, other
    FX 5; GC 1, SI 3, PL 1, PA 1, HG 4, CL 2, BZ 2, NG 3 (else the row's digits)."""
    root = inst_root(inst)
    if root in YAHOO_DECIMALS:
        return YAHOO_DECIMALS[root]
    if inst.get("kind") == "fx":
        return 3 if inst.get("quote") == "JPY" else 5
    return int(inst.get("digits", 5))


def _round_dec(x, places):
    """A float rounded half up to places decimals (through its shortest text)."""
    return float(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def clean_rows(rows, meta, interval, inst):
    """Raw rows -> bars {start, open, high, low, close, volume}: drops the live tick, rows
    with a null price and (FX and metals) prices of 0 or less; rounds to yahoo_decimals;
    fixes high and low; one bar per start, sorted. meta has regularMarketTime or market_time."""
    meta = meta or {}
    mt = meta.get("regularMarketTime", meta.get("market_time"))
    places = yahoo_decimals(inst)
    by_start = {}
    for i, r in enumerate(rows):
        t = r[0]
        if t % 60 != 0 or (i == len(rows) - 1 and mt is not None and t == mt):
            continue
        if any(x is None for x in r[1:5]):
            continue
        o, h, l, c = [_round_dec(x, places) for x in r[1:5]]
        if inst.get("kind") in ("fx", "metal") and min(o, h, l, c) <= 0:
            continue
        by_start[t] = {"start": ts_to_utc(t), "open": o, "high": max(h, o, c), "low": min(l, o, c),
                       "close": c, "volume": 0 if inst.get("kind") == "fx" else (r[5] or 0)}
    return [by_start[t] for t in sorted(by_start)]


def _span_of(template, utc, cfg, tables=True):
    """The open span (open_utc, close_utc) of a template that holds utc, or None."""
    d = fx_day(utc, "17:00 New York")
    if d is None:
        return None
    for a, b in open_spans(template, d, cfg, tables=tables):
        if a <= utc < b:
            return (a, b)
    return None


def bar_done(start_utc, interval, market_time_utc, now, inst, cfg):
    """True when the bar is over: the data clock passed its end, or it is the last bar
    of its open span (break, weekend, holiday, early close) and now is at least
    DONE_MARGIN_MIN minutes after that close."""
    step = datetime.timedelta(seconds=STEP_S[interval])
    end = start_utc + step
    if market_time_utc is not None and end <= market_time_utc:
        return True
    if now is None or end > now:
        return False
    span = _span_of(inst["data_hours"], start_utc, cfg)
    if span is None:
        return False
    close = span[1]
    return end > close - step and now >= close + datetime.timedelta(minutes=DONE_MARGIN_MIN)


def load_bars(data_dir, symbol, interval, now, rolls=None, include_forming=False, inst=None, cfg=None):
    """Cleaned bars of one cache file: stray bars outside the data hours dropped,
    back-adjusted for rolls (futures), on the plan scale (x yahoo_to_broker), and only
    bars done by now (include_forming adds the forming one). inst (the config row, for
    yahoo_to_broker) and cfg (holidays, early closes) are required."""
    if inst is None or cfg is None:
        raise ValueError("load_bars needs inst= and cfg=")
    doc = load_cache(data_dir, symbol, interval)
    if not doc["bars"]:
        return []
    fetch = last_fetch(doc) or {}
    mt = fetch.get("market_time")
    mt = ts_to_utc(mt) if mt is not None else None
    bars = clean_rows(doc["bars"], {"market_time": fetch.get("market_time")}, interval, inst)
    bars = [b for b in bars if _span_of(inst["data_hours"], b["start"], cfg, tables=False)]
    root = inst_root(inst)
    if root and rolls and str(symbol).endswith("=F"):
        mine = [r for r in rolls if parse_utc(r.get("found_at")) is None or parse_utc(r["found_at"]) <= now]
        bars = adjust_rows(bars, mine, root, interval)
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    if u != 1:
        q = Decimal(1).scaleb(-int(inst["digits"]))
        for b in bars:
            for k in ("open", "high", "low", "close"):
                b[k] = float((_dec_clean(b[k]) * u).quantize(q, rounding=ROUND_HALF_UP))
    at = parse_utc(fetch.get("at"))
    wall = now if at is None else min(at, now)
    step = datetime.timedelta(seconds=STEP_S[interval])
    out = []
    for b in bars:
        if b["start"] + step <= now and bar_done(b["start"], interval, mt, wall, inst, cfg):
            out.append(b)
        elif include_forming and b["start"] <= now:
            b["forming"] = True
            out.append(b)
    return out


def data_age_min(at_utc, now):
    """How many minutes old a price is (at least 0)."""
    return max(0.0, (now - at_utc).total_seconds() / 60.0)


def _plan_bid(raw, inst):
    """A raw Yahoo price -> (plan-scale bid, Yahoo price rounded to yahoo_decimals)."""
    y = _round_dec(raw, yahoo_decimals(inst))
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    if u == 1:
        return y, y
    q = Decimal(1).scaleb(-int(inst["digits"]))
    return float((_dec_clean(y) * u).quantize(q, rounding=ROUND_HALF_UP)), y


def latest_price(data_dir, inst, now, cfg):
    """The newest Yahoo price of the instrument: {bid (plan scale), yahoo, at, age_min,
    source, contract} from the newest 5m or 1h fetch; in a replay (the cache is newer than
    now) the close of the last bar done by now, at its end. None when there is no data."""
    best = None
    for interval in ("5m", "1h"):
        f = last_fetch(load_cache(data_dir, inst["yahoo"], interval))
        if f and f.get("market_time") is not None and f.get("price") is not None:
            if best is None or f["market_time"] > best[1]["market_time"]:
                best = (interval, f)
    futures = inst.get("price_source") == "futures"
    if best is not None:
        at = ts_to_utc(best[1]["market_time"])
        fetched = parse_utc(best[1].get("at"))
        if at <= now and (fetched is None or fetched <= now):
            bid, y = _plan_bid(best[1]["price"], inst)
            return {"bid": bid, "yahoo": y, "at": at, "age_min": data_age_min(at, now),
                    "source": "yahoo " + best[0], "contract": best[1].get("short_name") if futures else None}
    last = None
    for interval in ("5m", "1h"):
        bars = load_bars(data_dir, inst["yahoo"], interval, now, inst=inst, cfg=cfg)
        if bars:
            end = bars[-1]["start"] + datetime.timedelta(seconds=STEP_S[interval])
            if last is None or end > last[1]:
                last = (interval, end, bars[-1])
    if last is None:
        return None
    raw = last[2]["close"]
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    y = float(_dec_clean(raw) / u) if u != 1 else raw
    contract = None
    if futures:
        newest = None
        for interval in ("5m", "1h"):
            for f in load_cache(data_dir, inst["yahoo"], interval)["fetches"]:
                t = parse_utc(f.get("at"))
                if t is not None and t <= now and (newest is None or t > newest):
                    newest, contract = t, f.get("short_name")
    return {"bid": raw, "yahoo": _round_dec(y, yahoo_decimals(inst)), "at": last[1],
            "age_min": data_age_min(last[1], now), "source": "yahoo " + last[0], "contract": contract}


MONTH_CODES = {"Jan": "F", "Feb": "G", "Mar": "H", "Apr": "J", "May": "K", "Jun": "M",
               "Jul": "N", "Aug": "Q", "Sep": "U", "Oct": "V", "Nov": "X", "Dec": "Z"}


ROLL_MATCH_BARS = 3          # BZ: the old contract still matches on the newest 3 shared bars


ROLL_CHECK_HOURS = 6         # BZ: the match test at most once every 6 hours (not every_run)


ROLL_SAME_DAYS = 3           # a roll of the root within 3 days is the same roll


def rolls_path(data_dir):
    """The path of data/rolls.json."""
    return os.path.join(data_dir, "rolls.json")


def load_rolls_doc(data_dir):
    """The whole rolls.json doc {rolls, checked, updated_at}; an empty one when missing."""
    doc = load_json(rolls_path(data_dir), None) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("rolls", []), list):
        raise ValueError("%s is broken. Delete it: the rolls are found again." % rolls_path(data_dir))
    doc.setdefault("rolls", [])
    doc.setdefault("checked", {})
    return doc


def load_rolls(data_dir):
    """The list of rolls in rolls.json, sorted by first_bar_new ([] when missing)."""
    return sorted(load_rolls_doc(data_dir)["rolls"], key=lambda r: r.get("first_bar_new") or "")


def save_rolls(data_dir, rolls, checked=None, now=None):
    """Write rolls.json: the rolls sorted by first_bar_new; checked kept from the file
    unless given; updated_at = now (the real clock when None)."""
    doc = load_rolls_doc(data_dir)
    if checked is None:
        checked = doc.get("checked") or {}
    out = {"updated_at": utc_iso(now or utc_now()),
           "rolls": sorted(rolls, key=lambda r: r.get("first_bar_new") or ""), "checked": checked}
    save_json_atomic(rolls_path(data_dir), out)


def contract_from_name(short_name, root, suffix):
    """The single contract named by a Yahoo shortName: "Gold Dec 26" -> "GCZ26.CMX";
    None when the name has no month (BZ=F) or is null."""
    if not short_name:
        return None
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\s*$", str(short_name))
    if not m:
        return None
    return "%s%s%s%s" % (root, MONTH_CODES[m.group(1)], m.group(2), suffix)


def cme_business_day(d):
    """True for Monday to Friday when HOLIDAYS does not shut the CME all day."""
    return d.weekday() < 5 and "cme" not in HOLIDAYS.get(d.isoformat(), {}).get("closed", [])


def add_business_days(d, n):
    """The CME business day n business days after d (n below 0: before it)."""
    step = datetime.timedelta(days=1 if n >= 0 else -1)
    left = abs(n)
    while left:
        d += step
        if cme_business_day(d):
            left -= 1
    return d


def _month_add(year, month, n):
    """(year, month) moved by n months."""
    k = year * 12 + (month - 1) + n
    return k // 12, k % 12 + 1


def _last_business_day(year, month):
    """The last CME business day of a month."""
    y, m = _month_add(year, month, 1)
    d = datetime.date(y, m, 1) - datetime.timedelta(days=1)
    while not cme_business_day(d):
        d -= datetime.timedelta(days=1)
    return d


def roll_dates(rule, year, month):
    """For the contract that delivers in (year, month): (window_start, window_end, Y) as
    dates, by its roll rule (part 02 5.2). Y is the day Yahoo's daily series switches."""
    py, pm = _month_add(year, month, -1)
    if rule in ("gc_fnd", "si_hg"):
        y_day = _last_business_day(py, pm)
        return add_business_days(y_day, -10), add_business_days(y_day, 1), y_day
    if rule == "cl_ltd":
        d25 = datetime.date(py, pm, 25)
        last = add_business_days(d25, -3 if cme_business_day(d25) else -4)
        back = 8
    elif rule == "bz_ltd":
        last = _last_business_day(*_month_add(year, month, -2))
        back = 10
    elif rule == "ng_ltd":
        last = add_business_days(datetime.date(year, month, 1), -3)
        back = 8
    else:
        raise ValueError('Unknown roll_rule "%s". Use gc_fnd, si_hg, cl_ltd, bz_ltd or ng_ltd.' % rule)
    return add_business_days(last, -back), add_business_days(last, 1), add_business_days(last, 1)


def _cycle_contracts(inst, first_year, last_year):
    """[(year, month, symbol)] of the cycle contracts in those years, in order."""
    fut = inst["futures"]
    code_month = dict((c, i + 1) for i, c in enumerate("FGHJKMNQUVXZ"))
    months = sorted(code_month[c] for c in fut["cycle"])
    out = []
    for y in range(first_year, last_year + 1):
        for m in months:
            out.append((y, m, "%s%s%02d%s" % (fut["root"], "FGHJKMNQUVXZ"[m - 1], y % 100, fut["suffix"])))
    return out


def _windows(inst, first_year, last_year):
    """[(old, new, window_start_utc, window_end_utc, Y date)] for each cycle contract."""
    cs = _cycle_contracts(inst, first_year, last_year + 1)
    out = []
    for (y, m, old), (_, _, new) in zip(cs, cs[1:]):
        a, b, y_day = roll_dates(inst["futures"]["roll_rule"], y, m)
        out.append((old, new, ny_to_utc(at_time(a - datetime.timedelta(days=1), "17:00")),
                    ny_to_utc(at_time(b, "17:00")), y_day))
    return out


def expected_front(root, utc, inst):
    """The cycle contract the calendar rule expects as Yahoo's front at utc: the one whose
    search window END is the first one after utc ("GCZ26.CMX" on 2026-11-11)."""
    for old, new, a, b, y_day in _windows(inst, utc.year - 1, utc.year + 1):
        if b > utc:
            return old
    raise ValueError("No %s contract found after %s." % (root, utc_iso(utc)))


def expected_rolls(root, inst, from_utc, to_utc):
    """Each expected roll (old, new, window_start, window_end, Y) whose search window
    overlaps [from_utc, to_utc]. Y is the date the daily series switches."""
    return [w for w in _windows(inst, from_utc.year - 1, to_utc.year + 1)
            if w[2] <= to_utc and w[3] >= from_utc]


def _roll_bar(row, places):
    """A raw [t, o, h, l, c, v] row -> a bar dict rounded to places decimals."""
    o, h, l, c = [_round_dec(x, places) for x in row[1:5]]
    return {"start": ts_to_utc(row[0]), "open": o, "high": max(h, o, c), "low": min(l, o, c),
            "close": c, "volume": row[5] or 0}


def adjust_rows(rows, rolls, root, interval="1h"):
    """Back-adjusted copies of cleaned bars (dicts): each bar times the ratios of the rolls
    of root that start later. The 1h bar at a roll is its replacement, or keeps the flag
    "roll"; 5m bars in that hour get the flag. The cache is never edited."""
    places = YAHOO_DECIMALS.get(root, 2)
    mine = []
    for r in rolls or []:
        if r.get("root") == root and parse_utc(r.get("first_bar_new")) is not None:
            mine.append((parse_utc(r["first_bar_new"]), r))
    out = []
    for bar in rows:
        b = dict(bar)
        start = b["start"]
        hit = None
        for at, r in mine:
            if (interval == "1h" and at == start) or (interval == "5m" and at <= start < at + datetime.timedelta(hours=1)):
                hit = r
        if hit is not None and interval == "1h" and (hit.get("replacement") or {}).get("1h"):
            b = _roll_bar(hit["replacement"]["1h"], places)
        elif hit is not None and interval in ("1h", "5m"):
            b["flags"] = sorted(set((b.get("flags") or []) + ["roll"]))
        factor = Decimal(1)
        for at, r in mine:
            if at > start:
                factor *= Decimal(repr(float(r["ratio"])))
        if factor != 1:
            q = Decimal(1).scaleb(-places)
            for k in ("open", "high", "low", "close"):
                b[k] = float((Decimal(repr(float(b[k]))) * factor).quantize(q, rounding=ROUND_HALF_UP))
        out.append(b)
    return out


ROLL_AWAY = {"stop_loss": "entry", "take_profit": "entry", "stop_at_open": "entry", "chart_stop": "chart_entry"}


def _get_path(item, path):
    """item["a"]["b"] for the path "a.b", or None."""
    cur = item
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(item, path, value):
    """Set item["a"]["b"] = value for the path "a.b" (the parents must exist)."""
    parts = path.split(".")
    cur = item
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value


def rebase(item, rolls, fields, inst=None):
    """Move an open item's plan-scale fields (dotted paths) by each roll it has not had:
    by ratio for a new roll id, by ratio / ratio_applied for a changed one. inst is the
    item's instrument row: only the rolls of its root count (all of load_rolls may be
    given), and the moved levels are rounded to its tick. Returns (new item, ids that
    changed something)."""
    if inst is None:
        raise ValueError("rebase needs the instrument row")
    new = copy.deepcopy(item)
    applied = dict(new.get("rolls_applied") or {})
    root = inst_root(inst)
    if not root:
        new["rolls_applied"] = applied
        return new, []
    mine = [r for r in (rolls or []) if r.get("root") == root]
    mult = Decimal(1)
    ids = []
    for r in sorted(mine, key=lambda r: r.get("first_bar_new") or ""):
        ratio = float(r["ratio"])
        done = applied.get(r["id"])
        if done is None:
            mult *= Decimal(repr(ratio))
        elif float(done) != ratio:
            mult *= Decimal(repr(ratio)) / Decimal(repr(float(done)))
        else:
            continue
        applied[r["id"]] = ratio
        ids.append(r["id"])
    new["rolls_applied"] = applied
    if not ids:
        return new, ids
    plain = [f for f in fields if f.split(".")[-1] not in ROLL_AWAY]
    away = [f for f in fields if f.split(".")[-1] in ROLL_AWAY]
    for f in plain + away:
        v = _get_path(new, f)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        x = _dec_clean(v) * mult
        last = f.split(".")[-1]
        entry_path = ".".join(f.split(".")[:-1] + [ROLL_AWAY[last]]) if last in ROLL_AWAY else None
        entry = _get_path(new, entry_path) if entry_path else None
        if entry is not None and float(x) != float(entry):
            _set_path(new, f, round_away(float(x), entry, inst))
        else:
            _set_path(new, f, round_price(float(x), inst))
    return new, ids


ROLL_WORDS = {"GC": "gold", "SI": "silver", "PL": "platinum", "PA": "palladium", "HG": "copper",
              "CL": "oil", "BZ": "Brent", "NG": "gas"}


def roll_tolerance(inst):
    """Two Yahoo closes match when they differ by at most half a Yahoo step (GC: 0.05)."""
    return 0.5 * 10 ** -yahoo_decimals(inst) + 1e-9


def _contract_bars(data_dir, symbol, inst, now, w0, w1):
    """The 1h bars of a single contract in [w0, w1], fetched into its own cache file;
    None when Yahoo has no data for it (a 404, or no bars in the window)."""
    doc = load_cache(data_dir, symbol, "1h")
    try:
        doc = fetch_into(data_dir, doc, symbol, "1h", now, None, utc_to_ts(w0), utc_to_ts(w1) + 60)
    except NoData:
        return None
    bars = [b for b in clean_rows(doc["bars"], {}, "1h", inst) if w0 <= b["start"] <= w1]
    return bars or None


def _switch_bar(cont, old_by, new_by, tol):
    """(index of the switch bar S in cont, mixed) or None: S is the first bar on the new
    contract after the last bar on the old one (old_by None: a bar not on new stands in)."""
    def on(b, by):
        x = by.get(b["start"]) if by else None
        return x is not None and abs(b["close"] - x["close"]) <= tol
    marks = []
    for b in cont:
        is_new = on(b, new_by)
        is_old = (not is_new) if old_by is None else on(b, old_by)
        marks.append("both" if is_new and is_old else ("new" if is_new else ("old" if is_old else "")))
    olds = [i for i, m in enumerate(marks) if m == "old"]
    start = olds[-1] + 1 if olds else 0
    news = [i for i in range(start, len(cont)) if marks[i] == "new"]
    if not news:
        return None
    if olds and start < len(cont):
        b = cont[start]
        o, n = (old_by or {}).get(b["start"]), new_by.get(b["start"])
        if old_by is not None and o is not None:
            # open on old, close off old (on new, or new has no bar there): the switch is inside
            if abs(b["open"] - o["open"]) <= tol and abs(b["close"] - o["close"]) > tol \
                    and (n is None or abs(b["close"] - n["close"]) <= tol):
                return start, True
        elif old_by is None and n is not None and marks[start] == "new":
            return start, abs(b["open"] - n["open"]) > tol
    return news[0], False


def _roll_ratio(cont, s, old_by, new_by):
    """(ratio, old_price, new_price, prices_at, exact) for a switch at cont[s] (part 02
    5.5 step 5); old_by None: the continuous bars before S stand in for old."""
    at = cont[s]["start"]
    def pair(b):
        o = (old_by or {}).get(b["start"]) if old_by is not None else (b if b["start"] < at else None)
        n = new_by.get(b["start"])
        return (o["close"], n["close"]) if o is not None and n is not None else None
    before = [b for b in cont[:s] if b["start"] >= at - datetime.timedelta(hours=12) and pair(b)]
    if before:
        b = before[-1]
    else:
        after = [b for b in cont[s:] if pair(b)]
        b = after[0] if after else None
    if b is not None:
        o, n = pair(b)
        return n / o, o, n, b["start"], old_by is not None
    if s == 0:
        return None
    o, n = cont[s - 1]["close"], cont[s]["close"]
    return n / o, o, n, cont[s]["start"], False


def _newest_fetch(data_dir, symbol):
    """The newest fetch record (by "at") of the 1h and 5m caches of a symbol, or None."""
    best = None
    for interval in ("1h", "5m"):
        f = last_fetch(load_cache(data_dir, symbol, interval))
        if f and (best is None or (f.get("at") or "") > (best.get("at") or "")):
            best = f
    return best


def _bz_still_old(data_dir, inst, old, cont, now):
    """BZ: True when the old contract's 1h closes match BZ=F on the newest shared bars
    (no roll yet); False when old is gone or does not match."""
    bars = _contract_bars(data_dir, old, inst, now, now - datetime.timedelta(hours=24), now)
    if not bars:
        return False
    by = dict((b["start"], b) for b in bars)
    shared = [b for b in cont if b["start"] in by][-ROLL_MATCH_BARS:]
    tol = roll_tolerance(inst)
    return bool(shared) and all(abs(b["close"] - by[b["start"]]["close"]) <= tol for b in shared)


def _name_change(data_dir, symbol):
    """The shortNames of the two newest fetches (1h and 5m caches, by "at") that have one,
    as (older, newer), when they differ. None on a first run (one name only) or no change:
    a forward roll is only a change between two of our own fetches."""
    rows = []
    for interval in ("1h", "5m"):
        for f in load_cache(data_dir, symbol, interval)["fetches"]:
            if f.get("short_name"):
                rows.append((f.get("at") or "", f["short_name"]))
    rows.sort()
    if len(rows) < 2 or rows[-2][1] == rows[-1][1]:
        return None
    return rows[-2][1], rows[-1][1]


def check_roll_forward(data_dir, inst, cfg, now, offline, every_run=False):
    """After a refresh of a futures symbol: find a roll that just happened (a shortName
    change, or for BZ a failed price match inside a roll window), measure it with the old
    and new contracts' own bars, save it in rolls.json and return the record (else None)."""
    if offline:
        return None
    fut = inst["futures"]
    root, yahoo = fut["root"], inst["yahoo"]
    word = ROLL_WORDS.get(root, inst.get("name", root).lower())
    rdoc = load_rolls_doc(data_dir)
    mine = sorted([r for r in rdoc["rolls"] if r.get("root") == root], key=lambda r: r.get("first_bar_new") or "")
    raw = load_cache(data_dir, yahoo, "1h")["bars"]
    cont = clean_rows(raw, {}, "1h", inst)
    first = cont[0]["start"] if cont else now
    old_name = new_name = None
    if fut.get("shortname_month", True):
        pair = _name_change(data_dir, yahoo)
        if pair is None:
            return None
        old_name, new_name = pair
        old = contract_from_name(old_name, root, fut["suffix"])
        cur = contract_from_name(new_name, root, fut["suffix"])
        if old is None or cur is None or old == cur or any(r.get("new") == cur for r in mine):
            return None
        new, found_by = cur, "shortname"
    else:
        wins = [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]]
        if not wins:
            return None
        checked = rdoc["checked"].setdefault(root, {"history_from": None, "history_to": None, "last_forward": None})
        last = parse_utc(checked.get("last_forward"))
        if not every_run and last is not None and now - last < datetime.timedelta(hours=ROLL_CHECK_HOURS):
            return None
        checked["last_forward"] = utc_iso(now)
        old, new = wins[0][0], wins[0][1]
        if any(r.get("new") == new for r in mine) or _bz_still_old(data_dir, inst, old, cont, now):
            save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
            return None
        found_by = "match"
    starts = [w[2] for w in expected_rolls(root, inst, now - datetime.timedelta(days=400), now) if w[1] == new]
    w0 = starts[-1] if starts else ny_to_utc(at_time(add_business_days(to_ny(now).date(), -10), "00:00"))
    w0 = max(w0, first)
    old_bars = _contract_bars(data_dir, old, inst, now, w0, now)
    new_bars = _contract_bars(data_dir, new, inst, now, w0, now)
    if new_bars is None:
        if found_by == "match":
            save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
        raise NoData("Yahoo %s moved to a new contract, but Yahoo has no data for it. "
                     "The roll size will come from daily prices." % word)
    old_by = dict((b["start"], b) for b in old_bars) if old_bars else None
    new_by = dict((b["start"], b) for b in new_bars)
    cw = [b for b in cont if w0 <= b["start"] <= now]
    hit = _switch_bar(cw, old_by, new_by, roll_tolerance(inst))
    if hit is None:
        return None
    s, mixed = hit
    measured = _roll_ratio(cw, s, old_by, new_by)
    if measured is None:
        return None
    ratio, op, np_, at, exact = measured
    S = cw[s]
    rep = None
    if mixed:
        rep = [r for r in load_cache(data_dir, new, "1h")["bars"] if r[0] == utc_to_ts(S["start"])]
        rep = rep[0] if rep else None
    note = "" if old_bars else "The old %s contract is gone from Yahoo. The roll size is an estimate." % word
    rec = {"id": "%s-%s" % (root, S["start"].date().isoformat()), "root": root, "yahoo": yahoo,
           "old": old, "new": new, "old_name": old_name, "new_name": new_name,
           "first_bar_new": utc_iso(S["start"]), "ratio": ratio, "old_price": op, "new_price": np_,
           "prices_at": utc_iso(at), "found_by": found_by,
           "confidence": "exact" if exact else "estimated", "replaced_bar": rep is not None,
           "found_at": utc_iso(now), "note": note, "replacement": {"1h": rep}}
    if mixed and rep is None:
        rec["note"] = (rec["note"] + " The switch bar is flagged roll.").strip()
    return _keep_roll(data_dir, rdoc, rec, now)


def _keep_roll(data_dir, rdoc, rec, now):
    """Add a roll record to rolls.json: a roll of the root within 3 days is replaced only
    by an exact record over an estimated one (keeping its id). Returns the saved record or None."""
    at = parse_utc(rec["first_bar_new"])
    rolls = list(rdoc["rolls"])
    for i, r in enumerate(rolls):
        other = parse_utc(r.get("first_bar_new"))
        if r.get("root") == rec["root"] and other is not None and abs(other - at) <= datetime.timedelta(days=ROLL_SAME_DAYS):
            if rec["confidence"] == "exact" and r.get("confidence") == "estimated":
                rec = dict(rec, id=r["id"])
                rolls[i] = rec
                save_rolls(data_dir, rolls, rdoc["checked"], now)
                return rec
            return None
    rolls.append(rec)
    save_rolls(data_dir, rolls, rdoc["checked"], now)
    return rec


RATE_PAIRS = {"EUR": ("EURUSD", "mul"), "GBP": ("GBPUSD", "mul"), "AUD": ("AUDUSD", "mul"),
              "NZD": ("NZDUSD", "mul"), "JPY": ("USDJPY", "div"), "CHF": ("USDCHF", "div"),
              "CAD": ("USDCAD", "div")}


class TooSmall(ValueError):
    """A refused size: the order would be below min_lot (tools exit 1, not 2). It keeps
    min_lot, min_lot_loss_usd, risk_limit_usd and decided_by for the words."""

    def __init__(self, message, min_lot=None, min_lot_loss_usd=None, risk_limit_usd=None,
                 decided_by=None):
        ValueError.__init__(self, message)
        self.min_lot = min_lot
        self.min_lot_loss_usd = min_lot_loss_usd
        self.risk_limit_usd = risk_limit_usd
        self.decided_by = decided_by


def _cents(d):
    """A Decimal amount of money, half up to cents, as a float (never -0.0)."""
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def _num_word(x):
    """A plain number for messages: 24 -> "24", 30.5 -> "30.5"."""
    return format(_dec_clean(x).normalize(), "f")


def _pct_word(x):
    """A percent for messages, at least 1 decimal: 1 -> "1.0", 0.25 -> "0.25"."""
    s = _num_word(x)
    return s if "." in s else s + ".0"


def _pccy(inst):
    """The instrument's profit currency (the quote when the row has none)."""
    return inst.get("profit_currency") or inst["quote"]


def _is_currency(code):
    """True for USD and the currencies of RATE_PAIRS (the ones the pack can turn into USD)."""
    return code == "USD" or code in RATE_PAIRS


def _rate_from_price(currency, price):
    """USD per 1 unit of currency from the price of its RATE_PAIRS pair (exact Decimal)."""
    p = _price_dec(price)
    return p if RATE_PAIRS[currency][1] == "mul" else Decimal(1) / p


def rate_entry(currency, price, at=None, age_h=0.0):
    """One row of the rates dict: {usd, pair, price, at, age_h} plus the exact Decimal
    copy "_usd" the money helpers use. usd is a float with 9 significant digits."""
    d = _rate_from_price(currency, price)
    return {"usd": float(format(d, ".9g")), "pair": RATE_PAIRS[currency][0],
            "price": float(_price_dec(price)), "at": at, "age_h": age_h, "_usd": d}


def rate_age_h(at_utc, now):
    """Market hours from at_utc to now, 1 decimal: the hours between minus each FX
    weekend (Friday 17:00 to Sunday 17:00 New York). Never below 0."""
    if now <= at_utc:
        return 0.0
    gone = datetime.timedelta(0)
    d = to_ny(at_utc).date() - datetime.timedelta(days=7)
    last = to_ny(now).date()
    while d <= last:
        if d.weekday() == 4:
            w0 = ny_to_utc(datetime.datetime.combine(d, datetime.time(17)))
            w1 = ny_to_utc(datetime.datetime.combine(d + datetime.timedelta(days=2), datetime.time(17)))
            lo, hi = max(at_utc, w0), min(now, w1)
            if hi > lo:
                gone += hi - lo
        d += datetime.timedelta(days=1)
    hours = Decimal(str(((now - at_utc) - gone).total_seconds())) / Decimal(3600)
    return float(max(hours, Decimal(0)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def fx_rates(data_dir, now, cfg):
    """USD value of one unit of each currency, from the cache only (latest_price, no
    network): {"USD": {...}, "JPY": {"usd", "pair", "price", "at", "age_h", "_usd"}}.
    A pair that is not in the config or has no cached price is left out."""
    out = {"USD": {"usd": 1.0, "pair": None, "price": None, "at": None, "age_h": 0.0,
                   "_usd": Decimal(1)}}
    rows = cfg.get("instruments") or {}
    for ccy, (key, how) in RATE_PAIRS.items():
        if key not in rows:
            continue
        lp = latest_price(data_dir, instrument(cfg, key), now, cfg)
        if not lp or lp.get("bid") is None:
            continue
        at = lp.get("at")
        at_dt = parse_utc(at) if isinstance(at, str) else at   # (check) str or datetime
        age = rate_age_h(at_dt, now) if at_dt is not None else 0.0
        out[ccy] = rate_entry(ccy, lp["bid"], utc_iso(at_dt), age)
    return out


def usd_per_unit(currency, rates, cfg=None):
    """USD for 1 unit of currency, a Decimal. With cfg, a rate older than
    fx_rates_max_age_hours (market hours) is refused."""
    if currency == "USD":
        return Decimal(1)
    if not _is_currency(currency):
        raise ValueError("%s is not a currency. Set profit_currency and margin_currency to USD "
                         "or a currency code." % (currency,))
    row = (rates or {}).get(currency)
    pair = RATE_PAIRS[currency][0]
    if not row:
        raise ValueError("No USD rate for %s. It comes from %s. Keep %s in your config and run "
                         "once with Yahoo on (not --offline)." % (currency, pair, pair))
    if cfg is not None:
        limit = cfg.get("fx_rates_max_age_hours", 24)
        age = row.get("age_h") or 0.0
        if _dec_clean(age) > _dec_clean(limit):
            raise ValueError("The USD rate for %s is %.1f hours old (your limit is %s). Run once "
                             "with Yahoo on to refresh it." % (currency, age, _num_word(limit)))
    if row.get("_usd") is not None:
        return row["_usd"]
    if row.get("price") is not None:
        return _rate_from_price(currency, row["price"])
    return _dec_clean(row["usd"])


def _base_has_rate(inst):
    """True for an FX pair whose profit currency is the quote and whose base has a USD
    rate (USD itself or a RATE_PAIRS currency): its loss rate moves with the stop."""
    return (inst.get("kind") == "fx" and _pccy(inst) == inst.get("quote")
            and _is_currency(inst.get("base")))


def loss_usd_per_unit(inst, stop_loss, rates, cfg=None):
    """USD for 1 unit of the profit currency for the LOSS at the stop, the safer rate:
    an FX pair takes max(today, base rate / stop) (USD-first pairs: 1 / stop)."""
    ccy = _pccy(inst)
    if ccy == "USD":
        return Decimal(1)
    today = usd_per_unit(ccy, rates, cfg)
    if not _base_has_rate(inst):
        return today
    return max(today, usd_per_unit(inst["base"], rates, cfg) / _price_dec(stop_loss))


def reward_usd_per_unit(inst, take_profit, rates, cfg=None):
    """The mirror of loss_usd_per_unit for the reward: the smaller of today's rate and
    base rate / take_profit on an FX pair (the safe side)."""
    ccy = _pccy(inst)
    if ccy == "USD":
        return Decimal(1)
    today = usd_per_unit(ccy, rates, cfg)
    if not _base_has_rate(inst):
        return today
    return min(today, usd_per_unit(inst["base"], rates, cfg) / _price_dec(take_profit))


def rate_symbols(inst, cfg):
    """The instrument keys whose prices this trade needs for USD rates (profit currency,
    margin currency, the base of an FX pair): GBPJPY -> ["USDJPY", "GBPUSD"]."""
    out = []
    codes = [_pccy(inst), inst.get("margin_currency") or inst.get("base")]
    if inst.get("kind") == "fx":
        codes.append(inst.get("base"))
    for code in codes:
        if code in RATE_PAIRS and RATE_PAIRS[code][0] not in out:
            out.append(RATE_PAIRS[code][0])
    return out


def usd_per_point_per_lot(inst, rates, cfg=None):
    """USD a 1-point move makes on 1 lot (MetaTrader's tick value when tick = point):
    point x contract_size x USD per profit unit, half up to 4 decimals."""
    d = (_dec_clean(inst["point"]) * _dec_clean(inst["contract_size"])
         * usd_per_unit(_pccy(inst), rates, cfg))
    return float(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _swap_free(cfg):
    """True when the account is swap-free (D20)."""
    return bool(((cfg or {}).get("account") or {}).get("swap_free"))


def _margin_ccy_usd(inst, price, rates, cfg):
    """USD for 1 unit of the margin currency (part 04 5.1 step 2): the order's own price
    when the rate pair is this instrument; price x the profit rate for a commodity base."""
    mc = inst.get("margin_currency") or inst.get("base")
    if mc == "USD":
        return Decimal(1)
    if mc in RATE_PAIRS and RATE_PAIRS[mc][0] == inst.get("key") and price is not None:
        return _rate_from_price(mc, price)
    if _is_currency(mc) or mc != inst.get("base"):
        return usd_per_unit(mc, rates, cfg)
    if price is None:
        raise ValueError("%s: the margin is in %s. The tool needs a price to count it."
                         % (inst.get("key"), mc))   # (check) words
    return _price_dec(price) * usd_per_unit(_pccy(inst), rates, cfg)


def _commission(inst, lots):
    """Commission for both sides, unrounded Decimal: commission_per_lot_side x lots x 2."""
    return _dec_clean(inst.get("commission_per_lot_side") or 0) * _dec_clean(lots) * 2


def _spread(inst, lots, rates, cfg):
    """The spread's money, unrounded Decimal: spread_points x point x contract_size x
    lots x USD per profit unit (words only: it is already inside the stop distance)."""
    return (_dec_clean(inst["spread_points"]) * _dec_clean(inst["point"])
            * _dec_clean(inst["contract_size"]) * _dec_clean(lots)
            * usd_per_unit(_pccy(inst), rates, cfg))


def _swap(inst, side, lots, nights, rates, cfg, price):
    """The swap for lots over nights (weighted, 6.3), unrounded Decimal, signed as in
    MetaTrader (plus = paid to you). 0 on a swap-free account (D20)."""
    side_word(side)
    if _swap_free(cfg):
        return Decimal(0)
    key = inst.get("key")
    v = _dec_clean(inst.get("swap_long" if side == "buy" else "swap_short") or 0)
    mode = inst.get("swap_mode") or "points"
    if mode == "disabled":
        return Decimal(0)
    if mode == "points":
        per = v * _dec_clean(inst["point"]) * _dec_clean(inst["contract_size"])
        per *= usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "deposit":
        per = v
    elif mode == "profit":
        per = v * usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "base":
        base = inst.get("base")
        if _is_currency(base):
            per = v * usd_per_unit(base, rates, cfg)
        elif price is None:
            raise ValueError("%s swaps are in %s. The tool needs a price to count them."
                             % (key, base))   # (check) words
        else:
            per = v * _price_dec(price) * usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "margin":
        per = v * _margin_ccy_usd(inst, price, rates, cfg)
    elif mode == "interest":
        if price is None:
            raise ValueError("%s swaps are a yearly percent. The tool needs a price to count them."
                             % key)
        per = _price_dec(price) * _dec_clean(inst["contract_size"]) * v / 100 / 360
        per *= usd_per_unit(_pccy(inst), rates, cfg)
    else:
        raise ValueError('%s: swap_mode "%s" is not known. Use points, deposit, profit, base, '
                         'margin, interest or disabled.' % (key, mode))   # (check) words
    return per * _dec_clean(lots) * _dec_clean(nights)


def _fee(inst, lots, nights, cfg):
    """The swap-free fee (D20), unrounded Decimal: fee_per_lot x lots x the nights after
    swap_free_grace_nights. 0 when the account is not swap-free or has no fee."""
    fee = inst.get("swap_free_fee_per_lot")
    if not _swap_free(cfg) or fee is None:
        return Decimal(0)
    paid = max(Decimal(0), _dec_clean(nights) - _dec_clean(inst.get("swap_free_grace_nights") or 0))
    return _dec_clean(fee) * _dec_clean(lots) * paid


def swap_nights(open_utc, close_utc, inst, cfg):
    """Rollovers held (each Monday to Friday 17:00 New York with open < t <= close),
    the swap_triple_day one counted 3 ("none": every night 1). Naive UTC times."""
    if getattr(open_utc, "tzinfo", None) is not None or getattr(close_utc, "tzinfo", None) is not None:
        raise ValueError("Times must be naive UTC datetimes.")   # (check) words
    if close_utc <= open_utc:
        return 0
    triple = inst.get("swap_triple_day") or "wednesday"   # (check) default
    n = 0
    d = to_ny(open_utc).date()
    last = to_ny(close_utc).date() + datetime.timedelta(days=1)
    while d <= last:
        if d.weekday() < 5:
            t = ny_to_utc(datetime.datetime.combine(d, datetime.time(17)))
            if open_utc < t <= close_utc:
                n += 3 if triple != "none" and FULL_DAY_NAMES[d.weekday()].lower() == triple else 1
        d += datetime.timedelta(days=1)
    return n


def _costs(inst, side, lots, nights, rates, cfg, price):
    """The costs inside a max loss, unrounded Decimal: commission + swap-free fee + the
    swap you pay (a swap credit counts 0). The spread is not here (it is in the price)."""
    return (_commission(inst, lots) + _fee(inst, lots, nights, cfg)
            + max(Decimal(0), -_swap(inst, side, lots, nights, rates, cfg, price)))


def costs_usd(inst, side, lots, nights, rates, cfg, price):
    """orders.json plan.costs_usd: _costs for the order's lots, half up to cents."""
    return _cents(_costs(inst, side, lots, nights, rates, cfg, price))


def trade_costs(inst, side, lots, nights, rates, cfg, price=None):
    """{spread, commission, swap, fee, total} in USD, each half up to cents; total =
    spread + commission + fee - swap on the unrounded cores, rounded once."""
    sp = _spread(inst, lots, rates, cfg)
    co = _commission(inst, lots)
    sw = _swap(inst, side, lots, nights, rates, cfg, price)
    fe = _fee(inst, lots, nights, cfg)
    return {"spread": _cents(sp), "commission": _cents(co), "swap": _cents(sw),
            "fee": _cents(fe), "total": _cents(sp + co + fe - sw)}


def _lots_dec(lots):
    """Lots as a cleaned Decimal; ValueError when not above 0."""
    d = _dec_clean(lots)
    if d <= 0:
        raise ValueError("Lots must be above 0.")
    return d


def _check_stop_side(side, entry, stop_loss):
    """entry and stop_loss as Decimals; ValueError when the stop is on the wrong side."""
    side_word(side)
    e, s = _price_dec(entry), _price_dec(stop_loss)
    if side == "buy" and not s < e:
        raise ValueError("A buy's stop must be below the entry.")
    if side == "sell" and not s > e:
        raise ValueError("A sell's stop must be above the entry.")
    return e, s


def max_loss_core(inst, side, entry, stop_loss, nights, rates, cfg):
    """The max loss of 1 lot at the stop, all costs in, unrounded Decimal (6.7 on the
    cores; the swap with price=entry). max_loss_usd is this x lots, rounded once."""
    e, s = _check_stop_side(side, entry, stop_loss)
    price_loss = abs(e - s) * _dec_clean(inst["contract_size"]) * loss_usd_per_unit(inst, s, rates, cfg)
    return price_loss + _costs(inst, side, 1, nights, rates, cfg, e)


def max_loss_usd(inst, side, lots, entry, stop_loss, nights, rates, cfg):
    """The loss in USD if the stop fills at its price, all costs in (the spread is in
    the price, a swap credit counts 0), half up to cents once."""
    return _cents(_lots_dec(lots) * max_loss_core(inst, side, entry, stop_loss, nights, rates, cfg))


def net_core(inst, side, entry, stop_loss, take_profit, nights, rates, cfg):
    """Reward after costs over the max loss, per 1 lot, unrounded Decimal (6.8)."""
    e, s = _check_stop_side(side, entry, stop_loss)
    t = _price_dec(take_profit)
    if side == "buy" and not t > e:
        raise ValueError("A buy's target must be above the entry.")
    if side == "sell" and not t < e:
        raise ValueError("A sell's target must be below the entry.")
    reward = abs(t - e) * _dec_clean(inst["contract_size"]) * reward_usd_per_unit(inst, t, rates, cfg)
    reward -= _costs(inst, side, 1, nights, rates, cfg, e)
    return reward / max_loss_core(inst, side, e, s, nights, rates, cfg)


def reward_risk_net(inst, side, entry, stop_loss, take_profit, nights, rates, cfg):
    """Reward after costs over the max loss, per 1 lot, half up to 2 decimals."""
    d = net_core(inst, side, entry, stop_loss, take_profit, nights, rates, cfg)
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pl_core(inst, side, entry, exit_price, nights, rates, cfg):
    """1 lot's P/L after the swap and the swap-free fee but before commission,
    unrounded Decimal. A USD-first pair turns it into USD at 1 / exit_price."""
    side_word(side)
    e, x = _price_dec(entry), _price_dec(exit_price)
    move = (x - e) if side == "buy" else (e - x)
    ccy = _pccy(inst)
    if inst.get("kind") == "fx" and inst.get("base") == "USD" and ccy == inst.get("quote"):
        rate = Decimal(1) / x
    else:
        rate = usd_per_unit(ccy, rates, cfg)
    pl = move * _dec_clean(inst["contract_size"]) * rate
    return pl + _swap(inst, side, 1, nights, rates, cfg, e) - _fee(inst, 1, nights, cfg)


def position_pl_usd(position, bid, inst, rates, cfg):
    """An open position's P/L in USD at the latest Yahoo bid (plan scale): a sell closes
    at the ask. The swap and fee so far are in; the commission is booked apart."""
    side = position["side"]
    exit_price = bid if side == "buy" else ask_of(bid, inst)
    nights = position.get("swap_nights") or 0
    lots = _lots_dec(position["lots"])
    return _cents(lots * _pl_core(inst, side, position["entry"], exit_price, nights, rates, cfg))


def _margin(inst, lots, price, side, cfg, rates):
    """The margin of lots in USD, unrounded Decimal (5.1, per calc_mode). price is the
    order's entry: a buy's ask, a sell's bid."""
    side_word(side)
    n = _lots_dec(lots)
    p = _price_dec(price)
    key = inst.get("key")
    mode = inst.get("calc_mode")
    lev = _dec_clean(inst.get("leverage") or ((cfg or {}).get("account") or {}).get("leverage") or 100)
    im = inst.get("initial_margin")
    im = None if im is None else _dec_clean(im)
    size = _dec_clean(inst["contract_size"])
    if mode not in ("forex", "forex_no_leverage", "cfd", "cfd_leverage", "futures"):
        raise ValueError('%s: calc_mode "%s" is not known. Use forex, forex_no_leverage, cfd, '
                         'cfd_leverage or futures.' % (key, mode))
    if mode == "futures" and im is None:
        raise ValueError("%s uses calc_mode futures. Copy its Initial margin from MetaTrader "
                         "into initial_margin." % key)
    if mode == "forex":
        m = n * (im if im is not None else size) / lev
    elif mode == "forex_no_leverage":
        m = n * (im if im is not None else size)
    elif mode == "cfd":
        m = n * im if im is not None else n * size * p
    elif mode == "cfd_leverage":
        m = n * im / lev if im is not None else n * size * p / lev
    else:
        m = n * im
    m *= _dec_clean(inst.get("margin_rate") or 1)
    return m * _margin_ccy_usd(inst, p, rates, cfg)


def margin_usd(inst, lots, price, side, cfg, rates):
    """The margin of lots in USD per calc_mode, at the order's entry price, x margin_rate,
    in USD, half up to cents. Each position counts its full margin (no hedge credit)."""
    return _cents(_margin(inst, lots, price, side, cfg, rates))


def _floor_lots(raw, inst):
    """Raw lots DOWN to a whole number of lot_step (cleaned to 9 decimals first, D11).
    0 or less gives 0. No min_lot check here."""
    step = _dec_clean(inst["lot_step"])
    if raw <= 0:
        return Decimal(0)
    steps = (raw / step).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    return (steps.to_integral_value(rounding=ROUND_FLOOR) * step).quantize(step.normalize())


def _lot_word(d):
    """Lots as plain text for files and messages: Decimal 0.68 -> "0.68", 100 -> "100"."""
    return format(_dec_clean(d).normalize(), "f")


def _risk_words(inst, min_lot_loss, risk_usd):
    """The 4.4 "by risk" refusal, without the percent line."""
    return ("%s lot is the smallest order for %s. At this stop it loses %s. Your max loss is "
            "%s. So this trade is too big for your account. Do not move the stop closer to "
            "make it fit." % (_lot_word(inst["min_lot"]), inst.get("key"), money(min_lot_loss),
                              money(risk_usd)))


def _loss_per_lot(entry, stop_loss, inst, rates, extra_per_lot_usd, cfg):
    """D13 loss of 1 lot at the stop plus the costs per lot, unrounded Decimal."""
    e, s = _price_dec(entry), _price_dec(stop_loss)
    if e == s:
        raise ValueError("The stop is at the entry price. There is no risk to size.")
    return (abs(e - s) * _dec_clean(inst["contract_size"]) * loss_usd_per_unit(inst, s, rates, cfg)
            + _dec_clean(extra_per_lot_usd))


SIZE_LIMITS = ("risk_pct", "risk_usd", "max_lot", "margin_cap", "free_margin")


def _risk_money(balance, cfg, risk_pct, risk_usd):
    """(pct, cap) for size_order (D22): the flags may only lower the config limits.
    cap is None when no dollar limit is set."""
    risk = (cfg or {}).get("risk") or {}
    pct = _dec_clean(risk.get("max_risk_per_trade_pct", 1.0))
    if risk_pct is not None:
        if _dec_clean(risk_pct) > pct:
            raise ValueError("Your config allows at most %s%% a trade. To go higher, change "
                             "risk.max_risk_per_trade_pct in fx/config.json." % _pct_word(pct))
        pct = _dec_clean(risk_pct)
    cap = risk.get("max_risk_per_trade_usd")
    cap = None if cap is None else _dec_clean(cap)
    if risk_usd is not None:
        if cap is not None and _dec_clean(risk_usd) > cap:
            raise ValueError("Your config allows at most %s a trade. To go higher, change "
                             "risk.max_risk_per_trade_usd in fx/config.json." % money(cap))
        cap = _dec_clean(risk_usd)
    return pct, cap


def size_order(inst, side, entry, stop_loss, nights, balance, used_margin_usd, rates, cfg,
               risk_pct=None, risk_usd=None, equity=None):
    """The lots for one open order: the smallest of the risk limits (D22), max_lot, the
    margin cap and free margin, with decided_by. TooSmall below min_lot (exit 1)."""
    e, s = _check_stop_side(side, entry, stop_loss)
    bal, used = _dec_clean(balance), _dec_clean(used_margin_usd)
    pct, cap = _risk_money(bal, cfg, risk_pct, risk_usd)
    by_pct = bal * pct / 100
    risk_limit = by_pct if cap is None else min(by_pct, cap)
    if risk_limit <= 0:
        raise ValueError("The max loss must be above $0.")
    extra = _costs(inst, side, 1, nights, rates, cfg, e)
    per_lot = _loss_per_lot(e, s, inst, rates, extra, cfg)
    per_margin = _margin(inst, 1, e, side, cfg, rates)
    cap_pct = _dec_clean(((cfg or {}).get("risk") or {}).get("max_margin_used_pct", 30))
    room = bal * cap_pct / 100 - used
    free = (bal if equity is None else _dec_clean(equity)) - used
    limits = {"risk_pct": _floor_lots(by_pct / per_lot, inst),
              "risk_usd": None if cap is None else _floor_lots(cap / per_lot, inst),
              "max_lot": _dec_clean(inst["max_lot"]),
              "margin_cap": _floor_lots(room / per_margin, inst),
              "free_margin": _floor_lots(free / per_margin, inst)}
    decided = None
    for name in SIZE_LIMITS:
        if limits[name] is not None and (decided is None or limits[name] < limits[decided]):
            decided = name
    lots = limits[decided]
    min_lot = _dec_clean(inst["min_lot"])
    min_loss = min_lot * per_lot
    if lots < min_lot:
        if decided in ("risk_pct", "risk_usd"):
            words = _risk_words(inst, min_loss, risk_limit)
            if decided == "risk_pct":
                words += " At %s%% a trade you need about %s for this trade." % (
                    _pct_word(pct), money(min_loss / pct * 100))
        elif decided == "margin_cap":
            words = ("%s lot of %s needs %s margin. You have room for %s (%s%% of your balance, "
                     "minus margin in use). So this trade does not fit."
                     % (_lot_word(min_lot), inst.get("key"), money(min_lot * per_margin),
                        money(max(room, Decimal(0))), _num_word(cap_pct)))
        else:
            words = ("%s lot of %s needs %s margin. Your free margin is %s. So this trade does "
                     "not fit." % (_lot_word(min_lot), inst.get("key"), money(min_lot * per_margin),
                                   money(max(free, Decimal(0)))))   # (check) words
        raise TooSmall(words, min_lot=float(min_lot), min_lot_loss_usd=_cents(min_loss),
                       risk_limit_usd=_cents(risk_limit), decided_by=decided)
    return {"lots": lots, "decided_by": decided, "risk_limit_usd": _cents(risk_limit),
            "max_loss_usd": _cents(lots * per_lot), "loss_per_lot_usd": _cents(per_lot),
            "min_lot_loss_usd": _cents(min_loss), "margin_usd": _cents(lots * per_margin),
            "margin_per_lot_usd": _cents(per_margin),
            "limits": dict((k, None if v is None else _lot_word(v)) for k, v in limits.items())}


MIN_DAY_MINUTES_LEFT = 15


def _setup(name, side, style, words, min_bars, listed, stop):
    """One row of SETUPS as a dict."""
    return {"name": name, "side": side, "style": style, "words": words,
            "min_bars": min_bars, "listed": listed, "stop": stop}


SETUPS = (
    _setup("breakout_20d", "buy", "swing", "20-day high breakout", 60, "near", "atr:2"),
    _setup("breakdown_20d", "sell", "swing", "20-day low breakdown", 60, "near", "atr:2"),
    _setup("breakout_55d", "buy", "swing", "55-day high breakout", 60, "near", "atr:2"),
    _setup("breakdown_55d", "sell", "swing", "55-day low breakdown", 60, "near", "atr:2"),
    _setup("pullback_ema20", "buy", "swing", "pullback to a rising 20-day EMA", 100,
           "pattern", "swing-low"),
    _setup("rally_ema20", "sell", "swing", "rally to a falling 20-day EMA", 100,
           "pattern", "swing-high"),
    _setup("bull_flag", "buy", "swing", "bull flag (ATR rule)", 60, "pattern", "flag-far"),
    _setup("bear_flag", "sell", "swing", "bear flag (ATR rule)", 60, "pattern", "flag-far"),
    _setup("reclaim_ma20", "buy", "swing", "back over the 20-day average", 60,
           "pattern", "swing-low"),
    _setup("lose_ma20", "sell", "swing", "back under the 20-day average", 60,
           "pattern", "swing-high"),
    _setup("oversold_bounce", "buy", "swing", "oversold bounce (RSI 14 was under 30)", 60,
           "pattern", "swing-low"),
    _setup("overbought_drop", "sell", "swing", "overbought drop (RSI 14 was over 70)", 60,
           "pattern", "swing-high"),
    _setup("inside_break_up", "buy", "swing", "inside day and NR7, upper side", 60,
           "pattern", "bar-far"),
    _setup("inside_break_down", "sell", "swing", "inside day and NR7, lower side", 60,
           "pattern", "bar-far"),
    _setup("or_breakout", "buy", "day",
           "opening-range breakout (a close above the range high)", 0, "near", "or-mid"),
    _setup("or_breakdown", "sell", "day",
           "opening-range breakdown (a close below the range low)", 0, "near", "or-mid"),
)


CONFIG_NOTE = "No fx/config.json yet. Using the example values. Copy your own from MetaTrader."


SECRET_KEYS = ("password", "login", "investor_password", "master_password", "account_number")


MERGED_KEYS = ("account", "risk", "swing", "day", "news")   # merged one level deep; the rest replaced whole


D15_DAYS = (("2026-01-14", "US winter"), ("2026-07-15", "US summer"))   # the two test FX days of 01 3.3


KINDS = ("fx", "metal", "energy")


PRICE_SOURCES = ("spot_fx", "futures")


CALC_MODES = ("forex", "forex_no_leverage", "cfd", "cfd_leverage", "futures")


SWAP_MODES = ("points", "deposit", "base", "margin", "profit", "interest", "disabled")


TRIPLE_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "none")


PROFIT_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")   # the rates the cache has


FUTURES_KEYS = ("root", "suffix", "cycle", "roll_rule", "settle", "shortname_month", "history_check")


FUTURES_SUFFIXES = (".CMX", ".NYM")


FUTURES_MONTH_LETTERS = "".join(MONTH_CODES.values())   # FGHJKMNQUVXZ, Jan to Dec (part 02)


ROLL_RULES = ("gc_fnd", "si_hg", "cl_ltd", "bz_ltd", "ng_ltd")   # part 02 section 5 (check)


SWING_STOP_RULES = ("swing-low", "swing-high", "flag-far", "bar-far", "setup")   # part 05 8


DAY_STOP_RULES = ("or-mid", "or-far", "setup")


SESSION_WORDS = {"london": "London", "new_york": "New York", "tokyo": "Tokyo"}


NEEDED_COLUMNS = ("name", "kind", "yahoo", "price_source", "base", "quote", "profit_currency",
                  "margin_currency", "contract_size", "digits", "min_lot", "max_lot", "lot_step",
                  "calc_mode", "spread_points", "swap_mode", "swap_long", "swap_short",
                  "swap_triple_day", "hours", "data_hours", "group")   # plus futures for a futures row


COLUMN_DEFAULTS = {"pip": None, "margin_rate": 1.0, "leverage": None, "initial_margin": None,
                   "commission_per_lot_side": 0, "swap_free_fee_per_lot": None,
                   "swap_free_grace_nights": 0, "stops_level_points": 0, "yahoo_to_broker": 1.0,
                   "futures": None}   # a new key's columns that are not needed (01 4.1)


BUILTIN_INSTRUMENTS = {
    "EURUSD": {
        "name": "Euro / US dollar", "kind": "fx", "yahoo": "EURUSD=X", "price_source": "spot_fx",
        "base": "EUR", "quote": "USD", "pip": 0.0001, "hours": "fx", "data_hours": "yahoo_fx",
        "group": "usd_majors", "broker_symbol": "EURUSD", "contract_size": 100000, "digits": 5,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 1,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": -6.23,
        "swap_short": 3.1, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "GBPUSD": {
        "name": "British pound / US dollar", "kind": "fx", "yahoo": "GBPUSD=X",
        "price_source": "spot_fx", "base": "GBP", "quote": "USD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "GBPUSD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 3, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": -1.63, "swap_short": -1.37, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": None, "swap_free_grace_nights": 0, "stops_level_points": 0,
        "profit_currency": "USD", "margin_currency": "GBP", "yahoo_to_broker": 1.0,
        "futures": None},
    "USDJPY": {
        "name": "US dollar / Japanese yen", "kind": "fx", "yahoo": "USDJPY=X",
        "price_source": "spot_fx", "base": "USD", "quote": "JPY", "pip": 0.01, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "jpy", "broker_symbol": "USDJPY",
        "contract_size": 100000, "digits": 3, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 1, "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 6.9,
        "swap_short": -13.93, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 10,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "JPY",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": None},
    "USDCHF": {
        "name": "US dollar / Swiss franc", "kind": "fx", "yahoo": "USDCHF=X",
        "price_source": "spot_fx", "base": "USD", "quote": "CHF", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "USDCHF",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 4, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": 6.23, "swap_short": -10.07, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": 10, "swap_free_grace_nights": 6, "stops_level_points": 0,
        "profit_currency": "CHF", "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": None},
    "AUDUSD": {
        "name": "Australian dollar / US dollar", "kind": "fx", "yahoo": "AUDUSD=X",
        "price_source": "spot_fx", "base": "AUD", "quote": "USD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "AUDUSD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 1, "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 0.7,
        "swap_short": -2.3, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "AUD", "yahoo_to_broker": 1.0,
        "futures": None},
    "USDCAD": {
        "name": "US dollar / Canadian dollar", "kind": "fx", "yahoo": "USDCAD=X",
        "price_source": "spot_fx", "base": "USD", "quote": "CAD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "USDCAD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 2, "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 4.4,
        "swap_short": -7.5, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "CAD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": None},
    "NZDUSD": {
        "name": "New Zealand dollar / US dollar", "kind": "fx", "yahoo": "NZDUSD=X",
        "price_source": "spot_fx", "base": "NZD", "quote": "USD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "NZDUSD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 3, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": -3.12, "swap_short": 1.37, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": None, "swap_free_grace_nights": 0, "stops_level_points": 0,
        "profit_currency": "USD", "margin_currency": "NZD", "yahoo_to_broker": 1.0,
        "futures": None},
    "EURJPY": {
        "name": "Euro / Japanese yen", "kind": "fx", "yahoo": "EURJPY=X", "price_source": "spot_fx",
        "base": "EUR", "quote": "JPY", "pip": 0.01, "hours": "fx", "data_hours": "yahoo_fx",
        "group": "jpy", "broker_symbol": "EURJPY", "contract_size": 100000, "digits": 3,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 5,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 4.56,
        "swap_short": -9.21, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 5,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "JPY",
        "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "GBPJPY": {
        "name": "British pound / Japanese yen", "kind": "fx", "yahoo": "GBPJPY=X",
        "price_source": "spot_fx", "base": "GBP", "quote": "JPY", "pip": 0.01, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "jpy", "broker_symbol": "GBPJPY",
        "contract_size": 100000, "digits": 3, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 10, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": 9.07, "swap_short": -21.8, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": 10, "swap_free_grace_nights": 6, "stops_level_points": 0,
        "profit_currency": "JPY", "margin_currency": "GBP", "yahoo_to_broker": 1.0,
        "futures": None},
    "EURGBP": {
        "name": "Euro / British pound", "kind": "fx", "yahoo": "EURGBP=X",
        "price_source": "spot_fx", "base": "EUR", "quote": "GBP", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "eur_crosses", "broker_symbol": "EURGBP",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 4, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": -4.31, "swap_short": 2.12, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": None, "swap_free_grace_nights": 0, "stops_level_points": 0,
        "profit_currency": "GBP", "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "AUDJPY": {
        "name": "Australian dollar / Japanese yen", "kind": "fx", "yahoo": "AUDJPY=X",
        "price_source": "spot_fx", "base": "AUD", "quote": "JPY", "pip": 0.01, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "jpy", "broker_symbol": "AUDJPY",
        "contract_size": 100000, "digits": 3, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 9, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": 6.58, "swap_short": -12.78, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": 5, "swap_free_grace_nights": 6, "stops_level_points": 0,
        "profit_currency": "JPY", "margin_currency": "AUD", "yahoo_to_broker": 1.0,
        "futures": None},
    "EURCHF": {
        "name": "Euro / Swiss franc", "kind": "fx", "yahoo": "EURCHF=X", "price_source": "spot_fx",
        "base": "EUR", "quote": "CHF", "pip": 0.0001, "hours": "fx", "data_hours": "yahoo_fx",
        "group": "eur_crosses", "broker_symbol": "EURCHF", "contract_size": 100000, "digits": 5,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 9,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 4.43,
        "swap_short": -9.99, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 10,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "CHF",
        "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "XAUUSD": {
        "name": "Gold", "kind": "metal", "yahoo": "GC=F", "price_source": "futures", "base": "XAU",
        "quote": "USD", "pip": None, "hours": "comex", "data_hours": "cme", "group": "metals",
        "broker_symbol": "XAUUSD", "contract_size": 100, "digits": 2, "min_lot": 0.01,
        "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 9,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": -54.84,
        "swap_short": 38.52, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 45,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "GC", "suffix": ".CMX", "cycle": "GJMQZ",
                    "roll_rule": "gc_fnd", "settle": ["ny", "13:30"],
                    "shortname_month": True, "history_check": True}},
    "XAGUSD": {
        "name": "Silver", "kind": "metal", "yahoo": "SI=F", "price_source": "futures",
        "base": "XAG", "quote": "USD", "pip": None, "hours": "comex", "data_hours": "cme",
        "group": "metals", "broker_symbol": "XAGUSD", "contract_size": 5000, "digits": 3,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": None, "initial_margin": None, "spread_points": 21,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": -8.21,
        "swap_short": 3.99, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 15,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "SI", "suffix": ".CMX", "cycle": "HKNUZ",
                    "roll_rule": "si_hg", "settle": ["ny", "13:25"],
                    "shortname_month": True, "history_check": False}},
    "XPTUSD": {
        "name": "Platinum", "kind": "metal", "yahoo": "PL=F", "price_source": "futures",
        "base": "XPT", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "metals", "broker_symbol": "XPTUSD", "contract_size": 100, "digits": 2,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": None, "initial_margin": None, "spread_points": 420,
        "commission_per_lot_side": 3.0, "swap_mode": "interest", "swap_long": -4.78,
        "swap_short": 3.36, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "PL", "suffix": ".NYM", "cycle": "FJNV",
                    "roll_rule": "gc_fnd", "settle": ["ny", "13:05"],
                    "shortname_month": True, "history_check": True}},
    "XPDUSD": {
        "name": "Palladium", "kind": "metal", "yahoo": "PA=F", "price_source": "futures",
        "base": "XPD", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "metals", "broker_symbol": "XPDUSD", "contract_size": 100, "digits": 2,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": None, "initial_margin": None, "spread_points": 1640,
        "commission_per_lot_side": 3.0, "swap_mode": "interest", "swap_long": -4.78,
        "swap_short": 3.36, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "PA", "suffix": ".NYM", "cycle": "HMUZ",
                    "roll_rule": "gc_fnd", "settle": ["ny", "13:00"],
                    "shortname_month": True, "history_check": True}},
    "COPPER": {
        "name": "Copper", "kind": "metal", "yahoo": "HG=F", "price_source": "futures",
        "base": "XCU", "quote": "USD", "pip": None, "hours": "comex", "data_hours": "cme",
        "group": "industrial", "broker_symbol": "COPPER", "contract_size": 1000, "digits": 4,
        "min_lot": 1, "max_lot": 250, "lot_step": 1, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": 50, "initial_margin": None, "spread_points": 21,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": -16.03,
        "swap_short": 6.19, "swap_triple_day": "friday", "swap_free_fee_per_lot": 1,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "HG", "suffix": ".CMX", "cycle": "HKNUZ",
                    "roll_rule": "si_hg", "settle": ["ny", "13:00"],
                    "shortname_month": True, "history_check": False}},
    "USOIL": {
        "name": "WTI crude oil", "kind": "energy", "yahoo": "CL=F", "price_source": "futures",
        "base": "WTI", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "energy", "broker_symbol": "USOIL", "contract_size": 1, "digits": 2, "min_lot": 1,
        "max_lot": 10000, "lot_step": 1, "calc_mode": "cfd_leverage", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 4,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": 0.78,
        "swap_short": -1.39, "swap_triple_day": "friday", "swap_free_fee_per_lot": 0.1,
        "swap_free_grace_nights": 3, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "CL", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ",
                    "roll_rule": "cl_ltd", "settle": ["ny", "14:30"],
                    "shortname_month": True, "history_check": True}},
    "UKOIL": {
        "name": "Brent crude oil", "kind": "energy", "yahoo": "BZ=F", "price_source": "futures",
        "base": "BRENT", "quote": "USD", "pip": None, "hours": "ice_brent", "data_hours": "cme",
        "group": "energy", "broker_symbol": "UKOIL", "contract_size": 1, "digits": 2, "min_lot": 1,
        "max_lot": 10000, "lot_step": 1, "calc_mode": "cfd_leverage", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 4,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": 2.27,
        "swap_short": -3.17, "swap_triple_day": "friday", "swap_free_fee_per_lot": 0.1,
        "swap_free_grace_nights": 3, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "BZ", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ",
                    "roll_rule": "bz_ltd", "settle": ["london", "19:30"],
                    "shortname_month": False, "history_check": True}},
    "NATGAS": {
        "name": "Natural gas", "kind": "energy", "yahoo": "NG=F", "price_source": "futures",
        "base": "NG", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "natgas", "broker_symbol": "NATGAS", "contract_size": 10000, "digits": 3,
        "min_lot": 0.01, "max_lot": 50, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": 20, "initial_margin": None, "spread_points": 4,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": -8.99,
        "swap_short": 5.31, "swap_triple_day": "friday", "swap_free_fee_per_lot": 80,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "NG", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ",
                    "roll_rule": "ng_ltd", "settle": ["ny", "14:30"],
                    "shortname_month": True, "history_check": True}},
}


DEFAULTS = {
    "account": {"currency": "USD", "balance": 1000, "leverage": 100, "margin_call_pct": 100,
                "stop_out_pct": 50, "swap_free": False, "server_time": "ny+7", "platform": "mt5",
                "hedging": True},
    "paper": True,
    "yahoo": True,
    "allow_short": True,
    "style": "swing",
    "day_end": "17:00 New York",
    "universe": list(BUILTIN_INSTRUMENTS),
    "day_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "risk": {"max_risk_per_trade_pct": 1.0, "max_risk_per_trade_usd": None,
             "max_daily_loss_pct": 3.0, "max_daily_loss_usd": None, "max_open_trades": 3,
             "max_trades_per_day": 3, "max_orders_per_day": 10, "max_group_risk_pct": 2.0,
             "max_currency_risk_pct": 2.0, "min_reward_risk": 1.5, "require_stop": True,
             "max_margin_used_pct": 30, "min_margin_level_pct": 300, "blocked": []},
    "swing": {"stop": "atr:2", "target": "2R", "entry": "market", "max_hold_days": 20,
              "friday_cutoff_ny": "12:00"},
    "day": {"or_session": "london", "or_minutes": 15, "stop": "or-mid", "target": "2R",
            "no_entries_after": "21:00", "flat_by": "23:30"},
    "news": {"quiet_before_min": 30, "quiet_after_min": 30, "impacts": ["High"], "feeds": [
        {"name": "fxstreet", "url": "https://www.fxstreet.com/rss/news"},
        {"name": "investinglive", "url": "https://investinglive.com/feed/"},
        {"name": "investing-commodities", "url": "https://www.investing.com/rss/news_11.rss"},
        {"name": "investing-forex", "url": "https://www.investing.com/rss/news_1.rss"},
        {"name": "oilprice", "url": "https://oilprice.com/rss/main"},
        {"name": "cnbc-energy",
         "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768"},
        {"name": "fed-monetary", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml"},
        {"name": "ecb-press", "url": "https://www.ecb.europa.eu/rss/press.html"},
        {"name": "boe-news", "url": "https://www.bankofengland.co.uk/rss/news"},
        {"name": "boj-news", "url": "https://www.boj.or.jp/en/rss/whatsnew.xml"}]},
    "calendar_url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "holidays": {},
    "early_closes": {},
    "fx_rates_max_age_hours": 24,
    "instruments": BUILTIN_INSTRUMENTS,
}


def _cfg_get(cfg, key):
    """The value at a dotted key ("risk.max_open_trades"); None when a part is missing."""
    v = cfg
    for part in key.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v


def _cfg_put(cfg, key, value):
    """Set the value at a dotted key (the parents must exist)."""
    parts = key.split(".")
    for part in parts[:-1]:
        cfg = cfg[part]
    cfg[parts[-1]] = value


def _is_number(x):
    """True for a JSON int or float that is finite; never for a bool."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _num_text(x):
    """A range end for a message: 100000000 -> "100,000,000"; 0.01 -> "0.01"."""
    if float(x) == int(x):
        return "{:,}".format(int(x))
    return "%g" % x


def _is_hhmm(text):
    """True for "HH:MM": two digits, a colon, two digits, 00:00 to 23:59."""
    return isinstance(text, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text) is not None


def _cfg_bad(path, key, want, value):
    """The generic message: <path>: "<key>" must be <want> (you have <value>)."""
    return ValueError('%s: "%s" must be %s (you have %s)' % (path, key, want, json.dumps(value)))


def _cfg_bool(cfg, key, path):
    """The value at key, which must be JSON true or false."""
    v = _cfg_get(cfg, key)
    if not isinstance(v, bool):
        raise _cfg_bad(path, key, "true or false", v)
    return v


def _cfg_number(cfg, key, lo, hi, path, null_ok=False):
    """The value at key, a number from lo to hi (or null when null_ok)."""
    v = _cfg_get(cfg, key)
    if v is None and null_ok:
        return None
    if not _is_number(v) or not lo <= v <= hi:
        raise _cfg_bad(path, key, "a number from %s to %s" % (_num_text(lo), _num_text(hi)), v)
    return v


def _cfg_int(cfg, key, lo, hi, path):
    """The value at key, a whole number from lo to hi. 3.0 is stored back as 3."""
    v = _cfg_get(cfg, key)
    if not _is_number(v) or v != int(v) or not lo <= v <= hi:
        raise _cfg_bad(path, key, "a whole number from %s to %s" % (_num_text(lo), _num_text(hi)), v)
    _cfg_put(cfg, key, int(v))
    return int(v)


def _cfg_choice(cfg, key, choices, path):
    """The value at key, which must be one of choices."""
    v = _cfg_get(cfg, key)
    if not isinstance(v, str) or v not in choices:
        raise _cfg_bad(path, key, "one of " + ", ".join(choices), v)
    return v


def _cfg_hhmm(cfg, key, path):
    """The value at key, which must be "HH:MM"."""
    v = _cfg_get(cfg, key)
    if not _is_hhmm(v):
        raise ValueError('%s: "%s" must be a time like 23:30 (you have %s)' % (path, key, json.dumps(v)))
    return v


def _cfg_keys(cfg, key, path, at_least=1, repeats=False):
    """The list at key: instrument keys of cfg["instruments"], at least at_least of them."""
    v = _cfg_get(cfg, key)
    if not isinstance(v, list) or len(v) < at_least or not all(isinstance(x, str) for x in v):
        want = "a list of instrument keys" + (", at least %d" % at_least if at_least else "")
        raise _cfg_bad(path, key, want, v)
    seen = set()
    for sym in v:
        if sym not in cfg["instruments"]:
            raise ValueError('%s: "%s": "%s" is not in your instrument list' % (path, key, sym))
        if sym in seen and not repeats:
            raise ValueError('%s: "%s" lists "%s" twice' % (path, key, sym))
        seen.add(sym)
    return v


def _rule_k(text, form):
    """K of "atr:K" (form "atr") or "KR" (form "R") when K is 0.5 to 10; else None."""
    pat = r"atr:(\d+(?:\.\d+)?)" if form == "atr" else r"(\d+(?:\.\d+)?)R"
    m = re.fullmatch(pat, text) if isinstance(text, str) else None
    if not m or not 0.5 <= float(m.group(1)) <= 10:
        return None
    return float(m.group(1))


def _is_date(text):
    """True for a real date written "YYYY-MM-DD"."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return False
    return True


def _check_account(cfg, path):
    """The account.* keys of 01 3.2, in table order."""
    if _cfg_get(cfg, "account.currency") != "USD":
        raise ValueError('%s: "account.currency" must be "USD". The pack counts money in US '
                         'dollars only.' % path)
    _cfg_number(cfg, "account.balance", 1, 100000000, path)
    _cfg_number(cfg, "account.leverage", 1, 5000, path)
    call = _cfg_number(cfg, "account.margin_call_pct", 0, 1000, path)
    if _cfg_number(cfg, "account.stop_out_pct", 0, 1000, path) > call:
        raise ValueError('%s: "account.stop_out_pct" must not be above "account.margin_call_pct"' % path)
    _cfg_bool(cfg, "account.swap_free", path)
    server = _cfg_get(cfg, "account.server_time")
    try:
        if not isinstance(server, str):
            raise ValueError(SERVER_TIME_BAD)
        server_offset(server)
    except ValueError:
        raise ValueError("%s: %s" % (path, SERVER_TIME_BAD))
    platform = _cfg_choice(cfg, "account.platform", ("mt5", "mt4"), path)
    if not _cfg_bool(cfg, "account.hedging", path) and platform == "mt4":
        raise ValueError('%s: MT4 accounts always hedge. Set "account.hedging" to true.' % path)


def _check_top(cfg, path):
    """paper, yahoo, allow_short, style, day_end, universe and day_universe."""
    for key in ("paper", "yahoo", "allow_short"):
        _cfg_bool(cfg, key, path)
    _cfg_choice(cfg, "style", ("swing", "day"), path)
    _cfg_choice(cfg, "day_end", DAY_ENDS, path)
    _cfg_keys(cfg, "universe", path)
    _cfg_keys(cfg, "day_universe", path)


def _check_risk(cfg, path):
    """The risk.* keys of 01 3.2, in table order (after the account keys)."""
    below = '%s: "%s" is below the risk of one trade. No trade could pass.'
    pct = _cfg_number(cfg, "risk.max_risk_per_trade_pct", 0.01, 10, path)
    usd = _cfg_number(cfg, "risk.max_risk_per_trade_usd", 1, 1000000, path, null_ok=True)
    if _cfg_number(cfg, "risk.max_daily_loss_pct", 0.01, 30, path) < pct:
        raise ValueError(below % (path, "risk.max_daily_loss_pct"))
    daily_usd = _cfg_number(cfg, "risk.max_daily_loss_usd", 1, 1000000, path, null_ok=True)
    if daily_usd is not None and usd is not None and daily_usd < usd:
        raise ValueError(below % (path, "risk.max_daily_loss_usd"))
    _cfg_int(cfg, "risk.max_open_trades", 1, 50, path)
    per_day = _cfg_int(cfg, "risk.max_trades_per_day", 1, 100, path)
    if _cfg_int(cfg, "risk.max_orders_per_day", 1, 200, path) < per_day:
        raise ValueError('%s: "risk.max_orders_per_day" must be at least "risk.max_trades_per_day"' % path)
    _cfg_number(cfg, "risk.max_group_risk_pct", 0.01, 30, path)
    _cfg_number(cfg, "risk.max_currency_risk_pct", 0.01, 30, path)
    _cfg_number(cfg, "risk.min_reward_risk", 0, 10, path)
    _cfg_bool(cfg, "risk.require_stop", path)
    _cfg_number(cfg, "risk.max_margin_used_pct", 1, 100, path)
    if _cfg_number(cfg, "risk.min_margin_level_pct", 0, 100000, path) < cfg["account"]["margin_call_pct"]:
        raise ValueError('%s: "risk.min_margin_level_pct" must be at least the margin call level' % path)
    _cfg_keys(cfg, "risk.blocked", path, at_least=0, repeats=True)


def _cfg_target(cfg, key, path):
    """A target rule "KR" with K from 0.5 to 10 ("1.5R" is fine)."""
    v = _cfg_get(cfg, key)
    if _rule_k(v, "R") is None:
        raise _cfg_bad(path, key, "like 2R, from 0.5R to 10R", v)
    return v


def _cfg_stop(cfg, key, names, path):
    """A stop rule: "atr:K" (K from 0.5 to 10) or one of names."""
    v = _cfg_get(cfg, key)
    if _rule_k(v, "atr") is None and not (isinstance(v, str) and v in names):
        raise _cfg_bad(path, key, "atr:K (K from 0.5 to 10) or one of " + ", ".join(names), v)
    return v


def _check_swing(cfg, path):
    """The swing.* keys of 01 3.2."""
    stop = _cfg_get(cfg, "swing.stop")
    if isinstance(stop, str) and stop.startswith("price:"):
        raise ValueError('%s: "swing.stop" cannot be a fixed price. Give a price with --stop on '
                         'the command line.' % path)
    _cfg_stop(cfg, "swing.stop", SWING_STOP_RULES, path)
    _cfg_target(cfg, "swing.target", path)
    _cfg_choice(cfg, "swing.entry", ("market", "limit"), path)
    _cfg_int(cfg, "swing.max_hold_days", 1, 120, path)
    if _cfg_hhmm(cfg, "swing.friday_cutoff_ny", path) >= "17:00":
        raise ValueError('%s: "swing.friday_cutoff_ny" must be before 17:00 New York (the week '
                         'ends then)' % path)


def _check_day(cfg, path):
    """The day.* keys of 01 3.2, then the D15 check of 01 3.3."""
    _cfg_choice(cfg, "day.or_session", ("london", "new_york", "tokyo"), path)
    m = _cfg_get(cfg, "day.or_minutes")
    if _is_number(m) and m == int(m) and 5 <= m <= 120 and int(m) % 5:
        raise ValueError('%s: "day.or_minutes" must be a multiple of 5 (the bars are 5 minutes)' % path)
    _cfg_int(cfg, "day.or_minutes", 5, 120, path)
    _cfg_stop(cfg, "day.stop", DAY_STOP_RULES, path)
    _cfg_target(cfg, "day.target", path)
    _cfg_hhmm(cfg, "day.no_entries_after", path)
    _cfg_hhmm(cfg, "day.flat_by", path)
    _check_day_times(cfg, path)


def _pkt_hhmm(utc):
    """naive UTC -> "03:00 PKT" (the Karachi clock)."""
    return to_pkt(utc).strftime("%H:%M") + " PKT"


def _check_day_times(cfg, path):
    """The D15 check (01 3.3): day.flat_by and day.no_entries_after on a US winter and a
    US summer FX day, rules 1 to 4 in order. Uses the part 03 time helpers."""
    day_end, day = cfg["day_end"], cfg["day"]
    flat, last = day["flat_by"], day["no_entries_after"]
    q = datetime.timedelta(minutes=15)
    for t, season in D15_DAYS:
        s, e = fx_day_bounds(t, day_end)
        r = e if day_end == "17:00 New York" else ny_to_utc(at_time(parse_fx_date(t), "17:00"))
        f = pkt_time_on_fx_day(flat, t, day_end)
        n = pkt_time_on_fx_day(last, t, day_end)
        or_end = opening_range_window(day["or_session"], t, day["or_minutes"])[1]
        if f < s + datetime.timedelta(minutes=60):
            raise ValueError('%s: "day.flat_by" %s falls in the first hour of the FX day in %s '
                             '(that hour starts at %s). Pick a time like 23:30.'
                             % (path, flat, season, _pkt_hhmm(s)))
        if f > r - q:
            raise ValueError('%s: "day.flat_by" %s is too close to the 17:00 New York rollover in '
                             '%s (%s). Pick 01:45 or earlier.' % (path, flat, season, _pkt_hhmm(r)))
        if n > f - q:
            raise ValueError('%s: "day.no_entries_after" %s must be at least 15 minutes before '
                             '"day.flat_by" %s' % (path, last, flat))
        if n <= or_end:
            raise ValueError('%s: "day.no_entries_after" %s is before the %s opening range ends '
                             '(%s in %s). No day trade could start.'
                             % (path, last, SESSION_WORDS[day["or_session"]], _pkt_hhmm(or_end), season))


def _check_news(cfg, path):
    """The news.* keys and calendar_url of 01 3.2."""
    _cfg_int(cfg, "news.quiet_before_min", 0, 240, path)
    _cfg_int(cfg, "news.quiet_after_min", 0, 240, path)
    imp = _cfg_get(cfg, "news.impacts")
    if not isinstance(imp, list) or not imp or not all(x in ("High", "Medium", "Low") for x in imp):
        raise _cfg_bad(path, "news.impacts", "some of High, Medium, Low", imp)
    feeds = _cfg_get(cfg, "news.feeds")
    bad = '%s: "news.feeds": each feed needs a "name" (letters, digits, -) and a "url"' % path
    if not isinstance(feeds, list):
        raise ValueError(bad)
    names = set()
    for feed in feeds:
        name = feed.get("name") if isinstance(feed, dict) else None
        url = feed.get("url") if isinstance(feed, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]{1,40}", name):
            raise ValueError(bad)
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError(bad)
        if name in names:
            raise ValueError('%s: "news.feeds" lists "%s" twice' % (path, name))
        names.add(name)
    url = cfg.get("calendar_url")
    if not isinstance(url, str) or (url and not url.startswith("https://")):
        raise ValueError('%s: "calendar_url" must start with https:// or be "" (off)' % path)


def _check_dates(cfg, path):
    """holidays, early_closes and fx_rates_max_age_hours of 01 3.2."""
    hol = cfg.get("holidays")
    if not isinstance(hol, dict) or not all(_is_date(k) and isinstance(v, str) for k, v in hol.items()):
        raise ValueError('%s: "holidays" wants {"2026-12-25": "Christmas"}' % path)
    early = cfg.get("early_closes")
    if not isinstance(early, dict) or not all(_is_date(k) and _is_hhmm(v) for k, v in early.items()):
        raise ValueError('%s: "early_closes" wants {"2026-11-27": "13:45"} (New York time)' % path)
    for d in sorted(early):
        if early[d] >= "17:00":
            raise ValueError('%s: "early_closes" %s %s must be before 17:00 New York (the day '
                             'ends then)' % (path, d, early[d]))
    _cfg_number(cfg, "fx_rates_max_age_hours", 1, 168, path)


def _plain(v):
    """A value for a message: numbers without exponents (0.00001, not 1e-05); else JSON."""
    if _is_number(v):
        return format(Decimal(repr(v)), "f")
    return json.dumps(v)


def _row_bad(col, want, v):
    """The generic column message (the caller adds the path and the key)."""
    return ValueError('"%s" must be %s (you have %s)' % (col, want, _plain(v)))


def _row_number(row, col, lo, hi=None, null_ok=False, lo_open=False):
    """row[col], a number from lo to hi (above lo when lo_open; no top when hi is None)."""
    v = row.get(col)
    if v is None and null_ok:
        return None
    low_ok = _is_number(v) and (v > lo if lo_open else v >= lo)
    if not low_ok or (hi is not None and v > hi):
        want = ("a number above %s" if lo_open else "a number from %s") % _num_text(lo)
        if hi is not None:
            want += (", up to %s" if lo_open else " to %s") % _num_text(hi)
        raise _row_bad(col, want, v)
    return v


def _row_int(row, col, lo, hi):
    """row[col], a whole number from lo to hi. 3.0 is stored back as 3."""
    v = row.get(col)
    if not _is_number(v) or v != int(v) or not lo <= v <= hi:
        raise _row_bad(col, "a whole number from %s to %s" % (_num_text(lo), _num_text(hi)), v)
    row[col] = int(v)
    return int(v)


def _row_choice(row, col, choices):
    """row[col], which must be one of choices."""
    v = row.get(col)
    if not isinstance(v, str) or v not in choices:
        raise _row_bad(col, "one of " + ", ".join(choices), v)
    return v


def _inst_size(row):
    """kind, price_source, digits and point, tick_size, contract_size and the lots (01 4.6)."""
    _row_choice(row, "kind", KINDS)
    _row_choice(row, "price_source", PRICE_SOURCES)
    digits = _row_int(row, "digits", 0, 8)
    point = Decimal(1).scaleb(-digits)
    p = row.get("point")
    if p is not None and (not _is_number(p) or _dec_clean(p) != point):
        raise ValueError('"point" %s does not match "digits" %d (that gives %s)'
                         % (_plain(p), digits, "%.*f" % (digits, point)))
    tick = row.get("tick_size")
    if tick is not None:
        steps = _dec_clean(tick) / point if _is_number(tick) else Decimal("0.5")
        if steps != int(steps) or not 1 <= steps <= 1000:
            raise ValueError('"tick_size" must be a whole number of points')
    if not _is_number(row.get("contract_size")) or row["contract_size"] <= 0:
        raise ValueError('"contract_size" must be above 0. Copy "Contract size" from MetaTrader.')
    lo = _row_number(row, "min_lot", 0, lo_open=True)
    hi = _row_number(row, "max_lot", 0, lo_open=True)
    step = _row_number(row, "lot_step", 0, lo_open=True)
    if lo > hi:
        raise ValueError('"min_lot" must not be above "max_lot"')
    if _dec_clean(lo) % _dec_clean(step) != 0:
        raise ValueError('"min_lot" %s is not a whole number of "lot_step" %s' % (_plain(lo), _plain(step)))


def _inst_money(row):
    """calc_mode, swaps, hours, spread, commission, fees, leverage, margin and the unit
    factor (01 4.6), in table order."""
    _row_choice(row, "calc_mode", CALC_MODES)
    if row.get("swap_mode") not in SWAP_MODES:
        raise ValueError('"swap_mode" must be one of ' + ", ".join(SWAP_MODES))
    if row.get("swap_triple_day") not in TRIPLE_DAYS:
        raise ValueError('"swap_triple_day" must be monday to friday, or none. MetaTrader charges '
                         'no swap on Saturday or Sunday nights.')
    _row_choice(row, "hours", BROKER_TEMPLATES)
    _row_choice(row, "data_hours", DATA_TEMPLATES)
    spread = row.get("spread_points")
    if _is_number(spread) and 0 < spread < 1:
        raise ValueError('"spread_points" is in points, not price. 0.09 for gold looks like a '
                         'price: use 9.')
    _row_number(row, "spread_points", 0, 100000)
    _row_number(row, "commission_per_lot_side", 0, 1000)
    for col in ("swap_long", "swap_short"):
        if not _is_number(row.get(col)):
            raise ValueError('"%s" must be a number, like -6.23' % col)
    _row_number(row, "swap_free_fee_per_lot", 0, 1000, null_ok=True)
    _row_int(row, "swap_free_grace_nights", 0, 60)
    _row_number(row, "leverage", 1, 5000, null_ok=True)
    _row_number(row, "initial_margin", 0, null_ok=True, lo_open=True)
    _row_number(row, "margin_rate", 0, 100, lo_open=True)
    _row_number(row, "yahoo_to_broker", 0, lo_open=True)
    _row_int(row, "stops_level_points", 0, 100000)   # (check) not in the 4.6 table


def _inst_futures(row):
    """The futures object of a futures row (01 4.5 and 4.6)."""
    fut = row.get("futures")
    if not isinstance(fut, dict) or not all(k in fut for k in FUTURES_KEYS):
        raise ValueError('"futures" needs ' + ", ".join(FUTURES_KEYS))
    if not isinstance(fut["root"], str) or not fut["root"]:
        raise _row_bad("futures.root", 'a Yahoo root like "GC"', fut["root"])
    if fut["suffix"] not in FUTURES_SUFFIXES:
        raise _row_bad("futures.suffix", "one of " + ", ".join(FUTURES_SUFFIXES), fut["suffix"])
    cycle = fut["cycle"]
    if not isinstance(cycle, str) or not cycle or not all(c in FUTURES_MONTH_LETTERS for c in cycle):
        raise _row_bad("futures.cycle", "month letters from " + FUTURES_MONTH_LETTERS, cycle)
    if fut["roll_rule"] not in ROLL_RULES:
        raise _row_bad("futures.roll_rule", "one of " + ", ".join(ROLL_RULES), fut["roll_rule"])
    settle = fut["settle"]
    if not (isinstance(settle, list) and len(settle) == 2 and settle[0] in ("ny", "london")
            and _is_hhmm(settle[1])):
        raise _row_bad("futures.settle", 'like ["ny", "13:30"]', settle)
    for col in ("shortname_month", "history_check"):
        if not isinstance(fut[col], bool):
            raise _row_bad("futures." + col, "true or false", fut[col])


def _check_instrument(key, row, path):
    """One instrument row: the needed columns of a new key (01 4.1), then 01 4.6 in order.
    The message is <path>: instrument "<KEY>": <what>."""
    try:
        if not isinstance(row, dict):
            raise ValueError("must be a JSON object: {...}")
        if key not in BUILTIN_INSTRUMENTS:
            need = NEEDED_COLUMNS + (("futures",) if row.get("price_source") == "futures" else ())
            for col in need:
                if row.get(col) is None:
                    raise ValueError('a new instrument needs "%s". Copy it from MetaTrader '
                                     '(the built-in rows show each column).' % col)
        _inst_size(row)
        _inst_money(row)
        if row["price_source"] == "futures":
            _inst_futures(row)
        if row.get("profit_currency") not in PROFIT_CURRENCIES:
            raise ValueError('"profit_currency" %s has no Yahoo rate in this pack'
                             % json.dumps(row.get("profit_currency")))
        mc = row.get("margin_currency")
        if mc not in PROFIT_CURRENCIES and mc != row.get("base"):
            raise ValueError('"margin_currency" %s must be USD, a currency of this pack or the '
                             'base %s' % (json.dumps(mc), row.get("base")))
    except ValueError as exc:
        raise ValueError('%s: instrument "%s": %s' % (path, key, exc))


def check_config(cfg, path="fx/config.json"):
    """Check a whole config (01 3.2, 3.3, 4.1 and 4.6), in table order. The first bad
    value raises ValueError "<path>: ..."; whole-number values are stored back as ints."""
    if not isinstance(cfg, dict):
        raise ValueError("%s must be a JSON object: {...}" % path)
    for key in MERGED_KEYS + ("instruments",):
        if not isinstance(cfg.get(key), dict):
            raise ValueError('%s: "%s" must be a JSON object: {...}' % (path, key))
    _check_account(cfg, path)
    _check_top(cfg, path)
    _check_risk(cfg, path)
    _check_swing(cfg, path)
    _check_day(cfg, path)
    _check_news(cfg, path)
    _check_dates(cfg, path)
    for key, row in cfg["instruments"].items():
        _check_instrument(key, row, path)
    return cfg


def _find_secret(doc):
    """The first key named like a login or a password (any case) anywhere in doc; else None."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(k, str) and k.lower() in SECRET_KEYS:
                return k
            hit = _find_secret(v)
            if hit is not None:
                return hit
    elif isinstance(doc, list):
        for v in doc:
            hit = _find_secret(v)
            if hit is not None:
                return hit
    return None


def _fill_derived(key, row):
    """Fill broker_symbol (the key), point (10 ** -digits) and tick_size (the point)
    when the row does not give them (01 3.1 step 5)."""
    if isinstance(row, dict) and not row.get("broker_symbol"):
        row["broker_symbol"] = key
    digits = row.get("digits") if isinstance(row, dict) else None
    if not _is_number(digits) or digits != int(digits) or not 0 <= digits <= 8:
        return
    point = round(10 ** -int(digits), int(digits))
    if row.get("point") is None:
        row["point"] = point
    if row.get("tick_size") is None:
        row["tick_size"] = point


def merge_config(doc, path="fx/config.json"):
    """A deep copy of DEFAULTS with the user's file doc on top (01 3.1 steps 1 to 5):
    account, risk, swing, day and news key by key; instruments column by column; the
    rest replaced whole. A new instrument key starts from the column defaults of 01 4.1."""
    cfg = copy.deepcopy(DEFAULTS)
    for key, value in doc.items():
        value = copy.deepcopy(value)
        if key in MERGED_KEYS or key == "instruments":
            if not isinstance(value, dict):
                raise ValueError('%s: "%s" must be a JSON object: {...}' % (path, key))
        if key in MERGED_KEYS:
            cfg[key].update(value)
        elif key == "instruments":
            for sym, row in value.items():
                if not isinstance(row, dict):
                    raise ValueError('%s: instrument "%s": must be a JSON object: {...}' % (path, sym))
                if sym not in cfg["instruments"]:
                    cfg["instruments"][sym] = dict(COLUMN_DEFAULTS)
                cfg["instruments"][sym].update(row)
        else:
            cfg[key] = value
    for sym, row in cfg["instruments"].items():
        _fill_derived(sym, row)
    return cfg


def config_warnings(doc, cfg, path="fx/config.json"):
    """The warning lines of a loaded config (01 3.1 step 7 and 3.2): unknown top-level
    keys of the file, and a UTC+0 server with the default day_end."""
    lines = ['%s: unknown key "%s" (skipped). Check the spelling.' % (path, k)
             for k in doc if k not in DEFAULTS]
    if server_offset(cfg["account"]["server_time"]) == 0 and cfg["day_end"] == DEFAULTS["day_end"]:
        lines.append('Your server clock is UTC+0. Such brokers often end the day at 00:00 UTC. '
                     'Check "day_end".')
    return lines


def load_config(path, required=False):
    """Read the config: DEFAULTS, the file on top, then check_config; warnings go to
    stderr. A missing file gives the DEFAULTS and CONFIG_NOTE, but when required (the
    user typed --config FILE) it is an error (01 1.2). Logins are refused."""
    doc = load_json(path)
    if doc is None and required:
        raise ValueError("no config file at %s (copy config.example.json from the "
                         "fx-budget folder)" % path)
    if doc is None:
        sys.stderr.write(CONFIG_NOTE + "\n")
        return check_config(merge_config({}, path), path)
    if not isinstance(doc, dict):
        raise ValueError("%s must be a JSON object: {...}" % path)
    secret = _find_secret(doc)
    if secret is not None:
        raise ValueError('%s: remove "%s". Never put MetaTrader logins or passwords in the '
                         "pack's files." % (path, secret))
    cfg = check_config(merge_config(doc, path), path)
    for line in config_warnings(doc, cfg, path):
        sys.stderr.write(line + "\n")
    return cfg


# ---- end of the copies



# ---------------------------------------------------------------- fx-budget's own code

TRAPS = """The six traps when you copy your broker's values (MetaTrader: right click the
symbol in Market Watch > Specification):

- Oil lot size. One lot of oil is 1 barrel (Tickmill), 100 barrels (IC, HFM) or 1,000
  barrels (Exness, FxPro). The same 2-dollar move is 2, 200 or 2,000 USD a lot. Copy yours.
- Silver lot size. 5,000 oz at most brokers (Tickmill, Exness, FxPro, XM), but 1,000 oz
  at IC. Copy yours.
- Gold digits. Most brokers show 2 digits (4126.20), some 3 (4126.200). Then one point
  is 0.01 or 0.001, and a swap of -54.84 points is -54.84 or -5.484 USD a lot.
- Swap in points or money. Copy "Swap type" too. -6.23 in points on EURUSD is -6.23 USD
  a lot; -6.23 "in money" means the same here only by chance. Exness web pages give swaps
  in pips, MetaTrader in points (10 times more on 5-digit pairs).
- Triple-swap day. Tickmill: Wednesday for FX, gold and silver; Friday for the rest.
  Exness: none for energies, Thursday for USDCAD. Copy "3-day swap" (MT5 shows a rate per
  weekday; the day with 3 is the triple day).
- Min lot. 0.01 lot of gold is 1 oz; Tickmill oil is 1 barrel a lot but the min is 1
  lot; IC oil min is 0.5 lot (50 barrels). A small account may not fit even one min lot.
"""

PLAN_LEVELS = ["chart_entry", "chart_stop", "entry", "stop_loss", "take_profit"]
POSITION_LEVELS = ["entry", "fill_price", "stop_loss", "take_profit", "stop_at_open"]
COSTS_NOTE_PCT = 25   # costs over this percent of the price loss get a note


class Refused(Exception):
    """A plain "no" of the steps (exit 1). str(e) is the reason line."""


def pct_text(x):
    """A percent for people: "1.0%", "0.5%", "0.25%": at least 1 decimal, at most 2."""
    d = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    text = format(d, "f")
    if "." not in text:
        text += ".0"
    return text + "%"


def fresh_quote(data_dir, paths, inst, now, cfg, replay):
    """latest_price, or a newer prices.json quote of the same Yahoo symbol (not after
    now, never in a replay): {bid, at, age_min, source}. None when there is no price."""
    lp = latest_price(data_dir, inst, now, cfg)
    if replay:
        return lp
    try:
        doc = load_json(paths["prices"]) or {}
        q = (doc.get("quotes") or {}).get(inst["key"]) if isinstance(doc, dict) else None
        if not isinstance(q, dict) or q.get("yahoo") != inst["yahoo"]:
            return lp
        at = parse_utc(q.get("at"))
        bid = q.get("bid")
        if at is None or at > now or isinstance(bid, bool) or not isinstance(bid, (int, float)) or bid <= 0:
            return lp
        if lp is not None and lp.get("at") is not None and at <= lp["at"]:
            return lp
        return {"bid": bid, "yahoo": bid, "at": at, "age_min": max(0.0, (now - at).total_seconds() / 60.0),
                "source": "prices.json", "contract": (lp or {}).get("contract")}
    except ValueError:
        return lp


def roll_line(inst, roll):
    """The once-per-roll words of 02 5.8."""
    root = (inst.get("futures") or {}).get("root")
    word = ROLL_WORDS.get(root, str(inst.get("name") or inst["key"]).lower())
    ratio = Decimal(repr(float(roll["ratio"])))
    pct = ((ratio - 1) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("%s rolled on Yahoo (%s%s%%). Your plan levels moved %s by the same amount. Your "
            "broker's %s did not jump: keep the SL and TP you set in MetaTrader."
            % (word[0].upper() + word[1:], "+" if pct >= 0 else "", pct,
               "up" if ratio >= 1 else "down", inst.get("broker_symbol") or inst["key"]))


def flat_words(utc):
    """"11:30 PM PKT (2:30 PM New York)" for a flat-by time."""
    return "%s PKT (%s)" % (clock_label(to_pkt(utc)), ny_label(utc))


def check_plan(plan, cfg, path):
    """The 01 1.3 and 01 5.2 plan.json checks (exit 2). Returns the instrument row."""
    check_symbol(plan.get("symbol"), cfg, path)
    side = check_side(plan.get("side"), path)
    style = check_style(plan.get("style"), path)
    check_utc(plan, "created_at", path)
    for key in ("entry", "stop_loss", "take_profit"):
        check_price(plan, key, path)
    if plan.get("entry_type") not in ("market", "stop", "limit"):
        raise ValueError('%s: entry_type must be "market", "stop" or "limit", not "%s"'
                         % (path, plan.get("entry_type")))   # (check) words
    inst = instrument(cfg, plan["symbol"])
    e, s, t = (Decimal(str(plan[k])) for k in ("entry", "stop_loss", "take_profit"))
    if (side == "buy" and (s >= e or t <= e)) or (side == "sell" and (s <= e or t >= e)):
        raise ValueError("%s: the stop or the target is on the wrong side of the entry. "
                         "Run fx-exit again." % path)
    if plan.get("risk_points") != to_points(abs(e - s), inst):
        raise ValueError("%s does not add up (risk_points). Run fx-exit again." % path)
    need = "flat_by_utc" if style == "day" else "exit_by"
    if not plan.get(need):
        raise ValueError('%s: a %s plan needs "%s"' % (path, style, need))
    check_utc(plan, need, path)
    return inst


def check_portfolio(doc, cfg, path):
    """The 01 5.2 portfolio.json checks (exit 2). Returns the positions list."""
    def bad(what):
        return ValueError("%s: %s. Fix it by hand or restore a backup." % (path, what))
    acc = doc.get("account") or {}
    if not isinstance(acc, dict) or acc.get("currency", "USD") != "USD":
        raise bad("account.currency must be USD")
    positions = check_objects(doc, "positions", path) if "positions" in doc else []
    seen = set()
    for pos in positions:
        if pos.get("symbol") not in (cfg.get("instruments") or {}):
            raise bad('position %s has the key "%s", which is not in your instruments'
                      % (pos.get("id"), pos.get("symbol")))
        if pos.get("id") in seen:
            raise bad('two positions have the id "%s"' % pos.get("id"))
        seen.add(pos.get("id"))
        check_side(pos.get("side"), path)
        check_lots(pos.get("lots"), path)
        check_price(pos, "entry", path)
    return positions


def roll_list(data_dir, now, replay):
    """load_rolls, without the rolls found after now in a replay (02 5.8)."""
    rolls = load_rolls(data_dir)
    if replay:
        rolls = [r for r in rolls if parse_utc(r.get("found_at")) is None
                 or parse_utc(r["found_at"]) <= now]
    return rolls


def positions_now(portfolio, cfg, rates, data_dir, now, rolls, replay):
    """Step 15: (used_usd, equity_usd, notes) for the open positions. A position with no
    fresh price counts its margin at its entry and its P/L as -max_loss_usd (safe side)."""
    balance = Decimal(str(portfolio["balance"]))
    used, pl, notes = Decimal(0), Decimal(0), []
    for pos in portfolio["positions"]:
        pinst = instrument(cfg, pos["symbol"])
        pos, _ids = rebase(pos, rolls, POSITION_LEVELS, inst=pinst)
        q = latest_price(data_dir, pinst, now, cfg)
        fresh = q is not None and q["age_min"] <= STALE_MINUTES[pinst["price_source"]]
        if fresh:
            price = ask_of(q["bid"], pinst) if pos["side"] == "buy" else q["bid"]
            pl += Decimal(str(position_pl_usd(pos, q["bid"], pinst, rates, cfg)))
        else:
            price = pos["entry"]
            pl -= Decimal(str(pos.get("max_loss_usd") or 0))
            notes.append("No fresh price for your open %s. Its margin is counted at its entry "
                         "and its loss at the stop." % pos["symbol"])   # (check) words
        used += Decimal(str(margin_usd(pinst, pos["lots"], price, pos["side"], cfg, rates)))
    return float(used), float(balance + pl), notes


def fetch_all(data_dir, inst, plan, positions, cfg, now, offline):
    """Step 6 (online only): the 1h refreshes (rates, the plan's key, the positions),
    5m for a day plan, and a roll check for each futures row. Returns the failed list."""
    failed = []
    if offline:
        return failed
    keys = rate_symbols(inst, cfg) + [inst["key"]] + [p["symbol"] for p in positions]
    done = []
    for key in keys:
        if key in done or key not in (cfg.get("instruments") or {}):
            continue
        done.append(key)
        row = instrument(cfg, key)
        intervals = ["1h"] + (["5m"] if key == inst["key"] and plan.get("style") == "day" else [])
        for interval in intervals:
            try:
                refresh(data_dir, row["yahoo"], interval, now, cfg, offline)
            except ValueError:
                if row["yahoo"] not in failed:
                    failed.append(row["yahoo"])
        if row.get("futures") and row["yahoo"] not in failed:
            try:
                check_roll_forward(data_dir, row, cfg, now, offline)
            except ValueError:
                failed.append(row["yahoo"])
    return failed


def no_prices(data_dir, inst):
    """The 06 2.4 "no prices" error."""
    return ValueError("No hourly prices for %s yet (%s). Run without --offline once to get them."
                      % (inst["key"], cache_file(data_dir, inst["yahoo"], "1h")))


def price_name(inst):
    """"gold" for a commodity row, the key for an FX pair."""
    if inst.get("kind") == "fx":
        return inst["key"]
    name = str(inst.get("name") or inst["key"])
    return name[0].lower() + name[1:] if not (len(name) > 1 and name[1].isupper()) else name


def setup_words(code):
    """The setup's words (05 2.2, 3), never the code name; None when not known."""
    for row in SETUPS:
        if row["name"] == code:
            return row["words"]
    return None


def plain_num(x):
    """1.5 -> "1.5", 30 -> "30" (config numbers in words)."""
    return format(Decimal(str(x)).normalize(), "f")


def load_inputs(args, ctx):
    """Steps 1 and 2: the flags, the config, the clock and the plan (into ctx)."""
    for flag, value in (("--risk-pct", args.risk_pct), ("--risk-usd", args.risk_usd)):
        if value is not None and not (math.isfinite(value) and value > 0):
            raise ValueError("%s must be a number above 0." % flag)
    work_dir = os.path.dirname(args.config) if args.config else "fx"
    paths = fx_paths(work_dir, args.data, args.config)
    cfg = load_config(paths["config"], required=args.config is not None)
    now = parse_now(args.now)
    replay = args.now is not None
    ctx.update(paths=paths, cfg=cfg, now=now, replay=replay, data_dir=paths["data"],
               offline=bool(args.offline or replay or not cfg.get("yahoo")),
               today=last_fx_day(now, cfg))
    plan_path = args.plan or paths["plan"]
    plan = load_doc(plan_path, "plan", "fx-exit")
    inst = check_plan(plan, cfg, plan_path)
    ctx.update(plan_path=plan_path, plan0=plan, plan=plan, inst=inst)
    pdoc = load_doc(paths["portfolio"], "portfolio", "fx-broker", required=False)
    positions, balance = [], (cfg.get("account") or {}).get("balance")
    if pdoc is not None:
        positions = check_portfolio(pdoc, cfg, paths["portfolio"])
        bal = (pdoc.get("account") or {}).get("balance")
        if isinstance(bal, (int, float)) and not isinstance(bal, bool):
            balance = bal
    ctx.update(positions=positions, balance=balance)


def plan_gates(ctx):
    """Steps 3 to 5: the plan's day, the side and the blocked list (Refused)."""
    plan, cfg, inst = ctx["plan"], ctx["cfg"], ctx["inst"]
    made = last_fx_day(parse_utc(plan["created_at"]), cfg)
    if made != ctx["today"]:
        raise Refused("This plan was made on %s. Make a new plan today: /fx-exit."
                      % day_label(datetime.date.fromisoformat(made)))
    if plan["side"] == "sell" and not cfg.get("allow_short", True):
        raise Refused("Sells are off in your config (allow_short is false).")
    if inst["key"] in ((cfg.get("risk") or {}).get("blocked") or []):
        raise Refused("%s is on your blocked list." % inst["key"])


def rebase_plan(ctx):
    """Steps 6 and 7: fetch (online only), the rolls, and the plan rebased in memory."""
    inst, cfg, now = ctx["inst"], ctx["cfg"], ctx["now"]
    ctx["failed"] = fetch_all(ctx["data_dir"], inst, ctx["plan"], ctx["positions"], cfg, now,
                              ctx["offline"])
    rolls = roll_list(ctx["data_dir"], now, ctx["replay"])
    plan, applied = rebase(ctx["plan"], rolls, PLAN_LEVELS, inst=inst)
    by_id = dict((r["id"], r) for r in rolls)
    ctx["roll_notes"] = [roll_line(inst, by_id[i]) for i in applied if i in by_id]
    plan["risk_per_unit"] = round_price(abs(Decimal(str(plan["entry"])) - Decimal(str(plan["stop_loss"]))), inst)
    ctx.update(plan=plan, rolls=rolls)


def sizing_entry(ctx):
    """Steps 8 and 9: the fresh price and the sizing entry e (the worse one)."""
    plan, inst, cfg, now = ctx["plan"], ctx["inst"], ctx["cfg"], ctx["now"]
    q = fresh_quote(ctx["data_dir"], ctx["paths"], inst, now, cfg, ctx["replay"])
    if q is None or q.get("bid") is None:
        raise no_prices(ctx["data_dir"], inst)
    limit = STALE_MINUTES[inst["price_source"]]
    bid_now = q["bid"] if q["age_min"] <= limit else None
    notes = []
    e = plan["entry"]
    if bid_now is None:
        notes.append("No fresh %s price (the newest is %s). Sized at the plan's entry. fx-risk "
                     "needs a price under %d min old." % (price_name(inst), age_text(q["age_min"]),
                                                         limit))
    elif plan["entry_type"] == "market":
        if plan["side"] == "buy":
            e = max(Decimal(str(plan["entry"])), Decimal(str(ask_of(bid_now, inst))))
        else:
            e = min(Decimal(str(plan["entry"])), Decimal(str(bid_now)))
        e = round_price(e, inst)
    if Decimal(str(e)) != Decimal(str(plan["entry"])):
        notes.insert(0, "The price moved since the plan. Sized at %s, not %s. A worse entry "
                        "means a bigger loss at the same stop." % (price_text(e, inst),
                                                                    price_text(plan["entry"], inst)))
    ctx.update(quote=q, bid_now=bid_now, e=e, entry_notes=notes)


def price_gates(ctx):
    """Steps 11, 10 and 12: time left, already at the stop or the target, reward:risk."""
    plan, inst, cfg, now = ctx["plan"], ctx["inst"], ctx["cfg"], ctx["now"]
    bid, e = ctx["bid_now"], Decimal(str(ctx["e"]))
    # (check) the time test runs before the price test (step 11 before 10), so a
    # late day plan says "less than 15 minutes" (test 8), not "at the stop".
    if plan["style"] == "day":
        flat = parse_utc(plan["flat_by_utc"])
        if now >= flat - datetime.timedelta(minutes=MIN_DAY_MINUTES_LEFT):
            raise Refused("Less than %d minutes to your flat-by time (%s)."
                          % (MIN_DAY_MINUTES_LEFT, flat_words(flat)))
    elif now >= parse_utc(plan["exit_by"]):
        raise Refused("This plan's last day has passed. Make a new plan: /fx-exit.")
    sl, tp = Decimal(str(plan["stop_loss"])), Decimal(str(plan["take_profit"]))
    if bid is not None:
        b = Decimal(str(bid))
        a = Decimal(str(ask_of(bid, inst)))
        buy = plan["side"] == "buy"
        if (buy and b <= sl) or (not buy and a >= sl):
            raise Refused("The price is already at the stop (%s %s, stop %s). Make a new plan: "
                          "/fx-exit." % ("bid" if buy else "ask", price_text(b if buy else a, inst),
                                         price_text(sl, inst)))
        if (buy and b >= tp) or (not buy and a <= tp):
            raise Refused("The price is already at the target. The move it planned for has happened.")
    rr = abs(tp - e) / abs(e - sl)
    ctx["rr"] = rr
    least = Decimal(str((cfg.get("risk") or {}).get("min_reward_risk", 1.5)))
    if rr < least:
        raise Refused("Reward:risk %s (target %s, entry %s, stop %s) is under your minimum %s, so "
                      "fx-risk would block it. Skip it, or make a new plan with /fx-exit."
                      % (rr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), price_text(tp, inst),
                         price_text(e, inst), price_text(sl, inst),
                         least.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))


def size_it(ctx):
    """Steps 13 to 16: nights, rates, margin in use, equity and size_order (into ctx).
    size_order raises TooSmall (exit 1) or ValueError (exit 2)."""
    plan, inst, cfg, now, args = ctx["plan"], ctx["inst"], ctx["cfg"], ctx["now"], ctx["args"]
    if plan["style"] == "day":
        nights = 0
    else:
        est = ((plan.get("costs") or {}).get("nights_est")) or 0
        nights = max(int(est), swap_nights(now, parse_utc(plan["exit_by"]), inst, cfg))
    rates = fx_rates(ctx["data_dir"], now, cfg)
    used, equity, pnotes = positions_now({"balance": ctx["balance"], "positions": ctx["positions"]},
                                         cfg, rates, ctx["data_dir"], now, ctx["rolls"], ctx["replay"])
    ctx.update(nights=nights, rates=rates, used=used, equity=equity, position_notes=pnotes)
    ctx["s"] = size_order(inst, plan["side"], ctx["e"], plan["stop_loss"], nights, ctx["balance"],
                          used, rates, cfg, risk_pct=args.risk_pct, risk_usd=args.risk_usd,
                          equity=equity)


def money_parts(ctx):
    """Step 17: the money for the printout and the order (into ctx["m"])."""
    plan, inst, cfg, s = ctx["plan"], ctx["inst"], ctx["cfg"], ctx["s"]
    side, e, sl, n, rates = plan["side"], ctx["e"], plan["stop_loss"], ctx["nights"], ctx["rates"]
    lots = s["lots"]
    m = {"lots": lots}
    m["max_loss"] = max_loss_usd(inst, side, lots, e, sl, n, rates, cfg)
    m["costs"] = trade_costs(inst, side, lots, n, rates, cfg, price=e)
    m["costs_usd"] = costs_usd(inst, side, lots, n, rates, cfg, e)
    m["price_loss"] = float(Decimal(str(m["max_loss"])) - Decimal(str(m["costs_usd"])))
    m["one"] = trade_costs(inst, side, 1, n, rates, cfg, price=e)
    m["one_price"] = float(Decimal(str(s["loss_per_lot_usd"]))
                           - Decimal(str(costs_usd(inst, side, 1, n, rates, cfg, e))))
    m["margin"] = margin_usd(inst, lots, e, side, cfg, rates)
    m["vpp"] = usd_per_point_per_lot(inst, rates, cfg)
    m["net"] = reward_risk_net(inst, side, e, sl, plan["take_profit"], n, rates, cfg)
    m["next"] = None
    if s["decided_by"] in ("risk_pct", "risk_usd"):
        nxt = lots + Decimal(str(inst["lot_step"]))
        m["next"] = (nxt, max_loss_usd(inst, side, nxt, e, sl, n, rates, cfg))
    gap = plan.get("gap") or {}
    m["gap"] = None
    if gap.get("loss_per_lot_usd") is not None:
        m["gap"] = float((Decimal(str(gap["loss_per_lot_usd"])) * lots).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP))
    ctx["m"] = m


def lose_word(lots):
    """"0.02 lots lose" or "1.00 lot loses"."""
    t = lots_text(lots)
    return "%s %s" % (t, "loses" if t.endswith(" lot") else "lose")


def need_word(lots):
    """"0.09 lots need" or "1.00 lot needs"."""
    t = lots_text(lots)
    return "%s %s" % (t, "needs" if t.endswith(" lot") else "need")


def why_line(ctx):
    """The order's one plain "why" line, by decided_by."""
    s, m, cfg, args = ctx["s"], ctx["m"], ctx["cfg"], ctx["args"]
    risk = cfg.get("risk") or {}
    pct = args.risk_pct if args.risk_pct is not None else risk.get("max_risk_per_trade_pct", 1.0)
    bal = Decimal(str(ctx["balance"]))
    by = s["decided_by"]
    if by == "risk_pct":
        return "%s of %s is %s. %s %s at the stop, costs in." % (
            pct_text(pct), money(bal), money(bal * Decimal(str(pct)) / 100),
            lose_word(m["lots"]), money(m["max_loss"]))
    if by == "risk_usd":
        cap = args.risk_usd if args.risk_usd is not None else risk.get("max_risk_per_trade_usd")
        return "Your cap is %s a trade. %s %s at the stop, costs in." % (
            money(cap), lose_word(m["lots"]), money(m["max_loss"]))
    if by == "max_lot":
        return "%s is the broker's largest order. It loses %s at the stop, costs in." % (
            lots_text(m["lots"]), money(m["max_loss"]))
    if by == "margin_cap":
        return "Your margin limit is %s%% of %s. %s %s margin." % (
            plain_num(risk.get("max_margin_used_pct", 30)), money(bal), need_word(m["lots"]),
            money(m["margin"]))
    return "Your free margin is %s. %s %s margin." % (
        money(Decimal(str(ctx["equity"])) - Decimal(str(ctx["used"]))), need_word(m["lots"]),
        money(m["margin"]))


def make_order(plan, inst, s, e, nights, money):
    """The one order of section 5. money: the step 17 parts plus "why"."""
    day = plan["style"] == "day"
    keep = ("setup", "fx_day", "chart_entry", "chart_stop")
    sub = dict((k, plan.get(k)) for k in keep)
    sub["risk_per_unit"] = round_price(abs(Decimal(str(e)) - Decimal(str(plan["stop_loss"]))), inst)
    for k in ("target_r", "flat_by", "flat_by_utc", "exit_by"):
        sub[k] = plan.get(k)
    sub.update(nights_est=nights, max_loss_usd=money["max_loss"], costs_usd=money["costs_usd"],
               margin_usd=money["margin"], usd_per_point_per_lot=money["vpp"])
    return {"symbol": plan["symbol"], "side": plan["side"], "intent": "open",
            "lots": float(s["lots"]), "type": plan["entry_type"], "entry": round_price(e, inst),
            "stop_loss": plan["stop_loss"], "take_profit": plan["take_profit"],
            "expiry": plan["flat_by_utc"] if day else "gtc", "style": plan["style"],
            "broker_symbol": inst["broker_symbol"], "position_id": None,
            "rolls_applied": plan.get("rolls_applied") or {}, "plan": sub,
            "decided_by": s["decided_by"], "why": money["why"]}


def tm_lines(ctx):
    """Step 18: the time machine line(s), or the "older plan" line, or nothing."""
    try:
        tm = load_json(ctx["paths"]["timemachine"], None)
    except ValueError:
        return []
    if not isinstance(tm, dict):
        return []
    p0, tp = ctx["plan0"], tm.get("plan") or {}
    keys = ("symbol", "side", "entry", "stop_loss", "take_profit")
    if not isinstance(tp, dict) or any(tp.get(k) != p0.get(k) for k in keys):
        return ["The time machine ran on an older plan. Not shown."]
    odds = tm.get("odds") or {}
    try:
        line = ("Time machine (a simulation, not a prediction): in %s replays the target came "
                "first %d%%, the stop %d%%, neither %d%%." % (
                    format(int(tm.get("runs") or 0), ","), round(odds["target_first"] * 100),
                    round(odds["stop_first"] * 100), round(odds["neither"] * 100)))
    except (KeyError, TypeError, ValueError):
        return ["The time machine file has no odds. Not shown."]   # (check) words
    out = [line]
    if tm.get("verdict"):
        out.append(str(tm["verdict"]))
    return out


def note_list(ctx):
    """The notes, in the order of section 6 (each without the "Note: " start)."""
    plan, cfg, m, inst = ctx["plan"], ctx["cfg"], ctx["m"], ctx["inst"]
    out = list(ctx["entry_notes"]) + list(ctx["roll_notes"])
    want = (cfg.get("swing") or {}).get("entry")
    if plan["style"] == "swing" and want and want != plan["entry_type"]:
        out.append("Your config says swing entries are %s; this plan uses %s %s order. The plan "
                   "wins." % (want, "an" if plan["entry_type"] == "market" else "a",
                              plan["entry_type"]))
    gap = plan.get("gap") or {}
    if gap.get("note") and m["gap"] is not None:
        out.append("%s For %s that is %s more." % (gap["note"], lots_text(m["lots"]), money(m["gap"])))
    least = (cfg.get("risk") or {}).get("min_reward_risk", 1.5)
    if Decimal(str(m["net"])) < Decimal(str(least)):
        line = ("After costs this trade wins %s times what it risks. Your minimum is %s."
                % (format(Decimal(str(m["net"])).quantize(Decimal("0.01")), "f"), plain_num(least)))
        if plan["style"] == "swing":
            line += " The swap for the whole hold is counted."
        out.append(line)
    if m["price_loss"] > 0:
        share = Decimal(str(m["costs_usd"])) / Decimal(str(m["price_loss"])) * 100
        if share > COSTS_NOTE_PCT:
            out.append("Costs are about %d%% of the price loss (over %d%%). You pay them win or lose."
                       % (int(share.quantize(Decimal(1), rounding=ROUND_HALF_UP)), COSTS_NOTE_PCT))
    for w in plan.get("warnings") or []:
        if not any(str(w) in n for n in out):   # fx-exit's own W-net line is said once
            out.append(str(w))
    out.extend(ctx.get("position_notes") or [])
    q = ctx.get("quote") or {}
    for sym in ctx.get("failed") or []:
        out.append("Yahoo could not be read for %s. Using the saved prices (%s)."
                   % (sym, age_text(q.get("age_min")) if sym == inst["yahoo"] else "older ones"))
    return out


def order_type_words(plan, inst):
    """"Market order: you buy at the ask." and the pending forms of section 6."""
    side, kind = plan["side"], plan["entry_type"]
    if kind == "market":
        return "Market order: you %s at the %s." % (side, "ask" if side == "buy" else "bid")
    move = {("buy", "stop"): "the ask rises", ("buy", "limit"): "the ask falls",
            ("sell", "stop"): "the bid falls", ("sell", "limit"): "the bid rises"}[(side, kind)]
    return "%s %s order at %s: it fills if %s to it." % (
        side.capitalize(), kind, price_text(plan["entry"], inst), move)


def entry_line(ctx):
    """"Entry X = Yahoo bid B + spread S." (buy) or "Entry X = the Yahoo bid." (sell)."""
    plan, inst, e, bid = ctx["plan"], ctx["inst"], ctx["e"], ctx["bid_now"]
    if plan["entry_type"] == "market" and bid is not None:
        if plan["side"] == "buy" and Decimal(str(ask_of(bid, inst))) == Decimal(str(e)):
            return "Entry %s = Yahoo bid %s + spread %s." % (
                price_text(e, inst), price_text(bid, inst), price_text(spread_price(inst), inst))
        if plan["side"] == "sell" and Decimal(str(bid)) == Decimal(str(e)):
            return "Entry %s = the Yahoo bid." % price_text(e, inst)
    return "Entry %s: the plan's entry." % price_text(e, inst)   # (check) words


def limit_rows(ctx):
    """The limits table rows of section 6.1 ("<- decides" on decided_by)."""
    s, cfg, args, inst = ctx["s"], ctx["cfg"], ctx["args"], ctx["inst"]
    risk = cfg.get("risk") or {}
    bal = Decimal(str(ctx["balance"]))
    pct = args.risk_pct if args.risk_pct is not None else risk.get("max_risk_per_trade_pct", 1.0)
    cap = args.risk_usd if args.risk_usd is not None else risk.get("max_risk_per_trade_usd")
    free = Decimal(str(ctx["equity"])) - Decimal(str(ctx["used"]))
    text = {"risk_pct": ("max loss", "%s of %s = %s, costs in" % (
                pct_text(pct), money(bal), money(bal * Decimal(str(pct)) / 100))),
            "risk_usd": ("max loss", "your cap %s, costs in" % (money(cap) if cap is not None else "")),
            "max_lot": ("max lot", "the broker's largest order"),
            "margin_cap": ("margin cap", "%s%% of %s, minus %s in use" % (
                plain_num(risk.get("max_margin_used_pct", 30)), money(bal), money(ctx["used"]))),
            "free_margin": ("free margin", "%s free" % money(max(free, Decimal(0))))}
    out = ["Limits (the smallest one wins):"]
    for name in SIZE_LIMITS:
        lots = s["limits"].get(name)
        if lots is None:
            continue
        label, words = text[name]
        mark = "  <- decides" if name == s["decided_by"] else ""
        out.append("  %-12s %-40s %10s%s" % (label, words, lots_text(Decimal(lots)), mark))
    return out


def loss_lines(ctx):
    """"One lot loses ..." and the "Max loss at the stop" block."""
    plan, inst, s, m, n = ctx["plan"], ctx["inst"], ctx["s"], ctx["m"], ctx["nights"]
    one, c = m["one"], m["costs"]
    parts = ["%s price and spread" % money(m["one_price"])]
    if one["commission"] > 0:
        parts.append("%s commission" % money(one["commission"]))
    if one["swap"] < 0:
        parts.append("%s swap for %d nights" % (money(-one["swap"]), n))
    if one["fee"] > 0:
        parts.append("%s swap-free fee" % money(one["fee"]))
    out = textwrap.wrap("One lot loses %s at the stop: %s." % (
        money(s["loss_per_lot_usd"]), ", ".join(parts)), 74)
    out += ["", "Max loss at the stop: %s, costs included." % money(m["max_loss"])]
    dist = price_text(abs(Decimal(str(ctx["e"])) - Decimal(str(plan["stop_loss"]))), inst)
    if (inst.get("profit_currency") or inst["quote"]) == "USD":
        how = "%s x %s x %s = %s" % (lots_text(m["lots"]), dist, format(int(inst["contract_size"]), ","),
                                     money(m["price_loss"]))
    else:
        how = "%s (turned into US dollars)" % money(m["price_loss"])   # (check) words
    out.append("  price and spread  %s" % how)
    out.append("  commission        %s (both sides)" % money(c["commission"]))
    if plan["style"] == "swing":
        out += swap_rows(ctx)
    if m["next"] is not None:
        out.append("  one more step (%s) would lose %s: over your %s" % (
            lots_text(m["next"][0]), money(m["next"][1]), money(s["risk_limit_usd"])))
    return out


def swap_rows(ctx):
    """The swap (and fee) rows of a swing trade."""
    inst, plan, c, n, cfg = ctx["inst"], ctx["plan"], ctx["m"]["costs"], ctx["nights"], ctx["cfg"]
    if (cfg.get("account") or {}).get("swap_free"):
        out = ["  swap              $0.00 (swap-free account)"]
        if c["fee"] > 0:
            out.append("  fee               %s after %s free nights" % (
                money(c["fee"]), plain_num(inst.get("swap_free_grace_nights") or 0)))
        return out
    if c["swap"] > 0:
        return ["  swap              %s paid to you over %d nights (not counted: a broker can "
                "change it)" % (signed_money(c["swap"]), n)]
    v = inst.get("swap_long" if plan["side"] == "buy" else "swap_short") or 0
    mode = inst.get("swap_mode") or "points"
    rate = "%s points a night" % plain_num(v) if mode == "points" else "%s a night, %s" % (plain_num(v), mode)
    return ["  swap              %s if held all %d nights (%s)" % (money(-c["swap"]), n, rate)]


def head_lines(ctx):
    """The lines from SIZED to the hold line."""
    plan, inst, m, q = ctx["plan"], ctx["inst"], ctx["m"], ctx["quote"]
    side, e = plan["side"], Decimal(str(ctx["e"]))
    out = ["SIZED: %s of %s (%s, %s)." % (lots_text(m["lots"]), inst["key"], side_word(side), plan["style"])]
    words = setup_words(plan.get("setup"))
    out += textwrap.wrap(("Setup: %s. " % words if words else "") + order_type_words(plan, inst), 78)
    out.append(entry_line(ctx))
    out.append("  %s %s (%s, %s)" % (price_name(inst), price_text(q["bid"], inst), scale_note(inst),
                                     age_text(q["age_min"])))
    sl, tp = Decimal(str(plan["stop_loss"])), Decimal(str(plan["take_profit"]))
    buy = side == "buy"
    out.append("Stop %s: %s %s the entry." % (price_text(sl, inst), pips_text(e - sl, inst),
                                              "under" if buy else "over"))
    out.append("Target %s: %s %s the entry. Reward:risk %s." % (
        price_text(tp, inst), pips_text(tp - e, inst), "over" if buy else "under",
        ctx["rr"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))
    if plan["style"] == "day":
        out.append("Day trade: flat by %s. The order ends then." % flat_words(parse_utc(plan["flat_by_utc"])))
    else:
        x = parse_utc(plan["exit_by"])
        days = plan.get("max_hold_days")
        out.append(("Hold: up to %s FX days. " % days if days else "Hold: ")
                   + "Exit by %s, %s." % (both_times(x), to_pkt(x).date().isoformat()))
    return out


def money_lines(ctx):
    """The margin, value and swap-time lines."""
    inst, m, plan, cfg = ctx["inst"], ctx["m"], ctx["plan"], ctx["cfg"]
    bal = Decimal(str(ctx["balance"]))
    share = (Decimal(str(m["margin"])) / bal * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if bal > 0 else Decimal(0)
    per_point = Decimal(str(m["vpp"])) * m["lots"]
    point = Decimal(str(inst["point"]))
    if inst.get("pip"):
        more = "%s a pip" % money(per_point * Decimal(str(inst["pip"])) / point)
    else:
        more = "%s a 1.00 move" % money(per_point / point)
    out = ["Margin: %s (%s%% of your balance). Value: %s a point, %s." % (
        money(m["margin"]), share, money(per_point), more)]
    if plan["style"] == "swing" and not (cfg.get("account") or {}).get("swap_free"):
        day = to_ny(ctx["now"]).date()
        roll = ny_to_utc(datetime.datetime.combine(day, datetime.time(17)))
        line = "Swap is charged each night at 5:00 PM New York (%s PKT now)." % clock_label(to_pkt(roll))
        triple = inst.get("swap_triple_day") or "wednesday"
        if triple != "none":
            line += " %s nights count 3 times." % triple.capitalize()
        out += textwrap.wrap(line, 78)
    return out


def report(ctx):
    """The section 6 lines of a sized order."""
    quiet = ctx["args"].quiet
    out = head_lines(ctx)[:1] if quiet else head_lines(ctx)
    if not quiet:
        out += [""] + limit_rows(ctx) + loss_lines(ctx) + money_lines(ctx)
        for n in ctx["notes"]:
            out += textwrap.wrap("Note: " + n, 78)
        for line in ctx["tm"]:
            out += textwrap.wrap(line, 78)
        out.append("")
    out += order_lines(ctx)
    out.append(ctx["saved_line"])
    if not quiet:
        out.append("Next: python3 fx-risk/check.py")
    return out


def order_lines(ctx):
    """The "Proposed order" block."""
    o, inst = ctx["order"], ctx["inst"]
    first = "  %s %s %s" % (side_word(o["side"]), lots_text(o["lots"]), o["broker_symbol"])
    if o["type"] == "market":
        first += " at the market."
    else:
        first += " with a %s %s order at %s." % (o["side"], o["type"], price_text(o["entry"], inst))
    first += " SL %s. TP %s." % (price_text(o["stop_loss"], inst), price_text(o["take_profit"], inst))
    out = ["Proposed order (nothing has been placed):"]
    if o["expiry"] == "gtc":
        out.append(first + " Good till cancelled.")
    else:
        out += [first, "  Ends at %s." % flat_words(parse_utc(o["expiry"]))]
    return out


def build_parser():
    """The one command's flags (06 2.1 plus section 2); --help ends with the six traps."""
    p = argparse.ArgumentParser(
        prog="size.py", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=TRAPS,
        description="Size ONE trade in lots from fx/plan.json, costs in. With --write it saves "
                    "ONE order to fx/orders.json. It never places an order.")
    add_common(p, write=True)
    p.add_argument("--plan", metavar="FILE", default=None,
                   help="the plan to size (default fx/plan.json)")
    p.add_argument("--risk-pct", metavar="X", type=float, default=None,
                   help="a smaller max loss a trade, as a percent of the balance (1.0 means 1%%)")
    p.add_argument("--risk-usd", metavar="X", type=float, default=None,
                   help="a smaller max loss a trade, in US dollars")
    return p


def json_doc(ctx, ok):
    """The common keys of 06 2.5 plus size.py's own keys."""
    notes = list(ctx.get("notes") or []) + list(ctx.get("tm") or [])
    return {"v": JSON_VERSION, "tool": SKILL, "cmd": None, "now": utc_iso(ctx["now"]), "ok": ok,
            "notes": notes, "order": ctx.get("order"), "decided_by": ctx.get("decided_by"),
            "limits": ctx.get("limits") or {"risk_usd": None, "margin_free_usd": None,
                                            "max_lot": None},
            "written": bool(ctx.get("written"))}


def refusal(ctx, reason, too_small=None):
    """Print a plain no (exit 1): NO ORDER, the reason, "Nothing was written."."""
    args, plan, inst = ctx["args"], ctx.get("plan") or {}, ctx.get("inst") or {}
    reason = " ".join(str(reason).split())
    ctx["notes"] = list(ctx.get("entry_notes") or []) + list(ctx.get("roll_notes") or [])
    ctx["tm"] = []
    if args.json:
        doc = json_doc(ctx, False)
        doc["order"] = None
        doc["written"] = False
        doc["decided_by"] = too_small.decided_by if too_small is not None else None
        lim = {"risk_usd": None, "margin_free_usd": None,
               "max_lot": float(inst["max_lot"]) if inst.get("max_lot") is not None else None}
        if too_small is not None:
            lim["risk_usd"] = too_small.risk_limit_usd
        if ctx.get("equity") is not None:
            lim["margin_free_usd"] = float((Decimal(str(ctx["equity"])) - Decimal(str(ctx["used"])))
                                           .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        doc["limits"] = lim
        doc["refused"] = {"reason": reason,
                          "min_lot_loss_usd": too_small.min_lot_loss_usd if too_small is not None else None}
        emit_json(doc)
        return 1
    print("NO ORDER for %s (%s, %s)." % (plan.get("symbol"), side_word(plan["side"]), plan.get("style")))
    for line in textwrap.wrap(reason, 78):
        print(line)
    if not args.quiet:
        for n in ctx["notes"]:
            for line in textwrap.wrap("Note: " + n, 78):
                print(line)
    print("Nothing was written.")
    return 1


def run(args, ctx):
    """Steps 1 to 21 of section 4. Returns the exit code (0) or raises."""
    load_inputs(args, ctx)
    plan_gates(ctx)
    rebase_plan(ctx)
    sizing_entry(ctx)
    price_gates(ctx)
    size_it(ctx)
    money_parts(ctx)
    s, plan, inst, m = ctx["s"], ctx["plan"], ctx["inst"], ctx["m"]
    m["why"] = why_line(ctx)
    ctx["notes"] = note_list(ctx)
    ctx["tm"] = tm_lines(ctx)
    order = make_order(plan, inst, s, ctx["e"], ctx["nights"], m)
    ctx.update(order=order, decided_by=s["decided_by"])
    ctx["limits"] = {"risk_usd": s["risk_limit_usd"],
                     "margin_free_usd": float((Decimal(str(ctx["equity"])) - Decimal(str(ctx["used"])))
                                              .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                     "max_lot": float(inst["max_lot"])}
    orders_path = ctx["paths"]["orders"]
    if args.write:
        save_json_atomic(orders_path, {"created_by": SKILL, "created_at": utc_iso(ctx["now"]),
                                       "orders": [order]})
        ctx["written"] = True
        ctx["saved_line"] = "Wrote 1 order to %s." % orders_path
    else:
        ctx["saved_line"] = "Not saved. Add --write to save it to %s." % orders_path
    if args.json:
        emit_json(json_doc(ctx, True))
    else:
        for line in report(ctx):
            print(line)
    return 0


def main(argv=None):
    """Parse, size, print. 0 sized, 1 refused, 2 error (one line on stderr)."""
    args = build_parser().parse_args(argv)
    ctx = {"args": args, "written": False, "now": None}
    try:
        return run(args, ctx)
    except TooSmall as exc:
        return refusal(ctx, exc, too_small=exc)
    except Refused as exc:
        return refusal(ctx, exc)
    except ValueError as exc:
        return fail(exc, TOOL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
