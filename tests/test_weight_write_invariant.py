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

"""#weight-write-invariant (P6 / audit MED-1).

TWO SUBJECTS, and only one of them ships:

* `plan_weight_writes` (most of this file) is called by NO converter pass. The
  retrofit of `_match_leg_motion_to_body` measured NEUTRAL and was reverted
  (DESIGN_P6 section 3b). Those tests pin the helper so it is trustworthy WHEN
  wired, and prove nothing about what the converter writes today.
* `_cap_and_renormalise_rows` (bottom of the file) IS wired, into
  `_match_rigid_leg_bend_to_body` -- the pass the checkpoint trace found
  introduces ALL the on-disk drift -- and is DEFAULT ON since 2026-07-26.

The invariant on real output is checked by `scripts/verify_weight_invariant.py`
against saved NIFs, which is the only place it can honestly be measured.

Save semantics, measured 2026-07-25 on a real shape (see DESIGN_P6):
  * the file never holds >4 influences -- the save truncates
  * it keeps the LARGEST 4 (a .05 and a .19 were dropped in favour of two .20s)
  * it does NOT renormalise -- that row came back summing 1.160
  * writing 0.0 DOES remove an influence (the merge can be defeated)

The last point is what makes a deterministic write possible at all: a weight
cannot be cleared by omission, but it can be cleared by stating it."""
import numpy as np

from src.weights import (SKYRIM_MAX_INFLUENCES, check_weight_invariant,
                         plan_weight_writes)

BONES = ["A", "B", "C", "D", "E", "F"]


def _plan(weights, had=None, rows=None):
    W = np.asarray(weights, dtype=np.float64)
    if had is None:
        had = W > 0
    if rows is None:
        rows = np.arange(W.shape[0])
    return plan_weight_writes(W, rows, BONES, np.asarray(had, dtype=bool))


def _row(plan, vert):
    """{bone: weight} actually written for `vert` (0.0 entries included)."""
    return {b: w for b, pairs in plan.items() for v, w in pairs if v == vert}


def test_row_is_pruned_to_four_and_renormalised():
    # 6 intended influences -> only 4 may ship, and they must sum to 1.
    plan = _plan([[0.30, 0.25, 0.20, 0.15, 0.06, 0.04]])
    row = _row(plan, 0)
    live = {b: w for b, w in row.items() if w > 0}
    assert len(live) == SKYRIM_MAX_INFLUENCES
    assert set(live) == {"A", "B", "C", "D"}          # the largest four
    assert abs(sum(live.values()) - 1.0) < 1e-9


def test_dropped_influence_the_vertex_HAD_is_written_as_zero():
    # E was carried before and is being dropped: omitting it would leave the
    # stale value behind (setShapeWeights merges), so it must be zeroed.
    plan = _plan([[0.30, 0.25, 0.20, 0.15, 0.06, 0.04]])
    row = _row(plan, 0)
    assert row["E"] == 0.0 and row["F"] == 0.0


def test_bone_the_vertex_never_had_is_not_written_at_all():
    # Nothing stale to clear -> no wasted write.
    had = [[True, True, True, True, False, False]]
    plan = _plan([[0.30, 0.25, 0.20, 0.15, 0.06, 0.04]], had=had)
    assert "E" not in plan and "F" not in plan


def test_small_grafted_weight_survives_instead_of_being_discarded():
    # THE POINT OF P6. A graft adds a small weight to a vert that already has 4.
    # Left to the save, keep-largest-4 discards the graft and the pass silently
    # does nothing. Here the pass decides: the smallest existing influence goes.
    plan = _plan([[0.50, 0.30, 0.15, 0.02, 0.03, 0.0]])
    row = _row(plan, 0)
    live = {b: w for b, w in row.items() if w > 0}
    assert "E" in live, "the grafted bone must survive"
    assert row["D"] == 0.0, "the least significant existing influence is dropped"
    assert abs(sum(live.values()) - 1.0) < 1e-9


def test_untouched_vertices_are_never_written():
    # Only `rows` may be written -- every other vert relies on merge semantics,
    # and zero-writing them would destroy weighting no pass intended to change.
    W = np.array([[0.5, 0.5, 0, 0, 0, 0],
                  [0.5, 0.5, 0, 0, 0, 0]])
    plan = plan_weight_writes(W, [0], BONES, W > 0)
    assert all(v == 0 for pairs in plan.values() for v, _ in pairs)


def test_row_with_no_weight_left_is_skipped_not_unskinned():
    # Writing zeros everywhere would skin the vertex to the origin -- a visible
    # spike, worse than the drift being fixed. Leave such a row alone.
    plan = _plan([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]], had=[[True] * 6])
    assert plan == {}


