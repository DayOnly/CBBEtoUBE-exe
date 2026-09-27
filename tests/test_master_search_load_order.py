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

"""#master-search-load-order -- masters and UBE race plugins are looked up in
the game's order.

THE DEFECT. `_discover_master_data_dirs(sources[0])` returned the game Data
folder, then every folder in the mods root in directory order: alphabetical,
disabled mods included, the first source's own folder left out. The master
lookup is first-folder-wins, so a master resolved to the base game's copy or an
alphabetically first -- losing or disabled -- copy, and one only the first
source ships was not found (live: 31 of 637 master names read a copy the game
does not load, 1 not found). The UBE race scan read every plugin there, so a
disabled mod's UBE race plugin would have become a master of the patches.

THE RULE. For a folder of the discovered modlist (or its game Data folder):
overwrite, the ENABLED mods highest priority first (the source's own folder
included), then the game Data folder. Elsewhere, the old list.
`CBBE2UBE_NO_MASTER_SEARCH_LOAD_ORDER=1` restores the old list everywhere.
"""
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import paths
from src import ube_patcher as up
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring

OFF = "CBBE2UBE_NO_MASTER_SEARCH_LOAD_ORDER"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    up.clear_batch_caches()
    yield
    up.clear_batch_caches()


def _plugin(path: Path, races=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = [Record(sig=b"RACE", flags=0, formid=(1 << 24) | (0x800 + i),
                   timestamp_vc=0, version_unk=0x2C,
                   payload=encode_subrecord(b"EDID", encode_zstring(r)))
            for i, r in enumerate(races)]
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[Group(label=b"RACE", records=recs)] if recs else []).save(path)
    return path


def _instance(tmp_path, monkeypatch):
    """An MO2 instance: 'ZZ High' above 'AA Low' (alphabetically the other
    way round), 'MM Disabled' disabled, an overwrite and a game Data folder."""
    inst = tmp_path / "inst"
    mods = inst / "mods"
    for m in ("ZZ High", "AA Low", "MM Disabled"):
        (mods / m).mkdir(parents=True)
    ow = inst / "overwrite"
    ow.mkdir()
    data = tmp_path / "Stock Game" / "Data"
    data.mkdir(parents=True)
    (data / "Skyrim.esm").write_bytes(b"TES4")
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True)
    (prof / "modlist.txt").write_text("+ZZ High\n+AA Low\n-MM Disabled\n",
                                      encoding="utf-8")
    lay = paths.Layout(mods_root=mods, instance_dir=inst, selected_profile="Default",
                       game_data_dirs=[data], overwrite_dir=ow)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    return mods, ow, data


def test_the_folders_are_in_the_games_order(tmp_path, monkeypatch):
    mods, ow, data = _instance(tmp_path, monkeypatch)
    dirs = ac._discover_master_data_dirs(mods / "ZZ High")
    assert dirs == [ow, mods / "ZZ High", mods / "AA Low", data]


def test_a_master_resolves_to_the_copy_the_game_loads(tmp_path, monkeypatch):
    mods, _ow, data = _instance(tmp_path, monkeypatch)
    _plugin(mods / "AA Low" / "Shared.esp")
    _plugin(mods / "ZZ High" / "Shared.esp")
    _plugin(mods / "AA Low" / "Update.esm")
    _plugin(data / "Update.esm")
    dirs = ac._discover_master_data_dirs(mods / "AA Low")
    assert up._find_master_path("Shared.esp", dirs) == mods / "ZZ High" / "Shared.esp"
    # A mod's copy of a base-game master wins over the game folder's, as in game.
    assert up._find_master_path("Update.esm", dirs) == mods / "AA Low" / "Update.esm"


def test_a_master_only_the_first_source_ships_is_found(tmp_path, monkeypatch):
    mods, _ow, _data = _instance(tmp_path, monkeypatch)
    _plugin(mods / "ZZ High" / "Only Here.esp")
    dirs = ac._discover_master_data_dirs(mods / "ZZ High")
    assert up._find_master_path("Only Here.esp", dirs) == mods / "ZZ High" / "Only Here.esp"


def test_a_disabled_mods_ube_race_plugin_is_never_found(tmp_path, monkeypatch):
    mods, _ow, _data = _instance(tmp_path, monkeypatch)
    _plugin(mods / "MM Disabled" / "Custom Race.esp", races=["UBE_CustomRace"])
    _plugin(mods / "AA Low" / "Other Race.esp", races=["UBE_OtherRace"])
    dirs = ac._discover_master_data_dirs(mods / "ZZ High")
    assert [p for p, _fid, _e in up._discover_ube_races(dirs)] == ["Other Race.esp"]


def test_a_folder_outside_the_modlist_keeps_the_old_list(tmp_path, monkeypatch):
    _instance(tmp_path, monkeypatch)
    other = tmp_path / "far" / "away" / "mods"      # no game folder above it
    for m in ("A", "B"):
        (other / m).mkdir(parents=True)
    assert sorted(d.name for d in ac._discover_master_data_dirs(other / "A")) == ["B"]


def test_switched_off_the_old_list_comes_back(tmp_path, monkeypatch):
    mods, _ow, data = _instance(tmp_path, monkeypatch)
    monkeypatch.setenv(OFF, "1")
    dirs = ac._discover_master_data_dirs(mods / "ZZ High")
    assert dirs[0] == data
    assert sorted(d.name for d in dirs[1:]) == ["AA Low", "MM Disabled"]
