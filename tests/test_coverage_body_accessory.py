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

r"""#coverage-body-accessory -- a hooded robe's hood is drawn on UBE with the robe.

THE DEFECT. The body pass kept only armatures with a converted mesh or a
hands/feet slot; the non-body pass skips any armour with a deforming slot. A
hood armature (31/41/43) on a robe was covered by neither, so on a UBE actor the
robe drew and the hood did not. Live: 101 armours.

THE RULE. A DefaultRace armature of the armour whose own BOD2 names slots and
none deforming rides along with the deforming armatures minted -- UBE-primary,
its own mesh. An armour the other rules leave out stays out whole.
`CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY=1` leaves the hood off again.
"""
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY"
BODY = 1 << 2                                   # slot 32
HOOD = (1 << 1) | (1 << 11) | (1 << 13)         # slots 31, 41, 43
ROBE = r"clothes\robes\robe_1.nif"
HOODM = r"clothes\robes\hood_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _arma(formid, slots, models, race=DEFAULT, bod2=True):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    if bod2:
        p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", race))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _robe(tmp_path, robe_models, hood=None):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    armas = [_arma(0x01000800, BODY, robe_models)]
    if hood is not None:
        armas.append(hood)
    q = encode_subrecord(b"EDID", encode_zstring("HoodedRobe"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", BODY | HOOD, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    armo = Record(sig=b"ARMO", flags=0, formid=0x01000810, payload=q)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=armas), Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _pass(tmp_path, world, conv):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, world, converted_rel_paths=conv, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True)
    return st, _minted(out)


def _hood(**kw):
    return _arma(0x01000801, HOOD, {b"MOD3": HOODM}, **kw)


def test_the_hood_rides_with_the_robe(tmp_path):
    st, minted = _pass(tmp_path, _robe(tmp_path, {b"MOD3": ROBE}, _hood()),
                       {ROBE.replace("\\", "/")})
    mod3 = sorted(m[b"MOD3"] for m in minted)
    assert mod3 == sorted(["!UBE\\" + ROBE, HOODM]), "the hood keeps its own mesh"
    assert st["body_accessory"] == ["mod.esp|801"]


def test_switched_off_the_hood_stays_off(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, minted = _pass(tmp_path, _robe(tmp_path, {b"MOD3": ROBE}, _hood()),
                       {ROBE.replace("\\", "/")})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]
    assert st["body_accessory"] == []


def test_an_armour_left_out_stays_out_whole(tmp_path):
    """The robe's world mesh was not converted (only its first-person one), so
    #coverage-world-mesh leaves the robe out; the hood must not go on alone."""
    first = r"clothes\robes\1strobe_1.nif"
    st, minted = _pass(tmp_path,
                       _robe(tmp_path, {b"MOD3": ROBE, b"MOD5": first}, _hood()),
                       {first.replace("\\", "/")})
    assert minted == [] and st["body_accessory"] == []


def test_a_custom_race_hood_is_not_taken(tmp_path):
    st, minted = _pass(tmp_path,
                       _robe(tmp_path, {b"MOD3": ROBE}, _hood(race=0x00013740)),
                       {ROBE.replace("\\", "/")})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]


def test_an_armature_without_its_own_slots_is_not_taken(tmp_path):
    """No BOD2 of its own: nothing says it is non-deforming."""
    st, minted = _pass(tmp_path,
                       _robe(tmp_path, {b"MOD3": ROBE}, _hood(bod2=False)),
                       {ROBE.replace("\\", "/")})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]


def test_a_deforming_second_armature_is_not_an_accessory(tmp_path):
    """Control: a second BODY armature with an unconverted mesh is not smuggled
    in by this rule -- the world-mesh rule owns it."""
    other = _arma(0x01000801, BODY, {b"MOD3": r"clothes\robes\under_1.nif"})
    st, minted = _pass(tmp_path, _robe(tmp_path, {b"MOD3": ROBE}, other),
                       {ROBE.replace("\\", "/")})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]
    assert st["body_accessory"] == []


def test_a_body_candidate_slot_is_not_an_accessory(tmp_path):
    """Slot 46 is a conversion slot: an unconverted armature there is body-fitted
    cloth the planner meant to convert, not a hood."""
    # Not cloak-named: the slot alone must keep it out.
    cape = _arma(0x01000801, 1 << 16, {b"MOD3": r"clothes\robes\sash46_1.nif"})
    st, minted = _pass(tmp_path, _robe(tmp_path, {b"MOD3": ROBE}, cape),
                       {ROBE.replace("\\", "/")})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]


def test_a_cape_on_a_free_slot_is_not_an_accessory(tmp_path):
    """Slot 35 is free, but a cape-named mesh is a cloak the planner admits for
    conversion by name; left unconverted it would draw its CBBE fit."""
    cape = _arma(0x01000801, 1 << 5, {b"MOD3": r"armor\witch\ShamanCapeF_1.nif"})
    st, minted = _pass(tmp_path, _robe(tmp_path, {b"MOD3": ROBE}, cape),
                       {ROBE.replace("\\", "/")})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]
    assert st["body_accessory"] == []


def test_the_hoods_are_reported(capsys):
    ac._report_coverage_holds([{"body_accessory": ["skyrim.esm|65BB5"]}])
    assert "1 hood/accessory armature(s) of body armour drawn on UBE" in (
        capsys.readouterr().out)
