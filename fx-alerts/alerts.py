#!/usr/bin/env python3
"""fx-alerts: price and clock alerts for forex, gold, silver, oil and gas.

    python3 fx-alerts/alerts.py add --from-plan
    python3 fx-alerts/alerts.py add USDJPY above 158.50
    python3 fx-alerts/alerts.py check
    python3 fx-alerts/alerts.py quote XAUUSD EURUSD
    python3 fx-alerts/alerts.py quote XAUUSD --broker-price 2429.15
    python3 fx-alerts/alerts.py clock
    python3 fx-alerts/alerts.py list

Each rule rings once. A buy's stop and target ring on the bid, a sell's on the ask
(the Yahoo bid plus the spread), as in MetaTrader. Prices are Yahoo's, not the
broker's. An alert is information, never an order: this tool never places, closes or
changes an order. Exit 0: something rang (or the command is done); 1: nothing rang;
2: an error. Python 3.8, standard library only.
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

TOOL = "alerts.py"


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


def broker_note():
    """The words next to a price the user typed from the broker."""
    return "your broker's price (you typed it)"


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


def to_london(utc):
    """naive UTC -> London wall clock."""
    return utc + datetime.timedelta(hours=1) if uk_dst(utc) else utc


def london_to_utc(local):
    """London wall clock -> naive UTC (a doubled hour gives the summer one)."""
    a = local - datetime.timedelta(hours=1)
    return a if uk_dst(a) else local


def to_sydney(utc):
    """naive UTC -> Sydney wall clock."""
    return utc + datetime.timedelta(hours=11 if sydney_dst(utc) else 10)


def sydney_to_utc(local):
    """Sydney wall clock -> naive UTC (a doubled hour gives the summer one)."""
    a = local - datetime.timedelta(hours=11)
    return a if sydney_dst(a) else local - datetime.timedelta(hours=10)


def to_tokyo(utc):
    """naive UTC -> Tokyo wall clock (UTC+9, no daylight saving)."""
    return utc + datetime.timedelta(hours=9)


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


def odd_week(utc):
    """True in the weeks when only one of the US and the UK is on summer time."""
    return us_dst(utc) != uk_dst(utc)


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


LBMA_TIMES = {"gold": ["10:30", "15:00"], "silver": ["12:00"], "platinum": ["09:45", "14:00"],
              "palladium": ["09:45", "14:00"]}


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


def key_times(fx_date, cfg):
    """The fixed daily times of an FX day, as sorted (utc, label): US data times, the
    LBMA gold prices, the London 4 pm fix, the CME break and the rollover."""
    d = parse_fx_date(fx_date)
    out = [(ny_to_utc(at_time(d, "08:30")), "US data time"),
           (ny_to_utc(at_time(d, "10:00")), "US data time"),
           (london_to_utc(at_time(d, "16:00")), "London 4 pm FX fix"),
           (ny_to_utc(at_time(d, "17:00")), "Gold and oil daily break"),
           (ny_to_utc(at_time(d, "17:00")), "Rollover (swap is booked)")]
    row = HOLIDAYS.get(d.isoformat(), {})
    uk_holiday = "fx" in row.get("thin", []) and "(UK" in row.get("name", "")
    if not uk_holiday and not full_fx_holiday(d, cfg):
        out += [(london_to_utc(at_time(d, t)), "LBMA gold price") for t in LBMA_TIMES["gold"]]
    return sorted(out, key=lambda x: x[0])


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


def calendar_info(data_dir, now):
    """The status of this week's calendar file: {week, ok, fetched_at, tried_at,
    age_min, error, events}; None values when there is no file. events is a count."""
    week = calendar_week(now)
    doc = calendar_file_doc(os.path.join(data_dir, "calendar", week + ".json")) or {}
    fetched = parse_utc(doc.get("fetched_at"))
    return {"week": week, "ok": doc.get("ok"), "fetched_at": doc.get("fetched_at"),
            "tried_at": doc.get("tried_at"), "error": doc.get("error"),
            "age_min": int((now - fetched).total_seconds() // 60) if fetched else None,
            "events": len(doc["events"]) if isinstance(doc.get("events"), list) else None}


UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"


YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")


PACE_SECONDS = 2.0     # at least this long between any two Yahoo requests (both hosts)


TIMEOUT = 25           # socket timeout in seconds


SPARK_MAX = 20         # spark takes at most 20 symbols (21 gives HTTP 400)


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


def spark(symbols):
    """Quick closes for a printout, never for levels: {asked symbol: {close, at, short_name}},
    in calls of at most SPARK_MAX symbols; unknown symbols are just missing."""
    out = {}
    symbols = list(symbols)
    for i in range(0, len(symbols), SPARK_MAX):
        part = symbols[i:i + SPARK_MAX]
        url = (YAHOO_HOSTS[0] + "/v7/finance/spark?symbols="
               + ",".join(urllib.parse.quote(s, safe="") for s in part) + "&range=1d&interval=5m")
        doc = json.loads(yahoo_get(url).decode("utf-8"))
        for item in ((doc.get("spark") or {}).get("result") or []):
            sym = item.get("symbol")
            resp = (item.get("response") or [None])[0]
            if sym not in part or not resp:
                continue
            meta, rows = parse_chart(resp, sym)
            closes = [r[4] for r in rows if r[4] is not None]
            at = meta.get("regularMarketTime")
            out[sym] = {"close": closes[-1] if closes else meta.get("regularMarketPrice"),
                        "at": ts_to_utc(at) if at is not None else None,
                        "short_name": meta.get("shortName")}
    return out


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


ROLL_GUARD_ATR = 3           # a 1h move above 3 x the 1h ATR inside a roll window may be a roll


def roll_guard(inst, bars_1h, rolls, now, cfg):
    """fx-alerts check: inside an expected roll window, a last 1h bar that moved more
    than 3 x the 1h ATR with no roll found in the last 24 hours may be a roll. Returns
    the line that holds a stop or target ring, else None."""
    root = inst_root(inst)
    if not root or not inst.get("futures") or len(bars_1h) < 2:
        return None
    if not [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]]:
        return None
    done = [b for b in bars_1h if not b.get("forming")]
    if len(done) < 2:
        return None
    a = atr_wilder(usable_bars(done)[-ATR_WINDOW["1h"]:], 14)
    if not a:
        return None
    last, prev = done[-1], done[-2]
    if max(abs(last["close"] - last["open"]), abs(last["open"] - prev["close"])) <= ROLL_GUARD_ATR * a:
        return None
    for r in rolls or []:
        at = parse_utc(r.get("first_bar_new"))
        if r.get("root") == root and at is not None and now - datetime.timedelta(hours=24) <= at <= now:
            return None
    return "Yahoo %s may have rolled. Check your MetaTrader price." % ROLL_WORDS.get(root, root)


K_LIMITS = {"fx": 0.01, "metal": 0.03, "energy": 0.20}   # how far k may be from 1, by kind


BROKER_PRICE_MINUTES = 60    # a typed broker price counts as today's this long (D23)


def broker_k(inst, broker_bid, yahoo_bid):
    """k = broker_bid / (yahoo_bid x yahoo_to_broker), not rounded; ValueError when the
    broker price is too far from Yahoo's (K_LIMITS by kind)."""
    b, y = _price_dec(broker_bid), _price_dec(yahoo_bid)
    plan = y * _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    k = float(b) / float(plan)
    limit = K_LIMITS[inst["kind"]]
    if abs(k - 1) > limit:
        q = Decimal(1).scaleb(-int(inst["digits"]))
        raise ValueError("Your broker price %s for %s is far from Yahoo's %s (more than %s%%). "
                         "Check the symbol and the price you typed."
                         % ("{:f}".format(b.normalize()), inst["key"],
                            plan.quantize(q, rounding=ROUND_HALF_UP), format(limit * 100, "g")))
    return k


def saved_broker_price(prices, symbol, now, cfg, rolls):
    """The prices.json broker record of symbol when it is at most 60 minutes old, on the
    FX day of now, and no roll of its root came after its yahoo_at (D23); else None."""
    rec = ((prices or {}).get("broker") or {}).get(symbol)
    if not rec:
        return None
    at = parse_utc(rec.get("at"))
    if at is None or at > now or now - at > datetime.timedelta(minutes=BROKER_PRICE_MINUTES):
        return None
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = fx_day(now, day_end)
    if d is None or fx_day(at, day_end) != d:
        return None
    row = ((cfg or {}).get("instruments") or {}).get(symbol) or {}
    root = (row.get("futures") or {}).get("root")
    y_at = parse_utc(rec.get("yahoo_at")) or at
    for r in rolls or []:
        fbn = parse_utc(r.get("first_bar_new"))
        if root and r.get("root") == root and fbn is not None and fbn > y_at:
            return None
    return rec


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


# ---- end of the copied block


# ---------------------------------------------------------------- fx-alerts: settings

CLOCK_LATE_MIN = 30        # a session alert is not rung later than this after the open
FLAT_BY_WARN_MIN = 15      # a flat_by rule rings this early
EXIT_BY_WARN_MIN = 60      # an exit_by rule rings this early
SESSION_WARN_MIN = 5       # a session_open rule rings this early
ROLLOVER_WARN_MIN = 30     # a rollover rule rings this early
PRICE_KINDS = ("above", "below", "stop", "target")
AT_KINDS = ("flat_by", "exit_by", "friday_close")
CLOCK_KINDS = ("session_open", "rollover", "news")
RULE_KINDS = PRICE_KINDS + AT_KINDS + CLOCK_KINDS
RULE_KEYS = ("id", "active", "from", "symbol", "kind", "level", "side", "at_utc",
             "minutes_before", "session", "position_id", "rolls_applied", "note", "added_at")
POSITION_FIELDS = ["entry", "fill_price", "stop_loss", "take_profit", "stop_at_open"]
GROUP_LABELS = {"fx": "FX", "comex": "CME metals", "nymex": "NYMEX metals and energy",
                "ice_brent": "Brent"}
