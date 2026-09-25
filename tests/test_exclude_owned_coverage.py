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

"""#exclude-owned-coverage -- the winner scan leaves an excluded mod's armour
alone, not only its meshes.

THE DEFECT. `--exclude-mods` removed a mod from the sources and nothing else:
`_cmd_auto` handed `_cmd_convert` a fresh Namespace without it, and the coverage
winner scan -- the SOLE generator over every armour in the load order -- minted
armatures for the excluded mod's armour anyway. Reported in game on a follower
excluded because a hand-made UBE refit exists: she wore converted male boots.

OWNED = the DEFINING plugin ships in the excluded mod's folder (user, 09-23).
Of four readings it was the only one that caught all 14 of her minted armours;
the winning override caught 3 (an overhaul patch wins the rest).
"""
import inspect
import struct
from pathlib import Path

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring

DEFAULT = 0x00000019
HEAD = 1 << 0
FEET = 1 << 7
OFF = "CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE"


def _save(path, masters, groups):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _armo(formid, slots=HEAD, arma=None):
    p = encode_subrecord(b"EDID", encode_zstring("Piece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    if arma is not None:
        p += encode_subrecord(b"MODL", struct.pack("<I", arma))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=p)


def _arma(formid, slots=HEAD):
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD3", encode_zstring(r"follower\f\piece_1.nif"))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _follower(root):
    """mods/Follower Mod/Follower.esp defines armour 0x801 and overrides the
    master's armour 0x012E46; mods/Follower Mod/Unused.esp is not active."""
    fol = root / "Follower Mod"
    p = _save(fol / "Follower.esp", ["Skyrim.esm"],
              [Group(label=b"ARMO", records=[_armo(0x01000801),
                                             _armo(0x00012E46)])])
    _save(fol / "Unused.esp", ["Skyrim.esm"],
          [Group(label=b"ARMO", records=[_armo(0x01000802)])])
    return p


# ------------------------------------------------------------ ownership

def test_owned_means_defined_by_a_plugin_in_the_excluded_folder(tmp_path):
    p = _follower(tmp_path)
    owned, per_mod = ac._armos_defined_by_mods(tmp_path, ["follower mod"], [p])
    assert owned == {("follower.esp", 0x801)}
    assert per_mod == {"Follower Mod": 1}


def test_an_override_of_a_masters_armour_is_not_owned(tmp_path):
    """Vanilla armour an excluded mod merely edits must keep its coverage. Checked
    by FormID under any plugin name: counted, it would read as the excluded
    plugin's own record, which no generator identity matches -- and would
    still be wrong."""
    p = _follower(tmp_path)
    owned, per_mod = ac._armos_defined_by_mods(tmp_path, ["Follower Mod"], [p])
    assert not [i for i in owned if i[1] == 0x012E46]
    assert per_mod == {"Follower Mod": 1}


def test_an_inactive_plugin_owns_nothing(tmp_path):
    p = _follower(tmp_path)
    owned, _ = ac._armos_defined_by_mods(tmp_path, ["Follower Mod"], [p])
    assert ("unused.esp", 0x802) not in owned


def test_a_folder_name_with_a_comma_still_matches(tmp_path):
    """The CLI splits every value on commas, so a folder named with one arrives
    in pieces; all its pieces given = that folder."""
    fol = tmp_path / "Armour, Robes and Hoods"
    p = _save(fol / "Mine.esp", ["Skyrim.esm"],
              [Group(label=b"ARMO", records=[_armo(0x01000801)])])
    owned, _ = ac._armos_defined_by_mods(tmp_path, ["Armour", "Robes and Hoods"], [p])
    assert owned == {("mine.esp", 0x801)}
    owned, _ = ac._armos_defined_by_mods(tmp_path, ["Armour"], [p])
    assert owned == set(), "one piece alone is not that folder"


def test_a_name_matching_no_folder_is_reported(tmp_path):
    p = _follower(tmp_path)
    missing = []
    ac._armos_defined_by_mods(tmp_path, ["Follower Mod", "Folower Mod"], [p],
                              missing=missing)
    assert missing == ["folower mod"]


def test_the_vanilla_pseudo_name_owns_nothing(tmp_path):
    """`vanilla` switches the vanilla sweep off; it is not a mod folder."""
    fol = tmp_path / "Vanilla"
    p = _save(fol / "Mine.esp", ["Skyrim.esm"],
              [Group(label=b"ARMO", records=[_armo(0x01000801)])])
    assert ac._armos_defined_by_mods(tmp_path, ["vanilla"], [p]) == (set(), {})


# ------------------------------------------------------- the two passes

def _world(tmp_path, slots):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[_arma(0x01000800, slots)]),
                 Group(label=b"ARMO", records=[_armo(0x01000801, slots, 0x01000800)])])
    return [sky, ube, mod]


