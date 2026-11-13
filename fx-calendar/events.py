#!/usr/bin/env python3
"""fx-calendar: the economic calendar for currency pairs, metals and energy.

    python3 fx-calendar/events.py week  [--impact LIST] [--symbols A,B] [common flags]
    python3 fx-calendar/events.py next  [--impact LIST] [--symbols A,B] [--count N] [common flags]
    python3 fx-calendar/events.py quiet SYMBOL [--days N] [common flags]

week: this FX week's events by time, currency and impact, in Karachi time with New
York time in brackets, plus the market holidays and early closes of the week.
next: the next big events for the watchlist, the plan and the open trades, and which
of them fall inside each trade.
quiet: the no-new-trade windows around big events for one instrument (the same
windows fx-risk blocks), and whether now is inside one.

The calendar feed has no results. This tool never shows a price, never places an
order and never writes a file of the order path. It downloads the feed at most once
an hour, and never with --offline or --now. Standard library only, Python 3.8.
"""

import argparse
import copy
import datetime
import difflib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


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


# ---- end of the copies. Own code of fx-calendar below.


TOOL = "events.py"
WEEK_IMPACTS = ("High", "Medium")
NEXT_COUNT = 10
QUIET_DAYS = 7
LINE_MAX = 78          # words-mode lines wrap at about 78 characters (06 2.6)
IMPACT_ORDER = ("High", "Medium", "Low")
NO_RESULTS = "The calendar has no results, only the time, the forecast and the last value."
PAST_FRIDAY = ("The calendar only covers this week. Next week's news comes on Sunday. "
               "Big dates from the built-in tables are shown.")
OWN_NOTICE = (" Your broker's own notice wins: add it to \"holidays\" or \"early_closes\" "
              "in fx/config.json.")
BUILT_IN_ONLY = ("Only built-in dates are shown: central banks, EIA oil and gas, US jobs "
                 "and COT. Other news (like CPI) is missing.")
QUIET_RULE = ("Note: fx-risk blocks every new trade (day and swing, buy and sell) in these "
              "windows. Closing a trade is never blocked.")
QUIET_SCALE = ("Note: the window comes from \"news.quiet_before_min\" and "
               "\"news.quiet_after_min\" in fx/config.json. For scale: FTMO uses 2 minutes, "
               "HexFunded 5 minutes.")


def parse_symbols(text, cfg, tool):
    """--symbols text -> instrument keys: split on commas, strip, capitals, no repeats.
    An unknown key names the nearest known key (06 2.1)."""
    keys = list(cfg["instruments"])
    out = []
    for part in str(text or "").split(","):
        sym = part.strip().upper()
        if not sym:
            continue
        if sym not in cfg["instruments"]:
            near = difflib.get_close_matches(sym, keys, n=1, cutoff=0.6)
            msg = "%s is not in your instruments." % sym
            if near:
                msg += " Did you mean %s?" % near[0]
            raise ValueError(msg)
        if sym not in out:
            out.append(sym)
    if not out:
        raise ValueError("--symbols is empty.")
    return out


def parse_impacts(text):
    """--impact text -> a tuple of impacts in High, Medium, Low order; None when not
    given. "all" gives the three. Any case."""
    if text is None:
        return None
    names = dict((w.lower(), w) for w in IMPACT_ORDER)
    got = set()
    parts = [p.strip() for p in str(text).split(",")]
    for part in parts:
        low = part.lower()
        if low == "all":
            got.update(IMPACT_ORDER)
        elif low in names:
            got.add(names[low])
        else:
            raise ValueError('--impact wants High, Medium, Low or all (commas between), not "%s".'
                             % part)
    return tuple(w for w in IMPACT_ORDER if w in got)


def impacts_text(impacts, joiner="or"):
    """("High",) -> "High"; ("High", "Medium") -> "High or Medium"."""
    words = list(impacts)
    if len(words) <= 1:
        return "".join(words)
    return "%s %s %s" % (", ".join(words[:-1]), joiner, words[-1])


def week_span(week):
    """(start_utc, end_utc) of a calendar week: Sunday 00:00 to the next Sunday 00:00,
    New York time (the feed's week)."""
    d = datetime.date.fromisoformat(week)
    return (ny_to_utc(at_time(d, "00:00")),
            ny_to_utc(at_time(d + datetime.timedelta(days=7), "00:00")))


