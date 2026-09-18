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

"""#per-file-progress and #cancel-says-so.

The bar used to move only on the per-mod marker, so a 76-file mod after a few
small ones looked hung for its whole duration, and it jumped a whole mod ahead
the moment one started. Cancel fired the tree-kill and then the ordinary
finish path, which said "Finished with exit code 1 - check the log for
errors/warnings" over a run the user had stopped on purpose.

The window's decisions here are closure-bound, so the two that cannot be
called from a test are read by SOURCE and scoped by indentation, the pattern
tests/test_memory_death_is_reported.py set; everything else is a module-level
function tested by value. Tk-free.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac      # noqa: E402
from src import failure_summary as fs   # noqa: E402
from src import gui                     # noqa: E402


def _body(src: str, header: str) -> str:
    """The indented body of the first `def` whose line contains `header`."""
    lines = src.splitlines()
    start = next(k for k, ln in enumerate(lines) if header in ln)
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for ln in lines[start + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        body.append(ln)
    return "\n".join(body)


# --- the bar and the mod's own estimate --------------------------------------------

def test_the_bar_sits_on_the_mods_finished_plus_this_mods_share():
    assert gui._bar_value(1, 0, 76) == 0.0, "the first mod starts from an empty bar"
    assert gui._bar_value(3, 38, 76) == 2.5
    assert gui._bar_value(3, 76, 76) == 3.0
    assert gui._bar_value(3, 90, 76) == 3.0, "never past the mod it is on"
    assert gui._bar_value(3, 0, 0) == 2.0, "a mod with no files counts as its start"


def test_the_mod_eta_comes_from_its_own_rate():
    assert gui._mod_eta(100.0, 0, 10, 105.0) == "estimating…"
    assert gui._mod_eta(100.0, 4, 10, 120.0) == "~30s left"      # 5 s a file, 6 to go
    assert gui._mod_eta(100.0, 10, 10, 150.0) == "finishing…"
    assert gui._mod_eta(100.0, 4, 10, 100.0) == "estimating…", "a clock not ahead of the start"


# --- the markers ---------------------------------------------------------------------

def test_both_markers_parse_and_the_batch_prints_the_per_file_one():
    m = gui._PROG_RX.match("[progress] 2 10 Some Mod\n")
    assert m and m.groups() == ("2", "10", "Some Mod")
    m = gui._NIF_RX.match("[progress-nif] 3 76\n")
    assert m and m.groups() == ("3", "76")
    src = inspect.getsource(ac.auto_convert_mod)
    assert 'print(f"[progress-nif] {done} {len(work_items)}", flush=True)' in src, (
        "the batch no longer prints the per-file marker the window's bar reads")
    i = src.index("NIF/s  ETA")
    assert "[progress-nif]" in src[i:i + 500], (
        "the marker must ride with the human per-file line: same throttle, same place")
    assert gui._NIF_RX.match(f"[progress-nif] {3} {76}\n"), "the printed shape must parse"


def test_the_window_reads_the_per_file_marker_and_strips_both():
    src = inspect.getsource(gui.launch_gui)
    poll = _body(src, "def _poll(")
    assert "_NIF_RX.finditer(item)" in poll and "_update_nif_progress(" in poll
    assert "_NIF_RX.sub(" in poll and "_PROG_RX.sub(" in poll, (
        "both machine markers must be stripped from the visible log")


# --- cancel ---------------------------------------------------------------------------

def test_a_cancelled_run_is_worded_as_cancelled_not_as_an_error():
    line = fs.status_line(1, [], cancelled=True)
    assert line.startswith("Cancelled") and "exit code" not in line
    failed = [{"kind": "x", "item": "y", "severity": "failure"}]
    assert fs.status_line(1, failed, cancelled=True) == line, (
        "a cancelled run is cancelled whatever the killed child left behind")
    assert fs.status_line(1, []).startswith("Finished with exit code 1"), "unchanged otherwise"
    assert fs.status_line(0, []).startswith("Done - success")


def test_the_window_sets_the_flag_on_cancel_and_finish_reads_it():
    src = inspect.getsource(gui.launch_gui)
    assert 'state["cancelled"] = True' in _body(src, "def _cancel_run("), (
        "Cancel must record that the end of this run is the user's doing")
    finish = _body(src, "def _finish(")
    assert 'cancelled=bool(state.get("cancelled"))' in finish, (
        "the status line must be worded from the flag")
    assert "if not cancelled:" in finish, "the failures popup must be gated on it"
    after_gate = finish.split("if not cancelled:", 1)[1]
    assert "_show_failures_popup(fails)" in after_gate.split("\n\n")[0]
    assert "_render_results()" in finish, (
        "the Results tab still repaints: the checkpoint report says what did finish")
    assert src.count('state["cancelled"] = False') >= 1, "the flag is reset when a run starts"
