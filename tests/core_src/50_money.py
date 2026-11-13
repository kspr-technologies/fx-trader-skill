# group: money

RATE_PAIRS = {"EUR": ("EURUSD", "mul"), "GBP": ("GBPUSD", "mul"), "AUD": ("AUDUSD", "mul"),
              "NZD": ("NZDUSD", "mul"), "JPY": ("USDJPY", "div"), "CHF": ("USDCHF", "div"),
              "CAD": ("USDCAD", "div")}
MIN_STOP_SPREADS = 3
GAP_ATR = {"fx": 1.5, "metal": 2.0, "energy": 3.0}
GAP_ATR_DAY = 3.0


class TooSmall(ValueError):
    """A refused size: the order would be below min_lot (tools exit 1, not 2). It keeps
    min_lot, min_lot_loss_usd, risk_limit_usd and decided_by for the words."""

    def __init__(self, message, min_lot=None, min_lot_loss_usd=None, risk_limit_usd=None,
                 decided_by=None):
        ValueError.__init__(self, message)
        self.min_lot = min_lot
        self.min_lot_loss_usd = min_lot_loss_usd
        self.risk_limit_usd = risk_limit_usd
        self.decided_by = decided_by


def _cents(d):
    """A Decimal amount of money, half up to cents, as a float (never -0.0)."""
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def _num_word(x):
    """A plain number for messages: 24 -> "24", 30.5 -> "30.5"."""
    return format(_dec_clean(x).normalize(), "f")


def _pct_word(x):
    """A percent for messages, at least 1 decimal: 1 -> "1.0", 0.25 -> "0.25"."""
    s = _num_word(x)
    return s if "." in s else s + ".0"


def _pccy(inst):
    """The instrument's profit currency (the quote when the row has none)."""
    return inst.get("profit_currency") or inst["quote"]


def _is_currency(code):
    """True for USD and the currencies of RATE_PAIRS (the ones the pack can turn into USD)."""
    return code == "USD" or code in RATE_PAIRS


def _rate_from_price(currency, price):
    """USD per 1 unit of currency from the price of its RATE_PAIRS pair (exact Decimal)."""
    p = _price_dec(price)
    return p if RATE_PAIRS[currency][1] == "mul" else Decimal(1) / p


def rate_entry(currency, price, at=None, age_h=0.0):
    """One row of the rates dict: {usd, pair, price, at, age_h} plus the exact Decimal
    copy "_usd" the money helpers use. usd is a float with 9 significant digits."""
    d = _rate_from_price(currency, price)
    return {"usd": float(format(d, ".9g")), "pair": RATE_PAIRS[currency][0],
            "price": float(_price_dec(price)), "at": at, "age_h": age_h, "_usd": d}


