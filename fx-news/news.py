#!/usr/bin/env python3
"""fx-news: forex and commodity headlines, tagged to your instruments.

Reads the RSS feeds in your config ("news.feeds"), tags each headline to the
instruments it touches (pairs, commodity words, currency words, central banks), and
shows only what is new since the last check, newest first, in Karachi and New York
time, with the source and the link.

    python3 fx-news/news.py [--symbols A,B] [--all] [--dry-run] [--hours H]
                            [--feeds NAMES] [--max N] [--watchlist FILE]
                            [--config FILE] [--data DIR] [--offline] [--now T]
                            [--json] [--quiet]

It reads only the RSS files, never an article page. It prints and saves only the
title, the source, the time and the link. It shows no prices and places no orders.
Standard library only, Python 3.8.
"""

import copy
import datetime
import difflib
import email.utils
import gzip
import html
import json
import math
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.request
import argparse
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from xml.etree import ElementTree


# ---------------------------------------------------------------- copy together
# Each line is a helper copied from tests/fx_core.py and the fx_core names it uses.
# Copy those too, and what they use in turn. "-" means it uses nothing else.
#   load_json: -
#   save_json_atomic: -
#   fx_paths: -
#   load_doc: load_json
#   check_objects: -
#   check_symbol: -
#   check_side: -
#   check_style: -
#   JSON_VERSION: -
#   _words_dec: -
#   age_text: _words_dec
#   add_common: -
#   fail: -
#   emit_json: -
#   _dec_clean: -
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
#   BROKER_TEMPLATES: -
#   DATA_TEMPLATES: -
#   UA: -
#   SLEEP: -
#   MONTH_CODES: -
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


BROKER_TEMPLATES = ("fx", "comex", "nymex", "ice_brent")


DATA_TEMPLATES = ("yahoo_fx", "cme")


UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"


SLEEP = time.sleep     # tests replace it


MONTH_CODES = {"Jan": "F", "Feb": "G", "Mar": "H", "Apr": "J", "May": "K", "Jun": "M",
               "Jul": "N", "Aug": "Q", "Sep": "U", "Oct": "V", "Nov": "X", "Dec": "Z"}


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


# ---------------------------------------------------------------- fx-news's own part

TOOL = "fx-news"
SCRIPT = "news.py"
NEWS_PACE_S = 1.5            # at least this long between two feed requests (all hosts)
NEWS_TIMEOUT = 20            # socket timeout in seconds
NEWS_MIN_REFRESH_S = 900     # a feed tried less than 15 minutes ago is read from its saved copy
NEWS_MAX_BYTES = 2000000     # a bigger answer (after gzip) is "too big"
NEWS_KEEP_DAYS = 30          # seen links are kept this long
NEWS_HOURS = 48              # the default window
NEWS_MAX_ITEMS = 40          # the default --max
NEWS_LATE_MIN = 10           # items up to 10 minutes after the clock still count
NEWS_FUTURE_DAYS = 2         # an item dated more than 2 days after its fetch has a bad date (check)
CLOCK = time.monotonic       # the pace clock; tests swap it with SLEEP
_last_get = [None]           # CLOCK() of the last feed request of this run
OPEN_URL = urllib.request.urlopen   # the opener; tests swap it (never the network)

NEWS_SOURCES = {"fxstreet": "FXStreet", "investinglive": "investingLive",
                "investing-commodities": "Investing.com", "investing-forex": "Investing.com",
                "oilprice": "OilPrice.com", "cnbc-energy": "CNBC", "fed-monetary": "Federal Reserve",
                "ecb-press": "ECB", "boe-news": "Bank of England", "boj-news": "Bank of Japan"}

FEED_CURRENCY = {"fed-monetary": "USD", "ecb-press": "EUR", "boe-news": "GBP", "boj-news": "JPY"}

INSTRUMENT_WORDS = {
    "XAUUSD": ["gold", "bullion", "precious metal", "precious metals", "xau"],
    "XAGUSD": ["silver", "xag"],
    "XPTUSD": ["platinum", "xpt"],
    "XPDUSD": ["palladium", "xpd"],
    "COPPER": ["copper", "comex copper", "lme copper"],
    "USOIL": ["oil", "crude", "wti", "opec", "barrel", "barrels", "refinery", "refineries",
              "gasoline", "diesel", "distillate", "distillates", "crude inventories",
              "crude stocks", "stockpiles", "rig count", "baker hughes", "hormuz", "spr"],
    "UKOIL": ["oil", "crude", "brent", "opec", "barrel", "barrels", "refinery", "refineries",
              "gasoline", "diesel", "distillate", "distillates", "crude inventories",
              "crude stocks", "stockpiles", "hormuz"],
    "NATGAS": ["natural gas", "nat gas", "natgas", "lng", "henry hub", "ttf", "gas storage"],
}