def past_friday(now):
    """True from Friday 17:00 New York to Sunday 00:00 New York (the feed then shows a
    week that has ended)."""
    n = to_ny(now)
    return n.weekday() == 5 or (n.weekday() == 4 and n.hour >= 17)


def day_month(d):
    """A date (or datetime) -> "Wed 11 Nov"."""
    return short_date(d)


def when_text(utc):
    """"Thu 12 Nov 10:00 PM PKT (12:00 PM New York)": Karachi first."""
    return pkt_label(utc, date=True) + " (" + ny_label(utc) + ")"


def wrap_line(text, indent="", width=LINE_MAX):
    """Break text at spaces into lines of at most width characters (when it can); the
    lines after the first start with indent."""
    lines = []
    while len(text) > width:
        cut = text.rfind(" ", 0, width + 1)
        if cut <= len(indent) or not text[:cut].strip():
            break
        lines.append(text[:cut].rstrip())
        text = indent + text[cut:].lstrip()
    lines.append(text)
    return lines


def touches(event, symbols):
    """True when the event touches one of symbols (None = any instrument)."""
    own = event.get("symbols") or []
    if symbols is None:
        return bool(own)    # (check) an event that touches no instrument is not shown
    return any(s in own for s in symbols)


def impact_word(event, symbols):
    """"High for oil" or "High for gas" when the EIA raise applies to the symbols shown
    (None = all); else the feed's impact."""
    kind = event.get("kind")
    if kind in ENERGY_KEYS and (symbols is None or any(s in ENERGY_KEYS[kind] for s in symbols)):
        return "High for oil" if kind == "eia_crude" else "High for gas"
    return event.get("impact") or ""


def in_week(event, impacts, symbols):
    """The week rule (4.2 step 3): a holiday row, an impact asked, or an EIA report
    when High is asked (it counts as High for oil and gas)."""
    if not touches(event, symbols):
        return False
    if event.get("impact") == "Holiday" or event.get("impact") in impacts:
        return True
    kind = event.get("kind")
    return (kind in ENERGY_KEYS and "High" in impacts
            and (symbols is None or any(s in ENERGY_KEYS[kind] for s in symbols)))


def is_past(event, now):
    """True when the event is over at now."""
    return (event.get("end") or event["at"]) < now


def event_name(event):
    """An event's short name in sentences: a feed row "CPI m/m (USD)", a built-in row
    its title ("Fed rate decision (FOMC)")."""
    if event.get("source") == "forexfactory":
        return event_label(event)
    return event.get("title") or ""


def forecast_part(event):
    """"forecast 0.3%, last 0.6%", "forecast 0.3%", "last 0.6%" or ""."""
    f, p = event.get("forecast"), event.get("previous")
    if f and p:
        return "forecast %s, last %s" % (f, p)
    if f:
        return "forecast %s" % f
    if p:
        return "last %s" % p
    return ""


def event_line(event, now, symbols, full=True, lead=""):
    """The event or holiday line (6.1), wrapped at 78 characters: a list of lines.
    full=False leaves out the forecast part (inside a trade block). lead goes first."""
    past = " (past)" if is_past(event, now) else ""
    if event.get("impact") == "Holiday":
        text = "%s%s  %s  Holiday: %s. Few traders.%s" % (
            lead, day_month(to_ny(event["at"]).date()), event.get("country") or "",
            event.get("title") or "", past)
        return wrap_line(text, "    ")
    head = "%s%s  %s  %s  %s" % (lead, when_text(event["at"]), event.get("country") or "",
                                 impact_word(event, symbols), event.get("title") or "")
    fc = forecast_part(event) if full else ""
    tail = ""
    if event.get("provisional"):
        tail += " (expected date)"
    if event.get("source") != "forexfactory":
        tail += " (built-in table)"
    tail += past
    whole = head + ("  " + fc if fc else "") + tail
    if len(whole) <= LINE_MAX:
        return [whole]
    if len(head) <= LINE_MAX:
        return [head] + wrap_line("    " + (fc + tail).strip(), "    ")
    return wrap_line(whole, "    ")


def event_json(event, now, symbols):
    """The 5.6 event shape for --json: times as UTC strings, plus past and impact_word."""
    out = dict(event)
    out["at"] = utc_iso(event["at"])
    out["end"] = utc_iso(event.get("end"))
    out["symbols"] = list(event.get("symbols") or [])
    out["past"] = is_past(event, now)
    out["impact_word"] = impact_word(event, symbols)
    return out


