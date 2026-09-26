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

r"""#vfs-index-fail-loud, second round -- what the walk indexes, and how a
selection with an unreadable folder is kept.

1. The walk indexes FILES. `Path.rglob('*.nif')` also yielded a FOLDER named
   like a mesh (`armor\x_1.nif\`), which then won its key over a real file in
   a lower-priority mod and failed to load. Only files are meshes.
2. A `meshes` folder whose existence cannot even be checked is skipped alone.
3. A selection whose only problem is a folder it could not read is kept, with
   its warning: every GUI refresh used to repeat the whole scan (1.5-3 minutes)
   for a folder that stays unreadable. The kept selection says its warning
   again each time it is reused, and is selected again as soon as one of those
   folders can be read. A selection whose index FAILED is still not kept.
4. A folder deleted or renamed since (the fix the warning asks for) is not
   "still unreadable": the selection runs again instead of repeating a warning
   about a folder that is gone. An over-long path that reports "not found"
   while it exists stays unreadable. A selection with an unreadable folder AND
   an unread vanilla sweep is not kept either.
"""
import errno
import os
import pathlib
import shutil
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import discovery, paths


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


# --- 1. files, not folders ---------------------------------------------------------

def test_a_folder_named_like_a_mesh_is_not_a_mesh(tmp_path):
    mods = tmp_path / "mods"
    _touch(mods / "High" / "meshes" / "armor" / "x_1.nif" / "inner.nif")
    real = mods / "Low" / "meshes" / "armor" / "x_1.nif"
    _touch(real)
    for keys in (None, {"armor/x_1.nif"}):
        idx = discovery.build_mesh_index(mods, ["High", "Low"], target_keys=keys)
        assert idx["armor/x_1.nif"] == real, (
            "the higher mod's FOLDER must not win the key over a real file")
    assert "armor/x_1.nif/inner.nif" in discovery.build_mesh_index(
        mods, ["High", "Low"]), "a mesh inside such a folder is still a mesh"


# --- 2. a meshes folder that cannot be checked ------------------------------------

def test_a_meshes_folder_that_cannot_be_checked_is_skipped_alone(tmp_path,
                                                                  monkeypatch):
    mods = tmp_path / "mods"
    _touch(mods / "Broken" / "meshes" / "armor" / "a_1.nif")
    _touch(mods / "Other" / "meshes" / "armor" / "b_1.nif")
    real = pathlib.Path.is_dir

    def is_dir(self):
        if self.name == "meshes" and self.parent.name == "Broken":
            raise OSError(errno.EIO, "The device is not ready", str(self))
        return real(self)

    monkeypatch.setattr(pathlib.Path, "is_dir", is_dir)
    bad: list = []
    idx = discovery.build_mesh_index(mods, ["Broken", "Other"], unreadable=bad)
    assert sorted(idx) == ["armor/b_1.nif"]
    assert [m for m, _ in bad] == ["Broken"], bad


# --- 3. the selection memo ---------------------------------------------------------

class _NoArchives:
    def __init__(self, *a, **k):
        pass

    def contains(self, rel):
        return False


ORDER = ["Armour Mod", "Body Build"]


@pytest.fixture
def modlist(tmp_path, monkeypatch):
    """mods/Armour Mod: a plugin, no loose mesh; its armour mesh is in
    mods/Body Build, beside a folder `deep` that may be unreadable."""
    mods = tmp_path / "mods"
    (mods / "Armour Mod").mkdir(parents=True)
    (mods / "Armour Mod" / "Armour.esp").write_bytes(b"TES4")
    _touch(mods / "Body Build" / "meshes" / "armor" / "set" / "coat_1.nif")
    _touch(mods / "Body Build" / "meshes" / "deep" / "x_1.nif")
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    monkeypatch.setattr(ac, "_BsaMeshIndex", _NoArchives)
    monkeypatch.delenv("CBBE2UBE_NO_BSA_ONLY_SOURCES", raising=False)
    monkeypatch.setattr(
        ac, "_player_armor_mesh_bases",
        lambda d, **kw: ({"armor/set/coat", "deep/x"} if d.name == "Armour Mod"
                         else set()))
    stores = (ac._ARMOR_MOD_DIRS_CACHE, ac._BATCH_MESH_INDEX,
              ac._SELECTION_RUN_WARNINGS, ac._SELECTION_BSA_INDEX,
              ac._ARMOR_MOD_DIRS_UNREADABLE)
    for d in stores:
        d.clear()
    yield mods
    for d in stores:
        d.clear()


