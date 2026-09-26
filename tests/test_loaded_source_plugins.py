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

"""#loaded-source-plugins -- a mod's source plugins are the copies the game loads.

THE DEFECT. `_find_source_esps` read every plugin anywhere in a mod folder. MO2
loads plugins only from a mod's ROOT, and of several root copies of one name only
the highest-priority mod's. A nested copy (a mod packed one folder too deep
inside itself) and a losing duplicate (a base mod's plugin that its hotfix
replaces) were still read: their armatures were planned, and their per-source
patch -- named by the plugin stem alone, in one shared folder -- was written by
whichever copy ran LAST, the lowest-priority one, sidecars included.

THE RULE. For a folder of the modlist's mods root, a plugin in a subfolder and a
root plugin whose name loads from another folder are not sources (a folder
outside the modlist keeps every plugin). A mod left with no loaded plugin plans
nothing -- never "every mesh it ships". Within one batch, a per-source patch an
earlier (higher-priority) source wrote is never overwritten by a later one.
`CBBE2UBE_NO_LOADED_SOURCE_PLUGINS=1` reads every copy again.
"""
import json
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import paths
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring
from src import ube_patcher as up

OFF = "CBBE2UBE_NO_LOADED_SOURCE_PLUGINS"
NEW = " (CBBEtoUBE src).esp"
DEFAULT = 0x00000019
BODY = up._BIPED_SLOT_BODY_BIT
OWN = 1 << 24


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    ac._LOADED_PLUGIN_INDEX.clear()
    yield
    ac._LOADED_PLUGIN_INDEX.clear()


def _plugin(path: Path, mod3: "str | None"):
    """A plugin with one DefaultRace body armature + its armour (MOD3 `mod3`),
    or with no ARMA group at all when `mod3` is None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    groups = []
    if mod3 is not None:
        arma = Record(sig=b"ARMA", flags=0, formid=OWN | 0x800, timestamp_vc=0,
                      version_unk=0x2C, payload=(
            encode_subrecord(b"EDID", encode_zstring("BodyAA"))
            + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
            + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
            + encode_subrecord(b"MOD3", encode_zstring(mod3))))
        armo = Record(sig=b"ARMO", flags=0, formid=OWN | 0x801, timestamp_vc=0,
                      version_unk=0x2C, payload=(
            encode_subrecord(b"EDID", encode_zstring("Body"))
            + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
            + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
            + encode_subrecord(b"MODL", struct.pack("<I", OWN | 0x800))
            + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))))
        groups = [Group(label=b"ARMA", records=[arma]),
                  Group(label=b"ARMO", records=[armo])]
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=groups).save(path)
    return path


def _masters(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=[], num_records=0, next_object_id=0x900,
                          version=1.7, flags=0x1), groups=[]).save(d / "Skyrim.esm")
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[]).save(d / "UBE_AllRace.esp")
    return d


def _modlist(tmp_path, monkeypatch, order):
    """An MO2 instance whose mods, highest priority first, are `order`."""
    inst = tmp_path / "inst"
    mods = inst / "mods"
    for m in order:
        (mods / m).mkdir(parents=True, exist_ok=True)
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "modlist.txt").write_text(
        "".join(f"+{m}\n" for m in order), encoding="utf-8")
    lay = paths.Layout(mods_root=mods, instance_dir=inst,
                       selected_profile="Default")
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    return mods


def _names(ps):
    return [p.name for p in ps]


# ---------------------------------------------------------------- the rule

def test_a_nested_copy_is_not_a_source(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Pack"])
    _plugin(mods / "Pack" / "Armour.esp", "armor/a/body_1.nif")
    _plugin(mods / "Pack" / "Pack" / "Armour Old.esp", "armor/old/body_1.nif")
    skipped = []
    assert _names(ac._find_source_esps(mods / "Pack", skipped=skipped)) == ["Armour.esp"]
    assert [(p.name, "subfolder" in why) for p, why in skipped] == [("Armour Old.esp", True)]


def test_a_losing_duplicate_is_not_a_source(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Hotfix", "Base"])
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Extra.esp", "armor/x/body_1.nif")
    skipped = []
    assert _names(ac._find_source_esps(mods / "Base", skipped=skipped)) == ["Extra.esp"]
    assert [(p.name, "'Hotfix'" in why) for p, why in skipped] == [("Quest.esp", True)]
    assert _names(ac._find_source_esps(mods / "Hotfix")) == ["Quest.esp"]


def test_mod_priority_decides_which_copy_is_read(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Base", "Hotfix"])
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]
    assert _names(ac._find_source_esps(mods / "Hotfix")) == []


def test_a_folder_outside_the_modlist_keeps_every_plugin(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["Pack"])
    loose = tmp_path / "download"
    _plugin(loose / "Armour.esp", "armor/a/body_1.nif")
    _plugin(loose / "Optional" / "Armour Alt.esp", "armor/b/body_1.nif")
    assert _names(ac._find_source_esps(loose)) == ["Armour.esp", "Armour Alt.esp"]


def test_switched_off_every_copy_is_read(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Hotfix", "Base"])
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Base" / "Quest.esp", "armor/q/body_1.nif")
    monkeypatch.setenv(OFF, "1")
    skipped = []
    assert _names(ac._find_source_esps(mods / "Base", skipped=skipped)) == [
        "Quest.esp", "Quest.esp"]
    assert skipped == []


def test_the_losing_copys_armatures_are_not_planned(tmp_path, monkeypatch):
    """Selection plans from the copy the game loads: the base mod's stale
    armature (another mesh) is not planned, the winner's is, by its own mod."""
    mods = _modlist(tmp_path, monkeypatch, ["Hotfix", "Base"])
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/fixed_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/stale_1.nif")
    assert ac._player_armor_mesh_bases(mods / "Base") == set()
    assert ac._player_armor_mesh_bases(mods / "Hotfix") == {"armor/q/fixed"}


