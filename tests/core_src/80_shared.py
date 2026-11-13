# group: shared by skill scripts

# Helpers that two or more skill scripts use (decision 2 of the build brief).

RECENT_FX_DAYS = 5        # roll notes and outage lines look this many FX days back
WRAP = 78              # the line width of fx-scanner and fx-entry
WIDTH = 78             # the line width of fx-news and fx-risk


def back_fx_day(fx_date, n, cfg):
    """The FX day n FX days before fx_date (weekends and full FX holidays skipped)."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    for _ in range(n):
        d -= datetime.timedelta(days=1)
        while d.weekday() >= 5 or full_fx_holiday(d, cfg):
            d -= datetime.timedelta(days=1)
    return d.isoformat()


def recent_start(fx_date, cfg):
    """The start (UTC) of the window "the last 5 FX days" that ends with fx_date."""
    day_end = cfg.get("day_end", "17:00 New York")
    return fx_day_bounds(back_fx_day(fx_date, RECENT_FX_DAYS - 1, cfg), day_end)[0]


def contract_month(symbol, root):
    """"GCQ26.CMX" -> "Aug" (by MONTH_CODES), or None."""
    letter = str(symbol or "")[len(root):len(root) + 1]
    for month, code in MONTH_CODES.items():
        if code == letter:
            return month
    return None


def roll_window_date(w):
    """The first FX day of an expected roll window (its start is 17:00 New York the day
    before), as "YYYY-MM-DD"."""
    n = to_ny(w[2])
    d = n.date() + datetime.timedelta(days=1 if n.hour >= 17 else 0)
    return d.isoformat()


def fx_day_of(utc, day_end):
    """The FX day of a moment; a weekend moment gives its New York date."""
    return fx_day(utc, day_end) or to_ny(utc).date().isoformat()


def rolls_known(rolls, now):
    """The rolls already found at now: a replay sees a roll only from its found_at on."""
    out = []
    for r in rolls:
        found = parse_utc(r.get("found_at"))
        if found is None or found <= now:
            out.append(r)
    return out


def roll_notes(inst, rolls, now, fx_day, cfg, data_dir):
    """The roll lines of a futures hit (section 4.5): a roll ahead in the next 5 FX days,
    a roll in the last 5 FX days, a roll that the history search missed."""
    root = inst_root(inst)
    if not root or not inst.get("futures"):
        return []
    day_end = cfg.get("day_end", "17:00 New York")
    name = inst.get("name") or inst["key"]
    word = ROLL_WORDS.get(root, name.lower())
    mine = [r for r in rolls if r.get("root") == root]

    def roll_in(a, b):
        return any(a <= (parse_utc(r.get("first_bar_new")) or a - datetime.timedelta(1)) <= b
                   for r in mine)

    out = []
    horizon = fx_day_bounds(add_fx_days(fx_day, RECENT_FX_DAYS, cfg), day_end)[1]
    for w in expected_rolls(root, inst, now, horizon):
        if w[2] < horizon and w[3] > now and not roll_in(w[2], w[3]):
            old_m, new_m = contract_month(w[0], root), contract_month(w[1], root)
            if w[2] > now:
                when = roll_window_date(w)
            else:   # the window is open now (check: the spec shows only a date)
                when = "now, by %s at the latest" % to_ny(w[3]).date().isoformat()
            out.append("%s rolls about %s (%s to %s): Yahoo prices jump then."
                       % (name, when, old_m, new_m))
            break
    since = recent_start(fx_day, cfg)
    for r in mine:
        first = parse_utc(r.get("first_bar_new"))
        if first is not None and since <= first <= now and r.get("ratio"):
            pct = (float(r["ratio"]) - 1.0) * 100.0
            out.append("%s rolled on Yahoo on %s (%+.2f%%). Older prices are adjusted to match."
                       % (name, day_label(parse_fx_date(fx_day_of(first, day_end), weekend_ok=True)), pct))
    if (inst["futures"] or {}).get("history_check"):
        closes = _daily_closes(data_dir, inst, now, cfg, True)
        today = to_ny(now).date()
        for w in expected_rolls(root, inst, since, now):
            if w[3] <= now and w[3] >= since and w[4] <= today and w[4] in closes \
                    and not roll_in(w[2], w[3]):
                out.append("Could not find the %s roll of about %s in Yahoo data. ATR and "
                           "setups near that date may be off." % (word, w[4].isoformat()))
    return out


def pct_text(x):
    """A percent for people: "1.0%", "0.5%", "0.25%": at least 1 decimal, at most 2."""
    d = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    text = format(d, "f")
    if "." not in text:
        text += ".0"
    return text + "%"


def fresh_quote(data_dir, paths, inst, now, cfg, replay):
    """latest_price, or a newer prices.json quote of the same Yahoo symbol (not after
    now, never in a replay): {bid, at, age_min, source}. None when there is no price."""
    lp = latest_price(data_dir, inst, now, cfg)
    if replay:
        return lp
    try:
        doc = load_json(paths["prices"]) or {}
        q = (doc.get("quotes") or {}).get(inst["key"]) if isinstance(doc, dict) else None
        if not isinstance(q, dict) or q.get("yahoo") != inst["yahoo"]:
            return lp
        at = parse_utc(q.get("at"))
        bid = q.get("bid")
        if at is None or at > now or isinstance(bid, bool) or not isinstance(bid, (int, float)) or bid <= 0:
            return lp
        if lp is not None and lp.get("at") is not None and at <= lp["at"]:
            return lp
        return {"bid": bid, "yahoo": bid, "at": at, "age_min": max(0.0, (now - at).total_seconds() / 60.0),
                "source": "prices.json", "contract": (lp or {}).get("contract")}
    except ValueError:
        return lp
