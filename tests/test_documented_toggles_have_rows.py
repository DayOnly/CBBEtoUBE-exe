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

"""Every behaviour toggle the README documents is reachable from the GUI. #documented-toggle-rows

MO2 does not pass the environment to the program it starts -- the README says so
itself -- so a `CBBE2UBE_*` variable set in a shell has NO effect on the supported
launch path. Four documented toggles had no Setting row, which means a reader who
followed the README could not change them at all from MO2; one of those was a raw
`os.environ` read, invisible to the flag census as well as to the GUI.

The path and discovery overrides (CBBE2UBE_MO2_INI, CBBE2UBE_MODS_ROOT,
CBBE2UBE_GAME_DATA, CBBE2UBE_TEXCONV, CBBE2UBE_PAPYRUS_COMPILER) and the
CBBE2UBE_LAYER_DEBUG print switch are deliberately not rows: they are not
behaviour toggles, and the GUI has its own Paths tab for the ones that matter.
"""
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import gui_settings as gs      # noqa: E402

_TOKEN = re.compile(r"CBBE2UBE_[A-Z0-9_]+")

#: Documented, read through `_flag`, and deliberately NOT a row (see the
#: docstring). CBBE2UBE_LAYER_DEBUG was a raw read until 2026-09-16 and so
#: invisible to `flag_bindings()`; the exemption the prose always claimed is
#: now stated where the check can see it.
NOT_ROWS = {"CBBE2UBE_LAYER_DEBUG"}
_FLAG_CALL = re.compile(r"_flag\(\s*\"(CBBE2UBE_[A-Z0-9_]+)\"")
_RAW_READ = re.compile(r"os\.environ(?:\.get)?\(\s*\"(CBBE2UBE_[A-Z0-9_]+)\"")


def _src_text():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted((PROJ / "src").glob("*.py"))}


def flag_bindings():
    """Every CBBE2UBE_ name read through the shared boolean flag reader."""
    out = set()
    for text in _src_text().values():
        out |= set(_FLAG_CALL.findall(text))
    return out


def documented():
    return set(_TOKEN.findall((PROJ / "README.md").read_text(encoding="utf-8")))


def rows():
    return {s.env for s in gs.SETTINGS if s.env}


def test_every_documented_toggle_has_a_gui_row():
    toggles = (documented() & flag_bindings()) - NOT_ROWS
    assert len(toggles) >= 10, (
        f"only {len(toggles)} documented boolean toggle(s) seen -- the README scan "
        f"or the flag scan drifted and this check would be vacuous")
    missing = sorted(toggles - rows())
    assert not missing, (
        "the README documents these toggles but the GUI builds no row for them, so "
        f"an MO2 launch cannot reach them at all: {missing}")


def test_the_groove_toggle_is_read_like_every_other_flag():
    """It was `os.environ.get("CBBE2UBE_GROOVE_ONESIDED", "1") != "0"`: no census
    could see it (they parse `_flag(` calls) and the GUI could not write it."""
    assert "CBBE2UBE_GROOVE_ONESIDED" in flag_bindings()
    raw = {name: f for f, text in _src_text().items() for name in _RAW_READ.findall(text)
           if name == "CBBE2UBE_GROOVE_ONESIDED"}
    assert not raw, f"still read raw, outside the flag reader: {raw}"


def test_the_checks_see_what_they_are_for():
    """Controls: the three scans each find something, and a path override is not
    mistaken for a toggle."""
    assert "CBBE2UBE_NO_BUST_SPACING" in flag_bindings()
    assert "CBBE2UBE_NO_BUST_SPACING" in documented()
    assert "CBBE2UBE_NO_BUST_SPACING" in rows()
    assert "CBBE2UBE_MO2_INI" in documented() and "CBBE2UBE_MO2_INI" not in flag_bindings()
    assert len(rows()) >= 100, f"only {len(rows())} Setting rows carry an env var"
