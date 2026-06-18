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

"""End-to-end guard for HDT-SMP XML <-> NIF shape consistency.

`_harden_hdt_xml_for_fsmp` must DROP any per-vertex/per-triangle-shape block whose
shape name isn't in the converted NIF (Faster HDT-SMP can't attach to a shape that
isn't there -> the whole physics file silently fails to load), while KEEPING the
shapes that are present. Builds a REAL NIF + XML and runs the REAL pruner -- the
shape-name<->NIF consistency the pure-logic tests never exercise on a real mesh.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402
from tests.synthetic_nif import build_shape_nif, pynifly_available  # noqa: E402

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")


def _harden_xml(tmp_path, present_name, xml_body):
    """Build a 1-shape NIF named `present_name`, write `xml_body`, run the real
    FSMP-hardening pruner against that NIF, and return the resulting XML text."""
    build_shape_nif(tmp_path / "src.nif", name=present_name)
    nif = nc._pynifly().NifFile(filepath=str(tmp_path / "src.nif"))
    xmlp = tmp_path / "armor.xml"
    xmlp.write_text(xml_body)
    nc._harden_hdt_xml_for_fsmp(xmlp, nif)
    return xmlp.read_text()


def test_harden_drops_xml_shape_absent_from_nif(tmp_path):
    # XML references "Skirt" (in the NIF) + "Ghost" (NOT in the NIF). The Ghost
    # block must be pruned so FSMP never tries to attach to a missing shape.
    out = _harden_xml(
        tmp_path, "Skirt",
        '<system>\n'
        '<per-vertex-shape name="Skirt">\n<x/>\n</per-vertex-shape>\n'
        '<per-vertex-shape name="Ghost">\n<x/>\n</per-vertex-shape>\n'
        '</system>\n')
    assert "Skirt" in out, "present shape was wrongly dropped"
    assert "Ghost" not in out, "absent shape block was not pruned"


def test_harden_keeps_present_shape_intact(tmp_path):
    # Every XML shape exists in the NIF -> nothing is pruned (no over-pruning).
    out = _harden_xml(
        tmp_path, "Skirt",
        '<system>\n'
        '<per-vertex-shape name="Skirt">\n<x/>\n</per-vertex-shape>\n'
        '</system>\n')
    assert "Skirt" in out
    assert "per-vertex-shape" in out
