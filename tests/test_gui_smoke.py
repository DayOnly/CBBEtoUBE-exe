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

SMOKE only: it asserts the window builds, binds a control per setting, and
closes cleanly -- not that it looks right. Still worth having, because the
renderer walks all 44 settings, so a bad `hint_for`, a LAYOUT key naming a
setting that does not exist, or a group that renders nothing all raise here.
"""
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import gui_settings as gs


def _no_display() -> bool:
    """True when this environment cannot create a top-level window."""
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


@pytest.fixture
def isolated_gui(tmp_path, monkeypatch):
    """Launch against an EMPTY modlist and a throwaway config.

    Without this the launch-time preflight scans the real mod tree, which took
    ~35s per launch locally and would depend on machine state in CI. Pointing
    discovery at an empty directory keeps the test about the WINDOW.
    """
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(tmp_path / "settings.json"))
    mods = tmp_path / "mods"
    data = tmp_path / "Data"
    mods.mkdir()
    data.mkdir()
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_GAME_DATA", str(data))
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    return tmp_path


@needs_display
def test_window_builds_and_closes(isolated_gui):
    from src.gui import launch_gui
    assert launch_gui(argv=[], auto_close_ms=500, _smoke_settings=True) == 0


@needs_display
def test_a_broken_renderer_actually_fails_this_test(isolated_gui, monkeypatch):
    """The control for the test above.

    A smoke test that cannot fail is worse than none, because it reads as
    coverage. Plant a fault on the path the settings renderer walks and confirm
    the build raises rather than quietly returning 0.
    """
    def boom(_s):
        raise RuntimeError("planted renderer fault")

    monkeypatch.setattr(gs, "hint_for", boom)
    from src.gui import launch_gui
    with pytest.raises(RuntimeError, match="planted renderer fault"):
        launch_gui(argv=[], auto_close_ms=500, _smoke_settings=True)


# NOT ADDED: a test counting bound controls by spying on `tk.Variable.trace_add`.
# Patching that globally reaches ttk's own internals and the window stopped
# closing, so the test hung instead of failing. A hanging test in CI is worse
# than a missing one. `test_no_orphaned_settings` already covers the tab-level
# case, and the two tests above raise if the renderer touches a setting it
# cannot handle -- which is the failure that actually happens.
