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

r"""#coverage-dead-armature -- an armature none of whose meshes exists
anywhere is not minted.

THE DEFECT. Both coverage passes minted every armature the other rules
admitted. A hands/feet armature is admitted by its slot alone and a non-body
one keeps its source mesh, so an armature whose every named mesh is missing
from the whole modlist was minted too: a link that draws nothing, for anyone
(the source armature draws nothing either). Live: 41 source armatures, 48
links, 47 armours.

THE RULE. Judged LAST in both passes, after every other rule: an armature that
names at least one non-empty MOD2..MOD5 path and none alive is not minted. A
path is alive when this run converted it, a third-party mod ships its `!UBE\`
twin, or it or a weight sibling (`_0`, `_1`, none) exists where the game reads
meshes. An armature naming no mesh (a slot placeholder) is never dead. No
lookup (the modlist cannot be read) = mint as before, with a warning.
`CBBE2UBE_NO_COVERAGE_DEAD_ARMATURE=1` mints them again.
"""
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_COVERAGE_DEAD_ARMATURE"
HEAD = 1 << 0
BODY = 1 << 2
HANDS = 1 << 3
HOOD = (1 << 1) | (1 << 11)          # slots 31, 41
ARGONIAN, NORD = 0x013740, 0x013746

HELM_M = r"gear\helm\helm_m_1.nif"
HELM_F = r"gear\helm\helm_f_1.nif"
GLOVE_M = r"gear\glove\glove_m_1.nif"
GLOVE_F = r"gear\glove\glove_f_1.nif"
ROBE = r"gear\robe\robe_1.nif"
HOODM = r"gear\robe\hood_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _key(p):
    p = p.replace("\\", "/").lstrip("/").lower()
    return p[7:] if p.startswith("meshes/") else p


def _have(*paths):
    """A `_mesh_exists_anywhere` stand-in: these paths exist, nothing else.
    It records every question in `.asked`."""
    have = {_key(p) for p in paths}
    asked = []

    def exists(p):
        asked.append(p)
        return _key(p) in have
    exists.asked = asked
    return exists


def _arma(formid, slots, models, race=DEFAULT, races=(DEFAULT,)):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", race))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
    for r in races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots, armas, edid="Piece"):
    q = encode_subrecord(b"EDID", encode_zstring(edid))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=q)


def _world(tmp_path, armas, armos):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=armas),
                 Group(label=b"ARMO", records=armos)])
    return [sky, ube, mod]


def _nonbody(tmp_path, armas, armos=None, *, lookup, conv=(), twin=None):
    armos = armos or [_armo(0x01000810, HEAD, armas)]
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, armas, armos),
        converted_rel_paths={_key(c) for c in conv},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True, ube_twin_exists=twin,
        dead_mesh_exists=lookup)
    return st, _minted(out), out


def _body(tmp_path, armas, armos, *, lookup, conv=()):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, armas, armos),
        converted_rel_paths={_key(c) for c in conv},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True,
        dead_mesh_exists=lookup)
    return st, _minted(out)


def _helm(models, formid=0x01000800):
    return _arma(formid, HEAD, models)


# ----------------------------------------------------------- (a) all dead

def test_a_helmet_whose_meshes_exist_nowhere_is_not_minted(tmp_path):
    st, minted, _ = _nonbody(tmp_path, [_helm({b"MOD2": HELM_M, b"MOD3": HELM_F})],
                             lookup=_have())
    assert minted == []
    assert st["dead_armature_skipped"] == ["mod.esp|800"]
    assert st["dead_dropped"] == [(("mod.esp", 0x810), "Piece")]
    assert st["armo_targets"] == 0


def test_gauntlets_whose_meshes_exist_nowhere_are_not_minted_in_the_body_pass(tmp_path):
    """The body pass admits a hands/feet armature by its slot alone -- the
    population the live replay found most of (city-guard boots and gauntlets)."""
    glove = _arma(0x01000800, HANDS, {b"MOD2": GLOVE_M, b"MOD3": GLOVE_F})
    st, minted = _body(tmp_path, [glove], [_armo(0x01000810, HANDS, [glove])],
                       lookup=_have())
    assert minted == []
    assert st["dead_armature_skipped"] == ["mod.esp|800"]
    assert st["dead_dropped"] == [(("mod.esp", 0x810), "Piece")]


