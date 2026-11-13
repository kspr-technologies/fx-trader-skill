#!/usr/bin/env python3
"""fx_core: the canonical helpers of fx-trader-skill. Standard library only, Python 3.8.

Every skill script stays self-contained, so it COPIES the helpers it uses from this
file, verbatim: same name, same body, same docstring. Never edit a copied helper. If
you need other behaviour, write a new function with a new name in your script.
tests/test_repo.py checks that each copy is the same text as here. Helpers raise
ValueError with a plain message; a script wraps that in its own error class.

Times are naive datetimes. UTC inside; Karachi time (PKT) is UTC+5 all year (no
daylight saving) and is what people see. New York, London, Sydney and Tokyo times come
from hand-made daylight saving rules: no time zone files are used, and the machine's
own zone never matters.

Prices come from Yahoo Finance only: at least PACE_SECONDS between two requests, the
honest User-Agent UA, and the cache in fx/data/yahoo/. Never with --offline or --now.
The pack never places an order.
"""

# Imports used below (copy the ones your helpers need):
import copy
import datetime
import difflib
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