def _deep_unreadable(monkeypatch):
    """Listing any folder called `deep` fails while state['broken'] is set."""
    state = {"broken": True}
    real = os.scandir

    def scandir(path="."):
        if state["broken"] and Path(path).name == "deep":
            raise OSError(errno.EINVAL, "The filename or extension is too long",
                          str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    if hasattr(pathlib, "_NormalAccessor"):
        monkeypatch.setattr(pathlib._NormalAccessor, "scandir",
                            staticmethod(scandir))
    return state


def _counting_index(monkeypatch):
    calls = []
    real = discovery.build_mesh_index
    monkeypatch.setattr(discovery, "build_mesh_index",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def _select(mods):
    return [c["name"] for c in ac._find_armor_mod_dirs(
        mods, require_arma=True, enabled_ordered=ORDER)]


def _key(mods):
    return str(mods).lower()


def test_an_unreadable_folder_keeps_the_selection_and_says_it_again(
        modlist, monkeypatch, capsys):
    _deep_unreadable(monkeypatch)
    calls = _counting_index(monkeypatch)
    assert _select(modlist) == ["Armour Mod"]
    first = ac._SELECTION_RUN_WARNINGS[_key(modlist)]
    capsys.readouterr()
    assert _select(modlist) == ["Armour Mod"]
    assert len(calls) == 1, "a folder that stays unreadable must not cost a rescan"
    out = capsys.readouterr().out
    assert "!! 1 mod folder(s) could not be fully read" in out, (
        "the kept selection must say its warning again")
    assert "    - Body Build: OSError:" in out, out
    assert ac._SELECTION_RUN_WARNINGS[_key(modlist)] == first, (
        "the convert step must record the same warning on reuse")


def test_a_folder_that_became_readable_is_selected_again(modlist, monkeypatch,
                                                         capsys):
    state = _deep_unreadable(monkeypatch)
    calls = _counting_index(monkeypatch)
    _select(modlist)
    assert "deep/x_1.nif" not in ac._BATCH_MESH_INDEX[_key(modlist)]
    state["broken"] = False
    capsys.readouterr()
    assert _select(modlist) == ["Armour Mod"]
    assert len(calls) == 2, "a folder now readable must be read"
    assert "could not be fully read" not in capsys.readouterr().out
    assert ac._SELECTION_RUN_WARNINGS[_key(modlist)] == []
    assert "deep/x_1.nif" in ac._BATCH_MESH_INDEX[_key(modlist)]


def test_a_failed_index_is_still_tried_again(modlist, monkeypatch):
    """Control: only an unreadable folder is kept; an index that failed is
    not (a selection short of mods)."""
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError(errno.EINVAL, "The filename or extension is too long")

    monkeypatch.setattr(discovery, "build_mesh_index", boom)
    _select(modlist)
    _select(modlist)
    assert len(calls) == 2


# --- 4. a folder gone since, and a second problem ---------------------------------

@pytest.mark.parametrize("fix", ["delete", "rename"])
def test_a_folder_deleted_or_renamed_since_is_selected_again(
        modlist, monkeypatch, capsys, fix):
    state = _deep_unreadable(monkeypatch)
    calls = _counting_index(monkeypatch)
    _select(modlist)
    deep = modlist / "Body Build" / "meshes" / "deep"
    state["broken"] = False          # the fault goes with the folder
    if fix == "delete":
        shutil.rmtree(deep)
    else:
        deep.rename(deep.with_name("shallow"))
    capsys.readouterr()
    assert _select(modlist) == ["Armour Mod"]
    assert len(calls) == 2, "a folder that is gone must not keep the selection"
    assert "could not be fully read" not in capsys.readouterr().out, (
        "no warning about a folder that is not there")
    assert ac._SELECTION_RUN_WARNINGS[_key(modlist)] == []


def test_an_existing_folder_that_reads_as_not_found_stays_unreadable(
        modlist, monkeypatch):
    """An over-long path can report "not found" while it exists: that folder
    is still unreadable, and the kept selection is reused."""
    real = os.scandir

    def scandir(path="."):
        if Path(path).name == "deep":
            raise FileNotFoundError(errno.ENOENT,
                                    "The system cannot find the path specified",
                                    str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    if hasattr(pathlib, "_NormalAccessor"):
        monkeypatch.setattr(pathlib._NormalAccessor, "scandir",
                            staticmethod(scandir))
    calls = _counting_index(monkeypatch)
    _select(modlist)
    _select(modlist)
    assert len(calls) == 1


def test_an_unreadable_folder_and_an_unread_vanilla_sweep_are_not_kept(
        modlist, tmp_path, monkeypatch):
    """Two problems: the kept-with-its-folders rule is for a selection whose
    ONLY problem is unreadable folders; this one's vanilla keys were never
    located, so the next run must try again."""
    data = tmp_path / "Data"
    data.mkdir()
    monkeypatch.setattr(paths, "discover_layout",
                        lambda *a, **k: paths.Layout(game_data_dirs=[data]))

    def bases(d, **kw):
        if d == data:
            raise OSError(errno.EIO, "The device is not ready", str(d))
        return {"armor/set/coat", "deep/x"} if d.name == "Armour Mod" else set()

    monkeypatch.setattr(ac, "_player_armor_mesh_bases", bases)
    _deep_unreadable(monkeypatch)
    calls = _counting_index(monkeypatch)
    assert _select(modlist) == ["Armour Mod"]
    assert sorted(w[0] for w in ac._SELECTION_RUN_WARNINGS[_key(modlist)]) == [
        "mod folder unreadable", "vanilla mesh paths unread"]
    _select(modlist)
    assert len(calls) == 2, "a selection with an unread vanilla sweep must not be kept"
    assert "vanilla mesh paths unread" in [
        w[0] for w in ac._SELECTION_RUN_WARNINGS[_key(modlist)]]
