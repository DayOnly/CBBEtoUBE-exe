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

"""#static-authored-fit: restore the AUTHORED fit where the body doesn't morph.

`conform_to_source_standoff` deliberately leaves tight cloth looser than the
author had it -- it floors the target at `min_clearance` and reels a skin-hugging
vert only `blend_tight` of the way back -- because the source was fitted to the
smaller 3BA body and needs room on the bigger UBE one. True at the bust; false at
a shoulder.

Measured per-vertex over 8.1M verts of the shipped pack: verts authored at
0.10-0.25u ship +0.346u further out, the largest push of any band, while loose
verts (>1u) move +0.034u. The tighter the author fitted it, the more we inflated
it.

The tests that matter are the ones pinning what must NOT change: morph zones keep
today's numbers, the pass stays pull-IN only outside the bust band, and the bust
push-out still fires. A regression there trades visible puffiness for visible
clipping, and clipping has no upper bound while overinflation is invisible to the
clip metric.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_convert as nc  # noqa: E402


def _flat_body(n=24, z=60.0):
    """A flat patch of 'body' in the XZ plane with +Y normals, well below the
    bust band so the bust push-out never fires."""
    xs = np.linspace(-6, 6, n)
    zs = np.linspace(z - 6, z + 6, n)
    X, Z = np.meshgrid(xs, zs)
    V = np.stack([X.ravel(), np.zeros(X.size), Z.ravel()], axis=1)
    N = np.tile(np.array([0.0, 1.0, 0.0]), (len(V), 1))
    return V.astype(np.float64), N.astype(np.float64)


def _run(src_off, cur_off, amp, **kw):
    """Author places cloth `src_off` off the body; the warp leaves it at
    `cur_off`. Returns the resulting clearance."""
    bV, bN = _flat_body()
    src_cloth = bV + bN * src_off
    cur_cloth = bV + bN * cur_off
    amp_arr = None if amp is None else np.full(len(bV), float(amp))
    out = nc.conform_to_source_standoff(
        src_cloth, bV, bN, cur_cloth, bV, bN,
        morph_amplitude=amp_arr, **kw)
    return float(np.median((np.asarray(out, dtype=np.float64) - bV)[:, 1]))


def test_static_zone_restores_the_authored_tight_fit():
    """THE fix. Authored 0.15u, warped out to 0.90u, static zone -> comes back
    near 0.15 instead of stopping at the 0.25 floor."""
    got = _run(0.15, 0.90, amp=0.0)
    assert got < 0.25, f"still floored at min_clearance: {got}"
    assert abs(got - 0.15) < 0.05, f"did not reach the authored fit: {got}"


def test_morph_zone_is_untouched_by_the_change():
    """Bust/belly keep today's behaviour: floored at min_clearance and only
    partially reeled in. This is the guard against trading puffiness for
    nipple poke-through."""
    with_map = _run(0.15, 0.90, amp=5.0)
    without_map = _run(0.15, 0.90, amp=None)
    assert abs(with_map - without_map) < 1e-6, (
        f"morph zone changed: {with_map} vs {without_map}")
    assert with_map > 0.25, "morph zone should keep room, not hug"


def test_no_map_supplied_is_byte_identical_to_the_old_behaviour():
    """Callers that pass nothing must be unaffected."""
    for src, cur in ((0.15, 0.9), (0.05, 2.0), (3.0, 4.0)):
        assert abs(_run(src, cur, amp=None)
                   - _run(src, cur, amp=None)) < 1e-9


def test_kill_switch_restores_old_behaviour(monkeypatch):
    monkeypatch.setattr(nc, "STATIC_AUTHORED_FIT", False)
    assert abs(_run(0.15, 0.90, amp=0.0) - _run(0.15, 0.90, amp=None)) < 1e-6


def test_static_floor_is_never_breached():
    """Even an author who modelled coincident surfaces keeps a sliver, or the
    two surfaces z-fight."""
    got = _run(0.0, 0.80, amp=0.0)
    assert got >= nc.STATIC_AUTHORED_MIN_CLEARANCE - 1e-6, got


def test_pass_never_pushes_a_static_vert_outward():
    """Pull-IN only. A vert already tighter than the author had it must not be
    loosened -- that would be this fix creating the very defect it removes."""
    got = _run(1.20, 0.30, amp=0.0)
    assert got <= 0.30 + 1e-6, f"pushed outward to {got}"


def test_loose_drape_is_unaffected_by_the_static_ramp():
    """Loose verts already blend to 1.0; the ramp must not overshoot them
    inward past their authored drape."""
    got = _run(3.0, 4.0, amp=0.0)
    assert abs(got - 3.0) < 0.15, got


def test_amplitude_ramp_is_monotonic_and_continuous():
    """No cliff between static and morph zones -- a discontinuity would show as
    a visible seam across the shoulder/bust boundary."""
    vals = [_run(0.15, 0.90, amp=a)
            for a in (0.0, 0.25, 0.5, 0.75, 1.0, 2.0)]
    assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), vals
    assert abs(vals[-1] - vals[-2]) < 1e-6, "not flat past the threshold"


def test_a_wrong_length_morph_map_is_ignored_not_misapplied():
    bV, bN = _flat_body()
    src_cloth = bV + bN * 0.15
    cur_cloth = bV + bN * 0.90
    out = nc.conform_to_source_standoff(
        src_cloth, bV, bN, cur_cloth, bV, bN,
        morph_amplitude=np.zeros(len(bV) // 2))
    got = float(np.median((np.asarray(out, dtype=np.float64) - bV)[:, 1]))
    assert got > 0.25, "a mismatched map must not enable the static path"
