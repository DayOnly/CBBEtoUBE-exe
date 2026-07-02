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

"""Regression guard for the Traveling Mage equip CTD (2026-05-31).

`_normalize_partitions` collapses a multi-partition cloth shape into a single
SBP_32_BODY partition to unblock NioOverride morph routing. But collapsing a
LIMB partition (forearms=34) that a body-spanning shape legitimately owns
corrupts the skin partition's bone palette -> the renderer overruns its
bone-matrix buffer -> hard CTD on equip (proven on Traveling Mage's
`_Fuse00_TMage_Body`: source had SBP_34_FOREARMS + SBP_32_BODY, our output had
merged them into one).

The fix: forearms (34) and the other standard limb/accessory dismember slots
are in PRESERVE_DISMEMBER_SLOTS, so a shape owning one is left untouched. Only
the leg-region cloth slots (calves 38, modder leg slots) still collapse.
"""
from src import nif_convert


class _FakePart:
    def __init__(self, pid):
        self.id = pid
        self.flags = 257


class _FakeShape:
    def __init__(self, slot_ids):
        self.partitions = [_FakePart(i) for i in slot_ids]
        # partition_tris only consulted on the collapse path
        self.partition_tris = [0] * 10


def test_forearm_partition_preserved_no_collapse():
    """The exact CTD case: a body shape split across forearms(34)+body(32)
    must NOT be collapsed (returns False = left as-is)."""
    assert nif_convert._normalize_partitions(_FakeShape([34, 32])) is False


def test_standard_limb_slots_preserved():
    for slot in (33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 30, 31):
        assert nif_convert._normalize_partitions(
            _FakeShape([slot, 32])) is False, f"slot {slot} should be preserved"


def test_single_partition_is_noop():
    assert nif_convert._normalize_partitions(_FakeShape([32])) is False


def test_preserve_set_excludes_leg_region_collapse_targets():
    # Calves (38) and the modder leg slots stay COLLAPSIBLE — that's the
    # morph-routing fix (#118A) and collapsing them has never crashed.
    assert 38 not in nif_convert.PRESERVE_DISMEMBER_SLOTS
    assert 54 not in nif_convert.PRESERVE_DISMEMBER_SLOTS
    # Forearms is the slot we added.
    assert 34 in nif_convert.PRESERVE_DISMEMBER_SLOTS


# ---- _preserved_dismember_slot: keep an accessory's slot across an OVER-CAP
# split (the collapse path bails on a preserved slot, but the split path can't --
# it must re-slot, so it must keep the dismember slot or the accessory goes
# invisible). -----------------------------------------------------------------

def test_preserved_slot_uniform_accessory_kept():
    # Every partition on ONE preserved slot -> that slot is carried onto the
    # split partitions, so an over-cap gauntlet(33)/boot(37)/helmet(30) still
    # renders in its equip region instead of being rewritten to SBP_32_BODY.
    assert nif_convert._preserved_dismember_slot(_FakeShape([33, 33])) == 33
    assert nif_convert._preserved_dismember_slot(_FakeShape([37])) == 37
    assert nif_convert._preserved_dismember_slot(_FakeShape([30, 30, 30])) == 30


def test_preserved_slot_body_region_is_none():
    # Body-region shapes split onto SBP_32_BODY (the prior behavior).
    assert nif_convert._preserved_dismember_slot(_FakeShape([32])) is None
    assert nif_convert._preserved_dismember_slot(_FakeShape([32, 32])) is None


def test_preserved_slot_mixed_falls_back_none():
    # Mixed accessory+body or two preserved slots is ambiguous on a Z-rebinned
    # split -> None (fall back to SBP_32_BODY rather than guess a slot).
    assert nif_convert._preserved_dismember_slot(_FakeShape([33, 32])) is None
    assert nif_convert._preserved_dismember_slot(_FakeShape([33, 37])) is None


def test_preserved_slot_no_partitions_none():
    assert nif_convert._preserved_dismember_slot(_FakeShape([])) is None
