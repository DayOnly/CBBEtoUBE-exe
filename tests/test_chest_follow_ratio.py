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

"""#chest-follow-ratio -- the chest graft as a RATIO of the body's jiggle (P1).

The absolute cap (0.15) is a third of the body's motion at the bust (median breast
weight 0.427) and all of it at the butt (median 0.126). Measured with the ray metric:
every shape that exposes skin under motion tracks at <= 0.31; the one armour reported
clean in game tracks at 1.46.

The amount is DERIVED from geometry, not assigned by material:

    required follow = (1 - clearance / (bounce x body_jiggle)) x margin

Measured end-to-end on the traced leather cuirass, ratio mode ON:

    follow ratio at the nipple   0.25 -> 0.53   (the derived requirement)
    skin visible at 5u bounce     4.0% -> 0.0%
    skin visible at 6u bounce    16.8% -> 0.4%

THREE things had to change together, and each was found by measuring the previous
one failing:
  1. the absolute cap -> a ratio;
  2. the "already jiggling" gate, which dropped the cuirass on 9 of 3742 verts
     (0.24%) before the graft ever ran -- so the cap change alone did nothing;
  3. one ratio per SHAPE, not per vert -- the per-vert requirement was measured
     WORSE than doing nothing (4.0% -> 9.5%) because neighbouring verts moved by
     different amounts and the surface tore between them.

Ships OFF. It moves skinning on every soft torso garment in the pack."""
import importlib

import pytest

import src.nif_convert as nc


@pytest.fixture(autouse=True)
def _clean():
    yield
    import os
    for k in ("CBBE2UBE_CHEST_FOLLOW", "CBBE2UBE_CHEST_FOLLOW_SOFT",
              "CBBE2UBE_CHEST_FOLLOW_RIGID", "CBBE2UBE_CHEST_RIGID_JIGGLE_FRAC"):
        os.environ.pop(k, None)
    importlib.reload(nc)


class _Shape:
    def __init__(self, name, textures=None):
        self.name = name
        self.textures = textures or {}


# --- the flag ---------------------------------------------------------------

