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

"""Adaptive clearance must never give the BUST less room than the fixed path it
replaced, and must not give the BACK more.

`clear_armor_outside_body` has two ways to pick a clearance target. Without a
morph amplitude it uses a flat standoff plus a bust ramp up to ANTIPOKE_BUST_CLEAR.
With one it uses an adaptive ramp scaled by how far the body grows, clipped to
ADAPTIVE_CLEARANCE_MORPH_MAX. The adaptive branch REPLACED the bust ramp, and its
cap sat under the bust target -- so enabling adaptive clearance quietly gave the
breast LESS clearance than before, on the single zone that morphs most (measured
on the UBE body: breast amplitude 3.48 mean, ramp wants 0.95-1.32u, cap 0.8 clipped
72% of breast verts to 0.73u where the fixed path guaranteed 1.0u). The body then
sits proud of the cuirass at the breast, standing still and moving.

The bust ramp is now applied as a FLOOR (np.maximum), like rear_standoff. The floor
is gated on NIPPLE WEIGHT and a front-facing normal, not on `bust_z` alone: that is
a height range with no front/back test, so flooring by z would re-impose a fixed
standoff on the static back -- exactly what adaptive clearance exists to keep tight.
#bust-clearance-floor"""
import numpy as np
import pytest

from src import nif_convert as nc


def _body():
    """Two facing walls at chest height: a FRONT surface (normals +Y, nipple
    weight 1.0) and a BACK surface (normals -Y, nipple weight 0)."""
    xs = np.linspace(-2, 2, 5)
    zs = np.linspace(92, 96, 5)
    front = np.array([(x, 0.0, z) for x in xs for z in zs])
    back = np.array([(x, -6.0, z) for x in xs for z in zs])
    bv = np.vstack([front, back])
    bn = np.vstack([np.tile([0.0, 1.0, 0.0], (len(front), 1)),
                    np.tile([0.0, -1.0, 0.0], (len(back), 1))])
    nipple = np.concatenate([np.ones(len(front)), np.zeros(len(back))])
    return bv, bn, nipple, len(front)


def _run(armor, *, amp_value):
    bv, bn, nipple, _ = _body()
    amp = np.full(len(bv), float(amp_value))
    out = nc.clear_armor_outside_body(
        np.asarray(armor, np.float32), bv, bn,
        body_nipple=nipple, morph_amplitude=amp)
    return np.asarray(out, np.float64)


def test_bust_gets_at_least_the_legacy_target():
    """A front vert sitting 0.30u off the bust is pushed out to the legacy bust
    target. With amp ~0 the adaptive ramp alone would only ask for
    ADAPTIVE_CLEARANCE_BASE (0.25) and leave it where it is."""
    out = _run([(0.0, 0.30, 94.0)], amp_value=0.0)
    assert out[0][1] >= nc.ANTIPOKE_BUST_CLEAR - 1e-3, (
        f"bust vert ended at {out[0][1]:.3f}u, "
        f"below the {nc.ANTIPOKE_BUST_CLEAR}u floor")


def test_back_is_not_floored_by_the_bust_ramp():
    """A vert behind the body at the SAME height has nipple weight 0 and a
    rear-facing normal. It must keep the tight adaptive target, not get pushed to
    flat_clear -- `bust_z` is a height range and would otherwise catch the back."""
    start = -6.0 - 0.30                       # 0.30u clear of the back surface
    out = _run([(0.0, start, 94.0)], amp_value=0.0)
    moved = abs(out[0][1] - start)
    assert moved < 1e-3, (
        f"back vert moved {moved:.3f}u; the bust floor leaked onto the back "
        f"(adaptive base is {nc.ADAPTIVE_CLEARANCE_BASE}u, it was already clear)")


def test_adaptive_can_still_exceed_the_floor():
    """The floor is a minimum, not a clamp: where the body grows a lot the ramp
    still asks for more than ANTIPOKE_BUST_CLEAR."""
    high = (nc.ANTIPOKE_BUST_CLEAR + 0.3 - nc.ADAPTIVE_CLEARANCE_BASE) / \
        nc.ADAPTIVE_CLEARANCE_MORPH_FACTOR
    out = _run([(0.0, 0.30, 94.0)], amp_value=high)
    want = min(nc.ADAPTIVE_CLEARANCE_BASE + nc.ADAPTIVE_CLEARANCE_MORPH_FACTOR * high,
               nc.ADAPTIVE_CLEARANCE_MORPH_MAX)
    assert out[0][1] >= min(want, nc.ANTIPOKE_BUST_CLEAR) - 1e-3
    assert out[0][1] > nc.ANTIPOKE_BUST_CLEAR - 1e-3


def test_cap_clears_the_bust_floor():
    """The whole defect was a cap BELOW the floor it replaced. If someone lowers
    the cap under ANTIPOKE_BUST_CLEAR again, the ramp gets clipped under the very
    target it is supposed to be able to exceed."""
    assert nc.ADAPTIVE_CLEARANCE_MORPH_MAX >= nc.ANTIPOKE_BUST_CLEAR


def test_cap_is_env_tunable():
    """A reconvert can retune the cap without a rebuild."""
    import importlib, os
    os.environ["CBBE2UBE_CLEARANCE_MORPH_MAX"] = "1.35"
    try:
        importlib.reload(nc)
        assert nc.ADAPTIVE_CLEARANCE_MORPH_MAX == pytest.approx(1.35)
    finally:
        del os.environ["CBBE2UBE_CLEARANCE_MORPH_MAX"]
        importlib.reload(nc)
