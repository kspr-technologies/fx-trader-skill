"""The saved RSS feeds of the sample market (06 5.5): data/news/<name>.xml for the 10
feeds of the example config, each in the layout of that feed's real answer. Every item
is made up; links are on example.com."""

import datetime
import os
import re

from .util import write_text

MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# feed: [(UTC time, title, description)]; the designed items of 06 5.5 are marked
ITEMS = {
    "fxstreet": [
        ("2026-07-10 08:20", "XAUUSD drifts in a narrow range", "Gold trades in a tight band on Friday morning."),
        ("2026-07-16 21:40", "Gold climbs to a three-week high as the dollar slips",          # designed
         "Gold extends its gains late on Thursday as the US dollar loses ground."),
        ("2026-07-24 09:15", "GBP/USD steadies after UK retail sales", "Cable holds its range after the data."),
        ("2026-07-27 06:30", "XAU/USD: gold eases in quiet Asian trade",
         "Gold slips a little in a calm session."),
    ],
    "investinglive": [
        ("2026-07-09 13:05", "AUD/NZD edges up in thin trade", "A quiet session for the cross."),
        ("2026-07-23 06:50", "EUR/USD slips before the ECB decision",                          # designed
         "The euro eases in early European trade ahead of the ECB rate decision later today."),
        ("2026-07-28 14:10", "USD/JPY climbs as yields rise", "The pair moves up with US yields."),
    ],
    "investing-commodities": [
        ("2026-07-06 10:12", "Kiwi fruit exports reach a record", "Growers ship more fruit than last year."),
        ("2026-07-14 15:40", "Copper firms as Chilean output dips", "Mine output falls for a second month."),
        ("2026-07-24 11:05", "Silver gains with platinum on supply worries", "Precious metals move up."),
        ("2026-07-30 16:20", "Natural gas falls after a mild weather outlook", "Forecasts point to cooler days."),
    ],
    "investing-forex": [
        ("2026-07-08 09:30", "Dutch inflation rises", "Prices in the Netherlands rise faster in June."),
        ("2026-07-20 23:15", "Aussie slips as iron ore prices ease", "The Australian currency drifts lower."),
        ("2026-07-27 03:40", "Yen slides as BoJ holds", "The Japanese currency weakens after the decision."),
    ],
    "oilprice": [
        ("2026-07-10 14:30", "Refinery outage lifts diesel margins", "A plant shutdown tightens fuel supply."),
        ("2026-07-22 15:05", "Oil holds steady as US stocks build",                            # designed
         "Crude prices change little after a build in US crude stocks."),
        ("2026-07-29 13:45", "OPEC+ keeps output plan unchanged", "The group sticks to its plan for September."),
    ],
    "cnbc-energy": [
        ("2026-07-08 19:10", "Utilities sign new LNG supply deals", "Buyers lock in cargoes for the winter."),
        ("2026-07-22 20:30", "Gas prices at the pump ease before the summer peak", "Drivers pay a little less."),
        ("2026-07-30 12:00", "US-Iran talks weigh on crude", "Traders watch the talks for supply news."),
    ],
    "fed-monetary": [
        ("2026-06-30 20:00", "Federal Reserve Board announces approval of discount rate actions",
         "Discount rate actions of the Reserve Banks"),
        ("2026-07-08 18:00", "Minutes of the Federal Open Market Committee, June 16-17, 2026",
         "Minutes of the Federal Open Market Committee"),
        ("2026-07-29 18:00", "Federal Reserve issues FOMC statement", "Federal Reserve issues FOMC statement"),
    ],
    "ecb-press": [
        ("2026-07-09 10:00", "ECB publishes its Economic Bulletin", ""),
        ("2026-07-23 12:15", "Monetary policy decisions", ""),                                  # designed
        ("2026-07-27 08:00", "Christine Lagarde: Interview with a newspaper", ""),
    ],
    "boe-news": [
        ("2026-07-02 11:00", "Financial Stability Report - July 2026", "The Financial Policy Committee report."),
        ("2026-07-15 09:30", "Speech by a member of the Monetary Policy Committee", "A speech on the outlook."),
        ("2026-07-22 13:00", "Bank Rate maintained - July 2026 summary", "The Committee keeps Bank Rate."),
    ],
    "boj-news": [],
}

CHANNELS = {   # feed: (channel title, the zone written in pubDate)
    "fxstreet": ("FXStreet Forex &amp; Commodities News", "GMT"), "investinglive": ("Investinglive RSS Breaking News Feed", "GMT"),
    "investing-commodities": ("Commodities &amp; Futures News", "plain"), "investing-forex": ("Forex News", "plain"),
    "oilprice": ("Oilprice.com", "-0400"), "cnbc-energy": ("Energy", "GMT"), "fed-monetary": ("FRB: Press Release - Monetary Policy", "GMT"),
    "ecb-press": ("ECB - European Central Bank", "+0200"), "boe-news": ("News", "Z"), "boj-news": ("Bank of Japan RSS", "+0900"),
}


