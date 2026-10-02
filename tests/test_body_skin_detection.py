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

"""Regression guard for the Forsworn-style double-scale (#164).

A vanilla-topology body skin shipped by an armour replacer (e.g. HDT-SMP
Vanilla's forsworn `ForswornFemaleBody`: ~1.5k verts, ~22 bones, femalebody
diffuse, full character height) must be recognised as a BODY so it routes to
the phase-2 body-swap (source skin dropped, base UBE BaseShape injected) and
scales exactly once. The old 4000-vert / 40-bone generic body heuristic
dropped it into the CLOTH path, where it was warped at its BodySlide preset
bulk and then node-scaled AGAIN at runtime = double-scaled body under skimpy
armour.

The body-skin DIFFUSE texture gate is what keeps SMP skirts / robes (cloth
diffuse) out, so the vert/bone floors can be relaxed once it passes.
"""

from src import nif_convert


class _FakeShape:
    """Minimal stand-in for nif_io.Shape / a raw pynifly shape, carrying just
    what `_looks_like_inline_body` + `_shape_diffuse_is_body_skin` read."""
    def __init__(self, name, nverts, nbones, zspan, diffuse):
        # verts spanning [0, zspan] in Z so (max-min) == zspan
        self.verts = [(0.0, 0.0, 0.0)] + [(0.0, 0.0, float(zspan))] * (nverts - 1)
        self.name = name
        self.bone_names = [f"Bone{i}" for i in range(nbones)]
        self.textures = {"Diffuse": diffuse}
        self._backing = None


_BODY_DIFF = "textures\\actors\\character\\female\\femalebody_1.dds"
_CLOTH_DIFF = "textures\\armor\\forsworn\\forswornarmorf.dds"


def test_vanilla_topology_body_skin_detected_as_body():
    # The exact Forsworn case: low verts + low bones, but a body diffuse.
    s = _FakeShape("ForswornFemaleBody", nverts=1527, nbones=22,
                   zspan=103.0, diffuse=_BODY_DIFF)
    assert nif_convert._looks_like_inline_body(s) is True


def test_smp_skirt_not_misdetected_as_body():
    # Full height + many SMP bones + plenty of verts, but a CLOTH diffuse —
    # the texture gate must keep it classified as cloth (armour).
    s = _FakeShape("Skirt", nverts=2500, nbones=35, zspan=95.0,
                   diffuse=_CLOTH_DIFF)
    assert nif_convert._looks_like_inline_body(s) is False


def test_tiny_body_textured_patch_is_a_body():
    # #skin-stub-drop (issue #30): a small VISIBLE body-textured patch on
    # torso bones is body skin -- the neck-seam stub. The 500-vert floor used
    # to keep it on the garment path, citing a "tiny decal" #164 never
    # measured; the patch is what that floor actually let through.
    s = _FakeShape("decal", nverts=120, nbones=20, zspan=90.0,
                   diffuse=_BODY_DIFF)
    assert nif_convert._looks_like_inline_body(s) is True


def test_short_torso_skin_patch_is_a_body():
    # Body diffuse, torso bones, does not span the character: still body skin
    # (the injected body covers it), so it is dropped, not refit.
    s = _FakeShape("chest_patch", nverts=800, nbones=20, zspan=30.0,
                   diffuse=_BODY_DIFF)
    assert nif_convert._looks_like_inline_body(s) is True


def test_bare_hands_on_hand_bones_are_not_a_body():
    # Hands share the body diffuse, and the injected body has no hands: a
    # shape skinned ONLY to hand / finger / forearm bones stays.
    s = _FakeShape("hand_patch", nverts=800, nbones=0, zspan=30.0,
                   diffuse=_BODY_DIFF)
    s.bone_names = ["NPC L Hand [LHnd]", "NPC L Forearm [LLar]",
                    "NPC L Finger00 [LF00]", "NPC L Finger01 [LF01]"]
    assert nif_convert._looks_like_inline_body(s) is False


def test_highpoly_3ba_body_still_detected():
    # The pre-existing high-poly body path must still classify as body.
    s = _FakeShape("CustomBody", nverts=18436, nbones=45, zspan=103.0,
                   diffuse=_BODY_DIFF)
    assert nif_convert._looks_like_inline_body(s) is True
