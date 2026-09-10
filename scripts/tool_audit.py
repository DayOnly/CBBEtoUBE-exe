# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""AUDIT THE TOOLS: which measurement scripts have gone stale on the code?

    python scripts/tool_audit.py [--full]

A measurement tool that crashes is a nuisance. One that still RUNS and quietly
measures nothing is the expensive kind, because its output looks like a result.

THE PROVEN CASE, and the reason this exists: `flag_extract.py` regexes
`nif_convert.py` for module-level `CBBE2UBE_*` env assignments and prints the
default-OFF toggles. The codebase collapsed 287 inline spellings into
`_flag()/_knob()` on 2026-08-18. The tool did not crash -- it reported
"TOTAL module-level env assignments parsed: 11" and "DEFAULT-OFF TOGGLES (0)",
against a real surface of 287 constants. Read as a result, that is "there are no
cleanup candidates". Read correctly, it is "this tool no longer sees the
codebase".

THE CHECK: does a source-parsing tool ASSERT ITS OWN POPULATION FLOOR?

A tool coupled to an idiom cannot be relied on to notice when the idiom moves,
so it must refuse to report a suspiciously empty result. `survival_report.py`
exits 2 on "NO survival records found"; `survival_sweep.py` says "this sweep
measured NOTHING"; `test_analysis_repo_root.py` asserts `needs_root >= 30`
because "'none are missing' would be a claim about an empty set". flag_extract
has no floor, which is precisely why it could print 11 and look like an answer.

A FIRST VERSION OF THIS CHECK FAILED, recorded so it is not retried. It pulled
each tool's regex literals and ran them against the source, reporting any that
matched nothing. It flagged three tools whose patterns parse RUN LOGS rather
than source (all false) and MISSED flag_extract -- whose patterns do occur
somewhere in `src/`, just not on the lines it walks. "Matches somewhere in
2.6 MB" is a different question from "can this tool still see its population",
and only the second one matters.

Deliberately NOT a lint. It says nothing about style, only about whether a tool
would notice that it has stopped seeing anything.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
SRC_DIR = _REPO / "src"
TOOL_DIRS = [_REPO / "scripts"]
# The untracked working notes hold the majority of the probes and they rot
# fastest, so they are audited too when present.
_SCRATCH = sorted(_REPO.glob("scratchpad_handoff_*"))
TOOL_DIRS += _SCRATCH

# A tool that reads THESE as text is coupled to the source's idioms.
SOURCE_NAMES = ("nif_convert", "auto_convert", "gui_settings", "ube_patcher",
                "fit_metrics", "sliderset_gen", "envflags")

# A tool that REFUSES to report a degenerate population. Any one of these is
# enough; the point is only that something in the tool notices. Calibrated
# against tools whose status is known: survival_report (exits 2 on "NO survival
# records found"), survival_sweep ("this sweep measured NOTHING"),
# registered_bone_audit, comment_audit -- all match; flag_extract, the proven
# stale one, matches nothing.
HAS_FLOOR = re.compile(
    r"measured NOTHING|NO [A-Z][A-Za-z ]*found|collaps|vacuous|population floor"
    r"|SystemExit\(2\)|assert\s+\w+\s*>=|is not a pass|0/0", re.M | re.I)

# `src` joined to a converter module, in either the `/` or the string form:
#     SRC = REPO / "src" / "nif_convert.py"
#     Path(r".../src/nif_convert.py")
#     SRC = REPO / "src";  SRC.glob("*.py")        <- the whole-package form
# The glob form matters: `comment_audit.py` walks the package rather than naming
# a module, and leaving it out let the detector miss a tool that IS coupled to
# the source. Validated against six tools of known status before being trusted.
_SN = "|".join(SOURCE_NAMES)
PATHS_TO_SRC = re.compile(
    rf'src["\']?\s*[/,]\s*["\']?({_SN})|src[/\\]({_SN})'
    r'|["\']src["\']\s*\)?\s*(?:\n\s*)?[\s\S]{0,200}?\.r?glob\(', re.M)


