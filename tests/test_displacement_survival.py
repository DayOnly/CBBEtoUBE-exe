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

"""Per-pass displacement survival: did a later pass undo this one?

Written against the failure it exists to catch. The chain anti-poke moved the
hip band correctly; `_physics_chain_nowarp_blend` runs four passes later and
pins chain verts back to source by design; the two together produced a
bit-identical 7.30% at push 1.0 and at push 2.0. Every metric in the project
read that as "the pass does not work" rather than "the pass is cancelled", and
an offline probe with nothing running after it overstated the fix seven times.

THE NEGATIVE CONTROL IS THE LOAD-BEARING TEST HERE. A survival number that
reads 0 for a genuinely cancelled pass proves nothing on its own -- an
implementation that returns 0 unconditionally passes that. The uncancelled
case must read 1.0, or the tool is measuring nothing and the clean result is
worthless. Same for `frac_cancelled`: the mixed case must separate.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.fit_metrics as fm  # noqa: E402


def _grid(n=40):
    """n verts on a line -- positions are irrelevant, only displacement is."""
    v = np.zeros((n, 3))
    v[:, 0] = np.arange(n, dtype=float)
    return v


def _run(steps, n=40):
    """Feed a sequence of (label, verts) through the tracer, return its rows.

    The last snapshot doubles as the shipped geometry, which is the normal
    case; the rollback case is exercised separately.
    """
    d = fm.DisplacementSurvival(enabled=True)
    for label, v in steps:
        d.checkpoint(label, v)
    return d.analyse(steps[-1][1])


def _row(rows, name):
    for r in rows:
        if r["pass"] == name:
            return r
    raise AssertionError(f"no row for {name!r} in {[r['pass'] for r in rows]}")


# --------------------------------------------------------------- the controls

def test_negative_control_an_untouched_pass_reads_full_survival():
    """NOTHING runs after the pass -> survival must be 1.0.

    If this reads low, every "CANCELLED" verdict the tool produces is noise and
    the cancellation tests below are measuring nothing.
    """
    v0 = _grid()
    v1 = v0.copy()
    v1[:, 1] += 2.0
    rows = _run([("entry", v0), ("antipoke", v1)])
    assert rows, "measured nothing -- an empty result is not a clean result"
    r = _row(rows, "antipoke")
    assert np.isclose(r["survival"], 1.0), r
    assert r["frac_cancelled"] == 0.0, r
    assert r["frac_kept"] == 1.0, r
    assert "verdict" not in r, r
    assert "cancelled_by" not in r, r


def test_a_pass_undone_by_a_later_one_reads_zero_and_names_it():
    """The chain_blend shape: pass moves, later pass restores source exactly."""
    v0 = _grid()
    v1 = v0.copy()
    v1[:, 1] += 2.0
    v2 = v1.copy()
    v2[:, 1] += 0.5              # an unrelated pass in between
    v3 = v2.copy()
    v3[:, 1] = v0[:, 1]          # chain_blend: back to source
    rows = _run([("entry", v0), ("antipoke", v1),
                 ("softcloth", v2), ("chain_blend", v3)])
    assert rows
    r = _row(rows, "antipoke")
    assert np.isclose(r["survival"], 0.0, atol=1e-9), r
    assert r["verdict"] == "CANCELLED", r
    assert r["cancelled_by"] == "chain_blend", r
    assert np.isclose(r["cancelled_frac"], -1.25), r
    assert r["frac_cancelled"] == 1.0, r


def test_a_partly_pinned_pass_is_not_hidden_by_the_aggregate():
    """The real shape of the bug, and why one number is not enough.

    Half the verts a pass moves are chain-weighted and get pinned back; half are
    free and keep the motion. The aggregate reads a healthy 0.5 -- which is
    exactly how a fully-dead subpopulation stayed invisible. `frac_cancelled`
    is the number that must not average it away.
    """
    v0 = _grid(40)
    v1 = v0.copy()
    v1[:, 1] += 2.0              # pass moves ALL 40
    v2 = v1.copy()
    v2[:20, 1] = v0[:20, 1]      # 20 of them pinned back to source
    rows = _run([("entry", v0), ("antipoke", v1), ("chain_blend", v2)])
    r = _row(rows, "antipoke")
    assert np.isclose(r["survival"], 0.5), r
    assert "verdict" not in r, "0.5 is not a cancelled aggregate"
    assert np.isclose(r["frac_cancelled"], 0.5), r
    assert np.isclose(r["frac_kept"], 0.5), r
    assert r["cancelled_by"] == "chain_blend", r


# ------------------------------------------------------------- the arithmetic

def test_overshoot_reads_negative_and_amplification_reads_above_one():
    """The scale is signed and unbounded on purpose -- both are real outcomes
    and clamping either to [0,1] would erase the distinction between 'undone'
    and 'undone then pushed the other way'."""
    v0 = _grid()
    over = v0.copy()
    over[:, 1] += 1.0
    back = over.copy()
    back[:, 1] -= 3.0            # past the starting point
    assert _row(_run([("entry", v0), ("p", over), ("q", back)]),
                "p")["survival"] < 0

    more = over.copy()
    more[:, 1] += 1.0            # further the same way
    assert _row(_run([("entry", v0), ("p", over), ("q", more)]),
                "p")["survival"] > 1


def test_perpendicular_motion_is_not_cancellation():
    """A later pass moving on a different axis neither adds nor subtracts.

    Deliberate: the question is whether this pass's contribution is still
    present, not whether the vertex is still where this pass put it. Counting
    an orthogonal push as cancellation would flag most of the chain.
    """
    v0 = _grid()
    v1 = v0.copy()
    v1[:, 1] += 2.0
    v2 = v1.copy()
    v2[:, 2] += 5.0              # orthogonal, and much larger
    r = _row(_run([("entry", v0), ("p", v1), ("q", v2)]), "p")
    assert np.isclose(r["survival"], 1.0), r
    assert "cancelled_by" not in r, r


def test_attribution_sums_to_survival():
    """1 + (contributions of every later pass) == survival, exactly.

    The displacements telescope, so this is an identity rather than an
    approximation -- if it fails, the attribution is describing a different
    quantity from the headline number and one of them is lying.
    """
    rng = np.random.default_rng(0)
    v0 = _grid(30)
    steps = [("entry", v0)]
    cur = v0
    for i in range(5):
        cur = cur + rng.normal(scale=0.5, size=cur.shape)
        steps.append((f"p{i}", cur))
    d = fm.DisplacementSurvival(enabled=True)
    for label, v in steps:
        d.checkpoint(label, v)
    rows = d.analyse(steps[-1][1])
    assert len(rows) == 5
    # Re-derive the contributions the same way the class does and check the sum
    # for the FIRST pass, which has the longest tail of later passes.
    D = steps[1][1] - steps[0][1]
    denom = float(np.einsum("ij,ij->i", D, D).sum())
    total = 1.0 + sum(
        float(np.einsum("ij,ij->i", steps[m][1] - steps[m - 1][1], D).sum()
              / denom)
        for m in range(2, len(steps)))
    assert np.isclose(_row(rows, "p0")["survival"], total, atol=1e-6)


# ---------------------------------------------------------------- the honesty

def test_the_shipped_geometry_wins_over_the_last_checkpoint():
    """A chain rollback discards work, and that counts as not surviving."""
    v0 = _grid()
    v1 = v0.copy()
    v1[:, 1] += 2.0
    d = fm.DisplacementSurvival(enabled=True)
    d.checkpoint("entry", v0)
    d.checkpoint("antipoke", v1)
    rows = d.analyse(v0)                     # rolled back to entry
    r = _row(rows, "antipoke")
    assert np.isclose(r["survival"], 0.0), r
    assert r["cancelled_by"] == "(after last pass)", r


def test_motion_too_small_to_judge_is_flagged_not_reported_as_a_finding():
    """From the first real run: `bake_preset survival 46.9` off 8 verts moved a
    mean of 0.014u. Arithmetically right, meaningless as a result -- the ratio
    was measuring the passes that came after, not this one."""
    v0 = _grid()
    tiny = v0.copy()
    tiny[:, 1] += 0.014                  # under the floor
    big = tiny.copy()
    big[:, 1] += 0.66                    # later passes dwarf it, same direction
    r = _row(_run([("entry", v0), ("bake_preset", tiny), ("conform", big)]),
             "bake_preset")
    assert r["survival"] > 10, "the unstable ratio is still reported verbatim"
    assert r["low_signal"] is True, r
    assert "verdict" not in r, "no verdict on motion too small to judge"

    # ... and a pass that moved a REAL distance is not flagged, or the guard
    # would suppress every finding along with the noise.
    real = v0.copy()
    real[:, 1] += 0.5
    back = real.copy()
    back[:, 1] -= 0.5
    r2 = _row(_run([("entry", v0), ("antipoke", real), ("blend", back)]),
              "antipoke")
    assert "low_signal" not in r2, r2
    assert r2["verdict"] == "CANCELLED", r2


def test_a_pass_that_moved_nothing_says_so_rather_than_scoring_zero():
    """0/0 is not 'cancelled'. Reporting it as one would put a verdict on
    every skipped pass in the chain."""
    v0 = _grid()
    r = _row(_run([("entry", v0), ("noop", v0.copy())]), "noop")
    assert r["moved_verts"] == 0, r
    assert "survival" not in r, r
    assert r["note"] == "pass moved nothing"


def test_a_vert_count_change_is_recorded_not_swallowed():
    d = fm.DisplacementSurvival(enabled=True)
    d.checkpoint("entry", _grid(40))
    d.checkpoint("resample", _grid(30))
    rows = d.analyse(_grid(30))
    assert rows and "skipped" in rows[0], rows


def test_overflow_keeps_the_prefix_contiguous_and_admits_it():
    """Dropping a MIDDLE snapshot would merge two passes under one label and
    attribute motion to the wrong one -- worse than stopping early and saying
    so. Survival stays exact either way; only attribution is affected."""
    d = fm.DisplacementSurvival(enabled=True, max_passes=3)
    v = _grid()
    for i in range(6):
        v = v + np.array([0.0, 1.0, 0.0])
        d.checkpoint(f"p{i}", v)
    assert d.dropped == 3
    rows = d.analyse(v)
    assert [r["pass"] for r in rows][:2] == ["p1", "p2"]
    assert all(r.get("attrib_complete") is False for r in rows if "survival" in r)


def test_it_arms_without_a_body_or_a_region():
    """The two existing harnesses arm only where the bust metric can see the
    shape. The pass this was written for acts on the HIP, so a body-gated
    version of it would have stayed blind to the bug it exists to find."""
    import inspect
    sig = inspect.signature(fm.DisplacementSurvival.__init__)
    assert list(sig.parameters) == ["self", "enabled", "max_passes"]


def test_default_is_off():
    assert fm.SURVIVAL_TRACE is False
    assert fm.DisplacementSurvival().armed is False
    # and a disabled tracer must not quietly return a clean-looking result
    d = fm.DisplacementSurvival(enabled=False)
    d.checkpoint("entry", _grid())
    d.checkpoint("p", _grid() + 1.0)
    assert d.analyse(_grid()) == []
