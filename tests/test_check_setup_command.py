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

"""#check-setup -- the setup checks without a window, and a program name that is ours.

The docs tell a user to run Check setup, and to use the logs if the GUI will not
start, but `preflight.run_checks` was reachable only from the Tk window: a
`check-setup` argument was rejected by argparse, and every usage and error line
named the program `auto_convert`. `check-setup` prints the same one-line-per-check
text the diagnostics zip writes, from one shared formatter."""
import importlib.util
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import preflight as pf

REPO = Path(__file__).resolve().parents[1]


def _checks(*rows):
    return [pf.Check(*row) for row in rows]


def test_check_setup_prints_every_check_and_exits_1_on_a_failure(monkeypatch, capsys):
    monkeypatch.setattr(pf, "run_checks", lambda *a, **k: _checks(
        ("memory", "System memory and page file", pf.OK, "32.0 GB RAM"),
        ("modlist", "Modlist detected", pf.FAIL, "Couldn't find the MO2 mods folder.",
         "Run this through MO2.")))
    rc = ac.main(["check-setup"])
    assert capsys.readouterr().out.splitlines() == [
        "[ok] System memory and page file: 32.0 GB RAM",
        "[fail] Modlist detected: Couldn't find the MO2 mods folder.  | fix: Run this through MO2.",
    ]
    assert rc == 1


def test_check_setup_exits_0_when_nothing_fails(monkeypatch, capsys):
    monkeypatch.setattr(pf, "run_checks", lambda *a, **k: _checks(
        ("profile", "Active MO2 profile", pf.WARN, "No active profile detected.",
         "Launch from MO2.")))
    assert ac.main(["check-setup"]) == 0
    assert capsys.readouterr().out.startswith("[warn] Active MO2 profile:")


def test_the_zip_and_the_command_share_one_formatter():
    gui = (REPO / "src" / "gui.py").read_text(encoding="utf-8")
    assert "pf.format_checks(" in gui, "the diagnostics zip formats checks on its own again"
    assert 'f"[{c.status}] {c.label}: {c.detail}"' not in gui


def test_usage_names_the_program_not_a_module(capsys):
    with pytest.raises(SystemExit):
        ac.main(["--help"])
    assert capsys.readouterr().out.startswith("usage: CBBEtoUBE")


def test_the_frozen_exe_names_itself(monkeypatch):
    monkeypatch.setattr(ac.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ac.sys, "executable", r"C:\Games\Tools\CBBEtoUBE.exe")
    assert ac._build_parser().prog == "CBBEtoUBE.exe"


def test_check_setup_is_not_a_run_so_it_never_rotates_the_run_log():
    spec = importlib.util.spec_from_file_location("_entry_probe_check_setup",
                                                  REPO / "cbbe_to_ube_main.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    assert entry._log_target(["check-setup"]) == ("CBBEtoUBE_cli.log", False, False)
