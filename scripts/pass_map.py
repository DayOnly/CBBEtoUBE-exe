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

"""GENERATE the pass map: what runs, in what order, on each convert path.

    python scripts/pass_map.py            # write docs/PASS_MAP.md
    python scripts/pass_map.py --check    # exit 1 if the file is out of date

WHY GENERATED AND NOT WRITTEN. `nif_convert.py` is ~28k lines with 41 topic
banners, and not one of them says what runs WHEN -- the execution order had to
be reconstructed by hand twice in one week. A hand-written map would answer that
once and then rot, and this project has already paid for comments describing a
design that no longer exists (`project_comment_audit_2026_08_17`). So the map is
derived from the AST and pinned by `tests/test_pass_map.py`, which runs
`--check`. If the code moves, the test fails and the map is regenerated; it
cannot quietly go stale.

WHAT IT REPORTS, per entry point, in SOURCE ORDER: every call to a function
defined at module level in the same file, with the flag/knob guards that wrap
it. Guards are what make a pass conditional, so a map without them says a pass
runs when it may not.

DELIBERATE LIMITS, stated so the map is not over-read:
  * DEPTH 1. Only calls made DIRECTLY in the entry function's own body are
    listed. A pass reached through a helper does NOT appear -- and that is not
    hypothetical: `_split_bust_collider_shape` runs on both paths via
    `_finalize_physics_and_motion_match`, so grepping this map for it returns
    NOTHING and reads as "never called". It cost a wrong conclusion within a day
    of the map existing. `SHARED_TAILS` below are expanded one extra level for
    exactly that reason; anything else, confirm with a grep before concluding a
    pass is dead.
  * SOURCE order, not runtime order. A loop body is listed once; a call inside a
    branch is listed with its branch condition, not resolved.
  * Only functions defined in THIS module. Numpy, scipy and sibling modules are
    out of scope on purpose -- the question is which of OUR passes run.
  * `convert_nif` DISPATCHES to `convert_nif_phase2`; the two are reported
    separately and the dispatch is marked, never inlined. Flattening it is what
    made an earlier reachability probe read every stage as shared by both paths
    (`feedback_method_traps`).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "nif_convert.py"
OUT = REPO / "docs" / "PASS_MAP.md"

# THE CONVERTER IS THIS LIST OF FILES, not the one file above. Every guard
# that walks "the converter" (this generator, the two-path parity walker, the
# three swallowed-handler scans) reads the list from here, so a pass moved to
# a sibling module stays in every guard's view. `tests/test_split_preconditions`
# pins the list against `src/nif_convert*.py` on disk; a new module must be
# declared here or that test fails. `fit_metrics.minimum_push` is the one
# sibling-module pass already on a production path.
CONVERTER_MODULES = (
    "src/nif_convert.py",
    "src/nif_convert_telemetry.py",   # split step 1: recorders + their state
    "src/nif_convert_skinframe.py",   # split step 2: skin <-> world frame helpers
    "src/nif_convert_bodyrefs.py",    # split step 3: body / shapedata discovery + caches
    "src/nif_convert_trigen.py",   # split step 4
    "src/nif_convert_fitgeom.py",   # split step 5
    "src/nif_convert_bust.py",   # split step 6
    "src/nif_convert_layers.py",   # split step 7
    "src/nif_convert_weights.py",   # split step 8
    "src/nif_convert_physics.py",   # split step 9
    "src/nif_convert_writer.py",   # split step 10
    "src/fit_metrics.py",
)


def sibling_defs() -> dict:
    """name -> lineno for module-level functions in every declared module
    other than SRC. A call to one of these from an entry point is a pass and
    must keep its row."""
    out = {}
    for rel in CONVERTER_MODULES:
        p = REPO / rel
        if p.resolve() == SRC.resolve() or not p.is_file():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(n.name, n.lineno)
    return out

ENTRIES = ("convert_nif", "convert_nif_phase2")

# The per-shape fit loops were lifted out of the entry functions on
# 2026-09-01 (audit step 6, increment 1). They are the entry's own body, so
# their rows are spliced IN PLACE of the call row -- the map still reads as
# one source-ordered chain per path, and the per-entry row floor still holds.
ENTRY_INLINE = {
    "convert_nif": ("_fit_shapes_copy",),
    "convert_nif_phase2": ("_fit_shapes_swap",),
}

# Helpers both paths funnel through. Expanded ONE extra level so the passes
# inside them are not invisible; listed explicitly rather than recursing,
# because an unbounded walk across the dispatch boundary makes every stage
# read as shared by both paths (feedback_method_traps).
SHARED_TAILS = ("_finalize_physics_and_motion_match",)

# Calls that are bookkeeping rather than a pass. Kept SHORT and explicit: a
# generous filter would quietly hide a real pass, which is the failure this file
# exists to prevent.
NOISE = {
    "print", "len", "int", "float", "str", "bool", "list", "dict", "set",
    "tuple", "sorted", "range", "enumerate", "zip", "min", "max", "abs",
    "sum", "any", "all", "isinstance", "getattr", "setattr", "hasattr",
    "repr", "format", "next", "iter", "super", "type", "round",
}


def module_level_defs(tree) -> dict:
    """name -> lineno for every function defined at module level, plus every
    name bound by a module-level `from .<converter sibling> import ...` --
    a pass that moved out of this file keeps its row, anchored at the
    import."""
    sib = {Path(rel).stem for rel in CONVERTER_MODULES}
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = n.lineno
        elif (isinstance(n, ast.ImportFrom) and n.module
              and n.module.split(".")[-1] in sib):
            for a in n.names:
                if a.name != "*":
                    out.setdefault(a.asname or a.name, n.lineno)
    return out


def _segment(lines, node) -> str:
    """Source text of `node`, using a pre-split `lines`.

    NOT `ast.get_source_segment`: that re-splits the WHOLE source on every
    call, and this module is 1.2 MB. Profiled -- 513 calls cost 150 of the
    generator's 151 seconds (757M `len()` calls inside `_splitlines_no_ff`),
    which made the pinning test slower than the entire rest of the suite.
    Splitting once is the same answer in well under a second.
    """
    a, b = node.lineno - 1, node.end_lineno - 1
    if a == b:
        return lines[a][node.col_offset:node.end_col_offset]
    out = [lines[a][node.col_offset:]]
    out.extend(lines[a + 1:b])
    out.append(lines[b][:node.end_col_offset])
    return "\n".join(out)


def flag_consts(tree, text, lines=None) -> dict:
    """NAME -> default source, for every module-level `_flag`/`_knob` binding."""
    import re
    if lines is None:
        lines = text.splitlines()
    out = {}
    for n in tree.body:
        if not isinstance(n, ast.Assign) or len(n.targets) != 1:
            continue
        t = n.targets[0]
        if not isinstance(t, ast.Name):
            continue
        seg = _segment(lines, n.value)
        m = re.search(r'_(?:flag|knob)\(\s*"(CBBE2UBE_[A-Z0-9_]+)"\s*,'
                      r'\s*([^)]*)\)', seg)
        if m:
            out[t.id] = (m.group(1), m.group(2).strip())
    return out


def guard_names(node, parents, consts) -> list:
    """Flag/knob constants appearing in the `if`/`while` tests above `node`."""
    seen, out = set(), []
    cur = node
    while cur is not None:
        p = parents.get(cur)
        if isinstance(p, (ast.If, ast.While)):
            for sub in ast.walk(p.test):
                if (isinstance(sub, ast.Name) and sub.id in consts
                        and sub.id not in seen):
                    seen.add(sub.id)
                    out.append(sub.id)
        cur = p
    return list(reversed(out))


def build_parents(root) -> dict:
    parents = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def stage_label(node, text) -> "str | None":
    """`_stage('warp', v)` -> 'warp'. These are the measured chain boundaries."""
    f = node.func
    name = f.id if isinstance(f, ast.Name) else None
    if name not in ("_stage", "_stage_p1", "_stage_hf", "_stage_hf2"):
        return None
    if node.args and isinstance(node.args[0], ast.Constant):
        return str(node.args[0].value)
    return None


def collect(fn, defs, consts, parents, text) -> list:
    rows = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else None)
        if not name or name in NOISE:
            continue
        lbl = stage_label(node, text)
        if lbl is None and name not in defs:
            continue
        rows.append({
            "line": node.lineno,
            "name": name,
            "stage": lbl,
            "guards": guard_names(node, parents, consts),
            "def_at": defs.get(name),
        })
    rows.sort(key=lambda r: r["line"])
    # One row per (line, name): a call inside a comprehension can be visited
    # twice by ast.walk.
    seen, uniq = set(), []
    for r in rows:
        k = (r["line"], r["name"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def render(text) -> str:
    tree = ast.parse(text)
    src_lines = text.splitlines()
    defs = module_level_defs(tree)
    for name, ln in sibling_defs().items():     # a sibling's pass keeps its row
        defs.setdefault(name, ln)
    consts = flag_consts(tree, text, src_lines)
    # POPULATION FLOOR. This generator is coupled to two idioms -- the
    # module-level `def` and the `_flag()/_knob()` binding -- and if either moves
    # it emits a SMALL, TIDY, WRONG map instead of failing. That is exactly how
    # `flag_extract.py` came to report 11 flags against a real surface of 287
    # and still read as an answer; `scripts/tool_audit.py` exists for the class.
    # 298 defs / 287 constants on 2026-08-23, so these floors sit far below the
    # truth and catch only a collapse.
    if len(defs) < 100 or len(consts) < 100:
        raise SystemExit(
            f"pass_map: POPULATION COLLAPSED -- {len(defs)} module-level def(s) "
            f"and {len(consts)} flag/knob constant(s) in {SRC.name}. The idiom "
            f"this generator greps for has moved, so the map it would write is "
            f"not slightly wrong; it describes a different codebase.")
    parents = build_parents(tree)
    fns = {n.name: n for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    L = []
    L.append("# Pass map — what runs, in what order, on each convert path")
    L.append("")
    L.append("<!-- GENERATED by scripts/pass_map.py. Do not edit by hand: "
             "tests/test_pass_map.py regenerates this and fails if it "
             "differs. -->")
    L.append("")
    L.append(f"`src/nif_convert.py` defines **{len(defs)}** module-level "
             f"functions and binds **{len(consts)}** flag/knob constants. The "
             "two entry points below are the whole conversion surface; "
             "`convert_nif` dispatches to `convert_nif_phase2` for a body-swap "
             "piece and otherwise runs the copy path itself.")
    L.append("")
    L.append("Read the **Stage** column first: those are the checkpoints the "
             "survival trace measures (`CBBE2UBE_SURVIVAL_TRACE=1`), so they "
             "are the passes whose effect on the shipped mesh has actually "
             "been quantified. Everything else runs but is not per-shape "
             "traced. See `docs/PIPELINE.md` for what the labels mean.")
    L.append("")
    L.append("**Guards** are the flag/knob constants in the `if` tests wrapping "
             "the call. A pass with a guard does not necessarily run.")
    L.append("")

    L.append("## Section index")
    L.append("")
    L.append("The file's own topic banners, in order — generated, so it cannot "
             "drift from the source the way a hand-kept index would.")
    L.append("")
    import re as _re
    for i, line in enumerate(src_lines, 1):
        m = _re.match(r"^# [-=]{2,}\s*(.*?)\s*[-=]{2,}\s*$", line)
        if m and m.group(1):
            L.append(f"- {i}: {m.group(1)}")
    L.append("")

    for entry in (*ENTRIES, *SHARED_TAILS):
        fn = fns.get(entry)
        if fn is None:
            continue
        rows = collect(fn, defs, consts, parents, text)
        inline = [n for n in ENTRY_INLINE.get(entry, ()) if n in fns]
        if inline:
            spliced = []
            for r in rows:
                if r["name"] in inline:
                    sub = collect(fns[r["name"]], defs, consts, parents, text)
                    for s in sub:
                        s["via"] = r["name"]
                    spliced.extend(sub)
                else:
                    spliced.append(r)
            rows = spliced
        staged = [r for r in rows if r["stage"]]
        L.append(f"## `{entry}` — line {fn.lineno}")
        if inline:
            L.append("")
            L.append("The per-shape fit loop was lifted into `"
                     + "`, `".join(inline) + "` on 2026-09-01; its rows are "
                     "listed below at the call site, in source order, as if "
                     "still inline.")
        L.append("")
        if entry in SHARED_TAILS:
            L.append("**Shared tail, reached from BOTH entry points above.** "
                     "It is listed separately because this map is depth-1: "
                     "these passes do not appear in either entry's own table, "
                     "and grepping for one there wrongly reads as "
                     "\"never called\".")
            L.append("")
        L.append(f"{len(rows)} call(s) to module-level passes; "
                 f"{len(staged)} traced stage boundary/-ies.")
        L.append("")
        if staged:
            L.append("Traced chain, in source order: "
                     + " → ".join(f"`{r['stage']}`" for r in staged))
            L.append("")
        L.append("| line | stage | pass | guarded by |")
        L.append("|---:|---|---|---|")
        for r in rows:
            g = ", ".join(f"`{x}`" for x in r["guards"]) or ""
            st = f"**{r['stage']}**" if r["stage"] else ""
            nm = r["name"]
            nm = f"`{nm}`" if r["def_at"] else f"`{nm}` *(stage marker)*"
            # THE DISPATCH IS THE MOST IMPORTANT LINE IN THE FILE: everything
            # after it in `convert_nif` is the COPY path only. Flattening the
            # two made an earlier probe read every stage as shared.
            if r["name"] == "convert_nif_phase2" and entry == "convert_nif":
                nm += " — **DISPATCH: a body-swap piece returns here; "
                nm += "everything below is the COPY path only**"
            L.append(f"| {r['line']} | {st} | {nm} | {g} |")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if docs/PASS_MAP.md is out of date")
    args = ap.parse_args()
    text = SRC.read_text(encoding="utf-8")
    want = render(text)
    if args.check:
        have = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if have != want:
            print(f"{OUT.relative_to(REPO)} is OUT OF DATE -- regenerate with "
                  f"`python scripts/pass_map.py`", file=sys.stderr)
            return 1
        print(f"{OUT.relative_to(REPO)} is current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" so the file does not flip line endings on Windows
    # (feedback_windows_line_endings: a CRLF flip is a fake diff).
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(want)
    print(f"wrote {OUT.relative_to(REPO)} ({want.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
