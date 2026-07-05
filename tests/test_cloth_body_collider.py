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

"""Regression: a simulated cloth that collides with a body tag (`ColBody`) but
whose only collider is a LOWER-body proxy (a hip-level `Greaves`) lets the larger
UBE breast poke through at the chest. `_ensure_cloth_body_collider` registers the
already-present body shape (BaseShape) as a per-triangle `ColBody` collider so the
cloth rests on the whole UBE body. No new geometry -> no double-body. Off switch:
CBBE2UBE_NO_BODY_COLLIDER=1. #breast-collider (Ancient Falmer cuirass, 1.0)
"""
import re
import pytest

from tests.synthetic_nif import pynifly_available
import src.nif_convert as nc  # noqa: E402

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")

TRIS = [(0, 1, 2), (0, 2, 3), (0, 1, 3), (1, 2, 3)]


@pytest.fixture(autouse=True)
def _enable_collider(monkeypatch):
    # DEFAULT is OFF (it collapsed a body in-game); opt-in for these unit tests.
    monkeypatch.setenv("CBBE2UBE_BODY_COLLIDER", "1")


def _mk_nif(tmp_path, shapes):
    """shapes: list of (name, zlo, zhi) -> a NIF with those shapes at that z-band."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(tmp_path / "col.nif"))
    for name, zlo, zhi in shapes:
        verts = [(0.0, 0.0, zlo), (2.0, 0.0, zlo), (0.0, 2.0, zhi), (0.0, 0.0, zhi)]
        nif.createShapeFromData(name, verts, TRIS, [(0.0, 0.0)] * 4,
                                [(0.0, 0.0, 1.0)] * 4)
    nif.save()
    return pyn.NifFile(filepath=str(tmp_path / "col.nif"))


# Cloth collides with ColBody; only a lower-body Greaves collider supplies it.
_XML_GAP = """<?xml version="1.0"?>
<system>
	<per-triangle-shape name="Greaves">
		<tag>ColBody</tag>
		<can-collide-with-tag>Fabric</can-collide-with-tag>
	</per-triangle-shape>
	<per-vertex-shape name="Cloth">
		<tag>Fabric</tag>
		<can-collide-with-tag>ColBody</can-collide-with-tag>
		<can-collide-with-tag>ground</can-collide-with-tag>
	</per-vertex-shape>
</system>
"""


def _run(tmp_path, xml_text, shapes):
    xml = tmp_path / "c.xml"
    xml.write_text(xml_text)
    nif = _mk_nif(tmp_path, shapes)
    patched = nc._ensure_cloth_body_collider(xml, nif)
    return patched, xml.read_text()


def test_registers_baseshape_when_chest_uncovered(tmp_path):
    # Greaves is lower-body (z<90); BaseShape reaches the chest (z>90).
    patched, out = _run(tmp_path, _XML_GAP,
                        [("Greaves", 30.0, 45.0), ("BaseShape", 95.0, 112.0)])
    assert patched is True
    m = re.search(r'<per-triangle-shape name="BaseShape">(.*?)</per-triangle-shape>',
                  out, re.S)
    assert m, "BaseShape not registered as a collider"
    assert "<tag>ColBody</tag>" in m.group(1)
    assert "<can-collide-with-tag>Fabric</can-collide-with-tag>" in m.group(1)
    assert out.count('name="BaseShape"') == 1


def test_idempotent(tmp_path):
    xml = tmp_path / "c.xml"
    xml.write_text(_XML_GAP)
    nif = _mk_nif(tmp_path, [("Greaves", 30.0, 45.0), ("BaseShape", 95.0, 112.0)])
    assert nc._ensure_cloth_body_collider(xml, nif) is True
    assert nc._ensure_cloth_body_collider(xml, nif) is False  # already done
    assert xml.read_text().count('name="BaseShape"') == 1


def test_no_patch_when_chest_collider_exists(tmp_path):
    # A collider already reaches the chest (z>=90) with the ColBody tag -> no gap.
    xml_covered = _XML_GAP.replace(
        '<per-triangle-shape name="Greaves">',
        '<per-triangle-shape name="ChestPlate"><tag>ColBody</tag></per-triangle-shape>'
        '<per-triangle-shape name="Greaves">')
    patched, out = _run(tmp_path, xml_covered,
                        [("ChestPlate", 96.0, 110.0), ("Greaves", 30.0, 45.0),
                         ("BaseShape", 95.0, 112.0)])
    assert patched is False
    assert 'per-triangle-shape name="BaseShape"' not in out


def test_no_patch_when_cloth_only_hits_ground(tmp_path):
    xml_ground = _XML_GAP.replace(
        "<can-collide-with-tag>ColBody</can-collide-with-tag>\n\t\t", "")
    patched, out = _run(tmp_path, xml_ground,
                        [("Greaves", 30.0, 45.0), ("BaseShape", 95.0, 112.0)])
    assert patched is False


def test_default_off(tmp_path, monkeypatch):
    # Without the opt-in env, the collider is NOT registered (default off, since
    # the full-body collider collapsed the Ancient Falmer body in-game).
    monkeypatch.delenv("CBBE2UBE_BODY_COLLIDER", raising=False)
    patched, out = _run(tmp_path, _XML_GAP,
                        [("Greaves", 30.0, 45.0), ("BaseShape", 95.0, 112.0)])
    assert patched is False
    assert 'per-triangle-shape name="BaseShape"' not in out
