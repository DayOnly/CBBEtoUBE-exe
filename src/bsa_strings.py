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

"""Resolve localized ARMO/record names from Skyrim's `.STRINGS` tables.

Vanilla + DLC master ESMs (Skyrim.esm, Dawnguard.esm, Dragonborn.esm, ...)
are *localized*: a record's FULL subrecord holds a 4-byte string ID that
indexes into a `<plugin>_<language>.STRINGS` file rather than an inline
name. Those `.STRINGS` files live INSIDE `Skyrim - Interface.bsa` (Skyrim
Special Edition bundles every plugin's strings there, base game AND DLC).

When the UBE patcher overrides one of those master ARMO records it can't
carry the LSTRING ref into its own non-localized patch, so historically it
*synthesized* a name from the EDID — which produces developer-internal
names with variant/enchant tags (e.g. "ArmorVariant Red", "Robe
Destruction 06") instead of the real player-facing name (e.g. "Armor
Variant", "Robe"). This module recovers the REAL name by reading the actual
strings table out of the BSA.

Two small parsers, no third-party deps:
  * BSA v104/v105 archive reader (uncompressed + zlib + optional LZ4).
  * STRINGS / ILSTRINGS / DLSTRINGS table parser.

`StringResolver` ties them together: give it a game Data folder and it
lazily extracts + parses the right `<plugin>_english.STRINGS` table on
first lookup, caching the result.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


# --------------------------------------------------------------------------
# BSA archive reader (Bethesda Archive, TES5/SSE: version 104 / 105)
# --------------------------------------------------------------------------

_ARCHIVE_FLAG_DIRNAMES = 0x1
_ARCHIVE_FLAG_FILENAMES = 0x2
_ARCHIVE_FLAG_COMPRESSED = 0x4
_ARCHIVE_FLAG_EMBED_NAMES = 0x100
_FILE_SIZE_COMPRESS_TOGGLE = 0x40000000
_FILE_SIZE_MASK = 0x3FFFFFFF


class BSAArchive:
    """Minimal random-access reader for a Bethesda BSA (v104 Skyrim LE,
    v105 Skyrim SE). Indexes folder/file names on construction; extracts
    a single file's bytes on demand via `read_file`.
    """

    def __init__(self, path: str | Path, eager: bool = True):
        self.path = Path(path)
        # eager=True (default): read the full archive up front.
        # eager=False: read only the file-table region so list_files() is cheap
        # on large BSAs; read_file() lazily loads the rest on demand.
        self._eager = eager
        self._data = (self.path.read_bytes() if eager
                      else self._read_table_region())
        self._index: dict[str, tuple[int, int, bool]] = {}  # name -> (off, size, compressed)
        self._parse()

    def _read_table_region(self) -> bytes:
        """Read only the header+folder+file-record+name-block bytes that _parse()
        walks; leaves the bulk file data on disk. Falls back to reading the whole
        file if anything looks off."""
        try:
            with open(self.path, "rb") as f:
                head = f.read(36)
                if len(head) < 36 or head[:4] != b"BSA\x00":
                    f.seek(0)
                    return f.read()
                (version, folder_rec_off, archive_flags, folder_count, file_count,
                 tot_fold_nl, tot_file_nl, _ff) = struct.unpack_from("<8I", head, 4)
                frec = 24 if version >= 105 else 16
                end = folder_rec_off + folder_count * frec
                if archive_flags & _ARCHIVE_FLAG_DIRNAMES:
                    end += tot_fold_nl + folder_count    # +1 length byte per folder
                end += file_count * 16
                if archive_flags & _ARCHIVE_FLAG_FILENAMES:
                    end += tot_file_nl
                f.seek(0)
                return f.read(end + 64)                   # + slack
        except OSError:
            return self.path.read_bytes()

    def _parse(self) -> None:
        d = self._data
        magic = d[:4]
        if magic != b"BSA\x00":
            raise ValueError(f"{self.path.name}: not a BSA (magic {magic!r})")
        (version, folder_rec_off, archive_flags, folder_count, file_count,
         total_folder_name_len, total_file_name_len, file_flags) = struct.unpack_from(
            "<8I", d, 4)
        self.version = version
        self.archive_flags = archive_flags
        default_compressed = bool(archive_flags & _ARCHIVE_FLAG_COMPRESSED)
        embed_names = bool(archive_flags & _ARCHIVE_FLAG_EMBED_NAMES)

        # Folder record size differs across versions: v103/104 = 16 bytes
        # (hash:8 count:4 offset:4); v105 = 24 bytes (hash:8 count:4
        # pad:4 offset:8).
        off = folder_rec_off
        folders: list[tuple[int, int]] = []  # (file_count, block_offset)
        # Reject an absurd folder_count / out-of-range table offset from a
        # malformed header before it drives an OOB struct.unpack or a huge loop.
        _frec_sz = 24 if version >= 105 else 16
        if off < 0 or folder_count < 0 or off + folder_count * _frec_sz > len(d):
            raise ValueError(f"{self.path.name}: bad BSA folder table "
                             f"(count={folder_count}, off={off})")
        for _ in range(folder_count):
            if version >= 105:
                _hash, count, _pad, blk_off = struct.unpack_from("<QIIQ", d, off)
                off += 24
            else:
                _hash, count, blk_off = struct.unpack_from("<QII", d, off)
                off += 16
            # blk_off includes total_file_name_len as an addend.
            folders.append((count, blk_off - total_file_name_len))

        # Walk each folder's FileRecordBlock to collect (folder_name,
        # [(file_hash, size_field, file_off), ...]).
        folder_names: list[str] = []
        file_records: list[list[tuple[int, int]]] = []  # per folder: [(size_field, off)]
        for (count, blk_off) in folders:
            p = blk_off
            # A malformed header can make blk_off negative (huge
            # total_file_name_len) or past EOF; skip the folder rather than
            # negative-index-wrap into the buffer or OOB-unpack.
            if p < 0 or p >= len(d):
                folder_names.append("")
                file_records.append([])
                continue
            fname = ""
            if archive_flags & _ARCHIVE_FLAG_DIRNAMES:
                strlen = d[p]
                p += 1
                fname = d[p:p + strlen].split(b"\x00", 1)[0].decode(
                    "latin-1", "ignore")
                p += strlen
            recs = []
            # Clamp the per-folder file count to what actually fits (16 bytes
            # each) so a crafted count can't OOB-unpack or spin.
            if count < 0 or p + count * 16 > len(d):
                count = max(0, (len(d) - p) // 16)
            for _ in range(count):
                _fh, size_field, file_off = struct.unpack_from("<QII", d, p)
                p += 16
                recs.append((size_field, file_off))
            folder_names.append(fname.replace("\\", "/").lower())
            file_records.append(recs)

        # File names block (basenames, null-terminated, in folder/file order).
        file_names: list[str] = []
        if archive_flags & _ARCHIVE_FLAG_FILENAMES:
            # The block sits right after the last folder's file records.
            # Easiest: it is total_file_name_len bytes long and starts
            # immediately after the final FileRecordBlock we just parsed.
            names_start = p
            block = d[names_start:names_start + total_file_name_len]
            for chunk in block.split(b"\x00"):
                if chunk:
                    file_names.append(chunk.decode("latin-1", "ignore"))

        # Build the name -> (offset, size, compressed) index.
        name_iter = iter(file_names)
        for folder_name, recs in zip(folder_names, file_records):
            for (size_field, file_off) in recs:
                try:
                    basename = next(name_iter)
                except StopIteration:
                    basename = ""
                size = size_field & _FILE_SIZE_MASK
                compressed = default_compressed
                if size_field & _FILE_SIZE_COMPRESS_TOGGLE:
                    compressed = not compressed
                full = f"{folder_name}/{basename}".lower() if folder_name else basename.lower()
                self._index[full] = (file_off, size, compressed)
        self._embed_names = embed_names

    def list_files(self, prefix: str = "") -> list[str]:
        prefix = prefix.lower()
        return sorted(n for n in self._index if n.startswith(prefix))

    def read_file(self, name: str) -> bytes | None:
        """Return the decompressed bytes of `name` (forward-slash path,
        case-insensitive), or None if not present."""
        key = name.replace("\\", "/").lower()
        ent = self._index.get(key)
        if ent is None:
            return None
        off, size, compressed = ent
        if not self._eager and off + size > len(self._data):
            # non-eager archive (table-only) asked to extract -> load full file
            self._data = self.path.read_bytes()
            self._eager = True
        d = self._data
        # Bounds-check the file-supplied offset/size UNCONDITIONALLY (not only on
        # the non-eager branch): a malformed BSA whose record offset points past
        # EOF must return None, never OOB-read (IndexError / struct.error) from a
        # crafted archive.
        if off < 0 or size < 0 or off + size > len(d):
            return None
        p = off
        if self._embed_names:
            # bstring (uint8 length + chars, no null) prefix to skip.
            if p >= len(d):
                return None
            blen = d[p]
            p += 1 + blen
        if compressed:
            if p + 4 > len(d):
                return None
            orig_size = struct.unpack_from("<I", d, p)[0]
            p += 4
            comp = d[p:off + size]
            # Compression algorithm by BSA version: Oldrim (v104) = zlib;
            # Skyrim SE (v105) = LZ4 frame (magic 0x184D2204). Vanilla SSE
            # Meshes/Textures BSAs use LZ4 frame; lz4.block is a fallback for
            # non-frame LZ4 BSAs.
            if comp[:4] == b"\x04\x22\x4d\x18":
                try:
                    import lz4.frame  # type: ignore
                    return lz4.frame.decompress(comp)
                except Exception:
                    return None
            try:
                return zlib.decompress(comp)
            except zlib.error:
                try:
                    import lz4.block  # type: ignore
                    return lz4.block.decompress(comp, uncompressed_size=orig_size)
                except Exception:
                    return None
        return d[p:p + size]


# --------------------------------------------------------------------------
# STRINGS / ILSTRINGS / DLSTRINGS table parser
# --------------------------------------------------------------------------

def parse_strings_table(data: bytes, lengthprefixed: bool) -> dict[int, str]:
    """Parse a Bethesda strings table.

    Layout: uint32 count, uint32 dataSize, then `count` directory entries
    of (uint32 stringId, uint32 offset), then the string data block (its
    size == dataSize). `offset` is relative to the start of the data block.

    `.STRINGS`            -> null-terminated strings (lengthprefixed=False)
    `.ILSTRINGS`/`.DLSTRINGS` -> each string prefixed with uint32 length
                                 (lengthprefixed=True)
    """
    out: dict[int, str] = {}
    if len(data) < 8:
        return out
    count, _data_size = struct.unpack_from("<II", data, 0)
    dir_off = 8
    # Bound the file-supplied count against the buffer: each directory entry is
    # 8 bytes and must sit inside the file, so at most (len-8)//8 entries can be
    # real. Without this a crafted count (e.g. 0xFFFFFFFF) either spins for
    # billions of iterations (DoS) or raises a raw struct.error past the buffer.
    max_entries = max(0, (len(data) - dir_off) // 8)
    if count > max_entries:
        count = max_entries
    data_block = dir_off + count * 8
    for i in range(count):
        sid, soff = struct.unpack_from("<II", data, dir_off + i * 8)
        pos = data_block + soff
        if pos >= len(data):
            continue
        if lengthprefixed:
            if pos + 4 > len(data):
                continue
            slen = struct.unpack_from("<I", data, pos)[0]
            raw = data[pos + 4:pos + 4 + slen]
            raw = raw.split(b"\x00", 1)[0]
        else:
            end = data.find(b"\x00", pos)
            raw = data[pos:end if end >= 0 else len(data)]
        out[sid] = raw.decode("cp1252", "replace")
    return out


# --------------------------------------------------------------------------
# Resolver: master plugin name + string id -> real text
# --------------------------------------------------------------------------

class StringResolver:
    """Resolves localized FULL string IDs to text for vanilla/DLC masters.

    `data_dir` is the game's Data folder (the one with `Skyrim -
    Interface.bsa`). Strings tables are extracted from that BSA and cached
    per (plugin, language). Lookups for a plugin we have no strings for
    return None so the caller can fall back to EDID synthesis.
    """

    INTERFACE_BSA = "Skyrim - Interface.bsa"

    def __init__(self, data_dir: str | Path, language: str = "english"):
        self.data_dir = Path(data_dir)
        self.language = language.lower()
        self._bsa: BSAArchive | None = None
        self._bsa_loaded = False
        self._tables: dict[str, dict[int, str]] = {}  # plugin_stem -> {id: text}

    def _interface_bsa(self) -> BSAArchive | None:
        if not self._bsa_loaded:
            self._bsa_loaded = True
            p = self.data_dir / self.INTERFACE_BSA
            if p.is_file():
                try:
                    self._bsa = BSAArchive(p)
                except Exception:
                    self._bsa = None
        return self._bsa

    def _table_for(self, plugin_name: str) -> dict[int, str]:
        # plugin_name like "Dawnguard.esm" -> stem "dawnguard"
        stem = Path(plugin_name).stem.lower()
        if stem in self._tables:
            return self._tables[stem]
        table: dict[int, str] = {}
        bsa = self._interface_bsa()
        if bsa is not None:
            # ARMO FULL uses the regular .STRINGS table.
            inner = f"strings/{stem}_{self.language}.strings"
            raw = bsa.read_file(inner)
            # Loose strings (Data/Strings/...) win if present.
            loose = self.data_dir / "Strings" / f"{stem}_{self.language}.STRINGS"
            if loose.is_file():
                try:
                    raw = loose.read_bytes()
                except Exception:
                    pass
            if raw:
                try:
                    table = parse_strings_table(raw, lengthprefixed=False)
                except Exception:
                    table = {}
        self._tables[stem] = table
        return table

    def resolve(self, plugin_name: str, string_id: int) -> str | None:
        """Return the localized text for `string_id` defined by
        `plugin_name`, or None if unavailable."""
        if not string_id:
            return None
        table = self._table_for(plugin_name)
        return table.get(int(string_id))


# --------------------------------------------------------------------------
# Validation entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src import esp  # noqa: E402
    from src import paths as _paths  # noqa: E402

    _lay = _paths.discover_layout()
    data_dir = (sys.argv[1] if len(sys.argv) > 1
                else (str(_lay.game_data_dirs[0]) if _lay.game_data_dirs
                      else "."))
    masters_dir = data_dir
    if _lay.mods_root is not None:
        for _m in _lay.mods_root.iterdir() if _lay.mods_root.is_dir() else []:
            if (_m / "Dawnguard.esm").is_file():
                masters_dir = str(_m)
                break

    res = StringResolver(data_dir)
    bsa = res._interface_bsa()
    print("Interface.bsa strings files:")
    for n in (bsa.list_files("strings/") if bsa else []):
        print("  ", n)

    # Show real ARMO names from the cleaned DLC masters.
    for master in ["Dawnguard.esm", "Dragonborn.esm"]:
        path = Path(masters_dir) / master
        if not path.is_file():
            continue
        e = esp.ESP.load(str(path))
        print(f"\n=== {master} sample ARMO real names ===")
        shown = 0
        for g in e.groups:
            if g.label != b"ARMO":
                continue
            for rec in g.records:
                if rec.sig != b"ARMO":
                    continue
                edid = sid = None
                for sig, dat in esp.iter_subrecords(rec.payload):
                    if sig == b"EDID":
                        edid = dat.rstrip(b"\x00").decode("utf-8", "ignore")
                    elif sig == b"FULL" and len(dat) == 4:
                        sid = struct.unpack("<I", dat)[0]
                if edid and sid and ("Vampire" in edid or "MoragTong" in edid):
                    real = res.resolve(master, sid)
                    print(f"  {edid}  id={sid}  REAL={real!r}")
                    shown += 1
                    if shown >= 8:
                        break
            break
