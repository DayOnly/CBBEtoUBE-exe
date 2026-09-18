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

"""Ray casting in bounded chunks. The chunk boundary must be invisible.

`_pairs` emits one row per (ray, triangle) candidate, so cost scales with
rays x candidates. On a dense garment in a real run that reached **36,061,621
pairs** and a MemoryError trying to allocate 825 MiB. Two things then happened,
both silent:

  * `record_torso_bands` recorded a `standoff_band_error` -- visible, because it
    was written to record its own failures;
  * `ChainGuard.exposed()` swallowed the same error into a `-1`, which the chain
    contract reported as `unmeasurable`. One shape lost its entry diagnosis and
    the only trace was that single word.

Chunking is safe because rays are independent -- nothing about ray i depends on
ray j -- so the ONLY thing that can go wrong is an implementation slip at the
seam. That is exactly what these tests target: identical results whatever the
chunk size, including sizes that do not divide the ray count evenly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _sheet(z_lo, z_hi, n=26, half=9.0, y=0.0):
    xs = np.linspace(-half, half, n)
    zs = np.linspace(z_lo, z_hi, n)
    X, Z = np.meshgrid(xs, zs)
    V = np.stack([X.ravel(), np.full(X.size, y), Z.ravel()], 1).astype(float)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    N = np.tile(np.array([0.0, 1.0, 0.0]), (len(V), 1))
    return V, np.asarray(tris, np.int64), N


def _scene():
    bV, _bT, bN = _sheet(88.0, 112.0, n=32)
    gV, gT, _n = _sheet(88.0, 112.0, n=32, y=1.0)
    return bV, bN, gV, gT


# ------------------------------------------------------------- cast_chunked
@pytest.mark.parametrize("chunk", [1, 7, 64, 513, 100000])
def test_chunk_size_never_changes_the_distances(chunk):
    """Including sizes that do not divide the ray count -- seams are where a
    chunking slip shows up, not in the middle of a batch."""
    bV, bN, gV, gT = _scene()
    idx = fm.front_slab(bV, bN, 90.0, 102.0)
    assert len(idx) > 64, "fixture too small to exercise multiple chunks"
    t = fm._ClipTester(gV, gT, tmax=fm.TMAX)
    one = fm.cast_chunked(t, bV[idx], bN[idx], chunk=100000)
    got = fm.cast_chunked(t, bV[idx], bN[idx], chunk=chunk)
    assert len(got) == len(one)
    assert np.allclose(np.sort(got), np.sort(one), atol=1e-12)


def test_cast_chunked_matches_the_dense_reference():
    """The calibrated anchor comes from `standoff()`; the chunked sparse path
    must agree with it or every recorded number shifts."""
    bV, bN, gV, gT = _scene()
    idx = fm.front_slab(bV, bN, 90.0, 102.0)
    dense = fm.standoff(bV, bN, gV, gT, idx)
    t = fm._ClipTester(gV, gT, tmax=fm.TMAX)
    got = fm.cast_chunked(t, bV[idx], bN[idx], chunk=64)
    assert len(got) == len(dense)
    assert abs(float(np.median(got)) - float(np.median(dense))) < 1e-9


def test_empty_rays_are_handled():
    bV, bN, gV, gT = _scene()
    t = fm._ClipTester(gV, gT)
    assert len(fm.cast_chunked(t, np.zeros((0, 3)), np.zeros((0, 3)))) == 0


# ------------------------------------------------------------ clipping()
@pytest.mark.parametrize("chunk", [-1, 1, 9, 128, 99999])
def test_clipping_verdict_is_chunk_invariant(chunk):
    """A clipping verdict decides whether a garment ships. If a chunk boundary
    could change it, every count in the telemetry becomes chunk-dependent."""
    bV, bN, gV, gT = _scene()
    behind, _t2, _n2 = _sheet(88.0, 112.0, n=32, y=-0.6)   # garment behind skin
    idx = fm.front_slab(bV, bN, 90.0, 102.0)
    for verts in (gV, behind):
        t = fm._ClipTester(verts, gT)
        ref_m, ref_t = t.clipping(bV, bN, idx, chunk=-1)
        got_m, got_t = t.clipping(bV, bN, idx, chunk=chunk)
        assert np.array_equal(got_m, ref_m), "clip mask changed with chunk size"
        f = np.isfinite(ref_t)
        assert np.array_equal(np.isfinite(got_t), f)
        assert np.allclose(got_t[f], ref_t[f], atol=1e-12)


def test_clipping_still_separates_clean_from_clipping():
    """Guards against a chunking bug that makes everything read the same."""
    bV, bN, gV, gT = _scene()
    behind, _t, _n = _sheet(88.0, 112.0, n=32, y=-0.6)
    idx = fm.front_slab(bV, bN, 90.0, 102.0)
    clean = fm._ClipTester(gV, gT).clipping(bV, bN, idx)[0].sum()
    dirty = fm._ClipTester(behind, gT).clipping(bV, bN, idx)[0].sum()
    assert clean == 0 and dirty > 0, (clean, dirty)


def test_default_chunk_is_bounded():
    assert 0 < fm.RAY_CHUNK <= 8192, (
        "RAY_CHUNK is what bounds peak memory; an unbounded default reinstates "
        "the MemoryError this exists to prevent")


def test_a_dense_garment_no_longer_explodes():
    """Many rays against many triangles -- the shape of the real failure.

    Not a memory assertion (fragile across machines): the point is that the
    chunked path COMPLETES and agrees with a small-chunk reference on geometry
    whose unchunked pair count is large.
    """
    bV, _bT, bN = _sheet(88.0, 112.0, n=60)
    gV, gT, _n = _sheet(88.0, 112.0, n=60, y=1.0)
    idx = fm.front_slab(bV, bN, 88.0, 112.0)
    t = fm._ClipTester(gV, gT, tmax=fm.TMAX)
    ri, _ti = t._pairs(bV[idx][:fm.RAY_CHUNK])
    assert len(ri) > 0
    a = fm.cast_chunked(t, bV[idx], bN[idx], chunk=32)
    b = fm.cast_chunked(t, bV[idx], bN[idx], chunk=256)
    assert len(a) == len(b) and np.allclose(np.sort(a), np.sort(b), atol=1e-12)
