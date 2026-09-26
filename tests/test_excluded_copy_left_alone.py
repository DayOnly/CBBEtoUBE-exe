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

"""#excluded-copy-left-alone -- a plugin the game loads from a mod the user
excluded is left alone, and so is every other mod's copy of it.

THE DEFECT. #loaded-copy-reader read a mod's losing copy of a plugin whenever the
winning copy's folder failed the selection's gate, and that gate refuses the
run's exclusions. Excluding the mod the game loads the plugin from (a hand-made
UBE refit, say) made a lower mod's copy a source again: its armour was converted
and patched from a copy the game does not load, while #exclude-owned-coverage in
the same run named the excluded mod the owner and withheld the coverage. And the
gate saw exclusions only through `auto --exclude-mods`: the window's Select mode
(`--coverage-exclude-mods`) and `convert --exclude-mods` judged the same list
the other way.

THE RULE. The exclusions the coverage step withholds count as handled: the mod's
copy is left out. Every entry point feeds that same set. Any other winning copy
is judged by the gate without an exclusion list; a body mod, overwrite, child
content and a non-source name still leave the mod's own copy read.
`CBBE2UBE_NO_EXCLUDED_COPY_LEFT_ALONE=1` judges by the selection's gate with its
exclusions again.
"""
import argparse

import pytest

from src import auto_convert as ac
from src import discovery, paths
from src.gui import _armor_selection_argv
from tests.test_loaded_source_plugins import _modlist, _plugin

OFF = "CBBE2UBE_NO_EXCLUDED_COPY_LEFT_ALONE"
REFIT = "Quest UBE Refit"
ODD = "Quest Fur Morph"           # a winner the name gate refuses


class _NoArchives:
    def __init__(self, *a, **k):
        pass

    def contains(self, rel):
        return False


class _Stop(BaseException):       # the entry points catch every Exception
    pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in (OFF, "CBBE2UBE_NO_LOADED_COPY_READER",
              "CBBE2UBE_NO_LOADED_SOURCE_PLUGINS"):
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


def _select(mods, order, exclude=()):
    return sorted(c["name"] for c in ac._find_armor_mod_dirs(
        mods, extra_exclude_names=set(exclude), enabled_names=set(order),
        require_arma=True, enabled_ordered=list(order)))


