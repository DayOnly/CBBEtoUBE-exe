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
