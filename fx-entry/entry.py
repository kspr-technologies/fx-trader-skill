#!/usr/bin/env python3
"""fx-entry: the Entry Timer of fx-trader-skill.

    python3 fx-entry/entry.py check SYMBOL [options]
    python3 fx-entry/entry.py watchlist [options]
    python3 fx-entry/entry.py watch SYMBOL [options]

It watches one setup and names the moment its rule is met: the first FINISHED bar that
closes beyond the level. Swing: a finished daily bar (built from Yahoo 1h bars, cut at
day_end) closes beyond the scanner's level. Day: the first finished 5m bar after the
opening range closes above the range high (buy) or below the range low (sell), before
the entry cut-off. It says waiting, triggered, expired, late or closed, and with
--write it saves fx/entry.json for fx-exit.

It never places an order, never sizes a trade and never sets the stop. Prices come
from Yahoo only (never with --offline or --now). Standard library only, Python 3.8.
"""

import argparse
import copy
import datetime
import difflib
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP


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


def price_text(price, inst):
    """A price with all the instrument's digits kept and thousands commas:
    "2,433.40" for gold, "1.08450" for EURUSD, "-3.50" for oil below zero."""
    d = _words_dec(price, "a price")
    q = d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP)
    return "%s%s" % ("-" if q < 0 else "", format(abs(q), ",f"))


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


def fx_days_between(a, b, cfg):
    """How many FX days d with a < d <= b (weekends and full FX holidays skipped).
    b before a gives the negative count."""
    da, db = parse_fx_date(a, weekend_ok=True), parse_fx_date(b, weekend_ok=True)
    if db < da:
        return -fx_days_between(db, da, cfg)
    n, d = 0, da
    while d < db:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and not full_fx_holiday(d, cfg):
            n += 1
    return n


