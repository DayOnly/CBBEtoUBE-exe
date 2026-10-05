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

"""#xml-shape-name-case -- an XML shape reference that differs from one mesh shape
only in letter case names that shape.

Found in a real run: a skirt mod's XML names its cloth `Skirt` and the author's own
mesh calls the shape `skirt`. The prune compared names case for case, deleted the
cloth block, and the skirt shipped with colliders only. 6 of the 34 dropped blocks
of that run were case-only. The soft-body and collider sets compared the same way,
so the shape's authored skin was not protected either.
"""
import xml.etree.ElementTree as ET

import pytest

from src import nif_convert as nc
from tests import _converter_sources as _cs  # patch on every module that binds a name
from src import nif_convert_physics as ph

_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<system>\n'
    '\t<per-vertex-shape name="Skirt">\n'
    '\t\t<margin>0.1</margin>\n'
    '\t\t<tag>Cloth</tag>\n'
    '\t</per-vertex-shape>\n'
    '\t<per-triangle-shape name="feet">\n'
    '\t\t<margin>0.1</margin>\n'
    '\t\t<tag>Body</tag>\n'
    '\t</per-triangle-shape>\n'
    '\t<per-triangle-shape name="Gone">\n'
    '\t\t<tag>Body</tag>\n'
    '\t</per-triangle-shape>\n'
    '</system>\n'
)


class _Shape:
    def __init__(self, name):
        self.name = name
        self.bone_names = ["NPC Spine [Spn0]"]
        self.verts = [(0.0, 0.0, 95.0)]


class _Nif:
    def __init__(self, names):
        self.shapes = [_Shape(n) for n in names]
        self.nodes = {"NPC Spine [Spn0]": object()}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(nc, "XML_SHAPE_NAME_CASE", True)
    nc._begin_piece_pass_log()
    yield
    nc._begin_piece_pass_log()


def _refs(raw: bytes):
    root = ET.fromstring(raw.decode("utf-8"))
    return sorted(el.get("name") for el in root
                  if el.tag in ("per-vertex-shape", "per-triangle-shape"))


# --- the matching rule -------------------------------------------------------

def test_a_reference_one_shape_matches_by_case_names_that_shape():
    assert ph._xml_shape_spellings({"Skirt"}, {"skirt", "wst"}) == {"Skirt": "skirt"}


def test_an_exact_reference_is_not_remapped():
    assert ph._xml_shape_spellings({"skirt"}, {"skirt"}) == {}


def test_a_reference_two_shapes_match_by_case_is_left_alone():
    # the real converted file carries both `feet` and `Feet`
    assert ph._xml_shape_spellings({"FEET"}, {"feet", "Feet"}) == {}


def test_a_shape_another_reference_names_exactly_is_not_taken_twice():
    assert ph._xml_shape_spellings({"Skirt", "skirt"}, {"skirt"}) == {}


def test_two_case_only_references_to_one_shape_are_both_left_alone():
    assert ph._xml_shape_spellings({"Skirt", "SKIRT"}, {"skirt"}) == {}


def test_a_plural_is_not_a_case_match():
    assert ph._xml_shape_spellings({"Tassets"}, {"Tasset"}) == {}


def test_the_switch_turns_the_rule_off(monkeypatch):
    monkeypatch.setattr(nc, "XML_SHAPE_NAME_CASE", False)
    assert ph._xml_shape_spellings({"Skirt"}, {"skirt"}) == {}


# --- the prune ---------------------------------------------------------------

def test_the_cloth_block_is_kept_under_the_meshs_spelling(tmp_path):
    """THE DEFECT: the skirt's cloth block was deleted."""
    p = tmp_path / "skirt.xml"
    p.write_bytes(_XML.encode("utf-8"))
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["skirt", "feet", "Feet"]))
    assert _refs(p.read_bytes()) == ["feet", "skirt"]
    fails = " ".join(nc._piece_pass_failures())
    assert "'Gone'" in fails and "Skirt" not in fails
    assert any("#xml-shape-name-case" in e and "'Skirt' -> 'skirt'" in e
               for e in nc._piece_pass_effects())


