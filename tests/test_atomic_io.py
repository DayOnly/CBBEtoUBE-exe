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

"""atomic_io: every emitted plugin/mesh must be written atomically so a crash,
killed process, full disk, or locked destination can never leave a truncated
file the game would load and CTD on."""
import os
from pathlib import Path

import pytest

from src import atomic_io
from src.atomic_io import (atomic_write_bytes, atomic_copy,
                           atomic_nif_save, OutputLockedError)


def _no_tmp_leftover(d: Path) -> bool:
    return not any(".tmp" in p.name for p in d.iterdir())


def test_write_bytes_creates_and_no_temp_leftover(tmp_path):
    dst = tmp_path / "sub" / "out.esp"
    atomic_write_bytes(dst, b"hello")
    assert dst.read_bytes() == b"hello"
    assert _no_tmp_leftover(dst.parent)


def test_write_bytes_overwrites(tmp_path):
    dst = tmp_path / "out.esp"
    dst.write_bytes(b"old-and-longer")
    atomic_write_bytes(dst, b"new")
    assert dst.read_bytes() == b"new"
    assert _no_tmp_leftover(tmp_path)


def test_write_bytes_failure_leaves_original_and_cleans_temp(tmp_path, monkeypatch):
    dst = tmp_path / "out.esp"
    dst.write_bytes(b"ORIGINAL")
    # Simulate the destination being locked: os.replace raises PermissionError.
    monkeypatch.setattr(atomic_io.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(OutputLockedError):
        atomic_write_bytes(dst, b"NEWDATA")
    assert dst.read_bytes() == b"ORIGINAL"   # never half-overwritten
    assert _no_tmp_leftover(tmp_path)        # temp cleaned up


def test_write_bytes_save_failure_cleans_temp(tmp_path, monkeypatch):
    # A failure DURING the write (not the replace) must also leave no temp.
    dst = tmp_path / "out.esp"
    real_fdopen = atomic_io.os.fdopen

    class BoomFile:
        def __init__(self, f): self.f = f
        def __enter__(self): return self
        def __exit__(self, *a): self.f.close(); return False
        def write(self, _): raise OSError("disk full")
        def flush(self): pass
        def fileno(self): return self.f.fileno()

    monkeypatch.setattr(atomic_io.os, "fdopen",
                        lambda fd, mode: BoomFile(real_fdopen(fd, mode)))
    with pytest.raises(OSError):
        atomic_write_bytes(dst, b"data")
    assert not dst.exists()
    assert _no_tmp_leftover(tmp_path)


def test_atomic_copy(tmp_path):
    src = tmp_path / "a.nif"
    src.write_bytes(b"MESHDATA")
    dst = tmp_path / "b" / "c.nif"
    atomic_copy(src, dst)
    assert dst.read_bytes() == b"MESHDATA"
    assert _no_tmp_leftover(dst.parent)


def test_nif_save_writes_to_temp_then_replaces_and_restores_filepath(tmp_path):
    """atomic_nif_save must point the nif at a temp, save THERE, replace into
    place, and restore filepath to the real destination."""
    dst = tmp_path / "armor.nif"
    saved_to = []

    class FakeNif:
        filepath = None

        def save(self):
            saved_to.append(self.filepath)
            Path(self.filepath).write_bytes(b"NIFBYTES")

    nif = FakeNif()
    nif.filepath = str(dst)              # as the real code has it preset
    atomic_nif_save(nif, nif.filepath)

    assert dst.read_bytes() == b"NIFBYTES"          # final file present
    assert saved_to and saved_to[0].endswith(".nifsave.tmp")  # wrote to temp
    assert saved_to[0] != str(dst)                  # never wrote dst in place
    assert nif.filepath == str(dst)                 # filepath restored
    assert _no_tmp_leftover(tmp_path)               # no leftover temp


def test_write_armor_hdt_xml_routes_through_atomic(tmp_path, monkeypatch):
    # Lock the fix: the deployed game XML write must go through atomic_io, never a
    # bare write_text -- a truncated HDT-SMP physics XML = FSMP parse failure
    # (dead cloth / collapse), with the NIF still pointing at it.
    from src import hdt_xml_gen
    calls = []
    monkeypatch.setattr(hdt_xml_gen, "atomic_write_bytes",
                        lambda p, data: calls.append((Path(p), data)))
    out = tmp_path / "armor.xml"
    hdt_xml_gen.write_armor_hdt_xml(out, [("Skirt", ["NPC L Thigh"])])
    assert calls, "write_armor_hdt_xml must use atomic_write_bytes"
    assert calls[0][0] == out
    assert isinstance(calls[0][1], (bytes, bytearray)) and calls[0][1]


def test_nif_save_failure_during_write_cleans_temp(tmp_path):
    dst = tmp_path / "armor.nif"
    dst.write_bytes(b"PREVIOUS")

    class BoomNif:
        filepath = None

        def save(self):
            raise RuntimeError("pynifly write failed")

    nif = BoomNif()
    nif.filepath = str(dst)
    with pytest.raises(RuntimeError):
        atomic_nif_save(nif, nif.filepath)
    assert dst.read_bytes() == b"PREVIOUS"   # destination untouched
    assert _no_tmp_leftover(tmp_path)
