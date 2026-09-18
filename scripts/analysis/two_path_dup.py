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

"""HOW MUCH OF THE TWO CONVERT PATHS IS THE SAME TEXT  (audit F102)

    python -m scripts.analysis.two_path_dup [--min-run N] [--top N]

Audit finding F102 says the copy path and the body-swap path carry
copy-pasted blocks that have DIVERGED, so a reader cannot tell a deliberate
difference from a missed port -- and the file's own history agrees: a seam-weld
fix tore 5% twice, and panel rigidity landed 26/74 across the two.

**F102'S OWN NUMBERS CANNOT BE REPRODUCED.** Its verifier wrote "the dup_probe
counts (24 runs, 276 lines, 0.346) were not re-run -- treat the numbers as
unverified", and said the counts "should be regenerated and attached before the
refactor is scoped". The probe that produced them lived in an untracked
scratchpad and is GONE, and its normalisation was never written down. So this
tool does not claim to reproduce them; it states its own rule and measures
again. Two of the three numbers are not even comparable across the 2026-09-01
split, which cut the file from ~29.5k lines to 15k and moved code into siblings
-- every line number in F102's evidence is against the pre-split file.

TWO RATIOS, BECAUSE ONE NUMBER HIDES THE QUESTION:

  literal    comments, docstrings and blank lines removed, and leading
             whitespace stripped -- the two paths sit at different nesting
             depths, so indentation differs by construction and comparing it
             hides every real copy. This is the "someone pasted this" count.
  shape      the above, plus every NAME token replaced by a single symbol. This
             is the "same code, different variables" count, which is what
             matters for a refactor and is always the larger of the two.

The gap between them IS the finding F102 describes: text that is structurally
one block written twice with different identifiers.

Exit 0 = measured, 2 = bad arguments, 3 = a path's function was not found
(0/0 is not a pass -- a rename must not read as "no duplication").
"""
from __future__ import annotations

import ast
import difflib
import io
import sys
import tokenize
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from scripts import pass_map                                    # noqa: E402
from scripts.analysis._census_common import require_population   # noqa: E402

# The two paths, by the functions that ARE them. `convert_nif` dispatches to
# `convert_nif_phase2` for a body-swap piece and otherwise runs the copy path
# itself, and the swap path's per-shape work lives in `_fit_shapes_swap`.
COPY_PATH = ("convert_nif",)
SWAP_PATH = ("convert_nif_phase2", "_fit_shapes_swap")


def _functions() -> dict:
    """name -> (module path, first line, source lines). Across every converter
    module, because the split moved code out of the monolith."""
    out = {}
    for rel in pass_map.CONVERTER_MODULES:
        p = _REPO / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for node in ast.parse(text).body:
            if isinstance(node, ast.FunctionDef):
                out[node.name] = (rel, node.lineno,
                                  lines[node.lineno - 1:node.end_lineno])
    return out


def strip_noise(lines: "list[str]") -> "list[tuple[int, str]]":
    """(offset within the function, code text) with comments, docstrings and
    blank lines removed. Indentation is kept HERE and stripped by the caller
    for the literal compare -- see why in `main`.

    A TRAILING COMMENT CUTS THE COMMENT, NOT THE LINE. The first draft dropped
    the whole line whenever it carried one, which silently deleted real code
    from BOTH sides of the comparison and under-reported the duplication this
    tool exists to measure. Caught by its own test, not by reading it.
    """
    src = "\n".join(lines)
    drop, cut = set(), {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                if lines[row - 1][:col].strip():
                    cut[row] = min(cut.get(row, len(lines[row - 1])), col)
                else:
                    drop.add(row)              # a whole-line comment
            elif tok.type == tokenize.STRING:
                # A string alone on its line(s) is a docstring or a commented-out
                # block; one inside an expression is code and must stay.
                line = src.splitlines()[tok.start[0] - 1].strip()
                if line.startswith(('"""', "'''", '"', "'")):
                    for i in range(tok.start[0], tok.end[0] + 1):
                        drop.add(i)
    except (tokenize.TokenError, IndentationError):
        pass
    out = []
    for i, raw in enumerate(lines, start=1):
        if i in drop:
            continue
        code = (raw[:cut[i]] if i in cut else raw).rstrip()
        if not code.strip():
            continue
        out.append((i, code))
    return out


def shape_of(code: str) -> str:
    """Every NAME token replaced by one symbol: "same code, different names".

    Keywords are NOT names -- collapsing `if` and `for` would make every loop
    look like every branch and the ratio would mean nothing.
    """
    import keyword
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, IndentationError):
        return code.strip()
    out = []
    for t in toks:
        if t.type == tokenize.NAME and not keyword.iskeyword(t.string):
            out.append("_")
        elif t.type in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER,
                        tokenize.INDENT, tokenize.DEDENT):
            continue
        else:
            out.append(t.string)
    return " ".join(out)


