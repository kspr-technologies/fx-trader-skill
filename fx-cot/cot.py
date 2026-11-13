#!/usr/bin/env python3
"""fx-cot: the COT Reader. Standard library only, Python 3.8.

Reads the CFTC Commitments of Traders (COT) report for your instruments: what the big
speculators hold (managed money for metals and energy, leveraged funds for currencies),
net long or short, the change in the week, net as a share of all open contracts, and
the COT index over 156 reports. It is context, never a timing signal. It never gives
advice and never places an order.

    python3 fx-cot/cot.py show [--symbols A,B | --all] [--weeks N] [--dry-run]
    python3 fx-cot/cot.py new  [--symbols A,B | --all] [--weeks N] [--dry-run]

Common flags: --config, --data, --offline, --now, --json, --quiet.
The CFTC is asked only when a new report is due (the cache is fx/data/cot/), and
never with --offline or --now.
"""

import argparse
import copy
import csv
import datetime
import difflib
import gzip
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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


# ---------------------------------------------------------------- copy together
# Each line is a helper copied below and the names from tests/fx_core.py it uses.
# "-" means it uses nothing else from fx_core.
#   load_json: -
#   save_json_atomic: -
#   fx_paths: -
#   load_doc: load_json
#   check_objects: -
#   check_symbol: -
#   check_side: -
#   check_style: -
#   _positive_number: -
#   check_lots: _positive_number
#   JSON_VERSION: -
#   _words_dec: -
#   lots_text: _words_dec
#   side_word: -
#   add_common: -
#   fail: -
#   emit_json: -
#   _dec_clean: -
#   instrument: _dec_clean
#   KARACHI_HOURS: -
#   DAY_NAMES: -
#   MONTH_NAMES: -
#   utc_now: -
#   to_pkt: KARACHI_HOURS
#   pkt_to_utc: KARACHI_HOURS
#   parse_now: pkt_to_utc, utc_now
#   utc_iso: -
#   parse_utc: -
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
#   fx_day_bounds: check_day_end, ny_to_utc, parse_fx_date
#   pkt_time_on_fx_day: fx_day_bounds, parse_hhmm, pkt_to_utc, to_pkt
#   opening_range_window: OR_WINDOWS, at_time, city_to_utc, parse_fx_date
#   COT_RELEASES: -
#   BROKER_TEMPLATES: -
#   DATA_TEMPLATES: -
#   UA: -
#   PACE_SECONDS: -
#   TIMEOUT: -
#   SLEEP: -
#   _gunzip: -
#   MONTH_CODES: -
#   SIGNAL_BANNED: -
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