def pkt_clock(utc, other=None):
    """"5:45 PM" in Karachi time; with other, "Thu 12:00 AM" when the Karachi date
    differs from other's."""
    p = to_pkt(utc)
    if other is not None and to_pkt(other).date() != p.date():
        return "%s %s" % (DAY_NAMES[p.weekday()], clock_label(p))
    return clock_label(p)


def ny_clock(utc, other=None):
    """As pkt_clock, in New York time."""
    n = to_ny(utc)
    if other is not None and to_ny(other).date() != n.date():
        return "%s %s" % (DAY_NAMES[n.weekday()], clock_label(n))
    return clock_label(n)


def window_lines(start, end, event):
    """The 6.1 window line, wrapped, then the event under it."""
    head = "%s  No new trades from %s to %s PKT (%s to %s New York):" % (
        day_month(to_pkt(start)), pkt_clock(start), pkt_clock(end, start),
        ny_clock(start), ny_clock(end, start))
    return wrap_line(head) + ["    %s at %s PKT." % (event_name(event), pkt_clock(event["at"]))]


def ago_text(minutes):
    """age_text with "old" made "ago" and "live" made "just now": "14 min ago"."""
    text = age_text(minutes)
    if text == "live":
        return "just now"
    return text.replace(" old", " ago")


def status_line(info, cfg, offline, now):
    """The status line of the calendar (6.1 table, the first case that applies)."""
    if cfg.get("calendar_url", "") == "":
        return "Calendar feed is off (calendar_url is empty). Only built-in dates are shown."
    has = bool(info.get("events"))
    if offline:
        when = parse_utc(info.get("fetched_at") or info.get("tried_at"))
        if has and when is not None:
            return "Offline: the saved calendar from %s is used." % day_month(to_pkt(when))
        return ("Offline: there is no saved calendar for this week (fx/data/calendar/%s.json). %s"
                % (info["week"], BUILT_IN_ONLY))
    error = info.get("error")
    if error == "old week":
        return "Calendar not updated. The calendar site still shows last week."
    if error and has:
        return "Calendar not updated (the site said no). Using the copy from %s." % ago_text(info.get("age_min"))
    if error or not has or info.get("fetched_at") is None:
        return "Calendar site could not be read. " + BUILT_IN_ONLY
    return "Calendar: this week, updated %s." % ago_text(info.get("age_min"))


def table_day(iso):
    """True when the date is in the pack's own holiday or early-close tables."""
    return iso in HOLIDAYS or iso in EARLY_CLOSES


def market_day_notes(week, symbols, cfg):
    """[(date, note)] for Monday to Friday of the week: holiday_note for one instrument
    per hours template among the asked instruments; repeated texts once; a note from a
    table day ends with OWN_NOTICE."""
    keys = symbols if symbols is not None else list(cfg["instruments"])
    picked, seen_t = [], set()
    for key in cfg["instruments"]:
        if key in keys:
            inst = cfg["instruments"][key]
            if inst["hours"] not in seen_t:
                seen_t.add(inst["hours"])
                picked.append(inst)
    sunday = datetime.date.fromisoformat(week)
    out, seen = [], set()
    for i in range(1, 6):
        d = sunday + datetime.timedelta(days=i)
        for inst in picked:
            note = holiday_note(d, inst, cfg)
            if not note or note in seen:
                continue
            seen.add(note)
            mine = d.isoformat() in ((cfg.get("holidays") or {}))
            if table_day(d.isoformat()) and not mine and "(your config)" not in note:
                note += OWN_NOTICE
            out.append((d.isoformat(), note))
    return out


def this_week_note(to_utc, week_end):
    """The "Only this week ..." note when to_utc is after this week's end, else None."""
    if to_utc is None or to_utc <= week_end:
        return None
    saturday = to_ny(week_end).date() - datetime.timedelta(days=1)
    return ("Only this week (to %s) comes from the calendar site. After that only built-in "
            "dates are known: central banks, EIA oil and gas, US jobs and COT. Other news "
            "(like CPI) is missing." % day_month(saturday))


def check_times(doc, keys, path):
    """Each key of doc that is not null must be a UTC time (01 1.3)."""
    for key in keys:
        if doc.get(key) is not None:
            check_utc(doc, key, path)


