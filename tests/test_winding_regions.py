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

"""The winding repair works on REGIONS, never single triangles.

Reported in game as "a major clip between layers on the character's right
breast". The garment was not clipping: 29 of its triangles had been re-wound
individually, so 18 shared edges were traversed the SAME way by both their
triangles, one face of each pair was backface-culled, and the surface had holes
in it. The author's mesh has zero such edges.

The old rule flipped any triangle whose winding disagreed with its own vertex
normals. That test is sound on the author's geometry and unsound where the pass
actually runs -- after the fit chain has moved verts while the stored normals
have not followed. The authors themselves ship 52,683 triangles that "disagree"
on surfaces that hold together perfectly, which is the proof that per-triangle
agreement was never the invariant.

These pin the replacement: partition into consistently-wound regions, vote once
per region (area-weighted), flip whole regions only -- and refuse outright any
rewrite that would raise the seam count.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402


def _grid(nx=6, ny=6, z=0.0):
    """A consistently-wound plane: every quad split the same way, so every
    interior edge is traversed in opposite directions by its two triangles."""
    xs, ys = np.meshgrid(np.arange(nx, dtype=np.float64),
                         np.arange(ny, dtype=np.float64), indexing="ij")
    v = np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, z)], axis=1)
    t = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a = i * ny + j
            b = a + 1
            c = (i + 1) * ny + j
            d = c + 1
            t.append((a, c, b))
            t.append((b, c, d))
    return v, np.asarray(t, dtype=np.int64)


def _up(n):
    return np.tile(np.array([0.0, 0.0, 1.0]), (n, 1))


def _seams(t):
    return nc._winding_regions(np.asarray(t, dtype=np.int64))[1]


# --------------------------------------------------------------------------
# The metric itself
# --------------------------------------------------------------------------

def test_a_consistent_surface_has_no_seams_and_is_ONE_region():
    v, t = _grid()
    lab, seams = nc._winding_regions(t)
    assert seams == 0
    assert len(set(lab.tolist())) == 1, "a consistent plane is one region"


def test_flipping_ONE_interior_triangle_is_what_creates_seams():
    """The defect, reproduced. This is why the unit cannot be a triangle."""
    v, t = _grid()
    t2 = t.copy()
    t2[10] = t2[10][[0, 2, 1]]
    assert _seams(t2) > 0, "a lone flip must register as damage"
    lab, _ = nc._winding_regions(t2)
    assert len(set(lab.tolist())) > 1, "it also splits the surface"


# --------------------------------------------------------------------------
# What the repair must NOT do
# --------------------------------------------------------------------------

def test_a_consistent_surface_whose_normals_disagree_is_LEFT_ALONE():
    """The exact shape of the shipped defect: the surface is sound, but some
    triangles' stored normals no longer match their moved geometry. The old
    rule flipped precisely these and tore the mesh."""
    v, t = _grid()
    n = _up(len(v))
    # Three interior triangles carry normals pointing the other way -- as a
    # stale normal does after the fit chain moves a vertex under it.
    n = n.copy()
    for tri in (10, 11, 12):
        n[t[tri]] = np.array([0.0, 0.0, -1.0])
    out, flipped = nc._repair_winding_consistency(v, t, n)
    assert flipped == 0, "no region-wide evidence -> nothing may be flipped"
    assert np.array_equal(out, t)
    assert _seams(out) == 0


def test_the_repair_can_never_raise_the_seam_count():
    """The hard guard, stated as the acceptance property. Whatever the region
    logic decides, a surface may not leave with a new tear."""
    v, t = _grid()
    rng = np.random.default_rng(0)
    for trial in range(8):
        n = _up(len(v))
        pick = rng.choice(len(t), size=rng.integers(1, 6), replace=False)
        for tri in pick:
            n[t[tri]] = -n[t[tri]]
        out, _ = nc._repair_winding_consistency(v, t, n)
        assert _seams(out) <= _seams(t), f"trial {trial} introduced a tear"


def test_a_sliver_cannot_outvote_the_panel_it_sits_on():
    """The vote is area-weighted (`cr` is unnormalised, so its length is twice
    the area). A single tiny triangle disagreeing must not flip a whole panel."""
    v, t = _grid()
    v = v.copy()
    # Squash one triangle's apex onto its own edge -> near-zero area.
    v[t[0][2]] = v[t[0][0]] + 1e-4 * (v[t[0][2]] - v[t[0][0]])
    n = _up(len(v))
    n[t[0]] = np.array([0.0, 0.0, -1.0])
    out, flipped = nc._repair_winding_consistency(v, t, n)
    assert flipped == 0, "a sliver must not decide a panel's orientation"


# --------------------------------------------------------------------------
# What it must STILL do -- the reason the pass exists
# --------------------------------------------------------------------------

def test_a_WHOLLY_inverted_region_is_still_repaired():
    """The positive control. A negative-determinant transform bake mirrors the
    mesh and inverts an entire region at once; that is the case this pass was
    built for and it must survive the rewrite."""
    v, t = _grid()
    inv = t[:, [0, 2, 1]]                      # the whole surface mirrored
    assert _seams(inv) == 0, "a wholly inverted surface is still CONSISTENT"
    out, flipped = nc._repair_winding_consistency(v, inv, _up(len(v)))
    assert flipped == len(t), "the entire region must be turned back"
    assert np.array_equal(np.sort(out, axis=1), np.sort(t, axis=1))
    assert _seams(out) == 0
    cr = np.cross(v[out[:, 1]] - v[out[:, 0]], v[out[:, 2]] - v[out[:, 0]])
    assert (cr[:, 2] > 0).all(), "and must end up facing the stored normals"


def test_two_regions_are_voted_INDEPENDENTLY():
    """One inverted panel next to a correct one: fix the first, keep the
    second. A single global vote would get one of them wrong."""
    v1, t1 = _grid()
    v2, t2 = _grid()
    v2 = v2 + np.array([100.0, 0.0, 0.0])       # disjoint, so a second region
    v = np.vstack([v1, v2])
    t = np.vstack([t1, t2[:, [0, 2, 1]] + len(v1)])
    out, flipped = nc._repair_winding_consistency(v, t, _up(len(v)))
    assert flipped == len(t2), "only the inverted panel may be turned"
    assert np.array_equal(out[:len(t1)], t1), "the good panel is untouched"
    assert _seams(out) == 0


def test_an_already_correct_mesh_is_returned_untouched():
    v, t = _grid()
    out, flipped = nc._repair_winding_consistency(v, t, _up(len(v)))
    assert flipped == 0 and np.array_equal(out, t)


# --------------------------------------------------------------------------
# Degenerate input
# --------------------------------------------------------------------------

def test_non_manifold_edges_join_nothing():
    """Three triangles on one edge: the orientation there is undecidable, and
    guessing is how a repair starts inventing damage."""
    v = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]])
    t = np.array([[0, 1, 2], [0, 3, 1], [0, 1, 4]], dtype=np.int64)
    lab, seams = nc._winding_regions(t)
    assert seams == 0, "a >2-shared edge is not counted as a seam"
    assert len(set(lab.tolist())) == 3, "and it joins no triangles"


def test_empty_and_malformed_input_is_survivable():
    v, t = _grid()
    n = _up(len(v))
    empty = np.zeros((0, 3), np.int64)
    out, flipped = nc._repair_winding_consistency(v, empty, n)
    assert flipped == 0 and len(out) == 0
    # A normals array that does not match the verts cannot decide anything.
    out, flipped = nc._repair_winding_consistency(v, t, n[:3])
    assert flipped == 0 and np.array_equal(out, t)
    # Zero-length normals carry no direction; the region vote must ignore them
    # rather than read them as disagreement.
    out, flipped = nc._repair_winding_consistency(v, t, np.zeros_like(n))
    assert flipped == 0 and np.array_equal(out, t)


def test_the_flag_still_gates_it():
    assert isinstance(nc.WINDING_CONSISTENCY_REPAIR, bool)
    import importlib
    os.environ["CBBE2UBE_NO_WINDING_REPAIR"] = "1"
    try:
        assert importlib.reload(nc).WINDING_CONSISTENCY_REPAIR is False
    finally:
        del os.environ["CBBE2UBE_NO_WINDING_REPAIR"]
        importlib.reload(nc)
