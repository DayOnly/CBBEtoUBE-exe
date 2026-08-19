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

"""The ONE way to read a CBBE2UBE_* environment flag or knob.

Before this module, 366 read statements across src/ spelled the same two
ideas in TWELVE textual variants, and the variants disagreed about edge
values: setting a variable to "0" turned a feature ON in 14 statements and
OFF in 10, "true" enabled some opt-ins and not others, and a variable set
to the empty string crashed 141 numeric reads. None of those edge values is
ever written by the GUI (it writes "1" or unsets), so the disagreements were
latent -- but every one was a trap for a hand-set A/B recipe, and the
scattered idiom is why the flag surface kept growing audit findings
(unreachable opt-ins, wrong-polarity comments) faster than they were fixed.

The contract, for both helpers:

  * unset OR empty/whitespace  ->  the DEFAULT, always
  * "1", "true", "yes", "on" (any case)  ->  True for flag()
  * anything else              ->  False for flag()
  * knob(): a non-empty value is parsed by `cast`; a value `cast` rejects
    raises (a typo'd number should fail loudly, not fall back silently)

`flag(name, default=True)` is the NO_*-style kill switch read: the feature
runs unless the variable is set truthy -- callers write
`FEATURE = not flag("CBBE2UBE_NO_FEATURE", default=False)` for that family,
keeping the polarity visible at the call site.
"""
from __future__ import annotations

import os

_ON = ("1", "true", "yes", "on")


def flag(name: str, default: bool = False) -> bool:
    """Boolean env read: unset/empty -> `default`; else truthy iff the value
    is one of "1"/"true"/"yes"/"on" (any case, surrounding space ignored)."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in _ON


def knob(name: str, default, cast=float):
    """Numeric/typed env read: unset/empty -> `default`; else `cast(value)`.

    The default is evaluated by the CALLER (eagerly). A set-but-invalid value
    raises from `cast` on purpose: a mistyped number in a recipe must fail
    the run, not silently become the default.
    """
    raw = os.environ.get(name, "")
    raw = raw.strip() if isinstance(raw, str) else raw
    if not raw:
        return default
    return cast(raw)
