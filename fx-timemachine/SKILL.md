---
name: fx-timemachine
description: >-
  Time Machine for currency pairs, gold, silver, oil, gas and copper. Replays a
  planned buy or sell 10,000 times before you risk money, built from the market's
  own past Yahoo bars. Takes the plan from fx-exit (entry, stop loss, take
  profit). Swing plans replay up to 20 days of past daily moves, with swap each
  night and weekend gaps; day plans replay 5-minute bars from the same time of
  day, to the flat-by time. Counts the spread, the commission and the swap, and
  gaps past the stop. Prints the odds of target first, stop first or neither, the
  price at the end, the average R with costs, and one plain verdict, with the seed
  so you can repeat it. Use when the user says "run the time machine", "replay
  this trade", "what are the odds", "simulate this trade", "monte carlo",
  "backtest this plan", "how often does this hit the target", "is this a coin
  flip", or after /fx-exit writes a plan, before /fx-budget.
argument-hint: "[--seed N] [--runs N] [--raw] [--write]"
---

# fx-timemachine

The Time Machine sits between the plan and the money. fx-exit has set the
entry, the stop loss (SL) and the take profit (TP). Before anything is sized,
this skill asks one question.

If the coming days (swing) or the rest of today (day) play out like this
market's own past, how often does the plan hit the target first, the stop
first, or neither? It answers with 10,000 replays.

It is a simulation, not a prediction.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # fx/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save fx/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --seed 7 --runs 20000
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --raw --fan        # a swing plan with the drift kept
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --plan old/plan.json --json
```

Flags:

- `--plan FILE`: the plan to replay (default `fx/plan.json`, next to the config).
- `--runs N` (100 to 100,000, default 10,000), `--seed N` (default 19).
- `--raw`: swing only. Keep the drift (the past days' average move).
- `--fan`: also print the price fan (up to 12 rows: low 5 %, middle, high 5 %).
- `--write`: save `fx/timemachine.json`. Without it nothing is saved.
- `--config FILE`, `--data DIR`, `--json`, `--quiet`.
- `--now "2026-07-17 09:06"` (Karachi time, or `...Z` for UTC) pins the clock.
- `--offline` is accepted. It changes nothing: this tool never uses the network.

## What it reads and writes

| file | what |
| --- | --- |
| `fx/plan.json` | the plan from fx-exit: symbol, side, style, entry, stop, target, entry time, flat-by or exit-by, costs |
| `fx/config.json` | `day_end`, `account.swap_free`, `day.or_session`, holidays and early closes, the instrument row |
| `fx/data/yahoo/` | the saved Yahoo bars: hourly (swing) or 5-minute (day), and the FX rates. Never fetched here |
| `fx/data/rolls.json` | futures rolls: an old plan moves with a roll before the replay |
| `fx/timemachine.json` | with `--write`: the odds, R, the fan, the histogram and the verdict. fx-budget shows one line from it |

It never writes `fx/plan.json`. A roll moves the plan's levels in memory only.

## How it works

The plan's `style` picks the mode.

Swing (a plan held for days):

1. The pool: daily bars built from the saved Yahoo hourly bars, only days
   before the plan's FX day (no peeking). A pool day needs the day before it
   and an ATR. Each day becomes four moves from the close before: open, high,
   low and close.
2. Drift removed: the pool's average day is taken out, so up and down start
   even. `--raw` keeps it.
3. Scaled to today: each past day is sized to today's ATR (between 0.5 and 2
   times), so a calm past does not make today look calm.
4. Blocks: each replay strings together blocks of 5 past days in a row. Each
   block starts on the same weekday as its place in the path, so Mondays keep
   their weekend gaps.
5. The path runs from the plan's FX day to `exit_by` (at most `max_hold_days`
   FX days). Swap is counted for each night held.

Day (a plan closed the same day):

1. The pool: the saved 5-minute bars of past FX days (not holidays, not
   early-close days). Each bar's clock is counted from the session open
   (London by default), so "the London open" stays the same slot when clocks
   change.
2. Blocks of 3 bars, drawn from the same time of day, plus or minus 2 bars.
3. The path runs from the entry to the flat-by time. No swap.
4. At least 20 full past days are needed.

Both modes:

1. Prices are Yahoo bids. A buy's stop and target fire on the bid. A sell's
   fire on the ask (the bid plus the spread).
2. A bar that touches both the stop and the target counts as the stop.
3. Gaps: a day or bar that opens past the stop fills at the open, worse than
   the stop. These runs are counted ("gapped past the stop").
4. Rough FX data: for EURUSD, GBPUSD, AUDUSD and NZDUSD the stop counts as hit
   2 pips early (Yahoo has about one price a minute).
5. A stop or limit entry waits to fill. A replay that never fills counts as
   neither, at 0R.
6. R with costs: 1R is the max loss of one lot at the stop with all costs
   (commission, swap, any swap-free fee). A stop hit after the planned nights
   is exactly -1.00R. The target pays the net R (after costs).
7. The verdict: the break-even share is `1 / (1 + net R)`. It compares the
   share of target-first replays (of those that hit one side) with it:
   - costs eat the target (net R is 0 or less);
   - too few replays hit either side (under 10 %);
   - close to a coin flip (at 1R with no costs) or close to break-even (under 5
     points away);
   - leans toward the target or the stop (5 to 15 points);
   - strongly toward the target or the stop (15 points or more).
8. The seed fixes the draws. Same plan, same data, same seed: the same numbers.

## What it refuses

Each refusal is one line on stderr and exit code 2. Nothing is written.

- No plan: `no plan at fx/plan.json. Run fx-exit first.`
- A broken plan: the stop or the target on the wrong side, `risk_points` that
  does not add up, a swing plan with no `exit_by`, a day plan with no
  `flat_by_utc`, no `entry_at`.
- `--runs` out of 100 to 100,000; `--seed` out of 0 to 2,147,483,647.
- `--raw` on a day plan.
- No saved bars for the instrument (hourly for swing, 5-minute for day).
- Too few past days: under 40 usable daily bars (swing), under 20 full past
  days of 5-minute bars (day). From 40 to 119 daily bars it runs, with a note
  that the odds are rough.
- Nothing to replay: the exit-by or flat-by time is at or before the entry.
- No run of past days long enough for a block.
- A USD rate that is missing or too old (for pairs not priced in USD).

## Steps

1. Make sure `fx/plan.json` is today's plan from `/fx-exit`. If there is none,
   run that first.
2. Run `timemachine.py run --write`.
3. Tell the user, in this order:
   1. The mode and the pool (how many past days, which dates).
   2. The four odds: target first, stop first, neither, green.
   3. The price at the end (the middle, and the 5 to 95 range).
   4. The costs line and the average R. Say that R counts the costs.
   5. The verdict line and the lines under it, word for word.
   6. The seed.
4. When the pool is short, say "rough" (the note says so).
5. Say once that it is built from past days and is not a prediction.
6. Hand off to `/fx-budget` to size the plan.

## Example

The made-up sample market of the tests (not real prices). A swing BUY of gold,
planned on Fri 2026-07-17 at 9:05 AM PKT. `run --write --now "2026-07-17 09:06"`:

```
XAUUSD BUY swing plan: entry 2,433.49, stop 2,400.00, target 2,490.00 (1.69R).
  Yahoo price (gold futures), not your broker's price, live when planned (Fri 9:05 AM PKT).