CURRENCY_WORDS = {
    "USD": ["fed", "fomc", "federal reserve", "dollar", "greenback", "dxy", "treasury",
            "treasuries", "treasury yields", "payrolls", "nfp", "non-farm", "nonfarm",
            "jobless claims", "pce", "ism", "jolts", "us cpi", "u.s. cpi", "us inflation",
            "us jobs", "us gdp", "us yields"],
    "EUR": ["ecb", "european central bank", "euro", "euros", "eurozone", "euro zone",
            "euro area", "bund", "bunds", "eurostat", "ifo", "zew", "german", "germany",
            "france", "french", "italy", "italian", "spain", "spanish", "dutch", "netherlands"],
    "GBP": ["boe", "bank of england", "mpc", "sterling", "pound", "cable", "gilt", "gilts",
            "uk", "u.k.", "britain", "british", "ons"],
    "JPY": ["boj", "bank of japan", "yen", "jgb", "jgbs", "japan", "japanese", "tankan",
            "shunto", "intervention", "rate check"],
    "CHF": ["snb", "swiss national bank", "franc", "swissie", "swiss", "switzerland"],
    "AUD": ["rba", "reserve bank of australia", "aussie", "australia", "australian", "iron ore"],
    "CAD": ["boc", "bank of canada", "loonie", "canada", "canadian"],
    "NZD": ["rbnz", "reserve bank of new zealand", "kiwi", "new zealand", "dairy",
            "global dairy trade"],
    "CNY": ["china", "chinese", "pboc", "yuan", "renminbi", "cnh", "beijing", "caixin"],
}

# People's names (case-sensitive whole words). Names that are also common words
# (Cook, Barr, Lane, Hunter, Williams, Greene) are left out (check).
PEOPLE_CHECKED_ON = "2026-11-10"
PEOPLE = {
    "USD": ["Warsh", "Powell", "Waller"],
    "EUR": ["Lagarde", "Vuj\u010di\u0107", "Vujcic", "Schnabel", "Nagel"],
    "GBP": ["Bailey", "Lombardelli", "Breeden"],
    "JPY": ["Ueda"],
    "CHF": ["Schlegel"],
    "AUD": ["Bullock"],
    "CAD": ["Macklem"],
    "NZD": ["Breman"],
}

TRAP_WORDS = {"pound", "cable", "kiwi"}   # count only with another word of the same code
USD_METALS = ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD")   # USD words touch these too (check)
CNY_TOUCHES = ("COPPER", "AUDUSD", "NZDUSD", "AUDJPY")   # CNY words touch these (check)

KIND_WORDS = [
    ("central bank", ["rate decision", "interest rate", "rates", "rate hike", "rate cut", "hike",
                      "hikes", "cut rates", "holds rates", "policy statement", "monetary policy",
                      "minutes", "press conference", "fed", "fomc", "ecb", "boe", "boj", "snb",
                      "rba", "boc", "rbnz"]),
    ("data", ["cpi", "inflation", "ppi", "pce", "gdp", "payrolls", "nfp", "jobs", "employment",
              "unemployment", "jobless claims", "retail sales", "pmi", "ism", "inventories",
              "stocks build", "stocks draw", "gas storage", "trade balance"]),
    ("supply", ["opec", "output", "production", "supply", "sanctions", "pipeline", "refinery",
                "outage", "hormuz", "red sea", "strike"]),
    ("intervention", ["intervention", "intervene", "intervenes", "rate check"]),
    ("conflict", ["war", "attack", "missile", "ceasefire", "invasion"]),
]

# Words in a title that mark news which can move a price at once (the "!" mark).
# The mark never says which way, and never says to trade.
IMPORTANT_WORDS = ["rate decision", "raises rates", "cuts rates", "hikes rates", "holds rates",
                   "rates unchanged", "rate hike", "rate cut", "monetary policy decision",
                   "monetary policy decisions", "fomc statement", "intervention", "intervenes",
                   "emergency", "nonfarm payrolls", "non-farm payrolls", "jobs report", "cpi",
                   "gdp", "output cut", "production cut"]


class FeedError(Exception):
    """A feed request or answer that failed; .error is "http 403", "network",
    "not a feed" or "too big". It never reaches the user as a traceback."""

    def __init__(self, error):
        Exception.__init__(self, error)
        self.error = error


# ---- reading a feed (spec 4.2)

def clean(text):
    """Feed HTML or text -> one plain line: tags out, entities decoded, soft hyphens and
    zero-width marks out, an em dash made "-", runs of spaces made one, ends stripped."""
    if not text:
        return ""
    t = html.unescape(re.sub(r"<[^>]+>", " ", text))
    t = re.sub("[\u00ad\u200b-\u200d\u2060\ufeff]", "", t).replace(chr(0x2014), "-")
    return re.sub(r"\s+", " ", t).strip()


def lead(text, size=300):
    """The start of a story, cut at a word. Used only for tagging; never shown."""
    t = text or ""
    if len(t) > size:
        t = t[:size].rsplit(" ", 1)[0].rstrip(",;:")
    return t


