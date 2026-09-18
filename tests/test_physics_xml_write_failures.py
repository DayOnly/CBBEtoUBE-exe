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

"""A failed physics-XML write must be recorded, not ship the old physics. #silent-xml-write

Four passes rewrite a piece's HDT-SMP XML and swallowed a failed write -- a
locked or read-only file, a full disk -- so the piece kept its previous physics
and nothing said so. Each is driven here to its write with the write refused.
The fixtures follow tests/test_xml_encoding_roundtrip.py (fake meshes) and
tests/test_bust_collider_split.py (a fake pynifly). The three NIF backup restores
need a real NIF that fails its post-save check, so they are held by the
structural ratchets in test_no_silent_pass_failures and test_deterministic_output.
"""
from __future__ import annotations

import pytest

import src.nif_convert as nc
from src import nif_convert_bust as bust
from src import nif_convert_physics as phys

_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<system>\n'
    '\t<per-triangle-shape name="Keep">\n'
    '\t\t<margin>0.1</margin>\n'
    '\t\t<tag>Body</tag>\n'
    '\t</per-triangle-shape>\n'
    '\t<bone name="NPC Spine [Spn0]">\n'
    '\t\t<mass>1.5</mass>\n'
    '\t</bone>\n'
    '</system>\n'
)
_CLOTH_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<system>\n'
    '\t<per-vertex-shape name="Cloth">\n'
    '\t\t<tag>Fabric</tag>\n'
    '\t\t<can-collide-with-tag>ColBody</can-collide-with-tag>\n'
    '\t</per-vertex-shape>\n'
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


class _ED:
    def __init__(self, name):
        self.name = name


class _Root:
    def __init__(self, eds):
        self._eds = eds

    def extra_data(self):
        return self._eds


class _BustNif:
    def __init__(self, shapes, eds):
        self.shapes = [_Shape(n) for n in shapes]
        self.rootNode = _Root([_ED(n) for n in eds])


@pytest.fixture
def recorded():
    before = list(nc._PASS_FAILURES_THIS_PIECE)
    nc._PASS_FAILURES_THIS_PIECE.clear()
    yield lambda label: [e for e in nc._piece_pass_failures()
                         if e.startswith(f"PASS FAILED {label} ")]
    nc._PASS_FAILURES_THIS_PIECE[:] = before


def _refuse(*_a, **_k):
    raise PermissionError("injected: the XML is locked")


def _xml(tmp_path, text, name="phys.xml"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_static_chains_records_a_failed_xml_write(tmp_path, monkeypatch, recorded):
    monkeypatch.setattr(nc, "STATIC_CHAINS", True)
    monkeypatch.setattr(phys, "atomic_write_bytes", _refuse)
    p = _xml(tmp_path, _XML)
    nc._make_chains_static(p)
    assert p.read_bytes() == _XML.encode("utf-8"), "control: the write really failed"
    assert recorded("_make_chains_static/xml-write")


def test_fsmp_hardening_records_a_failed_xml_write(tmp_path, monkeypatch, recorded):
    monkeypatch.setattr(phys, "atomic_write_bytes", _refuse)
    p = _xml(tmp_path, _XML)
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["Other"]))       # 'Keep' is absent -> rewrite
    assert recorded("hdt_xml_shape_dropped"), "control: the pass reached its write"
    assert p.read_bytes() == _XML.encode("utf-8")
    assert recorded("_harden_hdt_xml_for_fsmp/xml-write")


def test_body_collider_records_a_failed_xml_write(tmp_path, monkeypatch, recorded):
    monkeypatch.setenv("CBBE2UBE_BODY_COLLIDER", "1")
    control = _xml(tmp_path, _CLOTH_XML, "control.xml")
    assert nc._ensure_cloth_body_collider(control, _Nif(["Cloth", "BaseShape"])) is True, (
        "control: this fixture must reach the write when the write works")
    monkeypatch.setattr(phys, "atomic_write_bytes", _refuse)
    p = _xml(tmp_path, _CLOTH_XML)
    assert nc._ensure_cloth_body_collider(p, _Nif(["Cloth", "BaseShape"])) is False
    assert recorded("_ensure_cloth_body_collider/xml-write")


def test_bust_split_records_a_failed_xml_write(tmp_path, monkeypatch, recorded):
    for flag in ("BUST_COLLIDER_SPLIT", "TORSO_JIGGLE_TRANSFER", "TRANSFER_BODY_JIGGLE"):
        monkeypatch.setattr(nc, flag, True)
    nif = _BustNif(["Garment", "Garment" + nc._BUST_SPLIT_COL_SUFFIX, "BaseShape"],
                   ["HDT Skinned Mesh Physics Object"])

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return nif

    monkeypatch.setattr(nc, "_pynifly", lambda: _Pyn)
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    _xml(control_dir, '<per-triangle-shape name="Garment">\n', "piece.xml")
    assert nc._split_bust_collider_xml(control_dir / "piece_1.nif") == 1, (
        "control: this fixture must reach the write when the write works")
    monkeypatch.setattr(bust, "atomic_write_bytes", _refuse)
    _xml(tmp_path, '<per-triangle-shape name="Garment">\n', "piece.xml")
    assert nc._split_bust_collider_xml(tmp_path / "piece_1.nif") == 0
    assert recorded("_split_bust_collider_xml/xml-write")


def test_a_write_that_works_records_nothing(tmp_path, monkeypatch, recorded):
    """Negative control: the new recorders fire on a failure, not on every call."""
    monkeypatch.setattr(nc, "STATIC_CHAINS", True)
    p = _xml(tmp_path, _XML)
    nc._make_chains_static(p)
    assert b"<mass>0</mass>" in p.read_bytes()
    assert not recorded("_make_chains_static/xml-write")
