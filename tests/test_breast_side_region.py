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

"""The FLANK of the torso must be judged, and judged with the right arm cut.

THE BLIND SPOT THIS PINS. Every torso region gated on `ny` -- front-facing or
rear-facing -- so a vertex whose normal points SIDEWAYS was in no region at all.
Measured on the UBE body over z90-103: 1275 lateral-facing verts and 1985 verts
in no existing region. A user reported the bust coming out the SIDE of a cuirass
under a weapon swing while every region read clean, which is the same failure the
`upper_back` strip had.

THE SECOND HALF IS THE ARM CUT, and it is not a detail. In the bind A-pose the
upper arm hangs beside the chest at |x| 12-20 -- INSIDE `ARM_X` -- carrying arm
weight 1.000, while the flank at |x| 8-12 carries 0.220. The front/rear regions
get away with the |x| cut because bare arm skin is uncovered at bind and the
covered-at-bind baseline drops it. A lateral region does not: the arm is exactly
what sits lateral to the bust. So it excludes by SKIN WEIGHT.
"""
import numpy as np
import pytest

from scripts.analysis import pose_set as ps


def _sel(name):
    return dict(ps.REGIONS)[name]


def test_a_lateral_region_exists():
    names = [n for n, _s in ps.REGIONS]
    assert "breast_side" in names, "the flank of the bust is judged by nothing"


def test_breast_side_selects_sideways_and_not_front_or_rear():
    z = np.full(200, 96.0)
    lateral = _sel("breast_side")(z, np.zeros(200), np.ones(200))
    front = _sel("breast_side")(z, np.ones(200), np.zeros(200))
    rear = _sel("breast_side")(z, -np.ones(200), np.zeros(200))
    assert lateral.all(), "breast_side selects nothing lateral-facing"
    assert not front.any(), "breast_side must not select FRONT-facing skin"
    assert not rear.any(), "breast_side must not select REAR-facing skin"


def test_breast_side_covers_the_breast_band_height():
    """It has to reach the bust, or it is measuring the ribs."""
    z = np.linspace(60.0, 130.0, 701)
    band = z[_sel("breast_side")(z, np.zeros_like(z), np.ones_like(z))]
    assert band.min() <= 90.0 and band.max() >= 102.0, (
        f"breast_side spans z{band.min():.1f}-{band.max():.1f}; the breast band "
        f"is z90-102 and must be inside it")


def test_breast_side_refuses_a_two_argument_call():
    """A lateral selector given only (z, ny) would gate on the WRONG component
    and hand back front-facing skin under a lateral region's name. It must
    refuse -- silently returning something is how a harness reports a confident
    zero."""
    z = np.full(10, 96.0)
    with pytest.raises(ValueError):
        _sel("breast_side")(z, np.zeros(10))


def test_lateral_regions_are_arm_excluded_by_weight_not_by_x():
    """`ARM_X` keeps 1378 pure-arm verts at breast height. If a lateral region
    ever falls back to it, the measurement becomes a measurement of the arm."""
    assert "breast_side" in ps.WEIGHT_ARM_EXCLUDED
    assert 0.0 < ps.ARM_WEIGHT_MAX < 1.0
    assert any("UpperArm" in k or "Upperarm" in k for k in ps.ARM_BONE_KEYS)


def test_breast_side_is_driven_by_twist_and_swing_not_forward_lean():
    """A flank opens under TWIST and an asymmetric arm sweep. A pose list of
    forward leans could not move it and it would read clean forever."""
    poses = ps.REGION_POSES.get("breast_side")
    assert poses, "breast_side has no poses -> judged at bind only"
    for p in poses:
        assert p in ps.POSE_SET, f"unknown pose {p!r}"
    assert any("swing" in p for p in poses), (
        "the defect was reported under a weapon swing; the region needs one")
    assert any("twist" in p or "side bend" in p for p in poses)


def test_a_swing_is_asymmetric_and_twists_the_spine():
    """THE REASON THE EXISTING SET COULD NOT REPRODUCE IT. `arms forward` and
    `arms crossed` move both arms identically with no torso rotation, so neither
    flank ever leads. A swing must do both, or it is just another arm pose."""
    for name in ("swing windup", "swing strike"):
        specs = ps.POSE_SET[name]
        bones = [b for b, _a, _d in specs]
        assert any("Spine" in b and a == 'z' for b, a, _d in specs), \
            f"{name} does not twist the spine"
        left = [b for b in bones if " L " in b]
        right = [b for b in bones if " R " in b]
        assert right and left != right, f"{name} is not asymmetric"
