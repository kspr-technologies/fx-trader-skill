---
name: fx-cot
description: >-
  The COT Reader. Shows what the big speculators hold in gold, silver, oil, gas,
  copper and the main currencies, from the CFTC Commitments of Traders report:
  net long or short, the change in the week, the share of all open contracts, and
  the COT index over 3 years. Says the position date (a Tuesday) and when it came
  out (Friday 3:30 PM New York, Saturday early morning in Karachi), turns the sign
  for USDJPY, USDCHF and USDCAD, and says if your trade is on the same side.
  Context only, never a timing signal. Use when the user says "what is the COT
  saying", "commitments of traders", "are hedge funds long gold", "how are
  speculators positioned in EURUSD", "is the new COT out", or "run fx-cot".
argument-hint: "[show|new] [--symbols XAUUSD,EURUSD] [--all]"
---

# fx-cot

The CFTC is the US futures regulator. Each week it posts the Commitments of
Traders (COT) report. It says who holds the big US futures markets.
This skill reads the part about the big speculators (hedge funds and the like).
It shows if they are net long or net short, and how that changed in the week.
It does this for gold, silver, platinum, palladium, copper, oil, gas and the main currencies.

It is old news: positions from a Tuesday, out the Friday after.
It is context, not a timing signal.

Tool in this folder (standard library Python 3.8):

```bash
python3 "${CLAUDE_SKILL_DIR}/cot.py" --help
```

## What it reads and writes

| file | how | what |
| --- | --- | --- |
| `fx/watchlist.json`, `fx/plan.json`, `fx/portfolio.json` | reads | which instruments; your plan and open trades |
| `fx/config.json` | reads | `universe`, the instrument table |
| `fx/data/cot/<dataset>-<code>.json` | reads and writes | the saved CFTC rows, one file a market |
| `fx/state/cot.json` | writes | the newest report shown per market, the last check |

- The data comes from the CFTC's public data site (no key, no login).
  If that fails, it reads the CFTC's weekly text file (the newest week only).
- It asks the CFTC only when a new report is due: about once a week.
  Never twice in one hour, even after a failure. At most 2 calls a run.
- It waits at least 2 seconds between two calls. One try each, no retry loop.
- `"yahoo": false` does not stop it. COT is not a Yahoo price.
- `--offline`: no network at all. Only the saved files in `fx/data/cot/`.
- `--now "2026-07-17 09:01"`: a replay. No network, and a report is shown only
  if it was out by that time (Karachi time).
- `--dry-run`: does not write `fx/state/cot.json`.

## How to read it

- **Net**: long contracts minus short contracts of the big speculators.
- **Change in the week**: how much the net moved since the report before.
- **Share of open contracts**: net as a % of all open contracts in that market.
- **COT index**: where the net sits between its lowest (0) and highest (100)
  over the last 156 reports (3 years). `--weeks 52` uses one year.
  Near 80 or above is near the top; near 20 or below is near the bottom.
  Positions can stay there for months.
- **Who**: metals and energy show managed money (Disaggregated report).
  Currencies show leveraged funds (TFF report).
- **The turned sign**: yen, Swiss franc and Canadian dollar futures are priced in
  dollars per that currency. So long yen futures is the sell side of USDJPY.
  The tool turns the sign for you.
- **Crosses** like EURJPY have no COT market. Each currency is shown on its own,
  as two legs. There is no mixed score.
- **Brent** (UKOIL): only the small NYMEX Brent market is in this report.
  Most Brent trades on ICE in London, which is not read.
- The tool names the side the big speculators hold. It never says what to do.

## Step 1: show

```bash
python3 "${CLAUDE_SKILL_DIR}/cot.py" show
python3 "${CLAUDE_SKILL_DIR}/cot.py" show --symbols XAUUSD,USDJPY
python3 "${CLAUDE_SKILL_DIR}/cot.py" show --all --weeks 52 --offline
python3 "${CLAUDE_SKILL_DIR}/cot.py" show --symbols XAUUSD --offline --now "2026-07-17 09:01"
```

- Which instruments: `--symbols`, else `--all` (your whole `universe`), else the
  watchlist hits, then the plan, then your open trades (no repeats).
  If there are none of those, the whole universe.
- `--symbols` and `--all` together is an error.
- Each instrument gets a block: the market, the position date, when it came out,
  net, change, share, COT index, the side, and the report age.
- If you have a plan or an open trade on it, it says so. It also says if the big
  speculators are on the same side or the other side. That is a plain fact.
- Exit 0 when at least one market was shown. Exit 2 when no market has data.

## Step 2: new

```bash
python3 "${CLAUDE_SKILL_DIR}/cot.py" new
python3 "${CLAUDE_SKILL_DIR}/cot.py" new --symbols EURUSD,EURJPY --json --dry-run
```

- Shows only the reports not shown before (the `seen` dates in `fx/state/cot.json`).
- The first run shows the newest report of each market.
- Exit 0 when something is new. Exit 1 when nothing is new (a plain no).
- Run it once a week, after Friday 3:30 PM New York.
  That is Saturday 12:30 AM PKT in US summer, 1:30 AM PKT in US winter.
  In a week with a US holiday the report comes out on Monday instead.

## Example

The made-up sample market, on Friday 17 July at 9:01 AM Karachi time.
The 14 July report comes out only at 12:30 AM PKT on Saturday, so it is hidden:

```bash
python3 "${CLAUDE_SKILL_DIR}/cot.py" show --symbols XAUUSD --offline --now "2026-07-17 09:01"
```

```
COT for 1 instrument (from --symbols). Positions from Tuesdays, out on Fridays.

XAUUSD (Gold)
  gold futures (COMEX), CFTC code 088691
  Positions on Tue 2026-07-07.
  Out Sat 11 Jul 12:30 AM PKT (Fri 10 Jul 3:30 PM New York).
  Big speculators (managed money): net long 98,000 contracts, +6,200 in the week.
  That is 23.5% of all open contracts.
  COT index 14.8 of 100 over 156 reports (0 = lowest net, 100 = highest).
  Note: that is near the bottom of 156 reports. Positions can stay there for months.
  Net long gold futures is the buy side of XAUUSD.
  The report is 10 days old.

The next COT report is due Sat 18 Jul 12:30 AM PKT (Fri 17 Jul 3:30 PM New York).
Replay at Fri 17 Jul 9:01 AM PKT: reports that came out later are not shown.
Source: CFTC Commitments of Traders (saved in fx/data/cot).
COT is old news: positions from a Tuesday, out the Friday after.
It is context, not a timing signal.
```

COT is a reason to look at a chart, never a setup. `/fx-scanner` and `/fx-entry`
decide if there is a trade. `/fx-budget` sizes it, `/fx-risk` checks it, and
`/fx-broker` shows the order only after your yes.

## What it never does

- It never gives advice. It never says buy or sell. It only names the side the
  big speculators hold.
- COT is never a timing signal, a setup or an order.
- It never writes a file that another skill reads. fx-entry reads nothing from it.
- It never shows a price. The only age it shows is the report's age.
- It never uses the network with `--offline` or `--now`.
- It never picks a market by its name or size, only by its CFTC code.
- It never takes the report date from a calendar, only from the data.
- In a replay, it never shows a report before it came out.

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
