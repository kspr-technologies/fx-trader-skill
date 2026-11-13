---
name: fx-risk
description: >-
  Risk Manager for currency pairs, gold, silver, oil, gas and copper at a
  MetaTrader 4/5 broker. Checks every order against your limits before
  fx-broker shows it, and stops anything over them. For a buy or a sell: a stop
  is needed, the loss at the stop (spread, commission and swap included) must
  fit your max loss per trade, reward:risk must meet your minimum, today's
  losses must fit your daily loss limit, and the trades per day, open trades,
  margin, margin level, correlated groups and currency exposure must fit. It
  also blocks new trades when the market is closed, near big news, after the
  Friday cut-off, late in a day trade, on a stale price or after a futures roll.
  Getting out is never blocked. Prints PASS or BLOCK with the reasons. Use when
  the user says "check my order", "risk check", "is this within my rules",
  "can I take this trade", "how much have I risked today", "am I over my
  limit", or before /fx-broker, always.
---

# fx-risk

The Risk Manager is the gate between a proposal and the ticket. Every order
from fx-budget, or a close order from fx-broker, goes through it.

A BLOCK is final for that order. fx-broker shows no ticket for it.

The limits keep each loss and each day's losses small. They do not make a
trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                                    # check fx/orders.json, write fx/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json                             # the verdicts as one JSON object
python3 "${CLAUDE_SKILL_DIR}/check.py" --quiet                            # verdict lines only
python3 "${CLAUDE_SKILL_DIR}/check.py" --offline                          # cache only, no Yahoo, no calendar download
python3 "${CLAUDE_SKILL_DIR}/check.py" --config fx2/config.json           # another account folder (fx2/)
python3 "${CLAUDE_SKILL_DIR}/check.py" --offline --now "2026-07-17 09:08" # the sample market (tests)
```

Exit codes: 0 every order PASS, 1 any order BLOCK, 2 an error (one line on
stderr, nothing written).

## What it reads and writes

| file | what |
| --- | --- |
| `fx/orders.json` | the orders to check (fx-budget, or fx-broker `close`) |
| `fx/config.json` | your limits and the instruments' MetaTrader values |
| `fx/portfolio.json` | your balance and open trades (fx-broker keeps it) |
| `fx/state/broker.json` | what you said yes to, placed and closed: today's trades, daily loss, duplicates |
| `fx/prices.json` | optional: a newer Yahoo quote (fx-alerts `quote`) |
| `fx/data/` | Yahoo prices, FX rates, futures rolls, this week's economic calendar |
| `fx/state/risk.json` | the verdicts it writes, with each order's fingerprint and the check time |

It may read Yahoo prices for the orders, your open trades and the FX
rates (a few small requests, 2 seconds apart), and this week's economic calendar (at
most once an hour). --offline and --now read only the saved files.

- Yahoo prices only when `"yahoo": true` in the config. The calendar only when
  `calendar_url` is set.
- A batch of only close orders makes no request at all.
- `--now` pins the clock (Karachi time, or `...Z` for UTC). It also means offline.
  The check time in `fx/state/risk.json` is that time.

## Opens and closes

- An open (`"intent": "open"`) must pass every rule R1 to R31 below.
- A close (`"intent": "close"`) is checked only for sense: C1 the order is well
  formed, C2 the position exists, C3 the key, side and lots match the position.
  A close is never blocked by a limit, the clock, the news or the market hours.
  Getting out must always be possible.
- Orders are checked in file order. Each open that passed counts as filled for the
  ones after it, so a batch that breaks a limit together is caught.
- One bad field in one order blocks only that order (R1 or C1). The others are
  still checked.

## The checks

| id | an open is blocked when |
| --- | --- |
| R1 | a field of the order is broken (side, style, type, lots, a price, expiry) |
| R2 | the key is not in your instrument table |
| R3 | the key is on your blocked list (`risk.blocked`) |
| R4 | it is a sell and `allow_short` is false |
| R5 | it has no stop (while `require_stop` is on) or no take profit |
| R6 | the stop or the target is on the wrong side, or the price is already at the stop |
| R7 | a stop or limit order is on the wrong side of the price now |
| R8 | MetaTrader's stops level says the stop or target is too close |
| R9 | the lots are under the min lot, over the max lot, or not a whole lot step |
| R10 | the max loss at the stop (costs in) is over your max loss per trade |
| R11 | reward:risk (from the order prices) is under your minimum |
| R12 | today's losses plus this max loss would pass your daily loss limit |
| R13 | you already have your trades for today |
| R14 | you already said yes to your orders for today |
| R15 | you already have your open trades |
| R16 | the same order was taken today, or the same key and side came earlier in this batch |
| R17 | a netting account holds the other side of this key |
| R18 | the risk in one group (metals, US dollar pairs, yen pairs ...) would pass `max_group_risk_pct` |
| R19 | the risk on one currency, one way (short USD, long EUR ...) would pass `max_currency_risk_pct` |
| R20 | the margin in use would pass `max_margin_used_pct` of your balance |
| R21 | the margin level would fall under `min_margin_level_pct` |
| R22 | the market is closed (weekend, holiday, early close, the CME or Brent break, the 5 PM rollover) |
| R23 | there is no fresh price (15 min for FX, 30 min for futures) |
| R24 | it is inside the quiet window of big news for this key |
| R25 | a swing open on Friday after `swing.friday_cutoff_ny` (New York time) |
| R26 | a day open after `day.no_entries_after`, or under 15 minutes before the flat-by time |
| R27 | a swing plan whose last day has passed |
| R28 | the orders were made on another FX day |
| R29 | the futures contract rolled on Yahoo and the order missed it |
| R30 | a USD rate the order needs is missing or too old |
| R31 | the order needs more margin than you have free |

How the numbers work:

- The measuring entry: a market buy uses the higher of the plan entry and the ask
  now; a market sell the lower of the plan entry and the bid now. A stop or limit
  order uses its own price.
- Max loss: the loss if the stop fills at its price, in USD, with the spread,
  commission, any swap-free fee and the swap you pay for the planned nights.
- Reward:risk is the gross number from the order prices. The number after all
  costs is a note (W-net), not a block.
- The daily loss: the full max loss of every open taken today, win or lose, plus the
  losses of older trades closed today, plus the opens that passed earlier in this
  batch. Wins never give room back.
- Group risk adds the max losses of every trade in one group, both sides. Currency
  risk adds the max losses of trades that bet the same way on one currency (a gold
  buy is short USD; oil is not counted as a dollar bet).
- Margin and equity count your open trades at the latest Yahoo price. A trade with
  no fresh price counts its full max loss. Open profit never makes room.

## Warnings

Shown as `Note:` lines. They never block.

- W-live: the account is live (`"paper": false`).
- W-closed: a close while the market is shut; MetaTrader closes it only when it opens.
- W-nostop: no stop (only when `require_stop` is off); it counts as a full max loss.
- W-coarse: Yahoo's prices for this pair are rough for a stop this tight.
- W-net: after all costs (and the swap for the whole hold) reward:risk is under your minimum.
- W-news: big news while the trade is open (day: to the flat-by time; swing: the next 24 hours).
- W-weekend: a swing trade stays open over the weekend; Monday can open past the stop.
- W-roll: the futures contract rolls on Yahoo before the plan ends.
- W-leverage: the trade is more than 10:1 leverage on your balance.
- W-same: you already hold this key on this side.
- W-calendar: no calendar file for this week; only the built-in dates were used.
- W-scale: a futures-based key: the Yahoo price is not your broker's price.
- W-margin: equity at Yahoo prices is about right, not exact.

## Steps

1. If the price may have moved, refresh it first: `/fx-alerts quote XAUUSD`.
2. Run `python3 "${CLAUDE_SKILL_DIR}/check.py"`. Exit 0: all PASS. Exit 1: any BLOCK.
3. Tell the user order by order: the max loss, reward:risk, and how much of today's
   limits it uses.
4. For a BLOCK, give the reason and the fix: a new plan, a smaller `--risk-usd` in
   fx-budget, or wait for the market. Never change a limit yourself.
5. Any change to an order needs a new check: the PASS is tied to the order's
   fingerprint.
6. Run it again right before the yes: a PASS counts for 5 minutes.

## Example

The sample market's prices are made up (`tests/fixtures/fx-market/`). The sample
gold swing buy, `--offline --now "2026-07-17 09:08"`, shortened:

```
PASS #1 buy XAUUSD 0.02 lots: max loss $97.81 (limit $100.00), costs included.
  Market order. Measured from 2,433.49 (the ask now; the plan said 2,433.49).
  gold 2,433.40 (Yahoo price (gold futures), not your broker's price, 3 min old)
  Stop 2,400.00, 33.49 (3,349 points) away. Target 2,490.00. Reward:risk 1.69 (min 1.50).
  Today before this: 0 of 3 trades, $0.00 of $300.00 daily loss used.
  Margin after: $48.67 (0.5% of your balance, limit 30%). Free margin $10,000.00.
  Note: After costs this trade wins 0.84 times what it risks. Your minimum is
  1.5. The swap for the whole hold is counted.
  ...

1 passed, 0 blocked. Checked at Fri 9:08 AM PKT. Paper account.
A PASS is a rules check, not a yes. fx-broker still asks you.
Run this check again right before you say yes: a PASS counts for 5 minutes.
Next: python3 fx-broker/ticket.py show
```

The sample day sell one lot step too big (0.87 lots, at 07-23 12:43):

```
BLOCK #1 sell EURUSD 0.87 lots:
- Max loss $100.05 is over your limit of $100.00 a trade (1.0% of $10,000.00),
  costs included.
  Market order. Measured from 1.08452 (the bid now; the plan said 1.08452).
  EURUSD 1.08452 (Yahoo price, close to your broker's price, 3 min old)
  ...
A BLOCK is final for that order. Fix the cause (a new plan, a smaller risk,
wait for the market), then run fx-budget and this check again.
```

A close from fx-broker (`ticket.py close --position EURUSD-1`, at 07-23 15:21):

```
PASS #1 close EURUSD 0.86 lots: closes your SELL position EURUSD-1.
```

## Rules

- Every open must pass every rule. Every failed rule is listed, in table order.
- A close is never blocked by a limit. It is checked only for sense.
- A PASS belongs to one exact order (its fingerprint) and counts for 5 minutes.
- An old orders file (made on another FX day) is blocked: make a new plan.
- An order that missed a futures roll is blocked: run fx-exit and fx-budget again.
- The check always writes `fx/state/risk.json`, also when every order is blocked.
- Exit 0 only when every order passed.

## What it never does

- It never places an order and never edits `fx/orders.json`.
- It never loosens a limit and never treats a PASS as the user's yes.
- It never blocks a close for a limit, the clock, the news or the market hours.
- It never says a trade is good. A PASS is a rules check, not a yes.

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