# ---------------------------------------------------------------- the convert step

def _convert(src, out, tmp_path, monkeypatch, claimed=None, calls=None):
    def _resolve(bases, *a, **k):
        if calls is not None:
            calls.append(set(bases))
        return []
    monkeypatch.setattr(ac, "_resolve_armor_meshes", _resolve)
    ref = tmp_path / "ref.nif"
    ref.write_bytes(b"x")
    return ac.auto_convert_mod(src, out, ube_body_ref_path=ref,
                               master_data_dirs=[_masters(tmp_path / "data")],
                               nif_workers=1, claimed_patch_paths=claimed)


def test_a_mod_left_with_no_loaded_plugin_plans_nothing(tmp_path, monkeypatch):
    """Not the plugin-less fallback that converts every mesh the folder ships:
    the mod has a plugin, the game just loads another copy of it."""
    mods = _modlist(tmp_path, monkeypatch, ["Hotfix", "Base"])
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    (mods / "Base" / "meshes" / "armor" / "q").mkdir(parents=True)
    (mods / "Base" / "meshes" / "armor" / "q" / "body_1.nif").write_bytes(b"x")
    calls = []
    r = _convert(mods / "Base", tmp_path / "out", tmp_path, monkeypatch, calls=calls)
    assert calls == []            # nothing resolved: not "every NIF it ships"
    assert r.source_esps == [] and r.output_esps == []
    assert any("not the copy the game loads" in n and "Quest.esp" in n
               for n in r.notes), r.notes


def test_a_later_source_never_overwrites_an_earlier_ones_patch(tmp_path, monkeypatch):
    """Outside a modlist nothing says which copy loads, so both are read; the
    batch's first (highest-priority) writer keeps the patch and its sidecar."""
    first = _plugin(tmp_path / "srcA" / "Quest.esp", "armor/q/a_1.nif")
    second = _plugin(tmp_path / "srcB" / "Quest.esp", "armor/q/b_1.nif")
    out = tmp_path / "out"
    claimed: set = set()
    ra = _convert(first.parent, out, tmp_path, monkeypatch, claimed=claimed)
    rb = _convert(second.parent, out, tmp_path, monkeypatch, claimed=claimed)
    assert [p.name for p in ra.output_esps] == ["Quest" + NEW]
    assert rb.output_esps == []
    assert any("already written this run" in n for n in rb.notes), rb.notes
    snap = json.loads((out / "_unmerged_patches" / ("Quest" + NEW + ".espgen.json"))
                      .read_text(encoding="utf-8"))
    assert Path(snap["source_esp"]) == first


def test_switched_off_the_later_source_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    first = _plugin(tmp_path / "srcA" / "Quest.esp", "armor/q/a_1.nif")
    second = _plugin(tmp_path / "srcB" / "Quest.esp", "armor/q/b_1.nif")
    out = tmp_path / "out"
    claimed: set = set()
    _convert(first.parent, out, tmp_path, monkeypatch, claimed=claimed)
    rb = _convert(second.parent, out, tmp_path, monkeypatch, claimed=claimed)
    assert [p.name for p in rb.output_esps] == ["Quest" + NEW]
    snap = json.loads((out / "_unmerged_patches" / ("Quest" + NEW + ".espgen.json"))
                      .read_text(encoding="utf-8"))
    assert Path(snap["source_esp"]) == second


def test_the_plugins_only_replay_keeps_the_first_writer_too(tmp_path, monkeypatch):
    first = _plugin(tmp_path / "srcA" / "Quest.esp", "armor/q/a_1.nif")
    second = _plugin(tmp_path / "srcB" / "Quest.esp", "armor/q/b_1.nif")
    out = tmp_path / "out"
    pdir = out / "_unmerged_patches"
    pdir.mkdir(parents=True)
    Path(str(pdir / ("Quest" + NEW)) + ".espgen.json").write_text(json.dumps({
        "source_esp": str(first), "converted_rel_paths": [],
        "body_mesh_rel_paths": []}), encoding="utf-8")
    mdd = [_masters(tmp_path / "data")]
    claimed: set = set()
    ra = ac.refresh_mod_esp(first.parent, out, master_data_dirs=mdd,
                            claimed_patch_paths=claimed)
    rb = ac.refresh_mod_esp(second.parent, out, master_data_dirs=mdd,
                            claimed_patch_paths=claimed)
    assert [p.name for p in ra.output_esps] == ["Quest" + NEW]
    assert rb.output_esps == []
    assert any("already written this run" in n for n in rb.notes), rb.notes
