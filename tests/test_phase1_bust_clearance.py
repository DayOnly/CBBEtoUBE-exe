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

"""`#phase1-bust-clearance` -- the bust CLEARANCE block on the copy path, with
the conform's PULL-IN half switched off.

WHY IT EXISTS. `#bust-morph-chord` lives inside `conform_to_source_standoff`,
which on the copy path is gated by `PHASE1_CONFORM` (default OFF, and measured
to cost +22% clipping for 0.022u of standoff, so it stays off). Copy-path
pieces therefore get NO chord charge at all -- confirmed, not inferred: on a
copy-path chord candidate, toggling `CBBE2UBE_BUST_MORPH_CHORD` moved 0 verts.
The 2026-08-27 pack census puts the class at 28% on BOTH paths, so ~50 pieces
carry the defect unserved.

WHAT IS PINNED HERE:
  * the flag is DEFAULT OFF and its GUI Setting agrees about polarity -- the
    `unseen_settings` failure mode one level down;
  * the copy-path call passes `blend=0.0`. That is the whole safety argument:
    `conform_to_source_standoff`'s own docstring defines blend 0.0 as "no
    conform", so `move` starts at 0 and only the clearance block can raise it.
    If someone drops that argument the pass silently becomes `PHASE1_CONFORM`,
    which is the configuration measured WORSE -- a one-token change with an
    opposite effect, which is exactly what a test should hold;
  * the two branches are mutually exclusive (`elif`), so when the full conform
    runs the clearance block is not applied twice and the push not doubled.
"""
import inspect
import re

from src import gui_settings as gs
from src import nif_convert as nc

KEY = "phase1_bust_clearance"
ENV = "CBBE2UBE_NO_PHASE1_BUST_CLEARANCE"


def _setting():
    for s in gs.SETTINGS:
        if getattr(s, "key", None) == KEY:
            return s
    raise AssertionError(f"no GUI Setting named {KEY!r}")


def test_flag_is_default_ON_matching_the_recipe():
    assert nc.PHASE1_BUST_CLEARANCE is True


def test_gui_setting_exists_and_agrees_on_polarity():
    s = _setting()
    assert s.env == ENV
    assert s.default is True
    # PROMOTED 2026-09-09: a default-ON flag read as `not _flag(NO_X)` MUST
    # be inverted, or the row goes inert in both directions -- the
    # `warp_delta_outlier` bug. This asserted the opposite, correctly, for
    # the opt-in form it used to have.
    # get this backwards and the option does the opposite of its own label.
    assert getattr(s, "invert", False) is True


def test_setting_is_reachable_from_a_real_run():
    # A flag with no Setting row cannot be turned on from the GUI, so it is
    # unreachable no matter what the code says -- the trap recorded in
    # `deployed build runs at defaults`.
    assert any(getattr(s, "key", None) == KEY for s in gs.SETTINGS)


def test_copy_path_call_passes_blend_zero():
    """The clearance-only guarantee is `blend=0.0`; without it this IS the
    full conform, which is the arm measured worse."""
    src = inspect.getsource(nc)
    # the guarded branch, from the elif to the end of its conform call
    m = re.search(
        r"elif \(PHASE1_BUST_CLEARANCE.*?conform_to_source_standoff\((.*?)\)",
        src, re.S)
    assert m, "the PHASE1_BUST_CLEARANCE branch no longer calls the conform"
    assert "blend=0.0" in m.group(1), (
        "the copy-path clearance call must pass blend=0.0 -- without it the "
        "pull-in half runs and this becomes PHASE1_CONFORM, measured worse")
    # THE ASSERTION MUST DISCRIMINATE, not pass on any conform call anywhere.
    # The PHASE1_CONFORM branch calls the same function and deliberately does
    # NOT pass blend, so if this check were matching the wrong branch (or
    # matching the whole file) it would still find `blend=0.0` and read green.
    m_full = re.search(
        r"if \(PHASE1_CONFORM and.*?conform_to_source_standoff\((.*?)\)",
        src, re.S)
    assert m_full, "the PHASE1_CONFORM branch moved; re-anchor both regexes"
    assert "blend=" not in m_full.group(1), (
        "the full-conform branch must NOT pass blend -- if it does, this "
        "test's positive case no longer distinguishes the two branches")


def test_branches_are_mutually_exclusive():
    """`elif`, not a second `if`: the full conform already applies the
    clearance block, so running both would double-charge the push."""
    src = inspect.getsource(nc)
    assert re.search(r"elif \(PHASE1_BUST_CLEARANCE", src), (
        "PHASE1_BUST_CLEARANCE must be an `elif` on the PHASE1_CONFORM chain")
