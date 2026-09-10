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

"""#last-carrier-hold -- the 4-influence cap may not strand a bone.

A bone the AUTHOR held on ONE vertex is by construction the lightest influence
there, so "keep the largest four" evicts it and leaves it in the shape's bone
list with no weight at all: `#zeroweight-bone-desync`, the equip-CTD class.

BISECTED, not reasoned. On the piece that ships one (`Top_Wrap` /
`SkirtBBone02`, author weight 0.03467 on vertex 757 alone):

    defaults                          zero-weight 2   SkirtBBone02 live 0
    CBBE2UBE_NO_LEG_BEND_MATCH=1      zero-weight 0   SkirtBBone02 live 1
    CBBE2UBE_NO_FULL_WEIGHT_MATCH=1   zero-weight 2   (not the actor)

So the producer is a CAPPED site, `_match_rigid_leg_bend_to_body`, and the cap
is the eviction rather than something missing from it: the pass grafts
`NPC R Butt` onto that vertex at 0.04747, the row goes to five, and the author's
0.03467 is the one that loses.

THE RULE IS NARROW ON PURPOSE. Only a bone that is NEW to the row may be
unseated, which is how the record phrases the rule that works -- "Only refusing
the newcomer its slot saves the bone" -- and is `#family-weight-invariant`'s own
rule (incumbents keep their slots; newcomers take free ones) applied at the cap.
A wider form that reordered incumbents was tried first; its A/B looked like a
2771-row cascade, but two IDENTICAL pool-scale arms differ by ~1100 weight rows
on the same SMP collider shapes (the record's weight-tail nondeterminism), so
that number was noise and is NOT the argument. The argument is the rule."

The tests that carry the design are
`test_the_newcomer_yields_to_the_bone_it_would_strand` (the defect),
`test_two_incumbents_are_never_reordered` (the bound that makes it safe) and
`test_no_newcomers_means_byte_identical_behaviour` (the opt-in).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc  # noqa: E402
from src.nif_convert_weights import _cap_and_renormalise_rows  # noqa: E402


@pytest.fixture
def hold(monkeypatch):
    def _set(on):
        monkeypatch.setattr(nc, "LAST_CARRIER_HOLD", on)
    return _set


def _live(row):
    return {b: w for b, w in row.items() if w > nc._WRITE_MIN}


def test_the_newcomer_yields_to_the_bone_it_would_strand(hold):
    """The measured case, to the numbers. `SkirtBBone02` holds ONE vertex;
    `NPC R Butt` is a bone THIS PASS GRAFTS onto it."""
    hold(True)
    vw = [
        {"NPC Pelvis": 0.7912, "NPC R Thigh": 0.1014, "NPC Spine": 0.0728,
         "NPC R Butt": 0.0475, "SkirtBBone02": 0.0347},
        {"NPC Pelvis": 0.9, "NPC R Butt": 0.1},          # R Butt lives here too
    ]
    writable = {"NPC Pelvis", "NPC R Thigh", "NPC Spine", "NPC R Butt",
                "SkirtBBone02"}
    _cap_and_renormalise_rows(vw, len(vw), writable,
                              incumbents=[frozenset(vw[0]) - {"NPC R Butt"}, frozenset(vw[1])])
    assert "SkirtBBone02" in _live(vw[0]), (
        "the bone whose ONLY vertex this is must keep its slot")
    assert "NPC R Butt" not in _live(vw[0]), (
        "the newcomer survives on its other vertex, so it yields here")
    assert abs(sum(_live(vw[0]).values()) - 1.0) < 1e-9


def test_two_incumbents_are_never_reordered(hold):
    """THE BOUND. `D` is a last carrier and `A`..`C` are not, but none of them
    is a newcomer, so the row keeps the plain weight order. Reordering incumbents
    is what cascaded 2643 rows on a collider shape in the first attempt."""
    hold(True)
    vw = [
        {"Spine": 0.90, "A": 0.04, "B": 0.03, "C": 0.02, "D": 0.01},
        {"Spine": 1.0, "A": 1.0, "B": 1.0, "C": 1.0},   # only D is a last carrier
    ]
    _cap_and_renormalise_rows(vw, len(vw), {"Spine", "A", "B", "C", "D"},
                              incumbents=[frozenset(r) for r in vw])
    assert sorted(_live(vw[0])) == ["A", "B", "C", "Spine"], (
        "with no newcomer in the row, nothing may move")


def test_a_dominant_newcomer_is_still_unseated(hold):
    """The rule is about WHO IS NEW, not about how heavy. A graft that lands
    dominant still yields a slot rather than strand an authored bone -- and a
    graft is capped small by its own pass, so this is the safe direction."""
    hold(True)
    vw = [
        {"Graft": 0.90, "A": 0.04, "B": 0.03, "C": 0.02, "D": 0.01},
        {"Graft": 1.0, "A": 1.0, "B": 1.0, "C": 1.0},
    ]
    _cap_and_renormalise_rows(vw, len(vw), {"Graft", "A", "B", "C", "D"},
                              incumbents=[frozenset(vw[0]) - {"Graft"}, frozenset(vw[1])])
    assert sorted(_live(vw[0])) == ["A", "B", "C", "D"]


def test_a_bone_with_another_live_row_is_not_protected(hold):
    """No free lunch: the hold is for bones that would be STRANDED, not for
    every small influence."""
    hold(True)
    vw = [
        {"P": 0.7, "Q": 0.1, "R": 0.09, "S": 0.06, "T": 0.05},
        {"S": 0.5, "T": 0.5},                  # both survive elsewhere
    ]
    _cap_and_renormalise_rows(vw, len(vw), {"P", "Q", "R", "S", "T"},
                              incumbents=[frozenset(r) for r in vw])
    assert sorted(_live(vw[0])) == ["P", "Q", "R", "S"], (
        "with nothing to strand, the old weight order stands")


def test_the_kill_switch_restores_the_old_survivor_choice(hold):
    """`CBBE2UBE_NO_LAST_CARRIER_HOLD=1` must reproduce the previous behaviour
    exactly -- the claim the flag comment makes."""
    row = {"NPC Pelvis": 0.7912, "NPC R Thigh": 0.1014, "NPC Spine": 0.0728,
           "NPC R Butt": 0.0475, "SkirtBBone02": 0.0347}
    writable = set(row)
    hold(False)
    off = [dict(row), {"NPC Pelvis": 0.9, "NPC R Butt": 0.1}]
    _cap_and_renormalise_rows(
        off, len(off), writable,
        incumbents=[frozenset(off[0]) - {"NPC R Butt"}, frozenset(off[1])])
    assert sorted(_live(off[0])) == ["NPC Pelvis", "NPC R Butt", "NPC R Thigh",
                                     "NPC Spine"], "weight order, as before"
    assert "SkirtBBone02" not in _live(off[0])


def test_equal_weights_still_break_on_the_bone_name(hold):
    """#deterministic-set-iteration: symmetric bones tie EXACTLY, and which one
    survived used to depend on PYTHONHASHSEED. The hold must not reintroduce
    that -- neither of these is a last carrier, so the name decides."""
    for on in (False, True):
        hold(on)
        vw = [{"P": 0.7, "Q": 0.2, "L Breast02": 0.05, "R Breast02": 0.05,
               "Z": 0.001 + nc._WRITE_MIN},
              {"L Breast02": 0.5, "R Breast02": 0.3, "Z": 0.2}]
        _cap_and_renormalise_rows(vw, len(vw), set(vw[0]) | set(vw[1]),
                                  incumbents=[frozenset(r) for r in vw])
        assert sorted(_live(vw[0])) == ["L Breast02", "P", "Q", "R Breast02"], (
            f"hold={on}: the name must break the tie, not the hash seed")


def test_an_evicted_bone_becomes_a_last_carrier_for_its_remaining_row(hold):
    """The carrier count is DECREMENTED as rows are capped. A bone with two live
    rows that loses one is down to its last, and must be held there -- otherwise
    the fix leaks exactly the bones it exists to keep."""
    hold(True)
    vw = [
        # `S` is NEW to both of X's rows -- it is what pushes X out. X starts
        # with two rows, so it is not held on the first one and is evicted;
        # that leaves row 1 as its last, where the hold must bite.
        {"P": 0.7, "Q": 0.1, "R": 0.09, "S": 0.08, "X": 0.03},
        {"P": 0.7, "Q": 0.1, "R": 0.09, "S": 0.08, "X": 0.03},
        {"P": 1.0}, {"Q": 1.0}, {"R": 1.0}, {"S": 1.0},
    ]
    incumbents = [frozenset(r) - {"S"} for r in vw]
    _cap_and_renormalise_rows(vw, len(vw), {"P", "Q", "R", "S", "X"},
                              incumbents=incumbents)
    kept = ["X" in _live(vw[0]), "X" in _live(vw[1])]
    assert kept == [False, True], (
        "X is not held on the first row (it still has another) and IS held on "
        f"the second, which is its last -- got {kept}")


def test_a_row_that_does_not_overflow_is_untouched_by_the_hold(hold):
    hold(True)
    vw = [{"P": 0.6, "Q": 0.4}, {"P": 1.0}]
    before = [dict(r) for r in vw]
    _cap_and_renormalise_rows(vw, len(vw), {"P", "Q"})
    assert vw == before


def test_an_unweighted_row_is_left_alone_not_zeroed(hold):
    """An unweighted vertex skins to the origin, which is a visible spike -- the
    behaviour the docstring already promises, unchanged by the hold."""
    hold(True)
    vw = [{"P": 0.0}, {"P": 1.0}]
    _cap_and_renormalise_rows(vw, len(vw), {"P"})
    assert vw[0] == {"P": 0.0}


def test_rows_restricts_the_walk_but_not_the_carrier_count(hold):
    """A bone live on a vertex this pass never touched is NOT being stranded, so
    the count has to span the whole shape even when the walk does not."""
    hold(True)
    vw = [
        {"P": 0.7, "Q": 0.1, "R": 0.09, "S": 0.08, "X": 0.03},
        {"X": 1.0},                       # outside `rows`, but X lives here
    ]
    _cap_and_renormalise_rows(vw, len(vw), {"P", "Q", "R", "S", "X"}, rows=[0],
                              incumbents=[frozenset(r) for r in vw])
    assert "X" not in _live(vw[0]), (
        "X survives on row 1, so it must not take the contested slot on row 0")
    assert vw[1] == {"X": 1.0}, "a row outside `rows` is never rewritten"


def test_no_newcomers_means_byte_identical_behaviour(hold):
    """THE OPT-IN. A caller that names no newcomers gets the old function, so
    `_install_skin`'s cap and the jiggle transfer are untouched by this rule
    until someone deliberately opts them in."""
    hold(True)
    row = {"P": 0.7, "Q": 0.1, "R": 0.09, "S": 0.08, "X": 0.03}
    for kw in ({}, {"incumbents": None}, {"incumbents": []}):
        vw = [dict(row), {"P": 1.0}, {"Q": 1.0}, {"R": 1.0}, {"S": 1.0}]
        _cap_and_renormalise_rows(vw, len(vw), set(row), **kw)
        assert sorted(_live(vw[0])) == ["P", "Q", "R", "S"], kw
