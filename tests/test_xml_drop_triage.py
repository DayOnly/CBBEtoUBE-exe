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

"""#xml-drop-triage (audit 2026-10-20 F01).

The FSMP prune reported 146 "PASS FAILED" drops for 4 real losses. A pruned block is a failure only when the source
mesh had that shape and the converter neither replaced it with its own proxy nor swapped it for the injected body.
"""
from src import nif_convert as nc
from src import nif_convert_physics as phys
from tests.test_physics_xml_write_failures import _Nif, _xml, recorded  # noqa: F401

XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<system>\n'
       '\t<per-triangle-shape name="Gone">\n\t\t<tag>a</tag>\n\t</per-triangle-shape>\n'
       '\t<per-triangle-shape name="Shared">\n\t\t<tag>b</tag>\n\t</per-triangle-shape>\n'
       '\t<per-triangle-shape name="Skirt">\n\t\t<tag>c</tag>\n\t</per-triangle-shape>\n'
       '\t<per-triangle-shape name="3BA">\n\t\t<tag>d</tag>\n\t</per-triangle-shape>\n'
       '</system>\n')


def test_only_a_real_loss_is_a_failure(tmp_path, recorded):  # noqa: F811
    p = _xml(tmp_path, XML)
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["SkirtCol", "BaseShape"]), src_shapes={"Gone", "Skirt", "3BA"})
    got = recorded("hdt_xml_shape_dropped")
    assert len(got) == 1 and "'Gone'" in got[0]
    assert "Shared" not in got[0] and "'Skirt'" not in got[0] and "3BA" not in got[0]


def test_without_the_source_or_switched_off_every_drop_counts(tmp_path, recorded, monkeypatch):  # noqa: F811
    p = _xml(tmp_path, XML)
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["SkirtCol", "BaseShape"]))
    assert "'Shared'" in recorded("hdt_xml_shape_dropped")[0]
    monkeypatch.setattr(phys, "XML_DROP_TRIAGE", False)
    p = _xml(tmp_path, XML, "b.xml")
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["SkirtCol", "BaseShape"]), src_shapes={"Gone", "Skirt", "3BA"})
    assert any("'Shared'" in r for r in recorded("hdt_xml_shape_dropped"))
