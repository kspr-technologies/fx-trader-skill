"""The repo checks: layout, SKILL.md files, the Safety block, the README, the manifests,
Python 3.8, the copied helpers and the shared values (spec 06 section 6.1).

Run it alone from the pack root: python3 -m unittest discover -s tests -p test_repo.py
With the whole suite: python3 -m unittest discover -s tests
Offline. Only the --help checks start a tool (python3 <script> --help).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ast
import contextlib
import difflib
import importlib.util
import io
import json
import re
import subprocess
import unittest

import check_copies
from _load import ROOT

# the 11 skills: folder -> script (BUILD-BRIEF)
SKILLS = {"fx-scanner": "scan.py", "fx-news": "news.py", "fx-entry": "entry.py", "fx-exit": "exit.py",
          "fx-timemachine": "timemachine.py", "fx-risk": "check.py", "fx-budget": "size.py",
          "fx-broker": "ticket.py", "fx-alerts": "alerts.py", "fx-calendar": "events.py", "fx-cot": "cot.py"}
# the example files a skill spec names (they ship next to the script)
EXAMPLES = {"fx-budget": ["config.example.json"], "fx-alerts": ["alerts.example.json"]}
# spec 06 2.2: the subcommands of each tool ([] = one command, no subcommands)
SUBCOMMANDS = {"fx-scanner": ["swing", "day", "refresh"], "fx-news": [], "fx-calendar": ["week", "next", "quiet"],
               "fx-cot": ["show", "new"], "fx-entry": ["check", "watchlist", "watch"], "fx-exit": ["plan"],
               "fx-timemachine": ["run"], "fx-budget": [], "fx-risk": [],
               "fx-broker": ["show", "approve", "decline", "result", "close", "flatten", "stop", "log"],
               "fx-alerts": ["check", "add", "quote", "clock", "list"]}
# spec 06 4.2: the ABOUT line (the README and plugin.json description)
ABOUT = ("11 Claude skills for trading currency pairs, gold and oil at a MetaTrader broker: scan for setups on "
         "both sides, track the news and the calendar, time every entry and exit, and replay a trade 10,000 times "
         "before you risk a dollar.")
# D7: the skills on the way to an order carry the one Safety block; the two reading tools do not
ORDER_PATH = ["fx-scanner", "fx-entry", "fx-exit", "fx-timemachine", "fx-budget", "fx-cot", "fx-risk",
              "fx-broker", "fx-alerts"]
READING = ["fx-news", "fx-calendar"]
READING_LINE = "Not financial advice. Nothing in this pack places an order."
SELF = os.path.abspath(__file__)
CORE = os.path.join(ROOT, "tests", "fx_core.py")
CORE_SRC = os.path.join(ROOT, "tests", "core_src")
GENERATOR = os.path.join(ROOT, "tests", "fixtures", "makefix", "common.py")
MARKET = os.path.join(ROOT, "tests", "fixtures", "fx-market")
EXAMPLE_CONFIG = os.path.join(ROOT, "fx-budget", "config.example.json")
# D18 and the PASS age
ORDER_FIELDS = ("symbol", "side", "intent", "lots", "type", "entry", "stop_loss", "take_profit", "expiry", "style")
FRESH_CHECK_MINUTES = 5
UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"
# skeleton 3.1 and 3.3
TOP_KEYS = ["account", "paper", "yahoo", "allow_short", "style", "day_end", "universe", "day_universe", "risk",
            "swing", "day", "news", "calendar_url", "holidays", "early_closes", "fx_rates_max_age_hours",
            "instruments"]
INSTRUMENTS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
               "AUDJPY", "EURCHF", "XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "COPPER", "USOIL", "UKOIL", "NATGAS"]
# skeleton 4.8: the keys of an alert rule ("added_at" is optional in a hand-written rule)
RULE_KEYS = ["id", "active", "from", "symbol", "kind", "level", "side", "at_utc", "minutes_before", "session",
             "position_id", "rolls_applied", "note"]
RULE_KINDS = ["above", "below", "stop", "target", "flat_by", "exit_by", "friday_close", "session_open", "news",
              "rollover"]
# spec 06 4.1: the README's "## " sections, in this order
README_SECTIONS = ["What it does", "Prices: read this first", "Install", "The eleven", "How to use it",
                   "The tools", "Fine print", "Files"]
README_TITLE = "# The FX Trader skill"
PYTHON_LINE = "The tools need Python 3.8 or newer. Nothing to pip install."
TEST_LINES = ["cd tests && python3 -m unittest discover -v",
              "The tests run offline, on the made-up sample market in tests/fixtures/fx-market/ (rebuild it with "
              "python3 make_fixtures.py in tests/fixtures/)."]
README_FACTS = ["not financial advice", "74% to 89%", '"paper": true', "Yahoo's terms", "CME owns that data",
                "Futures rolls", "Weekend gaps", "Swap", "EPD Circular Letter No. 08 of 2022", "9 December 2025",
                "FBR"]
SAID_ONCE = ["74% to 89%", "EPD Circular Letter", "9 December 2025", "CME owns that data"]
# spec 06 5.8: the designed numbers of the README example
DESIGNED = ["2,418.4", "2,431.6", "2,433.49", "33.49", "56.51", "0.02 lots", "$97.81", "2,429.24", "2,395.75",
            "2,485.75", "2,421.59", "2,512.41", "1.08480", "1.08640", "1.08452", "1.08561", "1.08234",
            "0.86 lots", "$98.90", "$100.05"]
# PSX words. In skill files: none at all (spec 06 6.1 #10). Elsewhere (tests, fixtures, docs): no rupees,
# no KSE, no PSX paths or site. The README may name PSX only in its credit line.
PSX_IN_SKILLS = [r"PSX", r"KSE", r"(?i)rupee", r"NCCPL", r"Juma", r"psx_", r"psx/", r"psx-", r"dps\.psx",
                 r"\bRs\.? ?\d", r"\bPKR\b"]
PSX_ANYWHERE = [r"(?i)rupee", r"\bRs\.? ?\d", r"\bPKR\b", r"KSE", r"psx-", r"dps\.psx", r"NCCPL"]
US_WORDS = ["Alpaca", "Stooq", "EDGAR", "Pelosi", "TSLA", "FINRA", "pdt_guard"]
# an email address (none may ship, except the author's own in AUTHOR)
EMAIL = re.compile(r"\b[\w.+-]+@(?:[\w-]+\.)+(?:com|net|org|io|pk|co|uk|edu|gov|ai|dev|me|info)\b")
TEXT_ENDS = (".md", ".py", ".json", ".csv", ".txt", ".xml", ".html", ".jsonl", ".sh")
# the modules the pack uses, for Python 3.8 and 3.9 (they have no sys.stdlib_module_names)
STDLIB_38 = {"argparse", "ast", "bisect", "calendar", "codecs", "collections", "contextlib", "copy", "csv",
             "datetime", "decimal", "difflib", "email", "errno", "fnmatch", "fractions", "functools", "glob", "gzip",
             "hashlib", "heapq", "html", "http", "importlib", "inspect", "io", "itertools", "json", "locale", "math",
             "numbers", "operator", "os", "pathlib", "platform", "pprint", "random", "re", "shlex", "shutil",
             "signal", "socket", "stat", "statistics", "string", "struct", "subprocess", "sys", "tempfile",
             "textwrap", "threading", "time", "traceback", "types", "typing", "unicodedata", "unittest", "urllib",
             "uuid", "warnings", "xml", "zlib", "__future__"}
LICENSE_LINES = ["MIT License", "Copyright (c) 2026 Jake Schincariol", "Copyright (c) 2026 Waleed Amjad"]
# the one author, named in the manifests (the only email that may ship)
AUTHOR = {"name": "Waleed Amjad", "email": "waleedamjad99@hotmail.com"}

# spec 06 section 3, word for word: the LAST "## " section of each order-path SKILL.md
SAFETY = """## Safety

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
"""

# spec 06 section 4.3, word for word: the README's fine print (the section before "## Files")
FINE_PRINT = """## Fine print

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
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def rel(path):
    return os.path.relpath(path, ROOT)


