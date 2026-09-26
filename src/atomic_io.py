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

from .envflags import flag as _flag
import re
import tempfile
import time
from pathlib import Path


class OutputLockedError(OSError):
    """Raised when an output file can't be written because it (or its folder)
    is locked by another process -- typically the running game, Mod Organizer,
    or the housecarl-mcp server holding the MO2 output. The existing file on
    disk is left intact (never half-overwritten)."""


# Windows fails a rename when ANYTHING holds the destination open, including for
# a moment: another converter worker writing the same path, an antivirus or the
# search indexer touching a file we just created, or MO2's VFS. Most of those
# clear in milliseconds, and the old code gave up on the first refusal.
#
# MEASURED on a full pack reconvert (161 mods, 3907 NIFs): 24 `.tri` writes lost
# this race. It is not random -- SEVERAL NIFs regenerate the SAME `.tri` (one
# armour's `_0`/`_1` plus its 1st-person variants; a glass cuirass has ten NIFs
# over one TRI), so with a worker pool two of them collide on the swap. Where one
# writer won, the file is correct and the loser's error is noise; where all lost,
# the piece shipped with NO body-morph TRI and will not follow body sliders.
#
# Retry before surfacing. Bounded and short: this must not mask a genuinely held
# file (the running game) by hanging a batch for minutes.
_SWAP_RETRIES = 6
_SWAP_BACKOFF = 0.05          # seconds; doubles each try -> ~3.1s worst case


def _swap_into_place(tmp: str, dst: Path) -> None:
    """os.replace(tmp -> dst), retrying a transient lock, with temp cleanup."""
    delay = _SWAP_BACKOFF
    for attempt in range(_SWAP_RETRIES):
        try:
            os.replace(tmp, str(dst))
            return
        except PermissionError as e:
            if attempt == _SWAP_RETRIES - 1:
                _quiet_unlink(tmp)
                raise OutputLockedError(
                    f"cannot write '{dst}': still locked after "
                    f"{_SWAP_RETRIES} attempts over "
                    f"{_SWAP_BACKOFF * (2 ** _SWAP_RETRIES - 1):.1f}s. "
                    f"If the GAME is running, close it and re-run. Mod Organizer "
                    f"itself does NOT need closing -- it launches this tool, so "
                    f"it is always running. (the existing file was left "
                    f"unchanged)"
                ) from e
            time.sleep(delay)
            delay *= 2
        except BaseException:
            _quiet_unlink(tmp)
            raise


def _quiet_unlink(p) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


# Every temp name a writer below leaves when its process is killed mid-write:
# the fixed `<name>.nifsave.tmp` / `<name>.trisave.tmp`, and mkstemp's
# `<name>.<8 random chars>.tmp` (tempfile draws them from [a-z0-9_]). `<name>`
# always carries its own extension, which keeps a user's `notes.tmp` out.
# #orphan-temps
_ORPHAN_TEMP = re.compile(r"^.+\.[A-Za-z0-9]+\.(?:nifsave|trisave|[a-z0-9_]{8})\.tmp$")


def orphan_temps(root, older_than: float) -> "list[Path]":
    """Temp files under `root` that a writer here left behind, last modified
    before `older_than` (a time.time() value). A newer one can belong to a write
    still in progress, so it is never returned."""
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for p in root.rglob("*.tmp"):
        try:
            if (_ORPHAN_TEMP.match(p.name) and p.is_file()
                    and p.stat().st_mtime < older_than):
                found.append(p)
        except OSError:
            continue
    return sorted(found)


def sweep_orphan_temps(root, older_than: float) -> "list[Path]":
    """Delete `orphan_temps(root, older_than)`; return the ones removed."""
    removed = []
    for p in orphan_temps(root, older_than):
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            pass
    return removed


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


