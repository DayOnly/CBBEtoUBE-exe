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

"""#layer-follow-divergence -- stacked layers must resolve the body TOGETHER.

REPORTED IN GAME after 1.3-alpha: "layers clipping into other layers (not the
body)". The full-vector weight match copies the covered body's whole weight row
into each shape INDEPENDENTLY. Measured on that piece as mean weight-row
divergence between stacked vertex pairs, 1.2 -> 1.3-alpha:

    chest_plate / top    0.190 -> 0.309
    top / corset         0.075 -> 0.111
    belts / corset       0.026 -> 0.071
    belts_metal / belts  0.027 -> 0.204

Turning the pass off restored EVERY pair to its 1.2 value, which is what makes
it the cause rather than a correlate.

TWO mechanisms, isolated by controls:
  * BASIS -- the row is renormalised onto each shape's OWN bone list, so `top`
    (26 bones, Breast02/03 + Belly) and `chest_plate` (9, none) take different
    rows from the SAME body vertex. Renormalising both onto their shared bones
    collapses that pair 0.288 -> 0.062.
  * RAY PAIRING -- cast from each vert along its OWN normal, so stacked layers
    hit different body triangles. The whole of `belts_metal/belts`, which
    survives every other control (both rewritten, same KD body vertex, shared
    basis: 0.190 -> 0.191).

Ruled out, do not re-attempt: PARTIAL APPLICATION (18,254 of 18,389 stacked
pairs have both members rewritten, and the one-only class diverges LESS).
"""
import inspect

import numpy as np
import pytest

from src import nif_convert as nc


class _FakeShape:
    def __init__(self, name, verts, bones=(), tris=None, shift=(0.0, 0.0, 0.0)):
        self.name = name
        self.verts = [tuple(v) for v in verts]
        self.bone_names = list(bones)
        self.tris = tris if tris is not None else []
        self.shift = np.asarray(shift, dtype=np.float64)


@pytest.fixture(autouse=True)
def _world_space(monkeypatch):
    """Give each fake shape its own global-to-skin, so the world-space
    assertions below are actually exercising a transform.

    Also forces the guard ON. The pass now ships OFF (it was destructive in
    game -- see test_guard_defaults_OFF_and_why), but these tests exercise the
    MACHINERY, which must stay correct for any successor design. Without this
    they would all pass vacuously on an empty group list."""
    monkeypatch.setattr(nc, "_shape_global_to_skin", lambda s: s)
    monkeypatch.setattr(
        nc, "_verts_skin_to_world",
        lambda sv, xf: np.asarray(sv, dtype=np.float64) + xf.shift)
    monkeypatch.setattr(nc, "_FULL_WEIGHT_LAYER_GUARD", True)


def _grid(z, n=8, step=1.0, x0=0.0):
    return [(x0 + i * step, j * step, z) for i in range(n) for j in range(n)]


# --------------------------------------------------------------- the thresholds

def test_guard_defaults_OFF_and_why():
    """SHIPPED ON, JUDGED IN GAME, AND TURNED OFF THE SAME DAY.

    Reported: "clips at the belts and the breasts", "the back hard regressed at
    poses", "the sleeves being bound". Measured on that piece, 1.2 -> pass ON:

        top          ARM 3840.6 -> 2928.3   BREAST  708.1 ->  118.9
        chest_plate  ARM   73.0 ->    0.3   BREAST 1208.3 ->  155.2

    with the lost mass landing on SPINE/CLAVICLE. Same build with it OFF keeps
    them (ARM 3741.2 / 81.4, BREAST 990.8 / 1008.9).

    The design is wrong, not mistuned: routing every stacked layer through the
    INNERMOST member's anchor makes a sleeve -- grouped with the chest plate
    because they overlap -- resolve its body row through a TORSO point.

    Asserted on the SOURCE, not the module attribute, because the autouse
    fixture forces the flag on for the machinery tests below.
    """
    src = inspect.getsource(nc)
    i = src.index("_FULL_WEIGHT_LAYER_GUARD = (")
    decl = src[i:i + 240]
    assert '"CBBE2UBE_FULL_WEIGHT_LAYER_GUARD"' in decl, (
        "the flag must be an opt-IN (bare name), not a NO_* kill-switch")
    assert "not in (" not in decl, (
        "`not in` would make this default ON again -- it was destructive in game")


