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

"""`fit_metrics.minimum_push` -- the conditionality is the whole safety story.

A pack census found only 4 of 72 judged pieces (6%) clipping above 1% at the
bust. A pass that fires unconditionally would therefore risk regressing 94% of
the output to fix 6%. The guarantee that makes this safe to enable is: a piece
with nothing exposed exits having moved ZERO vertices. These tests hold that
line with synthetic geometry (no modlist required, so they run in CI).

They also pin the two guards that were added only after each one caught a
real self-inflicted regression during development:
  * RIM MARGIN -- on the user's confirmed-CLEAN armour, 35 of 35 flagged
    underside verts sat within 1.33u of a hem. Without the guard the pass moved
    37 verts on a piece with nothing wrong.
  * MAX REACH -- skin whose nearest garment vertex is 4.6-5.4u away is not
    pierced, it is past the edge of where fabric reaches. Pushing there fixed
    0 of 49 and created 73 NEW pokes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _grid(z_center, n=24, half=9.0, y=0.0):
    """A flat patch in the bust band whose faces point +y, same as its normals.

    WINDING MATTERS AND GOT THIS WRONG ONCE. The first version of this helper
    wound triangles so the face normals came out at -y while the vertex normals
    said +y. The orientation gate then (correctly) rejected every synthetic
    clipping case, all three positive tests SKIPPED, and the suite passed while
    exercising only the negative controls -- a check that measured nothing.
    A real garment's outward face points the same way as the skin it covers, so
    face and vertex normals must agree here too.
    """
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


def test_synthetic_geometry_actually_produces_clipping():
    """Guards the tests themselves. If this fails, every positive case below is
    vacuous and the suite is only proving the pass does nothing."""
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-0.5)
    tester = fm._ClipTester(gV, gT)
    z = bV[:, 2]
    idx = np.flatnonzero((z >= fm.PUSH_Z_LO) & (z <= fm.PUSH_Z_HI))
    clip, _ = tester.clipping(bV, bN, idx)
    assert clip.any(), (
        "synthetic 'garment behind skin' produced NO measurable clipping -- "
        "check triangle winding: face normals must agree with vertex normals")


def _body_and_garment(gap):
    """Body at y=0 facing +y; garment is the same patch offset +gap in y.

    Positive gap => garment in FRONT of the skin (correct, no clipping).
    Negative gap => garment BEHIND the skin (clipping).
    """
    bV, bT, bN = _grid(95.0)
    gV, gT, gN = _grid(95.0, y=gap)
    return bV, bT, bN, gV, gT, gN


def test_clean_geometry_moves_zero_verts():
    """THE load-bearing guarantee: nothing exposed -> nothing moved."""
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=+1.2)
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    assert st["moved"] == 0, f"pass fired on clean geometry: {st}"
    assert np.array_equal(out, gV), "clean input must come back untouched"


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_MIN_PUSH", "1")
    import importlib
    importlib.reload(fm)
    try:
        bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-0.5)
        out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
        assert st["skipped"] == "disabled" and st["moved"] == 0
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_MIN_PUSH", raising=False)
        importlib.reload(fm)


def test_simulated_verts_never_move():
    """Chain-driven positions are the rest pose the SMP solver starts from;
    displacing them is the documented jitter/launch failure class."""
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-0.4)
    chain = np.zeros(len(gV), bool)
    chain[::3] = True
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN, is_chain=chain)
    assert np.allclose(out[chain], gV[chain]), "a simulated vert moved"


def test_all_simulated_is_a_no_op():
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-0.4)
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN,
                              is_chain=np.ones(len(gV), bool))
    assert st["skipped"] == "all simulated" and np.array_equal(out, gV)


def test_never_moves_inward():
    """One-sided by design: pulling leather toward skin only creates clipping."""
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-0.4)
    out, _ = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    along = ((out - gV) * gN).sum(axis=1)
    assert (along >= -1e-6).all(), "a vert was pulled inward"


def test_push_is_bounded():
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-1.0)
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    assert st["max_push"] <= fm.PUSH_MAX_TOTAL + 1e-6


def test_never_returns_a_regression():
    """The measurement is the authority: a push that increased exposure must be
    discarded, not shipped. Without this the pass could make a piece worse and
    still report success."""
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=-0.4)
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    assert st["exposed_after"] <= st["exposed_before"], st


def test_degenerate_inputs_are_safe():
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=+1.0)
    for bad in ({"garment_tris": np.zeros((0, 3), np.int64)},
                {"garment_verts": gV[:2]}):
        kw = dict(garment_verts=gV, garment_tris=gT, garment_normals=gN,
                  body_verts=bV, body_tris=bT, body_normals=bN)
        kw.update(bad)
        out, st = fm.minimum_push(**kw)
        assert st["moved"] == 0 and st["skipped"]


def test_region_is_bust_only_not_whole_body():
    """A patch far below the bust band must be out of scope entirely -- the
    pass is not licensed to reshape the legs."""
    bV, bT, bN = _grid(40.0)                     # knees, not bust
    gV, gT, gN = _grid(40.0, y=-0.5)             # clipping, but out of region
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    assert st["moved"] == 0 and st["skipped"], st


def test_rim_margin_and_reach_are_configurable_but_default_conservative():
    assert fm.PUSH_RIM_MARGIN >= 1.5
    assert fm.PUSH_MAX_REACH <= 4.0
    assert fm.PUSH_REQ_CAP <= 2.0
    assert fm.PUSH_Z_LO >= fm.BAND_Z[0] - 6.0, (
        "band must not reach so far below the bust that it hits coverage-edge "
        "noise -- measured: at 8u below, garment is already 4.6-5.4u away")


@pytest.mark.parametrize("gap", [-0.2, -0.5, -0.9])
def test_reduces_exposure_when_there_is_real_clipping(gap):
    bV, bT, bN, gV, gT, gN = _body_and_garment(gap=gap)
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    if st["exposed_before"] == 0:
        pytest.skip("synthetic case produced no measurable exposure")
    assert st["exposed_after"] < st["exposed_before"], st
    assert st["moved"] > 0