PELVIS = 1 << 22   # slot 52: a body slot the non-body pass sees


def test_the_non_body_pass_withholds_an_owned_armour(tmp_path):
    """Body territory (#exclude-body-only keeps only non-body pieces): a
    pelvis piece no other mod patches is still withheld."""
    out = tmp_path / "nb.esp"
    kw = dict(exclude_names={out.name}, master_data_dirs=[tmp_path], cover_all=True)
    paths = _world(tmp_path, PELVIS)
    nobody = ac._ExclusionKeepProbe(tmp_path / "mods", [])
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, paths, withheld_armo_abs={("mod.esp", 0x801)},
        exclusion_probe=nobody, **kw)
    assert st["armo_targets"] == 0
    assert [a for a, _e in st["withheld"]] == [("mod.esp", 0x801)]
    st = up.generate_modded_nonbody_ube_coverage_patch(out, paths, **kw)
    assert st["armo_targets"] == 1 and st["withheld"] == []


def test_an_owned_body_armour_is_named_even_if_the_female_guard_would_drop_it(tmp_path):
    """The reported case for a mod with no refit: an excluded body armour whose
    male mesh was converted elsewhere and whose female mesh was not. It has no
    UBE armature from anyone, so it must be named as withheld."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", 1 << 2, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD2", encode_zstring(r"armor\m\cuirass_1.nif"))
    p += encode_subrecord(b"MOD3", encode_zstring(r"follower\f\vest_1.nif"))
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=p)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]),
                 Group(label=b"ARMO", records=[_armo(0x01000801, 1 << 2, 0x01000800)])])
    out = tmp_path / "bd.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod], converted_rel_paths={"armor/m/cuirass_1.nif"},
        exclude_names={out.name}, master_data_dirs=[tmp_path], cover_all=True,
        cover_hands_feet=True, withheld_armo_abs={("mod.esp", 0x801)})
    assert [a for a, _e in st["withheld"]] == [("mod.esp", 0x801)]
    assert st["female_guard_dropped"] == [] and st["female_guard_skipped"] == []


def test_the_body_pass_withholds_an_owned_armour(tmp_path):
    out = tmp_path / "bd.esp"
    kw = dict(converted_rel_paths=set(), exclude_names={out.name},
              master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True)
    paths = _world(tmp_path, FEET)
    st = up.generate_modded_body_ube_coverage_patch(
        out, paths, withheld_armo_abs={("mod.esp", 0x801)}, **kw)
    assert st["armo_targets"] == 0
    assert [a for a, _e in st["withheld"]] == [("mod.esp", 0x801)]
    st = up.generate_modded_body_ube_coverage_patch(out, paths, **kw)
    assert st["armo_targets"] == 1 and st["withheld"] == []


# ----------------------------------------------------------- the wiring

def _emit(tmp_path, monkeypatch, exclude_mods):
    """Run _emit_unified_coverage_patches against a fake modlist and capture what
    each pass is told to withhold."""
    mods = tmp_path / "mods"
    fol = _follower(mods)
    out = tmp_path / "out"
    (out / "meshes" / "!UBE").mkdir(parents=True)
    (out / "meshes" / "!UBE" / "x_1.nif").write_bytes(b"x")
    patches = out / "_unmerged_patches"
    patches.mkdir()
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered", lambda lay: ["Follower.esp"])
    monkeypatch.setattr(ac.paths, "plugin_file_index", lambda lay: {"follower.esp": str(fol)})
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda output: _LOOKUP)
    seen = {}

    def _fake(name):
        def run(*a, **k):
            seen[name] = k.get("withheld_armo_abs")
            seen[name + "_lookup"] = k.get("female_mesh_exists")
            return {"armo_targets": 1, "minted_armas": 1}
        return run
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_nonbody_ube_coverage_patch",
                        _fake("nb"))
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_body_ube_coverage_patch",
                        _fake("bd"))
    ok, _t, body = ac._emit_unified_coverage_patches(
        out, patches, [], "CBBE_to_UBE_Combined.esp", exclude_mods=exclude_mods)
    assert ok and body
    return seen


def _LOOKUP(model):
    return True


def _held(seen):
    return {k: v for k, v in seen.items() if not k.endswith("_lookup")}


def test_both_passes_are_told_what_the_excluded_mods_own(tmp_path, monkeypatch):
    seen = _emit(tmp_path, monkeypatch, ["Follower Mod"])
    assert _held(seen) == {"nb": {("follower.esp", 0x801)},
                           "bd": {("follower.esp", 0x801)}}


def test_the_off_switch_withholds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    seen = _emit(tmp_path, monkeypatch, ["Follower Mod"])
    assert _held(seen) == {"nb": set(), "bd": set()}


def test_no_exclusions_withhold_nothing(tmp_path, monkeypatch):
    assert _held(_emit(tmp_path, monkeypatch, ())) == {"nb": set(), "bd": set()}


def test_both_passes_get_the_mesh_lookup(tmp_path, monkeypatch):
    """#coverage-female-guard: without it no female path reads as dead, and the
    dead-path exemption the user asked for never happens."""
    seen = _emit(tmp_path, monkeypatch, ())
    assert seen["nb_lookup"] is _LOOKUP and seen["bd_lookup"] is _LOOKUP


def test_no_lookup_with_the_guard_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "1")
    seen = _emit(tmp_path, monkeypatch, ())
    assert seen["nb_lookup"] is None and seen["bd_lookup"] is None


def test_auto_hands_its_exclusions_to_convert():
    """`auto` builds a fresh Namespace for `convert`; a field left off it is the
    whole original defect. Both lists go: --exclude-mods and the coverage-only
    list a Select-mods run sends."""
    src = inspect.getsource(ac._cmd_auto)
    assert "exclude_mods=(_user_excl + (_split_mod_arg(" in src
    assert 'getattr(args, "coverage_exclude_mods", None)) or [])) or None,' in src


def test_auto_takes_a_coverage_only_exclusion(tmp_path):
    args = ac._build_parser().parse_args(
        ["auto", "--only-mods", "Picked", "--coverage-exclude-mods", "Follower Mod"])
    assert ac._split_mod_arg(args.coverage_exclude_mods) == ["Follower Mod"]
    assert args.exclude_mods is None, "it must not gate conversion"


def test_the_window_sends_exclusions_in_both_modes():
    from src import gui
    assert gui._armor_selection_argv(False, [], ["Follower Mod"]) == \
        ["--exclude-mods", "Follower Mod"]
    got = gui._armor_selection_argv(True, ["Picked"], ["Follower Mod"])
    assert got == ["--only-mods", "Picked", "--coverage-exclude-mods", "Follower Mod"]


def test_a_picked_mod_is_not_excluded_from_coverage():
    """Select mode is an explicit pick: a mod the user ticks is converted, so its
    armour must get its armatures even if it is on the exclusion list."""
    from src import gui
    got = gui._armor_selection_argv(True, ["Follower Mod"], ["follower mod"])
    assert got == ["--only-mods", "Follower Mod"]


def run_convert(base, monkeypatch, *, exclude=None, built=None, on_result=None):
    """Drive the real `_cmd_convert` over one source mod inside a temporary
    modlist, up to and through the coverage step, and record what it hands on:
    returns (the kwargs each `auto_convert_mod` call got, the kwargs of the
    coverage call or None when it never ran).

    `exclude` is the `--exclude-mods` list as argparse builds it (one entry per
    flag, each may hold commas). `built` = {mod folder: [rel under
    meshes\\!UBE]} -- enabled mods that ship loose BUILT UBE meshes, highest
    MO2 priority first. The conversion and the merge are stubs; the lookups
    `_cmd_convert` builds for the batch are real. `on_result(result, out)`, if
    given, shapes the stub's result and output before the batch goes on."""
    import argparse
    from src import preflight as pf
    base.mkdir(parents=True, exist_ok=True)
    mods = base / "mods"
    mods.mkdir(exist_ok=True)
    for name, rels in (built or {}).items():
        for rel in rels:
            f = mods / name / "meshes" / "!UBE" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
    mod = base / "SomeMod"
    mod.mkdir(exist_ok=True)
    out = base / "out"
    settings = base / "CBBEtoUBE_settings.json"
    settings.write_text("{}", encoding="utf-8")
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(settings))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(base / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: list(built or ()))
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data",
                        lambda *a, **k: base / "SkyPatcher.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)
    converted = []

    def _converted(source_dir, *a, **k):
        converted.append(k)
        patches = out / "_unmerged_patches"
        patches.mkdir(parents=True, exist_ok=True)
        (patches / "SomeMod UBE patch.esp").write_bytes(b"")
        res = ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=out)
        if on_result is not None:
            on_result(res, out)
        return res
    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models", lambda *a, **k: {})
    coverage = {}

    def _emit(*a, **k):
        coverage.update(k)
        return True, 1, False
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches", _emit)
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split", lambda *a, **k: {})
    ns = argparse.Namespace(
        sources=[mod], output=out, esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False, exclude_mods=exclude)
    ac._cmd_convert(ns)
    return converted, (coverage or None)


