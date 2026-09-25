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

r"""#accessory-race-guard -- a robe's hood rides along only if a human draws it.

THE DEFECT. #coverage-body-accessory built its list after #coverage-beast-variant
took a beast-only variant out of the body pass, and asked only for DefaultRace
and non-deforming slots of its own. A hooded robe with a human hood and a Khajiit
hood variant minted BOTH hoods for UBE, so a UBE actor drew two hoods (the beast
switch had no effect on it). A hood that already names a UBE race got a second
UBE copy drawn over it.

THE RULE. The accessory list skips a beast variant the beast rule skipped (so
the beast switch still brings it back) and an armature that already names a UBE
race. `CBBE2UBE_NO_ACCESSORY_RACE_GUARD=1` takes both again.
"""
import struct

import pytest

from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_ACCESSORY_RACE_GUARD"
BEAST_OFF = "CBBE2UBE_NO_COVERAGE_BEAST_VARIANT"
ACC_OFF = "CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY"
BODY = 1 << 2                                   # slot 32
HOOD = (1 << 1) | (1 << 11) | (1 << 13)         # slots 31, 41, 43
NORD, KHAJIIT, KHAJIIT_V = 0x00013746, 0x00013745, 0x00088845
UBE_RACE = 0x01005734                            # UBE_AllRace.esp, master index 1
ROBE = r"clothes\robes\robe_1.nif"
HOODM = r"clothes\robes\hood_1.nif"
HOODKH = r"clothes\robes\hoodkhajiit_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, BEAST_OFF, ACC_OFF):
        monkeypatch.delenv(k, raising=False)


def _arma(formid, slots, mod3, races):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    for r in races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _pass(tmp_path, hoods):
    """A hooded robe: the converted robe armature plus `hoods`."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    armas = [_arma(0x02000800, BODY, ROBE, [NORD])] + hoods
    q = encode_subrecord(b"EDID", encode_zstring("HoodedRobe"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", BODY | HOOD, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    armo = Record(sig=b"ARMO", flags=0, formid=0x02000810, payload=q)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
                [Group(label=b"ARMA", records=armas),
                 Group(label=b"ARMO", records=[armo])])
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod], converted_rel_paths={ROBE.replace("\\", "/")},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True)
    return st, sorted(m[b"MOD3"] for m in _minted(out))


def _beast_hoods():
    return [_arma(0x02000801, HOOD, HOODM, [NORD]),
            _arma(0x02000802, HOOD, HOODKH, [KHAJIIT, KHAJIIT_V])]


def _ube_hood():
    return [_arma(0x02000801, HOOD, HOODM, [NORD, UBE_RACE])]


def test_the_beast_hood_variant_does_not_ride_along(tmp_path):
    st, mod3 = _pass(tmp_path, _beast_hoods())
    assert mod3 == sorted(["!UBE\\" + ROBE, HOODM]), "one hood on a UBE actor"
    assert st["body_accessory"] == ["mod.esp|801"]
    assert st["beast_variant_skipped"] == ["mod.esp|802"]


def test_the_beast_switch_brings_the_variant_back(tmp_path, monkeypatch):
    """The guard follows the beast rule's own skip list, so its switch still
    controls the accessory armatures too."""
    monkeypatch.setenv(BEAST_OFF, "1")
    st, mod3 = _pass(tmp_path, _beast_hoods())
    assert mod3 == sorted(["!UBE\\" + ROBE, HOODM, HOODKH])
    assert st["body_accessory"] == ["mod.esp|801", "mod.esp|802"]
    assert st["beast_variant_skipped"] == []


def test_a_hood_that_already_names_a_ube_race_is_not_copied(tmp_path):
    st, mod3 = _pass(tmp_path, _ube_hood())
    assert mod3 == ["!UBE\\" + ROBE], "the hood already draws on UBE"
    assert st["body_accessory"] == []


def test_switched_off_both_are_taken_again(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, mod3 = _pass(tmp_path / "beast", _beast_hoods())
    assert mod3 == sorted(["!UBE\\" + ROBE, HOODM, HOODKH])
    assert st["body_accessory"] == ["mod.esp|801", "mod.esp|802"]
    st, mod3 = _pass(tmp_path / "ube", _ube_hood())
    assert mod3 == sorted(["!UBE\\" + ROBE, HOODM])
    assert st["body_accessory"] == ["mod.esp|801"]
