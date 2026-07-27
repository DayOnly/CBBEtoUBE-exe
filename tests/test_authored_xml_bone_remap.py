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

"""#authored-xml-bone-remap (OPT-IN, default OFF) -- retarget an authored CBBE/3BA
physics XML's breast-chain bone names onto the chain the converted UBE mesh carries.
109 of 216 authored XMLs on a real modlist name the 3BA chain.

THE ORIGINAL JUSTIFICATION WAS DISPROVEN -- these tests pin the MECHANICS only,
not a claim that the remap is correct. It was written believing those names were
DEAD (no bust collision at all). They are not: FSMP resolves XML bone names against
the ACTOR SKELETON, and XPMSSE's skeleton_female.nif carries `NPC L Breast`,
`NPC L PreBreast` and `NPC L PreButt` -- verified. The fallback premise ("they
exist but nothing drives them on UBE") is also doubtful: the active body config
drives BOTH naming schemes. So the remap is a hypothesis awaiting an in-game A/B,
and it ships behind `CBBE2UBE_XML_BONE_REMAP`."""
from src.nif_convert import _remap_authored_xml_bones

# What the UBE body reference and a converted UBE armor actually carry.
UBE_BONES = {"L Breast01", "L Breast02", "L Breast03",
             "R Breast01", "R Breast02", "R Breast03",
             "NPC L Butt", "NPC R Butt", "NPC Spine2 [Spn2]"}


def _xml(*bones):
    body = "\n".join(f'\t<bone name="{b}"/>' for b in bones)
    return f"<system>\n{body}\n</system>"


def test_3ba_breast_names_retargeted_to_ube():
    text, plan = _remap_authored_xml_bones(
        _xml("NPC L Breast", "NPC R Breast", "NPC L Butt"), UBE_BONES)
    assert plan == {"NPC L Breast": "L Breast01",
                    "NPC R Breast": "R Breast01"}
    assert '<bone name="L Breast01"/>' in text
    assert '<bone name="R Breast01"/>' in text
    assert "NPC L Breast" not in text
    # A name that already resolves must be left exactly as it was.
    assert '<bone name="NPC L Butt"/>' in text


def test_substring_name_is_not_corrupted():
    # "NPC L Breast" is a PREFIX of "NPC L Breast01": a plain str.replace turns
    # the latter into "L Breast0101". Both must map to their own targets.
    text, plan = _remap_authored_xml_bones(
        _xml("NPC L Breast", "NPC L Breast01"), UBE_BONES)
    assert plan == {"NPC L Breast": "L Breast01",
                    "NPC L Breast01": "L Breast02"}
    assert '<bone name="L Breast01"/>' in text
    assert '<bone name="L Breast02"/>' in text
    assert "0101" not in text


def test_no_rename_when_target_already_declared():
    # Some XMLs carry BOTH schemes; renaming into a name the file already
    # declares would duplicate the bone.
    src = _xml("NPC L Breast", "L Breast01", "L Breast02")
    text, plan = _remap_authored_xml_bones(src, UBE_BONES)
    assert "NPC L Breast" not in plan
    assert text == src
    assert text.count('<bone name="L Breast01"/>') == 1


def test_no_rename_when_mesh_lacks_the_target():
    # PreBreast maps to Breast00, which this mesh does not have -> leave it.
    text, plan = _remap_authored_xml_bones(
        _xml("NPC L PreBreast"), UBE_BONES)
    assert plan == {}
    assert '<bone name="NPC L PreBreast"/>' in text


def test_prebutt_is_never_invented():
    # UBE has no PreButt counterpart. Leaving the reference dead (status quo)
    # beats inventing a target.
    _, plan = _remap_authored_xml_bones(
        _xml("NPC L PreButt", "NPC R PreButt"), UBE_BONES)
    assert plan == {}


def test_name_valid_for_this_mesh_is_left_alone():
    # If the mesh genuinely has the 3BA-named bone, the XML is already correct.
    src = _xml("NPC L Breast")
    text, plan = _remap_authored_xml_bones(
        src, UBE_BONES | {"NPC L Breast"})
    assert plan == {}
    assert text == src