def test_thresholds_are_sane():
    assert nc._LAYER_STACK_RADIUS > 0.0
    assert 0.0 < nc._LAYER_STACK_COVER <= 1.0


def test_shared_basis_is_OFF_and_the_counter_metric_is_why():
    """PINNED AGAINST A PLAUSIBLE-LOOKING REGRESSION.

    Narrowing a stacked group's copy to the bones every member shares DOES fix
    the divergence -- all five pairs on the reported piece fell to <=0.032,
    below their 1.2 values. Judged on that number alone it looks like the right
    answer. The counter-metric says otherwise; same build, same piece,
    1.2 -> 1.3-alpha -> shared basis:

        chest_plate  jiggle 1208 -> 958 -> 155   body gap 0.253 -> 0.298 -> 0.617
        top          jiggle  710 -> 993 ->  76   body gap 0.082 -> 0.082 -> 0.571

    ~87% of the breast/butt/belly follow gone and body-follow 7x worse than
    EITHER baseline, because the five layers form one connected group and the
    shared set collapses to what an 8-bone accessory and a 9-bone plate have in
    common. Structural, not a threshold. If this ever defaults ON again it must
    arrive with a jiggle-mass and body-gap measurement beside it.
    """
    assert nc._LAYER_STACK_SHARED_BASIS is False


def test_the_basis_floor_guards_the_renormalisation():
    """`BF` is rescaled so the captured share becomes 1.0, so a basis that
    captures little turns a partial sample into a confident wrong answer. The
    floor is a SHARE of the body row, not an epsilon."""
    assert 0.0 <= nc._FULL_WEIGHT_BASIS_MIN <= 1.0
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_okb = _bs > max(_FULL_WEIGHT_BASIS_MIN, 1e-6)" in src, \
        "the floor is defined but not applied to the row selection"


def test_disabling_the_guard_groups_nothing(monkeypatch):
    a = _FakeShape("a", _grid(0.0))
    b = _FakeShape("b", _grid(1.0))
    monkeypatch.setattr(nc, "_FULL_WEIGHT_LAYER_GUARD", False)
    assert nc._stacked_layer_groups([a, b]) == []


# ------------------------------------------------------------------- grouping

def test_two_stacked_sheets_are_one_group():
    """The reported geometry: two layers 1u apart over the same area."""
    a = _FakeShape("a", _grid(0.0))
    b = _FakeShape("b", _grid(1.0))
    groups = nc._stacked_layer_groups([a, b])
    assert len(groups) == 1
    assert {g[0] for g in groups[0]} == {"a", "b"}


def test_a_trim_strip_that_only_TOUCHES_is_not_a_layer():
    """COVERAGE, not contact. A buckle or piping meets a cuirass along its
    border; grouping it would constrain the cuirass's whole bone basis to the
    trim's. This is the distinction that kept the rule off two thirds of the
    pack."""
    a = _FakeShape("a", _grid(0.0, n=8))
    # a 2-vert strip touching one corner only
    b = _FakeShape("b", [(0.0, 0.0, 0.5), (0.0, 1.0, 0.5)] + _grid(40.0, n=6))
    assert nc._stacked_layer_groups([a, b]) == []


def test_far_apart_sheets_are_not_grouped():
    a = _FakeShape("a", _grid(0.0))
    b = _FakeShape("b", _grid(25.0))
    assert nc._stacked_layer_groups([a, b]) == []


