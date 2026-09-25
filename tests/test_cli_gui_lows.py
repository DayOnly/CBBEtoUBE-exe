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

"""Eight low findings from the CLI/GUI bug hunt on 94340ee, by value.

  #workers-box-guard           a bad Worker processes value stranded the window
  #dry-run-keeps-the-run-log   a dry run rotated the dead run's log away
  #select-list-ube-native      the Select list offered mods the run drops
  #mod-scan-rescan             Refresh returned the first scan of the session
  #read-only-tool-folder       no log, no progress, saves failing silently
  #gui-session-log-kept        the window's own log closed after the first run
  #fingerprint-skips-plumbing  --incremental reconverted on a launch detail
  #whole-mod-names             a folder name with a comma arrived as two names

Tk-free: the window's decisions live in module-level helpers of src/gui.py."""
import argparse
import importlib.util
import io
import os
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src import auto_convert as ac      # noqa: E402
from src import gui                     # noqa: E402

COMMA_MOD = "Armor, Clothing Pack"


def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_cli_gui_lows", REPO / "cbbe_to_ube_main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.upper().startswith("CBBE2UBE_"):
            monkeypatch.delenv(k, raising=False)


# --- #workers-box-guard -------------------------------------------------------

@pytest.mark.parametrize("raw", ["", "  ", "abc", "0", "-1", "4.5", None, "4 4"])
def test_a_worker_count_that_is_not_a_whole_number_is_refused(raw):
    assert gui.parse_workers(raw) is None


@pytest.mark.parametrize("raw,n", [("4", 4), (" 12 ", 12), (3, 3), ("1", 1)])
def test_a_whole_worker_count_is_read(raw, n):
    assert gui.parse_workers(raw) == n


def test_a_failed_argument_build_locks_nothing():
    calls = []

    def build():
        raise ValueError("Worker processes must be a whole number")

    ok, err = gui.start_run(build, lambda: calls.append("lock"),
                            lambda a: calls.append("start"),
                            lambda e: calls.append("unlock"))
    assert not ok and "Worker processes" in err
    assert calls == [], "the window was locked before the arguments were built"


def test_a_start_that_raises_unlocks_the_window():
    calls = []

    def start(argv):
        calls.append(("start", tuple(argv)))
        raise OSError("no thread")

    ok, err = gui.start_run(lambda: ["auto"], lambda: calls.append("lock"),
                            start, lambda e: calls.append(("unlock", "OSError" in e)))
    assert not ok
    assert calls == ["lock", ("start", ("auto",)), ("unlock", True)]


def test_a_good_launch_builds_then_locks_then_starts():
    calls = []
    ok, err = gui.start_run(lambda: calls.append("build") or ["auto"],
                            lambda: calls.append("lock"),
                            lambda a: calls.append("start"),
                            lambda e: calls.append("unlock"))
    assert ok and err == ""
    assert calls == ["build", "lock", "start"]


# --- #dry-run-keeps-the-run-log -----------------------------------------------

@pytest.mark.parametrize("argv", [
    ["auto", "--list-only"], ["auto", "--dry-run"], ["auto", "--workers", "4", "--dry"],
    ["auto", "--list"],
])
def test_a_dry_run_writes_the_cli_log_and_rotates_nothing(argv):
    m = _entry_module()
    assert m._log_target(argv) == ("CBBEtoUBE_cli.log", False, False)


@pytest.mark.parametrize("argv", [
    ["auto"], ["auto", "--workers", "4"], ["auto", "--", "--dry-run"],
    ["convert", "--list-only"],
])
def test_a_real_run_still_rotates(argv):
    m = _entry_module()
    assert m._log_target(argv) == ("CBBEtoUBE_last_run.log", True, True)


@pytest.mark.parametrize("dry", [True, False])
def test_the_window_tails_the_log_its_child_writes(dry):
    """The window's plan and the child's _log_target must name one file, or
    the window tails a file nobody writes."""
    m = _entry_module()
    argv = ["auto", "--workers", "2"] + gui._run_mode_argv(dry, True, True)
    name, _use, rotate = m._log_target(argv)
    assert gui.run_log_plan(dry) == (name, rotate)


def _after_a_dead_run(folder):
    (folder / "CBBEtoUBE_last_run.log").write_text("the run that died",
                                                   encoding="utf-8")
    (folder / "CBBEtoUBE_last_failures.json").write_text('{"failures": ["x"]}',
                                                         encoding="utf-8")
    (folder / "CBBEtoUBE_cli.log").write_text("an older command",
                                              encoding="utf-8")


