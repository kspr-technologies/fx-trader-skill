# The sample FX market (made up)

All prices in this folder are made up. They are not real Yahoo, CME or broker data.

## Why it is made up

- Yahoo's terms do not allow copying its data without permission.
- CME owns the futures prices.
- So the tests use made-up prices in Yahoo's real file format.

## What is real and what is not

- Real: the file formats, the symbols, the market hours, the calendar layout.
- Made up: every price, volume, calendar number, COT number and news item.
- The prices are kept far from real ones (gold near 2,400, EURUSD near 1.085).

## How to rebuild it

    cd tests/fixtures
    python3 make_fixtures.py

The script uses the Python standard library only. It gives the same files every time.

## The story

- Fri 2026-07-03: an early close for gold and oil (13:00 New York).
- Thu 2026-07-16: gold closes at 2,431.6, over its 20-day high of 2,418.4.
- Fri 2026-07-17: a swing BUY of gold at 9:05 AM PKT. Stop 2,400.0, target 2,490.0.
- Sun 2026-07-19: gold opens 0.70% over Friday's last price (a weekend gap).
- Tue 2026-07-21: Yahoo has no prices from 7:00 AM to 5:00 PM PKT (an outage).
- Thu 2026-07-23: a day SELL of EURUSD at 12:40 PM PKT, after the London opening range
  (1.08480 to 1.08640). The target is hit at 3:20 PM PKT. The ECB decision at 5:15 PM
  PKT moves EURUSD up 0.6%.
- Wed 2026-07-29: Yahoo gold moves from the Aug to the Dec contract at 12:00 PM PKT
  (+0.90%). The plan levels move up with it. No alert rings on the jump.
- Thu 2026-07-30: gold reaches the moved target, 2,512.41, in the 5-minute bar that
  ends at 7:40 PM PKT.