def test_with_the_switch_off_the_block_is_dropped_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "XML_SHAPE_NAME_CASE", False)
    p = tmp_path / "skirt.xml"
    p.write_bytes(_XML.encode("utf-8"))
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["skirt", "feet", "Feet"]))
    assert _refs(p.read_bytes()) == ["feet"]


def test_a_rename_alone_is_written(tmp_path):
    """No block goes, one is renamed: the file must still be rewritten."""
    xml = _XML.replace('name="Gone"', 'name="wst"')
    p = tmp_path / "skirt.xml"
    p.write_bytes(xml.encode("utf-8"))
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["skirt", "feet", "wst"]))
    assert _refs(p.read_bytes()) == ["feet", "skirt", "wst"]
    assert not nc._piece_pass_failures()


def test_the_replayed_prune_renames_too():
    """discovery's smp-gain rule replays the prune through this function; it must
    see the same surviving block the conversion keeps."""
    out = ph._hdt_xml_shape_pruned(_XML.encode("utf-8"), {"skirt", "feet"})
    assert _refs(out) == ["feet", "skirt"]


def test_the_replayed_prune_leaves_an_untouched_file_byte_for_byte():
    raw = _XML.replace('name="Skirt"', 'name="skirt"').replace(
        'name="Gone"', 'name="wst"').encode("utf-8")
    assert ph._hdt_xml_shape_pruned(raw, {"skirt", "feet", "wst"}) is raw


# --- the soft-body / collider sets ------------------------------------------

def test_the_soft_body_set_names_the_meshs_shape(monkeypatch):
    monkeypatch.setattr(ph, "_read_source_hdt_xml_text", lambda *a, **k: _XML)
    names = ph._hdt_softbody_shape_names("x.nif", nif=_Nif(["skirt", "feet"]))
    assert "skirt" in names and "Skirt" in names


def test_the_collider_set_names_the_meshs_shape(monkeypatch):
    xml = _XML.replace('name="feet"', 'name="Feet"')
    monkeypatch.setattr(ph, "_read_source_hdt_xml_text", lambda *a, **k: xml)
    names = ph._hdt_collider_shape_names("x.nif", nif=_Nif(["skirt", "feet"]))
    assert "feet" in names


def test_without_a_readable_mesh_the_sets_are_the_xmls_own(monkeypatch, tmp_path):
    monkeypatch.setattr(ph, "_read_source_hdt_xml_text", lambda *a, **k: _XML)
    assert ph._hdt_softbody_shape_names(tmp_path / "none.nif") == {"Skirt"}


def test_the_mesh_names_are_read_once_per_file_state(monkeypatch, tmp_path):
    p = tmp_path / "a.nif"
    p.write_bytes(b"x")
    loads = []

    class _Pyn:
        def NifFile(self, filepath):
            loads.append(filepath)
            return _Nif(["skirt"])

    monkeypatch.setattr(nc, "_pynifly", lambda: _Pyn())
    ph._hdt_xml_cache_clear()
    assert ph._mesh_shape_names(p) == {"skirt"}
    assert ph._mesh_shape_names(p) == {"skirt"}
    assert len(loads) == 1
    ph._hdt_xml_cache_clear()
    ph._mesh_shape_names(p)
    assert len(loads) == 2


def test_the_switch_is_on_by_default():
    assert 'not _flag("CBBE2UBE_NO_XML_SHAPE_CASE", False)' in _cs.whole_text()


def test_the_replayed_prune_returns_a_rename_alone():
    """No block goes, one is renamed: the replay must return the renamed bytes,
    not the raw file."""
    raw = _XML.replace('name="Gone"', 'name="wst"').encode("utf-8")
    out = ph._hdt_xml_shape_pruned(raw, {"skirt", "feet", "wst"})
    assert _refs(out) == ["feet", "skirt", "wst"]
