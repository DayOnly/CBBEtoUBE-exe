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

"""#chain-welded-torso -- a cuirass modelled as ONE shape with its own physics skirt.

Two changes that only make sense together:

  1. a PER-VERT CHAIN GUARD in `_match_rigid_leg_bend_to_body`. That pass had none --
     not shape-level, not per-vert -- because its shape gates happened to filter chain
     garments out before they arrived. Measured over the shipped output, 93 shapes
     reach it carrying 33,773 chain verts inside the proximity window, so the hole was
     real, just unreachable. Writing a graft onto a simulated vert is the layered-cloth
     equip crash (C2, 2026-07-09).

  2. a CLAIM for the chain-welded torso, judged on its RIGID verts. Such a shape fails
     every whole-shape gate for a reason that has nothing to do with its chest: the
     skirt hangs away, so whole-shape fit collapses (traced cuirass 0.50 whole vs 0.69
     rigid) and chain_frac lands far over the limit (0.33 vs 0.05).

The claim is interlocked to the guard -- without it, claiming these shapes would graft
jiggle straight onto SMP cloth, which is the crash the guard exists to prevent.

HONEST STATUS -- READ BEFORE TRUSTING THIS FEATURE. Measured by SOURCE-CONVERTING the
traced cuirass four times through the real pipeline (`armor/studded/female/body_1.nif`,
`bodyREVISE`, 3339 verts, 33.5% chain, requirement 0.808):

    ceiling 0.35, no claim   follow 0.338
    ceiling 0.35, claim      follow 0.338
    ceiling 1.00, no claim   follow 0.793
    ceiling 1.00, claim      follow 0.793

**The claim column is identical: it is a NO-OP in a real conversion.** The MATERIAL
CEILING is the entire effect (posed bust exposure under motion 71.2% -> 8.8%). Verified
on six more chain-welded candidates -- none differs by more than 1e-3. Instrumenting
`_conform_orphans_shape` over four real conversions found it consulted TWICE, claiming
nothing: the defer path needs a shape that arrives already carrying >=1% jiggle verts,
and these do not. The 1.02% that made it look otherwise is jiggle THIS PASS WROTE, seen
because the first measurement re-ran the pass on shipped OUTPUT rather than converting
from source. Do not repeat that; use `scripts/convert_one_armor.py`.

So the tests below pin the DECISION LOGIC, which is correct and safe, not a delivered
improvement. The claim stays default OFF and unproven.

The BUTT of such a piece cannot be helped here at all: 89 of `bodyREVISE`'s 89
butt-band verts are chain verts, so the guard refuses every one, and `_butt_match_vert`
has no eligible vert anyway (it needs >=2 of L/R Thigh + Pelvis; the shape carries only
Pelvis). That is a cloth-simulation problem, not a skinning one."""
import importlib
import os

import numpy as np
import pytest

import src.nif_convert as nc

_ENV = ("CBBE2UBE_CHAIN_TORSO", "CBBE2UBE_NO_LEG_CHAIN_GUARD",
        "CBBE2UBE_CHEST_FOLLOW", "CBBE2UBE_CHEST_FOLLOW_UNKNOWN")


@pytest.fixture(autouse=True)
def _clean():
    yield
    for k in _ENV:
        os.environ.pop(k, None)
    importlib.reload(nc)


class _Shape:
    def __init__(self, name="Cuirass", bone_weights=None, textures=None):
        self.name = name
        self.bone_weights = bone_weights or {}
        self.textures = textures or {}


def _torso_weights():
    """Weight map that satisfies `_shape_is_rigid_torso_armor` (>=35% of total skin
    weight on upper-torso rigid bones)."""
    return {
        "NPC L Clavicle [LClv]": [(0, 1.0)],
        "NPC Spine2 [Spn2]": [(1, 1.0)],
        "NPC Pelvis [Pelv]": [(2, 1.0)],
    }


def _vw(n_rigid, n_chain, chain_weight=0.9):
    """`vw` for a shape of n_rigid skeleton-driven + n_chain cloth-driven verts."""
    out = [{"NPC Spine2 [Spn2]": 1.0} for _ in range(n_rigid)]
    out += [{"Skirt N_01": chain_weight, "NPC Pelvis [Pelv]": 1.0 - chain_weight}
            for _ in range(n_chain)]
    return out


# --- flags -------------------------------------------------------------------

def test_guard_defaults_on(monkeypatch):
    """It only ever writes LESS. Off by default would leave the CTD class open."""
    monkeypatch.delenv("CBBE2UBE_NO_LEG_CHAIN_GUARD", raising=False)
    assert importlib.reload(nc).LEG_CHAIN_GUARD is True


