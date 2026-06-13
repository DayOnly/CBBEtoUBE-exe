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

"""BodySlide / RaceMenu BODYTRI (.tri) binary format.

A TRI file referenced via a NiStringExtraData("BODYTRI", "...") block
on an armor shape provides per-shape runtime morph data. NioOverride /
RaceMenu reads it, looks up each active slider name in the per-shape
morph table, and applies the deltas (scaled by slider value) to the
shape's verts at render time. This is how UBE / 3BA armors follow
body sliders without geometry changes.

The format is **PIRT** ("TRIP" backwards) — distinct from Bethesda's
face TRI (FRTRI00...) and from the OSD format. Reverse-engineered by
inspecting a revealing-top TRI output of BodySlide. Cross-validated by
round-tripping the parsed structure back into bytes that match the
original within rounding error on the quantized deltas.

Layout:
  Header:
    4 bytes:  "PIRT" magic
    2 bytes:  uint16 version (= 7)

  Per shape (repeated until EOF — no shape count anywhere):
    1 byte:   shape name length
    N bytes:  shape name (ASCII)
    2 bytes:  uint16 num_morphs

    Per morph (num_morphs times):
      1 byte:   morph name length
      M bytes:  morph name (ASCII)
      4 bytes:  float32 multiplier   (quantization scale)
      2 bytes:  uint16 num_offsets
      Per offset (num_offsets times), 8 bytes:
        2 bytes:  uint16 vert_idx
        2 bytes:  int16  dx_quantized
        2 bytes:  int16  dy_quantized
        2 bytes:  int16  dz_quantized

      actual_delta = (dx, dy, dz) * multiplier

The quantization scale is chosen so that int16 captures the largest
absolute delta with minimum precision loss. For typical body morphs
where max delta is ~5 units, multiplier ~= 5/32767 ~= 1.5e-4.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


TRI_MAGIC = b"PIRT"
# IMPORTANT — the uint16 at bytes 4-5 is NOT a version field; it is the
# SHAPE COUNT. Old PIRT documentation labeled it "version" (and that's
# what our dataclass field is still called for back-compat), but
# inspecting every hand-built UBE .tri on disk shows bytes 4-5 == actual
# shape count, with zero exceptions: the official UBE collision-body mesh's .tri
# (12 shapes) writes 0x0c, UBE femalebody_tangent.tri (1 shape) writes
# 0x01, and BodySlide-built armors all match their on-disk shape count.
#
# skee's BodyMorph cache reads it as `trishapeCount` (UInt16 in packed
# mode) and stops parsing after that many shapes. So if we write a
# constant value (e.g. 9), skee processes only the first 9 shapes of
# EVERY armor we produce, no matter how many shapes the .tri actually
# carries — late-list shapes are silently dropped on equip. That was the
# real cause of the long-standing "9-shape cap" (Cape onward freezes on
# a mashup cuirass, late pieces freeze on a multi-piece armor / a color-variant armor, etc.)
# It was NOT a NioOverride per-NIF shape limit (skee's apply loop has no
# such cap), and it was NOT solvable by merging shapes to fit under 9.
#
# save() below writes `len(self.shapes)` at bytes 4-5; the constant
# remains only as a default for callers that don't override it on the
# dataclass (and to keep `version=` named-arg call sites working until
# they're all updated).
TRI_VERSION = 9
# Hard cap from the on-wire format: bytes 4-5 are uint16, so a .tri
# can address at most 65535 distinct shapes. Real armors are nowhere
# near this — typical max is ~20 — but we guard at save time so a bug
# upstream can't silently truncate.
TRI_MAX_SHAPES = 0xFFFF


@dataclass
class TriMorph:
    """One named morph for one shape — a sparse list of vertex deltas."""
    name: str
    # (vert_idx, dx, dy, dz) in floats. Stored quantized on disk.
    offsets: list[tuple[int, float, float, float]]


@dataclass
class TriShape:
    """A shape's morph table — all named morphs that apply to this shape."""
    name: str
    morphs: list[TriMorph]


