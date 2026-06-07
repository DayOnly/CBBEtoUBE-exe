"""Physics-cloth no-warp + extremity-classification guards (#177).

Two coupled fixes that stop self-simulated cloth (skirt/belt/cape) from
collapsing / falling through the floor after conversion:

1. A floor-length robe/dress whose hem merely GRAZES NPC L/R Foot (a fraction of
   a percent of weight, zero foot-dominant verts) must NOT be classified as a
   hand/foot extremity. The old "any extremity bone present" test misfired and
   routed the robe down the rigid hand/foot path (full warp, no chain-nowarp,
   leg-only scale bones), collapsing its skirt physics. A real boot ENCASES the
   foot (hundreds of foot-dominant verts) and must still classify as fine-anim.

2. The body-delta warp must hold self-simulated chain cloth at its SOURCE
   position (so it rides its source-bind chain bones); only actor-driven body /
   soft-body verts get warped to the UBE body.
"""
import sys
from pathlib import Path

import numpy as np

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.nif_convert import (  # noqa: E402
    _shape_has_fine_animation_bones,
    _physics_chain_nowarp_blend,
)


class FakeShape:
    def __init__(self, name, verts, bone_weights, textures=None):
        self.name = name
        self.verts = verts
        self.bone_weights = bone_weights
        self.bone_names = list(bone_weights.keys())
        self.textures = textures or {"diffuse": "x.dds"}


# ---- classification -------------------------------------------------------

def test_robe_grazing_foot_not_fine_anim():
    # 200 verts on a skirt chain; 5 hem verts graze NPC L Foot at 2% weight ->
    # 0 foot-dominant verts -> NOT a hand/foot shape.
    bw = {
        "TMage_Skirt_L1 01": [(i, 1.0) for i in range(200)],
        "NPC L Foot [Lft ]": [(i, 0.02) for i in range(5)],
    }
    assert _shape_has_fine_animation_bones(
        FakeShape("RobeBody", [(0, 0, 0)] * 200, bw)) is False


def test_boot_is_fine_anim():
    # Most verts majority-weighted to the foot bone -> real foot geometry.
    bw = {
        "NPC L Foot [Lft ]": [(i, 1.0) for i in range(200)],
        "NPC L Calf [LClf]": [(i, 0.1) for i in range(200)],
    }
    assert _shape_has_fine_animation_bones(
        FakeShape("SomeBoot", [(0, 0, 0)] * 200, bw)) is True


def test_named_gauntlet_is_fine_anim_without_hand_bones():
    # Name-based path: stylized gauntlet rigged only to the forearm.
    bw = {"NPC L Forearm [LLar]": [(i, 1.0) for i in range(50)]}
    assert _shape_has_fine_animation_bones(
        FakeShape("FancyGauntlet", [(0, 0, 0)] * 50, bw)) is True


def test_pure_cloth_not_fine_anim():
    bw = {"TMage_Skirt_Back 01": [(i, 1.0) for i in range(60)]}
    assert _shape_has_fine_animation_bones(
        FakeShape("Skirt", [(0, 0, 0)] * 60, bw)) is False


# ---- no-warp blend --------------------------------------------------------

def test_chain_cloth_held_at_source():
    src = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]], float)
    warped = src + 5.0                       # everything moved +5 by the warp
    bw = {
        "TMage_Skirt_L1 01": [(0, 1.0), (1, 1.0)],   # self-simulated chain
        "NPC L Breast": [(2, 1.0)],                  # soft-body (actor-driven)
        "NPC Spine2 [Spn2]": [(3, 1.0)],             # skeleton (actor-driven)
    }
    out = _physics_chain_nowarp_blend(
        FakeShape("RobeBody", src, bw), src, warped)
    assert np.allclose(out[0], src[0])      # chain verts snapped back to source
    assert np.allclose(out[1], src[1])
    assert np.allclose(out[2], warped[2])   # breast soft-body stays warped
    assert np.allclose(out[3], warped[3])   # spine skeleton stays warped


def test_partial_chain_weight_blends_proportionally():
    src = np.array([[0, 0, 0]], float)
    warped = src + 10.0
    # 50% chain / 50% skeleton -> vert ends halfway back toward source.
    bw = {
        "TMage_Skirt_L1 01": [(0, 0.5)],
        "NPC Spine2 [Spn2]": [(0, 0.5)],
    }
    out = _physics_chain_nowarp_blend(
        FakeShape("RobeBody", src, bw), src, warped)
    assert np.allclose(out[0], src[0] + 5.0)


def test_blend_noop_without_chain_bones():
    src = np.array([[0, 0, 0], [1, 0, 0]], float)
    warped = src + 3.0
    bw = {"NPC L Foot [Lft ]": [(0, 1.0), (1, 1.0)]}
    out = _physics_chain_nowarp_blend(
        FakeShape("Boot", src, bw), src, warped)
    assert np.allclose(out, warped)         # no chain bones -> unchanged
