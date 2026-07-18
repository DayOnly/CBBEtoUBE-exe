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

"""Guard for the missing-_0 invisibility bug: the converter's crash guard looks
up an armor mesh's biped slot to decide keep/drop. The ESP names ONE weight
(usually _1); the engine derives _0. If the slot lookup isn't weight-agnostic,
the _0 sibling reads slot 0, fails the body-slot test, and a non-body-fit piece
(gauntlet/glove/cloak) gets DROPPED -> only _1 ships -> the piece is INVISIBLE
in game (Skyrim needs BOTH _0 and _1). This locks the weight-folding property
the guard depends on."""
from src.auto_convert import _weight_base_key, _BODY_SLOT_BITS


def test_weight_variants_share_base_key():
    # _0 and _1 of the same piece MUST normalize to the same key
    assert (_weight_base_key("ModArmor/GauntletsF_0.nif")
            == _weight_base_key("ModArmor/GauntletsF_1.nif"))
    # case / meshes-prefix / backslashes don't matter
    assert (_weight_base_key("meshes\\Armor\\X_0.nif")
            == _weight_base_key("armor/x_1.NIF"))


def test_gauntlet_slot33_is_a_body_bit():
    # the _1 gauntlet is kept because slot 33 is a body slot bit; the fix makes
    # the _0 sibling inherit that same slot via the weight-agnostic fold.
    assert (_BODY_SLOT_BITS & (1 << (33 - 30))) != 0


def test_both_weights_kept_with_agnostic_lookup():
    # replicate the guard's keep/drop decision with the weight-agnostic fold
    slot_map = {"x/gauntletsf_1.nif": 1 << (33 - 30)}   # ESP names only _1
    agn = {}
    for k, v in slot_map.items():
        agn[_weight_base_key(k)] = agn.get(_weight_base_key(k), 0) | v
    for rel in ("x/GauntletsF_0.nif", "x/GauntletsF_1.nif"):
        gslot = slot_map.get(rel.lower(), 0) or agn.get(_weight_base_key(rel), 0)
        assert (gslot & _BODY_SLOT_BITS) != 0, f"{rel} would be dropped -> invisible"
