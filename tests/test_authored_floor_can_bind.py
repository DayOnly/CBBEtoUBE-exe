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

"""#authored-floor-same-rule: A CAP MUST BE TIGHTER THAN WHAT IT CAPS.

Both authored floors spent months shipping as unreachable code on the two bands
they were written for. Neither was dead in the `#coherence-kink` sense -- the
branch ran, the arithmetic was correct, telemetry showed it firing -- it just
could not change the answer, because its headroom term was computed by a rule
five times more generous than the push it was capping:

    floor = max(authored, ARMOR_TO_SKIN_BUFFER + min(amp, AUTHORED_INFLATE_AMP_CAP))
    push  = ADAPTIVE_CLEARANCE_BASE + ADAPTIVE_CLEARANCE_MORPH_FACTOR * amp

On a breast (amplitude p50 3.944u on the UBE body this pack fits) that put the
floor at 1.650u against a shipped standoff of 1.436u and an authored one of
0.948u. `authored` could not win the `maximum` for any author, any garment, any
flag combination -- so two sessions of A/B measurement on `CBBE2UBE_AUTHORED_
INFLATE` and `CBBE2UBE_SRC_NORMAL_FIX` were reading an inert pass and concluding
things about the author's fit.

That is not caught by the dead-gate audit, which looks for gates that never
change value. This one is a gate whose two sides are never both reachable, and
the only way to see it is to compare the cap against the quantity it bounds.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_convert as nc  # noqa: E402,F401  (`_nc()` resolves it)
from src import nif_convert_fitgeom as fg  # noqa: E402

# Measured on the UBE body this pack is fitted to (`floor_probe`), and on the
# shipped pack against the author's own build (`bust_gap.py`, 38 shapes common
# to every arm). Re-measure before moving them; they are observations, not
# targets.
BUST_AMPLITUDE_P50 = 3.944
BELLY_AMPLITUDE_P50 = 2.750
BUTT_AMPLITUDE_P50 = 0.115
BUST_SHIPPED_STANDOFF = 1.436
BUST_AUTHORED_STANDOFF = 0.948


def _adaptive_push(amp):
    """The per-vert clearance the ramp itself allocates -- the quantity the
    authored floor exists to bound. Mirrors `inflate_armor_outward`."""
    return min(fg.ADAPTIVE_CLEARANCE_BASE
               + fg.ADAPTIVE_CLEARANCE_MORPH_FACTOR * amp,
               fg.ADAPTIVE_CLEARANCE_MORPH_MAX)


@pytest.mark.parametrize("amp", [0.0, 0.115, 0.85, 1.66, 2.75, 3.944, 5.34, 8.7])
def test_the_floor_never_reserves_more_room_than_the_push_it_caps(amp):
    """THE INVARIANT. A floor that reserves more headroom than the ramp hands
    out cannot bind, and a pass that cannot bind is not a pass -- it is a
    comment. Checked across the whole measured amplitude range, from static
    zones (butt 0.115) to the belly's outliers (8.7)."""
    room = float(fg._authored_floor_amp_room(np.array([amp]))[0])
    assert room <= _adaptive_push(amp) + 1e-9, (
        f"at amplitude {amp}u the authored floor reserves {room:.3f}u while the "
        f"ramp it caps allocates {_adaptive_push(amp):.3f}u -- the floor is "
        "above the push, so it can never reduce one")


def test_the_floor_can_actually_bind_on_a_breast():
    """The band the whole mechanism was aimed at. If the floor sits at or above
    what we already ship there, turning the flag on measures nothing."""
    room = float(fg._authored_floor_amp_room(np.array([BUST_AMPLITUDE_P50]))[0])
    assert room < BUST_SHIPPED_STANDOFF, (
        f"bust floor {room:.3f}u >= shipped standoff {BUST_SHIPPED_STANDOFF}u: "
        "the floor cannot reduce the bust push, which is the defect it exists "
        "to fix")


def test_the_old_formula_would_fail_that__the_control():
    """The control. Both assertions above would pass against a checker that had
    silently stopped reading the real constants, so reproduce the OLD rule from
    the same constants and prove it fails where the new one passes. If this
    stops failing, the numbers moved and the test above proves nothing."""
    old = nc.ARMOR_TO_SKIN_BUFFER + min(BUST_AMPLITUDE_P50,
                                        nc.AUTHORED_INFLATE_AMP_CAP)
    assert old >= BUST_SHIPPED_STANDOFF, (
        f"the superseded rule now yields {old:.3f}u, below the shipped "
        f"{BUST_SHIPPED_STANDOFF}u -- the constants have moved and this file's "
        "measurements need redoing")
    assert old > BUST_AUTHORED_STANDOFF


