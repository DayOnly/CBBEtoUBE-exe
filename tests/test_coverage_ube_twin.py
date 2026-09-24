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

r"""#coverage-ube-twin -- a minted coverage slot points at the hand-made UBE
mesh a third-party mod ships at `!UBE\<source path>`.

THE DEFECT. Two boots/gloves records of a clothing overhaul reuse armatures
whose own armour a hand-made UBE patch covers. The converter skips that mod
(already UBE), so nothing of ours exists for the mesh, and the coverage pass
minted both drawing the CBBE mesh while the UBE version sat loose in the
patch's folder.

ON BY DEFAULT. The live census moved 8 links, not the 2 expected, and every
one points at a UBE version of the same mesh already installed (the patch, or
the user's own UBE BodySlide build). `CBBE2UBE_NO_COVERAGE_UBE_TWIN=1` turns
it off.
"""
import struct
from pathlib import Path

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

DEFAULT = 0x00000019
HEAD = 1 << 0
BODY = 1 << 2
FEET = 1 << 7
SWITCH = "CBBE2UBE_NO_COVERAGE_UBE_TWIN"
BOOTS = r"Patrol\Armor\Boots_Female_1.nif"


def _on(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)


def _off(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


# ---------------------------------------------------------------- the switch

def test_it_is_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    assert up._coverage_ube_twin() is True
    monkeypatch.setenv(SWITCH, "0")
    assert up._coverage_ube_twin() is True
    _off(monkeypatch)
    assert up._coverage_ube_twin() is False


# ------------------------------------------------------------- the passes

def _save(path, masters, groups):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _world(tmp_path, models, slots):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=p)
    q = encode_subrecord(b"EDID", encode_zstring("Piece"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    q += encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
    armo = Record(sig=b"ARMO", flags=0, formid=0x01000801, payload=q)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]), Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _twin_of(*paths, mod="A UBE Patch"):
    have = {p.lower() for p in paths}
    return lambda p: mod if p.lower() in have else None


def _body(tmp_path, models, slots, conv, twin):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, models, slots), converted_rel_paths=conv,
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True,
        ube_twin_exists=twin)
    return st, _minted(out)


def _minted(out):
    if not out.is_file():
        return []
    return [{s: d.rstrip(b"\x00").decode() for s, d in iter_subrecords(r.payload)
             if s in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")}
            for g in ESP.load(out).groups if g.label == b"ARMA" for r in g.records]


def test_boots_point_at_the_hand_made_ube_mesh(tmp_path, monkeypatch):
    _on(monkeypatch)
    st, minted = _body(tmp_path, {b"MOD3": BOOTS}, FEET, set(), _twin_of(BOOTS))
    assert minted[0][b"MOD3"] == "!UBE\\" + BOOTS
    assert st["ube_twin"] == [{"arma": "mod.esp|800", "slot": "MOD3",
                               "path": "!UBE\\" + BOOTS, "mod": "A UBE Patch"}]


def test_switched_off_the_boots_keep_the_cbbe_mesh(tmp_path, monkeypatch):
    _off(monkeypatch)
    st, minted = _body(tmp_path, {b"MOD3": BOOTS}, FEET, set(), _twin_of(BOOTS))
    assert minted[0][b"MOD3"] == BOOTS and st["ube_twin"] == []


def test_no_twin_keeps_the_source_mesh(tmp_path, monkeypatch):
    """Negative control: the lookup is asked, not assumed."""
    _on(monkeypatch)
    st, minted = _body(tmp_path, {b"MOD3": BOOTS}, FEET, set(), _twin_of())
    assert minted[0][b"MOD3"] == BOOTS and st["ube_twin"] == []


def test_a_twin_never_admits_an_armature(tmp_path, monkeypatch):
    """A torso nothing of ours was converted for stays out: the twin moves where
    a minted slot points, it does not mint. (#skip-built-ube-path admits it when
    the planner leaves the mesh to its builder; its own tests cover that.)"""
    _on(monkeypatch)
    monkeypatch.setenv("CBBE2UBE_NO_SKIP_BUILT_UBE_PATH", "1")
    st, minted = _body(tmp_path, {b"MOD3": r"patrol\armor\cuirass_1.nif"}, BODY,
                       set(), _twin_of(r"patrol\armor\cuirass_1.nif"))
    assert minted == [] and st["armo_targets"] == 0


def test_a_twin_is_a_converted_world_mesh(tmp_path, monkeypatch):
    """Our first-person torso admits it; the twin is the world mesh it draws, so
    #coverage-world-mesh keeps it."""
    _on(monkeypatch)
    torso, first = r"patrol\armor\cuirass_1.nif", r"patrol\armor\1stcuirass_1.nif"
    # The patch ships both; ours wins for the first-person mesh.
    st, minted = _body(tmp_path, {b"MOD3": torso, b"MOD5": first}, BODY,
                       {first.replace("\\", "/")}, _twin_of(torso, first))
    assert st["world_mesh_skipped"] == []
    assert minted[0][b"MOD3"] == "!UBE\\" + torso
    assert minted[0][b"MOD5"] == "!UBE\\" + first
    assert [d["slot"] for d in st["ube_twin"]] == ["MOD3"], "ours is not a twin"


def test_an_accessory_points_at_its_twin(tmp_path, monkeypatch):
    _on(monkeypatch)
    choker = r"patrol\jewelry\choker_1.nif"
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, {b"MOD3": choker}, HEAD), converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True, ube_twin_exists=_twin_of(choker))
    assert _minted(out)[0][b"MOD3"] == "!UBE\\" + choker
    assert len(st["ube_twin"]) == 1


