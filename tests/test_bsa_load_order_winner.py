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

r"""#bsa-load-order-winner -- the archive the game loads wins.

THE DEFECT. `_BsaMeshIndex` took a mesh from the first archive in MO2 priority
order. The game resolves two archives holding the same file by the plugins that
load them: `<plugin>.bsa` / `<plugin> - Textures.bsa` of the plugin that loads
LATER overrides. When the two orders disagreed, the converter extracted and
converted a different copy than the one drawn in game. Live: 436 mesh paths
change winner, none of them read by the converter.
`CBBE2UBE_NO_BSA_LOAD_ORDER_WINNER=1` takes the MO2 order again.
"""
from pathlib import Path

import pytest

from src import auto_convert as ac
from tests.test_bsa_seek_read import _write_bsa

OFF = "CBBE2UBE_NO_BSA_LOAD_ORDER_WINNER"
KEY = "armor/x/cuirass_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _mod(root, mod, archive, payload):
    _write_bsa(root / mod / archive, [(r"meshes\armor\x", "cuirass_1.nif", payload, None)])
    return root / mod


def _two(tmp_path):
    """MO2 priority: ModHigh above ModLow. Each archive holds the mesh."""
    return [_mod(tmp_path, "ModHigh", "High.bsa", b"HIGH"),
            _mod(tmp_path, "ModLow", "Low.bsa", b"LOW")]


def test_the_archive_of_the_later_plugin_wins(tmp_path):
    idx = ac._BsaMeshIndex(_two(tmp_path), None,
                           plugin_order=["Skyrim.esm", "High.esp", "Low.esp"])
    assert idx.read_bytes(KEY) == b"LOW"


def test_where_both_orders_agree_nothing_changes(tmp_path):
    idx = ac._BsaMeshIndex(_two(tmp_path), None,
                           plugin_order=["Skyrim.esm", "Low.esp", "High.esp"])
    assert idx.read_bytes(KEY) == b"HIGH"


def test_a_textures_archive_counts_for_its_plugin(tmp_path):
    dirs = [_mod(tmp_path, "ModHigh", "High.bsa", b"HIGH"),
            _mod(tmp_path, "ModLow", "Low - Textures.bsa", b"LOW")]
    idx = ac._BsaMeshIndex(dirs, None, plugin_order=["High.esp", "Low.esp"])
    assert idx.read_bytes(KEY) == b"LOW"


def test_an_archive_no_plugin_loads_ranks_below_a_loaded_one(tmp_path):
    """The INI-listed base-game archives load before any plugin's, and a stray
    archive is not loaded at all."""
    dirs = [_mod(tmp_path, "ModHigh", "Stray.bsa", b"STRAY"),
            _mod(tmp_path, "ModLow", "Low.bsa", b"LOW")]
    idx = ac._BsaMeshIndex(dirs, None, plugin_order=["Skyrim.esm", "Low.esp"])
    assert idx.read_bytes(KEY) == b"LOW"


def test_archives_no_plugin_loads_keep_the_mo2_order(tmp_path):
    dirs = [_mod(tmp_path, "ModHigh", "StrayA.bsa", b"A"),
            _mod(tmp_path, "ModLow", "StrayB.bsa", b"B")]
    idx = ac._BsaMeshIndex(dirs, None, plugin_order=["Skyrim.esm"])
    assert idx.read_bytes(KEY) == b"A"


def test_without_a_plugin_order_the_mo2_order_stands(tmp_path):
    idx = ac._BsaMeshIndex(_two(tmp_path), None)
    assert idx.read_bytes(KEY) == b"HIGH"


def test_the_batch_extracts_the_copy_the_game_loads(tmp_path):
    idx = ac._BsaMeshIndex(_two(tmp_path), tmp_path / "_staging",
                           plugin_order=["High.esp", "Low.esp"])
    got = idx.extract(KEY)
    assert got is not None and Path(got[0]).read_bytes() == b"LOW"


def test_a_listing_is_adopted_only_under_the_same_plugin_order(tmp_path):
    dirs = _two(tmp_path)
    sel = ac._BsaMeshIndex(dirs, None, plugin_order=["High.esp", "Low.esp"])
    assert sel.contains(KEY)
    assert not ac._BsaMeshIndex(dirs, None).adopt_listing(sel)
    assert not ac._BsaMeshIndex(dirs, None, plugin_order=["Low.esp", "High.esp"]
                                ).adopt_listing(sel)
    same = ac._BsaMeshIndex(dirs, None, plugin_order=["High.esp", "Low.esp"])
    assert same.adopt_listing(sel) and same.read_bytes(KEY) == b"LOW"


def test_the_plugin_order_is_read_unless_switched_off(monkeypatch):
    monkeypatch.setattr(ac.paths, "active_plugins_ordered", lambda lay: ["A.esp"])
    assert ac._bsa_plugin_order(object()) == ["A.esp"]
    monkeypatch.setenv(OFF, "1")
    assert ac._bsa_plugin_order(object()) is None
