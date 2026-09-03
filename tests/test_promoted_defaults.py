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
import re

import pytest

from src import gui_settings as gs
from src import nif_convert as nc
from src.envflags import flag, knob
from tests import _converter_sources as _cs  # source text across the split modules

PROMOTED = {
    "mixed_cloth_clearance": ("MIXED_CLOTH_CLEARANCE", True),
    "panel_rigid_ride": ("PANEL_RIGID_RIDE", True),
    "per_anchor_seed": ("PER_ANCHOR_ANCHOR_SEED", True),
    "panel_rigidity": ("PANEL_RIGIDITY", 0.75),
}

# #defaults-promoted-2026-08-26 -- the two BUG-15(a)/(b) fixes, plus
# `ride_body_floor` (see its own note below). Pinned in the
# SAME structure as the 2026-08-22 four so every guarantee above applies to them
# too: code and GUI agree, at-default emits no env, and the OFF case is still
# expressible. That last one is the whole point of this file -- a default-ON
# bool the GUI cannot switch off is the bug it was written for.
#
# THESE WERE PROMOTED WITHOUT AN IN-GAME VERDICT, which is the reverse of the
# 2026-08-22 order, at the user's explicit instruction so the reconvert that
# produces the verdict runs them. Measured, not judged. If the verdict is bad,
# the honest fix is to move them back, not to re-argue the measurements.
PROMOTED_2026_08_26 = {
    "bust_morph_chord": ("BUST_MORPH_CHORD", True),
    "panel_rigid_surface_guard": ("PANEL_RIGID_SURFACE_GUARD", True),
    # Added later the same day. Unlike the other two this one HAD been in
    # the live recipe for weeks while the code shipped it OFF, so it is
    # the 2026-08-22 situation repeating -- and `verify_reconvert.py`
    # failed a pack built without it.
    "ride_body_floor": ("RIDE_BODY_FLOOR", True),
}
PROMOTED.update(PROMOTED_2026_08_26)

# 2026-09-02. The first promotion made on a BOOKKEEPING measurement rather than
# a fit one: it moves no vertex, so it is judged on the orphan-bone gate and the
# report's bad-sum count, with bust follow held flat as the counter-metric.
# On a 38-NIF outfit through the deployed exe at defaults:
#   zero-weight bones 21 -> 0, bad-sum verts 361 -> 0, verts moved 0,
#   bust follow 1.033-1.082 -> 1.033-1.084 on the shape losing L/R Breast03.
# See docs/worklog/ZEROWEIGHT_BONE_PRODUCER.md. In-game verdict OWED.
PROMOTED_2026_09_02 = {
    "family_weight_invariant": ("FAMILY_WEIGHT_INVARIANT", True),
    # Added later the same day, and the FIRST promotion here that was judged in
    # game BEFORE the flip rather than after: the copy path had no body repair
    # at all (BUG-02), the reporting piece went 59.3% of its butt band inside
    # the body to 0.0% against the author's own 0.0%, and two populations came
    # back clean on the axes that killed F010 -- standoff moved INWARD on 8 of
    # 11 butt arms, and no bone was stranded on any arm of either mod.
    "phase1_antipoke": ("PHASE1_ANTIPOKE", True),
}
PROMOTED.update(PROMOTED_2026_09_02)


def test_the_in_code_record_matches_this_test():
    """`nif_convert._DEFAULTS_PROMOTED_2026_08_22` is the block four comments in
    that file point at, and it must not drift from the set pinned here.

    It exists because those comments cited it while it existed NOWHERE -- a
    cross-reference the reader cannot follow still reads as corroboration, and
    cannot be checked. Consuming it here is what keeps it honest: a record
    nothing reads is just a comment with extra syntax, and the next cleanup
    would delete it as a dead constant."""
    assert set(nc._DEFAULTS_PROMOTED_2026_08_22) == {
        attr for key, (attr, _want) in PROMOTED.items()
        if key not in PROMOTED_2026_08_26
        and key not in PROMOTED_2026_09_02}, (
        "the in-code promotion record and this test disagree about WHICH "
        "defaults were promoted on 2026-08-22")


def test_the_2026_08_26_record_matches_this_test():
    """Same contract as the 2026-08-22 record above, for the second promotion.

    A promotion record nothing reads is a comment with extra syntax; consuming
    it here is what stops the two drifting apart."""
    assert set(nc._DEFAULTS_PROMOTED_2026_08_26) == {
        attr for attr, _want in PROMOTED_2026_08_26.values()}, (
        "the in-code 2026-08-26 promotion record and this test disagree about "
        "WHICH defaults were promoted")


def test_the_2026_09_02_record_matches_this_test():
    """Same contract again, for the third promotion (#family-weight-invariant).

    This one is the orphan-bone fix, so it also has a gate of its own:
    `verify_zero_weight_bones.py` went 21 -> 0 on the measured mod. A promotion
    whose record drifts from the test is how "the fix is written but not
    enabled" gets read as fact -- which this file's own history records
    happening twice."""
    assert set(nc._DEFAULTS_PROMOTED_2026_09_02) == {
        attr for attr, _want in PROMOTED_2026_09_02.values()}, (
        "the in-code 2026-09-02 promotion record and this test disagree about "
        "WHICH defaults were promoted")


