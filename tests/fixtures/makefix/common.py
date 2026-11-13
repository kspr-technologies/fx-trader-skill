"""Shared helpers of the sample-market generator. Standard library only, Python 3.8.

The part under "copied" is a verbatim copy of canonical helpers from fx_core.py
(same name, same body, same docstring), so the generator runs inside the pack on
its own. Never edit a copied helper. tests/test_core_fixtures.py and test_repo
check that each copy is the same text as in fx_core.py.
"""

import datetime
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------- copied (verbatim from fx_core.py)

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


KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving


DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def to_pkt(utc):
    """UTC -> naive Karachi time (UTC+5). An aware datetime is turned to UTC first."""
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc + datetime.timedelta(hours=KARACHI_HOURS)


def utc_iso(utc):
    """naive UTC -> "2026-11-11T06:30:00Z" (how files store times). None stays None."""
    if utc is None:
        return None
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


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


DAY_ENDS = ("17:00 New York", "00:00 UTC")


FULL_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


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


def is_open(inst, utc, cfg, which="hours"):
    """True when market_state says "open"."""
    return market_state(inst, utc, cfg, which)["state"] == "open"


LOOKBACK = {"5m": 60, "1h": 730, "1d": 3650}           # days Yahoo keeps (1d: we cap at 10 years)


STEP_S = {"5m": 300, "1h": 3600, "1d": 86400}          # bar length in seconds


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


def cache_file(data_dir, symbol, interval):
    """The cache path data_dir/yahoo/<file>/<interval>.json; <file> is the symbol with
    "=" and "." turned into "_" (GC=F -> GC_F). A strange symbol is refused."""
    if not re.match(r"^[A-Za-z0-9=.^-]+$", str(symbol)):
        raise ValueError("Bad Yahoo symbol: %s" % symbol)
    name = str(symbol).replace("=", "_").replace(".", "_")
    return os.path.join(data_dir, "yahoo", name, "%s.json" % interval)


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

