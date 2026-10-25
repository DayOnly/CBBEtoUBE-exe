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

"""#xml-body-repoint (audit 2026-10-20 F02).

The body swap replaces the source body with the injected `BaseShape`; the prune then dropped the author's collider
block for that body, and the simulated top that bounced off it passed through the body. The block is re-pointed at
`BaseShape` when a simulated shape really collides with it.
"""
from src import nif_convert_physics as ph

XML = """<?xml version="1.0" encoding="UTF-8"?>
<system>
    <per-triangle-shape name="VirtualGround">
\t\t<tag>ground</tag>
\t\t<no-collide-with-tag>ground</no-collide-with-tag>
\t\t<no-collide-with-tag>body</no-collide-with-tag>
\t</per-triangle-shape>
\t<per-triangle-shape name="3BA">
\t\t<margin>0.5</margin>
\t\t<tag>body</tag>
\t\t<can-collide-with-tag>arm</can-collide-with-tag>
\t\t<no-collide-with-tag>body</no-collide-with-tag>
\t\t<no-collide-with-tag>ground</no-collide-with-tag>
\t</per-triangle-shape>
\t<per-vertex-shape name="top">
\t\t<tag>arm</tag>
\t\t<no-collide-with-tag>arm</no-collide-with-tag>
\t\t<can-collide-with-tag>body</can-collide-with-tag>
\t\t<can-collide-with-tag>ground</can-collide-with-tag>
\t</per-vertex-shape>
</system>"""
MESH = {"VirtualGround", "top", "BaseShape"}


def _kept(xml, mesh):
    return "\n".join(line for line, gone, _ in ph._hdt_shape_prune(xml.splitlines(), mesh) if not gone)


def test_the_body_collider_is_repointed_not_dropped():
    out = _kept(XML, MESH)
    assert '<per-triangle-shape name="BaseShape">' in out and 'name="3BA"' not in out
    assert "<margin>0.5</margin>" in out, "the author's settings ride along"


def test_a_body_only_the_ground_meets_stays_pruned():
    xml = XML.replace("<can-collide-with-tag>body</can-collide-with-tag>", "")\
             .replace("<can-collide-with-tag>arm</can-collide-with-tag>", "<can-collide-with-tag>skirt</can-collide-with-tag>")
    assert 'name="BaseShape"' not in _kept(xml, MESH)


def test_no_injected_body_or_one_already_named():
    assert 'name="BaseShape"' not in _kept(XML, MESH - {"BaseShape"})
    xml = XML.replace('<per-vertex-shape name="top">',
                      '<per-triangle-shape name="BaseShape">\n\t\t<tag>x</tag>\n\t</per-triangle-shape>\n'
                      '\t<per-vertex-shape name="top">')
    assert _kept(xml, MESH).count('name="BaseShape"') == 1


def test_off_switch(monkeypatch):
    monkeypatch.setattr(ph, "XML_BODY_REPOINT", False)
    assert 'name="BaseShape"' not in _kept(XML, MESH)


def test_fsmp_tag_rule():
    a = ("triangle", "3BA", {"body"}, {"arm"}, {"body", "ground"})
    top = ("vertex", "top", {"arm"}, {"body", "ground"}, {"arm"})
    ground = ("triangle", "VirtualGround", {"ground"}, set(), {"ground", "body"})
    assert ph._fsmp_collide(a, top) and not ph._fsmp_collide(a, ground)


def test_the_body_bones_the_author_did_not_declare_are_declared():
    # the UBE body carries bones the author's body did not (the rear calves): FSMP must resolve every vertex of the
    # re-pointed collider, so they are declared like the author's own body bones (2026-10-22 run)
    from types import SimpleNamespace
    lines = ['<system>', '\t<bone name="NPC L Calf [LClf]"/>', '\t<per-triangle-shape name="BaseShape">',
             '\t</per-triangle-shape>', '</system>']
    body = SimpleNamespace(name="BaseShape", bone_weights={"NPC L Calf [LClf]": [(0, 1.0)],
                                                           "NPC L RearCalf [LrClf]": [(1, 0.5)],
                                                           "Unweighted": [(2, 0.0)]})
    out = ph._xml_declare_body_bones(lines, SimpleNamespace(shapes=[body]),
                                     {"npc l calf [lclf]", "npc l rearcalf [lrclf]", "unweighted"})
    assert out[1] == '\t<bone name="NPC L RearCalf [LrClf]"/>' and out[2] == lines[1]
    assert sum("<bone " in x for x in out) == 2, "declared once, unweighted bones left out"
    assert ph._xml_declare_body_bones(out, SimpleNamespace(shapes=[body]), {"npc l rearcalf [lrclf]"}) == out