def test_element_text_form_is_remapped():
    src = "<system><bone>NPC L Breast</bone></system>"
    text, plan = _remap_authored_xml_bones(src, UBE_BONES)
    assert plan == {"NPC L Breast": "L Breast01"}
    assert "<bone>L Breast01</bone>" in text


def test_bhunp_style_names_retargeted():
    text, plan = _remap_authored_xml_bones(
        _xml("Breast_L 01", "Breast_R01"), UBE_BONES)
    assert plan == {"Breast_L 01": "L Breast01", "Breast_R01": "R Breast01"}
    assert "Breast_L" not in text


def test_empty_inputs_are_safe():
    assert _remap_authored_xml_bones("", UBE_BONES) == ("", {})
    assert _remap_authored_xml_bones(_xml("NPC L Breast"), set())[1] == {}


# --- defects found in review 2026-07-25 -------------------------------------

def test_two_aliases_cannot_claim_the_same_target():
    """NOT INJECTIVE: `NPC L Breast`, `Breast_L 01` and `Breast_L01` all target
    `L Breast01`. Without a claimed-target guard an XML declaring two of them
    emits TWO `<bone name="L Breast01">` entries -- two rigid bodies for one
    skeleton node, the very duplicate the `new in present` guard prevents."""
    text, plan = _remap_authored_xml_bones(
        _xml("NPC L Breast", "Breast_L01"), UBE_BONES)
    assert list(plan.values()).count("L Breast01") == 1
    assert text.count('<bone name="L Breast01"/>') == 1


def test_whitespace_padded_element_text_is_actually_substituted():
    """`present` is built with .strip(), so a padded `<bone>` is DETECTED. If the
    substitution does not also match the padding, the plan is non-empty while the
    document is unchanged -- and the caller logs a retarget it never made."""
    src = "<system><bone>\n\tNPC L Breast\n</bone></system>"
    text, plan = _remap_authored_xml_bones(src, UBE_BONES)
    assert plan == {"NPC L Breast": "L Breast01"}
    assert "NPC L Breast" not in text
    assert "L Breast01" in text


def test_a_non_empty_plan_always_means_the_text_changed():
    """The invariant behind the log line: never report a rename that did not
    happen. A name detected but not substitutable must report nothing."""
    for src in (_xml("NPC L Breast"),
                "<system><bone>\n NPC L Breast \n</bone></system>",
                "<system><bone name='NPC L Breast'/></system>"):  # single-quoted
        text, plan = _remap_authored_xml_bones(src, UBE_BONES)
        assert bool(plan) == (text != src), f"plan/text disagree for {src!r}"


def test_name_is_not_matched_mid_string():
    """The lookbehind must not permit a match starting inside a longer name."""
    src = _xml("Custom NPC L Breast")
    text, plan = _remap_authored_xml_bones(src, UBE_BONES)
    assert plan == {} and text == src


def test_remap_is_opt_in_and_defaults_off():
    """Shipped once ungated on 109 XMLs with a justification later disproven (the
    actor skeleton DOES carry `NPC L Breast`, and the active body config drives
    both naming schemes). It stays off until an in-game A/B earns it."""
    import importlib
    import os
    prev = os.environ.pop("CBBE2UBE_XML_BONE_REMAP", None)
    try:
        nc = importlib.reload(importlib.import_module("src.nif_convert"))
        assert nc.XML_BONE_REMAP_ENABLED is False
        os.environ["CBBE2UBE_XML_BONE_REMAP"] = "1"
        nc = importlib.reload(importlib.import_module("src.nif_convert"))
        assert nc.XML_BONE_REMAP_ENABLED is True
    finally:
        os.environ.pop("CBBE2UBE_XML_BONE_REMAP", None)
        if prev is not None:
            os.environ["CBBE2UBE_XML_BONE_REMAP"] = prev
        importlib.reload(importlib.import_module("src.nif_convert"))
