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

"""#skin-stub-drop (issue #30): a small visible body-skin patch is body skin.

A collar / chest patch of 46-870 verts on 3-15 torso bones, textured with the
body skin, used to fall through every branch of `_looks_like_inline_body`: no
body name, and under the Z / bone / vert floors of the skin heuristic. It then
went down the garment path with its CBBE skin textures and UVs and landed
0.85u off the UBE neck ring -- a visible neck seam in game. The positive
control was the prefix branch: the same stub named `FemaleUnderwearBody:0` is
dropped and the UBE body injected, and that piece is fine.

Now a VISIBLE, SKINNED shape whose diffuse is the body skin is a body whatever
its size, name, bone count or height. Kept out on purpose, each pinned below:
hidden shapes (colliders and clones the renderer never draws), effect-shader
glow overlays, bare hands / feet (body-skin textured, but the injected body has
no hands or feet), unskinned shapes, and shader-less collision proxies (no
diffuse at all).
"""
from __future__ import annotations

from src import nif_convert as nc
from src import nif_convert_writer as writer


_BODY_DIFF = "textures/actors/character/female/FemaleBody_1.dds"
_CLOTH_DIFF = "textures/clothes/robe/robef.dds"


class _Shape:
    """What the detector reads: verts, name, bones, textures, flags."""

    def __init__(self, name, nverts=90, nbones=8, zspan=15.0, diffuse=_BODY_DIFF,
                 bones=None, flags=14, textures=None):
        self.name = name
        self.verts = [(0.0, 0.0, 100.0)] + [(0.0, 0.0, 100.0 + float(zspan))] * (nverts - 1)
        self.bone_names = (list(bones) if bones is not None
                           else [f"NPC Bone{i}" for i in range(nbones)])
        self.textures = ({"Diffuse": diffuse} if textures is None else textures)
        self.flags = flags
        self._backing = None


class _Nif:
    def __init__(self, shapes):
        self.shapes = shapes


# ------------------------------------------------------------- the rule

def test_a_small_skin_patch_on_torso_bones_is_a_body():
    """The exact case: 90 verts, 8 bones, 15u tall, body diffuse."""
    assert nc._looks_like_inline_body(_Shape("Collar")) is True


def test_the_same_patch_with_a_cloth_diffuse_is_not():
    assert nc._looks_like_inline_body(_Shape("Collar", diffuse=_CLOTH_DIFF)) is False


def test_the_prefix_named_stub_still_classifies_as_body():
    """The positive control (70 verts, skin diffuse) keeps its verdict."""
    s = _Shape("FemaleUnderwearBody:0", nverts=70, nbones=5)
    assert nc._looks_like_inline_body(s) is True


def test_a_hidden_skin_shape_is_left_alone():
    """Flag bit 0 set: a collider clone the renderer skips is not the seam."""
    assert nc._looks_like_inline_body(_Shape("Collar", flags=15)) is False


def test_a_hidden_full_body_is_still_a_body():
    """The size-gated heuristic is untouched: a hidden full-height body skin
    on many bones classifies as it did before this rule."""
    s = _Shape("Body", nverts=1527, nbones=22, zspan=103.0, flags=15)
    assert nc._looks_like_inline_body(s) is True


def test_a_glow_overlay_is_left_alone(monkeypatch):
    glow = _Shape("TorsoGlow")
    monkeypatch.setattr(nc, "_shape_has_effect_shader", lambda s: s is glow)
    assert nc._looks_like_inline_body(glow) is False


def test_bare_feet_on_foot_bones_are_left_alone():
    feet = _Shape("Feet", nverts=600, zspan=12.0,
                  bones=["NPC L Foot [Lft ]", "NPC L Toe0 [LToe]",
                         "NPC L Calf [LClf]", "NPC R Foot [Rft ]"])
    assert nc._is_extremity_skin_shape(feet) is True
    assert nc._looks_like_inline_body(feet) is False


def test_a_patch_with_one_torso_bone_is_not_an_extremity():
    """`every` bone must be an extremity bone: a stub on clavicle + upper-arm
    twist is torso skin even though a forearm bone is in its list."""
    s = _Shape("Collar", bones=["NPC L Clavicle [LClv]", "NPC L Forearm [LLar]"])
    assert nc._is_extremity_skin_shape(s) is False
    assert nc._looks_like_inline_body(s) is True


def test_an_unskinned_skin_shape_is_left_alone():
    assert nc._looks_like_inline_body(_Shape("Static", nbones=0)) is False


def test_a_shaderless_collision_proxy_is_left_alone():
    """No diffuse at all: the texture test declines it before anything else."""
    assert nc._looks_like_inline_body(_Shape("Collision", textures={})) is False


# ------------------------------------------------------- where it lands

def test_classify_shapes_routes_the_stub_to_the_body_list():
    """`classify_shapes` is the split the body swap acts on: the stub must be
    in the body half so it is dropped and the UBE body injected."""
    robe = _Shape("robes", nverts=3600, nbones=70, zspan=114.0, diffuse=_CLOTH_DIFF)
    stub = _Shape("FemBody", nverts=70, nbones=5, zspan=6.4)
    proxy = _Shape("CapeProxy", nverts=81, nbones=18, zspan=52.0, textures={})
    body, armor = writer.classify_shapes(_Nif([robe, stub, proxy]))
    assert body == ["FemBody"]
    assert armor == ["robes", "CapeProxy"]
