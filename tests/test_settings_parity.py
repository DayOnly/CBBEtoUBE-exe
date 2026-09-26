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

"""#check-setup-settings, #headless-exclusions -- a run without the window
reads what the window would.

Measured 2026-09-26 on c0367ee. `CBBEtoUBE.exe check-setup`, which USING.md
says "writes the same checks" as the window's Check setup, read the bare
environment: with a UBE body picked on the Paths tab the window said OK and
the command said "UBE body reference built: FAIL" and exited 1. And a
headless `auto` applied CBBEtoUBE_settings.json but not
CBBEtoUBE_exclusions.json, so a mod the user had marked as already built for
UBE was converted again. Both follow #settings-everywhere: same entry point,
same off-switch (CBBE2UBE_NO_HEADLESS_SETTINGS=1), and the window's own child
(CBBE2UBE_SETTINGS_APPLIED) is left alone."""
import importlib.util
import os
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import exclusions as ex
from src import gui
from src import gui_settings as gs
from src import nif_convert_bodyrefs as br
from src import paths
from src import preflight as pf

REPO = Path(__file__).resolve().parents[1]
UBE_ROW = "UBE body reference built"


def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_parity", REPO / "cbbe_to_ube_main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A private environment, an empty modlist, and saved files that pick a
    UBE body; no record of an earlier apply."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    mods = tmp_path / "mods"
    mods.mkdir()
    body = tmp_path / "picked" / "femalebody_1.nif"
    body.parent.mkdir()
    body.write_bytes(b"not read here")
    settings = tmp_path / "CBBEtoUBE_settings.json"
    assert gs.save_values({**gs.defaults(), "ube_body": str(body)}, settings)
    e["CBBE2UBE_CONFIG"] = str(settings)
    e["CBBE2UBE_EXCLUSIONS"] = str(tmp_path / "CBBEtoUBE_exclusions.json")
    e["CBBE2UBE_MODS_ROOT"] = str(mods)
    monkeypatch.setattr(os, "environ", e)
    monkeypatch.setattr(gs, "_HEADLESS_REPORT", None)
    lay = paths.Layout(mods_root=mods)
    monkeypatch.setattr(paths, "discover_layout", lambda start=None: lay)
    # Only the override can name the body: nothing found by name, no CBBE body.
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda: None)
    monkeypatch.setattr(pf, "_probe_cbbe_body", lambda: None)
    br._BODY_DISCOVERY_CACHE.clear()   # mutate, never rebind: shared object
    yield {"env": e, "body": body, "mods": mods}
    br._BODY_DISCOVERY_CACHE.clear()


def _window_rows():
    """The window's Check setup: run_checks under the saved settings."""
    br._BODY_DISCOVERY_CACHE.clear()
    with gs.SettingsOverlay(gs.load_values()):
        checks = pf.run_checks()
    br._BODY_DISCOVERY_CACHE.clear()
    return checks


def _cli(monkeypatch, capsys):
    """`CBBEtoUBE.exe check-setup`, through the entry point's `_run`."""
    m = _entry_module()
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", "check-setup"])
    capsys.readouterr()
    rc = m._run()
    return rc, capsys.readouterr().out.splitlines()


def _row(lines, label):
    return next(ln for ln in lines if label in ln)


# --- check-setup ---------------------------------------------------------------

def test_cli_check_setup_and_the_window_agree_on_the_picked_body(env, monkeypatch, capsys):
    win = _window_rows()
    win_lines = pf.format_checks(win)
    assert str(env["body"]) in _row(win_lines, UBE_ROW), win_lines
    rc, lines = _cli(monkeypatch, capsys)
    assert _row(lines, UBE_ROW) == _row(win_lines, UBE_ROW), lines
    assert rc == (1 if pf.overall(win) == pf.FAIL else 0)
    assert "CBBE2UBE_UBE_BODY" in env["env"], "applied for the run, as auto does"


def test_check_setup_says_where_its_settings_came_from(env, monkeypatch, capsys):
    _rc, lines = _cli(monkeypatch, capsys)
    assert lines[0].startswith("effective settings: from "), lines[:3]
    assert env["env"]["CBBE2UBE_CONFIG"] in lines[0]
    assert "UBE_BODY=" in lines[0], lines[0]


def test_switched_off_check_setup_ignores_the_file_as_before(env, monkeypatch, capsys):
    env["env"][gs.HEADLESS_SWITCH] = "1"
    rc, lines = _cli(monkeypatch, capsys)
    assert "FAIL" in _row(lines, UBE_ROW).upper() and str(env["body"]) not in "\n".join(lines)
    assert rc == 1
    assert "NOT applied" in lines[0], lines[0]


# --- the saved exclusions on a headless auto -------------------------------------

def _save_exclusions(armor=(), overlay=()):
    st = ex.empty()
    ex.set_excluded(st, "armor", list(armor))
    ex.set_excluded(st, "overlay", list(overlay))
    assert ex.save(st)


