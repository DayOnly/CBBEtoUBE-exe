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

"""Groin/pants-collapse fix: armor must not be weighted to genital physics
bones, and rigid leg-encasing armor must not get breast/butt/belly jiggle.

Root cause (measured on the wolf Greaves): the converter's scale-bone transfer
put up to 57% of the rigid groin plate's weight on the body's genital/anus
HDT-SMP bones (source had 0%), and up to 48% on butt/belly at the hip band.
Those bones are unstable on the UBE race, dragging the "pants" into a collapse.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402


def test_genital_bones_excluded_from_scale_set():
    for kw in ("clit", "pussy", "vagina", "anus", "nipple"):
        assert kw not in nc.SCALE_BONE_KEYWORDS, f"{kw} must not be a scale bone"


def test_body_morph_bones_retained():
    # legitimate body-slider following must still work
    for kw in ("breast", "butt", "belly", "frontthigh", "rearthigh", "rearcalf"):
        assert kw in nc.SCALE_BONE_KEYWORDS


def test_is_leg_rigid_bone():
    assert nc._is_leg_rigid_bone("NPC L Thigh [LThg]") is True
    assert nc._is_leg_rigid_bone("NPC R Calf [RClf]") is True
    assert nc._is_leg_rigid_bone("NPC L Foot [Lft ]") is True
    assert nc._is_leg_rigid_bone("NPC R Toe0 [RToe]") is True
    # scale bones contain "thigh"/"calf" but are NOT rigid leg bones
    assert nc._is_leg_rigid_bone("NPC L FrontThigh") is False
    assert nc._is_leg_rigid_bone("NPC R RearThigh") is False
    assert nc._is_leg_rigid_bone("NPC L RearCalf [LrClf]") is False
    assert nc._is_leg_rigid_bone("NPC Pelvis [Pelv]") is False


def test_is_physics_jiggle_scale_bone():
    assert nc._is_physics_jiggle_scale_bone("NPC L Butt") is True
    assert nc._is_physics_jiggle_scale_bone("NPC R Butt") is True
    assert nc._is_physics_jiggle_scale_bone("NPC Belly") is True
    assert nc._is_physics_jiggle_scale_bone("L Breast01") is True
    # static leg-shape scale bones are KEPT for leg armor (no physics)
    assert nc._is_physics_jiggle_scale_bone("NPC L FrontThigh") is False
    assert nc._is_physics_jiggle_scale_bone("NPC R RearCalf [RrClf]") is False
    # genital bones are no longer in the scale set at all
    assert nc._is_physics_jiggle_scale_bone("VaginaDeep1") is False
    assert nc._is_physics_jiggle_scale_bone("Clitoral1") is False


def test_is_genital_anatomy_bone():
    assert nc._is_genital_anatomy_bone("Clitoral1") is True
    assert nc._is_genital_anatomy_bone("NPC L Pussy02") is True
    assert nc._is_genital_anatomy_bone("VaginaDeep1") is True
    assert nc._is_genital_anatomy_bone("NPC Anus") is True
    # NOT genital: breast/butt/belly ARE bone-driven on UBE -> must be kept
    assert nc._is_genital_anatomy_bone("L Breast01") is False
    assert nc._is_genital_anatomy_bone("NPC L Butt") is False
    assert nc._is_genital_anatomy_bone("NPC Belly") is False
    assert nc._is_genital_anatomy_bone("NPC Pelvis [Pelv]") is False


def test_strip_genital_weights_renormalizes_to_body_bones():
    # wolf Greaves pattern: a vert mostly Pelvis with a small genital weight that
    # would otherwise pull it to the origin on UBE.
    wm = {
        "NPC Pelvis [Pelv]": [(0, 0.84), (5, 1.0)],
        "Clitoral1": [(0, 0.13)],
        "NPC L Pussy02": [(0, 0.03)],
        "NPC L Thigh [LThg]": [(1, 1.0)],
    }
    out = nc._strip_genital_weights_map(wm)
    assert all(not nc._is_genital_anatomy_bone(b) for b in out)
    pel = dict(out["NPC Pelvis [Pelv]"])
    assert abs(pel[0] - 1.0) < 1e-6      # affected vert renormalized to 1.0
    assert abs(pel[5] - 1.0) < 1e-6      # unaffected vert unchanged
    assert dict(out["NPC L Thigh [LThg]"])[1] == 1.0  # untouched bone preserved


def test_strip_genital_only_vert_falls_back_to_pelvis():
    out = nc._strip_genital_weights_map({"Clitoral1": [(7, 1.0)]})
    assert "Clitoral1" not in out
    assert dict(out["NPC Pelvis [Pelv]"])[7] == 1.0


def test_strip_genital_noop_when_clean():
    clean = {"NPC Spine [Spn0]": [(0, 1.0)]}
    # genital-free map returned unchanged (same object -> zero overhead)
    assert nc._strip_genital_weights_map(clean) is clean


def _legdom_wm():
    # leg-bone-dominant shape (stocking / greave / pant): every vert mostly on
    # the thigh, with butt/belly jiggle on a subset.
    return {
        "NPC L Thigh [LThg]": [(i, 0.8) for i in range(10)],
        "NPC L Butt": [(i, 0.2) for i in range(5)],
        "NPC Belly": [(i, 0.2) for i in range(5, 10)],
    }


def test_strip_jiggle_keeps_source_jiggle_on_fitted_leg_cloth():
    # Skin-tight pant/stocking: butt+belly are SOURCE bones -> kept so the cloth
    # conforms to the jiggling UBE body. Stripping them (the old bug) made fitted
    # pants go rigid and the body clipped straight through.
    out = nc._strip_jiggle_weights_map(
        _legdom_wm(),
        src_bones={"NPC L Thigh [LThg]", "NPC L Butt", "NPC Belly"},
        force=False)
    assert out.get("NPC L Butt")           # source jiggle kept
    assert out.get("NPC Belly")            # source jiggle kept


def test_strip_jiggle_removes_grafted_jiggle_on_rigid_greave():
    # Rigid metal greave: the converter GRAFTED butt/belly (absent from source) ->
    # strip it so the plate reverts and doesn't collapse under physics jiggle.
    out = nc._strip_jiggle_weights_map(
        _legdom_wm(),
        src_bones={"NPC L Thigh [LThg]"},
        force=False)
    assert not out.get("NPC L Butt")       # grafted -> stripped
    assert not out.get("NPC Belly")        # grafted -> stripped


# ---- fitted-cloth body conform pass (skin-tight garment clip fix) -----------

def test_conform_gate_constants_present():
    # the conform tunables exist and have sane defaults
    assert 0.0 < nc._CONFORM_BLEND <= 1.0
    assert nc._CONFORM_DELTA > 0.0
    assert nc._CONFORM_FIT_FRAC > 0.0
    assert nc._CONFORM_MIN_JIGGLE_VERTS >= 1


def test_conform_disabled_by_env(monkeypatch):
    # CBBE2UBE_NO_CONFORM kill switch -> immediate no-op, no body load
    monkeypatch.setattr(nc, "CONFORM_FITTED_CLOTH", False)
    assert nc._conform_fitted_to_body("whatever_1.nif", biped_slots=0) == 0


def test_conform_skips_hands_feet():
    # hands(33)/feet(37) are not the clip class -> never conformed (guard runs
    # before any body load, so this holds even without the UBE body present)
    assert nc._conform_fitted_to_body(
        "x_1.nif", biped_slots=nc.BIPED_SLOT33_BIT) == 0
    assert nc._conform_fitted_to_body(
        "x_1.nif", biped_slots=nc.BIPED_SLOT37_BIT) == 0


def test_conform_missing_file_is_safe(tmp_path):
    # a path that can't be loaded must return 0, never raise
    assert nc._conform_fitted_to_body(
        str(tmp_path / "does_not_exist_1.nif"), biped_slots=0) == 0


# ---- the pure per-vert conform decision (_conform_blend_vert) ---------------

def test_conform_blend_skips_already_matched():
    # weights within the delta of the body -> leave untouched (None)
    dv = {"NPC L Thigh [LThg]": 0.60, "NPC Pelvis [Pelv]": 0.40}
    bd = {"NPC L Thigh [LThg]": 0.63, "NPC Pelvis [Pelv]": 0.37}  # max delta 0.03
    assert nc._conform_blend_vert(dv, bd, blend=0.9, delta=0.08) is None


def test_conform_blend_skips_no_shared_bones():
    dv = {"CustomChainBone": 1.0}
    bd = {"NPC L Thigh [LThg]": 1.0}
    assert nc._conform_blend_vert(dv, bd, blend=0.9, delta=0.08) is None


def test_conform_blend_moves_toward_body_and_renormalizes():
    # the inner-back-thigh case: pant 54% thigh, body 65% -> blend closes ~90%
    dv = {"NPC L Thigh [LThg]": 0.54, "NPC Pelvis [Pelv]": 0.46}
    bd = {"NPC L Thigh [LThg]": 0.65, "NPC Pelvis [Pelv]": 0.35}
    out = nc._conform_blend_vert(dv, bd, blend=0.9, delta=0.08)
    assert out is not None
    assert abs(sum(out.values()) - 1.0) < 1e-6          # renormalized
    # moved toward the body but not past it (blend < 1.0)
    assert 0.54 < out["NPC L Thigh [LThg]"] < 0.65


def test_conform_blend_keeps_bone_set_no_body_only_bones_added():
    # a body-only bone (Pelvis) must NOT be grafted onto the vert; the vert's own
    # bones can only shrink -> partition palettes stay valid.
    dv = {"NPC L Thigh [LThg]": 0.80, "GarmentBone": 0.20}
    bd = {"NPC L Thigh [LThg]": 0.60, "NPC Pelvis [Pelv]": 0.40}
    out = nc._conform_blend_vert(dv, bd, blend=0.9, delta=0.08)
    assert out is not None
    assert "NPC Pelvis [Pelv]" not in out               # body-only bone NOT added
    assert set(out).issubset(set(dv))                   # bone set only shrinks


def test_conform_blend_matches_reference_formula():
    # spec-lock: the extracted helper must stay byte-identical to the original
    # inline formula it replaced (guards against silent drift on edits).
    def _ref(dv, bd, blend, delta):
        shared = set(dv) & set(bd)
        if not shared:
            return None
        if max(abs(dv.get(b, 0.0) - bd.get(b, 0.0)) for b in shared) <= delta:
            return None
        new = {b: (1.0 - blend) * dv[b] + (blend * bd[b] if b in bd else 0.0)
               for b in dv}
        ss = sum(new.values())
        if ss <= 0:
            return None
        return {b: w / ss for b, w in new.items() if w / ss > 1e-4}

    cases = [
        ({"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}),          # matched
        ({"A": 0.2, "B": 0.8}, {"A": 0.9, "C": 0.1}),          # partial overlap
        ({"A": 0.54, "B": 0.46}, {"A": 0.65, "B": 0.35}),      # the residual case
        ({"X": 1.0}, {"Y": 1.0}),                              # disjoint
        ({"A": 1e-5, "B": 1.0}, {"A": 0.9, "B": 0.1}),         # tiny bone -> drop
    ]
    for dv, bd in cases:
        assert nc._conform_blend_vert(dv, bd, 0.9, 0.08) == _ref(dv, bd, 0.9, 0.08)


def test_is_skeleton_bone_custom_chain_not_misclassified():
    # a custom physics-chain bone whose mod prefix contains a body-part word
    # ('neck', 'spine'...) must NOT be classed as a skeleton bone -- else
    # _precreate_custom_bone_chains skips it and its chain nodes are recreated
    # flat at the origin (cloth sinks through the floor).
    assert nc._is_skeleton_bone("_WiDu_Neck_L_01 02") is False
    assert nc._is_skeleton_bone("_SkirtChain_Spine_03") is False
    # real skeleton / prefix-less jiggle bones still classify correctly
    assert nc._is_skeleton_bone("NPC Spine2 [Spn2]") is True
    assert nc._is_skeleton_bone("L Breast01") is True
    assert nc._is_skeleton_bone("Clitoral1") is True
    assert nc._is_skeleton_bone("NPC Neck [Neck]") is True


def test_hdt_collider_vs_softbody_split(monkeypatch):
    # SMP per-triangle shapes are COLLIDERS (must skip the body-fit graft, or it
    # over-jiggles them and the cloth they collide against implodes/sinks);
    # per-vertex shapes are the soft-body cloth.
    xml = ('<system>'
           '<per-triangle-shape name="WiDu_ColBodySkirt"></per-triangle-shape>'
           '<per-triangle-shape name="ColGround"></per-triangle-shape>'
           '<per-vertex-shape name="Skirt_Big"></per-vertex-shape>'
           '<per-vertex-shape name="Skirt_Short"></per-vertex-shape>'
           '</system>')
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", lambda p: xml)
    monkeypatch.setattr(nc, "CHAIN_TO_SOFTBODY", False)
    assert nc._hdt_collider_shape_names(Path("x.nif")) == {
        "WiDu_ColBodySkirt", "ColGround"}
    assert nc._hdt_softbody_shape_names(Path("x.nif")) == {
        "Skirt_Big", "Skirt_Short"}


def test_hdt_collider_names_empty_when_no_xml(monkeypatch):
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", lambda p: None)
    assert nc._hdt_collider_shape_names(Path("x.nif")) == set()


def test_conform_blend_full_match_at_blend_one():
    # blend=1.0 -> shared bones become EXACTLY the body's (the lever that would
    # close the last inner-back-thigh residual; the QA residual probe relies on
    # this). Vert-only bones drop out (no body weight to keep them).
    dv = {"NPC L Thigh [LThg]": 0.54, "NPC Pelvis [Pelv]": 0.46}
    bd = {"NPC L Thigh [LThg]": 0.65, "NPC Pelvis [Pelv]": 0.35}
    out = nc._conform_blend_vert(dv, bd, blend=1.0, delta=0.08)
    assert out is not None
    assert abs(out["NPC L Thigh [LThg]"] - 0.65) < 1e-6
    assert abs(out["NPC Pelvis [Pelv]"] - 0.35) < 1e-6