def read_watchlist(path, cfg):
    """The watchlist's hit symbols, or None with no file (01 1.3 and 5.2 checks on the
    fields read: mode and hits[].symbol)."""
    doc = load_doc(path, "watchlist", "fx-scanner", required=False)
    if doc is None:
        return None
    if doc.get("mode") not in ("swing", "day"):
        raise ValueError('%s: "mode" must be "swing" or "day"' % path)
    return [check_symbol(hit.get("symbol"), cfg, path) for hit in check_objects(doc, "hits", path)]


def read_plan(path, cfg):
    """The plan, or None with no file (checks on symbol, side, style and the times)."""
    doc = load_doc(path, "plan", "fx-exit", required=False)
    if doc is None:
        return None
    check_symbol(doc.get("symbol"), cfg, path)
    check_side(doc.get("side"), path)
    style = check_style(doc.get("style"), path)
    check_times(doc, ("entry_at", "flat_by_utc", "exit_by"), path)
    need = "flat_by_utc" if style == "day" else "exit_by"
    if doc.get(need) is None:
        raise ValueError('%s: a %s plan needs "%s"' % (path, style, need))
    return doc


def read_portfolio(path, cfg):
    """The open positions (a list), or None with no file (checks on the fields read)."""
    doc = load_doc(path, "portfolio", "fx-broker", required=False)
    if doc is None:
        return None
    positions = check_objects(doc, "positions", path)
    ids = set()
    for pos in positions:
        check_symbol(pos.get("symbol"), cfg, path)
        check_side(pos.get("side"), path)
        check_style(pos.get("style"), path)
        check_times(pos, ("opened_at", "flat_by_utc", "exit_by"), path)
        if pos.get("id") in ids:
            raise ValueError("%s: two positions have the id %s. Fix it by hand or restore a backup."
                             % (path, pos.get("id")))
        ids.add(pos.get("id"))
    return positions


def pick_symbols(args, cfg, paths):
    """(keys, from): --symbols ("flag"); else the watchlist hits, the plan and the open
    positions, no repeats ("files"); else the universe ("universe")."""
    if args.symbols is not None:
        return parse_symbols(args.symbols, cfg, TOOL), "flag"
    out = []
    hits = read_watchlist(paths["watchlist"], cfg) or []
    plan = read_plan(paths["plan"], cfg)
    positions = read_portfolio(paths["portfolio"], cfg) or []
    for sym in hits + ([plan["symbol"]] if plan else []) + [p["symbol"] for p in positions]:
        if sym not in out:
            out.append(sym)
    if out:
        return out, "files"
    return list(cfg["universe"]), "universe"


def trade_end(doc):
    """The end of a trade or plan: flat_by_utc for a day trade, exit_by for a swing."""
    return parse_utc(doc.get("flat_by_utc") if doc.get("style") == "day" else doc.get("exit_by"))


def trade_blocks(plan, portfolio, events, now, impacts, cfg):
    """One block per open position, then one for the plan (skipped when a position has
    its symbol, side and entry): {what, id, symbol, side, style, from, to, events}.
    A trade whose end is past gets no block."""
    items = [("position", p, p.get("opened_at")) for p in (portfolio or [])]
    if plan and not any(p.get("symbol") == plan.get("symbol") and p.get("side") == plan.get("side")
                        and p.get("entry") == plan.get("entry") for p in (portfolio or [])):
        items.append(("plan", plan, plan.get("entry_at")))
    out = []
    for what, doc, start in items:
        to = trade_end(doc)
        if to is None or to <= now:
            continue
        frm = max(parse_utc(start) or now, now)
        sym = doc["symbol"]
        evs = [e for e in events if sym in (e.get("symbols") or [])
               and event_impact(e, sym) in impacts and frm <= e["at"] < to]
        out.append({"what": what, "id": doc.get("id") if what == "position" else None,
                    "symbol": sym, "side": doc["side"], "style": doc["style"],
                    "from": frm, "to": to, "events": evs})
    return out


