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

r"""#sweep-piece-family -- the sweep's read-back after the merge reads the
merge's own files, not a user's copy of an old Combined.

THE DEFECT. After the merge the sweep keeps its moves only when the new
Combined names none of the moved bases. It read every `<stem>*.esp` beside
the Combined, so a user's `<stem> - Copy.esp` or `<stem>_backup.esp` of an
older Combined -- which names the old meshes -- put every move back on every
run, with a warning; an unreadable copy did the same. #piece-family-match had
narrowed every other post-merge reader to the Combined and its numbered split
pieces; the sweep's read-back was never moved onto that matcher.

THE RULE. The read-back walks `ube_patcher._combined_piece_family`, which also
honours `CBBE2UBE_NO_PIECE_FAMILY_MATCH`. `CBBE2UBE_NO_SWEEP_PIECE_FAMILY=1`
restores the old glob.
"""
import pytest

from src import stale_sweep as ss
from src.esp import encode_subrecord, encode_zstring
from tests.test_selection_winner_playable import _plugin, _rec
from tests.test_stale_output_sweep import (
    IRON, OLD, WHOLE, World, _finish, _sweep, _vanilla)
# The autouse fixture of the sweep's own tests: clean switches and caches.
from tests.test_stale_output_sweep import _clean  # noqa: F401

SWITCH = "CBBE2UBE_NO_SWEEP_PIECE_FAMILY"
SHARED = "CBBE2UBE_NO_PIECE_FAMILY_MATCH"
IRON_M = "!UBE\\armor\\iron\\cuirass_1.nif"
OLD_M = "!UBE\\armor\\old\\cuirass_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.delenv(SHARED, raising=False)


def _esp(w, name, *models):
    recs = [_rec(b"ARMA", 0x01000800 + i, encode_subrecord(b"MOD3", encode_zstring(m)))
            for i, m in enumerate(models)]
    _plugin(w.out / name, ARMA=recs)


@pytest.fixture
def moved(tmp_path):
    """A full run moved OLD; the new Combined names only the current mesh."""
    w = World(tmp_path)
    w.mesh(IRON, "_0.nif", "_1.nif", ".tri")
    w.mesh(OLD, *WHOLE)
    w.record({OLD: "Old Mod", IRON: "vanilla"})
    results = [_vanilla(w)]
    _sweep(w, results)
    assert not w.has(OLD)
    _esp(w, "Combined.esp", IRON_M)
    return w, results


@pytest.mark.parametrize("copy", ["Combined - Copy.esp", "Combined_backup.esp",
                                  "combined old.esp"])
def test_a_copy_of_an_old_combined_does_not_put_the_moves_back(moved, copy):
    w, results = moved
    _esp(w, copy, IRON_M, OLD_M)
    assert _finish(w, results) == 0
    assert not w.has(OLD) and w.moved(OLD).is_file()


def test_an_unreadable_copy_does_not_put_the_moves_back(moved):
    w, results = moved
    (w.out / "Combined - Copy.esp").write_bytes(b"")
    assert _finish(w, results) == 0
    assert not w.has(OLD) and w.moved(OLD).is_file()


def test_a_numbered_split_piece_naming_a_moved_mesh_still_puts_it_back(moved):
    w, results = moved
    _esp(w, "Combined2.esp", OLD_M)
    assert ss.combined_references(w.out / "Combined.esp", {OLD}) == [
        f"Combined2.esp: {OLD_M}"]
    assert _finish(w, results) >= 1
    assert w.has(OLD) and not w.moved(OLD).exists()


def test_an_unreadable_split_piece_still_puts_them_back(moved):
    w, results = moved
    (w.out / "Combined2.esp").write_bytes(b"")
    assert _finish(w, results) >= 1
    assert w.has(OLD)


def test_switched_off_a_copy_puts_every_move_back(moved, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    w, results = moved
    _esp(w, "Combined - Copy.esp", IRON_M, OLD_M)
    assert ss.combined_references(w.out / "Combined.esp", {OLD}) == [
        f"Combined - Copy.esp: {OLD_M}"]
    assert _finish(w, results) >= 1
    assert w.has(OLD)


def test_the_shared_matchers_switch_reads_the_copy_too(moved, monkeypatch):
    """One matcher for the merge's files: its own off-switch reaches here."""
    monkeypatch.setenv(SHARED, "1")
    w, results = moved
    _esp(w, "Combined - Copy.esp", OLD_M)
    assert _finish(w, results) >= 1
    assert w.has(OLD)
