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

"""UBE nude-body collider generator (#cloth-clip).

The UBE body ships with no HDT-SMP physics, so cloth that `can-collide-with
body` has nothing to collide against -> it clips when moving. `generate_body_
collision_xml` emits a minimal body collider (one per-triangle-shape on the body
mesh, tag `body`), modeled on CBBE 3BA's `3BBB` shape.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.hdt_xml_gen import generate_body_collision_xml  # noqa: E402

BONES = ["NPC Spine [Spn0]", "NPC Pelvis [Pelv]",
         "NPC L Thigh [LThg]", "NPC R Calf [RClf]"]


def test_parses_and_single_body_shape():
    root = ET.fromstring(generate_body_collision_xml("BaseShape", BONES))
    shapes = root.findall("per-triangle-shape")
    assert len(shapes) == 1
    assert shapes[0].get("name") == "BaseShape"
    assert shapes[0].find("tag").text == "body"


def test_bones_declared_kinematic():
    # Every bone declared, and as a self-closing kinematic <bone/> (no <mass>)
    root = ET.fromstring(generate_body_collision_xml("BaseShape", BONES))
    decl = root.findall("bone")
    assert [b.get("name") for b in decl] == BONES
    for b in decl:
        assert b.find("mass") is None  # kinematic — driven by the actor skeleton


def test_body_shape_has_no_can_collide_list():
    # Matches 3BA's 3BBB: the body carries tag `body` only; the cloth's
    # `can-collide-with body` is what triggers collision (works with ANY cloth).
    root = ET.fromstring(generate_body_collision_xml("BaseShape", BONES))
    shape = root.find("per-triangle-shape")
    assert shape.find("can-collide-with-tag") is None
    assert shape.find("no-collide-with-tag").text == "body"  # no body self-collide


def test_custom_shape_name_and_params():
    xml = generate_body_collision_xml("CBBE", ["NPC Spine [Spn0]"],
                                      margin=0.02, prenetration=0.3)
    root = ET.fromstring(xml)
    s = root.find("per-triangle-shape")
    assert s.get("name") == "CBBE"
    assert s.find("margin").text == "0.02"
    assert s.find("prenetration").text == "0.3"
