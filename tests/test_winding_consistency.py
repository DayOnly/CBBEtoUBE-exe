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

"""#winding-consistency -- culling reads the winding, lighting reads the
normals, and a triangle where they disagree is culled from the side it is lit
for: a flat dark shape.

The safety argument is that this only ever reorders three indices, so the tests
assert exactly that -- no vertex, normal, UV or weight can move, because the
function never returns anything but a triangle array.
"""
import numpy as np

from src import nif_convert as nc


def _sphere(n_lat=10, n_lon=16):
    """Closed sphere: every triangle correctly wound, normals = radial."""
    pts, tris = [], []
    for i in range(n_lat + 1):
        th = np.pi * i / n_lat
        for j in range(n_lon):
            ph = 2 * np.pi * j / n_lon
            pts.append([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph),
                        np.cos(th)])
    for i in range(n_lat):
        for j in range(n_lon):
            a = i * n_lon + j
            b = i * n_lon + (j + 1) % n_lon
            c = (i + 1) * n_lon + j
            d = (i + 1) * n_lon + (j + 1) % n_lon
            tris += [[a, c, b], [b, c, d]]
    v = np.array(pts, dtype=np.float64)
    t = np.array(tris, dtype=np.int64)
    n = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    # normalise the winding so the fixture starts perfectly consistent
    t, _ = nc._repair_winding_consistency(v, t, n)
    return v, t, n


def _disagreeing(v, t, n):
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    cr = np.cross(p1 - p0, p2 - p0)
    avg = n[t].mean(1)
    return int((np.einsum("ij,ij->i", cr, avg) < 0).sum())


def test_a_healthy_mesh_is_untouched():
    v, t, n = _sphere()
    assert _disagreeing(v, t, n) == 0, "fixture is not clean"
    out, flipped = nc._repair_winding_consistency(v, t, n)
    assert flipped == 0
    assert np.array_equal(out, t)


def _non_degenerate(v, t, want):
    """Pick triangles with real area. A sphere's pole rows collapse to slivers,
    and flipping one of those registers as nothing -- which is correct
    behaviour, but makes it a useless fixture."""
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    area = np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
    return np.argsort(-area)[:want]


def test_flipped_triangles_are_repaired_to_zero():
    v, t, n = _sphere()
    t = t.copy()
    picked = _non_degenerate(v, t, 5)
    for i in picked:
        t[i] = t[i][[0, 2, 1]]
    assert _disagreeing(v, t, n) == 5

    out, flipped = nc._repair_winding_consistency(v, t, n)
    assert flipped == 5
    assert _disagreeing(v, out, n) == 0


def test_repair_only_reorders_indices():
    """No vertex moves and no triangle gains or loses a corner -- which is why
    UVs, normals and skin weights (all addressed BY vertex index) are safe."""
    v, t, n = _sphere()
    t = t.copy()
    t[3] = t[3][[0, 2, 1]]
    before = v.copy()
    out, _ = nc._repair_winding_consistency(v, t, n)
    assert np.array_equal(v, before)
    assert out.shape == t.shape
    for a, b in zip(np.sort(t, axis=1), np.sort(out, axis=1)):
        assert list(a) == list(b), "a triangle changed which vertices it uses"


def test_degenerate_triangles_are_left_alone():
    v = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    t = np.array([[0, 1, 2]], dtype=np.int64)
    n = np.tile([0.0, 0.0, 1.0], (3, 1))
    out, flipped = nc._repair_winding_consistency(v, t, n)
    assert flipped == 0 and np.array_equal(out, t)


def test_unusable_input_is_a_no_op():
    v, t, n = _sphere()
    for bad in (np.zeros((0, 3), dtype=np.int64),
                np.array([[0, 1]], dtype=np.int64)):
        out, flipped = nc._repair_winding_consistency(v, bad, n)
        assert flipped == 0
    out, flipped = nc._repair_winding_consistency(v, t, n[:-1])
    assert flipped == 0, "mismatched normal count must not be guessed at"


def test_ships_on_with_a_kill_switch():
    """Default ON: it moves no geometry and is a no-op on a correct mesh, so
    the usual ship-it-off rule for FIT changes does not apply."""
    assert nc.WINDING_CONSISTENCY_REPAIR is True
