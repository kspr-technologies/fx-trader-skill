---
name: fx-news
description: >-
  News Monitor for currency pairs, gold, silver, oil, gas and copper. Reads forex
  and commodity headlines from 10 free RSS feeds (FXStreet, investingLive,
  Investing.com, OilPrice, CNBC Energy, the Fed, the ECB, the Bank of England and
  the Bank of Japan) and tags each one to your instruments: EUR/USD or EURUSD,
  gold, crude, the dollar, the ECB. Shows only what is new since the last check,
  newest first, in Karachi and New York time, with the source and the link. Use
  when the user says "any news on gold", "what's the news on EURUSD", "why is the
  dollar moving", "news on my watchlist", "morning briefing", "did the Fed say
  anything", "oil news", or after /fx-scanner finds setups.
argument-hint: "[symbols]"
---

# fx-news

A chart can look fine just before big news.
A rate decision or a jobs report can move a price in one minute.
So before a setup becomes a trade, this skill reads the news for it.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                                   # watchlist + plan + open trades
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols XAUUSD,EURUSD --hours 72
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols XAUUSD --dry-run --offline --now "2026-07-17 09:01"
python3 "${CLAUDE_SKILL_DIR}/news.py" --feeds fed-monetary,ecb-press --all
python3 "${CLAUDE_SKILL_DIR}/news.py" --json --quiet
```

Flags: `--symbols A,B` (only these), `--all` (also items seen before),
`--dry-run` (mark nothing as seen), `--hours H` (1 to 720, default 48),
`--feeds NAMES` (only these feeds of your config), `--max N` (1 to 500,
default 40), `--watchlist FILE`, and the common `--config`, `--data`,
`--offline`, `--now`, `--json`, `--quiet`.

## What it reads and writes

| file or feed | reads or writes | what |
| --- | --- | --- |
| `--symbols`, else `fx/watchlist.json` | reads | the scanner's hits (fx-scanner) |
| `fx/plan.json` | reads | the trade you are planning (fx-exit) |
| `fx/portfolio.json` | reads | your open trades (fx-broker) |
| `fx/config.json` | reads | `news.feeds`, `universe`, the instrument names |
| the RSS feeds in `news.feeds` | reads | the headlines (only the RSS files) |
| `fx/data/news/<name>.xml` | reads and writes | the saved feeds, for `--offline` and `--now` |
| `fx/state/news.json` | reads and writes | what was shown (kept 30 days) and when each feed was read |

- The feeds are read only when you run the tool.
- Each feed at most once each 15 minutes. A newer run reads the saved copy.
- Two requests are at least 1.5 seconds apart.
- `--offline` and `--now` read only the saved feeds. No request at all.
- `"yahoo": false` does not stop the feeds. News is not a price.
- With no `--symbols`, no watchlist, no plan and no open trades, it shows news
  for your whole universe.

## The rules

- **Headlines are leads, not facts.** Read the story before saying something
  happened. Quote the source and link it. Never sum up a story you could not read.
- **Open only the article links the user asks about.** Never fetch pages in a
  loop. Never open an OilPrice page: its terms ban robots.
- **Never copy a whole story.** Give the title, the source, the time and the link.
- **Central bank pages beat headlines.** The Fed, ECB, BoE or BoJ item is the source.
- **How a headline finds an instrument:**
  - a pair code, with or without a slash: `XAU/USD`, `XAUUSD`, `EUR/USD`;
  - a commodity word: gold, silver, crude, Brent, OPEC, natural gas, LNG, copper;
  - a currency word, a central bank or a person: dollar, euro, yen, Fed, ECB,
    BoE, Powell, Lagarde. Fed and dollar news also touches gold and silver;
  - an item of the Fed, ECB, BoE or BoJ feed touches that bank's currency.
- **`!` marks news that can move prices at once:** rate decisions, key data
  (jobs, CPI, GDP), intervention, an output cut. It does not say which way.
- **This tool shows no prices.** For a price and its age, use `/fx-alerts quote`.
- **No predictions.** Report what was published. Never say what a price will do.

## Steps

1. Run it: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 0 is a report,
   also for `Nothing new.` Exit code 2 is an error, with one line that says why.
2. Read every `!` item first.
3. Open the links that matter (only those), and read the story.
4. Write a short brief per symbol: what happened, the source and the link,
   and one line on why it matters to the setup. For example: "the ECB decides
   at 5:15 PM PKT, inside the day trade".
5. For big scheduled events (rate decisions, jobs, CPI), run `/fx-calendar next`.
6. If a story changes the picture, say so before `/fx-entry` and `/fx-exit`.
   Never act on a headline alone.

During the session: `/loop 15m /fx-news`. On a Monday, `--hours 72` takes in Friday.

## Example

The sample market (made up). Friday 2026-07-17, 9:01 AM in Karachi, before the
gold swing trade:
`news.py --symbols XAUUSD --dry-run --offline --now "2026-07-17 09:01"`

```
1 new headline.
For: XAUUSD (from --symbols).
From Wed 15 Jul 9:01 AM PKT to now (the last 48 hours).

Fri 17 Jul 2:40 AM PKT (5:40 PM New York)  FXStreet
    Gold climbs to a three-week high as the dollar slips
    About: XAUUSD (gold).
    https://example.com/fxstreet/gold-climbs-to-a-three-week-high

Offline: the saved feeds in fx/data/news/ are used (10 of 10 found).
Dry run: nothing was marked as seen.
Checked Fri 17 Jul 9:01 AM PKT. Headlines are leads, not facts: open the link
and read the story. Not financial advice.
```

- "gold" finds XAUUSD by name, so the word "dollar" adds nothing more for it.
- 21:40 UTC is 2:40 AM in Karachi on Friday and 5:40 PM in New York on Thursday.
- `--dry-run` marks nothing as seen, so the next run shows it again.

## What it never does

- Never fetches an article page. It reads only the RSS files.
- Never republishes text. It prints and saves only the title, the source, the
  time and the link. The summary is read for tags only.
- Never shows a price. For a price, use `/fx-alerts quote`.
- Never says what a price will do, and never says to buy or sell on a headline.
- Never places an order, and never writes a file of the order path.

Not financial advice. Nothing in this pack places an order.