def start(args):
    """The shared start of every subcommand (4.1): the clock, the paths, the config,
    the events and the status line, as a dict."""
    now = parse_now(args.now)
    offline = bool(args.offline or args.now is not None)
    work_dir = os.path.dirname(os.path.abspath(args.config)) if args.config else "fx"   # (check)
    paths = fx_paths(work_dir, args.data, args.config)
    cfg = load_config(paths["config"], required=args.config is not None)
    events = load_calendar(paths["data"], now, cfg, offline)
    info = calendar_info(paths["data"], now)
    return {"now": now, "offline": offline, "paths": paths, "cfg": cfg, "events": events,
            "info": info, "status": status_line(info, cfg, offline, now)}


def render(items, quiet):
    """The words-mode lines of a report. items: (kind, text) with kind "first", "note",
    "block" (text, wrapped), "event" (a list of lines) or "blank". --quiet keeps only
    the first line, the event, window and block lines."""
    lines = []
    for kind, text in items:
        if quiet and kind in ("note", "blank"):
            continue
        if kind == "event":
            lines.extend(text)
        elif kind == "blank":
            if lines and lines[-1] != "":
                lines.append("")
        else:
            lines.extend(wrap_line(text))
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def report(ctx, cmd, args, items, own):
    """Print the report (words or --json) and return 0."""
    if args.json:
        doc = {"v": JSON_VERSION, "tool": "fx-calendar", "cmd": cmd, "now": utc_iso(ctx["now"]),
               "ok": True, "doc": None, "calendar": ctx["info"], "offline": ctx["offline"],
               "feed_off": ctx["cfg"].get("calendar_url", "") == "",
               "notes": [text for kind, text in items if kind in ("first", "note")]}
        doc.update(own)
        emit_json(doc)
        return 0
    for line in render(items, args.quiet):
        print(line)
    return 0


def count_text(events, symbols):
    """"3 High and 2 Medium events" (EIA raises count as High), plus holidays."""
    counts = dict((w, 0) for w in IMPACT_ORDER)
    holidays = 0
    for e in events:
        if e.get("impact") == "Holiday":
            holidays += 1
            continue
        word = impact_word(e, symbols).split(" ")[0]
        if word in counts:
            counts[word] += 1
    parts = ["%d %s" % (counts[w], w) for w in IMPACT_ORDER if counts[w]]
    n = sum(counts.values())
    text = ("%s %s" % (impacts_text(parts, "and"), "event" if n == 1 else "events")) if parts else "no events"
    if holidays:
        text += ", and %d %s" % (holidays, "holiday" if holidays == 1 else "holidays")
    return text


def cmd_week(args):
    """events.py week: this FX week's events, then the market days (4.2)."""
    impacts = parse_impacts(args.impact) or WEEK_IMPACTS
    ctx = start(args)
    cfg = ctx["cfg"]
    symbols = parse_symbols(args.symbols, cfg, TOOL) if args.symbols is not None else None
    now, week = ctx["now"], calendar_week(ctx["now"])
    begin, end = week_span(week)
    shown = [e for e in ctx["events"] if begin <= e["at"] < end and in_week(e, impacts, symbols)]
    days = market_day_notes(week, symbols, cfg)
    later = []
    if past_friday(now):
        nb, ne = week_span(calendar_week(end))
        later = [e for e in ctx["events"] if nb <= e["at"] < ne and e.get("source") != "forexfactory"
                 and in_week(e, impacts, symbols)]
    sunday = datetime.date.fromisoformat(week)
    items = [("first", "Week of %s (New York): %s." % (day_month(sunday), count_text(shown, symbols))),
             ("note", ctx["status"]),
             ("note", "Shown: %s, and holidays. Times: Karachi, then New York."
              % impacts_text(impacts, "and"))]
    if symbols is not None:
        items.append(("note", "Only events that touch %s." % ", ".join(symbols)))
    items.append(("blank", ""))
    for e in shown:
        items.append(("event", event_line(e, now, symbols)))
    if not shown:
        items.append(("note", "No events of these kinds this week."))
    items.append(("blank", ""))
    if days:
        items.append(("note", "Market days:"))
        items += [("note", note) for _, note in days]
    else:
        items.append(("note", "Market days: no holiday or early close this week."))
    if past_friday(now):
        items += [("blank", ""), ("note", PAST_FRIDAY)]
        for e in later:
            items.append(("event", event_line(e, now, symbols)))
        if not later:
            items.append(("note", "No big built-in dates next week."))
        items.append(("blank", ""))
    items += [("note", NO_RESULTS), ("note", "Next: python3 fx-calendar/events.py next")]
    own = {"week": week, "span": [utc_iso(begin), utc_iso(end)], "impacts": list(impacts),
           "symbols": symbols, "events": [event_json(e, now, symbols) for e in shown],
           "market_days": [{"date": d, "note": n} for d, n in days],
           "next_week": [event_json(e, now, symbols) for e in later]}
    return report(ctx, "week", args, items, own)