def test_the_same_gauntlets_are_minted_when_their_mesh_exists(tmp_path):
    """Control for the one above: the hands/feet admission is untouched."""
    glove = _arma(0x01000800, HANDS, {b"MOD2": GLOVE_M, b"MOD3": GLOVE_F})
    st, minted = _body(tmp_path, [glove], [_armo(0x01000810, HANDS, [glove])],
                       lookup=_have(GLOVE_F))
    assert [m[b"MOD3"] for m in minted] == [GLOVE_F]
    assert st["dead_armature_skipped"] == [] and st["dead_dropped"] == []


# ------------------------------------------------- (b)/(c) partly dead: kept

def test_only_the_first_person_meshes_dead_is_minted(tmp_path):
    models = {b"MOD2": HELM_M, b"MOD3": HELM_F,
              b"MOD4": r"gear\helm\1st_m_1.nif", b"MOD5": r"gear\helm\1st_f_1.nif"}
    st, minted, _ = _nonbody(tmp_path, [_helm(models)],
                             lookup=_have(HELM_M, HELM_F))
    assert [m[b"MOD3"] for m in minted] == [HELM_F]
    assert st["dead_armature_skipped"] == []


def test_a_dead_female_mesh_with_a_live_male_one_is_minted(tmp_path):
    st, minted, _ = _nonbody(tmp_path, [_helm({b"MOD2": HELM_M, b"MOD3": HELM_F})],
                             lookup=_have(HELM_M))
    assert len(minted) == 1 and st["dead_armature_skipped"] == []


def test_one_dead_armature_of_two_leaves_the_armour_its_live_one(tmp_path):
    live = _helm({b"MOD3": HELM_F}, formid=0x01000800)
    dead = _helm({b"MOD3": r"gear\helm\gone_1.nif"}, formid=0x01000801)
    st, minted, _ = _nonbody(tmp_path, [live, dead], lookup=_have(HELM_F))
    assert [m[b"MOD3"] for m in minted] == [HELM_F]
    assert st["dead_armature_skipped"] == ["mod.esp|801"]
    assert st["dead_dropped"] == [] and st["armo_targets"] == 1


# ------------------------------------------------------ (d) a weight sibling

def test_a_dead_path_with_a_live_weight_sibling_is_minted(tmp_path):
    st, minted, _ = _nonbody(tmp_path, [_helm({b"MOD3": HELM_F})],
                             lookup=_have(HELM_F.replace("_1.nif", "_0.nif")))
    assert len(minted) == 1 and st["dead_armature_skipped"] == []


def test_the_weight_siblings_of_a_path():
    assert up._weight_siblings(r"a\b_1.nif") == [r"a\b_1.nif", r"a\b.nif",
                                                 r"a\b_0.nif"]
    assert up._weight_siblings(r"a\b.NIF") == [r"a\b.NIF", r"a\b.nif",
                                               r"a\b_0.nif", r"a\b_1.nif"]
    assert up._weight_siblings(r"a\b.tri") == [r"a\b.tri"]


# ------------------------------------------ (e) ours, (f) a third-party twin

def test_a_mesh_this_run_converted_is_alive(tmp_path):
    """The source file is gone but its converted copy is ours -- `meshes\\`
    prefix and all."""
    st, minted, _ = _nonbody(tmp_path, [_helm({b"MOD3": "meshes\\" + HELM_F})],
                             lookup=_have(), conv={HELM_F})
    assert len(minted) == 1 and st["dead_armature_skipped"] == []


def test_a_mesh_only_a_third_party_ube_twin_has_is_alive(tmp_path):
    twin = lambda p: "A UBE Patch" if _key(p) == _key(HELM_F) else None
    st, minted, _ = _nonbody(tmp_path, [_helm({b"MOD3": HELM_F})],
                             lookup=_have(), twin=twin)
    assert len(minted) == 1 and st["dead_armature_skipped"] == []


# ----------------------------------------------------- (g) no mesh named

def test_an_armature_that_names_no_mesh_is_not_dead(tmp_path):
    """A slot placeholder (it hides a body part and draws nothing on purpose)."""
    st, minted, _ = _nonbody(tmp_path, [_helm({}), _helm({b"MOD3": ""}, 0x01000801)],
                             lookup=_have())
    assert len(minted) == 2 and st["dead_armature_skipped"] == []


