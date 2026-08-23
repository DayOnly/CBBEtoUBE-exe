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

"""#defaults-promoted-2026-08-22 -- the defaults are the CONFIGURATION THAT WAS
JUDGED.

Four opt-ins had sat in the user's live recipe for weeks while the code shipped
them OFF, so a defaults-only convert produced something NOBODY HAD EVER RUN and
every "defaults-only" baseline described an unjudged configuration. The
2026-08-22 reconvert ran them ON across all 163 mods and the in-game verdict was
"everything looks as it should", so they were promoted.

`warp_push_shell_cap` was in that same recipe and was deliberately NOT promoted:
`test_warp_flag_defaults` records that it is inert on every large spike measured
and REGRESSES TWO SHAPES while moving geometry by 1.38u. A general "looks fine
so far" does not overturn a specific negative measurement.

THE TRAP THIS FILE EXISTS TO CATCH. Promoting a bool default to True exposed a
latent bug in `env_string_for`: its bool branch emitted "1" or None and never
"0", which was indistinguishable from correct while every default was False.
Against a DEFAULT-ON flag, None means "leave it on" -- so the GUI could not turn
the feature off at all. Any future promotion must keep the round trip below
green.
"""
from __future__ import annotations

import os

import pytest

from src import gui_settings as gs
from src import nif_convert as nc
from src.envflags import flag, knob

PROMOTED = {
    "mixed_cloth_clearance": ("MIXED_CLOTH_CLEARANCE", True),
    "panel_rigid_ride": ("PANEL_RIGID_RIDE", True),
    "per_anchor_seed": ("PER_ANCHOR_ANCHOR_SEED", True),
    "panel_rigidity": ("PANEL_RIGIDITY", 0.75),
}


def test_the_in_code_record_matches_this_test():
    """`nif_convert._DEFAULTS_PROMOTED_2026_08_22` is the block four comments in
    that file point at, and it must not drift from the set pinned here.

    It exists because those comments cited it while it existed NOWHERE -- a
    cross-reference the reader cannot follow still reads as corroboration, and
    cannot be checked. Consuming it here is what keeps it honest: a record
    nothing reads is just a comment with extra syntax, and the next cleanup
    would delete it as a dead constant."""
    assert set(nc._DEFAULTS_PROMOTED_2026_08_22) == {
        attr for attr, _want in PROMOTED.values()}, (
        "the in-code promotion record and this test disagree about WHICH "
        "defaults were promoted on 2026-08-22")


@pytest.mark.parametrize("key", sorted(PROMOTED))
def test_code_and_gui_agree_on_the_new_default(key):
    """Two sources of truth for one default is how a GUI ends up reporting
    'changed from default' for a value that IS the default."""
    attr, want = PROMOTED[key]
    assert getattr(nc, attr) == want, f"{attr} is not {want!r}"
    assert gs.defaults()[key] == want, f"GUI default for {key} is not {want!r}"


def test_the_shell_cap_was_NOT_promoted_and_the_reason_is_recorded():
    """It was in the same recipe. It stays opt-in because a MEASUREMENT says so."""
    assert nc.WARP_PUSH_SHELL_CAP is False
    assert gs.defaults()["warp_push_shell_cap"] is False


@pytest.mark.parametrize("key", sorted(PROMOTED))
def test_a_promoted_default_can_still_be_TURNED_OFF_from_the_gui(key):
    """THE REGRESSION THIS FILE GUARDS. With a True default, an env layer that
    can only say "1" or nothing can never say OFF."""
    s = next(x for x in gs.SETTINGS if x.key == key)
    vals = gs.defaults()
    vals[key] = 0.0 if s.kind != "bool" else False
    env = gs.apply_env(vals, base_env={})
    raw = env.get(s.env)
    assert raw is not None, (
        f"{key}: turning it OFF emitted NO env var, so the code default (ON) "
        f"still applies and the GUI switch does nothing")
    saved = os.environ.pop(s.env, None)
    try:
        os.environ[s.env] = raw
        if s.kind == "bool":
            got = flag(s.env, s.default)
            if s.invert:
                got = not got
            assert got is False, f"{key}: env {raw!r} did not read as OFF"
        else:
            assert knob(s.env, s.default) == 0.0
    finally:
        os.environ.pop(s.env, None)
        if saved is not None:
            os.environ[s.env] = saved


@pytest.mark.parametrize("key", sorted(PROMOTED))
def test_a_setting_AT_its_default_emits_no_env(key):
    """A value at its default must leave the var UNSET, or the code default is
    bypassed and a later default change never reaches the user."""
    s = next(x for x in gs.SETTINGS if x.key == key)
    env = gs.apply_env(gs.defaults(), base_env={})
    assert s.env not in env, (
        f"{key} is at its default but still emitted {s.env}={env[s.env]!r}")


def test_the_off_case_is_expressible_for_every_bool_setting():
    """Structural: no bool setting anywhere may be un-turn-off-able. This is the
    general form of the bug, not just the four promoted ones."""
    broken = []
    for s in gs.SETTINGS:
        if s.kind != "bool" or s.env is None:
            continue
        want_off = bool(s.default)          # flip away from the default
        raw = gs.env_string_for(s, not want_off)
        if raw is None and bool(s.default) is not (not want_off):
            broken.append(s.key)
    assert not broken, (
        f"these bool settings cannot express the non-default state: {broken}")