def _local(tag):
    """An XML tag without its namespace: "{http://...}link" -> "link"."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(el, name):
    """The whole text of the first child of el whose local name is name, or ""."""
    for c in el:
        if _local(c.tag) == name:
            return "".join(c.itertext()).strip()
    return ""


def _read_time(form, text):
    """One try of parse_feed_time; None when it does not fit."""
    try:
        if form == "rfc822":
            dt = email.utils.parsedate_to_datetime(text)
            if dt is None:
                return None
            if dt.tzinfo is not None:
                dt = (dt - dt.utcoffset()).replace(tzinfo=None)
            return dt
        if form == "plain":
            return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        if form == "month":
            m = re.match(r"^([A-Za-z]{3}) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}) GMT$", text)
            if not m or m.group(1).title() not in MONTH_NAMES:
                return None
            return datetime.datetime(int(m.group(3)), MONTH_NAMES.index(m.group(1).title()) + 1,
                                     int(m.group(2)), int(m.group(4)), int(m.group(5)))
        if form == "iso" and re.match(r"^\d{4}-\d{2}-\d{2}T", text):
            return parse_utc(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return None


def parse_feed_time(text):
    """A feed's date text -> naive UTC, or None (never raises). Reads RFC 822 with a
    zone, "2026-11-10 06:38:55" (UTC), "Nov 09, 2026 15:51 GMT" and ISO 8601.
    A zone is taken as written (EIA writes EST even in summer) (check)."""
    if not text or not isinstance(text, str):
        return None
    t = " ".join(text.split())
    for form in ("rfc822", "plain", "month", "iso"):
        dt = _read_time(form, t)
        if dt is not None and 1990 <= dt.year <= 2200:
            return dt
    return None


def _feed_root(body):
    """The parsed root element of feed bytes (a BOM and spaces cut first), or None."""
    raw = (body or b"").lstrip()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:].lstrip()
    try:
        return ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError):
        return None


def check_feed(body):
    """Raise FeedError("not a feed") unless the bytes parse as XML with a root named
    rss, RDF or feed (namespace dropped)."""
    root = _feed_root(body)
    if root is None or _local(root.tag) not in ("rss", "RDF", "feed"):
        raise FeedError("not a feed")


def _item_link(el):
    """The item's link: the text of <link>, or for Atom the href of the alternate link."""
    link = _child_text(el, "link")
    if link:
        return link
    hrefs = [c for c in el if _local(c.tag) == "link" and c.get("href")]
    best = [c for c in hrefs if c.get("rel", "alternate") == "alternate"] or hrefs
    return best[0].get("href").strip() if best else ""


def parse_feed(data, feed, fetched=None):
    """RSS 2.0, RSS 1.0 or Atom bytes -> (items, bad_dates). Each item: key, links,
    title, link, text (for tags only, never shown), at (naive UTC), source, feed.
    An item with no readable date, or dated more than 2 days after fetched (the fetch
    time, when known), is left out and counted in bad_dates. Bytes that are not a
    feed raise FeedError("not a feed")."""
    root = _feed_root(data)
    if root is None or _local(root.tag) not in ("rss", "RDF", "feed"):
        raise FeedError("not a feed")
    name = feed["name"]
    channel = ""
    for el in root.iter():
        if _local(el.tag) in ("channel", "feed"):
            channel = clean(_child_text(el, "title"))
            break
    if not channel and _local(root.tag) == "feed":
        channel = clean(_child_text(root, "title"))
    source = NEWS_SOURCES.get(name) or channel[:40] or name
    items, bad = [], 0
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = clean(_child_text(el, "title"))
        link = _item_link(el)
        if not title and not link:
            continue
        key = link or _child_text(el, "guid") or _child_text(el, "id") or "%s:%s" % (name, title)
        when = None
        for tag in ("pubDate", "published", "updated", "date"):
            raw = _child_text(el, tag)
            if raw:
                when = parse_feed_time(raw)
                break
        if when is None or (fetched is not None
                            and when > fetched + datetime.timedelta(days=NEWS_FUTURE_DAYS)):
            bad += 1
            continue
        body = (_child_text(el, "description") or _child_text(el, "summary")
                or _child_text(el, "encoded"))
        items.append({"key": key, "links": [key], "title": title, "link": link,
                      "text": (title + " " + lead(clean(body))).strip(), "at": when,
                      "source": source, "feed": name})
    return items, bad


# ---- tagging (spec 4.3 and 4.4)

HOW_RANK = {"named": 3, "currency": 2, "feed": 1}


def _first_word(words, low):
    """The earliest (position, word) of the lower-case whole words in low, or None."""
    best = None
    for w in words:
        m = re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), w)
    return best


def _first_name(names, text):
    """The earliest (position, name) of the case-sensitive whole names in text, or None."""
    best = None
    for w in names:
        m = re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), w)
    return best


def _pair_spans(text, cfg):
    """[(start, end, key, matched)] of every instrument's pair code (BASEQUOTE,
    BASE/QUOTE) and key in text, case-sensitive, letters at both edges."""
    spans = []
    for key, row in cfg["instruments"].items():
        forms = {key}
        base, quote = row.get("base"), row.get("quote")
        if all(isinstance(x, str) and re.fullmatch(r"[A-Z]{3}", x) for x in (base, quote)):
            forms.update([base + quote, base + "/" + quote])
        alts = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
        for m in re.finditer(r"(?<![A-Za-z])(?:%s)(?![A-Za-z])" % alts, text):
            spans.append((m.start(), m.end(), key, m.group(0)))
    return spans


