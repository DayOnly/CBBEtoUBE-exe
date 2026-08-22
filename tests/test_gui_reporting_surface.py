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

"""The reporting surface: a Results TAB and a Help MENU, not three buttons.

WHAT THIS PINS, and why it is worth a test rather than a code comment.

The action bar used to carry `Report`, `Export diagnostics` and `Copy report`.
The first and third were routinely confused, because the word meant two
unrelated things: `Report` showed how YOUR RUN WENT, `Copy report` built a
PROBLEM REPORT to send to someone. The second and third emitted the SAME text.

So the surface was reorganised by INTENT:

    how the run went   ->  the Results tab   (state, always present)
    asking for help    ->  the Help menu     (both deliveries, one place)

Nothing here checks that it LOOKS right -- it checks the shape is still the one
that was decided, because the failure mode is somebody re-adding a "Copy report"
button next to a "Report" button and re-creating the collision.

Runs in a CHILD PROCESS for the same reason `test_gui_smoke` does: Tk
interpreter state is per-process and shared, and an in-process window build
races other tkinter tests about one run in three.

EVERY assertion here is paired with an anti-vacuity check on a widget that MUST
exist ("Convert", the "Run" tab). A spy that silently recorded nothing would
otherwise make every "X is absent" assertion pass on a blank window.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from tests.test_gui_smoke import _launch_in_child, needs_display  # noqa: E402

# Spies wrapped around the widget constructors the window build walks. Recorded
# into a list and dumped at exit, so the assertions run against what the build
# ACTUALLY created rather than against a re-reading of the source.
_SPY = """
import atexit
import tkinter as tk
from tkinter import ttk

_tabs, _btns, _casc, _cmds, _labels = [], [], [], [], []

_o_add = ttk.Notebook.add
def _add(self, child, **kw):
    if kw.get("text"):
        _tabs.append(kw["text"])
    return _o_add(self, child, **kw)
ttk.Notebook.add = _add

_o_btn = ttk.Button.__init__
def _btn(self, master=None, **kw):
    if kw.get("text"):
        _btns.append(kw["text"])
    return _o_btn(self, master, **kw)
ttk.Button.__init__ = _btn

_o_lbl = ttk.Label.__init__
def _lbl(self, master=None, **kw):
    if kw.get("text"):
        _labels.append(kw["text"])
    return _o_lbl(self, master, **kw)
ttk.Label.__init__ = _lbl

_o_cfg = ttk.Label.configure
def _cfg(self, cnf=None, **kw):
    if kw.get("text"):
        _labels.append(kw["text"])
    return _o_cfg(self, cnf, **kw)
ttk.Label.configure = _cfg

_o_casc = tk.Menu.add_cascade
def _casc_(self, cnf={}, **kw):
    if kw.get("label"):
        _casc.append(kw["label"])
    return _o_casc(self, cnf, **kw)
tk.Menu.add_cascade = _casc_

_o_cmd = tk.Menu.add_command
def _cmd_(self, cnf={}, **kw):
    if kw.get("label"):
        _cmds.append(kw["label"])
    return _o_cmd(self, cnf, **kw)
tk.Menu.add_command = _cmd_

@atexit.register
def _dump():
    print("SPY_TABS=" + ascii(_tabs))
    print("SPY_BTNS=" + ascii(_btns))
    print("SPY_CASC=" + ascii(_casc))
    print("SPY_CMDS=" + ascii(_cmds))
    print("SPY_LBLS=" + ascii(_labels))
