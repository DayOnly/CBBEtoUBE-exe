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

"""#esp-cache-release -- the mod-list refresh releases the ESP parse cache.

`list_convertible_mods` (the GUI's mod list, and scan_ube_native) parses every
enabled plugin through `ESP.load_cached` to decide which mods carry armour,
and returns only names and counts. The cache never evicts, so those parses
stayed in the GUI process for the whole session; the converter child parses
what it needs on its own. The scan is stubbed here to parse one real plugin the
way the real scan does, so the tests see a populated cache before the call
returns."""
import pytest

from src import auto_convert as ac
from src import esp
from tests.test_vanilla_sweep import _arma, _armo, _mk_data_dir


def _plugin(tmp_path):
    data = _mk_data_dir(tmp_path, [_arma(0x800, "ArmorA", 1 << 2, "armor\\x\\a_1.nif")],
                        [_armo(0x801, "ArmorO", 0x800, 1 << 2)])
    return data / "Skyrim.esm"


@pytest.fixture
def discovery(monkeypatch, tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    plugin = _plugin(tmp_path)
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda *a, **k: ac.paths.Layout(mods_root=mods))
    monkeypatch.setattr(ac.paths, "export_to_env", lambda lay: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: None)
    monkeypatch.setattr(ac.nif_convert, "_find_cbbe_base_body", lambda *a, **k: None)
    monkeypatch.setattr(ac.nif_convert, "_find_ube_femalebody", lambda *a, **k: None)
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda *a, **k: None)
    monkeypatch.setenv("CBBE2UBE_NO_VANILLA_SWEEP", "1")
    seen = {}

    def _scan(*a, **k):
        esp.ESP.load_cached(plugin)              # what the real scan does per plugin
        seen["entries"] = len(esp._LOAD_CACHE)
        if k.get("progress") == "raise":
            raise RuntimeError("the scan failed half-way")
        return [{"name": "SomeArmorMod", "armor_nifs": 3}]

    monkeypatch.setattr(ac, "_find_armor_mod_dirs", _scan)
    esp._LOAD_CACHE.clear()
    yield seen
    esp._LOAD_CACHE.clear()


def test_the_mod_list_leaves_no_parsed_plugin_behind(discovery):
    assert ac.list_convertible_mods() == [{"name": "SomeArmorMod", "nifs": 3}]
    assert discovery.get("entries", 0) >= 1, (
        "the scan never parsed a plugin, so nothing below measures anything")
    assert len(esp._LOAD_CACHE) == 0, "the parses stayed in memory after the list was built"


def test_a_scan_that_fails_releases_the_cache_too(discovery):
    with pytest.raises(RuntimeError):
        ac.list_convertible_mods(progress="raise")
    assert discovery.get("entries", 0) >= 1
    assert len(esp._LOAD_CACHE) == 0


def test_clear_load_cache_says_what_it_dropped(tmp_path):
    plugin = _plugin(tmp_path)
    esp._LOAD_CACHE.clear()
    esp.ESP.load_cached(plugin)
    assert esp.clear_load_cache() == 1
    assert not esp._LOAD_CACHE