def squeeze(text):
    """Spaces and line breaks squeezed to one space."""
    return " ".join(text.split())


def shipped_files():
    """Every file of the pack (fx-work is a sibling folder, so it is never here)."""
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__"))
        for f in sorted(files):
            if f != ".DS_Store" and not f.endswith(".pyc"):
                yield os.path.join(base, f)


def text_files():
    for path in shipped_files():
        if path.endswith(TEXT_ENDS) or os.path.basename(path) == "LICENSE":
            yield path


def py_files():
    return [p for p in shipped_files() if p.endswith(".py")]


def skill_files():
    """The files inside the 11 skill folders (.md, .py, .json)."""
    for s in SKILLS:
        folder = os.path.join(ROOT, s)
        for f in sorted(os.listdir(folder)):
            path = os.path.join(folder, f)
            if os.path.isfile(path) and f.endswith((".md", ".py", ".json")):
                yield path


def script(skill):
    return os.path.join(ROOT, skill, SKILLS[skill])


def frontmatter(path):
    """({key: value}, text) of a SKILL.md. A folded value (>- or |) is joined with spaces."""
    text = read(path)
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out, key = {}, None
    for line in (m.group(1).split("\n") if m else []):
        k = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if k:
            key, value = k.group(1), k.group(2).strip()
            out[key] = "" if value in (">", ">-", "|", "|-") else value
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out, text


