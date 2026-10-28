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

"""Reuse freed large numpy blocks instead of handing them back to Windows. #numpy-block-cache

THE COST. Every large numpy array is a fresh VirtualAlloc that the kernel zero-fills page by page on first touch and
takes back on free. A full conversion (10-27, e774406) took 2.87 G page faults; on a measured piece 26 s of its 180 s
of CPU was kernel time. The faults come from many passes (ray casting ~32%, `generate_armor_tri` 12%, `astype` 8%,
...), so the fix is one allocator rather than buffer reuse pass by pass (tried on `_cast_slice`: -10% faults).

WHAT IT IS. native/npcache.c, installed as numpy's data handler: arrays under 256 KB go to numpy's own handler
untouched; a freed large block goes on a free list for its size class and is handed to the next array of that
class. Measured on 9 pieces at 256 MB: page faults 50.7 -> 25.4 M, kernel CPU 94 -> 75 s, and on a 3-mod batch
65.6 -> 36.8 M; every NIF, TRI, XML, plugin and INI byte-identical, also with reused blocks poisoned (no pass reads
memory it did not write).

MEMORY. Cached blocks stay committed: up to CBBE2UBE_NUMPY_BLOCK_CACHE_MB per process. The cache empties itself and
stops caching while the machine's free RAM or free commit, whichever is lower, is under
CBBE2UBE_NUMPY_BLOCK_CACHE_LOW_MB (default 3072: the scheduler's 2 GB reserve plus 1 GB), and resumes 1 GB above
that. It reads the live machine, never an earlier run.

NEVER HOLDS A HEAVY PIECE BACK. The batch scheduler admits a second heavy piece only when live free RAM and free
commit cover it, and cached bytes read as used there. So (1) a worker empties its cache when it finishes a piece
(`flush`), so an idle worker -- one the scheduler is holding back -- holds nothing; and (2) each worker publishes what
it holds in a shared tally (`share`), which the scheduler adds back to its headroom (`reclaimable_gb`). Adding it back
is sound only because the cache frees itself below LOW_MB, so it counts only while LOW_MB is above the scheduler's
reserve. The scheduler itself never waits on memory with no heavy piece running, so it cannot stall either way.

OFF unless CBBE2UBE_NUMPY_BLOCK_CACHE_MB is set above 0. Without native/npcache.pyd (or beside the frozen exe) it
does nothing.
"""
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

from .envflags import knob as _knob

CACHE_MB = _knob("CBBE2UBE_NUMPY_BLOCK_CACHE_MB", 0, int)
LOW_MB = _knob("CBBE2UBE_NUMPY_BLOCK_CACHE_LOW_MB", 3072, int)

_MOD = None


def _load():
    """npcache.pyd beside the exe (frozen) or in native/ (source); None when absent."""
    here = Path(__file__).resolve().parent
    for c in (Path(getattr(sys, "_MEIPASS", "")) / "npcache.pyd", here.parent / "native" / "npcache.pyd"):
        if c.is_file():
            loader = importlib.machinery.ExtensionFileLoader("npcache", str(c))
            spec = importlib.util.spec_from_file_location("npcache", str(c), loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    return None


def share_name(pid: "int | None" = None) -> str:
    """The shared tally's name for the conversion process `pid` (this one by default): one per run."""
    return f"Local\\cbbe2ube-npcache-{os.getpid() if pid is None else int(pid)}"


def install(cache_mb: "int | None" = None, low_mb: "int | None" = None, share: "str | None" = None) -> bool:
    """Make the cache this process's numpy handler, publishing what it holds in the tally `share` when given (a pool
    worker). True if it is now installed; False when switched off, when the module is missing, or when it fails to
    load -- then numpy keeps its own handler and nothing changes."""
    global _MOD
    cache_mb = CACHE_MB if cache_mb is None else cache_mb
    low_mb = LOW_MB if low_mb is None else low_mb
    if cache_mb <= 0 or sys.platform != "win32":
        return False
    try:
        if _MOD is None:
            _MOD = _load()
        if _MOD is None:
            return False
        _MOD.install(int(cache_mb), int(low_mb))
        if share:
            _MOD.share(share, 1)
        return True
    except Exception:
        return False


def open_share(name: str) -> bool:
    """The conversion process: map the tally its pool workers will publish to (it claims no slot itself, so its own
    cache is never counted as free). False when the cache is not loaded here -- switched off."""
    if _MOD is None:
        return False
    try:
        return bool(_MOD.share(name, 0))
    except Exception:
        return False


def flush() -> None:
    """Free every cached block now (a worker, at the end of each piece)."""
    if _MOD is not None:
        _MOD.flush()


def reclaimable_gb(pids, reserve_gb: float) -> float:
    """GB the live processes `pids` hold in their caches: memory that is freed by itself before free RAM falls to
    the scheduler's `reserve_gb`, so the scheduler may count it as free. 0 without a tally, or when LOW_MB is not
    at least half a GB above the reserve (then it might not be freed in time)."""
    if _MOD is None or LOW_MB / 1024.0 < reserve_gb + 0.5:
        return 0.0
    try:
        return _MOD.shared_cached([int(p) for p in pids]) / 2.0 ** 30
    except Exception:
        return 0.0


def stats() -> "dict | None":
    """Hits, misses, flushes and bytes held, or None when the cache was never loaded."""
    return _MOD.stats() if _MOD is not None else None
