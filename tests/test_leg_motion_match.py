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

"""Leg-motion weight match (#leg-motion-match) -- see clipping log F1.

A garment that keeps its SOURCE skin carries CBBE-fitted leg weights over a UBE
body, so the body out-travels it under hip flexion and skin emerges. The pass
rebalances the garment's leg-bone weights toward the body's.

Every invariant here cost a measured wrong result while building it, so each test
names the failure it guards against.
"""
import importlib
import inspect
import os

import numpy as np

import src.nif_convert as nc
from src.weights import plan_weight_writes

_TEST_BONES = ["A", "B", "C", "D", "E"]


def _plan(weights, had=None, rows=None):
    """Run the pass's write planner over one or more rows of intended weights."""
    W = np.asarray(weights, dtype=np.float64)
    if had is None:
        had = W > 0
    if rows is None:
        rows = np.arange(W.shape[0])
    return plan_weight_writes(W, rows, _TEST_BONES, np.asarray(had, dtype=bool))


def _written(plan, vert):
    """{bone: weight} actually written for `vert`, including 0.0 removals."""
    return {b: w for b, pairs in plan.items() for v, w in pairs if v == vert}


def test_flag_default_on_and_kill_switch(monkeypatch):
    assert nc.MATCH_LEG_MOTION is True
    monkeypatch.setenv("CBBE2UBE_NO_LEG_MOTION_MATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MATCH_LEG_MOTION is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_LEG_MOTION_MATCH", raising=False)
        importlib.reload(nc)


def test_pass_is_wired_into_both_convert_paths():
    src = inspect.getsource(nc)
    # One call site per convert path; a pass defined but never called is the
    # failure mode that made the UBE-native backstop dead code for weeks.
    assert src.count("_match_leg_motion_to_body(dst_path") >= 2


def test_row_gate_has_a_body_bone_fallback_when_no_BaseShape_is_injected():
    """Without this, the row gate rejects EVERY row on 26 of 150 sampled
    pieces and both the arm and spine passes silently do nothing there.

    `ube_bones` is read off the injected BaseShape. 110 of 150 converted
    outputs have none -- every boot and gauntlet among them -- and 26 of those
    also carry a physics XML, which is the combination that makes the gate
    fire at all. With an empty body-bone set EVERY bone reads foreign, so
    `foreign <= 1e-4` is false for every vert and nothing is written. A
    silent no-op, indistinguishable in the logs from a clean run.
    """
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "if not ube_bones:" in src
    assert "_body_bones" in src
    # and it must be populated BEFORE the per-shape loop consults it
    fallback = src.index("if not ube_bones:")
    gate = src.index("_row_gate = False")
    assert fallback < gate, (
        "the body-bone fallback must run before the row gate reads it")


def test_empty_body_bone_set_would_reject_every_row():
    """The arithmetic behind the test above, so the reason survives a refactor.

    Any bone not in the body set counts as foreign; with no body set, foreign
    weight equals the vert's whole (normalised) weight, which is 1.0 -- never
    <= 1e-4. This is why the fallback is load-bearing and not defensive."""
    shape_bones = ["NPC Spine2 [Spn2]", "NPC L Clavicle [LClv]"]
    G = np.array([[0.6, 0.4], [0.5, 0.5]])
    for body_bones, expect_any in ((set(), False), (set(shape_bones), True)):
        foreign = np.zeros(len(G))
        for j, b in enumerate(shape_bones):
            if b not in body_bones:
                foreign += G[:, j]
        assert bool((foreign <= 1e-4).any()) is expect_any


def test_leg_pass_uses_the_per_row_smp_fallback():
    """#smp-row-gate. The shape-level SMP heuristic fires on the main garment of
    pieces that carry a generated physics XML, and this pass then did nothing at
    all on them -- measured over 400 converted pieces, 39 leg-bearing shapes were
    gated out entirely, and every one had rows that survive the per-row test.

    Measured effect (hip band, follow ratio, verts below 0.5):
        dragonbone cuirass  0.882 -> 1.119   21.8% -> 12.0%
        draugr chain        1.006 -> 1.092    9.6% ->  3.4%
    Weights only: zero vertex movement on either weight of all three probes.
    """
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "smp_row_gate=True" in src


