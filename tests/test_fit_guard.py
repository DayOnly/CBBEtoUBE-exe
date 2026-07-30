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

"""Frame precondition + diagnose/treat/verify guard.

Both exist because the chain was speculative: twelve phase-2 passes compute
against the body, all assume the garment is in body space, none asserted it, and
nothing between them measured whether a pass helped. A 40u frame error therefore
corrupted all twelve in silence, and an over-inflated mesh reached the user
twice because each pass capped only its own contribution.

The guard tests deliberately include an ARMING test. A guard that silently fails
to arm reverts nothing and reports nothing, which is indistinguishable from a
guard that armed and found everything fine -- the exact "clean result that
measured nothing" failure this project keeps hitting.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _grid(z_center, n=24, half=9.0, y=0.0):
    xs = np.linspace(-half, half, n)
    zs = np.linspace(z_center - 6.0, z_center + 6.0, n)
    X, Z = np.meshgrid(xs, zs)
    V = np.stack([X.ravel(), np.full(X.size, y), Z.ravel()], 1).astype(float)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    N = np.tile(np.array([0.0, 1.0, 0.0]), (len(V), 1))
    return V, np.asarray(tris, np.int64), N


# ----------------------------------------------------------- frame_report
def test_frame_offset_that_helps_is_chosen():
    bV, _bT, _bN = _grid(95.0)
    shifted = bV - np.array([0.0, 0.0, 60.0])
    r = fm.frame_report(shifted, [0, 0, 60], bV)
    assert r["chosen"] == "offset" and not r["corrected"]


def test_frame_offset_that_displaces_is_reported():
    """The real bug: verts already on the body, offset shoves them away."""
    bV, _bT, _bN = _grid(95.0)
    r = fm.frame_report(bV, [-40, 0, 0], bV)
    assert r["corrected"] is True, r
    assert r["chosen"] == "raw"
    assert r["offset_reach"] > r["raw_reach"]


def test_frame_zero_offset_is_not_flagged():
    bV, _bT, _bN = _grid(95.0)
    r = fm.frame_report(bV, [0, 0, 0], bV)
    assert not r["corrected"] and not r["suspect"]


def test_frame_far_shape_is_suspect_but_not_corrected():
    """Neither frame near the body. Not necessarily wrong -- a hat, a cape hem --
    but it is also what a genuine frame error looks like, so it is recorded."""
    bV, _bT, _bN = _grid(95.0)
    far = bV + np.array([0.0, 0.0, 400.0])
    r = fm.frame_report(far, [0, 0, 1], bV)
    assert r["suspect"] is True


def test_frame_report_never_raises_on_junk():
    bV, _bT, _bN = _grid(95.0)
    for bad in (None, np.zeros((0, 3)), np.zeros(3)):
        r = fm.frame_report(bad, [1, 0, 0], bV)
        assert isinstance(r, dict)


# --------------------------------------------------------------- FitGuard
def test_guard_arms_on_a_measurable_shape():
    """If this fails every other guard test below is vacuous."""
    bV, bT, bN = _grid(95.0)
    gV, gT, _gN = _grid(95.0, y=1.0)
    g = fm.FitGuard(bV, bN, gT)
    assert g.armed, "guard failed to arm on a shape the metric can see"


def test_guard_does_not_arm_without_enough_region():
    bV, bT, bN = _grid(40.0)          # knees: outside the measured band
    _gV, gT, _gN = _grid(40.0, y=1.0)
    assert not fm.FitGuard(bV, bN, gT).armed


def test_guard_reverts_a_regression():
    bV, bT, bN = _grid(95.0)
    gV, gT, _gN = _grid(95.0, y=1.0)      # in front of skin: fine
    worse, _t, _n = _grid(95.0, n=24, y=-0.6)   # behind skin: clipping
    g = fm.FitGuard(bV, bN, gT)
    assert g.armed
    out, verdict = g.guard("synthetic", gV, worse)
    assert verdict.startswith("reverted"), verdict
    assert np.array_equal(out, gV), "a regressing pass must be rolled back"


def test_guard_keeps_an_improvement():
    bV, bT, bN = _grid(95.0)
    bad, gT, _n = _grid(95.0, y=-0.6)     # clipping
    good, _t2, _n2 = _grid(95.0, y=1.0)   # fixed
    g = fm.FitGuard(bV, bN, gT)
    out, verdict = g.guard("synthetic", bad, good)
    assert verdict.startswith("kept"), verdict
    assert np.array_equal(out, good)


def test_guard_keeps_a_neutral_change():
    bV, bT, bN = _grid(95.0)
    a, gT, _n = _grid(95.0, y=1.0)
    b = a + np.array([0.0, 0.05, 0.0])
    g = fm.FitGuard(bV, bN, gT)
    out, verdict = g.guard("synthetic", a, b)
    assert verdict.startswith("kept") and np.array_equal(out, b)


def test_unarmed_guard_is_a_passthrough():
    bV, bT, bN = _grid(40.0)
    _gV, gT, _gN = _grid(40.0, y=1.0)
    g = fm.FitGuard(bV, bN, gT)
    a, b = np.zeros((4, 3)), np.ones((4, 3))
    out, verdict = g.guard("x", a, b)
    assert verdict == "unguarded" and np.array_equal(out, b), (
        "an unarmed guard must not silently alter geometry")


def test_guard_logs_every_decision():
    bV, bT, bN = _grid(95.0)
    good, gT, _n = _grid(95.0, y=1.0)
    bad, _t, _n2 = _grid(95.0, y=-0.6)
    g = fm.FitGuard(bV, bN, gT)
    g.guard("p1", good, bad)
    g.guard("p2", bad, good)
    assert [e[0] for e in g.log] == ["p1", "p2"]
    assert g.log[0][3] == "REVERTED" and g.log[1][3] == "kept"
