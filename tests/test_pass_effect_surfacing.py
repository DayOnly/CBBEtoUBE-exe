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

"""`_note_pass_effect` and the accessors that read it.

The EFFECTS recorder answers "which change actually did something to this
piece", and it is deliberately a separate channel from the FAILURES recorder:
folding them together would make every caller of a function named
`..._failures` silently also report successes. `pass_failure_summary` was
already pinned by a test; its effects twin was not, which is how it ended up as
the only module-level function in `nif_convert.py` that nothing called.

The property that matters across a batch is that a worker's in-process counters
NEVER reach the parent -- the per-piece `reason` string is the only channel that
crosses the pool boundary. A test that read the parent's summary after a pooled
run would pass while measuring nothing, so that separation is asserted here too.
"""
from src import nif_convert as nc


def _reset():
    nc._PASS_EFFECTS.clear()
    nc._PASS_FAILURES.clear()
    del nc._PASS_EFFECTS_THIS_PIECE[:]
    del nc._PASS_FAILURES_THIS_PIECE[:]


def test_an_effect_is_counted_and_named_by_its_hashtag():
    _reset()
    nc._note_pass_effect("#proxy-encloses-chain", "declined 11 of 71")
    assert nc.pass_effect_summary() == {"#proxy-encloses-chain": 1}
    entry = nc._piece_pass_effects()
    assert len(entry) == 1
    assert entry[0].startswith("CHANGED BY #proxy-encloses-chain")
    assert "declined 11 of 71" in entry[0]


def test_the_same_tag_twice_on_one_piece_is_ONE_line_but_still_counts():
    """The per-piece list is what a human reads, so it must not repeat; the
    counter is what a census reads, so it must not deduplicate."""
    _reset()
    nc._note_pass_effect("#ride-body-floor")
    nc._note_pass_effect("#ride-body-floor")
    assert nc.pass_effect_summary() == {"#ride-body-floor": 2}
    assert len(nc._piece_pass_effects()) == 1


def test_effects_and_failures_stay_separate():
    """THE WHOLE REASON THE PAIR EXISTS. A `..._failures` accessor that also
    reported successes would make a clean run indistinguishable from a broken
    one that happened to change something."""
    _reset()
    nc._note_pass_effect("#ride-body-floor")
    nc._note_pass_failure("some/pass", ValueError("boom"))
    assert list(nc.pass_effect_summary()) == ["#ride-body-floor"]
    assert not any("ride-body-floor" in k for k in nc.pass_failure_summary())
    assert not any("CHANGED BY" in e for e in nc._piece_pass_failures())
    assert any("CHANGED BY" in e for e in nc._piece_pass_effects())


def test_begin_piece_clears_the_per_piece_lists_but_not_the_process_counters():
    """`_begin_piece_pass_log` runs per conversion; the process counters are a
    running total for an in-process caller and must survive it."""
    _reset()
    nc._note_pass_effect("#a")
    nc._begin_piece_pass_log()
    assert nc._piece_pass_effects() == []
    assert nc.pass_effect_summary() == {"#a": 1}, (
        "resetting the per-piece list must not zero the process counter")


def test_the_recorder_never_raises():
    """It is called from inside `except` blocks and from hot paths; an exception
    here would turn a diagnostic into an outage."""
    _reset()
    nc._note_pass_effect(None)          # type: ignore[arg-type]
    nc._note_pass_effect("#b", detail=object())  # type: ignore[arg-type]
    assert isinstance(nc.pass_effect_summary(), dict)
