---
name: fx-exit
description: >-
  Exit Planner for currency pairs, gold, silver, oil, gas and copper at a
  MetaTrader broker. Sets your stop loss and take profit before you buy or sell,
  so you know when to get out. Reads the trigger from fx-entry (or a price you
  give). Swing trades get a stop of 2 daily ATR and a 2R target, held up to 20
  days; day trades get the opening-range middle and 2R, flat by 11:30 PM Karachi
  time. Turns Yahoo chart prices into MetaTrader order prices (buy at the ask,
  sell at the bid), checks the stops level, the spread and rough data, and
  estimates the spread, commission and swap. Refuses a stop on the wrong side or
  too close, and says why. Writes fx/plan.json. Use when the user says "where's
  my stop", "set my stop and target", "stop loss", "take profit", "plan the
  exit", "when do I get out", "what's my R", "risk reward", or right after
  /fx-entry triggers, before /fx-timemachine.
argument-hint: "[--stop atr:K|or-mid|or-far|swing-low|swing-high|price:X] [--target 2R|price:X]"
---

# fx-exit

The Exit Planner decides how a trade ends before it starts.

- The stop is where the setup has failed.
- The target is what you came for.
- Both are set BEFORE the entry and written down. So nobody has to decide
  anything in the middle of a fast move.

