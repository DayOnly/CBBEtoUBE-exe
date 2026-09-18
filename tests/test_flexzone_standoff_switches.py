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

"""`CBBE2UBE_NO_REAR_STANDOFF` / `CBBE2UBE_NO_CALF_STANDOFF` were DEAD SWITCHES.

They lived in `nif_convert.py` as

    if _flag("CBBE2UBE_NO_REAR_STANDOFF", False):
        REAR_STANDOFF = 0.0

but `REAR_STANDOFF` is IMPORTED from `nif_convert_fitgeom`, and
`clear_armor_outside_body` binds `rear_standoff=REAR_STANDOFF` as a PARAMETER
DEFAULT at fitgeom import time. Rebinding the name in `nif_convert` therefore
changed a value no pass ever reads. Proven 2026-09-06 with both env vars set:
`nc.REAR_STANDOFF` went to 0.0 while `fitgeom.REAR_STANDOFF` and the pass
default both stayed at 1.0 (calf: 0.0 vs 0.6).

Neither switch appears in `flag_surface` either -- the census binds NAMED
constants, and these were bare `if _flag(...)` blocks -- so the flag audit could
not have found them.

The rule this encodes: **a constant that moves modules takes its switches with
it.** The switch must be applied where the constant is DEFINED, because callers
capture parameter defaults at import.
"""
import inspect
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import nif_convert_fitgeom as fg      # noqa: E402
from src.envflags import flag, knob            # noqa: E402


def test_defaults_are_unchanged_when_the_switch_is_off():
    """The shipped values. Exposing the switch must not move them."""
    assert fg.REAR_STANDOFF == 1.0
    assert fg.CALF_STANDOFF == 0.6


def test_the_pass_takes_the_module_constant_as_its_default():
    """THE MECHANISM the old switch missed: the value is captured into the
    signature at import, so a later rebinding anywhere cannot reach it."""
    sig = inspect.signature(fg.clear_armor_outside_body)
    assert sig.parameters["rear_standoff"].default is fg.REAR_STANDOFF
    assert sig.parameters["calf_standoff"].default is fg.CALF_STANDOFF


def test_the_switch_zeroes_the_value_it_is_documented_to_zero(monkeypatch):
    """The switch is now applied at the DEFINITION, so it reaches the constant
    the pass actually uses. Evaluated the same way the module does."""
    for env, base_env, base_default in (
        ("CBBE2UBE_NO_REAR_STANDOFF", "CBBE2UBE_REAR_BUTT_STANDOFF", 1.0),
        ("CBBE2UBE_NO_CALF_STANDOFF", "CBBE2UBE_CALF_STANDOFF", 0.6),
    ):
        monkeypatch.delenv(env, raising=False)
        monkeypatch.delenv(base_env, raising=False)
        off = 0.0 if flag(env, False) else knob(base_env, base_default)
        assert off == base_default, env

        monkeypatch.setenv(env, "1")
        on = 0.0 if flag(env, False) else knob(base_env, base_default)
        assert on == 0.0, env


def test_no_module_rebinds_these_after_import():
    """The regression guard. A re-added `REAR_STANDOFF = 0.0` outside fitgeom
    would be silently dead again -- and it read as working for weeks."""
    import re
    bad = []
    pat = re.compile(r"^\s*(REAR_STANDOFF|CALF_STANDOFF)\s*=")
    for p in sorted((PROJ / "src").glob("*.py")):
        if p.name == "nif_convert_fitgeom.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.match(line):
                bad.append(f"{p.name}:{i}: {line.strip()}")
    assert not bad, (
        "these rebind a fitgeom constant after import, which no pass reads:\n"
        + "\n".join(bad))