def test_convert_takes_the_flag_and_passes_it_on(tmp_path, monkeypatch):
    """Behavioural: `convert --exclude-mods` reaches the coverage winner scan.
    Dropping it there is the original defect -- the excluded follower's armour
    was covered again and she wore converted male boots."""
    args = ac._build_parser().parse_args(
        ["convert", str(tmp_path), "-o", str(tmp_path / "o"),
         "--exclude-mods", "A Mod,B Mod", "--exclude-mods", "C Mod"])
    assert ac._split_mod_arg(args.exclude_mods) == ["A Mod", "B Mod", "C Mod"]
    _, coverage = run_convert(tmp_path / "run", monkeypatch,
                              exclude=args.exclude_mods)
    assert coverage is not None, "the coverage step never ran"
    assert list(coverage["exclude_mods"]) == ["A Mod", "B Mod", "C Mod"]


def test_convert_without_the_flag_excludes_nothing(tmp_path, monkeypatch):
    """Control: no flag, an empty exclusion list -- the recorder above sees
    what `_cmd_convert` passes, not a constant."""
    _, coverage = run_convert(tmp_path, monkeypatch, exclude=None)
    assert coverage is not None and list(coverage["exclude_mods"]) == []


def test_the_holds_are_reported(capsys):
    ac._report_coverage_holds([
        {"withheld": [(("follower.esp", 0x801), "Glasses")]},
        {"female_kept": [{"slot": "MOD3", "kept": "f.nif", "male": "!UBE\\m.nif"}],
         "female_dead_male": [{"slot": "MOD3", "dead": "gone.nif", "male": "!UBE\\m.nif"}],
         "female_guard_skipped": ["mod.esp|800"],
         "female_guard_dropped": [(("mod.esp", 0x802), "Vest")]}])
    text = capsys.readouterr().out
    assert "1 armour(s) of an excluded mod have no UBE armature" in text
    assert "Glasses" in text and "follower.esp|000801" in text
    assert "1 female model slot(s) kept" in text and "1 body armature(s)" in text
    assert "not covered: Vest" in text
    assert "1 female model slot(s) name a mesh that exists nowhere" in text
    assert "gone.nif" in text
    dead_line = next(l for l in text.splitlines() if "exists nowhere" in l)
    assert dead_line.lstrip().startswith("NOTE:"), \
        "kept on purpose, as before the guard: information, not a problem"


def test_nothing_held_prints_nothing(capsys):
    ac._report_coverage_holds([{"withheld": [], "female_kept": []}, {}])
    assert capsys.readouterr().out == ""
