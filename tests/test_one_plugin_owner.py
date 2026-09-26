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

"""#one-plugin-owner -- one mod owns each plugin, for the sources and for the
exclusions alike.

THE DEFECT. Which copy of a plugin the sources read and whose armour an
exclusion withholds were decided apart, and disagreed:
(a) an excluded mod whose copy sits under an overwrite copy: coverage withheld
    its armour as the excluded mod's, while a lower mod's copy was converted;
(b) an excluded mod whose copy LOSES to mod A: coverage withheld A's armour as
    the excluded mod's, while A was converted as usual;
(c) a body mod's plugin: `auto` converted it from a lower mod's copy, a
    standalone `convert` did not.

THE RULE. `_plugin_owner(name)` is the highest-priority enabled mod with a root
copy of the plugin (the mod the game loads it from; under an overwrite copy,
the mod that copy shadows). A mod's root copy is read only when that mod owns
it. Coverage withholds a plugin's armour only when its owner is excluded. An
owner the tool does not convert (a body mod, child clothing, a skipped name)
leaves the plugin unconverted -- no other copy stands in -- and the convert step
names it. `CBBE2UBE_NO_ONE_PLUGIN_OWNER=1` restores the two separate rules.
"""
import argparse

import pytest

from src import auto_convert as ac
from src import discovery, paths
from src.gui import _armor_selection_argv
from tests.test_loaded_source_plugins import _modlist, _plugin

OFF = "CBBE2UBE_NO_ONE_PLUGIN_OWNER"
REFIT = "Quest UBE Refit"
ODD = "Quest Fur Morph"           # an owner the name gate refuses
BODY_MESH = "actors/character/character assets/femalebody_1.nif"


class _NoArchives:
    def __init__(self, *a, **k):
        pass

    def contains(self, rel):
        return False


