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

"""An authored physics XML with junk after `</system>`, or a default xmlns on
`<system>` (2026-09-25).

Two halves:
  * `validate_armor_hdt_xml` used to stop at "failed to parse" / "root tag !=
    'system'" and skip every bone and collision check on those pieces, though
    FSMP reads them fine. It now validates the declarations and names the tail;
    damage INSIDE the root still fails.
  * `#hdt-xml-sanitise` (opt-in) voices a successful repair as a pass EFFECT at
    both of its sites, not as "PASS FAILED hdt_xml_sanitised".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from src import hdt_xml_gen as hx                 # noqa: E402
from src import nif_convert as nc                 # noqa: E402
from src import nif_convert_physics as ph         # noqa: E402
from src import nif_convert_telemetry as tel      # noqa: E402
from tests import _converter_sources as _cs       # noqa: E402
from tests.synthetic_nif import (                 # noqa: E402
    build_skinned_shape_nif, pynifly_available)

PTR = "HDT Skinned Mesh Physics Object"
# A bone nothing can resolve: its warning proves the bone checks RAN.
BODY = ('<system>\n  <bone name="Nowhere Bone"/>\n'
        '  <per-triangle-shape name="Col">\n'
        '    <can-collide-with-tag>Fabric</can-collide-with-tag>\n'
        '  </per-triangle-shape>\n</system>')
JUNK = BODY + "undefined</xml>\n"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(nc, "_SKELETON_BONES_CACHE", set())
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    tel._begin_piece_pass_log()
    yield
    tel._begin_piece_pass_log()


def _validate(tmp_path, text: str):
    p = tmp_path / "piece.xml"
    p.write_bytes(text.encode("utf-8"))
    return hx.validate_armor_hdt_xml(p, ["NPC Pelvis [Pelv]"])


# ------------------------------------------------------------ the validator

def test_junk_after_the_root_still_gets_every_check(tmp_path):
    w = _validate(tmp_path, JUNK)
    assert not any("failed to parse" in x for x in w)
    assert any("'Nowhere Bone'" in x for x in w)
    assert any("text after its root element" in x for x in w)


def test_a_clean_file_gets_no_tail_note(tmp_path):
    w = _validate(tmp_path, BODY + "\n")
    assert not any("text after its root element" in x for x in w)
    assert any("'Nowhere Bone'" in x for x in w)


def test_a_default_namespace_still_gets_every_check(tmp_path):
    w = _validate(tmp_path, BODY.replace("<system>",
                                         '<system xmlns="urn:validator">') + "\n")
    assert not any("root tag" in x for x in w)
    assert any("'Nowhere Bone'" in x for x in w)
    # the collision check reads the namespaced collider too
    assert not any("no per-triangle-shape" in x for x in w)


def test_damage_inside_the_root_still_fails_to_parse(tmp_path):
    w = _validate(tmp_path, '<system>\n  <bone name="A">\n</system>\n')
    assert any("failed to parse" in x for x in w)


# ------------------------------------------------ the sanitiser's voice

needs_nif = pytest.mark.skipif(not pynifly_available(),
                               reason="pynifly native lib unavailable")


@needs_nif
def test_a_repair_on_read_is_an_effect_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "HDT_XML_SANITISE", True)
    d = tmp_path / "Mod" / "meshes" / "brand"
    d.mkdir(parents=True)
    (d / "piece.xml").write_bytes(JUNK.encode("utf-8"))
    src = build_skinned_shape_nif(d / "piece_1.nif", name="Col")
    nf = nc._pynifly().NifFile(filepath=str(src))
    from pyn.pynifly import NiStringExtraData  # type: ignore
    NiStringExtraData.New(nf, name=PTR, string_value="Meshes\\brand\\piece.xml",
                          parent=nf.rootNode)
    nf.save()
    text = ph._read_source_hdt_xml_text_uncached(src)
    assert text is not None and "undefined" not in text
    assert any("CHANGED BY #hdt-xml-sanitise" in e
               for e in tel._piece_pass_effects())
    assert not any("sanitis" in f for f in tel._piece_pass_failures())


@needs_nif
def test_a_repair_at_the_ship_site_is_an_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "HDT_XML_SANITISE", True)
    _cs.patch(monkeypatch, "_find_cbbe_base_body", lambda *a, **k: None)
    _cs.patch(monkeypatch, "_find_ube_femalebody", lambda *a, **k: None)
    authored = tmp_path / "authored.xml"
    authored.write_bytes(JUNK.encode("utf-8"))
    _cs.patch(monkeypatch, "_read_source_hdt_xml_disk",
              lambda p, nif=None: authored)
    out = tmp_path / "Out" / "meshes" / "brand"
    out.mkdir(parents=True)
    dst = build_skinned_shape_nif(out / "piece_1.nif", name="Col")
    src = build_skinned_shape_nif(tmp_path / "piece_1.nif", name="Col")
    assert ph._finalize_hdt_physics(dst, src) is True
    assert b"undefined" not in (out / "piece.xml").read_bytes()
    assert any("CHANGED BY #hdt-xml-sanitise" in e
               for e in tel._piece_pass_effects())
    assert not any("sanitis" in f for f in tel._piece_pass_failures())