# ---------------------------------------------------------------------------
# Whole-record appends to a file several processes write at once.
# #atomic-audit-append
#
# Every pool worker appends to the same `standoff_audit.jsonl` (and, with the
# glow diagnostic on, the same glow log). `open(p, "a")` does NOT make that
# safe on Windows: the C runtime emulates append as "seek to the end, then
# write", two steps another process can get between, so two workers write at
# the same offset and one record lands inside the other. MEASURED: a full run
# tore 14-16 lines and lost ~30 records, differently each run; the live sink
# had 58 torn lines; 8 processes x 500 records of up to 30 KB tore ~800 lines
# per trial with no lock (the same test with this lock: 0).
#
# The fix is a cross-process lock around ONE write of the whole encoded record.
# The lock is a byte-range lock far past the end of the file itself, so it
# needs no second file, never overlaps the bytes being written, and the OS
# drops it when a worker dies holding it. A per-worker-file-and-merge scheme
# was rejected: the sink is also written outside any batch with a parent to
# merge it (single converts, and several converter processes sharing one
# CBBE2UBE_STANDOFF_LOG), and it appends across runs by design.
#
# The bytes are exactly what the old text-mode append wrote -- the same UTF-8,
# "\n" written as os.linesep -- so a reader sees no difference but the tears.
# Record ORDER is still arrival order across workers (as before); the multiset
# of records is what is deterministic. CBBE2UBE_NO_ATOMIC_AUDIT_APPEND=1
# restores the old unlocked writer.
_APPEND_LOCK_OFFSET_HIGH = 0x7FFFFFFF      # lock byte at 0x7FFFFFFF_00000000


def _atomic_append_enabled() -> bool:
    return not _flag("CBBE2UBE_NO_ATOMIC_AUDIT_APPEND", False)


if os.name == "nt":
    import ctypes as _ct
    from ctypes import wintypes as _wt

    class _OVERLAPPED(_ct.Structure):
        _fields_ = [("Internal", _ct.c_void_p), ("InternalHigh", _ct.c_void_p),
                    ("Offset", _wt.DWORD), ("OffsetHigh", _wt.DWORD),
                    ("hEvent", _wt.HANDLE)]

    # A private kernel32 handle: argtypes set here cannot leak into other
    # modules' `ctypes.windll.kernel32`.
    _K32 = _ct.WinDLL("kernel32", use_last_error=True)
    _K32.LockFileEx.argtypes = (_wt.HANDLE, _wt.DWORD, _wt.DWORD, _wt.DWORD,
                                _wt.DWORD, _ct.POINTER(_OVERLAPPED))
    _K32.LockFileEx.restype = _wt.BOOL
    _K32.UnlockFileEx.argtypes = (_wt.HANDLE, _wt.DWORD, _wt.DWORD,
                                  _wt.DWORD, _ct.POINTER(_OVERLAPPED))
    _K32.UnlockFileEx.restype = _wt.BOOL
    _LOCKFILE_EXCLUSIVE_LOCK = 0x2

    def _append_lock(fd: int, lock: bool) -> None:
        import msvcrt
        ov = _OVERLAPPED(0, 0, 0, _APPEND_LOCK_OFFSET_HIGH, None)
        h = msvcrt.get_osfhandle(fd)
        if lock:      # no FAIL_IMMEDIATELY flag: blocks until it is ours
            ok = _K32.LockFileEx(h, _LOCKFILE_EXCLUSIVE_LOCK, 0, 1, 0,
                                 _ct.byref(ov))
        else:
            ok = _K32.UnlockFileEx(h, 0, 1, 0, _ct.byref(ov))
        if not ok:
            raise _ct.WinError(_ct.get_last_error())
else:
    def _append_lock(fd: int, lock: bool) -> None:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX if lock else fcntl.LOCK_UN)


def append_whole(path, text: str, errors: str = "strict") -> None:
    """Append `text` to `path` so no other process's append can land inside it.

    Byte-for-byte what `open(path, "a", encoding="utf-8", errors=errors)
    .write(text)` wrote, but as ONE os.write under an exclusive cross-process
    lock. A lock that cannot be taken (a filesystem without byte-range locks)
    does not cost the record: it is written unlocked, as before -- a record
    that might tear beats one that is never written. Raises what opening or
    writing the file raises; the callers are telemetry and swallow it."""
    if not _atomic_append_enabled():
        with open(path, "a", encoding="utf-8", errors=errors) as f:
            f.write(text)
        return
    data = text.replace("\n", os.linesep).encode("utf-8", errors)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT
                 | getattr(os, "O_BINARY", 0), 0o666)
    try:
        try:
            _append_lock(fd, True)
            locked = True
        except OSError:
            locked = False
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            if locked:
                try:
                    _append_lock(fd, False)
                except OSError:
                    pass          # closing the file below releases it anyway
    finally:
        os.close(fd)


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
    _debug_glow_controller_check(nif, dst_path)


