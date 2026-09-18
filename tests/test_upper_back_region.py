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

"""The rear of the torso must be covered by a region, with NO GAP.

THE BLIND SPOT THIS PINS. `lower_back` stopped at z95 and `upper_chest` is the
z99-112 band FRONT-facing, so the REAR above z95 belonged to no region at all --
the shoulder blades were judged by nothing. A user-reported defect sat exactly
there and every offline number came back clean.

The first `upper_back` mirrored upper_chest at z99-112, which was tidier and
WRONG: it left rear z95-99 still unjudged and measured 4 covered verts where the
contiguous version measures 60. The entire signal was in that 4u strip. So the
contiguity assertion below is the point of this file -- symmetry with the front
band is not.
"""
import numpy as np

from scripts.analysis import pose_set as ps


def _sel(name):
    return dict(ps.REGIONS)[name]


def _band_z(name, ny):
    """z values (0..130) the region accepts at the given normal-y."""
    z = np.linspace(0.0, 130.0, 1301)
    return z[_sel(name)(z, np.full_like(z, ny))]


def test_upper_back_exists_and_is_rear_facing():
    names = [n for n, _s in ps.REGIONS]
    assert "upper_back" in names, "the rear torso above z95 is judged by nothing"
    rear = _band_z("upper_back", -1.0)
    front = _band_z("upper_back", +1.0)
    assert len(rear) > 0, "upper_back selects nothing rear-facing"
    assert len(front) == 0, "upper_back must not select FRONT-facing skin"


def test_no_gap_between_lower_back_and_upper_back():
    """The regression: a 4u strip of rear torso in neither region. Contiguity
    beats symmetry with the front band -- an unjudged strip BETWEEN two regions
    is exactly where a defect hides."""
    lower = _band_z("lower_back", -1.0)
    upper = _band_z("upper_back", -1.0)
    assert len(lower) and len(upper)
    gap_lo, gap_hi = lower.max(), upper.min()
    assert gap_hi <= gap_lo + 0.2, (
        f"rear torso is unjudged between z{gap_lo:.1f} and z{gap_hi:.1f} -- "
        f"that strip is where the shoulder-blade defect lived")


def test_upper_back_reaches_the_shoulder_blades():
    upper = _band_z("upper_back", -1.0)
    assert upper.max() >= 110.0, (
        f"upper_back stops at z{upper.max():.1f}; the shoulder blades sit above "
        f"that and would be judged at bind only")


def test_upper_back_is_driven_by_arm_and_spine_poses_not_legs():
    """Measured: every SPINE pose scored 0.00% on the piece this was derived
    from and every ARM pose scored 16-31%. A leg-only pose list would make the
    region unable to move and it would read clean forever."""
    poses = ps.REGION_POSES.get("upper_back")
    assert poses, "upper_back has no poses -> judged at bind only"
    leg_only = {"stride", "deep stride", "knee bend", "legs together"}
    assert not set(poses) <= leg_only
    assert any("arm" in p or "bow" in p for p in poses), (
        "upper-back exposure measured as ARM-driven; the region needs arm poses")
    for p in poses:
        assert p in ps.POSE_SET, f"unknown pose {p!r}"