def rate_age_h(at_utc, now):
    """Market hours from at_utc to now, 1 decimal: the hours between minus each FX
    weekend (Friday 17:00 to Sunday 17:00 New York). Never below 0."""
    if now <= at_utc:
        return 0.0
    gone = datetime.timedelta(0)
    d = to_ny(at_utc).date() - datetime.timedelta(days=7)
    last = to_ny(now).date()
    while d <= last:
        if d.weekday() == 4:
            w0 = ny_to_utc(datetime.datetime.combine(d, datetime.time(17)))
            w1 = ny_to_utc(datetime.datetime.combine(d + datetime.timedelta(days=2), datetime.time(17)))
            lo, hi = max(at_utc, w0), min(now, w1)
            if hi > lo:
                gone += hi - lo
        d += datetime.timedelta(days=1)
    hours = Decimal(str(((now - at_utc) - gone).total_seconds())) / Decimal(3600)
    return float(max(hours, Decimal(0)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def fx_rates(data_dir, now, cfg):
    """USD value of one unit of each currency, from the cache only (latest_price, no
    network): {"USD": {...}, "JPY": {"usd", "pair", "price", "at", "age_h", "_usd"}}.
    A pair that is not in the config or has no cached price is left out."""
    out = {"USD": {"usd": 1.0, "pair": None, "price": None, "at": None, "age_h": 0.0,
                   "_usd": Decimal(1)}}
    rows = cfg.get("instruments") or {}
    for ccy, (key, how) in RATE_PAIRS.items():
        if key not in rows:
            continue
        lp = latest_price(data_dir, instrument(cfg, key), now, cfg)
        if not lp or lp.get("bid") is None:
            continue
        at = lp.get("at")
        at_dt = parse_utc(at) if isinstance(at, str) else at   # (check) str or datetime
        age = rate_age_h(at_dt, now) if at_dt is not None else 0.0
        out[ccy] = rate_entry(ccy, lp["bid"], utc_iso(at_dt), age)
    return out


def usd_per_unit(currency, rates, cfg=None):
    """USD for 1 unit of currency, a Decimal. With cfg, a rate older than
    fx_rates_max_age_hours (market hours) is refused."""
    if currency == "USD":
        return Decimal(1)
    if not _is_currency(currency):
        raise ValueError("%s is not a currency. Set profit_currency and margin_currency to USD "
                         "or a currency code." % (currency,))
    row = (rates or {}).get(currency)
    pair = RATE_PAIRS[currency][0]
    if not row:
        raise ValueError("No USD rate for %s. It comes from %s. Keep %s in your config and run "
                         "once with Yahoo on (not --offline)." % (currency, pair, pair))
    if cfg is not None:
        limit = cfg.get("fx_rates_max_age_hours", 24)
        age = row.get("age_h") or 0.0
        if _dec_clean(age) > _dec_clean(limit):
            raise ValueError("The USD rate for %s is %.1f hours old (your limit is %s). Run once "
                             "with Yahoo on to refresh it." % (currency, age, _num_word(limit)))
    if row.get("_usd") is not None:
        return row["_usd"]
    if row.get("price") is not None:
        return _rate_from_price(currency, row["price"])
    return _dec_clean(row["usd"])


def _base_has_rate(inst):
    """True for an FX pair whose profit currency is the quote and whose base has a USD
    rate (USD itself or a RATE_PAIRS currency): its loss rate moves with the stop."""
    return (inst.get("kind") == "fx" and _pccy(inst) == inst.get("quote")
            and _is_currency(inst.get("base")))


def loss_usd_per_unit(inst, stop_loss, rates, cfg=None):
    """USD for 1 unit of the profit currency for the LOSS at the stop, the safer rate:
    an FX pair takes max(today, base rate / stop) (USD-first pairs: 1 / stop)."""
    ccy = _pccy(inst)
    if ccy == "USD":
        return Decimal(1)
    today = usd_per_unit(ccy, rates, cfg)
    if not _base_has_rate(inst):
        return today
    return max(today, usd_per_unit(inst["base"], rates, cfg) / _price_dec(stop_loss))


def reward_usd_per_unit(inst, take_profit, rates, cfg=None):
    """The mirror of loss_usd_per_unit for the reward: the smaller of today's rate and
    base rate / take_profit on an FX pair (the safe side)."""
    ccy = _pccy(inst)
    if ccy == "USD":
        return Decimal(1)
    today = usd_per_unit(ccy, rates, cfg)
    if not _base_has_rate(inst):
        return today
    return min(today, usd_per_unit(inst["base"], rates, cfg) / _price_dec(take_profit))


def rate_symbols(inst, cfg):
    """The instrument keys whose prices this trade needs for USD rates (profit currency,
    margin currency, the base of an FX pair): GBPJPY -> ["USDJPY", "GBPUSD"]."""
    out = []
    codes = [_pccy(inst), inst.get("margin_currency") or inst.get("base")]
    if inst.get("kind") == "fx":
        codes.append(inst.get("base"))
    for code in codes:
        if code in RATE_PAIRS and RATE_PAIRS[code][0] not in out:
            out.append(RATE_PAIRS[code][0])
    return out


def usd_per_point_per_lot(inst, rates, cfg=None):
    """USD a 1-point move makes on 1 lot (MetaTrader's tick value when tick = point):
    point x contract_size x USD per profit unit, half up to 4 decimals."""
    d = (_dec_clean(inst["point"]) * _dec_clean(inst["contract_size"])
         * usd_per_unit(_pccy(inst), rates, cfg))
    return float(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _swap_free(cfg):
    """True when the account is swap-free (D20)."""
    return bool(((cfg or {}).get("account") or {}).get("swap_free"))


def _margin_ccy_usd(inst, price, rates, cfg):
    """USD for 1 unit of the margin currency (part 04 5.1 step 2): the order's own price
    when the rate pair is this instrument; price x the profit rate for a commodity base."""
    mc = inst.get("margin_currency") or inst.get("base")
    if mc == "USD":
        return Decimal(1)
    if mc in RATE_PAIRS and RATE_PAIRS[mc][0] == inst.get("key") and price is not None:
        return _rate_from_price(mc, price)
    if _is_currency(mc) or mc != inst.get("base"):
        return usd_per_unit(mc, rates, cfg)
    if price is None:
        raise ValueError("%s: the margin is in %s. The tool needs a price to count it."
                         % (inst.get("key"), mc))   # (check) words
    return _price_dec(price) * usd_per_unit(_pccy(inst), rates, cfg)


def _commission(inst, lots):
    """Commission for both sides, unrounded Decimal: commission_per_lot_side x lots x 2."""
    return _dec_clean(inst.get("commission_per_lot_side") or 0) * _dec_clean(lots) * 2


def _spread(inst, lots, rates, cfg):
    """The spread's money, unrounded Decimal: spread_points x point x contract_size x
    lots x USD per profit unit (words only: it is already inside the stop distance)."""
    return (_dec_clean(inst["spread_points"]) * _dec_clean(inst["point"])
            * _dec_clean(inst["contract_size"]) * _dec_clean(lots)
            * usd_per_unit(_pccy(inst), rates, cfg))


def _swap(inst, side, lots, nights, rates, cfg, price):
    """The swap for lots over nights (weighted, 6.3), unrounded Decimal, signed as in
    MetaTrader (plus = paid to you). 0 on a swap-free account (D20)."""
    side_word(side)
    if _swap_free(cfg):
        return Decimal(0)
    key = inst.get("key")
    v = _dec_clean(inst.get("swap_long" if side == "buy" else "swap_short") or 0)
    mode = inst.get("swap_mode") or "points"
    if mode == "disabled":
        return Decimal(0)
    if mode == "points":
        per = v * _dec_clean(inst["point"]) * _dec_clean(inst["contract_size"])
        per *= usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "deposit":
        per = v
    elif mode == "profit":
        per = v * usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "base":
        base = inst.get("base")
        if _is_currency(base):
            per = v * usd_per_unit(base, rates, cfg)
        elif price is None:
            raise ValueError("%s swaps are in %s. The tool needs a price to count them."
                             % (key, base))   # (check) words
        else:
            per = v * _price_dec(price) * usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "margin":
        per = v * _margin_ccy_usd(inst, price, rates, cfg)
    elif mode == "interest":
        if price is None:
            raise ValueError("%s swaps are a yearly percent. The tool needs a price to count them."
                             % key)
        per = _price_dec(price) * _dec_clean(inst["contract_size"]) * v / 100 / 360
        per *= usd_per_unit(_pccy(inst), rates, cfg)
    else:
        raise ValueError('%s: swap_mode "%s" is not known. Use points, deposit, profit, base, '
                         'margin, interest or disabled.' % (key, mode))   # (check) words
    return per * _dec_clean(lots) * _dec_clean(nights)


def _fee(inst, lots, nights, cfg):
    """The swap-free fee (D20), unrounded Decimal: fee_per_lot x lots x the nights after
    swap_free_grace_nights. 0 when the account is not swap-free or has no fee."""
    fee = inst.get("swap_free_fee_per_lot")
    if not _swap_free(cfg) or fee is None:
        return Decimal(0)
    paid = max(Decimal(0), _dec_clean(nights) - _dec_clean(inst.get("swap_free_grace_nights") or 0))
    return _dec_clean(fee) * _dec_clean(lots) * paid


def commission_usd(inst, lots):
    """Commission for both sides in USD, half up to cents (E1: 3.00 x 0.68 x 2 = 4.08)."""
    return _cents(_commission(inst, lots))


def spread_usd(inst, lots, rates, cfg=None):
    """The spread's money in USD, half up to cents (words and trade_costs only)."""
    return _cents(_spread(inst, lots, rates, cfg))


def swap_usd(inst, side, lots, nights, rates, cfg, price=None):
    """The swap in USD, signed (plus = paid to you), half up to cents; 0.0 swap-free.
    price (the entry) is needed for the "interest" mode."""
    return _cents(_swap(inst, side, lots, nights, rates, cfg, price))


def swap_free_fee_usd(inst, lots, nights, cfg):
    """The swap-free fee in USD after the grace nights, half up to cents (always a cost)."""
    return _cents(_fee(inst, lots, nights, cfg))



def swap_nights(open_utc, close_utc, inst, cfg):
    """Rollovers held (each Monday to Friday 17:00 New York with open < t <= close),
    the swap_triple_day one counted 3 ("none": every night 1). Naive UTC times."""
    if getattr(open_utc, "tzinfo", None) is not None or getattr(close_utc, "tzinfo", None) is not None:
        raise ValueError("Times must be naive UTC datetimes.")   # (check) words
    if close_utc <= open_utc:
        return 0
    triple = inst.get("swap_triple_day") or "wednesday"   # (check) default
    n = 0
    d = to_ny(open_utc).date()
    last = to_ny(close_utc).date() + datetime.timedelta(days=1)
    while d <= last:
        if d.weekday() < 5:
            t = ny_to_utc(datetime.datetime.combine(d, datetime.time(17)))
            if open_utc < t <= close_utc:
                n += 3 if triple != "none" and FULL_DAY_NAMES[d.weekday()].lower() == triple else 1
        d += datetime.timedelta(days=1)
    return n


def _costs(inst, side, lots, nights, rates, cfg, price):
    """The costs inside a max loss, unrounded Decimal: commission + swap-free fee + the
    swap you pay (a swap credit counts 0). The spread is not here (it is in the price)."""
    return (_commission(inst, lots) + _fee(inst, lots, nights, cfg)
            + max(Decimal(0), -_swap(inst, side, lots, nights, rates, cfg, price)))


def costs_usd(inst, side, lots, nights, rates, cfg, price):
    """orders.json plan.costs_usd: _costs for the order's lots, half up to cents."""
    return _cents(_costs(inst, side, lots, nights, rates, cfg, price))


def trade_costs(inst, side, lots, nights, rates, cfg, price=None):
    """{spread, commission, swap, fee, total} in USD, each half up to cents; total =
    spread + commission + fee - swap on the unrounded cores, rounded once."""
    sp = _spread(inst, lots, rates, cfg)
    co = _commission(inst, lots)
    sw = _swap(inst, side, lots, nights, rates, cfg, price)
    fe = _fee(inst, lots, nights, cfg)
    return {"spread": _cents(sp), "commission": _cents(co), "swap": _cents(sw),
            "fee": _cents(fe), "total": _cents(sp + co + fe - sw)}


def _lots_dec(lots):
    """Lots as a cleaned Decimal; ValueError when not above 0."""
    d = _dec_clean(lots)
    if d <= 0:
        raise ValueError("Lots must be above 0.")
    return d


def _check_stop_side(side, entry, stop_loss):
    """entry and stop_loss as Decimals; ValueError when the stop is on the wrong side."""
    side_word(side)
    e, s = _price_dec(entry), _price_dec(stop_loss)
    if side == "buy" and not s < e:
        raise ValueError("A buy's stop must be below the entry.")
    if side == "sell" and not s > e:
        raise ValueError("A sell's stop must be above the entry.")
    return e, s


def max_loss_core(inst, side, entry, stop_loss, nights, rates, cfg):
    """The max loss of 1 lot at the stop, all costs in, unrounded Decimal (6.7 on the
    cores; the swap with price=entry). max_loss_usd is this x lots, rounded once."""
    e, s = _check_stop_side(side, entry, stop_loss)
    price_loss = abs(e - s) * _dec_clean(inst["contract_size"]) * loss_usd_per_unit(inst, s, rates, cfg)
    return price_loss + _costs(inst, side, 1, nights, rates, cfg, e)


def max_loss_usd(inst, side, lots, entry, stop_loss, nights, rates, cfg):
    """The loss in USD if the stop fills at its price, all costs in (the spread is in
    the price, a swap credit counts 0), half up to cents once."""
    return _cents(_lots_dec(lots) * max_loss_core(inst, side, entry, stop_loss, nights, rates, cfg))


def net_core(inst, side, entry, stop_loss, take_profit, nights, rates, cfg):
    """Reward after costs over the max loss, per 1 lot, unrounded Decimal (6.8)."""
    e, s = _check_stop_side(side, entry, stop_loss)
    t = _price_dec(take_profit)
    if side == "buy" and not t > e:
        raise ValueError("A buy's target must be above the entry.")
    if side == "sell" and not t < e:
        raise ValueError("A sell's target must be below the entry.")
    reward = abs(t - e) * _dec_clean(inst["contract_size"]) * reward_usd_per_unit(inst, t, rates, cfg)
    reward -= _costs(inst, side, 1, nights, rates, cfg, e)
    return reward / max_loss_core(inst, side, e, s, nights, rates, cfg)


def reward_risk_net(inst, side, entry, stop_loss, take_profit, nights, rates, cfg):
    """Reward after costs over the max loss, per 1 lot, half up to 2 decimals."""
    d = net_core(inst, side, entry, stop_loss, take_profit, nights, rates, cfg)
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def r_multiple(result_usd, max_loss_usd):
    """result / max loss, half up to 2 decimals; None when max_loss_usd <= 0."""
    m = _dec_clean(max_loss_usd)
    if m <= 0:
        return None
    return float((_dec_clean(result_usd) / m).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def _pl_core(inst, side, entry, exit_price, nights, rates, cfg):
    """1 lot's P/L after the swap and the swap-free fee but before commission,
    unrounded Decimal. A USD-first pair turns it into USD at 1 / exit_price."""
    side_word(side)
    e, x = _price_dec(entry), _price_dec(exit_price)
    move = (x - e) if side == "buy" else (e - x)
    ccy = _pccy(inst)
    if inst.get("kind") == "fx" and inst.get("base") == "USD" and ccy == inst.get("quote"):
        rate = Decimal(1) / x
    else:
        rate = usd_per_unit(ccy, rates, cfg)
    pl = move * _dec_clean(inst["contract_size"]) * rate
    return pl + _swap(inst, side, 1, nights, rates, cfg, e) - _fee(inst, 1, nights, cfg)


def result_core(inst, side, entry, exit_price, nights, rates, cfg):
    """A closed trade of 1 lot after all costs, unrounded Decimal (6.10 on the cores;
    the swap with price=entry). exit_price: the bid for a buy, the ask for a sell."""
    return _pl_core(inst, side, entry, exit_price, nights, rates, cfg) - _commission(inst, 1)


def result_usd(inst, side, lots, entry, exit_price, nights, rates, cfg):
    """A closed trade in USD after all costs, signed (minus = a loss), half up to cents once."""
    return _cents(_lots_dec(lots) * result_core(inst, side, entry, exit_price, nights, rates, cfg))


def position_pl_usd(position, bid, inst, rates, cfg):
    """An open position's P/L in USD at the latest Yahoo bid (plan scale): a sell closes
    at the ask. The swap and fee so far are in; the commission is booked apart."""
    side = position["side"]
    exit_price = bid if side == "buy" else ask_of(bid, inst)
    nights = position.get("swap_nights") or 0
    lots = _lots_dec(position["lots"])
    return _cents(lots * _pl_core(inst, side, position["entry"], exit_price, nights, rates, cfg))


def _margin(inst, lots, price, side, cfg, rates):
    """The margin of lots in USD, unrounded Decimal (5.1, per calc_mode). price is the
    order's entry: a buy's ask, a sell's bid."""
    side_word(side)
    n = _lots_dec(lots)
    p = _price_dec(price)
    key = inst.get("key")
    mode = inst.get("calc_mode")
    lev = _dec_clean(inst.get("leverage") or ((cfg or {}).get("account") or {}).get("leverage") or 100)
    im = inst.get("initial_margin")
    im = None if im is None else _dec_clean(im)
    size = _dec_clean(inst["contract_size"])
    if mode not in ("forex", "forex_no_leverage", "cfd", "cfd_leverage", "futures"):
        raise ValueError('%s: calc_mode "%s" is not known. Use forex, forex_no_leverage, cfd, '
                         'cfd_leverage or futures.' % (key, mode))
    if mode == "futures" and im is None:
        raise ValueError("%s uses calc_mode futures. Copy its Initial margin from MetaTrader "
                         "into initial_margin." % key)
    if mode == "forex":
        m = n * (im if im is not None else size) / lev
    elif mode == "forex_no_leverage":
        m = n * (im if im is not None else size)
    elif mode == "cfd":
        m = n * im if im is not None else n * size * p
    elif mode == "cfd_leverage":
        m = n * im / lev if im is not None else n * size * p / lev
    else:
        m = n * im
    m *= _dec_clean(inst.get("margin_rate") or 1)
    return m * _margin_ccy_usd(inst, p, rates, cfg)


def margin_usd(inst, lots, price, side, cfg, rates):
    """The margin of lots in USD per calc_mode, at the order's entry price, x margin_rate,
    in USD, half up to cents. Each position counts its full margin (no hedge credit)."""
    return _cents(_margin(inst, lots, price, side, cfg, rates))


def margin_level_pct(equity, used_margin):
    """equity / used margin x 100, half up to 2 decimals; None when no margin is in use."""
    u = _dec_clean(used_margin)
    if u <= 0:
        return None
    return float((_dec_clean(equity) / u * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def margin_state(equity, used_margin, cfg):
    """{level_pct, call_equity, stop_out_equity, free_margin, state}: state is "stop_out"
    at or below account.stop_out_pct, "call" at or below margin_call_pct, else "ok"."""
    acc = (cfg or {}).get("account") or {}
    call_pct = _dec_clean(acc.get("margin_call_pct", 100))
    stop_pct = _dec_clean(acc.get("stop_out_pct", 50))
    u, e = _dec_clean(used_margin), _dec_clean(equity)
    level = margin_level_pct(e, u)
    state = "ok"
    if u > 0:
        exact = e / u * 100
        if exact <= stop_pct:
            state = "stop_out"
        elif exact <= call_pct:
            state = "call"
    return {"level_pct": level, "call_equity": _cents(u * call_pct / 100),
            "stop_out_equity": _cents(u * stop_pct / 100), "free_margin": _cents(e - u),
            "state": state}


def _floor_lots(raw, inst):
    """Raw lots DOWN to a whole number of lot_step (cleaned to 9 decimals first, D11).
    0 or less gives 0. No min_lot check here."""
    step = _dec_clean(inst["lot_step"])
    if raw <= 0:
        return Decimal(0)
    steps = (raw / step).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    return (steps.to_integral_value(rounding=ROUND_FLOOR) * step).quantize(step.normalize())


def _lot_word(d):
    """Lots as plain text for files and messages: Decimal 0.68 -> "0.68", 100 -> "100"."""
    return format(_dec_clean(d).normalize(), "f")


def _risk_words(inst, min_lot_loss, risk_usd):
    """The 4.4 "by risk" refusal, without the percent line."""
    return ("%s lot is the smallest order for %s. At this stop it loses %s. Your max loss is "
            "%s. So this trade is too big for your account. Do not move the stop closer to "
            "make it fit." % (_lot_word(inst["min_lot"]), inst.get("key"), money(min_lot_loss),
                              money(risk_usd)))


def _loss_per_lot(entry, stop_loss, inst, rates, extra_per_lot_usd, cfg):
    """D13 loss of 1 lot at the stop plus the costs per lot, unrounded Decimal."""
    e, s = _price_dec(entry), _price_dec(stop_loss)
    if e == s:
        raise ValueError("The stop is at the entry price. There is no risk to size.")
    return (abs(e - s) * _dec_clean(inst["contract_size"]) * loss_usd_per_unit(inst, s, rates, cfg)
            + _dec_clean(extra_per_lot_usd))


def lots_for_risk(risk_usd, entry, stop_loss, inst, rates, extra_per_lot_usd=0, cfg=None):
    """The lots that lose at most risk_usd at the stop (D13 loss per unit plus the costs
    per lot), DOWN to lot_step. TooSmall below min_lot; max_lot is not applied here."""
    risk = _dec_clean(risk_usd)
    if risk <= 0:
        raise ValueError("The max loss must be above $0.")
    per_lot = _loss_per_lot(entry, stop_loss, inst, rates, extra_per_lot_usd, cfg)
    lots = _floor_lots(risk / per_lot, inst)
    min_lot = _dec_clean(inst["min_lot"])
    if lots < min_lot:
        loss = min_lot * per_lot
        raise TooSmall(_risk_words(inst, loss, risk), min_lot=float(min_lot),
                       min_lot_loss_usd=_cents(loss), risk_limit_usd=_cents(risk), decided_by=None)
    return lots


SIZE_LIMITS = ("risk_pct", "risk_usd", "max_lot", "margin_cap", "free_margin")


def _risk_money(balance, cfg, risk_pct, risk_usd):
    """(pct, cap) for size_order (D22): the flags may only lower the config limits.
    cap is None when no dollar limit is set."""
    risk = (cfg or {}).get("risk") or {}
    pct = _dec_clean(risk.get("max_risk_per_trade_pct", 1.0))
    if risk_pct is not None:
        if _dec_clean(risk_pct) > pct:
            raise ValueError("Your config allows at most %s%% a trade. To go higher, change "
                             "risk.max_risk_per_trade_pct in fx/config.json." % _pct_word(pct))
        pct = _dec_clean(risk_pct)
    cap = risk.get("max_risk_per_trade_usd")
    cap = None if cap is None else _dec_clean(cap)
    if risk_usd is not None:
        if cap is not None and _dec_clean(risk_usd) > cap:
            raise ValueError("Your config allows at most %s a trade. To go higher, change "
                             "risk.max_risk_per_trade_usd in fx/config.json." % money(cap))
        cap = _dec_clean(risk_usd)
    return pct, cap


def size_order(inst, side, entry, stop_loss, nights, balance, used_margin_usd, rates, cfg,
               risk_pct=None, risk_usd=None, equity=None):
    """The lots for one open order: the smallest of the risk limits (D22), max_lot, the
    margin cap and free margin, with decided_by. TooSmall below min_lot (exit 1)."""
    e, s = _check_stop_side(side, entry, stop_loss)
    bal, used = _dec_clean(balance), _dec_clean(used_margin_usd)
    pct, cap = _risk_money(bal, cfg, risk_pct, risk_usd)
    by_pct = bal * pct / 100
    risk_limit = by_pct if cap is None else min(by_pct, cap)
    if risk_limit <= 0:
        raise ValueError("The max loss must be above $0.")
    extra = _costs(inst, side, 1, nights, rates, cfg, e)
    per_lot = _loss_per_lot(e, s, inst, rates, extra, cfg)
    per_margin = _margin(inst, 1, e, side, cfg, rates)
    cap_pct = _dec_clean(((cfg or {}).get("risk") or {}).get("max_margin_used_pct", 30))
    room = bal * cap_pct / 100 - used
    free = (bal if equity is None else _dec_clean(equity)) - used
    limits = {"risk_pct": _floor_lots(by_pct / per_lot, inst),
              "risk_usd": None if cap is None else _floor_lots(cap / per_lot, inst),
              "max_lot": _dec_clean(inst["max_lot"]),
              "margin_cap": _floor_lots(room / per_margin, inst),
              "free_margin": _floor_lots(free / per_margin, inst)}
    decided = None
    for name in SIZE_LIMITS:
        if limits[name] is not None and (decided is None or limits[name] < limits[decided]):
            decided = name
    lots = limits[decided]
    min_lot = _dec_clean(inst["min_lot"])
    min_loss = min_lot * per_lot
    if lots < min_lot:
        if decided in ("risk_pct", "risk_usd"):
            words = _risk_words(inst, min_loss, risk_limit)
            if decided == "risk_pct":
                words += " At %s%% a trade you need about %s for this trade." % (
                    _pct_word(pct), money(min_loss / pct * 100))
        elif decided == "margin_cap":
            words = ("%s lot of %s needs %s margin. You have room for %s (%s%% of your balance, "
                     "minus margin in use). So this trade does not fit."
                     % (_lot_word(min_lot), inst.get("key"), money(min_lot * per_margin),
                        money(max(room, Decimal(0))), _num_word(cap_pct)))
        else:
            words = ("%s lot of %s needs %s margin. Your free margin is %s. So this trade does "
                     "not fit." % (_lot_word(min_lot), inst.get("key"), money(min_lot * per_margin),
                                   money(max(free, Decimal(0)))))   # (check) words
        raise TooSmall(words, min_lot=float(min_lot), min_lot_loss_usd=_cents(min_loss),
                       risk_limit_usd=_cents(risk_limit), decided_by=decided)
    return {"lots": lots, "decided_by": decided, "risk_limit_usd": _cents(risk_limit),
            "max_loss_usd": _cents(lots * per_lot), "loss_per_lot_usd": _cents(per_lot),
            "min_lot_loss_usd": _cents(min_loss), "margin_usd": _cents(lots * per_margin),
            "margin_per_lot_usd": _cents(per_margin),
            "limits": dict((k, None if v is None else _lot_word(v)) for k, v in limits.items())}


def _points_word(n):
    """Whole points for messages: "1 point", "1,913 points"."""
    return "%s %s" % (format(n, ","), "point" if n == 1 else "points")


def stops_problems(side, entry_type, entry, stop_loss, take_profit, bid_now, inst):
    """The MetaTrader stops-level and side checks (R2 7e) as plain lines; empty when all
    is well. Market orders test against the bid and ask now, pending ones against entry."""
    side_word(side)
    if entry_type not in ("market", "limit", "stop"):
        raise ValueError('The order type must be "market", "limit" or "stop".')   # (check) words
    bid = _price_dec(bid_now)
    ask = _price_dec(ask_of(bid, inst))
    point = _dec_clean(inst["point"])
    lvl_pts = int(_dec_clean(inst.get("stops_level_points") or 0))
    lvl = lvl_pts * point
    out = []

    def check(dist, what):
        if dist <= 0:
            out.append("Your %s is on the wrong side of the price now." % what)
        elif dist < lvl:
            out.append("Your %s is too close. MetaTrader needs it at least %s (%s) from the price. "
                       "It is %s away." % (what, _points_word(lvl_pts), price_text(lvl, inst),
                                           _points_word(abs(to_points(dist, inst)))))

    buy = side == "buy"
    if entry_type == "market":
        ref_sl, ref_tp = (bid, bid) if buy else (ask, ask)
    else:
        p = _price_dec(entry)
        ref_sl = ref_tp = p
        dist = {("buy", "limit"): ask - p, ("sell", "limit"): p - bid,
                ("buy", "stop"): p - ask, ("sell", "stop"): bid - p}[(side, entry_type)]
        above = (side, entry_type) in (("buy", "stop"), ("sell", "limit"))
        if dist <= 0:
            out.append("A %s %s order must be %s the price now (%s %s)." % (
                side, entry_type, "above" if above else "below", "ask" if buy else "bid",
                price_text(ask if buy else bid, inst)))
        elif dist < lvl:   # (check) words
            out.append("Your order price is too close. MetaTrader needs it at least %s (%s) from "
                       "the price. It is %s away." % (_points_word(lvl_pts), price_text(lvl, inst),
                                                      _points_word(abs(to_points(dist, inst)))))
    if stop_loss is not None:
        s = _price_dec(stop_loss)
        check(ref_sl - s if buy else s - ref_sl, "stop")
    if take_profit is not None:
        t = _price_dec(take_profit)
        check(t - ref_tp if buy else ref_tp - t, "target")
    return out


def bar_hits(side, bar, stop_loss, take_profit, inst, margin_points=0):
    """D5 on one Yahoo (bid) bar: "stop", "target", "both" (order unknown: callers count
    it as the stop) or None. A buy's levels fire on the bid, a sell's on the ask. A bar
    that OPENS past a level hit that level first."""
    side_word(side)
    s = _dec_clean(spread_price(inst))
    m = _dec_clean(margin_points) * _dec_clean(inst["point"])
    o, h, lo = _dec_clean(bar["open"]), _dec_clean(bar["high"]), _dec_clean(bar["low"])
    sl = None if stop_loss is None else _dec_clean(stop_loss)
    tp = None if take_profit is None else _dec_clean(take_profit)
    if side == "buy":
        if sl is not None and o <= sl:
            return "stop"
        if tp is not None and o >= tp:
            return "target"
        stop = sl is not None and lo <= sl + m
        target = tp is not None and h >= tp
    else:
        if sl is not None and o + s >= sl:
            return "stop"
        if tp is not None and o + s <= tp:
            return "target"
        stop = sl is not None and h + s >= sl - m
        target = tp is not None and lo + s <= tp
    if stop and target:
        return "both"
    return "stop" if stop else ("target" if target else None)


def pending_fill(side, entry_type, bar, entry, inst):
    """The fill price of an entry order in one bar, or None: a stop or limit fills at
    its price, or at the open when the bar opens past it; market fills at the open
    (a buy at the ask). In the fill bar only a stop hit counts (the safe side)."""
    side_word(side)
    o = _dec_clean(bar["open"])
    if entry_type == "market":
        return ask_of(o, inst) if side == "buy" else round_price(o, inst)
    if entry_type not in ("stop", "limit"):
        raise ValueError('The order type must be "market", "limit" or "stop".')   # (check) words
    s = _dec_clean(spread_price(inst))
    p = _price_dec(entry)
    h, lo = _dec_clean(bar["high"]), _dec_clean(bar["low"])
    if side == "buy" and entry_type == "stop":
        fill = (o + s) if o + s >= p else (p if h + s >= p else None)
    elif side == "buy":
        fill = (o + s) if o + s <= p else (p if lo + s <= p else None)
    elif entry_type == "stop":
        fill = o if o <= p else (p if lo <= p else None)
    else:
        fill = o if o >= p else (p if h >= p else None)
    return None if fill is None else round_price(fill, inst)


def gap_fill(side, bar_open, stop_loss, inst):
    """The fill when a bar opens past the stop (it becomes a market order): a buy at the
    open bid, a sell at the open ask. None when the open is not past the stop."""
    side_word(side)
    o, sl = _dec_clean(bar_open), _dec_clean(stop_loss)
    if side == "buy":
        return round_price(o, inst) if o <= sl else None
    a = o + _dec_clean(spread_price(inst))
    return round_price(a, inst) if a >= sl else None
