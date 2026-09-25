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

r"""#coverage-race-subset -- an author's per-race armatures of one piece are
each minted for the UBE counterparts of their own races.

THE DEFECT. Both coverage passes minted every DefaultRace-primary armature of
an armour for all 16 UBE races. An author who splits a piece by race -- one
armature listing the human races, one Orc only, one the three elves, or one only
a race of their own -- gets one of them per vanilla race (an actor matches an
armature only by a race it lists), but a UBE actor drew every one at once: two
identical circlets plus the elf circlet, one helmet twice, a second robe made
for a mod's own race. Live: 7 armours, 1 link removed, 15 links narrowed; every
UBE race now draws one armature of each. `CBBE2UBE_NO_COVERAGE_RACE_SUBSET=1`
mints them for every UBE race again.
"""
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import ESP, Group, Record, encode_subrecord, encode_zstring, iter_subrecords
from tests.test_coverage_ube_twin import DEFAULT, _save

OFF = "CBBE2UBE_NO_COVERAGE_RACE_SUBSET"
BEAST_OFF = "CBBE2UBE_NO_COVERAGE_BEAST_VARIANT"
HEAD = 1 << 12          # circlet slot
AMULET = 1 << 5
HANDS = 1 << 3

# Skyrim.esm races (low 24) and the UBE counterparts the minted copy targets.
BRETON, IMPERIAL, NORD, REDGUARD = 0x013741, 0x013744, 0x013746, 0x013748
DARK, HIGH, WOOD, ORC = 0x013742, 0x013743, 0x013749, 0x013747
BRETON_V, IMPERIAL_V, NORD_V, REDGUARD_V = 0x08883C, 0x088844, 0x088794, 0x088846
DARK_V, HIGH_V, WOOD_V, ORC_V = 0x08883D, 0x088840, 0x088884, 0x0A82B9
KHAJIIT = 0x013745
HUMANS = [BRETON, IMPERIAL, NORD, REDGUARD, BRETON_V, IMPERIAL_V, NORD_V, REDGUARD_V]
ELVES = [DARK, HIGH, WOOD, DARK_V, HIGH_V, WOOD_V]
ORCS = [ORC, ORC_V]
MOD_RACE = 0x01000900   # Mod.esp's own race

ALL16 = sorted(up.UBE_RACE_FIDS_24)


def ube(races):
    return sorted(up.UBE_RACE_FOR_VANILLA_24[r] for r in races)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(BEAST_OFF, raising=False)


def _arma(formid, slots, mod3, races):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    for r in races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots, armas):
    q = encode_subrecord(b"EDID", encode_zstring(f"Piece{formid:X}"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=q)


def _world(tmp_path, armas, armos):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube_ = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=armas), Group(label=b"ARMO", records=armos)])
    return [sky, ube_, mod]


def _minted(out):
    """{MOD3: sorted UBE races (low 24)} of every minted armature."""
    e = ESP.load(out)
    ub = [m.lower() for m in e.header.masters].index("ube_allrace.esp")
    got = {}
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            sub = list(iter_subrecords(r.payload))
            mod3 = next(d.rstrip(b"\x00").decode() for s, d in sub if s == b"MOD3")
            got[mod3] = sorted(struct.unpack("<I", d)[0] & 0xFFFFFF for s, d in sub
                               if s == b"MODL" and struct.unpack("<I", d)[0] >> 24 == ub)
    return got


def _nonbody(tmp_path, armas, slots=HEAD, armos=None):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    armos = armos or [_armo(0x01000810, slots, armas)]
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, armas, armos), converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True)
    return st, _minted(out)


CROWN = lambda: _arma(0x01000800, HEAD, r"circlet\human_1.nif", HUMANS)
CROWN_ORC = lambda: _arma(0x01000801, HEAD, r"circlet\orc_1.nif", ORCS)
CROWN_ELF = lambda: _arma(0x01000802, HEAD, r"circlet\elf_1.nif", ELVES)