def currency_word(code, text, low):
    """The first word of a currency code found in an item, or None. Trap words
    (pound, cable, kiwi) count only next to another word of the same code."""
    hits = []
    plain = [w for w in CURRENCY_WORDS.get(code, []) if w not in TRAP_WORDS]
    traps = [w for w in CURRENCY_WORDS.get(code, []) if w in TRAP_WORDS]
    for found in (_first_word(plain, low), _first_name([code] + PEOPLE.get(code, []), text)):
        if found:
            hits.append(found)
    if not hits:
        return None
    trap = _first_word(traps, low)
    if trap:
        hits.append(trap)
    return min(hits)[1]


def code_touches(code, cfg):
    """The instrument keys a currency code touches: the fx pairs with that base or
    quote; USD also the four metals, CNY also copper and the AUD and NZD pairs."""
    keys = [k for k, row in cfg["instruments"].items()
            if row.get("kind") == "fx" and code in (row.get("base"), row.get("quote"))]
    extra = USD_METALS if code == "USD" else CNY_TOUCHES if code == "CNY" else ()
    return keys + [k for k in extra if k in cfg["instruments"] and k not in keys]


def _instrument_words(key, cfg):
    """The words of one instrument: its INSTRUMENT_WORDS row, else (not a pair) its name."""
    if key in INSTRUMENT_WORDS:
        return INSTRUMENT_WORDS[key]
    row = cfg["instruments"].get(key, {})
    name = str(row.get("name") or "").strip().lower()
    return [name] if name and row.get("kind") != "fx" else []


def item_tags(text, title):
    """(tags, important): the kinds whose words are in the text, and whether the title
    holds one of IMPORTANT_WORDS."""
    low = text.lower()
    tags = [tag for tag, words in KIND_WORDS if _first_word(words, low)]
    return tags, bool(_first_word(IMPORTANT_WORDS, title.lower()))


def tag_items(items, symbols, cfg):
    """Give each item "about" ([{symbol, how, word}] for the symbols asked, the
    strongest how per symbol: named, then currency, then feed), "tags" and
    "important". Returns the same list."""
    for item in items:
        text = item["title"] + " " + item["text"]
        found = {}

        def touch(sym, how, word):
            if sym in symbols and (sym not in found
                                   or HOW_RANK[how] > HOW_RANK[found[sym][0]]):
                found[sym] = (how, word)

        # 1. pair codes and keys; then cut them, so XAU/USD adds no USD touch
        spans = sorted(_pair_spans(text, cfg))
        for start, end, key, matched in spans:
            touch(key, "named", matched)
        cut = list(text)
        for start, end, key, matched in spans:
            cut[start:end] = [" "] * (end - start)
        cut = "".join(cut)
        low = cut.lower()
        # 2. instrument words
        for key in symbols:
            hit = _first_word(_instrument_words(key, cfg), low)
            if hit:
                touch(key, "named", hit[1])
        # 3. currency words
        for code in CURRENCY_WORDS:
            word = currency_word(code, cut, low)
            if word:
                for key in code_touches(code, cfg):
                    touch(key, "currency", "%s word %s" % (code, word))
        # 4. central bank feeds
        code = FEED_CURRENCY.get(item.get("feed"))
        if code:
            for key in code_touches(code, cfg):
                touch(key, "feed", "%s feed" % item["source"])
        item["about"] = [{"symbol": s, "how": found[s][0], "word": found[s][1]}
                         for s in symbols if s in found]
        item["tags"], item["important"] = item_tags(text, item["title"])
    return items


# ---- one item per story, the window, the seen marks (spec 4.1 steps 8 to 11)

def _story_key(item):
    """The same story: its title in lower case without signs, on the same Karachi date."""
    words = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
    return (words, to_pkt(item["at"]).date().isoformat())


def dedupe(items):
    """One item per story: the same link, or the same title (lower case, no signs) on
    the same Karachi date. The first kept is from the earlier feed; it takes the union
    of the about lists and keeps every merged link."""
    out, by_link, by_story = [], {}, {}
    for item in items:
        keep = None
        for link in item["links"]:
            keep = keep or by_link.get(link)
        if keep is None and item["title"]:
            keep = by_story.get(_story_key(item))
        if keep is None:
            item = dict(item, links=list(item["links"]), about=list(item.get("about", [])))
            out.append(item)
            keep = item
        else:
            have = {a["symbol"]: n for n, a in enumerate(keep["about"])}
            for a in item.get("about", []):
                n = have.get(a["symbol"])
                if n is None:
                    keep["about"].append(a)
                elif HOW_RANK[a["how"]] > HOW_RANK[keep["about"][n]["how"]]:
                    keep["about"][n] = a
            for link in item["links"]:
                if link not in keep["links"]:
                    keep["links"].append(link)
            keep["tags"] = [t for t, _ in KIND_WORDS
                            if t in keep.get("tags", []) or t in item.get("tags", [])]
            keep["important"] = bool(keep.get("important") or item.get("important"))
        for link in keep["links"]:
            by_link[link] = keep
        if keep["title"]:
            by_story[_story_key(keep)] = keep
    return out


