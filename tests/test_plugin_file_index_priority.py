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

"""Guard for plugin_file_index MO2-priority resolution (#plugin-priority).

Resolving a plugin filename to a physical file is a mod-PRIORITY question: when
two mods ship the same plugin filename (a mod + its patch as separate MO2 mods),
the winner scan must read the highest-priority mod's copy. Arbitrary os.walk
(alphabetical) order could pick the loser -> wrong ARMO records (slots, models,
alt-textures) feed the winner index.
"""
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import paths


def _mk(p: Path, data=b"TES4"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_duplicate_plugin_resolves_to_higher_priority_mod(tmp_path):
    inst = tmp_path / "inst"
    mods = inst / "mods"
    # Both mods ship Foo.esp; ModHigh is higher MO2 priority (listed first).
    _mk(mods / "ModHigh" / "Foo.esp")
    _mk(mods / "ModLow" / "Foo.esp")
    _mk(mods / "ModLow" / "Bar.esp")           # unique -> unambiguous
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True, exist_ok=True)
    # MO2 modlist.txt: top line = highest priority = wins.
    (prof / "modlist.txt").write_text("+ModHigh\n+ModLow\n", encoding="utf-8")

    lay = paths.Layout(mods_root=mods, instance_dir=inst,
                       selected_profile="Default")
    idx = paths.plugin_file_index(lay)
    assert idx["foo.esp"] == mods / "ModHigh" / "Foo.esp", \
        "duplicate plugin must resolve to the higher-priority mod"
    assert idx["bar.esp"] == mods / "ModLow" / "Bar.esp"


def test_priority_flips_with_modlist_order(tmp_path):
    # Same layout, reversed priority -> the OTHER copy wins. Proves it's the
    # modlist order, not filesystem/alphabetical, that decides.
    inst = tmp_path / "inst"
    mods = inst / "mods"
    _mk(mods / "ModHigh" / "Foo.esp")
    _mk(mods / "ModLow" / "Foo.esp")
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "modlist.txt").write_text("+ModLow\n+ModHigh\n", encoding="utf-8")
    lay = paths.Layout(mods_root=mods, instance_dir=inst,
                       selected_profile="Default")
    idx = paths.plugin_file_index(lay)
    assert idx["foo.esp"] == mods / "ModLow" / "Foo.esp"


def test_root_level_plugin_still_indexed(tmp_path, monkeypatch):
    # The LEGACY walk (switched off): a plugin sitting DIRECTLY in the mods
    # root (no mod folder) was indexed by the old os.walk, and so was one in a
    # mod's subfolder.
    monkeypatch.setenv(OFF, "1")
    mods = tmp_path / "mods"
    _mk(mods / "Loose.esp")
    _mk(mods / "ModA" / "InMod.esp")
    _mk(mods / "ModA" / "optional" / "Sub.esp")
    lay = paths.Layout(mods_root=mods)
    idx = paths.plugin_file_index(lay)
    assert idx["loose.esp"] == mods / "Loose.esp"
    assert idx["inmod.esp"] == mods / "ModA" / "InMod.esp"
    assert idx["sub.esp"] == mods / "ModA" / "optional" / "Sub.esp"


# ---------------------------------------------------------------------------
# #root-plugin-index (2026-09-25): only what the game loads -- ROOT plugin
# files of overwrite, enabled mods (MO2 priority) and Data. The legacy walk read
# our own un-loaded `_unmerged_patches` copy of a name in place of the third-
# party plugin the game loads (22 names on a real modlist).
# ---------------------------------------------------------------------------
OFF = "CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _instance(tmp_path, modlist):
    inst = tmp_path / "inst"
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "modlist.txt").write_text("".join(l + "\n" for l in modlist),
                                      encoding="utf-8")
    return inst, inst / "mods"


def test_a_plugin_in_a_mod_subfolder_is_not_indexed(tmp_path):
    """RPI-a / RPI-i: our output keeps its per-source patches in a subfolder
    the game never loads; the same name at another mod's root is the plugin
    the game loads, and the one the winner scan must read -- even when our
    output has the higher priority."""
    inst, mods = _instance(tmp_path, ["+Our Output", "+Their UBE Patch"])
    _mk(mods / "Our Output" / "_unmerged_patches" / "X UBE patch.esp")
    _mk(mods / "Our Output" / "Combined.esp")
    _mk(mods / "Their UBE Patch" / "X UBE patch.esp")
    _mk(mods / "Their UBE Patch" / "fomod" / "Only In Fomod.esp")
    idx = paths.plugin_file_index(paths.Layout(
        mods_root=mods, instance_dir=inst, selected_profile="Default"))
    assert idx["x ube patch.esp"] == mods / "Their UBE Patch" / "X UBE patch.esp"
    assert idx["combined.esp"] == mods / "Our Output" / "Combined.esp"
    assert "only in fomod.esp" not in idx


