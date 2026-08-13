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

"""The census report must be read from the CANDIDATE arm's point of view.

`inflate_census` takes arbitrary --arm-on/--arm-off, but its reporter was born
answering one question -- "does REMOVING inflate pay?" -- where OFF was the
proposal. That direction was baked into the arithmetic, not just the wording:
`band()` computed `off - on`, the inside-vert count compared `ioff > ion`, and
the worst-regression list sorted on `off - on`. Pointed at a census whose
candidate is the ON arm, it printed the wrong sign on every line and named the
pieces the proposal IMPROVED as its worst regressions.

A sign error reads as a perfectly confident report, so it needs a test rather
than care. Each case here asserts the verdict FLIPS with the candidate, and
the second half asserts it does not flip when it shouldn't.
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "scripts" / "analysis"))
import inflate_census_report as rep  # noqa: E402


def _run(on, off, higher_is_better, cand):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rep.band("metric", np.asarray(on, float), np.asarray(off, float),
                 higher_is_better=higher_is_better, cand=cand)
    return buf.getvalue()


# ON is smaller. For a lower-is-better metric that makes the ON arm the winner.
LOWER_ON = ([1.0, 1.0, 1.0], [2.0, 2.0, 2.0])


def test_lower_is_better_names_the_on_arm_when_on_wins():
    out = _run(*LOWER_ON, higher_is_better=False, cand="ON")
    assert "ON better" in out, out


def test_control_the_same_data_names_off_as_worse_from_offs_view():
    """NEGATIVE CONTROL. Identical inputs, opposite candidate: the verdict must
    invert. If it does not, `cand` is not driving the direction and the test
    above would pass however the arithmetic was written."""
    out = _run(*LOWER_ON, higher_is_better=False, cand="OFF")
    assert "OFF WORSE" in out, out


def test_higher_is_better_flips_with_the_candidate_too():
    # ON is larger, so ON wins a higher-is-better metric.
    higher_on = ([5.0, 5.0, 5.0], [1.0, 1.0, 1.0])
    assert "ON better" in _run(*higher_on, higher_is_better=True, cand="ON")
    assert "OFF WORSE" in _run(*higher_on, higher_is_better=True, cand="OFF")


def test_the_improve_percentage_is_also_candidate_relative():
    """The share of shapes that improve must be counted for the candidate, not
    just the arrow relabelled: 100% for the winner is 0% for the loser."""
    on = [1.0, 1.0, 1.0, 1.0]
    off = [2.0, 2.0, 2.0, 2.0]
    a = _run(on, off, higher_is_better=False, cand="ON")
    b = _run(on, off, higher_is_better=False, cand="OFF")
    assert "100.0% of shapes improve" in a, a
    assert "  0.0% of shapes improve" in b, b


def test_a_tie_is_not_reported_as_a_win_for_either_arm():
    """Identical arms: whatever it says, it must not claim one side is better
    while the other is WORSE -- a null has to read as a null."""
    tie = ([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    on = _run(*tie, higher_is_better=False, cand="ON")
    off = _run(*tie, higher_is_better=False, cand="OFF")
    assert "+0.0000" in on and "+0.0000" in off, (on, off)
    assert "0.0% of shapes improve" in on, on


def test_no_data_is_reported_as_no_data():
    """All-NaN input must say so rather than print a confident zero."""
    out = _run([np.nan, np.nan], [np.nan, np.nan],
               higher_is_better=False, cand="ON")
    assert "no data" in out, out


@pytest.mark.parametrize("cand", ["ON", "OFF"])
def test_the_arrow_always_names_the_candidate(cand):
    """Whichever way the result goes, the verdict word must be about the arm
    being proposed -- that is what makes the line readable at all."""
    out = _run(*LOWER_ON, higher_is_better=False, cand=cand)
    assert cand in out, out