SESSION_LABELS = {"sydney": "Sydney", "tokyo": "Tokyo", "london": "London", "new_york": "New York"}
SESSION_CITY = {"sydney": to_sydney, "tokyo": to_tokyo, "london": to_london, "new_york": to_ny}
HOLIDAY_TAIL = (' Your broker\'s own notice wins: add it to "holidays" or "early_closes" '
                'in fx/config.json.')


class AlertError(ValueError):
    """A plain refusal of fx-alerts: printed as one line, exit 2."""


# ---------------------------------------------------------------- fx-alerts: files

def rule_problem(path, rid, what):
    return AlertError('%s: rule "%s": %s' % (path, rid, what))


def check_rules(doc, path, cfg):
    """The rules list of alerts.json with the 01 5.2 checks; a bad rule stops all."""
    rules = check_objects(doc, "rules", path)
    seen = set()
    for n, r in enumerate(rules, 1):
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            raise rule_problem(path, "#%d" % n, 'it has no "id"')
        if rid in seen:
            raise rule_problem(path, rid, "the id is used twice")
        seen.add(rid)
        kind = r.get("kind")
        if kind not in RULE_KINDS:
            raise rule_problem(path, rid, 'unknown kind "%s". Use one of: %s.' % (kind, ", ".join(RULE_KINDS)))
        sym = r.get("symbol")
        if sym is not None and sym not in cfg["instruments"]:
            raise rule_problem(path, rid, '"%s" is not in your instrument list' % sym)
        if sym is None and (kind in PRICE_KINDS or kind in ("exit_by", "friday_close", "news")):
            raise rule_problem(path, rid, 'a %s rule needs a "symbol"' % kind)
        if kind in PRICE_KINDS and not _positive_number(r.get("level")):
            raise rule_problem(path, rid, 'a %s rule needs a "level" above 0' % kind)
        if kind in ("stop", "target") and r.get("side") not in ("buy", "sell"):
            raise rule_problem(path, rid, 'a %s rule needs "side": "buy" or "sell"' % kind)
        if kind in AT_KINDS and parse_utc(r.get("at_utc")) is None:
            raise rule_problem(path, rid, 'a time rule needs "at_utc" like 2026-11-11T08:40:00Z')
        if kind == "session_open" and r.get("session") not in SESSIONS:
            raise rule_problem(path, rid, '"session" must be "sydney", "tokyo", "london" or "new_york"')
        mb = r.get("minutes_before")
        if mb is not None and (isinstance(mb, bool) or not isinstance(mb, int) or not 0 <= mb <= 1440):
            raise rule_problem(path, rid, '"minutes_before" must be 0 to 1440 or null')
        if r.get("added_at") is not None and parse_utc(r.get("added_at")) is None:
            raise rule_problem(path, rid, '"added_at" is not a UTC time like 2026-11-11T08:40:00Z')
    return rules


def read_alerts(path, cfg, now=None):
    """alerts.json as a doc (checked), or None when missing; now makes a new doc."""
    doc = load_doc(path, "alert rules", "fx-alerts add", required=False)
    if doc is None:
        if now is None:
            return None
        return {"created_by": "fx-alerts", "created_at": utc_iso(now), "rules": []}
    if "rules" not in doc:
        doc["rules"] = []
    check_rules(doc, path, cfg)
    return doc


def load_state(path, now):
    """state/alerts.json; a broken file is renamed and started fresh (01 5.2).
    Returns (state, notes)."""
    notes = []
    try:
        st = load_json(path, None)
        if st is not None and not isinstance(st, dict):
            raise ValueError("not an object")
        for key in ("rang", "seen", "held"):
            if st is not None and not isinstance(st.get(key, {}), dict):
                raise ValueError("bad " + key)
    except ValueError:
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        base = os.path.splitext(path)[0]
        try:
            os.replace(path, "%s.broken-%s.json" % (base, stamp))
        except OSError:
            pass
        notes.append("%s was damaged. Started a new one." % path)
        st = None
    st = st or {}
    for key in ("rang", "seen", "held"):
        st.setdefault(key, {})
    st.setdefault("checked_at", None)
    return st, notes


def read_portfolio(path, cfg):
    """portfolio.json (may be missing: an empty one) with the 01 5.2 checks."""
    doc = load_doc(path, "portfolio", "fx-broker", required=False)
    if doc is None:
        return {"positions": [], "closed": []}
    bad = lambda what: AlertError("%s: %s. Fix it by hand or restore a backup." % (path, what))
    acct = doc.get("account")
    if isinstance(acct, dict) and acct.get("currency", "USD") != "USD":
        raise bad("the account currency must be USD")
    doc.setdefault("positions", [])
    positions = check_objects(doc, "positions", path)
    ids = set()
    for p in positions:
        if p.get("symbol") not in cfg["instruments"]:
            raise bad('position %s has "%s", which is not in your instruments' % (p.get("id"), p.get("symbol")))
        if p.get("id") in ids:
            raise bad("two positions have the id %s" % p.get("id"))
        ids.add(p.get("id"))
        check_side(p.get("side"), path)
    return doc


def read_plan(path, cfg):
    """plan.json with the 01 5.2 checks the alerts need."""
    plan = load_doc(path, "plan", "fx-exit", required=True)
    check_symbol(plan.get("symbol"), cfg, path)
    side = check_side(plan.get("side"), path)
    style = check_style(plan.get("style"), path)
    entry = check_price(plan, "entry", path)
    sl = check_price(plan, "stop_loss", path)
    tp = check_price(plan, "take_profit", path)
    wrong = (sl >= entry or tp <= entry) if side == "buy" else (sl <= entry or tp >= entry)
    if wrong:
        raise AlertError("%s: the stop or the target is on the wrong side of the entry. "
                         "Run fx-exit again." % path)
    key = "flat_by_utc" if style == "day" else "exit_by"
    if not plan.get(key):
        raise AlertError('%s: a %s plan needs "%s"' % (path, style, key))
    check_utc(plan, key, path)
    return plan


def open_position(portfolio, symbol, side=None, style=None):
    """The newest open position of that symbol (and side and style), or None."""
    hits = [p for p in portfolio.get("positions") or []
            if p.get("symbol") == symbol and (side is None or p.get("side") == side)
            and (style is None or p.get("style") == style)]
    if not hits:
        return None
    return max(hits, key=lambda p: (p.get("opened_at") or "", p.get("id") or ""))


def roots_rolls(rolls, inst):
    """{roll id: ratio} of every known roll of the instrument's root (02 5.8)."""
    root = inst_root(inst)
    if not root:
        return {}
    return {r["id"]: float(r["ratio"]) for r in rolls if r.get("root") == root and r.get("id")}


def rolls_by_now(data_dir, now):
    """load_rolls minus the rolls found after now (a replay, 02 5.8)."""
    out = []
    for r in load_rolls(data_dir):
        found = parse_utc(r.get("found_at"))
        if found is None or found <= now:
            out.append(r)
    return out


def next_rule_id(rules):
    """"a<n>" with n = 1 + the highest n in the file."""
    best = 0
    for r in rules:
        m = re.match(r"^a(\d+)$", str(r.get("id") or ""))
        if m:
            best = max(best, int(m.group(1)))
    return "a%d" % (best + 1)


def new_rule(kind, symbol=None, level=None, side=None, at_utc=None, minutes_before=None,
             session=None, position_id=None, rolls_applied=None, note="", src="user", now=None):
    return {"id": None, "active": True, "from": src, "symbol": symbol, "kind": kind,
            "level": level, "side": side, "at_utc": at_utc, "minutes_before": minutes_before,
            "session": session, "position_id": position_id,
            "rolls_applied": dict(rolls_applied or {}), "note": note, "added_at": utc_iso(now)}


# ---------------------------------------------------------------- fx-alerts: words

def when_text(at, date=True):
    """"Fri 14 Aug 2:00 AM PKT (5:00 PM New York)"."""
    return "%s (%s)" % (pkt_label(at, date=date), ny_label(at))


def rule_text(rule, cfg):
    """A rule in a few words: "XAUUSD stop 2,400.00", "London opens" ..."""
    kind, sym = rule["kind"], rule.get("symbol")
    at = parse_utc(rule.get("at_utc"))
    if kind in PRICE_KINDS:
        inst = instrument(cfg, sym)
        return "%s %s %s" % (sym, kind, price_text(rule["level"], inst))
    if kind == "flat_by":
        return "%s flat by %s" % (sym or "all day trades", when_text(at, date=False))
    if kind == "exit_by":
        return "%s exit by %s" % (sym, when_text(at))
    if kind == "friday_close":
        return "%s Friday check at %s" % (sym, when_text(at))
    if kind == "session_open":
        return "%s opens" % SESSION_LABELS.get(rule.get("session"), rule.get("session"))
    if kind == "rollover":
        return "Rollover (5:00 PM New York)"
    return "news for %s" % sym


def fires_on(side):
    if side == "sell":
        return "it fires on the ask: the Yahoo bid plus the spread"
    return "it fires on the bid"


def scale_words(inst):
    """"These are Yahoo prices (gold futures), not your broker's." and the like."""
    text = scale_note(inst).replace("Yahoo price", "These are Yahoo prices", 1)
    return text.replace("your broker's price", "your broker's") + "."


def added_line(rule, cfg):
    text = "Added %s: %s" % (rule["id"], rule_text(rule, cfg))
    kind = rule["kind"]
    if kind in ("stop", "target"):
        whose = rule.get("position_id") or "plan"
        text += " for your %s %s (%s)" % (side_word(rule["side"]), whose, fires_on(rule["side"]))
    elif kind in ("flat_by", "exit_by") or (kind in CLOCK_KINDS and rule.get("minutes_before")):
        text += ", %d min early" % (rule.get("minutes_before") or 0)
    return text + "."