@pytest.mark.parametrize("amp", [0.0, 0.115, 0.85, 1.66, 2.75, 3.944, 5.34, 8.7])
def test_the_morph_driven_headroom_is_never_given_away(amp):
    """The other side of the invariant, and the safety argument for touching the
    last line against skin through steel.

    The requirement splits in two: the MORPH-DRIVEN part scales with how far the
    body can grow here and is not the author's to waive, while the STATIC part
    is. So the floor must never fall below `buffer + factor * amplitude`, and it
    must never fall below the physical buffer at all.

    Using the ramp's `ADAPTIVE_CLEARANCE_BASE` as that minimum instead is what a
    first cut did, and it is wrong in the other direction: it overrode an author
    who had the vertex at 0.2u up to 0.25u on a body that does not move there,
    which `test_a_tight_author_over_a_still_body_is_left_tighter` catches."""
    room = float(fg._authored_floor_amp_room(np.array([amp]))[0])
    ceiling = min(fg.ADAPTIVE_CLEARANCE_MORPH_MAX, nc.AUTHORED_INFLATE_AMP_CAP)
    want = min(nc.ARMOR_TO_SKIN_BUFFER
               + fg.ADAPTIVE_CLEARANCE_MORPH_FACTOR * amp, ceiling)
    assert room == pytest.approx(want, abs=1e-9)
    assert room >= nc.ARMOR_TO_SKIN_BUFFER - 1e-9


def test_the_high_morph_bands_are_the_ones_that_move_most():
    """The bands that were disarmed must change materially, or the fix is
    cosmetic. Bust and belly are where the whole +0.467u gap against the author
    was measured; the butt, which was never the defect, moves an order of
    magnitude less."""
    def _old(amp):
        return nc.ARMOR_TO_SKIN_BUFFER + min(amp, nc.AUTHORED_INFLATE_AMP_CAP)

    def _new(amp):
        return float(fg._authored_floor_amp_room(np.array([amp]))[0])

    for amp in (BUST_AMPLITUDE_P50, BELLY_AMPLITUDE_P50):
        assert _new(amp) < _old(amp) - 0.3, (
            f"amplitude {amp}u: floor {_old(amp):.3f} -> {_new(amp):.3f}u is "
            "not a meaningful reduction on a band that was inert")
    butt_delta = _old(BUTT_AMPLITUDE_P50) - _new(BUTT_AMPLITUDE_P50)
    bust_delta = _old(BUST_AMPLITUDE_P50) - _new(BUST_AMPLITUDE_P50)
    assert butt_delta < 0.1
    assert bust_delta > 7 * butt_delta, (
        f"butt moved {butt_delta:.3f}u against the bust's {bust_delta:.3f}u -- "
        "this is meant to be a reallocation concentrated where the disarming "
        "was, not a flat loosening of the whole body")


def test_the_new_bust_floor_lands_on_the_authored_standoff():
    """NOT A FITTED TARGET -- a check. `ADAPTIVE_CLEARANCE_MORPH_FACTOR` was
    tuned against poke-through long before anyone measured the author's bust
    spacing, and the two numbers were never compared. Allocating the floor by
    that factor puts the breast at 0.939u against an independently measured
    authored 0.948u. If a later tuning pulls them apart, the rule has stopped
    reproducing the author and the reason to trust it is gone."""
    room = float(fg._authored_floor_amp_room(np.array([BUST_AMPLITUDE_P50]))[0])
    assert abs(room - BUST_AUTHORED_STANDOFF) < 0.10, (
        f"bust floor {room:.3f}u vs authored {BUST_AUTHORED_STANDOFF}u -- the "
        "ramp factor no longer reproduces the author's spacing")


def test_a_floor_refuses_zero_length_source_normals():
    """The fail-open half. `dot(src_armor - src_body, 0)` is 0, which reads as
    'the author fitted this skin-tight' -- so an unguarded floor degrades into a
    flat clearance rule wearing the author's name. This pack's own source body
    ships 18436 of 18436 zero-length normals, so it is the DEFAULT case, not an
    edge one."""
    assert fg._authored_normals_usable(np.zeros((100, 3))) is False
    assert fg._authored_normals_usable(None) is False
    assert fg._authored_normals_usable(np.zeros((0, 3))) is False
    good = np.tile(np.array([0.0, 0.0, 1.0]), (100, 1))
    assert fg._authored_normals_usable(good) is True
    half = good.copy()
    half[:60] = 0.0
    assert fg._authored_normals_usable(half) is False, (
        "a mostly-zero array carries no usable authored signal either")
