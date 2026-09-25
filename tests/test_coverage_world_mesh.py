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

"""#coverage-world-mesh -- a body armature the winner scan mints must draw a
converted female WORLD mesh, not only a converted first-person one.

THE DEFECT. The body pass admitted a DefaultRace armature when ANY of
MOD2..MOD5 was converted. A converted first-person torso was enough, and the
minted armature then drew its unconverted CBBE world mesh on the UBE body.
Measured live: 87 armatures on 89 links, each with only a first-person mesh
converted; 62 of the links let in by one shared vanilla first-person torso.

THE RULE (user, 09-24). A slot-32 body armature (its own BOD2, else the
armour's) is minted when MOD3 was converted, or MOD3 is absent/empty/dead and
MOD2 was converted. Hands/feet and slot 34/38 are not affected. Children's
clothing is dropped with the rest -- no special case.
"""
import struct
from pathlib import Path

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

DEFAULT = 0x00000019
BODY = 1 << 2                # slot 32
HANDS = 1 << 3               # slot 33
FOREARMS = 1 << 4            # slot 34
FEET = 1 << 7                # slot 37
OFF = "CBBE2UBE_NO_COVERAGE_WORLD_MESH"

M_W = r"armor\m\cuirass_1.nif"          # male world
F_W = r"armor\f\cuirass_1.nif"          # female world
M_1 = r"armor\m\1stpersoncuirass_1.nif"  # male first person
F_1 = r"clothes\f\1stpersontorso_1.nif"  # the shared first-person torso


def _save(path, masters, groups):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _arma_payload(models, slots):
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return p


def _armo(fid, slots, armas, edid="Piece"):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        p += encode_subrecord(b"MODL", struct.pack("<I", a))
    return Record(sig=b"ARMO", flags=0, formid=fid, payload=p)