# ---------------------------------------------------------------- fx-alerts: add

def same_rule(a, b):
    keys = ("kind", "symbol", "side", "level", "session", "at_utc")
    return a.get("active") and all(a.get(k) == b.get(k) for k in keys)


def friday_cutoff_utc(now, cfg):
    """swing.friday_cutoff_ny on the first Friday whose 17:00 New York is after now."""
    d = to_ny(now).date()
    fri = d + datetime.timedelta(days=(4 - d.weekday()) % 7)
    if ny_to_utc(at_time(fri, "17:00")) <= now:
        fri += datetime.timedelta(days=7)
    return ny_to_utc(at_time(fri, cfg["swing"]["friday_cutoff_ny"]))


def plan_rules(plan, position, now, cfg, rolls, minutes_before=None):
    """The rules of add --from-plan (4.3), without ids."""
    sym, side, style = plan["symbol"], plan["side"], plan["style"]
    src = position or plan
    pid = position["id"] if position else None
    ra = dict(src.get("rolls_applied") or {})
    common = dict(symbol=sym, side=side, position_id=pid, rolls_applied=ra, src="plan", now=now)
    out = [new_rule("stop", level=src["stop_loss"], note="plan stop: %s" % (plan.get("stop_label") or ""), **common),
           new_rule("target", level=src["take_profit"], note="plan target: %s" % (plan.get("target_label") or ""), **common)]
    if style == "day":
        at = (position or {}).get("flat_by_utc") or plan["flat_by_utc"]
        mb = FLAT_BY_WARN_MIN if minutes_before is None else minutes_before
        hhmm = plan.get("flat_by") or cfg["day"]["flat_by"]
        out.append(new_rule("flat_by", at_utc=at, minutes_before=mb, note="flat by %s PKT" % hhmm, **common))
    else:
        at = (position or {}).get("exit_by") or plan["exit_by"]
        mb = EXIT_BY_WARN_MIN if minutes_before is None else minutes_before
        days = plan.get("max_hold_days") or cfg["swing"]["max_hold_days"]
        out.append(new_rule("exit_by", at_utc=at, minutes_before=mb,
                            note="last day of the %d-day hold" % days, **common))
        fri = friday_cutoff_utc(now, cfg)
        if fri < parse_utc(at):
            note = "Friday %s New York: decide about the weekend" % cfg["swing"]["friday_cutoff_ny"]
            out.append(new_rule("friday_close", at_utc=utc_iso(fri), note=note, **common))
    return out


def context(args):
    """paths, cfg, now, offline, today for one run."""
    paths = fx_paths("fx", args.data, args.config)
    cfg = load_config(paths["config"], required=args.config is not None)
    now = parse_now(args.now)
    return {"paths": paths, "cfg": cfg, "now": now,
            "offline": bool(args.offline or args.now is not None),
            "today": fx_day(now, cfg["day_end"])}


def symbol_key(text, cfg):
    """A typed symbol in capitals; an unknown key names the nearest one (06 2.1)."""
    key = str(text or "").strip().upper()
    rows = cfg["instruments"]
    if key not in rows:
        near = difflib.get_close_matches(key, list(rows), n=1, cutoff=0.5)
        such = near[0] if near else "XAUUSD"
        raise AlertError("%s is not in your instruments. Did you mean %s?" % (key, such))
    return key


def typed_price(text):
    try:
        x = float(text)
    except (TypeError, ValueError):
        raise AlertError("The price must be a number, like 2450, not %s." % text)
    if not math.isfinite(x) or x <= 0:
        raise AlertError("The price must be above 0, not %s." % text)
    return x


def user_rule(args, c, portfolio, rolls):
    """The one rule of a user add (4.3, other forms)."""
    cfg, now = c["cfg"], c["now"]
    words, mb = list(args.words or []), args.minutes_before
    if args.session:
        return new_rule("session_open", session=args.session, now=now,
                        minutes_before=SESSION_WARN_MIN if mb is None else mb, note=args.note)
    if args.rollover:
        return new_rule("rollover", now=now, note=args.note,
                        minutes_before=ROLLOVER_WARN_MIN if mb is None else mb)
    if args.news:
        sym = symbol_key(args.news, cfg)
        return new_rule("news", symbol=sym, now=now, note=args.note,
                        minutes_before=cfg["news"]["quiet_before_min"] if mb is None else mb)
    if args.flat_by:
        hhmm = parse_hhmm(args.flat_by, "--flat-by")
        if c["today"] is None:
            raise AlertError("It is the weekend. There is no FX day for --flat-by.")
        if len(words) > 1:
            raise AlertError("Say what to watch, for example: add XAUUSD above 2450.")
        sym = symbol_key(words[0], cfg) if words else None
        at = pkt_time_on_fx_day(hhmm, c["today"], cfg["day_end"])
        pos = open_position(portfolio, sym, style="day") if sym else None
        return new_rule("flat_by", symbol=sym, at_utc=utc_iso(at), now=now, note=args.note,
                        minutes_before=FLAT_BY_WARN_MIN if mb is None else mb,
                        position_id=pos["id"] if pos else None, side=pos["side"] if pos else None)
    sym = symbol_key(words[0], cfg)
    if len(words) < 2 or words[1].lower() not in PRICE_KINDS:
        raise AlertError("Say what to watch, for example: add XAUUSD above 2450.")
    kind = words[1].lower()
    if len(words) < 3:
        raise AlertError("add %s %s needs a price, for example: add %s %s 2450." % (sym, kind, sym, kind))
    inst = instrument(cfg, sym)
    level = round_price(typed_price(words[2]), inst)
    side = pid = None
    if kind in ("stop", "target"):
        if args.side is None:
            raise AlertError("A stop or target alert needs --side buy or --side sell "
                             "(a sell's stop fires on the ask).")
        side = args.side
        pos = open_position(portfolio, sym, side=side)
        pid = pos["id"] if pos else None
    return new_rule(kind, symbol=sym, level=level, side=side, position_id=pid, now=now,
                    rolls_applied=roots_rolls(rolls, inst), note=args.note)


def cmd_add(args):
    c = context(args)
    cfg, now, paths = c["cfg"], c["now"], c["paths"]
    kinds = [bool(args.from_plan), bool(args.session), bool(args.rollover), bool(args.news),
             bool(args.flat_by), bool(args.words) and not args.flat_by]
    if sum(kinds) != 1:
        raise AlertError("Say what to watch, for example: add XAUUSD above 2450.")
    mb = args.minutes_before
    if mb is not None and not 0 <= mb <= 1440:
        raise AlertError("--minutes-before must be 0 to 1440, not %d." % mb)
    portfolio = read_portfolio(paths["portfolio"], cfg)
    doc = read_alerts(paths["alerts"], cfg, now)
    rolls = rolls_by_now(paths["data"], now)
    notes, lines = [], []
    if args.from_plan:
        plan = read_plan(paths["plan"], cfg)
        sym = plan["symbol"]
        pos = open_position(portfolio, sym, plan["side"], plan["style"])
        if pos is None:
            notes.append("Note: no open %s trade yet. The alerts watch the plan's levels." % sym)
        new = plan_rules(plan, pos, now, cfg, rolls, mb)
        if args.note:
            for r in new:
                r["note"] = args.note
        pid = pos["id"] if pos else None

        def old_plan_rule(r):
            if r.get("from") != "plan" or r.get("symbol") != sym:
                return False
            if pid is not None and r.get("position_id") == pid:
                return True
            return r.get("position_id") is None and r.get("side") == plan["side"]
        gone = [r for r in doc["rules"] if old_plan_rule(r)]
        if gone:
            doc["rules"] = [r for r in doc["rules"] if not old_plan_rule(r)]
            notes.append("Replaced %d earlier plan alerts for %s." % (len(gone), sym))
    else:
        new = [user_rule(args, c, portfolio, rolls)]
        if args.note:
            new[0]["note"] = args.note
        for r in doc["rules"]:
            if same_rule(r, new[0]):
                if args.json:
                    emit_json(json_doc("add", now, True, notes + ["Already there: %s." % r["id"]], doc))
                else:
                    print("Already there: %s." % r["id"])
                return 0
    added = []
    for r in new:
        r["id"] = next_rule_id(doc["rules"])
        doc["rules"].append(r)
        added.append(r)
        lines.append(added_line(r, cfg))
    doc["created_by"] = "fx-alerts"
    doc["created_at"] = utc_iso(now)
    save_json_atomic(paths["alerts"], doc)
    syms = [r["symbol"] for r in added if r.get("symbol") and r["kind"] in PRICE_KINDS]
    if syms:
        inst = instrument(cfg, syms[0])
        lines.append(scale_words(inst) + " Your MetaTrader SL and TP are the ones that close the trade.")
        for r in added:
            if r["kind"] in ("above", "below"):
                lp = latest_price(paths["data"], inst, now, cfg)
                if lp and (lp["bid"] >= r["level"] if r["kind"] == "above" else lp["bid"] <= r["level"]):
                    notes.append("Note: %s is already %s %s (%s, %s). It rings on the next check."
                                 % (r["symbol"], r["kind"], price_text(r["level"], inst),
                                   price_text(lp["bid"], inst), age_text(lp["age_min"])))
    lines.append("Next: python3 fx-alerts/alerts.py check (every few minutes, for example with /loop 5m).")
    if args.json:
        emit_json(json_doc("add", now, True, notes, doc))
    elif args.quiet:
        for line in lines[:len(added)]:
            print(line)
    else:
        for line in lines[:len(added)] + notes + lines[len(added):]:
            print(line)
    return 0


def json_doc(cmd, now, ok, notes, doc, **more):
    """The --json object of 06 2.5."""
    out = {"v": JSON_VERSION, "tool": "fx-alerts", "cmd": cmd, "now": utc_iso(now),
           "ok": ok, "notes": list(notes), "doc": doc}
    out.update(more)
    return out



