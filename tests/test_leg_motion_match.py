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


def test_skips_colliders_and_softbody():
    """Standing rule: every skin pass leaves authored physics geometry alone --
    a skin pass touching an SMP collider CTDs, and touching softbody drifts."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "_hdt_collider_shape_names" in src
    assert "_hdt_softbody_shape_names" in src
    assert "_shape_has_hdt_smp_rigging" in src


def test_never_moves_a_vertex():
    """The pass must only touch WEIGHTS. Moving verts would undo the bind-pose
    clearance the earlier passes established."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert ".verts =" not in src
    assert "set_shape_verts" not in src
    assert "setShapeWeights" in src


def test_saves_and_restores_skin_to_bone():
    """setShapeWeights can reset a shape's skin-to-bone transforms; an STB left at
    identity skins the shape to the origin and the armor explodes in game."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "get_shape_skin_to_bone" in src
    assert "set_skin_to_bone_xform" in src


def test_push_up_only():
    """Never LOWER a leg share -- a garment already tracking the body is left be."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "np.maximum(" in src


def test_prunes_to_the_skin_partition_influence_cap():
    """Skyrim holds 4 influences per vertex. Exceed it and the SAVE drops one of
    its own choosing and renormalises, scrambling the computed split (measured:
    65 -> 18 newly-exposed written vs 65 -> 0 for the same weights in memory).
    The pass must prune the smallest itself so the result is deterministic."""
    assert nc._SKIN_MAX_INFLUENCES == 4
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "_SKIN_MAX_INFLUENCES" in src
    assert "put_along_axis" in src


def test_does_not_filter_rows_to_leg_share_increases():
    """REGRESSION GUARD. Most of the benefit comes from RE-SPLITTING the leg mass a
    vert already carries across thigh/calf/pelvis to match the body -- a vert whose
    TOTAL is already right can still follow the wrong bone. An earlier version
    filtered rows to `target > g_mass` and the fix measured 65 -> 65 (i.e. did
    nothing); including those verts gives 65 -> 18."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
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

    So: any bone the vert already had must be floored above the write threshold and
    written back, and every touched row must be renormalised unconditionally."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "_WRITE_MIN" in src, "the write threshold must be a named constant"
    assert "_had = G[rows] > 1e-4" in src, (
        "must detect bones the vertex ALREADY had, so none is silently dropped")
    # the floor and the renormalise must both be present, renormalise last
    i_floor = src.index("_had & (_sub <= _WRITE_MIN)")
    i_norm = src.index("_sub[_ok] /= _ss[_ok, None]", i_floor)
    assert i_norm > i_floor, "renormalise must come after the floor"


def test_write_threshold_matches_the_floor():
    """The floor must sit ABOVE the write threshold, or the floored value is itself
    dropped on write and the stale weight survives anyway."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "_WRITE_MIN * 2.0" in src
    assert "> _WRITE_MIN])" in src, "the write must use the same named threshold"


def test_row_that_loses_all_weight_is_restored():
    """A row normalised from a zero sum would skin to the origin -- a vertex spike."""
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "_sub[~_ok] = G[rows][~_ok]" in src


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
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "BaseShape" in src
    assert "_body_conform_ref" in src           # fallback retained


def test_hands_and_feet_slots_are_skipped():
    src = inspect.getsource(nc._match_leg_motion_to_body)
    assert "BIPED_SLOT33_BIT" in src and "BIPED_SLOT37_BIT" in src