def _debug_glow_controller_check(nif, dst_path) -> None:
    """DEBUG (CBBE2UBE_DEBUG_GLOW_CTRL=1): after a save, flag any shape whose
    shape-level controllerID == its shaderPropertyID (the dangling effect-shader
    self-reference that CTDs Daedric MaleTorsoGlow on equip) and log the calling
    pass via a stack trace. Off by default -> zero cost. Names the introducing
    pass so we can fix it at the source instead of only repairing in postflight."""
    if not _flag("CBBE2UBE_DEBUG_GLOW_CTRL", False):
        return
    try:
        hits = []
        for s in nif.shapes:
            pr = s.properties
            cid = getattr(pr, "controllerID", 0xFFFFFFFF)
            spid = getattr(pr, "shaderPropertyID", 0xFFFFFFFF)
            if cid != 0xFFFFFFFF and cid == spid:
                hits.append(getattr(s, "name", "?"))
        if not hits:
            return
        import traceback
        # Trimmed caller chain (skip this fn + atomic_nif_save), name the pass.
        frames = traceback.format_stack()[:-2][-10:]
        body = [f"\n=== CORRUPT glow controller after save: {dst_path}\n",
                f"    shapes: {hits}\n",
                "    call stack (most recent last):\n"]
        body += ["      " + fr.rstrip().replace("\n", "\n      ") + "\n"
                 for fr in frames]
        _glow_log_write("".join(body))
    except Exception:
        pass


# Said ONCE per process: this runs after every save, and a warning per save
# would bury the run log it is trying to help someone read.
_GLOW_LOG_WARNED = False


def _glow_log_write(text: str) -> None:
    """Append to the glow log, CREATING its directory, and SAY SO on failure.

    This silently wrote nothing for an unknown length of time. The whole body
    of the caller sits under `except Exception: pass`, and `CBBE2UBE_GLOW_LOG`
    was pointed at `...\\tools\\CBBEtoUBE\\`, a directory that does not exist --
    the tool's real folder is `CBBE to UBE`, with spaces. Every append raised
    FileNotFoundError and was swallowed, so the diagnostic looked switched on
    and caught nothing. A diagnostic that cannot report its own failure is
    worse than one that is off, because it is read as evidence of absence.

    So: create the parent, fall back to the tool's own folder when the
    configured path cannot be opened, and if even that fails say it on stderr
    rather than returning quietly.
    """
    global _GLOW_LOG_WARNED
    import sys as _sys
    # The tool's own folder, not %TEMP% (under AppData). #tool-folder-only
    from .paths import tool_dir as _tool_dir
    fallback = Path(_tool_dir()) / "CBBEtoUBE_glowdebug.log"
    configured = os.environ.get("CBBE2UBE_GLOW_LOG", "").strip()
    candidates = [Path(configured), fallback] if configured else [fallback]
    failures = []
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Every pool worker appends here too: one whole block per save,
            # never spliced into another's. #atomic-audit-append
            append_whole(path, text, errors="replace")
            return
        except Exception as e:
            failures.append((path, e))
    # Only here has NOTHING been written -- a fallback that succeeded is not
    # worth a warning, and warning on it would train the reader to ignore this.
    if not _GLOW_LOG_WARNED:
        _GLOW_LOG_WARNED = True
        for path, e in failures:
            print(f"  WARN: glow diagnostic could not write {path}: {e!r}",
                  file=_sys.stderr)
        print("  WARN: the glow diagnostic is switched on and recording "
              "NOTHING -- do not read its silence as a clean result",
              file=_sys.stderr)


def atomic_tri_save(tri, dst_path) -> None:
    """Save a pynifly TriFile (auto-generated body-morph TRI) to `dst_path`
    atomically: write to a temp file in the same directory, then os.replace it
    into place. A crash/kill during the (native) write corrupts only the temp --
    the destination stays the previous complete file. A truncated .tri breaks
    RaceMenu/BodyMorph loading (the armor stops following body sliders, or worse),
    so this closes the last non-atomic game-loaded-output write. Raises
    OutputLockedError if the destination is locked."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_path.with_name(dst_path.name + ".trisave.tmp")
    try:
        tri.save(str(tmp))
    except BaseException:
        _quiet_unlink(str(tmp))
        raise
    _swap_into_place(str(tmp), dst_path)
