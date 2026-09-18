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

"""MEASURE what the body OSD costs a worker: the PrivateUsage a parse leaves
behind, the parse time, and with --oracle the same for the tuple-per-offset
parser the converter used before #osd-columnar, plus a field-for-field identity
check between the two.

    python -m scripts.analysis.osd_footprint [<file.osd>] [--oracle]

With no path it resolves the pack's UBE body OSD the way the converter does
(CBBE2UBE_MO2_INI, or run from inside the instance). A file with no morphs or
no offsets is "measured NOTHING" and exits 3: 0/0 is not a pass.

WHY. Every worker parses the body OSD once at pre-warm and keeps it for its
whole life (the OSD cache in nif_convert_trigen). As tuples that cost 166 MB of
PrivateUsage per worker for an 11 MB file -- 65% of a freshly warmed worker
(2026-09-11, re-measured 2026-09-16 with this tool). PrivateUsage is the commit
charge, which is what the memory guard prices; never the working set
(feedback_measure_commit_not_working_set).
"""
from __future__ import annotations

import argparse
import gc
import struct
import sys
import time
import tracemalloc
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

import numpy as np                       # noqa: E402
from src.osd import OSD_MAGIC, OsdFile   # noqa: E402


def private_usage_mb() -> float:
    """This process's PrivateUsage in MB (Windows); tracemalloc's current
    size elsewhere, which counts the Python-side allocations only."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        ok = k32.K32GetProcessMemoryInfo(wintypes.HANDLE(k32.GetCurrentProcess()),
                                         ctypes.byref(c), c.cb)
        if not ok:
            raise OSError(ctypes.GetLastError())
        return c.PrivateUsage / 2**20
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    return tracemalloc.get_traced_memory()[0] / 2**20


def parse_tuples(data: bytes):
    """The parser before #osd-columnar, verbatim: a tuple per offset."""
    assert data[:4] == OSD_MAGIC
    version = struct.unpack_from("<I", data, 4)[0]
    morph_count = min(struct.unpack_from("<I", data, 8)[0], len(data) // 3, 100_000)
    p = 12
    morphs = []
    for _ in range(morph_count):
        if p + 1 > len(data):
            break
        name_len = data[p]; p += 1
        name = data[p:p + name_len].decode("utf-8", errors="replace")
        p += name_len
        if p + 2 > len(data):
            break
        n = struct.unpack_from("<H", data, p)[0]; p += 2
        if p + n * 14 > len(data):
            break
        offs = []
        for _ in range(n):
            vi = struct.unpack_from("<H", data, p)[0]
            dx, dy, dz = struct.unpack_from("<3f", data, p + 2)
            offs.append((vi, dx, dy, dz))
            p += 14
        morphs.append((name, offs))
    return version, morphs


def measure(fn, data):
    """(result, seconds, MB of PrivateUsage still held afterwards)."""
    gc.collect()
    p0 = private_usage_mb()
    t = time.perf_counter()
    out = fn(data)
    dt = time.perf_counter() - t
    gc.collect()
    return out, dt, private_usage_mb() - p0


def _resolve_default() -> "Path | None":
    from src import paths
    paths.export_to_env(paths.discover_layout())
    from src.nif_convert_trigen import _find_ube_body_osd
    return _find_ube_body_osd()


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("osd", nargs="?", help="an .osd file; default: the pack's UBE body OSD")
    ap.add_argument("--oracle", action="store_true",
                    help="also parse as tuples, compare footprints, check identity")
    a = ap.parse_args(argv)
    path = Path(a.osd) if a.osd else _resolve_default()
    if path is None or not path.is_file():
        print("no UBE body OSD found: pass a path, set CBBE2UBE_MO2_INI, "
              "or run from inside the instance")
        return 2
    data = path.read_bytes()
    mb = len(data) / 2**20
    osd, dt, held = measure(OsdFile.parse, data)
    n_off = sum(len(m) for m in osd.morphs)
    if not osd.morphs or not n_off:
        print(f"measured NOTHING -- {len(osd.morphs)} morphs, {n_off} offsets in "
              f"{path.name}; 0/0 is not a pass")
        return 3
    print(f"file    : {path.name}  {len(data):,} bytes ({mb:.2f} MB)")
    print(f"arrays  : {len(osd.morphs)} morphs, {n_off:,} offsets, parse {dt * 1000:.0f} ms, "
          f"PrivateUsage held {held:.1f} MB (x{held / mb:.1f} the file)")
    if not a.oracle:
        return 0
    (version, tup), dt2, held2 = measure(parse_tuples, data)
    print(f"tuples  : {len(tup)} morphs, parse {dt2 * 1000:.0f} ms, "
          f"PrivateUsage held {held2:.1f} MB (x{held2 / mb:.1f} the file)")
    bad = 0
    fields = 0
    assert version == osd.version and len(tup) == len(osd.morphs)
    for m, (name, offs) in zip(osd.morphs, tup):
        same = (m.name == name and len(m) == len(offs))
        if same and len(offs):
            want = np.array(offs, dtype=np.float64)
            same = (np.array_equal(m.idx.astype(np.int64), want[:, 0].astype(np.int64))
                    and np.array_equal(m.delta.astype(np.float64), want[:, 1:4])
                    and np.array_equal(m.delta.view(np.uint32),
                                       want[:, 1:4].astype(np.float32).view(np.uint32)))
        bad += not same
        fields += 4 * len(offs)
    print(f"identity: {fields:,} fields over {len(tup)} morphs, "
          f"mismatching morphs = {bad}" + ("" if not bad else "  <-- NOT IDENTICAL"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
