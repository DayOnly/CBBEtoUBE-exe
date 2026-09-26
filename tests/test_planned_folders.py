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

r"""#planned-folders -- a folder is spelled by the plan, not by a race.

Windows keeps the spelling a folder was created with, and a worker created its
piece's folder when it started writing. On a fresh output the batch-wide
schedule therefore named `meshes\!UBE\armor` after the vanilla sweep's largest
piece where one source at a time named it `Armor` after an earlier mod (485
files, every byte the same). Each source now creates its pieces' folders at
the end of its planning, in plan order.

Pinned here through the real planning steps and a writing worker: the first
source to plan a folder spells it on both schedules, whatever converts first
(and switched off, the schedule's race is back -- the control that this test
can see a spelling at all); a folder only a skipped piece asked for is removed
again, one that was there before is not; `_cmd_convert` shares one list across
the batch and empties it once the pool is down, on both schedules.
"""
import os
from pathlib import Path

import pytest

from src import auto_convert as ac
from src.nif_convert import ConvertResult
from tests.test_global_schedule import (SWITCH as SCHEDULE, _no_pynifly, _Pool,
                                        _setup_batch, _steps_recording, _item)
from tests.test_npc_worn_nonplayable import load_order  # noqa: F401 (fixture)

SWITCH = "CBBE2UBE_NO_PLANNED_FOLDERS"


def _case_insensitive(tmp_path) -> bool:
    (tmp_path / "CaseProbe").mkdir()
    return (tmp_path / "caseprobe").is_dir()


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.delenv(SCHEDULE, raising=False)


def _worker(skip=()):
    """Writes like the converter: the folder is created when the piece starts,
    and a skipped piece creates none."""
    def _convert(item):
        if Path(item[1]).name in skip:
            return ConvertResult(src_path=Path(item[0]), dst_path=None,
                                 status="skipped", reason="no armor shapes")
        Path(item[1]).parent.mkdir(parents=True, exist_ok=True)
        Path(item[1]).write_bytes(b"converted")
        return ConvertResult(src_path=Path(item[0]), dst_path=str(item[1]),
                             status="converted (copy)")
    return _convert


def _admit(monkeypatch):
    """These placeholder pieces are on no slot the plugin names; the crash
    guard keeps such a piece only when it is skinned to the body, so say it
    is. The load check afterwards gets a loader that rejects the bytes."""
    monkeypatch.setattr(ac, "_nif_has_bodyfit_skin", lambda p: True)
    _no_pynifly(monkeypatch)


def _two_sources(load_order, tmp_path, monkeypatch):
    """The first source plans `Armor/set/small_1.nif`, the second, later in
    source order, `armor/set/big_1.nif` -- the larger, so the batch-wide
    schedule converts it first."""
    small, big = tmp_path / "small_1.nif", tmp_path / "big_1.nif"
    small.write_bytes(b"s")
    big.write_bytes(b"b" * 4096)
    plans = iter([[(small, "Armor/set/small_1.nif")],
                  [(big, "armor/set/big_1.nif")]])
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: next(plans))
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker())
    _admit(monkeypatch)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    return ref


def _convert_two(load_order, tmp_path, monkeypatch, *, one_schedule):
    ref = _two_sources(load_order, tmp_path, monkeypatch)
    out = tmp_path / "out"
    mgr = ac._NifPool(1, pool_factory=lambda: _Pool())
    claimed: set = set()
    made: list = []
    kw = dict(nif_pool=mgr, claimed_dst_paths=claimed, ube_body_ref_path=ref,
              master_data_dirs=[], planned_folders=made)
    if one_schedule:
        results: list = []
        ac._convert_sources_global(
            [load_order, load_order], results, mgr, claimed,
            make_steps=lambda s: ac._auto_convert_mod_steps(
                s, out, batch_schedule=True, **kw),
            convert_serial=lambda s: pytest.fail("no serial retry here"),
            checkpoint=lambda view, progress: None)
        assert [e for _s, _r, e in results] == [None, None]
    else:
        for s in (load_order, load_order):
            ac.auto_convert_mod(s, out, **kw)
    ac._remove_empty_planned_folders(made)
    root = out / "meshes" / "!UBE"
    assert (root / "armor" / "set" / "small_1.nif").is_file()
    assert (root / "armor" / "set" / "big_1.nif").is_file()
    return os.listdir(root)


@pytest.mark.parametrize("one_schedule", [True, False],
                         ids=["one-schedule", "one-source-at-a-time"])
def test_the_first_source_to_plan_a_folder_spells_it(
        load_order, tmp_path, monkeypatch, one_schedule):
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system to keep one spelling")
    assert _convert_two(load_order, tmp_path, monkeypatch,
                        one_schedule=one_schedule) == ["Armor"]


def test_switched_off_the_first_piece_converted_spells_it(load_order, tmp_path, monkeypatch):
    """The control: the race is real and this test sees it. Switched off, the
    batch-wide schedule converts the later source's larger piece first and its
    spelling names the folder."""
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system to keep one spelling")
    monkeypatch.setenv(SWITCH, "1")
    assert _convert_two(load_order, tmp_path, monkeypatch, one_schedule=True) == ["armor"]


@pytest.mark.parametrize("one_schedule", [True, False],
                         ids=["one-schedule", "one-source-at-a-time"])
