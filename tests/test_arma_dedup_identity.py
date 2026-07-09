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

"""`_arma_dedup_identity` must key on the FULL render identity, not just
(rnam, mod2, mod3). Two ARMAs that share meshes + race but cover DIFFERENT biped
slots (or differ in 1st-person mesh) are NOT duplicates -- collapsing them drops
real slot coverage on equip. The group key is [:6]; [6] is the completeness
tiebreak. #arma-dedup-slots
"""
import struct

import src.esp as esp
import src.ube_patcher as up


def _arma(rnam, mod2, mod3, *, mod4=None, mod5=None, bod2=b"\x04\x00\x00\x00\x00\x00\x00\x00",
          edid="Test_UBE", extra=b""):
    p = esp.encode_subrecord(b"EDID", esp.encode_zstring(edid))
    p += esp.encode_subrecord(b"BOD2", bod2)
    p += esp.encode_subrecord(b"RNAM", struct.pack("<I", rnam))
    p += esp.encode_subrecord(b"MOD2", esp.encode_zstring(mod2))
    p += esp.encode_subrecord(b"MOD3", esp.encode_zstring(mod3))
    if mod4:
        p += esp.encode_subrecord(b"MOD4", esp.encode_zstring(mod4))
    if mod5:
        p += esp.encode_subrecord(b"MOD5", esp.encode_zstring(mod5))
    return p + extra


def _key(payload):
    return up._arma_dedup_identity(payload)[:7]


def test_same_mesh_different_slots_are_not_duplicates():
    body = _arma(0x100, "m.nif", "f.nif", bod2=struct.pack("<II", 0x4, 0))
    body_amulet = _arma(0x100, "m.nif", "f.nif", bod2=struct.pack("<II", 0x4 | 0x100, 0))
    assert _key(body) != _key(body_amulet)          # slot flags distinguish them


def test_same_mesh_different_actslike44_are_not_duplicates():
    a = _arma(0x100, "m.nif", "f.nif", bod2=struct.pack("<II", 0x4, 0))
    b = _arma(0x100, "m.nif", "f.nif", bod2=struct.pack("<II", 0x4, 0x1))  # flags differ
    assert _key(a) != _key(b)


def test_different_first_person_mesh_not_duplicate():
    a = _arma(0x100, "m.nif", "f.nif", mod5="!UBE\\1stF.nif")
    b = _arma(0x100, "m.nif", "f.nif", mod5="!UBE\\other1stF.nif")
    assert _key(a) != _key(b)


def test_identical_except_edid_are_duplicates():
    a = _arma(0x100, "m.nif", "f.nif", edid="ArmorA_UBE")
    b = _arma(0x100, "m.nif", "f.nif", edid="ArmorB_UBE")
    assert _key(a) == _key(b)                        # EDID is not part of identity


def test_different_alt_texture_set_not_duplicate():
    # Same mesh/slots/race but different embedded alt-texture set (colour variant).
    a = _arma(0x100, "m.nif", "f.nif", extra=esp.encode_subrecord(b"MO3S", b"\x11" * 8))
    b = _arma(0x100, "m.nif", "f.nif", extra=esp.encode_subrecord(b"MO3S", b"\x22" * 8))
    assert _key(a) != _key(b)


def test_completeness_tiebreak_is_last_element():
    lean = _arma(0x100, "m.nif", "f.nif")
    rich = _arma(0x100, "m.nif", "f.nif", extra=esp.encode_subrecord(b"DNAM", b"\x00" * 12))
    # same render key, different subrecord count -> tiebreak differs
    assert _key(lean) == _key(rich)
    assert up._arma_dedup_identity(rich)[7] > up._arma_dedup_identity(lean)[7]
