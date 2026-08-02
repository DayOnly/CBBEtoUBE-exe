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

"""The chain contract: diagnose at entry, verify at exit, roll back if worse.

Applied to the CHAIN rather than to each pass, because the per-pass version is
measurably wrong here: over 48 traced shapes only `conform` ever regressed bust
fit, all 5 of its regressions were recovered downstream, and 0 of 48 shapes
ended worse than they started. Reverting it would have blocked a correct pass
and biased every garment looser -- the over-inflation the user reported twice.

THE TEST THAT MATTERS is test_rolls_back_a_chain_that_ends_worse. On real output
this guard is expected never to fire, and a guard that never fires is
indistinguishable from a guard that cannot fire. Everything else here is
bookkeeping; that one is the reason to believe the bookkeeping means anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import fit_metrics as fm  # noqa: E402


def _grid(z_center=95.0, n=24, half=9.0, y=0.0):
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


def _scene():
    bV, _bT, bN = _grid()
    good, gT, _n = _grid(y=1.0)     # clear of the skin
    bad, _t, _n2 = _grid(y=-0.6)    # behind the skin: clipping
    return bV, bN, gT, good, bad


def test_arms_on_a_measurable_shape():
    """If this fails every assertion below is vacuous."""
    bV, bN, gT, _good, _bad = _scene()
    assert fm.ChainGuard(bV, bN, gT).armed


def test_does_not_arm_outside_the_measured_band():
    bV, _bT, bN = _grid(z_center=40.0)      # knees
    _gV, gT, _gN = _grid(z_center=40.0, y=1.0)
    assert not fm.ChainGuard(bV, bN, gT).armed


def test_rolls_back_a_chain_that_ends_worse():
    """THE NEGATIVE CONTROL. A chain that starts clean, passes through a good
    state, and ends clipping must ship the good state, not the final one."""
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    assert g.armed
    g.begin(good)
    g.checkpoint("warp", good)
    out, outcome = g.finish(bad)
    assert g.rolled_back_to is not None, (
        f"the guard did not fire on a chain that clearly regressed: {outcome}")
    assert np.array_equal(out, good), "rolled back to the wrong geometry"
    assert "ROLLED BACK" in outcome


def test_shipped_reports_what_was_actually_kept():
    """`final` is the REJECTED measurement when a rollback fires.

    Reading `final` on the first pack-wide run made 20 rolled-back shapes look
    like they shipped 174 exposed verts when they actually shipped 101, and
    made a run with zero regressions read as "20 shapes ended worse". Anything
    judging output quality must read `shipped`.
    """
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(good)
    g.checkpoint("warp", good)
    g.finish(bad)
    assert g.rolled_back_to is not None
    assert g.final > g.entry, "the rejected measurement should be the worse one"
    assert g.shipped <= g.entry, (
        f"shipped={g.shipped} must be the KEPT count, not the rejected "
        f"{g.final}")


def test_shipped_equals_final_when_nothing_rolled_back():
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(bad)
    g.checkpoint("warp", bad)
    g.finish(good)
    assert g.rolled_back_to is None and g.shipped == g.final


def test_keeps_a_chain_that_improves():
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(bad)
    g.checkpoint("warp", bad)
    out, outcome = g.finish(good)
    assert g.rolled_back_to is None and outcome.startswith("ok")
    assert np.array_equal(out, good)


def test_tolerates_an_intermediate_regression():
    """conform pulls IN by design and later passes push back out. The chain
    contract must not punish that -- this is precisely the case where per-pass
    reverting was wrong."""
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(good)
    g.checkpoint("conform", bad)      # a real, legitimate mid-chain regression
    out, outcome = g.finish(good)
    assert outcome.startswith("ok"), outcome
    assert np.array_equal(out, good)
    assert g.rolled_back_to is None


def test_entry_measurement_can_be_shared():
    """Turning the pass trace on must not double the measurement cost."""
    bV, bN, gT, good, _bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    assert g.begin(good, known=7) == 7 and g.entry == 7


def test_checkpoints_cost_no_measurements():
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    calls = []
    g.exposed = lambda v: (calls.append(1), 0)[1]
    g.begin(good, known=0)
    for i in range(8):
        g.checkpoint(f"p{i}", bad if i % 2 else good)
    assert calls == [], "a checkpoint measured; snapshots must be copies only"


def test_a_clean_chain_costs_exactly_two_measurements():
    bV, bN, gT, good, _bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    n = [0]
    real = g.exposed

    def counted(v):
        n[0] += 1
        return real(v)

    g.exposed = counted
    g.begin(good)
    for i in range(6):
        g.checkpoint(f"p{i}", good)
    g.finish(good)
    assert n[0] == 2, f"expected 2 measurements on the happy path, got {n[0]}"
    assert g.extra_measurements == 0


def test_snapshots_are_copies_not_views():
    """A view would mutate as later passes edit the array in place, so the
    'rollback' would restore the broken geometry it was meant to escape."""
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    v = good.copy()
    g.begin(v)
    g.checkpoint("warp", v)
    v[:] = bad                      # a later pass edits in place
    out, _o = g.finish(v)
    assert np.array_equal(out, good), "snapshot aliased the live array"


def test_checkpoint_cap_keeps_entry():
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT, max_checkpoints=4)
    g.begin(good)
    for i in range(12):
        g.checkpoint(f"p{i}", bad)
    assert g._snaps[0][0] == "entry", (
        "the entry snapshot was evicted; the chain lost its only known-good "
        "fallback")
    assert len(g._snaps) <= 4


def test_release_frees_snapshots():
    bV, bN, gT, good, _bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(good)
    g.checkpoint("warp", good)
    g.release()
    assert g._snaps == []


def test_disabled_guard_is_a_passthrough():
    bV, bN, gT, good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT, enabled=False)
    assert not g.armed
    out, outcome = g.finish(bad)
    assert outcome == "unarmed" and np.array_equal(out, bad)


def test_unrecoverable_regression_is_reported_not_hidden():
    """If every snapshot is bad there is nothing to roll back to. That must be
    stated loudly, not silently shipped as 'ok'."""
    bV, bN, gT, _good, bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(bad)
    worse = bad.copy()
    worse[:, 1] -= 0.4
    g.checkpoint("warp", worse)
    _out, outcome = g.finish(worse)
    assert "ok" in outcome or "REGRESSED" in outcome
    if "REGRESSED" in outcome:
        assert g.rolled_back_to is None


def test_none_geometry_never_raises():
    bV, bN, gT, good, _bad = _scene()
    g = fm.ChainGuard(bV, bN, gT)
    g.begin(good)
    g.checkpoint("warp", None)
    out, _o = g.finish(None)
    assert out is None
