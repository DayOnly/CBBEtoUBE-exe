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


# rglob, NOT glob: a non-recursive glob silently stopped covering 35 scripts the
# moment they moved into scripts/analysis/, and the suite stayed GREEN while
# checking less. Parametrised on the path relative to scripts/ so the test id
# names the subfolder and a future move is visible in the diff.
@pytest.mark.parametrize("script", sorted(
    str(p.relative_to(_SCRIPTS)).replace("\\", "/")
    for p in _SCRIPTS.rglob("*.py")))
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


# --- the same guard, extended to src/ -----------------------------------------
#
# The scripts/ guard above exists because four diagnostic scripts each re-derived
# a "breast band" by eye and all four landed on the upper chest. src/ was never
# covered, and it has the same failure: a band NAMED for the breast whose bounds
# are not the breast gives a CLEAN answer to the WRONG question.
#
# This checks the bounds rather than the lower edge alone, because the real
# offender found in src/ starts at a plausible z 93 and runs to z 118 -- past the
# upper chest (102-112) and into the neck.
_SRC = Path(__file__).resolve().parent.parent / "src"

# KNOWN, DELIBERATELY NOT SILENT. `_inflate_cloth_over_bust_butt` builds
#     breast = (y > 1.0) & (z > 93.0) & (z < 118.0) & (ny > 0.2)
# which is 16 units taller than BREAST_Z. It is gated on a front-facing normal,
# so it is not simply wrong -- but it is not the breast either, and narrowing it
# CHANGES the softcloth inflation area, i.e. fit. Listed here so it is visible
# and so any NEW wide band fails instead of joining it quietly. Removing the
# entry requires measuring the fit change, not just editing the number.
_KNOWN_WIDE_BREAST_BANDS = {"nif_convert.py": 1}


def _breastish_band_upper_bounds(text):
    """Upper z bound of every band assigned to a breast/bust-named variable."""
    out = []
    for m in re.finditer(
            r"\b(breast|bust)\w*\s*=\s*\(?[^\n]*?2\]\s*<=?\s*([0-9]+(?:\.[0-9]+)?)",
            text, re.I):
        out.append(float(m.group(2)))
    return out


@pytest.mark.parametrize("mod", sorted(p.name for p in _SRC.glob("*.py")))
def test_src_breast_bands_do_not_reach_past_the_upper_chest(mod):
    """A band called `breast` must not extend into the upper chest / neck.

    BREAST_Z tops out at 102 and UPPER_CHEST_Z (102-112) is named precisely so
    nobody reaches for it. A band ending above 112 is neither.
    """
    if mod == "body_zones.py":
        return
    text = (_SRC / mod).read_text(encoding="utf-8", errors="ignore")
    wide = [b for b in _breastish_band_upper_bounds(text) if b > UPPER_CHEST_Z[1]]
    allowed = _KNOWN_WIDE_BREAST_BANDS.get(mod, 0)
    assert len(wide) <= allowed, (
        f"{mod}: {len(wide)} breast-named band(s) end above z{UPPER_CHEST_Z[1]:.0f} "
        f"({wide}); the breast is z{BREAST_Z[0]:.0f}-{BREAST_Z[1]:.0f}. Import the "
        f"zone from src.body_zones, or add a measured justification to "
        f"_KNOWN_WIDE_BREAST_BANDS.")


def test_the_known_wide_band_still_exists():
    """The allowlist must not outlive what it excuses -- a stale entry silently
    weakens the guard for whatever replaces it."""
    text = (_SRC / "nif_convert.py").read_text(encoding="utf-8", errors="ignore")
    wide = [b for b in _breastish_band_upper_bounds(text) if b > UPPER_CHEST_Z[1]]
    assert len(wide) == _KNOWN_WIDE_BREAST_BANDS["nif_convert.py"], (
        "the known wide breast band changed; if it was narrowed (good), drop the "
        "_KNOWN_WIDE_BREAST_BANDS entry and record the measured fit change")
