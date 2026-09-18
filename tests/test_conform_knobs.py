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

"""The three conform-shape knobs exposed 2026-09-06 for the `#src-normal-fix`
retune: `CONFORM_TIGHT_STANDOFF`, `CONFORM_LOOSE_STANDOFF`, `CONFORM_MAX_PULL`.

They were bare literals in `conform_to_source_standoff`'s signature. Every A/B
here is two arms of ONE code state differing only by a trailing `VAR=VALUE`, so
a constant with no env name cannot be moved by an arm AT ALL -- the A1 retune
stalled precisely there.

Two ways this change could be worthless, and one test each:

  * the default drifts off the literal it replaced, silently changing the
    shipped fit for everyone (`test_defaults_are_the_literals_they_replaced`);
  * a CALL SITE passes the parameter explicitly, so the knob is overridden and
    an arm that sets it measures nothing -- the "guard wired into a default-off
    branch is unverified" trap (`test_no_call_site_overrides_them`).
"""
import inspect
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import nif_convert_fitgeom as fg      # noqa: E402
from src.envflags import knob                  # noqa: E402


def test_defaults_are_the_literals_they_replaced():
    """Exposing a constant must not change it. These are the values every fit
    constant in the chain was tuned around."""
    assert fg.CONFORM_TIGHT_STANDOFF == 1.0
    assert fg.CONFORM_LOOSE_STANDOFF == 4.0
    assert fg.CONFORM_MAX_PULL == 4.0


def test_the_signature_actually_takes_them():
    """A knob the pass does not read is decoration. Pin that the parameter
    defaults ARE the constants, not copies of the old literals."""
    sig = inspect.signature(fg.conform_to_source_standoff)
    assert sig.parameters["tight_standoff"].default is fg.CONFORM_TIGHT_STANDOFF
    assert sig.parameters["loose_standoff"].default is fg.CONFORM_LOOSE_STANDOFF
    assert sig.parameters["max_pull"].default is fg.CONFORM_MAX_PULL


def test_the_env_names_are_live(monkeypatch):
    """The plumbing, without reloading the module: `knob` must read the name
    the constant is declared with."""
    for env, default, val in (
        ("CBBE2UBE_CONFORM_TIGHT_STANDOFF", 1.0, 0.5),
        ("CBBE2UBE_CONFORM_LOOSE_STANDOFF", 4.0, 6.0),
        ("CBBE2UBE_CONFORM_MAX_PULL", 4.0, 2.0),
    ):
        monkeypatch.delenv(env, raising=False)
        assert knob(env, default) == default
        monkeypatch.setenv(env, str(val))
        assert knob(env, default) == val


def test_no_call_site_overrides_them():
    """THE ONE THAT MATTERS. If a caller passes `max_pull=` explicitly, an arm
    setting the env changes nothing and the sweep measures noise. Checked at
    source level over the whole package, because the call sites are in a
    different module from the default."""
    pat = re.compile(r"\b(tight_standoff|loose_standoff|max_pull|"
                     r"bust_clearance)\s*=")
    offenders = []
    for p in sorted((PROJ / "src").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or not pat.search(line):
                continue
            # the declarations themselves are the point, not an override
            if p.name == "nif_convert_fitgeom.py" and (
                    "CONFORM_" in line or ": float =" in line):
                continue
            offenders.append(f"{p.name}:{i}: {s}")
    assert not offenders, (
        "a call site overrides a conform knob, so an arm setting it is inert:\n"
        + "\n".join(offenders))
