---
name: fx-alerts
description: >-
  Price and clock alerts for forex, gold, silver, oil and gas. Rings once when a price, your stop, your target, your flat-by time, the end of a swing hold, the Friday weekend check, a session open, the 5 PM New York rollover or big news comes. Turns the plan into alerts in one go. A sell's stop rings on the ask, as in MetaTrader. Knows FX hours, the CME break, holidays and Karachi and New York time, says how old each Yahoo price is, and moves its levels at a futures roll. Use when the user says "alert me when", "tell me if gold hits 2450", "watch my stop", "watch my trade", "remind me to close by 11:30", "is the market open", "when does London open", "get me a price", or right after /fx-broker records a fill.
argument-hint: "[symbol] [price]"
---

# fx-alerts

fx-alerts watches the prices and the clock, so the user does not have to stare at a chart.
An alert is information, never an order. When a stop alert rings, this skill closes nothing.
MetaTrader's own SL and TP close the trade, if the user set them.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Run it from the folder that holds `fx/`.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                 # stop, target and times from fx/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add USDJPY above 158.50
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add XAUUSD stop 2400 --side buy  # a stop or target needs the side
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --session london            # 5 min before London opens
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --rollover                  # 30 min before 5 PM New York
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --news XAUUSD               # before big news for gold
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --flat-by 23:00 EURUSD      # a flat-by reminder today
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                           # exit 0 rang, 1 nothing rang, 2 an error
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote XAUUSD EURUSD             # -> fx/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote XAUUSD --broker-price 2429.15
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote XAUUSD --fast             # quick look, rounded, not saved
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                           # market hours, sessions, big events
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list                            # every rule; --off a3, --on a3, --reset a3
```

- A bare `alerts.py` runs `check`.
- `--now "2026-07-23 15:20"` pins the clock (Karachi time; `...Z` is UTC). It is a replay: no network.
- `--offline` never touches the network. `--data DIR` and `--config FILE` change the folders.
- `--json` prints one JSON object. `--quiet` prints only the ALERT lines (or `Nothing hit.`).
- `add --minutes-before N` (0 to 1440) sets how early a time rule rings. `add --note TEXT` keeps a note.
- `check --symbols XAUUSD,EURUSD` checks only those (clock rules always).

## What it reads and writes

| file | what |
| --- | --- |
| `fx/plan.json` | the trade from fx-exit, for `add --from-plan` |
| `fx/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `fx/state/alerts.json` | which rules rang and when, so each rings once |
| `fx/portfolio.json` | the open trades: their stop, target and flat-by; moved at a futures roll |
| `fx/prices.json` | the quotes `quote` saves for fx-budget and fx-risk, and your typed broker price |
| `fx/state/broker.json` | one "rebase" line when a futures roll moves a trade |
| `fx/config.json` | hours, holidays, `day.flat_by`, `swing.friday_cutoff_ny`, news settings |
| `fx/data/` | Yahoo prices (fetched, paced, cached), rolls, the economic calendar |

- `alerts.example.json` has clock rules only (London and New York opens, the rollover, gold news).
  Copy it to `fx/alerts.json` to start, or let `add` make the file.
- A broken `fx/state/alerts.json` is renamed and started fresh. Any other broken file is an error.
- A rule of a trade that is closed is turned off. Its flat-by and exit-by never ring.

## Rule kinds

Each rule rings once. `list --reset ID` lets it ring again.

| kind | rings when |
| --- | --- |
| `above` | the Yahoo bid reaches the level |
| `below` | the Yahoo bid falls to the level |
| `stop` | a BUY: the bid falls to the stop. A SELL: the ask (bid plus spread) rises to the stop |
| `target` | a BUY: the bid reaches the target. A SELL: the ask falls to the target |
| `flat_by` | 15 min before your day trade's flat-by time (also when the check comes late) |
| `exit_by` | 60 min before the end of your swing hold |
| `friday_close` | from the Friday cutoff (12:00 New York) to the weekend close, once a week |
| `session_open` | 5 min before Sydney, Tokyo, London or New York opens (not later than 30 min after) |
| `rollover` | 30 min before 5 PM New York, once a day |
| `news` | before the next big event for that instrument, once a day |

- A missed Friday check prints `Missed: the Friday 12:00 PM New York check (Fri 07-17).`
  Then the rule moves to the next Friday, until the swing hold ends.
- Clock rules do not ring on a full FX holiday. The holiday line is shown instead.

## Prices

- All prices come from Yahoo Finance. They are not your broker's prices.
- Gold, silver, oil and gas come from futures. They are about 10 minutes late.
  The level can differ from MetaTrader by a few dollars.
