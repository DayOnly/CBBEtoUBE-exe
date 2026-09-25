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

"""#settings-everywhere -- the saved settings reach every run and every helper.

Measured 2026-09-25 on 94340ee: the window applied CBBEtoUBE_settings.json only
to its conversion child. Check setup ran in the window with the bare
environment, so a UBE body picked on the Paths tab was reported missing (and
every Convert then asked "convert anyway?"); a headless `CBBEtoUBE.exe auto`
never read the file at all and said "effective settings: all registry
defaults" over a recipe the user had saved."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import build_info as bi
from src import gui
from src import gui_settings as gs
from src import preflight as pf

REPO = Path(__file__).resolve().parents[1]
RECIPE = {"conform_to_body": False, "layer_order_last": True}


def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_settings", REPO / "cbbe_to_ube_main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A private process environment holding a saved recipe, and no record
    of an earlier apply."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    settings = tmp_path / "CBBEtoUBE_settings.json"
    assert gs.save_values({**gs.defaults(), **RECIPE}, settings)
    e["CBBE2UBE_CONFIG"] = str(settings)
    monkeypatch.setattr(os, "environ", e)
    monkeypatch.setattr(gs, "_HEADLESS_REPORT", None)
    return e


def _recipe_env():
    return gs.apply_env({**gs.defaults(), **RECIPE}, base_env={})


# --- a headless run -----------------------------------------------------------------

def test_a_headless_auto_runs_with_the_saved_settings(env):
    want = _recipe_env()
    assert want, "the recipe must change at least one variable"
    _entry_module()._apply_saved_settings(["auto", "--workers", "2"])
    for var, val in want.items():
        assert env.get(var) == val, (var, env.get(var))
    rep = gs.headless_report()
    assert rep["applied"] == want and rep["kept"] == {} and not rep["skipped"]


def test_the_entry_applies_them_before_the_converter_runs(env, monkeypatch):
    """`_run` itself -- the frozen exe's path -- not only the helper."""
    m = _entry_module()
    seen = {}
    monkeypatch.setattr(ac, "main", lambda: seen.update(
        {k: os.environ.get(k) for k in _recipe_env()}) or 0)
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", "auto"])
    assert m._run() == 0
    assert seen == _recipe_env(), seen


def test_convert_applies_them_too_and_help_or_validate_does_not(env):
    m = _entry_module()
    for argv in (["auto", "--help"], ["validate", "x"], ["merge"], []):
        m._apply_saved_settings(argv)
        assert not any(k in env for k in _recipe_env()), argv
    m._apply_saved_settings(["convert", "-o", "out", "src"])
    assert all(env.get(k) == v for k, v in _recipe_env().items())


def test_a_variable_already_set_wins_over_the_file(env):
    var = sorted(_recipe_env())[0]
    env[var] = "explicit"
    gs.apply_saved_settings()
    assert env[var] == "explicit"
    rep = gs.headless_report()
    assert rep["kept"] == {var: "explicit"} and var not in rep["applied"]


def test_the_log_says_where_the_settings_came_from_and_what_was_set(env, capsys):
    gs.apply_saved_settings()
    ac._echo_active_experiment_flags()
    log = capsys.readouterr().out
    line = next(ln for ln in log.splitlines() if "effective settings: from" in ln)
    assert env["CBBE2UBE_CONFIG"] in line
    for var, val in _recipe_env().items():
        assert f"{var[9:]}={val}" in line, line
    assert "conform_to_body=False" in log and "layer_order_last=True" in log, log
    assert bi.run_config()["settings_applied"]["applied"] == _recipe_env()


def test_switched_off_the_environment_is_left_exactly_as_it_was(env):
    env[gs.HEADLESS_SWITCH] = "1"
    before = dict(env)
    rep = gs.apply_saved_settings()
    assert env == before
    assert rep["skipped"] == f"{gs.HEADLESS_SWITCH}=1"
    assert "NOT applied" in gs.settings_source_line()


def test_a_torn_file_applies_nothing(env):
    Path(env["CBBE2UBE_CONFIG"]).write_text('{"conform_to_body": fa', encoding="utf-8")
    before = dict(env)
    assert gs.apply_saved_settings()["skipped"] == "settings file malformed"
    assert env == before


# --- the window's own child ----------------------------------------------------------

