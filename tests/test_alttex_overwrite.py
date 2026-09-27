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

r"""#alttex-overwrite -- the colour-variant source lookup reads the same copy
of a mesh the convert step converted, MO2's overwrite included.

THE DEFECT (review of the selection merge, 2026-09-26). #overwrite-mesh-index
made the convert step's mesh index read MO2's overwrite folder. The
alt-texture reconcile's `_alttex_source_paths` builds its own index when no
batch index exists (a standalone `convert`, or selection's index failed) and
did not pass `overwrite=`, so it could read a lower-priority mod's copy while
the convert step converted the overwrite copy. It rides the same switch:
`CBBE2UBE_NO_OVERWRITE_MESH_INDEX=1` leaves overwrite out of both.
"""
from pathlib import Path

import pytest

from src import auto_convert as ac, paths, ube_patcher as up

OFF = "CBBE2UBE_NO_OVERWRITE_MESH_INDEX"
KEY = "armor/x/robe_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.setenv("CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE", "1")


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"nif")
    return p


def _modlist(monkeypatch, tmp_path):
    """A modlist whose overwrite holds the robe, and a lower BodySlide output
    that holds another copy; no batch index (a standalone convert)."""
    mods = tmp_path / "mods"
    lay = paths.Layout(mods_root=mods, instance_dir=tmp_path,
                       overwrite_dir=tmp_path / "overwrite")
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "mods_root", lambda: mods)
    monkeypatch.setattr(paths, "enabled_mods_ordered",
                        lambda l: ["Other BodySlide Output"])
    monkeypatch.setattr(ac, "_BATCH_MESH_INDEX", {})
    ow = _touch(tmp_path / "overwrite" / "meshes" / KEY)
    other = _touch(mods / "Other BodySlide Output" / "meshes" / KEY)
    return ow, other


def test_the_lookup_reads_the_copy_the_convert_step_converted(tmp_path,
                                                             monkeypatch):
    ow, _other = _modlist(monkeypatch, tmp_path)
    got = up._alttex_source_paths(tmp_path / "out" / "meshes", [KEY])
    assert got == {KEY: ow}
    # the convert step's own index, as _cmd_convert builds it
    from src import discovery
    idx = discovery.build_mesh_index(
        tmp_path / "mods", ["Other BodySlide Output"], target_keys={KEY},
        skip_mods={"out"}, overwrite=ac._modlist_overwrite(tmp_path / "mods"))
    assert idx[KEY] == got[KEY]


def test_switched_off_neither_reads_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _ow, other = _modlist(monkeypatch, tmp_path)
    got = up._alttex_source_paths(tmp_path / "out" / "meshes", [KEY])
    assert got == {KEY: other}
