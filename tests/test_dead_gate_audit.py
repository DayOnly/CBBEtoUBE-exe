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

"""A GATE THAT CANNOT FIRE must fail the suite, not survive review.

`#coherence-kink` shipped for months as a full comment block -- rationale, a
worked example with measured numbers, an instruction on how to repair -- while
its `kink` variable was assigned `False` and never set `True`. The branch was
unreachable. Nothing caught it because reviewing a diff shows the comment and
the declaration; only grepping for the variable being SET reveals the gap.

These two invariants are currently CLEAN. Pinning them means the next one
fails here instead of being found by a user reporting the symptom it was
supposed to fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "analysis"))

import dead_gate_audit as dga  # noqa: E402


def test_no_local_gate_is_assigned_a_constant_and_then_read_as_a_condition():
    """The `#coherence-kink` shape: `kink = False`, never set True, then
    `if not ok and ... kink`. The comment above it described a whole mechanism.

    A hit here is not automatically a bug -- a deliberately retired branch is
    legitimate -- but it MUST be looked at and then either wired or added to
    `dead_gate_audit.KNOWN_DEAD` with the reason, so the next reader does not
    have to re-derive whether the feature exists.
    """
    hits = dga.dead_local_gates()
    assert hits == [], (
        "unreachable gate(s) -- each is either an unimplemented feature or a "
        "retired one that should be recorded in KNOWN_DEAD:\n  "
        + "\n  ".join(f"{f}:{ln} {fn}() -> `{n}` never set otherwise"
                      for f, fn, n, ln in hits))


def test_every_flag_constant_is_actually_read_somewhere():
    """A constant bound from `_flag`/`_knob` that nothing references means
    setting its env var does nothing -- the switch is a lie. The pack has been
    here before ("17.3% was telemetry NOTHING READS")."""
    never = dga.defined_never_read()
    assert never == [], (
        "flag/knob constant(s) nothing reads -- setting the env var cannot "
        "change anything:\n  "
        + "\n  ".join(f"{c} ({f}:{ln}) env={e}" for c, f, ln, e in never))


def test_the_audit_can_actually_find_something():
    """The control. A checker that always returns an empty list would pass both
    tests above while measuring nothing, so prove it parses the real tree and
    finds the flag population it is supposed to reason about."""
    consts = dga.flag_constants()
    assert len(consts) > 200, (
        f"only {len(consts)} flag/knob constants found -- the audit is not "
        "reading the source tree it thinks it is, so the two assertions above "
        "are vacuous")
    assert "SKIRT_PROXY_REBUILD" in consts
