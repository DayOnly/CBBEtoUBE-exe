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

"""Standoff recorded up the whole torso, not just the bust front.

The ceiling guards z 90-102. A gap reported in game sat at z 108-114, where no
pack-wide record has ever looked -- "within ceiling" was an accurate statement
about a region the user was not looking at. The under-bust has been an open lead
just as long with no numbers behind it.

Two properties carry these tests.

`test_sparse_path_agrees_with_the_calibrated_one` is the load-bearing one. The
bands use `_ClipTester` while the calibrated bust record uses `standoff()`, for
a real reason -- the dense formulation reached 15 GB measuring several bands on
one cuirass -- but a mixed implementation is only acceptable if the two agree.
If they drift, the new bands stop being comparable to the 1.15u anchor and the
numbers become quietly meaningless.

`test_bands_are_recorded_separately` pins the other one: merging the torso into
a single wider band would reproduce the aggregation error that made a nine-arm
bisect read identically across every arm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

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


def _scene(gap=1.0):
    """Body spanning every band; garment `gap` units in front of it."""
    bV, _bT, bN = _sheet(76.0, 116.0, n=40)
    gV, gT, _gN = _sheet(76.0, 116.0, n=40, y=gap)
    return bV, bN, gV, gT


def test_sparse_path_agrees_with_the_calibrated_one():
    """THE test. Bands use _ClipTester; the bust anchor uses standoff()."""
    bV, bN, gV, gT = _scene(gap=1.0)
    idx = fm.front_slab(bV, bN, 90.0, 102.0)
    assert len(idx) > fm.TRACE_MIN_HITS
    dense = fm.standoff(bV, bN, gV, gT, idx)
    tester = fm._ClipTester(gV, gT, tmax=fm.TMAX)
    sparse_med, hits = fm.slab_standoff(tester, bV, bN, idx)
    assert hits == len(dense), (
        f"hit COUNT differs: sparse {hits} vs dense {len(dense)} -- the two "
        f"paths are not measuring the same rays")
    assert abs(sparse_med - float(np.median(dense))) < 1e-6, (
        "the band median drifts from the calibrated implementation")


def test_bands_are_recorded_separately(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(fm, "_append", lambda p, r: written.append(r))
    bV, bN, gV, gT = _scene()
    out = fm.record_torso_bands(tmp_path / "a.nif", "shape", gV, gT, bV, bN)
    names = [r["band"] for r in out]
    assert names == [b[0] for b in fm.TORSO_BANDS], names
    assert len(written) == len(fm.TORSO_BANDS)
    for r in out:
        assert r["z_lo"] < r["z_hi"] and r["n"] > 0


def test_bands_cover_underbust_and_strap_line():
    """The two regions that motivated this, both outside the old band."""
    lo = min(b[1] for b in fm.TORSO_BANDS)
    hi = max(b[2] for b in fm.TORSO_BANDS)
    assert lo <= 80.0, "under-bust (the long-standing open lead) not covered"
    assert hi >= 114.0, "strap line (the reported gap) not covered"
    names = {b[0] for b in fm.TORSO_BANDS}
    assert {"underbust", "strap"} <= names


def test_bands_carry_no_verdict():
    """`over` belongs to the bust record alone -- it is the only band with a
    calibrated anchor. A garment legitimately stands further off at the strap
    line, so reusing that ceiling would manufacture failures."""
    bV, bN, gV, gT = _scene()
    for r in fm.record_torso_bands(Path("x/a.nif"), "s", gV, gT, bV, bN):
        assert "over" not in r


def test_a_band_the_garment_misses_is_omitted_not_zeroed(tmp_path,
                                                          monkeypatch):
    """A garment that does not reach a band must produce NO row there, rather
    than a 0.0u row that reads as 'perfectly fitted'."""
    monkeypatch.setattr(fm, "_append", lambda p, r: None)
    bV, _bT, bN = _sheet(76.0, 116.0, n=40)
    gV, gT, _n = _sheet(76.0, 88.0, n=26, y=1.0)      # under-bust only
    out = fm.record_torso_bands(tmp_path / "a.nif", "s", gV, gT, bV, bN)
    got = {r["band"] for r in out}
    assert "strap" not in got and "upperchest" not in got, got
    assert "underbust" in got


def test_it_measures_the_real_distance():
    bV, bN, gV, gT = _scene(gap=2.5)
    out = fm.record_torso_bands(Path("x/a.nif"), "s", gV, gT, bV, bN)
    for r in out:
        assert abs(r["median"] - 2.5) < 0.05, r


def test_never_raises_and_records_its_own_failure(monkeypatch):
    written = []
    monkeypatch.setattr(fm, "_append", lambda p, r: written.append(r))
    monkeypatch.setattr(fm, "front_slab",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    bV, bN, gV, gT = _scene()
    assert fm.record_torso_bands(Path("x/a.nif"), "s", gV, gT, bV, bN) == []
    assert any(r.get("kind") == "standoff_band_error" for r in written)


def test_disabled_audit_records_nothing(monkeypatch):
    monkeypatch.setattr(fm, "_enabled", lambda: False)
    bV, bN, gV, gT = _scene()
    assert fm.record_torso_bands(Path("x/a.nif"), "s", gV, gT, bV, bN) == []
