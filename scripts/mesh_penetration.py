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

"""Body-through-garment penetration measured against the garment SURFACE.

WHY THIS EXISTS. The obvious metric -- take each body vertex's NEAREST GARMENT VERTEX
and project the offset onto the body normal, negative = "poking" -- is invalid wherever
the garment does not touch the body. Where cloth hangs away (skirts, robe backs) the
nearest vertex sits at a grazing angle and the dot goes negative with ZERO penetration.

Measured on real output: verts that metric called "poking" at the butt averaged 4.09u
from the nearest garment vertex, and only 0.2% were within 1.0u. It was reporting
DRAPE. Breast numbers from it were fine (tight coverage), which is how a bogus
"breast 2.3% vs butt 13.7%" asymmetry survived long enough to justify building a
feature that was then reverted.

WHAT THIS DOES INSTEAD, per body vertex:
  * exact distance to the nearest POINT ON A TRIANGLE of the garment (not a vertex),
  * the SIDE it falls on, from that triangle's geometric normal,
  * a CONTACT GATE, so vertices the garment merely drapes over are not judged at all.

A vertex counts as poking only when it is on the outward side AND within `contact`
of the surface. Loose drape then reads as "not covered", which is the truth.

ORIENTATION. Triangle normals come from winding, which a NIF does not guarantee to be
outward. `surface_side` resolves the global sign by majority vote against the shape's
stored per-vertex normals, and reports the agreement so a caller can refuse an
ambiguous mesh rather than silently invert the result.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

__all__ = ["closest_point_on_triangles", "surface_penetration"]


def closest_point_on_triangles(pts, a, b, c):
    """Closest point on each triangle (a,b,c) to each point. Vectorised, exact.

    pts (N,3); a/b/c (N,3) -- one triangle per point (broadcast the candidates
    yourself). Standard region-based solve: vertex, edge, then interior.
    """
    ab, ac, ap = b - a, c - a, pts - a
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = pts - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = pts - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)

    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v_int = np.where(np.abs(denom) > 1e-20, vb / denom, 0.0)
        w_int = np.where(np.abs(denom) > 1e-20, vc / denom, 0.0)
    out = a + ab * v_int[:, None] + ac * w_int[:, None]

    # region overrides, applied last-to-first so earlier regions win
    with np.errstate(divide="ignore", invalid="ignore"):
        w_ac = np.where((d2 - d6) != 0, d2 / (d2 - d6), 0.0)
        w_bc = np.where(((d4 - d3) + (d5 - d6)) != 0,
                        (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0)
        v_ab = np.where((d1 - d3) != 0, d1 / (d1 - d3), 0.0)

    m = (va < 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)          # edge bc
    out[m] = (b + (c - b) * w_bc[:, None])[m]
    m = (vb < 0) & (d2 >= 0) & (d6 <= 0)                        # edge ac
    out[m] = (a + ac * w_ac[:, None])[m]
    m = (vc < 0) & (d1 >= 0) & (d3 <= 0)                        # edge ab
    out[m] = (a + ab * v_ab[:, None])[m]
    m = (d5 > 0) & (d6 >= d5)                                   # vertex c
    out[m] = c[m]
    m = (d3 >= 0) & (d4 <= d3)                                  # vertex b
    out[m] = b[m]
    m = (d1 <= 0) & (d2 <= 0)                                   # vertex a
    out[m] = a[m]
    return out


def surface_penetration(body_verts, garment_verts, garment_tris,
                        garment_normals=None, k=24, contact=1.5):
    """Signed distance from each body vertex to the garment surface.

    Returns (signed, dist, covered, agree):
      signed   (N,) +ve = body is on the OUTWARD side of the garment (poking),
                    -ve = body is inside the garment shell (correct).
      dist     (N,) unsigned distance to the nearest point on the surface.
      covered  (N,) bool, dist <= contact -- the gate. Judge ONLY these; far from an
               OPEN surface (a cuirass is not a closed shell) the nearest triangle is
               some hem and its sign is meaningless.

    NOTE ON `contact`: it bounds the measurable DEPTH as well as the coverage, because
    a vertex poking `d` through the surface is itself at distance `d`. Set it too tight
    and the deepest penetrations are gated OUT rather than reported -- at 1.5u every
    max-depth on a real pack pinned to exactly 1.49-1.50, which is the gate, not the
    geometry. Keep it comfortably above the deepest penetration worth seeing (5.0 is
    the census default) and treat `max_depth == contact` as truncated.
      agree    float in [0.5, 1.0], or None when the mesh ships no normals: how
               strongly triangle winding agreed with the stored normals after the
               global flip. Near 0.5 means the orientation is ambiguous and the SIGN
               should not be trusted -- refuse the mesh rather than report from it.

    `contact` is deliberately small. A garment 4u away is not containing that vertex
    in any meaningful sense, and counting it is precisely the bug this replaces.
    """
    gv = np.asarray(garment_verts, dtype=np.float64)
    tris = np.asarray(garment_tris, dtype=np.int64).reshape(-1, 3)
    bv = np.asarray(body_verts, dtype=np.float64)

    a, b, c = gv[tris[:, 0]], gv[tris[:, 1]], gv[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    ln = np.linalg.norm(fn, axis=1)
    keep = ln > 1e-12                       # drop degenerate triangles
    a, b, c, fn, tris = a[keep], b[keep], c[keep], fn[keep] / ln[keep, None], tris[keep]
    if not len(a):
        z = np.zeros(len(bv))
        return z, np.full(len(bv), np.inf), np.zeros(len(bv), dtype=bool), None

    # Resolve winding->outward once, by majority vote against stored vertex normals.
    flip = 1.0
    agree = None
    if garment_normals is not None:
        gn = np.asarray(garment_normals, dtype=np.float64)
        if len(gn) == len(gv):
            vote = np.einsum("ij,ij->i", fn, gn[tris].mean(axis=1))
            agree = float((vote > 0).mean())
            if agree < 0.5:
                flip, agree = -1.0, 1.0 - agree
    fn = fn * flip

    cent = (a + b + c) / 3.0
    kk = min(k, len(cent))
    _d, idx = cKDTree(cent).query(bv, k=kk)
    if kk == 1:
        idx = idx[:, None]

    n = len(bv)
    best = np.full(n, np.inf)
    best_signed = np.zeros(n)
    rows = np.arange(n)
    for j in range(kk):
        t = idx[:, j]
        cp = closest_point_on_triangles(bv, a[t], b[t], c[t])
        off = bv - cp
        d = np.linalg.norm(off, axis=1)
        better = d < best
        if better.any():
            s = np.einsum("ij,ij->i", off, fn[t])
            best[better] = d[better]
            best_signed[better] = np.sign(s[better]) * d[better]
        _ = rows
    return best_signed, best, best <= float(contact), agree
