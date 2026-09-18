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

"""A cloth that names no body-ish collide tag cannot collide with the body.

HDT-SMP collision is MUTUAL -- either side naming the other's tag is enough --
and the validator only ever checked the body side. So a cloth declaring nothing
passed silently while being unable to collide with the body at all, whatever the
collider said. Measured over a converted pack: 28 of 231 cloth XMLs.

The warning must also say whether the piece is CONSTRAINED, because that decides
whether it can be fixed: adding `body` to unconstrained cloth rebuilds the
per-vertex + per-triangle + no-generic-constraint pattern that crashes on equip.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import hdt_xml_gen as hx  # noqa: E402

CONSTRAINT = ('<generic-constraint bodyA="a" bodyB="b"/>')


def _xml(tmp_path, cloth_tags, constrained=True, name="skirt.xml"):
    tags = "".join(f"<can-collide-with-tag>{t}</can-collide-with-tag>"
                   for t in cloth_tags)
    p = tmp_path / name
    p.write_text(
        '<system>'
        '<bone name="NPC Pelvis [Pelv]"/>'
        '<per-triangle-shape name="VirtualBody">'
        '<tag>body</tag><can-collide-with-tag>cloth1</can-collide-with-tag>'
        '</per-triangle-shape>'
        f'<per-vertex-shape name="Skirt"><tag>cloth1</tag>{tags}'
        '</per-vertex-shape>'
        f'{CONSTRAINT if constrained else ""}'
        '</system>', encoding="utf-8")
    return p


def _hits(warnings):
    return [w for w in warnings if "no body-ish can-collide-with-tag" in w]


def test_cloth_naming_body_is_clean(tmp_path):
    w = hx.validate_armor_hdt_xml(_xml(tmp_path, ["body"]),
                                  ["NPC Pelvis [Pelv]"])
    assert not _hits(w), w


def test_cloth_naming_nothing_is_flagged(tmp_path):
    w = hx.validate_armor_hdt_xml(_xml(tmp_path, []), ["NPC Pelvis [Pelv]"])
    assert len(_hits(w)) == 1, w


def test_cloth_naming_only_a_non_body_tag_is_flagged(tmp_path):
    """"It declares SOMETHING" is not the test -- it has to be the body."""
    w = hx.validate_armor_hdt_xml(_xml(tmp_path, ["ground", "hair"]),
                                  ["NPC Pelvis [Pelv]"])
    assert len(_hits(w)) == 1, w


def test_body_variants_are_accepted(tmp_path):
    for tag in ("body", "Body", "body2", "ColBody"):
        w = hx.validate_armor_hdt_xml(_xml(tmp_path, [tag], name=f"{tag}.xml"),
                                      ["NPC Pelvis [Pelv]"])
        assert not _hits(w), (tag, w)


def test_the_warning_says_a_constrained_piece_is_fixable(tmp_path):
    w = _hits(hx.validate_armor_hdt_xml(_xml(tmp_path, [], constrained=True),
                                        ["NPC Pelvis [Pelv]"]))
    assert w and "can be added safely" in w[0]


def test_the_warning_refuses_an_unconstrained_piece(tmp_path):
    """The load-bearing half. On unconstrained cloth the 'fix' is the documented
    equip-CTD pattern, so the warning must say so rather than invite it."""
    w = _hits(hx.validate_armor_hdt_xml(_xml(tmp_path, [], constrained=False),
                                        ["NPC Pelvis [Pelv]"]))
    assert w and "equip CTD" in w[0] and "NO constraints" in w[0]


def test_our_own_generated_xml_passes(tmp_path):
    """Regression: the generator already emits body collision correctly, so the
    new check must not fire on our own output."""
    p = tmp_path / "gen.xml"
    xml = hx.generate_armor_hdt_xml(
        [("Skirt", ["NPC Pelvis [Pelv]"])],
        body_collision_shape_name="VirtualBody",
    )
    p.write_text(xml, encoding="utf-8")
    assert not _hits(hx.validate_armor_hdt_xml(p, ["NPC Pelvis [Pelv]"]))