def select(items, state, now, hours, show_all, max_n):
    """(items, left_out): the items dated from now - hours to now + 10 minutes; only
    those none of whose links was seen, unless show_all (then seen ones get new False);
    newest first (a tie keeps feed order); at most max_n."""
    start = now - datetime.timedelta(hours=hours)
    end = now + datetime.timedelta(minutes=NEWS_LATE_MIN)
    seen = state.get("seen") or {}
    keep = []
    for n, item in enumerate(items):
        if not (start <= item["at"] <= end):
            continue
        new = not any(link in seen for link in item["links"])
        if new or show_all:
            keep.append((n, dict(item, new=new)))
    keep.sort(key=lambda p: p[1]["at"], reverse=True)   # stable: a tie keeps feed order
    keep = [it for _, it in keep]
    return keep[:max_n], max(0, len(keep) - max_n)


def remember(items, state, now):
    """Mark the shown items seen (seen[link] = now, when not there yet), drop marks
    older than NEWS_KEEP_DAYS days, and set last_check."""
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    for item in items:
        for link in item["links"]:
            seen.setdefault(link, utc_iso(now))
    cut = now - datetime.timedelta(days=NEWS_KEEP_DAYS)
    for link in list(seen):
        when = parse_utc(seen[link]) if isinstance(seen[link], str) else None
        if when is None or when < cut:
            del seen[link]
    state["seen"] = seen
    state["last_check"] = utc_iso(now)


# ---- the state and the saved feeds (spec 3.3, 4.1 step 5, 5)

DAMAGED = "fx/state/news.json was damaged. Started a new one."


def load_state(path, now, rename=True):
    """(state, note or None). A missing file gives an empty state. A broken one is
    renamed news.broken-<UTC stamp>.json (unless rename is False, as in --dry-run) and
    an empty state is used, with the DAMAGED note."""
    try:
        doc = load_json(path)
        ok = doc is None or (isinstance(doc, dict)
                             and isinstance(doc.get("seen", {}), dict)
                             and isinstance(doc.get("feeds", {}), dict))
    except ValueError:
        ok = False
    if ok:
        doc = doc or {}
        doc.setdefault("seen", {})
        doc.setdefault("last_check", None)
        doc.setdefault("feeds", {})
        return doc, None
    if rename:
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        broken = os.path.join(os.path.dirname(path), "news.broken-%s.json" % stamp)
        try:
            os.replace(path, broken)
        except OSError as exc:
            raise ValueError("could not rename %s: %s" % (path, exc.strerror or exc))
    return {"seen": {}, "last_check": None, "feeds": {}}, DAMAGED


def save_bytes_atomic(path, data):
    """Write bytes to path + ".tmp", then move it onto path in one step. Makes the
    folder first. An OSError becomes ValueError("could not save <path>: <reason>")."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ValueError("could not save %s: %s" % (path, exc.strerror or exc))


# ---- the network (spec 3.2): RSS files only, one try, paced

def http_get(url, timeout=NEWS_TIMEOUT):
    """One GET of a feed -> the body bytes (un-gzipped). Sends only the pack's UA and
    "Accept-Encoding: gzip". Raises FeedError("http <code>"), FeedError("network")
    (no answer, a timeout, a cut-off body, a broken gzip) or FeedError("too big").
    The pace between requests is kept by news_pace (tests swap this function)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with OPEN_URL(req, timeout=timeout) as r:
            status = getattr(r, "status", 200)
            enc = str(r.headers.get("Content-Encoding") or "").lower()
            body = r.read(NEWS_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FeedError("http %s" % exc.code)
    except Exception:       # URLError, a timeout, a reset or a cut-off answer
        raise FeedError("network")
    if status != 200:
        raise FeedError("http %s" % status)
    if len(body) > NEWS_MAX_BYTES:
        raise FeedError("too big")
    if "gzip" in enc or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:   # OSError, EOFError or zlib.error
            raise FeedError("network")
    if len(body) > NEWS_MAX_BYTES:
        raise FeedError("too big")
    return body


def news_pace():
    """Wait (SLEEP) so two feed requests of this run are at least NEWS_PACE_S apart."""
    last = _last_get[0]
    if last is not None:
        wait = NEWS_PACE_S - (CLOCK() - last)
        if wait > 0:
            SLEEP(wait)


def _file_utc(path):
    """A file's change time as naive UTC, or None."""
    try:
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(os.path.getmtime(path)))
    except (OSError, ValueError, OverflowError):
        return None


