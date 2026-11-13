# group: instruments and rounding


def _dec_clean(x):
    """x as a Decimal with float noise under a billionth cleaned (half up), so
    1.1234999999999 counts as 1.1235. ValueError "not a number: x" when not finite."""
    if isinstance(x, bool) or x is None:
        raise ValueError("not a number: %r" % (x,))
    try:
        d = Decimal(str(x))
        if not d.is_finite():
            raise ValueError("not a number: %r" % (x,))
        return d.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError("not a number: %r" % (x,))


def _price_dec(x):
    """A price as a cleaned Decimal; ValueError when it is not above 0."""
    d = _dec_clean(x)
    if d <= 0:
        raise ValueError("A price must be above 0.")
    return d


def instrument(cfg, key):
    """The instrument row cfg["instruments"][key] as a NEW dict, with the defaults
    filled (key, broker_symbol, point, tick_size, pip ...) and its lot and tick checks."""
    rows = cfg.get("instruments") or {}
    if key not in rows:
        near = difflib.get_close_matches(str(key), list(rows), n=1, cutoff=0.6)
        such = near[0] if near else ("XAUUSD" if "XAUUSD" in rows or not rows else sorted(rows)[0])
        raise ValueError('Unknown instrument "%s". Use a key from your config, such as %s. '
                         'Put your broker\'s name in "broker_symbol".' % (key, such))
    inst = copy.deepcopy(rows[key])
    inst["key"] = key
    if not inst.get("broker_symbol"):
        inst["broker_symbol"] = key
    digits = int(inst["digits"])
    point = Decimal(1).scaleb(-digits)
    if inst.get("point") is not None and _dec_clean(inst["point"]) != point:
        raise ValueError("%s: point %s does not match digits %s. Copy both from MetaTrader."
                         % (key, inst["point"], digits))
    inst["point"] = float(point)
    if inst.get("tick_size") is None:
        inst["tick_size"] = float(point)
    if inst.get("pip") is None:
        if inst.get("kind") == "fx":
            inst["pip"] = 0.01 if inst.get("quote") == "JPY" else 0.0001
        else:
            inst["pip"] = None
    for name in ("leverage", "initial_margin", "swap_free_fee_per_lot", "futures"):
        inst.setdefault(name, None)
    for name in ("margin_rate", "yahoo_to_broker"):
        if inst.get(name) is None:
            inst[name] = 1.0
    for name in ("swap_free_grace_nights", "commission_per_lot_side", "stops_level_points"):
        if inst.get(name) is None:
            inst[name] = 0
    sizes = [_dec_clean(inst[n]) for n in ("contract_size", "lot_step", "min_lot", "max_lot")]
    if min(sizes) <= 0 or sizes[2] > sizes[3]:
        raise ValueError("%s: contract_size, min_lot, max_lot and lot_step must be above 0." % key)
    if sizes[2] % sizes[1] != 0:
        raise ValueError("%s: min_lot %s is not a whole number of lot_step %s."
                         % (key, inst["min_lot"], inst["lot_step"]))
    tick = _dec_clean(inst["tick_size"])
    if tick <= 0 or tick % point != 0:
        raise ValueError("%s: tick_size %s does not fit digits %s." % (key, inst["tick_size"], digits))
    return inst


def _to_tick(d, inst, rounding):
    """A cleaned Decimal price to a whole number of ticks (the given rounding), then
    quantized to the instrument's digits, as a float."""
    tick = _dec_clean(inst["tick_size"])
    q = (d / tick).to_integral_value(rounding=rounding) * tick
    return float(q.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP))


def round_price(price, inst):
    """A price to the nearest tick, half up (entries, fills, plain levels):
    EURUSD 1.124905 -> 1.12491, XAUUSD 4134.175 -> 4134.18."""
    return _to_tick(_price_dec(price), inst, ROUND_HALF_UP)


def round_away(price, entry, inst):
    """A stop or a target to the tick AWAY from the entry, so the risk never shrinks
    and the target never comes closer: below the entry down, above it up."""
    p = _price_dec(price)
    e = _price_dec(entry)
    if p == e:
        raise ValueError("A stop or a target cannot be at the entry price.")
    return _to_tick(p, inst, ROUND_FLOOR if p < e else ROUND_CEILING)


def to_points(distance, inst):
    """A price distance in whole points, half up, the sign kept:
    to_points(-0.00141, EURUSD) = -141."""
    d = _dec_clean(distance) / _dec_clean(inst["point"])
    return int(d.to_integral_value(rounding=ROUND_HALF_UP))


def from_points(points, inst):
    """Points back to a price distance, quantized to digits: from_points(9, XAUUSD) = 0.09."""
    d = _dec_clean(points) * _dec_clean(inst["point"])
    return float(d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP))


def pips_text(distance, inst):
    """A distance in plain words, sign dropped: "14.1 pips (141 points)" for FX,
    "174.51 (17,451 points)" for gold and the other non-FX rows (no pip word)."""
    d = abs(_dec_clean(distance))
    pts = abs(to_points(d, inst))
    pts_word = "%s %s" % (format(pts, ","), "point" if pts == 1 else "points")
    if inst.get("pip"):
        pips = (d / _dec_clean(inst["pip"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return "%s %s (%s)" % (format(pips, ",f"), "pip" if pips == 1 else "pips", pts_word)
    q = d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP)
    return "%s (%s)" % (format(q, ",f"), pts_word)


def spread_price(inst):
    """The table spread as a price distance: spread_points x point, exact (not rounded
    to the tick), on the plan scale. EURUSD 1 point = 0.00001, XAUUSD 9 points = 0.09."""
    return float(_dec_clean(inst["spread_points"]) * _dec_clean(inst["point"]))


def ask_of(bid, inst):
    """The ask from a Yahoo bid (D5): bid + the table spread, to the nearest tick."""
    return round_price(_price_dec(bid) + Decimal(str(spread_price(inst))), inst)
