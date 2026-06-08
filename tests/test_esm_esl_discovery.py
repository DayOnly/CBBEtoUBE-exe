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

"""Guard for the .esm/.esl source-discovery fix (#179) + the --only-mods
incremental filter.

#179: `_find_source_esps` used to glob `*.esp` ONLY, so bespoke-armour content
mods shipped as a master (Vigilant.esm, Legacy of the Dragonborn.esm,
Unslaad.esm, Glenmoril.esm) were never discovered -> never converted -> invisible
on UBE actors. The fix globs .esp + .esm + .esl while excluding vanilla/DLC/CC
masters (handled by the vanilla-compat path) and our own outputs.
"""
import sys
import types
import argparse
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

import src.auto_convert as ac
from src.auto_convert import _find_source_esps, _has_any_source_plugin


def _touch(p: Path, data=b"TES4"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_finds_esm_esl_and_excludes_masters(tmp_path):
    mod = tmp_path / "VigilantMod"
    _touch(mod / "Vigilant.esm")              # bespoke-armour .esm -> FOUND (#179)
    _touch(mod / "MyArmor.esp")               # normal .esp        -> FOUND
    _touch(mod / "Cool.esl")                  # .esl               -> FOUND
    _touch(mod / "Skyrim.esm")                # vanilla master     -> skipped
    _touch(mod / "Dawnguard.esm")             # vanilla DLC master -> skipped
    _touch(mod / "ccBGSSSE001-Fish.esm")      # Creation Club      -> skipped
    _touch(mod / "_ResourcePack.esl")         # CC resource pack   -> skipped

    found = {p.name for p in _find_source_esps(mod)}
    assert {"Vigilant.esm", "MyArmor.esp", "Cool.esl"} <= found
    assert "Skyrim.esm" not in found
    assert "Dawnguard.esm" not in found
    assert "ccBGSSSE001-Fish.esm" not in found
    assert "_ResourcePack.esl" not in found


def test_excludes_outputs_and_buried_plugins(tmp_path):
    # NB: keep "ube" OUT of this test's name -- pytest derives tmp_path from it,
    # and _find_source_esps excludes any path PART containing "ube" (so it skips
    # the converter's own outputs); a tmp dir named "...ube..." would falsely
    # exclude everything and mask the real assertion.
    mod = tmp_path / "X"
    _touch(mod / "Real.esm")                       # real source -> found
    _touch(mod / "Foo_UBE patch.esp")              # "ube" in name -> skipped
    _touch(mod / "meshes" / "armor" / "buried.esp")  # under meshes\ -> skipped
    found = {p.name for p in _find_source_esps(mod)}
    assert found == {"Real.esm"}


class _Stop(Exception):
    """Sentinel to halt _cmd_auto right after the convert call in the test."""


def test_only_mods_filters_sources_and_skips_vanilla(monkeypatch, tmp_path):
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda: types.SimpleNamespace(game_data_dirs=[]))
    monkeypatch.setattr(ac.paths, "export_to_env", lambda lay: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: tmp_path)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: [])
    monkeypatch.setattr(ac.nif_convert, "_find_cbbe_base_body", lambda w: None)
    monkeypatch.setattr(ac.nif_convert, "_find_ube_femalebody", lambda w: None)
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda: None)
    fake = [
        {"name": "ModA", "path": tmp_path / "ModA", "armor_nifs": 3, "esps": 1},
        {"name": "ModB", "path": tmp_path / "ModB", "armor_nifs": 2, "esps": 1},
        {"name": "ModC", "path": tmp_path / "ModC", "armor_nifs": 1, "esps": 1},
    ]
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: list(fake))

    captured = {}

    def fake_convert(conv):
        captured["sources"] = list(conv.sources)
        raise _Stop()

    monkeypatch.setattr(ac, "_cmd_convert", fake_convert)

    args = argparse.Namespace(
        output=tmp_path / "out", workers=1, no_textures=False,
        merged_name="C.esp", no_vanilla_compat=False, no_vanilla_bodies=False,
        list_only=False, only_mods=["ModA", "ModC"], force_vanilla=False)
    try:
        ac._cmd_auto(args)
    except _Stop:
        pass

    names = {Path(s).name for s in captured["sources"]}
    assert names == {"ModA", "ModC"}             # only the selected subset
    assert args.no_vanilla_compat is True        # incremental auto-skips vanilla
    assert args.no_vanilla_bodies is True


def test_only_mods_comma_split_and_force_vanilla(monkeypatch, tmp_path):
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda: types.SimpleNamespace(game_data_dirs=[]))
    monkeypatch.setattr(ac.paths, "export_to_env", lambda lay: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: tmp_path)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: [])
    monkeypatch.setattr(ac.nif_convert, "_find_cbbe_base_body", lambda w: None)
    monkeypatch.setattr(ac.nif_convert, "_find_ube_femalebody", lambda w: None)
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda: None)
    fake = [{"name": "ModA", "path": tmp_path / "ModA", "armor_nifs": 1, "esps": 1},
            {"name": "ModB", "path": tmp_path / "ModB", "armor_nifs": 1, "esps": 1}]
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: list(fake))

    captured = {}

    def fake_convert(conv):
        captured["sources"] = list(conv.sources)
        raise _Stop()

    monkeypatch.setattr(ac, "_cmd_convert", fake_convert)

    # one flag, comma-separated value (case-insensitive) + --force-vanilla
    args = argparse.Namespace(
        output=tmp_path / "out", workers=1, no_textures=False,
        merged_name="C.esp", no_vanilla_compat=False, no_vanilla_bodies=False,
        list_only=False, only_mods=["moda,modb"], force_vanilla=True)
    try:
        ac._cmd_auto(args)
    except _Stop:
        pass

    assert {Path(s).name for s in captured["sources"]} == {"ModA", "ModB"}
    assert args.no_vanilla_compat is False       # --force-vanilla keeps vanilla
    assert args.no_vanilla_bodies is False


