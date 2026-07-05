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

"""_find_master_path caching: resolves case-insensitively with first-dir-wins,
and the per-run index cache is transparent (same result warm) but refreshable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import ube_patcher as up


def _touch(d, name):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"TES4\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")


def _fresh(tmp):
    # These tests run via module-level invocation with a PERSISTENT dir; files
    # from a prior run would pre-populate the index and break the assertions.
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return tmp


def test_resolves_case_insensitive_first_dir_wins(tmp_path):
    tmp_path = _fresh(tmp_path)
    up.clear_master_path_cache()
    d1, d2 = tmp_path / "a", tmp_path / "b"
    _touch(d1, "Skyrim.esm")
    _touch(d2, "Skyrim.esm")      # a second copy in a later dir
    _touch(d2, "OnlyInB.esp")
    dirs = [d1, d2]
    # exact + case-insensitive resolution
    assert up._find_master_path("Skyrim.esm", dirs) == d1 / "Skyrim.esm"   # d1 wins
    assert up._find_master_path("skyrim.esm", dirs) == d1 / "Skyrim.esm"   # ci
    assert up._find_master_path("onlyinb.esp", dirs) == d2 / "OnlyInB.esp"
    assert up._find_master_path("nope.esp", dirs) is None
    print("  test_resolves_case_insensitive_first_dir_wins OK")


def test_cache_is_transparent_and_refreshable(tmp_path):
    tmp_path = _fresh(tmp_path)
    up.clear_master_path_cache()
    d = tmp_path / "m"
    _touch(d, "One.esp")
    dirs = [d]
    assert up._find_master_path("one.esp", dirs) == d / "One.esp"
    r1 = up._find_master_path("one.esp", dirs)   # warm hit -> same
    assert r1 == d / "One.esp"
    # a NEW file added after the index was built is NOT seen (static-dir cache)...
    _touch(d, "Two.esp")
    assert up._find_master_path("two.esp", dirs) is None
    # ...until the cache is cleared.
    up.clear_master_path_cache()
    assert up._find_master_path("two.esp", dirs) == d / "Two.esp"
    print("  test_cache_is_transparent_and_refreshable OK")


test_resolves_case_insensitive_first_dir_wins(
    Path(__file__).resolve().parent / "_tmp_mpc")
test_cache_is_transparent_and_refreshable(
    Path(__file__).resolve().parent / "_tmp_mpc2")
