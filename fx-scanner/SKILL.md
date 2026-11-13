---
name: fx-scanner
description: >-
  Chart Scanner for currency pairs and commodities (gold, silver, oil, gas,
  copper) at a MetaTrader broker. Scans Yahoo prices for setups on both sides:
  swing setups on daily bars (20-day and 55-day highs and lows, pullbacks,
  flags, RSI turns, inside days) and day setups on the London opening range
  with 5-minute bars. Says how far each chart is from its level in ATRs, how
  old the price is, and notes futures rolls, big news and holidays. Writes
  fx/watchlist.json for fx-news, fx-calendar, fx-cot and fx-entry. It never
  places an order. Use when the user says "scan the market", "scan forex",
  "find me setups", "what is breaking out", "any gold setups", "scan for
  sells", "opening range breakouts", "build my watchlist", "update the
  prices", or at the start of a trading day before /fx-news.
argument-hint: "[swing | day | refresh] [--symbols A,B] [--side buy|sell|both]"
---

# fx-scanner

The Chart Scanner reads the charts and lists the ones that match a rule.
That is all it does.

- A scan is a list of charts that match a rule, not a list of trades to take.
- A setup on a chart is not a prediction.
- Most retail traders lose money. The scanner does not change that.
- It only saves you from flipping through 20 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py"                                   # swing scan of universe
python3 "${CLAUDE_SKILL_DIR}/scan.py" swing --symbols XAUUSD,EURUSD --side buy
python3 "${CLAUDE_SKILL_DIR}/scan.py" day                               # after the London range
python3 "${CLAUDE_SKILL_DIR}/scan.py" day --now "2026-07-23 12:41" --offline --no-write
python3 "${CLAUDE_SKILL_DIR}/scan.py" refresh                           # fill the cache, scan nothing
python3 "${CLAUDE_SKILL_DIR}/scan.py" swing --json --quiet
```

Flags:

- `swing` is the default. `day` scans the opening range. `refresh` only updates
  the saved prices, the futures rolls and the calendar.
- `--symbols A,B`: only these instrument keys. Default: `universe` (swing),
  `day_universe` (day), both lists and the 7 rate pairs (refresh).
- `--side buy|sell|both`: default both, or buy when `"allow_short": false`.
- `--no-write`: print only. `--show N`: hits to print (1 to 200, default 30).
- The common flags: `--config FILE`, `--data DIR`, `--offline`, `--now "YYYY-MM-DD HH:MM"`
  (Karachi time, or `...Z` for UTC; it also means offline), `--json`, `--quiet`.
- Exit code 0 for any scan (0 hits too). 2 for an error, or when no instrument
  had any prices. The reason is one plain line.

## What it reads and writes

| file | what |
| --- | --- |
| `fx/config.json` | read: `universe`, `day_universe`, `allow_short`, `day_end`, `day` (range session and minutes, the entry cut-off), `risk.blocked`, `news.impacts`, `yahoo`, `calendar_url`, `holidays`, `early_closes`, `instruments` |
| `fx/data/yahoo/` | read and refreshed: 1-hour bars (swing), 5-minute bars (day), daily bars only for the futures roll check. Yahoo, at least 2 seconds between requests |
| `fx/data/rolls.json` | read; a new futures roll is added |
| `fx/data/calendar/` | read; this week's calendar, at most one download an hour |
| `fx/watchlist.json` | written by `swing` and `day` (not with `--no-write`): `mode`, `as_of`, `fx_day`, `session`, `universe`, `scanned`, `failed`, `hits[]`. Each hit has `symbol`, `side`, `setup`, `level`, `last`, `atr`, `distance_atr`, `notes`. fx-entry reads `symbol`, `side`, `setup`, `level`, `fx_day`, `opening_range` |

## Swing setups

Daily bars are built from Yahoo 1-hour bars. A day ends at 5:00 PM New York.
Levels are in ATRs, not percents: one rule fits EURUSD and gas alike.

| setup | side | the level the entry waits for |
| --- | --- | --- |
| 20-day high breakout | buy | the highest high of the 20 days before |
| 20-day low breakdown | sell | the lowest low of the 20 days before |
| 55-day high breakout | buy | the highest high of the 55 days before |
| 55-day low breakdown | sell | the lowest low of the 55 days before |
| pullback to a rising 20-day EMA | buy | the high of the day |
| rally to a falling 20-day EMA | sell | the low of the day |
| bull flag (ATR rule) | buy | the top of the flag |
| bear flag (ATR rule) | sell | the bottom of the flag |
| back over the 20-day average | buy | the 20-day average |
| back under the 20-day average | sell | the 20-day average |
| oversold bounce (RSI 14 was under 30) | buy | the high of the day |
| overbought drop (RSI 14 was over 70) | sell | the low of the day |
| inside day and NR7, upper side | buy | the high of the inside day |
| inside day and NR7, lower side | sell | the low of the inside day |

- A breakout is listed when the close is within 0.5 ATR of its level, or past it.
- A close more than 1 ATR past the level gets a note: a late entry is a
  different trade.
- The RSI turns trade against the trend. The hit says so.

## Day setups

- The opening range is the first 15 minutes of London: 8:00 AM London. That is
  1:00 PM PKT in winter and 12:00 PM PKT in summer. Three 5-minute bars.
- A range wider than 0.6 daily ATR is skipped. So is a range smaller than 8
  spreads.
- A hit is a close beyond the range (a close above the high for the buy side,
  below the low for the sell side), or a last close within 1 five-minute ATR of
  the level.
- No new day trades after 9:00 PM PKT (12:00 PM New York), or 30 minutes before
  an early close. Before the range is done, the scan says when to come back.

## Prices and their age

- Yahoo prices are the bid. FX prices are near live. Futures are about 10
  minutes late. Nothing here is real time.
- Gold, silver, oil, gas and copper come from Yahoo futures. That is not your
  broker's price. Every price line says whose price it is and how old it is.
- A futures roll makes Yahoo prices jump. The tool finds the roll and adjusts
  older bars to match. A hit near a roll gets a note.
- Yahoo EURUSD data is coarse (steps of about 1.25 pips). GBPUSD, AUDUSD and
  NZDUSD show about one price a minute.
- Yahoo FX starts on Monday at 00:00 London. A Yahoo outage leaves a hole. The
  scan names the hole, and a day with a hole gives no setups.

## Steps

1. Run `scan.py swing` before the day, or `scan.py day` after the range ends.
2. Tell the user the counts (scanned, failed) and the top hits with their notes.
3. The user picks one chart. Hand it to `/fx-news`, `/fx-calendar` and `/fx-cot`,
   then to `/fx-entry SYMBOL`.
4. Never pick a chart for the user.
5. A hit is stale once the price moves on. Scan again before acting.

## Example

On the made-up sample market (`--now "2026-07-17 09:00" --offline`), part of the
swing scan:

```
10 hits. Swing scan, Fri 2026-07-17 9:00 AM PKT.
Daily bars to Thu 2026-07-16. A day ends at 5:00 PM New York (2:00 AM PKT).
20 instruments. 20 scanned. 0 failed.
  XAUUSD  buy side  20-day high breakout: level 2,418.40, last 2,431.60,
    0.47 ATR past it.
    Yahoo price (gold futures), not your broker's price. Newest 2,433.40,
    live.
