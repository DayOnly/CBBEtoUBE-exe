"""`#phase1-antipoke` -- the copy path's body repair (BUG-02).

The copy path ran NOTHING that pushes a vert out of the body in its normal
branch: `snap_armor_outside_body` is the ELSE of "a CBBE base body exists", and
the anti-poke and the bust/butt inflate are body-swap only. Measured on the
piece an in-game report named (ruby-flower pants): the author's own trousers
have 0 of 2594 butt-band verts inside their CBBE body, ours had 1535 of 2589
inside the UBE body. With this flag: 0.

These tests pin the things that make it SAFE, because each is a way the pass
could silently become wrong rather than fail loudly.
"""
from __future__ import annotations

import ast
import inspect
import os
import textwrap

import pytest

from src import gui_settings as gs
from src import nif_convert as nc


def _copy_src() -> str:
    return textwrap.dedent(inspect.getsource(nc._fit_shapes_copy))


def test_off_by_default_with_a_working_switch():
    """OFF until judged on a population. One in-game look at one outfit is a
    verdict on that outfit, not on 78% of the pack."""
    import importlib
    assert nc.PHASE1_ANTIPOKE is False
    os.environ["CBBE2UBE_PHASE1_ANTIPOKE"] = "1"
    try:
        importlib.reload(nc)
        assert nc.PHASE1_ANTIPOKE is True, (
            "CBBE2UBE_PHASE1_ANTIPOKE=1 no longer turns the pass on -- the "
            "documented way to reproduce every measurement is dead")
    finally:
        del os.environ["CBBE2UBE_PHASE1_ANTIPOKE"]
        importlib.reload(nc)
    assert nc.PHASE1_ANTIPOKE is False


def test_it_is_reachable_from_the_gui():
    """A flag with no Setting row can only be set by hand in the environment.

    The GUI is the ONLY caller of `apply_env`, and `apply_env` manages
    registered settings only -- so without a row this pass cannot be switched
    on by a normal run at all, however correct the code is."""
    row = next((s for s in gs.SETTINGS if s.key == "phase1_antipoke"), None)
    assert row is not None, "no Setting row: unreachable from a normal run"
    assert row.env == "CBBE2UBE_PHASE1_ANTIPOKE"
    assert row.default is False and row.invert is False


def test_the_push_and_its_repair_travel_together():
    """Phase 2 pairs the anti-poke with `_rigidify_within_clearance`: the push
    re-deforms every panel it moves and the recovery restores what clearance
    allows. Porting the push ALONE would ship the damage and not the repair --
    a softened plate is the defect `#panel-rigidity` exists to prevent."""
    src = _copy_src()
    assert "clear_armor_outside_body" in src
    i = src.index("clear_armor_outside_body")
    assert "_rigidify_within_clearance" in src[i:], (
        "the copy-path anti-poke has no recovery call after it")


def test_simulated_verts_are_restored_after_the_push():
    """Pushing a SIMULATED vert out of the body fights the sim -- HDT-SMP
    decides where that vert goes at runtime, so a pushed rest position is
    simply wrong. Phase 2 restores them; this asserts the copy path does too,
    using the same chain-weight mask its panel rigidity already uses."""
    src = _copy_src()
    # the CALL, not the name -- it appears in this block's own comments first
    i = src.index("clear_armor_outside_body(")
    after = src[i:i + 3000]
    assert "_skip_p1" in after, (
        "no mixed-cloth restore after the copy-path anti-poke: simulated cloth "
        "would ship pushed, fighting its own simulation")


def test_the_smp_overshoot_cap_is_armed_and_callable():
    """A collision-only SMP shape pushed by the full default spreads verts on
    the convex bust and opens new gaps (#smp-collision-only-antipoke), so the
    push is clamped for those shapes.

    The gate sits in a try/except so it can never fail a conversion -- which is
    exactly how it could silently never arm. So check the call it depends on
    still has the signature the gate passes."""
    src = _copy_src()
    assert "SMP_ANTIPOKE_MAX_PUSH" in src, "the SMP overshoot cap is gone"
    sig = inspect.signature(nc._shape_has_hdt_smp_rigging)
    assert len(sig.parameters) == 2, (
        f"_shape_has_hdt_smp_rigging now takes {len(sig.parameters)} args; the "
        f"copy-path SMP gate passes 2 and would silently stop arming")
    assert isinstance(nc.SMP_ANTIPOKE_MAX_PUSH, (int, float))


def test_the_pass_is_guarded_by_its_flag_and_a_body():
    """No flag, or no body to measure against, means no call: that is what
    keeps the OFF path byte-identical (golden 15/15) and stops the pass running
    against a body it does not have."""
    tree = ast.parse(_copy_src())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "clear_armor_outside_body"]
    assert len(calls) == 1, f"expected one copy-path anti-poke, found {len(calls)}"
    guards = [ast.unparse(n.test) for n in ast.walk(tree)
              if isinstance(n, ast.If)
              and any(c in ast.walk(n) for c in calls)]
    joined = " ".join(guards)
    assert "PHASE1_ANTIPOKE" in joined, "the call is not behind its flag"
    assert "body_verts_for_fit" in joined and "body_normals_for_fit" in joined, (
        "the call is not guarded on having a body and its normals")


@pytest.mark.parametrize("name", ["antipoke", "panel_rigid_post"])
def test_the_stage_table_records_both_paths(name):
    """FIT_STAGES must say these now exist on the copy path, with the reason --
    the row for `snap` claimed both paths with no reason while the copy call was
    an else-branch fallback, and that misreading was quoted as fact."""
    row = next(r for r in nc.FIT_STAGES if r[0] == name)
    assert set(row[2]) == {"copy", "swap"}, f"{name} is no longer on both paths"
    # either spelling: the constant or the #tag the comments use
    assert row[3] and ("PHASE1_ANTIPOKE" in row[3]
                       or "phase1-antipoke" in row[3]), (
        f"{name}'s reason must name the flag that gates it on the copy path")