# ------------------------------------------------ (h) no lookup: fail open

def test_no_lookup_mints_every_armature(tmp_path):
    st, minted, _ = _nonbody(tmp_path, [_helm({b"MOD2": HELM_M, b"MOD3": HELM_F})],
                             lookup=None)
    assert len(minted) == 1 and st["dead_armature_skipped"] == []


def test_an_unreadable_modlist_gives_no_lookup_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda out: None)
    assert ac._dead_armature_lookup(tmp_path, None, None) is None
    out = capsys.readouterr().out
    assert "could not list the meshes the modlist has" in out
    assert "every armature is given a UBE armature, as before" in out


def test_the_lookup_is_shared_when_another_rule_built_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_mesh_exists_anywhere",
                        lambda out: pytest.fail("built a second time"))
    built = _have()
    assert ac._dead_armature_lookup(tmp_path, None, built) is built


def test_the_lookup_is_built_when_no_other_rule_built_one(tmp_path, monkeypatch):
    """The female guard and world-mesh rules may be off: their lookups are
    None, and this rule builds its own."""
    own = _have()
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda out: own)
    assert ac._dead_armature_lookup(tmp_path, None, None) is own


def test_switched_off_there_is_no_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda out: _have())
    assert ac._dead_armature_lookup(tmp_path, _have()) is None


def _step(tmp_path, monkeypatch, look):
    """Drive the real coverage step with both passes stubbed; return the
    `dead_mesh_exists` each pass was handed."""
    from src import paths
    inst = tmp_path / "inst"
    mods = inst / "mods"
    prof = inst / "profiles" / "P"
    prof.mkdir(parents=True)
    (prof / "modlist.txt").write_text("+Our Output\n+A Mod\n", encoding="utf-8")
    plug = _save(mods / "A Mod" / "X.esp", ["Skyrim.esm"], [])
    out = mods / "Our Output"
    (out / "meshes" / "!UBE").mkdir(parents=True)
    (out / "meshes" / "!UBE" / "x_1.nif").write_bytes(b"x")
    lay = paths.Layout(mods_root=mods, instance_dir=inst, selected_profile="P")
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered", lambda l: ["X.esp"])
    monkeypatch.setattr(ac.paths, "plugin_file_index", lambda l: {"x.esp": plug})
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda output: look)
    monkeypatch.setattr(ac, "_third_party_ube_twin_lookup", lambda *a, **k: None)
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda **k: None)
    monkeypatch.setattr(ac, "_game_view_mesh_resolver", lambda output: None)
    seen = {}

    def _fake(name):
        def run(out_path, ordered, **k):
            seen[name] = k.get("dead_mesh_exists")
            return {"armo_targets": 1, "minted_armas": 1}
        return run
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_nonbody_ube_coverage_patch",
                        _fake("nb"))
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_body_ube_coverage_patch",
                        _fake("bd"))
    ac._emit_unified_coverage_patches(out, out / "_unmerged_patches", [],
                                      "CBBE_to_UBE_Combined.esp")
    return seen


def test_the_step_hands_both_passes_the_lookup(tmp_path, monkeypatch):
    look = _have()
    seen = _step(tmp_path, monkeypatch, look)
    assert seen["nb"] is look and seen["bd"] is look


def test_the_step_hands_it_with_the_other_existence_rules_off(tmp_path, monkeypatch):
    """The female guard, world-mesh and nude-skin lookups are None when their
    switches are off; this rule's is not."""
    for name in ("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "CBBE2UBE_NO_COVERAGE_WORLD_MESH",
                 "CBBE2UBE_NO_COVERAGE_NUDE_SKIN"):
        monkeypatch.setenv(name, "1")
    look = _have()
    seen = _step(tmp_path, monkeypatch, look)
    assert seen["nb"] is look and seen["bd"] is look


def test_switched_off_the_step_hands_no_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    seen = _step(tmp_path, monkeypatch, _have())
    assert seen["nb"] is None and seen["bd"] is None


# ------------------------- (i) judged after the race-list and accessory rules

