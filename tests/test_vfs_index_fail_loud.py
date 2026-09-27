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

r"""#vfs-index-fail-loud -- a mesh index that cannot be built is said, never
passed off as "no mod ships these meshes".

THE DEFECT. Source selection built the armour-mesh index inside
`except Exception: vfs = {}`, printed nothing, and cached the empty dict for the
convert step, which reused it ("reusing 0 located armour mesh path(s)"). So a
mod whose armour meshes are in another mod (a BodySlide build, a replacer) was
dropped and reported "found nowhere", and every other source converted its own
or an archive copy instead of the mesh the game loads -- all in silence. One
error was enough: `rglob` let any error but a permission error escape from the
middle of its walk, so one unreadable folder in one mod aborted the index for
every mod. The vanilla sweep's mesh-path read was `except Exception: pass`.
"""
import errno
import os
import pathlib
import sys
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import discovery, paths
from tests.test_run_warnings_reach_the_tally import _convert, _tally


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


def _unreadable_folder(monkeypatch, name):
    """Every directory listing of a folder called `name` fails with an error
    that is not a permission error (an over-long path, a dead junction), for
    `os.walk` and for `Path.rglob` alike."""
    real = os.scandir

    def scandir(path="."):
        if Path(path).name == name:
            raise OSError(errno.EINVAL, "The filename or extension is too long",
                          str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    if hasattr(pathlib, "_NormalAccessor"):          # Python 3.10's rglob
        monkeypatch.setattr(pathlib._NormalAccessor, "scandir",
                            staticmethod(scandir))


# --- the walk ------------------------------------------------------------------------

def test_one_unreadable_folder_leaves_every_other_mod_indexed(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    _touch(mods / "Broken" / "meshes" / "armor" / "good_1.nif")
    _touch(mods / "Broken" / "meshes" / "armor" / "deep" / "lost_1.nif")
    _touch(mods / "Other" / "meshes" / "armor" / "other_1.nif")
    _unreadable_folder(monkeypatch, "deep")
    bad: list = []
    idx = discovery.build_mesh_index(mods, ["Broken", "Other"], unreadable=bad)
    assert sorted(idx) == ["armor/good_1.nif", "armor/other_1.nif"], (
        "one unreadable folder must cost only its own meshes")
    assert [m for m, _ in bad] == ["Broken"], bad
    assert "too long" in bad[0][1], bad


def test_the_walk_finds_what_rglob_found_in_the_same_order(tmp_path):
    """Control: with nothing unreadable the index is the one `rglob` gave --
    same meshes (any case of the extension), same order, same paths."""
    mods = tmp_path / "mods"
    for rel in ("a_1.nif", "sub/b_1.NIF", "sub/deeper/c_0.nif", "z/d.nif",
                "sub/notes.txt"):
        _touch(mods / "M" / "meshes" / rel)
    idx = discovery.build_mesh_index(mods, ["M"])
    md = mods / "M" / "meshes"
    want = {n.relative_to(md).as_posix().lower(): n for n in md.rglob("*.nif")}
    assert idx == want                                   # same meshes, same paths
    # The walk's own order: depth-first, as rglob gave it on the interpreter the
    # exe is frozen with. rglob went breadth-first in 3.12, so its ORDER is only
    # the reference before that; the walk's order does not change.
    assert list(idx) == ["a_1.nif", "sub/b_1.nif", "sub/deeper/c_0.nif", "z/d.nif"]
    if sys.version_info < (3, 12):
        assert list(idx.items()) == list(want.items())
    assert "sub/b_1.nif" in idx


# --- source selection ------------------------------------------------------------------

@pytest.fixture
def modlist(tmp_path, monkeypatch):
    """mods/Armour Mod: a plugin, no loose mesh; its armour mesh is in
    mods/Body Build. Hermetic: no host modlist, no archives."""
    mods = tmp_path / "mods"
    (mods / "Armour Mod").mkdir(parents=True)
    (mods / "Armour Mod" / "Armour.esp").write_bytes(b"TES4")
    _touch(mods / "Body Build" / "meshes" / "armor" / "set" / "coat_1.nif")
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    monkeypatch.setattr(ac, "_BsaMeshIndex", _NoArchives)
    monkeypatch.delenv("CBBE2UBE_NO_BSA_ONLY_SOURCES", raising=False)
    monkeypatch.setattr(
        ac, "_player_armor_mesh_bases",
        lambda d, **kw: {"armor/set/coat"} if d.name == "Armour Mod" else set())
    for d in (ac._ARMOR_MOD_DIRS_CACHE, ac._BATCH_MESH_INDEX,
              ac._SELECTION_RUN_WARNINGS, ac._SELECTION_BSA_INDEX):
        d.clear()
    yield mods
    for d in (ac._ARMOR_MOD_DIRS_CACHE, ac._BATCH_MESH_INDEX,
              ac._SELECTION_RUN_WARNINGS, ac._SELECTION_BSA_INDEX):
        d.clear()


class _NoArchives:
    def __init__(self, *a, **k):
        pass

    def contains(self, rel):
        return False


ORDER = ["Armour Mod", "Body Build"]


def _select(mods, memo=False):
    fn = ac._find_armor_mod_dirs if memo else ac._find_armor_mod_dirs_uncached
    return [c["name"] for c in fn(mods, require_arma=True, enabled_ordered=ORDER)]


def _index_fails(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError(errno.EINVAL, "The filename or extension is too long")

    monkeypatch.setattr(discovery, "build_mesh_index", boom)
    return calls


def test_the_mesh_in_another_mod_selects_the_armour_mod(modlist, capsys):
    """Control: the index builds, so the mod is a source and nothing is said."""
    assert _select(modlist) == ["Armour Mod"]
    assert "!!" not in capsys.readouterr().out
    assert ac._SELECTION_RUN_WARNINGS[str(modlist).lower()] == []


def test_a_failed_index_is_not_cached_as_empty(modlist, monkeypatch, capsys):
    _index_fails(monkeypatch)
    assert _select(modlist) == []
    out = capsys.readouterr().out
    assert ac._BATCH_MESH_INDEX[str(modlist).lower()] is None, (
        "an empty index is reused by the convert step as 'no mod ships these'")
    assert "!! could not locate armour meshes across the enabled mods" in out, out


def test_a_failed_index_does_not_claim_the_meshes_are_nowhere(modlist, monkeypatch,
                                                              capsys):
    _index_fails(monkeypatch)
    _select(modlist)
    out = capsys.readouterr().out
    assert "found nowhere" not in out, out
    assert "not searched for: 1 mod(s)" in out and "    - Armour Mod\n" in out, out
    rec = ac._SELECTION_RUN_WARNINGS[str(modlist).lower()]
    assert [r[0] for r in rec] == ["armour mesh index failed"], rec
    assert "too long" in rec[0][3] and "Armour Mod" in rec[0][3], rec


def test_with_the_archive_rule_off_the_unsearched_mods_are_still_named(
        modlist, monkeypatch, capsys):
    """That switch turns off the "found nowhere" list, not this warning."""
    monkeypatch.setenv("CBBE2UBE_NO_BSA_ONLY_SOURCES", "1")
    _index_fails(monkeypatch)
    _select(modlist)
    out = capsys.readouterr().out
    assert "not searched for: 1 mod(s)" in out and "    - Armour Mod\n" in out, out


def test_a_kept_selection_carries_no_old_warning(modlist, monkeypatch):
    """A selection kept from earlier found no problem; a later failed one with
    other settings must not lend it its warning."""
    _select(modlist, memo=True)
    _index_fails(monkeypatch)
    ac._find_armor_mod_dirs(modlist, require_arma=True, enabled_ordered=ORDER,
                            extra_exclude_names={"Unrelated"})
    assert ac._SELECTION_RUN_WARNINGS[str(modlist).lower()]
    assert _select(modlist, memo=True) == ["Armour Mod"]
    assert ac._SELECTION_RUN_WARNINGS[str(modlist).lower()] == []


def test_a_failed_selection_is_tried_again(modlist, monkeypatch):
    calls = _index_fails(monkeypatch)
    _select(modlist, memo=True)
    _select(modlist, memo=True)
    assert len(calls) == 2, "a selection short of mods must not be kept"


def test_a_clean_selection_is_kept(modlist, monkeypatch):
    """Control for the one above: the memo still saves the second scan."""
    calls = []
    real = discovery.build_mesh_index
    monkeypatch.setattr(discovery, "build_mesh_index",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _select(modlist, memo=True) == _select(modlist, memo=True) == ["Armour Mod"]
    assert len(calls) == 1


def test_an_unreadable_folder_is_a_selection_warning(modlist, monkeypatch, capsys):
    _touch(modlist / "Body Build" / "meshes" / "deep" / "x_1.nif")
    _unreadable_folder(monkeypatch, "deep")
    assert _select(modlist) == ["Armour Mod"], "the rest is still located"
    out = capsys.readouterr().out
    assert "!! 1 mod folder(s) could not be fully read" in out, out
    assert "    - Body Build: OSError:" in out, out
    rec = ac._SELECTION_RUN_WARNINGS[str(modlist).lower()]
    assert [(r[0], r[1]) for r in rec] == [("mod folder unreadable", "Body Build")]


def test_an_unread_vanilla_sweep_is_a_selection_warning(modlist, monkeypatch, capsys,
                                                        tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    monkeypatch.setattr(paths, "discover_layout",
                        lambda *a, **k: paths.Layout(game_data_dirs=[data]))
    monkeypatch.delenv("CBBE2UBE_NO_VANILLA_SWEEP", raising=False)

    def bases(d, **kw):
        if d == data:
            raise OSError(errno.EIO, "The device is not ready")
        return {"armor/set/coat"} if d.name == "Armour Mod" else set()

    monkeypatch.setattr(ac, "_player_armor_mesh_bases", bases)
    assert _select(modlist) == ["Armour Mod"]
    out = capsys.readouterr().out
    assert "!! could not read which vanilla armour meshes to locate" in out, out
    rec = ac._SELECTION_RUN_WARNINGS[str(modlist).lower()]
    assert [r[0] for r in rec] == ["vanilla mesh paths unread"], rec


# --- the convert step -------------------------------------------------------------------

@pytest.fixture
def clean_state():
    for d in (ac._BATCH_MESH_INDEX, ac._SELECTION_RUN_WARNINGS):
        d.clear()
    yield
    for d in (ac._BATCH_MESH_INDEX, ac._SELECTION_RUN_WARNINGS):
        d.clear()


def _mods_key(base):
    return str(base / "mods").lower()


def test_a_selection_warning_reaches_the_tally_and_the_failures_file(
        tmp_path, monkeypatch, capsys, clean_state):
    _, clean_log, _ = _convert(tmp_path / "ok", monkeypatch, capsys)
    base = tmp_path / "failed"
    ac._BATCH_MESH_INDEX[_mods_key(base)] = None
    ac._SELECTION_RUN_WARNINGS[_mods_key(base)] = [
        ("armour mesh index failed", "source selection", "mods", "OSError: x")]
    rc, log, fails = _convert(base, monkeypatch, capsys)
    assert _tally(log)[1] == _tally(clean_log)[1] + 1, (_tally(clean_log), _tally(log))
    hit = [e for e in fails if e["kind"] == "armour mesh index failed"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning", fails
    assert rc == 0


def _convert_builds(monkeypatch, build):
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: ["SomeMod"])
    monkeypatch.setattr(ac, "_player_armor_mesh_bases", lambda d, **kw: {"armor/x"})
    monkeypatch.setattr(discovery, "build_mesh_index", build)


def test_the_convert_steps_own_failure_is_counted(tmp_path, monkeypatch, capsys,
                                                  clean_state):
    _convert_builds(monkeypatch, lambda *a, **k: {})
    _, clean_log, _ = _convert(tmp_path / "ok", monkeypatch, capsys)

    def boom(*a, **k):
        raise OSError(errno.EINVAL, "The filename or extension is too long")

    _convert_builds(monkeypatch, boom)
    rc, log, fails = _convert(tmp_path / "failed", monkeypatch, capsys)
    assert "!! could not locate armour meshes across the enabled mods" in log
    assert _tally(log)[1] == _tally(clean_log)[1] + 1, (_tally(clean_log), _tally(log))
    hit = [e for e in fails if e["kind"] == "armour mesh index failed"]
    assert len(hit) == 1 and hit[0]["source"] == "convert step", fails


def test_the_convert_step_rebuilds_an_index_selection_could_not_build(
        tmp_path, monkeypatch, capsys, clean_state):
    calls = []
    _convert_builds(monkeypatch, lambda *a, **k: calls.append(1) or {})
    base = tmp_path / "rebuilt"
    ac._BATCH_MESH_INDEX[_mods_key(base)] = None
    _, log, _ = _convert(base, monkeypatch, capsys)
    assert calls == [1] and "reusing" not in log, log[-2000:]


def test_an_unreadable_folder_in_the_convert_step_is_counted(
        tmp_path, monkeypatch, capsys, clean_state):
    _convert_builds(monkeypatch, lambda *a, **k: {})
    _, clean_log, _ = _convert(tmp_path / "ok", monkeypatch, capsys)

    def partial(*a, unreadable=None, **k):
        unreadable.append(("Body Build", "OSError: too long"))
        return {}

    _convert_builds(monkeypatch, partial)
    rc, log, fails = _convert(tmp_path / "partial", monkeypatch, capsys)
    assert "    - Body Build: OSError: too long" in log
    assert _tally(log)[1] == _tally(clean_log)[1] + 1, (_tally(clean_log), _tally(log))
    assert [e["source"] for e in fails if e["kind"] == "mod folder unreadable"] == [
        "Body Build"], fails