def test_defaults_off(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_CHEST_FOLLOW", raising=False)
    assert importlib.reload(nc).CHEST_FOLLOW_RATIO is False


def test_opt_in(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW", "1")
    assert importlib.reload(nc).CHEST_FOLLOW_RATIO is True


# --- legacy path must be untouched ------------------------------------------

def test_follow_none_reproduces_the_absolute_cap():
    """`follow=None` is the shipped path and must be bit-for-bit the old behaviour:
    total capped at _CHEST_JIGGLE_CAP, then the per-bone clamp."""
    body = {"L Breast01": 0.30, "L Breast02": 0.20}      # bsum 0.50
    dv = {nc._CHEST_ANCHOR: 0.60}
    nc._chest_match_vert(dv, body, strength=1.0, follow=None)
    got = sum(dv.get(b, 0.0) for b in nc._CHEST_JIGGLE_BONES)
    assert abs(got - nc._CHEST_JIGGLE_CAP) < 1e-9, "total must equal the absolute cap"


def test_follow_none_still_applies_the_per_bone_clamp():
    """The clamp keeps a single bone under the 0.1 rigid-gate threshold."""
    body = {"L Breast01": 1.0}                            # everything on one bone
    dv = {nc._CHEST_ANCHOR: 0.90}
    nc._chest_match_vert(dv, body, strength=1.0, follow=None)
    assert dv.get("L Breast01", 0.0) <= nc._CHEST_JIGGLE_PERBONE + 1e-9


# --- ratio mode --------------------------------------------------------------

def test_ratio_one_tracks_the_body_exactly():
    body = {"L Breast01": 0.30, "L Breast02": 0.20}        # bsum 0.50
    dv = {nc._CHEST_ANCHOR: 0.90}
    nc._chest_match_vert(dv, body, strength=1.0, follow=1.0)
    got = sum(dv.get(b, 0.0) for b in nc._CHEST_JIGGLE_BONES)
    assert abs(got - 0.50) < 1e-6, "follow 1.0 must reproduce the body's own weight"


def test_ratio_is_proportional_not_absolute():
    """THE point of the change. The same ratio must give the same FRACTION whether
    the body jiggles a lot or a little -- an absolute cap cannot, which is how 0.15
    became 0.35x at the bust and 1.0x at the butt."""
    for bsum in (0.12, 0.50):
        body = {"L Breast01": bsum}
        dv = {nc._CHEST_ANCHOR: 0.95}
        nc._chest_match_vert(dv, body, strength=1.0, follow=0.5)
        got = sum(dv.get(b, 0.0) for b in nc._CHEST_JIGGLE_BONES)
        assert abs(got / bsum - 0.5) < 1e-6, f"ratio drifted at bsum={bsum}"


def test_ratio_cannot_exceed_the_available_anchor_mass():
    """Weight is drawn FROM the anchor -- the graft can never invent mass."""
    body = {"L Breast01": 0.90}
    dv = {nc._CHEST_ANCHOR: 0.10}
    nc._chest_match_vert(dv, body, strength=1.0, follow=1.0)
    total = sum(dv.values())
    assert abs(total - 0.10) < 1e-6, "mass must be conserved"


def test_ratio_mode_skips_the_per_bone_clamp():
    """Holding a bone under 0.1 would cap the achievable follow at ~0.6x the body
    and defeat the change. Documented trade-off, pinned here so it is deliberate."""
    body = {"L Breast01": 0.50}
    dv = {nc._CHEST_ANCHOR: 0.90}
    nc._chest_match_vert(dv, body, strength=1.0, follow=1.0)
    assert dv.get("L Breast01", 0.0) > nc._CHEST_JIGGLE_PERBONE


# --- material classification -------------------------------------------------

def test_soft_material_from_the_diffuse_path(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW", "1")
    m = importlib.reload(nc)
    s = _Shape("Armor002", {"Diffuse": r"textures\armorpack\heavy\impleather.dds"})
    assert m._chest_follow_for_shape(s) == m._CHEST_FOLLOW_SOFT


def test_rigid_material_wins_over_soft(monkeypatch):
    """'steel-studded leather' should stay stiff."""
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW", "1")
    m = importlib.reload(nc)
    s = _Shape("SteelLeatherCuirass", {"Diffuse": "textures/armor/steelleather.dds"})
    assert m._chest_follow_for_shape(s) == m._CHEST_FOLLOW_RIGID


def test_unknown_material_stays_rigid(monkeypatch):
    """The conservative direction is today's behaviour.

    Unknown now has its OWN ceiling (`_CHEST_FOLLOW_UNKNOWN`), defaulting to the rigid
    value so this stays true -- see #chain-welded-torso for why it was split out: 129
    of the 182 shapes the ceiling actually blocks are unlabelled, not metal."""
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW", "1")
    m = importlib.reload(nc)
    s = _Shape("Armor_001_1", {"Diffuse": "textures/armorpack/piece_001.dds"})
    assert m._chest_follow_for_shape(s) == m._CHEST_FOLLOW_UNKNOWN
    assert m._CHEST_FOLLOW_UNKNOWN == m._CHEST_FOLLOW_RIGID


def test_only_the_diffuse_slot_is_read(monkeypatch):
    """REGRESSION. Reading every texture slot misclassified real armour twice: a
    leather cuirass matched 'steel' from its ENVIRONMENT map (cubemaps/steel_e.dds)
    and a dress matched 'metal' from its PBR map (`*_metallic.dds`). Neither describes
    the garment."""
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW", "1")
    m = importlib.reload(nc)
    leather = _Shape("Armor002", {
        "Diffuse": r"textures\armorpack\impleather.dds",
        "EnvMap": r"textures\cubemaps\steel_e.dds",
        "Specular": r"textures\armorpack\impleather_m.dds"})
    assert m._chest_follow_for_shape(leather) == m._CHEST_FOLLOW_SOFT
    dress = _Shape("Armor003_Dress", {
        "Diffuse": r"textures\armorpack\gown_diffuse.dds",
        "Specular": r"textures\armorpack\gown_metallic.dds"})
    assert m._chest_follow_for_shape(dress) == m._CHEST_FOLLOW_SOFT


def test_missing_textures_do_not_raise(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW", "1")
    m = importlib.reload(nc)
    assert m._chest_follow_for_shape(_Shape("Thing")) == m._CHEST_FOLLOW_RIGID
    assert m._chest_follow_for_shape(_Shape("Thing", None)) == m._CHEST_FOLLOW_RIGID


# --- the "already jiggling" gate --------------------------------------------

def test_rigid_gate_uses_a_fraction_in_ratio_mode():
    """THE blocker the cap change alone could not get past. An absolute 8-vert count
    called a 3742-vert cuirass 'already jiggling' on 9 verts (0.24%) and dropped it
    before the graft ran. Genuinely jiggling shapes measure p10 0.70% / p50 8.35%."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    assert "_CHEST_RIGID_JIGGLE_FRAC" in src
    assert "CHEST_FOLLOW_RATIO and (jig / _n_v) < _CHEST_RIGID_JIGGLE_FRAC" in src, (
        "the fraction test must be gated on the flag so the default is unchanged")


def test_full_count_when_ratio_mode_is_on():
    """A fraction cannot be judged from a short-circuited count."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    assert "and not CHEST_FOLLOW_RATIO" in src


def test_fraction_threshold_sits_between_trace_and_real():
    """0.24-0.6% is a graft brushing a shape; 0.70% is the 10th percentile of shapes
    that really jiggle. The floor has to separate them."""
    assert 0.006 <= nc._CHEST_RIGID_JIGGLE_FRAC <= 0.02


# --- the requirement is DERIVED from geometry, not assigned ------------------

def test_no_follow_needed_when_the_body_cannot_reach():
    """Clearance greater than the body's travel means the garment is never touched,
    so it should stay rigid however soft its material is."""
    travel = nc._CHEST_FOLLOW_BOUNCE * 0.5           # 3.0u at bounce 6.0
    assert nc._chest_follow_required(clearance=travel + 1.0, body_jiggle=0.5) == 0.0


def test_requirement_rises_as_clearance_shrinks():
    a = nc._chest_follow_required(clearance=2.0, body_jiggle=0.5)
    b = nc._chest_follow_required(clearance=1.0, body_jiggle=0.5)
    assert 0.0 < a < b


def test_requirement_is_zero_where_the_body_does_not_jiggle():
    """A still region needs no tracking -- this is what keeps the graft off the back
    and shoulders without a z-band."""
    assert nc._chest_follow_required(clearance=0.5, body_jiggle=0.0) == 0.0


def test_requirement_matches_the_hand_derivation():
    """follow = (1 - clearance/travel) * margin. Pinned against the numbers used to
    validate the design: clearance 1.88, body jiggle 0.50."""
    got = nc._chest_follow_required(clearance=1.88, body_jiggle=0.50)
    travel = nc._CHEST_FOLLOW_BOUNCE * 0.50
    want = (1.0 - 1.88 / travel) * nc._CHEST_FOLLOW_MARGIN
    assert abs(got - want) < 1e-9


def test_design_bounce_matches_the_physics_config():
    """The live SMP config permits the breast chain -6.0..+3.0 of linear travel.
    Deriving from a SMALLER bounce under-grants: at 5.0 the traced cuirass still
    showed 8.0% of nipple skin at a 6u bounce; at 6.0 it shows 0.4%."""
    assert nc._CHEST_FOLLOW_BOUNCE >= 6.0


def test_material_ratio_is_only_a_CEILING_on_the_requirement():
    """Geometry decides the amount; the classifier only caps it.

    The ceiling is an AESTHETIC constraint, not a geometric one. A full-pack census
    (n=99 rigid / 370 soft) found metal and soft indistinguishable -- clearance p50
    1.58 vs 1.55, required follow 0.66 vs 0.61. Metal neither stands off more nor
    needs less. What the ceiling prevents is jiggling steel: without it the geometry
    would grant a chainmail cuirass 1.06. Two earlier justifications for this ceiling
    were wrong ("metal hugs tighter", then "named cases prove a difference"); this one
    claims no measurement it does not have."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    assert "min(_ceiling, float(np.percentile(_reqs, 90)))" in src


def test_one_ratio_per_shape_not_per_vertex():
    """MEASURED: applying the per-vert requirement directly was worse than doing
    nothing (leather cuirass 4.0% -> 9.5% skin visible at 5u) because neighbouring
    verts then move by different amounts and the surface tears between them. The
    garment deforms as one piece, so the ratio is resolved once per shape."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    i = src.index("_chest_follow = (min(_ceiling")
    # the call site must pass the shape-level value, not recompute per vert
    call = src[src.index("_chest_match_vert(vw[i]"):]
    assert "follow=_chest_follow" in call[:160]
    assert "_chest_follow_required(di" not in src, (
        "the requirement must not be evaluated per vertex")


# --- the coverage hole between the two passes (#conform-coverage-hole) -------

class _S:
    def __init__(self, name, textures=None):
        self.name = name
        self.textures = textures or {}


def _rows(n, chain=0.0):
    """n vert rows; `chain` fraction driven by a non-skeleton bone."""
    out = []
    for i in range(n):
        out.append({"Skirt 1_01": 0.9} if i < int(n * chain)
                   else {"NPC Spine2 [Spn2]": 0.9})
    return out


def test_orphan_claimed_when_only_the_fit_gate_rejects_it():
    """The hole: the leg-bend pass defers a shape that carries any jiggle to the
    conform pass, the conform pass declines on its 0.90 fit gate, and NOBODY grafts
    it. Measured on a real cuirass: follow left at 0.34 against a 0.81 requirement."""
    import numpy as np
    vw = _rows(100)
    d = np.full(100, 5.0)                    # far from the body -> fit 0.0
    assert nc._conform_orphans_shape(_S("Cuirass"), vw, 100, d, set(), set(), set())


def test_not_claimed_when_the_conform_will_take_it():
    """If the conform pass accepts the shape, deferring is correct -- claiming it
    would run two passes over the same shape."""
    import numpy as np
    vw = _rows(100)
    d = np.full(100, 0.5)                    # hugging -> fit 1.0
    assert not nc._conform_orphans_shape(_S("Cuirass"), vw, 100, d, set(), set(), set())


def test_never_claims_a_chain_garment():
    """CTD-CLASS GUARD. `_match_rigid_leg_bend_to_body` has no chain guard at all --
    neither shape-level nor per-vert -- because the jiggle gate always filtered chain
    garments out before they reached it. Claiming one would graft jiggle onto
    SMP-driven verts, which is the layered-cloth equip-CTD (C2). The traced leather
    cuirass is exactly this: chain_frac 0.335 from 40 `Skirt N_NN` bones."""
    import numpy as np
    vw = _rows(100, chain=0.335)
    d = np.full(100, 5.0)                    # would fail the fit gate...
    assert not nc._conform_orphans_shape(_S("Cuirass"), vw, 100, d,
                                         set(), set(), set()), \
        "a chain garment must never be claimed by the leg-bend pass"


def test_never_claims_what_both_passes_skip():
    """Colliders, soft-bodies, layered cloth and skip-named shapes are excluded from
    BOTH passes on purpose -- not orphans, and not ours to take."""
    import numpy as np
    vw = _rows(100)
    d = np.full(100, 5.0)
    for kw in ({"X"}, set(), set()), (set(), {"X"}, set()), (set(), set(), {"X"}):
        assert not nc._conform_orphans_shape(_S("X"), vw, 100, d, *kw)
    assert not nc._conform_orphans_shape(_S("MyRobe"), vw, 100, d, set(), set(), set())


def test_handoff_is_decided_after_the_body_query():
    """The predicate needs `d`, which does not exist at the old skip point. If the
    decision moves back before the query it silently reverts to deferring blind."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    assert src.index("_defer_to_conform = True") < src.index("d = d_k[:, 0]")
    assert src.index("d = d_k[:, 0]") < src.index("_conform_orphans_shape(")


def test_handoff_change_is_gated_on_the_flag():
    """With CHEST_FOLLOW_RATIO off, the old unconditional deferral must remain."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    i = src.index("_defer_to_conform = True")
    assert "if not CHEST_FOLLOW_RATIO:" in src[i - 900:i], (
        "the ratio-mode branch must sit under an explicit flag check so the "
        "shipped default still defers unconditionally")
