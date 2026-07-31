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

"""Physics bones that NOTHING can resolve -> chain placed at the origin.

Two shipped armours lost their chain anchors this way, by two different routes
into the SAME computation ("which bones must the output NIF carry"):

  A. `_is_skeleton_bone` matches its keyword list as UNANCHORED SUBSTRINGS, so
     the custom chain bone `LArmA 01` matched "arm" and was treated as a bone
     the actor supplies. It is not on any skeleton. Sleeves stretched from the
     shoulder to the world origin.
  B. the XML harvest read only `<bone name=>`, so a chain whose anchor appears
     ONLY as a `<generic-constraint>` bodyA/bodyB was never seen at all.

Both are zero-weight kinematic bones, so no shape's skin references them and
nothing else carries them either.

The regression risk runs the OTHER way: preserving too much. Baking real
skeleton bones into the armour at source bind is what broke swinging skirts on
2026-06-07 (FSMP anchored to a static copy instead of the live actor). So the
tests below pin BOTH directions -- custom bones kept, actor bones still left to
the actor.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import hdt_xml_gen as hx  # noqa: E402
from src import nif_convert as nc  # noqa: E402


# --- name normalisation -------------------------------------------------

def test_norm_bone_ignores_case_and_inner_whitespace():
    """Real data differs in both. XPMSSE writes 'NPC L Foot [Lft ]' with a
    trailing space; XMLs write '[Lft]'. '[PElv]' vs '[Pelv]' differ in case."""
    assert nc._norm_bone("NPC L Foot [Lft ]") == nc._norm_bone(
        "NPC L Foot [Lft]")
    assert nc._norm_bone("NPC Pelvis [PElv]") == nc._norm_bone(
        "NPC Pelvis [Pelv]")
    assert nc._norm_bone("  A   B  ") == "ab"


# --- the XML harvest ----------------------------------------------------

XML = """<system>
  <bone name="NPC L Clavicle [LClv]"/>
  <bone name="LArmA 02"/>
  <bone>  Padded Name  </bone>
  <constraint-group>
    <generic-constraint bodyA="LArmA 02" bodyB="LArmA 01"/>
  </constraint-group>
</system>"""


def test_harvest_includes_constraint_only_bones():
    """`LArmA 01` is never declared -- it exists only as a constraint body.
    That is the bone whose absence dragged the sleeves to the origin."""
    got = nc._xml_referenced_bone_names(XML)
    assert "LArmA 01" in got, "constraint-only bone missed (cause B)"
    assert "LArmA 02" in got
    assert "NPC L Clavicle [LClv]" in got


def test_harvest_includes_element_text_form_stripped():
    assert "Padded Name" in nc._xml_referenced_bone_names(XML)


def test_harvest_is_empty_on_junk_rather_than_raising():
    assert nc._xml_referenced_bone_names("not xml at all") == set()


# --- actor resolvability ------------------------------------------------

def test_custom_chain_bone_is_not_actor_resolvable(monkeypatch):
    """THE regression. `_is_skeleton_bone('LArmA 01')` is True because the name
    contains 'arm'; the actor has no such bone."""
    monkeypatch.setattr(nc, "_actor_skeleton_bone_names",
                        lambda: {"npc l upperarm [luar]", "npc spine [spn0]"})
    assert nc._is_skeleton_bone("LArmA 01") is True, (
        "premise gone: the substring false positive is what this guards")
    assert nc._actor_can_resolve_bone("LArmA 01") is False


def test_real_skeleton_bone_is_actor_resolvable_despite_spacing(monkeypatch):
    monkeypatch.setattr(nc, "_actor_skeleton_bone_names",
                        lambda: {"NPC L Foot [Lft ]"})
    assert nc._actor_can_resolve_bone("NPC L Foot [Lft]") is True


def test_falls_back_to_the_heuristic_when_no_skeleton(monkeypatch):
    """No skeleton found must keep the OLD behaviour, not preserve everything
    -- over-preserving bakes a static skeleton copy into the armour, which is
    its own in-game failure."""
    monkeypatch.setattr(nc, "_actor_skeleton_bone_names", lambda: set())
    assert nc._actor_can_resolve_bone("LArmA 01") is True
    assert nc._actor_can_resolve_bone("SkirtBone 03") is False


def test_a_broken_skeleton_read_does_not_propagate(monkeypatch):
    def boom():
        raise RuntimeError("skeleton unreadable")
    monkeypatch.setattr(nc, "_actor_skeleton_bone_names", boom)
    assert nc._actor_can_resolve_bone("LArmA 01") is True   # heuristic fallback


# --- the postflight check ----------------------------------------------

def _write(tmp_path, text):
    p = tmp_path / "phys.xml"
    p.write_text(text, encoding="utf-8")
    return p


def test_postflight_flags_the_unresolvable_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "_actor_skeleton_bone_names",
                        lambda: {"npc l clavicle [lclv]"})
    w = hx.validate_armor_hdt_xml(
        _write(tmp_path, XML), ["LArmA 02", "NPC L Clavicle [LClv]"])
    joined = " | ".join(w)
    assert "LArmA 01" in joined, "the anchor that breaks the chain went unflagged"
    assert "ORIGIN" in joined


def test_postflight_stays_quiet_about_bones_the_actor_supplies(
        tmp_path, monkeypatch):
    """It used to warn on every declared bone absent from the NIF -- ~45 lines
    per file of pure noise, which is why the six real ones were invisible."""
    monkeypatch.setattr(nc, "_actor_skeleton_bone_names",
                        lambda: {"npc l clavicle [lclv]", "larma 01"})
    w = hx.validate_armor_hdt_xml(
        _write(tmp_path, XML), ["LArmA 02"])
    assert not [x for x in w if "NPC L Clavicle" in x], (
        "warned about a bone the actor resolves")
    assert not [x for x in w if "LArmA 01" in x]


# --- anchor-mode selection ---------------------------------------------

def test_arm_anchor_matches_the_limb_bone_not_the_chain_bone():
    """The chain bone `LArmA 01` must NOT read as an arm ANCHOR -- only the
    actual limb bone does. Getting this backwards is how the anchor set filled
    with chain bones instead of `NPC L Forearm`."""
    assert nc._is_arm_anchor("NPC L Forearm [LLar]") is True
    assert nc._is_arm_anchor("NPC R UpperArm [RUar]") is True
    assert nc._is_arm_anchor("LArmA 01") is False
    assert nc._is_arm_anchor("NPC Pelvis [Pelv]") is False
    assert nc._is_arm_anchor("") is False


def test_arm_keywords_are_not_in_the_upper_body_list():
    """They are deliberately separate: the upper-body list flips the WHOLE file
    to nested, and 3 measured rigs mix a pelvis-anchored skirt with an
    arm-anchored sleeve. Nesting those pelvis chains is the June skirt-sag
    regression."""
    for kw in nc._ARM_ANCHOR_KEYWORDS:
        assert kw not in nc._UPPER_BODY_ANCHOR_KEYWORDS
