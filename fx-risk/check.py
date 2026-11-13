#!/usr/bin/env python3
"""fx-risk: the Risk Manager. Checks every order in fx/orders.json against your limits.

    python3 fx-risk/check.py                      # check fx/orders.json, write fx/state/risk.json
    python3 fx-risk/check.py --json               # the verdicts as one JSON object
    python3 fx-risk/check.py --quiet              # verdict lines only
    python3 fx-risk/check.py --offline            # cache only, no Yahoo, no calendar download
    python3 fx-risk/check.py --config fx2/config.json
    python3 fx-risk/check.py --offline --now "2026-07-17 09:08"

Each order gets PASS or BLOCK with plain reasons. An open must pass every rule R1 to
R31. A close is checked only for sense (C1 to C3): getting out is never blocked by a
limit. The verdicts go to fx/state/risk.json with each order's fingerprint and the
check time. fx-broker shows a ticket READY only for a PASS of that exact order, at
most 5 minutes old. A PASS is a rules check, not a yes. Nothing here places an order.

Exit codes: 0 every order PASS, 1 any order BLOCK, 2 an error.
Standard library only, Python 3.8.
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


# ---- copied from tests/fx_core.py: do not edit (tests/check_copies.py checks it)
# Copy together (the names each copied helper needs are copied too): the helpers of
# the groups clock, dst, fx day and sessions, market hours, calendar tables, economic
# calendar, yahoo client, cache, bars, rolls, instruments and rounding, money, the
# setups constants (CORRELATION_GROUPS, group_of, currency_legs, MIN_DAY_MINUTES_LEFT),
# files and config, and words that this script uses, with all they use in turn.

ORDER_FIELDS = ("symbol", "side", "intent", "lots", "type", "entry", "stop_loss",
                "take_profit", "expiry", "style")


FRESH_CHECK_MINUTES = 5


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


def fingerprint(order):
    """The order's hash (D18, the earlier pack's rule unchanged): the ORDER_FIELDS that are
    present and not null, as sorted compact JSON, sha256, the first 16 hex characters."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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


def check_close_order(order, n, path):
    """A close order (intent "close"): entry, stop_loss, take_profit and expiry null,
    type market, position_id set. n is the order's number (from 1)."""
    ok = (all(order.get(k) is None for k in ("entry", "stop_loss", "take_profit", "expiry"))
          and order.get("type") == "market" and bool(order.get("position_id")))
    if not ok:
        raise ValueError("%s: order %s closes a position. Leave entry, stop_loss, "
                         "take_profit and expiry empty (null). Use type market. Set "
                         "position_id." % (path, n))
    return order


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


COARSE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}


COARSE_MIN_STOP_PIPS = 10


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


RATE_PAIRS = {"EUR": ("EURUSD", "mul"), "GBP": ("GBPUSD", "mul"), "AUD": ("AUDUSD", "mul"),
              "NZD": ("NZDUSD", "mul"), "JPY": ("USDJPY", "div"), "CHF": ("USDCHF", "div"),
              "CAD": ("USDCAD", "div")}


def _cents(d):
    """A Decimal amount of money, half up to cents, as a float (never -0.0)."""
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


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


def _points_word(n):
    """Whole points for messages: "1 point", "1,913 points"."""
    return "%s %s" % (format(n, ","), "point" if n == 1 else "points")


def stops_problems(side, entry_type, entry, stop_loss, take_profit, bid_now, inst):
    """The MetaTrader stops-level and side checks (R2 7e) as plain lines; empty when all
    is well. Market orders test against the bid and ask now, pending ones against entry."""
    side_word(side)
    if entry_type not in ("market", "limit", "stop"):
        raise ValueError('The order type must be "market", "limit" or "stop".')   # (check) words
    bid = _price_dec(bid_now)
    ask = _price_dec(ask_of(bid, inst))
    point = _dec_clean(inst["point"])
    lvl_pts = int(_dec_clean(inst.get("stops_level_points") or 0))
    lvl = lvl_pts * point
    out = []

    def check(dist, what):
        if dist <= 0:
            out.append("Your %s is on the wrong side of the price now." % what)
        elif dist < lvl:
            out.append("Your %s is too close. MetaTrader needs it at least %s (%s) from the price. "
                       "It is %s away." % (what, _points_word(lvl_pts), price_text(lvl, inst),
                                           _points_word(abs(to_points(dist, inst)))))

    buy = side == "buy"
    if entry_type == "market":
        ref_sl, ref_tp = (bid, bid) if buy else (ask, ask)
    else:
        p = _price_dec(entry)
        ref_sl = ref_tp = p
        dist = {("buy", "limit"): ask - p, ("sell", "limit"): p - bid,
                ("buy", "stop"): p - ask, ("sell", "stop"): bid - p}[(side, entry_type)]
        above = (side, entry_type) in (("buy", "stop"), ("sell", "limit"))
        if dist <= 0:
            out.append("A %s %s order must be %s the price now (%s %s)." % (
                side, entry_type, "above" if above else "below", "ask" if buy else "bid",
                price_text(ask if buy else bid, inst)))
        elif dist < lvl:   # (check) words
            out.append("Your order price is too close. MetaTrader needs it at least %s (%s) from "
                       "the price. It is %s away." % (_points_word(lvl_pts), price_text(lvl, inst),
                                                      _points_word(abs(to_points(dist, inst)))))
    if stop_loss is not None:
        s = _price_dec(stop_loss)
        check(ref_sl - s if buy else s - ref_sl, "stop")
    if take_profit is not None:
        t = _price_dec(take_profit)
        check(t - ref_tp if buy else ref_tp - t, "target")
    return out


MIN_DAY_MINUTES_LEFT = 15


CORRELATION_GROUPS = {
    "usd_majors": ("EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"),
    "jpy": ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"),
    "eur_crosses": ("EURGBP", "EURCHF"),
    "metals": ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"),
    "industrial": ("COPPER",),
    "energy": ("USOIL", "UKOIL"),   # WTI and Brent: one oil market (check: not measured)
    "natgas": ("NATGAS",),
}


def group_of(symbol, cfg):
    """The correlation group of a key: its "group" column when set, else the group of
    CORRELATION_GROUPS that holds it, else the key itself (a group of one)."""
    row = ((cfg or {}).get("instruments") or {}).get(symbol) or {}
    if row.get("group"):
        return row["group"]
    for name, keys in CORRELATION_GROUPS.items():
        if symbol in keys:
            return name
    return symbol


def currency_legs(symbol, side, inst):
    """The currency bets of one trade, +1 long and -1 short: fx {base: s, quote: -s},
    metal {base: s, "USD": -s}, energy {base: s} only; s = 1 for a buy, -1 for a sell."""
    if side not in ("buy", "sell"):
        raise ValueError('A side is "buy" or "sell", not "%s".' % (side,))
    s = 1 if side == "buy" else -1
    kind = inst.get("kind")
    if kind == "fx":
        return {inst["base"]: s, inst["quote"]: -s}
    if kind == "metal":
        return {inst["base"]: s, "USD": -s}
    if kind == "energy":
        return {inst["base"]: s}
    raise ValueError('%s: kind must be "fx", "metal" or "energy", not "%s".' % (symbol, kind))


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


# ---- end of the copied helpers


# ---------------------------------------------------------------- fx-risk's own helpers