class _Stop(BaseException):       # the entry points catch every Exception
    pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in (OFF, "CBBE2UBE_NO_EXCLUDED_COPY_LEFT_ALONE",
              "CBBE2UBE_NO_LOADED_COPY_READER", "CBBE2UBE_NO_LOADED_SOURCE_PLUGINS",
              "CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(ac, "_BsaMeshIndex", _NoArchives)
    stores = (ac._LOADED_PLUGIN_INDEX, ac._SOURCE_GATE, ac._ARMOR_MOD_DIRS_CACHE,
              ac._ARMOR_MOD_DIRS_UNREADABLE, ac._BATCH_MESH_INDEX,
              ac._SELECTION_RUN_WARNINGS, ac._SELECTION_BSA_INDEX,
              ac._RUN_USER_EXCLUSIONS, ac._BODY_MODS_SEEN)
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


def _overwrite(tmp_path, mod3="armor/q/body_1.nif"):
    return _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", mod3)


def _run(mods, order, excluded=()):
    """One `auto` run as far as the plugins go: the user's exclusions recorded,
    the selection (body mods and the exclusions refused, as `auto` does), and
    the ownership the coverage step computes from the loaded plugins."""
    ac._set_run_user_exclusions(list(excluded))
    sel = sorted(c["name"] for c in ac._find_armor_mod_dirs(
        mods, extra_exclude_names=set(excluded) | ac._body_mod_names(mods),
        enabled_names=set(order), require_arma=True, enabled_ordered=list(order)))
    lay = paths.discover_layout()
    loaded = list(paths.plugin_file_index(lay).values())
    owned = ac._armos_defined_by_mods(mods, list(excluded), loaded)[0]
    return sel, owned


# ------------------------------------------------ (a) under an overwrite copy

def _refit_under_overwrite(tmp_path, monkeypatch):
    """The review's probe: the hand-made refit ships Quest.esp above the base
    mod, and a cleaned copy saved to overwrite is the one the game loads."""
    order = [REFIT, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / REFIT / "Quest.esp", "!UBE/armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    _overwrite(tmp_path)
    return mods, order


def test_an_excluded_owner_under_overwrite_is_left_alone_by_both(tmp_path,
                                                                  monkeypatch):
    """The refit owns Quest.esp, so excluding it withholds the armour AND no
    lower copy is converted in its place: the two passes agree."""
    mods, order = _refit_under_overwrite(tmp_path, monkeypatch)
    sel, owned = _run(mods, order, excluded=[REFIT])
    assert sel == [] and owned == {("quest.esp", 0x801)}
    skipped = []
    assert ac._find_source_esps(mods / "Base", skipped=skipped) == []
    assert ac._player_armor_mesh_bases(mods / "Base") == set()
    assert len(skipped) == 1 and f"'{REFIT}'" in skipped[0][1]
    assert "overwrite" in skipped[0][1] and "you excluded" in skipped[0][1]


def test_switched_off_the_lower_copy_is_converted_while_coverage_withholds(
        tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods, order = _refit_under_overwrite(tmp_path, monkeypatch)
    sel, owned = _run(mods, order, excluded=[REFIT])
    assert sel == ["Base"] and owned == {("quest.esp", 0x801)}
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]


def test_under_overwrite_the_owner_reads_its_own_copy_and_no_other(tmp_path,
                                                                    monkeypatch):
    """No exclusion: the highest mod with a copy owns it and reads its own copy
    (overwrite is no source); the lower mod's copy is not read, so one patch is
    made, from the owner's copy."""
    order = ["Top", "Low"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Top" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Low" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Top")
    _mesh(mods / "Low")
    _overwrite(tmp_path)
    sel, owned = _run(mods, order)
    assert sel == ["Top"] and owned == set()
    assert _names(ac._find_source_esps(mods / "Top")) == ["Quest.esp"]
    skipped = []
    assert ac._find_source_esps(mods / "Low", skipped=skipped) == []
    assert "'Top'" in skipped[0][1] and "overwrite" in skipped[0][1]
    assert ac._plugin_owner("Quest.esp", mods) == mods / "Top"


def test_a_single_copy_under_overwrite_is_read(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    _overwrite(tmp_path)
    sel, owned = _run(mods, ["Base"], excluded=["Other"])
    assert sel == ["Base"] and owned == set()
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]


def test_the_owners_copy_is_noted_when_overwrites_armour_differs(tmp_path,
                                                                 monkeypatch):
    """Full run and --plugins-only replay: the owner's copy is converted, and
    the log says the copy the game loads carries other armour."""
    from tests.test_loaded_source_plugins import _convert
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _overwrite(tmp_path, "armor/q/other_1.nif")
    calls = []
    r = _convert(mods / "Base", tmp_path / "out", tmp_path, monkeypatch, calls=calls)
    assert calls == [{"armor/q/body"}]
    assert any("Quest.esp" in n and "overwrite" in n and "other meshes" in n
               for n in r.notes), r.notes
    r = ac.refresh_mod_esp(mods / "Base", tmp_path / "out2",
                           master_data_dirs=[tmp_path / "data"])
    assert any("Quest.esp" in n and "other meshes" in n for n in r.notes), r.notes


def test_an_identical_overwrite_copy_is_not_noted(tmp_path, monkeypatch):
    from tests.test_loaded_source_plugins import _convert
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _overwrite(tmp_path)
    r = _convert(mods / "Base", tmp_path / "out", tmp_path, monkeypatch)
    assert not any("other meshes" in n for n in r.notes), r.notes


def test_an_excluded_mod_under_overwrite_owns_its_plugin(tmp_path, monkeypatch):
    """Coverage reads the overwrite copy (the records the game loads) and
    names the excluded mod that copy shadows its owner."""
    mods = _modlist(tmp_path, monkeypatch, [REFIT])
    _plugin(mods / REFIT / "Quest.esp", "!UBE/armor/q/body_1.nif")
    _overwrite(tmp_path)
    sel, owned = _run(mods, [REFIT], excluded=[REFIT])
    assert owned == {("quest.esp", 0x801)}


# ------------------------------------------------ (b) an excluded copy that loses

def _excluded_loser(tmp_path, monkeypatch):
    order = ["Armour", "Old"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Armour" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Old" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Armour")
    return mods, order


def test_an_excluded_losing_copy_does_not_take_the_winners_armour(tmp_path,
                                                                   monkeypatch):
    """Excluding the mod whose copy loses changes nothing about the winner: it
    is converted, and coverage withholds none of its armour."""
    mods, order = _excluded_loser(tmp_path, monkeypatch)
    sel, owned = _run(mods, order, excluded=["Old"])
    assert sel == ["Armour"] and owned == set()
    assert _names(ac._find_source_esps(mods / "Armour")) == ["Quest.esp"]
    assert ac._player_armor_mesh_bases(mods / "Armour") == {"armor/q/body"}


def test_switched_off_the_losing_copy_withholds_the_winners_armour(tmp_path,
                                                                    monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods, order = _excluded_loser(tmp_path, monkeypatch)
    sel, owned = _run(mods, order, excluded=["Old"])
    assert sel == ["Armour"] and owned == {("quest.esp", 0x801)}


def test_an_earlier_steps_switch_turns_the_owner_off_too(tmp_path, monkeypatch):
    """Each earlier switch of the chain restores its own parent in both passes;
    coverage never used the owner before this step."""
    monkeypatch.setenv("CBBE2UBE_NO_EXCLUDED_COPY_LEFT_ALONE", "1")
    mods, order = _excluded_loser(tmp_path, monkeypatch)
    assert _run(mods, order, excluded=["Old"])[1] == {("quest.esp", 0x801)}


# ------------------------------------------------ (c) an owner that is no source

def _body_winner(tmp_path, monkeypatch):
    order = ["Body", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Body" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Body", BODY_MESH)
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    return mods, order


def test_a_body_mods_plugin_is_converted_from_no_copy_in_any_entry_point(
        tmp_path, monkeypatch):
    """Standalone (no selection) and `auto` agree: the game loads the body
    mod's records, so no lower copy is converted, and the reason says so."""
    mods, order = _body_winner(tmp_path, monkeypatch)
    skipped = []
    assert ac._find_source_esps(mods / "Base", skipped=skipped) == []
    assert "'Body'" in skipped[0][1] and "a body mod" in skipped[0][1]
    sel, owned = _run(mods, order)
    assert sel == [] and owned == set()
    assert ac._find_source_esps(mods / "Base") == []


def test_switched_off_auto_reads_the_lower_copy_of_a_body_mods_plugin(tmp_path,
                                                                       monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods, order = _body_winner(tmp_path, monkeypatch)
    assert _run(mods, order)[0] == ["Base"]
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]


def test_the_plugins_no_source_owns_are_named(tmp_path, monkeypatch):
    mods, order = _body_winner(tmp_path, monkeypatch)
    lines = ac._plugins_no_source_owns()
    assert len(lines) == 1
    assert "Quest.esp" in lines[0] and "'Body'" in lines[0] and "'Base'" in lines[0]
    assert "a body mod" in lines[0]


def test_an_excluded_owner_is_not_in_that_list(tmp_path, monkeypatch):
    """Coverage reports what an exclusion withholds; this list is for the
    plugins nobody asked to leave alone."""
    order = [ODD, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / ODD / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    assert [("a mod skipped by its name" in ln) for ln in
            ac._plugins_no_source_owns()] == [True]
    ac._set_run_user_exclusions([ODD])
    assert ac._plugins_no_source_owns() == []


def test_a_mod_left_with_no_plugin_says_why(tmp_path, monkeypatch):
    from tests.test_loaded_source_plugins import _convert
    mods, order = _body_winner(tmp_path, monkeypatch)
    calls = []
    r = _convert(mods / "Base", tmp_path / "out", tmp_path, monkeypatch, calls=calls)
    assert calls == [] and r.source_esps == []
    assert any("Quest.esp" in n and "a body mod" in n for n in r.notes), r.notes


# ------------------------------------------------ a legit duplicate

def test_the_legit_duplicate_is_read_once_from_its_owner(tmp_path, monkeypatch):
    """A hotfix ships the plugin above the base mod that ships the meshes: the
    hotfix is the source and reads its copy (the mesh is found in the base
    mod); the base mod's copy is not read, and nothing is withheld."""
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    sel, owned = _run(mods, order)
    assert sel == ["Hotfix"] and owned == set()
    assert _names(ac._find_source_esps(mods / "Hotfix")) == ["Quest.esp"]
    assert ac._find_source_esps(mods / "Base") == []
    assert ac._plugins_no_source_owns() == []


# ------------------------------------------------ the reviewers' probes

def test_probe_excluded_refit_winner(tmp_path, monkeypatch):
    """The refit wins outright and the selection refuses it by name only (no
    run exclusions recorded): still nothing from the base mod."""
    order = [REFIT, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / REFIT / "Quest.esp", "!UBE/armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    sel = sorted(c["name"] for c in ac._find_armor_mod_dirs(
        mods, extra_exclude_names={REFIT}, enabled_names=set(order),
        require_arma=True, enabled_ordered=list(order)))
    assert sel == [] and ac._find_source_esps(mods / "Base") == []
    assert ac._player_armor_mesh_bases(mods / "Base") == set()
    owned = ac._armos_defined_by_mods(mods, [REFIT], [mods / REFIT / "Quest.esp"])[0]
    assert owned == {("quest.esp", 0x801)}


def test_probe_standalone_then_selection_excluding_the_owner(tmp_path, monkeypatch):
    order = ["Hotfix", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Hotfix" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert ac._find_source_esps(mods / "Base") == []
    ac._find_armor_mod_dirs(mods, extra_exclude_names={"Hotfix"},
                            enabled_names=set(order), require_arma=True,
                            enabled_ordered=list(order))
    ac._LOADED_PLUGIN_INDEX.clear()
    assert ac._find_source_esps(mods / "Base") == []


# ------------------------------------------------ every entry point, one owner

def _two_plugin_modlist(tmp_path, monkeypatch):
    order = [ODD, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / ODD / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Extra.esp", "armor/e/body_1.nif")
    _mesh(mods / "Base")
    _mesh(mods / "Base", "armor/e/body_1.nif")
    monkeypatch.setenv(paths.MODS_ROOT_ENV, str(mods))
    monkeypatch.delenv(paths.GAME_DATA_ENV, raising=False)
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    return mods


def _auto_reads(mods, monkeypatch, argv):
    def _stop(*a, **k):
        raise _Stop(_names(ac._find_source_esps(mods / "Base")))
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", _stop)
    args = ac._build_parser().parse_args(["auto", *argv])
    with pytest.raises(_Stop) as got:
        ac._cmd_auto(args)
    return got.value.args[0]


def _convert_keys(mods, tmp_path, monkeypatch, exclude):
    """A standalone `convert Base`, stopped at the mesh index, which is asked
    for the planned keys."""
    def _index(mods_root, enabled, target_keys=None, skip_mods=None, **kw):
        raise _Stop(set(target_keys or ()))
    monkeypatch.setattr(discovery, "build_mesh_index", _index)
    ns = argparse.Namespace(
        sources=[mods / "Base"], output=tmp_path / "out", esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, incremental=False, plugins_only=False,
        exclude_mods=exclude)
    ac._BATCH_MESH_INDEX.clear()
    with pytest.raises(_Stop) as got:
        ac._cmd_convert(ns)
    return got.value.args[0]


def test_every_entry_point_reads_the_same_copies(tmp_path, monkeypatch):
    """All mods, Select mods and a standalone `convert`, with and without the
    exclusion: the owner is the same, so the same copies are read."""
    mods = _two_plugin_modlist(tmp_path, monkeypatch)
    assert _auto_reads(mods, monkeypatch, []) == ["Extra.esp"]
    assert _auto_reads(mods, monkeypatch,
                       _armor_selection_argv(False, [], [ODD])) == ["Extra.esp"]
    assert _auto_reads(mods, monkeypatch,
                       _armor_selection_argv(True, ["Base"], [ODD])) == ["Extra.esp"]
    for excl in (None, [ODD]):
        keys = _convert_keys(mods, tmp_path, monkeypatch, excl)
        assert "armor/e/body_1.nif" in keys and "armor/q/body_1.nif" not in keys


def test_the_convert_step_names_the_plugins_no_source_owns(tmp_path, monkeypatch,
                                                           capsys):
    mods = _two_plugin_modlist(tmp_path, monkeypatch)
    _convert_keys(mods, tmp_path, monkeypatch, None)
    out = capsys.readouterr().out
    assert "plugins not converted from another mod's copy: 1" in out
    assert f"Quest.esp: owned by '{ODD}'" in out


# ------------------------------------------------ the selection memo

def test_the_switch_is_part_of_the_selection_memo(tmp_path, monkeypatch):
    mods, order = _body_winner(tmp_path, monkeypatch)
    assert _run(mods, order)[0] == []
    monkeypatch.setenv(OFF, "1")
    assert _run(mods, order)[0] == ["Base"]
