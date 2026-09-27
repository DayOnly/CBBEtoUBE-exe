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

"""#popup-per-kind -- the end-of-run popup words a FAILED item by what failed.

Reviewed 2026-09-25 on 8c8d178: #one-tally put the Combined ESP's load-breaking
issues in the failures file, and the popup then said "3 item(s) failed to
convert ... Items marked FAILED did NOT convert this run -- their armor keeps
its previous state" over a plugin the run HAD built and found unsafe to load.
The words come from src/failure_summary.py, the same entries the run records
(src/auto_convert._record_failure)."""
import pytest

from src import auto_convert as ac
from src import failure_summary as fs


def _recorded(*calls):
    """The entries `_record_failure` writes for these calls, as the run does."""
    ac._RUN_FAILURES.clear()
    try:
        for args, kw in calls:
            ac._record_failure(*args, **kw)
        return [dict(e) for e in ac._RUN_FAILURES]
    finally:
        ac._RUN_FAILURES.clear()


_CTD = (("load-breaking plugin issue", "Combined ESP",
         "3 issue(s): the plugin is NOT safe to load", "[a] x"), {"count": 3})
_MESH = (("mesh failed", "ModA", "a.nif", "boom"), {})
_WARN = (("patch validator", "per-source patches", "2 warning(s)"),
         {"severity": "warning", "count": 2})


def test_a_plugin_that_is_unsafe_to_load_is_not_called_unconverted():
    entries = _recorded(_CTD)
    title, intro = fs.popup_title(entries), fs.popup_intro(entries)
    assert title == "3 problem(s) in what was written", title
    assert "did NOT convert" not in intro and "previous state" not in intro, intro
    assert "the Combined ESP was built, but it is NOT safe to load" in intro, intro
    assert "did not convert" not in fs.status_line(0, entries)


def test_each_kind_is_worded_by_itself_beside_a_real_conversion_failure():
    entries = _recorded(_MESH, _CTD, _WARN)
    title, intro = fs.popup_title(entries), fs.popup_intro(entries)
    assert title == ("1 item(s) failed to convert, 3 problem(s) in what was "
                     "written, 2 warning(s)"), title
    assert "Items marked FAILED did NOT convert this run" in intro, intro
    assert "Items marked FAILED: load-breaking plugin issue" in intro, intro
    assert "Everything else converted normally" not in intro, (
        "not true while a written file is broken")
    assert "1 item(s) did not convert" in fs.status_line(0, entries)


_OWN = {**fs.WRITTEN_BUT_BROKEN, **fs.NOT_WRITTEN}


@pytest.mark.parametrize("kind", sorted(_OWN))
def test_every_written_but_broken_kind_has_its_own_sentence(kind):
    entries = [{"kind": kind, "source": "s", "item": "i", "severity": "failure"}]
    intro = fs.popup_intro(entries)
    assert _OWN[kind] in intro and "did NOT convert" not in intro


# --- a merge that wrote nothing is not a problem in what was written ------------
# Reviewed on 0081b20: "merge failed" / "merge skipped" were counted as
# "problem(s) in what was written" although no Combined ESP was written.

_MERGE_FAILED = (("merge failed", "Combined ESP", "Combined.esp", "boom"), {})
_MERGE_SKIPPED = (("merge skipped", "Combined ESP", "Combined.esp",
                   "1 source(s) failed ESP generation"), {})


@pytest.mark.parametrize("call", [_MERGE_FAILED, _MERGE_SKIPPED],
                         ids=["merge-failed", "merge-skipped"])
def test_a_merge_that_wrote_nothing_is_said_as_such(call):
    entries = _recorded(call)
    title, status = fs.popup_title(entries), fs.status_line(0, entries)
    assert title == "no Combined ESP was built", title
    assert "what was written" not in title + status, (title, status)
    assert status.startswith("Done (exit 0), but no Combined ESP was built - "), status
    assert "no Combined ESP was built this run" in fs.popup_intro(entries)


def test_a_merge_beside_other_failures_keeps_each_count():
    entries = _recorded(_MESH, _CTD, _MERGE_FAILED, _WARN)
    assert fs.popup_title(entries) == (
        "1 item(s) failed to convert, 3 problem(s) in what was written, "
        "no Combined ESP was built, 2 warning(s)")
    both = _recorded(_CTD, _MERGE_SKIPPED)
    assert fs.status_line(0, both).startswith(
        "Done (exit 0), but 3 problem(s) in what was written and no Combined "
        "ESP was built - "), fs.status_line(0, both)


def test_a_conversion_failure_reads_as_it_always_did():
    """The control: nothing changes for what did not convert."""
    entries = _recorded(_MESH)
    assert fs.popup_title(entries) == "1 item(s) failed to convert"
    assert fs.popup_intro(entries).startswith(
        "Items marked FAILED did NOT convert this run — their armor keeps its "
        "previous state (or is invisible on UBE actors). Everything else "
        "converted normally.")
