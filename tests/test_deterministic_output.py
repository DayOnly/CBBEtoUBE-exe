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

"""The converter must emit the same bytes for the same input, run to run.

It did not. Iterating a SET of strings orders by hash, which varies per
process, and that order reached the output three separate ways:

  1. `for bn in touched:` set the order `setShapeWeights` was called, and write
     order decides which influence survives a >4-influence row at save time.
     One vertex on a light cuirass swapped `HideSkirt 5_01` / `6_01` (0.038).
  2. `for sl in all_sliders:` set the MORPH ORDER inside a generated .tri. Same
     morph set, different order -- harmless to the engine, but a permanent
     spurious diff that defeats byte-verification of TRI output.
  3. the 4-influence cap broke EXACT TIES by dict insertion order. Symmetric
     bones tie exactly: `L Breast02` / `R Breast02` at 0.003428 on one vertex of
     a fitted dress.

Cost: the golden regression gate flagged a piece on ~half of runs, and a gate
that cries wolf gets ignored -- a real regression on that piece would have been
waved off as "the flaky one".

These are SOURCE-level guards. The end-to-end proof (46 emitted artifacts --
NIFs, .tri and .xml -- byte/semantically identical across PYTHONHASHSEED 0, 2,
5 and 7) needs real meshes and lives in scratchpad/det_allout.py; two seeds was
NOT enough to catch (3), so use four.
"""
import ast
import inspect
import re
from pathlib import Path

import src.nif_convert as nc

_SRC_DIR = Path(nc.__file__).resolve().parent
ORDER_SENSITIVE = ("setShapeWeights", "add_bone", "set_skin_to_bone_xform")


def _setish(node) -> bool:
    if isinstance(node, (ast.Set, ast.SetComp)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "set":
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitOr, ast.Sub)):
        return _setish(node.left) or _setish(node.right)
    return False


def _set_iterating_writes(path: Path):
    """(line, func, name) for `for x in <set>:` whose body writes skin data."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    out = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        names = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and _setish(n.value):
                names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            elif isinstance(n, ast.AugAssign) and _setish(n.value) \
                    and isinstance(n.target, ast.Name):
                names.add(n.target.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                    and n.value is not None and _setish(n.value):
                names.add(n.target.id)
        for n in ast.walk(fn):
            if not isinstance(n, ast.For):
                continue
            if not (isinstance(n.iter, ast.Name) and n.iter.id in names):
                continue
            calls = {c.func.attr for c in ast.walk(n)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute)}
            if calls & set(ORDER_SENSITIVE):
                out.append((n.lineno, fn.name, n.iter.id))
    return out


# --- order-sensitive write loops: PROVE ORDER, don't try to prove set-ness ---
#
# The original guard here asked "is this iterable a set?" and could prove it
# for almost nothing: measured 2026-08-19, it classified 11 of 1278 candidate
# loops and its intersection with the write set was EMPTY. It asserted over
# nothing and reported clean for months.
#
# Inverted: every loop that performs an order-sensitive write must have a
# PROVABLY ORDERED iterable, or be pinned below with a reason. `sorted()` and
# `range()` are unconditionally ordered; `list()`, `tuple()`, `enumerate()`,
# `reversed()` and comprehensions only inherit their source's order --
# `[x for x in a_set]` is exactly as arbitrary as the set, which is the bug.
_UNCONDITIONAL_ORDER = {"sorted", "range"}
_PASSTHROUGH_ORDER = {"list", "tuple", "enumerate", "reversed"}
# Dict views are insertion-ordered (py3.7+), so deterministic given
# deterministic insertion -- and if the dict was filled by iterating a set,
# THAT loop is the offender and this guard catches it there instead.
_ORDERED_METHODS = {"items", "keys", "values"}
_ORDERED_ATTRS = {"shapes", "bone_names"}      # pynifly returns lists

# Loops whose iterable this walker cannot prove, each judged by hand. Keyed by
# "file:function:variable" and pinned to an exact site count, so a new
# unproven loop fails even if it reuses one of these names.
_KNOWN_UNPROVEN_ORDER = {
    # `bone_names` is a PARAMETER; the callers build it as
    # `bones = list(src_shape.bone_names or [])` -> a list. Cross-function, so
    # a per-function walker cannot see it.
    "nif_convert.py:_install_skin:surviving": 3,
    # `list(s.bone_names or list(bw.keys()))`, later `bone_list + want` --
    # ordered, but through a BoolOp and a BinOp this walker does not model.
    "nif_convert.py:_sync_bust_plate_follow_postwrite:bone_list": 1,
    # *** A GENUINE OFFENDER, KEPT VISIBLE ON PURPOSE. ***
    # `need` is a real set (`need: "set" = set()`), so `to_add` inherits its
    # arbitrary order and feeds `add_bone` -- which fixes the bone PALETTE
    # order in the written NIF. Today this is masked by the interpreter's
    # hash seed being pinned in the spec, so shipped output is stable; remove
    # that pin and this varies per run. Wrapping it in sorted() is the fix,
    # but it CHANGES the palette order and therefore the output bytes, so it
    # needs an A/B and an in-game verdict rather than a silent edit.
    "nif_convert.py:_match_rigid_leg_bend_to_body:to_add": 3,
}


def _iter_is_ordered(node, assigns, depth=0):
    if depth > 6 or node is None:
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        return True
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
        return bool(node.generators) and _iter_is_ordered(
            node.generators[0].iter, assigns, depth + 1)
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            if f.id in _UNCONDITIONAL_ORDER:
                return True
            if f.id in _PASSTHROUGH_ORDER:
                return bool(node.args) and _iter_is_ordered(
                    node.args[0], assigns, depth + 1)
        if isinstance(f, ast.Attribute):
            return f.attr in _ORDERED_METHODS or f.attr in _UNCONDITIONAL_ORDER
        return False
    if isinstance(node, ast.Attribute):
        return node.attr in _ORDERED_ATTRS
    if isinstance(node, ast.Name):
        vals = assigns.get(node.id)
        return bool(vals) and all(
            _iter_is_ordered(v, assigns, depth + 1) for v in vals)
    return False


def _order_sensitive_loops(path):
    """(func, variable-or-expr, ordered?) for every loop that writes skin."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    out = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        assigns = {}
        for n in ast.walk(fn):
            tgts, val = [], None
            if isinstance(n, ast.Assign):
                tgts, val = n.targets, n.value
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                tgts, val = [n.target], n.value
            for t in tgts:
                if isinstance(t, ast.Name) and val is not None:
                    assigns.setdefault(t.id, []).append(val)
        for n in ast.walk(fn):
            if not isinstance(n, ast.For):
                continue
            calls = {c.func.attr for c in ast.walk(n)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute)}
            if not (calls & set(ORDER_SENSITIVE)):
                continue
            name = (n.iter.id if isinstance(n.iter, ast.Name)
                    else ast.unparse(n.iter)[:40])
            out.append((fn.name, name, n.lineno,
                        _iter_is_ordered(n.iter, assigns)))
    return out