def test_grouping_is_done_in_WORLD_space():
    """Shapes in one NIF can carry different global-to-skin transforms. On raw
    verts these two coincide exactly; in world space they are 25u apart."""
    a = _FakeShape("a", _grid(0.0))
    b = _FakeShape("b", _grid(0.0), shift=(0.0, 0.0, 25.0))
    assert nc._stacked_layer_groups([a, b]) == []


def test_excluded_shapes_never_join_a_group():
    """The census grouped a shoe with the FEET and a cuirass with its own
    ColBody collider. Neither is a cloth layer, and either would drag the
    garment's basis down to a non-garment's."""
    a = _FakeShape("a", _grid(0.0))
    col = _FakeShape("ColBody", _grid(1.0))
    assert nc._stacked_layer_groups([a, col], exclude={"ColBody"}) == []


def test_three_layers_form_ONE_connected_group():
    """Transitivity matters: A over B over C must resolve through one anchor,
    not two overlapping pairs with different answers."""
    sh = [_FakeShape("a", _grid(0.0)), _FakeShape("b", _grid(1.0)),
          _FakeShape("c", _grid(2.0))]
    groups = nc._stacked_layer_groups(sh)
    assert len(groups) == 1
    assert {g[0] for g in groups[0]} == {"a", "b", "c"}


# ----------------------------------------------------------------- the plan

def _plan_for(shapes, ube_bones):
    from scipy.spatial import cKDTree
    body = cKDTree(np.array([(i * 1.0, j * 1.0, -1.0)
                             for i in range(8) for j in range(8)]))
    groups = nc._stacked_layer_groups(shapes)
    assert groups, "fixture must actually produce a group"
    return nc._stacked_layer_plan(groups, body, ube_bones)


def _quad(z, shift=(0.0, 0.0, 0.0), name="s", bones=()):
    v = [(0.0, 0.0, z), (1.0, 0.0, z), (1.0, 1.0, z), (0.0, 1.0, z)]
    return _FakeShape(name, v * 16, bones=bones,
                      tris=[(0, 1, 2), (0, 2, 3)], shift=shift)


def test_the_anchor_is_the_INNERMOST_member():
    """The layer closest to the skin has the most trustworthy pairing, so the
    stack asks the body ONE question through it."""
    inner = _quad(0.0, name="inner", bones=["NPC Spine2"])
    outer = _quad(1.0, name="outer", bones=["NPC Spine2"])
    plan = _plan_for([inner, outer], {"NPC Spine2"})
    assert set(plan) == {"inner", "outer"}
    # every anchor point the OUTER layer uses is a point on the INNER surface
    assert np.allclose(np.unique(plan["outer"]["pos"][:, 2]), [0.0])
    assert np.allclose(np.unique(plan["inner"]["pos"][:, 2]), [0.0])


def test_the_basis_is_the_INTERSECTION_over_the_group():
    """A group can only follow the body as far as its least-capable member: two
    layers 2u apart following different bones interpenetrate. This is the
    mechanism behind 76% of the worst measured pair."""
    inner = _quad(0.0, name="inner", bones=["NPC Spine2", "NPC Pelvis"])
    outer = _quad(1.0, name="outer",
                  bones=["NPC Spine2", "L Breast02", "NPC Belly"])
    plan = _plan_for([inner, outer], {"NPC Spine2", "NPC Pelvis",
                                      "L Breast02", "NPC Belly"})
    for nm in ("inner", "outer"):
        assert plan[nm]["basis"] == {"NPC Spine2"}, nm


def test_a_bone_the_BODY_lacks_never_enters_the_basis():
    """Same invariant as the pass's clean-row gate: blending toward a body that
    has no chain bone would drain an authored chain to zero."""
    inner = _quad(0.0, name="inner", bones=["NPC Spine2", "CHAIN 1"])
    outer = _quad(1.0, name="outer", bones=["NPC Spine2", "CHAIN 1"])
    plan = _plan_for([inner, outer], {"NPC Spine2"})
    assert plan["inner"]["basis"] == {"NPC Spine2"}


