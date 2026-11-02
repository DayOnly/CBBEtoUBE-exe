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

"""`_closest_on_body` evaluates each candidate triangle once and is BIT-IDENTICAL
to the padded form it replaced (every vertex's triangle list padded to the body's
maximum degree -- 141 on the UBE body -- so a point evaluated ~1128 slots for ~28
real triangles: 26% of a heavy piece's conversion).

PINNED HERE: on a mesh with a high-degree fan vertex, and with query points
placed exactly over shared vertices and edges (where several triangles tie),
point, triangle and barycentrics equal the padded reference bit for bit.
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_convert_weights as nw      # noqa: E402


def _padded_reference(P, bV, bT, tree, vt_adj, k=8, chunk=4096):
    """The previous implementation, verbatim in substance (the oracle)."""
    P = np.asarray(P, np.float64)
    m = len(P)
    best_q = np.zeros((m, 3)); best_t = np.zeros(m, dtype=np.int64); best_w = np.zeros((m, 3))
    deg = max((len(a) for a in vt_adj), default=0)
    pad = np.full((len(vt_adj), max(deg, 1)), -1, np.int64)
    for j, a in enumerate(vt_adj):
        pad[j, :len(a)] = a
    _, nn = tree.query(P, k=k)
    nn = np.asarray(nn).reshape(m, -1)
    for s0 in range(0, m, chunk):
        sl = slice(s0, s0 + chunk)
        cand = pad[nn[sl]].reshape(len(nn[sl]), -1)
        ok = cand >= 0
        cs = np.where(ok, cand, 0)
        A, B, C = bV[bT[cs, 0]], bV[bT[cs, 1]], bV[bT[cs, 2]]
        ab, ac, ap = B - A, C - A, P[sl, None, :] - A
        d00 = (ab * ab).sum(-1); d01 = (ab * ac).sum(-1); d11 = (ac * ac).sum(-1)
        d20 = (ap * ab).sum(-1); d21 = (ap * ac).sum(-1)
        den = d00 * d11 - d01 * d01 + 1e-12
        v = np.clip((d11 * d20 - d01 * d21) / den, 0, 1)
        w = np.clip((d00 * d21 - d01 * d20) / den, 0, 1)
        u = np.clip(1 - v - w, 0, 1)
        s = u + v + w
        u, v, w = u / s, v / s, w / s
        Q = A * u[..., None] + B * v[..., None] + C * w[..., None]
        dd = np.where(ok, np.linalg.norm(Q - P[sl, None, :], axis=-1), np.inf)
        j = np.argmin(dd, axis=1)
        r = np.arange(len(j))
        best_q[sl] = Q[r, j]; best_t[sl] = cs[r, j]
        best_w[sl] = np.stack([u[r, j], v[r, j], w[r, j]], axis=1)
    return best_q, best_t, best_w


def _mesh():
    """A curved grid plus a 60-triangle fan around one vertex (a high degree)."""
    g = np.arange(-4.0, 4.01, 0.5)
    X, Z = np.meshgrid(g, g)
    V = [np.c_[X.ravel(), 0.05 * (X.ravel() ** 2 + Z.ravel() ** 2), Z.ravel()]]
    n = len(g)
    T = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            T += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    base = n * n
    ang = np.linspace(0, 2 * np.pi, 61)[:-1]
    ring = np.c_[6.0 + 1.0 * np.cos(ang), np.full(60, 1.0), 1.0 * np.sin(ang)]
    V.append(np.array([[6.0, 1.3, 0.0]]))
    V.append(ring)
    hub = base
    for q in range(60):
        T.append([hub, base + 1 + q, base + 1 + (q + 1) % 60])
    bV = np.vstack(V)
    bT = np.asarray(T, dtype=np.int64)
    vt = [[] for _ in range(len(bV))]
    for ti, (a, b, c) in enumerate(bT):
        vt[a].append(ti); vt[b].append(ti); vt[c].append(ti)
    return bV, bT, [np.asarray(x, np.int64) for x in vt]


def _points(bV):
    rng = np.random.default_rng(7)
    over_verts = bV[:289] + np.array([0.0, 0.4, 0.0])        # exactly over grid verts: ties
    mids = (bV[:288] + bV[1:289]) / 2 + np.array([0.0, 0.3, 0.0])   # over edges: ties
    near_hub = np.array([6.0, 1.8, 0.0]) + rng.normal(0, 0.3, (200, 3))
    rand = rng.uniform([-4, -1, -4], [7, 3, 4], (2000, 3))
    return np.vstack([over_verts, mids, near_hub, rand])


def test_bit_identical_to_the_padded_form():
    bV, bT, vt = _mesh()
    P = _points(bV)
    tree = cKDTree(bV)
    ref = _padded_reference(P, bV, bT, tree, vt)
    new = nw._closest_on_body(P, bV, bT, tree, vt)
    for a, b in zip(ref, new):
        assert np.array_equal(a, b)


def test_chunking_does_not_change_the_answer():
    bV, bT, vt = _mesh()
    P = _points(bV)
    tree = cKDTree(bV)
    a = nw._closest_on_body(P, bV, bT, tree, vt)
    b = nw._closest_on_body(P, bV, bT, tree, vt, chunk=37)
    for x, y in zip(a, b):
        assert np.array_equal(x, y)