def _positive_number(x):
    """True when x is a finite int or float above 0 (never a bool or text)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and x > 0


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


COT_RELEASES = {"time": "15:30", "provisional_years": [2027], "dates": [
    "2026-01-05", "2026-01-09", "2026-01-16", "2026-01-23", "2026-01-30", "2026-02-06",
    "2026-02-13", "2026-02-20", "2026-02-27", "2026-03-06", "2026-03-13", "2026-03-20",
    "2026-03-27", "2026-04-03", "2026-04-10", "2026-04-17", "2026-04-24", "2026-05-01",
    "2026-05-08", "2026-05-15", "2026-05-22", "2026-05-29", "2026-06-05", "2026-06-12",
    "2026-06-22", "2026-06-26", "2026-07-06", "2026-07-10", "2026-07-17", "2026-07-24",
    "2026-07-31", "2026-08-07", "2026-08-14", "2026-08-21", "2026-08-28", "2026-09-04",
    "2026-09-11", "2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16",
    "2026-10-23", "2026-10-30", "2026-11-06", "2026-11-16", "2026-11-20", "2026-11-30",
    "2026-12-04", "2026-12-11", "2026-12-18", "2026-12-28", "2027-01-04", "2027-01-08",
    "2027-01-15", "2027-01-22", "2027-01-29", "2027-02-05", "2027-02-12", "2027-02-19",
    "2027-02-26", "2027-03-05", "2027-03-12", "2027-03-19", "2027-03-26", "2027-04-02",
    "2027-04-09", "2027-04-16", "2027-04-23", "2027-04-30", "2027-05-07", "2027-05-14",
    "2027-05-21", "2027-05-28", "2027-06-04", "2027-06-11", "2027-06-21", "2027-06-25",
    "2027-07-02", "2027-07-09", "2027-07-16", "2027-07-23", "2027-07-30", "2027-08-06",
    "2027-08-13", "2027-08-20", "2027-08-27", "2027-09-03", "2027-09-10", "2027-09-17",
    "2027-09-24", "2027-10-01", "2027-10-08", "2027-10-15", "2027-10-22", "2027-10-29",
    "2027-11-05", "2027-11-15", "2027-11-19", "2027-11-29", "2027-12-03", "2027-12-10",
    "2027-12-17", "2027-12-27", "2028-01-03",
]}


BROKER_TEMPLATES = ("fx", "comex", "nymex", "ice_brent")


DATA_TEMPLATES = ("yahoo_fx", "cme")


UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"


PACE_SECONDS = 2.0     # at least this long between any two Yahoo requests (both hosts)


TIMEOUT = 25           # socket timeout in seconds


SLEEP = time.sleep     # tests replace it


def _gunzip(body, headers):
    """body unzipped when the headers say Content-Encoding gzip. A gzip body that was
    cut short gives b"" (so the caller sees a broken answer, not no answer)."""
    if ((headers.get("Content-Encoding") if headers else "") or "").lower() != "gzip":
        return body
    try:
        return gzip.decompress(body)
    except Exception:
        return b""


MONTH_CODES = {"Jan": "F", "Feb": "G", "Mar": "H", "Apr": "J", "May": "K", "Jun": "M",
               "Jul": "N", "Aug": "Q", "Sep": "U", "Oct": "V", "Nov": "X", "Dec": "Z"}


SIGNAL_BANNED = ("strong buy", "strong sell", "buy now", "sell now", "you should buy",
                 "you should sell", "sure thing", "guaranteed", "can't lose", "cannot lose",
                 "winning trade", "easy money", "recommend")


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


# ---- end of the copies from tests/fx_core.py


# ---------------------------------------------------------------- fx-cot's own part

TOOL = "fx-cot"
SCRIPT = "cot.py"

COT_API = "https://publicreporting.cftc.gov/resource/"
COT_TEXT_URLS = {"72hh-3qpy": "https://www.cftc.gov/dea/newcot/f_disagg.txt",
                 "gpe5-46if": "https://www.cftc.gov/dea/newcot/FinFutWk.txt"}
COT_WEEKS = 156
COT_MIN_WEEKS = 26
COT_MIN_REPORTS = 26
COT_MAX_ROWS = 160
COT_LIMIT_PER_CODE = 170
COT_FROM_DAYS = 1134        # 162 weeks back, so 160 reports fit with room for a skipped week
COT_HIGH = 80               # the usual lines (check)
COT_LOW = 20
COT_OLD_DAYS = 14
COT_MIN_REFRESH_S = 3600
COT_MAX_AGE_DAYS = 7

# futures code (the instrument base or quote) -> (dataset, CFTC code, label)
COT_FUTURES = {
    "XAU": ("72hh-3qpy", "088691", "gold futures (COMEX)"),
    "XAG": ("72hh-3qpy", "084691", "silver futures (COMEX)"),
    "XPT": ("72hh-3qpy", "076651", "platinum futures (NYMEX)"),
    "XPD": ("72hh-3qpy", "075651", "palladium futures (NYMEX)"),
    "XCU": ("72hh-3qpy", "085692", "copper futures (COMEX)"),
    "WTI": ("72hh-3qpy", "067651", "WTI oil futures (NYMEX)"),
    "BRENT": ("72hh-3qpy", "06765T", "Brent oil futures (NYMEX)"),
    "NG": ("72hh-3qpy", "023651", "natural gas futures (NYMEX)"),
    "EUR": ("gpe5-46if", "099741", "euro futures (CME)"),
    "GBP": ("gpe5-46if", "096742", "pound futures (CME)"),
    "JPY": ("gpe5-46if", "097741", "yen futures (CME)"),
    "CHF": ("gpe5-46if", "092741", "Swiss franc futures (CME)"),
    "CAD": ("gpe5-46if", "090741", "Canadian dollar futures (CME)"),
    "AUD": ("gpe5-46if", "232741", "Australian dollar futures (CME)"),
    "NZD": ("gpe5-46if", "112741", "New Zealand dollar futures (CME)"),
    "MXN": ("gpe5-46if", "095741", "Mexican peso futures (CME)"),
}

# the currency word for "priced in dollars per <word>" (a turned sign)
COT_CURRENCY_WORDS = {"JPY": "yen", "CHF": "Swiss franc", "CAD": "Canadian dollar",
                      "MXN": "peso", "EUR": "euro", "GBP": "pound",
                      "AUD": "Australian dollar", "NZD": "New Zealand dollar"}

_COT_COMMON_FIELDS = ["report_date_as_yyyy_mm_dd", "cftc_contract_market_code",
                      "market_and_exchange_names", "open_interest_all", "contract_units",
                      "futonly_or_combined"]

COT_FIELDS = {
    "72hh-3qpy": {
        "group": "managed_money", "words": "big speculators (managed money)",
        "long": "m_money_positions_long_all", "short": "m_money_positions_short_all",
        "change_long": "change_in_m_money_long_all",
        "change_short": "change_in_m_money_short_all",
        "kept": ["pct_of_oi_m_money_long_all", "pct_of_oi_m_money_short_all",
                 "prod_merc_positions_long", "prod_merc_positions_short",
                 "swap_positions_long_all", "swap__positions_short_all"],
    },
    "gpe5-46if": {
        "group": "leveraged_funds", "words": "big speculators (leveraged funds)",
        "long": "lev_money_positions_long", "short": "lev_money_positions_short",
        "change_long": "change_in_lev_money_long",
        "change_short": "change_in_lev_money_short",
        "kept": ["pct_of_oi_lev_money_long", "pct_of_oi_lev_money_short",
                 "asset_mgr_positions_long", "asset_mgr_positions_short",
                 "dealer_positions_long_all", "dealer_positions_short_all"],
    },
}

# the weekly text files have no header: 1-based position -> API field name
TEXT_COLUMNS = {
    "72hh-3qpy": {"fields": 191, "columns": {
        1: "market_and_exchange_names", 3: "report_date_as_yyyy_mm_dd",
        4: "cftc_contract_market_code", 8: "open_interest_all",
        9: "prod_merc_positions_long", 10: "prod_merc_positions_short",
        11: "swap_positions_long_all", 12: "swap__positions_short_all",
        14: "m_money_positions_long_all", 15: "m_money_positions_short_all",
        62: "change_in_m_money_long_all", 63: "change_in_m_money_short_all",
        78: "pct_of_oi_m_money_long_all", 79: "pct_of_oi_m_money_short_all",
        186: "contract_units", 191: "futonly_or_combined"}},
    "gpe5-46if": {"fields": 87, "columns": {
        1: "market_and_exchange_names", 3: "report_date_as_yyyy_mm_dd",
        4: "cftc_contract_market_code", 8: "open_interest_all",
        9: "dealer_positions_long_all", 10: "dealer_positions_short_all",
        12: "asset_mgr_positions_long", 13: "asset_mgr_positions_short",
        15: "lev_money_positions_long", 16: "lev_money_positions_short",
        32: "change_in_lev_money_long", 33: "change_in_lev_money_short",
        49: "pct_of_oi_lev_money_long", 50: "pct_of_oi_lev_money_short",
        82: "contract_units", 87: "futonly_or_combined"}},
}

BRENT_NOTE = ("Note: this is NYMEX Brent. Most Brent trades on ICE in London, "
              "which is not in this report.")
CLOSING_LINES = ("COT is old news: positions from a Tuesday, out the Friday after.",
                 "It is context, not a timing signal.")


class CotError(ValueError):
    """An error that ends the run: one line on stderr, exit 2."""


def select_fields(dataset):
    """The $select list of a dataset: the common fields, then its own."""
    f = COT_FIELDS[dataset]
    return _COT_COMMON_FIELDS + [f["long"], f["short"], f["change_long"],
                                 f["change_short"]] + list(f["kept"])


# ---- release times (03 8.4): the report date comes from the data, never the table

def _as_date(x):
    """"2026-07-14", "2026-07-14T00:00:00.000", a date or a datetime -> a date."""
    if isinstance(x, datetime.datetime):
        return x.date()
    if isinstance(x, datetime.date):
        return x
    return datetime.datetime.strptime(str(x)[:10], "%Y-%m-%d").date()


def _ny_release(d):
    """The release moment (15:30 New York) of a date, as naive UTC."""
    return ny_to_utc(at_time(d, COT_RELEASES["time"]))


def _table_dates():
    return [_as_date(x) for x in COT_RELEASES["dates"]]


def _is_expected(d):
    """A table date in a guessed year (2027 on; 2028-01-03 too: check) is "expected"."""
    years = COT_RELEASES["provisional_years"]
    return bool(years) and d.year >= min(years)


def _next_friday(d):
    """The first Friday after date d (never d itself)."""
    return d + datetime.timedelta(days=(4 - d.weekday()) % 7 or 7)


def release_of(report_date):
    """When a report came out (or will): (utc, expected, from_table). The table date
    after the report date when it is 13 days or less away; else the first Friday after
    it, 15:30 New York, marked expected and not from the table."""
    d = _as_date(report_date)
    later = [x for x in _table_dates() if x > d]
    if later and (later[0] - d).days <= 13:
        x = later[0]
        return _ny_release(x), _is_expected(x), True
    return _ny_release(_next_friday(d)), True, False


def next_release(now):
    """The next release after now: (utc, expected, from_table). Past the table end:
    the first Friday 15:30 New York after now, expected, not from the table."""
    for x in _table_dates():
        at = _ny_release(x)
        if at > now:
            return at, _is_expected(x), True
    d = to_ny(now).date()
    at = _ny_release(d) if d.weekday() == 4 else None
    if at is None or at <= now:
        at = _ny_release(_next_friday(d))
    return at, True, False


def past_table(d):
    """True when date d is after the last date of the release table."""
    return _as_date(d) > _table_dates()[-1]


def row_date(row):
    """The report date of a row as "YYYY-MM-DD" (or None when it has none)."""
    text = row.get("report_date_as_yyyy_mm_dd") if isinstance(row, dict) else None
    if not isinstance(text, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return None
    return text[:10]


def visible_rows(rows, now, replay):
    """Without replay, all rows. With replay, only the rows out at or before now.
    Rows stay newest first; a row with no date is dropped."""
    out = []
    for row in rows:
        d = row_date(row)
        if d is None:
            continue
        if replay and release_of(d)[0] > now:
            continue
        out.append(row)
    return out


# ---- the numbers (4.5)

def num(row, field):
    """A CFTC value (text) as an int. None, "", "." or a missing key give None."""
    v = row.get(field) if isinstance(row, dict) else None
    if v is None or isinstance(v, bool):
        return None
    text = str(v).strip()
    if text in ("", "."):
        return None
    try:
        d = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int(d)


def cot_numbers(rows, dataset, weeks, now):
    """The numbers of the newest usable row (rows newest first), or None when no row
    has both the long and the short of the speculators."""
    f = COT_FIELDS[dataset]
    usable = [r for r in rows
              if row_date(r) and num(r, f["long"]) is not None and num(r, f["short"]) is not None]
    if not usable:
        return None
    r0 = usable[0]
    long_, short = num(r0, f["long"]), num(r0, f["short"])
    net = long_ - short
    oi = num(r0, "open_interest_all")
    cl, cs = num(r0, f["change_long"]), num(r0, f["change_short"])
    change = None
    if cl is not None and cs is not None:
        change = cl - cs
    elif len(usable) > 1:
        gap = (_as_date(row_date(r0)) - _as_date(row_date(usable[1]))).days
        if 6 <= gap <= 8:
            change = net - (num(usable[1], f["long"]) - num(usable[1], f["short"]))
    pct = None
    if oi:
        pct = float((Decimal(100) * net / Decimal(oi)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    window = [num(r, f["long"]) - num(r, f["short"]) for r in usable[:weeks]]
    index = None
    if len(window) >= COT_MIN_REPORTS:
        lo, hi = min(window), max(window)
        if hi == lo:
            index = 50.0
        else:
            index = float((Decimal(100) * (net - lo) / Decimal(hi - lo)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP))
    extreme = None
    if index is not None and index >= COT_HIGH:
        extreme = "high"
    elif index is not None and index <= COT_LOW:
        extreme = "low"
    report_date = row_date(r0)
    released_at, expected, from_table = release_of(report_date)
    age = (to_ny(now).date() - _as_date(report_date)).days
    return {"report_date": report_date, "released_at": released_at,
            "release_expected": expected, "from_table": from_table,
            "long": long_, "short": short, "net": net, "change": change,
            "open_interest": oi, "net_pct_oi": pct, "cot_index": index,
            "index_reports": len(window), "extreme": extreme, "age_days": age,
            "market": r0.get("market_and_exchange_names")}


def count_text(n, signed=False):
    """98,000; signed: +6,200, -1,948, 0."""
    text = format(abs(int(n)), ",d")
    if n < 0:
        return "-" + text
    if signed and n > 0:
        return "+" + text
    return text


def cot_legs(inst):
    """[(futures_code, sign)]: metal and energy one leg (+1) when the base has a COT
    market; FX the base leg (+1), then the quote leg (-1). USD has no leg."""
    base, quote = inst.get("base"), inst.get("quote")
    if inst.get("kind") in ("metal", "energy"):
        return [(base, 1)] if base in COT_FUTURES else []
    legs = []
    if base in COT_FUTURES:
        legs.append((base, 1))
    if quote in COT_FUTURES:
        legs.append((quote, -1))
    return legs


def pair_side(net, sign):
    """"buy" when net x sign is above 0, "sell" below 0, None at 0 (or no net)."""
    if net is None:
        return None
    v = net * sign
    if v > 0:
        return "buy"
    if v < 0:
        return "sell"
    return None


# ---- the cache (data/cot/<dataset>-<code>.json) and when to ask the CFTC (4.3)

def show_path(path):
    """A path for people: relative to the current folder when it is inside it."""
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


def cot_file(cot_dir, dataset, code):
    return os.path.join(cot_dir, "%s-%s.json" % (dataset, code))


def load_cot_file(cot_dir, dataset, code):
    """(doc or None, damaged). A file that does not parse, is not an object or whose
    "rows" is not a list is damaged (fetched again when online)."""
    try:
        doc = load_json(cot_file(cot_dir, dataset, code))
    except ValueError:
        return None, True
    if doc is None:
        return None, False
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return None, True
    return doc, False


def newest_date(doc):
    """The newest report date of a doc ("YYYY-MM-DD"), or None."""
    dates = [row_date(r) for r in (doc or {}).get("rows") or [] if row_date(r)]
    return max(dates) if dates else None


def fetch_due(doc, now):
    """True when the CFTC should be asked for this market now (4.3 step 3)."""
    if doc is None:
        return True
    last = parse_utc(doc.get("tried_at")) or parse_utc(doc.get("fetched_at"))
    if last is not None and (now - last).total_seconds() < COT_MIN_REFRESH_S:
        return False
    newest = newest_date(doc)
    if newest is None:
        return True
    if now >= next_release(release_of(newest)[0])[0]:
        return True
    fetched = parse_utc(doc.get("fetched_at"))
    if fetched is None or (now - fetched).total_seconds() >= COT_MAX_AGE_DAYS * 86400:
        return True
    return False


def api_url(dataset, codes, now):
    """The CFTC API (Socrata) URL for these market codes, about 162 weeks back."""
    start = to_ny(now).date() - datetime.timedelta(days=COT_FROM_DAYS)
    where = "cftc_contract_market_code in(%s) AND report_date_as_yyyy_mm_dd >= '%sT00:00:00'" % (
        ",".join("'%s'" % c for c in codes), start.isoformat())
    query = [("$select", ",".join(select_fields(dataset))),
             ("$where", where),
             ("$order", "cftc_contract_market_code,report_date_as_yyyy_mm_dd DESC"),
             ("$limit", str(COT_LIMIT_PER_CODE * len(codes)))]
    return COT_API + dataset + ".json?" + urllib.parse.urlencode(query)


COT_CLOCK = time.monotonic   # tests replace it (with SLEEP)
_cot_last_request = None     # COT_CLOCK() of the last CFTC request of this run


def cot_pace():
    """Wait (SLEEP) so two CFTC requests are at least PACE_SECONDS apart."""
    global _cot_last_request
    if _cot_last_request is not None:
        wait = PACE_SECONDS - (COT_CLOCK() - _cot_last_request)
        if wait > 0:
            SLEEP(wait)
    _cot_last_request = COT_CLOCK()


def cftc_get(url, accept):
    """One GET: (status, headers, body bytes). Never raises: a network problem gives
    status 0. Headers come back as a dict with Title-Case keys."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            heads = {k.title(): v for k, v in resp.headers.items()}
            return resp.status, heads, resp.read()
    except urllib.error.HTTPError as exc:
        heads = {k.title(): v for k, v in (exc.headers.items() if exc.headers else [])}
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, heads, body
    except Exception:
        return 0, {}, b""


