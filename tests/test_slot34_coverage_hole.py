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

"""#slot34-coverage-hole -- forearms and calves were covered by NOBODY.

THE DEFECT. Two winner-scan passes divide the work by slot: the NON-BODY pass
skips anything carrying a deforming slot, and the BODY pass handled only TORSO
(32) plus pure hands/feet (33/37), skipping the rest because that was "the
per-source builder's job".

Unified coverage made the winner scan the SOLE generator and LEAVES THE
PER-SOURCE PATCHES UNMERGED, so the fallback that sentence relies on no longer
runs. `_DEFORMING_SLOTS_MASK` is 32/33/34/37/38, so the hole was exactly
**slot 34 (forearms) and slot 38 (calves)**: rejected by the non-body pass for
HAVING a deforming slot, and by the body pass for not being body or hands/feet.

Measured on a real pack before the fix: ALL 51 slot-34-only and ALL 7
slot-38-only ARMOs had no armature link -- every one equippable and INVISIBLE on
a UBE race. It surfaced only because a user reported one piece.

After the fix, against the same live load order: all four slot-34 arm ARMOs and
both real slot-38 pieces are covered, minted armatures 2388 -> 2408, and a
modder's tower SHIELD parked on slot 38 is still correctly excluded.
"""
import inspect

from src import ube_patcher as up


def _bit(slot):
    return 1 << (slot - 30)


# ---------------------------------------------------- the shape of the hole

def test_deforming_mask_covers_forearms_and_calves():
    """If 34/38 ever leave this mask the hole moves rather than closing: the
    non-body pass would then claim them, and it mints with non-body semantics."""
    for slot in (32, 33, 34, 37, 38):
        assert up._DEFORMING_SLOTS_MASK & _bit(slot), slot


def test_forearms_and_calves_are_neither_body_nor_hands_feet():
    """The exact class that fell down the gap -- deforming, but matching neither
    of the two sub-roles the body pass used to handle."""
    for slot in (34, 38):
        assert not (up._BIPED_SLOT_BODY_BIT & _bit(slot)), slot
        assert not (up._BIPED_SLOT_HANDS_FEET_BITS & _bit(slot)), slot


def test_body_and_hands_feet_bits_are_what_the_gate_thinks_they_are():
    """Negative control for the test above: it must be able to fail. If these
    bits were empty, 'neither body nor hands/feet' would pass for everything."""
    assert up._BIPED_SLOT_BODY_BIT & _bit(32)
    assert up._BIPED_SLOT_HANDS_FEET_BITS & _bit(33)
    assert up._BIPED_SLOT_HANDS_FEET_BITS & _bit(37)


# ------------------------------------------------------------ the fix itself

def test_unified_mode_does_not_skip_other_deforming_slots():
    """The one-line regression. In unified mode there is no per-source fallback,
    so the skip must not fire -- otherwise forearms/calves go invisible again."""
    src = inspect.getsource(up.generate_modded_body_ube_coverage_patch)
    assert "_unified = bool(cover_hands_feet)" in src, \
        "the unified-mode flag is gone -- the coverage hole is back"
    assert "not _cover_hf and not _unified" in src, \
        ("the skip no longer exempts unified mode; slot 34/38 will be covered "
         "by neither pass")


def test_non_unified_mode_still_defers_to_the_per_source_builder():
    """The fix must NOT change the split-role path: when the per-source patches
    ARE merged, this pass should still leave non-body-non-HF deforming items to
    them, or both would cover the same armour (double armature = renders twice)."""
    src = inspect.getsource(up.generate_modded_body_ube_coverage_patch)
    assert "if cover_all and not _is_body and not _cover_hf and not _unified:" in src


# --------------------------------------------------- the guard that makes it safe

def test_minting_still_requires_default_race_and_a_converted_mesh():
    """Admitting 34/38 adds NO new guard on purpose -- these two are what stop a
    non-body mesh being handed UBE body races (the documented actor-setup
    ACCESS_VIOLATION). Verified against the worst case in a real pack: a tower
    SHIELD on slot 38 fails both, and stayed excluded after the fix."""
    src = inspect.getsource(up.generate_modded_body_ube_coverage_patch)
    assert "v[3] == DEFAULT_RACE" in src, \
        "the beast-race guard is gone -- a custom-race armature could be minted"
    assert "_conv_exists(mp) for mp in _arma_models" in src, \
        "the converted-mesh requirement is gone -- an unconverted mesh could be minted"