def test_overwrite_beats_a_mod_and_a_mod_beats_data(tmp_path):
    """RPI-b: MO2's resolution order, whatever the folder names."""
    inst, mods = _instance(tmp_path, ["+ModHigh", "+ModLow"])
    data = tmp_path / "Game" / "Data"
    ow = inst / "overwrite"
    for p in (data / "A.esp", data / "B.esp", data / "C.esp",
              mods / "ModLow" / "A.esp", mods / "ModLow" / "B.esp",
              mods / "ModHigh" / "A.esp", ow / "C.esp"):
        _mk(p)
    lay = paths.Layout(mods_root=mods, instance_dir=inst, selected_profile="Default",
                       game_data_dirs=[data], overwrite_dir=ow)
    idx = paths.plugin_file_index(lay)
    assert idx["a.esp"] == mods / "ModHigh" / "A.esp"
    assert idx["b.esp"] == mods / "ModLow" / "B.esp"
    assert idx["c.esp"] == ow / "C.esp"
    # Insertion order is priority order, highest first.
    assert list(idx) == ["c.esp", "a.esp", "b.esp"]


def test_a_disabled_mods_plugin_is_not_indexed(tmp_path):
    """RPI-c: a disabled or unlisted mod's plugin is not loaded by the game."""
    inst, mods = _instance(tmp_path, ["+On", "-Off"])
    _mk(mods / "On" / "On.esp")
    _mk(mods / "Off" / "Off.esp")
    _mk(mods / "Unlisted" / "Unlisted.esp")
    idx = paths.plugin_file_index(paths.Layout(
        mods_root=mods, instance_dir=inst, selected_profile="Default"))
    assert set(idx) == {"on.esp"}


def test_a_plugin_loose_in_the_mods_folder_is_not_indexed(tmp_path):
    """RPI-d: the inverse of the legacy parity kept above."""
    mods = tmp_path / "mods"
    _mk(mods / "Loose.esp")
    _mk(mods / "ModA" / "InMod.esp")
    idx = paths.plugin_file_index(paths.Layout(mods_root=mods))
    assert "loose.esp" not in idx
    assert idx["inmod.esp"] == mods / "ModA" / "InMod.esp"


def test_no_modlist_indexes_every_mod_root_in_name_order(tmp_path):
    """RPI-e: no readable modlist -- every mod folder's ROOT, sorted by name
    (the first name wins a duplicate), and still no subfolder."""
    mods = tmp_path / "mods"
    _mk(mods / "B Mod" / "Dup.esp")
    _mk(mods / "A Mod" / "Dup.esp")
    _mk(mods / "A Mod" / "Only.esm")
    _mk(mods / "A Mod" / "esp" / "Deeper.esp")
    idx = paths.plugin_file_index(paths.Layout(mods_root=mods))
    assert idx["dup.esp"] == mods / "A Mod" / "Dup.esp"
    assert idx["only.esm"] == mods / "A Mod" / "Only.esm"
    assert "deeper.esp" not in idx


def test_switched_off_the_recursive_walk_is_back(tmp_path, monkeypatch):
    """RPI-f: the switch restores the legacy walk for every caller -- the
    subfolder copy, the disabled mod and the loose file are indexed again."""
    monkeypatch.setenv(OFF, "1")
    assert paths.root_plugin_index_on() is False
    inst, mods = _instance(tmp_path, ["+Our Output", "+Their UBE Patch", "-Off"])
    _mk(mods / "Our Output" / "_unmerged_patches" / "X UBE patch.esp")
    _mk(mods / "Their UBE Patch" / "X UBE patch.esp")
    _mk(mods / "Off" / "Off.esp")
    idx = paths.plugin_file_index(paths.Layout(
        mods_root=mods, instance_dir=inst, selected_profile="Default"))
    assert idx["x ube patch.esp"] == (mods / "Our Output" / "_unmerged_patches"
                                      / "X UBE patch.esp")
    assert idx["off.esp"] == mods / "Off" / "Off.esp"
    monkeypatch.setenv(OFF, "0")
    assert paths.root_plugin_index_on() is True


def test_no_modlist_still_indexes_all(tmp_path):
    # No instance_dir/modlist -> falls back to a deterministic sorted walk but
    # still finds every plugin (no regression when priority info is absent).
    mods = tmp_path / "mods"
    _mk(mods / "ModX" / "Baz.esp")
    _mk(mods / "ModY" / "Qux.esm")
    lay = paths.Layout(mods_root=mods)
    idx = paths.plugin_file_index(lay)
    assert idx["baz.esp"] == mods / "ModX" / "Baz.esp"
    assert idx["qux.esm"] == mods / "ModY" / "Qux.esm"