# ---------------------------------------------------------------- fx-alerts: price rules

def watch_symbols(rules, portfolio, args):
    """Section 3: the symbols to refresh, open positions first, then active price
    rules, in file order; --symbols keeps only those."""
    out = []
    for p in portfolio.get("positions") or []:
        if p.get("symbol") not in out:
            out.append(p["symbol"])
    for r in rules:
        if r.get("active") and r["kind"] in PRICE_KINDS and r.get("symbol") not in out:
            out.append(r["symbol"])
    wanted = getattr(args, "symbols_list", None)
    if wanted:
        out = [s for s in out if s in wanted]
    return out


def bar_end(bar):
    return bar["start"] + datetime.timedelta(seconds=STEP_S[bar["interval"]])


def bars_since(data, inst, since, now, rolls, cfg):
    """4.1 step 6: the done 5m bars that end after since, and 1h bars only for the time
    the 5m bars do not cover; roll and forming bars out. Each bar gets "interval"."""
    b5 = load_bars(data, inst["yahoo"], "5m", now, rolls=rolls, inst=inst, cfg=cfg)
    b1 = load_bars(data, inst["yahoo"], "1h", now, rolls=rolls, inst=inst, cfg=cfg)
    five, hour = datetime.timedelta(minutes=5), datetime.timedelta(hours=1)
    u5 = [b for b in b5 if b["start"] + five > since]
    if u5:
        a, z = u5[0]["start"], u5[-1]["start"] + five
        u1 = [b for b in b1 if b["start"] + hour > since and (b["start"] + hour <= a or b["start"] >= z)]
    else:
        u1 = [b for b in b1 if b["start"] + hour > since]
    out = []
    for interval, bars in (("5m", u5), ("1h", u1)):
        for b in bars:
            if b.get("forming") or "roll" in (b.get("flags") or []):
                continue
            b = dict(b)
            b["interval"] = interval
            out.append(b)
    return sorted(out, key=lambda b: b["start"])


def ask_price(bid, inst):
    """bid + the spread, exact, on the instrument's digits (for words and rang)."""
    q = Decimal(1).scaleb(-int(inst["digits"]))
    return float((_dec_clean(bid) + _dec_clean(spread_price(inst))).quantize(q, rounding=ROUND_HALF_UP))


def bar_hit(rule, bar, inst):
    """The price shown when this one bar rings the rule (4.2), else None."""
    kind, level, side = rule["kind"], rule["level"], rule.get("side")
    if kind == "above":
        return bar["high"] if bar["high"] >= level else None
    if kind == "below":
        return bar["low"] if bar["low"] <= level else None
    if kind == "stop":
        if bar_hits(side, bar, level, None, inst) not in ("stop", "both"):
            return None
        return bar["low"] if side == "buy" else ask_price(bar["high"], inst)
    if bar_hits(side, bar, None, level, inst) not in ("target", "both"):
        return None
    return bar["high"] if side == "buy" else ask_price(bar["low"], inst)


def price_hit(rule, bars, live, inst):
    """The first hit {at, price, interval} on the bars, then on the live price."""
    for b in bars:
        price = bar_hit(rule, b, inst)
        if price is not None:
            return {"at": b["start"], "price": price, "interval": b["interval"]}
    if live is not None:
        price = bar_hit(rule, live, inst)
        if price is not None:
            return {"at": live["start"], "price": price, "interval": "live"}
    return None


def past(rule, bid, inst):
    """4.2: is the live bid still past the rule's level (the held test)?"""
    kind, level, side = rule["kind"], rule["level"], rule.get("side")
    ask = ask_price(bid, inst)
    if kind == "above":
        return bid >= level
    if kind == "below":
        return bid <= level
    if side == "buy":
        return bid <= level if kind == "stop" else bid >= level
    return ask >= level if kind == "stop" else ask <= level



# ---------------------------------------------------------------- fx-alerts: rolls and retire

