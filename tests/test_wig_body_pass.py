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

r"""#wig-body-pass -- a wig whose armour also carries a deforming slot is drawn
on UBE actors.

THE DEFECT. #coverage-wigs lived only in the non-body pass. An armour with any
of slots 32/33/34/37/38 goes to the body pass, which mints an armature only
with a converted mesh -- a wig never has one. So a playable, named wig whose
armour the author also flagged for the calves (armour 31+38, armature 31/41)
drew nothing on a UBE actor, and the wearer went bald.

THE RULE. When no other rule admitted an armature of the armour, a hair-only
armature (its own BOD2: slots 31/41 only) of a playable, named armour is minted
with its own mesh -- DefaultRace for every UBE race, else by its race list.
Never a beast variant or an armature that already names a UBE race; the dead-
armature rule still applies; no other accessory rides along.
`CBBE2UBE_NO_WIG_BODY_PASS=1` leaves it uncovered.
"""
import struct

import pytest

from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_human_race_list import _minted, _ube
from tests.test_coverage_ube_twin import DEFAULT, _save

OFF = "CBBE2UBE_NO_WIG_BODY_PASS"
WIGS_OFF = "CBBE2UBE_NO_COVERAGE_WIGS"
RACE_LIST_OFF = "CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST"
ACC_GUARD_OFF = "CBBE2UBE_NO_ACCESSORY_RACE_GUARD"

HAIR = (1 << 1) | (1 << 11)       # slots 31 + 41
CALVES = 1 << 8                   # slot 38
BODY = 1 << 2                     # slot 32
CIRCLET = 1 << 12                 # slot 42
HOOD = (1 << 1) | (1 << 11) | (1 << 13)   # 31 + 41 + 43
WOODELF, WOODELF_VAMP, KHAJIIT = 0x013749, 0x088884, 0x013745
UBE_BRETON = 0x005734
WIG = r"follower\wig\braid_1.nif"
BODY_MESH = r"armor\robe\robe_1.nif"
ARMOUR = ("mod.esp", 0x000810)
UBE_ALL = list(up.UBE_RACE_FIDS_24)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, WIGS_OFF, RACE_LIST_OFF, ACC_GUARD_OFF):
        monkeypatch.delenv(k, raising=False)


