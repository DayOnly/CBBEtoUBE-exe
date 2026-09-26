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

r"""#race-subset-dedup-agree -- the per-race split and the merge's
render-identical link dedup agree on which armatures draw the same thing.

THE DEFECT. #coverage-race-subset mints each of an author's per-race
armatures for the UBE counterparts of its own races; the races no sibling
lists go to every sibling. The merge then drops a link when another link on
the same armour has the same meshes, slots and PRIMARY race -- it never read
the rest of the race list. With two same-mesh siblings that gave:
  * different primary races -> both links kept, and the races both carry
    drew the same mesh twice (live: an effect mesh on two armours, twice on
    the six UBE elf races);
  * the same primary race -> one link dropped, and the races only it carried
    drew nothing for the armour (latent: a Nord copy and an Orc copy of one
    circlet).
Measured on the whole chain: the coverage pass, then `merge_patches`, then
what each UBE race draws from the INI. `CBBE2UBE_NO_RACE_SUBSET_DEDUP_AGREE=1`
restores both behaviours.
"""
import json
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import (ESP, Group, Record, TES4Header, encode_subrecord,
                     encode_zstring, iter_subrecords)
from tests.test_coverage_race_subset import (ALL16, ELVES, HANDS, HEAD, HUMANS,
                                             NORD, NORD_V, ORCS, _arma, _armo,
                                             _world, ube)

OFF = "CBBE2UBE_NO_RACE_SUBSET_DEDUP_AGREE"
FX = r"effects\glow_1.nif"
NORDS = [NORD, NORD_V]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_COVERAGE_RACE_SUBSET", raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_COVERAGE_BEAST_VARIANT", raising=False)


def _drawn(merged, lines):
    """{armour: [sorted UBE races (low 24) of each linked armature]} read
    back from the merged ESP through the INI lines, as the game would."""
    e = ESP.load(merged)
    m = e.header.masters
    races = {}
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            got = set()
            for s, d in iter_subrecords(r.payload):
                if s in (b"RNAM", b"MODL") and len(d) >= 4:
                    p, lo = up._record_abs_fid(struct.unpack("<I", d)[0], m, merged.name)
                    if p == "ube_allrace.esp":
                        got.add(lo)
            races[r.formid & 0xFFFFFF] = sorted(got)
    out = {}
    for line in lines:
        head, adds = line.split(":armorAddonsToAdd=")
        out[head[len("filterByArmors="):]] = sorted(
            races[int(a.rsplit("|", 1)[1], 16)] for a in adds.split(","))
    return out


def _per_race(links):
    """{UBE race: how many linked armatures draw it}."""
    n = {}
    for rs in links:
        for r in rs:
            n[r] = n.get(r, 0) + 1
    return n


def _cover_and_merge(tmp_path, armas, armos=None, body=False, slots=HEAD):
    armos = armos or [_armo(0x01000810, slots, armas)]
    world = _world(tmp_path, armas, armos)
    if body:
        out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
        st = up.generate_modded_body_ube_coverage_patch(
            out, world, converted_rel_paths=set(), exclude_names={out.name.lower()},
            master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
            preserve_textures=True, emit_sidecar=True)
    else:
        out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
        st = up.generate_modded_nonbody_ube_coverage_patch(
            out, world, converted_rel_paths=set(), exclude_names={out.name.lower()},
            master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True,
            emit_sidecar=True)
    merged = tmp_path / "Combined.esp"
    ms = up.merge_patches([out], merged, master_data_dirs=[tmp_path])
    return st, ms, _drawn(merged, ms["skypatcher_ini_lines"])


