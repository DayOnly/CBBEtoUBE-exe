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

"""Arm-motion weight match (#armhole-arm-follow) -- see clipping log S2.

CBBE and UBE rig the shoulder differently: CBBE's UpperArm weight stops at
z 99.7, UBE's runs to z 110.1. A CBBE-authored garment therefore arrives with
ZERO UpperArm weight across the armhole over a body carrying 0.179 there, the
shoulder out-travels it, and the body emerges in motion. Bind-pose clearance
cannot see it, which is why it survived every clearance pass.

The arm and leg instances share one implementation; these tests cover the arm
wiring and the parameterisation, and lean on test_leg_motion_match.py for the
shared maths.
"""
import importlib
import inspect

import numpy as np

import src.nif_convert as nc


def test_flag_default_on_and_kill_switch(monkeypatch):
    assert nc.MATCH_ARM_MOTION is True
    monkeypatch.setenv("CBBE2UBE_NO_ARM_MOTION_MATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MATCH_ARM_MOTION is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_ARM_MOTION_MATCH", raising=False)
        importlib.reload(nc)


def test_pass_is_wired_into_both_convert_paths():
    src = inspect.getsource(nc)
    # One call site per convert path. A pass defined but never called is the
    # failure mode that made the UBE-native backstop dead code for weeks.
    assert src.count("_match_arm_motion_to_body(dst_path") >= 2


def test_flag_is_read_at_call_time_not_captured():
    """Monkeypatching the module global must still disable the pass.

    If the wrapper passed MATCH_ARM_MOTION down as an argument evaluated at
    import, a test (or a caller) flipping the global would be silently ignored
    and the pass would run anyway.
    """
    src = inspect.getsource(nc._match_arm_motion_to_body)
    assert "if not MATCH_ARM_MOTION:" in src
    assert "return 0" in src


def test_clavicle_is_managed_alongside_upperarm():
    """The defect is the SPLIT, not just missing mass.

    The garment carries Clavicle 0.429 where the body carries Clavicle 0.377 +
    UpperArm 0.179. Managing UpperArm alone would add mass without moving the
    share off the wrong bone, and the measured fix depends on the re-split.
    """
    fam = nc._ARM_MOTION_BONES
    assert any("Clavicle" in b for b in fam)
    assert any("UpperArm" in b for b in fam)
    # Both sides, or one shoulder silently keeps the defect.
    assert sum("Clavicle" in b for b in fam) == 2
    assert sum("UpperArm" in b for b in fam) == 2


def test_band_covers_the_measured_deficit():
    """Deficit measured at z 87.7..113.9 on a vanilla cuirass; the band must
    contain it, or the pass selects nothing where the defect actually is."""
    assert nc._ARM_MOTION_Z_LO <= 87.7
    assert nc._ARM_MOTION_Z_HI >= 113.9


def test_hug_distance_is_tighter_than_the_leg_pass():
    """A shoulder sits close. The thing to keep out is a free-hanging pauldron
    or cape, which must not be pulled onto the arm and made to swing -- the
    measured garment-to-body hug in the deficit set is p90 1.45u."""
    assert nc._ARM_MOTION_MAX_DIST < nc._LEG_MOTION_MAX_DIST
    assert nc._ARM_MOTION_MAX_DIST >= 1.45


def test_hands_and_feet_slots_are_skipped():
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "BIPED_SLOT33_BIT | BIPED_SLOT37_BIT" in src


def test_arm_and_leg_share_one_implementation():
    """Guards against the copy-paste alternative: two 200-line passes that drift
    apart, so a fix to the 4-influence cap or the normalise invariant lands in
    one and not the other."""
    arm = inspect.getsource(nc._match_arm_motion_to_body)
    leg = inspect.getsource(nc._match_leg_motion_to_body)
    assert "_match_limb_motion_to_body(" in arm
    assert "_match_limb_motion_to_body(" in leg
    # Wrappers only -- the maths lives in the shared core.
    assert len(arm.splitlines()) < 40
    assert len(leg.splitlines()) < 40


def _push_up_target(g_mass, b_mass, strength=1.0):
    """The pass's target rule, isolated (see the core's `target = ...`)."""
    return np.maximum(
        np.clip(g_mass + strength * (b_mass - g_mass), 0.0, 1.0), g_mass)


def test_push_up_only_never_lowers_the_TARGET():
    """A garment already tracking the arm better than the body must be left
    alone -- lowering it would introduce the very defect we are fixing.

    SCOPE: this exercises the target rule in isolation, NOT the written weight.
    Measured on the shipped pass, the 4-influence cap can still reduce a family
    bone on an overflowing row (118 of 3254 changed verts, worst 0.0303), so
    "never lowers" holds for the target and not end-to-end. Named for what it
    actually checks.
    """
    g = np.array([0.90, 0.43, 0.00])
    b = np.array([0.55, 0.56, 0.20])
    t = _push_up_target(g, b)
    assert t[0] == 0.90                     # garment ahead -> untouched
    assert t[1] > 0.43 and t[1] <= 0.56     # garment behind -> raised
    assert np.all(t >= g)


def test_negative_control_no_body_arm_weight_is_a_no_op():
    """THE CONTROL. Where the covered body carries no arm-family weight, the
    target must collapse to the garment's own share, so the vert is written back
    unchanged. This is what makes the Z band a safety rail rather than the
    selector -- if this ever fails, the band alone decides what gets rewritten
    and a sleeveless dress's bare shoulder strap starts following the arm.
    """
    g = np.array([0.42, 0.10, 0.77])
    b = np.zeros(3)
    t = _push_up_target(g, b)
    assert np.allclose(t, g), (
        "body carries no arm weight but the target moved -- the pass would "
        "rewrite verts it has no evidence about")