def test_a_dead_race_list_armature_is_not_minted(tmp_path):
    """An Argonian-primary armature listing a human race is taken by the
    race-list rule; with its mesh dead it is still not minted."""
    amulet = _arma(0x01000800, 1 << 5, {b"MOD3": r"gear\amulet\amulet_1.nif"},
                   race=ARGONIAN, races=(ARGONIAN, NORD))
    armo = _armo(0x01000810, 1 << 5, [amulet], edid="Amulet")
    st, minted, _ = _nonbody(tmp_path, [amulet], [armo], lookup=_have())
    assert minted == []
    assert st["dead_armature_skipped"] == ["mod.esp|800"]
    assert st["dead_dropped"] == [(("mod.esp", 0x810), "Amulet")]
    assert st["race_listed"] == []


def test_a_live_race_list_armature_is_minted(tmp_path):
    """Control for the one above."""
    amulet = _arma(0x01000800, 1 << 5, {b"MOD3": r"gear\amulet\amulet_1.nif"},
                   race=ARGONIAN, races=(ARGONIAN, NORD))
    armo = _armo(0x01000810, 1 << 5, [amulet], edid="Amulet")
    st, minted, _ = _nonbody(tmp_path, [amulet], [armo],
                             lookup=_have(r"gear\amulet\amulet_1.nif"))
    assert len(minted) == 1 and st["race_listed"] == [(("mod.esp", 0x810), "Amulet")]


def test_a_dead_hood_riding_with_a_robe_is_not_minted(tmp_path):
    """The body-accessory rule adds the hood; its mesh is dead, so it is not
    minted. The robe is. The hood is not also counted as drawn with the body
    (the report would say both 'drawn on UBE' and 'not minted')."""
    robe = _arma(0x01000800, BODY, {b"MOD3": ROBE})
    hood = _arma(0x01000801, HOOD, {b"MOD3": HOODM})
    armo = _armo(0x01000810, BODY | HOOD, [robe, hood], edid="HoodedRobe")
    st, minted = _body(tmp_path, [robe, hood], [armo], lookup=_have(ROBE),
                       conv={ROBE})
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + ROBE]
    assert st["dead_armature_skipped"] == ["mod.esp|801"]
    assert st["body_accessory"] == [], "a hood not minted is not drawn with the body"
    assert st["dead_dropped"] == []


def test_a_live_hood_riding_with_a_robe_is_counted(tmp_path):
    """Control for the one above: the accessory count is untouched."""
    robe = _arma(0x01000800, BODY, {b"MOD3": ROBE})
    hood = _arma(0x01000801, HOOD, {b"MOD3": HOODM})
    armo = _armo(0x01000810, BODY | HOOD, [robe, hood], edid="HoodedRobe")
    st, minted = _body(tmp_path, [robe, hood], [armo], lookup=_have(ROBE, HOODM),
                       conv={ROBE})
    assert sorted(m[b"MOD3"] for m in minted) == sorted(["!UBE\\" + ROBE, HOODM])
    assert st["body_accessory"] == ["mod.esp|801"]


def test_a_dead_hood_is_reported_only_as_not_minted(tmp_path, capsys):
    """The run log's two lines agree: the hood is under the NOTE, not under
    'drawn on UBE with the body'."""
    robe = _arma(0x01000800, BODY, {b"MOD3": ROBE})
    hood = _arma(0x01000801, HOOD, {b"MOD3": HOODM})
    armo = _armo(0x01000810, BODY | HOOD, [robe, hood], edid="HoodedRobe")
    st, _m = _body(tmp_path, [robe, hood], [armo], lookup=_have(ROBE), conv={ROBE})
    capsys.readouterr()
    ac._report_coverage_holds([st])
    out = capsys.readouterr().out
    assert "1 armature(s) name only meshes that exist nowhere" in out
    assert "drawn on UBE with the body" not in out


# -------------------- a dead female slot the stand-in rule fills (#coverage-female-standin)

MALE_X = r"modgear\helm_m_1.nif"            # exists nowhere
FEMALE_Y = r"modgear\helm_f_1.nif"          # exists nowhere
VANILLA_Z = r"armor\vanilla\f\helm_f_1.nif"  # the vanilla counterpart, converted
GLOVE_X = r"modgear\glove_m_1.nif"
GLOVE_Y = r"modgear\glove_f_1.nif"
GLOVE_Z = r"armor\vanilla\f\glove_f_1.nif"


