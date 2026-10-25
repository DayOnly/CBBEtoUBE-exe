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

"""#armature-slot-partition (audit 2026-10-20 F18).

The game draws a partition only when the armature's BOD2 claims its slot. The
collapse and the over-cap split rewrote partitions to SBP_32_BODY, so a piece
whose armature does not claim 32 (a fur overlay on 57, a skirt on 49) vanished
under any body armour. With the armature's slots known, the rewrite keeps a slot
the armature draws; armatures that claim 32 are unchanged.
"""
from src import nif_convert as nc

SLOT = lambda s: 1 << (s - 30)  # noqa: E731


class _FakePart:
    def __init__(self, pid):
        self.id = pid
        self.flags = 257
        self.namedict = object()


class _FakeShape:
    def __init__(self, slot_ids):
        self.partitions = [_FakePart(i) for i in slot_ids]
        self.partition_tris = [i % max(1, len(slot_ids)) for i in range(10)]
        self.set_to = None

    def set_partitions(self, parts, tris):
        self.set_to = ([p.part_id for p in parts], list(tris))


class _FakePyn:
    class SkyPartition:
        def __init__(self, part_id, flags, namedict):
            self.part_id = part_id


def test_split_keeps_source_slot_when_armature_lacks_32():
    # The fur overlay: armature slot 57, source partition 57, over the vert cap.
    assert nc._preserved_dismember_slot(_FakeShape([57]), SLOT(57)) == 57
    # The skirt: armature slot 49, source partition 49, over the bone cap.
    assert nc._preserved_dismember_slot(_FakeShape([49, 49]), SLOT(49)) == 49


def test_armature_claiming_32_unchanged():
    assert nc._preserved_dismember_slot(_FakeShape([57]), SLOT(32) | SLOT(57)) is None
    assert nc._preserved_dismember_slot(_FakeShape([49]), SLOT(32)) is None


def test_unknown_slots_unchanged():
    assert nc._preserved_dismember_slot(_FakeShape([57])) is None
    assert nc._preserved_dismember_slot(_FakeShape([57]), 0) is None


def test_fixed_list_still_wins():
    assert nc._preserved_dismember_slot(_FakeShape([33]), SLOT(33)) == 33
    assert nc._preserved_dismember_slot(_FakeShape([37, 37]), SLOT(32)) == 37


def test_mixed_slots_pick_a_drawn_one():
    # Lowest source slot the armature claims.
    assert nc._preserved_dismember_slot(_FakeShape([52, 49]), SLOT(49) | SLOT(52)) == 49
    # None claimed -> the armature's own lowest slot, never an undrawn 32.
    assert nc._preserved_dismember_slot(_FakeShape([32, 38]), SLOT(46)) == 46


def test_off_switch(monkeypatch):
    monkeypatch.setattr(nc, "ARMATURE_SLOT_PARTITION", False)
    assert nc._preserved_dismember_slot(_FakeShape([57]), SLOT(57)) is None


def test_collapse_targets_drawn_slot(monkeypatch):
    monkeypatch.setattr(nc, "_pynifly", lambda: _FakePyn)
    s = _FakeShape([49, 54])
    assert nc._normalize_partitions(s, SLOT(49)) is True
    assert s.set_to[0] == [49]
    s = _FakeShape([38, 54])
    assert nc._normalize_partitions(s, SLOT(32)) is True
    assert s.set_to[0] == [nc.SBP_32_BODY_ID]
    s = _FakeShape([38, 54])
    assert nc._normalize_partitions(s) is True
    assert s.set_to[0] == [nc.SBP_32_BODY_ID]
