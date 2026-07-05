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

"""Regression: `detect_physics_chains` must recognise chain bones numbered with
NO underscore (e.g. `SkirtBBone01`) as well as the `Skirt 1_00` underscore form.
A rig that names its skirt bones `SkirtF/B/L/RBone01-03` was getting NO chain
constraints -> the skirt simulated as unconstrained free cloth and flapped wild
("disjointed skirt", the Anequina Armor case). The no-underscore match is gated
on a cloth-chain keyword + 2-3 digits so skeleton bones (`NPC Spine2`,
`ForearmTwist1`) are never mistaken for chains. #chain-nounderscore
"""
from src.hdt_xml_gen import detect_physics_chains


def test_no_underscore_skirt_bones_form_chains():
    bones = [f"Skirt{s}Bone0{i}" for s in "FBLR" for i in (1, 2, 3)]
    chains = detect_physics_chains(bones)
    prefixes = {c.prefix for c in chains}
    assert prefixes == {"SkirtFBone", "SkirtBBone", "SkirtLBone", "SkirtRBone"}, prefixes
    for c in chains:
        assert len(c.bones) == 3
        assert c.bones == sorted(c.bones)  # ordered by index


def test_skeleton_bones_are_not_chains():
    # Trailing digits on real skeleton bones must NOT be read as chains.
    skel = ["NPC Spine [Spn0]", "NPC Spine1 [Spn1]", "NPC Spine2 [Spn2]",
            "NPC L UpperarmTwist1 [LUt1]", "NPC L ForearmTwist2 [LLt2]",
            "NPC L Calf [LClf]", "NPC R Thigh [RThg]", "NPC Head [Head]",
            "L Breast01", "L Breast02", "L Breast03"]
    assert detect_physics_chains(skel) == []


def test_underscore_chains_still_detected():
    # The original underscore form must keep working.
    bones = ["Skirt 1_00", "Skirt 1_01", "Skirt 1_02", "SkirtF 2_00", "SkirtF 2_01"]
    prefixes = {c.prefix for c in detect_physics_chains(bones)}
    assert prefixes == {"Skirt 1", "SkirtF 2"}


def test_single_digit_no_underscore_is_ignored():
    # Single trailing digit (skeleton style) must not match the no-underscore
    # rule even with a keyword-ish name -- avoids over-matching.
    assert detect_physics_chains(["TailBone1", "TailBone2"]) == []


def test_other_cloth_keywords_match():
    for kw in ("Cape", "Tassel", "Flap", "Tail", "Sash"):
        bones = [f"{kw}Bone0{i}" for i in (1, 2, 3)]
        chains = detect_physics_chains(bones)
        assert len(chains) == 1 and len(chains[0].bones) == 3, kw