def _world(tmp_path, armas, armos):
    """armas: {fid: (models, slots)}; armos: [(fid, slots, [arma fids])]."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    ar = [Record(sig=b"ARMA", flags=0, formid=f, payload=_arma_payload(m, s))
          for f, (m, s) in armas.items()]
    ao = [_armo(f, s, a) for f, s, a in armos]
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=ar), Group(label=b"ARMO", records=ao)])
    return [sky, ube, mod]


def _body_pass(tmp_path, armas, armos, conv, **kw):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, armas, armos), converted_rel_paths=conv,
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True, **kw)
    return st, out


def _one_torso(tmp_path, models, conv, **kw):
    return _body_pass(tmp_path, {0x01000800: (models, BODY)},
                      [(0x01000801, BODY, [0x01000800])], conv, **kw)


def _minted(out):
    e = ESP.load(out)
    return [{s: d.rstrip(b"\x00").decode() for s, d in iter_subrecords(r.payload)
             if s in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")}
            for g in e.groups if g.label == b"ARMA" for r in g.records]


def _conv(*paths):
    return {p.replace("\\", "/").lower() for p in paths}


FULL = {b"MOD2": M_W, b"MOD3": F_W, b"MOD4": M_1, b"MOD5": F_1}


# ------------------------------------------------------------ the defect

def test_a_torso_with_only_its_first_person_mesh_converted_is_not_minted(tmp_path):
    """The live class: a converted first-person torso admitted the armature, and
    the minted armature drew the unconverted CBBE world mesh."""
    st, _out = _one_torso(tmp_path, FULL, _conv(F_1))
    assert st["armo_targets"] == 0
    assert st["world_mesh_skipped"] == ["mod.esp|800"]
    assert [a for a, _e in st["world_mesh_dropped"]] == [("mod.esp", 0x801)]


def test_the_off_switch_mints_it_again_with_the_cbbe_world_mesh(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, out = _one_torso(tmp_path, FULL, _conv(F_1))
    assert st["armo_targets"] == 1 and st["world_mesh_skipped"] == []
    m = _minted(out)[0]
    assert m[b"MOD3"] == F_W, "today's behaviour: the unconverted world mesh"
    assert m[b"MOD5"] == "!UBE\\" + F_1


def test_a_torso_with_its_world_mesh_converted_is_minted(tmp_path):
    """Negative control: the rule bites only on an unconverted world mesh."""
    st, out = _one_torso(tmp_path, FULL, _conv(F_W, F_1))
    assert st["armo_targets"] == 1 and st["world_mesh_skipped"] == []
    assert _minted(out)[0][b"MOD3"] == "!UBE\\" + F_W


def test_a_converted_male_mesh_alone_does_not_admit_a_named_female_mesh(tmp_path, monkeypatch):
    """With the female guard off the rule still holds: the female world mesh
    exists and was not converted, so the armature is not minted."""
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "1")
    st, _out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD3": F_W}, _conv(M_W))
    assert st["armo_targets"] == 0 and st["world_mesh_skipped"] == ["mod.esp|800"]


def test_the_guard_keeps_its_own_count(tmp_path):
    """The female guard runs first and already refuses the male-only torso; the
    world-mesh rule must not re-count it."""
    st, _out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD3": F_W}, _conv(M_W))
    assert st["female_guard_skipped"] == ["mod.esp|800"]
    assert st["world_mesh_skipped"] == []


# ---------------------------------------------------- where the male is right

def test_an_absent_female_world_mesh_takes_the_converted_male(tmp_path):
    st, out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD4": M_1}, _conv(M_W))
    assert st["armo_targets"] == 1
    assert _minted(out)[0][b"MOD3"] == "!UBE\\" + M_W


def test_an_empty_female_world_mesh_takes_the_converted_male(tmp_path):
    st, out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD3": ""}, _conv(M_W))
    assert st["armo_targets"] == 1
    assert _minted(out)[0][b"MOD3"] == "!UBE\\" + M_W


def test_a_dead_female_world_mesh_takes_the_converted_male(tmp_path, monkeypatch):
    """Dead = exists nowhere: the female-only selection's rule. Asked of the
    lookup even with the female guard off."""
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "1")
    asked = []
    st, out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD3": F_W}, _conv(M_W),
                         mesh_exists=lambda p: asked.append(p) or False)
    assert st["armo_targets"] == 1 and asked == [F_W]
    assert _minted(out)[0][b"MOD3"] == "!UBE\\" + M_W


def test_no_lookup_means_a_named_path_is_taken_to_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "1")
    st, _out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD3": F_W}, _conv(M_W))
    assert st["armo_targets"] == 0


def test_a_dead_female_path_without_the_male_converted_is_not_minted(tmp_path):
    st, _out = _one_torso(tmp_path, FULL, _conv(F_1), mesh_exists=lambda p: False)
    assert st["armo_targets"] == 0


def test_an_absent_female_with_an_unconverted_male_is_not_minted(tmp_path):
    st, _out = _one_torso(tmp_path, {b"MOD2": M_W, b"MOD4": M_1}, _conv(M_1))
    assert st["armo_targets"] == 0 and st["world_mesh_skipped"] == ["mod.esp|800"]


# ------------------------------------------------------------ what is exempt

def test_the_hands_armature_of_a_body_armour_is_judged_as_hands(tmp_path):
    """Per ARMATURE: a cuirass-and-gauntlets armour's gauntlets keep their
    source world mesh, as every hand/foot armature does."""
    gl = {b"MOD2": r"armor\m\gauntlets_1.nif", b"MOD3": r"armor\f\gauntlets_1.nif",
          b"MOD5": r"armor\f\1stgauntlets_1.nif"}
    st, out = _body_pass(
        tmp_path, {0x01000800: (FULL, BODY), 0x01000802: (gl, HANDS)},
        [(0x01000801, BODY | HANDS, [0x01000800, 0x01000802])],
        _conv(F_1, r"armor\f\1stgauntlets_1.nif"))
    assert st["world_mesh_skipped"] == ["mod.esp|800"]
    assert st["armo_targets"] == 1 and st["world_mesh_dropped"] == []
    assert [m[b"MOD3"] for m in _minted(out)] == [r"armor\f\gauntlets_1.nif"]


def test_pure_feet_are_not_affected(tmp_path):
    boots = {b"MOD2": r"armor\m\boots_1.nif", b"MOD3": r"armor\f\boots_1.nif"}
    st, out = _body_pass(tmp_path, {0x01000800: (boots, FEET)},
                         [(0x01000801, FEET, [0x01000800])], set())
    assert st["armo_targets"] == 1 and st["world_mesh_skipped"] == []


def test_forearms_are_not_affected(tmp_path):
    bracer = {b"MOD3": r"armor\f\bracer_1.nif", b"MOD5": r"armor\f\1stbracer_1.nif"}
    st, _out = _body_pass(tmp_path, {0x01000800: (bracer, FOREARMS)},
                          [(0x01000801, FOREARMS, [0x01000800])],
                          _conv(r"armor\f\1stbracer_1.nif"))
    assert st["armo_targets"] == 1 and st["world_mesh_skipped"] == []


def test_an_armature_without_its_own_slots_takes_the_armours(tmp_path):
    """No BOD2 on the armature: the armour's slot 32 decides (the guard's test)."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    p = b"".join(d for d in [encode_subrecord(b"EDID", encode_zstring("AA")),
                             encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))])
    for sig, path in FULL.items():
        p += encode_subrecord(sig, encode_zstring(path))
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=p)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]),
                 Group(label=b"ARMO", records=[_armo(0x01000801, BODY, [0x01000800])])])
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod], converted_rel_paths=_conv(F_1),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True)
    assert st["world_mesh_skipped"] == ["mod.esp|800"]


