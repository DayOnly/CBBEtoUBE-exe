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

"""#rim-chunk-budget -- `_rim_distance` peak no longer scales with the garment.

The loop chunks the BODY axis at 2048 and left the RIM-EDGE axis M unbounded,
so the peak was 120 bytes x 2048 x M and grew with the mesh being converted.
MEASURED over the 383 real NIFs in this repo (4250 shapes): p50 56 MiB, p90
342 MiB, p99 1.40 GiB, max 3.71 GiB on a 33k-vert shape with 16,217 rim edges.
"Rim" is not a thin hem: Skyrim NIFs split vertices at every UV seam and hard
edge, so "edge used by exactly one triangle" captures roughly half the mesh.

The failure was INVISIBLE. `nif_convert.py` wraps the call in `except
Exception`, and MemoryError subclasses Exception, so the shape shipped without
its minimum-push fit, the run continued, and the error surfaced in whichever
worker allocated next -- possibly a different mod entirely.

THE FIX CANNOT CHANGE OUTPUT, and that is provable rather than hoped: each
output slot depends only on its own body vertex and ALL M edges, and nothing
reduces across chunks, so chunk size is a pure memory/CPU trade."""
import numpy as np
import tracemalloc

from src.fit_metrics import _rim_distance, _RIM_CHUNK_BUDGET


def _case(n_body=1500, n_gar=6000, n_rim=4000, seed=11):
    rng = np.random.default_rng(seed)
    bV = rng.normal(size=(n_body, 3)) * 10.0
    gV = rng.normal(size=(n_gar, 3)) * 10.0
    rim = np.stack([rng.integers(0, n_gar, n_rim),
                    rng.integers(0, n_gar, n_rim)], 1).astype(np.int64)
    return bV, gV, rim


def test_chunk_size_cannot_change_a_single_value():
    """The correctness argument for the fix, asserted bitwise.

    Not `allclose`: the claim is that the arithmetic per output element is
    IDENTICAL, so anything short of bit-equality would mean the reasoning is
    wrong somewhere."""
    bV, gV, rim = _case()
    ref = _rim_distance(bV, gV, rim, chunk=2048)
    for c in (1, 7, 37, 777, 2048, 100000):
        got = _rim_distance(bV, gV, rim, chunk=c)
        assert np.array_equal(ref, got), f"chunk={c} changed the result"


def test_peak_is_bounded_regardless_of_rim_size():
    """The regression guard. Before the fix this peaked at 1,876 MiB."""
    for n_rim in (500, 4000, 12000):
        bV, gV, rim = _case(n_rim=n_rim)
        tracemalloc.start()
        try:
            _rim_distance(bV, gV, rim)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 96 << 20, (
            f"{n_rim} rim edges peaked at {peak / 2**20:.1f} MiB; the "
            f"rim-edge axis is unbounded again")


def test_the_bound_actually_bites_on_a_big_garment():
    """Assert the mechanism FIRED, not merely that the number came out small.

    A test that only checks the peak would also pass if `_rim_distance` started
    returning early or the case stopped being big -- so check the chunk was
    genuinely reduced below the 2048 default for a garment where it must be."""
    n_rim = 12000
    assert _RIM_CHUNK_BUDGET // (120 * n_rim) < 2048, (
        "the budget no longer constrains a 12k-edge garment; either the budget "
        "was raised past the point of usefulness or the cost model changed")


def test_a_small_garment_is_left_alone():
    """No throughput cost on the common case: a thin rim keeps chunk=2048."""
    assert _RIM_CHUNK_BUDGET // (120 * 100) >= 2048


def test_no_rim_edges_still_returns_infinities():
    bV, gV, _ = _case()
    out = _rim_distance(bV, gV, np.zeros((0, 2), np.int64))
    assert out.shape == (len(bV),) and np.all(np.isinf(out))