def test_the_family_invariant_is_what_stops_a_newcomer_stranding_a_bone():
    """WHY it is on, in the form a future reader can check.

    The family write filters on `_WRITE_MIN` while the SAVE keeps the four
    largest influences per vertex, so a newcomer bone can displace an existing
    bone's LAST weight -- leaving that bone in the shape's list carrying
    nothing (`#zeroweight-bone-desync`, an equip CTD; 225 such bones shipped in
    the 2026-08-28 pack). The invariant's rule -- keep the influences the vertex
    already HAS, spend only FREE slots on newcomers -- is what makes that
    impossible, so it must not be quietly switched back off.
    See docs/worklog/ZEROWEIGHT_BONE_PRODUCER.md."""
    assert nc.FAMILY_WEIGHT_INVARIANT is True
    src = _cs.source(nc._match_limb_motion_to_body)
    assert "FAMILY_WEIGHT_INVARIANT" in src, (
        "the family-weight-invariant branch is no longer in the limb-motion "
        "write -- the orphan-bone fix has been removed or moved")


def test_early_clearance_was_NOT_promoted_and_the_reason_is_recorded():
    """It is the obvious third candidate and it stays OFF because a MEASUREMENT
    says so -- re-tested WITH the surface guard (its old 12-better/6-worse
    verdict predates it) and still 1 better / 2 worse: velothisteel
    6.486 -> 10.813, cowarchrobe 3.466 -> 8.443."""
    assert nc.PANEL_RIGID_EARLY_CLEAR is False


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


# --- F031: a banner that still says "opt-in" above a flag that is now ON -----
#
# The header line is what a reader sees first, and what the generated,
# test-pinned PASS_MAP index reproduces verbatim; the "DEFAULT ON since ..."
# correction is usually buried 20-40 lines further down. A maintainer skimming
# banners sets `CBBE2UBE_<FLAG>=1` for an A/B and gets an arm IDENTICAL TO ITS
# CONTROL -- the broken-arm trap this project has hit more than once, including
# twice on 2026-09-02 (`#full-weight-match`'s docstring still said "why it is
# default OFF" four weeks after it went ON, and #family-weight-invariant's
# comment carried a safety story that its own promotion had falsified).
#
# Six such banners were found by the 2026-09-01 audit and fixed. This is the
# guard that stops the seventh, because the instances are cheap and the CLASS
# is not.

_OPTIN_WORDS = re.compile(r"opt-in|default off", re.I)
# A header may mention opt-in RETROSPECTIVELY -- "DEFAULT ON since X (was
# opt-in)" is correct and must not trip the check.
_CURRENT_WORDS = re.compile(r"default on|was opt|no longer opt|promoted", re.I)
_FLAG_BINDING = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*\(?\s*(?:not\s+)?_flag\(")


def _flag_headers():
    """(name, header line) for every module-level `_flag` binding, with the
    FIRST line of the contiguous comment block above it."""
    lines = _cs.whole_text().splitlines()
    for i, ln in enumerate(lines):
        m = _FLAG_BINDING.match(ln)
        if not m:
            continue
        j, block = i - 1, []
        while j >= 0 and (lines[j].lstrip().startswith("#") or not lines[j].strip()):
            if lines[j].strip():
                block.append(lines[j])
            j -= 1
            if len(block) > 60:
                break
        if block:
            yield m.group(1), block[-1]


def _stale_optin_headers(headers):
    return [(n, h.strip()) for n, h in headers
            if getattr(nc, n, None) is True
            and _OPTIN_WORDS.search(h) and not _CURRENT_WORDS.search(h)]


def test_no_default_on_flag_still_advertises_itself_as_opt_in():
    """A banner saying OPT-IN above a flag that is ON sends the next A/B into a
    control-identical arm, which reads exactly like "the change does nothing"."""
    bad = _stale_optin_headers(_flag_headers())
    assert not bad, (
        "these flags default to True but their comment BANNER still calls them "
        "opt-in / default off -- state the current default in the header, and "
        "keep the history as '(was opt-in ...)':\n  "
        + "\n  ".join(f"{n}: {h}" for n, h in bad))


def test_the_stale_header_scan_can_actually_fail():
    """Control. The scan above passing means nothing unless it can fail: a
    default-True flag under a bare opt-in banner MUST be reported, and the same
    flag under a corrected banner must NOT."""
    on = next(n for n, _w in PROMOTED.items() if PROMOTED[n][1] is True
              and getattr(nc, PROMOTED[n][0], None) is True)
    attr = PROMOTED[on][0]
    assert _stale_optin_headers([(attr, f"# #{on} -- OPT-IN, `CBBE2UBE_{attr}=1`")]), (
        "the scan no longer flags a bare OPT-IN banner over a default-ON flag")
    assert not _stale_optin_headers(
        [(attr, f"# #{on} -- DEFAULT ON since 2026-01-01 (was opt-in)")]), (
        "the scan now flags a CORRECTED banner, so it would fire on every "
        "promotion that documents its own history")
