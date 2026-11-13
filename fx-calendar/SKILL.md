---
name: fx-calendar
description: >-
  Economic Calendar for currency pairs, gold, silver, oil, gas and copper. Shows
  this week's events by currency and impact (CPI, jobs, central bank decisions,
  EIA oil and gas reports), in Karachi time with New York time beside it. Lists the
  next big events for your watchlist, your plan and your open trades, and which of
  them fall inside each trade. Shows the no-new-trade windows around big news that
  fx-risk uses. Also shows market holidays and early closes. Use when the user says
  "what's on the calendar this week", "any big news today", "when is the Fed",
  "when is NFP", "when is the ECB", "is there news for gold", "can I open EURUSD
  now or is there news", "news blackout", "quiet window", "economic calendar", or
  before /fx-entry and /fx-exit.
argument-hint: "week | next | quiet SYMBOL"
---

# fx-calendar

Prices can jump at big news, like a rate decision or a jobs report.
A stop can then fill past its level.
This skill says when the big news is, so you know before you plan a trade.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/events.py" week  [--impact LIST] [--symbols A,B]
python3 "${CLAUDE_SKILL_DIR}/events.py" next  [--impact LIST] [--symbols A,B] [--count N]
python3 "${CLAUDE_SKILL_DIR}/events.py" quiet SYMBOL [--days N]
```

- `week`: this week's events, by time, currency and impact. Past events say "(past)".
  Market holidays and early closes of the week come under them.
- `next`: the next big events for your watchlist, plan and open trades. Then, for
  each trade, the events that fall inside it (to its flat-by or exit-by time).
- `quiet SYMBOL`: the no-new-trade windows around big events for one instrument,
  and whether now is inside one.
- `--impact` takes `High`, `Medium`, `Low` or `all` (commas between).
  `week` shows High and Medium; `next` uses `news.impacts` from your config.
- Every command takes `--config`, `--data`, `--offline`, `--now`, `--json` and `--quiet`.

## What it reads and writes

| file | reads or writes | what |
| --- | --- | --- |
| `fx/data/calendar/<week>.json` | reads and writes | this week's calendar from the site (at most one download an hour) |
| built-in tables | reads | central bank days, EIA reports, US jobs days, COT days, holidays |
| `fx/watchlist.json` | reads | the scanner's hits (fx-scanner) |
| `fx/plan.json` | reads | your plan and when it ends (fx-exit) |
| `fx/portfolio.json` | reads | your open trades and when they end (fx-broker) |
| `fx/config.json` | reads | `calendar_url`, `news.impacts`, the quiet minutes, `holidays`, `early_closes` |

- It downloads the calendar at most once an hour, also after a failed try.
- `--offline` and `--now` read only the saved week. They never download.
- `"calendar_url": ""` in your config turns the feed off. Then only the built-in dates show.
- `"yahoo": false` does not turn the calendar off. The calendar is not a price.

## The rules

- The feed has no results. Say the time, the forecast and the last value, never
  a result. For a result, read the news (`/fx-news`) or the source.
- Only this week comes from the site. Later weeks have only the built-in dates.
  Say so when a swing trade runs past this week.
- "(expected date)" is a guess from past years. "(built-in table)" is from the
  pack's own tables.
- EIA oil and gas reports count as High for oil and gas, though the site rates
  them Low.
- A currency's event touches every pair with that currency. USD events touch the
  metals and energy too.
- In a quiet window fx-risk blocks every new trade, day or swing. Closing is
  never blocked. The window is a setting, not a law.
- Never call a time good or bad for a trade. Say what the calendar holds.

## Steps

1. Run `week` to see the week.
2. Run `next` before you plan a trade.
3. Run `quiet SYMBOL` before an entry.
4. If an event falls inside the trade, say it before `/fx-exit` sets the stop.
   A stop does not protect against a jump: the fill can be worse than the stop.

## Example

`events.py quiet EURUSD --offline --now "2026-07-23 17:00"` on the made-up sample
market (the ECB day):

```
QUIET: EURUSD is in a no-new-trade window now, to 5:45 PM PKT (8:45 AM New
York): Main Refinancing Rate (EUR) at 5:15 PM PKT.
Offline: the saved calendar from Mon 20 Jul is used.
Windows for EURUSD in the next 7 days (High events, 30 min before to 30 min
after):
Thu 23 Jul  No new trades from 4:45 PM to 5:45 PM PKT (7:45 AM to 8:45 AM New
York):
    Main Refinancing Rate (EUR) at 5:15 PM PKT.
Thu 23 Jul  No new trades from 5:15 PM to 6:15 PM PKT (8:15 AM to 9:15 AM New
York):
    ECB Press Conference (EUR) at 5:45 PM PKT.
Wed 29 Jul  No new trades from 10:30 PM to Thu 12:00 AM PKT (1:30 PM to 3:00
PM New York):
    Fed rate decision (FOMC) at 11:00 PM PKT.
Note: fx-risk blocks every new trade (day and swing, buy and sell) in these
windows. Closing a trade is never blocked.
Note: the window comes from "news.quiet_before_min" and "news.quiet_after_min"
in fx/config.json. For scale: FTMO uses 2 minutes, HexFunded 5 minutes.
Note: only this week (to Sat 25 Jul) comes from the calendar site. After that
only built-in dates are known: central banks, EIA oil and gas, US jobs and
COT. Other news (like CPI) is missing.
The calendar has no results, only the time, the forecast and the last value.
```

## What it never does

- It never shows a result. The feed has none.
- It never guesses an event or a time. A guessed row says "(expected date)".
- It never calls a time good or bad for a trade.
- It never shows a price and never places an order.
- It never writes a file of the order path.
- It never downloads more than once an hour, never retries in a loop, and never
  with `--offline` or `--now`.

Not financial advice. Nothing in this pack places an order.
