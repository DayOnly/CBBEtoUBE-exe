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

"""Atomic file output.

Every plugin/mesh this tool emits is loaded by the Skyrim engine. A partial
write (crash, killed process, full disk, or destination locked by the game /
Mod Organizer) leaves a truncated file that CTDs on load.

The fix is write-to-temp-then-rename: data goes to a temp file in the SAME
directory (so `os.replace` is atomic on the volume); only a fully-written
temp is swapped into place. The destination is always either the complete old
file or the complete new file.

If the destination is locked, `os.replace` raises and we surface a clear
`OutputLockedError` while leaving the existing file intact.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


class OutputLockedError(OSError):
    """Raised when an output file can't be written because it (or its folder)
    is locked by another process -- typically the running game, Mod Organizer,
    or the housecarl-mcp server holding the MO2 output. The existing file on
    disk is left intact (never half-overwritten)."""


def _swap_into_place(tmp: str, dst: Path) -> None:
    """os.replace(tmp -> dst) with lock-aware error + temp cleanup on failure."""
    try:
        os.replace(tmp, str(dst))
    except PermissionError as e:
        _quiet_unlink(tmp)
        raise OutputLockedError(
            f"cannot write '{dst}': it is locked by another process. Close the "
            f"game, Mod Organizer, and the housecarl-mcp server, then retry. "
            f"(the existing file was left unchanged)"
        ) from e
    except BaseException:
        _quiet_unlink(tmp)
        raise


def _quiet_unlink(p) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


def atomic_write_bytes(path, data: bytes) -> None:
    """Write `data` to `path` atomically (temp in the same dir, flush+fsync,
    then os.replace). Never leaves a truncated destination. Raises
    OutputLockedError if the destination is locked."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _quiet_unlink(tmp)
        raise
    _swap_into_place(tmp, path)


def atomic_copy(src, dst) -> None:
    """Copy `src` -> `dst` atomically (copy to a temp in dst's dir, then
    os.replace), preserving metadata. Never leaves a truncated destination;
    raises OutputLockedError if the destination is locked."""
    import shutil
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent),
                               prefix=dst.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
    except BaseException:
        _quiet_unlink(tmp)
        raise
    # Durability: best-effort flush to disk before the rename (matches
    # atomic_write_bytes -- else a power loss just after os.replace can leave the
    # directory entry pointing at not-yet-flushed data). fsync needs a WRITABLE
    # fd on Windows; never let a durability flush failure break the copy itself.
    try:
        _fd = os.open(tmp, os.O_RDWR)
        try:
            os.fsync(_fd)
        finally:
            os.close(_fd)
    except OSError:
        pass
    _swap_into_place(tmp, dst)


def atomic_nif_save(nif, dst_path) -> None:
    """Save a pynifly NifFile to `dst_path` atomically: point its filepath at a
    temp file in the same directory, let pynifly write that, then os.replace it
    into place. A crash/kill during pynifly's (native) write corrupts only the
    temp -- the destination stays the previous complete file. Raises
    OutputLockedError if the destination is locked."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_path.with_name(dst_path.name + ".nifsave.tmp")
    try:
        nif.filepath = str(tmp)
        nif.save()
    except BaseException:
        _quiet_unlink(str(tmp))
        raise
    _swap_into_place(str(tmp), dst_path)
    # Restore the real destination on the object so any post-save code that
    # reads nif.filepath sees the final path, not the temp.
    try:
        nif.filepath = str(dst_path)
    except Exception:
        pass
