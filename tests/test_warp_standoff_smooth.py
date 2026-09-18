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

"""Guards for the warp Pass-2 min-standoff FEATHERING (#warp-smooth).

Pass 2 pushes every vert below `min_standoff` up to the floor on a HARD
threshold, so the boundary of the pushed region is a cliff -- and an unsmoothed
per-vert push IS a crinkle (the systemic spike cause). Feathering the push
scalar over mesh adjacency turns the cliff into a gradient.

The invariant that makes it safe to ship: feathering may only ever ADD push.
It must never leave a vert closer to the body than the unsmoothed pass would --
measured on a real thin belt strap, naive re-flooring still regressed min standoff
0.0206 -> -0.0092 (outside -> INSIDE the body), because push spread onto a
neighbour is applied along THAT vert's own normal, which near a thin strap can
aim into another part of the surface. Hence the explicit geometric clamp.
"""
import numpy as np

from src import nif_convert as nc
from src.nif_convert import warp_armor_by_body_delta


def _flat_body(n=41, span=20.0):
    """A flat body slab in the XY plane at z=0 with +Z normals, zero warp delta,
    so the ONLY thing that moves a vert is the Pass-2 standoff push."""
    g = np.linspace(-span, span, n)
    xx, yy = np.meshgrid(g, g)
    bv = np.stack([xx.ravel(), yy.ravel(), np.zeros(xx.size)], axis=1)
    bn = np.tile(np.array([0.0, 0.0, 1.0]), (len(bv), 1))
    return bv.astype(np.float64), bn.astype(np.float64), np.zeros_like(bv)


def _grid_sheet(n=11, span=10.0, z_lo=0.02, z_hi=2.0):
    """An armor sheet over the slab: HALF of it sits under the standoff floor
    (so it gets pushed) and half well above it (so it does not) -- i.e. exactly
    the hard-threshold boundary the feathering is meant to soften."""
    g = np.linspace(-span, span, n)
    xx, yy = np.meshgrid(g, g)
    z = np.where(xx.ravel() < 0, z_lo, z_hi)
    verts = np.stack([xx.ravel(), yy.ravel(), z], axis=1).astype(np.float64)
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b = i * n + j, i * n + j + 1
            c, d = (i + 1) * n + j, (i + 1) * n + j + 1
            tris += [[a, b, c], [b, d, c]]
    return verts, np.asarray(tris, dtype=np.int64)


def _edge_gradient(v0, v1, tris):
    """Max jump in displacement magnitude across any edge = the crinkle metric."""
    d = np.linalg.norm(np.asarray(v1) - np.asarray(v0), axis=1)
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    return float(np.abs(d[e[:, 0]] - d[e[:, 1]]).max())


def _warp(verts, tris, bv, bn, delta, standoff=0.5):
    return np.asarray(warp_armor_by_body_delta(
        verts, bv, delta, ube_body_verts=bv, ube_body_normals=bn,
        min_standoff=standoff, tris=tris), dtype=np.float64)


def test_no_tris_is_byte_identical_to_unsmoothed():
    """Call sites that pass no `tris` must keep the exact original behaviour --
    the feathering is opt-in by data, so it can never surprise a caller."""
    bv, bn, delta = _flat_body()
    v, tris = _grid_sheet()
    a = _warp(v, None, bv, bn, delta)
    b = _warp(v, None, bv, bn, delta)
    assert np.array_equal(a, b)
    # and it genuinely differs from the smoothed result, else the test is vacuous
    assert not np.allclose(a, _warp(v, tris, bv, bn, delta), atol=1e-9)


def test_smoothing_reduces_the_push_boundary_cliff():
    bv, bn, delta = _flat_body()
    v, tris = _grid_sheet()
    hard = _edge_gradient(v, _warp(v, None, bv, bn, delta), tris)
    soft = _edge_gradient(v, _warp(v, tris, bv, bn, delta), tris)
    assert soft < hard, (hard, soft)


def test_standoff_floor_is_never_violated():
    """The whole point of Pass 2: nothing ends up below the floor. Feathering
    must not buy smoothness by giving up clearance."""
    bv, bn, delta = _flat_body()
    v, tris = _grid_sheet()
    out = _warp(v, tris, bv, bn, delta, standoff=0.5)
    assert out[:, 2].min() >= 0.5 - 1e-6, out[:, 2].min()


def test_smoothing_never_worsens_clearance_vs_unsmoothed():
    """The clamp invariant, stated directly: per-vert, smoothed clearance >=
    unsmoothed clearance. This is what a thin belt strap violated before the
    geometric clamp existed."""
    bv, bn, delta = _flat_body()
    v, tris = _grid_sheet()
    hard = _warp(v, None, bv, bn, delta)
    soft = _warp(v, tris, bv, bn, delta)
    # flat body with +Z normals -> signed clearance is just z
    assert (soft[:, 2] >= hard[:, 2] - 1e-6).all(), (
        float((hard[:, 2] - soft[:, 2]).max()))


def test_kill_switch_restores_hard_threshold(monkeypatch):
    bv, bn, delta = _flat_body()
    v, tris = _grid_sheet()
    monkeypatch.setattr(nc, "WARP_STANDOFF_SMOOTH_ENABLED", False)
    assert np.array_equal(_warp(v, tris, bv, bn, delta),
                          _warp(v, None, bv, bn, delta))