"""


def _spy(tmp_path):
    """Build the window with the spies installed; return the four lists."""
    r = _launch_in_child(tmp_path, extra_setup=_SPY)
    assert r.returncode == 0, f"GUI build failed:\n{r.stdout}\n{r.stderr}"
    out = {}
    for line in r.stdout.splitlines():
        for key in ("SPY_TABS", "SPY_BTNS", "SPY_CASC", "SPY_CMDS"):
            if line.startswith(key + "="):
                out[key] = ast.literal_eval(line[len(key) + 1:])
    missing = {"SPY_TABS", "SPY_BTNS", "SPY_CASC", "SPY_CMDS"} - set(out)
    assert not missing, f"spy produced no {missing}:\n{r.stdout}\n{r.stderr}"
    return out["SPY_TABS"], out["SPY_BTNS"], out["SPY_CASC"], out["SPY_CMDS"]


@needs_display
def test_results_is_a_tab(tmp_path):
    tabs, _btns, _casc, _cmds = _spy(tmp_path)
    assert "Run" in tabs, (
        f"the spy saw no Run tab, so this test cannot see tabs at all: {tabs}")
    assert "Results" in tabs, f"no Results tab was built: {tabs}"


@needs_display
def test_the_three_confusable_buttons_are_gone_from_the_bar(tmp_path):
    """`Report` / `Copy report` / `Export diagnostics` must not be buttons.

    This is the regression that matters: re-adding any of them re-creates the
    collision the revamp removed.
    """
    _tabs, btns, _casc, _cmds = _spy(tmp_path)
    assert "Convert" in btns, (
        f"the spy saw no Convert button, so it cannot see buttons: {btns}")
    for gone in ("Report", "Copy report", "Export diagnostics"):
        assert gone not in btns, (
            f"{gone!r} is a button again -- the reporting surface was "
            f"reorganised by intent (Results tab / Help menu) precisely so "
            f"that this word does not mean two things: {btns}")


@needs_display
def test_help_actions_live_in_the_menu_bar(tmp_path):
    tabs, _btns, casc, cmds = _spy(tmp_path)
    assert tabs, "no tabs recorded -- the window did not build"
    for menu in ("File", "Tools", "Help"):
        assert menu in casc, f"no {menu} menu: {casc}"
    for item in ("Copy problem report", "Save diagnostics zip…"):
        assert item in cmds, f"{item!r} missing from the menus: {cmds}"


@needs_display
def test_both_help_deliveries_are_offered_together(tmp_path):
    """Copy and zip are two DELIVERIES of one payload, so they belong side by
    side. They used to be two separate buttons at opposite ends of the bar, and
    the zip's own cover sheet is the same text the copy produces."""
    _tabs, _btns, _casc, cmds = _spy(tmp_path)
    assert "Copy problem report" in cmds and "Save diagnostics zip…" in cmds
    assert abs(cmds.index("Copy problem report")
               - cmds.index("Save diagnostics zip…")) == 1, (
        f"the two help deliveries drifted apart in the menu: {cmds}")


@needs_display
def test_the_web_links_are_reachable_from_the_menu(tmp_path):
    """The issue tracker and discussions URLs used to appear ONLY in a log line
    printed after a diagnostics export -- i.e. only if you already knew to
    export. They are menu items now."""
    _tabs, _btns, _casc, cmds = _spy(tmp_path)
    assert any("issue" in c.lower() for c in cmds), cmds
    assert any("question" in c.lower() or "discussion" in c.lower()
               for c in cmds), cmds


def test_the_source_no_longer_names_a_copy_report_button():
    """The diagnostics-export guidance used to end with 'send the report from
    the Copy report button'. That button does not exist any more, so the advice
    would send someone hunting for it."""
    src = (PROJ / "src" / "gui.py").read_text(encoding="utf-8")
    assert "'Copy report' button" not in src, (
        "gui.py still points the user at a 'Copy report' button")


# --------------------------------------------------------------------------
# Renaming a control is only half the job -- the docs that name it are the
# other half, and they fail silently
# --------------------------------------------------------------------------

# User-facing prose that TELLS SOMEONE WHICH CONTROL TO CLICK. gui.py is not in
# the list on purpose: its comments discuss the old names as history, which is
# the point of them.
_USER_FACING = (
    "README.md",
    "REPORTING.md",
    "CONTRIBUTING.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/conversion_problem.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
)

# Retired control labels. Each was a BUTTON that no longer exists; prose that
# still names one sends the reader looking for a control that is not there.
_RETIRED = ("Copy report", "Export diagnostics")


@pytest.mark.parametrize("rel", _USER_FACING)
def test_no_doc_tells_you_to_click_a_control_that_is_gone(rel):
    """This is the failure a UI rename actually ships.

    The revamp renamed three controls and moved two into a menu. The code
    changed, the tests passed, and TEN references across six user-facing files
    went on naming buttons that no longer exist -- found only by grepping for
    them afterwards. Nothing failed. This makes it fail.
    """
    p = PROJ / rel
    if not p.is_file():
        pytest.skip(f"{rel} not present")
    text = p.read_text(encoding="utf-8")
    for gone in _RETIRED:
        assert gone not in text, (
            f"{rel} still tells the reader to use {gone!r}, which is not a "
            f"control any more. The reporting surface is now: the Results TAB "
            f"for how a run went, and Help > Copy problem report / Help > Save "
            f"diagnostics zip for asking for help.")


def test_the_emitted_report_names_a_control_that_exists():
    """The report text is itself user-facing -- it is pasted into issues and
    chat, and its DIAGNOSTICS line tells the reader how to produce the zip. It
    named 'GUI -> Export diagnostics' after that button was removed."""
    from src import report_template as rt
    text = rt.build_report("1.3", kind="conversion", report=None)
    assert text, "build_report produced nothing -- this check would be vacuous"
    for gone in _RETIRED:
        assert gone not in text, (
            f"the emitted problem report still names {gone!r}: {text}")
    assert "Save diagnostics zip" in text, (
        "the report no longer tells the reader how to produce the zip at all")


