"""Tests for the time group of fx_core: clock, dst, fx day, sessions, hours, tables, calendar.
Offline only. DST rules are checked against fixed known dates (no time zone files)."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datetime
import json
import shutil
import tempfile
import unittest

import fx_core as fx

D = datetime.datetime
H = datetime.timedelta(hours=1)
M = datetime.timedelta(minutes=1)


def utc(text):
    """ "2026-11-11 06:30" -> naive datetime (test shorthand)."""
    return D.strptime(text, "%Y-%m-%d %H:%M")


class Clock(unittest.TestCase):
    def test_pkt(self):
        self.assertEqual(fx.to_pkt(utc("2026-11-11 06:30")), utc("2026-11-11 11:30"))
        self.assertEqual(fx.pkt_to_utc(utc("2026-11-11 11:30")), utc("2026-11-11 06:30"))
        aware = D(2026, 11, 11, 8, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(fx.to_pkt(aware), utc("2026-11-11 11:00"))
        self.assertEqual(fx.pkt_to_utc(aware), utc("2026-11-11 06:00"))

    def test_parse_now(self):
        self.assertEqual(fx.parse_now("2026-11-11 14:30"), utc("2026-11-11 09:30"))
        self.assertEqual(fx.parse_now("2026-11-11T14:30"), utc("2026-11-11 09:30"))
        self.assertEqual(fx.parse_now("2026-11-11 14:30:00"), utc("2026-11-11 09:30"))
        self.assertEqual(fx.parse_now(" 2026-11-11 14:30 PKT "), utc("2026-11-11 09:30"))
        self.assertEqual(fx.parse_now("2026-11-11T14:30Z"), utc("2026-11-11 14:30"))
        self.assertEqual(fx.parse_now("2026-11-11T14:30+02:00"), utc("2026-11-11 12:30"))
        self.assertIsInstance(fx.parse_now(None), D)
        self.assertIsInstance(fx.parse_now(""), D)
        for bad in ("tomorrow", "2026-11-11", "2026-13-11 14:30", "14:30"):
            with self.assertRaises(ValueError) as cm:
                fx.parse_now(bad)
            self.assertEqual(str(cm.exception),
                             '--now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)')

    def test_iso(self):
        self.assertEqual(fx.utc_iso(utc("2026-11-11 06:30")), "2026-11-11T06:30:00Z")
        self.assertIsNone(fx.utc_iso(None))
        self.assertEqual(fx.parse_utc("2026-11-11T06:30:00Z"), utc("2026-11-11 06:30"))
        self.assertEqual(fx.parse_utc("2026-11-11T06:30:00"), utc("2026-11-11 06:30"))
        self.assertEqual(fx.parse_utc("2026-11-10T08:30:00-05:00"), utc("2026-11-10 13:30"))
        for bad in (None, "", "soon", 5):
            self.assertIsNone(fx.parse_utc(bad))

    def test_ts(self):
        t = utc("2026-11-11 06:30")
        self.assertEqual(fx.ts_to_utc(fx.utc_to_ts(t)), t)
        self.assertEqual(fx.utc_to_ts(D(1970, 1, 2)), 86400)
        self.assertEqual(fx.ts_to_utc(86400.7), D(1970, 1, 2))

    def test_labels(self):
        self.assertEqual(fx.clock_label(datetime.time(15, 5)), "3:05 PM")
        self.assertEqual(fx.clock_label(datetime.time(0, 0)), "12:00 AM")
        self.assertEqual(fx.clock_label(datetime.time(12, 30)), "12:30 PM")
        self.assertEqual(fx.day_label(datetime.date(2026, 11, 11)), "Wed 2026-11-11")
        self.assertEqual(fx.day_label(utc("2026-11-11 23:00")), "Wed 2026-11-11")
        self.assertEqual(fx.short_date(datetime.date(2026, 11, 7)), "Sat 7 Nov")
        t = D(2026, 11, 11, 13, 30)
        self.assertEqual(fx.pkt_label(t), "Wed 6:30 PM PKT")
        self.assertEqual(fx.ny_label(t), "8:30 AM New York")
        self.assertEqual(fx.pkt_label(D(2026, 11, 11, 19, 30), date=True), "Thu 12 Nov 12:30 AM PKT")
        self.assertEqual(fx.ny_label(t, date=True), "Wed 11 Nov 8:30 AM New York")

    def test_parse_hhmm(self):
        self.assertEqual(fx.parse_hhmm("23:30", "--at"), datetime.time(23, 30))
        self.assertEqual(fx.parse_hhmm("9:05", "--at"), datetime.time(9, 5))
        self.assertEqual(fx.parse_hhmm(datetime.time(1, 2), "--at"), datetime.time(1, 2))
        for bad in ("24:00", "9:5", "nine", None, "12:60"):
            with self.assertRaises(ValueError) as cm:
                fx.parse_hhmm(bad, '"day.flat_by"')
            self.assertEqual(str(cm.exception), '"day.flat_by" wants a time like 23:30')


class Dst(unittest.TestCase):
    def test_weekday_helpers(self):
        self.assertEqual(fx.nth_weekday(2026, 3, 6, 2), datetime.date(2026, 3, 8))
        self.assertEqual(fx.nth_weekday(2026, 11, 6, 1), datetime.date(2026, 11, 1))
        self.assertEqual(fx.last_weekday(2026, 10, 6), datetime.date(2026, 10, 25))
        self.assertEqual(fx.last_weekday(2027, 12, 4), datetime.date(2027, 12, 31))
        with self.assertRaises(ValueError) as cm:
            fx.nth_weekday(2026, 2, 0, 5)
        self.assertEqual(str(cm.exception), "there is no weekday 0 number 5 in 2026-02")

    def edge(self, fn, moment, after):
        """fn is `after` from the moment on, and not one minute before."""
        self.assertEqual(fn(moment), after, moment)
        self.assertEqual(fn(moment - M), not after, moment)

    def test_us(self):
        self.edge(fx.us_dst, utc("2026-03-08 07:00"), True)
        self.edge(fx.us_dst, utc("2026-11-01 06:00"), False)
        self.edge(fx.us_dst, utc("2027-03-14 07:00"), True)
        self.edge(fx.us_dst, utc("2027-11-07 06:00"), False)

    def test_uk(self):
        self.edge(fx.uk_dst, utc("2026-03-29 01:00"), True)
        self.edge(fx.uk_dst, utc("2026-10-25 01:00"), False)
        self.edge(fx.uk_dst, utc("2027-03-28 01:00"), True)
        self.edge(fx.uk_dst, utc("2027-10-31 01:00"), False)

    def test_sydney(self):
        # first Sundays of April and October, at Saturday 16:00 UTC
        self.edge(fx.sydney_dst, utc("2026-04-04 16:00"), False)
        self.edge(fx.sydney_dst, utc("2026-10-03 16:00"), True)
        self.edge(fx.sydney_dst, utc("2027-04-03 16:00"), False)
        self.edge(fx.sydney_dst, utc("2027-10-02 16:00"), True)
        self.assertTrue(fx.sydney_dst(utc("2026-01-15 00:00")))
        self.assertFalse(fx.sydney_dst(utc("2026-07-15 00:00")))

    def test_conversions(self):
        self.assertEqual(fx.to_ny(utc("2026-11-11 13:30")), utc("2026-11-11 08:30"))
        self.assertEqual(fx.to_ny(utc("2026-07-15 13:30")), utc("2026-07-15 09:30"))
        self.assertEqual(fx.ny_to_utc(utc("2026-11-01 01:30")), utc("2026-11-01 05:30"))
        self.assertEqual(fx.ny_to_utc(utc("2026-03-08 02:30")), utc("2026-03-08 07:30"))
        self.assertEqual(fx.ny_to_utc(utc("2026-11-11 17:00")), utc("2026-11-11 22:00"))
        self.assertEqual(fx.ny_to_utc(utc("2026-07-29 17:00")), utc("2026-07-29 21:00"))
        self.assertEqual(fx.to_london(utc("2026-07-15 07:00")), utc("2026-07-15 08:00"))
        self.assertEqual(fx.to_london(utc("2026-11-11 08:00")), utc("2026-11-11 08:00"))
        self.assertEqual(fx.london_to_utc(utc("2026-07-15 08:00")), utc("2026-07-15 07:00"))
        self.assertEqual(fx.london_to_utc(utc("2026-10-25 01:30")), utc("2026-10-25 00:30"))
        self.assertEqual(fx.to_sydney(utc("2026-11-11 00:00")), utc("2026-11-11 11:00"))
        self.assertEqual(fx.to_sydney(utc("2026-07-15 00:00")), utc("2026-07-15 10:00"))
        self.assertEqual(fx.sydney_to_utc(utc("2026-11-11 07:00")), utc("2026-11-10 20:00"))
        self.assertEqual(fx.sydney_to_utc(utc("2026-07-15 07:00")), utc("2026-07-14 21:00"))
        self.assertEqual(fx.to_tokyo(utc("2026-11-11 00:00")), utc("2026-11-11 09:00"))
        self.assertEqual(fx.tokyo_to_utc(utc("2026-11-11 09:00")), utc("2026-11-11 00:00"))
        self.assertEqual(fx.city_to_utc("london", utc("2026-11-11 08:00")), utc("2026-11-11 08:00"))
        with self.assertRaises(ValueError):
            fx.city_to_utc("paris", utc("2026-11-11 08:00"))

    def test_round_trip_every_hour(self):
        t = utc("2026-01-01 00:00")
        while t < utc("2028-01-01 00:00"):
            for to, back in ((fx.to_ny, fx.ny_to_utc), (fx.to_london, fx.london_to_utc),
                             (fx.to_sydney, fx.sydney_to_utc), (fx.to_tokyo, fx.tokyo_to_utc)):
                local = to(t)
                if to(back(local)) == local and back(local) != t:
                    continue    # the doubled autumn hour: the first one wins
                self.assertEqual(back(local), t, (to.__name__, t))
            t += H

    def test_server(self):
        w, s = utc("2026-11-11 22:00"), utc("2026-07-15 21:00")
        self.assertEqual(fx.server_time(w, "ny+7"), utc("2026-11-12 00:00"))
        self.assertEqual(fx.server_time(s, " NY+7 "), utc("2026-07-16 00:00"))
        self.assertEqual(fx.server_time(w, "utc+0"), w)
        self.assertEqual(fx.server_time(w, "utc-3"), w - 3 * H)
        self.assertEqual(fx.server_to_utc(utc("2026-11-12 00:00"), "ny+7"), w)
        self.assertEqual(fx.server_to_utc(utc("2026-07-16 00:00"), "ny+7"), s)
        self.assertEqual(fx.server_to_utc(w + 2 * H, "utc+2"), w)
        msg = '"account.server_time" must be "ny+7" (GMT+2 in winter, GMT+3 in summer) or like "utc+0"'
        for bad in ("ny+6", "utc+15", "gmt+2", "", None):
            with self.assertRaises(ValueError) as cm:
                fx.server_time(w, bad)
            self.assertEqual(str(cm.exception), msg)

    def test_odd_week(self):
        self.assertTrue(fx.odd_week(utc("2026-03-10 12:00")))
        self.assertTrue(fx.odd_week(utc("2026-10-27 12:00")))
        self.assertTrue(fx.odd_week(utc("2027-03-16 12:00")))
        self.assertTrue(fx.odd_week(utc("2027-11-02 12:00")))
        self.assertFalse(fx.odd_week(utc("2026-11-11 12:00")))
        self.assertFalse(fx.odd_week(utc("2026-07-15 12:00")))
        self.assertFalse(fx.odd_week(utc("2026-03-30 12:00")))



NY, UTC0 = "17:00 New York", "00:00 UTC"


def inst(hours, data_hours, base="EUR", quote="USD"):
    return {"hours": hours, "data_hours": data_hours, "base": base, "quote": quote}


INST = {
    "EURUSD": inst("fx", "yahoo_fx", "EUR", "USD"),
    "EURGBP": inst("fx", "yahoo_fx", "EUR", "GBP"),
    "USDJPY": inst("fx", "yahoo_fx", "USD", "JPY"),
    "XAUUSD": inst("comex", "cme", "XAU", "USD"),
    "USOIL": inst("nymex", "cme", "WTI", "USD"),
    "UKOIL": inst("ice_brent", "cme", "BRENT", "USD"),
    "NATGAS": inst("nymex", "cme", "NG", "USD"),
}


def make_cfg(**kw):
    cfg = {"day_end": NY, "holidays": {}, "early_closes": {},
           "day": {"no_entries_after": "21:00", "flat_by": "23:30"},
           "news": {"quiet_before_min": 30, "quiet_after_min": 30, "impacts": ["High"]},
           "calendar_url": "https://example.invalid/cal.json", "instruments": dict(INST)}
    cfg.update(kw)
    return cfg


class FxDay(unittest.TestCase):
    def test_fx_day_table(self):
        rows = [("2026-11-10 21:59", "2026-11-10", "2026-11-10"),
                ("2026-11-10 22:00", "2026-11-11", "2026-11-10"),
                ("2026-11-13 21:59", "2026-11-13", "2026-11-13"),
                ("2026-11-13 22:00", None, None),
                ("2026-11-15 21:59", None, None),
                ("2026-11-15 22:00", "2026-11-16", "2026-11-16"),
                ("2026-07-28 21:00", "2026-07-29", "2026-07-28"),
                ("2026-11-14 10:00", None, None)]
        for t, a, b in rows:
            self.assertEqual(fx.fx_day(utc(t), NY), a, t)
            self.assertEqual(fx.fx_day(utc(t), UTC0), b, t)
        self.assertIsNone(fx.fx_day(utc("2026-07-24 21:30"), NY))   # Fri 21:30 UTC in July
        self.assertEqual(fx.fx_day(utc("2026-12-25 10:00"), NY), "2026-12-25")

    def test_bad_inputs(self):
        with self.assertRaises(ValueError) as cm:
            fx.fx_day(utc("2026-11-11 10:00"), "17:00 London")
        self.assertEqual(str(cm.exception), '"day_end" must be "17:00 New York" or "00:00 UTC"')
        with self.assertRaises(ValueError) as cm:
            fx.fx_day_bounds("11/11/2026", NY)
        self.assertEqual(str(cm.exception), 'An FX day wants a date like "2026-11-11", not "11/11/2026"')
        with self.assertRaises(ValueError) as cm:
            fx.fx_day_bounds("2026-02-30", NY)
        self.assertEqual(str(cm.exception), 'An FX day wants a date like "2026-11-11", not "2026-02-30"')
        with self.assertRaises(ValueError) as cm:
            fx.fx_day_bounds("2026-11-14", NY)
        self.assertEqual(str(cm.exception), "2026-11-14 is a Saturday. FX days are Monday to Friday.")

    def test_bounds_table(self):
        rows = [("2026-11-11", NY, "2026-11-10 22:00", "2026-11-11 22:00", 24),
                ("2026-07-29", NY, "2026-07-28 21:00", "2026-07-29 21:00", 24),
                ("2026-11-11", UTC0, "2026-11-11 00:00", "2026-11-12 00:00", 24),
                ("2026-11-16", UTC0, "2026-11-15 22:00", "2026-11-17 00:00", 26),
                ("2026-11-13", UTC0, "2026-11-13 00:00", "2026-11-13 22:00", 22),
                ("2026-03-09", UTC0, "2026-03-08 21:00", "2026-03-10 00:00", 27)]
        for d, end, a, b, hours in rows:
            s, e = fx.fx_day_bounds(d, end)
            self.assertEqual((s, e), (utc(a), utc(b)), d)
            self.assertEqual(e - s, hours * H)
        self.assertEqual(fx.to_pkt(fx.fx_day_bounds("2026-11-11", NY)[0]), utc("2026-11-11 03:00"))
        s, e = fx.fx_day_bounds(datetime.date(2026, 7, 23), NY)
        self.assertEqual((s, e), (utc("2026-07-22 21:00"), utc("2026-07-23 21:00")))

    def test_counting(self):
        cfg = make_cfg()
        self.assertEqual(fx.fx_days_between("2026-12-24", "2026-12-28", cfg), 1)
        self.assertEqual(fx.fx_days_between("2026-11-13", "2026-11-16", cfg), 1)
        self.assertEqual(fx.fx_days_between("2026-11-16", "2026-11-13", cfg), -1)
        self.assertEqual(fx.fx_days_between("2026-11-11", "2026-11-11", cfg), 0)
        self.assertEqual(fx.next_fx_day("2026-11-13", cfg), "2026-11-16")
        self.assertEqual(fx.next_fx_day("2026-11-14", cfg), "2026-11-16")
        self.assertEqual(fx.next_fx_day("2026-12-24", cfg), "2026-12-28")
        self.assertEqual(fx.next_fx_day("2026-12-31", cfg), "2027-01-04")
        self.assertEqual(fx.next_fx_day("2026-11-12", make_cfg(holidays={"2026-11-13": "broker shut"})), "2026-11-16")
        self.assertEqual(fx.add_fx_days("2026-11-11", 0, cfg), "2026-11-11")
        with self.assertRaises(ValueError):
            fx.add_fx_days("2026-11-11", -1, cfg)

    def test_swing_hold_examples(self):
        cfg = make_cfg()
        for entry, last, exit_by, pkt in (("2026-11-11", "2026-12-08", "2026-12-08T22:00:00Z", "Wed 9 Dec 3:00 AM PKT"),
                                          ("2026-12-10", "2027-01-08", "2027-01-08T22:00:00Z", "Sat 9 Jan 3:00 AM PKT")):
            day = fx.add_fx_days(entry, 19, cfg)
            self.assertEqual(day, last)
            end = fx.fx_day_bounds(day, NY)[1]
            self.assertEqual(fx.utc_iso(end), exit_by)
            self.assertEqual(fx.pkt_label(end, date=True), pkt)

    def test_last_fx_day(self):
        cfg = make_cfg()
        self.assertEqual(fx.last_fx_day(utc("2026-11-11 06:30"), cfg), "2026-11-11")
        self.assertEqual(fx.last_fx_day(utc("2026-11-13 22:30"), cfg), "2026-11-13")
        self.assertEqual(fx.last_fx_day(utc("2026-11-14 10:00"), cfg), "2026-11-13")
        self.assertEqual(fx.last_fx_day(utc("2026-11-15 21:00"), cfg), "2026-11-13")
        self.assertEqual(fx.last_fx_day(utc("2026-11-15 21:00"), make_cfg(day_end=UTC0)), "2026-11-13")

    def test_week(self):
        week = (utc("2026-11-08 22:00"), utc("2026-11-13 22:00"))
        self.assertEqual(fx.fx_week_bounds(utc("2026-11-11 06:30")), week)
        self.assertEqual(fx.fx_week_bounds(utc("2026-11-08 22:00")), week)
        nxt = (utc("2026-11-15 22:00"), utc("2026-11-20 22:00"))
        self.assertEqual(fx.fx_week_bounds(utc("2026-11-13 22:00")), nxt)
        self.assertEqual(fx.fx_week_bounds(utc("2026-11-14 10:00")), nxt)
        self.assertEqual(fx.fx_week_bounds(utc("2026-07-15 10:00")), (utc("2026-07-12 21:00"), utc("2026-07-17 21:00")))
        # the US clocks change inside the weekend: open in winter time, close in summer time
        self.assertEqual(fx.fx_week_bounds(utc("2026-03-04 10:00")), (utc("2026-03-01 22:00"), utc("2026-03-06 22:00")))
        self.assertEqual(fx.fx_week_bounds(utc("2026-03-10 10:00")), (utc("2026-03-08 21:00"), utc("2026-03-13 21:00")))


class Sessions(unittest.TestCase):
    def test_pkt_time_on_fx_day_table(self):
        rows = [("2026-11-11", NY, "21:00", "2026-11-11 16:00", "2026-11-11 21:00"),
                ("2026-11-11", NY, "23:30", "2026-11-11 18:30", "2026-11-11 23:30"),
                ("2026-11-11", NY, "01:30", "2026-11-11 20:30", "2026-11-12 01:30"),
                ("2026-11-11", NY, "03:30", "2026-11-10 22:30", "2026-11-11 03:30"),
                ("2026-07-29", NY, "23:30", "2026-07-29 18:30", "2026-07-29 23:30"),
                ("2026-07-29", NY, "01:30", "2026-07-29 20:30", "2026-07-30 01:30"),
                ("2026-11-11", UTC0, "01:30", "2026-11-11 20:30", "2026-11-12 01:30"),
                ("2026-11-16", UTC0, "03:30", "2026-11-15 22:30", "2026-11-16 03:30"),
                ("2026-11-13", UTC0, "03:30", "2026-11-13 22:30", "2026-11-14 03:30")]
        for d, end, hhmm, u, p in rows:
            got = fx.pkt_time_on_fx_day(hhmm, d, end)
            self.assertEqual(got, utc(u), (d, end, hhmm))
            self.assertEqual(fx.to_pkt(got), utc(p))
        self.assertEqual(fx.pkt_time_on_fx_day(datetime.time(23, 30), "2026-07-23", NY), utc("2026-07-23 18:30"))
        # the Friday case is past the end: no error, the caller compares
        self.assertGreater(fx.pkt_time_on_fx_day("03:30", "2026-11-13", UTC0), fx.fx_day_bounds("2026-11-13", UTC0)[1])
        with self.assertRaises(ValueError):
            fx.pkt_time_on_fx_day("25:00", "2026-11-11", NY)

    def pkt_span(self, name, d):
        a, b = fx.session_bounds(name, d)
        return fx.to_pkt(a).strftime("%H:%M") + "-" + fx.to_pkt(b).strftime("%H:%M")

    def test_session_table(self):
        # one Wednesday in each period of part 03 section 4
        rows = [("2026-01-14", "01:00-10:00", "05:00-14:00", "13:00-22:00", "18:00-03:00"),
                ("2026-03-11", "01:00-10:00", "05:00-14:00", "13:00-22:00", "17:00-02:00"),
                ("2026-04-01", "01:00-10:00", "05:00-14:00", "12:00-21:00", "17:00-02:00"),
                ("2026-07-15", "02:00-11:00", "05:00-14:00", "12:00-21:00", "17:00-02:00"),
                ("2026-10-07", "01:00-10:00", "05:00-14:00", "12:00-21:00", "17:00-02:00"),
                ("2026-10-28", "01:00-10:00", "05:00-14:00", "13:00-22:00", "17:00-02:00"),
                ("2026-11-11", "01:00-10:00", "05:00-14:00", "13:00-22:00", "18:00-03:00"),
                ("2027-03-17", "01:00-10:00", "05:00-14:00", "13:00-22:00", "17:00-02:00"),
                ("2027-03-31", "01:00-10:00", "05:00-14:00", "12:00-21:00", "17:00-02:00"),
                ("2027-07-14", "02:00-11:00", "05:00-14:00", "12:00-21:00", "17:00-02:00"),
                ("2027-10-13", "01:00-10:00", "05:00-14:00", "12:00-21:00", "17:00-02:00"),
                ("2027-11-03", "01:00-10:00", "05:00-14:00", "13:00-22:00", "17:00-02:00"),
                ("2027-12-15", "01:00-10:00", "05:00-14:00", "13:00-22:00", "18:00-03:00")]
        for d, syd, tok, lon, nyk in rows:
            got = tuple(self.pkt_span(n, d) for n in ("sydney", "tokyo", "london", "new_york"))
            self.assertEqual(got, (syd, tok, lon, nyk), d)
            start, end = fx.fx_day_bounds(d, NY)
            for n in ("tokyo", "london", "new_york"):
                a, b = fx.session_bounds(n, d)
                self.assertTrue(start <= a and b <= end, (n, d))
            self.assertLessEqual(fx.session_bounds("sydney", d)[0], start)
        with self.assertRaises(ValueError) as cm:
            fx.session_bounds("frankfurt", "2026-11-11")
        self.assertEqual(str(cm.exception), 'A session is "sydney", "tokyo", "london" or "new_york", not "frankfurt"')

    def test_opening_range(self):
        self.assertEqual(fx.opening_range_window("london", "2026-11-11", 15),
                         (utc("2026-11-11 08:00"), utc("2026-11-11 08:15")))
        self.assertEqual(fx.opening_range_window("london", "2026-07-15", 15),
                         (utc("2026-07-15 07:00"), utc("2026-07-15 07:15")))
        for d, lon, nyk in (("2026-11-11", "13:00", "18:00"), ("2026-07-15", "12:00", "17:00"),
                            ("2026-03-11", "13:00", "17:00")):
            self.assertEqual(fx.to_pkt(fx.opening_range_window("london", d, 15)[0]).strftime("%H:%M"), lon)
            self.assertEqual(fx.to_pkt(fx.opening_range_window("new_york", d, 15)[0]).strftime("%H:%M"), nyk)
            a, b = fx.opening_range_window("tokyo", d, 30)
            self.assertEqual((fx.to_pkt(a).strftime("%H:%M"), b - a), ("05:00", 30 * M))
        with self.assertRaises(ValueError) as cm:
            fx.opening_range_window("sydney", "2026-11-11", 15)
        self.assertEqual(str(cm.exception), '"day.or_session" must be "london", "new_york" or "tokyo"')
        for bad in (7, 0, 125, "15", 15.0, True):
            with self.assertRaises(ValueError) as cm:
                fx.opening_range_window("london", "2026-11-11", bad)
            self.assertEqual(str(cm.exception), '"day.or_minutes" must be a multiple of 5 (the bars are 5 minutes)')
        with self.assertRaises(ValueError) as cm:
            fx.opening_range_window("london", "2026-11-15", 15)
        self.assertEqual(str(cm.exception), "2026-11-15 is a Sunday. FX days are Monday to Friday.")


def ny_span(spans):
    """[(a, b)] in UTC -> [("Wed 18:00", "Thu 13:45")] in New York time (test shorthand)."""
    return [tuple(fx.to_ny(x).strftime("%a %H:%M") for x in s) for s in spans]


class Hours(unittest.TestCase):
    def test_templates(self):
        self.assertEqual(set(fx.HOURS), set(fx.BROKER_TEMPLATES) | set(fx.DATA_TEMPLATES))
        with self.assertRaises(ValueError) as cm:
            fx.open_spans("lme", "2026-11-11", make_cfg())
        self.assertEqual(str(cm.exception),
                         'Unknown hours template "lme". Use one of: fx, comex, nymex, ice_brent, yahoo_fx, cme.')
        with self.assertRaises(ValueError):
            fx.open_spans("fx", "2026-11-14", make_cfg())

    def test_normal_days(self):
        cfg = make_cfg()
        self.assertEqual(ny_span(fx.open_spans("fx", "2026-11-11", cfg)), [("Tue 17:05", "Wed 17:00")])
        self.assertEqual(ny_span(fx.open_spans("comex", "2026-11-16", cfg)), [("Sun 18:00", "Mon 17:00")])
        self.assertEqual(ny_span(fx.open_spans("yahoo_fx", "2026-11-16", cfg)), [("Sun 19:00", "Mon 17:00")])
        self.assertEqual(ny_span(fx.open_spans("yahoo_fx", "2026-11-17", cfg)), [("Mon 17:00", "Tue 17:00")])
        self.assertEqual(ny_span(fx.open_spans("ice_brent", "2026-11-11", cfg)), [("Tue 20:00", "Wed 17:00")])
        self.assertEqual(ny_span(fx.open_spans("ice_brent", "2026-11-16", cfg)), [("Sun 18:00", "Mon 17:00")])

    def test_cme_examples(self):
        cfg = make_cfg()
        self.assertEqual(fx.open_spans("cme", "2026-09-07", cfg), [])
        self.assertEqual(ny_span(fx.open_spans("cme", "2026-09-08", cfg)), [("Tue 00:00", "Tue 17:00")])
        self.assertEqual(ny_span(fx.open_spans("cme", "2026-11-26", cfg)), [("Wed 18:00", "Thu 00:00")])
        self.assertEqual(ny_span(fx.open_spans("cme", "2026-11-27", cfg)), [("Fri 00:00", "Fri 14:45")])
        self.assertEqual(ny_span(fx.open_spans("cme", "2026-12-24", cfg)), [("Wed 18:00", "Thu 13:45")])
        self.assertEqual(fx.open_spans("cme", "2026-12-25", cfg), [])
        # tables=False keeps only the weekly hours and the daily break
        self.assertEqual(ny_span(fx.open_spans("cme", "2026-09-07", cfg, tables=False)), [("Sun 18:00", "Mon 17:00")])
        # the broker template has no Yahoo cut
        self.assertEqual(ny_span(fx.open_spans("comex", "2026-11-26", cfg)), [("Wed 18:00", "Thu 14:30")])

    def test_config_tables(self):
        cfg = make_cfg(holidays={"2026-11-12": "broker notice"}, early_closes={"2026-11-11": "12:00"})
        self.assertEqual(fx.open_spans("fx", "2026-11-12", cfg), [])
        self.assertEqual(ny_span(fx.open_spans("fx", "2026-11-11", cfg)), [("Tue 17:05", "Wed 12:00")])
        cfg = make_cfg(early_closes={"2026-11-26": "15:00"})
        self.assertEqual(ny_span(fx.open_spans("comex", "2026-11-26", cfg)), [("Wed 18:00", "Thu 14:30")])
        cfg = make_cfg(early_closes={"2026-07-03": "13:00"})
        self.assertEqual(fx.day_close_utc(INST["EURUSD"], "2026-07-03", cfg), utc("2026-07-03 17:00"))

    def test_day_close(self):
        cfg = make_cfg()
        gold = INST["XAUUSD"]
        self.assertEqual(fx.day_close_utc(gold, "2026-07-03", cfg), utc("2026-07-03 17:00"))
        self.assertEqual(fx.day_close_utc(gold, "2026-11-11", cfg), utc("2026-11-11 22:00"))
        self.assertIsNone(fx.day_close_utc(gold, "2026-12-25", cfg))
        flat = fx.pkt_time_on_fx_day("23:30", "2026-07-03", NY)
        flat = min(flat, fx.day_close_utc(gold, "2026-07-03", cfg) - 15 * M)
        self.assertEqual(fx.utc_iso(flat), "2026-07-03T16:45:00Z")
        self.assertEqual(fx.pkt_label(flat), "Fri 9:45 PM PKT")
        self.assertEqual(fx.utc_iso(fx.entry_cutoff(gold, "2026-07-03", utc("2026-07-03 10:00"), cfg)),
                         "2026-07-03T16:00:00Z")
        self.assertEqual(fx.entry_cutoff(gold, "2026-11-11", utc("2026-11-11 10:00"), cfg), utc("2026-11-11 16:00"))
        late = make_cfg(day={"no_entries_after": "22:00", "flat_by": "23:30"})
        self.assertEqual(fx.entry_cutoff(gold, "2026-07-03", utc("2026-07-03 10:00"), late), utc("2026-07-03 16:30"))
        self.assertEqual(fx.entry_cutoff(gold, "2026-12-25", utc("2026-12-25 10:00"), cfg), utc("2026-12-25 16:00"))

    def test_last_done_fx_day(self):
        cfg = make_cfg()
        gold, eur = INST["XAUUSD"], INST["EURUSD"]
        self.assertEqual(fx.last_done_fx_day(eur, utc("2026-11-11 06:30"), cfg), "2026-11-10")
        self.assertEqual(fx.last_done_fx_day(eur, utc("2026-11-11 22:00"), cfg), "2026-11-11")
        self.assertEqual(fx.last_done_fx_day(eur, utc("2026-11-14 10:00"), cfg), "2026-11-13")
        self.assertEqual(fx.last_done_fx_day(eur, utc("2026-11-16 10:00"), cfg), "2026-11-13")
        # gold has no Yahoo data on Labor Day: the Friday before
        self.assertEqual(fx.last_done_fx_day(gold, utc("2026-09-08 10:00"), cfg), "2026-09-04")
        self.assertEqual(fx.last_done_fx_day(eur, utc("2026-09-08 10:00"), cfg), "2026-09-07")
        shut = make_cfg(holidays={"2026-11-%02d" % d: "x" for d in range(1, 31)})
        self.assertIsNone(fx.last_done_fx_day(eur, utc("2026-11-25 10:00"), shut))

    def test_market_state_table(self):
        cfg = make_cfg()
        rows = [("EURUSD", "hours", "2026-11-11 06:30", "open", "Thu 12 Nov 3:05 AM PKT", "Thu 12 Nov 3:00 AM PKT"),
                ("XAUUSD", "hours", "2026-11-11 22:30", "break", "Thu 12 Nov 4:00 AM PKT", "Fri 13 Nov 3:00 AM PKT"),
                ("EURUSD", "hours", "2026-11-14 10:00", "weekend", "Mon 16 Nov 3:05 AM PKT", "Tue 17 Nov 3:00 AM PKT"),
                ("EURUSD", "hours", "2026-11-15 22:02", "weekend", "Mon 16 Nov 3:05 AM PKT", "Tue 17 Nov 3:00 AM PKT"),
                ("EURUSD", "data_hours", "2026-11-15 23:00", "closed", "Mon 16 Nov 5:00 AM PKT", "Tue 17 Nov 3:00 AM PKT"),
                ("UKOIL", "hours", "2026-11-15 22:30", "weekend", "Mon 16 Nov 4:00 AM PKT", "Tue 17 Nov 3:00 AM PKT"),
                ("UKOIL", "hours", "2026-11-10 23:30", "break", "Wed 11 Nov 6:00 AM PKT", "Thu 12 Nov 3:00 AM PKT"),
                ("XAUUSD", "hours", "2026-11-26 20:00", "holiday", "Fri 27 Nov 4:00 AM PKT", "Sat 28 Nov 12:45 AM PKT"),
                ("UKOIL", "hours", "2026-11-26 19:00", "holiday", "Fri 27 Nov 6:00 AM PKT", "Sat 28 Nov 1:00 AM PKT"),
                ("XAUUSD", "hours", "2026-12-24 19:00", "holiday", "Mon 28 Dec 4:00 AM PKT", "Tue 29 Dec 3:00 AM PKT"),
                ("EURUSD", "hours", "2026-12-25 10:00", "holiday", "Mon 28 Dec 3:05 AM PKT", "Tue 29 Dec 3:00 AM PKT")]
        for key, which, t, state, nxt_open, nxt_close in rows:
            st = fx.market_state(INST[key], utc(t), cfg, which=which)
            got = (st["state"], fx.pkt_label(st["next_open"], date=True), fx.pkt_label(st["next_close"], date=True))
            self.assertEqual(got, (state, nxt_open, nxt_close), (key, t))
            self.assertEqual(st["template"], INST[key][which])
            self.assertEqual(fx.is_open(INST[key], utc(t), cfg, which=which), state == "open")

    def test_market_state_notes(self):
        cfg = make_cfg()
        note = lambda k, t, w="hours": fx.market_state(INST[k], utc(t), cfg, which=w)["note"]
        self.assertEqual(note("EURUSD", "2026-11-11 22:02"), "Short pause at the 5:00 PM New York rollover. Spreads are wide.")
        self.assertEqual(note("XAUUSD", "2026-11-11 22:30"), "Daily break from 5:00 PM to 6:00 PM New York.")
        self.assertEqual(note("UKOIL", "2026-11-10 23:30"), "Brent is shut from 5:00 PM New York to 1:00 AM London.")
        self.assertEqual(note("EURUSD", "2026-11-14 10:00"), "Closed for the weekend.")
        self.assertEqual(note("EURUSD", "2026-11-15 23:00", "data_hours"), "Yahoo has no FX prices before Monday 00:00 London.")
        self.assertEqual(note("EURUSD", "2026-12-25 10:00"), "Fri 25 Dec: Christmas. FX is shut all day."
                         " These hours are expected, not confirmed. Check your broker's notice.")
        self.assertEqual(note("EURUSD", "2026-11-12 10:00"), "")
        self.assertEqual(note("EURUSD", "2026-11-11 10:00"), "Wed 11 Nov: Veterans Day (US). Markets are open as usual.")
        st = fx.market_state(INST["XAUUSD"], utc("2026-09-07 15:00"), cfg, which="data_hours")
        self.assertEqual((st["state"], st["note"]), ("holiday", "No Yahoo futures prices on US holidays."))
        self.assertEqual(fx.market_state(INST["XAUUSD"], utc("2026-09-07 15:00"), cfg)["state"], "open")
        with self.assertRaises(ValueError):
            fx.market_state({"hours": "lme"}, utc("2026-11-11 10:00"), cfg)

    def test_holiday_note(self):
        cfg = make_cfg()
        gold, eur, brent = INST["XAUUSD"], INST["EURUSD"], INST["UKOIL"]
        self.assertEqual(fx.holiday_note("2026-11-26", gold, cfg),
                         "Thu 26 Nov: Thanksgiving (US). Gold and oil stops early, at 2:30 PM New York (Fri 12:30 AM PKT).")
        self.assertEqual(fx.holiday_note("2026-11-26", brent, cfg),
                         "Thu 26 Nov: Thanksgiving (US). Brent stops early, at 1:30 PM New York (Thu 11:30 PM PKT).")
        self.assertEqual(fx.holiday_note("2026-11-26", eur, cfg),
                         "Thu 26 Nov: Thanksgiving (US). Open, but few traders. Spreads can be wide.")
        self.assertEqual(fx.holiday_note("2026-12-28", eur, cfg),
                         "Mon 28 Dec: Boxing Day (UK). Open, but few traders. Spreads can be wide.")
        self.assertEqual(fx.holiday_note("2026-12-25", gold, cfg), "Fri 25 Dec: Christmas. Gold and oil is shut all day.")
        self.assertTrue(fx.holiday_note("2026-12-24", gold, cfg).endswith("Check your broker's notice."))
        self.assertIsNone(fx.holiday_note("2026-11-12", eur, cfg))
        self.assertEqual(fx.holiday_note("2028-01-04", eur, cfg),
                         "This tool's holiday list ends on 2027-12-31. Check your broker's holiday notice.")
        self.assertEqual(fx.holiday_note("2024-10-01", eur, cfg), "This tool's holiday list starts on 2024-11-01.")
        mine = make_cfg(holidays={"2026-11-12": "server move"}, early_closes={"2026-11-13": "12:00"})
        self.assertEqual(fx.holiday_note("2026-11-12", eur, mine), "Thu 12 Nov: your broker is shut (server move).")
        self.assertEqual(fx.holiday_note("2026-11-13", eur, mine),
                         "Fri 13 Nov: Early close (your config). FX stops early, at 12:00 PM New York (Fri 10:00 PM PKT).")


class Tables(unittest.TestCase):
    def test_shapes(self):
        groups = {"fx", "cme", "ice"}
        for iso, row in fx.HOLIDAYS.items():
            d = datetime.date.fromisoformat(iso)
            self.assertLess(d.weekday(), 5, iso)
            self.assertTrue(fx.TABLES_START <= iso <= fx.TABLES_END, iso)
            self.assertTrue(set(row) <= {"name", "closed", "thin", "no_data", "provisional"}, iso)
            for k in ("closed", "thin", "no_data", "provisional"):
                self.assertTrue(set(row.get(k, [])) <= groups, iso)
        for iso, row in fx.EARLY_CLOSES.items():
            self.assertLess(datetime.date.fromisoformat(iso).weekday(), 5, iso)
            for g in ("cme", "ice"):
                if g in row:
                    self.assertLess(fx.parse_hhmm(row[g], iso), datetime.time(17), iso)
        full = sorted(i for i in fx.HOLIDAYS if i >= "2026-11-11" and fx.full_fx_holiday(i, {}))
        self.assertEqual(full, ["2026-12-25", "2027-01-01"])
        nodata = sorted(i for i, r in fx.HOLIDAYS.items() if i >= "2026-11-11" and "cme" in r.get("no_data", []))
        self.assertEqual(nodata, ["2026-11-26", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18",
                                  "2027-07-05", "2027-09-06", "2027-11-25"])
        for bank in fx.CB_MEETINGS.values():
            for iso in bank["dates"]:
                self.assertLess(datetime.date.fromisoformat(iso).weekday(), 5, iso)
        self.assertEqual(len(fx.NFP_DATES), 13)
        self.assertEqual(fx.COT_RELEASES["dates"][-1], "2028-01-03")

    def test_eia_examples(self):
        ev = fx.builtin_events(utc("2026-11-09 00:00"), utc("2026-11-14 00:00"), make_cfg())
        eia = [(e["kind"], fx.pkt_label(e["at"], date=True)) for e in ev if e["kind"].startswith("eia")]
        self.assertEqual(eia, [("eia_crude", "Thu 12 Nov 10:00 PM PKT"), ("eia_gas", "Fri 13 Nov 8:30 PM PKT")])
        ev = fx.builtin_events(utc("2026-11-23 00:00"), utc("2026-11-28 00:00"), make_cfg())
        eia = [(e["kind"], fx.pkt_label(e["at"], date=True), e["provisional"]) for e in ev if e["kind"].startswith("eia")]
        self.assertEqual(eia, [("eia_crude", "Wed 25 Nov 8:30 PM PKT", False), ("eia_gas", "Wed 25 Nov 10:00 PM PKT", False)])
        ev = fx.builtin_events(utc("2027-01-18 00:00"), utc("2027-01-23 00:00"), make_cfg())
        crude = [e for e in ev if e["kind"] == "eia_crude"]
        self.assertEqual([(fx.pkt_label(e["at"], date=True), e["provisional"]) for e in crude],
                         [("Thu 21 Jan 10:00 PM PKT", True)])
        self.assertEqual(crude[0]["symbols"], ["USOIL", "UKOIL"])
        # summer: 10:30 New York is 7:30 PM PKT
        ev = fx.builtin_events(utc("2026-07-20 00:00"), utc("2026-07-25 00:00"), make_cfg())
        self.assertEqual([fx.pkt_label(e["at"]) for e in ev if e["kind"] == "eia_crude"], ["Wed 7:30 PM PKT"])

    def test_cb_checks(self):
        ev = fx.builtin_events(utc("2026-12-01 00:00"), utc("2027-07-01 00:00"), make_cfg())
        def at(title, iso):
            hit = [e for e in ev if e["title"] == title and fx.to_ny(e["at"]).date().isoformat() == iso
                   or e["title"] == title and fx.to_tokyo(e["at"]).date().isoformat() == iso]
            self.assertEqual(len(hit), 1, (title, iso))
            return hit[0]
        fomc = at("Fed rate decision (FOMC)", "2026-12-09")
        self.assertEqual((fomc["at"], fx.pkt_label(fomc["at"], date=True)), (utc("2026-12-09 19:00"), "Thu 10 Dec 12:00 AM PKT"))
        self.assertEqual((fomc["end"], fomc["impact"], fomc["country"], fomc["kind"]), (utc("2026-12-09 19:30"), "High", "USD", "cb"))
        self.assertEqual(fx.pkt_label(at("Fed rate decision (FOMC)", "2027-06-09")["at"]), "Wed 11:00 PM PKT")
        self.assertEqual(at("ECB rate decision", "2026-12-17")["at"], utc("2026-12-17 13:15"))
        self.assertEqual(at("ECB rate decision", "2027-06-10")["at"], utc("2027-06-10 12:15"))
        self.assertEqual(fx.pkt_label(at("BoE rate decision", "2026-12-17")["at"]), "Thu 5:00 PM PKT")
        self.assertEqual(at("BoJ rate decision", "2026-12-18")["at"], utc("2026-12-18 02:30"))
        self.assertEqual(fx.pkt_label(at("SNB rate decision", "2026-12-10")["at"]), "Thu 1:30 PM PKT")
        self.assertEqual(fx.pkt_label(at("BoC rate decision", "2026-12-09")["at"]), "Wed 7:45 PM PKT")
        self.assertTrue(at("Fed minutes", "2026-12-30")["provisional"])
        self.assertEqual(at("US jobs report (NFP)", "2026-12-04")["at"], utc("2026-12-04 13:30"))
        cot = [e for e in ev if e["kind"] == "cot"]
        self.assertTrue(all(e["symbols"] == list(INST) for e in cot))
        self.assertEqual([e["at"] for e in ev], sorted(e["at"] for e in ev))
        for e in ev:
            self.assertEqual((e["source"], e["forecast"], e["previous"], e["all_day"]), ("built-in", None, None, False))

    def test_cot_and_end(self):
        ev = fx.builtin_events(utc("2026-11-16 00:00"), utc("2026-11-18 00:00"), make_cfg())
        cot = [e for e in ev if e["kind"] == "cot"]
        self.assertEqual([fx.pkt_label(e["at"], date=True) for e in cot], ["Tue 17 Nov 1:30 AM PKT"])
        self.assertFalse(cot[0]["provisional"])
        ev = fx.builtin_events(utc("2027-12-20 00:00"), utc("2028-01-10 00:00"), make_cfg())
        self.assertTrue(all(fx.to_ny(e["at"]).date().isoformat() <= "2027-12-31" for e in ev))
        self.assertTrue([e for e in ev if e["kind"] == "cot"][0]["provisional"])
        # no crude report is lost in a whole year, and none falls on a cme holiday
        ev = fx.builtin_events(utc("2027-01-01 00:00"), utc("2028-01-01 00:00"), make_cfg())
        self.assertEqual(len([e for e in ev if e["kind"] == "eia_crude"]), 52)

    def test_key_times(self):
        kt = fx.key_times("2026-11-11", make_cfg())
        got = [(fx.to_pkt(t).strftime("%H:%M"), label) for t, label in kt]
        self.assertEqual(got, [("15:30", "LBMA gold price"), ("18:30", "US data time"), ("20:00", "US data time"),
                               ("20:00", "LBMA gold price"), ("21:00", "London 4 pm FX fix"),
                               ("03:00", "Gold and oil daily break"), ("03:00", "Rollover (swap is booked)")])
        summer = [(fx.to_pkt(t).strftime("%H:%M"), label) for t, label in fx.key_times("2026-07-15", make_cfg())]
        self.assertIn(("17:30", "US data time"), summer)
        self.assertIn(("20:00", "London 4 pm FX fix"), summer)
        self.assertIn(("02:00", "Rollover (swap is booked)"), summer)
        boxing = [label for t, label in fx.key_times("2026-12-28", make_cfg())]
        self.assertNotIn("LBMA gold price", boxing)


def row(title, country, date, impact="High", forecast="", previous=""):
    return {"title": title, "country": country, "date": date, "impact": impact,
            "forecast": forecast, "previous": previous}


# a made-up feed for the week of Sun 2026-11-08 (never a real file)
FEED = [row("CPI m/m", "USD", "2026-11-10T08:30:00-05:00", forecast="0.3%", previous="0.2%"),
        row("Bank Holiday", "USD", "2026-11-11T02:01:00-05:00", impact="Holiday"),
        row("Main Refinancing Rate", "EUR", "2026-11-12T08:15:00-05:00", forecast=" 2.00% "),
        row("Crude Oil Inventories", "USD", "2026-11-12T12:00:00-05:00", impact="Low", forecast="-1.2M", previous="-0.9M"),
        row("Natural Gas Storage", "USD", "2026-11-13T10:30:00-05:00", impact="Low"),
        row("CPI m/m", "USD", "2026-11-10T08:30:00-05:00"),
        row("Day off", "JPY", "2026-11-11", impact="Holiday"),
        row("Market note", "CHF", "2026-11-12", impact="Low"),
        row("Broken", "GBP", "soon"),
        "not a row"]


class Calendar(unittest.TestCase):
    def test_calendar_week(self):
        self.assertEqual(fx.calendar_week(utc("2026-11-11 06:30")), "2026-11-08")
        self.assertEqual(fx.calendar_week(utc("2026-11-14 12:00")), "2026-11-08")
        self.assertEqual(fx.calendar_week(utc("2026-11-15 10:00")), "2026-11-15")
        self.assertEqual(fx.calendar_week(utc("2026-11-16 03:00")), "2026-11-15")

    def test_event_label(self):
        self.assertEqual(fx.event_label({"title": "Main Refinancing Rate", "country": "EUR"}), "Main Refinancing Rate (EUR)")

    def test_parse_ff(self):
        ev = fx.parse_ff({"events": FEED})
        self.assertEqual(ev, fx.parse_ff(FEED))
        self.assertEqual(len(ev), 7)     # one duplicate and two bad rows dropped
        note = [e for e in ev if e["country"] == "CHF"][0]
        self.assertEqual((note["at"], note["end"], note["all_day"]), (utc("2026-11-12 05:00"), utc("2026-11-13 05:00"), True))
        cpi = ev[0]
        self.assertEqual((cpi["title"], cpi["at"], cpi["end"], cpi["forecast"], cpi["previous"]),
                         ("CPI m/m", utc("2026-11-10 13:30"), None, "0.3%", "0.2%"))
        self.assertEqual((cpi["source"], cpi["kind"], cpi["all_day"], cpi["provisional"], cpi["symbols"]),
                         ("forexfactory", None, False, False, None))
        hol = [e for e in ev if e["impact"] == "Holiday"]
        self.assertEqual([(e["at"], e["end"], e["all_day"]) for e in hol],
                         [(utc("2026-11-11 05:00"), utc("2026-11-12 05:00"), True)] * 2)
        ecb = [e for e in ev if e["country"] == "EUR"][0]
        self.assertEqual((ecb["kind"], ecb["forecast"], ecb["previous"]), ("cb", "2.00%", None))
        self.assertEqual([e["kind"] for e in ev if e["kind"] and e["kind"].startswith("eia")], ["eia_crude", "eia_gas"])
        self.assertEqual([e["at"] for e in ev], sorted(e["at"] for e in ev))
        self.assertEqual(fx.parse_ff(None), [])

    def test_symbols_and_impact(self):
        cfg = make_cfg()
        usd = fx.make_event("CPI m/m", "USD", utc("2026-11-10 13:30"), None, "High")
        self.assertEqual(fx.event_symbols(usd, cfg), ["EURUSD", "USDJPY", "XAUUSD", "USOIL", "UKOIL", "NATGAS"])
        eur = fx.make_event("x", "EUR", utc("2026-11-10 13:30"), None, "High")
        self.assertEqual(fx.event_symbols(eur, cfg), ["EURUSD", "EURGBP"])
        self.assertEqual(fx.event_symbols(fx.make_event("x", "CNY", utc("2026-11-10 13:30"), None, "High"), cfg), [])
        self.assertEqual(fx.event_symbols(fx.make_event("x", "All", utc("2026-11-10 13:30"), None, "High"), cfg), list(INST))
        crude = fx.parse_ff([FEED[3]])[0]
        self.assertEqual(fx.event_symbols(crude, cfg), ["USOIL", "UKOIL"])
        self.assertEqual(fx.event_impact(crude, "USOIL"), "High")
        self.assertEqual(fx.event_impact(crude, "XAUUSD"), "Low")
        gas = fx.parse_ff([FEED[4]])[0]
        self.assertEqual((fx.event_symbols(gas, cfg), fx.event_impact(gas, "NATGAS")), (["NATGAS"], "High"))
        own = fx.make_event("x", "USD", utc("2026-11-10 13:30"), None, "Low", symbols=["XAUUSD", "GOLD2"])
        self.assertEqual(fx.event_symbols(own, cfg), ["XAUUSD"])

    def test_quiet_examples(self):
        cfg = make_cfg()
        ev = fx.parse_ff(FEED)
        for e in ev:
            e["symbols"] = fx.event_symbols(e, cfg)
        w = fx.quiet_windows(ev, "EURUSD", cfg)
        cpi = [x for x in w if x[2]["title"] == "CPI m/m"]
        self.assertEqual([(a, b) for a, b, e in cpi], [(utc("2026-11-10 13:00"), utc("2026-11-10 14:00"))])
        self.assertEqual(fx.pkt_label(cpi[0][0]) + " " + fx.pkt_label(cpi[0][1]), "Tue 6:00 PM PKT Tue 7:00 PM PKT")
        self.assertFalse([x for x in fx.quiet_windows(ev, "EURGBP", cfg) if x[2]["country"] == "USD"])
        for key in ("USOIL", "UKOIL"):
            oil = [x for x in fx.quiet_windows(ev, key, cfg) if x[2]["kind"] == "eia_crude"]
            self.assertEqual([(fx.pkt_label(a), fx.pkt_label(b)) for a, b, e in oil], [("Thu 9:30 PM PKT", "Thu 10:30 PM PKT")])
        self.assertFalse([x for x in fx.quiet_windows(ev, "XAUUSD", cfg) if x[2]["kind"] == "eia_crude"])
        self.assertFalse([x for x in w if x[2]["all_day"]])
        self.assertEqual(fx.in_quiet_window(ev, "EURUSD", utc("2026-11-10 13:59"), cfg)["title"], "CPI m/m")
        self.assertIsNone(fx.in_quiet_window(ev, "EURUSD", utc("2026-11-10 14:00"), cfg))
        self.assertIsNone(fx.in_quiet_window(ev, "EURGBP", utc("2026-11-10 13:30"), cfg))
        # windows without "symbols" use event_symbols
        bare = fx.parse_ff(FEED)
        self.assertEqual(len(fx.quiet_windows(bare, "EURUSD", cfg)), len(w))
        fomc = fx.builtin_events(utc("2026-12-09 00:00"), utc("2026-12-10 00:00"), cfg)
        fomc = [e for e in fomc if e["title"] == "Fed rate decision (FOMC)"]
        for key in ("EURUSD", "USDJPY", "XAUUSD", "USOIL", "UKOIL", "NATGAS"):
            a, b, e = fx.quiet_windows(fomc, key, cfg)[0]
            self.assertEqual((fx.pkt_label(a), fx.pkt_label(b)), ("Wed 11:30 PM PKT", "Thu 1:00 AM PKT"))
        self.assertEqual(fx.quiet_windows(fomc, "EURGBP", cfg), [])

    def test_next_events(self):
        cfg = make_cfg()
        ev = fx.parse_ff(FEED)
        for e in ev:
            e["symbols"] = fx.event_symbols(e, cfg)
        t = utc("2026-11-11 00:00")
        self.assertEqual([e["title"] for e in fx.next_events(ev, None, t, 2)], ["Bank Holiday", "Day off"])
        got = fx.next_events(ev, ["USOIL"], t, 5, impacts=["High"])
        self.assertEqual([e["title"] for e in got], ["Crude Oil Inventories"])
        got = fx.next_events(ev, ["EURGBP"], t, 5)
        self.assertEqual([e["title"] for e in got], ["Main Refinancing Rate"])
        self.assertEqual(fx.next_events(ev, ["EURUSD"], utc("2026-11-14 00:00"), 5), [])
        self.assertEqual([e["title"] for e in fx.next_events(ev, None, t, 9, impacts=["High"])],
                         ["Main Refinancing Rate", "Crude Oil Inventories", "Natural Gas Storage"])


class FakeGet(object):
    """Stands in for fx_core.calendar_get: no network. Records each call."""
    def __init__(self, status=200, body=None, headers=None):
        self.status, self.headers, self.calls = status, headers or {}, []
        self.body = json.dumps(FEED).encode("utf-8") if body is None else body

    def __call__(self, url, headers, timeout=20):
        self.calls.append((url, dict(headers)))
        return self.status, self.body, self.headers


class LoadCalendar(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.real = fx.calendar_get
        self.now = utc("2026-11-11 06:30")
        self.path = os.path.join(self.dir, "calendar", "2026-11-08.json")

    def tearDown(self):
        fx.calendar_get = self.real
        shutil.rmtree(self.dir)

    def use(self, fake):
        fx.calendar_get = fake
        return fake

    def saved(self, path=None):
        with open(path or self.path) as fh:
            return json.load(fh)

    def test_first_fetch_and_merge(self):
        get = self.use(FakeGet(headers={"ETag": '"abc"', "Last-Modified": "Wed, 11 Nov 2026 05:42:00 GMT"}))
        cfg = make_cfg()
        ev = fx.load_calendar(self.dir, self.now, cfg, False)
        self.assertEqual(len(get.calls), 1)
        doc = self.saved()
        self.assertEqual((doc["ok"], doc["error"], doc["week"], doc["etag"]), (True, None, "2026-11-08", '"abc"'))
        self.assertEqual(doc["fetched_at"], "2026-11-11T06:30:00Z")
        self.assertEqual(doc["events"], FEED)          # raw rows, never edited
        # the feed's crude and gas rows win over the built-in ones; COT stays
        crude = [e for e in ev if e["kind"] == "eia_crude" and e["at"] < utc("2026-11-14 00:00")]
        self.assertEqual([e["source"] for e in crude], ["forexfactory"])
        self.assertEqual(crude[0]["symbols"], ["USOIL", "UKOIL"])
        self.assertTrue([e for e in ev if e["kind"] == "cot"])
        self.assertTrue([e for e in ev if e["title"] == "Fed rate decision (FOMC)"])   # 2026-12-09, within 35 days
        self.assertFalse([e for e in ev if e["at"] >= self.now + datetime.timedelta(days=35)])
        self.assertTrue(all(isinstance(e["symbols"], list) for e in ev))
        self.assertEqual([e["at"] for e in ev], sorted(e["at"] for e in ev))
        info = fx.calendar_info(self.dir, self.now + 14 * M)
        self.assertEqual((info["week"], info["ok"], info["age_min"], info["events"], info["error"]),
                         ("2026-11-08", True, 14, len(FEED), None))

    def test_once_an_hour_and_304(self):
        self.use(FakeGet(headers={"ETag": '"abc"', "Last-Modified": "lm"}))
        fx.load_calendar(self.dir, self.now, make_cfg(), False)
        get = self.use(FakeGet(status=304, body=b""))
        fx.load_calendar(self.dir, self.now + 59 * M, make_cfg(), False)
        self.assertEqual(get.calls, [])
        ev = fx.load_calendar(self.dir, self.now + 60 * M, make_cfg(), False)
        self.assertEqual(get.calls[0][1], {"If-None-Match": '"abc"', "If-Modified-Since": "lm"})
        doc = self.saved()
        self.assertEqual((doc["ok"], doc["fetched_at"], doc["tried_at"]), (True, "2026-11-11T07:30:00Z", "2026-11-11T07:30:00Z"))
        self.assertEqual(doc["events"], FEED)
        self.assertTrue([e for e in ev if e["title"] == "CPI m/m"])

    def test_failures_keep_old_events(self):
        self.use(FakeGet())
        fx.load_calendar(self.dir, self.now, make_cfg(), False)
        cases = [(FakeGet(status=403, body=b"<html>Request Denied</html>"), "http 403"),
                 (FakeGet(body=b"<html>"), "not json"),
                 (FakeGet(body=b'{"a": 1}'), "not a list"),
                 (FakeGet(body=b"[1, 2]"), "empty"),
                 (FakeGet(status=None, body=b""), "network")]
        t = self.now
        for fake, error in cases:
            t += 61 * M
            self.use(fake)
            ev = fx.load_calendar(self.dir, t, make_cfg(), False)
            doc = self.saved()
            self.assertEqual((doc["ok"], doc["error"], doc["tried_at"], doc["fetched_at"]),
                             (False, error, fx.utc_iso(t), "2026-11-11T06:30:00Z"), error)
            self.assertEqual(doc["events"], FEED)
            self.assertTrue([e for e in ev if e["title"] == "CPI m/m"])
        # a failed try also waits an hour
        get = self.use(FakeGet())
        fx.load_calendar(self.dir, t + 30 * M, make_cfg(), False)
        self.assertEqual(get.calls, [])

    def test_first_fetch_fails(self):
        self.use(FakeGet(status=None, body=b""))
        ev = fx.load_calendar(self.dir, self.now, make_cfg(), False)
        doc = self.saved()
        self.assertEqual((doc["ok"], doc["error"], doc["fetched_at"], doc["events"]), (False, "network", None, []))
        self.assertTrue(ev)
        self.assertTrue(all(e["source"] == "built-in" for e in ev))
        crude = [e for e in ev if e["kind"] == "eia_crude" and e["at"] < utc("2026-11-14 00:00")]
        self.assertEqual([fx.pkt_label(e["at"]) for e in crude], ["Thu 10:00 PM PKT"])

    def test_old_week(self):
        old = [row("CPI m/m", "USD", "2026-11-03T08:30:00-05:00"), row("GDP", "USD", "2026-11-05T08:30:00-05:00"),
               row("PMI", "EUR", "2026-11-06T04:00:00-05:00")]
        self.use(FakeGet(body=json.dumps(old).encode("utf-8")))
        fx.load_calendar(self.dir, self.now, make_cfg(), False)
        doc = self.saved()
        self.assertEqual((doc["ok"], doc["error"], doc["events"]), (False, "old week", []))
        prev = self.saved(os.path.join(self.dir, "calendar", "2026-11-01.json"))
        self.assertEqual((prev["ok"], prev["week"], prev["events"]), (True, "2026-11-01", old))

    def test_offline_and_off(self):
        get = self.use(FakeGet())
        ev = fx.load_calendar(self.dir, self.now, make_cfg(), True)
        self.assertEqual(get.calls, [])
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(all(e["source"] == "built-in" for e in ev))
        ev = fx.load_calendar(self.dir, self.now, make_cfg(calendar_url=""), False)
        self.assertEqual(get.calls, [])
        self.assertTrue(all(e["source"] == "built-in" for e in ev))
        info = fx.calendar_info(self.dir, self.now)
        self.assertEqual((info["ok"], info["fetched_at"], info["age_min"], info["events"]), (None, None, None, None))
        # a broken file is read as missing
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("{cut")
        self.assertEqual(fx.calendar_info(self.dir, self.now)["ok"], None)
        get = self.use(FakeGet())
        fx.load_calendar(self.dir, self.now, make_cfg(), False)
        self.assertEqual(len(get.calls), 1)
        self.assertTrue(self.saved()["ok"])


if __name__ == "__main__":
    unittest.main()
