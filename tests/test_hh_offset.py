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

"""Guard for hh_offset: the binary HH_OFFSET (heel) read + transplant that lets
heeled boots be UBE-converted while keeping their heel (pynifly drops the
NiFloatExtraData, so we re-inject it ourselves). Self-contained: builds a minimal
valid SSE NIF in memory via the module's own (round-trip-verified) serializer."""
import struct
from src import hh_offset as H


def _minimal_nif():
    # Root NiNode block: Name(int) + NumExtraData(uint=0) + opaque tail. Our
    # transplant only touches offset 4 (count) and inserts at offset 8, so the
    # block just needs >= 8 bytes; the rest is opaque to the parser.
    root = struct.pack("<i", -1) + struct.pack("<I", 0) + b"\x00" * 20
    p = dict(hdr_string=b"Gamebryo File Format, Version 20.2.0.7\n",
             version=0x14020007, endian=1, user_version=12, num_blocks=1,
             bs_version=100, author=b"diag", proc=b"", export=b"",
             block_types=[b"NiNode"], bti=[0], bsizes=[len(root)],
             max_str=0, strings=[], num_groups=0, groups=[],
             blocks=[root], footer=struct.pack("<I", 1) + struct.pack("<i", 0))
    return H._serialize(p)


def _nif_with_shape():
    # Root NiNode (block 0) + a BSTriShape (block 1). NiOverride High Heels reads
    # HH_OFFSET off the SHAPE, so the transplant must attach it to block 1, leaving
    # the root's extra-data count at 0.
    root = struct.pack("<i", -1) + struct.pack("<I", 0) + b"\x00" * 20
    shape = struct.pack("<i", -1) + struct.pack("<I", 0) + b"\x00" * 20
    p = dict(hdr_string=b"Gamebryo File Format, Version 20.2.0.7\n",
             version=0x14020007, endian=1, user_version=12, num_blocks=2,
             bs_version=100, author=b"diag", proc=b"", export=b"",
             block_types=[b"NiNode", b"BSTriShape"], bti=[0, 1],
             bsizes=[len(root), len(shape)],
             max_str=0, strings=[], num_groups=0, groups=[],
             blocks=[root, shape], footer=struct.pack("<I", 1) + struct.pack("<i", 0))
    return H._serialize(p)


def test_parser_round_trips_minimal_nif():
    data = _minimal_nif()
    assert H._serialize(H._parse(data)) == data
    assert H._parse_if_lossless(data) is not None


def test_transplant_attaches_to_shape_not_root(tmp_path):
    # With a BSTriShape present, HH_OFFSET must land on the shape (block 1), not the
    # root (block 0) -- the root-attached version is invisible to NiOverride HH.
    f = tmp_path / "boot.nif"
    f.write_bytes(_nif_with_shape())
    assert H.transplant_hh_offset(f, 4.65) is True
    assert abs(H.read_hh_offset(f) - 4.65) < 1e-4
    p = H._parse(f.read_bytes())
    root_extra, = struct.unpack_from("<I", p["blocks"][0], 4)
    shape_extra, = struct.unpack_from("<I", p["blocks"][1], 4)
    assert root_extra == 0, "HH_OFFSET must NOT be on the root node"
    assert shape_extra == 1, "HH_OFFSET must be on the BSTriShape"


def test_transplant_then_read_roundtrips(tmp_path):
    f = tmp_path / "boot.nif"
    f.write_bytes(_minimal_nif())
    # no heel yet
    assert H.read_hh_offset(f) is None
    # transplant a heel offset
    assert H.transplant_hh_offset(f, 4.65) is True
    # now readable + correct
    v = H.read_hh_offset(f)
    assert v is not None and abs(v - 4.65) < 1e-4
    # output still parses losslessly + carries the string
    out = f.read_bytes()
    assert H._parse_if_lossless(out) is not None
    assert b"HH_OFFSET" in out
    # root now lists one extra-data ref
    p = H._parse(out)
    n_extra, = struct.unpack_from("<I", p["blocks"][0], 4)
    assert n_extra == 1


def test_transplant_refuses_unparseable(tmp_path):
    # garbage that our parser can't round-trip -> transplant must no-op (False),
    # so the caller falls back to the original mesh instead of corrupting.
    f = tmp_path / "junk.nif"
    f.write_bytes(b"not a nif file at all" * 10)
    assert H.transplant_hh_offset(f, 4.65) is False
    assert H.read_hh_offset(f) is None
