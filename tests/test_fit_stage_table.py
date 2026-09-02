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