Mode: swing. 10,000 replays of up to 20 FX days, Fri 2026-07-17 to Thu 2026-08-13, seed 19.
The pool: 58 past days of gold, 2026-04-27 to 2026-07-16 (daily bars built from Yahoo hourly bars).
Each replay strings together blocks of 5 past days. Each block starts on the same weekday, so Mondays keep their weekend gaps.
Drift removed: the pool's average day (+0.03 %) was taken out, so up and down start even.
Scaled to today: each past day is sized to today's range (ATR 1.15 % of the price).
Note: Only 73 usable daily bars before this plan (120 is the normal minimum). The odds are rough.

  target first   10.7 %
  stop first     72.8 %
  neither        16.6 %   still open at the end, Fri 14 Aug 2:00 AM PKT (5:00 PM New York)
  green          23.8 %   target first, or above the entry at the end (by price, before costs)

  Price at the end: median 2,400.63, 90 % of replays between 2,272.18 and 2,477.56 (Yahoo bid).
  Costs: $1,541.52 a lot ($6.00 commission, $1,535.52 swap for 28 nights) = 0.315R.
  Average R: -0.475, costs included (1R = $4,890.52 a lot: +0.84R at the target, -1.00R at the stop).

Strongly toward the stop: at 1.69R after costs the target has to come first in 54 % of the replays that hit one side; it did in 13 %.
6.2 % of the replays gapped past the stop: the loss was bigger than 1R.
A simulation built from Gold's own past 58 days. It is not a prediction: news, rolls and gaps can do things those days never did.
Same seed, same numbers: repeat with --seed 19.
Wrote fx/timemachine.json.
Next: python3 fx-budget/size.py --write sizes it to your max loss per trade, costs included. Nothing was placed.
```

The pool is short here (73 usable daily bars), so the note says the odds are
rough. Tell the user that.

## Rules

- Every number comes from the run. Never make one up or round it again.
- Repeat the verdict lines word for word. Never rewrite them, never soften them.
- "green" is by price alone, before commission and swap.
- Always say the seed, so the user can repeat the run.
- One plan per run, from `fx/plan.json` (or `--plan FILE`).
- A verdict describes the replays. It never says buy, sell, take or skip.

## What it never does

- It never places an order, never sizes one, and never changes the plan.
- It never uses the network. It reads only the saved bars.
- It never uses a bar of the plan's own FX day or later in the pool.
- It never calls a result certain, and it never gives advice.

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
