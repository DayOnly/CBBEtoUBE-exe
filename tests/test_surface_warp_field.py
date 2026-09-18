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

"""#warp-field-staircase -- match the body to the nearest point on the target
SURFACE, not to its nearest VERTEX.

The load-bearing test is `test_field_is_smooth_where_nearest_vertex_stairsteps`,
with `test_nearest_vertex_really_does_stairstep` as its negative control: if the
control ever stops failing the way it is asserted to, the smooth case is proving
nothing and the whole comparison is void.
"""
import numpy as np

from src import nif_convert as nc


def _grid(n, spacing, z):
    """A flat sheet of `n` x `n` verts at height `z`, plus its triangles."""
    g = np.arange(n) * spacing
    x, y = np.meshgrid(g, g, indexing="ij")
    v = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, float(z))])
    t = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b = i * n + j, i * n + j + 1
            c, d = (i + 1) * n + j, (i + 1) * n + j + 1
            t += [[a, b, c], [b, d, c]]
    return v, np.asarray(t, dtype=np.int64)


# A fine source sheet against a COARSE target sheet 1.0 above it. Nearest-vertex
# has to quantise onto the coarse spacing; the true surface is flat and 1.0 away
# everywhere, so a correct field is exactly (0, 0, 1) at every point.
SRC, _ = _grid(11, 1.0, 0.0)
TGT_V, TGT_T = _grid(4, 10.0 / 3.0, 1.0)


def _nearest_vertex_delta(pts, tv):
    from scipy.spatial import cKDTree
    _, nn = cKDTree(tv).query(pts, k=1)
    return tv[nn] - pts


def _gradient(delta, pts, tol=1e-9):
    """max |d(a) - d(b)| / |a - b| over the source sheet's grid neighbours."""
    n = int(round(len(pts) ** 0.5))
    idx = np.arange(len(pts)).reshape(n, n)
    worst = 0.0
    for a, b in ((idx[:, :-1].ravel(), idx[:, 1:].ravel()),
                 (idx[:-1, :].ravel(), idx[1:, :].ravel())):
        step = np.linalg.norm(delta[a] - delta[b], axis=1)
        span = np.linalg.norm(pts[a] - pts[b], axis=1)
        worst = max(worst, float((step / np.maximum(span, tol)).max()))
    return worst


def test_nearest_vertex_really_does_stairstep():
    """NEGATIVE CONTROL. The defect must be present in the geometry the fix is
    measured on, or the improvement below is measuring nothing."""
    assert _gradient(_nearest_vertex_delta(SRC, TGT_V), SRC) > 0.9


def test_field_is_smooth_where_nearest_vertex_stairsteps():
    d = nc._closest_point_delta(SRC, TGT_V, TGT_T)
    assert _gradient(d, SRC) < 1e-6


def test_lands_on_the_target_surface():
    """Smoothness is worthless if the field stops reaching the target: every
    warped point must sit ON the sheet, not short of it."""
    d = nc._closest_point_delta(SRC, TGT_V, TGT_T)
    assert np.allclose((SRC + d)[:, 2], 1.0, atol=1e-9)


def test_never_worse_than_the_nearest_vertex_it_falls_back_to():
    """`k` is a speed knob, not a correctness one -- a vertex is itself a point
    on the surface, so the result can never be further than the fallback even
    when the candidate search is starved to a single triangle."""
    fb = _nearest_vertex_delta(SRC, TGT_V)
    d = nc._closest_point_delta(SRC, TGT_V, TGT_T, fallback=fb, k=1)
    assert (np.linalg.norm(d, axis=1)
            <= np.linalg.norm(fb, axis=1) + 1e-9).all()


def test_off_by_default():
    """Ships OFF until an in-game verdict; the flag is the only way in."""
    assert nc.SURFACE_WARP_FIELD is False or (
        __import__("os").environ.get("CBBE2UBE_SURFACE_WARP_FIELD") == "1")


def test_reachable_from_the_gui():
    """Rule 1: an env-only toggle can never be switched on by a user, so it can
    never be validated in game."""
    from src import gui_settings
    assert any(s.env == "CBBE2UBE_SURFACE_WARP_FIELD"
               for s in gui_settings.SETTINGS)
