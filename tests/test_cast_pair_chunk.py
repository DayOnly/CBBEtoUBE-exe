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

"""`_cast` tests its pairs in slices (CAST_PAIR_CHUNK) and must give what one
batch gives, BIT FOR BIT -- the nearest hit per ray and, with `want_tri`, the
triangle chosen when two triangles are hit at the same distance.

That tie-break is the risk: it is "the last pair in order wins", so slicing
that reordered pairs (slicing RAYS would rebuild the ray tree) could change
which triangle a ray hitting a shared edge reports. Pinned here with rays fired
straight at grid vertices and edge midpoints, where ties are guaranteed, and an
arming check that the fixture really produces ties and really slices.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402
from tests.test_clip_tester_exactness import _mixed_mesh, _rays  # noqa: E402


def _flat_grid(n=24):
    """A flat grid at y=0, so rays straight up hit vertices and edges exactly."""
    xs = np.linspace(-6, 6, n)
    X, Z = np.meshgrid(xs, xs)
    V = np.stack([X.ravel(), np.zeros(X.size), Z.ravel()], 1)
    T = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            T += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    return V, np.asarray(T, np.int64)


def _tie_rays(V, n=24):
    """Rays from below, straight up, at every interior vertex and edge midpoint."""
    inner = [i for i in range(len(V))
             if abs(V[i, 0]) < 5.9 and abs(V[i, 2]) < 5.9]
    pts = list(V[inner])
    step = 12.0 / (n - 1)
    pts += [p + np.array([step / 2, 0, 0]) for p in V[inner]]
    O = np.asarray(pts) + np.array([0.0, -2.0, 0.0])
    D = np.tile([0.0, 1.0, 0.0], (len(O), 1))
    return O, D


def _cast(V, T, O, D, chunk, monkeypatch):
    monkeypatch.setattr(fm, "CAST_PAIR_CHUNK", chunk)
    tester = fm._ClipTester(V, T, tmax=14.0)
    ri, ti = tester._pairs(O)
    return ri, tester._cast(O, D, ri, ti, len(O), want_tri=True)


CASES = [
    ("mixed", lambda: (*_mixed_mesh(), *_rays())),
    ("grazing", lambda: (*_mixed_mesh(seed=3), *_rays(seed=4, grazing=True))),
    ("ties", lambda: (lambda V, T: (V, T, *_tie_rays(V)))(*_flat_grid())),
]


@pytest.mark.parametrize("name,make", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("chunk", [1, 37, 4096])
def test_slices_give_what_one_batch_gives(name, make, chunk, monkeypatch):
    V, T, O, D = make()
    _ri, (t_one, who_one) = _cast(V, T, O, D, 0, monkeypatch)
    _ri, (t_cut, who_cut) = _cast(V, T, O, D, chunk, monkeypatch)
    assert np.array_equal(t_one, t_cut)
    assert np.array_equal(who_one, who_cut)


def test_the_fixtures_slice_tie_and_cull(monkeypatch):
    """ARMING TEST. Every case must have more pairs than the smallest real
    slice (or nothing is sliced), more than CAST_CULL_MIN (or the cull, which
    is decided once for the whole set, never runs), and the tie case must hit
    two triangles at one distance on some rays (or the tie-break is untested)."""
    for name, make in CASES:
        V, T, O, D = make()
        ri, (t, _who) = _cast(V, T, O, D, 0, monkeypatch)
        assert len(ri) > 4096 > fm.CAST_CULL_MIN - 1, name
        assert np.isfinite(t).sum() > 20, name
    V, T, O, D = CASES[2][1]()
    tester = fm._ClipTester(V, T, tmax=14.0)
    ri, ti = tester._pairs(O)
    r, _tr, tv = tester._cast_slice(O, D, ri, ti, False, 1e-4)
    best = np.full(len(O), np.inf)
    np.minimum.at(best, r, tv)
    tied = np.bincount(r[tv <= best[r] + 1e-12], minlength=len(O))
    assert (tied >= 2).sum() > 50
