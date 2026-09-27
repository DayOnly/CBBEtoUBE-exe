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

"""The window's own wiring, driven through the REAL window. #gui-wiring

Reviewed 2026-09-25 on 266d937: every fix of the CLI/GUI hunt was tested
through a module-level helper, and none through the Tk closure that calls it.
Three reverts at once -- Refresh without rescan/mark_ube_native, the run
worker releasing the session log and passing dry_run=False, the callback
error hook removed -- left all 231 GUI tests green. So did dropping the saved
settings from Refresh, the overlay list, the exclusions lister, the UBE-mesh
scan and the diagnostics zip's setup check.

Here the whole window is built in a child interpreter (as tests/test_gui_smoke.py
does, for the same Tk-state reason) and a driver presses its buttons and menu
entries in order: armor Refresh, overlay Refresh, both Exclusions dialogs and
the mesh scan, Help > Save diagnostics zip, then Convert as a dry run and as a
real run. The helpers they call are replaced by recorders that note the
saved-settings variable they ran under; the conversion child is a stand-in
process object, so nothing converts. One launch serves every test below."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from tests.test_gui_smoke import _no_display  # noqa: E402

pytestmark = pytest.mark.skipif(
    _no_display(), reason="no display: cannot construct a tkinter window")

# The saved setting the recorders look for: vanilla_sweep off sets it to "1"
# in the environment a conversion child gets, and nowhere else.
VAR = "CBBE2UBE_NO_VANILLA_SWEEP"

_CHILD = textwrap.dedent(r'''
    import json, os, sys, time, traceback
    from pathlib import Path
    for _k in [k for k in os.environ if k.upper().startswith("CBBE2UBE_")]:
        del os.environ[_k]
    sys.path.insert(0, __PROJ__)
    os.environ["CBBE2UBE_CONFIG"] = __CFG__
    os.environ["CBBE2UBE_MODS_ROOT"] = __MODS__
    os.environ["CBBE2UBE_GAME_DATA"] = __DATA__
    TOOL = Path(__TOOL__)
    VAR = __VAR__
    R = {"calls": [], "runs": [], "released": 0, "steps": [], "timeouts": [],
         "env_before": os.environ.get(VAR)}
    STEP = {"now": "launch"}

    def release_log_tee():
        """What the frozen entry point exposes on __main__: the window's
        session-log tee. A run must never release it."""
        R["released"] += 1

    import tkinter as tk
    import tkinter.messagebox as _mb
    for _n in ("showinfo", "showwarning", "showerror"):
        setattr(_mb, _n, lambda *a, **k: None)
    for _n in ("askokcancel", "askyesno", "askyesnocancel", "askquestion"):
        setattr(_mb, _n, lambda *a, **k: True)
    os.startfile = lambda *a, **k: None          # no Explorer window

    import src.gui as gui
    import src.auto_convert as ac
    import src.preflight as pf

    def rec(site, **kw):
        R["calls"].append(dict(site=site, step=STEP["now"],
                               env=os.environ.get(VAR), **kw))

    def fake_list(output_dir=None, progress=None, *, rescan=False,
                  mark_ube_native=False):
        rec("list_convertible_mods", progress=progress is not None,
            rescan=rescan, mark_ube_native=mark_ube_native)
        items = [{"name": "PlainMod", "nifs": 2}, {"name": "NativeMod", "nifs": 1}]
        if mark_ube_native:                     # the real contract
            for it in items:
                it["ube_native"] = it["name"] == "NativeMod"
        return items

    def fake_overlays():
        rec("list_overlay_mods")
        return [{"name": "InkMod"}]

    def fake_scan(domain="armor", sample_per_mod=6, progress=None):
        rec("scan_ube_native")
        return []

    def fake_checks(*a, **k):
        rec("run_checks", args=sorted(k))
        return []

    ac.list_convertible_mods = fake_list
    ac.list_overlay_mods = fake_overlays
    ac.scan_ube_native = fake_scan
    pf.run_checks = fake_checks
    gui.tool_folder = lambda: TOOL

    import subprocess
    _real_popen = subprocess.Popen

    class _Done:
        pid = 0
        returncode = 0
        def poll(self):
            return 0
        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, *a, **kw):
        if any("cbbe_to_ube_main" in str(c) for c in cmd):
            env = kw.get("env") or {}
            R["runs"].append({"argv": [str(c) for c in cmd[2:]],
                              "run_log": env.get("CBBE2UBE_RUN_LOG"),
                              "last_run": _read(TOOL / "CBBEtoUBE_last_run.log"),
                              "previous": _read(TOOL / "CBBEtoUBE_previous_run.log")})
            return _Done()
        return _real_popen(cmd, *a, **kw)

    def _read(p):
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None
    subprocess.Popen = fake_popen

    # ---- the driver --------------------------------------------------------
    def widgets(w):
        out = [w]
        for c in w.winfo_children():
            out += widgets(c)
        return out

    def text_of(w):
        try:
            return str(w.cget("text"))
        except Exception:
            return None

    def under(w, prefix):
        while w is not None:
            if w.winfo_class() == "TLabelframe" and (text_of(w) or "").startswith(prefix):
                return True
            w = w.master
        return False

    def button(root, text, section=None, cls="TButton"):
        hits = [w for w in widgets(root) if w.winfo_class() == cls
                and text_of(w) == text and (section is None or under(w, section))]
        assert len(hits) == 1, (text, section, len(hits))
        return hits[0]

    def press(w):
        w.state(["!disabled"])
        w.invoke()

    def panel_text(root):
        return "\n".join(w.get("1.0", "end") for w in widgets(root)
                         if w.winfo_class() == "Text")

    def calls(site, step):
        return [c for c in R["calls"] if c["site"] == site and c["step"] == step]

    def toplevels(root):
        return [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]

    def script(root):
        yield "launch check", lambda: calls("run_checks", "launch"), 20

        STEP["now"] = "callback"
        def _boom():
            raise RuntimeError("planted callback fault")
        root.after(0, _boom)
        yield "callback error", lambda: "planted callback fault" in panel_text(root), 4

        STEP["now"] = "refresh"
        press(button(root, "Refresh mod list", "Convert armor"))
        yield "refresh", lambda: any(text_of(w) and "PlainMod" in text_of(w)
                                     for w in widgets(root)
                                     if w.winfo_class() == "TCheckbutton"), 20
        R["listed"] = [text_of(w) for w in widgets(root)
                       if w.winfo_class() == "TCheckbutton"
                       and "Mod" in (text_of(w) or "")]
        yield "hidden note", lambda: "NativeMod" in panel_text(root), 3
        R["panel_after_refresh"] = panel_text(root)

        STEP["now"] = "overlay refresh"
        press(button(root, "Refresh mod list", "Convert overlays"))
        yield "overlay refresh", lambda: calls("list_overlay_mods", "overlay refresh"), 20

        STEP["now"] = "armor exclusions"
        press(button(root, "Exclusions…", "Convert armor"))
        yield "armor exclusions", lambda: calls("list_convertible_mods", "armor exclusions"), 20
        STEP["now"] = "mesh scan"
        win = toplevels(root)[-1]
        press(button(win, "Scan meshes for UBE-native"))
        yield "mesh scan", lambda: calls("scan_ube_native", "mesh scan"), 20
        win.destroy()

        STEP["now"] = "overlay exclusions"
        press(button(root, "Exclusions…", "Convert overlays"))
        yield "overlay exclusions", lambda: calls("list_overlay_mods", "overlay exclusions"), 20
        toplevels(root)[-1].destroy()

        STEP["now"] = "diagnostics"
        bar = root.nametowidget(root["menu"])
        helpm = next(root.nametowidget(bar.entrycget(i, "menu"))
                     for i in range(bar.index("end") + 1)
                     if bar.type(i) == "cascade" and bar.entrycget(i, "label") == "Help")
        helpm.invoke(next(i for i in range(helpm.index("end") + 1)
                          if helpm.type(i) == "command"
                          and helpm.entrycget(i, "label").startswith("Save diagnostics")))
        yield "diagnostics", lambda: calls("run_checks", "diagnostics"), 20

        cancel = button(root, "Cancel")
        STEP["now"] = "dry run"
        press(button(root, "Dry run (list mods, convert nothing)", cls="TCheckbutton"))
        press(button(root, "Convert"))
        yield "dry run", lambda: len(R["runs"]) >= 1, 20
        yield "dry run finished", lambda: "disabled" in cancel.state(), 20

        STEP["now"] = "real run"
        press(button(root, "Dry run (list mods, convert nothing)", cls="TCheckbutton"))
        press(button(root, "Convert"))
        yield "real run", lambda: len(R["runs"]) >= 2, 20
        yield "real run finished", lambda: "disabled" in cancel.state(), 20
        R["previous_after"] = _read(TOOL / "CBBEtoUBE_previous_run.log")
        R["env_after"] = os.environ.get(VAR)

    def finish(root):
        print("WIRING=" + json.dumps(R), flush=True)
        try:
            root.destroy()
        except Exception:
            pass

    def drive(root):
        gen = script(root)
        cur = {}

        def advance():
            try:
                label, pred, timeout = next(gen)
            except StopIteration:
                finish(root)
                return
            except Exception:
                R["driver_error"] = traceback.format_exc()
                finish(root)
                return
            cur.update(label=label, pred=pred, deadline=time.time() + timeout)
            root.after(50, tick)

        def tick():
            try:
                ok = bool(cur["pred"]())
            except Exception:
                ok = False
            if ok or time.time() > cur["deadline"]:
                (R["steps"] if ok else R["timeouts"]).append(cur["label"])
                advance()
                return
            root.after(50, tick)

        root.after(120000, lambda: (R.update(watchdog=True), finish(root)))
        advance()

    _orig_mainloop = tk.Misc.mainloop

    def _mainloop(self, n=0):
        self.after(300, lambda: drive(self))
        _orig_mainloop(self, n)
    tk.Tk.mainloop = _mainloop

    rc = gui.launch_gui(argv=[])
    print("LAUNCH_RC=%d" % rc, flush=True)
''')


@pytest.fixture(scope="module")
def wiring(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wiring")
    mods, data, tool = tmp / "mods", tmp / "Data", tmp / "tool"
    for d in (mods / "CBBEtoUBE Auto", data, tool):
        d.mkdir(parents=True)
    # The run log of a run that died: a dry run must leave it where it is.
    (tool / "CBBEtoUBE_last_run.log").write_text("OLD RUN", encoding="utf-8")
    from src import gui_settings as gs
    assert gs.save_values({**gs.defaults(), "vanilla_sweep": False,
                           "zeroed_body_refs": False}, tmp / "settings.json")
    code = (_CHILD
            .replace("__PROJ__", repr(str(PROJ)))
            .replace("__CFG__", repr(str(tmp / "settings.json")))
            .replace("__MODS__", repr(str(mods)))
            .replace("__DATA__", repr(str(data)))
            .replace("__TOOL__", repr(str(tool)))
            .replace("__VAR__", repr(VAR)))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=240, cwd=str(PROJ))
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("WIRING=")), None)
    assert line, f"the driver reported nothing:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}"
    res = json.loads(line[len("WIRING="):])
    res["_stderr"] = r.stderr
    assert "driver_error" not in res, res["driver_error"]
    assert not res.get("watchdog"), res
    return res


def _one(res, site, step):
    hits = [c for c in res["calls"] if c["site"] == site and c["step"] == step]
    assert hits, f"{site} was never called at step {step!r}: {res['timeouts']}"
    return hits[0]


# --- the harness itself -----------------------------------------------------------

def test_the_driver_reached_every_step(wiring):
    """The control: a driver that stalled would make the tests below read
    whatever the launch left behind."""
    reached = set(wiring["steps"]) | set(wiring["timeouts"])
    assert {"launch check", "refresh", "overlay refresh", "armor exclusions",
            "mesh scan", "overlay exclusions", "diagnostics", "dry run",
            "dry run finished", "real run", "real run finished"} <= set(wiring["steps"]), (
        wiring["timeouts"])
    assert "callback error" in reached
    assert wiring["env_before"] is None, "the setting leaked in before the window"


# --- #settings-everywhere: every helper runs under the saved settings -------------

def test_refresh_mod_list_runs_under_the_saved_settings(wiring):
    assert _one(wiring, "list_convertible_mods", "refresh")["env"] == "1"


def test_the_overlay_list_runs_under_the_saved_settings(wiring):
    assert _one(wiring, "list_overlay_mods", "overlay refresh")["env"] == "1"


def test_the_exclusions_listers_run_under_the_saved_settings(wiring):
    assert _one(wiring, "list_convertible_mods", "armor exclusions")["env"] == "1"
    assert _one(wiring, "list_overlay_mods", "overlay exclusions")["env"] == "1"


def test_the_ube_mesh_scan_runs_under_the_saved_settings(wiring):
    assert _one(wiring, "scan_ube_native", "mesh scan")["env"] == "1"


def test_the_diagnostics_zip_checks_the_setup_under_the_saved_settings(wiring):
    assert _one(wiring, "run_checks", "diagnostics")["env"] == "1"


def test_the_window_gets_its_own_environment_back(wiring):
    assert wiring["env_after"] is None, "a helper's overlay outlived it"


# --- #mod-scan-rescan, #select-list-ube-native -------------------------------------

def test_refresh_reads_the_mods_again(wiring):
    assert _one(wiring, "list_convertible_mods", "refresh")["rescan"] is True


def test_refresh_leaves_out_what_the_run_drops(wiring):
    assert _one(wiring, "list_convertible_mods", "refresh")["mark_ube_native"] is True
    listed = " ".join(wiring["listed"])
    assert "PlainMod" in listed and "NativeMod" not in listed, wiring["listed"]
    panel = wiring["panel_after_refresh"]
    assert "NativeMod" in panel and "Reference bodies" in panel, panel[-800:]


def test_the_exclusions_list_rescans_too(wiring):
    assert _one(wiring, "list_convertible_mods", "armor exclusions")["rescan"] is True


# --- #dry-run-keeps-the-run-log, #gui-session-log-kept ------------------------------

def test_a_dry_run_from_the_window_keeps_the_last_run_log(wiring):
    dry = wiring["runs"][0]
    assert "--list-only" in dry["argv"], dry
    assert Path(dry["run_log"]).name == "CBBEtoUBE_cli.log", dry
    assert dry["last_run"] == "OLD RUN" and dry["previous"] is None, dry


def test_a_real_run_from_the_window_rotates_the_run_log(wiring):
    real = wiring["runs"][1]
    assert "--list-only" not in real["argv"], real
    assert Path(real["run_log"]).name == "CBBEtoUBE_last_run.log", real
    assert real["previous"] == "OLD RUN", real


def test_a_run_from_the_window_leaves_the_session_log_open(wiring):
    assert len(wiring["runs"]) == 2
    assert wiring["released"] == 0, "a run released the window's session log"


def test_a_window_callback_error_reaches_the_log_panel(wiring):
    assert "callback error" in wiring["steps"], (
        "a callback's traceback never reached the log panel")
