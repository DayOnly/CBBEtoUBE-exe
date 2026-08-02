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

"""The frame precondition: is this garment even in body space?

Twelve phase-2 passes compute against the body and every one of them assumes
the garment shares its coordinate frame. None asserted it, so when
`shape_body_offset` displaced a skinned shape by 40 units all twelve were
corrupted in silence -- and it took three months to find, because every pass
still ran, still reported success, and still produced a mesh.

This is the cheapest check in the chain and it is the one that would have
caught that immediately.

The enforcement side used to live here too, as per-pass FitGuard tests. It was
removed in favour of the chain contract (`tests/test_chain_guard.py`): the pass
trace showed that of ~10 corrective passes only `conform` ever regressed bust
fit, all 5 of its regressions were recovered downstream, and 0 of 48 shapes
ended worse than they started -- so reverting per pass cost measurements to
block a pass that was working as designed.
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