def _headless(argv, apply=True):
    """Parse `argv` as the entry point would run it, then apply the saved
    exclusions the way `_cmd_auto` does."""
    if apply:
        _entry_module()._apply_saved_settings(argv)
    args = ac._build_parser().parse_args(argv)
    ac._apply_saved_exclusions(args)
    return args


def _window(argv_extra):
    return ac._build_parser().parse_args(["auto"] + argv_extra)


SEL = ("exclude_mods", "coverage_exclude_mods", "only_mods",
       "overlay_exclude_mods", "overlay_mods")


def _sel(args):
    return {a: ac._split_mod_arg(getattr(args, a, None)) for a in SEL}


def test_an_all_mods_auto_skips_them_as_the_window_does(env):
    _save_exclusions(armor=["Built Mod", "Leave Alone"])
    got = _headless(["auto"])
    want = _window(gui._armor_selection_argv(False, [], ["Built Mod", "Leave Alone"]))
    assert _sel(got) == _sel(want), _sel(got)
    assert ac._split_mod_arg(got.exclude_mods) == ["Built Mod", "Leave Alone"]


def test_with_only_mods_the_unpicked_ones_get_no_coverage_as_the_window_does(env):
    _save_exclusions(armor=["Built Mod", "Leave Alone"])
    got = _headless(["auto", "--only-mods", "Picked", "--only-mods", "leave alone"])
    want = _window(gui._armor_selection_argv(True, ["Picked", "leave alone"],
                                             ["Built Mod", "Leave Alone"]))
    assert _sel(got) == _sel(want), _sel(got)
    assert got.exclude_mods is None, "a pick is not gated, as in the window"
    assert ac._split_mod_arg(got.coverage_exclude_mods) == ["Built Mod"]


def test_overlay_exclusions_follow_the_overlay_selection(env):
    _save_exclusions(overlay=["Tattoo Mod"])
    got = _headless(["auto", "--convert-overlays"])
    assert ac._split_mod_arg(got.overlay_exclude_mods) == ["Tattoo Mod"]
    picked = _headless(["auto", "--convert-overlays", "--overlay-mods", "Other"],
                       apply=False)
    assert picked.overlay_exclude_mods is None, "a pick is not gated"
    plain = _headless(["auto"], apply=False)
    assert plain.overlay_exclude_mods is None, "no overlay run, nothing to pass"


def test_an_overlays_only_run_passes_no_armour_exclusions(env):
    """The window sends the armour selection only when armour is converted."""
    _save_exclusions(armor=["Built Mod"], overlay=["Tattoo Mod"])
    got = _headless(["auto", "--overlays-only"])
    assert got.exclude_mods is None and got.coverage_exclude_mods is None
    assert ac._split_mod_arg(got.overlay_exclude_mods) == ["Tattoo Mod"]


def test_a_name_on_the_command_line_is_kept_and_not_added_twice(env):
    _save_exclusions(armor=["Built Mod", "Leave Alone"])
    got = _headless(["auto", "--exclude-mods", "built mod"])
    assert ac._split_mod_arg(got.exclude_mods) == ["built mod", "Leave Alone"]


def test_the_windows_child_gets_them_as_arguments_only(env, monkeypatch):
    _save_exclusions(armor=["Built Mod"])
    child = gui.child_env({**gs.defaults()}, {}, "run.log", base_env=dict(env["env"]))
    monkeypatch.setattr(os, "environ", child)
    got = _headless(["auto"])
    assert got.exclude_mods is None and got.coverage_exclude_mods is None


def test_switched_off_a_headless_auto_ignores_them_as_before(env):
    _save_exclusions(armor=["Built Mod"], overlay=["Tattoo Mod"])
    env["env"][gs.HEADLESS_SWITCH] = "1"
    got = _headless(["auto", "--convert-overlays"])
    assert _sel(got) == _sel(_window(["--convert-overlays"]))


def test_a_run_the_entry_point_did_not_prepare_reads_only_its_arguments(env):
    """`python -m src.auto_convert auto` and a direct call: no headless report."""
    _save_exclusions(armor=["Built Mod"])
    got = _headless(["auto"], apply=False)
    assert got.exclude_mods is None


def test_the_run_itself_skips_them(env, monkeypatch, capsys):
    """`_cmd_auto` end to end as far as the candidate scan: the saved
    exclusion reaches the list of mods the scan leaves out."""
    _save_exclusions(armor=["Built Mod"])
    _entry_module()._apply_saved_settings(["auto"])
    seen = {}

    def _scan(mr, extra_exclude_names=(), **_kw):
        seen["exclude"] = set(extra_exclude_names)
        return []
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", _scan)
    assert ac.main(["auto"]) == 2
    assert "Built Mod" in seen["exclude"], seen
    out = capsys.readouterr().out
    assert "saved exclusions (" in out and "1 armour mod(s) not converted" in out, out
