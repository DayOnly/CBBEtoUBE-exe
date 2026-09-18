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

"""BodySlide / OutfitStudio .osd (Outfit Studio Data) format.

OSD is a binary file containing per-morph vertex deltas. A SliderSet
(.osp XML) references morph entries inside an .osd by name; BodySlide
loads them at build time to morph the reference mesh per the user's
preset.

Format reverse-engineered from UBE SE 2.0 Release Body.osd (202 morph
entries, 11.3 MB) and cross-checked against pynifly's nifly/osdfile.cpp
(BSDFile class — same author Calienté ships both pynifly's nifly and
BodySlide / Outfit Studio).

Layout:
  Header:
    1 byte:  0x00 prefix (unused / version pad)
    3 bytes: 'DSO'           # magic, reads "DSO" backward = OSD
    4 bytes: version uint32  (= 1)
    4 bytes: morph_count uint32

  Per-morph entry:
    1 byte:  name_len
    N bytes: name (UTF-8, no terminator)
    2 bytes: num_offsets uint16 (number of (vert_idx, delta) pairs)
    repeating num_offsets times:
      2 bytes:  vert_idx uint16 (assumes vert count fits in 16 bits)
      12 bytes: delta float32 x 3

Reverse-engineered by locating morph boundaries via 'BaseShape' name
prefix search and back-calculating: morph #1 was 15722 bytes total =
26 (name_len + 25-char name) + 2 (num_offsets=1121) + 1121*14 (offsets).

Only verts where the morph actually displaces them are stored; absent
verts are treated as zero-displacement. This makes OSD compact for
local sliders (e.g. NipplesShowUp only touches breast verts).

IN MEMORY each morph is two arrays, not a tuple per offset. #osd-columnar
Every worker parses the body OSD once at pre-warm and keeps it for its whole
life; as tuples that was 166 MB of PrivateUsage per worker for the 11 MB body
OSD (MEASURED 2026-09-11 and again 2026-09-16 with
scripts/analysis/osd_footprint.py: 805,112 offsets over 202 morphs, x15.5 the
file), 65% of a freshly warmed worker. The arrays hold exactly what the file
holds -- a uint16 index and three float32 deltas per offset -- so they cost
what the file costs. Numeric consumers read the idx and delta arrays directly.
The offsets property still yields the old tuples, built on demand and never
kept: a cache would bring the tuples back into every worker that touched it.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


OSD_MAGIC = b"\x00DSO"
# One stored offset exactly as the file lays it out: a uint16 vertex index and
# three float32 deltas, 14 bytes, no padding.
OSD_RECORD = np.dtype([("idx", "<u2"), ("delta", "<f4", (3,))])
assert OSD_RECORD.itemsize == 14


@dataclass(eq=False)
class OsdMorph:
    """A single named morph entry from an .osd file.

    Sparse: only the verts this morph displaces are listed. Position k moves
    vertex idx[k] by delta[k]. The deltas are float32 because the file's are,
    so a value read here and written back is the same bytes."""
    name: str
    idx: np.ndarray      # (n,) uint16
    delta: np.ndarray    # (n, 3) float32

    def __post_init__(self) -> None:
        idx = np.asarray(self.idx)
        if idx.dtype != np.uint16:
            idx = idx.astype(np.int64, copy=False)
            if idx.size and (int(idx.min()) < 0 or int(idx.max()) > 0xFFFF):
                raise ValueError(
                    f"vert_idx exceeds uint16 max in morph {self.name!r}")
            idx = idx.astype(np.uint16)
        self.idx = np.ascontiguousarray(idx).reshape(-1)
        self.delta = np.ascontiguousarray(
            np.asarray(self.delta, dtype=np.float32)).reshape(-1, 3)
        if len(self.delta) != len(self.idx):
            raise ValueError(f"morph {self.name!r}: {len(self.idx)} indices "
                             f"but {len(self.delta)} deltas")

    def __len__(self) -> int:
        return len(self.idx)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, OsdMorph) and self.name == other.name
                and np.array_equal(self.idx, other.idx)
                and np.array_equal(self.delta, other.delta))

    @classmethod
    def from_offsets(cls, name: str,
                     offsets: "list[tuple[int, float, float, float]]") -> "OsdMorph":
        """From (vert_idx, dx, dy, dz) tuples -- the shape tools and tests build."""
        rows = list(offsets)
        idx = np.fromiter((int(r[0]) for r in rows), dtype=np.int64, count=len(rows))
        delta = np.array([r[1:4] for r in rows], dtype=np.float64).reshape(-1, 3)
        return cls(name, idx, delta)

    @property
    def offsets(self) -> "list[tuple[int, float, float, float]]":
        """The tuples, made on demand and never kept: a fresh list per call."""
        if not len(self.idx):
            return []
        d = self.delta.astype(np.float64)
        return list(zip(self.idx.tolist(), d[:, 0].tolist(),
                        d[:, 1].tolist(), d[:, 2].tolist()))

    def offsets_dict(self) -> dict[int, tuple[float, float, float]]:
        return {i: (x, y, z) for i, x, y, z in self.offsets}


@dataclass
class OsdFile:
    """All morph entries in one .osd. Morph names are arbitrary strings;
    typically "<TargetShapeName><SliderName>" e.g. "BaseShapeBigButt".
    """
    version: int
    morphs: list[OsdMorph]

    def by_name(self) -> dict[str, OsdMorph]:
        return {m.name: m for m in self.morphs}

    @classmethod
    def load(cls, path: str | Path) -> "OsdFile":
        with open(path, "rb") as f:
            data = f.read()
        return cls.parse(data)

    @classmethod
    def parse(cls, data: bytes) -> "OsdFile":
        if data[:4] != OSD_MAGIC:
            raise ValueError(f"not an OSD file (magic={data[:4]!r})")
        version = struct.unpack_from("<I", data, 4)[0]
        morph_count = struct.unpack_from("<I", data, 8)[0]
        # SECURITY: clamp a crafted morph_count (each morph needs >=3 bytes) so a
        # tiny file can't drive millions of object allocations -> OOM.
        morph_count = min(morph_count, len(data) // 3, 100_000)
        p = 12

        morphs: list[OsdMorph] = []
        for _ in range(morph_count):
            if p + 1 > len(data):
                break
            name_len = data[p]; p += 1
            name = data[p:p + name_len].decode("utf-8", errors="replace")
            p += name_len
            if p + 2 > len(data):
                break
            num_offsets = struct.unpack_from("<H", data, p)[0]; p += 2
            # offsets table: 14 bytes per entry (2 uint16 + 12 float32)
            if p + num_offsets * 14 > len(data):
                break  # truncated/crafted offsets table -> stop cleanly
            if num_offsets:
                rec = np.frombuffer(data, dtype=OSD_RECORD, count=num_offsets, offset=p)
                # astype copies: contiguous, writable, and nothing keeps the
                # whole file buffer alive through a view into it.
                idx = rec["idx"].astype(np.uint16)
                delta = rec["delta"].astype(np.float32)
            else:
                idx = np.zeros(0, dtype=np.uint16)
                delta = np.zeros((0, 3), dtype=np.float32)
            p += num_offsets * 14
            morphs.append(OsdMorph(name=name, idx=idx, delta=delta))

        return cls(version=version, morphs=morphs)

    def save(self, path: str | Path) -> None:
        out = bytearray()
        out += OSD_MAGIC
        out += struct.pack("<II", self.version, len(self.morphs))
        for m in self.morphs:
            name_b = m.name.encode("utf-8")
            if len(name_b) > 255:
                raise ValueError(f"morph name too long ({len(name_b)} > 255): {m.name!r}")
            out += bytes([len(name_b)]) + name_b
            n = len(m)
            if n > 0xFFFF:
                raise ValueError(f"too many offsets for uint16 num_offsets: {n}")
            out += struct.pack("<H", n)
            # The same bytes struct.pack("<H3f") wrote per entry, in one write.
            rec = np.empty(n, dtype=OSD_RECORD)
            rec["idx"] = m.idx
            rec["delta"] = m.delta
            out += rec.tobytes()
        from .atomic_io import atomic_write_bytes
        atomic_write_bytes(path, bytes(out))
