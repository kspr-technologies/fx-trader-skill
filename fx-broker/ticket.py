#!/usr/bin/env python3
"""fx-broker: MetaTrader order tickets. Paper trading by default. It never places an order.

    python3 fx-broker/ticket.py show [--index N] [--broker-price P]
    python3 fx-broker/ticket.py approve --index N --reply "yes" [--broker-price P]
    python3 fx-broker/ticket.py decline --index N [--reply TEXT]
    python3 fx-broker/ticket.py result --index N --status filled [--fill P]
    python3 fx-broker/ticket.py close --position XAUUSD-1 [--already --fill P]
    python3 fx-broker/ticket.py flatten [--all]
    python3 fx-broker/ticket.py stop --position XAUUSD-1 --to 2421.59 [--wider]
    python3 fx-broker/ticket.py log [--days N | --today]

It shows each order of fx/orders.json as a plain ticket, waits for the user's explicit
yes, and only then prints the one line to type into MetaTrader. It keeps the ledger
(fx/state/broker.json) and the paper account (fx/portfolio.json). It has no network
code at all: it reads only files and the saved caches in fx/data.
Python 3.8, standard library only.
"""

import argparse
import copy
import datetime
import difflib
import hashlib
import json
import math
import os
import re
import sys
import textwrap
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

# ---- copied from tests/fx_core.py: do not edit (tests/check_copies.py checks it)

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


def server_time(utc, server_cfg):
    """naive UTC -> the MetaTrader server clock ("ny+7": New York + 7 h; "utc+N": UTC + N h)."""
    n = server_offset(server_cfg)
    if n is None:
        return to_ny(utc) + datetime.timedelta(hours=7)
    return utc + datetime.timedelta(hours=n)


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


CALENDAR_AHEAD_DAYS = 35        # built-in events this far ahead (a 20-FX-day swing hold)


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


def calendar_file_doc(path):
    """The saved calendar file, or None when it is missing or broken (check: a broken
    file is read as missing, so the next download replaces it)."""
    try:
        doc = load_json(path, None)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


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


def to_broker(level, k, inst):
    """A plan-scale entry or plain level on the broker scale: level x k, to the nearest tick."""
    return round_price(float(_dec_clean(level) * Decimal(repr(float(k)))), inst)


def to_broker_away(level, entry, k, inst, entry_broker=None):
    """A stop or target on the broker scale: the broker entry plus or minus the plan
    distance (entry - level), rounded away from the broker entry (D3)."""
    if entry_broker is None:
        entry_broker = to_broker(entry, k, inst)
    x = _dec_clean(entry_broker) - (_dec_clean(entry) - _dec_clean(level))
    return round_away(float(x), entry_broker, inst)


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


def delay_error(inst, age_min, bars_5m, daily_atr=None):
    """About how far the price may have moved since Yahoo's price: the 5m ATR(14) x
    sqrt(age_min / 5), or with no 5m bars daily_atr x sqrt(age_min / 1440); 0 under a
    minute (or with no ATR). Rounded to the instrument's digits."""
    if age_min is None or age_min < 1:
        return 0.0
    a = atr_wilder(usable_bars(bars_5m)[-ATR_WINDOW["5m"]:], 14) if bars_5m else None
    if a:
        x = a * math.sqrt(age_min / 5.0)
    elif daily_atr:
        x = daily_atr * math.sqrt(age_min / 1440.0)
    else:
        return 0.0
    return _round_dec(x, int(inst.get("digits", 2)))


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


def commission_usd(inst, lots):
    """Commission for both sides in USD, half up to cents (E1: 3.00 x 0.68 x 2 = 4.08)."""
    return _cents(_commission(inst, lots))


def swap_usd(inst, side, lots, nights, rates, cfg, price=None):
    """The swap in USD, signed (plus = paid to you), half up to cents; 0.0 swap-free.
    price (the entry) is needed for the "interest" mode."""
    return _cents(_swap(inst, side, lots, nights, rates, cfg, price))


def swap_free_fee_usd(inst, lots, nights, cfg):
    """The swap-free fee in USD after the grace nights, half up to cents (always a cost)."""
    return _cents(_fee(inst, lots, nights, cfg))


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


def result_usd(inst, side, lots, entry, exit_price, nights, rates, cfg):
    """A closed trade in USD after all costs, signed (minus = a loss), half up to cents once."""
    return _cents(_lots_dec(lots) * result_core(inst, side, entry, exit_price, nights, rates, cfg))


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


# ---- end of the copies from tests/fx_core.py


# ---------------------------------------------------------------- the yes rule (00 6.10a)

TOOL = "ticket.py"
SKILL = "fx-broker"
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each",
               "sab", "sare", "saare", "dono"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if", "when", "once", "until", "till", "provided", "assuming",
               "nahi", "nahin", "ruko", "ruk", "thehro", "mat", "lekin", "magar", "agar", "jab", "shayad"}
DOUBT_PHRASE = re.compile(r"\bas (?:long|soon) as\b")
SIDE_SAYS = {
    "buy": {"buy", "buying", "bought", "purchase", "long", "khareed", "khareedo", "kharid", "kharido",
            "khareedna", "kharidna", "khareedein", "kharidein"},
    "sell": {"sell", "selling", "sold", "sale", "short", "shorting", "becho", "bech", "bechdo",
             "bechna", "bechein", "farokht"},
}
CLOSE_SAYS = {"close", "closing", "exit", "flatten", "out", "square", "squareoff", "liquidate", "cashout"}
TYPE_SAYS = {"market": {"market", "mkt"}, "limit": {"limit", "lmt"}, "stop": {"stop"}}
ANY_PRICE = re.compile(r"\b(?:any|best|whatever|going|current)\s+(?:price|rate)\b")   # a market order in words
CHANGE_WORDS = {"double", "doubled", "half", "halve", "twice", "triple", "more", "less", "increase", "reduce",
                "decrease", "bigger", "smaller", "extra", "add", "tomorrow", "later", "next", "week", "tonight",
                "times", "lunch", "noon", "afternoon", "evening",
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                "dugna", "aadha", "adha", "zyada", "ziyada", "kam", "kal", "parson", "baad", "agle", "agla", "kabhi"}
TIMES = re.compile(r"(?i)(?<![a-z0-9])(?:[x×]\s?\d+|\d+\s?[x×])(?![a-z0-9])")   # x2, 2x: a new size
PLAIN_CAPS = {"BUY", "SELL", "SL", "TP", "USD", "OK", "YES", "Y", "LOT", "LOTS"}
WORD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
SYMBOL_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9.\-]*")
NUMBER = re.compile(r"(?<![A-Za-z0-9.,])\d[\d,]*(?:\.\d+)?")
DOLLARS = re.compile(r"(?i)\$|\busd\b")
STATUSES = ("placed", "filled", "skipped", "failed", "cancelled", "expired")
WHYS = ("stop", "target", "flat_by", "exit_by", "manual")
RECORD_KEYS = ("at", "fx_day", "status", "index", "fingerprint", "symbol", "side", "intent", "lots",
               "style", "max_loss_usd", "reply", "price", "broker_price", "result_usd", "position_id",
               "roll_id", "plain", "paper")
CLOSED_DAYS = 90


class Out(object):
    """The lines of one run: plain lines, notes ("Note: ..."), and the JSON keys."""

    def __init__(self, args, cmd, now):
        self.args, self.cmd, self.now = args, cmd, now
        self.lines, self.notes, self.keys = [], [], {}

    def say(self, line=""):
        self.lines.append(line)

    def note(self, text):
        self.notes.append(text)
        if not self.args.quiet:
            self.lines += textwrap.wrap("Note: " + text, width=78) or [""]

    def wrap(self, line):
        """A long plain line (not a MetaTrader line), wrapped at 78 characters."""
        self.lines += textwrap.wrap(line, width=78) or [""]

    def done(self, code):
        if self.args.json:
            doc = {"v": JSON_VERSION, "tool": SKILL, "cmd": self.cmd, "now": utc_iso(self.now),
                   "ok": code == 0, "notes": list(self.notes)}
            doc.update(self.keys)
            emit_json(doc)
        else:
            for line in self.lines:
                print(line)
        return code


def price_arg(text, flag):
    """A price the user typed after a flag: a number above 0, else a plain error."""
    if text is None:
        return None
    try:
        x = float(str(text).replace(",", ""))
    except ValueError:
        x = None
    if x is None or not math.isfinite(x) or x <= 0:
        raise ValueError("%s must be a price above 0, not %s." % (flag, text))
    return x


def plain_order(order):
    """"BUY 0.02 lots XAUUSD" for the ledger and the yes rule."""
    return "%s %s %s" % (side_word(order["side"]), lots_text(order["lots"]), order["symbol"])


def inst_word(inst, lower=False):
    """The instrument's name for a sentence: "Gold" (or "gold" with lower)."""
    name = str(inst.get("name") or inst.get("key") or "")
    if lower and name and not (len(name) > 1 and name[1].isupper()):
        name = name[0].lower() + name[1:]
    return name


def time_pair(utc):
    """"11:30 PM PKT (2:30 PM New York)": both clocks, no day name."""
    return "%s PKT (%s)" % (clock_label(to_pkt(utc)), ny_label(utc))


# ---------------------------------------------------------------- reading the fx/ files

def read_orders(paths, cfg):
    """orders.json with the common checks (01 1.3) and its own (01 5.2): (doc, orders)."""
    path = paths["orders"]
    doc = load_doc(path, "orders", "fx-budget")
    orders = check_objects(doc, "orders", path)
    if not orders:
        raise ValueError("%s has no orders. Run fx-budget with --write first." % path)
    for n, o in enumerate(orders, 1):
        bad = lambda what: ValueError("%s: order %d: %s" % (path, n, what))
        check_symbol(o.get("symbol"), cfg, path)
        check_side(o.get("side"), path)
        check_style(o.get("style"), path)
        check_lots(o.get("lots"), path)
        if o.get("intent") not in ("open", "close"):
            raise bad('intent must be "open" or "close", not "%s"' % (o.get("intent"),))
        if o.get("type") not in ("market", "stop", "limit"):
            raise bad('type must be "market", "stop" or "limit", not "%s"' % (o.get("type"),))
        inst = instrument(cfg, o["symbol"])
        if _dec_clean(o["lots"]) % _dec_clean(inst["lot_step"]) != 0:
            raise bad("lots %s is not a whole number of lot_step %s" % (o["lots"], inst["lot_step"]))
        if o["intent"] == "close":
            check_close_order(o, n, path)
            continue
        check_price(o, "entry", path)
        if o.get("stop_loss") is None:
            if cfg.get("risk", {}).get("require_stop", True):
                raise bad("it has no stop_loss. Every order needs a stop")
        else:
            check_price(o, "stop_loss", path)
        if o.get("take_profit") is not None:
            check_price(o, "take_profit", path)
        exp = o.get("expiry")
        if exp not in ("gtc", "today") and check_utc(o, "expiry", path) is None:
            raise bad('expiry must be "gtc", "today" or a UTC time')
    return doc, orders


