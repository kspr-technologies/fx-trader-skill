"""The economic calendar files of the sample market (06 5.5): three weeks in
ForexFactory's raw shape (skeleton 4.14). Every number is made up."""

import os

from .util import write_json

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# (New York time, country, title, impact, forecast, previous); July is UTC-4 in New York
WEEKS = {
    "2026-07-12": [
        ("2026-07-12 18:50", "JPY", "Core Machinery Orders m/m", "Low", "1.1%", "-0.6%"),
        ("2026-07-13 02:00", "EUR", "German WPI m/m", "Low", "0.2%", "0.1%"),
        ("2026-07-13 14:00", "USD", "Federal Budget Balance", "Low", "-210.4B", "-316.0B"),
        ("2026-07-14 05:00", "EUR", "ZEW Economic Sentiment", "Medium", "38.5", "35.1"),
        ("2026-07-14 08:30", "USD", "CPI m/m", "High", "0.2%", "0.3%"),
        ("2026-07-14 08:30", "USD", "Core CPI m/m", "Medium", "0.3%", "0.3%"),
        ("2026-07-14 21:30", "AUD", "Westpac Consumer Sentiment", "Low", "", "-1.2%"),
        ("2026-07-15 02:00", "GBP", "PPI Input m/m", "Low", "0.1%", "-0.3%"),
        ("2026-07-15 08:30", "USD", "Empire State Manufacturing Index", "Low", "-3.4", "-6.1"),
        ("2026-07-16 02:00", "GBP", "Claimant Count Change", "Low", "15.2K", "22.4K"),
        ("2026-07-16 08:30", "USD", "Unemployment Claims", "Low", "228K", "231K"),
        ("2026-07-16 10:00", "USD", "Business Inventories m/m", "Low", "0.3%", "0.2%"),
        ("2026-07-17 04:00", "EUR", "Current Account", "Low", "31.2B", "28.4B"),
        ("2026-07-17 08:30", "CAD", "Foreign Securities Purchases", "Low", "8.40B", "5.21B"),
        ("2026-07-17 10:00", "USD", "Prelim UoM Consumer Sentiment", "Low", "64.1", "63.8"),
    ],
    "2026-07-19": [
        ("2026-07-19 18:45", "NZD", "CPI q/q", "Low", "0.5%", "0.6%"),
        ("2026-07-20 00:00", "JPY", "Bank Holiday", "Holiday", "", ""),
        ("2026-07-20 02:00", "EUR", "German PPI m/m", "Low", "0.1%", "-0.2%"),
        ("2026-07-20 10:00", "USD", "CB Leading Index m/m", "Low", "-0.1%", "-0.3%"),
        ("2026-07-20 21:30", "AUD", "Monetary Policy Meeting Minutes", "Low", "", ""),
        ("2026-07-21 02:00", "GBP", "Public Sector Net Borrowing", "Low", "14.2B", "17.7B"),
        ("2026-07-21 08:30", "CAD", "CPI m/m", "Medium", "0.2%", "0.4%"),
        ("2026-07-21 10:00", "USD", "Richmond Manufacturing Index", "Low", "-4", "-7"),
        ("2026-07-22 04:00", "EUR", "Current Account", "Low", "30.1B", "31.2B"),
        ("2026-07-22 10:00", "USD", "Existing Home Sales", "Medium", "4.02M", "3.98M"),
        ("2026-07-22 10:30", "USD", "Crude Oil Inventories", "Medium", "-1.2M", "2.4M"),
        ("2026-07-22 18:45", "NZD", "Trade Balance", "Low", "-120M", "-215M"),
        ("2026-07-23 03:15", "EUR", "French Flash Manufacturing PMI", "Low", "49.6", "49.2"),
        ("2026-07-23 03:30", "EUR", "German Flash Manufacturing PMI", "Medium", "48.9", "48.5"),
        ("2026-07-23 04:30", "GBP", "Flash Manufacturing PMI", "Medium", "50.3", "50.1"),
        ("2026-07-23 08:15", "EUR", "Main Refinancing Rate", "High", "2.15%", "2.15%"),
        ("2026-07-23 08:30", "USD", "Unemployment Claims", "Medium", "229K", "226K"),
        ("2026-07-23 08:45", "EUR", "ECB Press Conference", "High", "", ""),
        ("2026-07-23 09:45", "USD", "Flash Manufacturing PMI", "Low", "52.1", "52.4"),
        ("2026-07-23 10:00", "USD", "New Home Sales", "Low", "648K", "623K"),
        ("2026-07-24 02:00", "GBP", "Retail Sales m/m", "Medium", "0.3%", "-0.4%"),
        ("2026-07-24 04:00", "EUR", "German ifo Business Climate", "Low", "88.9", "88.4"),
        ("2026-07-24 08:30", "USD", "Durable Goods Orders m/m", "Medium", "1.1%", "-0.8%"),
        ("2026-07-24 08:30", "CAD", "Retail Sales m/m", "Low", "0.4%", "0.2%"),
    ],
    "2026-07-26": [
        ("2026-07-26 18:50", "JPY", "SPPI y/y", "Low", "3.1%", "3.2%"),
        ("2026-07-27 10:30", "USD", "Dallas Fed Manufacturing Index", "Low", "-6.2", "-9.1"),
        ("2026-07-28 04:00", "EUR", "M3 Money Supply y/y", "Low", "3.4%", "3.3%"),
        ("2026-07-28 10:00", "USD", "CB Consumer Confidence", "Medium", "96.2", "95.0"),
        ("2026-07-28 21:30", "AUD", "CPI q/q", "Medium", "0.8%", "0.9%"),
        ("2026-07-29 08:15", "USD", "ADP Employment Change", "Medium", "95K", "112K"),
        ("2026-07-29 08:30", "USD", "Advance GDP q/q", "Medium", "1.9%", "1.4%"),
        ("2026-07-29 10:30", "USD", "Crude Oil Inventories", "Medium", "0.8M", "-1.6M"),
        ("2026-07-29 14:00", "USD", "Federal Funds Rate", "High", "4.00%", "4.00%"),
        ("2026-07-30 02:00", "EUR", "German Prelim CPI m/m", "Medium", "0.2%", "0.1%"),
        ("2026-07-30 05:00", "EUR", "CPI Flash Estimate y/y", "Medium", "2.0%", "1.9%"),
        ("2026-07-30 08:30", "USD", "Unemployment Claims", "Medium", "231K", "229K"),
        ("2026-07-30 19:30", "JPY", "Tokyo Core CPI y/y", "Low", "2.4%", "2.5%"),
        ("2026-07-31 08:30", "USD", "Core PCE Price Index m/m", "Medium", "0.2%", "0.3%"),
        ("2026-07-31 10:00", "USD", "Revised UoM Consumer Sentiment", "Low", "64.4", "64.1"),
    ],
}
LAST_MODIFIED = {"2026-07-12": "Sun, 12 Jul 2026 20:41:07 GMT", "2026-07-19": "Sun, 19 Jul 2026 20:38:52 GMT",
                 "2026-07-26": "Sun, 26 Jul 2026 20:44:19 GMT"}


def ff_row(when, country, title, impact, forecast, previous):
    """One raw ForexFactory event; the date in New York time with its -04:00 offset."""
    return {"title": title, "country": country, "date": when.replace(" ", "T") + ":00-04:00",
            "impact": impact, "forecast": forecast, "previous": previous}


def write(out):
    """Write data/calendar/<week>.json for the three weeks. Returns the paths."""
    written = []
    for week, rows in sorted(WEEKS.items()):
        at = week + "T21:00:00Z"   # fetched Sunday 17:00 New York, as the week starts
        doc = {"fetched_at": at, "tried_at": at, "error": None, "last_modified": LAST_MODIFIED[week],
               "etag": None, "url": URL, "week": week, "ok": True, "events": [ff_row(*r) for r in rows]}
        path = os.path.join(out, "data", "calendar", week + ".json")
        write_json(path, doc)
        written.append(path)
    return written