def _read_saved(path):
    """The bytes of a saved feed, or None when there is none."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def get_feed(feed, paths, state, now, offline):
    """(bytes or None, row) for one feed (spec 4.1 step 6). Online and not tried in the
    last 15 minutes: one paced request; a good answer is saved to data/news/<name>.xml.
    Else, or when the request fails, the saved copy is read. The feed status in
    state["feeds"] is updated after each request. row is the --json feeds row."""
    name = feed["name"]
    path = os.path.join(paths["news"], name + ".xml")
    feeds = state.setdefault("feeds", {})
    status = feeds.get(name) if isinstance(feeds.get(name), dict) else {}
    row = {"name": name, "source": NEWS_SOURCES.get(name) or name, "used": "none",
           "error": None, "fetched_at": status.get("fetched_at"), "items": 0, "_path": path}
    if not offline:
        tried = parse_utc(status.get("tried_at"))
        fresh = tried is not None and 0 <= (now - tried).total_seconds() < NEWS_MIN_REFRESH_S
        if not fresh:
            body = None
            news_pace()
            try:
                body = http_get(feed["url"])
                check_feed(body)
            except FeedError as exc:
                row["error"] = exc.error
                body = None
            finally:
                _last_get[0] = CLOCK()
            feeds[name] = {"tried_at": utc_iso(now), "fetched_at": status.get("fetched_at"),
                           "error": row["error"]}
            if body is not None:
                save_bytes_atomic(path, body)
                feeds[name]["fetched_at"] = row["fetched_at"] = utc_iso(now)
                row["used"] = "fetched"
                return body, row
    body = _read_saved(path)
    if body is not None:
        row["used"] = "saved"
    return body, row


# ---- flags and the symbols (spec 2 and 4.1 steps 3 and 4)

def _comma_list(text, flag):
    """"a, b,,c" -> ["a", "b", "c"]; an empty list is an error."""
    out = [x.strip() for x in str(text or "").split(",") if x.strip()]
    if not out:
        raise ValueError("%s is empty." % flag)
    return out


def parse_symbols(text, cfg, tool):
    """--symbols text -> instrument keys: commas, spaces dropped, capitals, no repeats.
    An unknown key is an error naming the nearest key (06 2.1). tool is for the
    caller's own use (the message gets its "news.py: " from fail)."""
    keys = list(cfg["instruments"])
    out = []
    for sym in _comma_list(text, "--symbols"):
        sym = sym.upper()
        if sym not in cfg["instruments"]:
            near = difflib.get_close_matches(sym, keys, n=1, cutoff=0.6)
            msg = "%s is not in your instruments." % sym
            raise ValueError(msg + (" Did you mean %s?" % near[0] if near else ""))
        if sym not in out:
            out.append(sym)
    return out


def feed_list(cfg, names):
    """The news.feeds rows to read, in config order: all, or only those named in the
    --feeds text. An empty news.feeds or an unknown name is an error."""
    feeds = (cfg.get("news") or {}).get("feeds") or []
    if not feeds:
        raise ValueError('Your config has no news feeds ("news.feeds" is empty in fx/config.json).')
    if names is None:
        return list(feeds)
    have = [f["name"] for f in feeds]
    want = []
    for name in _comma_list(names, "--feeds"):
        name = name.lower()
        if name not in have:
            near = difflib.get_close_matches(name, have, n=1, cutoff=0.6)
            msg = "%s is not one of your news feeds." % name
            if near:
                raise ValueError(msg + " Did you mean %s?" % near[0])
            raise ValueError(msg + " Your feeds are: %s." % ", ".join(have))
        want.append(name)
    return [f for f in feeds if f["name"] in want]


def _check_row(row, cfg, path):
    """The reader checks of one watchlist hit, plan or position: symbol, side, style."""
    check_symbol(row.get("symbol"), cfg, path)
    if row.get("side") is not None:
        check_side(row.get("side"), path)
    if row.get("style") is not None:
        check_style(row.get("style"), path)
    return row["symbol"]


def symbols_from_files(cfg, watchlist, plan, portfolio):
    """The symbols of the watchlist hits, the plan and the open positions, in that
    order, no repeats. A missing file is fine; a broken one is an error (01 1.3)."""
    out = []
    doc = load_doc(watchlist, "watchlist", "fx-scanner", required=False)
    if doc is not None:
        if doc.get("mode") not in ("swing", "day"):
            raise ValueError('%s: "mode" must be "swing" or "day"' % watchlist)
        out += [_check_row(h, cfg, watchlist) for h in check_objects(doc, "hits", watchlist)]
    doc = load_doc(plan, "plan", "fx-exit", required=False)
    if doc is not None:
        out.append(_check_row(doc, cfg, plan))
    doc = load_doc(portfolio, "portfolio", "fx-broker", required=False)
    if doc is not None:
        out += [_check_row(p, cfg, portfolio) for p in check_objects(doc, "positions", portfolio)]
    seen = []
    for sym in out:
        if sym not in seen:
            seen.append(sym)
    return seen


def pick_symbols(args, cfg, paths):
    """(keys, from): --symbols ("flag"), else the watchlist, plan and open trades
    ("files"), else the whole config universe ("universe")."""
    if args.symbols is not None:
        return parse_symbols(args.symbols, cfg, SCRIPT), "flag"
    keys = symbols_from_files(cfg, args.watchlist or paths["watchlist"], paths["plan"],
                              paths["portfolio"])
    if keys:
        return keys, "files"
    return [k for k in cfg.get("universe", []) if k in cfg["instruments"]], "universe"


def _number_flag(text, flag, lo, hi, whole):
    """A --hours or --max value inside lo to hi; else the plain error."""
    want = "a whole number" if whole else "a number"
    try:
        x = int(text) if whole else float(text)
    except (TypeError, ValueError):
        x = None
    if x is None or not (lo <= x <= hi) or (not whole and not math.isfinite(x)):
        raise ValueError("%s wants %s from %d to %d, not %s." % (flag, want, lo, hi, text))
    return x


# ---- the printout (spec 6)

WIDTH = 78             # the line width of fx-news and fx-risk
FROM_WORDS = {"flag": "from --symbols",
              "files": "from your watchlist, plan and open trades",
              "universe": "your whole universe: no watchlist, plan or open trades"}


