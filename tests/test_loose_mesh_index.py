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

"""#loose-mesh-index -- `_mesh_exists_anywhere` answers its loose-file questions
from one listing of every loose `meshes` folder, built on the first question.

The per-path probe it replaces checked `<dir>/meshes/<path>` in every loose
folder (overwrite, then mods by priority, then the game Data) before it asked
the archives; on the reported modlist each archived or dead path cost ~3,300
file checks. The answers must not change: case-insensitive, the FIRST folder
wins for `body_fit`, an unreadable folder costs only itself, a missing meshes
folder is nothing, files only, the archive fallback as before.
CBBE2UBE_NO_LOOSE_MESH_INDEX=1 probes per path again.
"""
import os
import pathlib
import sys

import pytest

from src import auto_convert as ac

REL = r"follower\m\hood_1.nif"


def _put(path: pathlib.Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _modlist(tmp_path, monkeypatch, order=("Out", "High Mod", "No Meshes Mod",
                                           "Low Mod", "Packed Mod")):
    """Mods in priority order (first wins), an overwrite, and a game Data."""
    mods = tmp_path / "mods"
    for n in order:
        (mods / n).mkdir(parents=True, exist_ok=True)
    (tmp_path / "overwrite").mkdir(exist_ok=True)
    (tmp_path / "Data").mkdir(exist_ok=True)

    class _Lay:
        game_data_dirs = [tmp_path / "Data"]
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: list(order))
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: tmp_path / "overwrite")
    # body_fit's reader: which copy was read, by its bytes.
    monkeypatch.setattr(ac, "_nif_bytes_body_fit", lambda data: data == b"HIGH")
    return mods


def _lookup(tmp_path):
    return ac._mesh_exists_anywhere(tmp_path / "mods" / "Out")


def _in_meshes(p) -> bool:
    """A path INSIDE a `meshes` folder -- by component, not by substring: a mod
    named "No Meshes Mod" is not a meshes folder. From Python 3.11 pathlib lists
    through os.scandir, so the archive scan's listing of that mod folder reached
    this counter and read as a second listing (3.10's pathlib bypassed it)."""
    return any(part.lower() == "meshes" for part in pathlib.PurePath(str(p)).parts)


def _count_scandir(monkeypatch) -> list:
    calls = []
    real = os.scandir

    def scandir(p="."):
        if _in_meshes(p):
            calls.append(str(p))
        return real(p)
    monkeypatch.setattr(ac.os, "scandir", scandir)
    return calls


def _no_file_checks(monkeypatch) -> list:
    calls = []
    real = pathlib.Path.is_file

    def is_file(self):
        if _in_meshes(self):
            calls.append(str(self))
        return real(self)
    monkeypatch.setattr(pathlib.Path, "is_file", is_file)
    return calls


# -------------------------------------------------------------- same answers