def count_words(n, impacts):
    """"1 High event.", "2 High events.", "no High events."."""
    word = impacts_text(impacts)
    if n == 0:
        return "no %s events." % word
    return "%d %s %s." % (n, word, "event" if n == 1 else "events")


def block_head(block, now, cfg):
    """"Inside your plan (SELL EURUSD, day, to 11:30 PM PKT (2:30 PM New York) today)"."""
    to = block["to"]
    if block["style"] == "day" and fx_day(to, cfg["day_end"]) == fx_day(now, cfg["day_end"]):
        end = "%s PKT (%s) today" % (clock_label(to_pkt(to)), ny_label(to))
    else:
        end = when_text(to)
    who = "your plan" if block["what"] == "plan" else "your open trade %s" % block["id"]
    return "Inside %s (%s %s, %s, to %s)" % (who, side_word(block["side"]), block["symbol"],
                                            block["style"], end)


def cmd_next(args):
    """events.py next: the next big events for your instruments and trades (4.3)."""
    impacts = parse_impacts(args.impact)
    if not 1 <= args.count <= 50:
        raise ValueError("--count wants a whole number from 1 to 50, not %s." % args.count)
    ctx = start(args)
    cfg, now, paths = ctx["cfg"], ctx["now"], ctx["paths"]
    impacts = impacts or tuple(cfg["news"]["impacts"])
    symbols, src = pick_symbols(args, cfg, paths)
    plan = read_plan(paths["plan"], cfg)
    positions = read_portfolio(paths["portfolio"], cfg)
    rows = next_events(ctx["events"], symbols, now, args.count, impacts)
    blocks = trade_blocks(plan, positions, ctx["events"], now, impacts, cfg)
    _, week_end = week_span(calendar_week(now))
    word = impacts_text(impacts)
    found = ("%d found." % len(rows)) if rows else "none found."
    if src == "universe":
        first = "Next %s events for your universe (%d instruments): %s" % (word, len(symbols), found)
    else:
        where = " (from your watchlist, plan and open trades)" if src == "files" else ""
        first = "Next %s events for %s%s: %s" % (word, ", ".join(symbols), where, found)
    items = [("first", first), ("note", ctx["status"])]
    if src == "universe":
        items.append(("note", "No watchlist, plan or open trades. Showing events for your whole "
                              "universe (%d instruments)." % len(symbols)))
    items.append(("blank", ""))
    jrows = []
    for e in rows:
        for_ = [s for s in symbols if s in (e.get("symbols") or []) and event_impact(e, s) in impacts]
        items.append(("event", event_line(e, now, symbols) + ["    for " + ", ".join(for_)]))
        jrows.append(dict(event_json(e, now, symbols), **{"for": for_}))
    jblocks = []
    for b in blocks:
        items += [("blank", ""), ("block", "%s: %s" % (block_head(b, now, cfg), count_words(len(b["events"]), impacts)))]
        for e in b["events"]:
            items.append(("event", event_line(e, now, [b["symbol"]], full=False, lead="  ")))
        note = this_week_note(b["to"], week_end)
        if note:
            items.append(("note", note))
        jblocks.append({"what": b["what"], "id": b["id"], "symbol": b["symbol"], "side": b["side"],
                        "style": b["style"], "from": utc_iso(b["from"]), "to": utc_iso(b["to"]),
                        "events": [event_json(e, now, [b["symbol"]]) for e in b["events"]]})
    items += [("blank", ""), ("note", NO_RESULTS),
              ("note", "Next: python3 fx-calendar/events.py quiet %s" % symbols[0])]
    own = {"symbols": symbols, "symbols_from": src, "impacts": list(impacts), "events": jrows,
           "trades": jblocks}
    return report(ctx, "next", args, items, own)


def window_json(w):
    """A window for --json: {start, end, event} with UTC strings."""
    return {"start": utc_iso(w[0]), "end": utc_iso(w[1]), "event": w[2]}