def source_text() -> str:
    out = []
    for p in sorted(SRC_DIR.glob("*.py")):
        try:
            out.append(p.read_text(encoding="utf-8"))
        except OSError:
            pass
    return "\n".join(out)


def regex_literals(tree, text, lines):
    """Every string literal handed to `re.*` in this tool."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "attr", None) if isinstance(f, ast.Attribute) else None
        if name not in ("compile", "search", "match", "findall", "finditer",
                        "sub", "split", "fullmatch"):
            continue
        if not node.args:
            continue
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append((a.lineno, a.value))
    return out


def reads_source(text) -> bool:
    """Does this tool build a PATH to a converter .py and read it as text?

    MENTIONING a source module is not enough, and the loose version of this test
    was wrong in the expensive direction: `change_attribution.py` parses RUN
    LOGS and only names `nif_convert._note_pass_effect` in its docstring, yet
    matched -- so the audit reported 26 findings, most of them tools that never
    touch the source at all. A checker whose population is inflated by
    false positives trains the reader to skip it, which is worse than no
    checker.

    So: require a path expression that JOINS `src` to one of those module
    names, which is what a tool actually coupled to the source has to do.
    """
    if not PATHS_TO_SRC.search(text):
        return False
    return any(k in text for k in ("read_text", "open(", "readlines",
                                   "splitlines"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="list every tool, not just the findings")
    args = ap.parse_args()

    src = source_text()
    tools = []
    for d in TOOL_DIRS:
        tools += sorted(p for p in d.rglob("*.py")
                        if "__pycache__" not in str(p))
    print(f"{len(tools)} tool(s) across {len(TOOL_DIRS)} dir(s); "
          f"source corpus {len(src):,} chars")

    broken, stale, undoc, ok = [], [], [], 0
    for p in tools:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            broken.append((p, f"does not parse: {e.msg} (line {e.lineno})"))
            continue
        if not ast.get_docstring(tree):
            undoc.append(p)
        if reads_source(text):
            if HAS_FLOOR.search(text):
                ok += 1
            else:
                stale.append(p)
    def rel(p):
        return p.relative_to(_REPO).as_posix()

    print(f"\n=== 1. DOES NOT PARSE ({len(broken)}) ===")
    for p, why in broken:
        print(f"  {rel(p)}\n      {why}")
    if not broken:
        print("  none")

    print(f"\n=== 2. PARSES THE SOURCE, ASSERTS NO POPULATION FLOOR "
          f"({len(stale)}) ===")
    print("    if the idiom one of these greps for moves, it reports a SMALL\n"
          "    NUMBER rather than an error -- and a small number reads as a\n"
          "    result. PROVEN: flag_extract.py printed 11 module-level env\n"
          "    assignments against a real surface of 287.")
    for p in stale:
        print(f"  {rel(p)}")
    if not stale:
        print("  none")

    print(f"\n=== 3. NO MODULE DOCSTRING ({len(undoc)}) ===")
    if args.full:
        for p in undoc:
            print(f"  {rel(p)}")
    else:
        for p in undoc[:10]:
            print(f"  {rel(p)}")
        if len(undoc) > 10:
            print(f"  ... and {len(undoc) - 10} more (--full)")
    if not undoc:
        print("  none")

    print(f"\nsource-parsing tools that DO assert a floor: {ok}")
    # EXIT ON TRACKED TOOLS ONLY. The scratchpad holds ~160 one-off probes that
    # are EXPECTED to rot; failing on those makes this audit noise, and an audit
    # people learn to ignore is worse than none. They are still LISTED above,
    # because a future session will otherwise pick one up and trust it.
    tracked = [q for q in list(stale) + [b for b, _ in broken]
               if "scratchpad" not in q.as_posix()]
    if tracked:
        print(f"  {len(tracked)} finding(s) are in TRACKED tools (exit 1); any "
              f"others are scratchpad probes, listed but not failed on.")
    return 1 if tracked else 0


if __name__ == "__main__":
    raise SystemExit(main())