def _paired_world(tmp_path, slots, male, fem, counterpart):
    """A vanilla (Skyrim.esm) armature pairs `male` with the converted
    `counterpart`; the mod's armature names `male` and `fem`, both dead."""
    van = _arma(0x000900, slots, {b"MOD2": male, b"MOD3": counterpart})
    sky = _save(tmp_path / "Skyrim.esm", [], [Group(label=b"ARMA", records=[van])])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    mine = _arma(0x01000800, slots, {b"MOD2": male, b"MOD3": fem})
    armo = _armo(0x01000810, slots, [mine], edid="Piece")
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[mine]),
                 Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _standin_nonbody(tmp_path):
    order = _paired_world(tmp_path, HEAD, MALE_X, FEMALE_Y, VANILLA_Z)
    look = _have(VANILLA_Z)
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, order, converted_rel_paths={_key(VANILLA_Z)},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True,
        female_mesh_exists=look, dead_mesh_exists=look)
    return st, _minted(out)


def test_a_dead_helmet_drawing_the_vanilla_stand_in_is_minted(tmp_path):
    """Its own meshes exist nowhere, but its male path pairs with a converted
    vanilla female mesh: the minted copy draws that stand-in, so UBE women see
    the helmet. It is not dead."""
    st, minted = _standin_nonbody(tmp_path)
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + VANILLA_Z]
    assert [d["arma"] for d in st["female_standin"]] == ["mod.esp|800"]
    assert st["dead_armature_skipped"] == [] and st["dead_dropped"] == []


def test_with_the_stand_in_rule_off_the_same_helmet_is_dropped(tmp_path, monkeypatch):
    """Nothing draws the stand-in then: the copy would draw nothing."""
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_STANDIN", "1")
    st, minted = _standin_nonbody(tmp_path)
    assert minted == []
    assert st["dead_armature_skipped"] == ["mod.esp|800"]


def test_an_unconverted_counterpart_is_no_stand_in(tmp_path):
    """The resolver gives none when the counterpart was not converted, so the
    armature is dead as before."""
    order = _paired_world(tmp_path, HEAD, MALE_X, FEMALE_Y, VANILLA_Z)
    look = _have(VANILLA_Z)
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, order, converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True,
        female_mesh_exists=look, dead_mesh_exists=look)
    assert _minted(out) == []
    assert st["dead_armature_skipped"] == ["mod.esp|800"]


def test_dead_gauntlets_drawing_the_vanilla_stand_in_are_minted_in_the_body_pass(tmp_path):
    order = _paired_world(tmp_path, HANDS, GLOVE_X, GLOVE_Y, GLOVE_Z)
    look = _have(GLOVE_Z)
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, order, converted_rel_paths={_key(GLOVE_Z)},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True,
        female_mesh_exists=look, dead_mesh_exists=look)
    minted = _minted(out)
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + GLOVE_Z]
    assert st["dead_armature_skipped"] == []


def test_a_dead_helmet_whose_male_mesh_the_stand_in_lookup_finds_is_minted(tmp_path):
    """The stand-in rule's "male as it is" branch: a non-body piece whose
    female slot is dead draws its male path where that mesh exists. Asked with
    the lookup the rebuild asks, so the two cannot disagree."""
    fem = _have(MALE_X)
    fem.body_fit = lambda p: False       # a helmet, not skinned to the body
    helm = _helm({b"MOD2": MALE_X, b"MOD3": FEMALE_Y})
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, [helm], [_armo(0x01000810, HEAD, [helm])]),
        converted_rel_paths=set(), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True,
        female_mesh_exists=fem, dead_mesh_exists=_have())
    assert [m[b"MOD3"] for m in _minted(out)] == [MALE_X]
    assert [d["arma"] for d in st["female_male_nonbody"]] == ["mod.esp|800"]
    assert st["dead_armature_skipped"] == []


# ------------------------ a hand/foot the nude-skin rule points at the UBE body

