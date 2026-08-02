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

"""The hot-path gate: skip ray work for garments that cannot be hit.

Arming used to test only the BODY region size, which is a constant -- the UBE
bust band is 5249 verts against a floor of 50 -- so every phase-2 shape armed
and paid seven ray casts (two chain measurements, a standoff record, up to four
bands) to discover it was a belt.

THE RISK IS ENTIRELY ONE-SIDED. A gate that admits too much costs time. A gate
that skips a garment which WOULD have measured something stops the metric
silently, and a converter that measures nothing reports no defects -- the exact
failure this project keeps hitting. So the load-bearing test here is
`test_gate_never_skips_a_garment_that_would_measure_something`: across a sweep
of distances it asserts that whenever the gate says skip, the real ray cast
finds nothing. Not "usually" -- never.

`test_anchor_is_unmoved_by_the_sparse_path` guards the other half: the standoff
record moved off the dense implementation onto `_ClipTester`, and the 1.15u /
1.52u calibration depends on it reporting the same numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _sheet(z_lo, z_hi, n=30, half=9.0, y=0.0):
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


def _body():
    bV, _bT, bN = _sheet(86.0, 106.0, n=34)
    return bV, bN, fm.front_slab(bV, bN, 90.0, 102.0)


def test_gate_admits_a_garment_on_the_band():
    bV, bN, idx = _body()
    gV, _gT, _n = _sheet(90.0, 102.0, y=1.0)
    assert fm.garment_reaches(gV, bV, idx)


def test_gate_rejects_a_garment_far_away():
    bV, bN, idx = _body()
    gV, _gT, _n = _sheet(90.0, 102.0, y=400.0)
    assert not fm.garment_reaches(gV, bV, idx)


@pytest.mark.parametrize("dist", [0.5, 2.0, 6.0, 11.0, 13.0, 20.0, 60.0, 300.0])
def test_gate_never_skips_a_garment_that_would_measure_something(dist):
    """THE test. gate==False must imply the ray cast finds nothing."""
    bV, bN, idx = _body()
    gV, gT, _n = _sheet(90.0, 102.0, y=dist)
    admitted = fm.garment_reaches(gV, bV, idx)
    tester = fm._ClipTester(gV, gT, tmax=fm.TMAX)
    O, N = bV[idx], bN[idx]
    t = tester._cast(O, N, *tester._pairs(O), len(O))
    hits = int(np.isfinite(t).sum())
    if not admitted:
        assert hits == 0, (
            f"the gate skipped a garment at {dist}u that the ray cast finds "
            f"{hits} hits on -- measurements are being silently lost")


def test_gate_is_conservative_at_the_boundary():
    """Exactly at the ray reach it must admit, not skip."""
    bV, bN, idx = _body()
    gV, _gT, _n = _sheet(90.0, 102.0, y=fm.TMAX - 0.01)
    assert fm.garment_reaches(gV, bV, idx)


def test_gate_fails_open_on_junk():
    """If the gate itself breaks, measure anyway -- never drop silently."""
    bV, bN, idx = _body()
    assert fm.garment_reaches(object(), bV, idx) is True


def test_gate_rejects_empty_geometry():
    bV, bN, idx = _body()
    assert not fm.garment_reaches(np.zeros((0, 3)), bV, idx)
    assert not fm.garment_reaches(np.ones((5, 3)), bV, np.array([], dtype=int))


def test_anchor_is_unmoved_by_the_sparse_path(tmp_path, monkeypatch):
    """record_standoff moved off dense standoff() onto _ClipTester. The
    calibrated 1.15u / 1.52u anchor depends on the numbers not changing."""
    written = []
    monkeypatch.setattr(fm, "_append", lambda p, r: written.append(r))
    # The body must sit at y > BAND_Y_MIN or `band_index` selects nothing --
    # the calibrated mask is the bust FRONT, not any sheet at the right height.
    bV, _bT, bN = _sheet(86.0, 110.0, n=40, y=3.0)
    gV, gT, _n = _sheet(86.0, 110.0, n=40, y=4.25)
    assert len(fm.band_index(bV)) >= fm.MIN_HITS, "fixture misses the band"
    rec = fm.record_standoff(tmp_path / "a.nif", "s", gV, gT, bV, bN)
    assert rec is not None, "the gate or the sparse path dropped a real measurement"
    idx = fm.band_index(bV)
    dense = fm.standoff(bV, bN, gV, gT, idx)
    assert abs(rec["median"] - round(float(np.median(dense)), 3)) < 1e-3, (
        f"anchor moved: sparse {rec['median']} vs dense {np.median(dense)}")
    assert rec["n"] == len(dense)


def test_a_gated_shape_records_nothing_at_all(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(fm, "_append", lambda p, r: written.append(r))
    bV, _bT, bN = _sheet(86.0, 110.0, n=40)
    far, gT, _n = _sheet(86.0, 110.0, n=40, y=500.0)
    assert fm.record_standoff(tmp_path / "a.nif", "s", far, gT, bV, bN) is None
    assert fm.record_torso_bands(tmp_path / "a.nif", "s", far, gT, bV, bN) == []
    assert written == [], "a gated shape still wrote to the sink"


def test_chain_guard_disarms_out_of_band(monkeypatch):
    bV, _bT, bN = _sheet(86.0, 110.0, n=34)
    _gV, gT, _n = _sheet(86.0, 110.0, n=34, y=1.0)
    far, _t2, _n2 = _sheet(86.0, 110.0, n=34, y=800.0)
    g = fm.ChainGuard(bV, bN, gT)
    assert g.armed, "guard did not arm before begin() -- test is vacuous"
    assert g.begin(far) == -1
    assert not g.armed and g.outcome == "out of band"


def test_chain_guard_still_arms_on_the_band(monkeypatch):
    bV, _bT, bN = _sheet(86.0, 110.0, n=34)
    gV, gT, _n = _sheet(86.0, 110.0, n=34, y=1.0)
    g = fm.ChainGuard(bV, bN, gT)
    assert g.begin(gV) >= 0 and g.armed, (
        "the gate disarmed a garment that IS on the band -- this would silently "
        "remove rollback protection from real torso pieces")
