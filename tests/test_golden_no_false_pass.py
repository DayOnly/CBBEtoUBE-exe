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

"""#golden-no-false-pass -- `golden_output.py check` must not PASS on nothing.

Two ways the golden harness said "PASS: output identical to the baseline"
without having looked:

  * STALE WORK FOLDER. A run killed part-way left its converted NIFs and
    sidecars in `golden/_check`, and the converter does not delete an old
    output when a conversion raises or skips. The next check fingerprinted the
    old files: a piece that now converts to nothing, or no longer writes its
    physics XML, read `ok`.
  * NOTHING COMPARED. A piece whose source changed only counted as "source
    changed", pieces missing from the baseline were dropped without a word, and
    a capture that found nothing wrote an empty manifest. 0 of N compared read
    as PASS with exit 0.

The unit of work here is the REAL `_measure`: only the conversion itself
(`_convert`, which reads a mesh from the user's mod list) and the NIF reader
(`_fingerprint`) are replaced. The fake conversion behaves like the real
worker where it matters: when it fails it writes nothing and deletes nothing.
"""
from pathlib import Path

import numpy as np
import pytest

from scripts import golden_output as go

_SUB = "armor/test/f"


def _piece(label):
    return (label, _SUB, label, go.SLOT_BODY, "test piece")


class _Mods:
    """A fake mod list and converter. `mode[label]` is what converting that
    piece does now: 'write' (NIF + physics XML), 'noxml' (the NIF only),
    'nothing' (the conversion fails: nothing written, nothing removed),
    'missing' (no enabled mod has the piece), 'interrupt' (Ctrl+C)."""

    def __init__(self, root: Path):
        self.root = root
        self.mode = {}

    def source(self, label) -> Path:
        p = self.root / "mods" / "SomeMod" / "meshes" / _SUB / f"{label}_1.nif"
        if not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"source mesh " + label.encode())
        return p

    def convert(self, sub, stem, slots, out_root):
        mode = self.mode.get(stem, "write")
        if mode == "missing":
            return None, None
        if mode == "interrupt":
            raise KeyboardInterrupt
        src = self.source(stem)
        dst_dir = Path(out_root) / "meshes" / sub
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{stem}_1.nif"
        if mode in ("write", "noxml"):
            dst.write_bytes(b"converted " + stem.encode())
        if mode == "write":
            (dst_dir / f"{stem}_1.xml").write_bytes(b"<physics/>")
        return src, dst


def _fingerprint(nif_path):
    k = float(len(Path(nif_path).read_bytes()))
    return {"body": {"verts": np.full((3, 3), k, np.float32),
                     "bones": ["NPC Pelvis [Pelv]"], "wsum": np.array([1.0])}}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    mods = _Mods(tmp_path)
    monkeypatch.setattr(go, "GOLDEN", tmp_path / "golden")
    monkeypatch.setattr(go, "_convert", mods.convert)
    monkeypatch.setattr(go, "_fingerprint", _fingerprint)
    monkeypatch.setattr(go, "_flags", lambda: {})
    monkeypatch.setattr(go, "_git_head", lambda: "abc1234")

    def pieces(*labels):
        monkeypatch.setattr(go, "PIECES", [_piece(x) for x in labels])
    mods.pieces = pieces
    return mods


def _plant_killed_run(harness, label):
    """What a check killed after converting `label` leaves in its folder."""
    d = go.GOLDEN / "_check" / label / "meshes" / _SUB
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{label}_1.nif").write_bytes(b"converted " + label.encode())
    (d / f"{label}_1.xml").write_bytes(b"<physics/>")


# ------------------------------------------------------- the stale work folder

def _check_after_a_killed_run(harness, capsys, now):
    """The regression is exactly what golden exists to catch: a piece that
    converts to nothing, or a physics XML no longer written."""
    harness.pieces("cuirass", "boots")
    assert go.capture() == 0
    _plant_killed_run(harness, "cuirass")
    harness.mode["cuirass"] = now
    capsys.readouterr()
    rc = go.check(1e-4)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "PASS" not in out, out
    line = [ln for ln in out.splitlines() if ln.startswith("  cuirass")]
    assert line and ("REGRESSION" in line[0] or "FAIL" in line[0]), out
    return out


def test_a_killed_run_cannot_hide_a_piece_that_converts_to_nothing(harness, capsys):
    out = _check_after_a_killed_run(harness, capsys, "nothing")
    assert "conversion produced nothing" in out, out


def test_a_killed_run_cannot_hide_a_dropped_physics_xml(harness, capsys):
    out = _check_after_a_killed_run(harness, capsys, "noxml")
    assert "sidecar MISSING: cuirass_1.xml" in out, out


def test_the_same_regression_fails_without_a_leftover_folder(harness, capsys):
    """The control: the verdict above is the regression's, not the folder's."""
    harness.pieces("cuirass")
    assert go.capture() == 0
    harness.mode["cuirass"] = "nothing"
    assert go.check(1e-4) == 1
    assert "conversion produced nothing" in capsys.readouterr().out