def _status_error(status):
    return "network" if not status else "http %s" % status


def _split_rows(rows, codes):
    """Rows by code: newest first, one row a date, at most COT_MAX_ROWS."""
    out = {}
    for code in codes:
        mine = [r for r in rows if str(r.get("cftc_contract_market_code", "")).strip() == code
                and row_date(r)]
        mine.sort(key=row_date, reverse=True)
        keep, dates = [], set()
        for r in mine:
            if row_date(r) not in dates:
                dates.add(row_date(r))
                keep.append(r)
        out[code] = keep[:COT_MAX_ROWS]
    return out


def fetch_api(dataset, codes, now):
    """One API call for these codes: (rows_by_code or None, error, last_modified)."""
    cot_pace()
    status, heads, body = cftc_get(api_url(dataset, codes, now), "application/json")
    if status != 200:
        return None, _status_error(status), None
    try:
        data = json.loads(_gunzip(body, heads).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "not json", None
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        return None, "not a list", None
    return _split_rows(data, codes), None, heads.get("Last-Modified")


def parse_text_line(cells, dataset):
    """One line of a weekly text file (a list of cells) -> a row with the API names.
    The date gets the API form; "." and empty cells are left out. A line with the
    wrong number of fields raises ValueError("text layout")."""
    layout = TEXT_COLUMNS[dataset]
    if len(cells) != layout["fields"]:
        raise ValueError("text layout")
    row = {}
    for pos, name in sorted(layout["columns"].items()):
        v = str(cells[pos - 1]).strip()
        if v in ("", "."):
            continue
        row[name] = v
    d = row.get("report_date_as_yyyy_mm_dd", "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        raise ValueError("text layout")
    row["report_date_as_yyyy_mm_dd"] = d + "T00:00:00.000"
    row["fx_from"] = "weekly text file"
    return row


def fetch_text(dataset, codes):
    """The backup: the weekly text file (newest week only): (rows_by_code or None, error)."""
    cot_pace()
    status, heads, body = cftc_get(COT_TEXT_URLS[dataset], "text/plain")
    if status != 200:
        return None, _status_error(status)
    raw = _gunzip(body, heads)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = []
    for cells in csv.reader(text.splitlines()):
        if not cells or not "".join(cells).strip():
            continue
        try:
            rows.append(parse_text_line(cells, dataset))
        except ValueError:
            return None, "text layout"
    return _split_rows(rows, codes), None


def _new_doc(dataset, code):
    return {"fetched_at": None, "tried_at": None, "error": None, "last_modified": None,
            "source": None, "dataset": dataset, "code": code, "market": None, "rows": []}


def _saved_note(error, doc):
    """The note for a failed fetch when saved rows are kept."""
    at = parse_utc(doc.get("fetched_at")) or parse_utc(doc.get("tried_at"))
    when = " (fetched %s)" % pkt_label(at, date=True) if at else ""
    return "Note: the CFTC site did not answer (%s). Showing the saved data%s." % (error, when)


def refresh_markets(cot_dir, markets, now, offline):
    """Read each market's cache file and, when online and due, ask the CFTC (one API
    call per dataset, the text file as a backup). Returns {(dataset, code): (doc or
    None, notes, why)}, why None, "no data" or "damaged". Never raises for a network
    problem."""
    out = {}
    due = {}
    for dataset, code in markets:
        doc, damaged = load_cot_file(cot_dir, dataset, code)
        path = show_path(cot_file(cot_dir, dataset, code))
        if offline:
            if damaged:
                out[(dataset, code)] = (None, ["Note: %s is damaged. Run once without --offline "
                                               "or --now to get it again." % path], "damaged")
            else:
                out[(dataset, code)] = (doc, [], None if doc else "no data")
            continue
        if fetch_due(doc, now):
            due.setdefault(dataset, []).append(code)
            out[(dataset, code)] = (doc, [], None)
        else:
            notes = []
            if doc.get("error"):
                notes.append("Note: the CFTC site failed less than 1 hour ago. Showing the saved data.")
            out[(dataset, code)] = (doc, notes, None)
    for dataset, codes in due.items():
        got, error, modified = fetch_api(dataset, codes, now)
        text_rows, text_error = None, None
        if got is None:
            text_rows, text_error = fetch_text(dataset, codes)
        for code in codes:
            old = out[(dataset, code)][0]
            doc = copy.deepcopy(old) if old else _new_doc(dataset, code)
            notes = []
            doc["tried_at"] = utc_iso(now)
            if got is not None and got.get(code):
                rows = got[code]
                doc.update({"rows": rows, "fetched_at": utc_iso(now), "error": None,
                            "source": "api", "last_modified": modified,
                            "market": rows[0].get("market_and_exchange_names")})
            else:
                err = error or "empty"
                doc["error"] = err
                newest = newest_date(doc)
                fresh = [r for r in (text_rows or {}).get(code, [])
                         if newest is None or row_date(r) > newest]
                if fresh:
                    doc["rows"] = (fresh[:1] + doc["rows"])[:COT_MAX_ROWS]
                    doc["source"] = "text"
                    doc["market"] = fresh[0].get("market_and_exchange_names") or doc.get("market")
                    notes.append("Note: the CFTC data site did not answer (%s). The newest week "
                                 "came from the CFTC text file." % err)
                elif doc["rows"]:
                    notes.append(_saved_note(err, doc))
            save_json_atomic(cot_file(cot_dir, dataset, code), doc)
            out[(dataset, code)] = (doc, notes, None if doc["rows"] else "no data")
    return out


# ---- state/cot.json (00 4.12)

def load_state(path, now):
    """(state, line). A broken file is renamed <name>.broken-<UTC stamp>.json and a new
    state starts; line is then the 01 5.2 line, else None."""
    empty = {"seen": {}, "last_check": None}
    try:
        doc = load_json(path)
    except ValueError:
        doc = False
    if doc is None:
        return empty, None
    if (isinstance(doc, dict) and isinstance(doc.get("seen", {}), dict)
            and all(isinstance(v, str) for v in doc.get("seen", {}).values())
            and (doc.get("last_check") is None or isinstance(doc.get("last_check"), str))):
        return {"seen": dict(doc.get("seen", {})), "last_check": doc.get("last_check")}, None
    stem = os.path.splitext(path)[0]
    broken = "%s.broken-%s.json" % (stem, now.strftime("%Y%m%dT%H%M%SZ"))
    try:
        os.replace(path, broken)
    except OSError as exc:
        raise CotError("could not move the damaged %s: %s" % (show_path(path), exc.strerror or exc))
    return empty, "%s was damaged. Started a new one." % show_path(path)


def mark_seen(state, key, date):
    """seen[key] = the later of the old date and this one (a seen date never goes back)."""
    seen = state.setdefault("seen", {})
    if date and date > seen.get(key, ""):
        seen[key] = date
    return state


# ---- which instruments (3.1) and your trades (4.8)

def symbol_keys(text, cfg):
    """--symbols text -> keys in capitals, no repeats. An unknown key is an error that
    names the nearest known key."""
    known = list(cfg.get("instruments") or {})
    out = []
    for part in str(text).split(","):
        key = part.strip().upper()
        if not key:
            continue
        if key not in known:
            near = difflib.get_close_matches(key, known, n=1, cutoff=0.6)
            more = " Did you mean %s?" % near[0] if near else ""
            raise CotError("%s is not in your instruments.%s" % (key, more))
        if key not in out:
            out.append(key)
    if not out:
        raise CotError("--symbols needs at least one instrument key, like XAUUSD.")
    return out


def read_watchlist(path, cfg):
    """The watchlist hit symbols (01 5.2 checks), or [] when there is no file."""
    doc = load_doc(path, "watchlist", "fx-scanner", required=False)
    if doc is None:
        return []
    shown = show_path(path)
    if doc.get("mode") not in ("swing", "day"):
        raise ValueError('%s: "mode" must be "swing" or "day"' % shown)
    out = []
    for n, hit in enumerate(check_objects(doc, "hits", shown), 1):
        sym = check_symbol(hit.get("symbol"), cfg, shown)
        if hit.get("level") is None:
            raise ValueError("%s: hit %d (%s) has no level. Run fx-scanner again." % (shown, n, sym))
        out.append(sym)
    return out


def read_plan(path, cfg):
    """{symbol, side, style} of plan.json (only the checks on those fields), or None."""
    doc = load_doc(path, "plan", "fx-exit", required=False)
    if doc is None:
        return None
    shown = show_path(path)
    return {"symbol": check_symbol(doc.get("symbol"), cfg, shown),
            "side": check_side(doc.get("side"), shown),
            "style": check_style(doc.get("style"), shown)}


def read_positions(path, cfg):
    """The open positions of portfolio.json (01 5.2 checks), or [] when there is no file."""
    doc = load_doc(path, "portfolio", "fx-broker", required=False)
    if doc is None:
        return []
    shown = show_path(path)

    def bad(what):
        return ValueError("%s: %s. Fix it by hand or restore a backup." % (shown, what))

    account = doc.get("account")
    if isinstance(account, dict) and account.get("currency") not in (None, "USD"):
        raise bad("the account currency is %s, not USD" % account.get("currency"))
    positions = check_objects(doc, "positions", shown) if "positions" in doc else []
    ids, out = set(), []
    for n, pos in enumerate(positions, 1):
        sym = pos.get("symbol")
        if not isinstance(sym, str) or sym not in (cfg.get("instruments") or {}):
            raise bad('position %d ("%s") is not in your instruments' % (n, sym))
        if pos.get("id") in ids:
            raise bad('two positions have the id "%s"' % pos.get("id"))
        ids.add(pos.get("id"))
        check_side(pos.get("side"), shown)
        check_style(pos.get("style"), shown)
        check_lots(pos.get("lots"), shown)
        out.append({"id": pos.get("id"), "symbol": sym, "side": pos["side"],
                    "style": pos["style"], "lots": pos["lots"], "paper": pos.get("paper") is True})
    return out


SOURCE_WORDS = {"--symbols": "--symbols", "--all": "--all",
                "watchlist": "your watchlist, plan and trades",
                "universe": "your universe (no watchlist, plan or trades)"}


def pick_symbols(args, cfg, paths):
    """(symbols, source): --symbols; else --all (the universe); else the watchlist hits,
    the plan symbol and the open positions, no repeats; if none, the universe."""
    if args.symbols:
        return symbol_keys(args.symbols, cfg), "--symbols"
    universe = list(cfg.get("universe") or [])
    if args.all:
        return universe, "--all"
    out = []
    plan = read_plan(paths["plan"], cfg)
    found = read_watchlist(paths["watchlist"], cfg)
    found += [plan["symbol"]] if plan else []
    found += [p["symbol"] for p in read_positions(paths["portfolio"], cfg)]
    for sym in found:
        if sym not in out:
            out.append(sym)
    if out:
        return out, "watchlist"
    return universe, "universe"


def your_trades(paths, symbols, cfg):
    """symbol -> {"plan": {side, style} or None, "positions": [...]} (4.8)."""
    plan = read_plan(paths["plan"], cfg)
    positions = read_positions(paths["portfolio"], cfg)
    out = {}
    for sym in symbols:
        mine = plan if plan and plan["symbol"] == sym else None
        out[sym] = {"plan": {"side": mine["side"], "style": mine["style"]} if mine else None,
                    "positions": [p for p in positions if p["symbol"] == sym]}
    return out


def short_label(label):
    """"gold futures (COMEX)" -> "gold futures"."""
    return label.split(" (")[0]


def market_key(dataset, code):
    return "%s-%s" % (dataset, code)


def collect(symbols, cfg, docs, now, replay, weeks, yours, cot_dir):
    """One entry a symbol: its legs with numbers, and the legs that are missing."""
    out = []
    for sym in symbols:
        inst = instrument(cfg, sym)
        entry = {"symbol": sym, "name": inst.get("name") or sym, "kind": inst.get("kind"),
                 "missing": [], "legs": [], "plan": yours[sym]["plan"],
                 "positions": yours[sym]["positions"], "n_legs": 0, "parts": []}
        legs = cot_legs(inst)
        entry["n_legs"] = len(legs)
        if not legs:
            entry["missing"].append({"futures": None, "why": "no market"})
        for fut, sign in legs:
            dataset, code, label = COT_FUTURES[fut]
            doc, _notes, why = docs[(dataset, code)]
            info = None
            if doc is not None:
                vis = visible_rows(doc.get("rows") or [], now, replay)
                info = cot_numbers(vis, dataset, weeks, now)
                if info is None:
                    why = why or ("no rows" if vis else "no data")
            if info is None:
                miss = {"futures": fut, "why": why or "no data", "label": label,
                        "dataset": dataset, "code": code,
                        "path": show_path(cot_file(cot_dir, dataset, code))}
                entry["missing"].append(miss)
                entry["parts"].append(("missing", miss))
                continue
            info.update({"futures": fut, "label": label, "dataset": dataset, "code": code,
                         "group": COT_FIELDS[dataset]["group"], "sign": sign,
                         "pair_side": pair_side(info["net"], sign), "new": False,
                         "market": info.get("market") or doc.get("market"),
                         "source": doc.get("source"), "fetched_at": doc.get("fetched_at")})
            entry["legs"].append(info)
            entry["parts"].append(("leg", info))
        out.append(entry)
    return out


# ---- the printout (6.1)

def missing_note(miss, offline, error=None):
    """The note for a leg with no numbers."""
    name = short_label(miss["label"])
    if miss["why"] == "damaged":
        return ("Note: %s is damaged. Run once without --offline or --now to get it again."
                % miss["path"])
    if miss["why"] == "no rows":
        return "Note: the %s data has no big speculator numbers. Nothing to show." % name
    if not offline and error:
        return ("Note: the CFTC site did not answer (%s). No COT data for %s is saved yet. "
                "Try again later." % (error, name))
    return ("Note: no COT data for %s yet (%s). Run once without --offline or --now to get it."
            % (name, miss["path"]))


def release_line(utc, expected):
    line = "Out %s (%s)." % (pkt_label(utc, date=True), ny_label(utc, date=True))
    return line + (" Expected date." if expected else "")


def render_leg(leg, key, weeks, is_new=False):
    """The lines of one leg (indented two spaces)."""
    words = COT_FIELDS[leg["dataset"]]["words"]
    name = short_label(leg["label"])
    net = leg["net"]
    if net > 0:
        where = "net long %s contracts" % count_text(net)
    elif net < 0:
        where = "net short %s contracts" % count_text(-net)
    else:
        where = "flat (net 0)"
    if leg["change"] is None:
        change = "change in the week not known"
    else:
        change = "%s in the week" % count_text(leg["change"], signed=True)
    lines = ["%s%s, CFTC code %s" % ("NEW: " if is_new else "", leg["label"], leg["code"]),
             "Positions on %s." % day_label(_as_date(leg["report_date"])),
             release_line(leg["released_at"], leg["release_expected"]),
             "%s%s: %s, %s." % (words[0].upper(), words[1:], where, change)]
    if leg["net_pct_oi"] is None:
        lines.append("Open interest not known.")
    else:
        lines.append("That is %s%% of all open contracts." % format(abs(leg["net_pct_oi"]), ".1f"))
    n = leg["index_reports"]
    if leg["cot_index"] is None:
        lines.append("Not enough reports for a COT index (%d of %d needed)." % (n, COT_MIN_REPORTS))
    else:
        over = "%d reports" % n if n >= weeks else "%d reports (fewer than %d saved)" % (n, weeks)
        lines.append("COT index %s of 100 over %s (0 = lowest net, 100 = highest)."
                     % (format(leg["cot_index"], ".1f"), over))
        if leg["extreme"]:
            lines.append("Note: that is near the %s of %d reports. Positions can stay there "
                         "for months." % ("top" if leg["extreme"] == "high" else "bottom", n))
    side = leg["pair_side"]
    if side is None:
        lines.append("Neither side of %s." % key)
    else:
        lines.append("Net %s %s is the %s side of %s." % ("long" if net > 0 else "short", name,
                                                         side, key))
    if leg["sign"] < 0:
        lines.append("The sign is turned: these futures are priced in dollars per %s."
                     % COT_CURRENCY_WORDS.get(leg["futures"], leg["futures"]))
    lines.append("The report is %d %s old." % (leg["age_days"], "day" if leg["age_days"] == 1 else "days"))
    if leg["age_days"] > COT_OLD_DAYS:
        lines.append("Note: no newer report yet. The CFTC can be late, for example in a US "
                     "government shutdown.")
    return lines


def trade_lines(entry):
    """Your open trade and your plan (4.8), with the side line for one-leg symbols."""
    lines, sides, day = [], [], False
    for pos in entry["positions"]:
        lines.append("Your open trade: %s %s (%s), %s." % (side_word(pos["side"]), lots_text(pos["lots"]),
                                                          "paper" if pos["paper"] else "live", pos["style"]))
        sides.append(pos["side"])
        day = day or pos["style"] == "day"
    if entry["plan"]:
        lines.append("Your plan: %s, %s." % (side_word(entry["plan"]["side"]), entry["plan"]["style"]))
        sides.append(entry["plan"]["side"])
        day = day or entry["plan"]["style"] == "day"
    if entry["n_legs"] == 1 and len(entry["legs"]) == 1 and entry["legs"][0]["pair_side"]:
        spec = entry["legs"][0]["pair_side"]
        for side in sorted(set(sides), key=sides.index):
            lines.append("Big speculators are on the %s side." % ("same" if side == spec else "other"))
    if day:
        lines.append("Note: COT is weekly. It says little about one day.")
    return lines


def render_symbol(entry, weeks, offline, errors, new_keys=None):
    """The block of one symbol: the head line, one part per leg (or its note), the
    Brent note and your trade lines. new_keys marks the legs that are new (for new)."""
    lines = ["%s (%s)" % (entry["symbol"], entry["name"])]
    body = []
    if entry["n_legs"] == 2:
        body.append("A cross has no COT market of its own. Each currency is shown on its own.")
    if entry["n_legs"] == 0:
        body.append("Note: there is no COT market for %s in this pack." % entry["name"])
    for kind, part in entry["parts"]:
        if kind == "missing":
            body.append(missing_note(part, offline, errors.get((part["dataset"], part["code"]))))
            continue
        key = market_key(part["dataset"], part["code"])
        body += render_leg(part, entry["symbol"], weeks, bool(new_keys and key in new_keys))
    if any(fut == "BRENT" for fut in [p["futures"] for k, p in entry["parts"]]):
        body.append(BRENT_NOTE)
    body += trade_lines(entry)
    return lines + ["  " + line for line in body]


def render_tail(now, replay, legs, cot_dir):
    """The next release, the run notes, the replay line and the source line."""
    at, expected, from_table = next_release(now)
    lines = ["The next COT report is due %s (%s).%s" % (
        pkt_label(at, date=True), ny_label(at, date=True), " Expected date." if expected else "")]
    guess = (expected and from_table) or any(l["release_expected"] and l["from_table"] for l in legs)
    past = (not from_table) or any(not l["from_table"] and past_table(l["report_date"]) for l in legs)
    if guess:
        lines.append("Note: the 2027 dates are a guess from the 2026 rule. Check the CFTC release page.")
    if past:
        lines.append("Note: the CFTC dates in this pack end on %s. This time is a guess: Friday "
                     "3:30 PM New York." % COT_RELEASES["dates"][-1])
    if replay:
        lines.append("Replay at %s: reports that came out later are not shown." % pkt_label(now, date=True))
        lines.append("Source: CFTC Commitments of Traders (saved in %s)." % show_path(cot_dir))
    else:
        times = [parse_utc(l["fetched_at"]) for l in legs if parse_utc(l.get("fetched_at"))]
        if times:
            lines.append("Source: CFTC Commitments of Traders (fetched %s)." % pkt_label(max(times), date=True))
        else:
            lines.append("Source: CFTC Commitments of Traders (saved in %s)." % show_path(cot_dir))
    return lines


def _parser():
    p = argparse.ArgumentParser(
        prog="cot.py",
        description="The COT Reader: what the big speculators hold, from the CFTC "
                    "Commitments of Traders report. Context only, never a timing signal.")
    sub = p.add_subparsers(dest="cmd")
    for name, text in (("show", "show the newest COT report for your instruments"),
                       ("new", "show only the reports not shown before")):
        s = sub.add_parser(name, help=text, description=text)
        s.add_argument("--symbols", metavar="A,B", default=None,
                       help="only these instruments (keys like XAUUSD,EURUSD)")
        s.add_argument("--all", action="store_true",
                       help="your whole universe, not the watchlist, plan and trades")
        s.add_argument("--weeks", type=int, default=COT_WEEKS, metavar="N",
                       help="the COT index window in reports, 26 to 156 (default 156)")
        s.add_argument("--dry-run", action="store_true",
                       help="do not write fx/state/cot.json")
        add_common(s)
    return p


# ---- the run (4.6 and 4.7)

def no_data_error(entries, docs, offline):
    """The section 7 error when no leg of any symbol has numbers."""
    first = None
    for entry in entries:
        for miss in entry["missing"]:
            if miss["futures"] and first is None:
                first = (entry, miss)
    if first is None:
        return CotError("There is no COT market for %s in this pack. COT covers gold, silver, "
                        "platinum, palladium, copper, oil, gas and the main currencies."
                        % entries[0]["name"])
    entry, miss = first
    if offline:
        return CotError("No COT data for %s yet (%s). Run once without --offline or --now to get it."
                        % (entry["symbol"], miss["path"]))
    doc = docs[(miss["dataset"], miss["code"])][0] or {}
    if doc.get("error"):
        return CotError("The CFTC site did not answer (%s) and no COT data is saved yet. "
                        "Try again later." % doc["error"])
    return CotError("No COT data for %s yet (%s). Try again later." % (entry["symbol"], miss["path"]))


def run(args):
    """Do one show or new run: (exit code, lines, json doc)."""
    work = (os.path.dirname(args.config) or ".") if args.config else "fx"
    paths = fx_paths(work, args.data, args.config)
    cfg = load_config(paths["config"], required=args.config is not None)
    now = parse_now(args.now)
    replay = args.now is not None
    offline = args.offline or replay
    if args.symbols and args.all:
        raise CotError("Use --symbols or --all, not both.")
    if not COT_MIN_WEEKS <= args.weeks <= COT_WEEKS:
        raise CotError("--weeks must be a whole number from 26 to 156.")
    symbols, source = pick_symbols(args, cfg, paths)
    yours = your_trades(paths, symbols, cfg)
    markets = []
    for sym in symbols:
        for fut, _sign in cot_legs(instrument(cfg, sym)):
            pair = COT_FUTURES[fut][:2]
            if pair not in markets:
                markets.append(pair)
    docs = refresh_markets(paths["cot"], markets, now, offline)
    state, state_line = load_state(paths["cot_state"], now)
    entries = collect(symbols, cfg, docs, now, replay, args.weeks, yours, paths["cot"])
    legs = [leg for e in entries for leg in e["legs"]]
    if not legs:
        raise no_data_error(entries, docs, offline)
    errors = {m: docs[m][0].get("error") for m in markets if docs[m][0] and docs[m][0].get("error")}
    run_notes = []
    for m in markets:
        for note in docs[m][1]:
            if note not in run_notes:
                run_notes.append(note)
    seen = state.get("seen", {})
    first = state.get("last_check") is None
    new_keys = set()
    for leg in legs:
        key = market_key(leg["dataset"], leg["code"])
        leg["new"] = leg["report_date"] > seen.get(key, "")
        if leg["new"]:
            new_keys.add(key)
    lines = [state_line] if state_line else []
    if args.cmd == "show":
        code = 0
        n = len(symbols)
        lines.append("COT for %d instrument%s (from %s). Positions from Tuesdays, out on Fridays."
                     % (n, "" if n == 1 else "s", SOURCE_WORDS[source]))
        lines += run_notes + [""]
        for entry in entries:
            lines += render_symbol(entry, args.weeks, offline, errors)
    elif new_keys:
        code = 0
        if first:
            lines.append("First run: showing the newest report of each market.")
        n = len(new_keys)
        lines.append("%d new COT report%s." % (n, "" if n == 1 else "s"))
        lines += run_notes + [""]
        for entry in entries:
            keys = {market_key(l["dataset"], l["code"]) for l in entry["legs"]}
            if keys & new_keys:
                lines += render_symbol(entry, args.weeks, offline, errors, new_keys)
    else:
        code = 1
        last = parse_utc(state.get("last_check")) or now
        lines.append("Nothing new since %s." % pkt_label(last, date=True))
        lines += run_notes
    lines += [""] + render_tail(now, replay, legs, paths["cot"]) + list(CLOSING_LINES)
    if not args.dry_run:
        for leg in legs:
            mark_seen(state, market_key(leg["dataset"], leg["code"]), leg["report_date"])
        state["last_check"] = utc_iso(now)
        save_json_atomic(paths["cot_state"], state)
    if args.quiet:
        lines = [l for l in lines if not l.strip().startswith("Note: ") and l not in CLOSING_LINES
                 and not l.startswith("First run:")]
    return code, lines, json_doc(args, now, code, lines, entries, replay, first)


JSON_LEG_KEYS = ("futures", "label", "dataset", "code", "market", "group", "sign", "report_date",
                 "released_at", "release_expected", "long", "short", "net", "change",
                 "open_interest", "net_pct_oi", "cot_index", "index_reports", "extreme",
                 "pair_side", "age_days", "new", "source", "fetched_at")


def json_doc(args, now, code, lines, entries, replay, first):
    """The --json answer: the common keys, doc null, and the numbers under "symbols"."""
    at, expected, _from_table = next_release(now)
    symbols = []
    for e in entries:
        legs = []
        for leg in e["legs"]:
            item = {k: leg.get(k) for k in JSON_LEG_KEYS}
            item["released_at"] = utc_iso(leg["released_at"])
            legs.append(item)
        symbols.append({"symbol": e["symbol"], "name": e["name"],
                        "missing": [{"futures": m["futures"], "why": m["why"]} for m in e["missing"]],
                        "plan": e["plan"], "positions": e["positions"], "legs": legs})
    return {"v": JSON_VERSION, "tool": TOOL, "cmd": args.cmd, "now": utc_iso(now),
            "ok": code == 0, "doc": None, "replay": replay, "weeks": args.weeks,
            "first_run": first if args.cmd == "new" else None,
            "next_release": {"at": utc_iso(at), "expected": expected},
            "notes": [l.strip() for l in lines if l.strip()], "symbols": symbols}


WRAP_WIDTH = 90    # about 78 (06 2.6); the spec's own example lines reach 84 (check)


def wrap_lines(lines):
    """Lines for people: a line over WRAP_WIDTH is cut at spaces, the rest keeps its indent."""
    out = []
    for line in lines:
        if len(line) <= WRAP_WIDTH:
            out.append(line)
            continue
        indent = line[:len(line) - len(line.lstrip())]
        out += textwrap.wrap(line.strip(), width=WRAP_WIDTH, initial_indent=indent,
                             subsequent_indent=indent, break_long_words=False,
                             break_on_hyphens=False)
    return out


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        code, lines, doc = run(args)
    except ValueError as exc:
        return fail(exc, SCRIPT)
    if args.json:
        emit_json(doc)
    else:
        sys.stdout.write("\n".join(wrap_lines(lines)) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
