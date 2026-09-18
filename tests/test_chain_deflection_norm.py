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

"""#chain-deflection-norm — a SHORT chain must not be silently 29x stiffer.

The 2026-06-05 tuning copied the authored PER-JOINT constraint values and
missed the authored JOINT COUNT. Measured over the 2026-08-21 pack:

    ours (149 generated)   joints per chain: min 1  MEDIAN 2   max 8
    authored (208)         joints per chain: min 2  MEDIAN 58  max 720
    both at the same per-joint angular limit, median 0.1 rad

Visible sway is roughly `joints x limit`, so a 2-joint cloak deflected ~11
degrees in total while authored cloth flows over ~58 joints. Reported in game
as "one segment where it bends but no physics (or the physics are so slim its
not noticable)".

The limit is now a function of the chain's own length. THE FLOOR IS THE SAFETY
PROPERTY and most of this file exists to pin it: any chain of 8+ joints keeps
exactly the previous value, so the change cannot reach a long authored-style
chain at all.
"""
import re
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import hdt_xml_gen as hx  # noqa: E402

LIMIT = hx.chain_angular_limit


# ---- the rule ------------------------------------------------------------

def test_a_long_chain_keeps_exactly_the_previous_value():
    """THE SAFETY PROPERTY. 0.1 was the shipped per-joint limit; every chain
    long enough to have worked before must be untouched."""
    for joints in (8, 9, 12, 20, 58, 200, 720):
        assert LIMIT(joints) == hx.CHAIN_ANGULAR_LIMIT_MIN, joints


def test_a_short_chain_gets_more_travel_per_joint():
    assert LIMIT(2) > LIMIT(8)
    assert LIMIT(3) > LIMIT(8)
    assert LIMIT(2) == pytest.approx(0.4)


def test_the_reported_cloak_goes_from_barely_moving_to_visible():
    """The burlap travel cloak: 3 chain bones = 2 joints."""
    before = 2 * hx.CHAIN_ANGULAR_LIMIT_MIN          # the shipped behaviour
    after = 2 * LIMIT(2)
    assert before == pytest.approx(0.2)              # ~11 degrees, the report
    assert after == pytest.approx(0.8)               # ~46 degrees
    assert after / before == pytest.approx(4.0)


def test_total_deflection_is_normalised_across_short_lengths():
    """The point of the rule: 2..8 joints all reach the same TOTAL, which is
    what the eye sees, rather than the same per-joint value."""
    for joints in range(2, 9):
        assert joints * LIMIT(joints) == pytest.approx(
            hx.CHAIN_TARGET_DEFLECTION, rel=1e-3), joints


def test_the_per_joint_ceiling_holds_for_a_single_joint():
    """A 1-joint chain would want the whole budget in one hinge. Capped, or a
    single link could fold back through itself."""
    assert LIMIT(1) == hx.CHAIN_ANGULAR_LIMIT_MAX
    assert LIMIT(1) < hx.CHAIN_TARGET_DEFLECTION


def test_degenerate_joint_counts_do_not_explode():
    """A chain with no constraints must not divide by zero or return inf."""
    for joints in (0, -1):
        assert LIMIT(joints) == hx.CHAIN_ANGULAR_LIMIT_MIN


def test_the_limit_never_leaves_its_clamp():
    for joints in range(0, 200):
        v = LIMIT(joints)
        assert hx.CHAIN_ANGULAR_LIMIT_MIN <= v <= hx.CHAIN_ANGULAR_LIMIT_MAX


