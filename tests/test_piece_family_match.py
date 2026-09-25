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

r"""#piece-family-match -- the post-merge passes leave a user's copy alone.

THE DEFECT. Five post-merge passes (alt-texture reconcile, armature dedup,
hands-slot fix, master re-sort, postflight) globbed `<stem>*<suffix>`, so a
`Combined - Copy.esp` or `Combined_backup.esp` the user kept in the output
folder was loaded, rewritten and reported "validated clean" on every run. The
stale-piece delete already matched `<stem><digits><suffix>` only; all six now
share one matcher. Live: nothing else of that stem in the output folder.
`CBBE2UBE_NO_PIECE_FAMILY_MATCH=1` globs the broad family again.
"""
from pathlib import Path

import pytest

from src import esp
from src import ube_patcher as up

OFF = "CBBE2UBE_NO_PIECE_FAMILY_MATCH"
COPIES = ("Combined - Copy.esp", "Combined_backup.esp", "CombinedNotes.esp")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _plugin(path, masters):
    esp.ESP(header=esp.TES4Header(masters=list(masters)), groups=[]).save(path)
    return path


def _folder(tmp_path):
    """The Combined and its piece, sorted masters; copies with a master list
    the re-sort would rewrite (a master-tier plugin after a regular one)."""
    comb = _plugin(tmp_path / "Combined.esp", ["Skyrim.esm", "Mod.esp"])
    _plugin(tmp_path / "Combined2.esp", ["Skyrim.esm", "Mod.esp"])
    for n in COPIES:
        _plugin(tmp_path / n, ["Mod.esp", "Skyrim.esm"])
    before = {n: (tmp_path / n).read_bytes() for n in COPIES}
    return comb, before


def _loads(monkeypatch):
    seen = []
    orig = esp.ESP.load.__func__

    def load(cls, path):
        seen.append(Path(path).name)
        return orig(cls, path)
    monkeypatch.setattr(esp.ESP, "load", classmethod(load))
    return seen


PASSES = {
    "reconcile": lambda c, t: up.reconcile_alt_texture_indices_all(c, t / "meshes"),
    "dedup": lambda c, t: up.dedup_armo_armature_refs_all(c),
    "hand_slot": lambda c, t: up.fix_spurious_hand_slot(c, t / "meshes"),
    "resort": lambda c, t: up.resort_masters_all(c, master_data_dirs=[t]),
    "postflight": lambda c, t: up.postflight_validate_combined(c, t / "meshes",
                                                               master_data_dirs=[t]),
}


@pytest.mark.parametrize("name", sorted(PASSES))
def test_a_pass_reads_only_the_combined_and_its_pieces(tmp_path, monkeypatch, name):
    comb, before = _folder(tmp_path)
    seen = _loads(monkeypatch)
    PASSES[name](comb, tmp_path)
    assert set(seen) <= {"Combined.esp", "Combined2.esp"}, seen
    assert {n: (tmp_path / n).read_bytes() for n in COPIES} == before


def test_the_re_sort_does_not_rewrite_or_count_a_copy(tmp_path):
    comb, before = _folder(tmp_path)
    assert up.resort_masters_all(comb, master_data_dirs=[tmp_path]) == 0
    assert {n: (tmp_path / n).read_bytes() for n in COPIES} == before


def test_the_postflight_does_not_report_a_copy(tmp_path):
    comb, _ = _folder(tmp_path)
    pf = up.postflight_validate_combined(comb, tmp_path / "meshes",
                                         master_data_dirs=[tmp_path])
    assert sorted(pf["pieces"]) == ["Combined.esp", "Combined2.esp"]


def test_switched_off_a_copy_is_rewritten_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    comb, before = _folder(tmp_path)
    assert up.resort_masters_all(comb, master_data_dirs=[tmp_path]) == len(COPIES)
    assert all((tmp_path / n).read_bytes() != before[n] for n in COPIES)


@pytest.mark.parametrize("name, tail", [
    ("Combined.esp", ""), ("Combined2.esp", "2"), ("combined12.ESP", "12"),
    ("Combined - Copy.esp", None), ("Combined_backup.esp", None),
    ("CombinedNotes.esp", None), ("Other.esp", None), ("Combined.esm", None)])
def test_the_one_matcher(name, tail):
    assert up._combined_piece_tail(name, "Combined", ".esp") == tail