def test_same_mesh_siblings_beside_unlisted_races_draw_once(tmp_path):
    """A human copy and an Orc copy of one effect mesh; no sibling lists the
    elves. The effect is drawn once, by one armature, for every UBE race --
    as before the split."""
    fx = [_arma(0x01000800, HEAD, FX, HUMANS), _arma(0x01000801, HEAD, FX, ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, fx)
    assert drawn == {"Mod.esp|000810": [ALL16]}
    assert st["race_subset_twins"] == ["mod.esp|801"]
    assert st["race_subset"] == [] and st["race_subset_dropped"] == []


def test_switched_off_the_unlisted_races_draw_the_effect_twice(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    fx = [_arma(0x01000800, HEAD, FX, HUMANS), _arma(0x01000801, HEAD, FX, ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, fx)
    per_race = _per_race(drawn["Mod.esp|000810"])
    assert {r for r, n in per_race.items() if n == 2} == set(ube(ELVES))
    assert st["race_subset_twins"] == []


def test_same_mesh_siblings_sharing_a_first_race_lose_no_race(tmp_path):
    """A Nord copy and an Orc copy of one circlet: both would start with the
    same UBE race, and the merge kept one. Every UBE race draws it once."""
    crowns = [_arma(0x01000800, HEAD, r"circlet\one_1.nif", NORDS),
              _arma(0x01000801, HEAD, r"circlet\one_1.nif", ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, crowns)
    assert _per_race(drawn["Mod.esp|000810"]) == {r: 1 for r in ALL16}


def test_switched_off_the_merge_loses_a_race_group(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    crowns = [_arma(0x01000800, HEAD, r"circlet\one_1.nif", NORDS),
              _arma(0x01000801, HEAD, r"circlet\one_1.nif", ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, crowns)
    assert ms["sp_dropped_render_identical"] == 1
    assert set(ALL16) - set(_per_race(drawn["Mod.esp|000810"])) == set(ube(ORCS))


def test_siblings_with_different_meshes_are_still_split(tmp_path):
    crowns = [_arma(0x01000800, HEAD, r"circlet\one_1.nif", NORDS),
              _arma(0x01000801, HEAD, r"circlet\orc_1.nif", ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, crowns)
    free = sorted(set(ALL16) - set(ube(NORDS + ORCS)))
    assert drawn == {"Mod.esp|000810": sorted([sorted(ube(NORDS) + free),
                                               sorted(ube(ORCS) + free)])}
    assert st["race_subset_twins"] == []


def test_a_same_mesh_pair_beside_another_sibling_draws_for_both_lists(tmp_path):
    """Human and Orc copies of one circlet plus an elf circlet: the copies are
    one armature for the humans and Orcs, the elf circlet keeps the elves."""
    armas = [_arma(0x01000800, HEAD, r"circlet\one_1.nif", HUMANS),
             _arma(0x01000801, HEAD, r"circlet\elf_1.nif", ELVES),
             _arma(0x01000802, HEAD, r"circlet\one_1.nif", ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, armas)
    assert drawn == {"Mod.esp|000810": sorted([ube(HUMANS + ORCS), ube(ELVES)])}
    assert st["race_subset_twins"] == ["mod.esp|802"]


def test_gauntlet_copies_with_one_mesh_are_one_armature_in_the_body_pass(tmp_path):
    """Hands keep their source primary race, so every copy would share it and
    the merge would keep one: the Orcs would have lost theirs."""
    gloves = [_arma(0x01000800, HANDS, r"armor\gloves_1.nif", HUMANS + ELVES),
              _arma(0x01000801, HANDS, r"armor\gloves_1.nif", ORCS)]
    st, ms, drawn = _cover_and_merge(tmp_path, gloves, body=True, slots=HANDS)
    assert _per_race(drawn["Mod.esp|000810"]) == {r: 1 for r in ALL16}
    assert st["race_subset_twins"] == ["mod.esp|801"]


def test_a_copy_another_armour_mints_is_not_reported(tmp_path):
    """Left to its twin by one armour, minted alone by another: not "left off"."""
    a = _arma(0x01000800, HEAD, FX, HUMANS)
    b = _arma(0x01000801, HEAD, FX, ORCS)
    armos = [_armo(0x01000810, HEAD, [a, b]), _armo(0x01000811, HEAD, [b])]
    st, ms, drawn = _cover_and_merge(tmp_path, [a, b], armos=armos)
    assert st["race_subset_twins"] == []
    assert drawn["Mod.esp|000810"] == [ALL16]


def _alt(txst):
    name = b"Shape"
    return encode_subrecord(b"MO3S", struct.pack("<I", 1) + struct.pack("<I", len(name))
                            + name + struct.pack("<Ii", txst, 0))


def _textured(formid, races, txst):
    r = _arma(formid, HEAD, FX, races)
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=r.payload + _alt(txst))


def _save(path, masters, groups):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return path


def test_equal_texture_bytes_from_two_plugins_are_not_one_armature(tmp_path):
    """The same texture-set bytes name different sets in two plugins (their
    masters differ): the copies look alike but are coloured differently."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube_ = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    extra = _save(tmp_path / "Extra.esp", ["Skyrim.esm"], [])
    orc = _textured(0x01000801, ORCS, 0x01000900)          # Other.esp's own set
    other = _save(tmp_path / "Other.esp", ["Skyrim.esm"],
                  [Group(label=b"ARMA", records=[orc])])
    human = _textured(0x03000800, HUMANS, 0x01000900)      # Extra.esp's set
    armo = _armo(0x03000810, HEAD, [human, Record(sig=b"ARMA", flags=0,
                                                  formid=0x02000801, payload=b"")])
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm", "Extra.esp", "Other.esp"],
                [Group(label=b"ARMA", records=[human]), Group(label=b"ARMO", records=[armo])])
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, [sky, ube_, extra, other, mod], converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path], cover_all=True,
        preserve_textures=True)
    assert st["race_subset_twins"] == []
    assert st["race_subset_minted"] == {"mod.esp|800", "other.esp|801"}
    assert st["race_subset"] == [(("mod.esp", 0x810), "Piece3000810")]


def test_equal_texture_sets_of_one_plugin_are_one_armature(tmp_path):
    fx = [_textured(0x01000800, HUMANS, 0x00000900), _textured(0x01000801, ORCS, 0x00000900)]
    st, ms, drawn = _cover_and_merge(tmp_path, fx)
    assert st["race_subset_twins"] == ["mod.esp|801"]
    assert drawn == {"Mod.esp|000810": [ALL16]}


# ----- the merge guard, on its own ----------------------------------------

UBE_BYTE = 1          # masters: Skyrim.esm, UBE_AllRace.esp
OWN = 2 << 24


def _minted(local, rnam, modl):
    p = encode_subrecord(b"EDID", encode_zstring(f"UBE_{local:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", HEAD, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", (UBE_BYTE << 24) | rnam))
    p += encode_subrecord(b"MOD3", encode_zstring(r"circlet\one_1.nif"))
    for r in modl:
        p += encode_subrecord(b"MODL", struct.pack("<I", (UBE_BYTE << 24) | r))
    return Record(sig=b"ARMA", flags=0, formid=OWN | local, payload=p)


def _patch_with_links(tmp_path, armas):
    ESP(header=TES4Header(masters=["Skyrim.esm", "UBE_AllRace.esp"], num_records=0,
                          next_object_id=0x900, version=1.7, flags=0),
        groups=[Group(label=b"ARMA", records=armas)]).save(tmp_path / "Patch.esp")
    doc = [{"armo": ["Mod.esp", 0x810],
            "adds": [{"fid": r.formid, "src": ["Mod.esp", 0x800 + i]}
                     for i, r in enumerate(armas)]}]
    (tmp_path / "Patch.esp.skypatcher.json").write_text(json.dumps(doc), encoding="utf-8")
    merged = tmp_path / "Combined.esp"
    ms = up.merge_patches([tmp_path / "Patch.esp"], merged)
    return ms, _drawn(merged, ms["skypatcher_ini_lines"])


B, N, O, E = ube([0x013741])[0], ube([NORD])[0], ube([0x013747])[0], ube([0x013742])[0]


def test_the_merge_keeps_a_copy_that_lists_a_race_the_other_does_not(tmp_path):
    ms, drawn = _patch_with_links(tmp_path, [_minted(0x800, B, [B, N]),
                                             _minted(0x801, B, [B, O])])
    assert sorted({r for rs in drawn["Mod.esp|000810"] for r in rs}) == sorted([B, N, O])
    assert ms["sp_kept_other_races"] == 1
    assert ms["sp_dropped_render_identical"] == 0
    assert ms["sp_links_emitted"] == 2


def test_the_merge_still_drops_a_copy_whose_races_the_other_lists(tmp_path):
    ms, drawn = _patch_with_links(tmp_path, [_minted(0x800, B, [B, N, O]),
                                             _minted(0x801, B, [B, O])])
    assert drawn == {"Mod.esp|000810": [sorted([B, N, O])]}
    assert ms["sp_dropped_render_identical"] == 1
    assert ms["sp_kept_other_races"] == 0


def test_switched_off_the_merge_drops_a_copy_with_other_races(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    ms, drawn = _patch_with_links(tmp_path, [_minted(0x800, B, [B, N]),
                                             _minted(0x801, B, [B, O])])
    assert len(drawn["Mod.esp|000810"]) == 1
    assert ms["sp_dropped_render_identical"] == 1
    assert ms["sp_kept_other_races"] == 0


def test_a_third_copy_covered_by_the_two_kept_is_dropped(tmp_path):
    ms, drawn = _patch_with_links(tmp_path, [_minted(0x800, B, [B, N]),
                                             _minted(0x801, B, [B, O]),
                                             _minted(0x802, B, [N, O])])
    assert len(drawn["Mod.esp|000810"]) == 2
    assert ms["sp_kept_other_races"] == 1
    assert ms["sp_dropped_render_identical"] == 1


# ----- the report ------------------------------------------------------------

def test_the_kept_copies_are_reported_and_the_links_balance():
    out = up.report_link_reconciliation(dict(
        sp_links_seen=3, sp_links_emitted=2, sp_dropped_render_identical=1,
        sp_kept_other_races=1))
    assert any("1 render-identical armature link(s) kept" in l for l in out), out
    assert not any("!!" in l for l in out), out


def test_the_same_mesh_copies_are_reported(capsys):
    ac._report_coverage_holds([
        {"race_subset_twins": ["mod.esp|801", "mod.esp|802"],
         "race_subset_minted": frozenset({"mod.esp|800"})},
        {"race_subset_twins": [], "race_subset_minted": frozenset({"mod.esp|802"})}])
    out = capsys.readouterr().out
    assert "1 per-race armature(s) with the same mesh as a sibling" in out
