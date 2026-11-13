"""Small tools of the sample-market generator: dates, numbers, files. Standard library only."""

import datetime
import json
import os
import random
import struct
from decimal import Decimal, ROUND_HALF_UP

from .common import open_spans, utc_iso, utc_to_ts

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/fixtures
PACK = os.path.dirname(os.path.dirname(HERE))                        # the pack root
EXAMPLE_CONFIG = os.path.join(PACK, "fx-budget", "config.example.json")

FIRST_DAY = datetime.date(2026, 4, 6)      # the first FX day of 1h bars (Monday)
FIVE_MIN_DAY = datetime.date(2026, 6, 22)  # the first FX day of 5m bars (Monday)
LAST_DAY = datetime.date(2026, 7, 31)      # the last FX day (Friday)
CLOSE_FETCH = datetime.time(21, 30)        # the weekly fetch: Friday 21:30 UTC, after the close
H = datetime.timedelta(hours=1)
M5 = datetime.timedelta(minutes=5)
D = datetime.datetime

# a bare config for the hour templates: no user holidays, no early closes (the broker
# notice of 07-03 is in the sample config only; Yahoo's FX trades to Friday 21:00 UTC)
HOURS_CFG = {"holidays": {}, "early_closes": {}}


# ---------------------------------------------------------------- numbers

def rng(*key):
    """A seeded random generator from any key (same key, same numbers)."""
    seed = 0
    for ch in "|".join(str(k) for k in key):
        seed = (seed * 131 + ord(ch)) % 2147483647
    return random.Random(seed)


def rnd(x, places):
    """x rounded half up to places decimals (through its shortest text), as a float."""
    return float(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def f32(x):
    """x as Yahoo sends it: the nearest float32 value (2433.4 -> 2433.39990234375)."""
    return struct.unpack("f", struct.pack("f", float(x)))[0]


# ---------------------------------------------------------------- dates

def fx_days(first=FIRST_DAY, last=LAST_DAY):
    """Every weekday from first to last (both in): the FX days of the sample."""
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def slots(template, step, first=FIRST_DAY, last=LAST_DAY):
    """[(start, after_break)] of every bar inside the open spans of an hours template
    ("yahoo_fx" or "cme"), FX day by FX day; after_break is True for the first bar after
    a closed time (the weekend, the CME daily break, a holiday)."""
    out = []
    for d in fx_days(first, last):
        for a, b in open_spans(template, d, HOURS_CFG):
            t = a
            while t + step <= b:
                out.append((t, not out or out[-1][0] + step != t))
                t += step
    return out


def ts(dt):
    """naive UTC datetime -> Unix seconds."""
    return utc_to_ts(dt)


def iso(dt):
    """naive UTC datetime -> "YYYY-MM-DDTHH:MM:SSZ"."""
    return utc_iso(dt)


def fridays(first, last):
    """The weekly fetch times (Friday 21:30 UTC) of the weeks from first to last."""
    out, d = [], first
    while d <= last:
        if d.weekday() == 4:
            out.append(D.combine(d, CLOSE_FETCH))
        d += datetime.timedelta(days=1)
    return out


# ---------------------------------------------------------------- files

def write_bytes(path, data):
    """Write bytes, making the folders."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_text(path, text):
    """Write UTF-8 text with "\\n" line ends."""
    write_bytes(path, text.encode("utf-8"))


def write_json(path, doc):
    """Write a JSON file with sorted keys, indent 2 and a final newline."""
    write_text(path, json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def write_config(out):
    """fx-market/config.json: config.example.json with exactly two changes (06 5.2):
    account.balance 10000 and early_closes {"2026-07-03": "13:00"}."""
    with open(EXAMPLE_CONFIG, encoding="utf-8") as fh:
        text = fh.read()
    for old, new in (('"balance": 1000,', '"balance": 10000,'),
                     ('"early_closes": {},', '"early_closes": {"2026-07-03": "13:00"},')):
        if text.count(old) != 1:
            raise ValueError("config.example.json changed: %s not found once" % old)
        text = text.replace(old, new)
    path = os.path.join(out, "config.json")
    write_text(path, text)
    return path
