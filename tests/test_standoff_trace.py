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

"""Per-pass standoff trace: which pass pushed the garment off the body.

The clipping trace answers the opposite question and is blind to a gap. A gap
reported in game at the strap line survived a nine-arm kill-switch bisect that
moved it by 0.02u, so no toggleable pass owns it -- this exists to name the one
that does, from the snapshots the chain already keeps.

Two properties carry the tests. It must MEASURE MOVEMENT (a trace that reports
nothing when a pass moved the garment a full unit is worse than no trace, since
it reads as "no pass is responsible"), and it must be OFF by default and
measure-only, because it costs a measurement per slab per checkpoint.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _grid(z_lo, z_hi, n=20, half=9.0, y=0.0):
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
    """Body spanning every trace slab, garment 1u in front of it."""
    bV, _bT, bN = _grid(88.0, 116.0, n=30)
    gV, gT, _gN = _grid(88.0, 116.0, n=30, y=1.0)
    return bV, bN, gV, gT


def _guard(monkeypatch, on=True):
    monkeypatch.setattr(fm, "STANDOFF_TRACE", on)
    bV, bN, gV, gT = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    return g, gV


def test_front_slab_selects_only_that_band():
    bV, bN, _g, _t = _scene()
    idx = fm.front_slab(bV, bN, 108.0, 111.0)
    assert len(idx)
    assert bV[idx][:, 2].min() >= 108.0 and bV[idx][:, 2].max() < 111.0


def test_front_slab_rejects_back_facing_skin():
    bV, bN, _g, _t = _scene()
    flipped = -bN
    assert len(fm.front_slab(bV, flipped, 108.0, 111.0)) == 0


def test_slab_standoff_reads_the_real_distance():
    bV, bN, gV, gT = _scene()
    t = fm._ClipTester(gV, gT, tmax=fm.TRACE_TMAX)
    med, hits = fm.slab_standoff(t, bV, bN, fm.front_slab(bV, bN, 108.0, 111.0))
    assert hits > fm.TRACE_MIN_HITS
    assert abs(med - 1.0) < 0.05, f"garment is 1u away, measured {med}"


def test_too_few_hits_reports_nan_not_a_number():
    """A slab the garment does not reach must not contribute a fake reading."""
    bV, bN, gV, gT = _scene()
    t = fm._ClipTester(gV, gT, tmax=fm.TRACE_TMAX)
    med, hits = fm.slab_standoff(t, bV, bN, np.array([0, 1], dtype=np.int64))
    assert hits < fm.TRACE_MIN_HITS and not np.isfinite(med)


def test_trace_is_off_by_default(monkeypatch):
    g, gV = _guard(monkeypatch, on=False)
    g.begin(gV, known=0)
    g.checkpoint("warp", gV)
    assert g.trace_standoff(Path("x/meshes/y/a.nif"), "s", gV) == []


def test_trace_measures_a_pass_that_moved_the_garment(monkeypatch, tmp_path):
    """THE test. A pass that pushes the garment out 1u must show up as a
    +1u delta on that pass, or the trace cannot do its job."""
    monkeypatch.setattr(fm, "_enabled", lambda: False)   # don't write a sink
    g, gV = _guard(monkeypatch)
    assert g.armed
    g.begin(gV, known=0)
    g.checkpoint("warp", gV)
    pushed = gV + np.array([0.0, 1.0, 0.0])
    g.checkpoint("inflate", pushed)
    rows = g.trace_standoff(tmp_path / "meshes" / "a.nif", "shape", pushed)
    assert rows, "trace produced nothing -- it would read as 'no pass moved'"
    band = [r for r in rows if r["pass"] == "inflate"
            and r["delta"] is not None]
    assert band, "no delta recorded for the pass that moved the garment"
    assert all(abs(r["delta"] - 1.0) < 0.1 for r in band), band


def test_a_pass_that_moves_nothing_reports_zero_delta(monkeypatch, tmp_path):
    monkeypatch.setattr(fm, "_enabled", lambda: False)
    g, gV = _guard(monkeypatch)
    g.begin(gV, known=0)
    g.checkpoint("warp", gV)
    g.checkpoint("conform", gV.copy())
    rows = g.trace_standoff(tmp_path / "meshes" / "a.nif", "shape", gV)
    d = [r["delta"] for r in rows if r["pass"] == "conform"
         and r["delta"] is not None]
    assert d and all(abs(x) < 1e-6 for x in d), d


def test_trace_covers_every_slab_and_the_final_geometry(monkeypatch, tmp_path):
    monkeypatch.setattr(fm, "_enabled", lambda: False)
    g, gV = _guard(monkeypatch)
    g.begin(gV, known=0)
    g.checkpoint("warp", gV)
    rows = g.trace_standoff(tmp_path / "meshes" / "a.nif", "shape", gV)
    assert {(r["z_lo"], r["z_hi"]) for r in rows} == set(fm.TRACE_SLABS)
    assert "final" in {r["pass"] for r in rows}


def test_trace_never_changes_geometry(monkeypatch, tmp_path):
    monkeypatch.setattr(fm, "_enabled", lambda: False)
    g, gV = _guard(monkeypatch)
    g.begin(gV, known=0)
    g.checkpoint("warp", gV)
    before = gV.copy()
    g.trace_standoff(tmp_path / "meshes" / "a.nif", "shape", gV)
    assert np.array_equal(gV, before)


def test_unarmed_guard_traces_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(fm, "STANDOFF_TRACE", True)
    bV, _bT, bN = _grid(38.0, 46.0)          # knees: outside the band
    _gV, gT, _gN = _grid(38.0, 46.0, y=1.0)
    g = fm.ChainGuard(bV, bN, gT)
    assert not g.armed
    assert g.trace_standoff(tmp_path / "a.nif", "s", None) == []


def test_a_broken_trace_records_the_error(monkeypatch, tmp_path):
    """A measurement that FAILED must not look like one that found nothing."""
    monkeypatch.setattr(fm, "STANDOFF_TRACE", True)
    written = []
    monkeypatch.setattr(fm, "_append", lambda p, r: written.append(r))
    g, gV = _guard(monkeypatch)
    g.begin(gV, known=0)
    g.checkpoint("warp", gV)
    monkeypatch.setattr(fm, "front_slab",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    g.trace_standoff(tmp_path / "a.nif", "s", gV)
    assert any(r.get("kind") == "standoff_trace_error" for r in written)
