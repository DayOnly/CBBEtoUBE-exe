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

"""`_close_pubic_holes` must orient EVERY fill triangle outward, not the fan
on average.

The old code took the mean dot across the whole fan and flipped all-or-nothing.
A pubic boundary loop is curved enough that one fan spans both orientations, so
every triangle disagreeing with the average stayed inverted and rendered black.
"""
import numpy as np

from src import nif_convert as nc


def _saddle_loop(n=16):
    """A closed loop INSIDE the pubic bbox (z 63-72, |x| <= 6) that is NOT planar.

    The waviness has to fit the bbox or the function correctly skips the loop
    and the test proves nothing -- the first version of this fixture spanned
    z +/-6 and was filtered out. Amplitudes here stay well inside the bounds
    while still curving enough that one fan spans both orientations.
    """
    z = 0.5 * (nc.PUBIC_HOLE_Z_MIN + nc.PUBIC_HOLE_Z_MAX)
    zamp = 0.3 * (nc.PUBIC_HOLE_Z_MAX - nc.PUBIC_HOLE_Z_MIN) / 2.0
    r = nc.PUBIC_HOLE_X_BOUND * 0.4
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    v = np.stack([r * np.cos(ang),
                  r * np.sin(ang) + 2.0 * np.sin(3 * ang),
                  np.full(n, z) + zamp * np.sin(2 * ang)], axis=1)
    assert v[:, 2].min() >= nc.PUBIC_HOLE_Z_MIN
    assert v[:, 2].max() <= nc.PUBIC_HOLE_Z_MAX
    assert np.abs(v[:, 0]).max() <= nc.PUBIC_HOLE_X_BOUND
    return v


def _build_open_ring():
    """Loop verts plus a skirt of triangles that leaves the ring a real
    boundary (each ring edge used exactly once), so the function sees a hole."""
    ring = _saddle_loop()
    n = len(ring)
    outer = ring * 1.6
    outer[:, 2] += 14.0                     # lift clear of the pubic bbox
    verts = np.vstack([ring, outer])
    tris = []
    for i in range(n):
        j = (i + 1) % n
        # one triangle per ring edge -> ring edges are boundary edges
        tris.append([i, n + j, n + i])
        tris.append([i, j, n + j])
    return verts, np.array(tris, dtype=np.int64)


def _outward_normals(verts):
    c = verts.mean(0)
    d = verts - c
    return d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)


def test_every_fill_triangle_faces_outward():
    verts, tris = _build_open_ring()
    normals = _outward_normals(verts)

    new_tris, closed = nc._close_pubic_holes(verts, tris, normals)
    assert closed >= 1, "no loop was closed -- the test built the wrong fixture"

    fill = new_tris[len(tris):]
    assert len(fill), "no fill triangles produced"

    v0, v1, v2 = verts[fill[:, 0]], verts[fill[:, 1]], verts[fill[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
    sn = normals[fill].mean(1)
    sn /= np.maximum(np.linalg.norm(sn, axis=1, keepdims=True), 1e-9)
    dots = np.einsum("ij,ij->i", fn, sn)

    inverted = int((dots < 0).sum())
    assert inverted == 0, (
        f"{inverted}/{len(fill)} fill triangles face INTO the body "
        f"(worst dot {dots.min():+.3f}) -- these render flat black")


def test_the_fixture_actually_exercises_both_orientations():
    """Guards the guard: if the loop were planar the old mean-vote would also
    pass, and this file would be testing nothing. Assert the fan really does
    span both windings before orientation is corrected."""
    verts, tris = _build_open_ring()
    normals = _outward_normals(verts)
    ring = list(range(len(verts) // 2))
    fan = np.array([[ring[0], ring[i], ring[i + 1]]
                    for i in range(1, len(ring) - 1)], dtype=np.int64)
    v0, v1, v2 = verts[fan[:, 0]], verts[fan[:, 1]], verts[fan[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
    sn = normals[fan].mean(1)
    sn /= np.maximum(np.linalg.norm(sn, axis=1, keepdims=True), 1e-9)
    d = np.einsum("ij,ij->i", fn, sn)
    assert (d > 0).any() and (d < 0).any(), (
        "fixture is single-orientation; it would pass with the old mean-dot "
        "vote and prove nothing")


def test_vertices_are_never_moved():
    """The fill may only append triangles over existing indices -- skin weights
    inherit through those indices, so a moved vert would silently desync them."""
    verts, tris = _build_open_ring()
    before = verts.copy()
    nc._close_pubic_holes(verts, tris, _outward_normals(verts))
    assert np.array_equal(verts, before)


def test_no_normals_is_a_no_op():
    verts, tris = _build_open_ring()
    out, closed = nc._close_pubic_holes(verts, tris, None)
    assert closed == 0 and out is tris
