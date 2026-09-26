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

"""#loaded-copy-reader -- a mod's plugin is left out only when a source reads the
copy the game loads.

THE DEFECT. #loaded-source-plugins left a mod's root plugin out whenever the game
loads that name from another folder. The index it asks ranks MO2's overwrite
folder first and holds every enabled mod, so the winning copy could sit where no
source reads it: overwrite, a mod excluded from the run, or one the selection's
name gate refuses. No source read the plugin at all, and its armour was no longer
planned, converted or patched.

THE RULE. The mod's copy is left out only when the game's copy is in a mod folder
the selection's gate admits (so that source reads it); otherwise the mod's own
copy is read, and the convert step notes when the two copies' armatures differ.
`CBBE2UBE_NO_LOADED_COPY_READER=1` leaves it out in every case again.
"""
import pytest

from src import auto_convert as ac
from tests.test_loaded_source_plugins import _convert, _modlist, _plugin

OFF = "CBBE2UBE_NO_LOADED_COPY_READER"


class _NoArchives:
    def __init__(self, *a, **k):
        pass

    def contains(self, rel):
        return False


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in (OFF, "CBBE2UBE_NO_LOADED_SOURCE_PLUGINS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(ac, "_BsaMeshIndex", _NoArchives)
    stores = (ac._LOADED_PLUGIN_INDEX, ac._SOURCE_GATE, ac._ARMOR_MOD_DIRS_CACHE,
              ac._ARMOR_MOD_DIRS_UNREADABLE, ac._BATCH_MESH_INDEX,
              ac._SELECTION_RUN_WARNINGS, ac._SELECTION_BSA_INDEX)
    for d in stores:
        d.clear()
    yield
    for d in stores:
        d.clear()


def _mesh(mod_dir, rel="armor/q/body_1.nif"):
    p = mod_dir / "meshes" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _names(ps):
    return [p.name for p in ps]


def _select(mods, order, exclude=()):
    return sorted(c["name"] for c in ac._find_armor_mod_dirs(
        mods, extra_exclude_names=set(exclude), enabled_names=set(order),
        require_arma=True, enabled_ordered=list(order)))


# ---------------------------------------------------------------- overwrite

def test_a_copy_in_overwrite_keeps_the_mods_own_copy(tmp_path, monkeypatch):
    """Overwrite is never a source: the mod's copy is the only one read."""
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", "armor/q/body_1.nif")
    skipped = []
    assert _names(ac._find_source_esps(mods / "Base", skipped=skipped)) == ["Quest.esp"]
    assert skipped == []
    assert ac._player_armor_mesh_bases(mods / "Base") == {"armor/q/body"}


def test_switched_off_a_copy_in_overwrite_drops_the_mods_copy(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", "armor/q/body_1.nif")
    skipped = []
    assert ac._find_source_esps(mods / "Base", skipped=skipped) == []
    assert [("overwrite" in why) for _, why in skipped] == [True]
    assert ac._player_armor_mesh_bases(mods / "Base") == set()


# ---------------------------------------------------------------- the run's gate

def test_a_copy_in_an_excluded_mod_keeps_the_mods_own_copy(tmp_path, monkeypatch):
    """The user excluded the mod the game loads the plugin from: the base mod
    is still a source, and the convert step that follows reads its copy."""
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order, exclude={"Hotfix"}) == ["Base"]
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]
    assert ac._player_armor_mesh_bases(mods / "Base") == {"armor/q/body"}


def test_the_legit_duplicate_is_read_by_the_mod_the_game_loads_it_from(
        tmp_path, monkeypatch):
    """Control: the same modlist without the exclusion. The hotfix is a source
    and reads the game's copy (its mesh is found in the base mod); the base
    mod's copy is left out, so the patch is written once, from the copy the
    game loads."""
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order) == ["Hotfix"]
    skipped = []
    assert ac._find_source_esps(mods / "Base", skipped=skipped) == []
    assert [("'Hotfix'" in why) for _, why in skipped] == [True]
    assert ac._player_armor_mesh_bases(mods / "Hotfix") == {"armor/q/body"}


def test_a_copy_in_a_mod_the_name_gate_refuses_keeps_the_mods_own_copy(
        tmp_path, monkeypatch):
    order = ["Quest Fur Morph", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Quest Fur Morph" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order) == ["Base"]
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]


def test_switched_off_an_excluded_mods_copy_drops_the_mods_copy(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order, exclude={"Hotfix"}) == []
    assert ac._find_source_esps(mods / "Base") == []


# ---------------------------------------------------------------- the note

def test_the_convert_step_says_when_the_unread_loaded_copy_differs(tmp_path,
                                                                   monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", "armor/q/other_1.nif")
    calls = []
    r = _convert(mods / "Base", tmp_path / "out", tmp_path, monkeypatch, calls=calls)
    assert calls == [{"armor/q/body"}], "the mod's own copy is planned"
    assert any("Quest.esp" in n and "overwrite" in n and "other meshes" in n
               for n in r.notes), r.notes


def test_an_identical_unread_loaded_copy_is_not_noted(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", "armor/q/body_1.nif")
    r = _convert(mods / "Base", tmp_path / "out", tmp_path, monkeypatch)
    assert not any("other meshes" in n for n in r.notes), r.notes


def test_the_plugins_only_replay_says_it_too(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", "armor/q/other_1.nif")
    r = ac.refresh_mod_esp(mods / "Base", tmp_path / "out",
                           master_data_dirs=[tmp_path / "data"])
    assert any("Quest.esp" in n and "other meshes" in n for n in r.notes), r.notes


# ---------------------------------------------------------------- the memo

def test_a_reused_selection_sets_its_gate_again(tmp_path, monkeypatch):
    """A GUI refresh without the exclusion, then the convert's selection with
    it again (a memo hit): the convert step must judge by the exclusion."""
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order, exclude={"Hotfix"}) == ["Base"]
    assert _select(mods, order) == ["Hotfix"]
    assert _select(mods, order, exclude={"Hotfix"}) == ["Base"]
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]


def test_the_switch_is_part_of_the_selection_memo(tmp_path, monkeypatch):
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order, exclude={"Hotfix"}) == ["Base"]
    monkeypatch.setenv(OFF, "1")
    assert _select(mods, order, exclude={"Hotfix"}) == []