def when_text(utc):
    """"Fri 17 Jul 2:40 AM PKT (5:40 PM New York)"."""
    return pkt_label(utc, date=True) + " (" + ny_label(utc) + ")"


def _plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def _wrap(text, first="", rest=""):
    """Lines of at most WIDTH characters (a long word is never cut)."""
    return textwrap.wrap(text, width=WIDTH, initial_indent=first, subsequent_indent=rest,
                         break_long_words=False, break_on_hyphens=False) or [first.rstrip()]


def about_text(about):
    """"XAUUSD (gold)"; "EURUSD, GBPUSD (USD word dollar)": named symbols first;
    symbols that share one word share one bracket."""
    groups = []
    for how in ("named", "currency", "feed"):
        for a in about:
            if a["how"] != how:
                continue
            for g in groups:
                if g[0] == a["word"]:
                    g[1].append(a["symbol"])
                    break
            else:
                groups.append((a["word"], [a["symbol"]]))
    return "; ".join("%s (%s)" % (", ".join(syms), word) for word, syms in groups)


def answer_line(items):
    """"Nothing new.", "1 new headline.", "N new headlines." or "N headlines (M new).\""""
    new = sum(1 for it in items if it["new"])
    if len(items) > new:
        return "%s (%d new)." % (_plural(len(items), "headline", "headlines"), new)
    if not new:
        return "Nothing new."
    return "%s." % _plural(new, "new headline", "new headlines")


def item_lines(item):
    """The four (or more, when wrapped) lines of one headline."""
    lines = ["%s  %s" % (when_text(item["at"]), item["source"])]
    title = item["title"] + ("" if item["new"] else " (seen before)")
    lines += _wrap(title, "  %s " % ("!" if item["important"] else " "), "    ")
    about = "About: %s." % about_text(item["about"])
    if item["tags"]:
        about += " Tags: %s." % ", ".join(item["tags"])
    lines += _wrap(about, "    ", "    ")
    if item.get("link"):
        lines.append("    " + item["link"])
    return lines


def render(result):
    """The words mode text of one run (spec 6.1)."""
    quiet = result["quiet"]
    lines = [answer_line(result["items"])]
    if not quiet:
        lines += _wrap("For: %s (%s)." % (", ".join(result["symbols"]),
                                         FROM_WORDS[result["symbols_from"]]))
        h = result["hours"]
        h = int(h) if float(h).is_integer() else h
        lines += _wrap("From %s to now (the last %s)." % (
            pkt_label(result["start"], date=True), _plural(h, "hour", "hours")
            if isinstance(h, int) else "%s hours" % h))
    lines.append("")
    for item in result["items"]:
        lines += item_lines(item) + [""]
    if not quiet:
        for note in result["notes"]:
            lines += _wrap(note)
        lines += _wrap("Checked %s. Headlines are leads, not facts: open the link and read "
                       "the story. Not financial advice." % pkt_label(result["now"], date=True))
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# ---- the run (spec 4.1)

def _age_words(row, now):
    """How old a saved copy is, for a note: "2 h 5 min ago", "just now"."""
    when = parse_utc(row.get("fetched_at")) or _file_utc(row["_path"])
    if when is None:
        return "an unknown time"
    text = age_text((now - when).total_seconds() / 60.0)
    return "just now" if text == "live" else text.replace(" old", " ago")


def read_feeds(feeds, paths, state, now, offline):
    """(items, rows, notes, bad_dates) of every feed, with the feed notes of 6.1."""
    items, rows, bad = [], [], 0
    kinds = {"saved": [], "lost": [], "missing": [], "broken": []}
    for feed in feeds:
        body, row = get_feed(feed, paths, state, now, offline)
        if body is not None:
            fetched = (now if row["used"] == "fetched"
                       else parse_utc(row.get("fetched_at")) or _file_utc(row["_path"]))
            try:
                got, n_bad = parse_feed(body, feed, fetched)
            except FeedError:
                row["used"] = "none"
                kinds["broken"].append("Note: the saved copy of %s is not a feed. It is left out."
                                       % row["source"])
                got, n_bad = [], 0
            if got:
                row["source"] = got[0]["source"]
            row["items"] = len(got)
            items += got
            bad += n_bad
        rel = "fx/data/news/%s.xml" % feed["name"]
        if row["error"] and row["used"] == "saved":
            kinds["saved"].append("Note: %s could not be read now (%s). Using its saved copy from %s."
                                  % (row["source"], row["error"], _age_words(row, now)))
        elif row["error"] and body is None:
            kinds["lost"].append("Note: %s could not be read now (%s). It has no saved copy, so it "
                                 "is left out." % (row["source"], row["error"]))
        elif body is None:
            kinds["missing"].append("Note: no saved copy of %s (%s)." % (row["source"], rel))
        rows.append(row)
    m = len(rows)
    if offline:
        found = sum(1 for f in feeds if os.path.isfile(os.path.join(paths["news"], f["name"] + ".xml")))
        if found == 0:
            raise ValueError("No saved feeds in fx/data/news/. Run without --offline once to get them.")
        notes = ["Offline: the saved feeds in fx/data/news/ are used (%d of %d found)." % (found, m)]
    else:
        if all(r["used"] == "none" for r in rows):
            raise ValueError("No news feed could be read, and there is no saved copy in "
                             "fx/data/news/. Check your internet and run again.")
        now_n = sum(1 for r in rows if r["used"] == "fetched")
        notes = ["Feeds: %d of %d read now, %d from a saved copy."
                 % (now_n, m, sum(1 for r in rows if r["used"] == "saved"))]
    notes += kinds["saved"] + kinds["lost"] + kinds["missing"] + kinds["broken"]
    return items, rows, notes, bad


