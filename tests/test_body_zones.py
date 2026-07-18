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

"""The anatomical z-bands must be defined ONCE and must bracket what they name.

Four diagnostic scripts each re-derived a "breast band" by eye, and four of them read
z 100-108 -- the UPPER CHEST. Measured there a cuirass reports +0.98u clearance and zero
poke-through, while the real breast (z 90-102) was 8% penetrated at rest. A band error
never produces a wrong answer; it produces a CLEAN answer to the wrong question, which is
why it survived an entire session. So: one definition, and a guard that no script quietly
reintroduces its own.

The apex check is the one that matters. If someone shifts BREAST_Z off the breast, the
band no longer contains the measured apex and this fails.
#body-zones"""
import re
from pathlib import Path

import numpy as np
import pytest

from src.body_zones import (
    BREAST_Z, BREAST_APEX_Z, BELLY_Z, BUTT_Z, UPPER_CHEST_Z,
    breast_mask, belly_mask, butt_mask, back_mask,
)

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_breast_band_contains_the_measured_apex():
    """Front-most body vertex z=95; strongest breast-morph vertex z=96. A band that
    doesn't bracket those isn't the breast."""
    assert BREAST_Z[0] < BREAST_APEX_Z < BREAST_Z[1]
    assert 95.0 <= BREAST_APEX_Z <= 96.0


def test_upper_chest_is_a_separate_band_above_the_breast():
    """z 100-108 is where the mismeasurement lived. It must not overlap the apex."""
    assert UPPER_CHEST_Z[0] >= BREAST_Z[1]
    assert not (UPPER_CHEST_Z[0] < BREAST_APEX_Z < UPPER_CHEST_Z[1])


def test_zones_are_ordered_and_disjoint_in_z():
    """butt < belly < breast < upper chest, walking up the body."""
    for lo, hi in (BUTT_Z, BELLY_Z, BREAST_Z, UPPER_CHEST_Z):
        assert lo < hi
    assert BUTT_Z[1] <= BELLY_Z[1]
    assert BELLY_Z[1] <= BREAST_Z[1]
    assert BREAST_Z[1] <= UPPER_CHEST_Z[0]


def _verts(z, y, x=0.0):
    return np.array([[x, y, z]], dtype=np.float64)


def test_breast_mask_is_front_only():
    """A z-band alone spans the BACK. Gating the bust clearance floor on height without
    a front test pushed a fur cuirass's back from 0.74u to 1.02u."""
    assert breast_mask(_verts(BREAST_APEX_Z, +6.0))[0]
    assert not breast_mask(_verts(BREAST_APEX_Z, -6.0))[0]   # rear at breast height
    assert not breast_mask(_verts(BREAST_APEX_Z, +6.0, x=20.0))[0]   # side/arm


def test_back_mask_is_the_rear_control_surface():
    """back_mask must catch exactly what breast_mask must not."""
    rear = _verts(BREAST_APEX_Z, -6.0)
    assert back_mask(rear)[0] and not breast_mask(rear)[0]


def test_upper_chest_is_not_selected_by_breast_mask():
    """The exact vertices whose 'clean' numbers hid the defect."""
    assert not breast_mask(_verts(105.0, +6.0))[0]


def test_belly_and_butt_masks_pick_their_own_sides():
    assert belly_mask(_verts(85.0, +4.0))[0]
    assert butt_mask(_verts(70.0, -6.0))[0]
    assert not butt_mask(_verts(70.0, +6.0))[0]     # butt is rear-facing


@pytest.mark.parametrize("script", sorted(p.name for p in _SCRIPTS.glob("*.py")))
def test_no_script_redefines_a_breast_band(script):
    """Guard the guard: a script that hardcodes its own z-band can silently drift off
    the anatomy again. If a script needs the breast, it imports it."""
    src = (_SCRIPTS / script).read_text(encoding="utf-8", errors="ignore")
    # The tell is a band whose LOWER bound sits in the upper chest (z >= 99..108) --
    # that band cannot be the breast. An UPPER bound of 100 is fine and common (a torso
    # slice ending at the chest), so matching any appearance of these numbers
    # false-positives on `(z >= 60) & (z <= 100)`.
    offenders = re.findall(r"2\]\s*>=?\s*(?:99|10[0-8])\b", src)
    assert not offenders, (
        f"{script} starts a z-band in the upper chest ({offenders}); the breast is "
        f"z{BREAST_Z[0]:.0f}-{BREAST_Z[1]:.0f} -- import it from src.body_zones")