def compare(a_lines, b_lines, min_run: int = 6):
    """(runs, matched_lines, ratio) over two prepared line lists."""
    sm = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
    runs = [(i, j, n) for i, j, n in sm.get_matching_blocks() if n >= min_run]
    matched = sum(n for _i, _j, n in runs)
    ratio = matched / max(min(len(a_lines), len(b_lines)), 1)
    return runs, matched, ratio


def main(argv=None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    min_run, top, i = 6, 8, 0
    while i < len(raw):
        if raw[i] == "--min-run" and i + 1 < len(raw):
            min_run = int(raw[i + 1]); i += 2; continue
        if raw[i] == "--top" and i + 1 < len(raw):
            top = int(raw[i + 1]); i += 2; continue
        if raw[i].startswith("--"):
            i += 1; continue
        print(__doc__)
        return 2

    fns = _functions()
    missing = [n for n in COPY_PATH + SWAP_PATH if n not in fns]
    if missing:
        print("NOT FOUND: %s -- a rename must not read as 'no duplication'. "
              "0/0 is not a pass." % ", ".join(missing))
        return 3

    def prep(names):
        rows, where = [], []
        for n in names:
            rel, first, lines = fns[n]
            for off, code in strip_noise(lines):
                rows.append(code)
                where.append((rel, first + off - 1, n))
        return rows, where

    a, a_at = prep(COPY_PATH)
    b, b_at = prep(SWAP_PATH)
    require_population(a, "code lines on the copy path")
    require_population(b, "code lines on the body-swap path")

    # STRIPPED, and that is not a shortcut. The two paths sit at different
    # nesting depths, so their leading whitespace differs BY CONSTRUCTION --
    # comparing raw indented text made literal duplication impossible to find
    # and reported 0 runs while an 11-line block was identical word for word.
    lit_runs, lit_n, lit_r = compare([x.strip() for x in a],
                                     [x.strip() for x in b], min_run)
    sa = [shape_of(x) for x in a]
    sb = [shape_of(x) for x in b]
    shp_runs, shp_n, shp_r = compare(sa, sb, min_run)

    print("=" * 76)
    print("TWO-PATH DUPLICATION   (audit F102, re-measured)")
    print("=" * 76)
    print("  copy path      : %s  (%d code lines)"
          % (", ".join(COPY_PATH), len(a)))
    print("  body-swap path : %s  (%d code lines)"
          % (", ".join(SWAP_PATH), len(b)))
    print("  runs counted at >= %d lines" % min_run)
    print()
    print("  %-10s %6s %8s %8s" % ("measure", "runs", "lines", "ratio"))
    print("  %-10s %6d %8d %8.3f" % ("literal", len(lit_runs), lit_n, lit_r))
    print("  %-10s %6d %8d %8.3f" % ("shape", len(shp_runs), shp_n, shp_r))
    print()
    print("      literal = the same TEXT. shape = the same code with different")
    print("      names, which is what a refactor would have to merge.")
    print()
    print("LONGEST SHAPE-IDENTICAL RUNS:")
    print("    `same` = lines that are IDENTICAL TEXT within the run. With")
    print("    literal duplication at 0, a run at 0/n is a pure RENAME; a run")
    print("    with some lines identical and some not is where a port could")
    print("    have been missed, and is the one worth reading.")
    print()
    for i, j, n in sorted(shp_runs, key=lambda r: -r[2])[:top]:
        ra, la, fa = a_at[i]
        rb, lb, fb = b_at[j]
        same = sum(1 for k in range(n) if a[i + k].strip() == b[j + k].strip())
        print("    %3d lines  same %2d/%-3d  %-22s %s:%d"
              % (n, same, n, fa, ra, la))
        print("                            %-22s %s:%d" % (fb, rb, lb))
    if not shp_runs:
        print("    (none at this run length)")
    mixed = [(i, j, n) for i, j, n in shp_runs
             if 0 < sum(1 for k in range(n)
                        if a[i + k].strip() == b[j + k].strip()) < n]
    print()
    print("  runs that are a PURE RENAME (0 lines identical)   : %d"
          % sum(1 for i, j, n in shp_runs
                if not any(a[i + k].strip() == b[j + k].strip()
                           for k in range(n))))
    print("  runs MIXED (some lines identical, some not)       : %d%s"
          % (len(mixed), "   <== read these" if mixed else ""))
    for i, j, n in sorted(mixed, key=lambda r: -r[2])[:3]:
        ra, la, _f = a_at[i]
        rb, lb, _g = b_at[j]
        print("      %s:%d  vs  %s:%d" % (ra, la, rb, lb))
        for k in range(n):
            if a[i + k].strip() != b[j + k].strip():
                print("        copy: %s" % a[i + k].strip()[:66])
                print("        swap: %s" % b[j + k].strip()[:66])
    return 0


if __name__ == "__main__":
    sys.exit(main())
