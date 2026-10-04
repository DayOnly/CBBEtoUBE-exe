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

"""A physics XML is opened from the copy that wins the load order (#xml-load-order, GitHub #28).

A mesh names its physics XML by a path. The game opens the copy that wins MO2's
priority order. The converter looked in the mesh's own mod first and then walked the
mod folders in alphabetical order, so an add-on mod that overrides a base mod's XML
(an SMP rig over a plain one) lost to the base mod's older file: on the reported
skirt the output kept the old chains and its colliders were pruned for shapes the
mesh does not have.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import nif_convert as nc

REL = "Meshes\\Maker\\Skirt\\SkirtPhysics.xml"


def _file(mods: Path, mod: str, rel: str = REL, text: str = "<system/>") -> Path:
    p = mods / mod / Path(*rel.replace("\\", "/").split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _nif(mods: Path, mod: str) -> Path:
    p = mods / mod / "meshes" / "Maker" / "Skirt" / "Skirt_1.nif"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00")
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.setattr(nc._paths, "mods_root", lambda: mods)
    monkeypatch.setattr(nc, "_LOAD_ORDER_DIRS", {})
    monkeypatch.setattr(nc, "_VFS_DATA_REL_MEMO", {})
    return SimpleNamespace(mods=mods, mp=monkeypatch)


def _order(world, *names, overwrite=None, data=()):
    dirs = ([overwrite] if overwrite else []) + [world.mods / n for n in names] + list(data)
    world.mp.setattr(nc, "_load_order_dirs", lambda: dirs)


def test_an_add_on_that_wins_the_order_beats_the_base_mod_the_mesh_lives_in(world):
    base = _file(world.mods, "Base Skirt", text="<old/>")
    addon = _file(world.mods, "Base Skirt - SMP", text="<smp/>")
    nif = _nif(world.mods, "Base Skirt")
    _order(world, "Base Skirt - SMP", "Base Skirt")
    assert nc._resolve_data_rel_in_vfs(REL, nif) == addon
    assert base.is_file()


def test_the_base_mod_still_wins_when_it_is_the_winner(world):
    base = _file(world.mods, "Base Skirt")
    _file(world.mods, "Base Skirt - SMP")
    nif = _nif(world.mods, "Base Skirt")
    _order(world, "Base Skirt", "Base Skirt - SMP")
    assert nc._resolve_data_rel_in_vfs(REL, nif) == base


def test_priority_decides_not_the_alphabet(world):
    _file(world.mods, "Alpha")
    zeta = _file(world.mods, "Zeta")
    nif = _nif(world.mods, "Mesh Mod")
    _order(world, "Zeta", "Alpha", "Mesh Mod")
    assert nc._resolve_data_rel_in_vfs(REL, nif) == zeta


def test_the_overwrite_folder_beats_every_mod(world, tmp_path):
    ow = tmp_path / "overwrite"
    over = _file(ow.parent, "overwrite")
    _file(world.mods, "Base Skirt")
    nif = _nif(world.mods, "Base Skirt")
    _order(world, "Base Skirt", overwrite=ow)
    assert nc._resolve_data_rel_in_vfs(REL, nif) == over


def test_a_file_the_order_does_not_ship_is_found_in_the_meshs_own_mod(world):
    own = _file(world.mods, "Base Skirt")
    nif = _nif(world.mods, "Base Skirt")
    _order(world, "Some Other Mod")
    assert nc._resolve_data_rel_in_vfs(REL, nif) == own


def test_with_no_profile_the_old_order_stands(world):
    local = _file(world.mods, "Base Skirt")
    _file(world.mods, "Aardvark Add-on")
    nif = _nif(world.mods, "Base Skirt")
    world.mp.setattr(nc, "_load_order_dirs", lambda: None)
    assert nc._resolve_data_rel_in_vfs(REL, nif) == local


def test_the_switch_restores_the_own_mod_first_order(world):
    base = _file(world.mods, "Base Skirt")
    _file(world.mods, "Base Skirt - SMP")
    nif = _nif(world.mods, "Base Skirt")
    _order(world, "Base Skirt - SMP", "Base Skirt")
    world.mp.setattr(nc, "XML_LOAD_ORDER", False)
    assert nc._resolve_data_rel_in_vfs(REL, nif) == base


def test_the_switch_is_on_by_default():
    assert nc.XML_LOAD_ORDER is True


def test_a_pointer_written_with_a_leading_data_folder_follows_the_order_too(world):
    addon = _file(world.mods, "Base Skirt - SMP", text="<smp/>")
    _file(world.mods, "Base Skirt")
    nif = _nif(world.mods, "Base Skirt")
    _order(world, "Base Skirt - SMP", "Base Skirt")
    assert nc._resolve_data_rel_in_vfs("Data\\" + REL, nif) == addon


def _layout(mods_root, game_data=()):
    return SimpleNamespace(mods_root=mods_root, game_data_dirs=list(game_data))


def test_the_load_order_is_overwrite_then_mods_by_priority_then_game_data(world, tmp_path):
    ow, data = tmp_path / "overwrite", tmp_path / "Data"
    ow.mkdir()
    data.mkdir()
    lay = _layout(world.mods, [data])
    world.mp.setattr(nc._paths, "discover_layout", lambda: lay)
    world.mp.setattr(nc._paths, "enabled_mods_ordered", lambda _l: ["B", "A"])
    world.mp.setattr(nc._paths, "overwrite_dir", lambda _l: ow)
    assert nc._load_order_dirs() == [ow, world.mods / "B", world.mods / "A", data]


def test_a_profile_for_another_mods_folder_is_not_used(world, tmp_path):
    # the process was given one mods folder; the discovered instance owns another
    lay = _layout(tmp_path / "somewhere else")
    world.mp.setattr(nc._paths, "discover_layout", lambda: lay)
    world.mp.setattr(nc._paths, "enabled_mods_ordered", lambda _l: ["A"])
    world.mp.setattr(nc._paths, "overwrite_dir", lambda _l: None)
    assert nc._load_order_dirs() is None


def test_no_readable_profile_gives_no_load_order(world):
    lay = _layout(world.mods)
    world.mp.setattr(nc._paths, "discover_layout", lambda: lay)
    world.mp.setattr(nc._paths, "enabled_mods_ordered", lambda _l: None)
    assert nc._load_order_dirs() is None


def test_a_failing_discovery_gives_no_load_order_instead_of_an_error(world):
    def boom():
        raise RuntimeError("no ini")
    world.mp.setattr(nc._paths, "discover_layout", boom)
    assert nc._load_order_dirs() is None
