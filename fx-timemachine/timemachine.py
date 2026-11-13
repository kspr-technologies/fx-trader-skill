#!/usr/bin/env python3
"""fx-timemachine: replay a planned buy or sell 10,000 times before you risk money.

    python3 fx-timemachine/timemachine.py run                    # fx/plan.json, seed 19
    python3 fx-timemachine/timemachine.py run --write            # also save fx/timemachine.json
    python3 fx-timemachine/timemachine.py run --seed 7 --runs 20000
    python3 fx-timemachine/timemachine.py run --raw --fan        # a swing plan, drift kept
    python3 fx-timemachine/timemachine.py run --plan old/plan.json --json

Swing plans replay blocks of 5 past days (same weekday), drift removed, scaled to
today's ATR, with swap each night. Day plans replay 5-minute bars from the same time
of day, in blocks of 3 bars, to the flat-by time. A buy's stop and target fire on the
bid, a sell's on the ask. Costs are in every R. It reads only the data folder: no
network. It never places an order and never changes the plan. A simulation, not a
prediction. Standard library only, Python 3.8.
"""

import argparse
import copy
import datetime
import difflib
import json
import math
import os
import random
import re
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

TOOL = "timemachine.py"
SKILL = "fx-timemachine"

# ---------------------------------------------------------------- copy together
# Each copied helper and the fx_core names it uses (all copied below).
#   load_json: -
#   save_json_atomic: -
#   fx_paths: -
#   load_doc: load_json
#   check_symbol: -
#   check_side: -
#   check_style: -
#   check_utc: -
#   _positive_number: -
#   check_price: _positive_number
#   JSON_VERSION: -
#   _words_dec: -
#   money: _words_dec
#   signed_money: _words_dec, money
#   price_text: _words_dec
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
#   SESSIONS: -
#   OR_WINDOWS: -
#   check_day_end: DAY_ENDS
#   parse_fx_date: FULL_DAY_NAMES
#   at_time: parse_hhmm
#   fx_day: check_day_end, to_ny
#   fx_day_bounds: check_day_end, ny_to_utc, parse_fx_date
#   full_fx_holiday: group_closed
#   next_fx_day: full_fx_holiday, parse_fx_date
#   pkt_time_on_fx_day: fx_day_bounds, parse_hhmm, pkt_to_utc, to_pkt
#   session_bounds: SESSIONS, at_time, city_to_utc, parse_fx_date
#   opening_range_window: OR_WINDOWS, at_time, city_to_utc, parse_fx_date
#   TABLES_END: -
#   TABLES_START: -
#   HOLIDAYS: -
#   EARLY_CLOSES: -
#   HOURS: -
#   BROKER_TEMPLATES: -
#   DATA_TEMPLATES: -
#   MARKET_WORDS: -
#   hours_template: HOURS
#   group_closed: HOLIDAYS, parse_fx_date
#   early_close_utc: EARLY_CLOSES, at_time, ny_to_utc, parse_fx_date
#   yahoo_no_data_cuts: HOLIDAYS, at_time, ny_to_utc, parse_fx_date
#   open_spans: DATA_TEMPLATES, at_time, city_to_utc, early_close_utc, group_closed, hours_template, ny_to_utc, parse_fx_date, yahoo_no_data_cuts
#   holiday_note: EARLY_CLOSES, HOLIDAYS, MARKET_WORDS, TABLES_END, TABLES_START, early_close_utc, hours_template, ny_label, parse_fx_date, pkt_label, short_date
#   STEP_S: -
#   cache_file: -
#   load_cache: cache_file
#   last_fetch: -
#   DONE_MARGIN_MIN: -
#   OUTAGE_MIN_SLOTS: -
#   COARSE: -
#   COARSE_MARGIN_PIPS: -
#   YAHOO_DECIMALS: -
#   symbol_root: -
#   inst_root: symbol_root
#   yahoo_decimals: YAHOO_DECIMALS, inst_root
#   _round_dec: -
#   clean_rows: _round_dec, ts_to_utc, yahoo_decimals
#   _span_of: fx_day, open_spans
#   bar_done: DONE_MARGIN_MIN, STEP_S, _span_of
#   load_bars: STEP_S, _dec_clean, _span_of, adjust_rows, bar_done, clean_rows, inst_root, last_fetch, load_cache, parse_utc, ts_to_utc
#   _overlap: -
#   _touched_fx_days: fx_day_bounds, to_ny
#   open_time_in: _overlap, _touched_fx_days, open_spans
#   find_outages: OUTAGE_MIN_SLOTS, STEP_S, _touched_fx_days, hours_template, open_time_in, yahoo_no_data_cuts
#   _daily_flags: _touched_fx_days, early_close_utc, hours_template, open_spans, open_time_in
#   daily_from_hourly: _daily_flags, _day_forming, check_day_end, find_outages, fx_day, fx_day_bounds
#   _day_forming: _touched_fx_days, open_spans
#   data_age_min: -
#   _plan_bid: _dec_clean, _round_dec, yahoo_decimals
#   latest_price: STEP_S, _dec_clean, _plan_bid, _round_dec, data_age_min, last_fetch, load_bars, load_cache, parse_utc, ts_to_utc, yahoo_decimals
#   MONTH_CODES: -
#   rolls_path: -
#   load_rolls_doc: load_json, rolls_path
#   load_rolls: load_rolls_doc
#   _roll_bar: _round_dec, ts_to_utc
#   adjust_rows: YAHOO_DECIMALS, _roll_bar, parse_utc
#   ROLL_AWAY: -
#   _get_path: -
#   _set_path: -
#   rebase: ROLL_AWAY, _dec_clean, _get_path, _set_path, inst_root, round_away, round_price
#   RATE_PAIRS: -
#   _num_word: _dec_clean
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
#   _swap_free: -
#   _margin_ccy_usd: RATE_PAIRS, _is_currency, _pccy, _price_dec, _rate_from_price, usd_per_unit
#   _commission: _dec_clean
#   _swap: _dec_clean, _is_currency, _margin_ccy_usd, _pccy, _price_dec, _swap_free, side_word, usd_per_unit
#   _fee: _dec_clean, _swap_free
#   swap_nights: FULL_DAY_NAMES, ny_to_utc, to_ny
#   _costs: _commission, _fee, _swap
#   _check_stop_side: _price_dec, side_word
#   max_loss_core: _check_stop_side, _costs, _dec_clean, loss_usd_per_unit
#   net_core: _check_stop_side, _costs, _dec_clean, _price_dec, max_loss_core, reward_usd_per_unit
#   _pl_core: _dec_clean, _fee, _pccy, _price_dec, _swap, side_word, usd_per_unit
#   result_core: _commission, _pl_core
#   bar_hits: _dec_clean, side_word, spread_price
#   pending_fill: _dec_clean, _price_dec, ask_of, round_price, side_word, spread_price
#   gap_fill: _dec_clean, round_price, side_word, spread_price
#   SKIP_FLAGS: -
#   usable_bars: SKIP_FLAGS
#   true_range: -
#   atr_wilder: true_range
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
SESSIONS = {
    "sydney":   ("sydney", "07:00", "16:00"),
    "tokyo":    ("tokyo",  "09:00", "18:00"),
    "london":   ("london", "08:00", "17:00"),
    "new_york": ("ny",     "08:00", "17:00"),
}
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


def full_fx_holiday(fx_date, cfg):
    """True when the date is in the config "holidays", or HOLIDAYS shuts "fx" all day."""
    return group_closed(fx_date, "fx", cfg)