def _refit_modlist(tmp_path, monkeypatch):
    """The review's probe: the game loads Quest.esp from a hand-made UBE refit
    ranked above the base mod, whose copy points at the CBBE mesh."""
    order = [REFIT, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / REFIT / "Quest.esp", "!UBE/armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    return mods, order


def _owned(mods, excluded, loaded):
    return ac._armos_defined_by_mods(mods, excluded, loaded)[0]


# ------------------------------------------------ the user's exclusion is handled

def test_an_excluded_winner_leaves_every_copy_of_its_plugin_alone(tmp_path,
                                                                 monkeypatch):
    """Selection as `auto` runs it, the refit in its exclusions: the base mod's
    losing copy is not read, nothing is planned, and the coverage step names the
    refit the owner of the same armour in the same run."""
    mods, order = _refit_modlist(tmp_path, monkeypatch)
    assert _select(mods, order, exclude={REFIT}) == []
    assert ac._find_source_esps(mods / "Base") == []
    assert ac._player_armor_mesh_bases(mods / "Base") == set()
    assert _owned(mods, [REFIT], [mods / REFIT / "Quest.esp"]) == {("quest.esp", 0x801)}


def test_the_same_through_the_runs_exclusions(tmp_path, monkeypatch):
    mods, order = _refit_modlist(tmp_path, monkeypatch)
    ac._set_run_user_exclusions([REFIT])
    assert _select(mods, order, exclude={REFIT}) == []
    skipped = []
    assert ac._find_source_esps(mods / "Base", skipped=skipped) == []
    assert [(f"'{REFIT}'" in why) for _, why in skipped] == [True]


def test_an_excluded_winner_the_name_gate_refuses_is_left_alone_too(tmp_path,
                                                                    monkeypatch):
    """Coverage withholds an excluded mod's armour whatever its name: the
    exclusion decides before the name gate does."""
    order = [ODD, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / ODD / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    ac._set_run_user_exclusions([ODD])
    assert _select(mods, order, exclude={ODD}) == []
    assert ac._find_source_esps(mods / "Base") == []
    assert _owned(mods, [ODD], [mods / ODD / "Quest.esp"]) == {("quest.esp", 0x801)}


def test_switched_off_an_excluded_winner_makes_the_mods_copy_a_source_again(
        tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods, order = _refit_modlist(tmp_path, monkeypatch)
    ac._set_run_user_exclusions([REFIT])
    assert _select(mods, order, exclude={REFIT}) == ["Base"]
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]
    assert ac._player_armor_mesh_bases(mods / "Base") == {"armor/q/body"}


# ------------------------------------------------ what still reads the mod's copy

def test_an_overwrite_winner_is_still_read(tmp_path, monkeypatch):
    """No source reads overwrite, and no exclusion names it: the coverage step
    owns nothing there, and the mod's own copy is converted."""
    mods = _modlist(tmp_path, monkeypatch, ["Base"])
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    ow = _plugin(tmp_path / "inst" / "overwrite" / "Quest.esp", "armor/q/body_1.nif")
    ac._set_run_user_exclusions(["Other"])
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]
    assert ac._player_armor_mesh_bases(mods / "Base") == {"armor/q/body"}
    assert _owned(mods, ["Other", "overwrite"], [ow]) == set()


def test_a_body_mod_winner_keeps_the_mods_own_copy(tmp_path, monkeypatch):
    """A body mod is skipped by what it ships, not by the user: it is no source
    and the coverage step does not withhold it, so the mod's copy is read."""
    order = ["Body", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Body" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Body", "actors/character/character assets/femalebody_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order, exclude=ac._body_mod_names(mods)) == ["Base"]
    assert _names(ac._find_source_esps(mods / "Base")) == ["Quest.esp"]


def test_a_selection_sees_a_body_mod_installed_since(tmp_path, monkeypatch):
    order = ["Pack", "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / "Pack" / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order, exclude={"Pack"}) == []
    _mesh(mods / "Pack", "actors/character/character assets/femalebody_1.nif")
    assert _select(mods, order, exclude={"Pack", "Other"}) == ["Base"]


# ------------------------------------------------ the selection memo

def test_the_runs_exclusions_are_part_of_the_selection_memo(tmp_path, monkeypatch):
    order = [ODD, "Base"]
    mods = _modlist(tmp_path, monkeypatch, order)
    _plugin(mods / ODD / "Quest.esp", "armor/q/body_1.nif")
    _plugin(mods / "Base" / "Quest.esp", "armor/q/body_1.nif")
    _mesh(mods / "Base")
    assert _select(mods, order) == ["Base"]
    ac._set_run_user_exclusions([ODD])
    assert _select(mods, order) == []


def test_the_switch_is_part_of_the_selection_memo(tmp_path, monkeypatch):
    mods, order = _refit_modlist(tmp_path, monkeypatch)
    ac._set_run_user_exclusions([REFIT])
    assert _select(mods, order, exclude={REFIT}) == []
    monkeypatch.setenv(OFF, "1")
    assert _select(mods, order, exclude={REFIT}) == ["Base"]


# ------------------------------------------------ every entry point, one set

def _two_plugin_modlist(tmp_path, monkeypatch):
    """Base ships Quest.esp, which the game loads from a mod the name gate
    refuses, and Extra.esp, its own. Only an exclusion of that mod changes
    whether Base's Quest.esp is read."""
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
    """Which of Base's plugins `auto` reads when its selection starts."""
    def _stop(*a, **k):
        raise _Stop(_names(ac._find_source_esps(mods / "Base")))
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", _stop)
    args = ac._build_parser().parse_args(["auto", *argv])
    with pytest.raises(_Stop) as got:
        ac._cmd_auto(args)
    return got.value.args[0]


def test_both_window_modes_and_the_cli_read_the_same_copies(tmp_path, monkeypatch):
    """All mods passes the exclusion as --exclude-mods, Select mode as
    --coverage-exclude-mods: the loaded-copy decision is the same in both."""
    mods = _two_plugin_modlist(tmp_path, monkeypatch)
    assert _auto_reads(mods, monkeypatch, []) == ["Extra.esp", "Quest.esp"]
    all_mods = _armor_selection_argv(False, [], [ODD])
    select = _armor_selection_argv(True, ["Base"], [ODD])
    assert "--exclude-mods" in all_mods and "--coverage-exclude-mods" in select
    assert _auto_reads(mods, monkeypatch, all_mods) == ["Extra.esp"]
    assert _auto_reads(mods, monkeypatch, select) == ["Extra.esp"]


def test_a_standalone_convert_reads_the_same_copies(tmp_path, monkeypatch):
    """`convert --exclude-mods` runs no selection: its own exclusions judge the
    copies. Stops at the mesh index, which is asked for the planned keys."""
    mods = _two_plugin_modlist(tmp_path, monkeypatch)

    def _index(mods_root, enabled, target_keys=None, skip_mods=None, **kw):
        raise _Stop(set(target_keys or ()))
    monkeypatch.setattr(discovery, "build_mesh_index", _index)

    def _keys(exclude):
        ns = argparse.Namespace(
            sources=[mods / "Base"], output=tmp_path / "out", esp_name=None,
            no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
            unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
            merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
            mods_root=None, incremental=False, plugins_only=False,
            exclude_mods=exclude)
        ac._BATCH_MESH_INDEX.clear()          # no selection ran in this process
        with pytest.raises(_Stop) as got:
            ac._cmd_convert(ns)
        return got.value.args[0]
    keys = _keys(None)
    assert "armor/e/body_1.nif" in keys and "armor/q/body_1.nif" in keys
    keys = _keys([ODD])
    assert "armor/e/body_1.nif" in keys and "armor/q/body_1.nif" not in keys
    # The next run in the same process judges by its own exclusions only.
    assert "armor/q/body_1.nif" in _keys(None)
