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

r"""#alttex-source-winner -- the reconcile checks an archive-only source against
the archive the convert step extracted it from.

THE DEFECT. The convert step extracts an archive-only mesh from the archive
whose plugin loads later (#bsa-load-order-winner). The reconcile accepted the
staged copy only when its bytes equalled the archive's, but read the archive
through an index built without the plugin order -- the MO2-first archive. Where
MO2 priority and plugin load order disagree the bytes never matched, the source
counted as unreadable and the colour entries of a same-named layer were
dropped. `CBBE2UBE_NO_ALTTEX_SOURCE_WINNER=1` reads the MO2-first archive again.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, paths, ube_patcher as up       # noqa: E402
from tests.test_bsa_seek_read import _write_bsa                     # noqa: E402

OFF = "CBBE2UBE_NO_ALTTEX_SOURCE_WINNER"
KEY = "armor/x/cuirass_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, "CBBE2UBE_NO_BSA_LOAD_ORDER_WINNER"):
        monkeypatch.delenv(k, raising=False)


def _modlist(monkeypatch, tmp_path, plugins=("High.esp", "Low.esp")):
    """MO2 priority: ModHigh above ModLow, each archive holding the mesh; the
    plugin order given (by default Low.esp loads later, so its archive wins)."""
    mods = tmp_path / "mods"
    for mod, arch, data in (("ModHigh", "High.bsa", b"HIGH"),
                            ("ModLow", "Low.bsa", b"LOW")):
        _write_bsa(mods / mod / arch,
                   [(r"meshes\armor\x", "cuirass_1.nif", data, None)])

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(paths, "mods_root", lambda: mods)
    monkeypatch.setattr(paths, "enabled_mods_ordered",
                        lambda lay: ["ModHigh", "ModLow"])
    monkeypatch.setattr(paths, "active_plugins_ordered",
                        lambda lay: list(plugins))
    monkeypatch.setitem(ac._BATCH_MESH_INDEX, str(mods).lower(), {})
    return mods


def _staged(tmp_path, data: bytes) -> Path:
    """The copy the convert step extracted to our output's staging folder."""
    p = tmp_path / "out" / "_bsa_staging" / "meshes" / "armor" / "x" / "cuirass_1.nif"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_the_copy_the_convert_step_staged_is_the_source(monkeypatch, tmp_path):
    _modlist(monkeypatch, tmp_path)
    staged = _staged(tmp_path, b"LOW")          # the later plugin's archive
    assert up._alttex_source_paths(tmp_path / "out" / "meshes", [KEY]) == {
        KEY: staged}


def test_a_staged_copy_from_the_other_archive_is_not_the_source(monkeypatch,
                                                                 tmp_path):
    """Negative control: the check still compares bytes."""
    _modlist(monkeypatch, tmp_path)
    _staged(tmp_path, b"HIGH")
    assert up._alttex_source_paths(tmp_path / "out" / "meshes", [KEY]) == {}


def test_with_the_load_order_winner_off_both_steps_take_the_mo2_order(
        monkeypatch, tmp_path):
    monkeypatch.setenv("CBBE2UBE_NO_BSA_LOAD_ORDER_WINNER", "1")
    _modlist(monkeypatch, tmp_path)
    staged = _staged(tmp_path, b"HIGH")
    assert up._alttex_source_paths(tmp_path / "out" / "meshes", [KEY]) == {
        KEY: staged}


def test_switched_off_the_mo2_first_archive_is_read_again(monkeypatch,
                                                          tmp_path):
    monkeypatch.setenv(OFF, "1")
    _modlist(monkeypatch, tmp_path)
    _staged(tmp_path, b"LOW")
    assert up._alttex_source_paths(tmp_path / "out" / "meshes", [KEY]) == {}