def build_parser():
    p = argparse.ArgumentParser(
        prog="news.py",
        description="Forex and commodity headlines from your RSS feeds, tagged to your "
                    "instruments. Shows only what is new since the last check.")
    p.add_argument("--symbols", metavar="A,B", default=None, help="only these instruments")
    p.add_argument("--all", action="store_true", help="also show items already seen")
    p.add_argument("--dry-run", action="store_true", help="do not mark anything as seen")
    p.add_argument("--hours", metavar="H", default=str(NEWS_HOURS),
                   help="only items from the last H hours (1 to 720, default 48)")
    p.add_argument("--feeds", metavar="NAMES", default=None,
                   help="read only these feeds of news.feeds (comma list)")
    p.add_argument("--max", metavar="N", default=str(NEWS_MAX_ITEMS),
                   help="show at most N items (1 to 500, default 40)")
    p.add_argument("--watchlist", metavar="FILE", default=None,
                   help="the watchlist to read (default fx/watchlist.json)")
    add_common(p)
    return p


def run(args):
    """One run (spec 4.1). Returns the result dict for render and --json."""
    now = parse_now(args.now)
    offline = bool(args.offline or args.now is not None)
    hours = _number_flag(args.hours, "--hours", 1, 720, whole=False)
    max_n = _number_flag(args.max, "--max", 1, 500, whole=True)
    work = os.path.dirname(os.path.abspath(args.config)) if args.config else "fx"
    paths = fx_paths(work, args.data, args.config)
    cfg = load_config(paths["config"], required=bool(args.config))
    feeds = feed_list(cfg, args.feeds)
    symbols, origin = pick_symbols(args, cfg, paths)
    state, damaged = load_state(paths["news_state"], now, rename=not args.dry_run)
    items, rows, notes, bad = read_feeds(feeds, paths, state, now, offline)
    items = dedupe([it for it in tag_items(items, symbols, cfg) if it["about"]])
    shown, left_out = select(items, state, now, hours, args.all, max_n)
    if bad:
        notes.append("Note: 1 headline had a date this tool cannot read. It is left out."
                     if bad == 1 else
                     "Note: %d headlines had a date this tool cannot read. They are left out." % bad)
    if left_out:
        more = min(2 * max_n, 500)
        notes.append("Note: 1 more headline is not shown. Use --max %d to see it." % more
                     if left_out == 1 else
                     "Note: %d more headlines are not shown. Use --max %d to see them."
                     % (left_out, more))
    if args.dry_run:
        notes.append("Dry run: nothing was marked as seen.")
    if origin == "universe":
        notes.append("No watchlist, plan or open trades. Showing news for your whole "
                     "universe (%s)." % _plural(len(symbols), "instrument", "instruments"))
    if damaged:
        notes.append(damaged)
    if any(it["important"] for it in shown):
        notes.append("This tool shows no prices. For a price, use /fx-alerts quote.")
    if not args.dry_run:
        remember(shown, state, now)
        names = [f["name"] for f in cfg["news"]["feeds"]]
        state["feeds"] = {k: v for k, v in state.get("feeds", {}).items() if k in names}
        save_json_atomic(paths["news_state"], {"seen": state["seen"],
                                               "last_check": state["last_check"],
                                               "feeds": state["feeds"]})
    return {"now": now, "start": now - datetime.timedelta(hours=hours), "hours": hours,
            "symbols": symbols, "symbols_from": origin, "items": shown, "left_out": left_out,
            "bad_dates": bad, "rows": rows, "notes": notes, "dry_run": bool(args.dry_run),
            "quiet": bool(args.quiet)}


def to_json(result):
    """The --json object (spec 6.3). No summary text is in it."""
    hours = result["hours"]
    return {"v": JSON_VERSION, "tool": TOOL, "cmd": None, "now": utc_iso(result["now"]),
            "ok": True, "doc": None, "notes": result["notes"], "symbols": result["symbols"],
            "symbols_from": result["symbols_from"],
            "hours": int(hours) if float(hours).is_integer() else hours,
            "dry_run": result["dry_run"],
            "items": [{"key": it["key"], "links": it["links"], "title": it["title"],
                       "link": it["link"], "source": it["source"], "feed": it["feed"],
                       "at": utc_iso(it["at"]), "about": it["about"], "tags": it["tags"],
                       "important": it["important"], "new": it["new"]}
                      for it in result["items"]],
            "left_out": result["left_out"], "bad_dates": result["bad_dates"],
            "feeds": [{k: v for k, v in r.items() if not k.startswith("_")}
                      for r in result["rows"]]}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except ValueError as e:
        return fail(e, SCRIPT)
    if args.json:
        emit_json(to_json(result))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
