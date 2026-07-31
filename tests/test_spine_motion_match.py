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

"""Spine-motion weight match (#spine-follow) -- see clipping log S3.

The garment carries its spine mass on Spine1 where the body uses Spine2. Spine2
sits furthest up the chain and accumulates the most rotation, so the garment
under-travels every spine bend by ~20%: measured under-bust follow 0.79-0.84
across all four spine poses, and completely invisible at bind pose.

Measured under-bust split on a vanilla cuirass (normalised):
    garment   Spine 0.121   Spine1 0.727   Spine2 0.151
    body      Spine 0.098   Spine1 0.492   Spine2 0.410
"""
import importlib
import inspect

import numpy as np

import src.nif_convert as nc


def test_flag_default_on_and_kill_switch(monkeypatch):
    assert nc.MATCH_SPINE_MOTION is True
    monkeypatch.setenv("CBBE2UBE_NO_SPINE_MOTION_MATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MATCH_SPINE_MOTION is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_SPINE_MOTION_MATCH", raising=False)
        importlib.reload(nc)


def test_pass_is_wired_into_both_convert_paths():
    src = inspect.getsource(nc)
    # Count CALL sites only -- the `def` line also contains the name, and taking
    # the raw count once hid that only one of the two convert paths was wired.
    calls = src.count("_match_spine_motion_to_body(dst_path, biped_slots)")
    assert calls >= 2, f"only {calls} call site(s)"


def test_all_three_spine_bones_are_managed_together():
    """The defect is the SPLIT across the chain, not missing mass.

    Managing Spine2 alone would add mass without moving it off Spine1, and the
    measured fix depends on the re-split.
    """
    assert nc._SPINE_MOTION_BONES == ("NPC Spine [Spn0]", "NPC Spine1 [Spn1]",
                                      "NPC Spine2 [Spn2]")


def test_band_covers_the_measured_mismatch():
    """Spine2 disagreement measured at z 80.9-110.7 between the two bodies."""
    assert nc._SPINE_MOTION_Z_LO <= 80.9
    assert nc._SPINE_MOTION_Z_HI >= 110.7


def test_spine_runs_before_arm_in_both_convert_paths():
    """ORDER IS LOAD-BEARING, and nothing else would catch this.

    Every family-scoped match rescales the bones it does not manage, so the
    spine and arm passes contend for the rows where their bands overlap and
    whichever runs LAST wins. Measured on a vanilla cuirass: spine running last
    drags the armhole from 1.106 back to 1.076. The reverse costs nothing,
    because under-bust rows carry no arm-family weight and the `g_mass > 1e-6`
    test skips them.

    Asserted per convert path, not globally, or wiring one path backwards would
    still pass on the other path's ordering.
    """
    src = inspect.getsource(nc)
    # Match the CALL form exactly. A prefix match on the bare name also hits the
    # `def` lines, whose relative order is meaningless -- that false positive is
    # what this assertion first tripped on.
    s_call = "_match_spine_motion_to_body(dst_path, biped_slots)"
    a_call = "_match_arm_motion_to_body(dst_path, biped_slots)"
    spine = [i for i in range(len(src)) if src.startswith(s_call, i)]
    arm = [i for i in range(len(src)) if src.startswith(a_call, i)]
    assert len(spine) >= 2 and len(arm) >= 2
    for s_at, a_at in zip(spine, arm):
        assert s_at < a_at, (
            "spine match must be called BEFORE the arm match in every convert "
            "path -- the later pass wins the overlapping rows")


def test_uses_the_per_row_smp_fallback():
    src = inspect.getsource(nc._match_spine_motion_to_body)
    assert "smp_row_gate=True" in src


def _push_up_target(g_mass, b_mass, strength=1.0):
    return np.maximum(
        np.clip(g_mass + strength * (b_mass - g_mass), 0.0, 1.0), g_mass)


def test_negative_control_no_body_spine_weight_is_a_no_op():
    """THE CONTROL. Where the covered body carries no spine weight, the target
    must collapse to the garment's own share so the row is written back
    unchanged -- otherwise the Z band alone decides what gets rewritten, and a
    sleeve or a free-hanging panel inside the band starts tracking the spine."""
    g = np.array([0.30, 0.82, 0.05])
    t = _push_up_target(g, np.zeros(3))
    assert np.allclose(t, g)


def test_push_up_only_never_lowers_the_spine_share():
    g = np.array([0.95, 0.62, 0.10])
    b = np.array([0.60, 0.97, 0.44])
    t = _push_up_target(g, b)
    assert t[0] == 0.95
    assert t[1] > 0.62
    assert np.all(t >= g)