def headings(text, level="## "):
    """[(line index, heading text)] of the headings of one level, outside code blocks."""
    out, fence = [], False
    for i, line in enumerate(text.split("\n")):
        if line.startswith("```"):
            fence = not fence
        elif not fence and line.startswith(level):
            out.append((i, line[len(level):].strip()))
    return out


def section(text, name, level="## "):
    """The text of one section, from its heading to the next heading of the same level (or the end)."""
    lines = text.split("\n")
    marks = headings(text, level)
    for k, (i, title) in enumerate(marks):
        if title == name:
            end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
            return "\n".join(lines[i:end])
    return None


_TREES = {}


def tree(path):
    """The parsed file (kept, so each file is parsed once)."""
    if path not in _TREES:
        _TREES[path] = ast.parse(read(path), filename=path)
    return _TREES[path]


def top_level(path):
    """{name: source text} of a file's top-level defs and assignments (as tests/check_copies.py reads them)."""
    return {name: src for name, (node, src) in check_copies.top_nodes(read(path), path).items()}


def run_help(*args):
    """python3 <args> --help, offline: (exit code, the output)."""
    proc = subprocess.run([sys.executable] + list(args) + ["--help"], cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
    return proc.returncode, proc.stdout


# names that came after Python 3.8: (module, attribute) used as module.attribute or "from module import name"
NEW_NAMES = {("itertools", "pairwise"), ("functools", "cache"), ("math", "lcm"), ("math", "nextafter"),
             ("math", "ulp"), ("random", "randbytes"), ("datetime", "UTC")}
NEW_MODULES = {"zoneinfo", "graphlib", "tomllib"}
NEW_METHODS = {"removeprefix", "removesuffix", "bit_count"}
BISECT = {"bisect", "bisect_left", "bisect_right", "insort", "insort_left", "insort_right"}
GENERIC = {"list", "dict", "tuple", "set", "frozenset", "type"}


def annotations(node):
    """The annotation nodes of the file: arguments, returns and annotated assignments."""
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if n.returns is not None:
                yield n.returns
        elif isinstance(n, ast.AnnAssign):
            yield n.annotation


def py38_problems(path):
    """Plain lines, one per thing in the file that needs Python 3.9 or newer."""
    return py38_source_problems(read(path), path)


def py38_source_problems(src, path="<text>"):
    """Plain lines, one per thing in the source text that needs Python 3.9 or newer."""
    try:
        root = ast.parse(src, filename=path, feature_version=(3, 8))
    except SyntaxError as exc:
        return ["line %s: does not parse as Python 3.8 (%s)" % (exc.lineno, exc.msg)]
    out = []
    for n in ast.walk(root):
        line = getattr(n, "lineno", 0)
        if hasattr(ast, "Match") and isinstance(n, ast.Match):
            out.append("line %d: a match statement" % line)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in NEW_MODULES:
                    out.append("line %d: import %s" % (line, a.name))
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            top = n.module.split(".")[0]
            if top in NEW_MODULES:
                out.append("line %d: from %s import" % (line, n.module))
            for a in n.names:
                if (top, a.name) in NEW_NAMES:
                    out.append("line %d: from %s import %s" % (line, top, a.name))
        elif isinstance(n, ast.Attribute):
            if n.attr in NEW_METHODS:
                out.append("line %d: .%s" % (line, n.attr))
            elif isinstance(n.value, ast.Name) and (n.value.id, n.attr) in NEW_NAMES:
                out.append("line %d: %s.%s" % (line, n.value.id, n.attr))
        elif isinstance(n, ast.Call):
            f = n.func
            fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            words = {k.arg for k in n.keywords}
            if fname == "zip" and "strict" in words:
                out.append("line %d: zip(..., strict=)" % line)
            if fname in BISECT and "key" in words:
                out.append("line %d: %s(..., key=)" % (line, fname))
    for ann in annotations(root):
        for n in ast.walk(ann):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in GENERIC:
                out.append("line %d: a %s[...] hint" % (n.lineno, n.value.id))
            elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                out.append("line %d: an X | Y hint" % n.lineno)
    return sorted(set(out), key=lambda s: int(s.split(":")[0].split()[1]))


RULE = "# " + "-" * 64 + " "


def defined_names(node):
    """The top-level names one statement defines (as tests/core_src/assemble.py counts them)."""
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def core_parts():
    """fx_core.py cut at its rule lines: (the table lines, [(group, the group's code)])."""
    lines = read(CORE).split("\n")
    marks = [(i, line[len(RULE):]) for i, line in enumerate(lines) if line.startswith(RULE)]
    if not marks or marks[0][1] != "copy together":
        return [], []
    table = lines[marks[0][0] + 1:marks[1][0] if len(marks) > 1 else len(lines)]
    groups = []
    for k, (i, name) in enumerate(marks[1:], 1):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        groups.append((name, "\n".join(lines[i + 1:end])))
    return table, groups


class Layout(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))
        self.assertEqual(sorted(d for d in os.listdir(ROOT) if d.startswith("fx-")), sorted(SKILLS))
        for skill, name in SKILLS.items():
            self.assertTrue(os.path.isfile(script(skill)), "%s has no %s" % (skill, name))
            have = sorted(f for f in os.listdir(os.path.join(ROOT, skill)) if f not in ("__pycache__", ".DS_Store"))
            self.assertEqual(have, sorted(["SKILL.md", name] + EXAMPLES.get(skill, [])), skill)

    def test_root_layout(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d not in (".DS_Store", "__pycache__"))
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "tests"] + list(SKILLS)))
        self.assertEqual(sorted(os.listdir(os.path.join(ROOT, ".claude-plugin"))), ["marketplace.json", "plugin.json"])

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertEqual(fm.get("name"), s, "%s: the frontmatter name must be the folder name" % s)
            self.assertGreater(len(fm.get("description", "")), 200, "%s: the description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, "%s: SKILL.md is too short" % s)


class SafetyBlock(unittest.TestCase):
    def test_order_path_skills_carry_one_safety_block(self):
        blocks = {}
        for s in ORDER_PATH:
            text = read(os.path.join(ROOT, s, "SKILL.md"))
            titles = [t for _, t in headings(text)]
            self.assertEqual(titles.count("Safety"), 1, "%s: one ## Safety section" % s)
            self.assertEqual(titles[-1], "Safety", "%s: ## Safety must be the last ## section" % s)
            self.assertEqual(titles[-2], "What it never does", "%s: ## Safety comes right after "
                             "## What it never does" % s)
            block = section(text, "Safety").strip()
            self.assertEqual(block, SAFETY.strip(), "%s: the Safety block is not spec 06 section 3 word for word" % s)
            low = squeeze(block.lower())
            for words in ("not financial advice", "yes", "nothing in this pack places an order", "74% to 89%",
                          "paper"):
                self.assertIn(words, low, "%s: %s" % (s, words))
            blocks[s] = block
        self.assertEqual(len(set(blocks.values())), 1, "the Safety blocks differ: %s" % sorted(blocks))

    def test_reading_skills_have_no_safety_block(self):
        for s in READING:
            text = read(os.path.join(ROOT, s, "SKILL.md"))
            self.assertNotIn("Safety", [t for _, t in headings(text)], s)
            self.assertNotIn("## Safety", text, s)
            last = [line for line in text.split("\n") if line.strip()][-1]
            self.assertEqual(last.strip(), READING_LINE, "%s must end with the reading-tool line" % s)


class Manifest(unittest.TestCase):
    def test_plugin_manifest_points_at_the_root_skills(self):
        plugin = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(plugin["name"], "fx-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        self.assertEqual(plugin["version"], "1.0.0")
        self.assertEqual(plugin["author"], AUTHOR)
        self.assertEqual(plugin["license"], "MIT")
        self.assertTrue(plugin["keywords"] and all(isinstance(k, str) and k for k in plugin["keywords"]))
        market = json.loads(read(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        self.assertEqual(market["name"], "fx-trader-skill")
        self.assertEqual(market["owner"], AUTHOR)
        self.assertEqual(len(market["plugins"]), 1)
        self.assertEqual(market["plugins"][0]["name"], "fx-trader-skill")
        self.assertEqual(market["plugins"][0]["source"], "./")
        self.assertTrue(market["description"] and market["plugins"][0]["description"])

    def test_license_is_mit(self):
        text = read(os.path.join(ROOT, "LICENSE"))
        for line in LICENSE_LINES:
            self.assertIn(line, text)
        self.assertIn("Permission is hereby granted, free of charge", text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', text)


class Readme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(os.path.join(ROOT, "README.md"))
        cls.flat = squeeze(cls.text)

    def test_readme_names_every_skill_and_the_fine_print(self):
        self.assertTrue(self.text.startswith(README_TITLE + "\n"), "the README starts with " + README_TITLE)
        for line in [ABOUT, "## Fine print", PYTHON_LINE] + TEST_LINES:
            self.assertIn(line, self.flat)
        self.assertIn("not financial advice", self.flat.lower())
        for words in README_FACTS:
            self.assertIn(words.lower(), self.flat.lower(), words)
        for s in SKILLS:
            self.assertIn("/" + s, self.text, s)
            self.assertIn("`/%s`" % s, section(self.text, "The eleven") or "", "%s is not in the table" % s)

    def test_readme_has_the_sections_of_the_spec(self):
        self.assertEqual([t for _, t in headings(self.text)], README_SECTIONS)

    def test_readme_holds_the_fine_print_word_for_word(self):
        fine = section(self.text, "Fine print")
        self.assertEqual(fine.strip(), FINE_PRINT.strip(), "the fine print is not spec 06 section 4.3 word for word")

    def test_fine_print_says_each_fact_once(self):
        for words in SAID_ONCE:
            self.assertEqual(self.flat.count(words), 1, words)

    def test_readme_example_has_the_designed_numbers(self):
        example = section(self.text, "An example, step by step", level="### ")
        self.assertIsNotNone(example, "the README has no ### An example, step by step")
        example = squeeze(example.split("\n## ")[0])
        for number in DESIGNED:
            self.assertIn(number, example, number)

    def test_readme_names_psx_only_in_its_credit_line(self):
        lines = [i for i, line in enumerate(self.text.split("\n")) if re.search(r"\bPSX\b", line)]
        self.assertLessEqual(len(lines), 1, "PSX is named on more than one line")
        what = headings(self.text)[0][0]
        for i in lines:
            self.assertLess(i, what, "PSX is named only in the credit line, at the top")
            self.assertIn("Day Trader skill", self.text.split("\n")[i])

    def test_readme_starts_in_paper_mode(self):
        how = section(self.text, "How to use it")
        for words in ("fx-budget/config.example.json", "fx/config.json", "Specification", '"paper": true'):
            self.assertIn(words, how, words)


class Words(unittest.TestCase):
    def test_no_em_dashes_and_no_personal_email(self):
        bad = []
        for path in text_files():
            text = read(path)
            for n, line in enumerate(text.split("\n"), 1):
                if chr(0x2014) in line:
                    bad.append("%s line %d: an em dash" % (rel(path), n))
                m = EMAIL.search(line)
                if m and m.group(0) != AUTHOR["email"]:
                    bad.append("%s line %d: an email address (%s)" % (rel(path), n, m.group(0)))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_no_psx_leftovers(self):
        # the skill files: no PSX word at all (spec 06 6.1 #10)
        bad = []
        for path in skill_files():
            for n, line in enumerate(read(path).split("\n"), 1):
                for pat in PSX_IN_SKILLS:
                    if re.search(pat, line):
                        bad.append("%s line %d: %s" % (rel(path), n, pat))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_no_psx_leftovers_in_the_other_shipped_files(self):
        # tests, fixtures, the README, LICENSE and the manifests: no rupees, no KSE, no PSX paths or site
        skills = {os.path.join(ROOT, s) for s in SKILLS}
        bad = []
        for path in text_files():
            if os.path.dirname(path) in skills or os.path.abspath(path) == SELF:
                continue
            for n, line in enumerate(read(path).split("\n"), 1):
                for pat in PSX_ANYWHERE:
                    if re.search(pat, line):
                        bad.append("%s line %d: %s" % (rel(path), n, pat))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_no_us_pack_leftovers(self):
        bad = []
        for path in skill_files():
            text = read(path)
            bad += ["%s: %s" % (rel(path), w) for w in US_WORDS if w in text]
        self.assertEqual(bad, [])


class Python38(unittest.TestCase):
    def test_every_tool_runs_on_python_3_8(self):
        # every .py of the pack: the 11 tools, fx_core, its fragments, the tests and the generator
        bad = []
        for path in py_files():
            bad += ["%s %s" % (rel(path), p) for p in py38_problems(path)]
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_the_checker_finds_new_python(self):
        # the 3.8 checker itself: each of these must be found, and plain code must pass
        cases = {"import zoneinfo\n": "import zoneinfo",
                 "x = 'a'.removeprefix('b')\n": ".removeprefix",
                 "def f(rows: list[int]) -> None:\n    pass\n": "list[...] hint",
                 "def f() -> dict[str, int]:\n    pass\n": "dict[...] hint",
                 "import bisect\nbisect.bisect_left([1], 1, key=abs)\n": "bisect_left(..., key=)",
                 "z = zip([1], [2], strict=True)\n": "zip(..., strict=)",
                 "from itertools import pairwise\n": "pairwise",
                 "import functools\n@functools.cache\ndef f():\n    pass\n": "functools.cache",
                 "def f(x: int | None):\n    pass\n": "X | Y hint",
                 "match x:\n    case 1:\n        pass\n": "does not parse as Python 3.8"}
        for src, want in cases.items():
            found = py38_source_problems(src)
            self.assertTrue(any(want in p for p in found), "%r: %s" % (src, found))
        plain = ("rows_list = [1]\nmy_dict = {}\nx = rows_list[0]\ny = my_dict.get(1)\n"
                 "def f(a: 'list[int]', b: int = 0) -> int:\n    return b\n")
        self.assertEqual(py38_source_problems(plain), [])

    def test_only_the_standard_library(self):
        std = set(getattr(sys, "stdlib_module_names", STDLIB_38))
        tests_dir = os.path.join(ROOT, "tests")
        local = set()
        for folder in (tests_dir, os.path.join(tests_dir, "fixtures"), CORE_SRC):
            for f in os.listdir(folder):
                if f.endswith(".py"):
                    local.add(f[:-3])
                elif os.path.isfile(os.path.join(folder, f, "__init__.py")):
                    local.add(f)
        tools = {os.path.abspath(script(s)) for s in SKILLS} | {CORE}
        bad = []
        for path in py_files():
            allowed = std if os.path.abspath(path) in tools else std | local
            for n in ast.walk(tree(path)):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0:
                    names = [n.module]
                for name in names:
                    if name.split(".")[0] not in allowed:
                        bad.append("%s line %d: import %s" % (rel(path), n.lineno, name))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


class Copies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = check_copies.top_nodes(read(CORE), CORE)

    def test_shared_helpers_are_exact_copies(self):
        # each fx_core name in a script (or in the generator) is a word-for-word copy, with all it needs
        # (tests/check_copies.py does the work; run it with --all to see the same list)
        problems, checked = [], 0
        for path in [script(s) for s in SKILLS] + [GENERATOR]:
            found, copies = check_copies.check(path, self.core)
            problems += found
            checked += copies
        self.assertEqual(problems, [], "\n" + "\n".join(problems))
        self.assertGreater(checked, 100)

    def test_each_script_stands_alone(self):
        marker = "# ---- copied from tests/fx_core.py: do not edit (tests/check_copies.py checks it)"
        for s in SKILLS:
            text = read(script(s))
            self.assertEqual(text.count(marker + "\n"), 1, "%s: the copy block line" % s)
            self.assertRegex(text, r"(?m)^def main\(argv(=None)?\):", s)
            self.assertIn('\nif __name__ == "__main__":\n    sys.exit(main(sys.argv[1:]))', text, s)
            self.assertRegex(text, r"(?m)^import argparse$", s)

    def test_no_helper_is_written_twice(self):
        # a helper that two scripts share lives in fx_core (lead decision 2), so test_repo checks the copies
        seen = {}
        for s in SKILLS:
            for name, (node, src) in check_copies.top_nodes(read(script(s)), script(s)).items():
                if name not in self.core:
                    seen.setdefault((name, src), []).append(s)
        twice = ["%s in %s" % (name, ", ".join(where)) for (name, _), where in sorted(seen.items()) if len(where) > 1]
        self.assertEqual(twice, [], "the same helper in two scripts: put it in fx_core\n" + "\n".join(twice))

    def test_copy_together_table_is_true(self):
        # the table at the top of fx_core.py, recomputed from the code with ast, as assemble.py makes it
        table, groups = core_parts()
        self.assertTrue(groups, "tests/fx_core.py has no copy together table")
        parsed = [(group, ast.parse(code)) for group, code in groups]
        defined = set()
        for _, body in parsed:
            for node in body.body:
                defined.update(defined_names(node))
        want = []
        for group, body in parsed:
            want.append("# group %s" % group)
            for node in body.body:
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                for name in defined_names(node):
                    needs = sorted(n for n in used if n in defined and n != name)
                    want.append("#   %s: %s" % (name, ", ".join(needs) if needs else "-"))
        have = [line for line in table if line.startswith(("# group ", "#   "))]
        diff = list(difflib.unified_diff(have, want, "fx_core.py", "from the code", lineterm="", n=0))[:40]
        self.assertEqual(have, want, "the copy together table is not what the code gives:\n" + "\n".join(diff))

    def test_core_is_the_assembled_build(self):
        # tests/fx_core.py is made by tests/core_src/assemble.py and never edited by hand
        spec = importlib.util.spec_from_file_location("fx_assemble", os.path.join(CORE_SRC, "assemble.py"))
        asm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(asm)
        head = asm.read(os.path.join(asm.HERE, asm.HEAD)).rstrip() + "\n"
        parts, bodies = [], []
        for fname, text in asm.fragments():
            parts.append((asm.group_name(fname, text), ast.parse(text, filename=fname)))
            bodies.append("\n\n" + asm.RULE + parts[-1][0] + "\n" + text.strip() + "\n")
        built = head + "\n\n" + asm.copy_table(parts) + "\n" + "".join(bodies)
        self.assertTrue(built == read(CORE), "tests/fx_core.py differs from its fragments: run "
                        "python3 tests/core_src/assemble.py")



class SharedValues(unittest.TestCase):
    def test_order_fields_are_the_same_for_risk_and_broker(self):
        files = [script("fx-risk"), script("fx-broker"), CORE]
        for name, want in (("ORDER_FIELDS", ORDER_FIELDS), ("FRESH_CHECK_MINUTES", FRESH_CHECK_MINUTES)):
            srcs = [top_level(path).get(name) for path in files]
            for path, src in zip(files, srcs):
                self.assertIsNotNone(src, "%s has no %s" % (rel(path), name))
            self.assertEqual(len(set(srcs)), 1, "%s differs between %s" % (name, [rel(p) for p in files]))
            self.assertEqual(ast.literal_eval(srcs[0].split("=", 1)[1].split("#")[0].strip()), want, name)

    def test_example_config_is_safe(self):
        raw = read(EXAMPLE_CONFIG)
        self.assertIn('"paper": true', raw)
        cfg = json.loads(raw)
        for key in TOP_KEYS:
            self.assertIn(key, cfg, key)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg["yahoo"], True)
        self.assertIs(cfg["allow_short"], True)
        self.assertEqual(cfg["account"]["currency"], "USD")
        self.assertEqual(cfg["account"]["balance"], 1000)
        self.assertIs(cfg["risk"]["require_stop"], True)
        self.assertEqual(cfg["risk"]["max_risk_per_trade_pct"], 1.0)
        self.assertEqual(cfg["style"], "swing")
        self.assertEqual(cfg["day_end"], "17:00 New York")
        self.assertEqual(sorted(cfg["instruments"]), sorted(INSTRUMENTS))
        spec = importlib.util.spec_from_file_location("fx_core_for_repo", CORE)
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        said = io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(said):
            loaded = core.load_config(EXAMPLE_CONFIG, required=True)
        self.assertEqual(said.getvalue(), "", "load_config warns on the example config")
        self.assertIs(loaded["paper"], True)

    def test_alert_example_loads(self):
        doc = json.loads(read(os.path.join(ROOT, "fx-alerts", "alerts.example.json")))
        self.assertTrue(doc["rules"])
        ids = [rule["id"] for rule in doc["rules"]]
        self.assertEqual(len(ids), len(set(ids)), "two rules share an id")
        for rule in doc["rules"]:
            for key in RULE_KEYS:
                self.assertIn(key, rule, "%s: %s" % (rule.get("id"), key))
            self.assertIn(rule["kind"], RULE_KINDS, rule["id"])

    def test_yahoo_manners(self):
        bad = []
        for path in py_files():
            text = read(path)
            if os.path.abspath(path) == SELF:
                continue
            for m in re.finditer(r"Mozilla/[^\"'\n]*", text):
                if m.group(0) != UA:
                    bad.append("%s: User-Agent %r" % (rel(path), m.group(0)))
            for words in ("Mozilla/5.0 (Windows", "Macintosh", "Chrome/", "v7/finance/quote"):
                if words in text:
                    bad.append("%s: %s" % (rel(path), words))
        for path in [script(s) for s in SKILLS] + [CORE]:
            text, names = read(path), top_level(path)
            if "UA" in names:
                self.assertEqual(names["UA"], "UA = %s" % json.dumps(UA), rel(path))
            for m in re.finditer(r"""["']User-Agent["']\s*:\s*([^,}\)\n]+)""", text):
                if m.group(1).strip() != "UA":
                    bad.append("%s: a User-Agent that is not UA" % rel(path))
            if "PACE_SECONDS" in names:
                value = ast.literal_eval(names["PACE_SECONDS"].split("=", 1)[1].split("#")[0].strip())
                self.assertGreaterEqual(value, 2.0, rel(path))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_no_network_in_tests(self):
        bad = []
        tests_dir = os.path.join(ROOT, "tests")
        for f in sorted(os.listdir(tests_dir)):
            if not (f.startswith("test_") and f.endswith(".py")):
                continue
            path = os.path.join(tests_dir, f)
            for n in ast.walk(tree(path)):
                if isinstance(n, ast.Call):
                    fn = n.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                    owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ""
                    if name == "urlopen" or (owner == "socket" and name in ("socket", "create_connection")) or \
                            name in ("HTTPConnection", "HTTPSConnection"):
                        bad.append("%s line %d: %s()" % (f, n.lineno, name))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_the_sample_market_says_it_is_made_up(self):
        self.assertIn("made up", read(os.path.join(MARKET, "README.md")).lower())
        size = sum(os.path.getsize(p) for p in shipped_files() if p.startswith(MARKET + os.sep))
        self.assertLess(size, 8 * 1024 * 1024, "the sample market is over 8 MB")

    def test_sample_config_differs_only_where_told(self):
        example = json.loads(read(EXAMPLE_CONFIG))
        sample = json.loads(read(os.path.join(MARKET, "config.json")))
        self.assertEqual(sample["account"]["balance"], 10000)
        self.assertEqual(sample["early_closes"], {"2026-07-03": "13:00"})
        example["account"]["balance"] = 10000
        example["early_closes"] = {"2026-07-03": "13:00"}
        self.assertEqual(sample, example)


# a documented command: python3 fx-<skill>/<script>.py ... (README, SKILL.md) or
# python3 "${CLAUDE_SKILL_DIR}/<script>.py" ... (SKILL.md). The command ends at a "#" or a backtick.
COMMAND = re.compile(r"python3 (?:\"\$\{CLAUDE_SKILL_DIR\}/([\w.-]+)\"|(fx-[a-z]+)/([\w.-]+))([^#`\n]*)")


def documented_commands():
    """[(where, skill, script name, words after it)] of every documented tool command."""
    out = []
    docs = [(os.path.join(ROOT, "README.md"), None)] + [(os.path.join(ROOT, s, "SKILL.md"), s) for s in SKILLS]
    for path, own in docs:
        for n, line in enumerate(read(path).split("\n"), 1):
            for m in COMMAND.finditer(line):
                skill, name = (own, m.group(1)) if m.group(1) else (m.group(2), m.group(3))
                where = "%s line %d" % (rel(path), n)
                out.append((where, skill, name, m.group(4).split(" python3 ")[0].split()))
    return out


class Commands(unittest.TestCase):
    def test_documented_commands_exist(self):
        found = documented_commands()
        self.assertGreater(len(found), 40)
        helps, bad = {}, []
        for where, skill, name, words in found:
            if skill not in SKILLS or name != SKILLS[skill]:
                bad.append("%s: %s/%s is not a tool of the pack" % (where, skill, name))
                continue
            words = [w.strip("[](),.;:'\"") for w in words]
            words = [w for w in words if w]
            sub = words[0] if words and not words[0].startswith("-") else None
            if sub is not None and sub not in SUBCOMMANDS[skill]:
                bad.append("%s: %s has no subcommand %r" % (where, name, sub))
                continue
            key = (skill, sub)
            if key not in helps:
                args = [script(skill)] + ([sub] if sub else [])
                helps[key] = run_help(*args)
            code, text = helps[key]
            if code != 0:
                bad.append("%s: %s %s --help exits %d" % (where, name, sub or "", code))
                continue
            for w in words:
                flag = w.split("=")[0]
                if flag.startswith("--") and len(flag) > 2 and not re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(flag),
                                                                               text):
                    bad.append("%s: %s %s has no %s" % (where, name, sub or "", flag))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_every_tool_has_help(self):
        for skill in SKILLS:
            code, text = run_help(script(skill))
            self.assertEqual(code, 0, "%s --help: %s" % (SKILLS[skill], text[-300:]))
            m = re.search(r"\{([\w,]+)\} \.\.\.", text)   # argparse shows subcommands as {a,b} ...
            subs = m.group(1).split(",") if m else []
            self.assertEqual(sorted(subs), sorted(SUBCOMMANDS[skill]), "%s: the subcommands of spec 06 2.2"
                             % SKILLS[skill])


if __name__ == "__main__":
    unittest.main()