# ------------------------------------------------------------- the lookup

def _modlist(tmp_path, monkeypatch, order):
    mods = tmp_path / "mods"
    for name, files in order:
        for f in files:
            p = mods / name / "meshes" / "!UBE" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        (mods / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: [n for n, _f in order])
    return mods


def test_the_lookup_names_the_winning_third_party_mod(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, [("High Patch", ["patrol/armor/boots_female_1.nif"]),
                                     ("Low Patch", ["patrol/armor/boots_female_1.nif"])])
    twin = ac._third_party_ube_twin_lookup(tmp_path / "mods" / "Out")
    assert twin(BOOTS) == "High Patch"
    assert twin(r"meshes\Patrol\Armor\Boots_Female_1.nif") == "High Patch"
    assert twin(r"patrol\armor\gloves_female_1.nif") is None


def test_our_output_is_never_a_third_party(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch,
                    [("Out", ["patrol/armor/boots_female_1.nif"]),
                     ("Old Output", ["patrol/armor/boots_female_1.nif"]),
                     ("A UBE Patch", ["patrol/armor/helm_1.nif"])])
    (mods / "Old Output" / "conversion_report_Some Mod.txt").write_text("x")
    twin = ac._third_party_ube_twin_lookup(mods / "Out")
    assert twin(BOOTS) is None, "neither this run's output nor an old one counts"
    assert twin(r"patrol\armor\helm_1.nif") == "A UBE Patch", "control: the lookup works"


def test_an_excluded_mod_is_never_a_third_party(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch,
             [("Follower Mod", ["patrol/armor/boots_female_1.nif"]),
              ("Comma, Mod", ["patrol/armor/gloves_female_1.nif"]),
              ("A UBE Patch", ["patrol/armor/helm_1.nif"])])
    twin = ac._third_party_ube_twin_lookup(
        tmp_path / "mods" / "Out", ["follower mod", "Comma", "Mod"])
    assert twin(BOOTS) is None
    assert twin(r"patrol\armor\gloves_female_1.nif") is None, \
        "a folder name with a comma arrives split by the CLI"
    assert twin(r"patrol\armor\helm_1.nif") == "A UBE Patch"


def test_the_lookup_ignores_overwrite(tmp_path, monkeypatch):
    """A stray converter output in MO2's overwrite must never read as a patch."""
    _modlist(tmp_path, monkeypatch, [("A Mod", [])])
    ow = tmp_path / "overwrite" / "meshes" / "!UBE" / "patrol" / "armor" / "boots_female_1.nif"
    ow.parent.mkdir(parents=True)
    ow.write_bytes(b"x")
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: tmp_path / "overwrite")
    assert ac._third_party_ube_twin_lookup(tmp_path / "mods" / "Out")(BOOTS) is None


def test_no_modlist_means_no_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: None)
    assert ac._third_party_ube_twin_lookup(tmp_path) is None


# ------------------------------------------------------------- the wiring

def _emit(tmp_path, monkeypatch, exclude_mods=()):
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
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda output: None)
    asked = []
    monkeypatch.setattr(ac, "_third_party_ube_twin_lookup",
                        lambda output, excl=(): asked.append(list(excl)) or _TWIN)
    seen = {}

    def _fake(name):
        def run(*a, **k):
            seen[name] = k.get("ube_twin_exists")
            return {"armo_targets": 1, "minted_armas": 1}
        return run
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_nonbody_ube_coverage_patch",
                        _fake("nb"))
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_body_ube_coverage_patch",
                        _fake("bd"))
    ac._emit_unified_coverage_patches(out, patches, [], "CBBE_to_UBE_Combined.esp",
                                      exclude_mods=exclude_mods)
    return seen, asked


def _TWIN(model):
    return None


def test_both_passes_get_the_lookup_when_asked_for(tmp_path, monkeypatch):
    _on(monkeypatch)
    seen, asked = _emit(tmp_path, monkeypatch, ["Follower Mod"])
    assert seen == {"nb": _TWIN, "bd": _TWIN}
    assert asked == [["Follower Mod"]], "the exclusions reach the lookup"


def test_no_lookup_when_switched_off(tmp_path, monkeypatch):
    _off(monkeypatch)
    seen, asked = _emit(tmp_path, monkeypatch)
    assert seen == {"nb": None, "bd": None} and asked == []


def test_the_postflight_resolves_a_twin_only_when_on(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_third_party_ube_twin_lookup",
                        lambda output, excl=(): _twin_of(BOOTS))
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_NUDE_SKIN", "1")
    _off(monkeypatch)
    assert ac._outside_ube_mesh_resolver(tmp_path) is None
    _on(monkeypatch)
    res = ac._outside_ube_mesh_resolver(tmp_path)
    assert res("!UBE\\" + BOOTS)
    assert not res("!UBE\\patrol\\armor\\other_1.nif")


def test_the_twins_are_reported(capsys):
    ac._report_coverage_holds([{"ube_twin": [
        {"arma": "mod.esp|800", "slot": "MOD3", "path": "!UBE\\" + BOOTS,
         "mod": "A UBE Patch"}]}])
    text = capsys.readouterr().out
    assert "1 model slot(s) point at a hand-made UBE mesh another mod ships" in text
    assert "(A UBE Patch)" in text