def test_has_any_source_plugin(tmp_path):
    a = tmp_path / "A"
    _touch(a / "Mod.esm")
    assert _has_any_source_plugin(a) is True            # root .esm -> found
    b = tmp_path / "B"
    _touch(b / "textures" / "x.dds")
    _touch(b / "meshes" / "y.nif")
    assert _has_any_source_plugin(b) is False           # no plugin anywhere
    c = tmp_path / "C"
    _touch(c / "meshes" / "armor" / "buried.esp")       # under meshes\ -> pruned
    assert _has_any_source_plugin(c) is False
    d = tmp_path / "D"
    _touch(d / "optional" / "Extra.esl")                # nested non-asset -> found
    assert _has_any_source_plugin(d) is True


def test_find_armor_mod_dirs_memoized(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_uncached(mods_root, **kw):
        calls["n"] += 1
        # deliberately unsorted so we can prove a caller's in-place sort can't
        # corrupt the cached order
        return [{"name": "ModB", "path": tmp_path / "ModB", "armor_nifs": 1, "esps": 1},
                {"name": "ModA", "path": tmp_path / "ModA", "armor_nifs": 1, "esps": 1}]

    monkeypatch.setattr(ac, "_find_armor_mod_dirs_uncached", fake_uncached)
    ac._ARMOR_MOD_DIRS_CACHE.clear()
    try:
        r1 = ac._find_armor_mod_dirs(tmp_path, require_arma=True)
        r2 = ac._find_armor_mod_dirs(tmp_path, require_arma=True)
        assert calls["n"] == 1                       # heavy scan ran ONCE (memoized)
        assert r1 == r2 and r1 is not r2             # equal value, independent lists
        assert [c["name"] for c in r1] == ["ModB", "ModA"]
        r1.sort(key=lambda c: c["name"])             # mutate a returned list...
        r3 = ac._find_armor_mod_dirs(tmp_path, require_arma=True)
        assert [c["name"] for c in r3] == ["ModB", "ModA"]   # ...cache order intact
        assert calls["n"] == 1
        # different inputs -> separate cache entry -> one more scan
        ac._find_armor_mod_dirs(tmp_path, require_arma=False)
        assert calls["n"] == 2
    finally:
        ac._ARMOR_MOD_DIRS_CACHE.clear()             # don't leak into other tests


class _FakeBSA:
    """Stand-in for bsa_strings.BSAArchive: one armour nif + a texture."""
    def __init__(self, path, eager=True):
        self.path = path

    def list_files(self):
        return ["meshes\\armor\\v\\robe_1.nif", "textures\\v\\robe.dds"]

    def read_file(self, name):
        return b"NIFDATA" if name.lower().endswith(".nif") else None


def test_bsa_mesh_index_extract(monkeypatch, tmp_path):
    import src.bsa_strings as bs
    monkeypatch.setattr(bs, "BSAArchive", _FakeBSA)
    mod = tmp_path / "VigilantSE"
    mod.mkdir()
    (mod / "Vigilant.bsa").write_bytes(b"BSA")          # mesh BSA -> scanned
    (mod / "Vigilant - Textures.bsa").write_bytes(b"BSA")  # texture BSA -> SKIPPED
    idx = ac._BsaMeshIndex([mod], tmp_path / "_stg")
    res = idx.extract("armor/v/robe_1.nif")             # strip meshes/ + lower
    assert res is not None
    p, rel = res
    assert p.is_file() and p.read_bytes() == b"NIFDATA"
    assert rel == "armor/v/robe_1.nif"
    assert idx.extract("armor/v/missing.nif") is None   # genuine miss -> None


def test_resolve_armor_meshes_bsa_fallback(monkeypatch, tmp_path):
    # No loose file + no VFS index -> the mesh is pulled from the BSA fallback.
    import src.bsa_strings as bs
    monkeypatch.setattr(bs, "BSAArchive", _FakeBSA)
    mod = tmp_path / "V"
    mod.mkdir()
    (mod / "V.bsa").write_bytes(b"BSA")
    idx = ac._BsaMeshIndex([mod], tmp_path / "_stg")
    monkeypatch.setattr(ac, "_BATCH_BSA_INDEX", idx)
    pairs = ac._resolve_armor_meshes({"armor/v/robe"}, None, None, [])
    assert len(pairs) == 1
    p, rel = pairs[0]
    assert rel == "armor/v/robe_1.nif" and p.read_bytes() == b"NIFDATA"
    # loose ALWAYS wins over the BSA fallback
    loose = tmp_path / "meshes" / "armor" / "v" / "robe_1.nif"
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_bytes(b"LOOSE")
    pairs2 = ac._resolve_armor_meshes(
        {"armor/v/robe"}, None, tmp_path / "meshes", [loose])
    assert pairs2[0][0].read_bytes() == b"LOOSE"        # loose beats BSA
