"""`nif_convert.FIT_STAGES` is the contract the two per-shape fit chains are
held to (audit step 6, increment 2, 2026-09-01).

The table does not run anything. This test derives the REAL sequence of fit
stage calls from `_fit_shapes_copy` and `_fit_shapes_swap` (the loops lifted
verbatim out of the two entry functions) and fails when the code and the
table disagree -- which is the only way "a fix landed on one path only" can
become a red test instead of an archaeology finding.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from src import nif_convert as nc

CALLEES = sorted({row[1] for row in nc.FIT_STAGES})


def _calls(fn_name: str) -> list[tuple[str, tuple[str, ...]]]:
    """(callee, sorted keyword names) for every fit-stage call, source order."""
    src = textwrap.dedent(inspect.getsource(getattr(nc, fn_name)))
    tree = ast.parse(src)
    out = []
    for c in ast.walk(tree):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in CALLEES:
            kws = tuple(sorted(k.arg for k in c.keywords if k.arg))
            out.append((c.lineno, c.func.id, kws))
    out.sort()
    return [(n, k) for _, n, k in out]


def _expected(path: str) -> list[tuple[str, str]]:
    return [(label, callee) for label, callee, paths, _ in nc.FIT_STAGES if path in paths]


def test_table_has_a_reason_for_every_one_path_stage():
    for label, callee, paths, reason in nc.FIT_STAGES:
        if set(paths) != {"copy", "swap"}:
            assert reason, f"{label} ({callee}) runs on {paths} only and gives no reason"


@pytest.mark.parametrize("path,fn", [("copy", "_fit_shapes_copy"), ("swap", "_fit_shapes_swap")])
def test_each_path_calls_exactly_the_stages_the_table_says_in_order(path, fn):
    actual = [n for n, _ in _calls(fn)]
    expected = [callee for _, callee in _expected(path)]
    # the copy path's conform runs twice at the same table position (the
    # push-out-only second call is inside the F094/phase1 reason)
    squashed = [n for i, n in enumerate(actual) if not (i and n == actual[i - 1] == "conform_to_source_standoff")]
    assert squashed == expected, (
        f"{fn} calls {squashed}\nFIT_STAGES says {expected}\n"
        "change the code and the table together")


def test_shared_stages_take_the_same_keywords_unless_the_table_says_why():
    copy, swap = _calls("_fit_shapes_copy"), _calls("_fit_shapes_swap")
    ci = si = 0
    bad = []
    for label, callee, paths, reason in nc.FIT_STAGES:
        if set(paths) != {"copy", "swap"}:
            if "swap" in paths:
                si += 1
            else:
                ci += 1
            continue
        ck = copy[ci][1]; sk = swap[si][1]
        assert copy[ci][0] == callee and swap[si][0] == callee, (label, copy[ci], swap[si])
        ci += 1; si += 1
        if callee == "conform_to_source_standoff" and ci < len(copy) and copy[ci][0] == callee:
            ci += 1                       # the copy path's second (blend=0.0) conform
        if ck != sk and not reason:
            bad.append(f"{label} ({callee}): copy kwargs {ck} vs swap kwargs {sk}")
    assert not bad, ("a shared stage is called differently on the two paths and "
                     "FIT_STAGES gives no reason:\n  " + "\n  ".join(bad))


def _branch_gated(fn_name: str) -> set:
    """Fit-stage callees that appear ONLY inside one arm of an if/else.

    Such a call does not run whenever the function runs -- the other arm does
    something else instead -- so listing it as a plain stage overstates what the
    path does. This is not hypothetical: `snap_armor_outside_body` sat in the
    copy path's legacy `else` (no CBBE base body) while the normal branch ran
    groove-smooth and no snap at all, and the table read `(copy, swap)` with no
    reason. That misreading was quoted as fact before it was measured
    (docs/worklog/BUTT_COPY_PATH_RUBY_FLOWER.md).

    A callee counts as branch-gated only when EVERY one of its call sites sits
    inside an if/else arm and none of them is in the sibling arm. A callee that
    also runs unconditionally somewhere (warp and panel rigidity both run again
    in the fine-animation sub-branch) is NOT gated -- the path does run it."""
    src = textwrap.dedent(inspect.getsource(getattr(nc, fn_name)))
    tree = ast.parse(src)
    # every call site -> the set of if/else arms containing it
    sites: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in CALLEES:
            sites.setdefault(node.func.id, []).append(node)
    arms = []                       # (If node, 'body'|'orelse', {call nodes})
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and node.orelse:
            for which, stmts in (("body", node.body), ("orelse", node.orelse)):
                mod = ast.Module(body=list(stmts), type_ignores=[])
                arms.append((node, which, {c for c in ast.walk(mod)
                                           if isinstance(c, ast.Call)}))
    out = set()
    for name, calls in sites.items():
        covering = []               # per call: the arms it sits in
        for c in calls:
            covering.append({(id(n), w) for n, w, cs in arms if c in cs})
        if any(not cov for cov in covering):
            continue                # at least one call is unconditional
        # gated only if no If has this callee in BOTH arms
        both = False
        for n, w, cs in arms:
            if w != "body":
                continue
            sib = next((s for m, ww, s in arms if m is n and ww == "orelse"), set())
            in_b = any(c in cs for c in calls)
            in_o = any(c in sib for c in calls)
            if in_b and in_o:
                both = True
        if not both:
            out.add(name)
    return out


def test_a_branch_gated_stage_must_say_so():
    """A stage that only runs in one arm of an if/else needs a reason saying so.

    The table cannot infer this: `_calls` walks the AST and sees both arms, so a
    fallback reads exactly like an unconditional stage."""
    # A callee may hold SEVERAL rows (the fine-animation sub-branch repeats warp
    # and panel rigidity), so a reason on ANY of its rows explains the gating --
    # keying a dict by callee would let a later reasonless row hide an earlier
    # explanation.
    explained = {callee for _l, callee, _p, reason in nc.FIT_STAGES if reason}
    bad = []
    for fn in ("_fit_shapes_copy", "_fit_shapes_swap"):
        for callee in sorted(_branch_gated(fn)):
            if callee not in explained:
                bad.append(f"{fn}: {callee} runs in ONE arm of an if/else and "
                           f"FIT_STAGES gives no reason")
    assert not bad, "\n  ".join([""] + bad)


def test_the_branch_gate_check_can_actually_fail():
    """Control: the detector must actually find the known branch-gated call."""
    assert "snap_armor_outside_body" in _branch_gated("_fit_shapes_copy"), (
        "the branch-gate detector no longer sees the copy path's legacy snap -- "
        "either the code changed or the detector is broken; a silent pass here "
        "is the whole failure mode this test exists for")


def test_the_check_can_actually_fail():
    """Control: drop one shared stage from a copy of the copy path and the
    sequence check must say so."""
    src = textwrap.dedent(inspect.getsource(nc._fit_shapes_copy))
    mutated = src.replace("snap_armor_outside_body(", "_snap_gone(", 1)
    assert mutated != src
    tree = ast.parse(mutated)
    names = [c.func.id for c in ast.walk(tree)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in CALLEES]
    assert "snap_armor_outside_body" not in names
    assert [c for _, c in _expected("copy")].count("snap_armor_outside_body") == 1