def add_fx_days(fx_date, n, cfg):
    """The FX day n FX days after fx_date (next_fx_day n times; n is 0 or more)."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("add_fx_days wants a whole number of days, 0 or more, not %r" % (n,))
    d = parse_fx_date(fx_date, weekend_ok=True).isoformat()
    for _ in range(n):
        d = next_fx_day(d, cfg)
    return d


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


NFP_DATES = [   # US jobs report, 08:30 New York (BLS schedule; the feed's time wins)
    "2026-12-04", "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07",
    "2027-06-04", "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05",
    "2027-12-03",
]


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


CB_MEETINGS = {
    "fed": {"currency": "USD", "city": "ny", "time": "14:00", "end": "14:30", "title": "Fed rate decision (FOMC)",
        "provisional": False, "dates": [
            "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
            "2026-10-28", "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
            "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
        ]},
    "fed_minutes": {"currency": "USD", "city": "ny", "time": "14:00", "end": None, "title": "Fed minutes",
        "provisional": True, "dates": [
            "2026-11-18", "2026-12-30", "2027-02-17", "2027-04-07", "2027-05-19", "2027-06-30",
            "2027-08-18", "2027-10-06", "2027-11-17", "2027-12-29",
        ]},
    "ecb": {"currency": "EUR", "city": "london", "time": "13:15", "end": "13:45", "title": "ECB rate decision",
        "provisional": False, "dates": [
            "2026-10-29", "2026-12-17", "2027-02-04", "2027-03-18", "2027-04-29", "2027-06-10",
            "2027-07-22", "2027-09-09", "2027-10-28", "2027-12-16",
        ]},
    "boe": {"currency": "GBP", "city": "london", "time": "12:00", "end": None, "title": "BoE rate decision",
        "provisional": False, "dates": [
            "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-18", "2026-07-30", "2026-09-17",
            "2026-11-05", "2026-12-17", "2027-02-04", "2027-03-18", "2027-04-29", "2027-06-17",
            "2027-07-29", "2027-09-16", "2027-11-04", "2027-12-16",
        ]},
    "boj": {"currency": "JPY", "city": "tokyo", "time": "11:30", "end": "13:30", "title": "BoJ rate decision",
        "provisional": False, "dates": [
            "2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16", "2026-07-31", "2026-09-18",
            "2026-10-30", "2026-12-18", "2027-01-22", "2027-03-18", "2027-04-28", "2027-06-11",
            "2027-07-22", "2027-09-22", "2027-10-29", "2027-12-17",
        ]},
    "snb": {"currency": "CHF", "city": "london", "time": "08:30", "end": "09:00", "title": "SNB rate decision",
        "provisional": False, "dates": [
            "2026-12-10", "2027-03-18", "2027-06-24", "2027-09-23", "2027-12-16",
        ]},
    "boc": {"currency": "CAD", "city": "ny", "time": "09:45", "end": None, "title": "BoC rate decision",
        "provisional": False, "dates": [
            "2026-12-09", "2027-01-27", "2027-03-03", "2027-04-28", "2027-06-02", "2027-07-21",
            "2027-09-08", "2027-10-27", "2027-12-08",
        ]},
}


EIA_TIMES = {
    "crude": {"weekday": 2, "time": "10:30", "title": "Crude Oil Inventories", "symbols": ["USOIL", "UKOIL"]},
    "gas":   {"weekday": 3, "time": "10:30", "title": "Natural Gas Storage",   "symbols": ["NATGAS"]},
}


EIA_MOVES = {
    "crude": {
        "2026-11-11": ("2026-11-12 12:00", "Veterans Day", False),
        "2027-01-20": ("2027-01-21 12:00", "MLK Day (Monday)", True),
        "2027-02-17": ("2027-02-18 12:00", "Presidents' Day", True),
        "2027-06-02": ("2027-06-03 12:00", "Memorial Day", True),
        "2027-07-07": ("2027-07-08 12:00", "Independence Day", True),
        "2027-09-08": ("2027-09-09 12:00", "Labor Day", True),
        "2027-10-13": ("2027-10-14 12:00", "Columbus Day", True),
    },
    "gas": {
        "2026-11-12": ("2026-11-13 10:30", "Veterans Day", False),
        "2026-11-26": ("2026-11-25 12:00", "Thanksgiving", False),
        "2027-11-11": ("2027-11-10 12:00", "Veterans Day (Thursday)", True),
        "2027-11-25": ("2027-11-24 12:00", "Thanksgiving", True),
    },
}


ENERGY_KEYS = {"eia_crude": ("USOIL", "UKOIL"), "eia_gas": ("NATGAS",)}


def make_event(title, country, at, kind, impact, end=None, symbols=None, provisional=False,
               source="built-in", all_day=False, forecast=None, previous=None):
    """One event in the economic calendar shape (part 03 9.5)."""
    return {"title": title, "country": country, "at": at, "end": end, "kind": kind,
            "impact": impact, "forecast": forecast, "previous": previous, "source": source,
            "symbols": symbols, "all_day": all_day, "provisional": provisional}


def builtin_events(from_utc, to_utc, cfg):
    """Central bank decisions, US jobs reports, EIA oil and gas reports and COT releases
    from the tables, with from_utc <= at < to_utc, sorted by time."""
    out = []
    last = datetime.date.fromisoformat(TABLES_END)
    keep = lambda d, at: d <= last and from_utc <= at < to_utc
    for bank in CB_MEETINGS.values():
        for iso in bank["dates"]:
            d = datetime.date.fromisoformat(iso)
            at = city_to_utc(bank["city"], at_time(d, bank["time"]))
            if keep(d, at):
                end = city_to_utc(bank["city"], at_time(d, bank["end"])) if bank["end"] else None
                out.append(make_event(bank["title"], bank["currency"], at, "cb", "High", end=end,
                                      provisional=bank["provisional"]))
    for iso in NFP_DATES:
        d = datetime.date.fromisoformat(iso)
        at = ny_to_utc(at_time(d, "08:30"))
        if keep(d, at):
            out.append(make_event("US jobs report (NFP)", "USD", at, "nfp", "High"))
    for iso in COT_RELEASES["dates"]:
        d = datetime.date.fromisoformat(iso)
        at = ny_to_utc(at_time(d, COT_RELEASES["time"]))
        if keep(d, at):
            out.append(make_event("COT report (CFTC)", "USD", at, "cot", "Low",
                                  symbols=list((cfg or {}).get("instruments") or {}),
                                  provisional=d.year in COT_RELEASES["provisional_years"]))
    d = to_ny(from_utc).date() - datetime.timedelta(days=2)
    stop = to_ny(to_utc).date() + datetime.timedelta(days=2)
    while d <= stop:
        for report, row in sorted(EIA_TIMES.items()):
            if d.weekday() != row["weekday"]:
                continue
            moved = EIA_MOVES[report].get(d.isoformat())
            if moved:
                when, prov = datetime.datetime.strptime(moved[0], "%Y-%m-%d %H:%M"), moved[2]
            elif "cme" in HOLIDAYS.get(d.isoformat(), {}).get("closed", []):
                continue
            else:
                when, prov = at_time(d, row["time"]), False
            at = ny_to_utc(when)
            if keep(d, at):
                out.append(make_event(row["title"], "USD", at, "eia_" + report, "Low",
                                      symbols=list(row["symbols"]), provisional=prov))
        d += datetime.timedelta(days=1)
    return sorted(out, key=lambda e: e["at"])


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


def day_close_utc(inst, fx_date, cfg):
    """When the instrument's broker market closes on that FX day (the early close on a
    short day), as naive UTC; None when it is shut all that day."""
    spans = open_spans(inst["hours"], fx_date, cfg)
    return spans[-1][1] if spans else None


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


def market_state(inst, utc, cfg, which="hours"):
    """Is the instrument's market open now? {state: open, break, closed, weekend or
    holiday; next_open, next_close (naive UTC or None), note, template}.
    which="data_hours" asks about Yahoo's data hours instead of the broker's."""
    template = inst[which]
    row = hours_template(template)
    group = row["group"]
    base = to_ny(utc).date()
    spans = []
    for k in range(-1, 23):
        c = base + datetime.timedelta(days=k)
        if c.weekday() < 5:
            spans += open_spans(template, c, cfg)
    current = [s for s in spans if s[0] <= utc < s[1]]
    later = [s for s in spans if s[0] > utc]
    next_open = later[0][0] if later else None
    next_close = current[0][1] if current else (later[0][1] if later else None)
    out = {"state": "closed", "next_open": next_open, "next_close": next_close, "note": "", "template": template}
    d = fx_day(utc, "17:00 New York")
    if d is None:
        out.update(state="weekend", note="Closed for the weekend.")
        return out
    note = holiday_note(d, inst, cfg)
    if group_closed(d, group, cfg):
        out.update(state="holiday", note=note or "")
        return out
    if template in DATA_TEMPLATES and group == "cme" and any(a <= utc < b for a, b in yahoo_no_data_cuts(d)):
        out.update(state="holiday", note="No Yahoo futures prices on US holidays.")
        return out
    if current:
        out.update(state="open", note=note or "")
        return out
    early = early_close_utc(d, group, cfg)
    if early is not None and utc >= early:
        out.update(state="holiday", note=note or "")
        return out
    today = open_spans(template, d, cfg, tables=False)
    if today and utc < today[0][0]:
        if parse_fx_date(d).weekday() == 0:
            if template == "yahoo_fx":
                out.update(state="closed", note="Yahoo has no FX prices before Monday 00:00 London.")
            else:
                out.update(state="weekend", note="Closed for the weekend.")
            return out
        breaks = {"fx": "Short pause at the 5:00 PM New York rollover. Spreads are wide.",
                  "ice": "Brent is shut from 5:00 PM New York to 1:00 AM London.",
                  "cme": "Daily break from 5:00 PM to 6:00 PM New York."}
        out.update(state="break", note=breaks[group])
        return out
    out["note"] = note or ""
    return out


def entry_cutoff(inst, fx_date, now, cfg):
    """The last moment for a new day trade (05 4.1): day.no_entries_after on the FX day,
    or 30 minutes before an early close when that comes first. now is not used (check)."""
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    cut = pkt_time_on_fx_day(cfg["day"]["no_entries_after"], fx_date, day_end)
    close = day_close_utc(inst, fx_date, cfg)
    if close is None:
        return cut
    return min(cut, close - datetime.timedelta(minutes=30))


def last_done_fx_day(inst, now, cfg):
    """The newest finished FX day (its end at or before now) on which Yahoo has data
    hours for the instrument; looks back at most 10 FX days; None when none."""
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = parse_fx_date(last_fx_day(now, cfg))
    seen = 0
    while seen < 10:
        if d.weekday() < 5:
            seen += 1
            if fx_day_bounds(d, day_end)[1] <= now and open_spans(inst["data_hours"], d, cfg):
                return d.isoformat()
        d -= datetime.timedelta(days=1)
    return None


CALENDAR_MIN_REFRESH_S = 3600   # at most one calendar download an hour, also after a failure


CALENDAR_AHEAD_DAYS = 35        # built-in events this far ahead (a 20-FX-day swing hold)


CALENDAR_TIMEOUT = 20           # seconds; one try, never a retry loop


FEED_TITLES = {
    "fed": ("federal funds rate", "fomc statement"), "fed_minutes": ("fomc meeting minutes",),
    "ecb": ("main refinancing rate",), "boe": ("official bank rate",), "boj": ("boj policy rate",),
    "snb": ("snb policy rate",), "boc": ("overnight rate",), "nfp": ("non-farm employment change",),
    "eia_crude": ("crude oil inventories",), "eia_gas": ("natural gas storage",),
}


def calendar_week(utc):
    """The New York date of the Sunday on or before this moment's New York date, as
    "YYYY-MM-DD" (the calendar file's week)."""
    n = to_ny(utc).date()
    return (n - datetime.timedelta(days=(n.weekday() + 1) % 7)).isoformat()


def event_label(event):
    """An event's name for people: "Main Refinancing Rate (EUR)"."""
    return "%s (%s)" % (event.get("title") or "", event.get("country") or "")


def feed_kind(title):
    """The FEED_TITLES key a feed title matches ("fed", "eia_crude" ...), or None."""
    low = (title or "").lower()
    for key, words in FEED_TITLES.items():
        if any(w in low for w in words):
            return key
    return None


def parse_ff(doc):
    """Calendar feed rows (a calendar file or a raw list) -> events with UTC times,
    no duplicates, sorted by time. Bad rows are dropped."""
    rows = doc.get("events") if isinstance(doc, dict) else doc
    out, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        text = lambda k: str(row.get(k) or "").strip()
        date, impact = text("date"), text("impact")
        at, end, all_day = parse_utc(date), None, False
        if (at is None or impact == "Holiday" or len(date) == 10) and re.match(r"^\d{4}-\d{2}-\d{2}", date):
            try:
                d = datetime.date.fromisoformat(date[:10])
            except ValueError:
                continue
            at = ny_to_utc(at_time(d, "00:00"))
            end = ny_to_utc(at_time(d + datetime.timedelta(days=1), "00:00"))
            all_day = True
        if at is None:
            continue
        key = feed_kind(text("title"))
        kind = "cb" if key in CB_MEETINGS else key
        ev = make_event(text("title"), text("country"), at, kind, impact, end=end, source="forexfactory",
                        all_day=all_day, forecast=text("forecast") or None, previous=text("previous") or None)
        k = (ev["title"], ev["country"], ev["at"])
        if k not in seen:
            seen.add(k)
            out.append(ev)
    return sorted(out, key=lambda e: e["at"])


def event_symbols(event, cfg):
    """The instrument keys an event touches, in table order: its own "symbols" (or the
    EIA keys by kind), every key for "All", else each pair with the country as a leg."""
    keys = list((cfg or {}).get("instruments") or {})
    own = event.get("symbols") or ENERGY_KEYS.get(event.get("kind"))
    if own:
        return [k for k in keys if k in own]
    country = event.get("country")
    if country == "All":
        return keys
    rows = cfg["instruments"]
    return [k for k in keys if country in (rows[k].get("base"), rows[k].get("quote"))]


def event_impact(event, symbol):
    """The feed's impact, raised to "High" for EIA oil reports on oil and EIA gas
    reports on gas."""
    if symbol in ENERGY_KEYS.get(event.get("kind"), ()):
        return "High"
    return event.get("impact")


def quiet_windows(events, symbol, cfg):
    """(start_utc, end_utc, event) around each timed event of a counted impact that
    touches symbol: news.quiet_before_min before it to news.quiet_after_min after it."""
    news = cfg["news"]
    before = datetime.timedelta(minutes=news["quiet_before_min"])
    after = datetime.timedelta(minutes=news["quiet_after_min"])
    out = []
    for e in events:
        syms = e.get("symbols")
        if syms is None:
            syms = event_symbols(e, cfg)
        if symbol not in syms or e.get("all_day") or event_impact(e, symbol) not in news["impacts"]:
            continue
        out.append((e["at"] - before, (e.get("end") or e["at"]) + after, e))
    return sorted(out, key=lambda w: w[0])


def in_quiet_window(events, symbol, utc, cfg):
    """The first event (by time) whose quiet window holds utc, or None."""
    hits = [w for w in quiet_windows(events, symbol, cfg) if w[0] <= utc < w[1]]
    return min(hits, key=lambda w: w[2]["at"])[2] if hits else None


def next_events(events, symbols, utc, n, impacts=None):
    """The first n events (by time) not over yet that touch any of symbols (None = all),
    and with impacts given, whose impact for one of those symbols is in impacts."""
    out = []
    for e in sorted(events, key=lambda x: x["at"]):
        if (e.get("end") or e["at"]) < utc:
            continue
        own = e.get("symbols") or []
        touched = list(own) if symbols is None else [s for s in symbols if s in own]
        if symbols is not None and not touched:
            continue
        if impacts is not None:
            if touched:
                if not any(event_impact(e, s) in impacts for s in touched):
                    continue
            elif e.get("impact") not in impacts:
                continue
        out.append(e)
        if len(out) >= n:
            break
    return out


def calendar_get(url, headers, timeout=CALENDAR_TIMEOUT):
    """One GET of the calendar feed, never retried and never raising:
    (status or None on a network error, body bytes, response headers dict)."""
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": UA}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, b"", dict(exc.headers.items()) if exc.headers else {}
    except Exception:
        return None, b"", {}


def merge_builtin(feed, builtin, feed_ok):
    """The built-in events minus those the feed (of an ok file) already has: the same
    country, within 3 hours, and a matching title. COT events always stay."""
    if not feed_ok:
        return list(builtin)
    titles = dict((b["title"], k) for k, b in CB_MEETINGS.items())
    out = []
    for b in builtin:
        key = titles.get(b["title"]) if b["kind"] == "cb" else b["kind"]
        words = FEED_TITLES.get(key, ())
        dup = b["kind"] != "cot" and any(
            f["country"] == b["country"] and abs(f["at"] - b["at"]) <= datetime.timedelta(hours=3)
            and any(w in f["title"].lower() for w in words) for f in feed)
        if not dup:
            out.append(b)
    return out


def calendar_good_rows(status, body):
    """(rows, None) for a good feed answer, else (None, error): "http <code>",
    "not json", "not a list", "empty" or "network"."""
    if status is None:
        return None, "network"
    if status != 200:
        return None, "http %d" % status
    try:
        rows = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "not json"
    if not isinstance(rows, list):
        return None, "not a list"
    if not any(isinstance(r, dict) and all(k in r for k in ("title", "country", "date")) for r in rows):
        return None, "empty"
    return rows, None


def calendar_refresh(path, doc, week, now, url):
    """Download this week's feed once into path (part 03 9.3 steps 3 to 6); returns the
    week's file doc. Never raises on a bad answer: it records the error instead."""
    old = doc or {"fetched_at": None, "tried_at": None, "error": None, "last_modified": None,
                  "etag": None, "url": url, "week": week, "ok": False, "events": []}
    head = {}
    if doc and doc.get("etag"):
        head["If-None-Match"] = doc["etag"]
    if doc and doc.get("last_modified"):
        head["If-Modified-Since"] = doc["last_modified"]
    status, body, rh = calendar_get(url, head)
    stamp = utc_iso(now)
    new = dict(old, tried_at=stamp, url=url)
    if status == 304 and doc:
        new.update(fetched_at=stamp, ok=True, error=None)
        save_json_atomic(path, new)
        return new
    rows, error = calendar_good_rows(status, body)
    if rows is None:
        new.update(ok=False, error=error if status != 304 else "http 304")
        save_json_atomic(path, new)
        return new
    times = sorted(t for t in (parse_utc(r.get("date")) for r in rows if isinstance(r, dict)) if t)
    their = calendar_week(times[len(times) // 2]) if times else week
    low = dict((k.lower(), v) for k, v in rh.items())
    good = {"fetched_at": stamp, "tried_at": stamp, "error": None, "last_modified": low.get("last-modified"),
            "etag": low.get("etag"), "url": url, "week": their, "ok": True, "events": rows}
    if their != week:
        save_json_atomic(os.path.join(os.path.dirname(path), their + ".json"), good)
        new.update(ok=False, error="old week")
        save_json_atomic(path, new)
        return new
    save_json_atomic(path, good)
    return good


def calendar_file_doc(path):
    """The saved calendar file, or None when it is missing or broken (check: a broken
    file is read as missing, so the next download replaces it)."""
    try:
        doc = load_json(path, None)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def load_calendar(data_dir, now, cfg, offline):
    """This week's events: the saved feed file (downloaded at most once an hour, never
    when offline) plus the built-in tables up to CALENDAR_AHEAD_DAYS ahead, each with
    "symbols", sorted by time."""
    week = calendar_week(now)
    path = os.path.join(data_dir, "calendar", week + ".json")
    doc = calendar_file_doc(path)
    url = cfg.get("calendar_url", "")
    if not offline and url:
        last = parse_utc((doc or {}).get("tried_at") or (doc or {}).get("fetched_at"))
        if doc is None or last is None or (now - last).total_seconds() >= CALENDAR_MIN_REFRESH_S:
            doc = calendar_refresh(path, doc, week, now, url)
    feed = parse_ff(doc) if (doc and url) else []
    start = ny_to_utc(at_time(datetime.date.fromisoformat(week), "00:00"))
    built = builtin_events(start, now + datetime.timedelta(days=CALENDAR_AHEAD_DAYS), cfg)
    events = feed + merge_builtin(feed, built, bool(doc and doc.get("ok")))
    for e in events:
        e["symbols"] = event_symbols(e, cfg)
    return sorted(events, key=lambda e: e["at"])


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


OUTAGE_MIN_SLOTS = {"5m": 6, "1h": 3, "1d": 1}   # an outage misses this many steps of open time


COARSE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}


COARSE_MIN_STOP_PIPS = 10


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


def coarse_note(inst, stop_points):
    """The warning for a tight stop on a pair with rough Yahoo data (COARSE), else None."""
    if inst.get("key") not in COARSE:
        return None
    pip = _dec_clean(inst.get("pip") or 0.0001)
    point = _dec_clean(inst.get("point") or Decimal(1).scaleb(-int(inst.get("digits", 5))))
    if abs(_dec_clean(stop_points)) * point < COARSE_MIN_STOP_PIPS * pip:
        return ("Yahoo prices for %s are rough (about one price a minute). A stop under %d pips "
                "can be hit without Yahoo showing it." % (inst["key"], COARSE_MIN_STOP_PIPS))
    return None


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


def _daily_closes(data_dir, inst, now, cfg, offline):
    """D: {New York date: Yahoo 1d close} from the continuous 1d cache (refreshed when
    online; a failed refresh keeps the cache)."""
    try:
        doc = refresh(data_dir, inst["yahoo"], "1d", now, cfg, offline)
    except ValueError:
        doc = load_cache(data_dir, inst["yahoo"], "1d")
    out = {}
    for b in clean_rows(doc["bars"], {}, "1d", inst):
        out[to_ny(b["start"]).date()] = b["close"]
    return out


SKIP_FLAGS = ("roll", "outage", "partial", "holiday", "forming")


ATR_WINDOW = {"1d": 250, "1h": 120, "5m": 288}


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


SWING_VALID_DAYS = 3      # FX days a swing hit waits for its close


OR_MAX_DAILY_ATR = 0.6    # an opening range wider than this x the daily ATR is skipped


OR_MIN_SPREADS = 8        # an opening range narrower than this x the spread is skipped


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


def opening_range(bars_5m, session, fx_date, minutes, inst):
    """The opening range of a session on fx_date (05 3.1): high, low, mid (to the tick of
    inst, half up) and how many of the expected 5m bars were there (roll bars left out).
    None when no range bar."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes % 5:
        raise ValueError("The opening range must be a whole number of 5-minute bars "
                         "(got %s minutes)." % (minutes,))
    start, end = opening_range_window(session, fx_date, minutes)
    rng = [b for b in bars_5m if start <= b["start"] < end
           and "roll" not in (b.get("flags") or ())]
    if not rng:
        return None   # no range bar at all (a range of roll bars only: None too, check)
    high = max(b["high"] for b in rng)
    low = min(b["low"] for b in rng)
    mid = round_price((high + low) / 2.0, inst)
    return {"session": session, "start": utc_iso(start), "end": utc_iso(end),
            "high": high, "low": low, "mid": mid, "bars": len(rng), "of": minutes // 5}


def or_trigger(bars_5m, or_, side, after_utc, until_utc):
    """The first finished 5m bar after the range (and after after_utc) whose close is
    beyond it: buy above the high, sell below the low; done by until_utc. Or None."""
    if or_["bars"] < or_["of"]:
        raise ValueError("The opening range is not complete.")
    if side not in ("buy", "sell"):
        raise ValueError('A side is "buy" or "sell", not "%s".' % (side,))
    frm = parse_utc(or_["end"])
    if after_utc is not None and after_utc > frm:
        frm = after_utc
    step = datetime.timedelta(minutes=5)
    for b in sorted(bars_5m, key=lambda x: x["start"]):
        if b["start"] < frm or b["start"] + step > until_utc:
            continue
        if "roll" in (b.get("flags") or ()) or b.get("after_gap"):   # every after_gap: the safe side
            continue
        if side == "buy" and b["close"] > or_["high"]:
            return b
        if side == "sell" and b["close"] < or_["low"]:
            return b
    return None


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


# ---- end of the copied block. fx-entry's own code follows.


# ---- copied from fx-scanner/scan.py (same code as scan.py; tests/test_entry.py checks it)

RECENT_FX_DAYS = 5        # roll notes and outage lines look this many FX days back


def back_fx_day(fx_date, n, cfg):
    """The FX day n FX days before fx_date (weekends and full FX holidays skipped)."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    for _ in range(n):
        d -= datetime.timedelta(days=1)
        while d.weekday() >= 5 or full_fx_holiday(d, cfg):
            d -= datetime.timedelta(days=1)
    return d.isoformat()


def recent_start(fx_date, cfg):
    """The start (UTC) of the window "the last 5 FX days" that ends with fx_date."""
    day_end = cfg.get("day_end", "17:00 New York")
    return fx_day_bounds(back_fx_day(fx_date, RECENT_FX_DAYS - 1, cfg), day_end)[0]


def contract_month(symbol, root):
    """"GCQ26.CMX" -> "Aug" (by MONTH_CODES), or None."""
    letter = str(symbol or "")[len(root):len(root) + 1]
    for month, code in MONTH_CODES.items():
        if code == letter:
            return month
    return None


def roll_window_date(w):
    """The first FX day of an expected roll window (its start is 17:00 New York the day
    before), as "YYYY-MM-DD"."""
    n = to_ny(w[2])
    d = n.date() + datetime.timedelta(days=1 if n.hour >= 17 else 0)
    return d.isoformat()


def fx_day_of(utc, day_end):
    """The FX day of a moment; a weekend moment gives its New York date."""
    return fx_day(utc, day_end) or to_ny(utc).date().isoformat()


def rolls_known(rolls, now):
    """The rolls already found at now: a replay sees a roll only from its found_at on."""
    out = []
    for r in rolls:
        found = parse_utc(r.get("found_at"))
        if found is None or found <= now:
            out.append(r)
    return out


def roll_notes(inst, rolls, now, fx_day, cfg, data_dir):
    """The roll lines of a futures hit (section 4.5): a roll ahead in the next 5 FX days,
    a roll in the last 5 FX days, a roll that the history search missed."""
    root = inst_root(inst)
    if not root or not inst.get("futures"):
        return []
    day_end = cfg.get("day_end", "17:00 New York")
    name = inst.get("name") or inst["key"]
    word = ROLL_WORDS.get(root, name.lower())
    mine = [r for r in rolls if r.get("root") == root]

    def roll_in(a, b):
        return any(a <= (parse_utc(r.get("first_bar_new")) or a - datetime.timedelta(1)) <= b
                   for r in mine)

    out = []
    horizon = fx_day_bounds(add_fx_days(fx_day, RECENT_FX_DAYS, cfg), day_end)[1]
    for w in expected_rolls(root, inst, now, horizon):
        if w[2] < horizon and w[3] > now and not roll_in(w[2], w[3]):
            old_m, new_m = contract_month(w[0], root), contract_month(w[1], root)
            if w[2] > now:
                when = roll_window_date(w)
            else:   # the window is open now (check: the spec shows only a date)
                when = "now, by %s at the latest" % to_ny(w[3]).date().isoformat()
            out.append("%s rolls about %s (%s to %s): Yahoo prices jump then."
                       % (name, when, old_m, new_m))
            break
    since = recent_start(fx_day, cfg)
    for r in mine:
        first = parse_utc(r.get("first_bar_new"))
        if first is not None and since <= first <= now and r.get("ratio"):
            pct = (float(r["ratio"]) - 1.0) * 100.0
            out.append("%s rolled on Yahoo on %s (%+.2f%%). Older prices are adjusted to match."
                       % (name, day_label(parse_fx_date(fx_day_of(first, day_end), weekend_ok=True)), pct))
    if (inst["futures"] or {}).get("history_check"):
        closes = _daily_closes(data_dir, inst, now, cfg, True)
        today = to_ny(now).date()
        for w in expected_rolls(root, inst, since, now):
            if w[3] <= now and w[3] >= since and w[4] <= today and w[4] in closes \
                    and not roll_in(w[2], w[3]):
                out.append("Could not find the %s roll of about %s in Yahoo data. ATR and "
                           "setups near that date may be off." % (word, w[4].isoformat()))
    return out

TOOL = "entry.py"
SKILL = "fx-entry"
HIT_FIELDS = ["level", "last", "opening_range.high", "opening_range.low", "opening_range.mid"]
STATUS_ORDER = ("triggered", "waiting", "expired", "late", "closed")
SIDE_WORDS = {"buy": "buy side (waits for a close above)",
              "sell": "sell side (waits for a close below)"}
MAX_FAILED_LOOKS = 5
WAKE_GRACE_S = 20        # a day watch looks again this long after a 5m bar should end
WRAP = 78              # the line width of fx-scanner and fx-entry


def check_rolls(rolls, path):
    """The 01 5.2 check of rolls.json: a ratio must be a number from 0.33 to 3."""
    for r in rolls:
        x = r.get("ratio")
        good = isinstance(x, (int, float)) and not isinstance(x, bool) and 0.33 <= x <= 3
        if not good:
            raise ValueError("%s: roll %s has a ratio that cannot be right (%s). "
                             "Fix or delete that roll." % (path, r.get("id"), x))
    return rolls


def pick_key(text, cfg):
    """SYMBOL typed by the user -> an instrument key (capitals). Unknown: the 06 2.1
    message with the nearest key."""
    key = str(text or "").strip().upper()
    keys = list(cfg.get("instruments") or {})
    if key in keys:
        return key
    near = difflib.get_close_matches(key, keys, n=1, cutoff=0.6)
    msg = "%s is not in your instruments." % key
    if near:
        msg += " Did you mean %s?" % near[0]
    raise ValueError(msg)


def setup_row(name):
    """The SETUPS row of a setup name, or None ("manual" has none)."""
    for s in SETUPS:
        if s["name"] == name:
            return s
    return None


def setup_words(name):
    """The words of a setup for people. A day setup drops the bracket: the side words
    already say "a close above" or "a close below" (check)."""
    if name == "manual":
        return "a close beyond your level"
    s = setup_row(name)
    if s is None:
        return str(name)
    if s["style"] == "day":
        return s["words"].split(" (")[0]
    return s["words"]


def first_lower(text):
    """The text with its first letter in lower case."""
    return text[:1].lower() + text[1:] if text else text


def dist_words(distance, inst):
    """A distance in short words: "2.0 pips" for FX, "68.40" for gold (pips_text
    without the points part)."""
    return pips_text(distance, inst).split(" (")[0]


def round_dist(x, inst):
    """A price distance rounded to the instrument's digits (may be 0 or below 0)."""
    q = Decimal(1).scaleb(-int(inst["digits"]))
    return float(_dec_clean(x).quantize(q, rounding=ROUND_HALF_UP))


def round_atr(atr, inst):
    """An ATR rounded to digits + 1, or None."""
    if atr is None:
        return None
    return round(atr, int(inst["digits"]) + 1)


def pkt_clock(utc):
    """naive UTC -> "12:40 PM PKT" (no day name)."""
    return "%s PKT" % clock_label(to_pkt(utc))


def wrap_line(text, width=WRAP):
    """One long line -> several lines of at most width characters (words kept)."""
    words, out, cur = text.split(" "), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = w if not cur else cur + " " + w
    if cur:
        out.append(cur)
    return out


def bar_doc(bar, interval):
    """A bar for entry.json: {start, interval, open, high, low, close}, or None."""
    if bar is None:
        return None
    return {"start": utc_iso(bar["start"]), "interval": interval, "open": bar["open"],
            "high": bar["high"], "low": bar["low"], "close": bar["close"]}


def read_watchlist(path, cfg, rolls, required=True):
    """fx/watchlist.json with the 01 1.3 and 5.2 checks, or None when it is missing and
    not required. A hit with no rolls_applied (an older file) gets every roll of its
    root found at or before the file's created_at (core gap, fx-scanner 5)."""
    doc = load_doc(path, "watchlist", "fx-scanner", required=required)
    if doc is None:
        return None
    if doc.get("mode") not in ("swing", "day"):
        raise ValueError('%s: "mode" must be "swing" or "day"' % path)
    hits = check_objects(doc, "hits", path)
    made = parse_utc(doc.get("created_at"))
    for n, hit in enumerate(hits, 1):
        check_symbol(hit.get("symbol"), cfg, path)
        check_side(hit.get("side"), path)
        check_style(hit.get("style", doc["mode"]), path)
        hit.setdefault("style", doc["mode"])
        lvl = hit.get("level")
        if isinstance(lvl, bool) or not isinstance(lvl, (int, float)) or lvl <= 0:
            raise ValueError("%s: hit %d (%s) has no level. Run fx-scanner again."
                             % (path, n, hit.get("symbol")))
        if not isinstance(hit.get("rolls_applied"), dict):
            root = inst_root(instrument(cfg, hit["symbol"]))
            hit["rolls_applied"] = dict(
                (r["id"], r["ratio"]) for r in rolls if root and r.get("root") == root
                and (made is None or parse_utc(r.get("found_at")) is None
                     or parse_utc(r.get("found_at")) <= made))
        hit.setdefault("fx_day", doc.get("fx_day"))
        hit.setdefault("rank", n)
        hit["_n"] = n
    return doc


def known_rolls(rolls, inst):
    """{roll id: ratio} of every roll of the instrument's root ({} for spot FX)."""
    root = inst_root(inst)
    return dict((r["id"], r["ratio"]) for r in rolls if root and r.get("root") == root)


def rebase_hit(hit, rolls, inst):
    """A swing hit moved by the rolls it has not had (02 5.8). Returns (hit, note)."""
    new, ids = rebase(hit, rolls, HIT_FIELDS, inst=inst)
    if not ids:
        return new, None
    mult = 1.0
    for r in rolls:
        if r["id"] in ids:
            done = (hit.get("rolls_applied") or {}).get(r["id"])
            mult *= float(r["ratio"]) / (float(done) if done else 1.0)
    note = ("%s rolled on Yahoo after the scan (%+.2f%%). The level moved from %s to %s "
            "to match." % (inst.get("name") or inst["key"], (mult - 1) * 100,
                           price_text(hit["level"], inst), price_text(new["level"], inst)))
    return new, note


def side_rules(args, cfg):
    """--side as typed, checked against allow_short (06 2.1): "buy", "sell" or "both"."""
    side = getattr(args, "side", None)
    if not cfg["allow_short"]:
        if side in ("sell", "both"):
            raise ValueError("Sells are off in your config (allow_short is false).")
        return "buy"
    return side or "both"


def hand_target(args, cfg, key, inst, now, known):
    """The target of --level P (a hand level, setup "manual"), after its checks."""
    p = args.level
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not p > 0 or p != p:
        raise ValueError("--level must be a price above 0, not %s." % args.level_text)
    row = setup_row(args.setup) if args.setup else None
    if args.style == "day" or (row is not None and row["style"] == "day"):
        raise ValueError("--level is for swing setups. A day setup uses the opening range.")
    if args.side_typed not in ("buy", "sell"):
        raise ValueError("--level needs --side buy or --side sell.")
    return {"symbol": key, "name": inst.get("name") or key, "side": args.side_typed,
            "style": "swing", "setup": "manual", "level": round_price(p, inst),
            "fx_day": last_done_fx_day(inst, now, cfg), "rank": None,
            "opening_range": None, "rolls_applied": dict(known)}


def pick_target(args, cfg, key, inst, watchlist, now, known):
    """4.1 step 4: (style, targets, notes). The first match wins: --level, the
    watchlist hits for key, a day target with no hit, else an error."""
    if args.level is not None:
        return "swing", [hand_target(args, cfg, key, inst, now, known)], []
    notes = []
    side = side_rules(args, cfg)
    hits = [h for h in ((watchlist or {}).get("hits") or []) if h["symbol"] == key]
    if args.setup:
        hits = [h for h in hits if h.get("setup") == args.setup]
    if args.side_typed in ("buy", "sell"):
        hits = [h for h in hits if h["side"] == args.side_typed]
    if args.style:
        hits = [h for h in hits if h["style"] == args.style]
    if hits and not cfg["allow_short"]:
        kept = [h for h in hits if h["side"] != "sell"]
        if len(kept) < len(hits):
            notes.append("Sells are off (allow_short is false).")
        if not kept:
            raise ValueError("Sells are off in your config (allow_short is false).")
        hits = kept
    if hits:
        hits = sorted(hits, key=lambda h: (h.get("rank") is None, h.get("rank") or 0))
        style = hits[0]["style"]
        if style == "day":
            today = fx_day(now, cfg["day_end"])
            if watchlist.get("fx_day") and watchlist.get("fx_day") != today:
                notes.append("The watchlist is from %s. Using today's opening range."
                             % day_label(parse_fx_date(watchlist["fx_day"], weekend_ok=True)))
        return style, [dict(h) for h in hits], notes
    style = args.style or cfg["style"]
    if style == "day":
        notes.append("%s is not on the watchlist. Using today's opening range." % key)
        return "day", [{"symbol": key, "name": inst.get("name") or key,
                        "side": side if side in ("buy", "sell") else None, "style": "day",
                        "setup": None, "level": None, "fx_day": None, "rank": None,
                        "opening_range": None, "rolls_applied": dict(known)}], notes
    if watchlist is None:
        raise ValueError("no watchlist at %s. Run fx-scanner first. Or give --level PRICE "
                         "--side buy|sell." % args.watchlist_path)
    raise ValueError("%s is not on %s. Run fx-scanner first, or give --level PRICE "
                     "--side buy|sell." % (key, args.watchlist_path))


def beyond(close, level, side):
    """True when a close is beyond the level: buy above, sell below. Equal is not."""
    return close > level if side == "buy" else close < level


def first_close_beyond(days, level, side, from_day):
    """The index of the first daily bar with fx_day >= from_day whose close is beyond
    the level, or None."""
    for i, d in enumerate(days):
        if (from_day is None or d["fx_day"] >= from_day) and beyond(d["close"], level, side):
            return i
    return None


def new_result(target, style):
    """An empty result dict for one target."""
    return {"symbol": target["symbol"], "name": target.get("name") or target["symbol"],
            "side": target.get("side"), "style": style, "setup": target.get("setup"),
            "rank": target.get("rank"), "level": target.get("level"), "status": None,
            "note": "", "head": "", "line2": None, "bar": None, "price": None,
            "triggered_at": None, "distance": None, "atr": None,
            "atr_interval": "1d" if style == "swing" else "5m", "fx_day": None,
            "opening_range": None, "rolls_applied": dict(target.get("rolls_applied") or {}),
            "notes": [], "_bar": None, "_trig": None, "_distance_atr": None}


def swing_status(days, target, done, inst, cfg, partner=None):
    """4.2 steps 4 to 8 on usable daily bars: the status of one swing target. Pure: no
    files and no clock (done is last_done_fx_day). partner is the other inside-day hit."""
    res = new_result(target, "swing")
    name = res["name"]
    if not days:
        res.update(status="closed", note="No usable daily bars for %s." % name)
        res["head"] = "CLOSED: " + res["note"]
        return res
    side, level = target["side"], target["level"]
    word = "above" if side == "buy" else "below"
    done = done or days[-1]["fx_day"]
    i = first_close_beyond(days, level, side, target.get("fx_day"))
    last = days[-1]
    bar = days[i] if i is not None else last
    upto = days[:(i if i is not None else len(days) - 1) + 1]
    atr = atr_wilder(upto[-ATR_WINDOW["1d"]:], 14)
    res["atr"] = round_atr(atr, inst)
    res["_bar"] = bar
    res["bar"] = bar_doc(bar, "1d")
    res["price"] = bar["close"]
    res["fx_day"] = bar["fx_day"]
    other = None
    if partner is not None:
        other = first_close_beyond(days, partner["level"], partner["side"], partner.get("fx_day"))
    when = lambda k: day_label(parse_fx_date(days[k]["fx_day"]))
    short = lambda d: DAY_NAMES[parse_fx_date(d).weekday()]
    if other is not None and (i is None or days[other]["fx_day"] < days[i]["fx_day"]):
        res.update(status="expired", note="The other side triggered first.")
    elif i is not None and bar["fx_day"] >= done:
        res["_trig"] = bar
        res["triggered_at"] = utc_iso(bar["end"])
        res.update(status="triggered", note="The daily bar of %s closed at %s, %s %s." % (
            when(i), price_text(bar["close"], inst), word, price_text(level, inst)))
    elif i is not None:
        after = days[i + 1]["fx_day"] if i + 1 < len(days) else done
        res.update(status="expired", note=(
            "The close %s %s came on %s. %s has closed since. A late entry is a different "
            "trade. Scan again." % (word, price_text(level, inst), short(bar["fx_day"]),
                                    short(after))))
    elif target.get("fx_day") and fx_days_between(target["fx_day"], done, cfg) >= SWING_VALID_DAYS:
        res.update(status="expired", note="No daily close %s %s in %d days. This setup expired."
                   % (word, price_text(level, inst), SWING_VALID_DAYS))
    else:
        gap = level - last["close"] if side == "buy" else last["close"] - level
        res["distance"] = round_dist(gap, inst)
        res["_distance_atr"] = round(gap / atr, 2) if atr else None
        atr_part = " (%.2f ATR)" % res["_distance_atr"] if atr else ""
        res.update(status="waiting", note="Last close %s, %s %s %s%s." % (
            price_text(last["close"], inst), dist_words(gap, inst),
            "under" if side == "buy" else "over", price_text(level, inst), atr_part))
    res["head"] = "%s: %s" % (res["status"].upper(), first_lower(res["note"])
                              if res["status"] in ("triggered", "waiting") else res["note"])
    return res


def day_closed(inst, cfg, now, fx_date):
    """4.3 steps 1 to 3: the note of a market that is closed for a day check, or None
    when it is open (or open later today). Returns (note, day_over)."""
    name = inst.get("name") or inst["key"]
    ms = market_state(inst, now, cfg)
    if fx_date is None:
        when = (" Opens %s." % pkt_label(ms["next_open"], date=True)) if ms["next_open"] else ""
        return "Closed for the weekend." + when, True
    if ms["state"] in ("weekend", "holiday", "closed", "break"):
        nxt = ms["next_open"]
        if ms["state"] == "break" and hours_template(inst["hours"])["group"] == "cme":
            note = "%s is in the CME daily break" % first_lower(name)
            note += (" until %s." % pkt_clock(nxt)) if nxt else "."
        else:
            note = ms["note"] or "%s is closed now." % name
            if nxt:
                note += " Opens %s." % pkt_label(nxt, date=True)
        over = ms["state"] in ("weekend", "holiday") or nxt is None \
            or fx_day(nxt, cfg["day_end"]) != fx_date
        return note, over
    if day_close_utc(inst, fx_date, cfg) is None:
        return "%s is closed all day today." % name, True
    return None, False


def or_words(or_, inst):
    """The opening range in one line: "London range Thu 12:00 PM PKT (3:00 AM New York)
    to 12:15 PM PKT: high 1.08640, low 1.08480, middle 1.08560 (3 of 3 bars)."."""
    a, b = parse_utc(or_["start"]), parse_utc(or_["end"])
    return ("%s range %s to %s: high %s, low %s, middle %s (%d of %d bars)." % (
        or_["session"].replace("_", " ").title().replace("New York", "New York"),
        both_times(a), pkt_clock(b), price_text(or_["high"], inst), price_text(or_["low"], inst),
        price_text(or_["mid"], inst), or_["bars"], or_["of"]))


def day_filter(or_, daily_atr, inst):
    """05 3.3: the "Skipped today." text of a range too wide or too small, else None."""
    width = or_["high"] - or_["low"]
    if daily_atr and width > OR_MAX_DAILY_ATR * daily_atr:
        return ("Opening range %s is wider than %s daily ATR (%s). Skipped today." % (
            dist_words(width, inst), OR_MAX_DAILY_ATR, dist_words(OR_MAX_DAILY_ATR * daily_atr, inst)))
    spread = spread_price(inst)
    if width < OR_MIN_SPREADS * spread:
        return ("Opening range %s is too small next to the spread (%s). Skipped today." % (
            dist_words(width, inst), dist_words(spread, inst)))
    return None


def pick_day_side(side_wish, trig, newest, or_, atr5):
    """4.3 step 13: the side to report. The wish, else the side that triggered first,
    else the side whose last close is nearer its level in ATRs (a tie: buy)."""
    if side_wish in trig:
        return side_wish
    fired = [(t["start"], s) for s, t in trig.items() if t is not None]
    if fired:
        return min(fired)[1]
    if len(trig) == 1:
        return list(trig)[0]
    up = or_["high"] - newest["close"]
    down = newest["close"] - or_["low"]
    return "buy" if up <= down else "sell"


def day_status(b5, daily_atr, inst, cfg, now, fx_date, session, side_wish, sides):
    """4.3 steps 7 to 14 on usable 5m bars done by now: the status of a day check.
    Pure: no files and no clock. sides = the sides allow_short allows (both are watched)."""
    res = new_result({"symbol": inst["key"], "name": inst.get("name"), "side": side_wish}, "day")
    minutes = cfg["day"]["or_minutes"]
    or_start, or_end = opening_range_window(session, fx_date, minutes)
    cutoff = entry_cutoff(inst, fx_date, now, cfg)
    d0, d1 = fx_day_bounds(fx_date, cfg["day_end"])
    step = datetime.timedelta(minutes=5)
    today = [b for b in b5 if d0 <= b["start"] < d1]
    newest = today[-1] if today else None
    res["fx_day"] = fx_date
    res["_newest"] = newest
    res["_today"] = today
    res["_cutoff"] = cutoff
    res["_or_end"] = or_end
    if newest is not None:
        upto = [b for b in b5 if b["start"] <= newest["start"]]
        res["atr"] = round_atr(atr_wilder(upto[-ATR_WINDOW["5m"]:], 14), inst)
    word = session.replace("_", " ").title()
    or_ = None
    trig = {}
    status, note = None, None
    if now < or_start:
        status, note = "waiting", "The %s range starts %s." % (word, both_times(or_start))
    elif now < or_end:
        got = len([b for b in b5 if or_start <= b["start"] < or_end])
        status, note = "waiting", "The range is forming (%d of %d bars so far)." % (got, minutes // 5)
    elif market_state(inst, now, cfg, which="data_hours")["state"] == "holiday":
        cme = hours_template(inst["data_hours"])["group"] == "cme"
        status = "closed"
        note = "No Yahoo futures prices on US holidays." if cme else (
            market_state(inst, now, cfg, which="data_hours")["note"] or "No Yahoo prices today.")
    else:
        or_ = opening_range(b5, session, fx_date, minutes, inst)
        if newest is None or newest["start"] + step < or_end:
            status = "waiting"
            note = ("Yahoo data is behind: the newest bar ends %s. The range is not complete "
                    "yet." % pkt_clock(newest["start"] + step) if newest else
                    "Yahoo has no prices for today yet. The range is not complete yet.")
            or_ = None
        elif or_ is None or or_["bars"] < or_["of"]:
            miss = (minutes // 5) - (or_["bars"] if or_ else 0)
            status = "closed"
            note = "The opening range is missing %d of %d bars. No trigger today." % (miss, minutes // 5)
            or_ = None
    if or_ is not None:
        res["opening_range"] = or_
        for s in sides:
            trig[s] = or_trigger(b5, or_, s, or_end, min(now, cutoff))
        side = pick_day_side(side_wish, trig, newest, or_, res["atr"])
        res["side"] = side
        res["setup"] = "or_breakout" if side == "buy" else "or_breakdown"
        res["level"] = or_["high"] if side == "buy" else or_["low"]
        status, note = day_after_range(res, or_, trig, side, newest, daily_atr, inst)
    res.update(status=status, note=note)
    if now >= cutoff:
        day_late(res, or_, trig, inst, cfg, fx_date, cutoff)
    day_fields(res, trig, inst)
    return res


def day_after_range(res, or_, trig, side, newest, daily_atr, inst):
    """4.3 steps 11 to 13 after a complete range: (status, note)."""
    skip = day_filter(or_, daily_atr, inst)
    if skip:
        return "expired", skip
    step = datetime.timedelta(minutes=5)
    mine = trig.get(side)
    others = [(t["start"], s) for s, t in trig.items() if t is not None and s != side]
    if mine is not None and not (others and min(others)[0] < mine["start"]):
        res["_trig"] = mine
        where = "above the range high" if side == "buy" else "below the range low"
        return "triggered", "The %s bar closed %s %s." % (
            clock_label(to_pkt(mine["start"])), where, price_text(res["level"], inst))
    if others:
        t0, s = min(others)
        t = trig[s]
        lvl = or_["high"] if s == "buy" else or_["low"]
        return "expired", "The other side triggered first: a close %s %s at %s." % (
            "above" if s == "buy" else "below", price_text(lvl, inst), pkt_clock(t["start"] + step))
    gap = or_["high"] - newest["close"] if side == "buy" else newest["close"] - or_["low"]
    res["distance"] = round_dist(gap, inst)
    res["_distance_atr"] = round(gap / res["atr"], 2) if res["atr"] else None
    where = "below the range high" if side == "buy" else "above the range low"
    return "waiting", "Last close %s, %s %s %s." % (
        price_text(newest["close"], inst), dist_words(gap, inst), where, price_text(res["level"], inst))


def day_late(res, or_, trig, inst, cfg, fx_date, cutoff):
    """4.3 step 7: past the entry cut-off the status is "late" (it wins over expired).
    The second line says what the range did."""
    plain = pkt_time_on_fx_day(cfg["day"]["no_entries_after"], fx_date, cfg["day_end"])
    early = " (the market stops early today)" if cutoff < plain else ""
    first = "It is past %s%s. No new day trades now." % (pkt_clock(cutoff), early)
    step = datetime.timedelta(minutes=5)
    second = res["note"]
    side = res.get("side")
    if or_ is not None and side in ("buy", "sell"):
        lvl = or_["high"] if side == "buy" else or_["low"]
        word = "above" if side == "buy" else "below"
        t = trig.get(side)
        if t is not None:
            second = "The close %s %s came at %s." % (word, price_text(lvl, inst), pkt_clock(t["start"] + step))
        else:
            second = "No close %s %s came by %s." % (word, price_text(lvl, inst), pkt_clock(cutoff))
    res["_trig"] = None
    res["distance"] = None
    res.update(status="late", note=first, line2=second)


def day_fields(res, trig, inst):
    """4.3 step 14: bar, price, triggered_at and the status line."""
    step = datetime.timedelta(minutes=5)
    t = res.get("_trig")
    bar = t if t is not None else res.get("_newest")
    res["_bar"] = bar
    res["bar"] = bar_doc(bar, "5m")
    res["price"] = bar["close"] if bar else None
    if res["status"] != "waiting" or res["opening_range"] is None:
        res["distance"] = None
    if res["status"] == "triggered":
        res["triggered_at"] = utc_iso(t["start"] + step)
        res["head"] = "TRIGGERED %s at %s: %s" % (pkt_clock(t["start"] + step),
                                                  price_text(t["close"], inst), first_lower(res["note"]))
        later = [b for b in res["_today"] if b["start"] > t["start"]]
        if later:
            n = len(later)
            res["after_head"] = ("The close came %d %s ago, at %s. A late entry is a different "
                                "trade." % (n, "bar" if n == 1 else "bars", pkt_clock(t["start"] + step)))
    else:
        text = res["note"]
        if text.startswith("Last close") or text.startswith("It is past"):
            text = first_lower(text)
        res["head"] = "%s  %s" % (res["status"].upper(), text)


def news_notes(res, inst, cfg, now, events):
    """The quiet-now note, else the news-ahead note (4.4), as a list of 0 or 1 line."""
    key = inst["key"]
    news = cfg["news"]
    ev = in_quiet_window(events, key, now, cfg)
    if ev is not None:
        a = ev["at"] - datetime.timedelta(minutes=news["quiet_before_min"])
        b = (ev.get("end") or ev["at"]) + datetime.timedelta(minutes=news["quiet_after_min"])
        return ["Quiet time for news now: %s at %s (%s), %s impact. fx-risk blocks new trades "
                "from %s to %s." % (event_label(ev), pkt_clock(ev["at"]), ny_label(ev["at"]),
                                     event_impact(ev, key), clock_label(to_pkt(a)), pkt_clock(b))]
    if res["style"] == "day":
        if not res.get("fx_day"):
            return []
        limit = pkt_time_on_fx_day(cfg["day"]["flat_by"], res["fx_day"], cfg["day_end"])
        when = "before the flat-by time"
    else:
        limit = now + datetime.timedelta(hours=24)
        when = "in the next 24 hours"
    evs = next_events(events, [key], now, 1, news["impacts"])
    if evs and evs[0]["at"] < limit:
        ev = evs[0]
        return ["%s at %s, %s impact, %s. Spreads widen and stops can slip." % (
            event_label(ev), both_times(ev["at"]), event_impact(ev, key), when)]
    return []


def check_notes(res, inst, cfg, now, events, rolls):
    """The 4.4 notes, in order: news, roll, rebased level, coarse data, futures delay,
    offline. They never change the status."""
    out = news_notes(res, inst, cfg, now, events)
    fx_day_ = res.get("fx_day") or last_fx_day(now, cfg)
    out += roll_notes(inst, rolls, now, fx_day_, cfg, res.get("_data_dir"))
    if res.get("_rebase_note"):
        out.append(res["_rebase_note"])
    or_ = res.get("opening_range")
    if res["style"] == "day" and res["status"] == "triggered" and or_:
        far = abs(res["price"] - or_["mid"]) + spread_price(inst)
        coarse = coarse_note(inst, to_points(far, inst))
        if coarse:
            out.append(coarse)
    if inst.get("price_source") == "futures":
        out.append("Yahoo futures prices are about 10 minutes late.")
    if res.get("_offline_note"):
        out.append("Offline: saved prices only.")
    return out


class EntryRun(object):
    """What one run shares between its checks: the clock, config, paths, rolls,
    events, the keys already refreshed and the watchlist."""

    def __init__(self, args, now):
        self.args = args
        self.now = now
        typed = getattr(args, "config", None)
        cpath = typed or os.path.join("fx", "config.json")
        self.cfg = load_config(cpath, required=typed is not None)
        self.paths = fx_paths(os.path.dirname(cpath) or ".", getattr(args, "data", None), cpath)
        self.data = self.paths["data"]
        self.wpath = getattr(args, "watchlist", None) or self.paths["watchlist"]
        self.offline = bool(args.offline or args.now is not None or not self.cfg["yahoo"])
        self.offline_cal = bool(args.offline or args.now is not None)
        self.refreshed = set()
        self.events = None
        self.load_rolls()

    def load_rolls(self):
        rolls = check_rolls(load_rolls(self.data), self.paths["rolls"])
        self.rolls = rolls_known(rolls, self.now)

    def calendar(self):
        if self.events is None:
            self.events = load_calendar(self.data, self.now, self.cfg, self.offline_cal)
        return self.events

    def refresh_key(self, inst, intervals):
        """One paced refresh of the key's caches (unless offline, or done already in
        this run), then the roll check for futures. Returns (notes, error text)."""
        key = inst["key"]
        if self.offline or key in self.refreshed:
            return [], None
        self.refreshed.add(key)
        notes = []
        try:
            for interval in intervals:
                refresh(self.data, inst["yahoo"], interval, self.now, self.cfg, False)
            if inst.get("futures"):
                rec = check_roll_forward(self.data, inst, self.cfg, self.now, False)
                if rec is not None:
                    self.load_rolls()
                    notes.append(roll_found_words(rec, inst))
        except ValueError as exc:
            return ["Yahoo did not answer just now. Using the saved prices."], str(exc)
        return notes, None


def roll_found_words(rec, inst):
    """The 02 5.5 lines for a roll check_roll_forward just found."""
    name = inst.get("name") or inst["key"]
    at = parse_utc(rec.get("first_bar_new"))
    when = (" at %s" % pkt_clock(at)) if at else ""
    return ("%s futures on Yahoo moved from the %s to the %s contract%s. Yahoo %s jumped %+.2f%% "
            "at that moment. That jump is not a real move. Older prices are adjusted by the same "
            "amount, so charts and stops still line up." % (
                name, rec.get("old"), rec.get("new"), when, first_lower(name),
                (float(rec["ratio"]) - 1) * 100))


def need_rows(run, inst, interval, error):
    """Refuse a check whose cache file has no rows at all (section 7)."""
    if load_cache(run.data, inst["yahoo"], interval)["bars"]:
        return
    if error:
        raise ValueError(error)
    what = "hourly" if interval == "1h" else "5-minute"
    raise ValueError("No %s prices for %s yet (%s). Run without --offline once to get them."
                     % (what, inst["key"], cache_file(run.data, inst["yahoo"], interval)))


def find_partner(target, watchlist, rolls, inst):
    """The other inside-day hit of a pair (same symbol and fx_day), rebased, or None."""
    pair = {"inside_break_up": "inside_break_down", "inside_break_down": "inside_break_up"}
    want = pair.get(target.get("setup"))
    if not want:
        return None
    for h in (watchlist or {}).get("hits") or []:
        if h["symbol"] == target["symbol"] and h.get("setup") == want and h.get("fx_day") == target.get("fx_day"):
            return rebase_hit(h, rolls, inst)[0]
    return None


def check_swing(run, inst, target, notes, error):
    """4.2 for one swing target: the result with its notes."""
    cfg, now = run.cfg, run.now
    need_rows(run, inst, "1h", error)
    bars = load_bars(run.data, inst["yahoo"], "1h", now, rolls=run.rolls, inst=inst, cfg=cfg)
    days_all = daily_from_hourly(bars, cfg["day_end"], inst, cfg, now=now)
    days = usable_bars(days_all)
    done = last_done_fx_day(inst, now, cfg)
    partner = find_partner(target, run.watchlist, run.rolls, inst)
    res = swing_status(days, target, done, inst, cfg, partner)
    name = inst.get("name") or inst["key"]
    flagged = [d for d in days_all if d["fx_day"] == done and d["flags"]]
    if done and days and done not in [d["fx_day"] for d in days] and flagged:
        bad = [f for f in SKIP_FLAGS if f in flagged[0]["flags"]] or flagged[0]["flags"]
        notes.append("The newest %s day (%s) is not usable on Yahoo (%s)." % (
            name, day_label(parse_fx_date(done)), bad[0]))
    ms = market_state(inst, now, cfg)
    if ms["state"] != "open" and ms["next_open"]:
        notes.append("The market is closed now. It opens %s." % pkt_label(ms["next_open"], date=True))
    d = fx_day(now, cfg["day_end"]) or next_fx_day(last_fx_day(now, cfg), cfg)
    res["_next_end"] = fx_day_bounds(d, cfg["day_end"])[1]
    res["_market"] = ms
    return res


def day_session(target, run):
    """The session of a day check: a day hit's own range session, else the config's."""
    own = (target.get("opening_range") or {}).get("session")
    if own is None and (run.watchlist or {}).get("mode") == "day" and target.get("setup"):
        own = run.watchlist.get("session")
    return own or run.cfg["day"]["or_session"]


def check_day(run, inst, target, notes, error):
    """4.3 for one day target: the result with its notes."""
    cfg, now = run.cfg, run.now
    fx_date = fx_day(now, cfg["day_end"])
    session = day_session(target, run)
    if session != cfg["day"]["or_session"]:
        notes.append("The scanner used the %s range." % session.replace("_", " "))
    shut, over = day_closed(inst, cfg, now, fx_date)
    wish = run.args.side_typed if run.args.side_typed in ("buy", "sell") else target.get("side")
    if shut is not None:
        res = new_result(dict(target, side=wish), "day")
        res.update(status="closed", note=shut, head="CLOSED  " + shut, fx_day=fx_date)
        res["_day_over"] = over
        res["_market"] = market_state(inst, now, cfg)
        res["_today"] = []
        return res
    need_rows(run, inst, "5m", error)
    raw = load_bars(run.data, inst["yahoo"], "5m", now, rolls=run.rolls, inst=inst, cfg=cfg)
    b5 = usable_bars(raw, find_outages(raw, inst, "5m", cfg))
    h1 = load_bars(run.data, inst["yahoo"], "1h", now, rolls=run.rolls, inst=inst, cfg=cfg)
    days = usable_bars(daily_from_hourly(h1, cfg["day_end"], inst, cfg, now=now))
    daily_atr = atr_wilder(days[-ATR_WINDOW["1d"]:], 14)
    sides = ("buy", "sell") if cfg["allow_short"] else ("buy",)
    res = day_status(b5, daily_atr, inst, cfg, now, fx_date, session, wish, sides)
    res["rank"] = target.get("rank")
    res["rolls_applied"] = dict(target.get("rolls_applied") or {})
    if res["setup"] is None and wish:
        res["setup"] = "or_breakout" if wish == "buy" else "or_breakdown"
    ms = market_state(inst, now, cfg)
    res["_market"] = ms
    res["_day_over"] = res["status"] in ("late", "expired") or (
        res["status"] == "closed" and res["note"].startswith("The opening range"))
    return res


def check_one(key, args, cfg, paths, now, target):
    """One check of one target: refresh unless offline, swing_status or day_status, the
    data age and the check_notes. args.run is the EntryRun of this run."""
    run = args.run
    inst = instrument(cfg, key)
    target = dict(target)
    rebase_note = None
    if target["style"] == "swing" and target.get("setup") != "manual":
        target, rebase_note = rebase_hit(target, run.rolls, inst)
    intervals = ["1h"] if target["style"] == "swing" else ["5m", "1h"]
    notes, error = run.refresh_key(inst, intervals)
    if target["style"] == "swing":
        res = check_swing(run, inst, target, notes, error)
    else:
        res = check_day(run, inst, target, notes, error)
    res["rolls_applied"] = known_rolls(run.rolls, inst)
    res["_rebase_note"] = rebase_note
    res["_offline_note"] = bool(args.offline and args.now is None)
    res["_data_dir"] = run.data
    latest = latest_price(run.data, inst, now, cfg)
    res["_latest"] = latest
    res["data_age_min"] = round(latest["age_min"], 1) if latest else None
    res["notes"] = res["notes"] + notes + check_notes(res, inst, cfg, now, run.calendar(), run.rolls)
    res["_inst"] = inst
    res["_now"] = now
    return res


def best_result(results):
    """The one result to report: by status (triggered, waiting, expired, late,
    closed), then by rank."""
    return sorted(results, key=lambda r: (STATUS_ORDER.index(r["status"]),
                                          r.get("rank") is None, r.get("rank") or 0))[0]


def entry_doc(res, now):
    """The entry.json doc (00 4.2, section 5) of one result."""
    note = res["note"]
    if res.get("line2"):
        note = "%s %s" % (note, res["line2"])
    return {"created_by": SKILL, "created_at": utc_iso(now), "symbol": res["symbol"],
            "side": res["side"], "style": res["style"], "setup": res["setup"],
            "fx_day": res["fx_day"], "status": res["status"], "level": res["level"],
            "opening_range": res["opening_range"], "bar": res["bar"], "price": res["price"],
            "triggered_at": res["triggered_at"],
            "distance": res["distance"] if res["status"] == "waiting" else None,
            "atr": res["atr"], "atr_interval": res["atr_interval"],
            "data_age_min": res.get("data_age_min"), "rolls_applied": res["rolls_applied"],
            "note": note}


def price_line(res, inst):
    """The newest price with its source and age (06 2.6 rule 4)."""
    latest = res.get("_latest")
    if not latest:
        return "%s. No saved price yet." % scale_note(inst)
    return "%s. Newest %s, %s." % (scale_note(inst), price_text(latest["bid"], inst),
                                  age_text(latest["age_min"]))


def target_line(res, inst):
    """Line 3: the key, the side words and the setup words."""
    if res["side"] not in ("buy", "sell"):
        return "%s  both sides watched  opening range." % res["symbol"]
    return "%s  %s  %s." % (res["symbol"], SIDE_WORDS[res["side"]], setup_words(res["setup"]))


def describe(res, inst):
    """The printed lines of 6.1 (swing) and 6.2 (day), without the write and next lines.
    The status line is never wrapped; the other lines wrap at 78."""
    out = [res["head"]]
    rest = []
    if res.get("after_head"):
        rest.append(res["after_head"])
    if res.get("line2"):
        rest.append(res["line2"])
    if res["status"] == "triggered":
        rest.append("This is the moment the rule names. Nothing was placed.")
    if res["style"] == "swing":
        if res["status"] == "waiting" and res.get("_next_end"):
            rest.append("A finished daily close beyond the level is needed. The next day ends %s."
                        % both_times(res["_next_end"]))
        rest.append(target_line(res, inst))
        bar = res.get("_bar")
        if bar is not None:
            which = "That day" if res["status"] in ("triggered", "expired") else "The newest day"
            atr = (" Daily ATR %s." % res["atr"]) if res["atr"] is not None else ""
            rest.append("%s ended %s.%s" % (which, both_times(bar["end"]), atr))
    else:
        rest.append(target_line(res, inst))
        if res.get("opening_range"):
            atr = (" 5-minute ATR %s." % res["atr"]) if res["atr"] is not None else ""
            rest.append(or_words(res["opening_range"], inst) + atr)
        cut = res.get("_cutoff")
        if cut is not None:
            rest.append("No new day trades after %s (%s)." % (pkt_clock(cut), ny_label(cut)))
    rest.append(price_line(res, inst))
    rest += ["Note: " + n for n in res["notes"]]
    for line in rest:
        out += wrap_line(line)
    return out


def bar_line(bar, res, inst):
    """One watch line for a newly finished bar (6.3)."""
    word = {"triggered": "Triggered.", "waiting": "Waiting."}.get(res["status"], res["status"].capitalize() + ".")
    if res["style"] == "swing":
        return "%s  the daily bar of %s closed at %s. %s" % (
            pkt_label(res.get("_now") or bar["end"]), day_label(parse_fx_date(bar["fx_day"])),
            price_text(bar["close"], inst), word)
    end = bar["start"] + datetime.timedelta(minutes=5)
    return "%s  the %s bar closed at %s. %s" % (
        pkt_clock(end), clock_label(to_pkt(bar["start"])), price_text(bar["close"], inst), word)


def start_check(args, now):
    """4.1 steps 1 to 3 for check and watch: (run, key, inst), with the flag checks."""
    run = EntryRun(args, now)
    args.run = run
    cfg = run.cfg
    key = pick_key(args.symbol, cfg)
    inst = instrument(cfg, key)
    args.side_typed = args.side
    side_rules(args, cfg)
    if args.setup and setup_row(args.setup) is None:
        raise ValueError('"%s" is not a setup name. Names: %s.'
                         % (args.setup, ", ".join(s["name"] for s in SETUPS)))
    args.level_text = args.level
    if args.level is not None:
        try:
            args.level = float(args.level)
        except ValueError:
            raise ValueError("--level must be a price above 0, not %s." % args.level_text)
        run.watchlist = None
    else:
        run.watchlist = read_watchlist(run.wpath, cfg, run.rolls, required=False)
    args.watchlist_path = run.wpath
    return run, key, inst


def run_check(args, run, key, inst):
    """One full check of SYMBOL: (the reported result, the other results, notes)."""
    known = known_rolls(run.rolls, inst)
    style, targets, notes = pick_target(args, run.cfg, key, inst, run.watchlist, run.now, known)
    results = [check_one(key, args, run.cfg, run.paths, run.now, t) for t in targets]
    res = best_result(results)
    res["notes"] = notes + res["notes"]
    return res, [r for r in results if r is not res]


def also_line(r, inst):
    """One short line for another hit of the same key (6.1)."""
    far = ""
    if r["status"] == "waiting" and r.get("_distance_atr") is not None:
        far = ", %.2f ATR away" % r["_distance_atr"]
    return "Also on the watchlist: %s %s side %s: %s%s." % (
        r["symbol"], r["side"], setup_words(r["setup"]), r["status"].upper(), far)


def next_lines(res, wrote):
    """The last lines: what was written and the next step."""
    out = []
    key = res["symbol"]
    if wrote:
        out.append("Wrote %s (%s)." % (wrote, res["status"]))
    else:
        out.append("Not written. Add --write to save fx/entry.json for fx-exit.")
    if res["status"] == "triggered":
        if wrote:
            out += ["Next: set the stop and the target before any order:",
                    "python3 fx-exit/exit.py plan --write"]
        else:
            out.append("Next: python3 fx-entry/entry.py check %s --write" % key)
    elif res["status"] == "waiting":
        out.append("Next: check again after the next %s." % (
            "daily close" if res["style"] == "swing" else "5-minute bar"))
    elif res["status"] == "expired":
        out.append("Next: python3 fx-scanner/scan.py" + (" day" if res["style"] == "day" else ""))
    return out


def answer(args, cmd, now, lines, code, doc):
    """Print the words (or one JSON object with --json) and return the exit code."""
    if args.json:
        emit_json({"v": JSON_VERSION, "tool": SKILL, "cmd": cmd, "now": utc_iso(now),
                   "ok": code == 0, "notes": lines, "doc": doc})
    else:
        for line in lines:
            print(line)
    return code


def result_lines(res, inst, others, wrote, quiet):
    """All printed lines of one check (or only the status lines with --quiet)."""
    lines = describe(res, inst)
    if quiet:
        keep = [res["head"]] + [x for x in (res.get("after_head"), res.get("line2")) if x]
        return keep
    lines += [also_line(r, inst) for r in others]
    return lines + next_lines(res, wrote)


def cmd_check(args):
    """check SYMBOL (4.1 to 4.4): exit 0 when triggered, else 1."""
    now = parse_now(args.now)
    run, key, inst = start_check(args, now)
    res, others = run_check(args, run, key, inst)
    doc, wrote = None, None
    if args.write:
        doc = entry_doc(res, now)
        save_json_atomic(run.paths["entry"], doc)
        wrote = run.paths["entry"]
    lines = result_lines(res, inst, others, wrote, args.quiet)
    return answer(args, "check", now, lines, 0 if res["status"] == "triggered" else 1, doc)


def hit_line(res, inst):
    """One watchlist line (6.4)."""
    side = "%s side" % res["side"]
    what = ""
    if res["status"] == "triggered" and res.get("_bar"):
        b = res["_bar"]
        when = DAY_NAMES[parse_fx_date(b["fx_day"]).weekday()] if res["style"] == "swing" \
            else pkt_clock(b["start"] + datetime.timedelta(minutes=5))
        what = "  close %s (%s)" % (price_text(b["close"], inst), when)
    elif res["status"] == "waiting" and res.get("distance") is not None:
        atr = (" (%.2f ATR)" % res["_distance_atr"]) if res.get("_distance_atr") is not None else ""
        what = "  %s to go%s" % (dist_words(res["distance"], inst), atr)
    return "  %-7s %-10s %-30s %s%s" % (res["symbol"], side, setup_words(res["setup"]),
                                        res["status"].upper(), what)


def cmd_watchlist(args):
    """watchlist (4.6): one line per hit; exit 0 when at least one triggered."""
    now = parse_now(args.now)
    run = EntryRun(args, now)
    args.run = run
    cfg = run.cfg
    doc = read_watchlist(run.wpath, cfg, run.rolls, required=True)
    run.watchlist = doc
    args.level, args.setup, args.style, args.side = None, None, None, None
    hits = sorted(doc["hits"], key=lambda h: (h.get("rank") is None, h.get("rank") or 0))
    made = parse_utc(doc.get("created_at")) or now
    lines = ["Watchlist of %s %s PKT (%s, %d %s)." % (
        day_label(to_pkt(made)), clock_label(to_pkt(made)), doc["mode"], len(hits),
        "hit" if len(hits) == 1 else "hits")]
    done, firsts, futures = [], [], []
    for hit in hits:
        inst = instrument(cfg, hit["symbol"])
        if hit["side"] == "sell" and not cfg["allow_short"]:
            lines.append("  %-7s sell side  sells are off (allow_short is false)" % hit["symbol"])
            continue
        args.side_typed = hit["side"]
        res = check_one(hit["symbol"], args, cfg, run.paths, now, hit)
        done.append(res)
        lines.append(hit_line(res, inst))
        if res["status"] == "triggered":
            firsts.append(res["symbol"])
        if inst.get("price_source") == "futures":
            futures.append(first_lower(inst.get("name") or inst["key"]))
    n = len(firsts)
    lines.append("%d of %d triggered. Nothing was placed." % (n, len(hits)))
    if not args.quiet:
        if futures:
            names = sorted(set(futures))
            what = "is a futures price" if len(names) == 1 else "are futures prices"
            lines += wrap_line("Prices: Yahoo. Futures are about 10 minutes late; %s %s, not "
                               "your broker's price." % (" and ".join(names), what))
        else:
            lines.append("Prices: Yahoo, close to your broker's prices.")
        if firsts:
            lines.append("Next: python3 fx-entry/entry.py check %s --write, then /fx-exit." % firsts[0])
    return answer(args, "watchlist", now, lines, 0 if n else 1, None)

class SimClock(object):
    """A clock that moves only when it sleeps: a replay runs without waiting."""

    def __init__(self, start):
        self.t = start
        self.slept = 0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += 1
        self.t += datetime.timedelta(seconds=max(1, int(round(seconds))))


class LiveClock(object):
    """The real clock; sleep is the module's SLEEP (tests swap it)."""

    def now(self):
        return utc_now()

    def sleep(self, seconds):
        SLEEP(seconds)


def watch_header(res, inst, now):
    """The first lines of a watch (6.3)."""
    side = ("%s side" % res["side"]) if res["side"] in ("buy", "sell") else "both sides"
    out = ["%s  %s  %s. Watching from %s." % (
        res["symbol"], side, setup_words(res["setup"]) if res["setup"] else "opening range",
        pkt_label(now))]
    or_ = res.get("opening_range")
    if res["style"] == "day" and or_:
        out.append("%s range %s to %s: high %s, low %s (%d of %d bars)." % (
            or_["session"].replace("_", " ").title(), clock_label(to_pkt(parse_utc(or_["start"]))),
            pkt_clock(parse_utc(or_["end"])), price_text(or_["high"], inst),
            price_text(or_["low"], inst), or_["bars"], or_["of"]))
    elif res["style"] == "swing" and res.get("level"):
        out.append("Level %s (a finished daily close %s it)." % (
            price_text(res["level"], inst), "above" if res["side"] == "buy" else "below"))
    return out


def new_bars(res, last_start):
    """The bars of this look that finished after last_start (the trigger bar left out)."""
    if res["style"] == "swing":
        bar = res.get("_bar")
        if bar is None or (last_start is not None and bar["start"] <= last_start):
            return []
        return [bar]
    trig = res.get("_trig")
    out = [b for b in res.get("_today") or [] if last_start is None or b["start"] > last_start]
    return [b for b in out if trig is None or b["start"] != trig["start"]]


def watch_wake(res, now, every, until):
    """(wake time, longest single sleep) after a look that did not end the watch."""
    ms = res.get("_market") or {}
    if ms.get("state") not in (None, "open") and ms.get("next_open"):
        wake, cap = ms["next_open"], 3600
    elif res["style"] == "day":
        newest = res.get("_newest")
        wake = (newest["start"] + datetime.timedelta(minutes=10, seconds=WAKE_GRACE_S)
                if newest else now)
        if wake <= now:
            wake = now + datetime.timedelta(seconds=every)
        cap = every
    else:
        wake = res["_next_end"] + datetime.timedelta(minutes=DONE_MARGIN_MIN + 1)
        if wake <= now:
            wake = now + datetime.timedelta(seconds=3600)
        cap = 3600
    if until is not None and wake > until:
        wake = until
    return wake, cap

def watch_loop(look, clock, sleep, emit, every, until, max_errors=MAX_FAILED_LOOKS):
    """4.5: look, print the new bars, stop on a trigger (0), an expiry, the day's end or
    --until (1), or after max_errors failed looks in a row (2). look(now) -> a result;
    clock() -> naive UTC; sleep(seconds). The clock is read again after each sleep, so
    a computer that slept catches up at once. Returns (code, the last result)."""
    errors, first, last_start, said, res = 0, True, None, set(), None
    while True:
        now = clock()
        if until is not None and now >= until:
            why = res["note"] if res is not None else "No look was made."
            emit("Stopped at --until %s without a trigger. %s" % (pkt_clock(until), why))
            return 1, res
        try:
            res = look(now)
            errors = 0
        except ValueError as exc:
            errors += 1
            emit("%s  could not read Yahoo: %s" % (pkt_clock(now), " ".join(str(exc).split())))
            if errors >= max_errors:
                emit("Stopping after %d failed reads in a row." % errors)
                return 2, None
            wake, cap = now + datetime.timedelta(seconds=every), every
        else:
            inst = res["_inst"]
            if first:
                for line in watch_header(res, inst, now):
                    emit(line)
                bars = new_bars(res, None)
                last_start = bars[-1]["start"] if bars else None
                first = False
            else:
                for bar in new_bars(res, last_start):
                    emit(bar_line(bar, res, inst))
                    last_start = bar["start"]
            if res["status"] == "triggered":
                return 0, res
            if res["status"] in ("expired", "late") or (res["status"] == "closed" and res.get("_day_over")):
                emit(res["head"])
                return 1, res
            wake, cap = watch_wake(res, now, every, until)
            ms = res.get("_market") or {}
            if ms.get("state") not in (None, "open") and ms.get("next_open") and "shut" not in said:
                said.add("shut")
                name = inst.get("name") or inst["key"]
                what = ("%s is in the CME daily break." % name if ms["state"] == "break"
                        and hours_template(inst["hours"])["group"] == "cme" else "%s is closed now." % name)
                emit("%s Watching again from %s." % (what, pkt_clock(ms["next_open"])))
        while True:
            left = (wake - clock()).total_seconds()
            if left <= 0:
                break
            sleep(min(cap, left))


def parse_until(text, now):
    """--until HH:MM (Karachi, or ...Z for UTC) -> the first such moment after now."""
    if text is None:
        return None
    t = str(text).strip()
    utc = t[-1:] in ("Z", "z")
    hhmm = parse_hhmm(t[:-1] if utc else t, "--until")
    if utc:
        cand = datetime.datetime.combine(now.date(), hhmm)
    else:
        cand = pkt_to_utc(datetime.datetime.combine(to_pkt(now).date(), hhmm))
    if cand <= now:
        cand += datetime.timedelta(days=1)
    return cand


def parse_every(text):
    """--every SECONDS: a whole number from 60 to 3600."""
    try:
        n = int(str(text))
    except ValueError:
        n = None
    if n is None or not 60 <= n <= 3600:
        raise ValueError("--every must be 60 to 3600 seconds (Yahoo asks for a polite pace).")
    return n

def cmd_watch(args):
    """watch SYMBOL (4.5): exit 0 on a trigger (or Ctrl+C), 1 when it stops without
    one, 2 after 5 failed looks in a row. A replay (--offline or --now) runs on a
    simulated clock with no waiting."""
    now = parse_now(args.now)
    every = parse_every(args.every)
    until = parse_until(args.until, now)
    run, key, inst = start_check(args, now)
    known = known_rolls(run.rolls, inst)
    style, targets, start_notes = pick_target(args, run.cfg, key, inst, run.watchlist, now, known)
    replay = bool(args.offline or args.now is not None)
    clock = SimClock(now) if replay else LiveClock()
    lines = []

    def emit(line):
        lines.append(line)
        if not args.json:
            print(line)
            sys.stdout.flush()

    def look(t):
        run.now = t
        run.refreshed = set()
        if run.events is not None and run.events_at is not None and (t - run.events_at).total_seconds() >= 3600:
            run.events = None
        if run.events is None:
            run.events_at = t
        run.load_rolls()
        results = [check_one(key, args, run.cfg, run.paths, t, x) for x in targets]
        res = best_result(results)
        res["notes"] = start_notes + res["notes"]
        return res

    run.events_at = None
    try:
        code, res = watch_loop(look, clock.now, clock.sleep, emit, every, until)
    except KeyboardInterrupt:
        emit("Stopped.")
        return answer_watch(args, now, lines, 0, None)
    doc = None
    if code == 0:
        wrote = None
        if args.write:
            doc = entry_doc(res, run.now)
            save_json_atomic(run.paths["entry"], doc)
            wrote = run.paths["entry"]
        for line in result_lines(res, inst, [], wrote, args.quiet):
            emit(line)
    return answer_watch(args, now, lines, code, doc)


def answer_watch(args, now, lines, code, doc):
    """The end of a watch: with --json one object (the lines go into notes)."""
    if args.json:
        emit_json({"v": JSON_VERSION, "tool": SKILL, "cmd": "watch", "now": utc_iso(now),
                   "ok": code == 0, "notes": lines, "doc": doc})
    return code


def build_parser():
    """The argparse parser: check, watchlist and watch (section 2)."""
    p = argparse.ArgumentParser(
        prog="entry.py", description="fx-entry: names the moment a setup's rule is met "
        "(a finished bar that closes beyond the level). It never places an order.")
    sub = p.add_subparsers(dest="cmd")
    for name in ("check", "watch"):
        s = sub.add_parser(name, help="check one symbol" if name == "check" else
                           "watch one symbol bar by bar until it triggers")
        s.add_argument("symbol", metavar="SYMBOL", help="an instrument key, such as XAUUSD")
        add_common(s, write=True)
        s.add_argument("--watchlist", metavar="FILE", default=None,
                       help="the scanner file (default fx/watchlist.json)")
        s.add_argument("--side", choices=("buy", "sell", "both"), default=None,
                       help="which side to report (default both, or buy when sells are off)")
        s.add_argument("--setup", metavar="NAME", default=None, help="pick the hit with this setup")
        s.add_argument("--style", choices=("swing", "day"), default=None,
                       help="the style when the watchlist has no hit for SYMBOL")
        s.add_argument("--level", metavar="PRICE", default=None,
                       help="swing only: your own level (needs --side buy or --side sell)")
        if name == "watch":
            s.add_argument("--every", metavar="SECONDS", default="60",
                           help="seconds between two looks while waiting for a 5m bar (60 to 3600)")
            s.add_argument("--until", metavar="HH:MM", default=None,
                           help="stop at this Karachi time (...Z for UTC)")
    s = sub.add_parser("watchlist", help="check every hit of fx/watchlist.json")
    add_common(s, write=False)
    s.add_argument("--watchlist", metavar="FILE", default=None,
                   help="the scanner file (default fx/watchlist.json)")
    return p


def main(argv=None):
    """Parse the flags, run the subcommand, turn a ValueError into one stderr line."""
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "watchlist":
            return cmd_watchlist(args)
        return cmd_watch(args)
    except ValueError as exc:
        return fail(exc, TOOL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
