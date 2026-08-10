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

"""#warp-shear-limit -- cap a triangle's stretch under the body-delta warp.

The load-bearing test here is not the cap, it is
`test_clamped_vertex_still_travels_the_full_local_delta`. Damping the warp is
what once left every armour CBBE-shaped, so the guard's right to exist depends
on it limiting only the DIFFERENTIAL between neighbours and never the local
average motion.
"""
import numpy as np

from src import nif_convert as nc


def _areas(v, t):
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)


# One big triangle plus a neighbour, so the ring average is meaningful.
V = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0],
              [0.0, 10.0, 0.0], [10.0, 10.0, 0.0]])
T = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64)


def test_a_runaway_stretch_is_capped():
    d = np.zeros((4, 3))
    d[1] = [30.0, 0.0, 0.0]          # drag one corner far: huge stretch
    grown = _areas(V + d, T)[0] / _areas(V, T)[0]
    assert grown > 3.0, "test setup does not actually stretch"

    out = nc._limit_triangle_shear(V, d, T, max_growth=2.0)
    capped = _areas(V + out, T)[0] / _areas(V, T)[0]
    assert capped <= 2.0 + 1e-6, f"still {capped:.2f}x"


def test_clamped_vertex_still_travels_the_full_local_delta():
    """The safety property that distinguishes this from 'warp less'.

    A uniform delta is pure translation: no triangle changes area, so the guard
    must pass it through untouched. If this ever fails, the guard is shrinking
    real warp motion and would leave armour CBBE-shaped.
    """
    d = np.tile(np.array([4.0, -2.0, 7.0]), (4, 1))
    out = nc._limit_triangle_shear(V, d, T, max_growth=2.0)
    assert np.allclose(out, d), "uniform warp motion was reduced"


def test_clamping_redistributes_motion_rather_than_deleting_it():
    """Clamping moves displacement BETWEEN neighbours; it must not remove it.

    Note what is NOT claimed: ring-averaging over an irregular graph is
    degree-weighted and so is not exactly mean-preserving (measured here: the
    mean drifts ~4%, and UPWARD). The property that matters for this pass is
    that total motion is not driven toward zero, because that -- not a
    conservation identity -- is what would leave armour CBBE-shaped.
    """
    d = np.zeros((4, 3))
    d[1] = [30.0, 0.0, 0.0]
    out = nc._limit_triangle_shear(V, d, T, max_growth=2.0)
    before, after = np.linalg.norm(d.mean(0)), np.linalg.norm(out.mean(0))
    assert after > 0.8 * before, f"mean motion collapsed {before} -> {after}"
    assert np.linalg.norm(out.sum(0)) > 0.5 * np.linalg.norm(d.sum(0))


def test_moderate_stretch_under_the_cap_is_untouched():
    d = np.zeros((4, 3))
    d[1] = [2.0, 0.0, 0.0]
    before = _areas(V + d, T)[0] / _areas(V, T)[0]
    assert before < 2.0
    out = nc._limit_triangle_shear(V, d, T, max_growth=2.0)
    assert np.allclose(out, d)


def test_degenerate_triangle_is_skipped_not_divided_by():
    """Area ratio against ~0 is meaningless; it must not pin the whole mesh."""
    v = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0],
                  [0.0, 5.0, 0.0]])
    t = np.array([[0, 1, 2], [0, 1, 3]], dtype=np.int64)
    d = np.zeros((4, 3))
    d[3] = [0.0, 3.0, 0.0]
    out = nc._limit_triangle_shear(v, d, t, max_growth=2.0)
    assert np.isfinite(out).all()


def test_unusable_topology_is_a_no_op():
    d = np.ones((4, 3))
    for bad in (np.zeros((0, 3), dtype=np.int64),
                np.array([[0, 1]], dtype=np.int64)):
        assert np.array_equal(nc._limit_triangle_shear(V, d, bad), d)


def test_ships_off_and_is_gui_reachable():
    """PIPELINE rule 8, plus the 2026-08-01 flag audit: an opt-in the user
    cannot switch on is an opt-in nobody can give a verdict on."""
    assert nc.WARP_SHEAR_LIMIT is False
    from src import gui_settings as gs
    s = gs.by_key()["warp_shear_limit"]
    assert s.default is False and s.invert is False
    assert s.env == "CBBE2UBE_WARP_SHEAR_LIMIT"
    assert s.key in dict(gs.LAYOUT["Armor"])["Fit and clearance"]