def cmd_quiet(args):
    """events.py quiet SYMBOL: the no-new-trade windows for one instrument (4.4)."""
    if "," in args.symbol:
        raise ValueError("quiet takes one symbol, like: quiet EURUSD")
    if not 1 <= args.days <= 35:
        raise ValueError("--days wants a whole number from 1 to 35, not %s." % args.days)
    ctx = start(args)
    cfg, now = ctx["cfg"], ctx["now"]
    sym = parse_symbols(args.symbol, cfg, TOOL)[0]
    until = now + datetime.timedelta(days=args.days)
    wins = [w for w in quiet_windows(ctx["events"], sym, cfg) if w[1] > now and w[0] < until]
    cur = in_quiet_window(ctx["events"], sym, now, cfg)
    cur_win = next((w for w in wins if w[2] is cur), None) if cur is not None else None
    later = [w for w in wins if w[0] > now]
    span = "the next %d %s" % (args.days, "day" if args.days == 1 else "days")
    if cur_win is not None:
        first = "QUIET: %s is in a no-new-trade window now, to %s PKT (%s New York): %s at %s PKT." % (
            sym, pkt_clock(cur_win[1], now), ny_clock(cur_win[1], now), event_name(cur),
            pkt_clock(cur["at"], now))
    elif later:
        first = "Not quiet: %s has no news window now. The next one starts %s." % (sym, when_text(later[0][0]))
    else:
        first = "Not quiet: %s has no news window now, and none in %s." % (sym, span)
    news = cfg["news"]
    hols = [e for e in ctx["events"] if e.get("impact") == "Holiday" and sym in (e.get("symbols") or [])
            and is_past(e, now) is False and e["at"] < until]
    items = [("first", first), ("note", ctx["status"])]
    if wins:
        items.append(("note", "Windows for %s in %s (%s events, %d min before to %d min after):" % (
            sym, span, impacts_text(news["impacts"]), news["quiet_before_min"], news["quiet_after_min"])))
    for w in wins:
        items.append(("event", window_lines(w[0], w[1], w[2])))
    for e in hols:
        items.append(("note", "Note: %s: holiday: %s. Few traders." % (day_month(to_ny(e["at"]).date()), event_label(e))))
    if wins:
        items += [("note", QUIET_RULE), ("note", QUIET_SCALE)]
    note = this_week_note(until, week_span(calendar_week(now))[1])
    if note:
        items.append(("note", "Note: " + note[0].lower() + note[1:]))
    items.append(("note", NO_RESULTS))
    ev = lambda e: event_json(e, now, [sym])
    own = {"symbol": sym, "days": args.days,
           "quiet_now": dict(window_json(cur_win), event=ev(cur)) if cur_win else None,
           "windows": [dict(window_json(w), event=ev(w[2])) for w in wins],
           "holidays": [ev(e) for e in hols]}
    return report(ctx, "quiet", args, items, own)


def build_parser():
    """argparse with the three subcommands and the common flags on each."""
    ap = argparse.ArgumentParser(prog="events.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    p = add_common(sub.add_parser("week", help="this week's economic calendar"))
    p.add_argument("--impact", metavar="LIST", default=None, help="High, Medium, Low or all (default High,Medium)")
    p.add_argument("--symbols", metavar="A,B", default=None, help="only events that touch these instruments")
    p = add_common(sub.add_parser("next", help="the next big events for your instruments and trades"))
    p.add_argument("--impact", metavar="LIST", default=None, help="High, Medium, Low or all (default: news.impacts)")
    p.add_argument("--symbols", metavar="A,B", default=None, help="these instruments (default: watchlist, plan, open trades)")
    p.add_argument("--count", type=int, default=NEXT_COUNT, help="how many next events (1 to 50, default 10)")
    p = add_common(sub.add_parser("quiet", help="the no-new-trade windows for one instrument"))
    p.add_argument("symbol", metavar="SYMBOL", help="one instrument key, like EURUSD")
    p.add_argument("--days", type=int, default=QUIET_DAYS, help="how many days ahead (1 to 35, default 7)")
    return ap


def main(argv=None):
    """Run events.py; returns the exit code (0 a report, 2 an error)."""
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return {"week": cmd_week, "next": cmd_next, "quiet": cmd_quiet}[args.cmd](args)
    except ValueError as e:
        return fail(e, TOOL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