def test_disjoint_race_siblings_each_draw_for_their_own_races(tmp_path):
    st, got = _nonbody(tmp_path, [CROWN(), CROWN_ORC(), CROWN_ELF()])
    assert got == {r"circlet\human_1.nif": ube(HUMANS),
                   r"circlet\orc_1.nif": ube(ORCS),
                   r"circlet\elf_1.nif": ube(ELVES)}
    assert st["race_subset"] == [(("mod.esp", 0x810), "Piece1000810")]
    assert st["race_subset_dropped"] == []


def test_switched_off_every_sibling_draws_for_every_race(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, got = _nonbody(tmp_path, [CROWN(), CROWN_ORC(), CROWN_ELF()])
    assert got == {r"circlet\human_1.nif": ALL16, r"circlet\orc_1.nif": ALL16,
                   r"circlet\elf_1.nif": ALL16}
    assert st["race_subset"] == [] and st["race_subset_dropped"] == []


def test_a_per_race_override_beside_the_rest_of_the_races(tmp_path):
    """A 14-race armature and an Orc-only one: the Orc one is the override."""
    rest = _arma(0x01000800, HEAD, r"helmet\helmet_1.nif", HUMANS + ELVES)
    st, got = _nonbody(tmp_path, [rest, CROWN_ORC()])
    assert got == {r"helmet\helmet_1.nif": ube(HUMANS + ELVES),
                   r"circlet\orc_1.nif": ube(ORCS)}


def test_an_armature_for_a_mods_own_race_is_left_off(tmp_path):
    full = _arma(0x01000800, HEAD, r"robe\robe_1.nif", HUMANS + ELVES + ORCS)
    own = _arma(0x01000801, HEAD, r"robe\robebone_1.nif", [MOD_RACE])
    st, got = _nonbody(tmp_path, [full, own])
    assert got == {r"robe\robe_1.nif": ALL16}
    assert st["race_subset_dropped"] == ["mod.esp|801"]


def test_overlapping_lists_are_drawn_together_as_before(tmp_path):
    """Two armatures that both list a race are both drawn on it in the base
    game (a layered piece); an omission in one list does not split them."""
    long_ = _arma(0x01000800, HEAD, r"robe\long_1.nif", HUMANS + ELVES + ORCS)
    short = _arma(0x01000801, HEAD, r"robe\short_1.nif", HUMANS + ELVES + [ORC])
    st, got = _nonbody(tmp_path, [long_, short])
    assert got == {r"robe\long_1.nif": ALL16, r"robe\short_1.nif": ALL16}
    assert st["race_subset"] == []


def test_a_lone_subset_armature_keeps_every_race(tmp_path):
    st, got = _nonbody(tmp_path, [CROWN_ORC()])
    assert got == {r"circlet\orc_1.nif": ALL16}
    assert st["race_subset"] == []


def test_siblings_on_different_slots_are_not_split(tmp_path):
    ring = _arma(0x01000801, AMULET, r"amulet\orc_1.nif", ORCS)
    st, got = _nonbody(tmp_path, [CROWN(), ring], slots=HEAD | AMULET)
    assert got == {r"circlet\human_1.nif": ALL16, r"amulet\orc_1.nif": ALL16}


def test_the_default_armature_takes_the_races_no_sibling_lists(tmp_path):
    plain = _arma(0x01000800, HEAD, r"helmet\plain_1.nif", [])
    st, got = _nonbody(tmp_path, [plain, CROWN_ORC()])
    assert got == {r"helmet\plain_1.nif": ube(HUMANS + ELVES),
                   r"circlet\orc_1.nif": ube(ORCS)}


def test_a_default_armature_beside_a_full_list_is_unchanged(tmp_path):
    """A full list beside an armature listing no race: nothing is split, and
    the default armature is not left with no race."""
    full = _arma(0x01000800, HEAD, r"robe\robe_1.nif", HUMANS + ELVES + ORCS)
    plain = _arma(0x01000801, HEAD, r"robe\belt_1.nif", [])
    st, got = _nonbody(tmp_path, [full, plain])
    assert got == {r"robe\robe_1.nif": ALL16, r"robe\belt_1.nif": ALL16}
    assert st["race_subset"] == [] and st["race_subset_dropped"] == []


def test_a_default_armature_with_every_race_claimed_is_left_off(tmp_path):
    plain = _arma(0x01000803, HEAD, r"helmet\plain_1.nif", [])
    st, got = _nonbody(tmp_path, [CROWN(), CROWN_ORC(), CROWN_ELF(), plain])
    assert got == {r"circlet\human_1.nif": ube(HUMANS),
                   r"circlet\orc_1.nif": ube(ORCS),
                   r"circlet\elf_1.nif": ube(ELVES)}
    assert st["race_subset_dropped"] == ["mod.esp|803"]


def test_races_no_sibling_lists_stay_on_every_sibling(tmp_path):
    """Humans and Orcs listed, elves by nobody and no default armature: the
    elves still draw both, as today -- never nothing."""
    st, got = _nonbody(tmp_path, [CROWN(), CROWN_ORC()])
    assert got == {r"circlet\human_1.nif": ube(HUMANS + ELVES),
                   r"circlet\orc_1.nif": ube(ORCS + ELVES)}


def test_an_armature_another_armour_mints_alone_keeps_every_race(tmp_path):
    """A minted armature is one record shared by every armour that lists it:
    split by one armour, drawn alone by another, it targets every race."""
    armas = [CROWN(), CROWN_ORC(), CROWN_ELF()]
    armos = [_armo(0x01000810, HEAD, armas), _armo(0x01000811, HEAD, [armas[1]])]
    st, got = _nonbody(tmp_path, armas, armos=armos)
    assert got == {r"circlet\human_1.nif": ube(HUMANS),
                   r"circlet\orc_1.nif": ALL16,
                   r"circlet\elf_1.nif": ube(ELVES)}


def test_a_beast_variant_is_left_to_its_own_rule(tmp_path, monkeypatch):
    """With #coverage-beast-variant off the beast-only armature is minted as
    that switch says -- for every race -- and does not split its sibling."""
    monkeypatch.setenv(BEAST_OFF, "1")
    cat = _arma(0x01000801, HEAD, r"circlet\cat_1.nif", [KHAJIIT])
    st, got = _nonbody(tmp_path, [CROWN(), cat])
    assert got == {r"circlet\human_1.nif": ALL16, r"circlet\cat_1.nif": ALL16}
    assert st["race_subset"] == []


def test_gauntlet_siblings_are_split_in_the_body_pass(tmp_path):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    armas = [_arma(0x01000800, HANDS, r"armor\gloves_1.nif", HUMANS + ELVES),
             _arma(0x01000801, HANDS, r"armor\glovesorc_1.nif", ORCS)]
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, armas, [_armo(0x01000810, HANDS, armas)]),
        converted_rel_paths=set(), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True)
    assert _minted(out) == {r"armor\gloves_1.nif": ube(HUMANS + ELVES),
                            r"armor\glovesorc_1.nif": ube(ORCS)}
    assert st["race_subset"] == [(("mod.esp", 0x810), "Piece1000810")]


def test_the_split_is_reported(capsys):
    ac._report_coverage_holds([
        {"race_subset": [(("mod.esp", 0x810), "Crown")],
         "race_subset_dropped": ["mod.esp|801"]},
        {"race_subset": [(("mod.esp", 0x810), "Crown")],
         "race_subset_dropped": ["mod.esp|801"]}])
    out = capsys.readouterr().out
    assert "1 armour(s) with a separate armature per race" in out
    assert "1 armature(s) made only for other races" in out


def test_no_dropped_line_without_a_dropped_armature(capsys):
    ac._report_coverage_holds([{"race_subset": [(("mod.esp", 0x810), "Crown")],
                                "race_subset_dropped": []}])
    out = capsys.readouterr().out
    assert "1 armour(s) with a separate armature per race" in out
    assert "made only for other races" not in out


def test_nothing_is_reported_without_a_split(capsys):
    ac._report_coverage_holds([{"race_subset": [], "race_subset_dropped": []}])
    assert "separate armature per race" not in capsys.readouterr().out