def test_the_windows_child_does_not_read_the_file_again(env):
    """The window already applied what it HOLDS, which a failed save can make
    newer than the file; the child must not lay the file over it."""
    child = gui.child_env({**gs.defaults()}, {}, "run.log", base_env=dict(env))
    assert not any(k in child for k in _recipe_env()), "the window holds defaults"
    before = dict(child)
    rep = gs.apply_saved_settings(environ=child)
    assert rep == {"by": "the settings window"}, rep
    assert child == before, "the child laid the file over what the window applied"


# --- helpers in the window --------------------------------------------------------------

def test_check_setup_sees_the_ube_body_picked_on_the_paths_tab(env, tmp_path, monkeypatch):
    body = tmp_path / "picked_femalebody_1.nif"
    body.write_bytes(b"not read here")
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda: None)
    settings = {**gs.defaults(), "ube_body": str(body)}
    with gs.SettingsOverlay(settings):
        inside = pf._probe_ube_body()
    assert inside is not None and Path(inside) == body, inside
    assert "CBBE2UBE_UBE_BODY" not in env, "the overlay must not outlive the helper"


def test_the_windows_launch_check_runs_under_the_saved_settings(tmp_path):
    """The window itself: its launch-time Check setup sees the Paths-tab body.
    Built in a child interpreter, as tests/test_gui_smoke.py does."""
    from tests.test_gui_smoke import _launch_in_child, _no_display
    if _no_display():
        pytest.skip("no display: cannot construct a tkinter window")
    body = tmp_path / "picked_femalebody_1.nif"
    body.write_bytes(b"x")
    assert gs.save_values({**gs.defaults(), "ube_body": str(body)},
                          tmp_path / "settings.json")
    spy = (
        'import src.preflight as _pf\n'
        'def _spy(*a, **k):\n'
        '    print("CHECK_SAW=%s" % os.environ.get("CBBE2UBE_UBE_BODY"), flush=True)\n'
        '    return []\n'
        '_pf.run_checks = _spy'
    )
    r = _launch_in_child(tmp_path, extra_setup=spy)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"CHECK_SAW={body}" in r.stdout, r.stdout + r.stderr


def test_the_overlay_is_the_childs_environment_and_is_put_back(env):
    stale = sorted(_recipe_env())[0]
    env[stale] = "left by the shell"          # a registry var at its default in
    with gs.SettingsOverlay({**gs.defaults(), "vanilla_sweep": False}):
        assert stale not in env, "apply_env pops a registry var at its default"
        assert ac._flag("CBBE2UBE_NO_VANILLA_SWEEP", False)
    assert env[stale] == "left by the shell"
    assert "CBBE2UBE_NO_VANILLA_SWEEP" not in env


def test_overlapping_helpers_keep_the_overlay_until_the_last_one_leaves(env):
    settings = {**gs.defaults(), "vanilla_sweep": False}
    first, second = gs.SettingsOverlay(settings), gs.SettingsOverlay(settings)
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    assert env.get("CBBE2UBE_NO_VANILLA_SWEEP") == "1", "restored under a running helper"
    second.__exit__(None, None, None)
    assert "CBBE2UBE_NO_VANILLA_SWEEP" not in env


def test_the_run_config_names_the_window_when_the_window_applied_them(env, monkeypatch):
    """#settings-source-line. The REAL path of a window run: the child's
    environment is what `child_env` builds, and the child goes through the
    entry point's `_apply_saved_settings` like every `auto`. It logged
    "settings file NOT applied (already applied by the settings window)" on
    every window run; the old version of this test hid that by forcing the
    report back to None after the apply."""
    child = gui.child_env({**gs.defaults(), **RECIPE}, {}, "run.log",
                          base_env=dict(env))
    monkeypatch.setattr(os, "environ", child)
    _entry_module()._apply_saved_settings(["auto", "--workers", "2"])
    line = gs.settings_source_line()
    assert line == "  effective settings: from the settings window", line
    assert bi.run_config()["settings_applied"] == {"by": "the settings window"}
    json.dumps(bi.run_config(), default=str)


def test_a_file_that_was_really_skipped_still_says_not_applied(env):
    """The control: 'NOT applied' stays for a file that genuinely was not."""
    Path(env["CBBE2UBE_CONFIG"]).write_text('{"conform_to_body": fa', encoding="utf-8")
    _entry_module()._apply_saved_settings(["auto"])
    line = gs.settings_source_line()
    assert "settings file NOT applied (settings file malformed)" in line, line
    assert bi.run_config()["settings_applied"]["skipped"] == "settings file malformed"
