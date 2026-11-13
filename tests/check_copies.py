#!/usr/bin/env python3
"""Check that skill scripts copy their fx_core helpers word for word.

    python3 tests/check_copies.py fx-scanner/scan.py [more scripts ...]
    python3 tests/check_copies.py --all
    python3 tests/check_copies.py --sync --all     # after a fix in fx_core

For each top-level def, class or NAME = ... in a script whose name is also in
tests/fx_core.py, the source text must be the same, line for line. Each copied
helper's needs (the fx_core names it uses) must be in the script too. A script's own
helpers must have names that fx_core does not use. Exit 1 on any problem.
--sync first rewrites every copy that differs with the fx_core text (a missing need
is still only reported: add it by hand).
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "tests", "fx_core.py")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def first_line(node):
    """The first line of a top-level statement: its first decorator, if any."""
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def top_nodes(text, filename):
    """name -> source text of the top-level statement that defines it."""
    tree = ast.parse(text, filename=filename)
    lines = text.split("\n")
    out = {}
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for name in names:
            # whole lines, decorators and a comment after the last line too
            out[name] = (node, "\n".join(lines[first_line(node) - 1:node.end_lineno]))
    return out


def needs(node, core_names, own):
    used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    return sorted(n for n in used if n in core_names and n != own)


def check(script, core):
    text = read(script)
    try:
        mine = top_nodes(text, script)
    except SyntaxError as exc:
        return ["%s: does not parse (line %s: %s)" % (script, exc.lineno, exc.msg)], 0
    problems, copies = [], 0
    for name, (node, src) in sorted(mine.items()):
        if name not in core:
            continue
        copies += 1
        core_node, core_src = core[name]
        if src != core_src:
            problems.append("%s: %s is not a word-for-word copy of fx_core (rename your own version)" % (script, name))
            continue
        for need in needs(core_node, core, name):
            if need not in mine:
                problems.append("%s: %s needs %s from fx_core, which is not copied" % (script, name, need))
    return problems, copies


def sync(script, core):
    """Rewrite each copy that differs from fx_core with the fx_core text. Returns the count."""
    text = read(script)
    mine = top_nodes(text, script)
    lines = text.split("\n")
    todo = []
    for name, (node, src) in mine.items():
        if name in core and src != core[name][1]:
            todo.append((first_line(node), node.end_lineno, core[name][1]))
    for start, end, new in sorted(set(todo), reverse=True):
        lines[start - 1:end] = new.split("\n")
    if todo:
        tmp = script + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        os.replace(tmp, script)
    return len(set(todo))


def scripts_all():
    out = []
    for folder in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, folder)
        if folder.startswith("fx-") and os.path.isdir(path):
            out.extend(os.path.join(path, n) for n in sorted(os.listdir(path)) if n.endswith(".py"))
    return out


def main(argv):
    core = top_nodes(read(CORE), CORE)
    do_sync = "--sync" in argv
    argv = [a for a in argv if a != "--sync"]
    targets = scripts_all() if argv == ["--all"] else argv
    if not targets:
        print(__doc__.strip())
        return 2
    bad = 0
    for script in targets:
        if do_sync:
            n = sync(script, core)
            if n:
                print("%s: synced %d helpers from fx_core" % (script, n))
        problems, copies = check(script, core)
        for p in problems:
            print(p)
        bad += len(problems)
        print("%s: %d helpers copied, %d problems" % (script, copies, len(problems)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