A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
The plan uses Yahoo prices. Your broker's prices are a little different.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                                   # fx/entry.json, the config's stop and target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop price:2400 --target price:2490 --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5 --target 3R --json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-far --flat-by 22:00 --write    # a day trade
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol USDJPY --side sell --entry 157.80 --style swing --stop swing-high
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --entry 4120.0 --entry-type stop --write  # plan ahead of a waiting entry
```

Other flags:

- `--entry-type market|limit|stop`: the MetaTrader order type of the entry.
  Swing: the config's `swing.entry` (market). Day: market.
- `--hold-days N`: a swing trade holds at most N FX days (1 to 120).
- `--flat-by HH:MM`: a day trade is flat by this Karachi time.
- `--json`: one JSON object. `plan` holds the plan, written or not.
- `--now "2026-07-17 09:05"`: pretend it is this Karachi time (no network).
- `--offline`, `--config FILE`, `--data DIR`, `--quiet`, `--write`.

## What it reads and writes

| file | what |
| --- | --- |
| `fx/entry.json` | the trigger from fx-entry: symbol, side, style, setup, price, time, ATR, opening range |
| `fx/config.json` | `style`, `allow_short`, `swing.stop`, `swing.target`, `swing.entry`, `swing.max_hold_days`, `day.stop`, `day.target`, `day.flat_by`, `day.no_entries_after`, `risk.min_reward_risk` (a note here; fx-risk enforces it), `account.swap_free`, the instrument rows |
| `fx/data/yahoo/` | Yahoo hourly bars (swing) and 5-minute bars (day), and the FX rates for the money |
| `fx/data/rolls.json` | futures rolls: old plans move with a roll |
| `fx/plan.json` | with `--write`: entry, stop, target, R, costs, flat-by or exit-by. fx-timemachine, fx-budget, fx-alerts and fx-broker read it |

It uses the network only when `"yahoo": true` and you did not give
`--offline` or `--now`. Then it gets the newest bars of the instrument, checks
for a futures roll, and gets the FX rates the money needs (at most 5 requests,
2 seconds apart). If Yahoo does not answer, it says so and uses the saved
prices. It never writes `fx/entry.json`.

## Stops and targets

| stop | style | buy | sell |
| --- | --- | --- | --- |
| `atr:K` (K 0.5 to 10) | both (swing: daily ATR; day: 5-minute ATR) | entry - K x ATR | entry + K x ATR |
| `or-mid` | day (the day default) | the opening-range middle | the opening-range middle |
| `or-far` | day | the range low | the range high |
| `swing-low` | swing, buy | lowest low of 5 days - 0.25 ATR | (refused) |
| `swing-high` | swing, sell | (refused) | highest high of 5 days + 0.25 ATR |
| `flag-far` | swing, bull or bear flag | the flag low - 0.25 ATR | the flag high + 0.25 ATR |
| `bar-far` | swing, inside day | the inside day's low - 0.25 ATR | its high + 0.25 ATR |
| `setup` | both | the setup's own stop | the same |
| `price:X` | both | X (a chart level) | X |

- The swing default is `atr:2` (2 x the daily ATR). The day default is `or-mid`.
- Targets: `KR` (K 0.5 to 10, like `2R` or `1.5R`): K times the risk from the
  entry. Or `price:X`, a chart level. The default is `2R`.
- A flag beats the config. The config beats the setup. The setup's own stop is
  used only with `--stop setup`.
- **Rounding.** The stop and the target round AWAY from the entry to the tick.
  So the risk never shrinks and the target never comes closer.

## Both sides

- A buy enters at the ask: the Yahoo bid plus the spread.
- A sell enters at the bid.
- A sell's stop and target fire on the ask. So the plan adds the spread to a
  sell's chart stop and chart target.

## Swing and day

- **Swing.** Up to `swing.max_hold_days` FX days (20), or `--hold-days N`. The
  plan's FX day is day 1. `exit_by` is the end of the last day (5:00 PM New
  York). The money counts the swap for each night held (3 on the triple day).
- **Day.** Flat by `day.flat_by` (11:30 PM Karachi time), or `--flat-by`, win
  or lose. No overnight hold, so no swap. On a short day (an early close) the
  plan is flat 15 minutes before the close, and it says so. No new day plan
  after `day.no_entries_after` (9:00 PM), or 30 minutes before an early close.
- Times print in Karachi time first, with New York time in brackets.

## What it refuses

It refuses the plan (exit 1) and says why. It never loosens a rule:

- a stop or a target on the wrong side of the entry;
- a stop closer than 3 spreads, or closer than 0.5 daily ATR (swing) or
  1 x the 5-minute ATR (day);
- a stop, a target or a pending entry inside the MetaTrader stops level, or
  on the wrong side of the price now;
- a swing entry when a newer FX day has closed since the trigger;
- a day entry from another day;
- a day plan when the market is shut all day, past the entry cut-off, with
  under 15 minutes left before flat-by, or (a market entry) when the market
  is not open.

It stops with an error (exit 2) for:

- no `fx/entry.json` and no `--entry`, or an entry that has not triggered
  (give `--entry` to plan ahead);
- a sell when `allow_short` is false;
- a bad flag: an unknown symbol, a bad stop or target rule, a day rule on a
  swing plan (or the other way), `swing-low` on a sell, `flag-far` or
  `bar-far` for another setup, a bad `--flat-by` or `--hold-days`;
- no saved bars, no saved price, too few bars for an ATR, a missing or old
  FX rate.

It warns (the plan still passes) about: a futures roll ahead, futures prices,
rough Yahoo FX data, a very wide stop, reward:risk under your minimum (before
or after costs; fx-risk blocks the gross one), the weekend, an old price, a
closed market, and a late day entry.

## Steps

1. Run it after `/fx-entry` triggers. Before that, `--entry PRICE` plans
   ahead at the level you expect.
2. Use the config's stop and target unless the user names others.
3. Run `exit.py plan --write`. Read back every line: the entry, the stop and
   its rule, the target and its rule, reward:risk before and after costs, the
   hold or the flat-by time, the costs for 1 lot and the gap line. Read every
   note too.
4. If it refuses, say the reason. Never loosen a refused plan on your own.
   The user may pick another stop or skip the trade.
5. Say once that a stop is not a guarantee, and that Yahoo prices are not the
   broker's prices.
6. Hand off to `/fx-timemachine` to replay the plan, then `/fx-budget` to
   size it. Offer `/fx-alerts` for the stop, the target and the time limit.

## Example

This is the made-up sample market of the tests. The prices are not real.
Gold closed over its 20-day high on Thu 2026-07-16. On Fri 2026-07-17 at
9:05 AM PKT, `exit.py plan --stop price:2400 --target price:2490 --write`:

```
PLAN  XAUUSD BUY  swing  20-day high breakout
  Price now  2,433.40 bid at Fri 9:05 AM PKT (Yahoo price (gold futures), not your broker's price, live)
  Entry   2,433.49  buy at the market (ask = bid 2,433.40 + spread 0.09)
  Stop    2,400.00  your price (2,400.00): 33.49 (3,349 points) under the entry
  Target  2,490.00  your price (2,490.00): 56.51 (5,651 points) over the entry
  Reward:risk  1.69 before costs, 0.84 after costs (1 lot, held all 20 days)
  Hold    up to 20 FX days: out by Fri 14 Aug 2:00 AM PKT (5:00 PM New York) at the latest
  1 lot   $1.00 a point. Costs: commission $6.00, spread $9.00 (in the price),
          swap -$54.84 a night (you pay), -$1,535.52 for 28 nights.
  Gap     If the price jumps past your stop, you lose more. A jump of 2 ATR
          (56.11) past the stop costs $5,610.80 more a lot.
Set before the entry. A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
These are Yahoo levels. fx-broker shows your broker's levels when you give it your broker's price.
Note: Gold rolls about 2026-07-17. Yahoo prices jump then; your broker's price does not.
Note: Yahoo gold is a futures price, about 10 minutes late. Your broker's gold price is a little different.
Note: After costs this trade wins 0.84 times what it risks. Your minimum is 1.5.
Note: The market closes for the weekend. On Monday the price can open past your stop.
Wrote fx/plan.json.
Next: python3 fx-timemachine/timemachine.py run, then python3 fx-budget/size.py --write. Nothing was placed.
```

- The risk is 33.49 x 100 = $3,349.00 a lot. The max loss a lot is
  3,349.00 + 6.00 commission + 28 x 54.84 swap = $4,890.52.
- After costs: (5,651.00 - 6.00 - 1,535.52) / 4,890.52 = 0.84.
- 20 FX days from Fri 07-17 end on Thu 08-13 at 5:00 PM New York. They hold
  20 rollovers, 4 of them Wednesdays (3 nights each): 28 nights.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- A plan describes levels. It never says buy, sell, take or skip.
- Plain numbers only. It never prints a profit promise. A 2R target is not a
  prediction that the price gets there.
- Both sides: a sell is planned as plainly as a buy.

## What it never does

It never places an order, never sizes a position (fx-budget does that),
never widens a stop to make a plan pass, never moves a stop after the entry,
never calls a stop a guarantee, and never writes any file but `fx/plan.json`
(and the shared price caches).

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
