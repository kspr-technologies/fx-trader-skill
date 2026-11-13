# The FX Trader skill

11 Claude skills for trading currency pairs, gold and oil at a MetaTrader broker: scan for setups on both sides, track the news and the calendar, time every entry and exit, and replay a trade 10,000 times before you risk a dollar.

By Waleed Amjad (waleedamjad99@hotmail.com), KSPR Technologies.

This pack was made from the PSX Day Trader skill pack, which came from the Day Trader skill by Jake Schincariol (github.com/Jakeschincariol/day-trader-skill). MIT.

Free, no signup, no API key. The tools are plain Python.
Nothing in this pack places an order. You place each trade yourself, in
your own MetaTrader. Paper trading is the default.

**Version 1.0.0: please use paper trading only.** A review found these gaps.
Most are in live mode (`"paper": false`). The next version fixes them. Until
then, keep `"paper": true`.

- A live trade can be sized from the paper balance in `fx/portfolio.json`.
- One wrong digit in `--broker-price` can move the stop, and the yes is saved
  before you see the broker's stop.
- `stop --wider` has no limit, and the daily loss limit does not see it.
- A pending (stop or limit) order that fills on a later day cannot be recorded,
  and pending orders are left out of the margin and group checks.
- After a "no" or a failed close, the same close cannot get a new yes that day.
- `--now` works in live mode, so an old risk PASS can be used again.
- `flatten` can give a time after an early close.
- Small: a day pending order expires at the flat-by time, not at the entry
  cut-off; for cross pairs (like GBPJPY) the loss can be a little over the limit.

## What it does

- It scans 20 markets for setups: 12 currency pairs, gold, silver,
  platinum, palladium, copper, two kinds of oil and natural gas.
- It works on both sides. A buy wins when the price goes up. A sell wins
  when the price goes down.
- It reads the news headlines and the economic calendar for your markets.
- It shows what the big traders hold in the futures market (the COT report).
- It tells you the moment a setup's rule is met.
- It sets your stop loss (SL) and take profit (TP) before you trade.
- The Time Machine replays the trade 10,000 times on the market's own past
  moves.
- It sizes the trade in lots, so the loss at the stop fits your max loss.
  The spread, the commission and the swap are counted.
- It checks every trade against your limits.
- It shows the order as a ticket for MetaTrader and waits for your yes.
- It rings an alert on your stop, your target and the clock.
- Two styles: swing (hold for days, up to 20) and day (out the same day).
- All times are Karachi time (PKT). New York time is shown beside it where
  it matters.

## Prices: read this first

- The prices come from Yahoo Finance. They are free.
- The tools ask Yahoo slowly, 2 seconds or more apart. They keep a copy in
  `fx/data/yahoo/` (the cache). The next time, they ask only for new bars.
- `--offline` uses only the saved copy. `--now "2026-07-17 09:00"` replays a
  past moment from the saved copy.
- For currency pairs, Yahoo's price is close to your broker's price.
- For gold, silver, oil, gas and copper, Yahoo shows a futures price. Your
  broker's price is a little different. So the ticket gives the stop and the
  target as a distance from your fill. Type your broker's price with
  `--broker-price` to get exact levels.
- Yahoo's prices can be late, and some hours can be missing. Every price the
  tools show says how old it is. Your MetaTrader screen is the live one.

See the fine print for Yahoo's terms, futures rolls, delays and gaps.

## Install