def test_every_skin_write_loop_has_a_provably_ordered_iterable():
    """Set iteration order is undefined, and here it reaches the mesh: the
    bone-add order becomes the palette order in the written NIF.

    Coverage is asserted, not assumed -- the guard this replaced classified 11
    of 1278 loops and intersected the write set at ZERO.
    """
    total, unproven = 0, []
    for path in sorted(_SRC_DIR.glob("*.py")):
        for fn, name, ln, ordered in _order_sensitive_loops(path):
            total += 1
            if not ordered:
                unproven.append((f"{path.name}:{fn}:{name}", ln))
    assert total >= 40, (
        f"only {total} order-sensitive write loops found -- the AST walker "
        f"broke; there were 47 on 2026-08-19 and this guard would be vacuous")
    seen = {}
    for key, _ln in unproven:
        seen[key] = seen.get(key, 0) + 1
    new = {k: v for k, v in seen.items() if k not in _KNOWN_UNPROVEN_ORDER}
    assert not new, (
        "a skin-writing loop iterates something this guard cannot prove is "
        "ordered -- wrap it in sorted(), or pin it in _KNOWN_UNPROVEN_ORDER "
        f"with the reason: {new}")
    drift = {k: (v, _KNOWN_UNPROVEN_ORDER[k]) for k, v in seen.items()
             if _KNOWN_UNPROVEN_ORDER.get(k) != v}
    missing = {k for k in _KNOWN_UNPROVEN_ORDER if k not in seen}
    assert not drift and not missing, (
        f"pinned unproven loops changed -- found-vs-pinned {drift}, "
        f"pinned but gone {missing}; update the pin deliberately")


def test_the_order_guard_can_actually_fail():
    """Control: the classifier must reject a set-derived iterable. Without
    this, every assertion above could be passing because the walker says
    'ordered' to everything."""
    src = ("def f(s):\n"
           "    want = set()\n"
           "    want |= other\n"
           "    for b in want:\n"
           "        s.setShapeWeights(b, [])\n")
    tree = ast.parse(src)
    fn = tree.body[0]
    assigns = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    assigns.setdefault(t.id, []).append(n.value)
        elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
            assigns.setdefault(n.target.id, []).append(n.value)
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.For))
    assert not _iter_is_ordered(loop.iter, assigns), (
        "the classifier called a set-derived iterable ordered")
    assert _iter_is_ordered(ast.parse("sorted(want)").body[0].value, assigns), (
        "the classifier failed to recognise sorted() as ordered")


