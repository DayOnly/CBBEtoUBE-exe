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

"""Skyrim SE ESP/ESM read+write.

Scope: enough of the format to:
  - Parse a CBBE armor mod's ESP and inspect its ARMOs + ARMAs
  - Produce a UBE patch ESP that matches the hand-authored UBE pattern
    (new ARMA records + ARMO overrides adding the new ARMAs)

NOT a full implementation. We don't handle compression on records (none of
our outputs need it), we don't handle group types other than top-level
(which is all SE armor mods need), and we don't try to be a generic xEdit
replacement.

References:
  - https://en.uesp.net/wiki/Skyrim_Mod:Mod_File_Format
  - https://en.uesp.net/wiki/Skyrim_Mod:File_Format_Conventions
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ----- subrecord encoding -------------------------------------------------

def encode_subrecord(sig: bytes, data: bytes) -> bytes:
    """Standard subrecord: 4-byte sig, 2-byte size, data.

    Sizes > 65535 use the XXXX trick (preceding XXXX subrecord with the real
    size). Not needed for our small ARMA/ARMO data; we error if hit.
    """
    if len(sig) != 4:
        raise ValueError(f"signature must be 4 bytes, got {sig!r}")
    if len(data) > 0xFFFF:
        raise NotImplementedError("XXXX-style large subrecords not implemented")
    return sig + struct.pack("<H", len(data)) + data


def encode_zstring(s: str) -> bytes:
    """zstring: null-terminated UTF-8."""
    return s.encode("utf-8") + b"\x00"


def iter_subrecords(payload: bytes) -> Iterable[tuple[bytes, bytes]]:
    """Yield (signature, data) for each subrecord in a record payload.

    Handles the XXXX large-size override: if we see XXXX before another
    subrecord, the next subrecord's 2-byte size field is ignored and the
    XXXX's 4-byte payload is the real size.
    """
    p = 0
    n = len(payload)
    pending_xxxx = None
    # Bounds-checked walk: stop cleanly (dropping unparseable trailing bytes)
    # on any size violation rather than raising struct.error or yielding junk.
    while p + 6 <= n:                       # need the 6-byte sig+size header
        sig = payload[p:p+4]
        size = struct.unpack_from("<H", payload, p+4)[0]
        p += 6
        if sig == b"XXXX":
            # XXXX carries a 4-byte real-size override for the NEXT subrecord.
            if size < 4 or p + size > n:
                return                      # truncated XXXX -> stop cleanly
            pending_xxxx = struct.unpack_from("<I", payload, p)[0]
            p += size
            continue
        if pending_xxxx is not None:
            size = pending_xxxx
            pending_xxxx = None
        if p + size > n:
            return                          # declared data runs past end -> stop
        yield sig, payload[p:p+size]
        p += size


# ----- record encoding ----------------------------------------------------

RECORD_HEADER_SIZE = 24
GRUP_HEADER_SIZE   = 24

FLAG_COMPRESSED = 0x00040000


@dataclass
class Record:
    """A non-GRUP record. Header + raw payload bytes."""
    sig: bytes               # 4 bytes (e.g. b"ARMA")
    flags: int = 0
    formid: int = 0
    timestamp_vc: int = 0     # 4 bytes
    version_unk: int = 0x002C # Skyrim SE form version 44 (LE was 0x2B)
    payload: bytes = b""

    @classmethod
    def parse(cls, data: bytes, offset: int) -> tuple["Record", int]:
        # Defend the validator/loader against a truncated or corrupt input: raise
        # a clean ValueError (catchable, descriptive) instead of a cryptic
        # struct.error / an `assert` that `python -O` would strip.
        if offset + RECORD_HEADER_SIZE > len(data):
            raise ValueError(
                f"truncated record header at offset {offset} "
                f"(need {RECORD_HEADER_SIZE}, have {len(data) - offset})")
        sig = data[offset:offset+4]
        size = struct.unpack_from("<I", data, offset+4)[0]
        flags = struct.unpack_from("<I", data, offset+8)[0]
        formid = struct.unpack_from("<I", data, offset+12)[0]
        timestamp_vc = struct.unpack_from("<I", data, offset+16)[0]
        version_unk = struct.unpack_from("<I", data, offset+20)[0]
        if offset + RECORD_HEADER_SIZE + size > len(data):
            raise ValueError(
                f"truncated record payload for {sig!r} at offset {offset} "
                f"(declared {size}, have "
                f"{len(data) - offset - RECORD_HEADER_SIZE})")
        payload = data[offset+24:offset+24+size]
        if flags & FLAG_COMPRESSED:
            if len(payload) < 4:
                raise ValueError(
                    "compressed record payload too short for its size header")
            uncomp_size = struct.unpack_from("<I", payload, 0)[0]
            # SECURITY: zlib amplifies ~1000x. Cap the declared size AND bound the
            # actual inflation (decompressobj with max_length) so a crafted record
            # can't OOM the process before the size check below ever runs.
            _MAX_DECOMP = 128 * 1024 * 1024
            if uncomp_size > _MAX_DECOMP:
                raise ValueError(
                    f"compressed record declares {uncomp_size} bytes "
                    f"(> {_MAX_DECOMP} cap)")
            _dco = zlib.decompressobj()
            payload = _dco.decompress(payload[4:], uncomp_size + 64)
            if _dco.unconsumed_tail:
                raise ValueError(
                    "compressed record inflates past its declared size")
            # Clear the compressed flag since we hold the inflated version
            flags &= ~FLAG_COMPRESSED
            if len(payload) != uncomp_size:
                raise ValueError(
                    f"decompressed size {len(payload)} != declared {uncomp_size}")
        return cls(sig=sig, flags=flags, formid=formid,
                   timestamp_vc=timestamp_vc, version_unk=version_unk,
                   payload=payload), offset + 24 + size

    def to_bytes(self) -> bytes:
        if self.flags & FLAG_COMPRESSED:
            raise NotImplementedError("output compression not supported; clear the flag")
        return (self.sig
                + struct.pack("<I", len(self.payload))
                + struct.pack("<I", self.flags)
                + struct.pack("<I", self.formid)
                + struct.pack("<I", self.timestamp_vc)
                + struct.pack("<I", self.version_unk)
                + self.payload)


@dataclass
class Group:
    """A top-level GRUP. Contains a list of Records (and possibly nested groups,
    but for top-level ARMO/ARMA groups, just records).
    """
    label: bytes              # 4 bytes (e.g. b"ARMO") for top-level groups
    group_type: int = 0       # 0 = top-level
    timestamp_vc: int = 0
    version_unk: int = 0x002C       # Skyrim SE form version 44
    records: list[Record] = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes, offset: int) -> tuple["Group", int]:
        if offset + GRUP_HEADER_SIZE > len(data):
            raise ValueError(
                f"truncated GRUP header at offset {offset} "
                f"(need {GRUP_HEADER_SIZE}, have {len(data) - offset})")
        sig = data[offset:offset+4]
        if sig != b"GRUP":
            raise ValueError(f"expected GRUP at offset {offset}, got {sig!r}")
        size = struct.unpack_from("<I", data, offset+4)[0]
        label = data[offset+8:offset+12]
        gtype = struct.unpack_from("<i", data, offset+12)[0]
        timestamp_vc = struct.unpack_from("<I", data, offset+16)[0]
        version_unk = struct.unpack_from("<I", data, offset+20)[0]

        records: list[Record] = []
        inner = offset + 24
        # Clamp to the buffer: a file-supplied `size` must not drive the walk
        # past EOF (a crafted oversized GRUP + a stray b"GRUP" near EOF would
        # otherwise OOB-unpack the nested size). Keeps the clean-error contract.
        end = min(offset + size, len(data))
        while inner + 4 <= end:
            inner_sig = data[inner:inner+4]
            if inner_sig == b"GRUP":
                # nested group — for v1 we don't recurse, just skip and warn
                if inner + 8 > len(data):
                    break  # truncated nested-GRUP header
                inner_size = struct.unpack_from("<I", data, inner+4)[0]
                if inner_size < GRUP_HEADER_SIZE:
                    break  # malformed/zero nested-GRUP size -> stop (no infinite loop)
                inner += inner_size
                continue
            rec, inner = Record.parse(data, inner)
            records.append(rec)
        return cls(label=label, group_type=gtype,
                   timestamp_vc=timestamp_vc, version_unk=version_unk,
                   records=records), offset + size

    def to_bytes(self) -> bytes:
        body = b"".join(r.to_bytes() for r in self.records)
        size = GRUP_HEADER_SIZE + len(body)
        return (b"GRUP"
                + struct.pack("<I", size)
                + self.label
                + struct.pack("<i", self.group_type)
                + struct.pack("<I", self.timestamp_vc)
                + struct.pack("<I", self.version_unk)
                + body)


# ----- TES4 header --------------------------------------------------------

@dataclass
class TES4Header:
    masters: list[str]            # ordered list of master filenames
    author: str = "cbbe-to-ube"
    description: str = ""
    flags: int = 0                # ESM/ESL flags in TES4's record flags (not relevant for our ESP patches)
    version: float = 1.7          # HEDR version (Skyrim SE = 1.7)
    num_records: int = 0          # HEDR
    next_object_id: int = 0x800   # HEDR

    @classmethod
    def parse_from_record(cls, rec: Record) -> "TES4Header":
        # Not an assert: `python -O` strips asserts, and a non-TES4 record here
        # would parse as a header with empty masters -> wrong master indices.
        if rec.sig != b"TES4":
            raise ValueError(f"expected TES4 header record, got {rec.sig!r}")
        masters: list[str] = []
        author = ""
        description = ""
        version = 1.7
        num_records = 0
        next_obj = 0x800
        pending_mast: str | None = None
        for sig, sd in iter_subrecords(rec.payload):
            if sig == b"HEDR":
                if len(sd) >= 12:
                    version, num_records, next_obj = struct.unpack("<fIi", sd[:12])
            elif sig == b"CNAM":
                author = sd.rstrip(b"\x00").decode("utf-8", errors="ignore")
            elif sig == b"SNAM":
                description = sd.rstrip(b"\x00").decode("utf-8", errors="ignore")
            elif sig == b"MAST":
                pending_mast = sd.rstrip(b"\x00").decode("utf-8", errors="ignore")
            elif sig == b"DATA":
                if pending_mast is not None:
                    masters.append(pending_mast)
                    pending_mast = None
        return cls(masters=masters, author=author, description=description,
                   flags=rec.flags, version=version,
                   num_records=num_records, next_object_id=next_obj)

    def to_record(self) -> Record:
        payload = b""
        payload += encode_subrecord(
            b"HEDR",
            struct.pack("<fIi", self.version, self.num_records, self.next_object_id),
        )
        payload += encode_subrecord(b"CNAM", encode_zstring(self.author))
        if self.description:
            payload += encode_subrecord(b"SNAM", encode_zstring(self.description))
        for m in self.masters:
            payload += encode_subrecord(b"MAST", encode_zstring(m))
            payload += encode_subrecord(b"DATA", struct.pack("<Q", 0))  # 8-byte master file size (typically 0)
        return Record(sig=b"TES4", flags=self.flags, formid=0,
                      version_unk=0x002C, payload=payload)  # SE form 44


# ----- ESP file -----------------------------------------------------------

# Read-only parse cache, keyed by (path, mtime, size). Each source ESP is
# parsed once per run. The cached object is shared — callers that mutate or
# emit output must use plain `ESP.load`, never `load_cached`.
_LOAD_CACHE: "dict[tuple, ESP]" = {}


def clear_load_cache() -> None:
    """Drop the read-only ESP parse cache (call at the start of a batch)."""
    _LOAD_CACHE.clear()


@dataclass
class ESP:
    """A whole ESP/ESM file: TES4 header + a list of top-level GRUPs."""
    header: TES4Header
    groups: list[Group] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "ESP":
        path = Path(path)
        data = path.read_bytes()
        tes4_rec, offset = Record.parse(data, 0)
        header = TES4Header.parse_from_record(tes4_rec)
        groups: list[Group] = []
        while offset < len(data):
            prev = offset
            grp, offset = Group.parse(data, offset)
            groups.append(grp)
            # A malformed top-level GRUP whose declared size is < the 24-byte
            # header (e.g. a zeroed/truncated size field) makes Group.parse
            # return the SAME offset -> this loop would spin forever, hanging the
            # whole batch with no error. The nested-GRUP walk already guards this
            # (Group.parse line ~211); enforce progress here too. Fail loud
            # instead of hanging so a corrupt plugin surfaces cleanly.
            if offset <= prev:
                raise ValueError(
                    f"non-advancing top-level GRUP at offset {prev} in "
                    f"{path.name} (declared size < header; plugin is corrupt)")
        return cls(header=header, groups=groups)

    @classmethod
    def load_cached(cls, path: str | Path) -> "ESP":
        """READ-ONLY cached `load` (keyed by path+mtime+size). The returned
        object is shared across callers — do not mutate it. Use plain `load`
        for any code path that edits records or emits output."""
        p = Path(path)
        try:
            st = p.stat()
            key = (str(p).lower(), int(st.st_mtime_ns), st.st_size)
        except OSError:
            return cls.load(p)
        cached = _LOAD_CACHE.get(key)
        if cached is None:
            cached = cls.load(p)
            _LOAD_CACHE[key] = cached
        return cached

    def save(self, path: str | Path) -> None:
        # Recount records for HEDR — sum of records across all groups.
        total = sum(len(g.records) for g in self.groups)
        self.header.num_records = total

        out = self.header.to_record().to_bytes()
        for g in self.groups:
            out += g.to_bytes()
        from .atomic_io import atomic_write_bytes
        atomic_write_bytes(path, out)

    def group(self, label: bytes) -> Group | None:
        for g in self.groups:
            if g.label == label:
                return g
        return None