TOOL = "fx-risk"
SCRIPT = "check.py"
TAKEN = ("yes", "placed", "filled", "closed")   # an open counts as taken (05 5.1)
WIDTH = 78             # the line width of fx-news and fx-risk
POSITION_FIELDS = ("entry", "fill_price", "stop_loss", "take_profit", "stop_at_open")
# Plain names of the correlation groups (R18): (name, why they count together).
GROUP_WORDS = {
    "usd_majors": ("US dollar pairs", "The US dollar pairs move together."),
    "jpy": ("Yen pairs", "The yen pairs move together."),
    "eur_crosses": ("Euro crosses", "The euro crosses move together."),
    "metals": ("Metals", "Gold, silver, platinum and palladium move together."),
    "industrial": ("Copper", "Copper trades count together."),
    "energy": ("Oil", "WTI and Brent are the same oil market."),
    "natgas": ("Gas", "Gas trades count together."),
}
# Plain names of the currency legs (R19).
CCY_NAMES = {
    "USD": "the US dollar", "EUR": "the euro", "GBP": "the pound", "JPY": "the yen",
    "CHF": "the Swiss franc", "AUD": "the Australian dollar", "CAD": "the Canadian dollar",
    "NZD": "the New Zealand dollar", "XAU": "gold", "XAG": "silver", "XPT": "platinum",
    "XPD": "palladium", "HG": "copper", "WTI": "WTI oil", "BRENT": "Brent oil",
    "NG": "gas",
}
ORDER_TYPES = ("market", "stop", "limit")


def pct_text(x):
    """A percent for people: "1.0%", "0.5%", "0.25%": at least 1 decimal, at most 2."""
    d = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    text = format(d, "f")
    if "." not in text:
        text += ".0"
    return text + "%"


def num_text(x):
    """A plain number with no trailing zeros: 30 -> "30", 1.5 -> "1.5", 0.01 -> "0.01"."""
    d = Decimal(str(x)).normalize()
    text = format(d, "f")
    return text


def dec(x):
    """x as an exact Decimal from its printed form (floats keep their short text)."""
    return Decimal(str(x))


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


def name_word(inst, capital=False):
    """The instrument's name in a sentence: "gold" / "Gold" for futures, the key for FX."""
    if inst.get("price_source") == "futures":
        name = str(inst.get("name") or inst.get("key") or "")
        if name and not (len(name) > 1 and name[1].isupper()):
            name = (name[0].upper() if capital else name[0].lower()) + name[1:]
        return name
    return inst.get("key") or ""


def closed_words(state, inst):
    """The R22 why: "CME daily break", "Brent daily break", "the 5:00 PM New York
    rollover", "weekend", "holiday" or "closed"."""
    s = state.get("state")
    if s == "break":
        hours = inst.get("hours")
        if hours in ("comex", "nymex"):
            return "CME daily break"
        if hours == "ice_brent":
            return "Brent daily break"
        return "the 5:00 PM New York rollover"
    if s in ("weekend", "holiday"):
        return s
    return "closed"


def wrap_line(text, first="  ", rest="  "):
    """One printed line, wrapped at about 78 characters, later parts indented."""
    return textwrap.wrap(text, width=WIDTH, initial_indent=first, subsequent_indent=rest,
                         break_long_words=False, break_on_hyphens=False) or [first.rstrip()]


def strip_id(text):
    """ "R10 Max loss ..." -> "Max loss ..." (the words mode hides the ids)."""
    head, _, tail = text.partition(" ")
    return tail if re.match(r"^(R\d+|C\d|W-[a-z]+)$", head) else text


