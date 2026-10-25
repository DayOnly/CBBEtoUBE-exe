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

"""The window during and after a run. #run-phase-display #results-problems
#mod-scan-child

MEASURED 2026-10-22: Refresh mod list ran 153 s inside the window and froze it
67 times (worst 1.3 s), keeping 330 MB; a full run planned 149 mods under a
spinning bar that said only "Converting..."; and the run's problems were
listed once, in a popup."""
import json
import multiprocessing as mp
from pathlib import Path

import pytest

from src import gui


# ---- the phase, from the run's own lines -------------------------------------------

def _phase(t=None):
    clock = t if t is not None else [1000.0]
    return gui.RunPhase(clock=lambda: clock[0]), clock


def test_planning_names_the_mod_and_counts():
    ph, clock = _phase()
    assert ph.feed("\n--- [3/149] planning 'Some Armour Mod' ---\n")
    clock[0] += 75
    assert ph.status("~5m left") == ("Planning mod 3/149: Some Armour Mod — planning "
                                     "~5m left — 1m15s so far")


def test_the_mesh_phase_counts_finished_mods_and_problems():
    ph, clock = _phase()
    ph.feed("=== NIF conversion: 3228 file(s) from 149 source(s) on one schedule ===\n")
    assert ph.name == "Converting armour pieces"
    assert not ph.feed("  [12/149] 'A Mod': 76 NIF(s) converted (1986s into the schedule)\n")
    ph.feed("  PASS FAILED: x on y.nif -- RuntimeError('z')\n")
    ph.feed("  !! something to fix -- somewhere\n")
    clock[0] += 3700
    assert ph.tail() == (" — 1h01m so far · 12/149 mods done · 2 problem(s) printed "
                         "(search the log for '!!')")


@pytest.mark.parametrize("line,name", [
    ("=== NIF conversion done: 3228 file(s) in 2055.9s ===", "Finishing the mods (patches, reports)"),
    ("--- unified coverage: winner-scan patches -> merge ---", "Coverage: armour without a converted mesh"),
    ("--- auto-merging 3 patch(es) into CBBE_to_UBE_Combined.esp ---", "Merging the plugins"),
    ("=== 0 failure(s), 2 warning(s) ===", "Finishing up"),
    ("=== all clear ===", "Finishing up"),
])
def test_the_steps_after_the_meshes_are_named(line, name):
    ph, _c = _phase()
    assert ph.feed(line + "\n")
    assert ph.name == name


def test_a_line_split_across_reads_is_read_once():
    ph, _c = _phase()
    assert not ph.feed("--- [1/2] plann")
    assert ph.feed("ing 'M' ---\n")
    assert (ph.i, ph.n, ph.mod) == (1, 2, "M")


# ---- the problems panel ---------------------------------------------------------------

def test_a_problem_links_to_its_mod_report(tmp_path):
    (tmp_path / "conversion_report_A_B.txt").write_text("x", encoding="utf-8")
    assert gui.mod_report_path(tmp_path, "A:B") == tmp_path / "conversion_report_A_B.txt"
    assert gui.mod_report_path(tmp_path, "Other") is None
    (tmp_path / "conversion_summary.txt").write_text("x", encoding="utf-8")
    assert gui.mod_report_path(tmp_path, "unified coverage") == tmp_path / "conversion_summary.txt"
    (tmp_path / "coverage_gaps.tsv").write_text("x", encoding="utf-8")
    assert gui.mod_report_path(tmp_path, "unified coverage") == tmp_path / "coverage_gaps.tsv"
    assert gui.mod_report_path("", "A") is None


def test_failures_come_before_warnings(tmp_path):
    p = tmp_path / "CBBEtoUBE_last_failures.json"
    p.write_text(json.dumps({"failures": [
        {"kind": "w1", "severity": "warning"}, {"kind": "f1", "severity": "failure"},
        {"kind": "w2", "severity": "warning", "count": 3, "source": "S", "item": "i"}]}),
        encoding="utf-8")
    rows = gui.read_run_problems(p)
    assert [r["kind"] for r in rows] == ["f1", "w1", "w2"]
    assert gui.problem_text(rows[2]) == "w2 (x3) — S: i"
    assert gui.read_run_problems(tmp_path / "missing.json") == []
    (tmp_path / "torn.json").write_text("{", encoding="utf-8")
    assert gui.read_run_problems(tmp_path / "torn.json") == []


# ---- the mod-list scan in a child process ----------------------------------------------

def test_the_scan_runs_in_a_real_child_process(monkeypatch, tmp_path):
    """A spawned child, as the frozen window starts it: an empty mods folder
    lists nothing, and the answer comes back through the pipe."""
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_MO2_INI", str(tmp_path / "none.ini"))
    seen = []
    items, printed, err = gui.run_mod_scan_child(
        "overlay", {}, "", seen.append, register=seen.append)
    assert err is None, (err, printed)
    assert items == []
    assert seen[-1] is None, "the window's handle on the child was not cleared"
    assert isinstance(seen[0], mp.process.BaseProcess)