def test_row_fallback_never_relaxes_the_collider_or_softbody_skip():
    """THE CONTROL on the row gate. It relaxes ONLY the bone-count heuristic.

    The collider/soft-body skips are a standing rule and are what makes the
    documented Markarth/Morthal behaviour correct (those shapes are declared as
    per-vertex soft bodies, so declining to rewrite them is right, not a miss).
    If the row gate ever moved above them, this pass would start rewriting
    simulated cloth and that resolution would silently become wrong.
    """
    src = inspect.getsource(nc._match_limb_motion_to_body)
    skip = src.index("in collider_names or")
    gate = src.index("_row_gate = False")
    assert skip < gate, (
        "the collider/softbody skip must stay ABOVE the row-gate fallback")
    assert "if not smp_row_gate:" in src


def test_skips_colliders_and_softbody():
    """Standing rule: every skin pass leaves authored physics geometry alone --
    a skin pass touching an SMP collider CTDs, and touching softbody drifts.

    The three checks are NOT equivalent any more, and lumping them together is
    how this test went stale. Collider and soft-body membership come FROM the
    physics XML and are absolute. `_shape_has_hdt_smp_rigging` is a NAME-COUNT
    heuristic (>40% of a shape's bones unknown to the body) and since
    #smp-row-gate it no longer skips the whole shape for an opted-in family --
    it falls back to the same test applied per ROW. Asserting all three
    identically implied a guarantee the middle one no longer gives.
    """
    src = inspect.getsource(nc._match_limb_motion_to_body)
    # ABSOLUTE -- these must keep short-circuiting the whole shape
    assert "_hdt_collider_shape_names" in src
    assert "_hdt_softbody_shape_names" in src
    assert "in collider_names or" in src, (
        "collider/softbody membership must still skip the shape outright")
    # CONDITIONAL by design -- the heuristic, not the XML
    assert "if not smp_row_gate:" in src, (
        "the SMP-rigging heuristic must be the conditional one: a family that "
        "opts in falls back to the per-row test instead of skipping the shape")
    assert "_shape_has_hdt_smp_rigging" in src


def test_never_moves_a_vertex():
    """The pass must only touch WEIGHTS. Moving verts would undo the bind-pose
    clearance the earlier passes established."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert ".verts =" not in src
    assert "set_shape_verts" not in src
    assert "setShapeWeights" in src


def test_saves_and_restores_skin_to_bone():
    """setShapeWeights can reset a shape's skin-to-bone transforms; an STB left at
    identity skins the shape to the origin and the armor explodes in game."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "get_shape_skin_to_bone" in src
    assert "set_skin_to_bone_xform" in src


def test_push_up_only():
    """Never LOWER a leg share -- a garment already tracking the body is left be."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "np.maximum(" in src


def _shipped_cap_then_floor(new_row, g_row=None):
    """Replicate the pass's own cap+floor arithmetic (nif_convert, the
    `_SKIN_MAX_INFLUENCES` prune followed by `#legmotion-normalise`) so the tests
    below assert what SHIPS rather than what a helper would do.

    Hand-copied from `_match_leg_motion_to_body`; if that block changes, this must
    change with it (the source assertions in these tests are the tripwire).

    TWO ARGUMENTS ON PURPOSE. In the pass, `G` is the PRE-match weight matrix and
    `NEW` is the POST-match one, so `_had = G[rows] > 1e-4` means "a bone the
    vertex carried BEFORE the match". Passing one row for both (the default)
    models a vert whose weights were only re-split, and the floor then restores a
    capped bone -> 5 influences ship. Passing them separately models the case the
    pass exists for -- a GRAFT, where `NEW` carries a bone `G` never had -- and
    there the graft is the smallest, the cap drops it, and `_had` does NOT bring
    it back: 4 influences ship and the graft is LOST. Aliasing the two hides that
    entirely."""
    NEW = np.array([new_row], dtype=np.float64)
    G = np.array([new_row if g_row is None else g_row], dtype=np.float64)
    rows = np.array([0])
    cap = nc._SKIN_MAX_INFLUENCES
    if NEW.shape[1] > cap:
        sub = NEW[rows]
        cut = np.argsort(sub, axis=1)[:, :-cap]
        np.put_along_axis(sub, cut, 0.0, axis=1)
        ss = sub.sum(axis=1)
        good = ss > 1e-6
        sub[good] /= ss[good, None]
        NEW[rows] = sub
    _sub = NEW[rows]
    _had = G[rows] > 1e-4
    _sub = np.where(_had & (_sub <= nc._WRITE_MIN), nc._WRITE_MIN * 2.0, _sub)
    _ss = _sub.sum(axis=1)
    _ok = _ss > 1e-6
    _sub[_ok] /= _ss[_ok, None]
    if (~_ok).any():
        _sub[~_ok] = G[rows][~_ok]
    NEW[rows] = _sub
    return NEW[0], G[0]


def test_prunes_to_the_skin_partition_influence_cap():
    """Skyrim holds 4 influences per vertex. Exceed it and the SAVE keeps the
    LARGEST 4 and does NOT renormalise (measured 2026-07-25: a row given two extra
    bones came back summing 1.160), scrambling the computed split (measured:
    65 -> 18 newly-exposed written vs 65 -> 0 for the same weights in memory).
    The pass prunes the smallest itself so the result is deterministic.

    KNOWN, DELIBERATE CONTRADICTION -- pinned here so nobody "fixes" it blind:
    the `#legmotion-normalise` floor below restores every bone the vertex ALREADY
    had, including one the cap just dropped, so a capped row ships with FIVE
    influences and the save resolves it after all. The floor exists because
    `setShapeWeights` merges (an omitted weight keeps its stale value), and the
    two requirements genuinely conflict. See DESIGN_P6: the resolution is an
    explicit 0.0 write, attempted and reverted because it measured neutral."""
    assert nc._SKIN_MAX_INFLUENCES == 4
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_SKIN_MAX_INFLUENCES" in src
    assert "put_along_axis" in src
    out, _ = _shipped_cap_then_floor([0.50, 0.20, 0.15, 0.10, 0.05])
    written = int((out > nc._WRITE_MIN).sum())
    assert written == 5, (
        "the cap-then-floor pair writes 5, not 4 -- if this becomes 4 the floor "
        "was removed, and stale merged weights will over-weight the vertex")
    assert abs(out.sum() - 1.0) < 1e-9, "the row must still sum to 1"


def test_floor_sits_above_the_write_threshold():
    """RESTORED COVERAGE. The floor must land STRICTLY above `_WRITE_MIN`, or the
    floored value is itself dropped by the write filter and the stale weight
    survives anyway -- defeating the whole point of flooring."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_WRITE_MIN * 2.0" in src
    assert "> _WRITE_MIN" in src, "the write must use the same named threshold"
    out, _ = _shipped_cap_then_floor([0.50, 0.20, 0.15, 0.10, 0.05])
    floored = out[out < 0.01]
    assert floored.size and (floored > nc._WRITE_MIN).all(), (
        "a floored bone must survive the write filter")


