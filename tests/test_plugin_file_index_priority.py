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

"""Guard for plugin_file_index MO2-priority resolution (#plugin-priority).

Resolving a plugin filename to a physical file is a mod-PRIORITY question: when
two mods ship the same plugin filename (a mod + its patch as separate MO2 mods),
the winner scan must read the highest-priority mod's copy. Arbitrary os.walk
(alphabetical) order could pick the loser -> wrong ARMO records (slots, models,
alt-textures) feed the winner index.
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import paths


def _mk(p: Path, data=b"TES4"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_duplicate_plugin_resolves_to_higher_priority_mod(tmp_path):
    inst = tmp_path / "inst"
    mods = inst / "mods"
    # Both mods ship Foo.esp; ModHigh is higher MO2 priority (listed first).
    _mk(mods / "ModHigh" / "Foo.esp")
    _mk(mods / "ModLow" / "Foo.esp")
    _mk(mods / "ModLow" / "Bar.esp")           # unique -> unambiguous
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True, exist_ok=True)
    # MO2 modlist.txt: top line = highest priority = wins.
    (prof / "modlist.txt").write_text("+ModHigh\n+ModLow\n", encoding="utf-8")

    lay = paths.Layout(mods_root=mods, instance_dir=inst,
                       selected_profile="Default")
    idx = paths.plugin_file_index(lay)
    assert idx["foo.esp"] == mods / "ModHigh" / "Foo.esp", \
        "duplicate plugin must resolve to the higher-priority mod"
    assert idx["bar.esp"] == mods / "ModLow" / "Bar.esp"


def test_priority_flips_with_modlist_order(tmp_path):
    # Same layout, reversed priority -> the OTHER copy wins. Proves it's the
    # modlist order, not filesystem/alphabetical, that decides.
    inst = tmp_path / "inst"
    mods = inst / "mods"
    _mk(mods / "ModHigh" / "Foo.esp")
    _mk(mods / "ModLow" / "Foo.esp")
    prof = inst / "profiles" / "Default"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "modlist.txt").write_text("+ModLow\n+ModHigh\n", encoding="utf-8")
    lay = paths.Layout(mods_root=mods, instance_dir=inst,
                       selected_profile="Default")
    idx = paths.plugin_file_index(lay)
    assert idx["foo.esp"] == mods / "ModLow" / "Foo.esp"


def test_no_modlist_still_indexes_all(tmp_path):
    # No instance_dir/modlist -> falls back to a deterministic sorted walk but
    # still finds every plugin (no regression when priority info is absent).
    mods = tmp_path / "mods"
    _mk(mods / "ModX" / "Baz.esp")
    _mk(mods / "ModY" / "Qux.esm")
    lay = paths.Layout(mods_root=mods)
    idx = paths.plugin_file_index(lay)
    assert idx["baz.esp"] == mods / "ModX" / "Baz.esp"
    assert idx["qux.esm"] == mods / "ModY" / "Qux.esm"