class _Dies:
    """A child that reports progress and then dies before its answer."""

    def __init__(self, target=None, args=(), daemon=None):
        self._conn = args[0]
        self.exitcode = None

    def start(self):
        self._conn.send(("progress", "half way"))
        self._conn.close()
        self.exitcode = 3

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass

    def terminate(self):
        pass


class _Ctx:
    Process = _Dies

    @staticmethod
    def Pipe(duplex=False):
        return mp.Pipe(duplex=duplex)


def test_a_child_that_dies_is_said_out_loud():
    seen = []
    items, _printed, err = gui.run_mod_scan_child(
        "armor", {}, "", seen.append, ctx=_Ctx(), poll_s=0.01)
    assert seen == ["half way"]
    assert items == [] and "stopped (exit 3)" in err


def test_the_in_window_scan_is_a_setting():
    from src import gui_settings
    s = gui_settings.by_key()["window_mod_scan_child"]
    assert (s.tab, s.default, s.env) == ("Diagnostics", True, None)


# ---- the log divider (#log-pane-divider) ---------------------------------------------

@pytest.mark.parametrize("saved,height", [
    (300, 300),             # as remembered
    (0, None),              # nothing remembered: the built-in 12 lines
    ("x", None),            # unreadable
    (None, None),
    (10, gui.LOG_DIVIDER_MIN_LOG),   # at least a few log lines
])
def test_the_divider_goes_back_where_it_was_left(saved, height):
    assert gui.log_pane_start_height(saved) == height


def test_the_divider_is_a_remembered_setting_not_a_control():
    from src import gui_settings
    s = gui_settings.by_key()["log_pane_height"]
    assert (s.tab, s.kind, s.default, s.env) == ("Appearance", "int", 0, None)
    assert "log_pane_height" not in gui_settings.displayed_keys()


_DIVIDER_CHILD = r'''
import os, sys
sys.path.insert(0, __PROJ__)
for _k in [k for k in os.environ if k.upper().startswith("CBBE2UBE_")]:
    del os.environ[_k]
os.environ["CBBE2UBE_CONFIG"] = __CFG__
os.environ["CBBE2UBE_MODS_ROOT"] = __MODS__
os.environ["CBBE2UBE_GAME_DATA"] = __DATA__
import tkinter as tk
MODE = __MODE__

def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)

_orig = tk.Misc.mainloop
def mainloop(self, n=0):
    root = self
    def drag(p, to_y):
        """A mouse drag of the divider, as a hand does it; counts how often the
        upper pane changed size before the button was let go."""
        upper = root.nametowidget(str(p.panes()[0]))
        h0, moved = upper.winfo_height(), 0
        x, y = 400, p.sash_coord(0)[1] + 3
        p.event_generate("<ButtonPress-1>", x=x, y=y); root.update()
        step = -10 if to_y < y else 10
        for yy in range(y, to_y, step):
            p.event_generate("<B1-Motion>", x=x, y=yy); root.update()
            moved += upper.winfo_height() != h0
        p.event_generate("<ButtonRelease-1>", x=x, y=to_y); root.update()
        return moved

    def probe():
        panes = [w for w in walk(root) if w.winfo_class() == "Panedwindow"]
        p = panes[0]
        kids = [root.nametowidget(str(x)) for x in p.panes()]
        texts = [w for w in walk(kids[-1]) if w.winfo_class() == "Text"]
        tabs = [w for w in walk(kids[0]) if w.winfo_class() == "TNotebook"]
        print("PANES=%d LOG_BELOW=%d TABS_ABOVE=%d" % (len(panes), len(texts), len(tabs)))
        if MODE != "look":
            root.geometry("1200x900"); root.update()
        print("TOTAL=%d SASH=%d LOG=%d" % (p.winfo_height(), p.sash_coord(0)[1],
                                           kids[-1].winfo_height()))
        if MODE == "drag":
            print("MOVED_DURING_DRAG=%d" % drag(p, p.winfo_height() - 300))
            print("DRAGGED_TO=%d" % kids[-1].winfo_height())
        if MODE == "grip":
            g = [w for w in p.winfo_children() if w.winfo_class() == "Canvas"][0]
            print("GRIP_MAPPED=%d GRIP_CX=%d HALF_W=%d GRIP_Y=%d SASH_Y=%d" % (
                g.winfo_ismapped(), g.winfo_x() + g.winfo_width() // 2,
                p.winfo_width() // 2, g.winfo_y(), p.sash_coord(0)[1]))
            h0 = kids[-1].winfo_height()
            g.event_generate("<ButtonPress-1>", x=20, y=4); root.update()
            for dy in range(0, -200, -10):
                g.event_generate("<B1-Motion>", x=20, y=4 + dy); root.update()
            g.event_generate("<ButtonRelease-1>", x=20, y=-196); root.update(); root.update()
            print("LOG_GREW=%d GRIP_FOLLOWED=%d" % (
                kids[-1].winfo_height() - h0,
                abs(g.winfo_y() - p.sash_coord(0)[1]) <= 1))
        if MODE == "squeeze":
            print("MOVED_DURING_DRAG=%d" % drag(p, 20))
            conv = [w for w in walk(kids[0]) if w.winfo_class() == "TButton"
                    and str(w.cget("text")) == "Convert"][0]
            bars = [w for w in walk(tabs[0]) if w.winfo_class() == "TScrollbar"
                    and w.winfo_ismapped() and w.winfo_height() > 1]
            print("UPPER=%d CONVERT_BOTTOM=%d RUN_SCROLLBARS=%d" % (
                kids[0].winfo_height(),
                conv.winfo_rooty() + conv.winfo_height() - kids[0].winfo_rooty(),
                len(bars)))
        root.tk.call(root.tk.call("wm", "protocol", root._w, "WM_DELETE_WINDOW"))
    root.after(1500, probe)
    return _orig(self, n)
tk.Misc.mainloop = mainloop
from src.gui import launch_gui
launch_gui(argv=[])
'''


