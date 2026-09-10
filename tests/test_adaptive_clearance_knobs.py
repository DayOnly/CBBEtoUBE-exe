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

"""`CBBE2UBE_INFLATION_MAGNITUDE` IS NOT AN ABLATION LEVER, and the two knobs
that actually drive pack-wide standoff are `CBBE2UBE_CLEARANCE_BASE` and
`CBBE2UBE_CLEARANCE_MORPH_FACTOR` (exposed 2026-09-06).

On the adaptive path -- which is every piece, since `ADAPTIVE_CLEARANCE_ENABLED`
is a hardcoded True and the OSD amplitude map is always found -- the pass does

    cap          = max(magnitude, ADAPTIVE_CLEARANCE_MORPH_MAX)
    per_vert_mag = clip(BASE + MORPH_FACTOR * amp, BASE, cap)

so `magnitude` enters ONLY through that `max`, and any value at or below
morph_max drops out completely. Setting it to 0 does NOT disable the pass.

This cost real measurements: the record carries several "inflate magnitude is
inert" results, and an arm at 0.35 was confirmed BYTE-IDENTICAL to one at 0.7
over 184 NIFs. They were all measuring a knob that cannot reach the pass. These
tests exist so nobody concludes "inflate is irreplaceable" from that knob again.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import nif_convert as nc                 # noqa: E402
from src import nif_convert_fitgeom as fg         # noqa: E402
from src.envflags import knob                     # noqa: E402


def per_vert_mag(magnitude, amp, base=None, factor=None, morph_max=None):
    """The pass's own arithmetic, lifted verbatim from `inflate_armor_outward`."""
    base = fg.ADAPTIVE_CLEARANCE_BASE if base is None else base
    factor = fg.ADAPTIVE_CLEARANCE_MORPH_FACTOR if factor is None else factor
    morph_max = fg.ADAPTIVE_CLEARANCE_MORPH_MAX if morph_max is None else morph_max
    cap = max(float(magnitude), float(morph_max))
    return np.clip(base + factor * np.asarray(amp, float), base, cap)


AMP = np.array([0.0, 0.5, 1.66, 3.35, 3.48, 5.34, 8.7])


def test_magnitude_at_or_below_morph_max_changes_nothing():
    """THE TRAP. 0.0, 0.35 and the 0.7 default all give the same push."""
    ref = per_vert_mag(0.7, AMP)
    for mag in (0.0, 0.1, 0.35, 0.5, 0.6, 1.0, fg.ADAPTIVE_CLEARANCE_MORPH_MAX):
        assert np.array_equal(per_vert_mag(mag, AMP), ref), mag


def test_zero_magnitude_does_not_disable_the_pass():
    """The comment used to say "0 = disable". Every vert still gets BASE."""
    got = per_vert_mag(0.0, AMP)
    assert (got >= fg.ADAPTIVE_CLEARANCE_BASE).all()
    assert got.max() > 0.0


def test_only_a_magnitude_above_morph_max_binds():
    """The BELT variant (1.5) is the one slot value that is not inert."""
    assert nc.ARMOR_INFLATION_MAGNITUDE_BELT > fg.ADAPTIVE_CLEARANCE_MORPH_MAX
    assert per_vert_mag(1.5, AMP).max() > per_vert_mag(0.7, AMP).max()


@pytest.mark.parametrize("name", ["ARMOR_INFLATION_MAGNITUDE",
                                  "ARMOR_INFLATION_MAGNITUDE_SLOT49",
                                  "ARMOR_INFLATION_MAGNITUDE_HANDS_FEET",
                                  "ARMOR_INFLATION_MAGNITUDE_SKIRT"])
def test_the_slot_table_is_inert_on_four_of_five_branches(name):
    """`_slot_aware_inflation_magnitude` looks like a per-slot tuning surface and
    is a no-op for everything but the belt. Pinned so that if morph_max is ever
    lowered below one of these, the fact that the branch WAKES UP is noticed."""
    assert getattr(nc, name) <= fg.ADAPTIVE_CLEARANCE_MORPH_MAX
    assert np.array_equal(per_vert_mag(getattr(nc, name), AMP),
                          per_vert_mag(0.7, AMP))


def test_base_and_factor_are_the_levers_that_actually_move_standoff():
    """The two that DO reach it -- the point of exposing them.

    Note what each one moves. BASE lifts the FLOOR, so it shows up on the
    low-amplitude verts. FACTOR is the SLOPE, so it moves the mid-range and
    leaves both ends alone: the floor is still BASE and the highest-amplitude
    verts are still pinned at the cap (amp 8.7 reaches 1.12 even at factor 0.10
    and clips to 1.1). Comparing maxima would therefore read "inert" -- the same
    shape of mistake the magnitude knob has been causing for weeks."""
    ref = per_vert_mag(0.7, AMP)

    lower_base = per_vert_mag(0.7, AMP, base=0.10)
    assert lower_base.min() < ref.min()
    assert (lower_base <= ref).all()

    lower_factor = per_vert_mag(0.7, AMP, factor=0.10)
    assert (lower_factor <= ref).all()
    assert lower_factor.sum() < ref.sum()          # the mid-range moved
    assert lower_factor.max() == ref.max()         # both still pinned at the cap


def test_the_new_env_names_are_live(monkeypatch):
    for env, default, val in (("CBBE2UBE_CLEARANCE_BASE", 0.25, 0.10),
                              ("CBBE2UBE_CLEARANCE_MORPH_FACTOR", 0.20, 0.10)):
        monkeypatch.delenv(env, raising=False)
        assert knob(env, default) == default
        monkeypatch.setenv(env, str(val))
        assert knob(env, default) == val


def test_defaults_are_the_literals_they_replaced():
    """Exposing a constant must not change what ships."""
    assert fg.ADAPTIVE_CLEARANCE_BASE == 0.25
    assert fg.ADAPTIVE_CLEARANCE_MORPH_FACTOR == 0.20