def test_nude_boots_drawing_the_ube_feet_are_not_dead(tmp_path):
    """A costume's "boots" are the nude CBBE feet; #coverage-nude-skin points
    them at the UBE body's own feet, which resolve. The copy draws those, so it
    is minted even where the named nude meshes are found nowhere."""
    from tests import test_coverage_nude_skin as ns
    st, minted = ns._run(tmp_path, [ns.FEET_ARMA], [(0x01000801, ns.FEET, [0x01000800])],
                         mesh_exists=lambda p: True, dead_mesh_exists=_have())
    assert [ns._mod3(m) for m in minted] == [ns.UBE_FEET]
    assert st["dead_armature_skipped"] == [] and st["dead_dropped"] == []


# ---------------------------------------------------- (j) the switch, parity

def test_switched_off_the_output_is_the_old_output(tmp_path, monkeypatch):
    armas = [_helm({b"MOD2": HELM_M, b"MOD3": HELM_F})]
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _st, _m, old_out = _nonbody(old, armas, lookup=None)
    monkeypatch.setenv(OFF, "1")
    st, minted, new_out = _nonbody(new, armas, lookup=_have())
    assert len(minted) == 1 and st["dead_armature_skipped"] == []
    assert new_out.read_bytes() == old_out.read_bytes()


def test_the_switch(monkeypatch):
    assert up._coverage_dead_armature() is True
    monkeypatch.setenv(OFF, "1")
    assert up._coverage_dead_armature() is False


# --------------------------------------------------- memoised per armature

def test_an_armature_shared_by_two_armours_is_judged_once(tmp_path):
    helm = _helm({b"MOD3": HELM_F})
    look = _have()
    st, minted, _ = _nonbody(
        tmp_path, [helm],
        [_armo(0x01000810, HEAD, [helm], "One"), _armo(0x01000811, HEAD, [helm], "Two")],
        lookup=look)
    assert minted == [] and len(st["dead_dropped"]) == 2
    assert len(look.asked) == len(up._weight_siblings(HELM_F))


# ------------------------------------------ an inactive plugin's archive

def test_a_mesh_only_in_an_unloaded_archive_counts_as_alive(tmp_path, monkeypatch):
    """The lookup lists every archive in the folders, loaded by a plugin or
    not. A mesh only in an archive no active plugin loads draws nothing in
    game, so the rule could drop it -- but it reads as alive and is minted as
    before: the lenient side, never a piece dropped for a mesh that may load."""
    from tests.test_bsa_seek_read import _write_bsa
    mods = tmp_path / "mods"
    _write_bsa(mods / "Packed Mod" / "NotLoaded.bsa",
               [(r"meshes\gear\helm", "helm_f_1.nif", b"x", None)])
    (mods / "Out").mkdir(parents=True)

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: ["Out", "Packed Mod"])
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: None)
    look = ac._dead_armature_lookup(mods / "Out")
    assert look is not None
    world = tmp_path / "world"
    world.mkdir()
    st, minted, _ = _nonbody(world, [_helm({b"MOD3": HELM_F})], lookup=look)
    assert len(minted) == 1 and st["dead_armature_skipped"] == []


# ------------------------------------------------------------ (k) the report

def _stats():
    nb = {"dead_armature_skipped": ["plug a.esp|800", "plug a.esp|801", "plug b.esm|900"],
          "dead_dropped": [(("plug a.esp", 0x810), "One"), (("plug b.esm", 0x910), "Two")]}
    bd = {"dead_armature_skipped": ["plug a.esp|801"],
          "dead_dropped": [(("plug a.esp", 0x811), "Three")]}
    return [nb, bd]


def test_the_report_counts_armatures_once_and_armours_left_without_one(capsys):
    ac._report_coverage_holds(_stats())
    out = capsys.readouterr().out
    assert ("[unified] 3 armature(s) name only meshes that exist nowhere (drawn by "
            "nobody, the source included) -- not minted (3 armour(s) left without "
            "one)") in out
    assert "NOTE" in out
    lines = out.splitlines()
    assert any(l.split() == ["2", "plug", "a.esp"] for l in lines)
    assert any(l.split() == ["1", "plug", "b.esm"] for l in lines)


def test_nothing_dead_says_nothing(capsys):
    ac._report_coverage_holds([{"dead_armature_skipped": [], "dead_dropped": []}])
    assert "exist nowhere" not in capsys.readouterr().out
