# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026
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

"""#smooth-reach: feathering must cover a DISTANCE, not a ring count.

Both feathering helpers spread a displacement by edge-neighbour averaging, so
`iters` rings carry it `iters * median_edge` units. Authored tessellation varies
~20x within a single piece, so the identical call feathered 2.4u on a leather
panel and 0.11u on a buckle strip -- and the buckle strip is what tore.
"""
import numpy as np
import pytest

from src import nif_convert as nc


def _grid(nx, ny, step):
    """A flat triangulated grid with a known, uniform edge length."""
    xs, ys = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step,
                         indexing="ij")
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(nx * ny)], axis=1)
    t = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a = i * ny + j
            t.append([a, a + 1, a + ny])
            t.append([a + 1, a + ny + 1, a + ny])
    return v, np.asarray(t, dtype=np.int64)


@pytest.fixture
def reach_on(monkeypatch):
    monkeypatch.setattr(nc, "SMOOTH_REACH", True)


def test_off_by_default_changes_nothing(monkeypatch):
    """The negative control. With the flag off the ring count is handed
    through untouched, whatever the tessellation."""
    monkeypatch.setattr(nc, "SMOOTH_REACH", False)
    v, t = _grid(12, 12, 0.05)
    assert nc._reach_iters(v, t, 2) == 2


def test_a_fine_mesh_gets_proportionally_more_rings(reach_on):
    """0.05u spacing against the 1.0u the current ring counts assume. Averaging
    is diffusion, spreading ~sqrt(rounds) * edge, so covering the same DISTANCE
    on a 20x finer mesh takes ~400x the rounds, not 20x."""
    v, t = _grid(12, 12, 0.05)
    assert nc._reach_iters(v, t, 2) > 2 * 100   # (1.0/0.05)**2 = 400


def test_a_coarse_mesh_is_left_exactly_alone(reach_on):
    """Floored at the caller's own value, so this can only ever ADD reach --
    a coarse shape must come out bit-identical to today."""
    v, t = _grid(12, 12, 1.2)
    assert nc._reach_iters(v, t, 2) == 2
    v, t = _grid(12, 12, 4.0)
    assert nc._reach_iters(v, t, 2) == 2


def test_the_scale_is_bounded(reach_on):
    """Cost is linear in rounds while the requirement grows as the SQUARE of
    the tessellation ratio, so this needs a ceiling: the finest strip on the
    reported piece already asks ~320x. Bounded, at the documented bound."""
    v, t = _grid(12, 12, 0.001)
    assert nc._reach_iters(v, t, 2) == 2 * nc._SMOOTH_REACH_MAX


def test_feathering_reaches_the_same_distance_on_both(reach_on):
    """The property itself, end to end rather than on the ring count.

    One vertex is displaced on a coarse grid and on a fine one. Feathering
    should carry it about as far in UNITS on both; with a fixed ring count it
    dies out ~20x sooner on the fine mesh, which is the defect.
    """
    def carried(step, verts):
        """Mass-weighted radius of the feathered displacement, in UNITS.

        Deliberately not "how many verts hold more than x% of the original":
        spreading the same displacement over more rings lowers every individual
        vertex, so a peak-amplitude threshold reports a WIDER feather as a
        narrower one. This measures where the displacement went, not how tall
        it stayed, and is invariant to amplitude.
        """
        v, t = _grid(41, 41, step)
        vec = np.zeros_like(v)
        centre = int(np.argmin(np.linalg.norm(v - v.mean(axis=0), axis=1)))
        vec[centre] = [0.0, 0.0, 1.0]
        out = nc._smooth_vertex_field(vec, t, iters=2,
                                      verts=(v if verts else None))
        w = np.abs(out[:, 2])
        if w.sum() <= 0:
            return 0.0
        d = np.linalg.norm(v - v[centre], axis=1)
        return float((w * d).sum() / w.sum())

    fine, coarse = carried(0.05, True), carried(1.0, True)
    assert fine > 0 and coarse > 0
    assert fine > coarse / 3.0, (
        f"feather carried {fine:.3f}u on the fine mesh vs {coarse:.3f}u on the "
        "coarse one -- the point of the fix is that these are comparable")

    # And the defect it replaces: with the verts withheld, the identical call
    # on the identical geometry reaches an order of magnitude less far.
    unfixed = carried(0.05, False)
    assert unfixed < fine / 5.0, (
        f"ring-counted feathering should reach far less on a fine mesh "
        f"({unfixed:.3f}u) than distance-scaled feathering ({fine:.3f}u)")


def test_a_shape_without_verts_threaded_is_unchanged(reach_on):
    """Not every call site can supply verts. Those must keep today's behaviour
    rather than fail or silently smooth differently."""
    _v, t = _grid(12, 12, 0.05)
    assert nc._reach_iters(None, t, 3) == 3


def test_degenerate_meshes_fall_back(reach_on):
    v, t = _grid(12, 12, 0.05)
    assert nc._reach_iters(v, np.zeros((0, 3), np.int64), 2) == 2
    assert nc._reach_iters(np.zeros((0, 3)), t, 2) == 2
    # every edge length zero -> no median to divide by
    assert nc._reach_iters(np.zeros((len(v), 3)), t, 2) == 2
