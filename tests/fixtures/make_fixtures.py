#!/usr/bin/env python3
"""Rebuild the made-up sample FX market in fx-market/. Standard library only, Python 3.8.

    cd tests/fixtures && python3 make_fixtures.py [--out DIR]

The output is the same on every run (seeded random numbers, fixed file times). The real
Yahoo, ForexFactory, CFTC and RSS formats are copied; the numbers are not (Yahoo's terms
and CME's ownership, README fine print). The parts live in makefix/: the price walks,
the designed story, the Yahoo answers and caches, the calendar, COT, feeds and README.
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from makefix import cot, econ, feeds, paths, readme, story, util, yahoo  # noqa: E402

OUT = os.path.join(HERE, "fx-market")
LIMIT_MB = 7.5


def build(out):
    """Write every file of the sample market into out. Returns the paths written."""
    for part in ("data", "raw"):
        shutil.rmtree(os.path.join(out, part), ignore_errors=True)
    walks = paths.build()
    story.apply(walks)
    gold = paths.contracts(walks)
    daily = paths.daily(walks, gold)
    files = yahoo.write_caches(out, walks, gold, daily)
    files += yahoo.write_raw(out, walks, gold)
    files.append(yahoo.write_rolls(out, gold))
    files += econ.write(out)
    files += cot.write(out)
    files += feeds.write(out)
    files.append(readme.write(out))
    files.append(util.write_config(out))
    return sorted(files)


def main(argv=None):
    """Build the market, print the file count and size; refuse a market over LIMIT_MB."""
    argv = list(sys.argv[1:] if argv is None else argv)
    out = OUT
    if argv[:1] == ["--out"] and len(argv) == 2:
        out = os.path.abspath(argv[1])
    elif argv:
        print("usage: make_fixtures.py [--out DIR]")
        return 2
    files = build(out)
    size = sum(os.path.getsize(f) for f in files) / 1e6
    if size > LIMIT_MB:
        print("The sample market is %.1f MB, over the %.1f MB limit." % (size, LIMIT_MB))
        for f in files:
            os.remove(f)
        for part in ("data", "raw"):
            shutil.rmtree(os.path.join(out, part), ignore_errors=True)
        return 1
    print("wrote %d files to %s" % (len(files), out))
    print("total %.1f MB" % size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
