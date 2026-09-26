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

r"""#sweep-put-back-wording -- a sweep put-back that failed is not worded as a
conversion that did not happen.

THE DEFECT. When the stale-output sweep's moves must go back and a file
cannot (its path is taken again, or it is locked), the run records the kind
"stale sweep put back failed" as a failure. The popup and the status line had
no sentence of their own for it, so they said the item "did NOT convert this
run -- their armor keeps its previous state". What happened is the opposite:
the run converted, and a mesh a plugin may name sits in `_superseded\`, the
missing-mesh crash the log's own warning names.

THE RULE. The kind has its own sentence (src/failure_summary.py
MOVED_NOT_PUT_BACK) and its own words in the title and the status line. It
still counts as a failure in the one tally. Words only: no switch.
"""
import pytest

from src import auto_convert as ac
from src import failure_summary as fs
from tests.test_stale_output_sweep import OLD, WHOLE, World, _finish, _sweep, _vanilla
# The autouse fixture of the sweep's own tests: clean switches and caches.
from tests.test_stale_output_sweep import _clean  # noqa: F401

KIND = "stale sweep put back failed"
_MESH = {"kind": "mesh failed", "source": "ModA", "item": "a.nif", "severity": "failure"}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])


@pytest.fixture
def stuck(tmp_path):
    """A full run moved OLD; the merge did not confirm it; one of its files
    cannot go back because its path is taken again. Returns what the run
    recorded."""
    w = World(tmp_path)
    w.mesh("armor/iron/cuirass", "_0.nif", "_1.nif")
    w.mesh(OLD, *WHOLE)
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w, claims=("armor/iron/cuirass",))]
    _sweep(w, results)
    w.mesh(OLD, "_1.nif")                         # the path is taken again
    _finish(w, results, merged=False)
    assert w.moved(OLD).is_file(), "the file stayed in the stamp folder"
    return [dict(e) for e in ac._RUN_FAILURES]


def test_the_run_records_the_kind(stuck):
    assert KIND in [e["kind"] for e in stuck]


def test_the_title_does_not_say_the_item_failed_to_convert(stuck):
    title = fs.popup_title(stuck)
    assert "failed to convert" not in title, title
    assert title.startswith("moved meshes were not all put back"), title


def test_the_intro_says_what_to_do_not_that_nothing_changed(stuck):
    intro = fs.popup_intro(stuck)
    assert "did NOT convert" not in intro and "previous state" not in intro, intro
    assert fs.MOVED_NOT_PUT_BACK[KIND] in intro, intro
    assert "_superseded" in intro and "before you play" in intro, intro


def test_the_status_line_does_not_say_the_item_did_not_convert(stuck):
    line = fs.status_line(0, stuck)
    assert "did not convert" not in line, line
    assert "moved meshes were not all put back" in line, line


def test_it_is_still_counted_as_a_failure(stuck):
    failures, _warnings = fs.counts(stuck)
    assert failures == 1


def test_beside_a_real_conversion_failure_each_is_worded_by_itself():
    entries = [_MESH, {"kind": KIND, "source": "out", "item": "1 file(s)",
                       "severity": "failure"}]
    title, intro = fs.popup_title(entries), fs.popup_intro(entries)
    assert title == "1 item(s) failed to convert, moved meshes were not all put back"
    assert "Items marked FAILED did NOT convert this run" in intro
    assert f"Items marked FAILED: {KIND}" in intro
    assert "1 item(s) did not convert" in fs.status_line(0, entries)