def test_guard_can_be_disabled_for_bisection(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_LEG_CHAIN_GUARD", "1")
    assert importlib.reload(nc).LEG_CHAIN_GUARD is False


def test_claim_defaults_off(monkeypatch):
    """It grafts 17 shapes nothing has ever grafted. Only in-game can judge them."""
    monkeypatch.delenv("CBBE2UBE_CHAIN_TORSO", raising=False)
    assert importlib.reload(nc).CHAIN_TORSO_CLAIM is False


def test_claim_opt_in(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    assert importlib.reload(nc).CHAIN_TORSO_CLAIM is True


# --- the chain mask ----------------------------------------------------------

def test_chain_mask_flags_custom_bone_verts():
    vw = _vw(2, 2)
    assert nc._chain_vert_mask(vw, 4) == [False, False, True, True]


def test_chain_mask_uses_the_same_0_1_threshold_as_every_other_pass():
    """A vert only counts as cloth above 0.1 on a custom bone. If this pass used a
    different threshold the passes would disagree about which verts are simulated,
    and a vert could be refused by one and written by another."""
    vw = [{"Skirt N_01": 0.05, "NPC Spine2 [Spn2]": 0.95},
          {"Skirt N_01": 0.11, "NPC Spine2 [Spn2]": 0.89}]
    assert nc._chain_vert_mask(vw, 2) == [False, True]


def test_chain_mask_does_not_flag_skeleton_bones():
    """Breast/butt bones are skeleton bones, not cloth chains -- flagging them would
    make the guard refuse exactly the verts the graft exists to serve."""
    vw = [{"NPC L Breast01": 0.9}, {"NPC L Butt": 0.9}]
    assert nc._chain_vert_mask(vw, 2) == [False, False]


# --- the claim ---------------------------------------------------------------

def _orphans(m, shape, vw, d):
    return m._conform_orphans_shape(shape, vw, len(vw), np.asarray(d, float),
                                    set(), set(), set())


def test_chain_shape_still_refused_when_the_claim_is_off(monkeypatch):
    """REGRESSION -- today's behaviour. A chain garment is refused outright."""
    monkeypatch.delenv("CBBE2UBE_CHAIN_TORSO", raising=False)
    m = importlib.reload(nc)
    s = _Shape(bone_weights=_torso_weights())
    assert _orphans(m, s, _vw(2, 2), [0.5] * 4) is False


def test_claim_is_interlocked_to_the_guard(monkeypatch):
    """SAFETY. Claiming a chain-welded shape without the per-vert guard would graft
    jiggle onto SMP-driven verts -- the crash the guard exists to prevent. The claim
    must refuse rather than trust the caller."""
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    monkeypatch.setenv("CBBE2UBE_NO_LEG_CHAIN_GUARD", "1")
    m = importlib.reload(nc)
    s = _Shape(bone_weights=_torso_weights())
    assert _orphans(m, s, _vw(2, 2), [0.5] * 4) is False


def test_claims_a_fitted_chain_welded_torso(monkeypatch):
    """The traced cuirass's shape: mostly-rigid torso whose rigid verts hug the body,
    welded to a skirt that does not."""
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    m = importlib.reload(nc)
    s = _Shape(bone_weights=_torso_weights())
    # 3 rigid verts at 0.5u (inside _CONFORM_FIT_PROX), 2 skirt verts hanging at 9u
    assert _orphans(m, s, _vw(3, 2), [0.5, 0.5, 0.5, 9.0, 9.0]) is True


def test_refuses_a_chain_shape_that_is_not_a_fitted_torso(monkeypatch):
    """A skirt or cape is chain-welded to nothing and carries no upper-torso weight.
    `_shape_is_rigid_torso_armor` is what separates them (measured: real skirt ~2%
    upper-torso weight, cuirass ~54%)."""
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    m = importlib.reload(nc)
    s = _Shape("Skirt", bone_weights={"NPC Pelvis [Pelv]": [(0, 1.0)]})
    assert _orphans(m, s, _vw(3, 2), [0.5, 0.5, 0.5, 9.0, 9.0]) is False


def test_refuses_a_chain_torso_whose_rigid_verts_stand_off(monkeypatch):
    """The rigid part must actually hug the body. A loose tabard over a chain skirt
    has nothing to poke through it."""
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    m = importlib.reload(nc)
    s = _Shape(bone_weights=_torso_weights())
    assert _orphans(m, s, _vw(3, 2), [9.0, 9.0, 9.0, 9.0, 9.0]) is False


def test_rigid_fit_is_judged_WITHOUT_the_chain_verts(monkeypatch):
    """THE point of the change. Both cases below have identical rigid verts, all
    hugging the body; they differ only in how far the skirt hangs. A whole-shape fit
    would reject the second, which is exactly how the traced cuirass (0.50 whole,
    0.69 rigid) fell through every gate."""
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    m = importlib.reload(nc)
    s = _Shape(bone_weights=_torso_weights())
    near = _orphans(m, s, _vw(3, 3), [0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
    far = _orphans(m, s, _vw(3, 3), [0.5, 0.5, 0.5, 40.0, 40.0, 40.0])
    assert near is True and far is True


# --- the non-chain path must be untouched -------------------------------------

def test_non_chain_orphan_behaviour_is_unchanged(monkeypatch):
    """#conform-coverage-hole's own case: no chain verts, fit under the conform's
    0.90 gate -> still claimed, with or without the new flag."""
    for val in ("", "1"):
        if val:
            monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", val)
        else:
            monkeypatch.delenv("CBBE2UBE_CHAIN_TORSO", raising=False)
        m = importlib.reload(nc)
        s = _Shape(bone_weights=_torso_weights())
        assert _orphans(m, s, _vw(4, 0), [0.5, 0.5, 9.0, 9.0]) is True


def test_non_chain_shape_that_hugs_the_body_is_left_to_the_conform(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_CHAIN_TORSO", "1")
    m = importlib.reload(nc)
    s = _Shape(bone_weights=_torso_weights())
    assert _orphans(m, s, _vw(4, 0), [0.5] * 4) is False


# --- the guard is actually WIRED IN --------------------------------------------
#
# The predicate tests above prove the decision; these prove the pass obeys it. A
# guard that exists but is never consulted is how the UBE-native backstop stayed dead
# code for weeks, and it is the exact shape of this bug: the rule was written down in
# two other passes and simply absent from this one.

def test_the_match_loop_applies_the_guard():
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    assert "LEG_CHAIN_GUARD and is_chain[i]" in src, \
        "the per-vert loop must skip simulated verts"


def test_the_follow_requirement_ignores_chain_verts():
    """A vert the graft will refuse must not size the graft either. Including them
    would derive the shape's follow ratio from cloth the pass never touches."""
    import inspect
    src = inspect.getsource(nc._match_rigid_leg_bend_to_body)
    assert "LEG_CHAIN_GUARD and is_chain[_i]" in src


def test_every_jiggle_pass_agrees_on_what_cloth_is():
    """All three passes must use the same rule, or a vert is refused by one and
    written by another. `_transfer_body_jiggle_to_fitted` and `_conform_weights_core`
    inline it; this pass goes through the shared helper."""
    import inspect
    for fn in (nc._transfer_body_jiggle_to_fitted, nc._conform_weights_core):
        src = inspect.getsource(fn)
        assert "_is_skeleton_bone" in src


# --- the material ceiling ------------------------------------------------------

def test_unknown_material_has_its_own_ceiling(monkeypatch):
    """Split out from the rigid ceiling because that is where it actually bites: of
    182 bust-covering shapes whose requirement exceeds their ceiling, 129 are UNKNOWN
    and 53 are recognised metal."""
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW_UNKNOWN", "0.8")
    m = importlib.reload(nc)
    s = _Shape("Armor_001", textures={"Diffuse": "textures/armorpack/piece_001.dds"})
    assert m._chest_follow_for_shape(s) == 0.8
    assert m._CHEST_FOLLOW_RIGID == 0.35, "the metal ceiling must not move with it"


def test_unknown_ceiling_defaults_to_todays_value(monkeypatch):
    """Ships as a no-op: the split is there so the choice can be MADE, not so it is
    made for the user. The rigid ceiling is an aesthetic judgement, not a measurement,
    so raising this is a taste call about unlabelled armour."""
    monkeypatch.delenv("CBBE2UBE_CHEST_FOLLOW_UNKNOWN", raising=False)
    m = importlib.reload(nc)
    assert m._CHEST_FOLLOW_UNKNOWN == m._CHEST_FOLLOW_RIGID


def test_studded_is_soft():
    """Vanilla studded armour is leather with metal studs, not plate. It matched
    neither list, so it took the unknown default and the traced cuirass sat at a 0.35
    ceiling against a 0.81 requirement -- the real diffuse path is asserted here."""
    s = _Shape("bodyREVISE", textures={
        "Diffuse": r"textures\armor\studded\StuddedArmorFem01_body_D.dds"})
    assert nc._chest_follow_for_shape(s) == nc._CHEST_FOLLOW_SOFT


def test_steel_studded_still_reads_rigid(monkeypatch):
    """REGRESSION -- the rigid-wins-over-soft rule protects the mixed case.

    The unknown ceiling is moved off its default first, deliberately: with both at
    0.35 this assertion passes whether the shape reads RIGID or falls through to
    UNKNOWN, which makes it no test at all. Separating them is what gives it teeth."""
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW_UNKNOWN", "0.9")
    m = importlib.reload(nc)
    s = _Shape("Cuirass", textures={"Diffuse": "textures/armor/steelstudded.dds"})
    assert m._chest_follow_for_shape(s) == m._CHEST_FOLLOW_RIGID
