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

"""Regression: _finalize's HDT framework-bone re-import must NEVER re-import a
dropped inline body (= double body = HDT-SMP CTD on equip), and must not treat
standard skeleton bones as needing a mesh carrier.

Root case (modded iron cuirass): the source body uniquely carried NPC L/R Hand,
which the authored HDT XML references. After convert_nif dropped the body and
injected the UBE BaseShape, _finalize saw those hand bones "missing" and
re-imported the WHOLE body to provide them -- but they are skeleton bones the
actor already supplies. Result: BaseShape + leftover body = CTD.
"""
from src.nif_convert import _select_framework_bone_carriers, _is_inline_body_name


def test_is_inline_body_name():
    # Canonical body names + vanilla placeholder prefixes -> True; armor/cloth/
    # collider names and the injected UBE BaseShape -> False (kept).
    assert _is_inline_body_name("3BA")
    assert _is_inline_body_name("3BA_Vagina")
    assert _is_inline_body_name("FemaleUnderwearBody:0")
    assert _is_inline_body_name("femalebody")
    assert not _is_inline_body_name("Cuirass")
    assert not _is_inline_body_name("Collision")
    assert not _is_inline_body_name("Belt Col")
    assert not _is_inline_body_name("BaseShape")
    assert not _is_inline_body_name(None)


def test_dropped_body_not_reimported_for_skeleton_bone():
    # NPC L/R Hand are unique to the body but are SKELETON bones -> no carrier
    # needed; the body must not come back.
    xml_bones = {"NPC L Hand [LHnd]", "NPC R Hand [RHnd]", "SkirtF 1_00"}
    present_bones = {"NPC Pelvis [Pelv]", "SkirtF 1_00"}
    source = [
        ("Cuirass", ["NPC Pelvis [Pelv]"]),
        ("Skirt Front", ["SkirtF 1_00"]),
        ("3BA", ["NPC L Hand [LHnd]", "NPC R Hand [RHnd]", "NPC Pelvis [Pelv]"]),
    ]
    carriers = _select_framework_bone_carriers(
        xml_bones, present_bones, source,
        exclude_names={"Cuirass", "Skirt Front"})
    assert "3BA" not in carriers
    assert carriers == []


def test_real_framework_shape_with_custom_bone_is_reimported():
    # A genuine framework shape (Stabilizer) carrying a CUSTOM constraint bone
    # must still be re-imported -- and the body must NOT be, even though it also
    # carries the bone.
    xml_bones = {"StabRoot", "NPC Spine [Spn0]"}
    present_bones = {"NPC Spine [Spn0]"}
    source = [
        ("Stabilizer", ["StabRoot"]),
        ("3BA", ["StabRoot", "NPC Spine [Spn0]"]),
    ]
    carriers = _select_framework_bone_carriers(
        xml_bones, present_bones, source, exclude_names=set())
    assert carriers == ["Stabilizer"]


def test_body_skipped_even_if_sole_carrier_of_custom_bone():
    # Pathological: only the dropped body carries a custom bone. Still skip it
    # (harden prunes the now-unresolved ref) rather than re-create a double body.
    carriers = _select_framework_bone_carriers(
        {"BodyOnlyChain"}, set(), [("3BA", ["BodyOnlyChain"])],
        exclude_names=set())
    assert carriers == []


def test_placeholder_body_prefix_skipped():
    # Vanilla placeholder bodies (FemaleUnderwearBody:0, ...) match by prefix.
    carriers = _select_framework_bone_carriers(
        {"NPC L Hand [LHnd]"}, set(),
        [("FemaleUnderwearBody:0", ["NPC L Hand [LHnd]"])], exclude_names=set())
    assert carriers == []


def test_explicit_skeleton_set_excluded():
    # Bones resolvable via the loaded actor skeleton set (not just the "NPC "
    # prefix) are not counted as needing a carrier.
    carriers = _select_framework_bone_carriers(
        {"WeirdSkeletonBone", "CustomChain"}, set(),
        [("Body", ["WeirdSkeletonBone"]), ("Cloth", ["CustomChain"])],
        skel_bones={"weirdskeletonbone"}, exclude_names=set())
    assert carriers == ["Cloth"]


def test_no_needed_bones_returns_empty():
    carriers = _select_framework_bone_carriers(
        {"NPC Spine [Spn0]"}, {"NPC Spine [Spn0]"},
        [("Cuirass", ["NPC Spine [Spn0]"])], exclude_names=set())
    assert carriers == []