A scan is a list of charts that match a rule, not a list of trades to take.
```

And the day scan (`--now "2026-07-23 12:41" --offline`), also made up:

```
1 hit. Day scan, Thu 2026-07-23 12:41 PM PKT (3:41 AM New York).
London opening range: Thu 12:00 PM PKT (3:00 AM New York) to 12:15 PM PKT.
  EURUSD  sell side  opening-range breakdown (a close below the range low):
    level 1.08480, last 1.08452, 0.42 ATR past it.
    Range 1.08480 to 1.08640 (3 of 3 bars). 5-minute ATR 0.00067.
    Note: Closed below the range low at 12:40 PM PKT.
Next: /fx-news and /fx-calendar, then python3 fx-entry/entry.py check EURUSD
```

## Rules

- Say what was scanned, how many failed, and the time of the data.
- Say a chart "matched a rule". Never "a buy", "a sell signal" or "a good trade".
- Never show a profit or a loss figure.
- Never call Yahoo prices real time.
- Name both sides plainly: buy side and sell side.

## What it never does

- It never places, sizes or checks an order. It never says which instrument to trade.
- It never passes a Yahoo futures price off as your broker's price.
- It never uses Yahoo daily bars as prices (only for the roll check).
- It never edits the saved Yahoo rows.
- It never downloads prices with `--offline`, with `--now`, or with `"yahoo": false`.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order. fx-broker shows the order and waits. It prints the line to
  type into MetaTrader only after fx-risk passed that exact order in the last 5 minutes and the user
  typed an explicit yes for that one order in the chat. The user then places it in their own
  MetaTrader. If the user ever adds a broker tool that can place orders, set it to "Needs approval"
  (Customize > Connectors > tool permissions), never "Always allow".
- Both sides. A buy opens at the ask, a sell opens at the bid. A sell's stop is above the price.
  Sells are on by default; `"allow_short": false` turns them off.
- Leverage makes losses bigger, as fast as gains. Every order carries a stop loss (SL) and a take
  profit (TP).
- A stop is not a promise. Over a weekend, at big news or at a futures roll the price can jump past
  the stop, and the fill is worse than the stop.
- Most retail CFD accounts lose money: 74% to 89% of them in EU regulators' studies (ESMA, 2018).
- MetaTrader logins, passwords and PINs never go in the chat.