@needs_display
def test_every_menu_path_the_docs_promise_actually_exists(tmp_path):
    """The positive half. The denylist above catches names that are GONE; this
    catches a doc that promises a menu item which was never added -- the same
    defect from the other side."""
    _tabs, _btns, _casc, cmds = _spy(tmp_path)
    promised = set()
    for rel in _USER_FACING:
        p = PROJ / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        for item in ("Copy problem report", "Save diagnostics zip"):
            if item in text:
                promised.add(item)
    assert promised, (
        "no doc names a Help menu item -- either the docs regressed or this "
        "check stopped finding them, and a silent empty set would pass")
    # Menu labels may carry a trailing ellipsis; compare on the stem.
    stems = {c.rstrip("… .") for c in cmds}
    for item in sorted(promised):
        assert item in stems, (
            f"the docs tell people to use {item!r} but the GUI builds no such "
            f"menu item: {sorted(stems)}")


# --------------------------------------------------------------------------
# The tab must RENDER, not merely exist
# --------------------------------------------------------------------------

def _spy_labels_with_report(tmp_path, report):
    """Build the window with a conversion_report.json already on disk, so the
    Results tab has something to paint. Returns the recorded Label texts.

    The output folder the GUI defaults to is `<mods_root>/CBBEtoUBE Auto`, and
    the smoke harness points `CBBE2UBE_MODS_ROOT` at `tmp_path/mods` -- so
    writing the report there is what a finished run would have left behind.
    """
    out = tmp_path / "mods" / "CBBEtoUBE Auto"
    out.mkdir(parents=True, exist_ok=True)
    (out / "conversion_report.json").write_text(json.dumps(report),
                                                encoding="utf-8")
    r = _launch_in_child(tmp_path, extra_setup=_SPY)
    assert r.returncode == 0, f"GUI build failed:\n{r.stdout}\n{r.stderr}"
    for line in r.stdout.splitlines():
        if line.startswith("SPY_LBLS="):
            return ast.literal_eval(line[len("SPY_LBLS="):])
    raise AssertionError(f"spy recorded no labels:\n{r.stdout}\n{r.stderr}")


@needs_display
def test_the_results_tab_actually_paints_the_scoreboard(tmp_path):
    """THE TEST THAT WAS MISSING, and it cost a real bug.

    When the modal became a tab, the render body ended up one indent level too
    deep -- swallowed into the `for w in ...: w.destroy()` loop above it. On the
    first paint that loop has no children, so the body never ran and the tab
    drew NOTHING. Every structural test still passed, because they only asserted
    the tab EXISTED. A tab that exists and renders nothing is the same failure
    mode as a metric that reports no problem because it cannot see one.
    """
    labels = _spy_labels_with_report(tmp_path, {
        "source_mods": 7, "converted_ok": 7, "armor_nifs": 42,
        "esp_patches": 3, "hard_failures": 0, "nif_errors": 0,
        "load_failures": 0, "zero_mesh_mods": [], "failed_mods": [],
        "weight_partner_warnings": [],
    })
    assert labels, "no labels recorded at all -- this check would be vacuous"
    for want in ("Source mods: ", "Converted OK: ", "Armor NIFs written: "):
        assert want in labels, (
            f"the Results tab did not paint {want!r}. The scoreboard is not "
            f"rendering: {[l for l in labels if ':' in str(l)][:20]}")


@needs_display
def test_the_results_heading_dates_the_run(tmp_path):
    """The panel persists across sessions now, so it must say WHEN. An undated
    scoreboard invites reading last week's run as this one's."""
    labels = _spy_labels_with_report(tmp_path, {"source_mods": 1,
                                                "converted_ok": 1})
    assert any(str(l).startswith("Last run — ") and len(str(l)) > len("Last run — ")
               for l in labels), (
        f"the Results heading carries no run date: "
        f"{[l for l in labels if 'Last run' in str(l)]}")


@needs_display
def test_with_no_report_the_tab_says_so_rather_than_going_blank(tmp_path):
    """The empty state has to be legible too -- a blank tab reads as broken."""
    r = _launch_in_child(tmp_path, extra_setup=_SPY)
    assert r.returncode == 0, f"GUI build failed:\n{r.stdout}\n{r.stderr}"
    labels = None
    for line in r.stdout.splitlines():
        if line.startswith("SPY_LBLS="):
            labels = ast.literal_eval(line[len("SPY_LBLS="):])
    assert labels, "no labels recorded -- vacuous"
    assert "No run yet" in labels, f"empty-state heading missing: {labels[:20]}"
    assert any("No conversion_report.json" in str(l) for l in labels), (
        "the empty Results tab explains nothing")