You need [Claude Code](https://claude.com/claude-code).
The tools need Python 3.8 or newer. Nothing to pip install.

**Easiest: as a plugin.** In Claude Code, type these two lines:

```
/plugin marketplace add kspr-technologies/fx-trader-skill
/plugin install fx-trader-skill@fx-trader-skill
```

Then the skills show up as `/fx-trader-skill:fx-scanner` and so on. You can
also just ask in plain words, like "scan forex for setups".

**Or copy the folders.** Get the code, then copy the skill folders:

```bash
git clone https://github.com/kspr-technologies/fx-trader-skill.git
cp -r fx-trader-skill/fx-* ~/.claude/skills/
```

Copied folders give plain `/fx-scanner`. For one project only, copy the folders
into that project's `.claude/skills/` instead.

**Where your files go.** Start Claude Code in the folder you want to trade
from. The tools keep your settings, prices and paper trades in an `fx/` folder
there. With no `fx/config.json`, they use safe defaults: paper trading, a
$1,000 paper balance, 1% max loss a trade. Say "set my rules" to change them.

No Claude Code at all? Paste one `SKILL.md` at the top of a chat. It runs as
a mode. You lose the Python tools, but the method still works.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/fx-scanner` | Chart Scanner | Scans your markets for setups on both sides: swing setups on daily bars, day setups on the London opening range. |
| `/fx-news` | News Monitor | Reads forex and commodity headlines from 10 free feeds and tags each one to your markets. |
| `/fx-calendar` | Economic Calendar | Shows the week's big events (jobs, prices, central banks, oil stocks) in Karachi and New York time, and the no-new-trade windows around them. |
| `/fx-cot` | COT Reader | Shows what the big speculators hold in the futures market, from the weekly CFTC report. Context only. |
| `/fx-entry` | Entry Timer | Watches one setup and names the moment its rule is met. |
| `/fx-exit` | Exit Planner | Sets your stop loss and take profit before you trade, with the costs and the time to be out. |
| `/fx-timemachine` | Time Machine | Replays the plan 10,000 times on the market's own past moves, before you risk a dollar. |
| `/fx-budget` | Budget Manager | Sizes the trade in lots to your max loss, with the spread, the commission and the swap. Says no when even the smallest lot is too big. |
| `/fx-risk` | Risk Manager | Checks every order against your limits and the market clock: PASS or BLOCK, with the reasons. |
| `/fx-broker` | Order Ticket | Shows the order for MetaTrader and waits for your yes. Paper by default. It never places an order. |
| `/fx-alerts` | Alerts | Rings once when a price, your stop, your target or a time comes. Shows the market hours and sessions. |

## How to use it

### Start here

1. Make a folder `fx/` in your project.
2. Copy `fx-budget/config.example.json` to `fx/config.json`.
3. Set your balance in `"account"`. Use only money you can lose.
4. Set your max loss per trade in `"risk"`. The default is 1% of the
   balance.
5. Copy your broker's real values. In MetaTrader, right click a symbol in
   Market Watch, then Specification (MT4: Symbols, then Properties). Copy
   the contract size, the digits, the lots, the spread, the commission and
   the swap into `"instruments"` in `fx/config.json`. The shipped values are
   only typical ones. `/fx-budget` lists the six traps when you copy.
6. Keep `"paper": true`. On paper, nothing reaches your broker. Stay on paper
   until you know the tools well. Going live is your own choice, in your own
   config.
7. Your MetaTrader login and password never go in a file or in the chat.

### The chain

Everything lands in the `fx/` folder in your project. Each skill reads what
the last one wrote:

```
scanner -> news, calendar, cot -> entry -> exit -> timemachine -> budget -> risk -> broker
alerts: the stop, the target, the time to be out, the Friday check   (while a trade is open)
```

All times are Karachi time (PKT, UTC+5 all year). New York time is shown
beside it where it matters. A trading day ends at 5:00 PM New York: that is
2:00 AM PKT in the US summer and 3:00 AM PKT in the US winter.

### Two styles: swing and day

- **Swing** (the default, `"style": "swing"`). Daily bars. The stop is 2 ATR
  from the entry (ATR is the average size of one day's move). The target is
  2 times the risk (2R). You can give other ones. The trade can stay open up
  to 20 trading days. Swap counts each night.
- **Day** (`"style": "day"`). 5-minute bars and the London opening range:
  the first 15 minutes after London opens (12:00 PM PKT in the UK summer,
  1:00 PM PKT in the UK winter). The stop is the middle of the range. The
  target is 2R.
  No new day trades after 9:00 PM PKT. Out by 11:30 PM PKT, win or lose.

A setup is a rule that is met. It is not a forecast. The tools describe what
the rule found. They never say "buy" or "sell" as advice.

### An example, step by step

The example below uses the **sample market made for the tests**. All its prices are
made up (gold near 2,400, EURUSD near 1.085). The files copy Yahoo's real format, so
the tests can repeat every number here. The account is a paper account of $10,000,
with a max loss of 1% ($100) a trade.

A swing BUY of gold (XAUUSD):

1. `/fx-scanner` at 9:00 AM PKT on Fri 2026-07-17: gold's daily bar closed at 2,431.6,
   over its 20-day high of 2,418.4. A 20-day breakout.
2. `/fx-news`, `/fx-calendar` and `/fx-cot`: a gold headline, the US events of the
   week, and big speculators net long (context only).
3. `/fx-entry`: TRIGGERED. The daily close is above the level.
4. `/fx-exit` with a stop of 2,400.0 and a target of 2,490.0: you buy at the ask,
   2,433.49 (Yahoo's 2,433.4 plus the 0.09 spread). The stop is 33.49 away, the target
   56.51. Reward to risk 1.69 before costs.
5. `/fx-timemachine` replays it 10,000 times over the next 20 days (seed 19). On this
   made-up data the target comes first in 10.7% of the replays and the stop in 72.8%.
   Its words: "Strongly toward the stop". The sample story then hits the target
   anyway. A replay is a reality check, not a forecast.
6. `/fx-budget`: 0.02 lots. At the stop you lose $97.81 in all: $66.98 from the price
   and the spread, $0.12 commission and $30.71 swap if you hold all 20 days.
   With the shipped $1,000 account it says no: even 0.01 lot would lose $48.91 at the
   stop, over the $10.00 limit. It never rounds up.
7. `/fx-risk`: PASS, with a note: with the swap for all 20 days counted, the trade can
   win only 0.84 times what it risks.
8. `/fx-broker` with your MetaTrader price, `--broker-price 2429.15`: your broker's
   levels are entry 2,429.24, SL 2,395.75, TP 2,485.75. You type yes. Only then does it
   show the line to type into MetaTrader. On paper it fills at 2,433.49.
9. `/fx-alerts`: on Tue 07-21 Yahoo has no gold prices from 7:00 AM to 5:00 PM PKT. At
   3:00 PM the check says the price is 8 hours old (stale) and rings nothing. On Wed
   07-29 Yahoo's gold moves to the Dec contract (+0.90%). The plan moves with it: stop
   2,421.59, target 2,512.41. No alert rings on the jump. On Thu 07-30 the target
   rings at 7:45 PM PKT.

A day SELL of EURUSD on Thu 2026-07-23:

1. The London opening range (12:00 to 12:15 PM PKT) is 1.08480 to 1.08640.
2. At 12:40 PM PKT the 5-minute bar closes at 1.08452, under the range low.
   TRIGGERED.
3. The stop is the range middle plus the spread: 1.08561 (10.9 pips). The target is
   twice that risk: 1.08234. Out by 11:30 PM PKT (2:30 PM New York), win or lose.
4. 0.86 lots. At the stop you lose $98.90, commission included. One more step (0.87
   lots) would lose $100.05, over the $100 limit: fx-risk would BLOCK it.
5. `/fx-risk` says PASS. You type yes. On paper it fills at 1.08452. The target alert
   rings at 3:20 PM PKT. The ECB decision comes later, at 5:15 PM PKT. fx-risk blocks
   new EURUSD trades from 4:45 to 6:15 PM PKT (the rate decision, then the press
   conference).

The paper ledger then closes both trades at their targets: EURUSD +$182.32 and gold
+$99.66, after all costs. These numbers come from made-up prices. They say nothing
about a real market. The lesson is real, though: in the gold trade, the costs
(mostly swap) were 46% of the price loss at the stop.

## The tools

All standard-library Python. Each tool says what it found in short lines, and
the last line says the next step.

```bash
python3 fx-scanner/scan.py swing                                # daily setups, both sides -> fx/watchlist.json
python3 fx-scanner/scan.py day                                  # the London opening range on 5-minute bars
python3 fx-scanner/scan.py refresh                              # only bring the saved Yahoo prices up to date
python3 fx-news/news.py --symbols XAUUSD,EURUSD                 # new headlines for these markets
python3 fx-calendar/events.py week                              # this week's events, in PKT and New York time
python3 fx-calendar/events.py next                              # the next big events for your trades
python3 fx-calendar/events.py quiet EURUSD                      # the no-new-trade windows around big news
python3 fx-cot/cot.py show --symbols XAUUSD                     # what the big speculators hold
python3 fx-entry/entry.py check XAUUSD --write                  # TRIGGERED or WAITING -> fx/entry.json
python3 fx-entry/entry.py watch EURUSD                          # checks again and again until it triggers
python3 fx-exit/exit.py plan --write                            # the stop and the target -> fx/plan.json
python3 fx-timemachine/timemachine.py run --write               # 10,000 replays -> fx/timemachine.json
python3 fx-budget/size.py --write                               # the lots -> fx/orders.json
python3 fx-risk/check.py                                        # PASS or BLOCK for each order
python3 fx-broker/ticket.py show                                # the tickets, READY or not
python3 fx-broker/ticket.py approve --index 1 --reply "yes"     # your exact words; the MetaTrader line comes after a yes
python3 fx-broker/ticket.py result --index 1 --status filled    # what happened (on paper: the paper fill)
python3 fx-broker/ticket.py close --position XAUUSD-1           # the close order for one open trade
python3 fx-broker/ticket.py log                                 # the ledger, in plain lines
python3 fx-alerts/alerts.py add --from-plan                     # alerts for the stop, the target and the times
python3 fx-alerts/alerts.py check                               # rings what hit, once
python3 fx-alerts/alerts.py clock                               # market hours, sessions and big events
```

Every tool also takes `--offline`, `--now`, `--json`, `--quiet`, `--config`
and `--data`. `python3 <tool> --help` shows all the options.

```bash
cd tests && python3 -m unittest discover -v
```

The tests run offline, on the made-up sample market in tests/fixtures/fx-market/ (rebuild it with python3 make_fixtures.py in tests/fixtures/).

`tests/fx_core.py` holds the shared helpers. Each tool copies the ones it
uses, word for word, so each tool stands alone. `tests/test_repo.py` checks
that the copies match.

## Fine print

**Not financial advice.** This is software that reads public data and does arithmetic.
It does not know your situation and it cannot see the future. You can lose money.
Decide for yourself, and talk to a licensed adviser if you need advice.

**Most retail CFD accounts lose money.** EU regulators found that 74% to 89% of retail
CFD accounts lose money (ESMA, 2018). Brokers that must print their own figure show
numbers in the same range.

**Leverage.** At 1:100, $1,000 of margin holds a $100,000 position. A 1% move against
it costs $1,000: all of that margin. The pack sizes each trade from the max loss you
set, not from the leverage you have.

**No trade goes through until you say yes.** Nothing in this pack places an order.
`/fx-broker` shows the order first. It prints the line to type into MetaTrader only
after `/fx-risk` passes that exact order and you type yes for it. Then you place it
yourself. MetaTrader has no simple, safe order link for a Mac (its Python package is
for Windows only), so the pack does not try. If a broker tool ever appears, set it to
"Needs approval" in Claude, never "Always allow". Your MetaTrader login and password
never go in the chat.

**Paper first.** `"paper": true` is the default in `config.example.json`. On paper,
fills go into `fx/portfolio.json` and nothing reaches your broker.

**Yahoo's terms.** The prices come from Yahoo Finance. Yahoo's terms (updated
1 November 2026) do not allow collecting data "using any automated means" without
Yahoo's prior permission. This pack is a personal tool that asks rarely and slowly
(2 seconds or more apart, with a cache), but that is still not clearly allowed. The
futures prices (gold, silver, platinum, palladium, copper, oil and gas) come from CME
Group's exchanges, and CME owns that data. Use the data for yourself only. To stop all
Yahoo requests, set `"yahoo": false`. The calendar, news and COT downloads are not
Yahoo and still run; `--offline` stops every download. The test data in this pack is
made up for the same reason.

**Yahoo's price is not your broker's price.** For gold, silver, oil, gas and copper,
Yahoo shows futures. Your broker's price is a CFD on spot or on another futures month.
The two differ (gold futures are often 0.1% to 1% above spot). So plans use Yahoo's
prices, and the ticket gives the stop and the target as distances from your fill. It
gives broker prices only after you type your broker's price with `--broker-price`. For
currency pairs the two prices are close.

**Futures rolls.** Yahoo's futures series move from one contract month to the next a
few times a year (oil and gas every month). The price jumps then, but nothing really
moved. The tools find each roll, adjust the older prices, and do not ring an alert on
the jump. Your broker's price does not jump.

**Delays and gaps in the data.** Yahoo's futures prices are about 10 minutes late.
Currency pairs are close to live. Every price the tools show says how old it is.
Sometimes Yahoo has no prices for hours. Your MetaTrader screen is the live one.

**Weekend gaps and news.** The market shuts from Friday 5:00 PM New York (2:00 or
3:00 AM PKT on Saturday) until Sunday evening in New York. Monday's first price can be
far from Friday's last. At big news, prices can jump in seconds. A stop (SL) then
fills at the next price, which can be much worse than the stop.

**Swap.** A trade still open at 5:00 PM New York pays or earns swap for that night,
and three nights' worth on one day a week (often Wednesday). Swing plans count it. On
a swap-free (Islamic) account the swap is zero, but some brokers charge a fee per night
instead. Set `swap_free_fee_per_lot` from your broker's page.

**Copy your broker's numbers.** The shipped instrument table holds typical values
only. Copy the real ones from MetaTrader: right click the symbol in Market Watch, then
Specification. Spreads get wider at the 5:00 PM New York rollover and at big news.

**Pakistan rules, as facts.** The State Bank of Pakistan's EPD Circular Letter No. 08
of 2022 (18 May 2022) says that sending money from Pakistan to overseas forex, margin
or CFD trading platforms, through any payment channel, is not allowed. SECP's press
release of 9 December 2025 says that in Pakistan, securities and commodities trading
can only be done through SECP-licensed securities brokers and futures market brokers.
Lists of licensed brokers are on the websites of the SECP, the Pakistan Stock Exchange and
PMEX (Pakistan Mercantile Exchange).

**Taxes.** Trading gains can be taxed. Ask FBR (fbr.gov.pk) or a tax adviser. Nothing
here is tax advice.

**The Time Machine is a simulation, not a prediction.** It replays past price moves.
The future can do things the past never did. Treat its odds as a reality check, never
a promise.

**COT is old news.** The CFTC's Commitments of Traders report shows positions on a
Tuesday and comes out the Friday after. It is context, not a timing signal.

## Files

```
fx-scanner/      SKILL.md, scan.py
fx-news/         SKILL.md, news.py
fx-calendar/     SKILL.md, events.py
fx-cot/          SKILL.md, cot.py
fx-entry/        SKILL.md, entry.py
fx-exit/         SKILL.md, exit.py
fx-timemachine/  SKILL.md, timemachine.py
fx-budget/       SKILL.md, size.py, config.example.json
fx-risk/         SKILL.md, check.py
fx-broker/       SKILL.md, ticket.py
fx-alerts/       SKILL.md, alerts.py, alerts.example.json
tests/           the tests, fx_core.py (the shared helpers), the made-up sample market
```

The tools write to an `fx/` folder in your project:

```
fx/config.json       your settings (from fx-budget/config.example.json)
fx/alerts.json       your alert rules (from fx-alerts/alerts.example.json)
fx/watchlist.json    the scanner's hits
fx/entry.json        the entry trigger
fx/plan.json         the plan: entry, stop, target, costs, the time to be out
fx/timemachine.json  the replay results
fx/orders.json       the proposed orders
fx/prices.json       the newest prices, and the broker prices you typed
fx/portfolio.json    your account (paper or live) and the open trades
fx/state/            the risk checks, the ledger, the alerts that rang, news seen
fx/data/             the saved Yahoo prices, futures rolls, calendar, COT and news
```

MIT. See LICENSE.