def _arma(formid, slots, mesh, primary=DEFAULT, extra=()):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary))
    p += encode_subrecord(b"MOD3", encode_zstring(mesh))
    for r in extra:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _world(tmp_path, armas, *, slots=HAIR | CALVES, playable=True, name="Braid"):
    """Skyrim.esm, UBE_AllRace.esp, and Mod.esp: the armatures and one armour
    (Mod.esp|000810) listing them."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    q = encode_subrecord(b"EDID", encode_zstring("FollowerWig"))
    if name is not None:
        q += encode_subrecord(b"FULL", encode_zstring(name))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    q += encode_subrecord(b"DATA", struct.pack("<If", 250, 0.1))
    armo = Record(sig=b"ARMO", flags=0 if playable else 0x4, formid=0x02000810,
                  payload=q)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
                [Group(label=b"ARMA", records=list(armas)),
                 Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _body(tmp_path, world, conv=(), **kw):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, world, converted_rel_paths=set(conv), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True, **kw)
    return st, _minted(out)


def _wig(formid=0x02000800, **kw):
    return _arma(formid, HAIR, WIG, **kw)


# ------------------------------------------------------------------ the rule

def test_a_wig_on_an_armour_with_a_calves_slot_is_drawn_with_its_own_mesh(tmp_path):
    st, minted = _body(tmp_path, _world(tmp_path, [_wig()]))
    assert [m["mod3"] for m in minted] == [WIG], "its own mesh, nothing converted"
    assert minted[0]["races"] == _ube(*UBE_ALL)
    assert [(a, e) for a, e in st["wigs"]] == [(ARMOUR, "FollowerWig")]


def test_a_wood_elf_wig_draws_on_the_ube_wood_elves(tmp_path):
    """The live shape: a Wood-Elf-primary wig listing the Wood Elf vampire."""
    world = _world(tmp_path, [_wig(primary=WOODELF, extra=(WOODELF_VAMP,))])
    st, minted = _body(tmp_path, world)
    want = [f for f in up.UBE_RACE_FIDS_24
            if f in (up.UBE_RACE_FOR_VANILLA_24[WOODELF],
                     up.UBE_RACE_FOR_VANILLA_24[WOODELF_VAMP])]
    assert len(minted) == 1 and minted[0]["mod3"] == WIG
    assert minted[0]["races"] == _ube(*want)
    assert st["wigs"] == [(ARMOUR, "FollowerWig")]


# ------------------------------------------------------------- the switches

def test_switched_off_the_wig_stays_uncovered(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, minted = _body(tmp_path, _world(tmp_path, [_wig()]))
    assert minted == [] and st["wigs"] == []


def test_the_wig_rule_switch_turns_it_off_too(tmp_path, monkeypatch):
    monkeypatch.setenv(WIGS_OFF, "1")
    st, minted = _body(tmp_path, _world(tmp_path, [_wig()]))
    assert minted == [] and st["wigs"] == []


def test_without_the_race_list_rule_a_wood_elf_wig_stays_out(tmp_path, monkeypatch):
    monkeypatch.setenv(RACE_LIST_OFF, "1")
    world = _world(tmp_path, [_wig(primary=WOODELF, extra=(WOODELF_VAMP,))])
    st, minted = _body(tmp_path, world)
    assert minted == [] and st["wigs"] == []


# ------------------------------------------------------ what is not a wig

def test_a_non_playable_hairstyle_is_not_a_wig(tmp_path):
    """An NPC costume's hair: the body pass reads a non-playable armour only
    when it has slot 32 (or hands/feet), so the costume says 31+32 -- and its
    hair is no wig."""
    world = _world(tmp_path, [_wig()], slots=HAIR | BODY, playable=False)
    st, minted = _body(tmp_path, world)
    assert minted == [] and st["wigs"] == []


def test_an_unnamed_hair_armour_is_not_a_wig(tmp_path):
    st, minted = _body(tmp_path, _world(tmp_path, [_wig()], name=None))
    assert minted == [] and st["wigs"] == []


def test_a_hood_is_judged_by_its_own_slots_and_is_not_a_wig(tmp_path):
    """31/41/43 is a hood, not hair only: it waits for a minted body."""
    st, minted = _body(tmp_path, _world(tmp_path, [_arma(0x02000800, HOOD, WIG)]))
    assert minted == [] and st["wigs"] == []


def test_a_race_listed_hood_is_not_a_wig_either(tmp_path):
    world = _world(tmp_path, [_arma(0x02000800, HOOD, WIG, primary=WOODELF,
                                    extra=(WOODELF_VAMP,))])
    st, minted = _body(tmp_path, world)
    assert minted == [] and st["wigs"] == []


def test_a_beast_variant_wig_is_not_taken(tmp_path):
    st, minted = _body(tmp_path, _world(tmp_path, [_wig(extra=(KHAJIIT,))]))
    assert minted == [] and st["wigs"] == []


def test_a_wig_that_already_names_a_ube_race_is_not_minted_again(tmp_path):
    """Someone's UBE wig: no second UBE copy over it."""
    world = _world(tmp_path, [_wig(extra=(0x01000000 | UBE_BRETON,))])
    st, minted = _body(tmp_path, world)
    assert minted == [] and st["wigs"] == []


def test_a_wig_whose_mesh_exists_nowhere_is_not_minted(tmp_path):
    st, minted = _body(tmp_path, _world(tmp_path, [_wig()]),
                       dead_mesh_exists=lambda p: False)
    assert minted == [] and st["wigs"] == []
    assert [a for a, _e in st["dead_dropped"]] == [ARMOUR]


def test_a_wig_pulls_no_other_accessory_along(tmp_path):
    """The circlet of the same armour is no wig, and a wig is no deforming
    armature for #coverage-body-accessory to ride along with."""
    world = _world(tmp_path, [_wig(), _arma(0x02000801, CIRCLET, "circlet_1.nif")],
                   slots=HAIR | CALVES | CIRCLET)
    st, minted = _body(tmp_path, world)
    assert [m["mod3"] for m in minted] == [WIG]
    assert st["body_accessory"] == []


# ------------------------------------------------------------------ control

def test_a_converted_body_keeps_its_hair_armature_as_an_accessory(tmp_path):
    """Control: with a deforming armature minted the old rules already take
    the hair armature, in the same order; the wig rule adds nothing."""
    world = _world(tmp_path, [_arma(0x02000801, BODY, BODY_MESH), _wig()],
                   slots=HAIR | BODY)
    st, minted = _body(tmp_path, world, conv={BODY_MESH.replace("\\", "/")})
    assert [m["mod3"] for m in minted] == ["!UBE\\" + BODY_MESH, WIG]
    assert st["wigs"] == []
    assert st["body_accessory"] == ["mod.esp|800"]


def test_the_hair_only_test_reads_the_armatures_own_slots():
    def pl(slots):
        return encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    assert up._is_hair_only_armature(pl(HAIR))
    assert up._is_hair_only_armature(pl(1 << 1))
    assert not up._is_hair_only_armature(pl(HOOD))
    assert not up._is_hair_only_armature(pl(0)), "no slots is no wig"
    assert not up._is_hair_only_armature(b"")