def test_a_shared_armature_is_counted_once(tmp_path):
    st, _out = _body_pass(tmp_path, {0x01000800: (FULL, BODY)},
                          [(0x01000801, BODY, [0x01000800]),
                           (0x01000802, BODY, [0x01000800])], _conv(F_1))
    assert st["world_mesh_skipped"] == ["mod.esp|800"]
    assert len(st["world_mesh_dropped"]) == 2


# ------------------------------------------- a partial drop is named, too
# #world-mesh-partial-report (review 09-24): the torso armature withheld, the
# gauntlets one minted. The armour stays a target, so it was never in
# world_mesh_dropped -- yet nothing draws its slot 32 on UBE, as with a full drop.

G_1 = r"armor\f\1stgauntlets_1.nif"   # converted: admits the gauntlets armature
GLOVES = {b"MOD2": r"armor\m\gauntlets_1.nif", b"MOD3": r"armor\f\gauntlets_1.nif",
          b"MOD5": G_1}


def test_a_body_armour_left_with_only_its_gauntlets_is_recorded(tmp_path):
    st, out = _body_pass(
        tmp_path, {0x01000800: (FULL, BODY), 0x01000802: (GLOVES, HANDS)},
        [(0x01000801, BODY | HANDS, [0x01000800, 0x01000802])], _conv(F_1, G_1))
    assert st["armo_targets"] == 1 and st["world_mesh_dropped"] == []
    assert st["world_mesh_partial"] == [(("mod.esp", 0x801), "Piece")]
    assert [m[b"MOD3"] for m in _minted(out)] == [GLOVES[b"MOD3"]]


def test_a_second_converted_torso_armature_is_not_a_partial_drop(tmp_path):
    """Control: one torso armature withheld, another one minted -- slot 32 is
    still drawn, so the armour is not named."""
    other = {b"MOD3": r"armor\f\cuirass_2.nif"}
    st, _out = _body_pass(
        tmp_path, {0x01000800: (FULL, BODY), 0x01000803: (other, BODY)},
        [(0x01000801, BODY, [0x01000800, 0x01000803])],
        _conv(F_1, r"armor\f\cuirass_2.nif"))
    assert st["world_mesh_skipped"] == ["mod.esp|800"]
    assert st["armo_targets"] == 1 and st["world_mesh_partial"] == []


def test_a_full_drop_is_not_also_a_partial_one(tmp_path):
    st, _out = _one_torso(tmp_path, FULL, _conv(F_1))
    assert len(st["world_mesh_dropped"]) == 1 and st["world_mesh_partial"] == []


def test_an_armour_a_later_rule_empties_is_not_named_as_drawn(tmp_path):
    """Named only while it stays a target: nude hands that do not resolve empty
    the armour, which the nude-skin line reports instead."""
    nude = {b"MOD3": r"actors\character\character assets\femalehands_1.nif",
            b"MOD5": G_1}
    st, _out = _body_pass(
        tmp_path, {0x01000800: (FULL, BODY), 0x01000802: (nude, HANDS)},
        [(0x01000801, BODY | HANDS, [0x01000800, 0x01000802])], _conv(F_1, G_1),
        mesh_exists=lambda p: False)
    assert st["armo_targets"] == 0 and st["world_mesh_partial"] == []
    assert [d[:2] for d in st["nude_dropped"]] == [(("mod.esp", 0x801), "Piece")]


def test_the_partial_drops_are_counted_and_named(capsys):
    ac._report_coverage_holds([{
        "world_mesh_skipped": ["mod.esp|800", "mod.esp|803"],
        "world_mesh_dropped": [(("mod.esp", 0x801), "Tunic")],
        "world_mesh_partial": [(("mod.esp", 0x804), "CuirassAndGloves")]}])
    text = capsys.readouterr().out
    assert "(1 armour(s) left without one, 1 drawn without the body piece)" in text
    assert "not covered: Tunic  (mod.esp|000801)" in text
    assert "no body piece: CuirassAndGloves  (mod.esp|000804)" in text


