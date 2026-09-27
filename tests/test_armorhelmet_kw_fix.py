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

r"""#armorhelmet-kw-fix -- the hair-only headgear test reads ArmorHelmet by its
real id.

THE DEFECT. A hair-slot-only armour counts as headgear (not a hairstyle) with a
gold value or the ArmorHelmet keyword. The keyword constant was 0x06BBD9, which
in Skyrim.esm is ArmorMaterialElven; ArmorHelmet is 0x06C0EE. So a zero-value
hair-slot helmet with ArmorHelmet was left off UBE actors, and a zero-value
hair-only armour with the elven material keyword was taken for a helmet.

THE RULE. The test reads 0x06C0EE from Skyrim.esm.
`CBBE2UBE_NO_ARMORHELMET_KW_FIX=1` reads the old id again.
"""
import struct

import pytest

from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_ARMORHELMET_KW_FIX"
HAIR = 1 << 1                        # slot 31
HELMET = 0x06C0EE                    # Skyrim.esm KYWD ArmorHelmet
ELVEN = 0x06BBD9                     # Skyrim.esm KYWD ArmorMaterialElven
MESH = r"helmets\hood\hood_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _payload(kw_fid, *, name=None, value=0):
    q = encode_subrecord(b"EDID", encode_zstring("HairHood"))
    if name is not None:
        q += encode_subrecord(b"FULL", encode_zstring(name))
    q += encode_subrecord(b"BOD2", struct.pack("<II", HAIR, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    q += encode_subrecord(b"KSIZ", struct.pack("<I", 1))
    q += encode_subrecord(b"KWDA", struct.pack("<I", kw_fid))
    q += encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
    q += encode_subrecord(b"DATA", struct.pack("<If", value, 0.1))
    return q


def _world(tmp_path, kw_fid, *, playable=False, name=None):
    """A zero-value hair-slot armour with one keyword. Non-playable and unnamed
    by default, so the wig rule (#coverage-wigs) cannot admit it."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    a = encode_subrecord(b"EDID", encode_zstring("HairHoodAA"))
    a += encode_subrecord(b"BOD2", struct.pack("<II", HAIR, 0))
    a += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    a += encode_subrecord(b"MOD3", encode_zstring(MESH))
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=a)
    armo = Record(sig=b"ARMO", flags=0 if playable else 0x4, formid=0x01000810,
                  payload=_payload(kw_fid, name=name))
    mod = _save(tmp_path / "Hoods.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]), Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _pass(tmp_path, world):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, world, converted_rel_paths=set(), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True)
    return st, sorted(m[b"MOD3"] for m in _minted(out))


def test_a_helmet_keyword_hair_armour_is_covered(tmp_path):
    st, mod3 = _pass(tmp_path, _world(tmp_path, HELMET))
    assert mod3 == [MESH], "headgear: minted with its own mesh"
    assert st["wigs"] == []


def test_an_elven_material_hair_armour_is_not_headgear(tmp_path):
    st, mod3 = _pass(tmp_path, _world(tmp_path, ELVEN))
    assert mod3 == []


def test_switched_off_the_old_id_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _st, helmet = _pass(tmp_path / "h", _world(_mk(tmp_path / "h"), HELMET))
    _st, elven = _pass(tmp_path / "e", _world(_mk(tmp_path / "e"), ELVEN))
    assert helmet == [] and elven == [MESH]


def test_a_playable_named_helmet_counts_as_headgear_not_a_wig(tmp_path):
    """The one live armour whose output the fix would change with the wig rule
    off: drawn either way with the wig rule on, now as headgear."""
    st, mod3 = _pass(tmp_path, _world(tmp_path, HELMET, playable=True, name="Hood"))
    assert mod3 == [MESH] and st["wigs"] == []


def test_the_keyword_counts_only_from_skyrim_esm():
    pay = _payload(HELMET)                       # master index 0
    assert up._hair_only_armo_is_equippable_headgear(pay, ["Skyrim.esm"])
    assert not up._hair_only_armo_is_equippable_headgear(pay, ["Other.esm"])
    assert not up._hair_only_armo_is_equippable_headgear(pay, [])


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p
