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

"""`_ball_bone_scores` -- the gap-aware 4-bone cap's neighbourhood scores
without a Python list per ball.

The list form (one batched `query_ball_point`, then a dict sum per ball) held
+5 GB on a dense worn prop. The cap keeps the 4 best-scoring bones, ties broken
by name, so the scores must be the SAME doubles, not close ones: a last-bit
difference between two near-equal scores would swap a kept bone. Pinned here
against the list form itself, bit for bit, on a dense cloud with many bones per
vert, a radius that lands exactly on a neighbour, and rows built to tie."""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_convert_weights as nw      # noqa: E402


def _list_scores(V, need, r, rows):
    """The previous implementation, verbatim in substance (the oracle)."""
    nbs = dict(zip(need, cKDTree(V).query_ball_point(V[need], r)))
    out = {}
    for i in need:
        score = {}
        for j in nbs[i]:
            for b, w in rows[j].items():
                if b in rows[i]:
                    score[b] = score.get(b, 0.0) + w
        out[i] = score
    return out


def _rows(rng, n, nbones=12, per=(3, 9)):
    names = [f"Bone{k:02d}" for k in range(nbones)]
    rows = []
    for _ in range(n):
        k = int(rng.integers(*per))
        pick = rng.choice(nbones, k, replace=False)
        w = rng.random(k); w /= w.sum()
        rows.append({names[a]: float(x) for a, x in zip(pick, w)})
    return rows


def _same(a, b):
    assert a.keys() == b.keys()
    for i in a:
        assert a[i].keys() == b[i].keys(), i
        for k in a[i]:
            assert a[i][k] == b[i][k], (i, k, a[i][k], b[i][k])   # exact doubles


def test_dense_cloud_is_bit_identical_to_the_lists():
    rng = np.random.default_rng(3)
    V = rng.uniform(0, 6, (3000, 3))          # dense: hundreds of verts per ball
    rows = _rows(rng, len(V))
    need = [i for i in range(len(V)) if len(rows[i]) > 4]
    _same(nw._ball_bone_scores(V, need, 1.5, rows), _list_scores(V, need, 1.5, rows))


def test_a_neighbour_exactly_at_the_radius_counts_the_same():
    g = np.arange(0.0, 8.0, 1.0)              # grid spacing == radius
    X, Y, Z = np.meshgrid(g, g, g)
    V = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    rows = _rows(np.random.default_rng(5), len(V))
    need = list(range(len(V)))
    _same(nw._ball_bone_scores(V, need, 1.0, rows), _list_scores(V, need, 1.0, rows))


def test_tied_rows_keep_their_tie():
    """Every vert carries the same five equal weights: every score ties, so the
    cap falls back to bone names -- only if the scores are exactly equal."""
    rng = np.random.default_rng(7)
    V = rng.uniform(0, 4, (800, 3))
    rows = [{f"B{k}": 0.2 for k in range(5)} for _ in range(len(V))]
    need = list(range(len(V)))
    got = nw._ball_bone_scores(V, need, 1.2, rows)
    _same(got, _list_scores(V, need, 1.2, rows))
    assert all(len(set(s.values())) == 1 for s in got.values())


def test_chunks_do_not_change_the_scores(monkeypatch):
    """Many small chunks (the pair budget forced low) give the one-chunk answer."""
    rng = np.random.default_rng(11)
    V = rng.uniform(0, 6, (1500, 3))
    rows = _rows(rng, len(V))
    need = [i for i in range(len(V)) if len(rows[i]) > 4]
    whole = nw._ball_bone_scores(V, need, 1.5, rows)
    real = nw.cKDTree if hasattr(nw, "cKDTree") else None
    import scipy.spatial as sp

    class _Small(sp.cKDTree):
        def query_ball_point(self, x, r, **k):
            if k.get("return_length"):
                return super().query_ball_point(x, r, **k) * 2_000
            return super().query_ball_point(x, r, **k)
    monkeypatch.setattr(sp, "cKDTree", _Small)
    _same(nw._ball_bone_scores(V, need, 1.5, rows), whole)
    assert real is None or nw.cKDTree is real