def signed_pct(ratio):
    """A roll ratio as a signed percent, 2 decimals: 1.008999 -> "+0.90%"."""
    d = ((dec(ratio) - 1) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("+" if d > 0 else "") + format(d, "f") + "%"


# ---------------------------------------------------------------- the files fx-risk reads

def read_orders(path):
    """orders.json with the file-level checks (exit 2 on a fault of the whole file)."""
    doc = load_doc(path, "orders", "fx-budget")
    orders = check_objects(doc, "orders", path)
    if not orders:
        raise ValueError("%s has no orders. Run fx-budget with --write first." % path)
    created = check_utc(doc, "created_at", path)
    return doc, orders, created


def read_portfolio(path, cfg):
    """portfolio.json with the 01 5.2 checks; an empty one when the file is missing."""
    doc = load_doc(path, "portfolio", "fx-broker", required=False)
    if doc is None:
        return {"account": {}, "positions": [], "closed": []}

    def bad(what):
        return ValueError("%s: %s. Fix it by hand or restore a backup." % (path, what))
    account = doc.get("account") or {}
    if not isinstance(account, dict):
        raise bad('"account" must be an object')
    if account.get("currency") not in (None, "USD"):
        raise bad('the account currency must be USD, not %s' % account.get("currency"))
    for key in ("positions", "closed"):
        items = doc.get(key, [])
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            raise bad('"%s" must be a list of objects' % key)
    seen = set()
    for pos in doc.get("positions", []):
        if pos.get("symbol") not in (cfg.get("instruments") or {}):
            raise bad('position %s has the key "%s", which is not in your instruments'
                      % (pos.get("id"), pos.get("symbol")))
        if pos.get("id") in seen:
            raise bad('two positions have the id %s' % pos.get("id"))
        seen.add(pos.get("id"))
        if pos.get("side") not in ("buy", "sell"):
            raise bad('position %s has no side "buy" or "sell"' % pos.get("id"))
        for k in ("lots", "entry", "max_loss_usd"):
            v = pos.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise bad('position %s has no number "%s"' % (pos.get("id"), k))
    return {"account": account, "positions": list(doc.get("positions", [])),
            "closed": list(doc.get("closed", []))}


def read_ledger(path):
    """The records of state/broker.json; a broken file is an error, never rebuilt."""
    doc = load_json(path)
    if doc is None:
        return []
    records = doc.get("records") if isinstance(doc, dict) else None
    if not isinstance(records, list):
        raise ValueError("%s is damaged at record 1. Nothing was changed." % path)
    for n, rec in enumerate(records, 1):
        if not isinstance(rec, dict) or not rec.get("at") or not rec.get("status"):
            raise ValueError("%s is damaged at record %d. Nothing was changed." % (path, n))
    return records


def read_rolls(data_dir, now, replay):
    """The rolls of rolls.json (in a replay only those found by now), with the ratio check."""
    rolls = load_rolls(data_dir)
    out = []
    for r in rolls:
        ratio = r.get("ratio")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0.33 <= ratio <= 3:
            raise ValueError("%s: roll %s has a ratio that cannot be right (%s). Fix or delete that roll."
                             % (rolls_path(data_dir), r.get("id"), ratio))
        found = parse_utc(r.get("found_at"))
        if replay and (found is None or found > now):
            continue
        out.append(r)
    return out


def limit_usd(balance, pct, usd):
    """min(balance x pct / 100, usd or no cap), half up to cents, as a Decimal."""
    lim = dec(balance) * dec(pct) / 100
    if usd is not None:
        lim = min(lim, dec(usd))
    return lim.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- the account now

def ledger_today(records, portfolio, today, day_end):
    """Step 8 (05 5.1, 5.4): {taken_today: fingerprint -> max loss, yes_today: set,
    pending: set, used_today: Decimal}. Records are only added, so the last one of a
    fingerprint is its newest (check)."""
    by_fp = {}
    for rec in records:
        if rec.get("intent", "open") != "open" or not rec.get("fingerprint"):
            continue
        by_fp.setdefault(rec["fingerprint"], []).append(rec)
    held = set(p.get("fingerprint") for p in portfolio["positions"])
    closed = dict((c.get("id"), c) for c in portfolio["closed"])
    taken, yes_today, pending = {}, set(), set()
    used = Decimal(0)
    for fp, recs in by_fp.items():
        newest = recs[-1]
        today_recs = [r for r in recs if r.get("fx_day") == today]
        if any(r.get("status") == "yes" for r in today_recs):
            yes_today.add(fp)
        if fp not in held and (newest.get("status") == "placed"
                               or (newest.get("status") == "yes" and newest.get("fx_day") == today)):
            pending.add(fp)
        if today_recs and newest.get("status") in TAKEN:
            losses = [r.get("max_loss_usd") for r in recs if isinstance(r.get("max_loss_usd"), (int, float))]
            ml = dec(losses[-1]) if losses else Decimal(0)
            taken[fp] = ml
            loss = Decimal(0)
            for r in recs:
                if r.get("status") == "filled" and r.get("position_id") in closed:
                    res = closed[r["position_id"]].get("result_usd")
                    if isinstance(res, (int, float)) and res < 0:
                        loss = max(loss, -dec(res))
            used += max(ml, loss)
    for c in portfolio["closed"]:
        res = c.get("result_usd")
        if c.get("fx_day_closed") != today or not isinstance(res, (int, float)) or res >= 0:
            continue
        opened = parse_utc(c.get("opened_at"))
        if opened is None or fx_day(opened, day_end) != today:
            used += -dec(res)
    return {"taken_today": taken, "yes_today": yes_today, "pending": pending, "used_today": used}


def positions_now(portfolio, cfg, rates, data_dir, now, rolls, replay, paths=None):
    """Step 7: each open position (a rebased COPY) at its latest price.
    Returns (rows, margin_open, equity_pl, notes, roll_ids)."""
    rows, notes, roll_ids = [], [], []
    margin_open, pl_sum = Decimal(0), Decimal(0)
    for pos in portfolio["positions"]:
        pinst = instrument(cfg, pos["symbol"])
        p, ids = rebase(pos, rolls, POSITION_FIELDS, inst=pinst)
        for i in ids:
            if i not in roll_ids:
                roll_ids.append(i)
        q = fresh_quote(data_dir, paths or fx_paths("fx", data_dir), pinst, now, cfg, replay)
        fresh = q is not None and q.get("bid") is not None and q["age_min"] <= STALE_MINUTES[pinst["price_source"]]
        ml = dec(p["max_loss_usd"])
        pl = -ml
        if fresh:
            try:
                pl = dec(position_pl_usd(p, q["bid"], pinst, rates, cfg))
            except ValueError:
                pl = -ml
        price = (ask_of(q["bid"], pinst) if p["side"] == "buy" else q["bid"]) if fresh else p["entry"]
        try:
            m = dec(margin_usd(pinst, p["lots"], price, p["side"], cfg, rates))
        except ValueError:
            m = ml   # (check) a safe stand-in when no rate counts the margin
            notes.append("No USD rate for %s's margin. Its margin is a guess." % pos["symbol"])
        margin_open += m
        pl_sum += pl
        rows.append({"id": pos.get("id"), "symbol": pos["symbol"], "side": p["side"], "lots": p["lots"],
                     "max_loss": ml, "margin": m, "group": group_of(pos["symbol"], cfg),
                     "legs": currency_legs(pos["symbol"], p["side"], pinst)})
    return rows, margin_open, pl_sum, notes, roll_ids


# ---------------------------------------------------------------- one order

def new_verdict(n, order, fp):
    """The verdict dict of section 5, empty."""
    return {"index": n, "fingerprint": fp, "verdict": "PASS", "symbol": order.get("symbol"),
            "side": order.get("side"), "intent": order.get("intent"), "lots": order.get("lots"),
            "max_loss_usd": None, "margin_usd": None, "reasons": [], "warnings": [], "_lines": []}


def check_close(n, order, portfolio, cfg, now, path="fx/orders.json"):
    """Step 10: a close is checked only for sense (C1 to C3), never for a limit."""
    v = new_verdict(n, order, fingerprint(order))
    v["max_loss_usd"], v["margin_usd"] = 0.0, 0.0
    try:
        check_close_order(order, n, path)
        check_side(order.get("side"), path)
        check_lots(order.get("lots"), path)
        check_symbol(order.get("symbol"), cfg, path)
        check_style(order.get("style") or "swing", path)
    except ValueError as exc:
        v["reasons"].append("C1 " + str(exc))
        v["verdict"] = "BLOCK"
        return v
    key = order["symbol"]
    inst = instrument(cfg, key)
    pid = order.get("position_id")
    pos = [p for p in portfolio["positions"] if p.get("id") == pid]
    if not pos:
        v["reasons"].append("C2 There is no open position %s." % pid)
    else:
        p = pos[0]
        lots, have = dec(order["lots"]), dec(p["lots"])
        step, min_lot = dec(inst["lot_step"]), dec(inst["min_lot"])
        if p["symbol"] != key:
            v["reasons"].append("C3 The position is %s; this close says %s." % (p["symbol"], key))
        if p["side"] != order["side"]:
            v["reasons"].append("C3 The position is a %s of %s; this close says %s."
                                % (p["side"], lots_text(p["lots"]), order["side"]))
        if lots > have:
            v["reasons"].append("C3 The position has %s; this close says %s."
                                % (lots_text(p["lots"]), lots_text(order["lots"])))
        elif lots % step != 0:
            v["reasons"].append("C3 %s is not a whole number of the lot step %s."
                                % (lots_text(order["lots"]), num_text(inst["lot_step"])))
        elif 0 < have - lots < min_lot:
            v["reasons"].append("C3 After this close %s would be left, under the minimum lot."
                                % lots_text(float(have - lots)))
    if not cfg.get("paper", True):
        v["warnings"].append("W-live LIVE account: this is real money.")
    state = market_state(inst, now, cfg)
    if state["state"] != "open":
        until = (" until %s" % pkt_label(state["next_open"])) if state.get("next_open") else ""
        v["warnings"].append("W-closed The market is closed. MetaTrader cannot close it%s." % until)
    if v["reasons"]:
        v["verdict"] = "BLOCK"
    if pos and not v["reasons"]:
        v["_head"] = "closes your %s position %s." % (side_word(pos[0]["side"]), pid)
    return v


def open_problem(order, n, path):
    """R1: the reader checks of one open order (01 1.3, 5.2); the message or None."""
    where = "%s: order %s" % (path, n)
    try:
        if order.get("intent") not in ("open", "close"):
            raise ValueError('%s: intent must be "open" or "close", not "%s"' % (where, order.get("intent")))
        check_side(order.get("side"), where)
        check_style(order.get("style"), where)
        if order.get("type") not in ORDER_TYPES:
            raise ValueError('%s: type must be "market", "stop" or "limit", not "%s"' % (where, order.get("type")))
        check_lots(order.get("lots"), where)
        check_price(order, "entry", where)
        for key in ("stop_loss", "take_profit"):
            if order.get(key) is not None:
                check_price(order, key, where)
        exp = order.get("expiry")
        if exp not in ("gtc", "today"):
            try:
                check_utc(order, "expiry", where)
            except ValueError:
                raise ValueError('%s: expiry must be "gtc", "today" or a UTC time like '
                                 '2026-11-11T08:40:00Z, not %s' % (where, exp))
    except ValueError as exc:
        return str(exc)
    return None


def order_nights(order, inst, now, cfg):
    """plan.nights_est; a day order without it 0; a swing order without it the
    rollovers from now to plan.exit_by (0 when that is missing too)."""
    plan = order.get("plan") or {}
    n = plan.get("nights_est")
    if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
        return n
    if order.get("style") == "day":
        return 0
    end = parse_utc(plan.get("exit_by"))
    return swap_nights(now, end, inst, cfg) if end is not None else 0


def is_rate_error(exc):
    """True for the usd_per_unit ValueErrors (a missing or old USD rate)."""
    text = str(exc)
    return "USD rate for" in text


def measure(order, inst, ctx):
    """The measuring entry e (05 5.1), the fresh price, max loss and margin of one open.
    Returns a dict: q, bid_now, ask_now, e, nights, max_loss, margin, rate_err, no_stop."""
    cfg, now = ctx["cfg"], ctx["now"]
    q = fresh_quote(ctx["data_dir"], ctx["paths"], inst, now, cfg, ctx["replay"])
    fresh = q is not None and q.get("bid") is not None and q["age_min"] <= STALE_MINUTES[inst["price_source"]]
    bid_now = q["bid"] if fresh else None
    ask_now = ask_of(bid_now, inst) if fresh else None
    entry = order["entry"]
    e = entry
    if order["type"] == "market" and fresh:
        e = max(dec(entry), dec(ask_now)) if order["side"] == "buy" else min(dec(entry), dec(bid_now))
        e = float(e)
    out = {"q": q, "bid_now": bid_now, "ask_now": ask_now, "e": e, "rate_err": None,
           "max_loss": None, "margin": None, "no_stop": order.get("stop_loss") is None}
    out["nights"] = order_nights(order, inst, now, cfg)
    try:
        out["margin"] = dec(margin_usd(inst, order["lots"], e, order["side"], cfg, ctx["rates"]))
    except ValueError as exc:
        if is_rate_error(exc):
            out["rate_err"] = str(exc)
    if out["no_stop"]:
        if not cfg["risk"].get("require_stop", True):
            out["max_loss"] = ctx["L_trade"]
    else:
        try:
            out["max_loss"] = dec(max_loss_usd(inst, order["side"], order["lots"], e, order["stop_loss"],
                                               out["nights"], ctx["rates"], cfg))
        except ValueError as exc:
            if is_rate_error(exc):
                out["rate_err"] = out["rate_err"] or str(exc)
    if out["rate_err"]:
        out["max_loss"], out["margin"] = None, None
    return out


def rules_order(order, inst, m, ctx):
    """R5 to R11: the order's own prices, lots and money. A list of reasons."""
    cfg, risk = ctx["cfg"], ctx["cfg"]["risk"]
    side, kind = order["side"], order["type"]
    stop, tp, e = order.get("stop_loss"), order.get("take_profit"), m["e"]
    bid, ask = m["bid_now"], m["ask_now"]
    out = []
    if stop is None and risk.get("require_stop", True):
        out.append("R5 No stop loss. Every trade needs one.")
    if tp is None:
        out.append("R5 No take profit. Every ticket has a stop and a target.")
    de = dec(e)
    ok_stop = stop is None or (dec(stop) < de if side == "buy" else dec(stop) > de)
    ok_tp = tp is None or (dec(tp) > de if side == "buy" else dec(tp) < de)
    if not (ok_stop and ok_tp):
        out.append("R6 A buy needs the stop under the entry and the target above it." if side == "buy"
                   else "R6 A sell needs the stop above the entry and the target under it.")
    if stop is not None and bid is not None:
        if (side == "buy" and dec(bid) <= dec(stop)) or (side == "sell" and dec(ask) >= dec(stop)):
            out.append("R6 The price is already at the stop.")
    if kind in ("stop", "limit") and bid is not None:
        p = dec(order["entry"])
        wrong = {("buy", "stop"): p <= dec(ask), ("buy", "limit"): p >= dec(ask),
                 ("sell", "stop"): p >= dec(bid), ("sell", "limit"): p <= dec(bid)}[(side, kind)]
        if wrong:
            above = (side, kind) in (("buy", "stop"), ("sell", "limit"))
            out.append("R7 A %s %s must be %s the price now (%s %s). Use a market order, or plan again."
                       % (side, kind, "above" if above else "below", "ask" if side == "buy" else "bid",
                          price_text(ask if side == "buy" else bid, inst)))
    if bid is not None:
        try:
            lines = stops_problems(side, kind, order["entry"], stop, tp, bid, inst)
        except ValueError:
            lines = []
        for line in lines:
            if not re.match(r"^A (buy|sell) (stop|limit) order must be", line):
                out.append("R8 " + line)
    lots = dec(order["lots"])
    if lots < dec(inst["min_lot"]):
        out.append("R9 %s is under the minimum lot %s." % (lots_text(order["lots"]), num_text(inst["min_lot"])))
    elif lots > dec(inst["max_lot"]):
        out.append("R9 %s is over the maximum lot %s." % (lots_text(order["lots"]), num_text(inst["max_lot"])))
    elif lots % dec(inst["lot_step"]) != 0:
        out.append("R9 %s is not a whole number of the lot step %s."
                   % (lots_text(order["lots"]), num_text(inst["lot_step"])))
    ml = m["max_loss"]
    if ml is not None and not m["no_stop"] and ml > ctx["L_trade"]:
        out.append("R10 Max loss %s is over your limit of %s a trade (%s), costs included."
                   % (money(ml), money(ctx["L_trade"]), limit_words(ctx)))
    if stop is not None and tp is not None and de != dec(stop):
        rr = abs(dec(tp) - de) / abs(de - dec(stop))
        if rr < dec(risk["min_reward_risk"]):
            out.append("R11 Reward:risk %s is under your minimum %s."
                       % (two(rr), two(risk["min_reward_risk"])))
    return out


def two(x):
    """A number with 2 decimals, half up: 1.6866 -> "1.69"."""
    return format(dec(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def limit_words(ctx):
    """R10's limit part: "1.0% of $10,000.00" when the percent is the smaller limit,
    else "your cap of $50.00"."""
    risk = ctx["cfg"]["risk"]
    pct_lim = limit_usd(ctx["balance"], risk["max_risk_per_trade_pct"], None)
    cap = risk.get("max_risk_per_trade_usd")
    if cap is not None and dec(cap) < pct_lim:
        return "your cap of %s" % money(dec(cap))
    return "%s of %s" % (pct_text(risk["max_risk_per_trade_pct"]), money(ctx["balance"]))


def leg_words(ccy, sign):
    """("USD", -1) -> ("Short USD", "bet against the US dollar")."""
    name = CCY_NAMES.get(ccy, ccy)
    return ("%s %s" % ("Long" if sign > 0 else "Short", ccy),
            "bet %s %s" % ("on" if sign > 0 else "against", name))


def rules_account(order, inst, m, ctx, batch):
    """R12 to R21 and R31: today's counts and the account's risk and margin, with the
    passed opens of this batch counted as filled. A list of reasons."""
    cfg, risk, lt = ctx["cfg"], ctx["cfg"]["risk"], ctx["ledger"]
    key, side, fp = order["symbol"], order["side"], ctx["fp"]
    rows, bal = ctx["rows"], dec(ctx["balance"])
    ml, mg = m["max_loss"], m["margin"]
    out = []
    used = lt["used_today"] + sum((b["max_loss"] for b in batch), Decimal(0))
    if ml is not None and used + ml > ctx["L_day"]:
        out.append("R12 Today's loss room: %s used of %s. This trade can lose %s, so it does not fit."
                   % (money(used), money(ctx["L_day"]), money(ml)))
    n = len(lt["taken_today"]) + len(batch)
    if n >= risk["max_trades_per_day"]:
        out.append("R13 You already have %d trades today (your limit is %d)." % (n, risk["max_trades_per_day"]))
    n = len(lt["yes_today"]) + len(batch)
    if n >= risk["max_orders_per_day"]:
        out.append("R14 You already said yes to %d orders today (your limit is %d)."
                   % (n, risk["max_orders_per_day"]))
    n = len(rows) + len(lt["pending"]) + len(batch)
    if n >= risk["max_open_trades"]:
        out.append("R15 You already have %d open trades (your limit is %d)." % (n, risk["max_open_trades"]))
    if fp in lt["taken_today"]:
        out.append("R16 This exact order was already taken today.")
    if any(b["symbol"] == key and b["side"] == side for b in batch):
        out.append("R16 Two %ss of %s in one batch." % (side, key))
    if not cfg["account"].get("hedging", True):
        other = [r for r in rows if r["symbol"] == key and r["side"] != side]
        if other:
            out.append("R17 Your account is netting: this %s would close or flip your open %s of %s. "
                       "Close it first (/fx-broker close)." % (side, other[0]["side"], key))
    if ml is not None:
        group = group_of(key, cfg)
        total = ml + sum((r["max_loss"] for r in rows + batch if r["group"] == group), Decimal(0))
        lim = bal * dec(risk["max_group_risk_pct"]) / 100
        if total > lim:
            name, why = GROUP_WORDS.get(group, (group, "These trades count together."))
            out.append("R18 %s risk would be %s (%s%% of your balance). Your limit is %s%%. %s"
                       % (name, money(total), two(total / bal * 100), two(risk["max_group_risk_pct"]), why))
        lim = bal * dec(risk["max_currency_risk_pct"]) / 100
        for ccy, sign in currency_legs(key, side, inst).items():
            same = [r for r in rows + batch if r["legs"].get(ccy) == sign]
            total = ml + sum((r["max_loss"] for r in same), Decimal(0))
            if total > lim:
                head, bet = leg_words(ccy, sign)
                trades = ", ".join("%s %s" % (r["side"], r["symbol"]) for r in same + [{"side": side, "symbol": key}])
                out.append("R19 %s risk would be %s (%s%% of your balance). Your limit is %s%%. "
                           "These trades all %s: %s." % (head, money(total), two(total / bal * 100),
                                                          two(risk["max_currency_risk_pct"]), bet, trades))
    if mg is not None:
        before = ctx["margin_open"] + sum((b["margin"] for b in batch), Decimal(0))
        after = before + mg
        pct = after / bal * 100
        if pct > dec(risk["max_margin_used_pct"]):
            out.append("R20 Margin would be %s, %s%% of your balance. Your limit is %s%%."
                       % (money(after), format(pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f"),
                          num_text(risk["max_margin_used_pct"])))
        if after > 0:
            level = min(bal, ctx["equity"]) / after * 100
            if level < dec(risk["min_margin_level_pct"]):
                out.append("R21 Margin level would be %s%%. Your minimum is %s%%."
                           % (format(level.quantize(Decimal(1), rounding=ROUND_FLOOR), ","),
                              num_text(risk["min_margin_level_pct"])))
        free = ctx["equity"] - before
        if mg > free:
            out.append("R31 This order needs %s margin. Your free margin is %s." % (money(mg), money(free)))
    return out


def day_times(order, inst, ctx):
    """(cutoff, flat, early) of a day order: the last entry time, the flat-by time and
    the early close that set the cut-off (or None)."""
    cfg, today = ctx["cfg"], ctx["today"]
    day_end = cfg.get("day_end", "17:00 New York")
    normal = pkt_time_on_fx_day(cfg["day"]["no_entries_after"], today, day_end)
    cutoff = entry_cutoff(inst, today, ctx["now"], cfg)
    close = day_close_utc(inst, today, cfg)
    early = close if (close is not None and cutoff < normal) else None
    flat = parse_utc((order.get("plan") or {}).get("flat_by_utc"))
    if flat is None:
        flat = pkt_time_on_fx_day(cfg["day"]["flat_by"], today, day_end)
        if close is not None:
            flat = min(flat, close - datetime.timedelta(minutes=15))
    return cutoff, flat, early


def pkt_clock(utc):
    """naive UTC -> "9:00 PM PKT" (the clock only)."""
    return "%s PKT" % clock_label(to_pkt(utc))


def rules_time(order, inst, m, ctx):
    """R22 to R30: the market, the price, the news, the clock, old orders, rolls and
    rates. A list of reasons."""
    cfg, now, key = ctx["cfg"], ctx["now"], order["symbol"]
    out = []
    state = market_state(inst, now, cfg)
    if state["state"] != "open":
        text = "R22 %s is closed now (%s)." % (name_word(inst, True), closed_words(state, inst))
        if state.get("next_open"):
            text += " It opens at %s." % pkt_label(state["next_open"])
        out.append(text)
    if m["bid_now"] is None:
        q = m["q"]
        if q is None or q.get("bid") is None:
            out.append("R23 There is no %s price in the cache. Run /fx-alerts quote %s." % (name_word(inst), key))
        else:
            out.append("R23 The newest %s price is %s. A new trade needs one under %d min old. "
                       "Run /fx-alerts quote %s." % (name_word(inst), age_text(q["age_min"]),
                                                     STALE_MINUTES[inst["price_source"]], key))
    ev = in_quiet_window(ctx["events"], key, now, cfg)
    if ev is not None:
        out.append("R24 %s at %s. %s impact. No new trades from %d min before to %d min after it."
                   % (event_label(ev), pkt_clock(ev["at"]), event_impact(ev, key),
                      cfg["news"]["quiet_before_min"], cfg["news"]["quiet_after_min"]))
    ny = to_ny(now)
    if order["style"] == "swing":
        cut = parse_hhmm(cfg["swing"]["friday_cutoff_ny"], '"swing.friday_cutoff_ny"')
        if ny.weekday() == 4 and ny.time() >= cut:
            out.append("R25 It is past %s New York on Friday. No new swing trades before the weekend: "
                       "a gap can jump past the stop." % clock_label(cut))
        end = parse_utc((order.get("plan") or {}).get("exit_by"))
        if end is not None and end <= now:
            out.append("R27 This plan's last day has passed. Make a new plan.")
    else:
        cutoff, flat, early = day_times(order, inst, ctx)
        if now >= cutoff:
            if early is not None:
                out.append("R26 %s stops early today, at %s (%s). No new day trades after %s."
                           % (name_word(inst, True), ny_label(early), pkt_clock(early), pkt_clock(cutoff)))
            else:
                out.append("R26 No new day trades after %s." % pkt_clock(cutoff))
        if now >= flat - datetime.timedelta(minutes=MIN_DAY_MINUTES_LEFT):
            out.append("R26 Less than %d minutes to your flat-by time (%s)."
                       % (MIN_DAY_MINUTES_LEFT, pkt_clock(flat)))
    if ctx["orders_day"] != ctx["today"]:
        out.append("R28 These orders were made on %s. Make a new plan today."
                   % day_label(datetime.date.fromisoformat(ctx["orders_day"])))
    root = inst_root(inst)
    applied = order.get("rolls_applied") or {}
    for r in ctx["rolls"]:
        start = parse_utc(r.get("first_bar_new"))
        if not root or r.get("root") != root or start is None or start > now:
            continue
        done = applied.get(r["id"]) if isinstance(applied, dict) else None
        if done is None or float(done) != float(r["ratio"]):
            ratio = r["ratio"] if done is None else dec(r["ratio"]) / dec(done)
            out.append("R29 %s rolled on Yahoo after this order was made (%s). Its prices are on the "
                       "old scale. Make it again: /fx-exit, then /fx-budget." % (name_word(inst, True), signed_pct(ratio)))
    out += rate_reasons(inst, m, ctx)
    return out


def rate_reasons(inst, m, ctx):
    """R30: each currency the order needs (profit, margin, an FX base) with no USD rate
    or a rate older than fx_rates_max_age_hours."""
    cfg, rates = ctx["cfg"], ctx["rates"]
    limit = dec(cfg.get("fx_rates_max_age_hours", 24))
    codes = [_pccy(inst), inst.get("margin_currency") or inst.get("base")]
    if inst.get("kind") == "fx":
        codes.append(inst.get("base"))
    out, seen = [], set()
    for c in codes:
        if c in seen or c == "USD" or c not in RATE_PAIRS:
            continue
        seen.add(c)
        row = rates.get(c)
        if not row:
            pair = RATE_PAIRS[c][0]
            out.append("R30 No USD rate for %s. It comes from %s. Keep %s in your config and run once "
                       "with Yahoo on (not --offline)." % (c, pair, pair))
        elif dec(row.get("age_h") or 0) > limit:
            out.append("R30 The %s rate is %d hours old. Run once with Yahoo on to refresh it."
                       % (c, int(row.get("age_h") or 0)))
    if m["rate_err"] and not out:
        out.append("R30 " + m["rate_err"])
    return out


def open_warnings(order, inst, m, ctx):
    """The 05 5.3 warnings of one open, in the order of section 6.2."""
    cfg, now, key, side = ctx["cfg"], ctx["now"], order["symbol"], order["side"]
    stop, tp, e = order.get("stop_loss"), order.get("take_profit"), m["e"]
    min_rr = cfg["risk"]["min_reward_risk"]
    out = []
    if not cfg.get("paper", True):
        out.append("W-live LIVE account: this is real money.")
    if m["no_stop"] and not cfg["risk"].get("require_stop", True):
        out.append("W-nostop No stop. The loss has no limit. It is counted as your full max loss "
                   "per trade (%s)." % money(ctx["L_trade"]))
    if stop is not None:
        note = coarse_note(inst, to_points(abs(dec(e) - dec(stop)), inst))
        if note:
            out.append("W-coarse " + note)
        if tp is not None:
            try:
                net = reward_risk_net(inst, side, e, stop, tp, m["nights"], ctx["rates"], cfg)
                if dec(net) < dec(min_rr):
                    out.append("W-net After costs this trade wins %s times what it risks. Your minimum "
                               "is %s. The swap for the whole hold is counted." % (two(net), num_text(min_rr)))
            except ValueError:
                pass
    if order["style"] == "day":
        end = day_times(order, inst, ctx)[1]
    else:
        end = now + datetime.timedelta(hours=24)
    for ev in next_events(ctx["events"], [key], now, 50, impacts=cfg["news"]["impacts"]):
        if ev.get("all_day") or ev["at"] > end:
            continue
        out.append("W-news %s at %s (%s), while this trade is open. %s impact. Spreads widen and "
                   "stops can slip." % (event_label(ev), pkt_label(ev["at"]), ny_label(ev["at"]),
                                        event_impact(ev, key)))
    if order["style"] == "swing":
        out.append("W-weekend The market closes for the weekend. On Monday the price can open past "
                   "your stop. A swing trade stays open over the weekend.")
        end = parse_utc((order.get("plan") or {}).get("exit_by"))
        root = inst_root(inst)
        if root and end is not None:
            held = set(r.get("new") for r in ctx["rolls"] if r.get("root") == root)
            ahead = [w for w in expected_rolls(root, inst, now, end) if w[1] not in held]
            if ahead:
                out.append("W-roll %s rolls on Yahoo about %s. Plan levels move then; your broker's "
                           "price does not." % (name_word(inst, True), ahead[0][4].isoformat()))
    try:
        notional = (dec(order["lots"]) * dec(inst["contract_size"]) * dec(e)
                    * usd_per_unit(_pccy(inst), ctx["rates"]))
        ratio = (notional / dec(ctx["balance"])).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        if ratio > 10:
            out.append("W-leverage This trade is %s:1 leverage on your balance. Losses grow just as fast."
                       % format(ratio, ","))
    except (ValueError, ArithmeticError):
        pass
    if any(r["symbol"] == key and r["side"] == side for r in ctx["rows"]):
        out.append("W-same You already hold a %s of %s." % (side, key))
    cal = ctx["cal"] or {}
    if cal.get("ok") is not True or cal.get("week") is None:
        out.append("W-calendar No calendar for this week. The news check used only the built-in "
                   "central bank and EIA dates.")
    if inst.get("price_source") == "futures":
        out.append("W-scale " + scale_note(inst))
    if ctx["rows"]:
        out.append("W-margin Equity counts your open trades at Yahoo prices, so it is about right, "
                   "not exact. A trade with no fresh price counts as its max loss.")
    return out


def open_lines(order, inst, m, ctx, batch):
    """The detail lines under an open (section 6.2, items 1 to 6), without the notes."""
    cfg, side, kind = ctx["cfg"], order["side"], order["type"]
    stop, tp, e, entry = order.get("stop_loss"), order.get("take_profit"), m["e"], order["entry"]
    out = []
    if kind != "market":
        out.append("%s %s order at %s." % ("Buy" if side == "buy" else "Sell", kind, price_text(entry, inst)))
    elif m["bid_now"] is None:
        out.append("Measured from the plan's entry %s (no fresh price)." % price_text(entry, inst))
    else:
        now_word = "the ask now" if side == "buy" else "the bid now"
        now_price = m["ask_now"] if side == "buy" else m["bid_now"]
        if dec(e) == dec(now_price):
            out.append("Market order. Measured from %s (%s; the plan said %s)."
                       % (price_text(e, inst), now_word, price_text(entry, inst)))
        else:
            out.append("Market order. Measured from %s (the plan's entry; %s is %s)."
                       % (price_text(e, inst), now_word, price_text(now_price, inst)))
    q = m["q"]
    if q is None or q.get("bid") is None:
        out.append("No %s price in the cache." % name_word(inst))
    elif m["bid_now"] is None:
        out.append("No fresh %s price: the newest is %s." % (name_word(inst), age_text(q["age_min"])))
    else:
        out.append("%s %s (%s, %s)" % (name_word(inst), price_text(q["bid"], inst), scale_note(inst),
                                       age_text(q["age_min"])))
    if stop is not None:
        text = "Stop %s, %s away." % (price_text(stop, inst), pips_text(abs(dec(e) - dec(stop)), inst))
        if tp is not None and dec(e) != dec(stop):
            rr = abs(dec(tp) - dec(e)) / abs(dec(e) - dec(stop))
            text += " Target %s. Reward:risk %s (min %s)." % (price_text(tp, inst), two(rr),
                                                              two(cfg["risk"]["min_reward_risk"]))
        out.append(text)
    elif tp is not None:
        out.append("No stop. Target %s." % price_text(tp, inst))
    lt = ctx["ledger"]
    used = lt["used_today"] + sum((b["max_loss"] for b in batch), Decimal(0))
    out.append("Today before this: %d of %d trades, %s of %s daily loss used."
               % (len(lt["taken_today"]) + len(batch), cfg["risk"]["max_trades_per_day"],
                  money(used), money(ctx["L_day"])))
    if m["margin"] is not None:
        before = ctx["margin_open"] + sum((b["margin"] for b in batch), Decimal(0))
        after = before + m["margin"]
        pct = (after / dec(ctx["balance"]) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        out.append("Margin after: %s (%s%% of your balance, limit %s%%). Free margin %s."
                   % (money(after), format(pct, "f"), num_text(cfg["risk"]["max_margin_used_pct"]),
                      money(ctx["equity"] - before)))
    if order["style"] == "day":
        cutoff, flat = day_times(order, inst, ctx)[:2]
        out.append("Flat by %s. No new day trades after %s." % (both_times(flat), pkt_label(cutoff)))
    return out


def rule_no(reason):
    """The table position of a reason id: "R12 ..." -> 12."""
    m = re.match(r"^R(\d+) ", reason)
    return int(m.group(1)) if m else 0


def check_open(n, order, ctx, batch):
    """Step 11: every rule R1 to R31 on one open, then the warnings. Returns the
    verdict dict (section 5) with "_lines" (the detail lines) and "_item" (for the batch)."""
    cfg, key = ctx["cfg"], order.get("symbol")
    fp = fingerprint(order)
    v = new_verdict(n, order, fp)
    problem = open_problem(order, n, ctx["path"])
    if problem:
        v["reasons"].append("R1 " + problem)
        v["verdict"] = "BLOCK"
        return v
    reasons = []
    inst = None
    if not isinstance(key, str) or key not in (cfg.get("instruments") or {}):
        reasons.append("R2 %s is not in your instrument table." % key)
    else:
        inst = instrument(cfg, key)
    if key in (cfg["risk"].get("blocked") or []):
        reasons.append("R3 %s is on your blocked list." % key)
    if order["side"] == "sell" and not cfg.get("allow_short", True):
        reasons.append("R4 Sells are off (allow_short is false).")
    if inst is not None:
        ctx = dict(ctx, fp=fp)
        m = measure(order, inst, ctx)
        reasons += rules_order(order, inst, m, ctx)
        reasons += rules_account(order, inst, m, ctx, batch)
        reasons += rules_time(order, inst, m, ctx)
        v["warnings"] = open_warnings(order, inst, m, ctx)
        v["_lines"] = open_lines(order, inst, m, ctx, batch)
        v["max_loss_usd"] = None if m["max_loss"] is None else float(m["max_loss"])
        v["margin_usd"] = None if m["margin"] is None else float(m["margin"])
        v["_item"] = {"symbol": key, "side": order["side"], "style": order["style"], "lots": order["lots"],
                      "fingerprint": fp, "max_loss": m["max_loss"] or Decimal(0),
                      "margin": m["margin"] or Decimal(0), "group": group_of(key, cfg),
                      "legs": currency_legs(key, order["side"], inst)}
    v["reasons"] = sorted(reasons, key=rule_no)
    if v["reasons"]:
        v["verdict"] = "BLOCK"
    return v


# ---------------------------------------------------------------- network, notes, printout

def fetch_all(orders, portfolio, cfg, data_dir, now, offline):
    """Step 4 (online only): refresh the 1h cache (and 5m for a day order) of each rate
    pair, each open order's instrument and each open position's; then the roll check of
    each futures one. Each symbol once. Returns the failed notes."""
    rows = cfg.get("instruments") or {}
    want = []   # (key, intervals)
    opens = [o for o in orders if o.get("intent") == "open" and o.get("symbol") in rows]

    def add(key, day=False):
        for item in want:
            if item[0] == key:
                if day and "5m" not in item[1]:
                    item[1].append("5m")
                return
        want.append((key, ["1h", "5m"] if day else ["1h"]))
    for o in opens:
        for k in rate_symbols(instrument(cfg, o["symbol"]), cfg):
            if k in rows:
                add(k)
    for o in opens:
        add(o["symbol"], o.get("style") == "day")
    for p in portfolio["positions"]:
        add(p["symbol"])
    failed = []
    for key, intervals in want:
        inst = instrument(cfg, key)
        bad = False
        for interval in intervals:
            try:
                refresh(data_dir, inst["yahoo"], interval, now, cfg, offline)
            except ValueError:
                bad = True
        if bad:
            lp = latest_price(data_dir, inst, now, cfg)
            age = (" (%s)" % age_text(lp["age_min"])) if lp else ""
            failed.append("Yahoo could not be read for %s. Using the saved prices%s." % (inst["yahoo"], age))
        if inst.get("futures"):
            try:
                check_roll_forward(data_dir, inst, cfg, now, offline)
            except ValueError:
                pass
    return failed


def calendar_line(cal, offline):
    """The calendar status line (03 9.7), or None when the saved calendar is fine and
    no download was due (offline)."""
    if not cal or cal.get("events") is None:
        if offline:
            return "Offline: no saved calendar for this week. Only built-in dates are used."
        return ("Calendar site could not be read. Only built-in dates are shown: central banks, "
                "EIA oil and gas, US jobs and COT. Other news (like CPI) is missing.")
    if offline:
        return None
    if cal.get("ok") is not True:
        return "Calendar not updated. Using the copy from %s." % age_text(cal.get("age_min")).replace(" old", " ago")
    return "Calendar: this week, updated %s." % age_text(cal.get("age_min")).replace(" old", " ago")


def roll_note(roll, cfg):
    """The 02 5.8 line for a roll that moved an open position's copy."""
    key = None
    for k, row in (cfg.get("instruments") or {}).items():
        if (row.get("futures") or {}).get("root") == roll.get("root"):
            key = k
            break
    word = ROLL_WORDS.get(roll.get("root"), roll.get("root") or "")
    word = word[:1].upper() + word[1:]
    up = "up" if float(roll["ratio"]) >= 1 else "down"
    return ("%s rolled on Yahoo (%s). Your plan levels moved %s by the same amount. Your broker's "
            "%s did not jump: keep the SL and TP you set in MetaTrader."
            % (word, signed_pct(roll["ratio"]), up, key or roll.get("root")))


def report(ctx, verdicts):
    """The words-mode lines (section 6)."""
    quiet = ctx["quiet"]
    out = []
    if not quiet:
        for note in ctx["notes"]:
            out += wrap_line("Note: " + note, "", "")
        if ctx["notes"]:
            out.append("")
    for v in verdicts:
        head = "%s #%d %s %s %s:" % (v["verdict"], v["index"],
                                     "close" if v["intent"] == "close" else v["side"], v["symbol"],
                                     lots_text(v["lots"]) if isinstance(v["lots"], (int, float))
                                     and not isinstance(v["lots"], bool) and v["lots"] >= 0 else v["lots"])
        if v["verdict"] == "PASS":
            if v["intent"] == "close":
                head += " " + v.get("_head", "closes your position.")
            else:
                head += " max loss %s (limit %s), costs included." % (money(v["max_loss_usd"] or 0),
                                                                     money(ctx["L_trade"]))
        out.append(head)
        for r in v["reasons"]:
            out += wrap_line("- " + strip_id(r), "", "  ")
        if quiet:
            continue
        for line in v.get("_lines", []):
            out.append("  " + line)
        for w in v["warnings"]:
            out += wrap_line("Note: " + strip_id(w))
    s = ctx["summary"]
    out.append("")
    out.append("%d passed, %d blocked. Checked at %s. %s" % (
        s["passed"], s["blocked"], pkt_label(ctx["now"]),
        "Paper account." if ctx["cfg"].get("paper", True) else "LIVE account: this is real money."))
    if quiet:
        return out
    out.append("Balance %s. Daily loss used: %s of %s, with the passed orders."
               % (money(s["balance"]), money(s["daily_loss_used_usd"]), money(s["daily_loss_limit_usd"])))
    if s["passed"]:
        out.append("A PASS is a rules check, not a yes. fx-broker still asks you.")
        out.append("Run this check again right before you say yes: a PASS counts for %d minutes."
                   % FRESH_CHECK_MINUTES)
    if s["blocked"]:
        out += wrap_line("A BLOCK is final for that order. Fix the cause (a new plan, a smaller risk, "
                         "wait for the market), then run fx-budget and this check again.", "", "")
    if s["passed"]:
        out.append("Next: python3 fx-broker/ticket.py show")
    return out


# ---------------------------------------------------------------- main

def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="check.py", description="Check every order in fx/orders.json against your limits. "
        "PASS or BLOCK per order. Writes fx/state/risk.json. Never places an order.")
    add_common(parser)
    return parser.parse_args(argv)


def run(args):
    """Steps 1 to 12. Returns (ctx, verdicts, doc)."""
    cfg_path = args.config or os.path.join("fx", "config.json")
    cfg = load_config(cfg_path, required=args.config is not None)
    work = os.path.dirname(cfg_path) or "."
    paths = fx_paths(work, args.data, cfg_path)
    data_dir = paths["data"]
    now = parse_now(args.now)
    replay = args.now is not None
    day_end = cfg.get("day_end", "17:00 New York")
    today = last_fx_day(now, cfg)
    doc, orders, created = read_orders(paths["orders"])
    orders_day = last_fx_day(created, cfg)
    portfolio = read_portfolio(paths["portfolio"], cfg)
    records = read_ledger(paths["broker_state"])
    acct = portfolio["account"]
    bal = acct.get("balance")
    if isinstance(bal, bool) or not isinstance(bal, (int, float)):
        bal = cfg["account"]["balance"]
    risk = cfg["risk"]
    l_trade = limit_usd(bal, risk["max_risk_per_trade_pct"], risk.get("max_risk_per_trade_usd"))
    l_day = limit_usd(bal, risk["max_daily_loss_pct"], risk.get("max_daily_loss_usd"))
    any_open = any(o.get("intent") != "close" for o in orders)
    offline = args.offline or replay or not cfg.get("yahoo", True)
    notes = []
    if not risk.get("require_stop", True):
        notes.append("require_stop is off: an order with no stop can pass.")
    if any_open:
        notes += fetch_all(orders, portfolio, cfg, data_dir, now, offline)
    rolls = read_rolls(data_dir, now, replay)
    rates = fx_rates(data_dir, now, cfg)
    events, cal = [], None
    if any_open:
        offline_cal = args.offline or replay
        events = load_calendar(data_dir, now, cfg, offline_cal)
        cal = calendar_info(data_dir, now)
        line = calendar_line(cal, offline_cal)
        if line:
            notes.append(line)
    rows, margin_open, pl_sum, pos_notes, roll_ids = positions_now(
        portfolio, cfg, rates, data_dir, now, rolls, replay, paths)
    notes += pos_notes
    for r in rolls:
        if r.get("id") in roll_ids:
            notes.append(roll_note(r, cfg))
    ctx = {"cfg": cfg, "now": now, "today": today, "paths": paths, "data_dir": data_dir,
           "replay": replay, "rates": rates, "events": events, "cal": cal, "rows": rows,
           "margin_open": margin_open, "equity": dec(bal) + pl_sum, "balance": bal,
           "L_trade": l_trade, "L_day": l_day, "orders_day": orders_day, "rolls": rolls,
           "ledger": ledger_today(records, portfolio, today, day_end), "path": paths["orders"],
           "notes": notes, "quiet": args.quiet}
    verdicts, batch = [], []
    for n, order in enumerate(orders, 1):
        if order.get("intent") == "close":
            v = check_close(n, order, portfolio, cfg, now, paths["orders"])
        else:
            v = check_open(n, order, ctx, batch)
            if v["verdict"] == "PASS" and v.get("_item"):
                batch.append(v["_item"])
        verdicts.append(v)
    passed = sum(1 for v in verdicts if v["verdict"] == "PASS")
    used = ctx["ledger"]["used_today"] + sum((b["max_loss"] for b in batch), Decimal(0))
    margin_used = margin_open + sum((b["margin"] for b in batch), Decimal(0))
    cents = lambda d: float(dec(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0
    summary = {"passed": passed, "blocked": len(verdicts) - passed, "daily_loss_used_usd": cents(used),
               "daily_loss_limit_usd": cents(l_day), "open_trades": len(rows), "balance": float(bal),
               "equity_usd": cents(ctx["equity"]), "margin_used_usd": cents(margin_used),
               "paper": bool(cfg.get("paper", True))}
    ctx["summary"] = summary
    out = {"created_by": TOOL, "created_at": utc_iso(now), "checked_at": utc_iso(now),
           "orders_created_at": doc["created_at"],
           "verdicts": [dict((k, x) for k, x in v.items() if not k.startswith("_")) for v in verdicts],
           "summary": summary}
    save_json_atomic(paths["risk_state"], out)
    return ctx, verdicts, out


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        ctx, verdicts, doc = run(args)
    except ValueError as exc:
        return fail(exc, SCRIPT)
    ok = all(v["verdict"] == "PASS" for v in verdicts)
    if args.json:
        emit_json({"v": JSON_VERSION, "tool": TOOL, "cmd": None, "now": utc_iso(ctx["now"]), "ok": ok,
                   "notes": list(ctx["notes"]), "risk": doc})
    else:
        print("\n".join(report(ctx, verdicts)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