def test_influence_cap_breaks_ties_deterministically():
    """`sorted(live, key=live.get)` is STABLE, so equal weights fall back to
    insertion order. Symmetric bones tie exactly, so this is reachable, not
    theoretical."""
    src = inspect.getsource(nc._cap_and_renormalise_rows)
    assert "key=lambda b: (-live[b], b)" in src, (
        "the 4-influence cap must break ties on a total order (weight, name)")
    assert "key=live.get" not in src


def test_cap_still_prefers_the_largest_weights():
    """The tie-break must not have changed WHICH weights win -- only how ties
    resolve. Guards against a sort-key edit that silently keeps the smallest."""
    vw = [{"a": 0.5, "b": 0.3, "c": 0.1, "d": 0.05, "e": 0.05}]
    nc._cap_and_renormalise_rows(vw, 1, set("abcde"))
    kept = {k for k, v in vw[0].items() if v > 1e-6}
    assert "a" in kept and "b" in kept and "c" in kept
    assert len(kept) == nc._SKIN_MAX_INFLUENCES
    assert abs(sum(vw[0][k] for k in kept) - 1.0) < 1e-6


def test_cap_tie_resolution_is_stable_across_insertion_order():
    """The actual property, exercised rather than asserted on source: the same
    weights in a different dict order must keep the same bones."""
    a = [{"L Breast02": 0.2, "R Breast02": 0.2, "x": 0.4, "y": 0.3, "z": 0.1}]
    b = [{"z": 0.1, "y": 0.3, "R Breast02": 0.2, "x": 0.4, "L Breast02": 0.2}]
    nc._cap_and_renormalise_rows(a, 1, set(a[0]))
    nc._cap_and_renormalise_rows(b, 1, set(b[0]))
    assert {k for k, v in a[0].items() if v > 1e-6} == \
           {k for k, v in b[0].items() if v > 1e-6}


def test_generated_tri_morph_order_is_deterministic():
    """A .tri whose morph order varies is a permanent spurious diff: the engine
    matches morphs by name, so it is harmless in game and invisible in review,
    which is precisely why it would never get fixed."""
    gen = (_SRC_DIR / "sliderset_gen.py").read_text(encoding="utf-8",
                                                    errors="ignore")
    assert "for sl in sorted(all_sliders):" in gen
    assert "for sl in all_sliders:" not in gen


def test_fit_passes_record_failures_instead_of_swallowing_them():
    """A pass that RAISES must not be indistinguishable from one that ran and
    did nothing.

    Every fit pass is called inside `try: ... except Exception: pass`. The catch
    is right -- one bad piece must not abort a 4000-piece batch -- but the
    silence is not: a stale keyword argument once raised TypeError on every
    build, the pass never ran, and the measurements read as "the design does not
    work". Three wrong verdicts before anyone thought to grep the log.
    """
    src = inspect.getsource(nc)
    for pass_name in ("_match_arm_motion_to_body", "_match_spine_motion_to_body",
                      "_match_leg_motion_to_body", "_transfer_body_jiggle_to_fitted",
                      "_conform_fitted_to_body", "_match_rigid_leg_bend_to_body"):
        assert f'_note_pass_failure("{pass_name}"' in src, (
            f"{pass_name} failures are still swallowed silently")


def test_pass_failure_recorder_never_raises():
    """It runs INSIDE an except-handler. A recorder that can throw turns a
    survivable pass failure into a lost piece."""
    body = inspect.getsource(nc._note_pass_failure)
    assert "except Exception:" in body and body.rstrip().endswith("pass")
    nc._note_pass_failure("probe", ValueError("x"), dst=None)
    nc._note_pass_failure("probe", ValueError("x"), dst=object())   # bad dst
    assert any(k.startswith("probe") for k in nc.pass_failure_summary())


def test_pass_failures_travel_home_in_ConvertResult_reason():
    """The batch runs on ProcessPoolExecutor, so a worker's module state and its
    stderr NEVER reach the parent -- the frozen exe discards pool-worker output
    outright. `reason` is the only channel that survives the boundary, and
    auto_convert already writes it into the report and the failures file.

    Verified end-to-end by breaking a pass inside a real worker process: the
    parent's pass_failure_summary() came back EMPTY (as it must) while
    ConvertResult.reason carried `PASS FAILED _match_arm_motion_to_body`.
    """
    src = inspect.getsource(nc)
    assert "_begin_piece_pass_log()" in src
    assert "_piece_pass_failures()" in src
    # both result assemblies -- the copy path and the body-swap path
    assert src.count("_piece_pass_failures()") >= 3, (
        "every ConvertResult path must carry pass failures home")


def test_piece_log_is_reset_at_the_single_entry_point_only():
    """phase 2 is reached THROUGH convert_nif, so resetting in both would throw
    away everything phase 1 recorded."""
    src = inspect.getsource(nc)
    assert src.count("_begin_piece_pass_log()") == 2, (
        "expected exactly one definition and one call site")


