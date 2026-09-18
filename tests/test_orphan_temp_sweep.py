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

"""A run killed mid-write leaves the atomic writer's temp file behind. #orphan-temps

The destination itself is safe -- absent, or the previous complete file -- but
the temp stays. `.nifsave.tmp` / `.trisave.tmp` are overwritten by the next run;
mkstemp's random `<name>.<8 chars>.tmp` (every ESP, INI, XML, JSON and copied NIF
write) piles up, and every output scan globs `*.nif`, so nothing ever saw them.
"""
import json
import os
import time

from src import atomic_io

OURS_OLD = (
    "meshes/armor/a.nif.nifsave.tmp",
    "meshes/armor/a.tri.trisave.tmp",
    "CBBE_to_UBE_Combined.esp.ab12cd_3.tmp",
    "meshes/armor/physics/a.xml.k9z8y7w6.tmp",
)
NOT_OURS = (
    "meshes/armor/a.nif",
    "notes.tmp",
    "readme.txt.bak",
    "photo.12345678.tmp",
)


def _plant(root, rel, mtime):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    os.utime(p, (mtime, mtime))
    return p


def test_old_temps_from_every_writer_go_and_everything_else_stays(tmp_path):
    started = time.time()
    for rel in OURS_OLD + NOT_OURS:
        _plant(tmp_path, rel, started - 3600)
    live = _plant(tmp_path, "meshes/armor/b.nif.nifsave.tmp", started + 5)
    removed = atomic_io.sweep_orphan_temps(tmp_path, started)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in removed) == sorted(OURS_OLD)
    for rel in OURS_OLD:
        assert not (tmp_path / rel).exists(), rel
    for rel in NOT_OURS:
        assert (tmp_path / rel).exists(), f"{rel} is not the converter's temp"
    assert live.exists(), "a temp newer than the run start can be a write in progress"


def test_the_pattern_matches_the_names_the_writers_really_leave(tmp_path, monkeypatch):
    """Pinned to the writers, not to names typed into this file: each writer
    runs with the final swap disabled, so its real temp is left on disk."""
    monkeypatch.setattr(atomic_io, "_swap_into_place", lambda tmp, dst: None)

    class FakeNif:
        filepath = None

        def save(self):
            with open(self.filepath, "wb") as fh:
                fh.write(b"NIF")

    class FakeTri:
        def save(self, path):
            with open(path, "wb") as fh:
                fh.write(b"TRI")

    atomic_io.atomic_write_bytes(tmp_path / "Combined.esp", b"ESP")
    atomic_io.atomic_nif_save(FakeNif(), tmp_path / "armor.nif")
    atomic_io.atomic_tri_save(FakeTri(), tmp_path / "armor.tri")
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) == 3, left
    found = atomic_io.orphan_temps(tmp_path, time.time() + 60)
    assert sorted(p.name for p in found) == left


def test_a_missing_output_folder_is_nothing_to_sweep(tmp_path):
    assert atomic_io.sweep_orphan_temps(tmp_path / "absent", time.time()) == []


def test_a_run_removes_them_counts_one_warning_and_reports_the_count(
        tmp_path, monkeypatch, capsys):
    """End to end through `_cmd_convert`, against a control run that differs only
    in the planted temp."""
    from tests.test_vanilla_sweep import (
        _read_failures, _run_cmd_convert_with_sweep_result, _tally)
    sweep = {"files": 1, "files_changed": 0, "shapes_fixed": 0, "pool_error": None}
    _, clean_log = _run_cmd_convert_with_sweep_result(
        tmp_path / "clean", monkeypatch, capsys, sweep)
    dirty = tmp_path / "dirty"
    orphan = _plant(dirty / "out", "meshes/armor/x.nif.nifsave.tmp", time.time() - 3600)
    _, dirty_log = _run_cmd_convert_with_sweep_result(dirty, monkeypatch, capsys, sweep)

    assert not orphan.exists()
    assert "removed 1 orphaned temp file(s)" in dirty_log
    assert "orphaned temp" not in clean_log
    assert _tally(dirty_log)[1] == _tally(clean_log)[1] + 1, (
        "removed orphans must add exactly one warning to the tally")
    assert [f for f in _read_failures(dirty) if f["kind"] == "orphaned temp files removed"]
    report = json.loads((dirty / "out" / "conversion_report.json").read_text(encoding="utf-8"))
    assert report["orphan_temps_removed"] == 1
    clean_report = json.loads(
        (tmp_path / "clean" / "out" / "conversion_report.json").read_text(encoding="utf-8"))
    assert clean_report["orphan_temps_removed"] == 0