def slug(title):
    """The link slug of a title: its first 6 words, lower case, joined by "-"."""
    words = title.split()[:6]
    return re.sub(r"[^a-z0-9]+", "-", " ".join(words).lower()).strip("-")


def pub_date(utc_text, zone):
    """A pubDate in the feed's own form: RFC 822 with GMT, Z or an offset, or the
    Investing.com plain UTC form "2026-07-23 06:50:00"."""
    t = datetime.datetime.strptime(utc_text, "%Y-%m-%d %H:%M")
    if zone == "plain":
        return t.strftime("%Y-%m-%d %H:%M:%S")
    if zone in ("GMT", "Z"):
        local, tail = t, zone
    else:
        sign = 1 if zone[0] == "+" else -1
        local, tail = t + sign * datetime.timedelta(hours=int(zone[1:3]), minutes=int(zone[3:])), zone
    return "%s, %02d %s %d %02d:%02d:00 %s" % (DAY[local.weekday()], local.day, MON[local.month - 1],
                                               local.year, local.hour, local.minute, tail)


def _x(text):
    """Text with &, < and > escaped for XML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _item(name, row, n):
    """One <item> in the feed's own layout."""
    when, title, desc = row
    link = "https://example.com/%s/%s" % (name, slug(title))
    date = pub_date(when, CHANNELS[name][1])
    t, d = _x(title), _x(desc)
    if name == "fxstreet":
        return ("        <item>\n            <title><![CDATA[%s]]></title>\n            <link>%s</link>\n"
                "            <guid isPermaLink=\"false\">fxs-%04d</guid>\n            <pubDate>%s</pubDate>\n"
                "            <description><![CDATA[%s]]></description>\n        </item>\n" % (title, link, n, date, desc))
    if name.startswith("investing-"):
        return ("    <item>\n      <title>%s</title>\n      <pubDate>%s</pubDate>\n      <author>Investing.com</author>\n"
                "      <link>%s</link>\n    </item>\n" % (t, date, link))
    if name == "fed-monetary":
        return ("        <item>\n            <title>%s</title>\n            <link><![CDATA[%s]]></link>\n"
                "            <guid><![CDATA[%s]]></guid>\n            <description><![CDATA[%s]]></description>\n"
                "            <category>Monetary Policy</category>\n            <pubDate><![CDATA[%s]]></pubDate>\n"
                "        </item>\n" % (t, link, link, desc, date))
    if name == "ecb-press":
        return "<item><title> %s</title><link>%s</link><guid>%s</guid><pubDate>%s</pubDate></item>" % (t, link, link, date)
    if name == "boe-news":
        return ("<item><guid isPermaLink=\"false\">{BOE-%04d}</guid><link>%s</link><title>%s</title>"
                "<description>%s</description><pubDate>%s</pubDate></item>" % (n, link, t, d, date))
    if name == "cnbc-energy":
        return ("    <item>      <link>%s</link>      <guid isPermaLink=\"false\">%d</guid>      <title>%s</title>"
                "      <description><![CDATA[%s]]></description>      <pubDate>%s</pubDate>    </item>" % (link, 1000 + n, t, desc, date))
    return ("    <item>\n      <title>%s</title>\n      <link>%s</link>\n      <guid isPermaLink=\"false\">%s-%d</guid>\n"
            "      <description>%s</description>\n      <pubDate>%s</pubDate>\n    </item>\n" % (t, link, name, n, d, date))


HEAD = '<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0">\n  <channel>\n    <title>%s</title>\n    <link>https://example.com/%s/</link>\n'
TAIL = "  </channel>\n</rss>\n"
HEADS = {
    "ecb-press": ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>\n'
                  '\t\t\t\t<title>%s</title><link>https://example.com/%s/</link><description>Latest releases (made up).</description><language>en</language>\n'),
    "boe-news": '<rss xmlns:a10="http://www.w3.org/2005/Atom" version="2.0"><channel><title>%s</title><link>https://example.com/%s/</link><description>News</description><language>en</language>',
    "cnbc-energy": '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">  <channel>    <title>%s</title>    <link>https://example.com/%s/</link>',
}
TAILS = {"ecb-press": "</channel></rss>\n", "boe-news": "</channel></rss>", "cnbc-energy": "  </channel></rss>"}


def feed_text(name):
    """The whole XML text of one saved feed (items newest first)."""
    rows = sorted(ITEMS[name], reverse=True)
    head = HEADS.get(name, HEAD) % (CHANNELS[name][0], name)
    body = "".join(_item(name, r, i + 1) for i, r in enumerate(rows))
    return head + body + TAILS.get(name, TAIL)


def write(out):
    """Write data/news/<name>.xml for the 10 feeds. Returns the paths."""
    written = []
    for name in sorted(ITEMS):
        path = os.path.join(out, "data", "news", name + ".xml")
        write_text(path, feed_text(name))
        written.append(path)
    return written
