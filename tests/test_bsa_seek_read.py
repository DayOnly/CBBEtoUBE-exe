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

"""#bsa-seek-read -- a table-only BSA reads one entry by seek.

`BSAArchive.read_file` on a table-only archive loaded the WHOLE archive into
memory, and the batch's mesh index and the interface-strings reader opened
theirs eagerly and kept them. The archives here are written by `_write_bsa` in
exactly the layout `BSAArchive._parse` reads, with a large filler entry so that
"loaded the file" and "read one entry" are far apart in size.

The LZ4-block entry sits BEFORE other entries on purpose: zlib and LZ4-frame
decoders ignore bytes after their stream, so a seek read that sliced past the
entry's end would still pass on those; an LZ4 block consumes its whole input."""
import struct
import zlib
from pathlib import Path

import pytest

from src import bsa_strings as bs


def _pack(payload, compress):
    if compress == "zlib":
        return zlib.compress(payload)
    if compress == "lz4":
        return pytest.importorskip("lz4.frame").compress(payload)
    if compress == "lz4block":
        return pytest.importorskip("lz4.block").compress(payload, store_size=False)
    raise ValueError(compress)


def _write_bsa(path, entries, *, compress_default=False, embed_names=False):
    """A v105 archive. `entries` is [(folder, name, payload, compress)] with
    compress one of None, "zlib", "lz4", "lz4block"."""
    flags = bs._ARCHIVE_FLAG_DIRNAMES | bs._ARCHIVE_FLAG_FILENAMES
    if compress_default:
        flags |= bs._ARCHIVE_FLAG_COMPRESSED
    if embed_names:
        flags |= bs._ARCHIVE_FLAG_EMBED_NAMES
    folders = {}
    for folder, name, payload, compress in entries:
        folders.setdefault(folder, []).append((name, payload, compress))
    order = list(folders)
    file_count = sum(len(v) for v in folders.values())
    total_folder_name_len = sum(len(f) + 1 for f in order)
    names_block = b"".join(n.encode("latin-1") + b"\x00"
                           for f in order for n, _, _ in folders[f])
    header_len, frec = 36, 24
    pos = header_len + len(order) * frec
    block_offsets = []
    for f in order:
        block_offsets.append(pos)
        pos += 1 + len(f) + 1 + 16 * len(folders[f])
    data_start = pos + len(names_block)

    bodies, file_offs, size_fields, cur = [], [], [], data_start
    for f in order:
        for name, payload, compress in folders[f]:
            body = b""
            if embed_names:
                full = f"{f}\\{name}".encode("latin-1")
                body += bytes([len(full)]) + full
            if compress:
                body += struct.pack("<I", len(payload)) + _pack(payload, compress)
                size = len(body)
            else:
                body += payload
                size = len(payload)
            toggle = bool(compress) != compress_default
            size_fields.append(size | (bs._FILE_SIZE_COMPRESS_TOGGLE if toggle else 0))
            file_offs.append(cur)
            bodies.append(body)
            cur += len(body)

    out = bytearray(b"BSA\x00" + struct.pack(
        "<8I", 105, header_len, flags, len(order), file_count,
        total_folder_name_len, len(names_block), 0))
    for f, block in zip(order, block_offsets):
        out += struct.pack("<QIIQ", 0, len(folders[f]), 0, block + len(names_block))
    i = 0
    for f in order:
        out += bytes([len(f) + 1]) + f.encode("latin-1") + b"\x00"
        for _ in folders[f]:
            out += struct.pack("<QII", 0, size_fields[i], file_offs[i])
            i += 1
    out += names_block
    assert len(out) == data_start
    for body in bodies:
        out += body
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


_ENTRIES = [
    ("meshes\\armor\\a", "cuirass_1.nif", b"NIF body " * 5000, "zlib"),
    ("meshes\\armor\\a", "gauntlets_1.nif", b"gauntlet " * 4000, "lz4block"),
    ("meshes\\armor\\a", "cuirass_0.nif", bytes(range(256)) * 4000, "lz4"),
    ("meshes\\armor\\b", "boots_1.nif", b"raw boots " * 3000, None),
    ("textures\\big", "filler.dds", bytes(2_000_000), None),
]


def _key(folder, name):
    return (folder.replace("\\", "/") + "/" + name).lower()


@pytest.mark.parametrize("compress_default, embed_names",
                         [(False, False), (True, False), (False, True), (True, True)])
def test_a_table_only_archive_reads_each_entry_by_seek(tmp_path, compress_default,
                                                       embed_names):
    p = _write_bsa(tmp_path / "a.bsa", _ENTRIES, compress_default=compress_default,
                   embed_names=embed_names)
    eager = bs.BSAArchive(p)
    lazy = bs.BSAArchive(p, eager=False)
    assert len(lazy.list_files()) == len(_ENTRIES)
    for folder, name, payload, _ in _ENTRIES:
        assert eager.read_file(_key(folder, name)) == payload
        assert lazy.read_file(_key(folder, name)) == payload, _key(folder, name)
    size = p.stat().st_size
    assert not lazy._eager and len(lazy._data) < size // 10, (
        f"reading {len(_ENTRIES)} entries left {len(lazy._data):,} of the "
        f"archive's {size:,} bytes in memory")


def test_an_entry_past_the_end_of_a_truncated_archive_is_refused(tmp_path):
    p = _write_bsa(tmp_path / "a.bsa", _ENTRIES)
    p.write_bytes(p.read_bytes()[:-1_000_000])         # cuts into the 2 MB filler
    key = _key("textures\\big", "filler.dds")
    assert bs.BSAArchive(p).read_file(key) is None
    assert bs.BSAArchive(p, eager=False).read_file(key) is None
    assert bs.BSAArchive(p, eager=False).read_file(
        _key("meshes\\armor\\b", "boots_1.nif")) == b"raw boots " * 3000


def test_the_batch_mesh_index_keeps_only_the_table(tmp_path):
    from src import auto_convert as ac
    mod = tmp_path / "SomeArmorMod"
    p = _write_bsa(mod / "SomeArmorMod.bsa", _ENTRIES)
    idx = ac._BsaMeshIndex([mod], tmp_path / "_staging")
    res = idx.extract("armor/a/cuirass_1.nif")
    assert res is not None and Path(res[0]).read_bytes() == b"NIF body " * 5000
    held = list(idx._open.values())
    assert len(held) == 1
    assert held[0]._eager is False and len(held[0]._data) < p.stat().st_size // 10, (
        "the batch index holds the whole archive for the rest of the batch")


def test_the_interface_strings_archive_is_opened_table_only(tmp_path):
    table = struct.pack("<II", 1, 5) + struct.pack("<II", 0x10, 0) + b"Robe\x00"
    data_dir = tmp_path / "Data"
    _write_bsa(data_dir / "Skyrim - Interface.bsa",
               [("strings", "skyrim_english.strings", table, None),
                ("textures\\big", "filler.dds", bytes(1_000_000), None)])
    resolver = bs.StringResolver(data_dir)
    assert resolver._table_for("Skyrim.esm").get(0x10) == "Robe"
    assert resolver._bsa is not None and resolver._bsa._eager is False, (
        "the 101 MB interface archive is loaded whole to read one strings table")