def read_ledger(paths):
    """state/broker.json: {"records": [...]}, empty when missing; damaged: an error."""
    path = paths["broker_state"]
    doc = load_json(path, None)
    if doc is None:
        return {"records": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        raise ValueError("%s is damaged at record 1. Nothing was changed." % path)
    for n, r in enumerate(doc["records"], 1):
        if not isinstance(r, dict) or not r.get("at") or not r.get("status"):
            raise ValueError("%s is damaged at record %d. Nothing was changed." % (path, n))
    return doc


def read_portfolio(paths, cfg):
    """portfolio.json with its checks (01 5.2), or None when there is none yet."""
    path = paths["portfolio"]
    doc = load_doc(path, "portfolio", SKILL, required=False)
    if doc is None:
        return None
    bad = lambda what: ValueError("%s: %s. Fix it by hand or restore a backup." % (path, what))
    acc = doc.get("account")
    if not isinstance(acc, dict) or acc.get("currency") != "USD":
        raise bad('"account.currency" must be "USD"')
    check_objects(doc, "positions", path)
    if "closed" in doc:
        check_objects(doc, "closed", path)
    else:
        doc["closed"] = []
    seen = set()
    for p in doc["positions"]:
        if p.get("symbol") not in cfg.get("instruments", {}):
            raise bad('position %s has the symbol "%s", which is not in your instruments'
                      % (p.get("id"), p.get("symbol")))
        if p.get("id") in seen:
            raise bad("two positions have the id %s" % p.get("id"))
        seen.add(p.get("id"))
    return doc


def new_portfolio(cfg, now):
    """The first portfolio.json (5): the config balance, no positions."""
    bal = float(cfg["account"]["balance"])
    return {"created_by": SKILL, "updated_at": utc_iso(now),
            "account": {"currency": "USD", "balance": bal, "start_balance": bal,
                        "realized_usd": 0.0, "paper": bool(cfg.get("paper", True))},
            "positions": [], "closed": []}


def read_risk(paths):
    """state/risk.json, or None when there is none."""
    doc = load_doc(paths["risk_state"], "risk check", "fx-risk", required=False)
    if doc is not None and "verdicts" in doc:
        check_objects(doc, "verdicts", paths["risk_state"])
    return doc


def read_prices(paths):
    """prices.json, or {} when there is none."""
    doc = load_doc(paths["prices"], "prices", "fx-alerts", required=False)
    return doc or {}


def read_plan(paths):
    """plan.json for the ticket's words only; None when missing or broken."""
    try:
        doc = load_json(paths["plan"], None)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


# ---------------------------------------------------------------- prices, levels, events

def yahoo_now(paths, inst, now, cfg, prices):
    """The Yahoo bid now (section 3): the newer of latest_price and the prices.json
    quote (never one from after now). {bid, yahoo, at, age_min} or None."""
    lp = latest_price(paths["data"], inst, now, cfg)
    best = None
    if lp:
        best = {"bid": lp["bid"], "yahoo": lp["yahoo"], "at": lp["at"], "age_min": lp["age_min"]}
    q = ((prices or {}).get("quotes") or {}).get(inst["key"])
    if isinstance(q, dict):
        at = parse_utc(q.get("at"))
        ok = at is not None and at <= now and _positive_number(q.get("bid"))
        if ok and (best is None or at > best["at"]):
            u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
            y = q["bid"] if u == 1 else float(_dec_clean(q["bid"]) / u)
            best = {"bid": q["bid"], "yahoo": y, "at": at, "age_min": data_age_min(at, now)}
    return best


def save_broker_price(paths, inst, price, now, cfg, prices, yahoo):
    """4.10: check k, then set prices.json broker[symbol] only. Returns the record."""
    if yahoo is None:
        raise ValueError("No Yahoo price for %s in fx/data. Run fx-alerts quote %s first."
                         % (inst["key"], inst["key"]))
    k = broker_k(inst, price, yahoo["yahoo"])
    rec = {"bid": price, "at": utc_iso(now), "yahoo_bid": yahoo["yahoo"],
           "yahoo_at": utc_iso(yahoo["at"]), "k": round(k, 6)}
    doc = read_prices(paths)
    doc.setdefault("broker", {})
    if not isinstance(doc["broker"], dict):
        doc["broker"] = {}
    doc["broker"][inst["key"]] = rec
    doc["updated_at"] = utc_iso(now)
    doc["created_by"] = SKILL
    save_json_atomic(paths["prices"], doc)
    return rec


def broker_levels(order, inst, bp):
    """4.2 step 4: {k, entry, stop_loss, take_profit, source, bid, at} on the broker
    scale, or None (a futures row with no broker price; or a close order)."""
    if order.get("intent") == "close":
        return None
    if bp:
        k, source = broker_k(inst, bp["bid"], bp["yahoo_bid"]), "broker"
    elif inst.get("price_source") == "spot_fx":
        k, source = 1.0, "spot"
    else:
        return None
    entry = order["entry"]
    be = to_broker(entry, k, inst)
    out = {"k": k, "entry": be, "stop_loss": None, "take_profit": None, "source": source,
           "bid": bp["bid"] if bp else None, "at": parse_utc(bp["at"]) if bp else None}
    for key in ("stop_loss", "take_profit"):
        if order.get(key) is not None:
            out[key] = to_broker_away(order[key], entry, k, inst, be)
    return out


def offline_events(data_dir, now, cfg):
    """load_calendar(..., offline=True) without its download code: this week's saved
    feed file plus the built-in tables, each with "symbols", sorted by time."""
    week = calendar_week(now)
    doc = calendar_file_doc(os.path.join(data_dir, "calendar", week + ".json"))
    url = cfg.get("calendar_url", "")
    feed = parse_ff(doc) if (doc and url) else []
    start = ny_to_utc(at_time(datetime.date.fromisoformat(week), "00:00"))
    built = builtin_events(start, now + datetime.timedelta(days=CALENDAR_AHEAD_DAYS), cfg)
    events = feed + merge_builtin(feed, built, bool(doc and doc.get("ok")))
    for e in events:
        e["symbols"] = event_symbols(e, cfg)
    return sorted(events, key=lambda e: e["at"])


class Rates(object):
    """fx_rates, read once per run and only when needed."""

    def __init__(self, paths, now, cfg):
        self.paths, self.now, self.cfg, self.value = paths, now, cfg, None

    def get(self):
        if self.value is None:
            self.value = fx_rates(self.paths["data"], self.now, self.cfg)
        return self.value

    def has_for(self, inst):
        rates = self.get()
        need = {inst.get("profit_currency") or "USD", inst.get("margin_currency") or "USD"}
        return all(c in rates for c in need)


# ---------------------------------------------------------------- the ledger and the positions

def new_record(status, item, now, today, cfg, **fields):
    """A 4.10 record with every key; item is the order or the position it is about."""
    rec = dict((k, None) for k in RECORD_KEYS)
    rec.update(at=utc_iso(now), fx_day=today, status=status, plain="",
               paper=bool(item.get("paper", cfg.get("paper", True))) if "id" in item
               else bool(cfg.get("paper", True)))
    for k in ("symbol", "side", "intent", "lots", "style"):
        rec[k] = item.get(k)
    if rec["intent"] is None:
        rec["intent"] = "open"
    rec.update(fields)
    return rec


def add_record(path, rec):
    """Append one record to state/broker.json: a fresh read, then one atomic save."""
    doc = read_ledger({"broker_state": path})
    doc["records"].append(rec)
    save_json_atomic(path, doc)
    return rec


def answered(ledger, fp, today, position_id=None):
    """True when a "yes" or "no" record of today has this fingerprint (R-c)."""
    for r in ledger["records"]:
        if r.get("status") in ("yes", "no") and r.get("fingerprint") == fp and r.get("fx_day") == today:
            if position_id is None or r.get("position_id") == position_id:
                return True
    return False


def newest_record(ledger, fp, today, position_id=None):
    """The newest record of today with this fingerprint (and position id), or None."""
    hit = None
    for r in ledger["records"]:
        if r.get("fingerprint") == fp and r.get("fx_day") == today:
            if position_id is None or r.get("position_id") == position_id:
                hit = r
    return hit


def find_position(portfolio, pid):
    """The open position with this id, or None."""
    for p in (portfolio or {}).get("positions") or []:
        if p.get("id") == pid:
            return p
    return None


def no_position(portfolio, pid):
    """The error for an id that is not open."""
    ids = [p.get("id") for p in (portfolio or {}).get("positions") or []]
    if not ids:
        return ValueError("There is no open position %s. You have no open positions." % pid)
    return ValueError("There is no open position %s. Open positions: %s." % (pid, ", ".join(ids)))


def next_position_id(symbol, portfolio, ledger):
    """<symbol>-<n>, n one above every n used for the symbol (never reused, 01 5.9)."""
    used = [p.get("id") for p in (portfolio or {}).get("positions") or []]
    used += [c.get("id") for c in (portfolio or {}).get("closed") or []]
    used += [r.get("position_id") for r in ledger["records"]]
    top = 0
    for pid in used:
        m = re.match(r"^%s-(\d+)$" % re.escape(symbol), str(pid or ""))
        if m:
            top = max(top, int(m.group(1)))
    return "%s-%d" % (symbol, top + 1)


def open_price(pos):
    """The position's open price on the plan scale: the fill, else the entry."""
    return pos.get("fill_price") if pos.get("fill_price") is not None else pos.get("entry")


def update_swaps(portfolio, now, cfg, rates, out):
    """4.1 step 4: swap_nights and swap_usd so far for every open position."""
    try:
        r = rates.get()
    except ValueError:
        r = None
    for p in portfolio.get("positions") or []:
        inst = instrument(cfg, p["symbol"])
        opened = parse_utc(p.get("opened_at"))
        if opened is None:
            continue
        nights = swap_nights(opened, now, inst, cfg)
        try:
            if r is None or not rates.has_for(inst):
                raise ValueError("no rates")
            usd = swap_usd(inst, p["side"], p["lots"], nights, r, cfg, price=open_price(p))
        except ValueError:
            out.note("no FX rates in fx/data, so swap so far is not updated.")
            continue
        p["swap_nights"], p["swap_usd"] = nights, usd


def save_portfolio(paths, portfolio, now, cfg, rates, out):
    """Write portfolio.json (swaps updated, updated_at, created_by), one atomic save."""
    update_swaps(portfolio, now, cfg, rates, out)
    portfolio["updated_at"] = utc_iso(now)
    portfolio["created_by"] = SKILL
    save_json_atomic(paths["portfolio"], portfolio)


REBASE_FIELDS = ["entry", "fill_price", "stop_loss", "take_profit", "stop_at_open"]


def roll_pct(ratio):
    """"+0.90%" for a ratio of 1.0089992."""
    d = ((_dec_clean(ratio) - 1) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "%s%s%%" % ("+" if d > 0 else "", format(d, "f"))


def rebase_positions(paths, rolls, now, cfg, today, out, rates):
    """4.1 step 3: move each open futures position by the rolls it has not had (D2).
    The broker_* levels never change. Returns the plain lines printed."""
    pf = read_portfolio(paths, cfg)
    if not pf or not pf.get("positions"):
        return []
    by_id = dict((r.get("id"), r) for r in rolls or [])
    changes = []
    for p in pf["positions"]:
        inst = instrument(cfg, p["symbol"])
        if not inst.get("futures"):
            continue
        new, ids = rebase(p, rolls, REBASE_FIELDS, inst=inst)
        if ids:
            changes.append((p["id"], new, ids, inst))
    if not changes:
        return []
    pf = read_portfolio(paths, cfg)
    lines, said = [], set()
    for pid, new, ids, inst in changes:
        p = find_position(pf, pid)
        if p is None:
            continue
        for f in REBASE_FIELDS + ["rolls_applied"]:
            if f in new:
                p[f] = new[f]
        for rid in ids:
            ratio = by_id.get(rid, {}).get("ratio", 1.0)
            word = "%s rolled on Yahoo (%s)." % (inst_word(inst), roll_pct(ratio))
            plain = "%s %s moved: stop %s, target %s." % (
                word, pid, price_text(p["stop_loss"], inst),
                price_text(p["take_profit"], inst) if p.get("take_profit") is not None else "none")
            rec = new_record("rebase", p, now, today, cfg, roll_id=rid, intent="open",
                             price=p.get("stop_loss"), position_id=pid, plain=plain)
            add_record(paths["broker_state"], rec)
            if rid not in said:
                said.add(rid)
                line = ("%s Your plan levels moved %s by the same amount. Your broker's %s did not "
                        "jump: keep the SL and TP you set in MetaTrader."
                        % (word, "up" if float(ratio) >= 1 else "down", inst["broker_symbol"]))
                lines.append(line)
                out.say(line)
                out.notes.append(line)
    save_portfolio(paths, pf, now, cfg, rates, out)
    return lines


# ---------------------------------------------------------------- READY and the yes rule

def ready(order, fp, risk, ledger, portfolio, rolls, now, cfg, n=1):
    """4.2 step 3: (ok, reason, verdict, next step). The first test that fails is the reason."""
    inst = instrument(cfg, order["symbol"])
    today = last_fx_day(now, cfg)
    again = "Next: python3 fx-risk/check.py"
    budget = "Next: python3 fx-budget/size.py --write, then python3 fx-risk/check.py"
    checked = parse_utc((risk or {}).get("checked_at"))
    if not risk or checked is None:
        return False, "No risk check found. Run fx-risk first.", None, again
    hits = [v for v in risk.get("verdicts") or [] if v.get("fingerprint") == fp]
    if not hits:
        return False, "fx-risk has not checked this exact order. Run fx-risk again.", None, again
    rv = hits[-1]
    if rv.get("verdict") != "PASS":
        return False, "fx-risk said BLOCK for this order.", rv, again
    age = (now - checked).total_seconds() / 60.0
    if age < 0:
        return False, "The fx-risk check is from after the time now. Run fx-risk again.", rv, again
    if age > FRESH_CHECK_MINUTES:
        return False, ("The fx-risk check is %d min old. A PASS counts for %d minutes. Run fx-risk again."
                       % (int(age), FRESH_CHECK_MINUTES)), rv, again
    close = order.get("intent") == "close"
    if answered(ledger, fp, today, order.get("position_id") if close else None):
        return False, ("You already answered order %d today. Run fx-budget and fx-risk for a new order."
                       % n), rv, budget
    root = (inst.get("futures") or {}).get("root")
    if root:
        done = order.get("rolls_applied") or {}
        for r in rolls or []:
            if r.get("root") == root and (r.get("id") not in done
                                          or float(done[r["id"]]) != float(r.get("ratio"))):
                return False, "%s rolled. Run fx-budget again." % inst_word(inst), rv, budget
    if close:
        p = find_position(portfolio, order.get("position_id"))
        if p is None or p.get("symbol") != order["symbol"] or p.get("side") != order["side"]:
            return False, ("There is no open position %s to close." % order.get("position_id")), rv, None
        return True, "", rv, None
    st = market_state(inst, now, cfg)
    if st["state"] != "open":
        why = "The %s market is shut now" % inst_word(inst, lower=True)
        if st.get("note"):
            why += " (%s)" % st["note"].rstrip(".")
        why += "."
        if st.get("next_open"):
            why += " It opens %s." % pkt_label(st["next_open"])
        return False, why, rv, "Next: wait for the market to open, then python3 fx-risk/check.py"
    return True, "", rv, None


def _same_price(a, b, inst):
    """True when two prices are the same at the instrument's digits."""
    q = Decimal(1).scaleb(-int(inst["digits"]))
    return (_dec_clean(a).quantize(q, rounding=ROUND_HALF_UP)
            == _dec_clean(b).quantize(q, rounding=ROUND_HALF_UP))


def order_words(order, inst):
    """"BUY 0.02 lots XAUUSD, SL 2,400.00, TP 2,490.00" for the yes rule's words."""
    text = plain_order(order)
    if order.get("stop_loss") is not None:
        text += ", SL %s" % price_text(order["stop_loss"], inst)
    if order.get("take_profit") is not None:
        text += ", TP %s" % price_text(order["take_profit"], inst)
    return text


def parse_yes(reply, order, batch_symbols, known_symbols, rv):
    """Is this reply an explicit yes for THIS one order (00 6.10a, 4.11)? (ok, why).
    order may carry "_broker" (broker levels), "_position_lots" (closes) and "_digits"."""
    inst = {"digits": int(order.get("_digits", 5))}
    raw = reply or ""
    if not raw.strip():
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    low = raw.lower()
    words = re.findall(r"[a-z']+", low)
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it, haan)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    side, typ = order.get("side"), str(order.get("type") or "").lower()
    doubt = set()
    for i, w in enumerate(words[1:], 1):
        if w in DOUBT_WORDS:
            if w == "stop" and typ == "stop" and words[i - 1] in ("buy", "sell"):
                continue
            doubt.add(w)
    if doubt or DOUBT_PHRASE.search(low):
        return False, "the reply has a condition or a doubt in it; ask again"
    rest = set(words[1:])
    sym = str(order.get("symbol") or "").upper()
    bsym = str(order.get("broker_symbol") or sym).upper()
    if order.get("intent") == "close":
        if (SIDE_SAYS["buy"] | SIDE_SAYS["sell"]) & rest:
            return False, "This order closes your %s of %s. Reply yes or yes, close." % (side_word(side), sym)
        if rest - {"close", "lot", "lots", sym.lower(), bsym.lower()}:
            return False, "a close needs only yes or yes, close"
        lots = order.get("_position_lots", order.get("lots"))
        for n in NUMBER.findall(DOLLARS.sub(" ", raw)):
            if Decimal(n.replace(",", "")) != _dec_clean(lots):
                return False, "the reply says %s, but this close is for %s" % (n, lots_text(lots))
        return True, "explicit yes to close %s" % order.get("position_id")
    other = {"buy": "sell", "sell": "buy"}.get(side)
    said = SIDE_SAYS.get(other, set()) & rest
    if said:
        return False, "the reply says %s, but this order is a %s" % (", ".join(sorted(said)), side)
    said = CLOSE_SAYS & rest
    if said:
        return False, "the reply says %s, but this order opens a new trade" % ", ".join(sorted(said))
    for t, ws in sorted(TYPE_SAYS.items()):
        said = ws & rest if t != typ else set()
        if said:
            return False, "the reply says %s, but this is a %s order" % (", ".join(sorted(said)), typ)
    if typ == "limit" and ANY_PRICE.search(low):
        return False, "the reply says \"%s\", but this is a limit order" % ANY_PRICE.search(low).group(0)
    said = sorted(CHANGE_WORDS & rest) + TIMES.findall(raw)
    if said:
        return False, ("the reply asks for a change (%s); a changed order needs a new fx-risk check and "
                       "a new yes" % ", ".join(said))
    mine = {sym, bsym}
    caps = PLAIN_CAPS | {typ.upper()}
    text = raw.upper()
    tokens = set(t.rstrip(".-") for t in SYMBOL_TOKEN.findall(text))
    others = {str(s).upper() for s in list(batch_symbols or []) + list(known_symbols or []) if s}
    named = tokens & (others - mine)
    for t in WORD_TOKEN.findall(DOLLARS.sub(" ", raw)):
        t = t.rstrip(".-")
        if len(t) > 1 and re.search(r"[A-Z]", t) and t == t.upper() and t not in mine and t not in caps:
            named.add(t)
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    prices = [order.get(k) for k in ("entry", "stop_loss", "take_profit")]
    prices += [v for v in (order.get("_broker") or {}).values() if v is not None]
    prices = [p for p in prices if p is not None]
    loss = (rv or {}).get("max_loss_usd")
    wrong = []
    for n in NUMBER.findall(DOLLARS.sub(" ", raw)):
        d = Decimal(n.replace(",", ""))
        ok = d == _dec_clean(order["lots"]) or any(_same_price(d, p, inst) for p in prices)
        if not ok and loss is not None:
            ok = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == _dec_clean(loss).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not ok:
            wrong.append(n.rstrip(","))
    if wrong:
        return False, "the reply says %s, but this order is %s" % (", ".join(wrong), order_words(order, inst))
    return True, "explicit yes for %s" % plain_order(order)


# ---------------------------------------------------------------- the MetaTrader line

def mt_price(price, inst):
    """A price for a MetaTrader box: all the digits, no thousands commas: "2395.75"."""
    return price_text(price, inst).replace(",", "")


def volume_text(lots):
    """The lots for the Volume box: "0.02", "1.00", "0.005"."""
    return lots_text(lots).split(" ")[0]


def expiry_utc(order, today, cfg):
    """The order's expiry as naive UTC: None for "gtc"; "today" is the FX day's end."""
    exp = order.get("expiry")
    if exp in (None, "gtc"):
        return None
    if exp == "today":
        return fx_day_bounds(today, cfg["day_end"])[1]
    return parse_utc(exp)


def expiry_server(order, today, cfg):
    """06 2.5 expiry_server: "gtc", a "YYYY-MM-DD HH:MM" server time, or None (a close)."""
    if order.get("intent") == "close":
        return None
    t = expiry_utc(order, today, cfg)
    if t is None:
        return "gtc"
    return server_time(t, cfg["account"]["server_time"]).strftime("%Y-%m-%d %H:%M")


def mt_line(order, levels, cfg, inst, today=None):
    """6.4: the one line to type into MetaTrader (printed only after a yes)."""
    mt4 = str(cfg["account"].get("platform", "mt5")).lower() == "mt4"
    sym = inst["broker_symbol"]
    if order.get("intent") == "close":
        if mt4:
            return "In MetaTrader: Terminal > Trade > right click the %s row > Close Order." % sym
        return "In MetaTrader: Toolbox > Trade > right click the %s row > Close Position." % sym
    lv = levels or {"entry": order["entry"], "stop_loss": order.get("stop_loss"),
                    "take_profit": order.get("take_profit")}
    side = side_word(order["side"]).capitalize()
    parts = ["New order", "Symbol %s" % sym]
    if order["type"] == "market":
        parts += ["Market Execution", "Volume %s" % volume_text(order["lots"])]
    else:
        parts += ["Pending Order", "%s %s" % (side, order["type"].capitalize()),
                  "Volume %s" % volume_text(order["lots"]),
                  "%s %s" % ("At price" if mt4 else "Price", mt_price(lv["entry"], inst))]
    if lv.get("stop_loss") is not None:
        parts.append("Stop Loss %s" % mt_price(lv["stop_loss"], inst))
    if lv.get("take_profit") is not None:
        parts.append("Take Profit %s" % mt_price(lv["take_profit"], inst))
    if order["type"] == "market":
        parts.append("%s by Market" % side)
    else:
        server = expiry_server(order, today or last_fx_day(utc_now(), cfg), cfg)
        if server not in (None, "gtc"):
            parts.append("Expiry %s" % server.replace("-", ".") if mt4 else "Expiration %s" % server)
        parts.append("Place")
    return "In MetaTrader: %s." % " > ".join(parts)


def other_side(side):
    """"buy" -> "SELL", "sell" -> "BUY": the order that would open a second trade."""
    return side_word({"buy": "sell", "sell": "buy"}[side])


def close_note(cfg, side):
    """The line under a close's MetaTrader line (6.4); side is the position's side."""
    if cfg["account"].get("hedging", True):
        return ("Do not place a %s order to close it: on a hedging account that opens a second trade."
                % other_side(side))
    return "Your account is netting: the Close button is still the safe way."


# ---------------------------------------------------------------- the ticket (6.1)

def row(label, value):
    """One ticket row: the label in an 18-wide column."""
    return "  %s%s" % (label.ljust(18), value)


def distance_words(a, b, inst, below, about=False):
    """"33.49 below the price (3,349 points)" for the distance between a and b."""
    head, tail = pips_text(abs(_dec_clean(a) - _dec_clean(b)), inst).split(" (", 1)
    return "%s%s %s the price (%s" % ("about " if about else "", head, "below" if below else "above", tail)


def expiry_words(order, cfg, today, now):
    """6.1's expiry words."""
    exp = order.get("expiry")
    pending = order.get("type") != "market"
    plan = order.get("plan") or {}
    if exp in (None, "gtc"):
        text = "good till cancelled"
        exit_by = parse_utc(plan.get("exit_by"))
        if order.get("style") == "swing" and exit_by is not None:
            text += " (swing: up to %s FX days, to %s)" % (cfg["swing"]["max_hold_days"],
                                                           pkt_label(exit_by, date=True))
        return text
    t = expiry_utc(order, today, cfg)
    server = expiry_server(order, today, cfg)
    if exp == "today":
        text = "until the FX day ends, %s" % time_pair(t)
    elif pending:
        text = "cancel at %s" % time_pair(t)
    else:
        day = "today" if to_pkt(t).date() == to_pkt(now).date() else short_date(to_pkt(t))
        return "close by %s %s (flat-by)" % (time_pair(t), day)
    return "%s: %s server time" % (text, server) if pending else text


def money_words(order, rv, inst, cfg):
    """The max loss sentence with its split (4.2 step 5), as one string."""
    plan = order.get("plan") or {}
    loss = (rv or {}).get("max_loss_usd")
    if loss is None:
        loss = plan.get("max_loss_usd")
    if loss is None:
        return None
    text = "Max loss at the stop: %s, costs included" % money(loss)
    p_loss, costs = plan.get("max_loss_usd"), plan.get("costs_usd")
    if p_loss is not None and costs is not None and abs(float(p_loss) - float(loss)) <= 0.01:
        nights = int(plan.get("nights_est") or 0)
        comm = _commission(inst, order["lots"])
        fee = _fee(inst, order["lots"], nights, cfg)
        swap = _dec_clean(costs) - _dec_clean(_cents(comm)) - _dec_clean(_cents(fee))
        parts = ["%s price and spread" % money(_dec_clean(p_loss) - _dec_clean(costs))]
        if comm > 0:
            parts.append("%s commission" % money(comm))
        if _cents(swap) > 0:
            parts.append("%s swap for %d %s" % (money(swap), nights, "night" if nights == 1 else "nights"))
        if fee > 0:
            parts.append("%s swap-free fee" % money(fee))
        text += " (%s)" % ", ".join(parts)
    return text + "."


def ticket_lines(order, verdict, levels, yahoo, cfg, inst, now, ctx=None):
    """6.1: (block lines, lines after the block, notes) of one ticket. ctx: n, total,
    today, position (closes), events, bars_5m, plan (plan.json)."""
    ctx = ctx or {}
    n, total, today = ctx.get("n", 1), ctx.get("total", 1), ctx.get("today") or last_fx_day(now, cfg)
    paper = bool(cfg.get("paper", True))
    lines = ["Order %d of %d (%s)" % (n, total, "paper" if paper else "LIVE: real money")]
    after, notes = [], []
    sym, side = order["symbol"], order["side"]
    if yahoo:
        now_row = row("Price now", "%s   %s, %s" % (price_text(yahoo["bid"], inst), scale_note(inst),
                                                    age_text(yahoo["age_min"])))
    else:
        now_row = row("Price now", "No Yahoo price for %s in fx/data yet. Run fx-alerts quote %s." % (sym, sym))
    if order.get("intent") == "close":
        pos = ctx.get("position") or {}
        opened = parse_utc(pos.get("opened_at"))
        lines.append("  Close %s: your %s of %s, %s%s" % (
            order.get("position_id"), side_word(side), sym, lots_text(order["lots"]),
            ", opened %s" % pkt_label(opened) if opened else ""))
        lines.append(now_row)
        lines.append("  Use the position's Close button in MetaTrader. Not a new %s order." % other_side(side))
        return lines, after, notes
    typ = order["type"]
    head = "market" if typ == "market" else "%s order at %s" % (typ, price_text(order["entry"], inst))
    lines.append("  %s %s, %s, %s" % (side_word(side), inst["broker_symbol"], head, lots_text(order["lots"])))
    buy = side == "buy"
    ref = levels["entry"] if levels else order["entry"]
    about = levels is None
    for label, key, below in (("Stop loss (SL)", "stop_loss", buy), ("Take profit (TP)", "take_profit", not buy)):
        if order.get(key) is None:
            continue
        lv = levels[key] if levels else None
        words = distance_words(ref, lv if lv is not None else order[key], inst, below, about)
        lines.append(row(label, "%s   %s" % (price_text(lv, inst), words) if lv is not None else words))
    lines.append(now_row)
    bp = ctx.get("bp")
    plan_words = "entry %s" % price_text(order["entry"], inst)
    for word, key in (("SL", "stop_loss"), ("TP", "take_profit")):
        if order.get(key) is not None:
            plan_words += ", %s %s" % (word, price_text(order[key], inst))
    if bp:
        at = parse_utc(bp.get("at"))
        lines.append(row("Your price", "%s   %s, %s" % (price_text(bp["bid"], inst), broker_note(),
                                                       age_text(data_age_min(at, now) if at else None))))
        lines.append(row("Broker levels", "entry %s (Yahoo plan: %s)" % (price_text(levels["entry"], inst),
                                                                        plan_words)))
    elif levels is None:
        lines.append(row("Plan levels", "%s (Yahoo's scale, not your broker's)" % plan_words))
        after.append("Type your MetaTrader price with --broker-price to get exact levels.")
    lines.append(row("Expiry", expiry_words(order, cfg, today, now)))
    loss = money_words(order, verdict, inst, cfg)
    if loss:
        lines += textwrap.wrap(loss, width=76, initial_indent="  ", subsequent_indent="  ")
    margin = (verdict or {}).get("margin_usd")
    if margin is None:
        margin = (order.get("plan") or {}).get("margin_usd")
    if margin is not None:
        lines.append("  Margin used: about %s." % money(margin))
    if levels and yahoo and yahoo["age_min"] >= 1:
        err = delay_error(inst, yahoo["age_min"], ctx.get("bars_5m") or [])
        if err:
            usd = (inst.get("profit_currency") or "USD") == "USD"
            after.append("Yahoo's price was %s, so these levels may be off by about %s%s. Check them "
                         "against your MetaTrader chart." % (age_text(yahoo["age_min"]),
                                                             "$" if usd else "", price_text(err, inst)))
    if levels and inst["kind"] == "energy" and inst.get("price_source") == "futures":
        name = str(inst.get("name") or "")
        word = "Brent" if "brent" in name.lower() else ("Gas" if "gas" in name.lower() else "Oil")
        after.append("%s CFDs can follow another month than Yahoo's futures. The gap moves at each "
                     "monthly roll." % word)
    notes += ticket_notes(order, verdict, cfg, inst, now, today, ctx)
    return lines, after, notes


def next_rollover(now):
    """The next 5:00 PM New York rollover after now, as naive UTC."""
    n = to_ny(now)
    t = ny_to_utc(datetime.datetime.combine(n.date(), datetime.time(17)))
    if t <= now:
        t = ny_to_utc(datetime.datetime.combine(n.date() + datetime.timedelta(days=1), datetime.time(17)))
    return t


def ticket_notes(order, verdict, cfg, inst, now, today, ctx):
    """4.2 step 6: the notes of one open ticket, in order (without "Note: ")."""
    notes = []
    if order["side"] == "buy":
        notes.append("a buy opens at the ask and its stop fires on the bid.")
    else:
        notes.append("a sell opens at the bid and its stop fires on the ask.")
    plan = order.get("plan") or {}
    end = parse_utc(plan.get("flat_by_utc")) if order.get("style") == "day" else None
    if end is None:
        end = now + datetime.timedelta(hours=24)
    impacts = cfg["news"]["impacts"]
    for e in next_events(ctx.get("events") or [], [order["symbol"]], now, 3, impacts=impacts):
        if e.get("all_day") or e["at"] > end:
            continue
        impact = event_impact(e, order["symbol"]) or ""
        notes.append("there is a %s-impact event at %s: %s. Prices can jump then."
                     % (impact.lower(), both_times(e["at"]), event_label(e)))
    swing = order.get("style") == "swing"
    if swing:
        roll = next_rollover(now)
        notes.append("this is a swing trade. Swap is booked each night at %s (%s PKT)."
                     % (ny_label(roll), clock_label(to_pkt(roll))))
        if parse_fx_date(today).weekday() == 4:
            notes.append("The market closes for the weekend. On Monday the price can open past your stop.")
    notes.append("A stop is not a promise. If the price jumps, it fills at the next price.")
    for w in (verdict or {}).get("warnings") or []:
        text = str(w)
        if re.match(r"^W-[A-Za-z0-9_-]+ ", text):
            text = text.split(" ", 1)[1]
        notes.append(text)
    return notes


# ---------------------------------------------------------------- the subcommands

class Run(object):
    """What every subcommand needs (4.1 steps 1 and 2)."""

    def __init__(self, args, cmd):
        self.args, self.cmd = args, cmd
        work = (os.path.dirname(args.config) or ".") if args.config else "fx"
        self.paths = fx_paths(work, args.data, args.config)
        self.cfg = load_config(self.paths["config"], required=bool(args.config))
        self.now = parse_now(args.now)
        self.today = last_fx_day(self.now, self.cfg)
        rolls = load_rolls(self.paths["data"])
        if args.now:
            rolls = [r for r in rolls if parse_utc(r.get("found_at")) is not None
                     and parse_utc(r["found_at"]) <= self.now]
        self.rolls = rolls
        self.out = Out(args, cmd, self.now)
        self.rates = Rates(self.paths, self.now, self.cfg)
        self._prices = None

    def prices(self):
        if self._prices is None:
            self._prices = read_prices(self.paths)
        return self._prices

    def rebase(self):
        return rebase_positions(self.paths, self.rolls, self.now, self.cfg, self.today, self.out, self.rates)

    def order(self, index):
        """(orders doc, orders, the order N): N must exist."""
        doc, orders = read_orders(self.paths, self.cfg)
        if index is None or index < 1 or index > len(orders):
            raise ValueError("There is no order %s. %s has %d %s." % (
                index, self.paths["orders"], len(orders), "order" if len(orders) == 1 else "orders"))
        return doc, orders, orders[index - 1]

    def bp_for(self, order, inst, typed):
        """The broker price of 4.2 step 4: typed (checked, not saved yet) or saved; None."""
        if order.get("intent") == "close":
            return None, None
        if typed is not None:
            y = yahoo_now(self.paths, inst, self.now, self.cfg, self.prices())
            if y is None:
                raise ValueError("No Yahoo price for %s in fx/data. Run fx-alerts quote %s first."
                                 % (inst["key"], inst["key"]))
            broker_k(inst, typed, y["yahoo"])
            return {"bid": typed, "at": utc_iso(self.now), "yahoo_bid": y["yahoo"],
                    "yahoo_at": utc_iso(y["at"])}, y
        return saved_broker_price(self.prices(), order["symbol"], self.now, self.cfg, self.rolls), None


def verdict_of(risk, fp):
    """The newest verdict of state/risk.json with this fingerprint, or None."""
    hits = [v for v in (risk or {}).get("verdicts") or [] if v.get("fingerprint") == fp]
    return hits[-1] if hits else None


def order_extra(order, levels, position=None):
    """The order plus the keys parse_yes reads (4.3 step 2)."""
    x = dict(order)
    if levels:
        x["_broker"] = {k: levels[k] for k in ("entry", "stop_loss", "take_profit")}
    if position:
        x["_position_lots"] = position.get("lots")
    return x


def ready_line(ok, why, risk):
    """The first line of a ticket: READY or NOT READY and why."""
    if ok:
        checked = parse_utc(risk["checked_at"])
        return ("READY: fx-risk PASS for this exact order (checked %s PKT). Waiting for your yes."
                % clock_label(to_pkt(checked)))
    return "NOT READY: %s" % why


def ticket_json(n, order, fp, ok, why, inst, levels, rv, yahoo, run):
    """One 06 2.5 ticket object."""
    close = order.get("intent") == "close"
    dist = lambda key: (None if close or order.get(key) is None else
                        float(abs(_dec_clean((levels or order)["entry"]) - _dec_clean((levels or order)[key]))
                              .quantize(Decimal(1).scaleb(-int(inst["digits"])))))
    return {"index": n, "fingerprint": fp, "ready": ok, "why_not": None if ok else why,
            "broker_symbol": inst["broker_symbol"], "side": order["side"], "type": order["type"],
            "lots": order["lots"], "entry": order.get("entry"), "stop_loss": order.get("stop_loss"),
            "take_profit": order.get("take_profit"),
            "expiry_server": expiry_server(order, run.today, run.cfg),
            "stop_distance": dist("stop_loss"), "target_distance": dist("take_profit"),
            "broker": ({"k": levels["k"], "entry": levels["entry"], "stop_loss": levels["stop_loss"],
                        "take_profit": levels["take_profit"]} if levels else None),
            "max_loss_usd": (rv or {}).get("max_loss_usd", (order.get("plan") or {}).get("max_loss_usd")),
            "data_age_min": round(yahoo["age_min"], 1) if yahoo else None}


def cmd_show(run):
    a, out, cfg = run.args, run.out, run.cfg
    typed = price_arg(a.broker_price, "--broker-price")
    run.rebase()
    doc, orders = read_orders(run.paths, cfg)
    if a.index is not None:
        run.order(a.index)
    elif typed is not None and len(orders) > 1:
        raise ValueError("--broker-price is for one order. Add --index N.")
    risk, ledger = read_risk(run.paths), read_ledger(run.paths)
    pf = read_portfolio(run.paths, cfg)
    events = None
    tickets, any_ready, why_last = [], False, None
    for n in ([a.index] if a.index else range(1, len(orders) + 1)):
        order = orders[n - 1]
        inst = instrument(cfg, order["symbol"])
        fp = fingerprint(order)
        ok, why, rv, nxt = ready(order, fp, risk, ledger, pf, run.rolls, run.now, cfg, n)
        bp, y = run.bp_for(order, inst, typed)
        if typed is not None and order.get("intent") != "close":
            bp = save_broker_price(run.paths, inst, typed, run.now, cfg, run.prices(), y)
            run._prices = None
        levels = broker_levels(order, inst, bp)
        yahoo = yahoo_now(run.paths, inst, run.now, cfg, run.prices())
        bars = []
        if levels and yahoo and yahoo["age_min"] >= 1:
            bars = load_bars(run.paths["data"], inst["yahoo"], "5m", run.now, rolls=run.rolls, inst=inst, cfg=cfg)
        if events is None and order.get("intent") != "close":
            events = offline_events(run.paths["data"], run.now, cfg)
        ctx = {"n": n, "total": len(orders), "today": run.today, "bp": bp, "bars_5m": bars,
               "events": events, "position": find_position(pf, order.get("position_id"))}
        block, after, notes = ticket_lines(order, rv, levels, yahoo, cfg, inst, run.now, ctx)
        if tickets:
            out.say()
        out.say(ready_line(ok, why, risk))
        out.say()
        for line in block:
            out.say(line)
        if after or (notes and not a.quiet):
            out.say()
        for line in after:
            out.wrap(line)
        for text in notes:
            out.note(text)
        any_ready = any_ready or ok
        why_last = why_last or nxt
        tickets.append(ticket_json(n, order, fp, ok, why, inst, levels, rv, yahoo, run))
    out.say()
    if any_ready:
        out.say("Type yes to approve this exact order. Anything else is a no.")
    else:
        out.say(why_last or "Next: python3 fx-risk/check.py")
    out.keys["tickets"] = tickets
    return 0 if any_ready else 1


def known_symbols(cfg):
    """Every instrument key of the config and every broker_symbol."""
    out = set()
    for key in cfg.get("instruments") or {}:
        out.add(key)
        out.add(instrument(cfg, key)["broker_symbol"])
    return out


def batch_symbols(orders, n, cfg):
    """The symbols and broker symbols of the OTHER orders in the file."""
    out = set()
    for i, o in enumerate(orders, 1):
        if i != n:
            out.add(o["symbol"])
            out.add(instrument(cfg, o["symbol"])["broker_symbol"])
    return out


def levels_words(levels, inst, now):
    """"entry 2,429.24, SL 2,395.75, TP 2,485.75 (from your broker's price ...)"."""
    text = "entry %s" % price_text(levels["entry"], inst)
    for word, key in (("SL", "stop_loss"), ("TP", "take_profit")):
        if levels.get(key) is not None:
            text += ", %s %s" % (word, price_text(levels[key], inst))
    if levels["source"] == "spot":
        return text + " (spot FX: Yahoo's price is close to your broker's)"
    age = data_age_min(levels["at"], now) if levels.get("at") else 0
    return text + " (from %s, %s%s)" % (broker_note(), price_text(levels["bid"], inst),
                                       ", %s" % age_text(age) if age >= 1 else "")


def cmd_approve(run):
    a, out, cfg = run.args, run.out, run.cfg
    typed = price_arg(a.broker_price, "--broker-price")
    doc, orders, order = run.order(a.index)
    n = a.index
    inst = instrument(cfg, order["symbol"])
    fp = fingerprint(order)
    risk, ledger, pf = read_risk(run.paths), read_ledger(run.paths), read_portfolio(run.paths, cfg)
    out.keys.update(record=None, line=None)
    ok, why, rv, nxt = ready(order, fp, risk, ledger, pf, run.rolls, run.now, cfg, n)
    if not ok:
        out.say("NOT READY: %s Nothing was recorded." % why)
        if nxt:
            out.say(nxt)
        return 1
    close = order.get("intent") == "close"
    pos = find_position(pf, order.get("position_id")) if close else None
    bp, y = run.bp_for(order, inst, typed)
    levels = broker_levels(order, inst, bp)
    x = order_extra(order, levels, pos)
    x["_digits"] = int(inst["digits"])
    yes, why = parse_yes(a.reply, x, batch_symbols(orders, n, cfg), known_symbols(cfg), rv)
    if not yes:
        if why.startswith("This order closes"):
            out.say(why)
            out.say("Not recorded. Ask again with the ticket for order %d." % n)
        else:
            out.say("Not recorded: %s. Ask again with the ticket for order %d." % (why, n))
        return 1
    if not close and levels is None:
        out.say("%s needs your MetaTrader price for exact SL and TP levels. Ask for the %s bid in "
                "MetaTrader, then run approve again with --broker-price P."
                % (inst_word(inst), inst["broker_symbol"]))
        out.say("Nothing was recorded.")
        return 1
    if typed is not None and not close:
        save_broker_price(run.paths, inst, typed, run.now, cfg, run.prices(), y)
    if close:
        plain = "You said yes to close %s." % order.get("position_id")
    else:
        plain = "You said yes to %s." % plain_order(order)
    rec = new_record("yes", order, run.now, run.today, cfg, index=n, fingerprint=fp, reply=a.reply,
                     max_loss_usd=None if close else (rv or {}).get("max_loss_usd"),
                     broker_price=levels["entry"] if levels else None,
                     position_id=order.get("position_id") if close else None, plain=plain)
    add_record(run.paths["broker_state"], rec)
    line = mt_line(order, levels, cfg, inst, run.today)
    out.keys.update(record=rec, line=line)
    if close:
        out.say("Recorded your yes for order %d: close %s (your %s of %s, %s)." % (
            n, order.get("position_id"), side_word(order["side"]), order["symbol"], lots_text(order["lots"])))
        out.say(line)
        out.say(close_note(cfg, order["side"]))
        if cfg.get("paper", True):
            out.say("Paper trade: do not close it in MetaTrader. Record the paper close:")
            out.say("  python3 fx-broker/ticket.py result --index %d --status filled" % n)
        else:
            out.say("Close it in your own MetaTrader. Then record it: python3 fx-broker/ticket.py result "
                    "--index %d --status filled --fill P (the price MetaTrader shows)." % n)
        return 0
    typ = "market" if order["type"] == "market" else "%s order" % order["type"]
    out.say("Recorded your yes for order %d: %s %s, %s, %s." % (
        n, side_word(order["side"]), inst["broker_symbol"], typ, lots_text(order["lots"])))
    out.say("  Broker levels: %s." % levels_words(levels, inst, run.now))
    out.say(line)
    if cfg.get("paper", True):
        out.say("Paper trade: do not place it in MetaTrader. Record the paper fill:")
        out.say("  python3 fx-broker/ticket.py result --index %d --status filled" % n)
    else:
        out.say("Place it in your own MetaTrader. Then record it: python3 fx-broker/ticket.py result "
                "--index %d --status filled --fill P (the price MetaTrader shows)." % n)
    return 0


def cmd_decline(run):
    a, out, cfg = run.args, run.out, run.cfg
    doc, orders, order = run.order(a.index)
    fp = fingerprint(order)
    close = order.get("intent") == "close"
    ledger = read_ledger(run.paths)
    out.keys.update(record=None, line=None)
    if answered(ledger, fp, run.today, order.get("position_id") if close else None):
        out.say("You already answered order %d today. Run fx-budget and fx-risk for a new order." % a.index)
        return 1
    what = ("close %s" % order.get("position_id")) if close else plain_order(order)
    rec = new_record("no", order, run.now, run.today, cfg, index=a.index, fingerprint=fp, reply=a.reply,
                     position_id=order.get("position_id") if close else None,
                     plain="You said no to %s." % what)
    add_record(run.paths["broker_state"], rec)
    out.keys["record"] = rec
    out.say("Recorded your no for order %d. Nothing goes to MetaTrader." % a.index)
    return 0


def live_k(run, order, inst):
    """k for a live fill: from the saved broker price, 1.0 for spot FX, else None."""
    bp = saved_broker_price(run.prices(), order["symbol"], run.now, run.cfg, run.rolls)
    if bp:
        return broker_k(inst, bp["bid"], bp["yahoo_bid"])
    return 1.0 if inst.get("price_source") == "spot_fx" else None


def fill_open(run, n, order, fp, inst, rv):
    """4.5 step 4: an open order filled. Adds the position and the "filled" record."""
    a, out, cfg = run.args, run.out, run.cfg
    paper = bool(cfg.get("paper", True))
    fill = price_arg(a.fill, "--fill")
    broker_fill = None
    if paper:
        price = round_price(fill, inst) if fill is not None else order["entry"]
        y = yahoo_now(run.paths, inst, run.now, cfg, run.prices())
        if fill is None and y and abs(_dec_clean(y["bid"]) - _dec_clean(price)) > _dec_clean(inst["tick_size"]):
            out.note("Yahoo's price now is %s. The paper fill uses the order price %s. Give --fill P to "
                     "use another price." % (price_text(y["bid"], inst), price_text(price, inst)))
    else:
        if fill is None:
            raise ValueError("A live fill needs --fill P: the price MetaTrader shows for the fill.")
        broker_fill = fill
        k = live_k(run, order, inst)
        if k is None:
            price = order["entry"]
            out.note("no broker price saved, so the plan keeps Yahoo's entry %s." % price_text(price, inst))
        else:
            price = round_price(fill / k, inst)
    bp = saved_broker_price(run.prices(), order["symbol"], run.now, cfg, run.rolls)
    levels = broker_levels(order, inst, bp)
    plan = order.get("plan") or {}
    loss = (rv or {}).get("max_loss_usd", plan.get("max_loss_usd"))
    if not _same_price(price, order["entry"], inst) and order.get("stop_loss") is not None:
        loss = max_loss_usd(inst, order["side"], order["lots"], price, order["stop_loss"],
                            int(plan.get("nights_est") or 0), run.rates.get(), cfg)
    pf = read_portfolio(run.paths, cfg) or new_portfolio(cfg, run.now)
    ledger = read_ledger(run.paths)
    pid = next_position_id(order["symbol"], pf, ledger)
    pos = {"id": pid, "symbol": order["symbol"], "side": order["side"], "style": order["style"],
           "lots": order["lots"], "fingerprint": fp, "paper": paper, "opened_at": utc_iso(run.now),
           "fx_day_opened": run.today, "entry": order["entry"], "fill_price": price,
           "broker_fill_price": broker_fill, "stop_loss": order.get("stop_loss"),
           "take_profit": order.get("take_profit"), "stop_at_open": order.get("stop_loss"),
           "broker_stop_loss": levels["stop_loss"] if levels else None,
           "broker_take_profit": levels["take_profit"] if levels else None,
           "max_loss_usd": loss, "flat_by_utc": plan.get("flat_by_utc"), "exit_by": plan.get("exit_by"),
           "rolls_applied": dict(order.get("rolls_applied") or {}), "swap_nights": 0, "swap_usd": 0.0}
    pf["positions"].append(pos)
    save_portfolio(run.paths, pf, run.now, cfg, run.rates, out)
    words = "%s %s at %s" % (side_word(order["side"]), lots_text(order["lots"]) + " " + order["symbol"],
                             price_text(broker_fill if broker_fill is not None else price, inst))
    plain = ("Paper fill: %s." if paper else "Filled in MetaTrader: %s.") % words
    rec = new_record("filled", order, run.now, run.today, cfg, index=n, fingerprint=fp, price=price,
                     broker_price=broker_fill, position_id=pid, max_loss_usd=loss, plain=plain)
    add_record(run.paths["broker_state"], rec)
    out.keys["record"] = rec
    out.say("Recorded order %d as filled." % n)
    out.say("%s Position %s." % (plain, pid))
    out.say("Next: python3 fx-alerts/alerts.py add --from-plan (watch the stop and the target).")
    flat = parse_utc(pos.get("flat_by_utc"))
    if order["style"] == "day" and flat is not None:
        out.say("Be flat by %s: python3 fx-broker/ticket.py flatten." % time_pair(flat))
    return 0


def close_why(position, exit_price, now, inst):
    """4.5 step 5: why the trade ended, from the exit price and the clock."""
    side = position["side"]
    if position.get("take_profit") is not None and _same_price(exit_price, position["take_profit"], inst):
        return "target"
    stop = position.get("stop_loss")
    if stop is not None:
        e, s_ = _dec_clean(exit_price), _dec_clean(stop)
        if _same_price(exit_price, stop, inst) or (e < s_ if side == "buy" else e > s_):
            return "stop"
    flat = parse_utc(position.get("flat_by_utc"))
    if flat is not None and now >= flat - datetime.timedelta(minutes=15):
        return "flat_by"
    exit_by = parse_utc(position.get("exit_by"))
    if exit_by is not None and now >= exit_by - datetime.timedelta(minutes=60):
        return "exit_by"
    return "manual"


def exit_prices(run, pos, inst):
    """4.5 step 5: (exit price on the plan scale, broker exit price or None) from --fill
    or the Yahoo bid now (paper only)."""
    fill = price_arg(run.args.fill, "--fill")
    if run.cfg.get("paper", True):
        if fill is not None:
            return round_price(fill, inst), None
        y = yahoo_now(run.paths, inst, run.now, run.cfg, run.prices())
        if y is None:
            raise ValueError("No Yahoo price for %s in fx/data. Give --fill P." % pos["symbol"])
        return (y["bid"] if pos["side"] == "buy" else ask_of(y["bid"], inst)), None
    if fill is None:
        raise ValueError("A live fill needs --fill P: the price MetaTrader shows for the fill.")
    k = live_k(run, pos, inst)
    return (round_price(fill / k, inst) if k else round_price(fill, inst)), fill


def close_position(run, pos, index, fp, already=False):
    """4.5 step 5: close a position on its exit price. Writes portfolio.json and the
    "closed" record; prints the close lines. Returns 0."""
    out, cfg = run.out, run.cfg
    inst = instrument(cfg, pos["symbol"])
    exit_price, broker_exit = exit_prices(run, pos, inst)
    why = run.args.why or close_why(pos, exit_price, run.now, inst)
    rates = run.rates.get()
    if not run.rates.has_for(inst):
        raise ValueError("No FX rates in fx/data, so the result cannot be worked out. Run fx-alerts "
                         "quote %s first." % pos["symbol"])
    opened = parse_utc(pos["opened_at"])
    nights = swap_nights(opened, run.now, inst, cfg)
    o_price = open_price(pos)
    a, b = o_price, exit_price
    if pos.get("broker_fill_price") is not None and broker_exit is not None:
        a, b = pos["broker_fill_price"], broker_exit
    result = result_usd(inst, pos["side"], pos["lots"], a, b, nights, rates, cfg)
    swap = swap_usd(inst, pos["side"], pos["lots"], nights, rates, cfg, price=o_price)
    costs = costs_usd(inst, pos["side"], pos["lots"], nights, rates, cfg, o_price)
    paper = bool(pos.get("paper", cfg.get("paper", True)))
    pf = read_portfolio(run.paths, cfg)
    live = find_position(pf, pos["id"])
    if live is None:
        raise no_position(pf, pos["id"])
    pf["positions"] = [p for p in pf["positions"] if p.get("id") != pos["id"]]
    pf["closed"].append({"id": pos["id"], "symbol": pos["symbol"], "side": pos["side"], "style": pos["style"],
                         "lots": pos["lots"], "opened_at": pos["opened_at"], "closed_at": utc_iso(run.now),
                         "fx_day_closed": run.today, "entry": o_price, "exit_price": exit_price,
                         "broker_exit_price": broker_exit, "result_usd": result, "costs_usd": costs,
                         "swap_usd": swap, "why": why, "paper": paper})
    cut = run.now - datetime.timedelta(days=CLOSED_DAYS)
    pf["closed"] = [c for c in pf["closed"] if (parse_utc(c.get("closed_at")) or run.now) >= cut]
    acc = pf["account"]
    acc["balance"] = _cents(_dec_clean(acc.get("balance") or 0) + _dec_clean(result))
    acc["realized_usd"] = _cents(_dec_clean(acc.get("realized_usd") or 0) + _dec_clean(result))
    save_portfolio(run.paths, pf, run.now, cfg, run.rates, out)
    shown = broker_exit if broker_exit is not None else exit_price
    head = "Paper close" if paper else "Closed in MetaTrader"
    plain = "%s: %s %s %s at %s. Result %s after costs." % (
        head, pos["id"], side_word(pos["side"]), lots_text(pos["lots"]), price_text(shown, inst),
        signed_money(result))
    if already:
        plain += " (recorded after MetaTrader closed it)"
    rec = new_record("closed", pos, run.now, run.today, cfg, index=index, fingerprint=fp, intent="close",
                     price=exit_price, broker_price=broker_exit, result_usd=result,
                     position_id=pos["id"], plain=plain)
    add_record(run.paths["broker_state"], rec)
    out.keys["record"] = rec
    if index is not None:
        out.say("Recorded order %d as filled." % index)
    out.say("%s: %s %s %s at %s (%s). Result %s after all costs." % (
        head, pos["id"], side_word(pos["side"]), lots_text(pos["lots"]), price_text(shown, inst),
        why.replace("_", "-"), signed_money(result)))
    stop = pos.get("stop_loss")
    if why == "stop" and stop is not None and not _same_price(exit_price, stop, inst):
        planned = result_usd(inst, pos["side"], pos["lots"], o_price, stop, nights, rates, cfg)
        out.say("Your stop was at %s. The price opened past it, at %s. The stop filled there. You lost "
                "%s more than planned." % (price_text(stop, inst), price_text(exit_price, inst),
                                           money(abs(planned - result))))
    out.say("Balance now %s (%s)." % (money(acc["balance"]), "paper" if paper else "live"))
    return 0


def cmd_result(run):
    a, out, cfg = run.args, run.out, run.cfg
    price_arg(a.fill, "--fill")
    run.rebase()
    doc, orders, order = run.order(a.index)
    n = a.index
    inst = instrument(cfg, order["symbol"])
    fp = fingerprint(order)
    close = order.get("intent") == "close"
    out.keys.update(record=None, line=None)
    ledger = read_ledger(run.paths)
    last = newest_record(ledger, fp, run.today, order.get("position_id") if close else None)
    if last is None:
        out.say("No yes is recorded for order %d today. Run approve first." % n)
        return 1
    if last["status"] in ("filled", "closed"):
        out.say("Order %d is already filled (%s)." % (n, last.get("position_id") or order["symbol"]))
        return 1
    if last["status"] not in ("yes", "placed"):
        out.say("Order %d is closed in the ledger (status: %s). It needs a new fx-risk check and a new yes."
                % (n, last["status"]))
        return 1
    if a.why and not close:
        raise ValueError("--why is for closes only.")
    what = ("close %s" % order.get("position_id")) if close else plain_order(order)
    if a.status in ("skipped", "failed", "cancelled", "expired"):
        plain = "Order %d (%s) %s" % (n, what, a.status)
        plain += ": %s." % a.note.rstrip(".") if a.note else "."
        rec = new_record(a.status, order, run.now, run.today, cfg, index=n, fingerprint=fp,
                         position_id=order.get("position_id") if close else None, plain=plain)
        add_record(run.paths["broker_state"], rec)
        out.keys["record"] = rec
        out.say("Recorded order %d as %s." % (n, a.status))
        return 0
    paper = bool(cfg.get("paper", True))
    pending = order.get("type") in ("stop", "limit")
    if a.status == "placed" and (not paper or pending):
        if last["status"] == "placed":
            out.say("Order %d is already placed. Record the fill: python3 fx-broker/ticket.py result "
                    "--index %d --status filled" % (n, n))
            return 1
        typ = "" if close or not pending else " %s" % order["type"].upper()
        at = "" if close else " at %s" % price_text(order["entry"], inst)
        words = "%s%s %s %s%s" % (side_word(order["side"]), typ, lots_text(order["lots"]), order["symbol"], at)
        if close:
            words = what
        plain = ("Paper order waits: %s." if paper else "Placed in MetaTrader: %s.") % words
        if a.note:
            plain += " %s" % a.note
        rec = new_record("placed", order, run.now, run.today, cfg, index=n, fingerprint=fp,
                         position_id=order.get("position_id") if close else None, plain=plain)
        add_record(run.paths["broker_state"], rec)
        out.keys["record"] = rec
        out.say("Recorded order %d as placed." % n)
        if paper:
            out.say("Paper: the order waits at %s. When the price gets there, record it: python3 "
                    "fx-broker/ticket.py result --index %d --status filled" % (price_text(order["entry"], inst), n))
        return 0
    if a.status == "placed":
        rec = new_record("placed", order, run.now, run.today, cfg, index=n, fingerprint=fp,
                         position_id=order.get("position_id") if close else None,
                         plain="Paper order placed: %s." % what)
        add_record(run.paths["broker_state"], rec)
    risk = read_risk(run.paths)
    rv = verdict_of(risk, fp)
    if not close:
        return fill_open(run, n, order, fp, inst, rv)
    pf = read_portfolio(run.paths, cfg)
    pos = find_position(pf, order.get("position_id"))
    if pos is None:
        raise no_position(pf, order.get("position_id"))
    return close_position(run, pos, n, fp)


def close_order(position, today, now, cfg=None):
    """4.6 step 2: ONE close order for an open position (00 4.5, D12: the position's side)."""
    opened = parse_utc(position.get("opened_at"))
    bsym = position["symbol"]
    if cfg is not None:
        bsym = instrument(cfg, position["symbol"])["broker_symbol"]
    why = "Close %s: your %s of %s, %s%s." % (
        position["id"], side_word(position["side"]), position["symbol"], lots_text(position["lots"]),
        ", opened %s" % pkt_label(opened, date=True) if opened else "")
    return {"symbol": position["symbol"], "side": position["side"], "intent": "close",
            "lots": position["lots"], "type": "market", "entry": None, "stop_loss": None,
            "take_profit": None, "expiry": None, "style": position["style"], "broker_symbol": bsym,
            "position_id": position["id"], "rolls_applied": dict(position.get("rolls_applied") or {}),
            "plan": {"setup": None, "fx_day": today, "chart_entry": None, "chart_stop": None,
                     "risk_per_unit": None, "target_r": None, "flat_by": None,
                     "flat_by_utc": position.get("flat_by_utc"), "exit_by": position.get("exit_by"),
                     "nights_est": 0, "max_loss_usd": 0.0, "costs_usd": 0.0, "margin_usd": 0.0,
                     "usd_per_point_per_lot": None},
            "decided_by": "close", "why": why}


def write_close_orders(run, closes):
    """Replace orders.json with the close orders (4.6 step 3). Returns how many it replaced."""
    try:
        old = load_json(run.paths["orders"], None)
        before = len(old.get("orders") or []) if isinstance(old, dict) else 0
    except ValueError:
        before = 0
    save_json_atomic(run.paths["orders"], {"created_by": SKILL, "created_at": utc_iso(run.now),
                                           "orders": closes})
    return before


def replaced_words(before):
    """"(it replaced 1 earlier order)"."""
    if before == 0:
        return "(there was no earlier order)"
    return "(it replaced %d earlier %s)" % (before, "order" if before == 1 else "orders")


def cmd_close(run):
    a, out, cfg = run.args, run.out, run.cfg
    if (a.fill is not None or a.why) and not a.already:
        raise ValueError("--fill and --why go with --already.")
    if a.already and a.fill is None:
        raise ValueError("close --already needs --fill P: the price the trade closed at.")
    price_arg(a.fill, "--fill")
    run.rebase()
    pf = read_portfolio(run.paths, cfg)
    pos = find_position(pf, a.position)
    if pos is None:
        raise no_position(pf, a.position)
    if a.already:
        out.keys.update(line=None)
        return close_position(run, pos, None, None, already=True)
    order = close_order(pos, run.today, run.now, cfg)
    before = write_close_orders(run, [order])
    out.keys["orders"] = [order]
    out.say("Close order for %s written to %s %s." % (pos["id"], run.paths["orders"], replaced_words(before)))
    out.say("Next: python3 fx-risk/check.py, then ticket.py show and the user's yes.")
    return 0


def cmd_flatten(run):
    a, out, cfg = run.args, run.out, run.cfg
    run.rebase()
    pf = read_portfolio(run.paths, cfg)
    pos = [p for p in (pf or {}).get("positions") or [] if a.all or p.get("style") == "day"]
    out.keys["orders"] = []
    if not pos:
        out.say("You have no open positions." if a.all else "No open day trades. Nothing to close.")
        return 1
    closes = [close_order(p, run.today, run.now, cfg) for p in pos]
    before = write_close_orders(run, closes)
    out.keys["orders"] = closes
    out.say("Close orders for %s written to %s %s." % (", ".join(p["id"] for p in pos), run.paths["orders"],
                                                       replaced_words(before)))
    if any(p.get("style") == "day" for p in pos):
        flat = pkt_time_on_fx_day(cfg["day"]["flat_by"], run.today, cfg["day_end"])
        out.say("Be flat by %s." % both_times(flat))
    out.say("Next: python3 fx-risk/check.py, then ticket.py show and the user's yes.")
    return 0


def stop_loss_now(inst, pos, stop, nights, rates, cfg):
    """The max loss at a stop (4.7 step 4). A stop at or past the open price has no
    price loss: then only the commission, the fee and the swap cost."""
    o = open_price(pos)
    loss_side = _dec_clean(stop) < _dec_clean(o) if pos["side"] == "buy" else _dec_clean(stop) > _dec_clean(o)
    if loss_side:
        return max_loss_usd(inst, pos["side"], pos["lots"], o, stop, nights, rates, cfg)
    return costs_usd(inst, pos["side"], pos["lots"], nights, rates, cfg, o)


def cmd_stop(run):
    a, out, cfg = run.args, run.out, run.cfg
    to = price_arg(a.to, "--to")
    out.keys.update(record=None, line=None)
    run.rebase()
    pf = read_portfolio(run.paths, cfg)
    pos = find_position(pf, a.position)
    if pos is None:
        raise no_position(pf, a.position)
    inst = instrument(cfg, pos["symbol"])
    side, o, old = pos["side"], open_price(pos), pos.get("stop_loss")
    loss_side = _dec_clean(to) < _dec_clean(o) if side == "buy" else _dec_clean(to) > _dec_clean(o)
    new = round_away(to, o, inst) if loss_side else round_price(to, inst)
    if old is not None and _same_price(new, old, inst):
        out.say("Not moved: the stop is already at %s." % price_text(new, inst))
        return 1
    wider = old is not None and (new < old if side == "buy" else new > old)
    if wider and not a.wider:
        out.say("Not moved: %s is further from your profit side than %s. Use --wider if you mean it."
                % (price_text(new, inst), price_text(old, inst)))
        return 1
    y = yahoo_now(run.paths, inst, run.now, cfg, run.prices())
    if y is None:
        out.note("no Yahoo price, so the distance to the price was not checked.")
    else:
        problems = stops_problems(side, "market", o, new, pos.get("take_profit"), y["bid"], inst)
        if problems:
            for line in problems:
                out.say(line)
            out.say("Not moved.")
            return 1
    rates = run.rates.get()
    end = parse_utc(pos.get("exit_by")) or parse_utc(pos.get("flat_by_utc"))
    nights = int(pos.get("swap_nights") or 0) + (swap_nights(run.now, end, inst, cfg) if end else 0)
    before = pos.get("max_loss_usd")
    loss = stop_loss_now(inst, pos, new, nights, rates, cfg)
    pf = read_portfolio(run.paths, cfg)
    p = find_position(pf, a.position)
    p["stop_loss"] = new
    broker_new = None
    if p.get("broker_stop_loss") is not None and old is not None:
        x = _dec_clean(p["broker_stop_loss"]) + (_dec_clean(new) - _dec_clean(old))
        ref = p.get("broker_fill_price")
        if ref is not None and not _same_price(x, ref, inst):
            broker_new = round_away(float(x), ref, inst)
        else:
            broker_new = round_price(float(x), inst)
        p["broker_stop_loss"] = broker_new
    p["max_loss_usd"] = loss
    save_portfolio(run.paths, pf, run.now, cfg, run.rates, out)
    plain = "Stop of %s moved from %s to %s. Max loss at the stop now %s." % (
        pos["id"], price_text(old, inst) if old is not None else "none", price_text(new, inst), money(loss))
    rec = new_record("stop_moved", pos, run.now, run.today, cfg, price=new, broker_price=broker_new,
                     position_id=pos["id"], plain=plain)
    add_record(run.paths["broker_state"], rec)
    out.say("Stop of %s moved from %s to %s (Yahoo plan scale). Max loss at the stop now %s." % (
        pos["id"], price_text(old, inst) if old is not None else "none", price_text(new, inst), money(loss)))
    if wider and before is not None:
        out.note("this stop is further away. Your max loss at the stop grows from %s to %s."
                 % (money(before), money(loss)))
    mt4 = str(cfg["account"].get("platform", "mt5")).lower() == "mt4"
    if broker_new is not None:
        line = "In MetaTrader: %s > Trade > double click the %s row > Stop Loss %s > Modify." % (
            "Terminal" if mt4 else "Toolbox", inst["broker_symbol"], mt_price(broker_new, inst))
    else:
        dist = abs(_dec_clean(new) - _dec_clean(old)) if old is not None else Decimal(0)
        line = "Move your MetaTrader stop %s by %s too." % (
            "up" if old is None or new > old else "down", price_text(dist, inst))
    out.say(line)
    out.keys.update(record=rec, line=line)
    return 0


def cmd_log(run):
    a, out = run.args, run.out
    if a.days < 1 or a.days > 3650:
        raise ValueError("--days must be a whole number from 1 to 3650, not %s." % a.days)
    ledger = read_ledger(run.paths)
    cut = run.now - datetime.timedelta(days=a.days)
    recs = []
    for r in ledger["records"]:
        at = parse_utc(r.get("at"))
        if at is None or at > run.now:
            continue
        if (a.today and r.get("fx_day") == run.today) or (not a.today and at >= cut):
            recs.append((at, r))
    recs.sort(key=lambda x: x[0])
    out.keys["records"] = [r for _, r in recs]
    if not recs:
        out.say("No records today." if a.today else "No records in the last %d days." % a.days)
        return 0
    for at, r in recs:
        out.say("%s  %s  %s" % (pkt_label(at, date=True), r["status"], r.get("plain") or ""))
    return 0


# ---------------------------------------------------------------- the command line

def build_parser():
    p = argparse.ArgumentParser(prog=TOOL, description="MetaTrader order tickets. Paper trading by "
                                "default. It never places an order.")
    sub = p.add_subparsers(dest="cmd")
    s = add_common(sub.add_parser("show", help="show each order as a ticket"))
    s.add_argument("--index", type=int, default=None, help="show only order N (from 1)")
    s.add_argument("--broker-price", default=None, metavar="P", help="your broker's BID now")
    s = add_common(sub.add_parser("approve", help="record the user's reply to one order"))
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--reply", required=True, help="the user's reply, word for word")
    s.add_argument("--broker-price", default=None, metavar="P", help="your broker's BID now")
    s = add_common(sub.add_parser("decline", help="record a no"))
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--reply", default=None)
    s = add_common(sub.add_parser("result", help="record what happened to an approved order"))
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--status", required=True, choices=STATUSES)
    s.add_argument("--fill", default=None, metavar="P", help="the fill price")
    s.add_argument("--why", default=None, choices=WHYS, help="closes only: why the trade ended")
    s.add_argument("--note", default=None)
    s = add_common(sub.add_parser("close", help="build the close order of one position"))
    s.add_argument("--position", required=True, metavar="ID")
    s.add_argument("--already", action="store_true", help="MetaTrader already closed it")
    s.add_argument("--fill", default=None, metavar="P")
    s.add_argument("--why", default=None, choices=WHYS)
    s = add_common(sub.add_parser("flatten", help="close orders for every open day trade"))
    s.add_argument("--all", action="store_true", help="every open position, not only day trades")
    s = add_common(sub.add_parser("stop", help="move the stop of one position"))
    s.add_argument("--position", required=True, metavar="ID")
    s.add_argument("--to", required=True, metavar="PRICE")
    s.add_argument("--wider", action="store_true", help="allow a stop further from the profit side")
    s = add_common(sub.add_parser("log", help="the ledger in plain lines"))
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--today", action="store_true")
    return p


COMMANDS = {}


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        run = Run(args, args.cmd)
        code = COMMANDS[args.cmd](run)
    except ValueError as exc:
        return fail(exc, TOOL)
    return run.out.done(code)


COMMANDS.update(show=cmd_show, approve=cmd_approve, decline=cmd_decline,
                result=cmd_result, close=cmd_close, flatten=cmd_flatten,
                stop=cmd_stop, log=cmd_log)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