def test_the_partial_names_are_capped_at_five(capsys):
    ac._report_coverage_holds([{
        "world_mesh_skipped": ["mod.esp|800"],
        "world_mesh_partial": [(("mod.esp", 0x900 + i), f"Set{i}") for i in range(7)]}])
    text = capsys.readouterr().out
    assert text.count("no body piece:") == 5
    assert "... and 2 more" in text


def test_adult_outfits_are_named_before_childrens_clothing(capsys):
    """Live, the first five names were all children's clothing (skipped on
    purpose) and the adult outfits sat in '... and 75 more'. Child = the name
    test source selection uses (`_is_child_content_asset`); order is otherwise
    kept."""
    kids = [(("skyrim.esm", 0x100 + i), f"Cloth_Child_Body_{i}") for i in range(5)]
    adults = [(("mod.esp", 0x801), "Tunic"), (("mod.esp", 0x802), "Robe")]
    ac._report_coverage_holds([{
        "world_mesh_skipped": ["mod.esp|800"],
        "world_mesh_dropped": kids + adults,
        "world_mesh_partial": [(("mod.esp", 0x805), "ChildrenVest"),
                               (("mod.esp", 0x806), "Cuirass")]}])
    lines = [ln.strip() for ln in capsys.readouterr().out.splitlines()]
    named = [ln for ln in lines if ln.startswith("not covered:")]
    assert named[:2] == ["not covered: Tunic  (mod.esp|000801)",
                         "not covered: Robe  (mod.esp|000802)"]
    assert named[2] == "not covered: Cloth_Child_Body_0  (skyrim.esm|000100)"
    assert "... and 2 more" in lines
    part = [ln for ln in lines if ln.startswith("no body piece:")]
    assert part == ["no body piece: Cuirass  (mod.esp|000806)",
                    "no body piece: ChildrenVest  (mod.esp|000805)"]


# ---------------------------------------------------------- report + wiring

def test_the_drops_are_reported(capsys):
    ac._report_coverage_holds([{
        "world_mesh_skipped": ["mod.esp|800", "mod.esp|803"],
        "world_mesh_dropped": [(("mod.esp", 0x801), "Tunic")]}])
    text = capsys.readouterr().out
    assert "2 body armature(s) were not minted because their female world mesh" in text
    assert "(1 armour(s) left without one, 0 drawn without the body piece)" in text
    assert "not covered: Tunic  (mod.esp|000801)" in text
    assert "no body piece:" not in text


def test_nothing_dropped_prints_nothing(capsys):
    ac._report_coverage_holds([{"world_mesh_skipped": [], "world_mesh_dropped": []}])
    assert capsys.readouterr().out == ""


def _emit(tmp_path, monkeypatch):
    """Run _emit_unified_coverage_patches on a fake modlist and capture what the
    body pass is handed."""
    (tmp_path / "mods" / "A Mod").mkdir(parents=True)
    fol = _save(tmp_path / "mods" / "A Mod" / "A.esp", ["Skyrim.esm"], [])
    out = tmp_path / "out"
    (out / "meshes" / "!UBE").mkdir(parents=True)
    (out / "meshes" / "!UBE" / "x_1.nif").write_bytes(b"x")
    patches = out / "_unmerged_patches"
    patches.mkdir()
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: tmp_path / "mods")
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered", lambda lay: ["A.esp"])
    monkeypatch.setattr(ac.paths, "plugin_file_index", lambda lay: {"a.esp": str(fol)})
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda output: _LOOKUP)
    seen = {}

    def run(*a, **k):
        seen.update(k)
        return {"armo_targets": 1, "minted_armas": 1}
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_nonbody_ube_coverage_patch",
                        lambda *a, **k: {"armo_targets": 1, "minted_armas": 1})
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_body_ube_coverage_patch", run)
    ok, _t, body = ac._emit_unified_coverage_patches(
        out, patches, [], "CBBE_to_UBE_Combined.esp")
    assert ok and body
    return seen


def _LOOKUP(model):
    return True


def test_the_body_pass_gets_the_lookup_with_the_guard_off(tmp_path, monkeypatch):
    """Dead-path detection must not depend on another rule's switch."""
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "1")
    seen = _emit(tmp_path, monkeypatch)
    assert seen["female_mesh_exists"] is None
    assert seen["mesh_exists"] is _LOOKUP


def test_no_lookup_when_nothing_asks_for_it(tmp_path, monkeypatch):
    for v in ("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", OFF,
              "CBBE2UBE_NO_COVERAGE_NUDE_SKIN"):
        monkeypatch.setenv(v, "1")
    seen = _emit(tmp_path, monkeypatch)
    assert seen["mesh_exists"] is None and seen["female_mesh_exists"] is None
