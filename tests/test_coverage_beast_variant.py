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

r"""#coverage-beast-variant -- an armature that lists only beast races is not
minted for UBE.

THE DEFECT. Both coverage passes minted every DefaultRace-primary armature of an
armour for all UBE races. A beast patch adds its variant that way (primary
DefaultRace, additional Khajiit or Argonian races only). No human draws it -- the
playable races carry no armor race, so an actor matches an armature only by a
race it lists -- but on a UBE actor it drew over the human armature: two
hairstyles on one wig, a beast helmet inside the human one. Live: 37 armatures,
58 links. `CBBE2UBE_NO_COVERAGE_BEAST_VARIANT=1` mints them again.
"""
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_COVERAGE_BEAST_VARIANT"
HEAD = 1 << 0
HANDS = 1 << 3
NORD, KHAJIIT, KHAJIIT_V, ARGONIAN = 0x013746, 0x013745, 0x088845, 0x013740


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _arma(formid, slots, mod3, races):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    for r in races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _world(tmp_path, slots, armas):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    q = encode_subrecord(b"EDID", encode_zstring("Piece"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    armo = Record(sig=b"ARMO", flags=0, formid=0x01000810, payload=q)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=armas), Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _nonbody(tmp_path, armas):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, HEAD, armas), converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True)
    return st, sorted(m[b"MOD3"] for m in _minted(out))


HUMAN = lambda: _arma(0x01000800, HEAD, r"wig\human_1.nif", [NORD])
CAT = lambda: _arma(0x01000801, HEAD, r"wig\khajiit_1.nif", [KHAJIIT, KHAJIIT_V])


def test_the_beast_variant_is_left_off(tmp_path):
    st, mod3 = _nonbody(tmp_path, [HUMAN(), CAT()])
    assert mod3 == [r"wig\human_1.nif"]
    assert st["beast_variant_skipped"] == ["mod.esp|801"]


def test_switched_off_both_are_minted(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, mod3 = _nonbody(tmp_path, [HUMAN(), CAT()])
    assert mod3 == [r"wig\human_1.nif", r"wig\khajiit_1.nif"]
    assert st["beast_variant_skipped"] == []


def test_an_armature_without_additional_races_is_unchanged(tmp_path):
    st, mod3 = _nonbody(tmp_path, [_arma(0x01000800, HEAD, r"wig\plain_1.nif", [])])
    assert mod3 == [r"wig\plain_1.nif"] and st["beast_variant_skipped"] == []


def test_a_list_with_one_human_race_is_not_a_beast_variant(tmp_path):
    st, mod3 = _nonbody(tmp_path, [_arma(0x01000800, HEAD, r"wig\mixed_1.nif",
                                         [KHAJIIT, NORD])])
    assert mod3 == [r"wig\mixed_1.nif"] and st["beast_variant_skipped"] == []


def test_the_same_ids_from_another_plugin_are_not_beast_races():
    """A race counts as a beast race by its identity in Skyrim.esm, not by its
    low id alone."""
    p = encode_subrecord(b"MODL", struct.pack("<I", 0x01013745))
    assert up._is_beast_variant((p, ["Skyrim.esm", "Other.esp"], "Mod.esp")) is False
    q = encode_subrecord(b"MODL", struct.pack("<I", 0x00013745))
    assert up._is_beast_variant((q, ["Skyrim.esm", "Other.esp"], "Mod.esp")) is True


def test_a_beast_gauntlet_variant_is_left_off_in_the_body_pass(tmp_path):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    armas = [_arma(0x01000800, HANDS, r"armor\gloves_1.nif", [NORD]),
             _arma(0x01000801, HANDS, r"armor\glovesarg_1.nif", [ARGONIAN])]
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, HANDS, armas), converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True)
    assert sorted(m[b"MOD3"] for m in _minted(out)) == [r"armor\gloves_1.nif"]
    assert st["beast_variant_skipped"] == ["mod.esp|801"]


def test_the_variants_are_reported(capsys):
    ac._report_coverage_holds([{"beast_variant_skipped": ["mod.esp|801"]},
                               {"beast_variant_skipped": ["mod.esp|801"]}])
    assert "1 beast-race variant armature(s) left off UBE" in capsys.readouterr().out