def test_floor_detects_bones_the_vertex_already_had():
    """RESTORED COVERAGE. The floor is gated on `_had` -- only bones the vertex
    ALREADY carried are restored. A bone it never had must stay at zero, or the
    pass invents an influence."""
    assert "_had = G[rows] > 1e-4" in inspect.getsource(
        nc._match_limb_motion_to_body)
    # 5th column was never present -> must not be floored into existence.
    out, _ = _shipped_cap_then_floor([0.50, 0.30, 0.15, 0.05, 0.0])
    assert out[4] == 0.0


def test_renormalise_comes_after_the_floor():
    """RESTORED COVERAGE. Ordering matters: flooring after the renormalise would
    push the row back off 1.0."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i_floor = src.index("_had & (_sub <= _WRITE_MIN)")
    i_norm = src.index("_sub[_ok] /= _ss[_ok, None]", i_floor)
    assert i_norm > i_floor
    out, _ = _shipped_cap_then_floor([0.50, 0.20, 0.15, 0.10, 0.05])
    assert abs(out.sum() - 1.0) < 1e-9


def test_row_that_loses_all_weight_is_restored_not_zeroed():
    """RESTORED COVERAGE. A row normalised from a zero sum would skin to the
    origin -- a visible vertex spike. It must be restored to its original
    weighting instead."""
    assert "_sub[~_ok] = G[rows][~_ok]" in inspect.getsource(
        nc._match_limb_motion_to_body)
    out, G = _shipped_cap_then_floor([0.0, 0.0, 0.0, 0.0, 0.0])
    assert np.allclose(out, G), "a zero row must come back as its original G"


def test_does_not_filter_rows_to_leg_share_increases():
    """REGRESSION GUARD. Most of the benefit comes from RE-SPLITTING the leg mass a
    vert already carries across thigh/calf/pelvis to match the body -- a vert whose
    TOTAL is already right can still follow the wrong bone. An earlier version
    filtered rows to `target > g_mass` and the fix measured 65 -> 65 (i.e. did
    nothing); including those verts gives 65 -> 18."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    rows_line = [ln for ln in src.splitlines() if "rows = np.where(" in ln]
    assert rows_line, "row selection not found"
    assert "target > g_mass" not in "".join(rows_line)


def test_never_drops_an_existing_weight_and_always_renormalises():
    """THE BUG THAT SHIPPED. `setShapeWeights` MERGES -- it updates only the pairs
    you pass, so a (bone, vert) you OMIT keeps its previous value. Zeroing a weight
    by leaving it out therefore does NOT remove it: the stale value survives and the
    vertex ends up over-weighted (measured: sums to 1.67). Weights that don't sum to
    1 transform the vertex by a partial/inflated sum of its bone matrices, so it
    drifts and drags near-degenerate triangles that flicker with VIEW ANGLE -- in
    game, "invisible head-on, fine from the side".

    The ORIGINAL fix floored every bone the vert already had and wrote it back. That
    defeated the 4-influence cap (a capped row got its dropped bones restored, so it
    shipped with 5-6 anyway and the SAVE chose the survivors). #weight-write-invariant
    keeps the guarantee and drops the contradiction: a dropped influence is cleared by
    writing an explicit 0.0 -- which genuinely removes it -- so nothing stale survives
    AND the cap holds."""
    # A bone the vertex HAD, now pruned, must be written as 0.0 -- never omitted.
    plan = _plan([[0.50, 0.20, 0.15, 0.10, 0.05]])
    row = _written(plan, 0)
    dropped = [b for b, w in row.items() if w == 0.0]
    assert dropped, "a pruned influence the vert had must be explicitly zeroed"
    # Every written row sums to exactly 1.
    assert abs(sum(w for w in row.values() if w > 0) - 1.0) < 1e-9


def test_a_bone_the_vertex_never_had_is_not_written():
    """The zero-write exists only to clear a STALE value. A bone the vertex never
    carried has nothing to clear, so writing it would be pure noise."""
    had = [[True, True, True, True, False]]
    plan = _plan([[0.50, 0.20, 0.15, 0.10, 0.05]], had=had)
    assert "E" not in plan


def test_row_that_loses_all_weight_is_left_untouched():
    """A row normalised from a zero sum would skin to the origin -- a vertex spike.
    It must be skipped entirely, so merge semantics keep its original weighting."""
    plan = _plan([[0.0, 0.0, 0.0, 0.0, 0.0]], had=[[True] * 5])
    assert plan == {}, "a zero row must not be written at all"


def test_influence_pruning_keeps_the_largest_and_renormalises():
    """The pruning maths itself, mirroring what the pass applies inline."""
    NEW = np.array([[0.50, 0.20, 0.15, 0.10, 0.05]])
    cap = nc._SKIN_MAX_INFLUENCES
    cut = np.argsort(NEW, axis=1)[:, :-cap]
    np.put_along_axis(NEW, cut, 0.0, axis=1)
    s = NEW.sum(axis=1)
    NEW[s > 1e-6] /= s[s > 1e-6, None]
    assert int((NEW > 0).sum()) == cap          # smallest dropped
    assert NEW[0, 4] == 0.0
    assert abs(NEW.sum() - 1.0) < 1e-9          # renormalised


def test_body_reference_prefers_the_injected_baseshape():
    """The injected BaseShape is the body the game skins beside these shapes and
    shares their space exactly; matching against a differently-ordered external
    body mapped verts to the wrong side."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "BaseShape" in src
    assert "_body_conform_ref" in src           # fallback retained


def test_hands_and_feet_slots_are_skipped():
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "BIPED_SLOT33_BIT" in src and "BIPED_SLOT37_BIT" in src


def test_a_grafted_bone_is_dropped_by_the_cap_and_NOT_restored():
    """THE CASE THE PASS EXISTS FOR, and the one an aliased G/NEW fixture hides.

    When the match grafts a bone the vertex never carried, that graft is typically
    the smallest influence, so the cap drops it -- and `_had` (built from the
    PRE-match weights) does not bring it back, because the vertex never had it.
    The graft is silently lost on that vertex.

    This is not a bug in the floor; it is the cap and the floor doing exactly what
    each was written to do, on a vertex that cannot satisfy both. It is pinned here
    so the trade-off is visible rather than surprising -- and so a future change
    that claims to "fix the cap" has to state what it does to this case."""
    g = [0.40, 0.20, 0.15, 0.25, 0.00]      # E never carried
    new = [0.36, 0.18, 0.14, 0.22, 0.10]    # E grafted by the match
    out, _ = _shipped_cap_then_floor(new, g)
    written = int((out > nc._WRITE_MIN).sum())
    assert written == 4, "cap holds when the dropped bone was never carried"
    assert out[4] == 0.0, "the GRAFT is what the cap dropped"
    assert abs(out.sum() - 1.0) < 1e-9

    # Contrast: same row, but the vertex already carried E -> the floor restores
    # it and 5 influences ship. Same code, opposite outcome.
    out2, _ = _shipped_cap_then_floor(new)
    assert int((out2 > nc._WRITE_MIN).sum()) == 5