def _divider_run(tmp_path, mode):
    import subprocess
    import sys
    from tests.test_gui_smoke import _no_display
    if _no_display():
        pytest.skip("no display")
    (tmp_path / "mods").mkdir(exist_ok=True)
    (tmp_path / "Data").mkdir(exist_ok=True)
    proj = Path(__file__).resolve().parents[1]
    code = (_DIVIDER_CHILD.replace("__PROJ__", repr(str(proj)))
            .replace("__CFG__", repr(str(tmp_path / "settings.json")))
            .replace("__MODS__", repr(str(tmp_path / "mods")))
            .replace("__DATA__", repr(str(tmp_path / "Data")))
            .replace("__MODE__", repr(mode)))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120, cwd=str(proj))
    vals = {}
    for ln in r.stdout.splitlines():
        for part in ln.split():
            if "=" in part:
                k, v = part.split("=", 1)
                vals[k] = int(v)
    assert vals, r.stdout[-2000:] + r.stderr[-2000:]
    return vals


def test_the_log_sits_below_a_divider_that_is_remembered(tmp_path):
    """The real window: tabs above the divider, the log below it; drag it,
    close, open again, and the log is the height it was left at."""
    first = _divider_run(tmp_path, "drag")
    assert (first["PANES"], first["LOG_BELOW"], first["TABS_ABOVE"]) == (1, 1, 1)
    assert abs(first["DRAGGED_TO"] - 300) <= 12, first
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert abs(saved.get("log_pane_height", 0) - first["DRAGGED_TO"]) <= 2
    second = _divider_run(tmp_path, "look")
    assert abs(second["LOG"] - first["DRAGGED_TO"]) <= 2


def test_dragging_moves_nothing_until_release(tmp_path):
    """MEASURED 2026-10-22: a live re-layout cost 30-38 ms per drag step and
    the button row trailed the divider. Now only a line follows the mouse."""
    assert _divider_run(tmp_path, "drag")["MOVED_DURING_DRAG"] == 0


def test_a_tall_log_leaves_the_buttons_and_a_scrollable_run_tab(tmp_path):
    """Dragged as far up as it goes: the tabs keep their minimum, the Convert
    button stays inside the upper pane, and the Run tab gets a scroll bar
    instead of hiding its options."""
    v = _divider_run(tmp_path, "squeeze")
    assert v["UPPER"] >= gui.LOG_DIVIDER_MIN_TOP - 2
    assert 0 < v["CONVERT_BOTTOM"] <= v["UPPER"]
    assert v["RUN_SCROLLBARS"] >= 1


def test_the_divider_has_a_notch_that_is_a_handle(tmp_path):
    """A dotted notch in the middle of the divider says it can be dragged; a
    drag started on it moves the divider, and it follows on release."""
    v = _divider_run(tmp_path, "grip")
    assert v["GRIP_MAPPED"] == 1
    assert abs(v["GRIP_CX"] - v["HALF_W"]) <= 1
    assert abs(v["GRIP_Y"] - v["SASH_Y"]) <= 1
    assert v["LOG_GREW"] >= 150
    assert v["GRIP_FOLLOWED"] == 1


def test_the_notch_dots_are_centred_in_two_staggered_rows():
    w, h = gui.LOG_GRIP_SIZE
    dots = gui.grip_dots(w, h)
    xs, ys = [x for x, _y in dots], sorted({y for _x, y in dots})
    assert len(ys) == 2 and all(0 <= y and y + 1 < h for y in ys)
    assert abs(min(xs) - (w - (max(xs) + 2))) <= 1
    assert len(dots) >= 12


@pytest.mark.parametrize("view,body,want", [
    (500, 356, (500, False)),     # room to spare: the body fills the tab
    (122, 356, (356, True)),      # squeezed: it keeps its height and scrolls
    (356, 356, (356, False)),
])
def test_the_run_tab_fills_or_scrolls(view, body, want):
    assert gui.run_body_fit(view, body) == want


def test_the_window_offers_both_logs():
    text = Path(gui.__file__).read_text(encoding="utf-8")
    assert 'label="Open run log"' in text and 'label="Open detail log"' in text
    assert gui.DETAIL_LOG_NAME == "CBBEtoUBE_last_run_detail.log"