def roll_line(roll, inst):
    """The 02 5.8 line, once per roll id."""
    ratio = float(roll["ratio"])
    word = ROLL_WORDS.get(roll.get("root"), str(roll.get("root")))
    pct = Decimal(repr((ratio - 1) * 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("%s rolled on Yahoo (%s%s%%). Your plan levels moved %s by the same amount. Your "
            "broker's %s did not jump: keep the SL and TP you set in MetaTrader."
            % (word[:1].upper() + word[1:], "+" if pct > 0 else "", pct,
               "up" if ratio >= 1 else "down", inst["broker_symbol"]))


def rebase_record(pos, roll, now, cfg):
    """One fx-broker style "rebase" record (fx-broker 4.9)."""
    inst = instrument(cfg, pos["symbol"])
    word = ROLL_WORDS.get(roll.get("root"), str(roll.get("root")))
    pct = Decimal(repr((float(roll["ratio"]) - 1) * 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    plain = "%s rolled on Yahoo (%s%s%%). %s moved: stop %s, target %s." % (
        word[:1].upper() + word[1:], "+" if pct > 0 else "", pct, pos["id"],
        price_text(pos["stop_loss"], inst), price_text(pos["take_profit"], inst))
    return {"at": utc_iso(now), "fx_day": last_fx_day(now, cfg), "status": "rebase", "index": None,
            "fingerprint": None, "symbol": pos["symbol"], "side": pos.get("side"), "intent": "open",
            "lots": pos.get("lots"), "style": pos.get("style"), "max_loss_usd": None, "reply": None,
            "price": pos.get("stop_loss"), "broker_price": None, "result_usd": None,
            "position_id": pos["id"], "roll_id": roll["id"], "plain": plain,
            "paper": pos.get("paper", cfg.get("paper"))}


def save_positions(paths, cfg, moved):
    """Fresh read of portfolio.json; change only the rebased fields of moved positions."""
    doc = read_portfolio(paths["portfolio"], cfg)
    for p in doc.get("positions") or []:
        if p.get("id") in moved:
            new = moved[p["id"]]
            for f in POSITION_FIELDS + ["rolls_applied"]:
                if f in new:
                    p[f] = new[f]
    save_json_atomic(paths["portfolio"], doc)


def add_broker_records(paths, records):
    """Fresh read of state/broker.json, append, save."""
    path = paths["broker_state"]
    doc = load_json(path, None) or {"records": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        raise AlertError("%s is damaged at record 1. Nothing was changed." % path)
    doc["records"].extend(records)
    save_json_atomic(path, doc)


def rebase_all(paths, rolls, now, cfg, rules, portfolio):
    """4.1 step 4: positions, rules, sync, records. Changes rules and portfolio in
    memory, writes portfolio.json and state/broker.json. Returns (lines, changed ids)."""
    by_id = {r["id"]: r for r in rolls if r.get("id")}
    lines, changed, done_ids, moved, records = [], set(), [], {}, []
    positions = {p["id"]: p for p in portfolio.get("positions") or []}
    for p in list(positions.values()):
        inst = instrument(cfg, p["symbol"])
        new, ids = rebase(p, rolls, POSITION_FIELDS, inst=inst)
        if ids:
            p.update({f: new[f] for f in POSITION_FIELDS + ["rolls_applied"] if f in new})
            moved[p["id"]] = p
            for rid in ids:
                records.append(rebase_record(p, by_id[rid], now, cfg))
                done_ids.append((rid, inst))
    if moved:
        save_positions(paths, cfg, moved)
        add_broker_records(paths, records)
    for r in rules:
        if not r.get("active") or not r.get("symbol"):
            continue
        inst = instrument(cfg, r["symbol"])
        pos = positions.get(r.get("position_id"))
        if r["kind"] in ("stop", "target") and pos is not None:
            field = "stop_loss" if r["kind"] == "stop" else "take_profit"
            same_scale = (r.get("rolls_applied") or {}) == (pos.get("rolls_applied") or {})
            if r.get("level") != pos.get(field) or not same_scale:
                if same_scale and pos.get(field) is not None:
                    lines.append("Note: your %s %s alert now follows your moved %s: %s." % (
                        r["symbol"], r["kind"], r["kind"], price_text(pos[field], inst)))
                for rid in pos.get("rolls_applied") or {}:
                    if rid not in (r.get("rolls_applied") or {}) and rid in by_id:
                        done_ids.append((rid, inst))
                r["level"] = pos.get(field)
                r["rolls_applied"] = dict(pos.get("rolls_applied") or {})
                changed.add(r["id"])
            continue
        if r.get("level") is None or not inst_root(inst):
            continue
        new, ids = rebase(r, rolls, ["level"], inst=inst)
        if ids:
            r["level"], r["rolls_applied"] = new["level"], new["rolls_applied"]
            changed.add(r["id"])
            done_ids.extend((rid, inst) for rid in ids)
    said = []
    for rid, inst in done_ids:
        if rid not in said:
            said.append(rid)
            lines.insert(len(said) - 1, roll_line(by_id[rid], inst))
    return lines, changed


def retire(rules, portfolio):
    """4.1 step 5: rules of a position that is not open go off. Returns (lines, ids)."""
    open_ids = {p.get("id") for p in portfolio.get("positions") or []}
    off = {}
    for r in rules:
        pid = r.get("position_id")
        if r.get("active") and pid is not None and pid not in open_ids:
            r["active"] = False
            off.setdefault(pid, []).append(r["id"])
    lines = []
    for pid, ids in off.items():
        lines.append("Note: alert%s %s %s off: %s is closed." % (
            "s" if len(ids) > 1 else "", ", ".join(ids), "are" if len(ids) > 1 else "is", pid))
    return lines, {i for ids in off.values() for i in ids}



# ---------------------------------------------------------------- fx-alerts: time rules

FX_ROW = {"key": "FX", "hours": "fx", "data_hours": "yahoo_fx"}


def friday_end(at):
    """17:00 New York of the Friday of at (the New York date of at)."""
    return ny_to_utc(at_time(to_ny(at).date(), "17:00"))


def friday_key(rule, at):
    return "%s@%s" % (rule["id"], to_ny(at).date().isoformat())


def missed_line(at, cfg):
    d = to_ny(at).date()
    hhmm = parse_hhmm(cfg["swing"]["friday_cutoff_ny"], "swing.friday_cutoff_ny")
    return "Missed: the Friday %s New York check (%s %s)." % (
        clock_label(hhmm), DAY_NAMES[d.weekday()], d.strftime("%m-%d"))


def move_friday(rule, now, cfg, position, rang):
    """4.2: after a ring, or a missed window, move at_utc to the next Friday cutoff.
    Missed windows get a rang key with "missed". Returns the Missed lines."""
    lines = []
    cut = cfg["swing"]["friday_cutoff_ny"]
    at = parse_utc(rule["at_utc"])
    exit_by = parse_utc((position or {}).get("exit_by"))
    while True:
        key = friday_key(rule, at)
        if now >= friday_end(at) and key not in rang:
            rang[key] = {"at": utc_iso(now), "price": None, "fx_day": to_ny(at).date().isoformat(),
                         "missed": True}
            lines.append(missed_line(at, cfg))
        if key not in rang:
            break
        nxt = to_ny(at).date() + datetime.timedelta(days=7)
        at = ny_to_utc(at_time(nxt, cut))
        rule["at_utc"] = utc_iso(at)
        if exit_by is not None and at >= exit_by:
            rule["active"] = False
            break
    return lines


def time_hit(rule, now, today, cfg, positions, events):
    """4.2 time rules: the rang key when the rule rings now, else None. A "hit" dict
    for the words is put in rule["_hit"]."""
    kind = rule["kind"]
    mb = datetime.timedelta(minutes=rule.get("minutes_before") or 0)
    if kind in ("flat_by", "exit_by"):
        at = parse_utc(rule["at_utc"])
        if now >= at - mb:
            rule["_hit"] = {"at": at}
            return rule["id"]
        return None
    if kind == "friday_close":
        at = parse_utc(rule["at_utc"])
        if at - mb <= now < friday_end(at):
            rule["_hit"] = {"at": at}
            return friday_key(rule, at)
        return None
    if today is None:
        return None
    key = "%s@%s" % (rule["id"], today)
    if kind == "session_open":
        start = session_bounds(rule["session"], today)[0]
        if not start - mb <= now < start + datetime.timedelta(minutes=CLOCK_LATE_MIN):
            return None
        st = market_state(FX_ROW, now, cfg)
        if st["state"] == "holiday":
            rule["_holiday"] = holiday_note(today, FX_ROW, cfg) or st["note"]
            return None
        if st["state"] == "open" or (st["next_open"] is not None and st["next_open"] <= start):
            rule["_hit"] = {"at": start}
            return key
        return None
    if kind == "rollover":
        t = fx_day_bounds(today, "17:00 New York")[1]
        if not t - mb <= now < t:
            return None
        if full_fx_holiday(today, cfg):
            rule["_holiday"] = holiday_note(today, FX_ROW, cfg)
            return None
        rule["_hit"] = {"at": t}
        return key
    for e in next_events(events or [], [rule["symbol"]], now, 1, cfg["news"]["impacts"]):
        if e["at"] - mb <= now < e["at"]:
            rule["_hit"] = {"at": e["at"], "event": e}
            return key
    return None


def mins_until(at, now):
    return max(0, int(math.ceil((at - now).total_seconds() / 60.0)))



# ---------------------------------------------------------------- fx-alerts: alert lines

def short_pkt(at, now):
    """"3:15 PM PKT" on the same Karachi day as now, else "Wed 3:15 PM PKT"."""
    if to_pkt(at).date() == to_pkt(now).date():
        return "%s PKT" % clock_label(to_pkt(at))
    return pkt_label(at)


def spread_words(inst):
    """"the 0.1 pip spread" (FX) or "the 0.09 spread"."""
    s = _dec_clean(spread_price(inst))
    if inst.get("pip"):
        pips = (s / _dec_clean(inst["pip"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return "the %s pip spread" % format(pips.normalize(), "f")
    return "the %s spread" % price_text(float(s), inst)


def price_alert_lines(rule, hit, inst, now, lp):
    """6.1: the ALERT line of a price rule, then the side line and the price now."""
    kind, side = rule["kind"], rule.get("side")
    up = (kind == "above") or (kind == "target" and side == "buy") or (kind == "stop" and side == "sell")
    who = "ask" if side == "sell" and kind in ("stop", "target") else "bid"
    verb = ("rose to" if who == "ask" else "reached") if up else "fell to"
    if hit["interval"] == "live":
        where = "at %s (live price)" % short_pkt(hit["at"], now)
    else:
        size = "5-minute" if hit["interval"] == "5m" else "1-hour"
        where = "in the %s %s bar" % (short_pkt(hit["at"], now), size)
    head = "ALERT %s %s %s: the %s %s %s %s." % (rule["symbol"], kind, price_text(rule["level"], inst),
                                               who, verb, price_text(hit["price"], inst), where)
    if hit.get("held_at"):
        head = head[:-1] + " (held at %s for a possible roll)." % short_pkt(hit["held_at"], now)
    out = [head]
    if kind in ("stop", "target"):
        what = "stop loss" if kind == "stop" else "take profit"
        if side == "buy":
            out.append("  Your BUY's %s fires on the bid." % what)
        else:
            out.append("  Your SELL's %s fires on the ask (the Yahoo bid plus %s)." % (what, spread_words(inst)))
    if lp is not None:
        out.append("  %s %s now (%s, %s)" % (rule["symbol"], price_text(lp["bid"], inst),
                                             scale_note(inst), age_text(lp["age_min"])))
    return out


def time_alert_line(rule, now, cfg, pos):
    """6.1: the ALERT line of a time or clock rule."""
    kind, hit, sym = rule["kind"], rule["_hit"], rule.get("symbol")
    at = hit["at"]
    still = (" %s is still open." % pos["id"]) if pos else ""
    if kind == "flat_by":
        left = mins_until(at, now)
        when = ("%d min left" % left) if now < at else "it is past your flat-by time"
        what = (" Your day trade %s is still open." % pos["id"]) if pos else ""
        return "ALERT %s flat by %s: %s.%s" % (sym or "your day trades", when_text(at, date=False), when, what)
    if kind == "exit_by":
        days = cfg["swing"]["max_hold_days"]
        return "ALERT %s exit by %s: the last day of your %d-day hold.%s" % (sym, when_text(at), days, still)
    if kind == "friday_close":
        return ("ALERT %s Friday check: it is %s. The market shuts for the weekend at %s.%s "
                "On Monday the price can open past your stop." % (sym, both_times(now),
                                                                  both_times(friday_end(at)), still))
    if kind == "session_open":
        name = rule["session"]
        local = SESSION_CITY[name](at)
        city = "New York" if name == "new_york" else SESSION_LABELS[name]
        left = mins_until(at, now)
        soon = ("in %d min" % left) if now < at else "now"
        return "ALERT %s opens at %s PKT (%s %s) %s." % (SESSION_LABELS[name], clock_label(to_pkt(at)),
                                                         clock_label(local), city, soon)
    if kind == "rollover":
        return ("ALERT Rollover at %s PKT (%s) in %d min. Swap is booked then. Spreads get wide."
                % (clock_label(to_pkt(at)), ny_label(at), mins_until(at, now)))
    e = hit["event"]
    impact = event_impact(e, sym)
    return ("ALERT news for %s in %d min: %s at %s PKT (%s), %s impact. Prices can jump. "
            "Spreads can widen." % (sym, mins_until(at, now), event_label(e), clock_label(to_pkt(at)),
                                    ny_label(at), impact))



def calendar_status(info, offline, now):
    """The 03 9.7 status line of this week's calendar file."""
    fetched = parse_utc(info.get("fetched_at"))
    if fetched is None:
        return ("Calendar site could not be read. Only built-in dates are shown: central banks, "
                "EIA oil and gas, US jobs and COT. Other news (like CPI) is missing.")
    ago = age_text(info.get("age_min")).replace(" old", " ago")
    if info.get("ok") is False:
        return "Calendar not updated (the site said no). Using the copy from %s." % ago
    if offline:
        return "Offline: the saved calendar from %s is used." % short_date(to_pkt(fetched))
    return "Calendar: this week, updated %s." % ago


def alert_line(rule, hit, inst, now):
    """The first ALERT line of a price rule (6.1)."""
    return price_alert_lines(rule, hit, inst, now, None)[0]


def rang_entry(at, price, now, cfg):
    return {"at": utc_iso(at), "price": price, "fx_day": fx_day(at, cfg["day_end"]) or last_fx_day(now, cfg)}


def held_ring(rule, hold, inst, rolls, lp, now, in_window):
    """4.1 step 6, a held rule: ("ring", hit), ("drop", None) or ("keep", None)."""
    at = parse_utc(hold.get("at")) or now
    root = inst_root(inst)
    found = False
    for r in rolls:
        f = parse_utc(r.get("found_at")) or parse_utc(r.get("first_bar_new"))
        if r.get("root") == root and f is not None and f >= at:
            found = True
    if not (found or not in_window):
        return "keep", None
    if lp is not None and past(rule, lp["bid"], inst):
        price = ask_price(lp["bid"], inst) if rule.get("side") == "sell" and rule["kind"] in ("stop", "target") else lp["bid"]
        return "ring", {"at": lp["at"], "price": price, "interval": "live", "held_at": at}
    return "drop", None


def write_rules(path, cfg, rules, changed):
    """Fresh read of alerts.json; replace only the changed rules; save."""
    if not changed:
        return
    doc = read_alerts(path, cfg) or {"created_by": "fx-alerts", "rules": []}
    mine = {r["id"]: r for r in rules if r["id"] in changed}
    out = []
    for r in doc["rules"]:
        r = mine.get(r.get("id"), r)
        out.append({k: v for k, v in r.items() if not k.startswith("_")})
    doc["rules"] = out
    save_json_atomic(path, doc)



def refresh_symbols(watch, rules, portfolio, c):
    """4.1 step 2: refresh each watched symbol; returns (failed, notes)."""
    cfg, now, data = c["cfg"], c["now"], c["paths"]["data"]
    off = c["offline"] or not cfg.get("yahoo", True)
    failed, notes = [], []
    for sym in watch:
        inst = instrument(cfg, sym)
        day = any(p.get("symbol") == sym and p.get("style") == "day" for p in portfolio.get("positions") or [])
        day = day or any(r.get("active") and r.get("symbol") == sym and r["kind"] == "flat_by" for r in rules)
        try:
            refresh(data, inst["yahoo"], "1h", now, cfg, off)
            if day or os.path.exists(cache_file(data, inst["yahoo"], "5m")):
                refresh(data, inst["yahoo"], "5m", now, cfg, off)
            if inst.get("futures"):
                check_roll_forward(data, inst, cfg, now, off, every_run=inst_root(inst) == "BZ")
        except ValueError as exc:
            failed.append(sym)
            notes.append("Note: Yahoo failed for %s (%s). The saved prices were used." % (sym, exc))
    return failed, notes


def check_prices(rules, c, state, rolls, wanted):
    """4.1 step 6: ring the price rules. Returns (rings, notes, prices, no_data)."""
    cfg, now, data = c["cfg"], c["now"], c["paths"]["data"]
    rang, seen, held = state["rang"], state["seen"], state["held"]
    rings, notes, prices, no_data = [], [], {}, []
    syms = []
    for r in rules:
        if r.get("active") and r["kind"] in PRICE_KINDS and r["symbol"] not in syms:
            if not wanted or r["symbol"] in wanted:
                syms.append(r["symbol"])
    for sym in syms:
        inst = instrument(cfg, sym)
        mine = [r for r in rules if r.get("active") and r["kind"] in PRICE_KINDS and r["symbol"] == sym]
        lp = latest_price(data, inst, now, cfg)
        prices[sym] = lp
        seen_at = parse_utc(seen.get(sym))
        since_of = {r["id"]: max([x for x in (parse_utc(r.get("added_at")), seen_at) if x is not None] or [now])
                    for r in mine}
        bars = bars_since(data, inst, min(since_of.values()), now, rolls, cfg)
        if not bars and lp is None:
            no_data.append(sym)
            continue
        last_end = bar_end(bars[-1]) if bars else seen_at
        live = None
        if lp is not None and (last_end is None or lp["at"] > last_end):
            b = lp["bid"]
            live = {"start": lp["at"], "open": b, "high": b, "low": b, "close": b, "interval": "live"}
        root = inst_root(inst)
        in_window = bool(root and inst.get("futures") and
                         [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]])
        b1 = load_bars(data, inst["yahoo"], "1h", now, rolls=rolls, inst=inst, cfg=cfg) if in_window else []
        for r in mine:
            if r["id"] in rang:
                continue
            if r["id"] in held:
                what, hit = held_ring(r, held[r["id"]], inst, rolls, lp, now, in_window)
                if what == "keep":
                    notes.append("Note: " + held[r["id"]]["line"])
                    continue
                del held[r["id"]]
                if what == "ring":
                    rings.append((r, hit, inst))
                continue
            use = [b for b in bars if bar_end(b) > since_of[r["id"]]]
            lv = live if live is not None and live["start"] > since_of[r["id"]] else None
            hit = price_hit(r, use, lv, inst)
            if hit is None:
                continue
            if r["kind"] in ("stop", "target") and in_window:
                line = roll_guard(inst, b1, rolls, now, cfg)
                if line:
                    held[r["id"]] = {"at": utc_iso(hit["at"]), "line": line}
                    notes.append("Note: " + line)
                    continue
            rings.append((r, hit, inst))
        if bars and (seen_at is None or bar_end(bars[-1]) > seen_at):
            seen[sym] = utc_iso(bar_end(bars[-1]))
    for r, hit, inst in rings:
        rang[r["id"]] = rang_entry(hit["at"], hit["price"], now, cfg)
    return rings, notes, prices, no_data



def cmd_check(args):
    c = context(args)
    cfg, now, paths, today = c["cfg"], c["now"], c["paths"], c["today"]
    data = paths["data"]
    wanted = [symbol_key(s, cfg) for s in (args.symbols or "").split(",") if s.strip()]
    args.symbols_list = wanted
    doc = read_alerts(paths["alerts"], cfg)
    if doc is None or not doc["rules"]:
        msg = "No alert rules yet. Add one: python3 fx-alerts/alerts.py add --from-plan"
        if args.json:
            emit_json(json_doc("check", now, True, [msg], None, rang=[], failed=[]))
        else:
            print(msg)
        return 1
    state, notes = load_state(paths["alerts_state"], now)
    portfolio = read_portfolio(paths["portfolio"], cfg)
    rules, changed = doc["rules"], set()
    for r in rules:
        if r.get("added_at") is None:
            r["added_at"] = utc_iso(now - datetime.timedelta(minutes=60))
            changed.add(r["id"])
    watch = watch_symbols(rules, portfolio, args)
    failed, more = refresh_symbols(watch, rules, portfolio, c)
    notes += more
    rolls = rolls_by_now(data, now)
    lines, ch = rebase_all(paths, rolls, now, cfg, rules, portfolio)
    roll_lines, rule_notes = [x for x in lines if not x.startswith("Note:")], [x for x in lines if x.startswith("Note:")]
    changed |= ch
    lines, ch = retire(rules, portfolio)
    rule_notes += lines
    changed |= ch
    rings, more, prices, no_data = check_prices(rules, c, state, rolls, wanted)
    notes += more
    price_syms = sorted({r["symbol"] for r in rules if r.get("active") and r["kind"] in PRICE_KINDS
                         and (not wanted or r["symbol"] in wanted)})
    if price_syms and all(s in failed and s in no_data for s in price_syms):
        raise AlertError("Yahoo did not answer, and there are no saved prices for %s. Try again later."
                         % ", ".join(price_syms))
    timed, missed, events = [], [], None
    if any(r.get("active") and r["kind"] == "news" for r in rules):
        events = load_calendar(data, now, cfg, c["offline"])
        line = calendar_status(calendar_info(data, now), c["offline"], now)
        if not line.startswith("Calendar: this week"):
            notes.append(line)
    positions = {p["id"]: p for p in portfolio.get("positions") or []}
    for r in rules:
        if not r.get("active") or r["kind"] in PRICE_KINDS:
            continue
        if wanted and r["kind"] in AT_KINDS and r.get("symbol") and r["symbol"] not in wanted:
            continue
        pos = positions.get(r.get("position_id"))
        key = time_hit(r, now, today, cfg, positions, events)
        if r.get("_holiday") and r["_holiday"] not in notes:
            notes.append(r["_holiday"])
        if key is not None and key not in state["rang"]:
            state["rang"][key] = rang_entry(now, None, now, cfg)
            timed.append((r, pos))
        if r["kind"] == "friday_close":
            before = (r.get("at_utc"), r.get("active"))
            missed += move_friday(r, now, cfg, pos, state["rang"])
            if (r.get("at_utc"), r.get("active")) != before:
                changed.add(r["id"])
    write_rules(paths["alerts"], cfg, rules, changed)
    state["checked_at"] = utc_iso(now)
    save_json_atomic(paths["alerts_state"], state)
    return print_check(args, c, rules, watch, prices, rings, timed, roll_lines + rule_notes,
                       notes, missed, failed, positions)



def stale_note(sym, inst, lp, c):
    """4.1 step 8 for one watched symbol, or None."""
    if lp is None:
        path = cache_file(c["paths"]["data"], inst["yahoo"], "1h")
        return "Note: no Yahoo prices for %s yet (%s)." % (sym, path)
    if lp["age_min"] <= STALE_MINUTES[inst["price_source"]]:
        return None
    if market_state(inst, c["now"], c["cfg"], "data_hours")["state"] != "open":
        return None
    root = inst_root(inst)
    name = ROLL_WORDS.get(root) if root else sym
    return ("Note: the %s price is stale: no new Yahoo price since %s (%s). No alert can ring on it "
            "until prices come back. Check MetaTrader." % (name, pkt_label(lp["at"]), age_text(lp["age_min"])))


def print_check(args, c, rules, watch, prices, rings, timed, roll_notes, notes, missed, failed, positions):
    """4.1 step 10 and 6.1: print, then the exit code."""
    cfg, now = c["cfg"], c["now"]
    alert, out, rang_json, footer_pos, trade_ring = [], [], [], [], False
    for r, hit, inst in rings:
        lp = prices.get(r["symbol"])
        block = price_alert_lines(r, hit, inst, now, lp)
        alert.append(block[0])
        out += block
        rang_json.append({"id": r["id"], "symbol": r["symbol"], "kind": r["kind"], "level": r["level"],
                          "at": utc_iso(hit["at"]), "price": hit["price"], "line": block[0]})
        if r["kind"] in ("stop", "target"):
            trade_ring = True
            if r.get("position_id") in positions and r["position_id"] not in footer_pos:
                footer_pos.append(r["position_id"])
    flat_ring = False
    for r, pos in timed:
        line = time_alert_line(r, now, cfg, pos)
        alert.append(line)
        out.append(line)
        rang_json.append({"id": r["id"], "symbol": r.get("symbol"), "kind": r["kind"], "level": r.get("level"),
                          "at": utc_iso(now), "price": None, "line": line})
        if r["kind"] in AT_KINDS:
            trade_ring = True
            if pos and pos["id"] not in footer_pos:
                footer_pos.append(pos["id"])
        flat_ring = flat_ring or r["kind"] == "flat_by"
    watched = sum(1 for r in rules if r.get("active"))
    tail = []
    for sym in watch:
        inst = instrument(cfg, sym)
        lp = prices.get(sym) if sym in prices else latest_price(c["paths"]["data"], inst, now, cfg)
        if lp is not None:
            tail.append("  %s  %s  %s, %s" % (sym, price_text(lp["bid"], inst), scale_note(inst), age_text(lp["age_min"])))
        note = stale_note(sym, inst, lp, c)
        if note:
            notes.append(note)
    if alert:
        if trade_ring:
            out.append("An alert is information, not an order. Nothing was closed. Your SL and TP in "
                       "MetaTrader work on their own.")
            for pid in footer_pos:
                out.append("To record a close: python3 fx-broker/ticket.py close --position %s, then "
                           "fx-risk and your yes." % pid)
                out.append("If MetaTrader already closed it: python3 fx-broker/ticket.py close "
                           "--position %s --already --fill P" % pid)
        else:
            out.append("An alert is information, not an order.")
        if flat_ring:
            out.append("Next: python3 fx-broker/ticket.py flatten")
        out += roll_notes + notes + missed
    else:
        out = ["Nothing hit. %d rule%s watched at %s." % (watched, "" if watched == 1 else "s", both_times(now))]
        out += tail + roll_notes + notes + missed + ["Next check: python3 fx-alerts/alerts.py check"]
        alert = ["Nothing hit."]
    code = 0 if rings or timed else 1
    if args.json:
        emit_json(json_doc("check", now, True, roll_notes + notes + missed, None,
                           rang=rang_json, failed=failed))
    else:
        for line in (alert if args.quiet else out):
            print(line)
    return code



def newest_fetch_at(data, inst, now, replay):
    """The newest fetch time of the symbol's cache files (a replay: None)."""
    if replay:
        return None
    best = None
    for interval in ("5m", "1h"):
        f = last_fetch(load_cache(data, inst["yahoo"], interval))
        at = parse_utc((f or {}).get("at"))
        if at is not None and (best is None or at > best):
            best = at
    return best


def quote_symbols(args, c, portfolio, rules):
    """4.4 step 1: the symbols given, else positions, active rules, the plan."""
    cfg = c["cfg"]
    if args.symbols:
        out = []
        for s in args.symbols:
            for part in s.split(","):
                if part.strip() and symbol_key(part, cfg) not in out:
                    out.append(symbol_key(part, cfg))
        return out
    out = []
    for p in portfolio.get("positions") or []:
        if p.get("symbol") not in out:
            out.append(p["symbol"])
    for r in rules:
        if r.get("active") and r.get("symbol") and r["symbol"] not in out:
            out.append(r["symbol"])
    try:
        plan = load_json(c["paths"]["plan"], None)
    except ValueError:
        plan = None
    if isinstance(plan, dict) and plan.get("symbol") in cfg["instruments"] and plan["symbol"] not in out:
        out.append(plan["symbol"])
    return out


def cmd_quote(args):
    c = context(args)
    cfg, now, paths = c["cfg"], c["now"], c["paths"]
    data = paths["data"]
    portfolio = read_portfolio(paths["portfolio"], cfg)
    doc = read_alerts(paths["alerts"], cfg)
    syms = quote_symbols(args, c, portfolio, (doc or {}).get("rules") or [])
    if not syms:
        raise AlertError("Say which instruments, for example: quote XAUUSD EURUSD.")
    if args.broker_price is not None and len(syms) != 1:
        raise AlertError("--broker-price is for one instrument. Quote one symbol.")
    if args.fast:
        return quote_fast(args, c, syms)
    off = c["offline"] or not cfg.get("yahoo", True)
    replay = args.now is not None
    rolls = rolls_by_now(data, now)
    lines, notes, quotes, failed = [], [], {}, []
    for sym in syms:
        inst = instrument(cfg, sym)
        try:
            refresh(data, inst["yahoo"], "1h", now, cfg, off)
            day = any(p.get("symbol") == sym and p.get("style") == "day" for p in portfolio.get("positions") or [])
            if day or os.path.exists(cache_file(data, inst["yahoo"], "5m")):
                refresh(data, inst["yahoo"], "5m", now, cfg, off)
            if inst.get("futures"):
                check_roll_forward(data, inst, cfg, now, off, every_run=inst_root(inst) == "BZ")
        except ValueError as exc:
            notes.append("Note: Yahoo failed for %s (%s). The saved prices were used." % (sym, exc))
        lp = latest_price(data, inst, now, cfg)
        if lp is None:
            failed.append(sym)
            notes.append("No Yahoo price for %s yet (%s). Run without --offline once to get them."
                         % (sym, cache_file(data, inst["yahoo"], "1h")))
            continue
        fetched = newest_fetch_at(data, inst, now, replay) or lp["at"]
        quotes[sym] = {"yahoo": inst["yahoo"], "bid": lp["bid"], "at": utc_iso(lp["at"]),
                       "fetched_at": utc_iso(fetched), "age_min": round(lp["age_min"], 1),
                       "contract": lp["contract"], "source": "chart"}
        text = "  %s  %s  %s, %s" % (sym, price_text(lp["bid"], inst), scale_note(inst), age_text(lp["age_min"]))
        if lp.get("contract"):
            text += ". Contract: %s." % lp["contract"]
        lines.append(text)
    broker = None
    if args.broker_price is not None and syms[0] in quotes:
        sym = syms[0]
        inst = instrument(cfg, sym)
        lp = latest_price(data, inst, now, cfg)
        k = broker_k(inst, args.broker_price, lp["yahoo"])
        broker = {"bid": args.broker_price, "at": utc_iso(now), "yahoo_bid": lp["yahoo"],
                  "yahoo_at": utc_iso(lp["at"]), "k": round(k, 6)}
    if not quotes:
        raise AlertError(" ".join(n for n in notes if n.startswith("No Yahoo price")))
    pdoc = load_json(paths["prices"], None)
    if not isinstance(pdoc, dict):
        pdoc = {}
    if not isinstance(pdoc.get("quotes"), dict):
        pdoc["quotes"] = {}
    pdoc["quotes"].update(quotes)
    if broker is not None:
        if not isinstance(pdoc.get("broker"), dict):
            pdoc["broker"] = {}
        pdoc["broker"][syms[0]] = broker
    pdoc["created_by"] = "fx-alerts"
    pdoc["updated_at"] = utc_iso(now)
    pdoc.pop("created_at", None)
    save_json_atomic(paths["prices"], pdoc)
    for sym in quotes:
        rec = saved_broker_price(pdoc, sym, now, cfg, rolls)
        if rec is not None:
            inst = instrument(cfg, sym)
            age = data_age_min(parse_utc(rec["at"]), now)
            lines.append("  Your last MetaTrader price for %s: %s (%s, %s)." % (
                sym, price_text(rec["bid"], inst), broker_note(), age_text(age)))
    head = "Quotes at %s:" % both_times(now)
    if args.json:
        emit_json(json_doc("quote", now, True, notes, pdoc))
    elif args.quiet:
        print("\n".join(lines))
    else:
        print("\n".join([head] + lines + notes + ["Saved to %s." % paths["prices"]]))
    return 0


def quote_fast(args, c, syms):
    """4.4 step 2: one spark call per 20 symbols, printed only, never saved."""
    if c["offline"]:
        raise AlertError("--fast needs the network.")
    cfg = c["cfg"]
    insts = [instrument(cfg, s) for s in syms]
    got = spark([i["yahoo"] for i in insts])
    lines = ["Quick price (Yahoo, rounded, can be 7 min older than shown) at %s:" % both_times(c["now"])]
    for inst in insts:
        q = got.get(inst["yahoo"])
        if not q or q.get("close") is None:
            lines.append("  %s  no quick price" % inst["key"])
            continue
        age = data_age_min(q["at"], c["now"]) if q.get("at") else None
        lines.append("  %s  %s  %s, %s" % (inst["key"], price_text(q["close"], inst), scale_note(inst), age_text(age)))
    lines.append("Nothing was saved. For sizing and the risk check run quote without --fast.")
    if args.json:
        emit_json(json_doc("quote", c["now"], True, [], None, quick=lines[1:-1]))
    else:
        print("\n".join(lines))
    return 0



def span_text(minutes):
    """"4 h 19 min", "35 min"."""
    m = max(0, int(minutes))
    h, mm = divmod(m, 60)
    if h and mm:
        return "%d h %d min" % (h, mm)
    return "%d h" % h if h else "%d min" % mm


def members_text(keys, inst_kind):
    if len(keys) <= 3:
        return ", ".join(keys)
    word = "pairs" if inst_kind == "fx" else "instruments"
    return "%s, %s ... %d %s" % (keys[0], keys[1], len(keys), word)


def group_line(name, keys, inst, now, cfg):
    """4.5 step 2: one market group in one line."""
    st = market_state(inst, now, cfg)
    label = GROUP_LABELS.get(name, name)
    text = "  %s (%s): %s." % (label, members_text(keys, inst.get("kind")), st["state"])
    if st["state"] == "open" and st["next_close"] is not None:
        close = st["next_close"]
        if name in ("comex", "nymex"):
            text += " Daily break %s to %s PKT." % (clock_label(to_pkt(close)),
                                                     clock_label(to_pkt(close + datetime.timedelta(hours=1))))
        elif name == "fx":
            text += " The day ends at %s PKT (%s)." % (clock_label(to_pkt(close)), ny_label(close))
        else:
            text += " It closes at %s." % when_text(close, date=False)
    else:
        if st["note"]:
            text += " " + st["note"]
        if st["next_open"] is not None:
            text += " It opens at %s." % when_text(st["next_open"])
    return text, st


def session_lines(d, now):
    """4.5 step 4: the four sessions of FX day d and the London and New York overlap."""
    parts = []
    for name in ("sydney", "tokyo", "london", "new_york"):
        a, b = session_bounds(name, d)
        city = SESSION_CITY[name]
        if a <= now < b:
            st = "open now"
        elif now < a:
            st = "opens in %s" % span_text((a - now).total_seconds() // 60)
        else:
            st = "closed"
        parts.append("%s %s to %s PKT (%s to %s %s, %s)" % (
            SESSION_LABELS[name], clock_label(to_pkt(a)), clock_label(to_pkt(b)),
            clock_label(city(a)), clock_label(city(b)), SESSION_LABELS[name], st))
    la, lb = session_bounds("london", d)
    na, nb = session_bounds("new_york", d)
    lines = ["Sessions: " + parts[0] + ", " + parts[1] + ","]
    lines.append("  " + parts[2] + ", " + parts[3] + ".")
    lines.append("  London and New York overlap: %s to %s PKT." % (clock_label(to_pkt(max(la, na))),
                                                                  clock_label(to_pkt(min(lb, nb)))))
    return lines


def event_line(e, keys):
    """03 9.7: one event in one line."""
    impact = e.get("impact") or ""
    raised = [k for k in keys if k in (e.get("symbols") or []) and event_impact(e, k) != impact]
    if raised:
        impact = "%s for %s" % (event_impact(e, raised[0]), raised[0])
    text = "%s  %s  %s" % (when_text(e["at"]), impact, event_label(e))
    extra = []
    if e.get("forecast"):
        extra.append("forecast %s" % e["forecast"])
    if e.get("previous"):
        extra.append("last %s" % e["previous"])
    if extra:
        text += "  " + ", ".join(extra)
    if e.get("provisional"):
        text += " (expected date)"
    elif e.get("source") == "built-in":
        text += " (built-in table)"
    return text



def cmd_clock(args):
    c = context(args)
    cfg, now, data, today = c["cfg"], c["now"], c["paths"]["data"], c["today"]
    keys = [symbol_key(s, cfg) for s in (args.symbols or "").split(",") if s.strip()] or list(cfg["universe"])
    insts = [instrument(cfg, k) for k in keys]
    d = today or next_fx_day(last_fx_day(now, cfg), cfg)
    p = to_pkt(now)
    head = "Clock: %s %s PKT (%s). " % (short_date(p), clock_label(p), ny_label(now))
    head += ("FX day %s." % day_label(parse_fx_date(today))) if today else "Weekend: no FX day now."
    out, groups = [head], []
    for inst in insts:
        for g in groups:
            if g[0] == inst["hours"]:
                g[1].append(inst["key"])
                break
        else:
            groups.append((inst["hours"], [inst["key"]], inst))
    seen_data = set()
    for name, members, inst in groups:
        line, st = group_line(name, members, inst, now, cfg)
        out.append(line)
        dt = inst["data_hours"]
        if dt not in seen_data:
            seen_data.add(dt)
            ds = market_state(inst, now, cfg, "data_hours")
            if ds["state"] != st["state"]:
                text = "  Yahoo %s data: %s." % ("FX" if dt == "yahoo_fx" else "futures", ds["state"])
                if ds["note"]:
                    text += " " + ds["note"]
                if ds["next_open"] is not None and ds["state"] != "open":
                    text += " Prices come back at %s." % when_text(ds["next_open"])
                out.append(text)
    out += session_lines(d, now)
    kt = ["%s PKT (%s) %s" % (clock_label(to_pkt(u)), ny_label(u), label) for u, label in key_times(d, cfg)]
    out.append("Key times: " + "; ".join(kt) + ".")
    if odd_week(now):
        out.append("This week New York is only 4 hours behind London. US news comes 1 hour earlier for Europe.")
    days, x = [d], d
    for _ in range(7):
        x = next_fx_day(x, cfg)
        days.append(x)
    hol = []
    for _name, _members, inst in groups:
        for day in days:
            note = holiday_note(day, inst, cfg)
            if note and not note.startswith("This tool's holiday list"):
                note += HOLIDAY_TAIL
            if note and note not in hol:
                hol.append(note)
    out += ["Holiday: " + n for n in hol]
    events = load_calendar(data, now, cfg, c["offline"])
    nxt = next_events(events, keys, now, 5, cfg["news"]["impacts"])
    if nxt:
        out.append("Next big events: " + event_line(nxt[0], keys))
        out += ["  " + event_line(e, keys) for e in nxt[1:]]
    else:
        out.append("Next big events: none found this week.")
    out.append(calendar_status(calendar_info(data, now), c["offline"], now))
    if args.json:
        emit_json(json_doc("clock", now, True, [], None, lines=out))
    else:
        print("\n".join(out[:2] if args.quiet else out))
    return 0



def last_rang(rid, rang):
    """When the rule rang last (its own key or a "<id>@..." key), or None."""
    best = None
    for key, rec in rang.items():
        if key == rid or key.startswith(rid + "@"):
            at = parse_utc((rec or {}).get("at"))
            if at is not None and not (rec or {}).get("missed") and (best is None or at > best):
                best = at
    return best


def list_line(rule, cfg, rang):
    """6.5: "a1  on   XAUUSD stop 2,421.59 (BUY, XAUUSD-1)  not rung yet"."""
    text = "%-4s %-4s %s" % (rule["id"], "on" if rule.get("active") else "off", rule_text(rule, cfg))
    who = [side_word(rule["side"])] if rule.get("side") in ("buy", "sell") else []
    if rule.get("position_id"):
        who.append(rule["position_id"])
    if who:
        text += " (%s)" % ", ".join(who)
    at = last_rang(rule["id"], rang)
    return text + ("  rang %s" % pkt_label(at) if at else "  not rung yet")


def cmd_list(args):
    c = context(args)
    cfg, now, paths = c["cfg"], c["now"], c["paths"]
    doc = read_alerts(paths["alerts"], cfg)
    rules = (doc or {}).get("rules") or []
    state, notes = load_state(paths["alerts_state"], now)
    rid = args.off or args.on or args.reset
    if rid is not None:
        ids = [r["id"] for r in rules]
        if rid not in ids:
            if not ids:
                raise AlertError("There is no rule %s. There are no rules yet." % rid)
            raise AlertError("There is no rule %s. Rules: %s to %s." % (rid, ids[0], ids[-1]))
        if args.reset:
            for key in list(state["rang"]):
                if key == rid or key.startswith(rid + "@"):
                    del state["rang"][key]
            state["held"].pop(rid, None)
            save_json_atomic(paths["alerts_state"], state)
            notes.append("Reset %s: it can ring again." % rid)
        else:
            for r in rules:
                if r["id"] == rid:
                    r["active"] = bool(args.on)
            write_rules(paths["alerts"], cfg, rules, {rid})
            notes.append("Turned %s %s." % (rid, "on" if args.on else "off"))
    order = [r for r in rules if r.get("active")] + [r for r in rules if not r.get("active")]
    out = [list_line(r, cfg, state["rang"]) for r in order] or [
        "No alert rules yet. Add one: python3 fx-alerts/alerts.py add --from-plan"]
    if args.json:
        emit_json(json_doc("list", now, True, notes, None, rules=order))
    else:
        print("\n".join(notes + out))
    return 0


# ---------------------------------------------------------------- fx-alerts: command line

SUBCOMMANDS = ("check", "add", "quote", "clock", "list")


def build_parser():
    p = argparse.ArgumentParser(prog="alerts.py", description="Price and clock alerts for FX and "
                                "commodities. Each rule rings once. Never places an order.")
    sub = p.add_subparsers(dest="cmd")
    s = add_common(sub.add_parser("check", help="ring the rules that hit (the default)"))
    s.add_argument("--symbols", default=None, help="check only these instruments, like XAUUSD,EURUSD")
    s.set_defaults(func=cmd_check)
    s = add_common(sub.add_parser("add", help="add an alert rule"))
    s.add_argument("words", nargs="*", help="SYMBOL above|below|stop|target PRICE")
    s.add_argument("--from-plan", action="store_true", help="the plan's stop, target and times")
    s.add_argument("--side", choices=("buy", "sell"), default=None, help="needed for stop and target")
    s.add_argument("--session", choices=tuple(SESSIONS), default=None, help="a session open alert")
    s.add_argument("--rollover", action="store_true", help="the 5 PM New York rollover")
    s.add_argument("--news", metavar="SYMBOL", default=None, help="big news for this instrument")
    s.add_argument("--flat-by", metavar="HH:MM", default=None, help="a flat-by reminder today (Karachi time)")
    s.add_argument("--minutes-before", type=int, default=None, help="how early a time rule rings (0 to 1440)")
    s.add_argument("--note", default="", help="a note stored in the rule")
    s.set_defaults(func=cmd_add)
    s = add_common(sub.add_parser("quote", help="Yahoo prices, saved to fx/prices.json"))
    s.add_argument("symbols", nargs="*", help="the instruments to quote")
    s.add_argument("--fast", action="store_true", help="one quick Yahoo call, rounded, not saved")
    s.add_argument("--broker-price", type=float, default=None, metavar="P",
                   help="your broker's BID now, for the one symbol given")
    s.set_defaults(func=cmd_quote)
    s = add_common(sub.add_parser("clock", help="market hours, sessions and big events"))
    s.add_argument("--symbols", default=None, help="the instruments to show")
    s.set_defaults(func=cmd_clock)
    s = add_common(sub.add_parser("list", help="show the rules; turn one off or on"))
    g = s.add_mutually_exclusive_group()
    g.add_argument("--off", metavar="ID", default=None, help="turn a rule off")
    g.add_argument("--on", metavar="ID", default=None, help="turn a rule on")
    g.add_argument("--reset", metavar="ID", default=None, help="let a rule ring again")
    s.set_defaults(func=cmd_list)
    return p


def main(argv):
    argv = list(argv)
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["check"] + argv
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        return fail(exc, TOOL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
