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

"""`_repair_degenerate_normals` -- a zero-length normal cannot shade a surface
and cannot decide a winding.

The UBE body ships six of them (|n| = 0.0068) sitting on the pubic hole
boundary, which is precisely where the converter adds fill triangles.
"""
import numpy as np

from src import nif_convert as nc


def _tetra():
    v = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0],
                  [0.0, 4.0, 0.0], [0.0, 0.0, 4.0]])
    t = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64)
    return v, t


def test_a_degenerate_normal_is_replaced_with_a_usable_one():
    v, t = _tetra()
    n = nc._vertex_normals_from_tris(v, t)
    n[2] = np.array([0.0, 0.0, 0.0068])          # the real observed magnitude
    fixed, count = nc._repair_degenerate_normals(v, t, n)
    assert count == 1
    assert np.linalg.norm(fixed[2]) > 0.9, "still unusable for shading"


def test_healthy_normals_are_left_byte_identical():
    """The repair must not restyle shading -- only unusable entries change."""
    v, t = _tetra()
    n = nc._vertex_normals_from_tris(v, t)
    n[1] = np.array([0.0, 0.0, 0.001])
    fixed, count = nc._repair_degenerate_normals(v, t, n)
    assert count == 1
    keep = [0, 2, 3]
    assert np.array_equal(fixed[keep], n[keep])


def test_no_degenerates_is_a_no_op():
    v, t = _tetra()
    n = nc._vertex_normals_from_tris(v, t)
    fixed, count = nc._repair_degenerate_normals(v, t, n)
    assert count == 0
    assert np.array_equal(fixed, n)


def test_every_output_normal_is_usable():
    """Including a vertex no triangle references, where geometry gives nothing.
    Shipping a zero vector would just move the defect downstream."""
    v, t = _tetra()
    v = np.vstack([v, [9.0, 9.0, 9.0]])          # orphan vertex
    n = np.vstack([nc._vertex_normals_from_tris(v[:4], t), [0.0, 0.0, 0.0]])
    n[0] = np.array([0.0, 0.0, 0.0])
    fixed, count = nc._repair_degenerate_normals(v, t, n)
    assert count == 2
    assert (np.linalg.norm(fixed, axis=1) > 0.9).all()


def test_the_fill_winding_vote_is_no_longer_decided_by_a_zero_vector():
    """End to end: with a degenerate normal on the hole boundary, the fill must
    still orient every triangle -- no dot of exactly 0.000."""
    z = 0.5 * (nc.PUBIC_HOLE_Z_MIN + nc.PUBIC_HOLE_Z_MAX)
    r = nc.PUBIC_HOLE_X_BOUND * 0.4
    ang = np.linspace(0, 2 * np.pi, 14, endpoint=False)
    ring = np.stack([r * np.cos(ang), r * np.sin(ang), np.full(14, z)], axis=1)
    outer = ring * 1.6
    outer[:, 2] += 14.0
    verts = np.vstack([ring, outer])
    tris = []
    for i in range(14):
        j = (i + 1) % 14
        tris.append([i, 14 + j, 14 + i])
        tris.append([i, j, 14 + j])
    tris = np.array(tris, dtype=np.int64)

    c = verts.mean(0)
    normals = verts - c
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
    normals[3] = np.array([0.0, 0.0, 0.0068])    # degenerate ON the boundary

    new_tris, closed = nc._close_pubic_holes(verts, tris, normals)
    assert closed >= 1
    fill = new_tris[len(tris):]
    fixed, _ = nc._repair_degenerate_normals(verts, tris, normals)
    v0, v1, v2 = verts[fill[:, 0]], verts[fill[:, 1]], verts[fill[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
    sn = fixed[fill].mean(1)
    sn /= np.maximum(np.linalg.norm(sn, axis=1, keepdims=True), 1e-9)
    dots = np.einsum("ij,ij->i", fn, sn)
    assert (np.abs(dots) > 1e-6).all(), "a winding was decided by a zero vector"
    assert (dots > 0).all(), "a fill triangle still faces inward"
