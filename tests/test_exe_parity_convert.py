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

"""Pins `scripts/analysis/exe_parity_convert.py`.

Promoted from an untracked scratchpad 2026-09-06. `AUDIT_2026_09_01.md` cites it
repeatedly as the standard proof mechanism ("rule 5: single-harness
`exe_parity_convert.py` A/B") for finding after finding, while the file existed
only under a handoff folder -- the third tool found missing from the toolkit
while being treated as standard practice, after `morph_sweep` and
`damage_ledger`.

Nothing here spawns the converter. The parts worth pinning are the ones that
made it unshippable (hardcoded machine paths) and the one that protects the
live pack (the run-state guard).
"""
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "scripts" / "analysis"))

import exe_parity_convert as epc                 # noqa: E402

SRC = (PROJ / "scripts" / "analysis" / "exe_parity_convert.py").read_text(
    encoding="utf-8")


def test_no_machine_paths_in_tracked_content():
    """The reason it could not be tracked before. Comments and docstrings
    included -- a path in a docstring is still a path in the repo.

    The drive letter must be a LONE letter: a naive `[A-Za-z]:/` also matches
    the `s:/` inside `https://`, which is how this test first failed against
    its own GPL header."""
    hits = re.findall(r"(?<![A-Za-z])[A-Za-z]:[\\/]", SRC)
    assert not hits, hits
    assert "Modlists" not in SRC


def test_exe_is_resolved_from_the_ini_not_remembered(tmp_path):
    """The deploy path MOVES; the live ini is the only source of truth for it."""
    exe = tmp_path / "tools" / "CBBEtoUBE" / "CBBEtoUBE.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    ini = tmp_path / "ModOrganizer.ini"
    ini.write_text(
        "[customExecutables]\n"
        "1\\binary=C:/nope/other.exe\n"
        f"23\\binary={exe.as_posix()}\n", encoding="utf-8")
    assert epc._exe_from_ini(str(ini)) == exe


def test_a_non_converter_binary_is_not_accepted(tmp_path):
    """`<n>\\binary=` names every tool in the instance, not just this one."""
    other = tmp_path / "xEdit.exe"
    other.write_bytes(b"MZ")
    ini = tmp_path / "ModOrganizer.ini"
    ini.write_text(f"5\\binary={other.as_posix()}\n", encoding="utf-8")
    assert epc._exe_from_ini(str(ini)) is None


def test_a_missing_or_unreadable_ini_yields_none():
    assert epc._exe_from_ini(None) is None
    assert epc._exe_from_ini("no/such/ModOrganizer.ini") is None


def test_it_refuses_rather_than_guessing(monkeypatch, tmp_path, capsys):
    """No exe and no ini must REFUSE, not fall back to a hopeful default -- a
    silent fallback would run some other build and the verdict would be void."""
    monkeypatch.delenv("CBBE2UBE_EXE", raising=False)
    monkeypatch.setenv("CBBE2UBE_MO2_INI", str(tmp_path / "missing.ini"))
    rc = epc.main([str(tmp_path / "out"), "SomeMod"])
    assert rc == 2
    assert "REFUSING" in capsys.readouterr().out


def test_usage_when_underspecified(capsys):
    assert epc.main([]) == 2
    assert "exe_parity_convert" in capsys.readouterr().out


def test_it_guards_the_live_run_state():
    """THE ONE THAT MATTERS for the pack under test. The exe writes its run log
    NEXT TO ITSELF, so a scratch arm overwrites the log belonging to the pack
    being judged in game -- lost that way twice. The harness must stash and
    restore both files, and keep the arm's own copy beside the arm's output."""
    for name in ("CBBEtoUBE_last_run.log", "CBBEtoUBE_last_failures.json"):
        assert name in SRC
    assert "shutil.copy2" in SRC
    assert "arm_" in SRC          # the arm keeps its own copy
    assert "finally:" in SRC      # restored even when the arm fails


def test_overrides_must_be_var_equals_value():
    """An A/B arm is armed by a trailing VAR=VALUE; a malformed one must refuse
    rather than silently run the base recipe as if it were the arm."""
    assert "is not VAR=VALUE" in SRC
