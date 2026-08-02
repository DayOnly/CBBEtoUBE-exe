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

"""Actually BUILD the window.

`src/gui.py` is ~2270 lines and, until this file, no test constructed a single
widget from it -- the existing gui tests all cover module-level pure helpers
(name matching, ETA, theme contrast, process kill). Every widget-building path
was unexercised, so a mistake in the settings renderer could ship and be found
only by launching the exe by hand. `launch_gui` already had the hooks for this
(`auto_close_ms`, `_smoke_settings`); nothing used them.

SMOKE only: the window builds and closes. Not that it looks right. Still worth
having, because the renderer walks every setting, so a bad `hint_for`, a LAYOUT
key naming a setting that does not exist, or a group that renders nothing all
raise here.

WHY A SUBPROCESS. Run in-process, this failed roughly one run in three -- always
at `tk.Tk()`, and only inside the full suite, never alone. Tk interpreter state
is per-process and shared with every other test that touches tkinter, so the
window build raced their teardown. A flaky GUI test is worse than no GUI test:
it trains everyone to re-run CI instead of reading it. A child process gets a
clean interpreter and the race disappears.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))


def _launch_in_child(tmp_path, extra_setup="", timeout=120):
    """Build the window in a fresh interpreter. Returns the CompletedProcess.

    Launches against an EMPTY modlist: otherwise the launch-time preflight scans
    the real mod tree, which took ~35s per launch locally and would depend on
    machine state in CI.
    """
    mods = tmp_path / "mods"
    data = tmp_path / "Data"
    mods.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    # Dedent BEFORE substituting: `extra_setup` arrives unindented, so
    # interpolating it first drops the template's common prefix to "" and
    # dedent silently becomes a no-op (IndentationError in the child).
    template = textwrap.dedent("""
        import os, sys
        sys.path.insert(0, __PROJ__)
        os.environ["CBBE2UBE_CONFIG"] = __CFG__
        os.environ["CBBE2UBE_MODS_ROOT"] = __MODS__
        os.environ["CBBE2UBE_GAME_DATA"] = __DATA__
        os.environ.pop("CBBE2UBE_MO2_INI", None)
        __EXTRA__
        from src.gui import launch_gui
        rc = launch_gui(argv=[], auto_close_ms=600, _smoke_settings=True)
        print("LAUNCH_RC=%d" % rc)
    """)
    code = (template
            .replace("__PROJ__", repr(str(PROJ)))
            .replace("__CFG__", repr(str(tmp_path / "settings.json")))
            .replace("__MODS__", repr(str(mods)))
            .replace("__DATA__", repr(str(data)))
            .replace("__EXTRA__", extra_setup))
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=timeout, cwd=str(PROJ))


def _no_display() -> bool:
    try:
        import tkinter as tk
    except Exception:
        return True
    try:
        r = tk.Tk()
        r.destroy()
        return False
    except Exception:
        return True


needs_display = pytest.mark.skipif(
    _no_display(), reason="no display: cannot construct a tkinter window")


@needs_display
def test_window_builds_and_closes(tmp_path):
    """The whole window, including the generated settings tabs, then quit."""
    r = _launch_in_child(tmp_path)
    assert r.returncode == 0, f"GUI build failed:\n{r.stdout}\n{r.stderr}"
    assert "LAUNCH_RC=0" in r.stdout, f"unexpected exit:\n{r.stdout}\n{r.stderr}"


@needs_display
def test_a_broken_renderer_actually_fails_this_test(tmp_path):
    """The control for the test above.

    A smoke test that cannot fail is worse than none, because it reads as
    coverage. Plant a fault on the path the settings renderer walks and confirm
    the build dies rather than quietly returning 0.
    """
    plant = (
        'import src.gui_settings as gs\n'
        'def _boom(_s):\n'
        '    raise RuntimeError("planted renderer fault")\n'
        'gs.hint_for = _boom'
    )
    r = _launch_in_child(tmp_path, extra_setup=plant)
    assert r.returncode != 0, (
        "a renderer fault did NOT fail the build -- this smoke test would "
        f"pass on a broken GUI:\n{r.stdout}")
    assert "planted renderer fault" in r.stderr
    assert "LAUNCH_RC=" not in r.stdout


# NOT ADDED: a test counting bound controls by spying on `tk.Variable.trace_add`.
# Patching that globally reaches ttk's own internals and the window stopped
# closing, so the test hung instead of failing. A hanging test in CI is worse
# than a missing one. `test_no_orphaned_settings` already covers the tab-level
# case, and the two tests above raise if the renderer touches a setting it
# cannot handle -- which is the failure that actually happens.