def test_a_work_folder_that_cannot_be_emptied_stops_the_run(tmp_path, monkeypatch):
    work = tmp_path / "_check"
    work.mkdir()
    (work / "held.nif").write_bytes(b"x")
    monkeypatch.setattr(go.shutil, "rmtree", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="could not empty"):
        list(go._measured([_piece("a")], work, 1))


def test_an_interrupted_check_leaves_no_work_folder(harness):
    harness.pieces("cuirass", "boots")
    assert go.capture() == 0
    harness.mode["boots"] = "interrupt"
    with pytest.raises(KeyboardInterrupt):
        go.check(1e-4)
    assert not (go.GOLDEN / "_check").exists()
    harness.mode["boots"] = "write"
    assert go.check(1e-4) == 0
    assert not (go.GOLDEN / "_check").exists()
    assert not (go.GOLDEN / "_work").exists()


# ------------------------------------------------------------ nothing compared

def test_every_source_changed_is_not_a_pass(harness, capsys):
    harness.pieces("cuirass", "boots")
    assert go.capture() == 0
    for label in ("cuirass", "boots"):
        harness.source(label).write_bytes(b"a mod update")
    capsys.readouterr()
    assert go.check(1e-4) == 3
    out = capsys.readouterr().out
    assert "PASS" not in out, out
    assert "NOTHING COMPARED: 0 of 2 piece(s)" in out, out
    assert "not compared: 2 source changed" in out, out


def test_a_partial_check_says_how_much_it_looked_at(harness, capsys):
    harness.pieces("cuirass", "boots")
    assert go.capture() == 0
    harness.source("boots").write_bytes(b"a mod update")
    capsys.readouterr()
    assert go.check(1e-4) == 0
    out = capsys.readouterr().out
    assert "PASS (PARTIAL): 1 of 2 piece(s)" in out, out
    assert "compared 1 of 2 piece(s); not compared: 1 source changed" in out
    assert "PASS: output identical" not in out, out


def test_a_full_check_says_it_compared_every_piece(harness, capsys):
    harness.pieces("cuirass", "boots")
    assert go.capture() == 0
    capsys.readouterr()
    assert go.check(1e-4) == 0
    out = capsys.readouterr().out
    assert "PASS: output identical to the baseline -- all 2 piece(s)" in out
    assert "compared 2 of 2 piece(s)\n" in out, out


def test_a_piece_added_after_the_capture_is_named(harness, capsys):
    harness.pieces("cuirass")
    assert go.capture() == 0
    harness.pieces("cuirass", "newrow")
    capsys.readouterr()
    assert go.check(1e-4) == 0
    out = capsys.readouterr().out
    assert "newrow" in out and "NOT IN BASELINE" in out, out
    assert "PASS (PARTIAL): 1 of 2" in out, out
    assert "1 not in the baseline" in out, out


def test_a_baseline_file_gone_from_disk_is_named(harness, capsys):
    harness.pieces("cuirass", "boots")
    assert go.capture() == 0
    (go.GOLDEN / "boots.npz").unlink()
    capsys.readouterr()
    assert go.check(1e-4) == 0
    out = capsys.readouterr().out
    assert "boots" in out and "boots.npz is missing" in out, out
    assert "PASS (PARTIAL): 1 of 2" in out, out


def test_a_capture_that_found_nothing_writes_no_baseline(harness, capsys):
    harness.pieces("cuirass")
    harness.mode["cuirass"] = "missing"
    assert go.capture() == 1
    assert "baseline NOT written: 0 of 1" in capsys.readouterr().out
    assert not (go.GOLDEN / "manifest.json").exists()
    assert go.check(1e-4) == 2      # no baseline: a refusal, not a PASS


def test_a_capture_that_found_nothing_keeps_the_old_baseline(harness):
    harness.pieces("cuirass")
    assert go.capture() == 0
    before = (go.GOLDEN / "manifest.json").read_bytes()
    harness.mode["cuirass"] = "missing"
    assert go.capture() == 1
    assert (go.GOLDEN / "manifest.json").read_bytes() == before


def test_a_capture_names_a_piece_that_converted_to_nothing(harness, capsys):
    """Found in the mod list but converted to nothing must not read like a
    piece that is simply not installed."""
    harness.pieces("cuirass", "boots", "gloves")
    harness.mode["boots"] = "nothing"
    harness.mode["gloves"] = "missing"
    assert go.capture() == 0
    out = capsys.readouterr().out
    assert "SKIP boots" in out and "the conversion produced nothing" in out, out
    assert "SKIP gloves" in out and "source not found" in out, out


def test_an_empty_piece_list_is_not_a_pass(harness, capsys):
    harness.pieces("cuirass")
    assert go.capture() == 0
    harness.pieces()
    capsys.readouterr()
    assert go.check(1e-4) == 3
    assert "NOTHING COMPARED: 0 of 0" in capsys.readouterr().out
