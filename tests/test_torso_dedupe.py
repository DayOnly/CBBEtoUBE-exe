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

"""One cast for the standoff record and all four torso bands.

They run on the same geometry, at the same moment, with the same `tmax`, over
ray sets that overlap heavily -- the calibrated bust mask and the `bust` slab
cover largely the same skin. That was five casts of one garment, and a
full-band cast on a dense garment measures ~11 s.

THE ONLY THING THAT MATTERS is that deduplication does not change a single
recorded number. The calibrated bust record carries the 1.15u / 1.52u anchor
that every over-inflation verdict is measured against; if slicing a union
shifted it even slightly, every historical comparison would silently break. So
these tests compare the shared-cast path against the per-consumer path
record-for-record rather than checking it "looks right".
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _sheet(z_lo, z_hi, n=34, half=9.0, y=0.0):
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


def _scene(gap=1.3):
    """Body at y>BAND_Y_MIN so the calibrated bust mask selects it."""
    bV, _bT, bN = _sheet(76.0, 116.0, n=44, y=3.0)
    gV, gT, _n = _sheet(76.0, 116.0, n=44, y=3.0 + gap)
    return bV, bN, gV, gT


def test_the_union_covers_every_consumer_ray_set():
    bV, bN, gV, gT = _scene()
    c = fm._TorsoCast(gV, gT, bV, bN)
    assert c.ok
    every = [fm.band_index(bV)] + [fm.front_slab(bV, bN, lo, hi)
                                   for _n, lo, hi in fm.TORSO_BANDS]
    for s in every:
        assert np.isin(s, c.all).all(), "a consumer's rays are not in the union"


def test_standoff_record_is_identical_with_and_without_the_shared_cast(
        tmp_path, monkeypatch):
    """The calibrated anchor must not move."""
    monkeypatch.setattr(fm, "_append", lambda p, r: None)
    bV, bN, gV, gT = _scene()
    solo = fm.record_standoff(tmp_path / "a.nif", "s", gV, gT, bV, bN)
    shared = fm.record_standoff(tmp_path / "a.nif", "s", gV, gT, bV, bN,
                                cast=fm._TorsoCast(gV, gT, bV, bN))
    assert solo is not None and shared is not None
    for k in ("n", "covered_pct", "median", "p90", "max", "over"):
        assert solo[k] == shared[k], f"{k}: {solo[k]} != {shared[k]}"


def test_band_records_are_identical_with_and_without_the_shared_cast(
        tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_append", lambda p, r: None)
    bV, bN, gV, gT = _scene()
    solo = fm.record_torso_bands(tmp_path / "a.nif", "s", gV, gT, bV, bN)
    shared = fm.record_torso_bands(tmp_path / "a.nif", "s", gV, gT, bV, bN,
                                   cast=fm._TorsoCast(gV, gT, bV, bN))
    assert solo, "the per-consumer path recorded nothing; test is vacuous"
    assert len(solo) == len(shared)
    for a, b in zip(solo, shared):
        assert a["band"] == b["band"]
        for k in ("n", "median", "p90", "max", "covered_pct"):
            assert a[k] == b[k], f"{a['band']}.{k}: {a[k]} != {b[k]}"


def test_the_shared_cast_casts_once(monkeypatch):
    """The whole point. Count the casts, not the wall clock."""
    bV, bN, gV, gT = _scene()
    calls = []
    real = fm._ClipTester._cast

    def spy(self, O, D, ray_i, tri_i, n_rays, want_tri=False):
        calls.append(n_rays)
        return real(self, O, D, ray_i, tri_i, n_rays, want_tri)

    monkeypatch.setattr(fm._ClipTester, "_cast", spy)
    monkeypatch.setattr(fm, "_append", lambda p, r: None)

    calls.clear()
    fm.record_standoff(Path("x/a.nif"), "s", gV, gT, bV, bN)
    fm.record_torso_bands(Path("x/a.nif"), "s", gV, gT, bV, bN)
    separate = len(calls)

    calls.clear()
    c = fm._TorsoCast(gV, gT, bV, bN)
    fm.record_standoff(Path("x/a.nif"), "s", gV, gT, bV, bN, cast=c)
    fm.record_torso_bands(Path("x/a.nif"), "s", gV, gT, bV, bN, cast=c)
    shared = len(calls)
    assert shared < separate, (
        f"dedupe did not reduce casts ({shared} vs {separate})")


def test_a_far_garment_short_circuits():
    bV, bN, _g, gT = _scene()
    far, _t, _n = _sheet(76.0, 116.0, n=44, y=900.0)
    c = fm._TorsoCast(far, gT, bV, bN)
    assert not c.ok and len(c.hits(fm.band_index(bV))) == 0


def test_hits_of_an_empty_index_is_empty():
    bV, bN, gV, gT = _scene()
    c = fm._TorsoCast(gV, gT, bV, bN)
    assert len(c.hits(np.array([], dtype=np.int64))) == 0
    assert len(c.hits(None)) == 0


def test_a_broken_cast_degrades_to_not_ok():
    bV, bN, gV, gT = _scene()
    c = fm._TorsoCast(gV, "not tris", bV, bN)
    assert not c.ok


def test_consumers_still_work_when_the_cast_is_unusable(tmp_path, monkeypatch):
    """A failed shared cast must not silently zero the records -- the consumer
    should report nothing rather than a fabricated clean result."""
    monkeypatch.setattr(fm, "_append", lambda p, r: None)
    bV, bN, gV, gT = _scene()
    dead = fm._TorsoCast(gV, "not tris", bV, bN)
    assert fm.record_standoff(tmp_path / "a.nif", "s", gV, gT, bV, bN,
                              cast=dead) is None
    assert fm.record_torso_bands(tmp_path / "a.nif", "s", gV, gT, bV, bN,
                                 cast=dead) == []
