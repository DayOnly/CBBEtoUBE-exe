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

"""The ray-cast speedups must be EXACT, not merely close.

Three optimisations make the fit contract affordable: radius-tiered ball
queries, a C-level sparse distance matrix instead of lists of Python lists, and
a ray-line cull before the intersection test. Each is a conservative bound, so
each should return the same hits as brute force.

That "should" is the whole risk. A cull that drops a real intersection makes a
clipping vert read as clean, and NOTHING downstream can tell that apart from a
garment that genuinely does not clip -- the metric, the guard, the census and
the release verdict would all agree, and all be wrong. So these tests compare
against exhaustive all-pairs Moller-Trumbore rather than against a previous
run's numbers, and they include geometry chosen to stress the bounds: wildly
mixed triangle sizes (which is what the tiers key off) and grazing rays (which
is what the cull is most likely to get wrong).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _brute(gV, gT, O, D, tmax):
    """Exhaustive every-ray-against-every-triangle. The reference.

    Broadcast rather than looped: the loop version was 46s, three quarters of
    the entire suite's runtime, which is a bad trade for a check that should be
    cheap enough to keep. Same arithmetic, same tolerances -- and the fixture
    stays large on purpose, because shrinking it below CAST_CULL_MIN would stop
    the cull engaging and quietly turn every test here into a test of the
    unoptimised path.
    """
    gV = np.asarray(gV, float)
    gT = np.asarray(gT, np.int64).reshape(-1, 3)
    O = np.asarray(O, float)
    D = np.asarray(D, float)
    a = gV[gT[:, 0]]                       # (T,3)
    e1 = gV[gT[:, 1]] - a
    e2 = gV[gT[:, 2]] - a
    # (R,T,3) via broadcasting: rays on axis 0, triangles on axis 1
    p = np.cross(D[:, None, :], e2[None, :, :])
    det = np.einsum("rtj,tj->rt", p, e1)
    ok = np.abs(det) > 1e-9
    inv = np.zeros_like(det)
    np.divide(1.0, det, out=inv, where=ok)
    t0 = O[:, None, :] - a[None, :, :]
    u = np.einsum("rtj,rtj->rt", t0, p) * inv
    q = np.cross(t0, e1[None, :, :])
    v = np.einsum("rtj,rj->rt", q, D) * inv
    t = np.einsum("rtj,tj->rt", q, e2) * inv
    hit = (ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
           & (t > 1e-4) & (t < tmax))
    masked = np.where(hit, t, np.inf)
    return masked.min(axis=1), masked.argmin(axis=1)


def _mixed_mesh(seed=0, n=26, mix=True):
    """A grid whose triangle sizes vary by an order of magnitude.

    Uniform triangles would put everything in one tier and leave the tiering
    untested; a single huge triangle is exactly the case that inflated the
    search ball for the whole mesh.
    """
    rng = np.random.default_rng(seed)
    xs = np.linspace(-6, 6, n)
    zs = np.linspace(-6, 6, n)
    X, Z = np.meshgrid(xs, zs)
    V = np.stack([X.ravel(), rng.normal(0, 0.35, X.size), Z.ravel()], 1)
    T = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            T += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    T = np.asarray(T, np.int64)
    if mix:
        # a few deliberately enormous triangles spanning the whole mesh
        T = np.vstack([T, np.array([[0, n * n - 1, n - 1],
                                    [0, n * (n - 1), n * n - 1]], np.int64)])
    return V, T


def _rays(seed=1, k=180, grazing=False):
    rng = np.random.default_rng(seed)
    O = np.stack([rng.uniform(-6, 6, k), np.full(k, -4.0),
                  rng.uniform(-6, 6, k)], 1)
    if grazing:
        # Nearly parallel to the sheet: a long shallow path, which is where the
        # ray-line distance bound is tightest and a bad cull would show first.
        # The origin has to sit CLOSE to the sheet -- an earlier version fired
        # these from y=-4 at 0.02 rise, so they needed t~200 against a tmax of
        # 14 and never hit anything. Both grazing cases then compared
        # all-infinity to all-infinity and passed while testing nothing.
        O[:, 1] = -1.0
        D = np.stack([rng.normal(0, 1, k), np.full(k, 0.15),
                      rng.normal(0, 1, k)], 1)
    else:
        D = np.stack([rng.normal(0, 0.25, k), np.ones(k),
                      rng.normal(0, 0.25, k)], 1)
    D /= np.linalg.norm(D, axis=1, keepdims=True)
    return O, D


def test_the_fixture_actually_engages_the_cull():
    """ARMING TEST. The cull is skipped below CAST_CULL_MIN pairs, so a smaller
    fixture would make every exactness test above a test of the unoptimised
    path -- passing, and proving nothing about the code that actually runs."""
    V, T = _mixed_mesh()
    O, _D = _rays()
    ri, _ti = fm._ClipTester(V, T, tmax=14.0)._pairs(O)
    assert len(ri) > fm.CAST_CULL_MIN, (
        f"only {len(ri)} pairs; the cull never engages and these tests are "
        f"vacuous")


def test_the_fixture_actually_engages_multiple_tiers():
    """Same hazard for the radius tiers: one tier means the tiering is untested."""
    V, T = _mixed_mesh()
    assert len(fm._ClipTester(V, T).tiers()) > 1


@pytest.mark.parametrize("grazing", [False, True])
@pytest.mark.parametrize("mix", [True, False])
def test_cast_matches_brute_force(grazing, mix):
    V, T = _mixed_mesh(mix=mix)
    O, D = _rays(grazing=grazing)
    tester = fm._ClipTester(V, T, tmax=14.0)
    ri, ti = tester._pairs(O)
    got = tester._cast(O, D, ri, ti, len(O))
    ref, _who = _brute(V, T, O, D, 14.0)
    assert np.array_equal(np.isfinite(got), np.isfinite(ref)), (
        f"hit/miss disagrees on {int((np.isfinite(got) != np.isfinite(ref)).sum())}"
        f" of {len(O)} rays")
    f = np.isfinite(ref)
    assert np.allclose(got[f], ref[f], atol=1e-7)
    assert f.any(), "no ray hit anything; this fixture proves nothing"


def test_cull_and_tiers_do_not_change_the_verdict(monkeypatch):
    """The optimised path must agree with the slow path it replaced."""
    V, T = _mixed_mesh()
    O, D = _rays()
    fast = fm._ClipTester(V, T, tmax=14.0)
    ri, ti = fast._pairs(O)
    hot = fast._cast(O, D, ri, ti, len(O))
    monkeypatch.setattr(fm, "CAST_CULL", False)
    slow = fm._ClipTester(V, T, tmax=14.0)
    ri2, ti2 = slow._pairs(O)
    cold = slow._cast(O, D, ri2, ti2, len(O))
    assert np.array_equal(np.isfinite(hot), np.isfinite(cold))
    f = np.isfinite(cold)
    assert np.allclose(hot[f], cold[f], atol=1e-9)


def test_cull_survives_unnormalised_directions(monkeypatch):
    """The cull divides by |D| instead of assuming it is 1.

    Compared against the SAME cast with the cull disabled, at each scale --
    not against the unit-length result. `t` is expressed in units of |D|, so
    scaling the direction also scales the effective tmax and legitimately
    changes which rays reach a triangle; that is pre-existing `_cast`
    semantics and not what this test is about. The invariant here is narrower
    and is the one that can silently lose hits: whatever the cull is handed,
    it must not remove a pair the intersection test would have found.
    """
    V, T = _mixed_mesh()
    O, D = _rays()
    tester = fm._ClipTester(V, T, tmax=14.0)
    ri, ti = tester._pairs(O)
    for scale in (0.25, 1.0, 7.0):
        monkeypatch.setattr(fm, "CAST_CULL", False)
        ref = tester._cast(O, D * scale, ri, ti, len(O))
        monkeypatch.setattr(fm, "CAST_CULL", True)
        got = tester._cast(O, D * scale, ri, ti, len(O))
        assert np.array_equal(np.isfinite(got), np.isfinite(ref)), (
            f"the cull dropped real hits at |D|={scale}")
        f = np.isfinite(ref)
        assert np.allclose(got[f], ref[f], atol=1e-9)


def test_pairs_is_a_superset_of_every_real_hit():
    """Candidate pruning must never drop a triangle a ray actually hits."""
    V, T = _mixed_mesh()
    O, D = _rays()
    tester = fm._ClipTester(V, T, tmax=14.0)
    ri, ti = tester._pairs(O)
    ref, who = _brute(V, T, O, D, 14.0)
    have = set(zip(ri.tolist(), ti.tolist()))
    hits = np.flatnonzero(np.isfinite(ref))
    assert len(hits), "no ray hit anything; this fixture proves nothing"
    missing = [(int(r), int(who[r])) for r in hits
               if (int(r), int(who[r])) not in have]
    assert not missing, (
        f"{len(missing)} ray(s) hit a triangle that candidate pruning never "
        f"offered, e.g. {missing[:3]}")


def test_tiers_cover_every_triangle():
    V, T = _mixed_mesh()
    tester = fm._ClipTester(V, T)
    covered = np.concatenate([sel for sel, _k, _r in tester.tiers()])
    assert np.array_equal(np.unique(covered), np.arange(len(T))), (
        "a triangle fell outside every tier and would never be tested")


def test_tiers_rebuild_when_the_garment_moves():
    """Stale tiers would test the OLD geometry -- a guard comparing before and
    after would then measure the same mesh twice and always report no change."""
    V, T = _mixed_mesh()
    tester = fm._ClipTester(V, T)
    _ = tester.tiers()
    tester.set_garment(V + np.array([0.0, 5.0, 0.0]))
    assert tester._tiers is None
    cent = tester.tiers()[0][1].data[0]
    assert np.isfinite(cent).all()


def test_empty_and_degenerate_inputs():
    V, T = _mixed_mesh()
    tester = fm._ClipTester(V, T)
    ri, ti = tester._pairs(np.empty((0, 3)))
    assert len(ri) == 0 and len(ti) == 0
    far = np.array([[0.0, 900.0, 0.0]])
    ri, ti = tester._pairs(far)
    out = tester._cast(far, np.array([[0.0, 1.0, 0.0]]), ri, ti, 1)
    assert not np.isfinite(out[0])