def test_the_limit_is_monotone_in_chain_length():
    """More joints must never mean MORE travel each -- that would be the bug
    inverted."""
    vals = [LIMIT(j) for j in range(1, 30)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


# ---- the emitted XML -----------------------------------------------------

def _chain(prefix, n):
    return hx.PhysicsChain(prefix=prefix,
                           bones=[f"{prefix}{i:02d}" for i in range(n)])


def _angular_uppers(xml):
    return [float(m) for m in
            re.findall(r'<angularUpperLimit x="([-\d.]+)"', xml)]


def test_the_emitted_xml_carries_the_scaled_limit():
    xml = hx.generate_armor_hdt_xml(
        [("Cloak", ["NPC Spine1 [Spn1]"])],
        body_collision_shape_name=None,
        chains=[_chain("SkirtBBone", 3)],          # 3 bones -> 2 joints
    )
    ups = _angular_uppers(xml)
    assert ups, f"no angular limit emitted at all:\n{xml}"
    assert ups[0] == pytest.approx(LIMIT(2))
    assert '<generic-constraint bodyA="SkirtBBone01" bodyB="SkirtBBone00"/>' in xml


def test_each_chain_gets_its_own_default_block():
    """A NIF can carry chains of different lengths, and one global default
    would give the short one the long one's limit -- which is the bug."""
    xml = hx.generate_armor_hdt_xml(
        [("Cloak", ["NPC Spine1 [Spn1]"])],
        body_collision_shape_name=None,
        chains=[_chain("Short", 3), _chain("Long", 12)],
    )
    assert xml.count("<generic-constraint-default>") == 2, xml
    ups = _angular_uppers(xml)
    assert len(ups) == 2
    assert sorted(ups) == [pytest.approx(LIMIT(11)), pytest.approx(LIMIT(2))]


def test_a_long_chain_emits_the_unchanged_value():
    """End-to-end form of the safety property: the bytes a long chain gets are
    the ones it got before."""
    xml = hx.generate_armor_hdt_xml(
        [("Skirt", ["NPC Pelvis [Pelv]"])],
        body_collision_shape_name=None,
        chains=[_chain("SkirtF", 20)],
    )
    assert _angular_uppers(xml) == [pytest.approx(0.1)]
    assert '<angularUpperLimit x="0.1" y="0.1" z="0.1"/>' in xml


def test_the_xml_still_validates_and_still_binds_the_body_tag():
    """The change must not disturb what already worked: the cloth still asks to
    collide with the body tag, which binds to the worn body's own SMP config at
    runtime (see the generator's section-3 comment) -- it is NOT a dangling
    reference to be 'fixed'."""
    xml = hx.generate_armor_hdt_xml(
        [("Cloak", ["NPC Spine1 [Spn1]"])],
        body_collision_shape_name="VirtualBody",
        chains=[_chain("SkirtBBone", 3)],
    )
    assert "<can-collide-with-tag>body</can-collide-with-tag>" in xml
    assert '<per-triangle-shape name="VirtualBody">' in xml
    assert xml.lstrip().startswith("<?xml")


def test_the_chain_anchor_does_not_pin_a_band_of_cloth():
    """#chain-anchor-threshold — the second in-game report on this cloak.

    `<weight-threshold bone="X">v</weight-threshold>` pins any vertex whose
    weight to X reaches v. Bone 0 of a chain is emitted at mass 0 so the rest
    hangs off it, so giving it the low 0.3 CHAIN threshold froze every vertex
    with even 30% weight to a bone that never moves. On a 3-bone cloak chain
    that band sits mid-cloth: "it appears that half way down it it is pinned in
    place". The anchor takes the same 1.0 the other STATIC bones take.
    """
    xml = hx.generate_armor_hdt_xml(
        [("Cloak", ["NPC Spine1 [Spn1]", "SkirtBBone01",
                    "SkirtBBone02", "SkirtBBone03"])],
        body_collision_shape_name=None,
        chains=[hx.PhysicsChain(prefix="SkirtBBone",
                                bones=["SkirtBBone01", "SkirtBBone02",
                                       "SkirtBBone03"])],
    )
    def thresh(bone):
        m = re.search(rf'<weight-threshold bone="{re.escape(bone)}">([\d.]+)<', xml)
        assert m, f"no threshold emitted for {bone}:\n{xml}"
        return float(m.group(1))

    assert thresh("SkirtBBone01") == hx.PRIMARY_BONE_THRESHOLD, (
        "the chain ANCHOR is static; a low threshold there pins cloth to a "
        "bone that never moves")
    assert thresh("SkirtBBone02") == 0.3
    assert thresh("SkirtBBone03") == 0.3
    assert thresh("NPC Spine1 [Spn1]") == hx.PRIMARY_BONE_THRESHOLD, (
        "unrelated static bones must be unaffected by the anchor rule")


def test_only_the_first_bone_of_each_chain_counts_as_an_anchor():
    """Two chains means two anchors -- and only two. A rule that promoted every
    chain bone would freeze the whole garment."""
    xml = hx.generate_armor_hdt_xml(
        [("Cloak", ["A00", "A01", "A02", "B00", "B01", "B02"])],
        body_collision_shape_name=None,
        chains=[hx.PhysicsChain(prefix="A", bones=["A00", "A01", "A02"]),
                hx.PhysicsChain(prefix="B", bones=["B00", "B01", "B02"])],
    )
    got = dict(re.findall(r'<weight-threshold bone="([^"]+)">([\d.]+)<', xml))
    assert got["A00"] == "1.0" and got["B00"] == "1.0"
    assert got["A01"] == "0.3" and got["A02"] == "0.3"
    assert got["B01"] == "0.3" and got["B02"] == "0.3"


def test_the_comment_records_the_arithmetic_for_the_next_reader():
    """The emitted XML is read by a human debugging physics in game; the joint
    count and resulting total are the two numbers they need."""
    xml = hx.generate_armor_hdt_xml(
        [("Cloak", ["NPC Spine1 [Spn1]"])],
        body_collision_shape_name=None,
        chains=[_chain("SkirtBBone", 3)],
    )
    assert re.search(r"2 joint\(s\) -> 0\.4 rad each, ~0\.8 rad across the chain",
                     xml), xml
