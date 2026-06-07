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
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


OSD_MAGIC = b"\x00DSO"


@dataclass
class OsdMorph:
    """A single named morph entry from an .osd file."""
    name: str
    # Sparse: only the verts that this morph actually displaces are listed.
    # Use as dict[vert_idx -> (dx,dy,dz)] via .offsets_dict() or iterate
    # via .offsets.
    offsets: list[tuple[int, float, float, float]]

    def offsets_dict(self) -> dict[int, tuple[float, float, float]]:
        return {idx: (dx, dy, dz) for idx, dx, dy, dz in self.offsets}


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
        p = 12

        morphs: list[OsdMorph] = []
        for _ in range(morph_count):
            name_len = data[p]; p += 1
            name = data[p:p + name_len].decode("utf-8", errors="replace")
            p += name_len
            num_offsets = struct.unpack_from("<H", data, p)[0]; p += 2
            # offsets table: 14 bytes per entry (2 uint16 + 12 float32)
            offsets = []
            for _ in range(num_offsets):
                vert_idx = struct.unpack_from("<H", data, p)[0]
                dx, dy, dz = struct.unpack_from("<3f", data, p + 2)
                offsets.append((vert_idx, dx, dy, dz))
                p += 14
            morphs.append(OsdMorph(name=name, offsets=offsets))

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
            if len(m.offsets) > 0xFFFF:
                raise ValueError(f"too many offsets for uint16 num_offsets: {len(m.offsets)}")
            out += struct.pack("<H", len(m.offsets))
            for idx, dx, dy, dz in m.offsets:
                if idx > 0xFFFF:
                    raise ValueError(f"vert_idx {idx} exceeds uint16 max")
                out += struct.pack("<H3f", idx, dx, dy, dz)
        Path(path).write_bytes(bytes(out))