def test_two_dry_runs_after_a_dead_run_keep_its_log(tmp_path):
    _after_a_dead_run(tmp_path)
    for _ in range(2):
        log_path, fail_path = gui.prepare_child_log(tmp_path, dry_run=True)
        assert Path(log_path).name == "CBBEtoUBE_cli.log"
        assert fail_path is None, "a dry run writes no failures file to show"
        Path(log_path).write_text("dry run output", encoding="utf-8")
    assert (tmp_path / "CBBEtoUBE_last_run.log").read_text(
        encoding="utf-8") == "the run that died"
    assert (tmp_path / "CBBEtoUBE_last_failures.json").is_file()
    assert not (tmp_path / "CBBEtoUBE_previous_run.log").exists()
    assert not (tmp_path / "CBBEtoUBE_previous_failures.json").exists()


def test_a_dry_run_starts_from_an_empty_cli_log(tmp_path):
    """Else the tail streams the previous command's text into the window."""
    _after_a_dead_run(tmp_path)
    log_path, _f = gui.prepare_child_log(tmp_path, dry_run=True)
    assert not Path(log_path).exists()


def test_only_the_cli_log_is_ever_discarded(tmp_path):
    _after_a_dead_run(tmp_path)
    assert gui.discard_dry_log(tmp_path / "CBBEtoUBE_last_run.log") is False
    assert (tmp_path / "CBBEtoUBE_last_run.log").is_file()
    assert gui.discard_dry_log(tmp_path / "CBBEtoUBE_cli.log") is True


def test_a_real_run_from_the_window_still_rotates(tmp_path):
    _after_a_dead_run(tmp_path)
    log_path, fail_path = gui.prepare_child_log(tmp_path, dry_run=False)
    assert Path(log_path).name == "CBBEtoUBE_last_run.log"
    assert (tmp_path / "CBBEtoUBE_previous_run.log").read_text(
        encoding="utf-8") == "the run that died"
    assert fail_path and Path(fail_path).name == "CBBEtoUBE_last_failures.json"


# --- #select-list-ube-native and #mod-scan-rescan ------------------------------

def _modlist(tmp_path, monkeypatch, names):
    mods = tmp_path / "mods"
    for n in names:
        (mods / n).mkdir(parents=True, exist_ok=True)
    _clean_env(monkeypatch)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_NO_VANILLA_SWEEP", "1")
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    return mods


def _native_verdicts(monkeypatch, native_names):
    monkeypatch.setattr(ac, "_body_trees", lambda: ("u", "c"))
    monkeypatch.setattr(
        ac, "_ube_native_verdict",
        lambda p, u, c: (("ube", "high", ["fit"]) if Path(p).name in native_names
                         else ("cbbe", "high", ["fit"])))


def test_the_select_list_leaves_out_what_the_run_drops(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Already UBE", "CBBE Mod"])
    cands = [{"name": n, "path": mods / n, "armor_nifs": 2}
             for n in ("Already UBE", "CBBE Mod")]
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: list(cands))
    _native_verdicts(monkeypatch, {"Already UBE"})

    items = ac.list_convertible_mods(mark_ube_native=True)
    shown, hidden = gui.select_list_split(items)
    run_keeps = {c["name"] for c in ac._drop_ube_native_candidates(list(cands))}
    assert {it["name"] for it in shown} == run_keeps == {"CBBE Mod"}
    assert hidden == ["Already UBE"]
    # Unmarked, the Exclusions list and the UBE-mesh scan still see every mod.
    assert {it["name"] for it in ac.list_convertible_mods()} == {
        "Already UBE", "CBBE Mod"}


