"""The COT files of the sample market (06 5.5): 160 weekly rows for gold (Disaggregated,
managed money) and for the euro (TFF, leveraged funds). Made-up numbers, CFTC field
names, values as strings, newest first (skeleton 4.15)."""

import datetime
import os

from .util import rng, write_json

NEWEST = datetime.date(2026, 7, 14)     # the newest report (out Fri 07-17 15:30 New York)
REPORTS = 160
FETCHED = "2026-07-17T20:05:00Z"        # after that release
MARKETS = {
    "gold": {"dataset": "72hh-3qpy", "code": "088691", "market": "GOLD - COMMODITY EXCHANGE INC.",
             "units": "(CONTRACTS OF 100 TROY OUNCES)", "long": "m_money_positions_long_all",
             "short": "m_money_positions_short_all", "chg_long": "change_in_m_money_long_all",
             "chg_short": "change_in_m_money_short_all", "pct_long": "pct_of_oi_m_money_long_all",
             "pct_short": "pct_of_oi_m_money_short_all", "oi": 420000, "net": (40000, 190000),
             "short_size": (15000, 40000), "pins": {datetime.date(2026, 6, 30): 91800, datetime.date(2026, 7, 7): 98000},
             "extra": (("prod_merc_positions_long", 45000), ("prod_merc_positions_short", 120000),
                       ("swap_positions_long_all", 70000), ("swap__positions_short_all", 160000))},
    "euro": {"dataset": "gpe5-46if", "code": "099741", "market": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
             "units": "(CONTRACTS OF EUR 125,000)", "long": "lev_money_positions_long",
             "short": "lev_money_positions_short", "chg_long": "change_in_lev_money_long",
             "chg_short": "change_in_lev_money_short", "pct_long": "pct_of_oi_lev_money_long",
             "pct_short": "pct_of_oi_lev_money_short", "oi": 650000, "net": (-80000, 30000),
             "short_size": (90000, 140000), "pins": {NEWEST: -42000},
             "extra": (("asset_mgr_positions_long", 380000), ("asset_mgr_positions_short", 150000),
                       ("dealer_positions_long_all", 60000), ("dealer_positions_short_all", 330000))},
}


def _series(name, m):
    """[(date, long, short, oi, extras)] oldest first, plus one hidden older week for the
    first change. The net walks inside m["net"] and is bent over 8 weeks onto each pin."""
    g = rng("cot", name)
    dates = [NEWEST - datetime.timedelta(weeks=k) for k in range(REPORTS, -1, -1)]
    lo, hi = m["net"]
    mid, net, nets = (lo + hi) / 2.0, (lo + hi) / 2.0, []
    for _ in dates:
        net += g.gauss(0, (hi - lo) / 25.0) - 0.08 * (net - mid)
        nets.append(min(max(net, lo), hi))
    for i, d in enumerate(dates):
        if d in m["pins"]:
            delta = m["pins"][d] - nets[i]
            for k in range(max(0, i - 8), len(nets)):
                nets[k] += delta * (1.0 if k >= i else (k - (i - 8)) / 8.0)
    out = []
    for d, n in zip(dates, nets):
        n = m["pins"].get(d, int(round(n / 100.0)) * 100)
        short = g.randint(*m["short_size"])
        oi = int(m["oi"] * (1 + g.gauss(0, 0.04)))
        extras = [(k, int(v * (1 + g.gauss(0, 0.05)))) for k, v in m["extra"]]
        out.append((d, n + short, short, oi, extras))
    return out


def rows_of(name):
    """The CFTC rows of one market, newest first, values as strings."""
    m = MARKETS[name]
    s = _series(name, m)
    rows = []
    for (d0, l0, s0, _, _), (d, long_, short, oi, extras) in zip(s, s[1:]):
        row = {"report_date_as_yyyy_mm_dd": d.isoformat() + "T00:00:00.000",
               "cftc_contract_market_code": m["code"], "market_and_exchange_names": m["market"],
               "open_interest_all": str(oi), m["long"]: str(long_), m["short"]: str(short),
               m["chg_long"]: str(long_ - l0), m["chg_short"]: str(short - s0),
               m["pct_long"]: "%.1f" % (100.0 * long_ / oi), m["pct_short"]: "%.1f" % (100.0 * short / oi),
               "contract_units": m["units"], "futonly_or_combined": "FutOnly"}
        for k, v in extras:
            row[k] = str(v)
        rows.append(row)
    return list(reversed(rows))


def write(out):
    """Write data/cot/<dataset>-<code>.json for gold and the euro. Returns the paths."""
    written = []
    for name in sorted(MARKETS):
        m = MARKETS[name]
        doc = {"fetched_at": FETCHED, "tried_at": FETCHED, "error": None,
               "last_modified": "Fri, 17 Jul 2026 19:30:41 GMT", "source": "api",
               "dataset": m["dataset"], "code": m["code"], "market": m["market"], "rows": rows_of(name)}
        path = os.path.join(out, "data", "cot", "%s-%s.json" % (m["dataset"], m["code"]))
        write_json(path, doc)
        written.append(path)
    return written
