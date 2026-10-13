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

"""`_bust_morph_chord_req` computes only the INSIDE (triangle, body point) pairs
per slider -- bit-identical to computing all triangle x K pairs and masking
(the old loop, ~10% of a heavy piece's conversion)."""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_convert as nc                  # noqa: E402,F401
from src import nif_convert_fitgeom as fg          # noqa: E402


def _reference_worst(cur, t, ui, bV, bN, bust_z, tree, stack, kb):
    """The old per-slider loop over all pairs (the oracle), returning `worst`."""
    A, B, C = cur[t[:, 0]], cur[t[:, 1]], cur[t[:, 2]]
    _d, bidx = tree.query((A + B + C) / 3.0, k=kb)
    P = bV[bidx]; nb = bN[bidx]
    v0 = (B - A)[:, None, :]; v1 = (C - A)[:, None, :]; v2 = P - A[:, None, :]
    d00 = np.einsum("tkj,tkj->tk", v0, v0); d01 = np.einsum("tkj,tkj->tk", v0, v1)
    d11 = np.einsum("tkj,tkj->tk", v1, v1); d20 = np.einsum("tkj,tkj->tk", v2, v0)
    d21 = np.einsum("tkj,tkj->tk", v2, v1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-12, 1e-12, den)
    wB = (d11 * d20 - d01 * d21) / den; wC = (d00 * d21 - d01 * d20) / den; wA = 1.0 - wB - wC
    inside = (wB >= -1e-6) & (wC >= -1e-6) & (wB + wC <= 1.0 + 1e-6)
    bz = P[:, :, 2]
    ok = inside & (bz >= bust_z[0]) & (bz <= bust_z[1])
    u0, u1, u2 = ui[t[:, 0]], ui[t[:, 1]], ui[t[:, 2]]
    worst = np.full(len(t), -np.inf)
    for dm in stack:
        d = dm.astype(np.float64)
        interp = (wA[:, :, None] * d[u0][:, None, :] + wB[:, :, None] * d[u1][:, None, :]
                  + wC[:, :, None] * d[u2][:, None, :])
        ch = np.einsum("tkj,tkj->tk", d[bidx] - interp, nb)
        np.maximum(worst, np.where(ok, ch, -np.inf).max(axis=1), out=worst)
    worst = np.where(np.isfinite(worst), worst, 0.0)
    return np.clip(worst, 0.0, 1e9)


def test_inside_pairs_only_is_bit_identical():
    rng = np.random.default_rng(11)
    g = np.arange(-6.0, 6.01, 0.25)
    X, Z = np.meshgrid(g, g)
    bV = np.c_[X.ravel(), np.zeros(X.size), Z.ravel() + 96.0]       # a bust-height plane
    bN = np.tile([0.0, 1.0, 0.0], (len(bV), 1))
    gg = np.arange(-5.0, 5.01, 1.3)
    GX, GZ = np.meshgrid(gg, gg)
    cur = np.c_[GX.ravel(), np.full(GX.size, 1.0), GZ.ravel() + 96.0]
    n = len(gg)
    t = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            t += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    t = np.asarray(t, np.int64)
    tree = cKDTree(bV)
    _, ui = tree.query(cur)
    stack = [rng.normal(0, 0.5, (len(bV), 3)).astype(np.float32) for _ in range(12)]
    in_bust = np.ones(len(cur), bool)
    kb = 24
    out = fg._bust_morph_chord_req(cur, t, ui, bV, bN, in_bust, (90.0, 102.0), tree,
                                   stack, k=kb, cap=1e9)
    ref = _reference_worst(cur, t, ui, bV, bN, (90.0, 102.0), tree, stack, kb)
    want = np.zeros(len(cur))
    for col in range(3):
        np.maximum.at(want, t[:, col], ref)
    assert out is not None and (want > 0).any()
    assert np.array_equal(out, want)