@dataclass
class TriFile:
    """A full BODYTRI file: a collection of shapes, each with their own
    morph table."""
    version: int
    shapes: list[TriShape]

    @classmethod
    def load(cls, path: str | Path) -> "TriFile":
        return cls.parse(Path(path).read_bytes())

    @classmethod
    def parse(cls, data: bytes) -> "TriFile":
        """Parse the main shape list.

        Note: BodySlide-output TRIs can contain a TRAILING ADDENDUM section
        after the main shape list — typically a uint16 count followed by
        additional shapes carrying "NEW_*" or experimentally-added sliders.
        We stop reading at the main-section boundary and ignore the rest;
        RaceMenu/NioOverride applies whatever it can find from the first
        section just fine, and our writer only produces single-section
        output anyway.
        """
        if data[:4] != TRI_MAGIC:
            raise ValueError(f"not a TRI file (magic={data[:4]!r})")
        version = struct.unpack_from("<H", data, 4)[0]
        p = 6
        shapes: list[TriShape] = []
        while p < len(data) - 3:
            # Defensive: a valid shape-name-length byte should be 1-255
            # AND followed by printable ASCII. If we see e.g. 02 00 09
            # (the addendum separator pattern), it doesn't parse as a
            # name and we stop. Likewise stop on any decode failure.
            sname_len = data[p]
            if sname_len == 0:
                break
            try:
                sname = data[p + 1:p + 1 + sname_len].decode("ascii")
            except UnicodeDecodeError:
                break
            if not all(c.isprintable() for c in sname):
                break
            p += 1 + sname_len
            if p + 2 > len(data):
                break
            num_morphs = struct.unpack_from("<H", data, p)[0]
            p += 2

            morphs: list[TriMorph] = []
            for _ in range(num_morphs):
                if p + 7 > len(data):
                    break  # truncated
                mname_len = data[p]; p += 1
                mname = data[p:p + mname_len].decode("ascii", errors="replace")
                p += mname_len
                mult = struct.unpack_from("<f", data, p)[0]; p += 4
                noff = struct.unpack_from("<H", data, p)[0]; p += 2
                if p + noff * 8 > len(data):
                    break  # truncated offsets table
                offs: list[tuple[int, float, float, float]] = []
                for _ in range(noff):
                    idx, dx_q, dy_q, dz_q = struct.unpack_from(
                        "<H3h", data, p)
                    p += 8
                    offs.append((
                        int(idx),
                        float(dx_q) * mult,
                        float(dy_q) * mult,
                        float(dz_q) * mult,
                    ))
                morphs.append(TriMorph(name=mname, offsets=offs))
            shapes.append(TriShape(name=sname, morphs=morphs))
        return cls(version=version, shapes=shapes)

    def save(self, path: str | Path) -> None:
        """Write to disk as the on-wire PIRT format.

        Bytes 4-5 are the SHAPE COUNT (uint16), not a version — see the
        TRI_VERSION docstring above. Write len(self.shapes), ignoring
        self.version. Guard against the uint16 ceiling.
        """
        n_shapes = len(self.shapes)
        if n_shapes > TRI_MAX_SHAPES:
            raise ValueError(
                f"TRI shape count {n_shapes} exceeds uint16 ceiling "
                f"({TRI_MAX_SHAPES}); cannot encode in PIRT header"
            )
        out = bytearray()
        out += TRI_MAGIC
        out += struct.pack("<H", n_shapes)
        for sh in self.shapes:
            sn = sh.name.encode("ascii")
            if len(sn) > 255:
                raise ValueError(f"shape name too long: {sh.name!r}")
            out += bytes([len(sn)]) + sn
            if len(sh.morphs) > 0xFFFF:
                raise ValueError(
                    f"shape {sh.name!r} has {len(sh.morphs)} morphs "
                    f"(uint16 num_morphs limit is 65535)"
                )
            out += struct.pack("<H", len(sh.morphs))
            for m in sh.morphs:
                mn = m.name.encode("ascii")
                if len(mn) > 255:
                    raise ValueError(f"morph name too long: {m.name!r}")
                out += bytes([len(mn)]) + mn

                # Compute quantization scale. We want the largest |delta|
                # to map to int16 range [-32767, 32767]. If all deltas
                # are zero (degenerate empty morph), use 1.0 to avoid
                # division by zero — int16 zeros round-trip cleanly.
                if not m.offsets:
                    out += struct.pack("<f", 1.0)
                    out += struct.pack("<H", 0)
                    continue
                arr = np.array(
                    [[dx, dy, dz] for _, dx, dy, dz in m.offsets],
                    dtype=np.float64,
                )
                max_abs = float(np.abs(arr).max())
                if max_abs <= 0.0:
                    mult = 1.0
                else:
                    mult = max_abs / 32767.0
                out += struct.pack("<f", mult)
                if len(m.offsets) > 0xFFFF:
                    raise ValueError(
                        f"morph {m.name!r} has {len(m.offsets)} offsets "
                        f"(uint16 num_offsets limit is 65535)"
                    )
                out += struct.pack("<H", len(m.offsets))

                # Vectorized quantize + emit. The per-element struct.pack
                # loop was the slowest part of TRI save for files with
                # many offsets (the auto-generated a slot-49 no-body cloth armor hits
                # ~900k offsets across all shapes). numpy gather + a
                # single tobytes() is roughly 30x faster on the same
                # data set.
                inv = 1.0 / mult if mult > 0 else 0.0
                if len(m.offsets) == 0:
                    continue
                arr = np.asarray(m.offsets, dtype=np.float64)
                idxs = arr[:, 0].astype(np.int64)
                if (idxs > 0xFFFF).any():
                    raise ValueError(
                        f"vert_idx exceeds uint16 max in morph {m.name!r}")
                q = np.round(arr[:, 1:4] * inv)
                np.clip(q, -32768.0, 32767.0, out=q)
                # Pack as (N, 4) uint16/int16 mixed via two views into a
                # single uint16 buffer (idx in column 0, three int16
                # delta components in columns 1-3 reinterpreted).
                buf = np.empty((len(m.offsets), 4), dtype=np.uint16)
                buf[:, 0] = idxs.astype(np.uint16)
                buf[:, 1:4] = q.astype(np.int16).view(np.uint16)
                out += buf.tobytes()
        from .atomic_io import atomic_write_bytes
        atomic_write_bytes(path, bytes(out))