- FX prices are near live and close to your broker's.
- Every price says how old it is. A price older than 15 min (FX) or 30 min (futures) while the
  market is open is "stale". Then no alert can ring on it: check MetaTrader.
- Yahoo prices are bids. A sell's stop and target ring on the ask: the bid plus the spread from your config.
- Yahoo moves to the next futures contract (a roll). The tool then moves its levels and your open
  trade's levels by the same amount, and says so once. It never rings on the roll jump. Your
  broker's price did not jump: keep the SL and TP you set in MetaTrader.
- Near an expected roll, a stop or target ring on a very big hourly move is held until the roll is
  found or the roll time is over. It then rings only if the price is still past the level.
- `quote` saves prices to `fx/prices.json` for fx-budget and fx-risk.
  `--broker-price P` also saves the BID you see in MetaTrader for one symbol.
- Yahoo is asked at most once a minute (5-minute bars) or once in 10 minutes (hourly bars),
  2 seconds apart. Run one tool that polls Yahoo at a time.

## The FX clock

All times are shown in Karachi time (PKT) first, with New York time in brackets.

- The FX day ends at 5 PM New York: 2:00 AM PKT in summer, 3:00 AM PKT in winter.
- The rollover is at 5 PM New York. Swap is booked then. Spreads get wide.
- Gold and oil (CME) stop for one hour each day: 5 PM to 6 PM New York.
- The week runs from Sunday 5 PM to Friday 5 PM New York.

| sessions (PKT) | Sydney | Tokyo | London | New York |
| --- | --- | --- | --- | --- |
| summer (April to October) | 2:00 AM to 11:00 AM | 5:00 AM to 2:00 PM | 12:00 PM to 9:00 PM | 5:00 PM to 2:00 AM |
| winter (November to March) | 1:00 AM to 10:00 AM | 5:00 AM to 2:00 PM | 1:00 PM to 10:00 PM | 6:00 PM to 3:00 AM |

- In a few weeks of March and October/November, the US is on summer time and the UK is not.
  Then New York is only 4 hours behind London, and `clock` says so.
- `clock` shows the market state per group, Yahoo's own data hours when they differ, the four
  sessions, key times, holidays for the next 7 FX days and the next big events.

## Steps

1. Right after fx-broker records a fill, run `add --from-plan`. Read each "Added" line back to the user.
2. Run `check` every few minutes, from `/loop 5m` or a scheduled task. It only reads and rings.
3. Tell the user what rang, one line each, with the price, its age and its source.
4. A stop or target ring: point to `python3 fx-broker/ticket.py close --position <id>`.
   If MetaTrader already closed it: `close --position <id> --already --fill P`. Never act for the user.
5. A flat-by ring: point to `python3 fx-broker/ticket.py flatten`.
6. Exit codes: 0 something rang (or the command is done), 1 nothing rang, 2 an error.

## Example

On the made-up sample market, a day SELL of EURUSD has its target at 1.08234:

```
$ python3 fx-alerts/alerts.py check --now "2026-07-23 15:20"
ALERT EURUSD target 1.08234: the ask fell to 1.08222 in the 3:15 PM PKT 5-minute bar.
  Your SELL's take profit fires on the ask (the Yahoo bid plus the 0.1 pip spread).
  EURUSD 1.08262 now (Yahoo price, close to your broker's price, live)
An alert is information, not an order. Nothing was closed. Your SL and TP in MetaTrader work on their own.
To record a close: python3 fx-broker/ticket.py close --position EURUSD-1, then fx-risk and your yes.
If MetaTrader already closed it: python3 fx-broker/ticket.py close --position EURUSD-1 --already --fill P
```

On Wed 07-29 Yahoo gold moves to the December contract (+0.90%):

```
$ python3 fx-alerts/alerts.py check --now "2026-07-29 13:15"
Nothing hit. 4 rules watched at Wed 1:15 PM PKT (4:15 AM New York).
  XAUUSD  2,504.80  Yahoo price (gold futures), not your broker's price, live
Gold rolled on Yahoo (+0.90%). Your plan levels moved up by the same amount. Your broker's XAUUSD did not jump: keep the SL and TP you set in MetaTrader.
Missed: the Friday 12:00 PM New York check (Fri 07-24).
Next check: python3 fx-alerts/alerts.py check
```

The gold stop moved from 2,400.00 to 2,421.59 and the target from 2,490.00 to 2,512.41.
On Thu 07-30 the target alert rings at 2,512.41.

## What it never does

- It never places, closes, changes or cancels an order.
- It never says where a price is going, and never gives advice.
- It never says a Yahoo price is the broker's price or up to the second.
- A stop alert sells nothing. MetaTrader's own SL does that, if the user set it.

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
