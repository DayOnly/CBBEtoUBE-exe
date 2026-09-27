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

r"""#coverage-wigs -- a wig the player can equip is drawn on UBE actors.

THE DEFECT. A hair-slot-only armour counted as headgear only with a gold value
or the ArmorHelmet keyword. Wigs have neither, so 100 playable, named wigs were
invisible on UBE actors. The user chose to cover them (2026-09-24).

THE RULE. Playable in the winning record and named counts as headgear too. The
wig's armatures are minted like a helmet's -- UBE-primary, own mesh -- including
the hidden SMP body collider the wig carries. `CBBE2UBE_NO_COVERAGE_WIGS=1`
leaves them uncovered.
"""
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_COVERAGE_WIGS"
HAIR = (1 << 1) | (1 << 11)          # slots 31 + 41
WIG = r"hairpack\wig\amber_1.nif"
COLLIDER = r"hairpack\wig\_CollisionbodyX_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _arma(formid, slots, mod3):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    p += encode_subrecord(b"MODL", struct.pack("<I", 0x013746))   # NordRace
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _wig(tmp_path, *, playable=True, name="Amber Lights", value=0):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    armas = [_arma(0x01000800, HAIR, WIG), _arma(0x01000801, 1 << 11, COLLIDER)]
    q = encode_subrecord(b"EDID", encode_zstring("WigAmber"))
    if name is not None:
        q += encode_subrecord(b"FULL", encode_zstring(name))
    q += encode_subrecord(b"BOD2", struct.pack("<II", HAIR, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    q += encode_subrecord(b"DATA", struct.pack("<If", value, 0.1))
    armo = Record(sig=b"ARMO", flags=0 if playable else 0x4, formid=0x01000810, payload=q)
    mod = _save(tmp_path / "Wigs.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=armas), Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _pass(tmp_path, world):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, world, converted_rel_paths=set(), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True)
    return st, sorted(m[b"MOD3"] for m in _minted(out))


def test_a_playable_named_wig_is_drawn_with_its_collider(tmp_path):
    st, mod3 = _pass(tmp_path, _wig(tmp_path))
    assert mod3 == sorted([WIG, COLLIDER]), "own meshes, collider included"
    assert [e for _a, e in st["wigs"]] == ["WigAmber"]


def test_switched_off_the_wig_stays_uncovered(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, mod3 = _pass(tmp_path, _wig(tmp_path))
    assert mod3 == [] and st["wigs"] == []


def test_a_non_playable_hairstyle_is_not_a_wig(tmp_path):
    """An NPC's hair worn as armour: no UBE armature, as before."""
    st, mod3 = _pass(tmp_path, _wig(tmp_path, playable=False))
    assert mod3 == [] and st["wigs"] == []


def test_an_unnamed_hair_armour_is_not_a_wig(tmp_path):
    st, mod3 = _pass(tmp_path, _wig(tmp_path, name=None))
    assert mod3 == []


def test_real_headgear_is_covered_as_before_and_not_counted_as_a_wig(tmp_path):
    """Control: a gold value already made it headgear; the wig rule adds nothing."""
    st, mod3 = _pass(tmp_path, _wig(tmp_path, value=25))
    assert mod3 == sorted([WIG, COLLIDER]) and st["wigs"] == []


def test_a_localized_name_counts():
    assert up._is_playable_named(0, encode_subrecord(b"FULL", struct.pack("<I", 7)))
    assert not up._is_playable_named(0, encode_subrecord(b"FULL", b"\x00\x00\x00\x00"))
    assert not up._is_playable_named(0x4, encode_subrecord(b"FULL", encode_zstring("x")))


def test_the_wigs_are_reported(capsys):
    ac._report_coverage_holds([{"wigs": [(("wigs.esp", 0x810), "WigAmber")]}])
    out = capsys.readouterr().out
    assert "1 playable wig(s) drawn on UBE as headgear" in out and "WigAmber" in out