def test_only_mods_on_a_dropped_mod_says_why(tmp_path, monkeypatch, capsys):
    mods = _modlist(tmp_path, monkeypatch, ["Already UBE", "CBBE Mod"])
    cands = [{"name": n, "path": mods / n, "armor_nifs": 2}
             for n in ("Already UBE", "CBBE Mod")]
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: list(cands))
    _native_verdicts(monkeypatch, {"Already UBE"})
    rc = ac.main(["auto", "-o", str(tmp_path / "out"), "--list-only",
                  "--only-mods", "Already UBE"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "'already ube' -- skipped by the UBE-native scan" in out, out[-1500:]


def test_refresh_reads_a_mod_updated_since_the_last_scan(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Mod A"])
    ac._ARMOR_MOD_DIRS_CACHE.clear()
    found = [[{"name": "Mod A", "path": mods / "Mod A", "armor_nifs": 1}]]
    monkeypatch.setattr(ac, "_find_armor_mod_dirs_uncached",
                        lambda *a, **k: list(found[0]))
    try:
        assert [i["nifs"] for i in ac.list_convertible_mods()] == [1]
        found[0] = [{"name": "Mod A", "path": mods / "Mod A", "armor_nifs": 5}]
        assert [i["nifs"] for i in ac.list_convertible_mods()] == [1], (
            "control: without rescan the memo answers")
        assert [i["nifs"] for i in ac.list_convertible_mods(rescan=True)] == [5]
    finally:
        ac._ARMOR_MOD_DIRS_CACHE.clear()


# --- #read-only-tool-folder ---------------------------------------------------

def test_a_writable_folder_is_writable_and_the_probe_leaves_nothing(tmp_path):
    assert gui.folder_write_error(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_a_folder_that_cannot_take_a_file_says_why(tmp_path):
    not_a_folder = tmp_path / "file.txt"
    not_a_folder.write_text("x", encoding="utf-8")
    err = gui.folder_write_error(not_a_folder)
    assert err and "Error" in err
    assert str(tmp_path) in gui.read_only_folder_note(tmp_path, err)


class _Proc:
    def __init__(self, rc=0):
        self.rc = rc

    def poll(self):
        return self.rc


def test_a_run_that_leaves_no_log_is_said_out_loud(tmp_path):
    said = []
    log = tmp_path / "CBBEtoUBE_last_run.log"
    assert gui.tail_child_log(_Proc(1), str(log), said.append,
                              sleep=lambda s: None) is False
    assert said and "wrote no log" in said[-1] and str(log) in said[-1]


def test_a_run_log_is_streamed(tmp_path):
    said = []
    log = tmp_path / "CBBEtoUBE_last_run.log"
    log.write_text("line one\n", encoding="utf-8")
    assert gui.tail_child_log(_Proc(0), str(log), said.append,
                              sleep=lambda s: None) is True
    assert "".join(said) == "line one\n"


def test_a_failed_save_pops_once_per_kind_and_always_says_so():
    n = gui.SaveNotice()
    assert n.check(True, "settings") == (None, None)
    st, pop = n.check(False, "settings")
    assert st and "could NOT be saved" in st and pop
    st2, pop2 = n.check(False, "settings")
    assert st2 and pop2 is None, "one popup per session, not one per slider move"
    st3, pop3 = n.check(False, "exclusions")
    assert "exclusions" in st3 and pop3


def test_an_unwritable_exclusions_file_reports_a_failed_save(tmp_path):
    """The window now reads this value; it used to be dropped."""
    from src import exclusions as excl
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert excl.save(excl.load(tmp_path / "none.json"),
                     blocker / "sub" / "excl.json") is False


# --- #gui-session-log-kept ----------------------------------------------------

def test_the_windows_session_log_survives_a_run(tmp_path, monkeypatch):
    """The window process tees to CBBEtoUBE_gui_session.log; getting the files
    ready for a run (dry or not) must leave that tee open, as the frozen exe
    runs it (the entry point is __main__)."""
    m = _entry_module()
    monkeypatch.setitem(sys.modules, "__main__", m)
    monkeypatch.setattr(m, "_log_dir_candidates", lambda: [str(tmp_path)])
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe"])
    monkeypatch.delenv("CBBE2UBE_RUN_LOG", raising=False)
    m._install_log_tee()
    try:
        gui.prepare_child_log(tmp_path, dry_run=False)
        gui.prepare_child_log(tmp_path, dry_run=True)
        print("after the first run")
    finally:
        m.release_log_tee()
    text = (tmp_path / "CBBEtoUBE_gui_session.log").read_text(encoding="utf-8")
    assert "after the first run" in text


def test_a_window_callback_error_reaches_the_log_and_the_panel():
    said, stream = [], io.StringIO()
    report = gui.tk_error_reporter(said.append, stream=stream)
    try:
        raise KeyError("in a callback")
    except KeyError:
        report(*sys.exc_info())
    assert "KeyError: 'in a callback'" in stream.getvalue()
    assert said and "KeyError: 'in a callback'" in said[-1]


# --- #fingerprint-skips-plumbing ----------------------------------------------

_ARGS = types.SimpleNamespace()


@pytest.mark.parametrize("var,val", [
    ("CBBE2UBE_NO_PAUSE", "1"), ("CBBE2UBE_RUN_LOG", r"C:\x\run.log"),
    ("CBBE2UBE_CONFIG", r"C:\x\settings.json"),
    ("CBBE2UBE_EXCLUSIONS", r"C:\x\excl.json"),
    ("CBBE2UBE_SETTINGS_APPLIED", "the settings window"),
    ("CBBE2UBE_NO_HEADLESS_SETTINGS", "1"),
])
def test_a_launch_detail_does_not_invalidate_the_cache(monkeypatch, var, val):
    _clean_env(monkeypatch)
    base = ac._nif_config_fingerprint(_ARGS)
    monkeypatch.setenv(var, val)
    assert ac._nif_config_fingerprint(_ARGS) == base


@pytest.mark.parametrize("var", ["CBBE2UBE_NO_VANILLA_SWEEP", "CBBE2UBE_MODS_ROOT",
                                 "CBBE2UBE_GAME_DATA"])
def test_a_setting_or_layout_still_invalidates_it(monkeypatch, var):
    _clean_env(monkeypatch)
    base = ac._nif_config_fingerprint(_ARGS)
    monkeypatch.setenv(var, "1")
    assert ac._nif_config_fingerprint(_ARGS) != base


def test_the_switch_hashes_every_variable_again(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CBBE2UBE_NO_FINGERPRINT_SKIPS_PLUMBING", "1")
    base = ac._nif_config_fingerprint(_ARGS)
    monkeypatch.setenv("CBBE2UBE_NO_PAUSE", "1")
    assert ac._nif_config_fingerprint(_ARGS) != base


# --- #whole-mod-names ---------------------------------------------------------

def test_a_folder_name_with_a_comma_is_one_name(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    (tmp_path / COMMA_MOD).mkdir()
    assert ac._split_mod_arg([COMMA_MOD], tmp_path) == [COMMA_MOD]
    assert ac._split_mod_arg([COMMA_MOD, "Other"], tmp_path) == [COMMA_MOD, "Other"]


def test_a_comma_list_on_the_command_line_still_splits(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    (tmp_path / COMMA_MOD).mkdir()
    assert ac._split_mod_arg(["Mod A, Mod B"], tmp_path) == ["Mod A", "Mod B"]
    assert ac._split_mod_arg(["Mod A,Mod B", "Mod C"], tmp_path) == [
        "Mod A", "Mod B", "Mod C"]


def test_the_mods_root_is_found_when_not_given(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    (tmp_path / COMMA_MOD).mkdir()
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(tmp_path))
    assert ac._split_mod_arg([COMMA_MOD]) == [COMMA_MOD]


def test_the_switch_splits_every_value_again(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    (tmp_path / COMMA_MOD).mkdir()
    monkeypatch.setenv("CBBE2UBE_NO_WHOLE_MOD_NAMES", "1")
    assert ac._split_mod_arg([COMMA_MOD], tmp_path) == ["Armor", "Clothing Pack"]


def test_an_excluded_comma_mod_is_excluded_from_the_run(tmp_path, monkeypatch,
                                                        capsys):
    mods = _modlist(tmp_path, monkeypatch, [COMMA_MOD, "Other"])
    seen = {}

    def fake_find(mr, extra_exclude_names=None, **k):
        seen["exclude"] = set(extra_exclude_names or ())
        return [{"name": "Other", "path": mods / "Other", "armor_nifs": 1}]

    monkeypatch.setattr(ac, "_find_armor_mod_dirs", fake_find)
    monkeypatch.setattr(ac, "_drop_ube_native_candidates", lambda c: c)
    rc = ac.main(["auto", "-o", str(tmp_path / "out"), "--list-only",
                  "--exclude-mods", COMMA_MOD])
    capsys.readouterr()
    assert rc == 0
    assert COMMA_MOD in seen["exclude"]


def test_only_mods_selects_a_comma_mod(tmp_path, monkeypatch, capsys):
    mods = _modlist(tmp_path, monkeypatch, [COMMA_MOD, "Other"])
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: [
        {"name": n, "path": mods / n, "armor_nifs": 1} for n in (COMMA_MOD, "Other")])
    monkeypatch.setattr(ac, "_drop_ube_native_candidates", lambda c: c)
    rc = ac.main(["auto", "-o", str(tmp_path / "out"), "--list-only",
                  "--only-mods", COMMA_MOD])
    out = capsys.readouterr().out
    assert rc == 0, out[-1500:]
    assert "--only-mods: 1/2 mod(s) selected" in out
    assert "NOT FOUND" not in out


def test_parser_still_takes_the_flag_repeatedly():
    ns = ac._build_parser().parse_args(
        ["auto", "--exclude-mods", COMMA_MOD, "--exclude-mods", "b"])
    assert isinstance(ns, argparse.Namespace)
    assert ns.exclude_mods == [COMMA_MOD, "b"]