def test_a_group_sharing_NO_body_bone_yields_an_empty_basis():
    """The caller refuses such a group outright -- writing a row renormalised
    over a different set per member IS the defect."""
    inner = _quad(0.0, name="inner", bones=["NPC Pelvis"])
    outer = _quad(1.0, name="outer", bones=["NPC Spine2"])
    plan = _plan_for([inner, outer], {"NPC Spine2", "NPC Pelvis"})
    assert plan["inner"]["basis"] == set()


def test_anchor_arrays_are_per_vertex_of_THAT_member():
    """`pos`/`nrm` are indexed by the member's own vert index, so a length
    mismatch would silently mis-pair every row."""
    inner = _quad(0.0, name="inner", bones=["NPC Spine2"])
    outer = _quad(1.0, name="outer", bones=["NPC Spine2"])
    plan = _plan_for([inner, outer], {"NPC Spine2"})
    for nm, sh in (("inner", inner), ("outer", outer)):
        assert plan[nm]["pos"].shape == (len(sh.verts), 3)
        assert plan[nm]["nrm"].shape == (len(sh.verts), 3)


# ------------------------------------------------------------ it is wired in

def test_the_plan_is_built_and_USED_by_the_pass():
    """Computed-and-discarded is the failure mode this project keeps hitting."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_stacked_layer_groups(" in src, "the plan is never built"
    assert "_stacked_layer_plan(" in src
    assert "_stack_plan.get(s.name)" in src, "the plan is never consulted"
    assert "_plan[\"near_ok\"]" in src, "the shared anchor is not used"
    assert "if _b in _fv_basis:" in src, "the copy basis is not applied"


def test_the_shared_anchor_is_applied_ONLY_WHERE_LAYERS_OVERLAP():
    """THE REGRESSION THIS COST US, PINNED (#layer-anchor-local).

    The first version substituted the shared anchor for EVERY vertex of a
    grouped shape. `itree.query(wv, k=1)` has no distance limit, so a SLEEVE
    vertex 20u from the corset still borrowed the nearest corset point -- a
    TORSO anchor for arm geometry. Reported in game as "the sleeves being
    bound", and measured: `top` ARM 3840.6 -> 2928.3, `chest_plate` 73.0 -> 0.3,
    with the mass landing on spine/clavicle.

    Reconciling two layers only means anything where they OVERLAP. So the
    substitution must be MASKED by `near_ok`, never wholesale -- both for the
    KD query and for the ray origin/normal, or the sleeve fires its ray from a
    point on the torso.
    """
    src = inspect.getsource(nc._match_limb_motion_to_body)
    # masked, not wholesale
    assert "_qv[_ok] = " in src, (
        "the anchor must be written only into the overlapping verts")
    assert "_src[_ok2] = " in src and "_gn[_ok2] = " in src, (
        "the ray origin AND normal must be masked the same way")
    # and the plan must actually carry the mask
    plan = inspect.getsource(nc._stacked_layer_plan)
    assert "near_ok" in plan and "_LAYER_STACK_RADIUS" in plan, (
        "the plan must bound the anchor by the stacking radius")


def test_the_guard_is_scoped_to_the_FULL_VECTOR_instance():
    """The four family passes rescale ONE family and leave the rest of the row
    proportional, so they decohere a stack far less -- and each was validated in
    game as it stands. Widening to them needs its own measurement."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i = src.index("_stack_plan: dict = {}")
    head = src[:i]
    assert "if full_vector and _FULL_WEIGHT_LAYER_GUARD:" in src[i:i + 400]
    assert "_stacked_layer_groups(" not in head, \
        "the stack plan must not be built for the family instances"


def test_the_own_distance_gate_survives_the_shared_anchor():
    """The anchor can hug the body while THIS layer hangs well off it. Dropping
    the member's own distance test would pull a free-hanging outer layer onto
    the body's motion -- the exact defect the hug gate exists to prevent."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "band &= np.asarray(tree.query(wv, k=1)[0]) <= max_dist" in src