def next_fx_day(fx_date, cfg):
    """The next Monday-to-Friday date after fx_date that is not a full FX holiday."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    while True:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and not full_fx_holiday(d, cfg):
            return d.isoformat()


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


def session_bounds(name, fx_date):
    """(start_utc, end_utc) of a trading session ("sydney", "tokyo", "london",
    "new_york") on the local calendar date equal to fx_date. Not clipped."""
    if name not in SESSIONS:
        raise ValueError('A session is "sydney", "tokyo", "london" or "new_york", not "%s"' % name)
    d = parse_fx_date(fx_date)
    city, a, b = SESSIONS[name]
    return city_to_utc(city, at_time(d, a)), city_to_utc(city, at_time(d, b))


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


TABLES_END = "2027-12-31"          # after this date holiday_note says the list has ended
TABLES_START = "2024-11-01"        # the 1h cache reaches 730 days back
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
MARKET_WORDS = {"fx": "FX", "cme": "Gold and oil", "ice": "Brent"}
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


def holiday_note(date, inst, cfg):
    """One plain line about a holiday, a thin day or an early close for the instrument's
    market on that FX date, or None on a normal day."""
    d = parse_fx_date(date, weekend_ok=True)
    iso = d.isoformat()
    group = hours_template(inst["hours"])["group"]
    word = MARKET_WORDS[group]
    day = short_date(d)
    mine = ((cfg or {}).get("holidays") or {})
    if iso in mine:
        return "%s: your broker is shut (%s)." % (day, mine[iso])
    row = HOLIDAYS.get(iso)
    early = EARLY_CLOSES.get(iso, {})
    sure = " These hours are expected, not confirmed. Check your broker's notice."
    if row and group in row.get("closed", []):
        text, prov = "%s: %s. %s is shut all day." % (day, row["name"], word), row.get("provisional", [])
    else:
        close = early_close_utc(d, group, cfg)
        if close is not None:
            name = early.get("name") if early.get(group) else None
            text = "%s: %s. %s stops early, at %s (%s)." % (
                day, name or "Early close (your config)", word, ny_label(close), pkt_label(close))
            prov = early.get("provisional", []) if name else []
        elif row and group in row.get("thin", []):
            text, prov = "%s: %s. Open, but few traders. Spreads can be wide." % (day, row["name"]), row.get("provisional", [])
        elif row:
            text, prov = "%s: %s. Markets are open as usual." % (day, row["name"]), row.get("provisional", [])
        elif iso > TABLES_END:
            return "This tool's holiday list ends on %s. Check your broker's holiday notice." % TABLES_END
        elif iso < TABLES_START:
            return "This tool's holiday list starts on %s." % TABLES_START
        else:
            return None
    return text + (sure if group in prov else "")


STEP_S = {"5m": 300, "1h": 3600, "1d": 86400}          # bar length in seconds
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


def last_fetch(doc):
    """The newest fetch record of a cache doc, or None."""
    fetches = (doc or {}).get("fetches") or []
    return fetches[-1] if fetches else None


DONE_MARGIN_MIN = 15          # a span's last bar is done this long after the close
OUTAGE_MIN_SLOTS = {"5m": 6, "1h": 3, "1d": 1}   # an outage misses this many steps of open time
COARSE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}
COARSE_MARGIN_PIPS = 2
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


def _overlap(a0, a1, b0, b1):
    """The length of the overlap of [a0, a1) and [b0, b1) as a timedelta (0 or more)."""
    return max(datetime.timedelta(0), min(a1, b1) - max(a0, b0))


def _touched_fx_days(start, end):
    """The 17:00-New-York FX days (date strings) whose bounds overlap [start, end)."""
    out = []
    d = to_ny(start).date() - datetime.timedelta(days=1)
    last = to_ny(end).date() + datetime.timedelta(days=1)
    while d <= last:
        if d.weekday() < 5:
            a, b = fx_day_bounds(d, "17:00 New York")
            if a < end and b > start:
                out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def open_time_in(template, start, end, cfg):
    """How much open time a template has inside [start, end), as a timedelta: the overlap
    with open_spans of each FX day it touches (tables used)."""
    total = datetime.timedelta(0)
    for e in _touched_fx_days(start, end):
        for a, b in open_spans(template, e, cfg):
            total += _overlap(start, end, a, b)
    return total


def find_outages(bars, inst, interval, cfg):
    """The holes between done bars that miss at least OUTAGE_MIN_SLOTS steps of open data
    time, as (from_utc, to_utc); a hole touching a Yahoo no-data day is never one."""
    step = datetime.timedelta(seconds=STEP_S[interval])
    need = step * OUTAGE_MIN_SLOTS[interval]
    template = inst["data_hours"]
    out = []
    for prev, nxt in zip(bars, bars[1:]):
        h0, h1 = prev["start"] + step, nxt["start"]
        if h1 - h0 < need:
            continue
        if hours_template(template)["group"] == "cme":
            cuts = [c for e in _touched_fx_days(h0, h1) for c in yahoo_no_data_cuts(e)]
            if any(a < h1 and b > h0 for a, b in cuts):
                continue
        if open_time_in(template, h0, h1, cfg) >= need:
            out.append((h0, h1))
    return out


def _daily_flags(day, bars, inst, cfg, outages):
    """The flags of one daily bar (part 02 4.2): partial, holiday, early_close, outage,
    roll, late_start."""
    start, end = day["start"], day["end"]
    expected = open_time_in(inst["data_hours"], start, end, cfg).total_seconds() / 3600.0
    flags = []
    if expected == 0:
        flags.append("holiday")
    elif day["hours"] < 0.75 * expected:
        flags.append("partial")
    group = hours_template(inst["data_hours"])["group"]
    touched = _touched_fx_days(start, end)
    if any(early_close_utc(e, group, cfg) is not None for e in touched):
        flags.append("early_close")
    if any(a < end and b > start for a, b in outages):
        flags.append("outage")
    if any("roll" in (b.get("flags") or []) for b in bars):
        flags.append("roll")
    opens = [max(a, start) for e in touched for a, b in open_spans(inst["hours"], e, cfg)
             if a < end and b > start]
    if opens and bars[0]["start"] > min(opens) + datetime.timedelta(minutes=60):
        flags.append("late_start")
    return flags


def daily_from_hourly(bars_1h, day_end, inst, cfg, include_forming=False, now=None):
    """Daily bars {fx_day, start, end, open, high, low, close, hours, flags} from done 1h
    bars, cut at day_end. The newest FX day is left out while it is forming (it holds now,
    or, with no now, its last bar is a forming bar or ends before its last data close)."""
    check_day_end(day_end)
    groups = {}
    for b in bars_1h:
        d = fx_day(b["start"], day_end)
        if d is not None:
            groups.setdefault(d, []).append(b)
    outages = find_outages([b for b in bars_1h if not b.get("forming")], inst, "1h", cfg)
    out = []
    for d in sorted(groups):
        bars = sorted(groups[d], key=lambda b: b["start"])
        start, end = fx_day_bounds(d, day_end)
        day = {"fx_day": d, "start": start, "end": end, "open": bars[0]["open"],
               "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars),
               "close": bars[-1]["close"], "hours": len(bars), "flags": []}
        day["flags"] = _daily_flags(day, bars, inst, cfg, outages)
        out.append(day)
    if out and _day_forming(out[-1], groups[out[-1]["fx_day"]], inst, cfg, now):
        if include_forming:
            out[-1]["flags"].append("forming")
        else:
            out.pop()
    return out


def _day_forming(day, bars, inst, cfg, now):
    """True when a daily bar is not over yet: now is before its end; with no now, its
    last 1h bar is forming or ends before the last data close inside the day."""
    if now is not None:
        return now < day["end"]
    last = max(bars, key=lambda b: b["start"])
    if last.get("forming"):
        return True
    closes = [min(b, day["end"]) for e in _touched_fx_days(day["start"], day["end"])
              for a, b in open_spans(inst["data_hours"], e, cfg) if a < day["end"] and b > day["start"]]
    return bool(closes) and last["start"] + datetime.timedelta(hours=1) < max(closes)


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


RATE_PAIRS = {"EUR": ("EURUSD", "mul"), "GBP": ("GBPUSD", "mul"), "AUD": ("AUDUSD", "mul"),
              "NZD": ("NZDUSD", "mul"), "JPY": ("USDJPY", "div"), "CHF": ("USDCHF", "div"),
              "CAD": ("USDCAD", "div")}
def _num_word(x):
    """A plain number for messages: 24 -> "24", 30.5 -> "30.5"."""
    return format(_dec_clean(x).normalize(), "f")


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


def result_core(inst, side, entry, exit_price, nights, rates, cfg):
    """A closed trade of 1 lot after all costs, unrounded Decimal (6.10 on the cores;
    the swap with price=entry). exit_price: the bid for a buy, the ask for a sell."""
    return _pl_core(inst, side, entry, exit_price, nights, rates, cfg) - _commission(inst, 1)


def bar_hits(side, bar, stop_loss, take_profit, inst, margin_points=0):
    """D5 on one Yahoo (bid) bar: "stop", "target", "both" (order unknown: callers count
    it as the stop) or None. A buy's levels fire on the bid, a sell's on the ask. A bar
    that OPENS past a level hit that level first."""
    side_word(side)
    s = _dec_clean(spread_price(inst))
    m = _dec_clean(margin_points) * _dec_clean(inst["point"])
    o, h, lo = _dec_clean(bar["open"]), _dec_clean(bar["high"]), _dec_clean(bar["low"])
    sl = None if stop_loss is None else _dec_clean(stop_loss)
    tp = None if take_profit is None else _dec_clean(take_profit)
    if side == "buy":
        if sl is not None and o <= sl:
            return "stop"
        if tp is not None and o >= tp:
            return "target"
        stop = sl is not None and lo <= sl + m
        target = tp is not None and h >= tp
    else:
        if sl is not None and o + s >= sl:
            return "stop"
        if tp is not None and o + s <= tp:
            return "target"
        stop = sl is not None and h + s >= sl - m
        target = tp is not None and lo + s <= tp
    if stop and target:
        return "both"
    return "stop" if stop else ("target" if target else None)


def pending_fill(side, entry_type, bar, entry, inst):
    """The fill price of an entry order in one bar, or None: a stop or limit fills at
    its price, or at the open when the bar opens past it; market fills at the open
    (a buy at the ask). In the fill bar only a stop hit counts (the safe side)."""
    side_word(side)
    o = _dec_clean(bar["open"])
    if entry_type == "market":
        return ask_of(o, inst) if side == "buy" else round_price(o, inst)
    if entry_type not in ("stop", "limit"):
        raise ValueError('The order type must be "market", "limit" or "stop".')   # (check) words
    s = _dec_clean(spread_price(inst))
    p = _price_dec(entry)
    h, lo = _dec_clean(bar["high"]), _dec_clean(bar["low"])
    if side == "buy" and entry_type == "stop":
        fill = (o + s) if o + s >= p else (p if h + s >= p else None)
    elif side == "buy":
        fill = (o + s) if o + s <= p else (p if lo + s <= p else None)
    elif entry_type == "stop":
        fill = o if o <= p else (p if lo <= p else None)
    else:
        fill = o if o >= p else (p if h >= p else None)
    return None if fill is None else round_price(fill, inst)


def gap_fill(side, bar_open, stop_loss, inst):
    """The fill when a bar opens past the stop (it becomes a market order): a buy at the
    open bid, a sell at the open ask. None when the open is not past the stop."""
    side_word(side)
    o, sl = _dec_clean(bar_open), _dec_clean(stop_loss)
    if side == "buy":
        return round_price(o, inst) if o <= sl else None
    a = o + _dec_clean(spread_price(inst))
    return round_price(a, inst) if a >= sl else None


SKIP_FLAGS = ("roll", "outage", "partial", "holiday", "forming")
def usable_bars(bars, outages=None):
    """New copies of the bars without the SKIP_FLAGS ones; each gets "after_gap": True
    on the first bar, after a dropped bar, or at the end of an outage, else False."""
    ends = set(to for _frm, to in (outages or []))
    out = []
    dropped = True
    for bar in bars:
        if any(f in SKIP_FLAGS for f in (bar.get("flags") or ())):
            dropped = True
            continue
        b = dict(bar)
        b["after_gap"] = bool(dropped or b.get("start") in ends)
        dropped = False
        out.append(b)
    return out


def true_range(bar, prev_close):
    """The true range of one bar: high - low, or the widest move from prev_close
    (prev_close None, or the bar after a gap: just high - low). Not rounded."""
    hl = bar["high"] - bar["low"]
    if prev_close is None or bar.get("after_gap"):
        return hl
    return max(hl, abs(bar["high"] - prev_close), abs(bar["low"] - prev_close))


def atr_wilder(bars, n=14):
    """Wilder ATR(n) of usable_bars output (the caller cuts the ATR_WINDOW first): the
    mean of the first n true ranges, then Wilder smoothing. None if under n + 1 bars or 0."""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        prev = None if bars[i].get("after_gap") else bars[i - 1]["close"]
        trs.append(true_range(bars[i], prev))
    atr = sum(trs[:n]) / float(n)
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr if atr > 0 else None


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


# ---------------------------------------------------------------- own helpers (timemachine.py only)

MIN_POOL_DAYS = 120        # under this many usable daily bars: a "rough" note (05 6.1)
MIN_POOL_DAYS_LOW = 40     # under this many: refused
MIN_SESSIONS_DAY = 20      # full past days of 5m bars a day replay needs
BLOCK_DAYS = 5             # swing: days per block
BLOCK_BARS = 3             # day: 5m bars per block
WINDOW_BARS = 2            # day: the same time of day, plus or minus this many bars
SCALE_CLAMP = (0.5, 2.0)   # swing: the ATR scale of one past day stays in this range
RUNS = 10000
SEED = 19
MAX_BANDS = 60             # day: at most this many fan steps
ATR_DAYS = 250             # the last 250 usable days make an ATR (05 2.1)
PLAN_FIELDS = ("chart_entry", "chart_stop", "entry", "stop_loss", "take_profit")
HIST_FROM, HIST_WIDTH, HIST_BINS = -2.0, 0.25, 24
FAN_ROWS = 12
EPS = 5e-10                # float slack: fx_core's Decimals are cleaned to 9 places


def _q(x, places):
    """x (float or Decimal) half up to places decimals, as a float (-0.0 becomes 0.0)."""
    d = Decimal(repr(x)) if isinstance(x, float) else Decimal(x)
    return float(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)) + 0.0


def pct(v, q):
    """The q-th percentile of v: sorted(v)[int(q * (len(v) - 1) + 0.5)] (05 6.5)."""
    s = sorted(v)
    return s[int(q * (len(s) - 1) + 0.5)]


def tm_pct(share):
    """A share 0..1 as "31.3 %" (one decimal, a space before the sign)."""
    return "%s %%" % format(Decimal(repr(float(share) * 100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")


def tm_whole_pct(share):
    """A share 0..1 as a whole percent, half up: 0.536 -> 54."""
    return int(Decimal(repr(float(share) * 100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tm_name(inst, start=False):
    """How the printout names the market: the key for an FX pair ("EURUSD"), the name
    for metals and energy ("gold", or "Gold" at the start of a sentence). (check)"""
    if inst.get("kind") == "fx":
        return inst["key"]
    name = str(inst.get("name") or inst["key"])
    if start:
        return name[0].upper() + name[1:]
    if not (len(name) > 1 and name[1].isupper()):
        name = name[0].lower() + name[1:]
    return name


def read_plan(path, cfg):
    """plan.json with the common checks (01 1.3) and the plan.json row of 01 5.2,
    then entry_at, the entry type and the nights. Returns the plan dict."""
    plan = load_doc(path, "plan", "fx-exit")
    check_symbol(plan.get("symbol"), cfg, path)
    side = check_side(plan.get("side"), path)
    style = check_style(plan.get("style"), path)
    for key in ("chart_entry", "entry", "stop_loss", "take_profit"):
        check_price(plan, key, path)
    e, sl, tp = plan["entry"], plan["stop_loss"], plan["take_profit"]
    if (side == "buy" and (sl >= e or tp <= e)) or (side == "sell" and (sl <= e or tp >= e)):
        raise ValueError("%s: the stop or the target is on the wrong side of the entry. "
                         "Run fx-exit again." % path)
    inst = instrument(cfg, plan["symbol"])
    if plan.get("risk_points") != to_points(abs(_dec_clean(e) - _dec_clean(sl)), inst):
        raise ValueError("%s does not add up (risk_points). Run fx-exit again." % path)
    need = "exit_by" if style == "swing" else "flat_by_utc"
    if not plan.get(need):
        raise ValueError('%s: a %s plan needs "%s"' % (path, style, need))
    check_utc(plan, need, path)
    if not plan.get("entry_at"):
        raise ValueError('%s has no "entry_at". Run fx-exit again.' % path)
    check_utc(plan, "entry_at", path)
    if parse_utc(str(plan.get("fx_day") or "") + "T00:00:00Z") is None:
        raise ValueError('%s: "fx_day" must be a date like 2026-07-17. Run fx-exit again.' % path)   # (check) words
    plan = dict(plan)
    plan.setdefault("entry_type", "market")
    if plan["entry_type"] not in ("market", "stop", "limit"):
        raise ValueError('%s: "entry_type" must be "market", "stop" or "limit", not "%s"'
                         % (path, plan["entry_type"]))   # (check) words
    costs = plan.get("costs") if isinstance(plan.get("costs"), dict) else {}
    nights = costs.get("nights_est") or 0
    if isinstance(nights, bool) or not isinstance(nights, int) or nights < 0:
        raise ValueError('%s: "costs.nights_est" must be a whole number of nights' % path)   # (check) words
    plan["costs"] = dict(costs, nights_est=nights)
    return plan


def roll_lines(plan, rolls, ids, inst):
    """One line per roll the in-memory rebase applied (02 5.8)."""
    old = plan.get("rolls_applied") or {}
    out = []
    for r in rolls:
        if r.get("id") not in ids:
            continue
        ratio = float(r["ratio"])
        if old.get(r["id"]) is not None:
            ratio = ratio / float(old[r["id"]])
        out.append("%s rolled on Yahoo (%+.2f%%). Your plan levels moved %s by the same amount. "
                   "Your broker's %s did not jump: keep the SL and TP you set in MetaTrader."
                   % (tm_name(inst, start=True), (ratio - 1) * 100, "up" if ratio >= 1 else "down",
                      inst["key"]))
    return out


def _factors(ro, rh, rl, rc):
    """The four ratios as multipliers of the close before: (e^ro, e^rh, e^rl, e^rc)."""
    return (math.exp(ro), math.exp(rh), math.exp(rl), math.exp(rc))


def swing_pool(U, raw, cfg):
    """05 6.2 on the usable daily bars before the plan: (pool, info). Each pool day has
    fx_day, wd (weekday), run (id), r (the four ratios after drift and scale) and f
    (their multipliers). info: mu, a_now, close_now, from, to."""
    rows = []
    for i in range(1, len(U)):
        if U[i].get("after_gap"):
            continue
        a_prev = atr_wilder(U[max(0, i - ATR_DAYS):i])
        if a_prev is None:
            continue
        pc = U[i - 1]["close"]
        r = [math.log(U[i][k] / pc) for k in ("open", "high", "low", "close")]
        rows.append({"fx_day": U[i]["fx_day"], "r": r, "atr_pct": a_prev / pc})
    a_now = atr_wilder(U[-ATR_DAYS:])
    close_now = U[-1]["close"] if U else None
    mu = sum(x["r"][3] for x in rows) / len(rows) if rows else 0.0
    pool = []
    run = 0
    for n, x in enumerate(rows):
        if n and x["fx_day"] != next_fx_day(rows[n - 1]["fx_day"], cfg):
            run += 1
        s = 1.0
        if a_now is not None:
            s = (a_now / close_now) / x["atr_pct"]
            s = min(max(s, SCALE_CLAMP[0]), SCALE_CLAMP[1])
        r = [(v if raw else v - mu) * s for v in x["r"]]
        wd = datetime.date(*[int(p) for p in x["fx_day"].split("-")]).weekday()
        pool.append({"fx_day": x["fx_day"], "wd": wd, "run": run, "r": r, "f": _factors(*r)})
    info = {"mu": mu, "a_now": a_now, "close_now": close_now,
            "from": pool[0]["fx_day"] if pool else None, "to": pool[-1]["fx_day"] if pool else None}
    return pool, info


def path_days(fx_date, exit_by, cfg, max_days=None):
    """The FX days of one swing path: fx_date, then next_fx_day, up to the FX day that
    ends at exit_by (at most max_days days; default the config's max_hold_days)."""
    limit = max_days or ((cfg.get("swing") or {}).get("max_hold_days")) or 20
    days = [fx_date]
    while fx_day_bounds(days[-1], cfg["day_end"])[1] < exit_by and len(days) < limit:
        days.append(next_fx_day(days[-1], cfg))
    return days


def swing_cands(pool, path):
    """4.3 step 2: {k: (L, [pool index j, ...])} for each block start k = 0, 5, 10 ...
    Same weekday as path day k first; else every start with a long enough run."""
    out = {}
    for k in range(0, len(path), BLOCK_DAYS):
        size = min(BLOCK_DAYS, len(path) - k)
        wd = datetime.date(*[int(p) for p in path[k].split("-")]).weekday()
        ok = [j for j in range(len(pool) - size + 1) if pool[j + size - 1]["run"] == pool[j]["run"]]
        same = [j for j in ok if pool[j]["wd"] == wd]
        out[k] = (size, same or ok)
    return out


FIVE_MIN = datetime.timedelta(minutes=5)


def _slot_of(start, s0):
    """The 5m slot number of a bar start counted from s0 (None when not on the grid)."""
    sec = (start - s0).total_seconds()
    if sec % 300:
        return None
    return int(sec // 300)


def _short_holes(ratios, slots):
    """True when the day has ratios at the first and last expected slot and every run of
    expected slots without ratios is shorter than an outage (OUTAGE_MIN_SLOTS["5m"]).
    Yahoo FX drops single 5m bars; such a day still counts as full, and the block draws
    skip the hole. (check: 05 6.4 says "every slot")"""
    if slots[0] not in ratios or slots[-1] not in ratios:
        return False
    hole = 0
    for q in slots:
        hole = 0 if q in ratios else hole + 1
        if hole >= OUTAGE_MIN_SLOTS["5m"]:
            return False
    return True


def day_pool(U5, plan, inst, cfg):
    """05 6.4 / 4.4: (sessions, slots, info). slots = the plan's expected slot numbers;
    sessions = [(fx_day, {slot: factors})] of the full past days in date order; info has
    the session name, the slot times and the counts."""
    day_end = cfg["day_end"]
    session = (plan.get("opening_range") or {}).get("session") or cfg["day"]["or_session"]
    fx_date = plan["fx_day"]
    s0 = session_bounds(session, fx_date)[0]
    entry_at = parse_utc(plan["entry_at"])
    flat = parse_utc(plan["flat_by_utc"])
    e = int(math.ceil((entry_at - s0).total_seconds() / 300.0))
    f = int(math.floor((flat - s0).total_seconds() / 300.0)) - 1
    spans = open_spans(inst["data_hours"], fx_date, cfg)
    slots = [q for q in range(e, f + 1)
             if any(a <= s0 + q * FIVE_MIN < b for a, b in spans)]
    info = {"session": session, "first": s0 + slots[0] * FIVE_MIN if slots else None,
            "slots": len(slots)}
    early = set(EARLY_CLOSES) | set((cfg.get("early_closes") or {}))
    by_day = {}
    for b in U5:
        d = fx_day(b["start"], day_end)
        if d is None or d >= fx_date:
            continue
        by_day.setdefault(d, []).append(b)
    sessions = []
    for d in sorted(by_day):
        if d in early or holiday_note(d, inst, cfg) is not None:
            continue
        sd = session_bounds(session, d)[0]
        bars = {}
        for b in by_day[d]:
            q = _slot_of(b["start"], sd)
            if q is not None:
                bars[q] = b
        ratios = {}
        for q, b in bars.items():
            p = bars.get(q - 1)
            if p is None:
                continue
            pc = p["close"]
            ratios[q] = _factors(math.log(b["open"] / pc), math.log(b["high"] / pc),
                                 math.log(b["low"] / pc), math.log(b["close"] / pc))
        if slots and _short_holes(ratios, slots):
            sessions.append((d, ratios))
    info["sessions"] = len(sessions)
    info["from"] = sessions[0][0] if sessions else None
    info["to"] = sessions[-1][0] if sessions else None
    return sessions, slots, info


def day_cands(sessions, slots):
    """4.5 step 1: {k: (L, [(session index, first slot), ...])} for k = 0, 3, 6 ...;
    sessions in date order, then delta from -2 to +2."""
    out = {}
    for k in range(0, len(slots), BLOCK_BARS):
        size = min(BLOCK_BARS, len(slots) - k)
        q = slots[k]
        got = []
        for n, (_d, ratios) in enumerate(sessions):
            for delta in range(-WINDOW_BARS, WINDOW_BARS + 1):
                if all(q + delta + i in ratios for i in range(size)):
                    got.append((n, q + delta))
        out[k] = (size, got)
    return out


def hit_lines(side, stop_loss, take_profit, inst, margin_points=0):
    """The plan's levels as bid lines, once (exact Decimal, then float):
    (stop by the open, target by the open, stop in the bar, target in the bar).
    buy: SL, TP, SL + m, TP; sell: SL - s, TP - s, SL - s - m, TP - s."""
    s = _dec_clean(spread_price(inst))
    m = _dec_clean(margin_points) * _dec_clean(inst["point"])
    sl, tp = _dec_clean(stop_loss), _dec_clean(take_profit)
    if side == "buy":
        return (float(sl), float(tp), float(sl + m), float(tp))
    return (float(sl - s), float(tp - s), float(sl - s - m), float(tp - s))


def fast_hits(side, o, h, l, lines):
    """bar_hits on floats with the lines of hit_lines: "stop", "target", "both" or None.
    The same answer as bar_hits (the open rules first). About 1.3 million bar checks a
    day run, so no Decimal here. (check)"""
    sl_open, tp_open, sl_in, tp_in = lines
    if side == "buy":
        if o <= sl_open + EPS:
            return "stop"
        if o >= tp_open - EPS:
            return "target"
        stop = l <= sl_in + EPS
        target = h >= tp_in - EPS
    else:
        if o >= sl_open - EPS:
            return "stop"
        if o <= tp_open + EPS:
            return "target"
        stop = h >= sl_in - EPS
        target = l <= tp_in + EPS
    if stop and target:
        return "both"
    return "stop" if stop else ("target" if target else None)


def _may_fill(side, entry_type, h, l, p, s):
    """A quick float test: can this bar fill the pending entry at p? (pending_fill then
    gives the exact fill; this only skips the bars that surely do not fill.)"""
    if side == "buy":
        return h + s >= p - EPS if entry_type == "stop" else l + s <= p + EPS
    return l <= p + EPS if entry_type == "stop" else h >= p - EPS


def _blocks(mode, pool, cands):
    """The candidate blocks as lists of factor tuples: [(k, [[f, f, ...], ...]), ...]."""
    out = []
    for k in sorted(cands):
        size, opts = cands[k]
        if mode == "swing":
            got = [[pool[j + i]["f"] for i in range(size)] for j in opts]
        else:
            got = [[pool[n][1][q + i] for i in range(size)] for n, q in opts]
        out.append((k, got))
    return out


def replay(mode, plan, inst, cfg, pool, cands, rng, runs, tables):
    """4.3 (swing) or 4.5 (day) for all runs. tables: steps, nights (steps + 1 values:
    a hit on step k, then neither), band_steps (0-based), rates, l1, margin_points.
    Returns the counts, the R list and the bid closes of the band steps."""
    side, etype = plan["side"], plan["entry_type"]
    buy = side == "buy"
    p0, entry = float(plan["chart_entry"]), float(plan["entry"])
    sl, tp = float(plan["stop_loss"]), float(plan["take_profit"])
    s = spread_price(inst)
    lines = hit_lines(side, plan["stop_loss"], plan["take_profit"], inst, tables["margin_points"])
    sl_open, tp_open = lines[0], lines[1]
    nights_tab, l1, rates = tables["nights"], tables["l1"], tables["rates"]
    n_steps = tables["steps"]
    closes = [None] * n_steps
    for t in tables["band_steps"]:
        closes[t] = []
    blocks = _blocks(mode, pool, cands)
    counts = {"target": 0, "stop": 0, "neither": 0, "gap": 0, "green": 0, "filled": 0}
    rs, cache = [], {}
    for _run in range(runs):
        steps = []
        for _k, opts in blocks:
            steps.extend(opts[rng.randrange(len(opts))])
        prev = p0 / steps[0][0]
        entered = etype == "market"
        run_entry = entry if entered else None
        result = None
        c = p0
        for t, (eo, eh, el, ec) in enumerate(steps):
            o, h, l, c = prev * eo, prev * eh, prev * el, prev * ec
            prev = c
            if closes[t] is not None:
                closes[t].append(c)
            if result is not None:
                continue
            if not entered:
                if _may_fill(side, etype, h, l, entry, s):
                    fp = pending_fill(side, etype, {"open": o, "high": h, "low": l}, entry, inst)
                    if fp is not None:
                        entered, run_entry = True, fp
                        if fast_hits(side, o, h, l, lines) in ("stop", "both"):
                            result = ("stop", sl, t)
                continue
            if t > 0:
                past_stop = o <= sl_open + EPS if buy else o >= sl_open - EPS
                if past_stop:
                    g = gap_fill(side, o, sl, inst)
                    px = sl if g is None else g
                    if (buy and px < sl) or (not buy and px > sl):
                        counts["gap"] += 1
                    result = ("stop", px, t)
                    continue
                if (o >= tp_open - EPS) if buy else (o <= tp_open + EPS):
                    result = ("target", tp, t)
                    continue
            hit = fast_hits(side, o, h, l, lines)
            if hit in ("stop", "both"):
                result = ("stop", sl, t)
            elif hit == "target":
                result = ("target", tp, t)
        if not entered:
            counts["neither"] += 1
            rs.append(0.0)
            continue
        counts["filled"] += 1
        if result is None:
            result = ("neither", c if buy else c + s, n_steps)
        kind, px, t = result
        nights = nights_tab[t]
        key = (px, nights, run_entry)
        if key not in cache:
            cache[key] = float(result_core(inst, side, run_entry, px, nights, rates, cfg) / l1)
        rs.append(cache[key])
        counts[kind] += 1
        if kind == "target" or (kind == "neither" and (px > run_entry if buy else px < run_entry)):
            counts["green"] += 1
    return {"counts": counts, "r": rs, "closes": closes, "runs": runs}


def band_steps(n):
    """The 0-based steps the fan keeps: all when n <= MAX_BANDS, else every
    ceil(n / 60)-th step (steps 3, 6, ...) and always the last. (core gap, check)"""
    if n <= MAX_BANDS:
        return list(range(n))
    every = int(math.ceil(n / float(MAX_BANDS)))
    out = [t for t in range(n) if (t + 1) % every == 0]
    if out[-1] != n - 1:
        out.append(n - 1)
    return out


def summarize(raw, l1, plan, inst):
    """4.6: odds, r, usd_per_lot, gap_through_stop, green, bands, histogram, plus "_"
    keys with the unrounded shares for the verdict and the printout."""
    runs = raw["runs"]
    c = raw["counts"]
    rs = raw["r"]
    digits = int(inst["digits"])
    share = {k: c[k] / float(runs) for k in ("target", "stop", "neither", "gap", "green")}
    mean = sum(rs) / float(len(rs))
    l1f = float(l1)
    usd = [x * l1f for x in rs]
    bands = []
    for t, v in enumerate(raw["closes"]):
        if v is None:
            continue
        s = sorted(v)
        row = {"step": t + 1}
        for name, q in (("p5", 0.05), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p95", 0.95)):
            row[name] = _q(s[int(q * (len(s) - 1) + 0.5)], digits)
        bands.append(row)
    counts = [0] * HIST_BINS
    for x in rs:
        b = int(math.floor((x - HIST_FROM) / HIST_WIDTH))
        counts[min(max(b, 0), HIST_BINS - 1)] += 1
    return {
        "odds": {"target_first": _q(share["target"], 4), "stop_first": _q(share["stop"], 4),
                 "neither": _q(share["neither"], 4)},
        "r": {"p5": _q(pct(rs, 0.05), 3), "p25": _q(pct(rs, 0.25), 3), "median": _q(pct(rs, 0.5), 3),
              "p75": _q(pct(rs, 0.75), 3), "p95": _q(pct(rs, 0.95), 3), "mean": _q(mean, 3)},
        "usd_per_lot": {"p5": _q(pct(usd, 0.05), 2), "median": _q(pct(usd, 0.5), 2),
                        "p95": _q(pct(usd, 0.95), 2)},
        "gap_through_stop": _q(share["gap"], 4),
        "green": _q(share["green"], 4),
        "bands": bands,
        "histogram": {"from": HIST_FROM, "width": HIST_WIDTH, "counts": counts},
        "_share": share, "_mean": mean,
    }


def verdict_lines(summary, plan, inst, cfg):
    """4.7 (05 6.7 word for word): (verdict_key, lines). summary needs _share, _mean,
    _costs (Decimal USD a lot), _net (net target R), _l1, mode, history.bars, seed."""
    sh = summary["_share"]
    tf, sf, nei = sh["target"], sh["stop"], sh["neither"]
    rn = float(summary["_net"])
    costs = summary["_costs"]
    big_r = float(plan.get("target_r") or 0)
    lines = []
    lean = None
    if rn <= 0:
        key = "costs_eat"
        pays = _dec_clean(summary["_net"]) * summary["_l1"] + costs
        lines.append("Costs eat the target: they are %s a lot, and the target pays %s a lot "
                     "before costs." % (money(costs), money(pays)))
    elif tf + sf < 0.10:
        key = "too_few"
        lines.append("Too few replays hit the stop or the target to judge (%s)." % tm_pct(tf + sf))
    else:
        be = 1.0 / (1.0 + rn)
        seen = tf / (tf + sf)
        d = (seen - be) * 100
        tail = ("at %gR after costs the target has to come first in %d %% of the replays that "
                "hit one side; it did in %d %%." % (big_r, tm_whole_pct(be), tm_whole_pct(seen)))
        lean = "target" if d > 0 else "stop"
        if abs(d) < 5 and big_r == 1 and costs == 0:
            key, lean = "coin_flip", None
            lines.append("Close to a coin flip.")
        elif abs(d) < 5:
            key, lean = "break_even", None
            lines.append("Close to break-even: " + tail)
        elif abs(d) < 15:
            key = "leans_" + lean
            lines.append("Leans toward the %s: %s" % (lean, tail))
        else:
            key = "strong_" + lean
            lines.append("Strongly toward the %s: %s" % (lean, tail))
    if nei > 0.40:
        if summary["mode"] == "day":
            when = pkt_label(parse_utc(plan["flat_by_utc"]))
        else:
            when = pkt_label(parse_utc(plan["exit_by"]), date=True)
        lines.append("%s of the replays hit neither by %s; the price then decides those."
                     % (tm_pct(nei), when))
    mean = summary["_mean"]
    if (lean == "target" and mean < 0) or (lean == "stop" and mean > 0):
        lines.append("The average result is %.3fR, costs included." % mean)
    if sh["gap"] > 0:
        lines.append("%s of the replays gapped past the stop: the loss was bigger than 1R."
                     % tm_pct(sh["gap"]))
    days = summary["history"]["bars"]
    if summary["mode"] == "day":
        what = "%d days of 5-minute bars" % days
    else:
        what = "%d days" % days
    lines.append("A simulation built from %s's own past %s. It is not a prediction: news, rolls "
                 "and gaps can do things those days never did." % (tm_name(inst, start=True), what))
    lines.append("Same seed, same numbers: repeat with --seed %d." % summary["seed"])
    return key, lines


ONE_MIN = datetime.timedelta(minutes=1)


def _swing_setup(plan, inst, cfg, data_dir, now, rolls, raw, notes):
    """4.2 and the tables of 4.3: (pool, cands, tables, info, history)."""
    key = inst["key"]
    bars_1h = load_bars(data_dir, inst["yahoo"], "1h", now, rolls=rolls, inst=inst, cfg=cfg)
    if not bars_1h:
        raise ValueError("No hourly prices for %s yet (%s). Run without --offline once to get them."
                         % (key, cache_file(data_dir, inst["yahoo"], "1h")))
    daily = daily_from_hourly(bars_1h, cfg["day_end"], inst, cfg, now=now)
    usable = usable_bars(daily, find_outages(bars_1h, inst, "1h", cfg))
    usable = [b for b in usable if b["fx_day"] < plan["fx_day"]]
    n = len(usable)
    if n < MIN_POOL_DAYS_LOW:
        raise ValueError("Only %d usable daily bars before this plan (need %d). Too few for a replay."
                         % (n, MIN_POOL_DAYS_LOW))
    if n < MIN_POOL_DAYS:
        notes.append("Note: Only %d usable daily bars before this plan (%d is the normal minimum). "
                     "The odds are rough." % (n, MIN_POOL_DAYS))
    pool, info = swing_pool(usable, raw, cfg)
    if info["a_now"] is None:
        raise ValueError("Too few bars for an ATR of %s (need 15 usable bars)." % key)
    entry_at, exit_by = parse_utc(plan["entry_at"]), parse_utc(plan["exit_by"])
    if exit_by <= entry_at:
        raise ValueError("The exit-by time is at or before the entry. There is nothing to replay.")
    path = path_days(plan["fx_day"], exit_by, cfg, plan.get("max_hold_days"))
    cands = swing_cands(pool, path)
    for k in sorted(cands):
        if not cands[k][1]:
            raise ValueError("The past days have no run of %d days in a row. There is nothing to "
                             "replay." % cands[k][0])
    nights = [swap_nights(entry_at, fx_day_bounds(d, cfg["day_end"])[0] + ONE_MIN, inst, cfg)
              for d in path]
    nights.append(swap_nights(entry_at, exit_by - ONE_MIN, inst, cfg))
    info.update({"path": path, "n_usable": n, "until": exit_by})
    history = {"from": info["from"], "to": info["to"], "bars": len(pool), "interval": "1d"}
    return pool, cands, {"steps": len(path), "nights": nights,
                         "band_steps": band_steps(len(path))}, info, history


def _day_setup(plan, inst, cfg, data_dir, now, rolls, notes):
    """4.4 and the tables of 4.5: (sessions, cands, tables, info, history)."""
    key = inst["key"]
    bars_5m = load_bars(data_dir, inst["yahoo"], "5m", now, rolls=rolls, inst=inst, cfg=cfg)
    if not bars_5m:
        raise ValueError("No 5-minute prices for %s yet (%s). The day time machine needs them."
                         % (key, cache_file(data_dir, inst["yahoo"], "5m")))
    u5 = usable_bars(bars_5m, find_outages(bars_5m, inst, "5m", cfg))
    sessions, slots, info = day_pool(u5, plan, inst, cfg)
    if not slots:
        raise ValueError("The flat-by time is at or before the entry. There is nothing to replay.")
    if len(sessions) < MIN_SESSIONS_DAY:
        raise ValueError("Only %d full past days of 5-minute bars for these hours (need %d)."
                         % (len(sessions), MIN_SESSIONS_DAY))
    cands = day_cands(sessions, slots)
    for k in sorted(cands):
        if not cands[k][1]:
            raise ValueError("The past days have no run of %d bars in a row. There is nothing to "
                             "replay." % cands[k][0])   # (check) words
    flat = parse_utc(plan["flat_by_utc"])
    if flat < now:
        notes.append("Note: This plan's flat-by time (%s) has passed. The replays still run to it."
                     % pkt_label(flat))
    info.update({"until": flat})
    history = {"from": info["from"], "to": info["to"], "bars": len(sessions), "interval": "5m"}
    return sessions, cands, {"steps": len(slots), "nights": [0] * (len(slots) + 1),
                             "band_steps": band_steps(len(slots))}, info, history


def run_machine(args):
    """The run subcommand without the printing: (doc, plan, inst, info, notes)."""
    now = parse_now(args.now)
    work = (os.path.dirname(args.config) or ".") if args.config else "fx"
    paths = fx_paths(work, args.data, args.config)
    cfg = load_config(paths["config"], required=bool(args.config))
    if not 100 <= args.runs <= 100000:
        raise ValueError("--runs must be 100 to 100,000, not %d." % args.runs)
    if not 0 <= args.seed <= 2147483647:
        raise ValueError("--seed must be a whole number from 0 to 2,147,483,647.")
    plan = read_plan(args.plan or paths["plan"], cfg)
    if args.raw and plan["style"] == "day":
        raise ValueError("--raw is for swing plans. A day plan keeps the drift anyway.")
    inst = instrument(cfg, plan["symbol"])
    data_dir = paths["data"]
    rolls = [r for r in load_rolls(data_dir)
             if parse_utc(r.get("found_at")) is None or parse_utc(r["found_at"]) <= now]
    plan2, ids = rebase(plan, rolls, PLAN_FIELDS, inst=inst)
    notes = roll_lines(plan, rolls, ids, inst)
    side = plan2["side"]
    entry_at = parse_utc(plan2["entry_at"])
    rates = fx_rates(data_dir, min(entry_at, now), cfg)
    nights_est = plan2["costs"]["nights_est"]
    entry, sl, tp = plan2["entry"], plan2["stop_loss"], plan2["take_profit"]
    l1 = max_loss_core(inst, side, entry, sl, nights_est, rates, cfg)
    costs = _costs(inst, side, 1, nights_est, rates, cfg, entry)
    net = net_core(inst, side, entry, sl, tp, nights_est, rates, cfg)
    mode = plan2["style"]
    if mode == "swing":
        pool, cands, tables, info, history = _swing_setup(plan2, inst, cfg, data_dir, now, rolls,
                                                          args.raw, notes)
    else:
        pool, cands, tables, info, history = _day_setup(plan2, inst, cfg, data_dir, now, rolls, notes)
    tables.update({"rates": rates, "l1": l1,
                   "margin_points": COARSE_MARGIN_PIPS * 10 if inst["key"] in COARSE else 0})
    started = time.monotonic()
    raw = replay(mode, plan2, inst, cfg, pool, cands, random.Random(args.seed), args.runs, tables)
    info["seconds"] = time.monotonic() - started
    summary = summarize(raw, l1, plan2, inst)
    doc = {"created_by": SKILL, "created_at": utc_iso(now), "mode": mode, "runs": args.runs,
           "seed": args.seed,
           "plan": {k: plan2.get(k) for k in ("symbol", "side", "style", "entry", "stop_loss",
                                               "take_profit", "fx_day")},
           "history": history}
    for k in ("odds", "r", "usd_per_lot"):
        doc[k] = summary[k]
    doc.update({"costs_r": _q(costs / l1, 3), "net_target_r": _q(net, 3),
                "max_loss_per_lot": _q(l1, 2), "gap_through_stop": summary["gap_through_stop"],
                "green": summary["green"], "until": utc_iso(info["until"])})
    summary.update({"_net": net, "_costs": costs, "_l1": l1, "mode": mode, "history": history,
                    "seed": args.seed})
    key, vlines = verdict_lines(summary, plan2, inst, cfg)
    doc.update({"verdict": vlines[0], "verdict_key": key, "drift_removed": mode == "swing" and not args.raw,
                "block": BLOCK_DAYS if mode == "swing" else BLOCK_BARS,
                "bands": summary["bands"], "histogram": summary["histogram"]})
    info.update({"now": now, "paths": paths, "summary": summary, "verdict": vlines, "rates": rates,
                 "costs": costs, "net": net, "l1": l1, "cfg": cfg, "raw": args.raw,
                 "margin": tables["margin_points"]})
    return doc, plan2, inst, info, notes


def _head_lines(doc, plan, inst, info):
    """The plan line and its price-source line."""
    side = plan["side"]
    out = ["%s %s %s plan: entry %s, stop %s, target %s (%gR)." % (
        inst["key"], side_word(side), plan["style"], price_text(plan["entry"], inst),
        price_text(plan["stop_loss"], inst), price_text(plan["take_profit"], inst),
        float(plan.get("target_r") or 0))]
    made = parse_utc(plan.get("created_at"))
    age = plan.get("data_age_min")
    src = "  %s, %s when planned" % (scale_note(inst), age_text(age))
    if made is not None:
        at = made - datetime.timedelta(minutes=float(age or 0))
        src += " (%s)" % pkt_label(at.replace(second=0))
    out.append(src + ".")
    return out


def _mode_lines(doc, plan, inst, info):
    """The mode, pool, drift and scale lines (swing) or the mode, pool and D5 lines (day)."""
    runs = format(doc["runs"], ",")
    h = doc["history"]
    if doc["mode"] == "swing":
        path = info["path"]
        mu = info["mu"] * 100
        out = ["Mode: swing. %s replays of up to %d FX days, %s to %s, seed %d." % (
            runs, len(path), day_label(_date(path[0])), day_label(_date(path[-1])), doc["seed"]),
            "The pool: %d past days of %s, %s to %s (daily bars built from Yahoo hourly bars)." % (
                h["bars"], tm_name(inst), h["from"], h["to"]),
            "Each replay strings together blocks of %d past days. Each block starts on the same "
            "weekday, so Mondays keep their weekend gaps." % BLOCK_DAYS]
        if info["raw"]:
            out.append("Drift kept (--raw): the pool's average day (%+.2f %%) is still in the "
                       "replays." % mu)
        else:
            out.append("Drift removed: the pool's average day (%+.2f %%) was taken out, so up and "
                       "down start even." % mu)
        out.append("Scaled to today: each past day is sized to today's range (ATR %.2f %% of the "
                   "price)." % (info["a_now"] / info["close_now"] * 100))
        return out
    flat = info["until"]
    first = info["first"]
    word = SESSION_WORDS.get(info["session"], info["session"])
    return ["Mode: day. %s replays of the rest of the FX day, %s to the flat-by time" % (
                runs, pkt_label(first)),
            "  %s (%s): %d five-minute bars, seed %d." % (
                pkt_label(flat), ny_label(flat), info["slots"], doc["seed"]),
            "Each bar is drawn from %s's own 5-minute bars at the same time of day (+-%d bars),"
            % (tm_name(inst), WINDOW_BARS),
            "  counted from the %s session open, in blocks of %d bars." % (word, BLOCK_BARS),
            "The pool: %d full past days, %s to %s." % (h["bars"], h["from"], h["to"]),
            ("A sell's stop and target fire on the ask (the bid plus the spread)."
             if plan["side"] == "sell" else "A buy's stop and target fire on the bid.")]


def _date(text):
    """"2026-07-17" -> datetime.date."""
    return datetime.date(*[int(p) for p in text.split("-")])


def _odds_lines(doc, plan, info):
    """The four odds lines."""
    sh = info["summary"]["_share"]
    until = info["until"]
    if doc["mode"] == "swing":
        end = "at the end, %s (%s)" % (pkt_label(until, date=True), ny_label(until))
        green_at = "at the end"
    else:
        end = green_at = "at %s PKT" % clock_label(to_pkt(until))
    way = "above" if plan["side"] == "buy" else "under"
    return ["  target first   %s" % tm_pct(sh["target"]),
            "  stop first     %s" % tm_pct(sh["stop"]),
            "  neither        %s   still open %s" % (tm_pct(sh["neither"]), end),
            "  green          %s   target first, or %s the entry %s (by price, before costs)"
            % (tm_pct(sh["green"]), way, green_at)]


def _money_lines(doc, plan, inst, info):
    """The price at the end, the costs, a swap credit and the average R."""
    cfg, rates = info["cfg"], info["rates"]
    side, entry = plan["side"], plan["entry"]
    nights = plan["costs"]["nights_est"] if doc["mode"] == "swing" else 0
    last = doc["bands"][-1]
    at = "the end" if doc["mode"] == "swing" else "%s PKT" % clock_label(to_pkt(info["until"]))
    out = ["  Price at %s: median %s, 90 %% of replays between %s and %s (Yahoo bid)." % (
        at, price_text(last["p50"], inst), price_text(last["p5"], inst), price_text(last["p95"], inst))]
    comm = _commission(inst, 1)
    fee = _fee(inst, 1, nights, cfg)
    swap = _swap(inst, side, 1, nights, rates, cfg, entry)
    cr = format(Decimal(repr(doc["costs_r"])), "f")
    if fee > 0:
        out.append("  Costs: %s commission and a %s swap-free fee for %d nights a lot = %sR." % (
            money(comm), money(fee), nights, cr))
    elif swap < 0:
        out.append("  Costs: %s a lot (%s commission, %s swap for %d nights) = %sR." % (
            money(info["costs"]), money(comm), money(-swap), nights, cr))
    else:
        out.append("  Costs: %s a lot (commission) = %sR." % (money(info["costs"]), cr))
    if swap > 0 and nights > 0:
        one = _swap(inst, side, 1, 1, rates, cfg, entry)
        out.append("  Swap %s a night is paid to you; it counts in each R." % signed_money(one))
    out.append("  Average R: %+.3f, costs included (1R = %s a lot: %+.2fR at the target, -1.00R at "
               "the stop)." % (info["summary"]["_mean"], money(info["l1"]), float(info["net"])))
    return out


def _fan_lines(doc, inst):
    """--fan: up to FAN_ROWS rows of the bands, evenly spread, always with the last."""
    bands = doc["bands"]
    if len(bands) <= FAN_ROWS:
        pick = list(range(len(bands)))
    else:
        pick = sorted(set(int(round(i * (len(bands) - 1) / float(FAN_ROWS - 1)))
                          for i in range(FAN_ROWS)))
    word = "day" if doc["mode"] == "swing" else "bar"
    out = ["Fan (Yahoo bid): step, low 5 %, middle, high 5 %"]
    for i in pick:
        b = bands[i]
        out.append("  %s %-4d %12s %12s %12s" % (word, b["step"], price_text(b["p5"], inst),
                                                 price_text(b["p50"], inst), price_text(b["p95"], inst)))
    return out


def tm_lines(doc, plan, inst, info, notes, quiet, fan):
    """Section 6: the words printout as a list of lines."""
    head = _head_lines(doc, plan, inst, info)
    odds = _odds_lines(doc, plan, info)
    verdict = info["verdict"]
    if quiet:
        return head[:1] + odds + verdict
    out = head + [n for n in notes if not n.startswith("Note: ")]
    out += _mode_lines(doc, plan, inst, info)
    if plan["entry_type"] != "market":
        out.append("The entry is a %s %s at %s. A replay that never fills counts as neither, at 0R."
                   % (plan["side"], plan["entry_type"], price_text(plan["entry"], inst)))
    out += [n for n in notes if n.startswith("Note: ")]
    if info["margin"]:
        out.append("Note: Yahoo %s prices are rough (about one price a minute). A stop counts as "
                   "hit %d pips early." % (inst["key"], COARSE_MARGIN_PIPS))
    out += [""] + odds + [""] + _money_lines(doc, plan, inst, info)
    if fan:
        out += [""] + _fan_lines(doc, inst)
    out += [""] + verdict
    if info.get("written"):
        out.append("Wrote %s." % info["paths"]["timemachine"])
    else:
        out.append("Not saved. Run again with --write to save %s." % info["paths"]["timemachine"])
    out.append("Next: python3 fx-budget/size.py --write sizes it to your max loss per trade, costs "
               "included. Nothing was placed.")
    return out


def build_parser():
    """The argparse parser: one subcommand, run."""
    parser = argparse.ArgumentParser(
        prog="timemachine.py",
        description="Replay a planned buy or sell 10,000 times from the market's own past "
                    "Yahoo bars. A simulation, not a prediction. Nothing is placed.")
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="replay fx/plan.json")
    add_common(run, write=True)
    run.add_argument("--plan", metavar="FILE", default=None,
                     help="the plan to replay (default fx/plan.json next to the config)")
    run.add_argument("--runs", type=int, default=RUNS, help="how many replays (100 to 100,000)")
    run.add_argument("--seed", type=int, default=SEED, help="the seed (default 19)")
    run.add_argument("--raw", action="store_true", help="swing only: keep the drift")
    run.add_argument("--fan", action="store_true", help="also print the price fan")
    return parser


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd != "run":
        parser.print_help()
        return 2
    try:
        doc, plan, inst, info, notes = run_machine(args)
        written = False
        if args.write:
            save_json_atomic(info["paths"]["timemachine"], doc)
            written = True
        info["written"] = written
        lines = tm_lines(doc, plan, inst, info, notes, args.quiet, args.fan)
    except ValueError as exc:
        return fail(exc, TOOL)
    if args.json:
        emit_json({"v": JSON_VERSION, "tool": SKILL, "cmd": "run", "now": utc_iso(info["now"]),
                   "ok": True, "notes": notes + info["verdict"], "doc": doc if written else None,
                   "result": doc, "written": written})
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
