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

"""The clip metric must not count garment seen ACROSS the body as clipping.

The inward ray used to keep travelling after it left the body, so a skirt on the
far side of the gap between the legs scored as a hip poke-through. Measured on a
shipped cuirass: 162 of 257 flagged hip verts (63%) were this, at a median 3.22u
through the body interior.

Built on synthetic geometry with a KNOWN answer, because the whole point is that
the old metric returned a confident wrong number on real meshes. Two cases carry
the test, and BOTH are required:

  * a garment genuinely under the skin  -> still counted   (not over-suppressed)
  * a garment beyond the body's far wall -> now rejected   (the fix)

A change that only proved the second would be indistinguishable from one that
broke the metric entirely.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "analysis"))
from mesh_penetration import (clipping_report, ray_first_hit,     # noqa: E402
                              _auto_chunk)


def _quad(z, y, size=6.0, flip=False):
    """Two triangles forming a plane at height z, facing +y (or -y)."""
    v = np.array([[-size, y, z - size], [size, y, z - size],
                  [size, y, z + size], [-size, y, z + size]], float)
    t = np.array([[0, 1, 2], [0, 2, 3]] if not flip else
                 [[0, 2, 1], [0, 3, 2]], np.int64)
    return v, t


def _body_slab():
    """A thin two-walled body: near wall at y=0, far wall at y=-4.

    Normals face +y (outward, toward the viewer) on the near wall, so the
    inward ray runs -y and crosses the far wall at t=4.
    """
    near_v, near_t = _quad(0.0, 0.0)
    far_v, far_t = _quad(0.0, -4.0)
    verts = np.vstack([near_v, far_v])
    tris = np.vstack([near_t, far_t + len(near_v)])
    normals = np.zeros_like(verts)
    normals[:len(near_v)] = (0.0, 1.0, 0.0)
    normals[len(near_v):] = (0.0, -1.0, 0.0)
    mask = np.zeros(len(verts), bool)
    mask[:len(near_v)] = True          # score the NEAR wall only
    return verts, tris, normals, mask


def test_auto_chunk_shrinks_for_dense_meshes():
    """A fixed chunk against a 58k-triangle body allocates gigabytes."""
    assert _auto_chunk(2_000) > _auto_chunk(58_000)
    assert _auto_chunk(58_000) >= 16
    assert _auto_chunk(10) <= 2048


def test_ray_first_hit_orders_by_distance():
    v, t = _quad(0.0, -2.0)
    o = np.array([[0.0, 0.0, 0.0]])
    d = np.array([[0.0, -1.0, 0.0]])
    assert np.isclose(ray_first_hit(o, d, v, t, tmax=10.0)[0], 2.0)
    # facing away -> no hit
    assert np.isinf(ray_first_hit(o, -d, v, t, tmax=10.0)[0])


def test_genuine_poke_through_is_still_counted():
    """Garment 1u under the skin, BEFORE the far wall. Must survive the fix."""
    bv, bt, bn, mask = _body_slab()
    g = _quad(0.0, -1.0)               # inside the slab
    r = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0)
    assert r["clipping_pct"] > 99.0, r


def test_garment_beyond_the_far_wall_is_rejected():
    """Garment at y=-6, past the far wall at y=-4. The artifact."""
    bv, bt, bn, mask = _body_slab()
    g = _quad(0.0, -6.0)
    r = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0)
    assert r["clipping_pct"] < 1.0, r


def test_old_behaviour_reproduces_the_artifact():
    """The opt-out must actually restore the bug, or it documents nothing.

    This is the negative control for the fix itself: if the far-side case scores
    zero with body_occlusion OFF too, then the synthetic geometry never
    exercised the artifact and the test above proves nothing.
    """
    bv, bt, bn, mask = _body_slab()
    g = _quad(0.0, -6.0)
    r = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0,
                        body_occlusion=False)
    assert r["clipping_pct"] > 99.0, r


def test_covered_from_outside_is_never_clipping():
    """A garment in FRONT of the skin covers; it is not a poke-through."""
    bv, bt, bn, mask = _body_slab()
    g = _quad(0.0, 1.0)
    r = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0)
    assert r["covered_pct"] > 99.0, r
    assert r["clipping_pct"] < 1.0, r


def test_uncovered_stays_uncovered():
    bv, bt, bn, mask = _body_slab()
    g = _quad(60.0, 1.0)               # far away in z, hits nothing
    r = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0)
    assert r["uncovered_pct"] > 99.0, r


def test_shallow_poke_is_not_lost_to_the_self_hit_guard():
    """A poke shallower than `eps` must still count.

    The first implementation stepped the GARMENT ray's origin inside the skin to
    dodge self-hits, which silently dropped every poke-through shallower than
    the offset -- under-reporting, the opposite of the bug being fixed. The guard
    belongs on the body cast's hit distance, not on the shared origin.
    """
    bv, bt, bn, mask = _body_slab()
    g = _quad(0.0, -0.02)              # 0.02u under the skin, well inside eps
    r = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0, eps=0.05)
    assert r["clipping_pct"] > 99.0, r


def test_body_cast_reaches_past_the_garment_range():
    """The far wall must be findable even when it is far beyond `tmax`.

    A torso's far wall is a median 12.8u away while garments are searched within
    5u. Sharing one tmax left the gate inert on two thirds of a real hip band --
    it silently found no wall and passed everything through.
    """
    bv, bt, bn, mask = _body_slab()
    far_v, far_t = _quad(0.0, -40.0)          # wall well past tmax
    bv2 = np.vstack([bv[:4], far_v])
    bt2 = np.vstack([bt[:2], far_t + 4])
    bn2 = np.zeros_like(bv2)
    bn2[:4] = (0.0, 1.0, 0.0)
    bn2[4:] = (0.0, -1.0, 0.0)
    m = np.zeros(len(bv2), bool)
    m[:4] = True
    g = _quad(0.0, -60.0)                     # garment beyond that far wall
    r = clipping_report(bv2, bt2, bn2, [g], mask=m, tmax=100.0,
                        body_tmax=200.0)
    assert r["clipping_pct"] < 1.0, r
    # with the body cast clamped to the garment range the wall is missed
    r2 = clipping_report(bv2, bt2, bn2, [g], mask=m, tmax=100.0, body_tmax=1.0)
    assert r2["clipping_pct"] > 99.0, r2