def test_a_folder_a_skipped_piece_made_keeps_its_spelling_for_a_later_source(
        load_order, tmp_path, monkeypatch, one_schedule):
    """The first source's only piece in `Armor\\x` is skipped, so its folder is
    empty when that source finishes; a later source then writes `armor\\x`.
    The folder is removed only once the whole batch is done: removed at the
    first source's finish, the later piece would create it again with its own
    spelling on the batch-wide schedule -- and on that schedule a piece of
    another source can be writing into a folder while it is still empty."""
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system to keep one spelling")
    big, small = tmp_path / "skip_1.nif", tmp_path / "keep_1.nif"
    big.write_bytes(b"b" * 4096)
    small.write_bytes(b"s")
    plans = iter([[(big, "Armor/x/skip_1.nif")], [(small, "armor/x/keep_1.nif")]])
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: next(plans))
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker(skip={"skip_1.nif"}))
    _admit(monkeypatch)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    out = tmp_path / "out"
    mgr = ac._NifPool(1, pool_factory=lambda: _Pool())
    claimed: set = set()
    made: list = []
    kw = dict(nif_pool=mgr, claimed_dst_paths=claimed, ube_body_ref_path=ref,
              master_data_dirs=[], planned_folders=made)
    if one_schedule:
        ac._convert_sources_global(
            [load_order, load_order], [], mgr, claimed,
            make_steps=lambda s: ac._auto_convert_mod_steps(
                s, out, batch_schedule=True, **kw),
            convert_serial=lambda s: pytest.fail("no serial retry here"),
            checkpoint=lambda view, progress: None)
    else:
        for s in (load_order, load_order):
            ac.auto_convert_mod(s, out, **kw)
    ac._remove_empty_planned_folders(made)
    root = out / "meshes" / "!UBE"
    assert (root / "armor" / "x" / "keep_1.nif").is_file()
    assert os.listdir(root) == ["Armor"]


@pytest.mark.parametrize("switched_off", [False, True], ids=["on", "switched-off"])
def test_a_folder_only_a_skipped_piece_asked_for_is_removed(
        load_order, tmp_path, monkeypatch, switched_off):
    """The planner makes the folder before it knows the piece will be skipped;
    what it made and is still empty is removed, and a folder that was there
    before stays, empty or not. Switched off nothing is made, so nothing is
    left either way."""
    if switched_off:
        monkeypatch.setenv(SWITCH, "1")
    src = tmp_path / "piece_1.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: [
        (src, "armor/kept/piece_1.nif"), (src, "armor/gone/deep/skip_1.nif"),
        (src, "armor/old/skip2_1.nif")])
    monkeypatch.setattr(ac, "_nif_convert_worker",
                        _worker(skip={"skip_1.nif", "skip2_1.nif"}))
    _admit(monkeypatch)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    root = tmp_path / "out" / "meshes" / "!UBE"
    (root / "armor" / "old").mkdir(parents=True)        # an earlier run's
    ac.auto_convert_mod(load_order, tmp_path / "out", nif_workers=1,
                        ube_body_ref_path=ref, master_data_dirs=[])
    assert (root / "armor" / "kept" / "piece_1.nif").is_file()
    assert not (root / "armor" / "gone").exists()
    assert (root / "armor" / "old").is_dir()


def test_the_folders_are_made_in_plan_order_and_recorded_ancestors_first(tmp_path):
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system to keep one spelling")
    root = tmp_path / "meshes" / "!UBE"
    (root / "armor").mkdir(parents=True)                # there before: kept
    made: list = []
    ac._make_planned_folders([("s", root / "Armor" / "a" / "x_1.nif"),
                              ("s", root / "armor" / "A" / "B" / "y_1.nif"),
                              ("s", root / "clothes" / "c" / "z_1.nif")], made)
    assert [str(p.relative_to(root)) for p in made] == [
        os.path.join("Armor", "a"), os.path.join("armor", "A", "B"),
        "clothes", os.path.join("clothes", "c")]
    assert os.listdir(root / "armor") == ["a"], "the first to plan it spells it"
    assert os.listdir(root / "armor" / "a") == ["B"]
    (root / "armor" / "a" / "B" / "y_1.nif").write_bytes(b"converted")
    assert ac._remove_empty_planned_folders(made) == 2 and made == []
    assert os.listdir(root) == ["armor"], "a folder that was there before stays"
    assert os.listdir(root / "armor" / "a") == ["B"], "one that is not empty stays"


@pytest.mark.parametrize("switched_off", [False, True],
                         ids=["one-schedule", "one-source-at-a-time"])
def test_the_batch_shares_one_list_and_empties_it_after_the_pool(
        tmp_path, monkeypatch, capsys, switched_off):
    if switched_off:
        monkeypatch.setenv(SCHEDULE, "1")
    a, b = tmp_path / "ModA", tmp_path / "ModB"
    a.mkdir()
    b.mkdir()
    ns = _setup_batch(tmp_path, monkeypatch, _Pool(), [a, b])
    lists: list = []
    inner = _steps_recording([], lambda n: [_item(n)])

    def _steps(source_dir, output_dir, **kw):
        made = kw["planned_folders"]
        lists.append(made)
        d = tmp_path / "made" / Path(source_dir).name
        d.mkdir(parents=True)
        made.append(d)
        return (yield from inner(source_dir, output_dir, **kw))
    monkeypatch.setattr(ac, "_auto_convert_mod_steps", _steps)
    assert ac._cmd_convert(ns) == 0, capsys.readouterr().out
    assert len(lists) == 2 and lists[0] is lists[1], "one list for the batch"
    assert lists[0] == [], "emptied after the batch"
    assert not (tmp_path / "made" / "ModA").exists()
    assert not (tmp_path / "made" / "ModB").exists()
