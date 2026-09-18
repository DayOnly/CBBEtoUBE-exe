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

"""#run-log-only-for-runs -- only a run may rotate the run log.

MEASURED on the 1.4.1 exe: after a run died, `CBBEtoUBE.exe --help` rotated its
log to CBBEtoUBE_previous_run.log, and one mistyped subcommand then rotated the
help text over THAT -- the death log was gone from every file beside the exe.
The bug form, REPORTING.md and the 1.4.1 release notes all tell exactly that
user to attach CBBEtoUBE_previous_run.log.

Same rule, second file: the failure summary is written only when a run
FINISHES, so a direct run killed part-way used to leave the PREVIOUS run's
CBBEtoUBE_last_failures.json beside a fresh log. A run now rotates it together
with its log, the way the GUI already did.

The end-to-end tests drive `_install_log_tee` itself, not a helper, so they
measure what a launch actually does to the files."""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEATH = "error: could not locate the MO2 mods folder (the run that died)"
STALE = '{"failures": ["from the run before"]}'


def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_rotation", REPO / "cbbe_to_ube_main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _invoke(m, monkeypatch, folder, argv, *, pinned=None):
    """Set the log up exactly as a launch with `argv` would, print one line
    through it, and release it."""
    monkeypatch.setattr(m, "_log_dir_candidates", lambda: [str(folder)])
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", *argv])
    if pinned is None:
        monkeypatch.delenv("CBBE2UBE_RUN_LOG", raising=False)
    else:
        monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(pinned))
    m._install_log_tee()
    try:
        print(f"output of {' '.join(argv) or '(no arguments)'}")
    finally:
        m.release_log_tee()


def _files(folder) -> "dict[str, str]":
    return {p.name: p.read_text(encoding="utf-8")
            for p in Path(folder).iterdir() if p.is_file()}


def _after_a_dead_run(folder):
    (folder / "CBBEtoUBE_last_run.log").write_text(DEATH, encoding="utf-8")
    (folder / "CBBEtoUBE_last_failures.json").write_text(STALE,
                                                         encoding="utf-8")


def test_the_measured_sequence_keeps_the_death_log(monkeypatch, tmp_path):
    """THE REGRESSION GUARD: the sequence measured on the 1.4.1 exe."""
    m = _entry_module()
    _after_a_dead_run(tmp_path)
    for argv in (["--help"], ["nosuchcmd"]):
        _invoke(m, monkeypatch, tmp_path, argv)
    holders = [n for n, t in _files(tmp_path).items() if DEATH in t]
    assert holders, ("--help then a typo erased the log of the run that died "
                     "from every file beside the exe")


@pytest.mark.parametrize("argv", [
    ["--help"], ["-h"], ["nosuchcmd"], ["Auto"], ["validate", "somewhere"],
    ["scan"], ["discover-body-ref"], ["auto", "--help"], ["convert", "-h"],
    ["merge", "--hel"],
])
def test_a_non_run_leaves_the_run_artefacts_alone(monkeypatch, tmp_path, argv):
    m = _entry_module()
    _after_a_dead_run(tmp_path)
    _invoke(m, monkeypatch, tmp_path, argv)
    files = _files(tmp_path)
    assert files.get("CBBEtoUBE_last_run.log") == DEATH, (
        f"{argv} rewrote the run log")
    assert "CBBEtoUBE_previous_run.log" not in files, (
        f"{argv} rotated the run log")
    assert files.get("CBBEtoUBE_last_failures.json") == STALE
    assert "CBBEtoUBE_previous_failures.json" not in files
    assert "output of" in files.get("CBBEtoUBE_cli.log", ""), (
        f"{argv}'s own output was not kept anywhere")


@pytest.mark.parametrize("argv", [
    ["auto"], ["convert", "-o", "out", "src"], ["merge"],
])
def test_a_run_rotates_its_log_and_its_failure_summary(monkeypatch, tmp_path,
                                                        argv):
    """The summary moves aside with its log, so a failures file beside
    CBBEtoUBE_last_run.log belongs to that log's run -- or is absent because
    the run has not reached its end. (Not a claim about the previous_* pair:
    a killed run writes no summary, so after one the previous files can come
    from different runs.)"""
    m = _entry_module()
    _after_a_dead_run(tmp_path)
    _invoke(m, monkeypatch, tmp_path, argv)
    files = _files(tmp_path)
    assert files.get("CBBEtoUBE_previous_run.log") == DEATH
    assert files.get("CBBEtoUBE_previous_failures.json") == STALE, (
        "the previous run's failure summary was left beside the new run's log")
    assert "CBBEtoUBE_last_failures.json" not in files
    assert "output of" in files.get("CBBEtoUBE_last_run.log", "")


def test_a_non_run_never_opens_a_pinned_run_log(monkeypatch, tmp_path):
    """A parent that pinned CBBE2UBE_RUN_LOG owns that file. Opening it with
    "w" for `--help` would erase it as surely as rotating it would."""
    m = _entry_module()
    pinned = tmp_path / "pinned" / "run.log"
    pinned.parent.mkdir()
    pinned.write_text(DEATH, encoding="utf-8")
    tool = tmp_path / "tool"
    tool.mkdir()
    _invoke(m, monkeypatch, tool, ["--help"], pinned=pinned)
    assert pinned.read_text(encoding="utf-8") == DEATH
    assert [p.name for p in pinned.parent.iterdir()] == ["run.log"]


def test_the_gui_parent_still_keeps_its_own_session_log(monkeypatch, tmp_path):
    """Unchanged behaviour, pinned: the settings window is not a run."""
    m = _entry_module()
    _after_a_dead_run(tmp_path)
    for argv in ([], ["gui"]):
        _invoke(m, monkeypatch, tmp_path, argv)
    files = _files(tmp_path)
    assert files.get("CBBEtoUBE_last_run.log") == DEATH
    assert "output of" in files.get("CBBEtoUBE_gui_session.log", "")


def test_the_run_subcommands_exist_in_the_real_parser():
    """A renamed subcommand would silently turn every run into a non-run, and
    its log would land in CBBEtoUBE_cli.log, where no document looks."""
    from src import auto_convert as ac
    parser = ac._build_parser()
    choices = set(next(a.choices for a in parser._subparsers._group_actions))
    m = _entry_module()
    assert m._RUN_SUBCOMMANDS <= choices, sorted(m._RUN_SUBCOMMANDS - choices)
    assert {"validate", "scan", "discover-body-ref"} <= choices


def test_the_failure_summary_name_matches_its_writer():
    """Two spellings of one filename drift apart; pin one to the other."""
    from src import auto_convert as ac
    assert _entry_module()._FAILURES_NAME == ac._failures_file_path().name
