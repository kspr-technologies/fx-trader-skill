#!/usr/bin/env python3
"""Build tests/fx_core.py from the fragments in tests/core_src/ (the core build only).

    python3 tests/core_src/assemble.py            # skip a fragment that does not parse
    python3 tests/core_src/assemble.py --strict   # any problem is an error (exit 1)

Fragments are NN_name.py files, joined in NN order after 00_head.py. A fragment is
not a module: it never imports another fragment. Its names live in the joined file.
The first line of a fragment may be "# group: <name>" (else the file name is used).
The "copy together" table is made here from the code (ast), so it is always right.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "fx_core.py")
HEAD = "00_head.py"
RULE = "# " + "-" * 64 + " "
BANNED = [(r"\bzoneinfo\b", "zoneinfo is Python 3.9+"), (r"\.removeprefix\(", "removeprefix is 3.9+"),
          (r"\.removesuffix\(", "removesuffix is 3.9+"), (r"(?<![\w.])(list|dict|tuple|set)\[", "list[...] style hints are 3.9+"),
          (chr(0x2014), "an em dash")]


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def fragments():
    names = sorted(n for n in os.listdir(HERE) if re.match(r"^\d\d_.+\.py$", n) and n != HEAD)
    return [(n, read(os.path.join(HERE, n))) for n in names]


def group_name(fname, text):
    first = text.lstrip().split("\n", 1)[0]
    m = re.match(r"#\s*group:\s*(.+)$", first)
    return m.group(1).strip() if m else fname[3:-3].replace("_", " ")


def top_names(tree):
    """Top-level names a fragment defines, in order."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append(node.target.id)
    return out


def used_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def copy_table(parts):
    """parts: [(group, tree)] -> the comment block that lists each helper's needs."""
    defined = {}
    for group, tree in parts:
        for node in tree.body:
            for name in top_names(ast.Module(body=[node], type_ignores=[])):
                defined[name] = node
    lines = [RULE + "copy together",
             "# Each line is a helper and the names from this file it uses. Copy those too, and",
             "# what they use in turn. \"-\" means it uses nothing else from this file.",
             "# This table is made by tests/core_src/assemble.py from the code itself."]
    for group, tree in parts:
        lines.append("# group %s" % group)
        for node in tree.body:
            for name in top_names(ast.Module(body=[node], type_ignores=[])):
                needs = sorted(n for n in used_names(node) if n in defined and n != name)
                lines.append("#   %s: %s" % (name, ", ".join(needs) if needs else "-"))
    return "\n".join(lines)


def main(argv):
    strict = "--strict" in argv
    head = read(os.path.join(HERE, HEAD)).rstrip() + "\n"
    problems, parts, bodies, seen = [], [], [], {}
    for fname, text in fragments():
        try:
            tree = ast.parse(text, filename=fname, feature_version=(3, 8))
        except SyntaxError as exc:
            problems.append("SKIPPED %s: line %s: %s" % (fname, exc.lineno, exc.msg))
            continue
        dup = [n for n in top_names(tree) if n in seen]
        if dup:
            problems.append("SKIPPED %s: %s already defined in %s" % (fname, ", ".join(dup), seen[dup[0]]))
            continue
        for n in top_names(tree):
            seen[n] = fname
        for pattern, why in BANNED:
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append("WARNING %s line %d: %s" % (fname, line, why))
        group = group_name(fname, text)
        parts.append((group, tree))
        bodies.append("\n\n" + RULE + group + "\n" + text.strip() + "\n")
    body = head + "\n\n" + copy_table(parts) + "\n" + "".join(bodies)
    try:
        ast.parse(body, filename="fx_core.py", feature_version=(3, 8))
        compile(body, "fx_core.py", "exec")
    except SyntaxError as exc:
        print("ERROR: the joined file does not parse: line %s: %s" % (exc.lineno, exc.msg))
        return 1
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, OUT)
    for p in problems:
        print(p)
    print("Wrote %s: %d fragments, %d names, %d lines." % (OUT, len(parts), len(seen), body.count("\n") + 1))
    return 1 if (strict and problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