def test_already_valid_row_is_preserved_exactly():
    plan = _plan([[0.5, 0.3, 0.2, 0.0, 0.0, 0.0]])
    row = _row(plan, 0)
    assert abs(row["A"] - 0.5) < 1e-9
    assert abs(row["B"] - 0.3) < 1e-9
    assert abs(row["C"] - 0.2) < 1e-9


def test_empty_rows_is_a_noop():
    assert plan_weight_writes(np.zeros((3, 6)), [], BONES,
                              np.zeros((3, 6), bool)) == {}


# --- the checker -------------------------------------------------------------

class _FakeShape:
    def __init__(self, weights):
        self.bone_names = list(weights)
        self.bone_weights = weights


def test_checker_passes_a_clean_shape():
    n_over, n_bad, worst = check_weight_invariant(
        _FakeShape({"A": [(0, 0.6)], "B": [(0, 0.4)]}))
    assert (n_over, n_bad) == (0, 0) and worst < 1e-6


def test_checker_flags_the_measured_failure_modes():
    # 5 influences AND a 1.16 sum -- exactly what the save produced in the
    # 2026-07-25 experiment.
    shape = _FakeShape({b: [(0, w)] for b, w in
                        zip(BONES, [0.329, 0.431, 0.20, 0.20, 0.05])})
    n_over, n_bad, worst = check_weight_invariant(shape)
    assert n_over == 1
    assert n_bad == 1
    assert abs(worst - 0.21) < 0.01


# --- the shipped flag -------------------------------------------------------

def test_weight_invariant_is_on_by_default_with_an_off_switch(monkeypatch):
    """DEFAULT ON since 2026-07-26. `_match_rigid_leg_bend_to_body` introduces ALL
    the on-disk weight drift (0 bad-sum verts before it, 2016 after); capping to 4
    and renormalising takes a real cuirass 1966 -> 16 and a bandit body 181 -> 0,
    and REMOVES pre-existing zero-weight bones (3 -> 1) rather than adding any.

    The off-switch matters: this shifts row magnitudes that the conform constants
    were tuned against, so a bad in-game result must be revertible by reconverting
    alone -- no rebuild."""
    import importlib
    monkeypatch.delenv("CBBE2UBE_NO_WEIGHT_INVARIANT", raising=False)
    nc = importlib.reload(importlib.import_module("src.nif_convert"))
    try:
        assert nc.WEIGHT_INVARIANT_ENABLED is True
        monkeypatch.setenv("CBBE2UBE_NO_WEIGHT_INVARIANT", "1")
        nc = importlib.reload(importlib.import_module("src.nif_convert"))
        assert nc.WEIGHT_INVARIANT_ENABLED is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_WEIGHT_INVARIANT", raising=False)
        importlib.reload(importlib.import_module("src.nif_convert"))


def test_cap_helper_never_leaves_a_row_unweighted():
    """An unweighted vertex skins to the origin -- a visible spike. A row whose
    writable mass is already zero must be left ALONE, not zeroed."""
    from src.nif_convert import _cap_and_renormalise_rows
    vw = [{"A": 0.0, "B": 0.0}]
    _cap_and_renormalise_rows(vw, 1, {"A", "B"})
    assert vw[0] == {"A": 0.0, "B": 0.0}


def test_cap_helper_prunes_to_four_and_sums_to_one():
    from src.nif_convert import _cap_and_renormalise_rows
    vw = [{"A": 0.50, "B": 0.20, "C": 0.15, "D": 0.10, "E": 0.05}]
    _cap_and_renormalise_rows(vw, 1, set("ABCDE"))
    live = {b: w for b, w in vw[0].items() if w > 1e-4}
    assert len(live) == 4 and "E" not in live
    assert abs(sum(live.values()) - 1.0) < 1e-9


def test_cap_helper_ignores_bones_outside_the_writable_set():
    """An `unsafe` bone has no skin-to-bone xform; weighting one skins those verts
    to the origin, so the cap must neither keep nor renormalise against it."""
    from src.nif_convert import _cap_and_renormalise_rows
    vw = [{"A": 0.5, "B": 0.3, "UNSAFE": 0.9}]
    _cap_and_renormalise_rows(vw, 1, {"A", "B"})
    assert vw[0]["UNSAFE"] == 0.9, "left untouched, not capped or scaled"
    assert abs(vw[0]["A"] + vw[0]["B"] - 1.0) < 1e-9
