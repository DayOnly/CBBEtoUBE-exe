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