def test_the_first_folder_in_priority_order_is_read(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "High Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    _put(tmp_path / "Data" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    assert _lookup(tmp_path).body_fit(REL) is True, "the higher-priority copy wins"


def test_overwrite_comes_before_every_mod(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch)
    _put(tmp_path / "overwrite" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    _put(mods / "High Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    assert _lookup(tmp_path).body_fit(REL) is True


def test_a_path_is_matched_in_any_case(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "Low Mod" / "Meshes" / "Follower" / "M" / "Hood_1.NIF", b"HIGH")
    exists = _lookup(tmp_path)
    assert exists(r"MESHES\FOLLOWER\m\hood_1.nif")
    assert exists.body_fit(r"meshes/follower/M/HOOD_1.nif") is True


def test_a_folder_is_not_a_file(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch)
    (mods / "High Mod" / "meshes" / "follower" / "m" / "hood_1.nif").mkdir(parents=True)
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    exists = _lookup(tmp_path)
    assert exists(r"follower\m\hood_1.nif")
    assert exists.body_fit(REL) is True, "the folder of the same name is skipped"
    assert not exists(r"follower\m")


def test_an_unreadable_folder_is_checked_on_disk(tmp_path, monkeypatch):
    """A folder the listing cannot open still holds its file for the game:
    that folder's paths are checked on disk, in priority order."""
    mods = _modlist(tmp_path, monkeypatch)
    locked = mods / "High Mod" / "meshes" / "follower"
    _put(locked / "m" / "hood_1.nif", b"HIGH")
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    _put(mods / "Low Mod" / "meshes" / "other" / "belt_1.nif")
    real = os.scandir

    def scandir(p="."):
        if pathlib.Path(p) == locked:
            raise PermissionError("access denied")
        return real(p)
    monkeypatch.setattr(ac.os, "scandir", scandir)
    exists = _lookup(tmp_path)
    assert exists.body_fit(REL) is True, "the locked higher-priority copy wins"
    assert exists(r"other\belt_1.nif"), "the rest of the listing still answers"
    assert not exists(r"follower\m\missing_1.nif")


def test_a_mesh_only_in_an_archive_is_found_there(tmp_path, monkeypatch):
    from tests.test_bsa_seek_read import _write_bsa
    mods = _modlist(tmp_path, monkeypatch)
    _write_bsa(mods / "Packed Mod" / "Packed.bsa",
               [(r"meshes\follower\m", "cowl_1.nif", b"HIGH", None)])
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    exists = _lookup(tmp_path)
    assert exists(r"follower\m\cowl_1.nif")
    assert exists.body_fit(r"follower\m\cowl_1.nif") is True
    assert exists.body_fit(REL) is False
    assert not exists(r"follower\m\missing_1.nif")
    assert exists.body_fit(r"follower\m\missing_1.nif") is None


def test_a_path_windows_resolves_is_checked_on_disk(tmp_path, monkeypatch):
    """`..` and the like reach a file no listing names: checked on disk."""
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    exists = _lookup(tmp_path)
    assert exists(r"follower\x\..\m\hood_1.nif") is exists(r"follower\m\hood_1.nif")
    assert exists.body_fit(r"follower\m\.\hood_1.nif") is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows trims a trailing dot")
def test_a_trailing_dot_reaches_the_file_as_on_disk(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    assert _lookup(tmp_path)(r"follower\m\hood_1.nif.")


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows link")
def test_a_linked_folder_is_followed_and_a_loop_listed_once(tmp_path, monkeypatch):
    import _winapi
    mods = _modlist(tmp_path, monkeypatch)
    real_dir = tmp_path / "elsewhere" / "m"
    _put(real_dir / "hood_1.nif", b"HIGH")
    (mods / "Low Mod" / "meshes" / "follower").mkdir(parents=True)
    _winapi.CreateJunction(str(real_dir), str(mods / "Low Mod" / "meshes" / "follower" / "m"))
    loop = mods / "High Mod" / "meshes" / "loop"
    loop.mkdir(parents=True)
    _put(loop / "belt_1.nif")
    _winapi.CreateJunction(str(loop), str(loop / "again"))
    calls = _count_scandir(monkeypatch)
    exists = _lookup(tmp_path)
    assert exists.body_fit(REL) is True, "a mesh behind a junction is loose"
    assert exists(r"loop\again\belt_1.nif")
    assert exists(r"loop\again\again\again\belt_1.nif"), "as a file check sees it"
    assert len([c for c in calls if "loop" in c]) <= 3, "the loop is listed once"


class _Listing:
    """A folder listing a test controls: fixed entries, or a failure."""
    def __init__(self, entries=(), fail=False):
        self._e, self._fail = list(entries), fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        if self._fail:
            raise OSError("the listing failed partway")
        return iter(self._e)


class _Entry:
    def __init__(self, name, path):
        self.name, self.path = name, path

    def is_dir(self):
        return False

    def is_file(self):
        return True

    def is_symlink(self):
        return False


def test_a_listing_that_fails_partway_is_checked_on_disk(tmp_path, monkeypatch):
    """A folder that opened but could not be listed to the end (a vanished or
    locked folder): what it holds is checked on disk, so the higher-priority
    copy the listing missed still wins."""
    mods = _modlist(tmp_path, monkeypatch)
    torn = mods / "High Mod" / "meshes" / "follower" / "m"
    _put(torn / "hood_1.nif", b"HIGH")
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    real = os.scandir

    def scandir(p="."):
        return _Listing(fail=True) if pathlib.Path(p) == torn else real(p)
    monkeypatch.setattr(ac.os, "scandir", scandir)
    assert _lookup(tmp_path).body_fit(REL) is True


def test_a_short_name_is_checked_on_disk(tmp_path, monkeypatch):
    """An 8.3 short name reaches a folder the listing names only by its long
    name; a file check resolves it, as the game does."""
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "Low Mod" / "meshes" / "follower" / "long folder" / "hood_1.nif", b"HIGH")
    short = mods / "Low Mod" / "meshes" / "follower" / "longfo~1" / "hood_1.nif"
    real = pathlib.Path.is_file
    monkeypatch.setattr(pathlib.Path, "is_file",
                        lambda self: True if self == short else real(self))
    assert _lookup(tmp_path)(r"follower\longfo~1\hood_1.nif")


def test_a_very_long_name_is_checked_on_disk(tmp_path, monkeypatch):
    """Past MAX_PATH a listing can name a file a plain file check cannot
    open: that folder's paths are checked on disk, and here none opens."""
    mods = _modlist(tmp_path, monkeypatch)
    folder = mods / "Low Mod" / "meshes" / "follower" / "m"
    folder.mkdir(parents=True)
    real = os.scandir

    def scandir(p="."):
        if pathlib.Path(p) == folder:
            return _Listing([_Entry("hood_1.nif", "x" * 260)])
        return real(p)
    monkeypatch.setattr(ac.os, "scandir", scandir)
    assert not _lookup(tmp_path)(REL)


# ------------------------------------------------------------ the speed-up

def test_the_folders_are_listed_once_and_no_path_is_probed(tmp_path, monkeypatch):
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    exists = _lookup(tmp_path)
    listed = _count_scandir(monkeypatch)
    probed = _no_file_checks(monkeypatch)
    assert exists(REL)
    n = len(listed)
    assert n > 0
    for i in range(20):
        assert not exists(rf"follower\m\dead_{i}.nif")
    assert exists.body_fit(REL) is True
    assert len(listed) == n, "listed once, on the first question"
    assert probed == [], "no per-folder file check for a plain path"


def test_switched_off_every_path_is_probed_again(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_LOOSE_MESH_INDEX", "1")
    mods = _modlist(tmp_path, monkeypatch)
    _put(mods / "High Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"HIGH")
    _put(mods / "Low Mod" / "meshes" / "follower" / "m" / "hood_1.nif", b"LOW")
    exists = _lookup(tmp_path)
    listed = _count_scandir(monkeypatch)
    probed = _no_file_checks(monkeypatch)
    assert not exists(r"follower\m\dead_1.nif")
    assert exists.body_fit(REL) is True
    assert listed == []
    assert len(probed) > 5, "one check per loose folder for the dead path"