def _silent_handlers_wrapping(names, path):
    """(line, func) for `except: pass` whose try body calls one of `names`."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Try):
            continue
        if not any(len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
                   for h in n.handlers):
            continue
        called = set()
        for c in ast.walk(ast.Module(body=n.body, type_ignores=[])):
            if isinstance(c, ast.Call):
                f = c.func
                called.add(f.id if isinstance(f, ast.Name) else
                           (f.attr if isinstance(f, ast.Attribute) else ""))
        hit = called & set(names)
        if hit:
            fn = "?"
            cur = n
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = cur.name
                    break
            out.append((n.lineno, fn, sorted(hit)))
    return out


def _all_silent_handlers(path):
    """Every `except: pass` Try in the module, with the call names its try
    body makes. The unfiltered population -- callers assert their own floor on
    it, so a broken walker reads as a FAILURE, not as a clean sweep."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Try):
            continue
        if not any(len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
                   for h in n.handlers):
            continue
        called = set()
        for c in ast.walk(ast.Module(body=n.body, type_ignores=[])):
            if isinstance(c, ast.Call):
                f = c.func
                called.add(f.id if isinstance(f, ast.Name) else
                           (f.attr if isinstance(f, ast.Attribute) else ""))
        fn = "?"
        cur = n
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = cur.name
                break
        out.append((n.lineno, fn, called))
    return out


def test_no_fit_pass_failure_is_swallowed_silently():
    """A pass that RAISES must never be indistinguishable from one that ran and
    did nothing -- the shape of the failure that cost three wrong verdicts.

    THE POPULATION IS DERIVED, NOT HAND-LISTED. The first version of this
    guard listed 16 pass names by hand; an audit (2026-08-18) found the module
    had grown to 111 silent handlers wrapping 40 distinct converter functions,
    and the list matched ZERO of them -- the guard policed an empty set for
    months and read as assurance. Now the protected set is every name the
    module itself records via `_note_pass_failure("<name>", ...)`: if a
    function records at one call site, a bare `except: pass` around it at
    another site is exactly the sibling-drift defect this test exists for.
    """
    src = Path(nc.__file__).read_text(encoding="utf-8", errors="ignore")
    recorded = {m.group(1).split("/")[0] for m in
                re.finditer(r'_note_pass_failure\(\s*[\'"]([^\'"]+)[\'"]', src)}
    # Population sanity: the recording convention is in heavy use. A collapse
    # here means the derivation broke, not that the module got clean.
    assert len(recorded) >= 40, (
        f"only {len(recorded)} recorded pass names found -- the population "
        f"derivation is broken, this guard would be vacuous")
    handlers = _all_silent_handlers(nc.__file__)
    assert len(handlers) >= 60, (
        f"only {len(handlers)} silent handlers found (there were 111 on "
        f"2026-08-18) -- the AST walker is broken, not the module clean")
    bad = [(ln, fn, sorted(called & recorded))
           for ln, fn, called in handlers if called & recorded]
    assert not bad, (
        "a pass that records via _note_pass_failure elsewhere is swallowed "
        "bare here -- route it through _note_pass_failure: " +
        "; ".join(f"{f}():{ln} ({','.join(h)})" for ln, f, h in bad))


def test_no_mesh_write_is_swallowed_silently():
    """A swallowed SAVE is worse than a swallowed pass: the piece ships
    unconverted or half-written while reporting success.

    Each guarded name must EXIST as a call in the module -- a renamed write
    API would otherwise rot this list into policing nothing (the 2026-08-18
    audit's defect class: a filter whose coverage nobody asserts).
    """
    WRITES = ("atomic_nif_save", "setShapeWeights", "add_bone")
    handlers = _all_silent_handlers(nc.__file__)
    assert len(handlers) >= 60, (
        f"only {len(handlers)} silent handlers found -- walker broken")
    all_calls = set()
    for _, _, called in handlers:
        all_calls |= called
    tree = ast.parse(Path(nc.__file__).read_text(encoding="utf-8",
                                                 errors="ignore"))
    module_calls = set()
    for c in ast.walk(tree):
        if isinstance(c, ast.Call):
            f = c.func
            module_calls.add(f.id if isinstance(f, ast.Name) else
                             (f.attr if isinstance(f, ast.Attribute) else ""))
    for w in WRITES:
        assert w in module_calls, (
            f"{w!r} is not called anywhere in nif_convert -- the WRITES list "
            f"has rotted; update it to the current write API")
    bad = [(ln, fn, sorted(called & set(WRITES)))
           for ln, fn, called in handlers if called & set(WRITES)]
    assert not bad, (
        "mesh write swallowed silently at " +
        "; ".join(f"{f}():{ln} ({','.join(h)})" for ln, f, h in bad))
