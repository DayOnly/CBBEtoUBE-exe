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

"""The pose set must actually exercise the regions it claims to.

A pose set is the kind of thing that rots silently: someone adds a region, or renames
a bone, and a region quietly ends up judged at BIND only again -- which is the exact
blind spot the set exists to close. These pin the wiring, not the physics."""
import numpy as np
import pytest

from scripts import pose_set as ps


def test_every_region_has_poses_that_can_move_it():
    for name, _sel in ps.REGIONS:
        assert name in ps.REGION_POSES, f"{name} has no poses -> judged at bind only"
        assert ps.REGION_POSES[name], f"{name} has an EMPTY pose list"


def test_region_poses_reference_real_poses():
    for region, poses in ps.REGION_POSES.items():
        for p in poses:
            assert p in ps.POSE_SET, f"{region} references unknown pose {p!r}"


def test_chest_regions_are_driven_by_torso_and_arms_not_legs():
    """THE regression this set was built for. `posed_clip_test` posed thighs and
    calves only, so the chest could not move and was judged at bind. If the chest
    ever ends up keyed to leg-only poses again, that blind spot is back."""
    leg_only = {"stride", "deep stride", "knee bend", "legs together"}
    for region in ("breast", "upper_chest"):
        poses = set(ps.REGION_POSES[region])
        assert poses - leg_only, f"{region} is driven by leg poses only"
        bones = {b for p in poses for (b, _a, _d) in ps.POSE_SET[p]}
        assert any("Spine" in b or "Arm" in b or "Clavicle" in b for b in bones), \
            f"{region} poses move no torso or arm bone"


def test_bind_is_the_identity_pose():
    assert ps.POSE_SET["bind"] == [], "the reference pose must be an identity"


def test_poses_are_ordered_root_to_leaf():
    """A knee bend must compose ON TOP of a hip swing, so the parent has to be
    listed first. Reversed order silently changes the pose."""
    depth = {"Spine": 0, "Clavicle": 1, "UpperArm": 2, "Forearm": 3,
             "Thigh": 1, "Calf": 2}

    def rank(bone):
        for k, v in depth.items():
            if k in bone:
                return v
        return 99
    for name, specs in ps.POSE_SET.items():
        side = {}
        for bone, _axis, _deg in specs:
            s = "L" if " L " in bone else ("R" if " R " in bone else "C")
            r = rank(bone)
            assert r >= side.get(s, -1), f"{name}: {bone} listed after a deeper bone"
            side[s] = r


def test_angles_stay_inside_a_plausible_animation_envelope():
    """A test that only fails at a contortion tells you nothing about play."""
    for name, specs in ps.POSE_SET.items():
        for bone, _axis, deg in specs:
            assert abs(deg) <= 70.0, f"{name}: {bone} at {deg} deg is not a pose"


def test_exclusion_constants_are_sane():
    assert 0 < ps.MID_X < ps.ARM_X, "midline exclusion must sit inside the arm cutoff"
