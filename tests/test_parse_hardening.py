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

"""Binary-parse hardening: the central subrecord walker must tolerate a
truncated/malformed source record (stop cleanly, never raise mid-pass nor yield
a short slice a verbatim-copy pass would re-emit corrupt), and validate_patch
must flag the headerless-MODT and out-of-range-KWDA crash classes."""
import struct

from src import esp, ube_patcher
from src.esp import encode_subrecord, encode_zstring, iter_subrecords


# ---- iter_subrecords bounds ------------------------------------------------

def test_iter_wellformed_unchanged():
    payload = (encode_subrecord(b"EDID", b"Hi\x00")
               + encode_subrecord(b"DATA", b"\x01\x02\x03\x04"))
    assert list(iter_subrecords(payload)) == [
        (b"EDID", b"Hi\x00"), (b"DATA", b"\x01\x02\x03\x04")]


def test_iter_truncated_header_stops_no_raise():
    # a valid EDID then 3 trailing bytes (< the 6-byte sig+size header)
    payload = encode_subrecord(b"EDID", b"X\x00") + b"\xAA\xBB\xCC"
    assert list(iter_subrecords(payload)) == [(b"EDID", b"X\x00")]


def test_iter_oversized_declared_size_does_not_yield_short_slice():
    # header declares 100 data bytes but only 4 are present -> must stop, NOT
    # yield (DESC, 4-bytes) which a verbatim copy would re-emit as a corrupt
    # subrecord whose size header overstates its data.
    payload = (encode_subrecord(b"EDID", b"X\x00")
               + b"DESC" + struct.pack("<H", 100) + b"\x01\x02\x03\x04")
    assert list(iter_subrecords(payload)) == [(b"EDID", b"X\x00")]


def test_iter_xxxx_large_size_override():
    big = b"A" * 70000   # > 0xFFFF -> requires the XXXX size override
    payload = (b"XXXX" + struct.pack("<H", 4) + struct.pack("<I", len(big))
               + b"DATA" + struct.pack("<H", 0) + big)
    assert list(iter_subrecords(payload)) == [(b"DATA", big)]


def test_iter_truncated_xxxx_stops():
    # XXXX header says 4 bytes of override but none present -> stop cleanly
    payload = b"XXXX" + struct.pack("<H", 4)
    assert list(iter_subrecords(payload)) == []


# ---- validate_patch: MODT structure + KWDA range ---------------------------

def _save_arma(tmp_path, modt_bytes):
    payload = (encode_subrecord(b"EDID", encode_zstring("TestAA"))
               + encode_subrecord(b"BOD2", struct.pack("<II", 1 << 2, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", 0x19))
               + encode_subrecord(b"MOD3", encode_zstring("a.nif"))
               + encode_subrecord(b"MO3T", modt_bytes))
    arma = esp.Record(sig=b"ARMA", flags=0, formid=(1 << 24) | 0x800,
                      timestamp_vc=0, version_unk=0x2C, payload=payload)
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"],
                                      next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMA", records=[arma])])
    p = tmp_path / "t.esp"
    e.save(p)
    return p


def test_validate_flags_headerless_modt(tmp_path):
    # Headerless LE-port MODT: 24 raw bytes; offset-4 u32 lands on "dds\0" =
    # 7.5M -> 12*(1+7.5M) != 24 -> malformed (the overread CTD class).
    p = _save_arma(tmp_path, b"dds\x00" * 6)
    w = ube_patcher.validate_patch(p, check_nifs=False)
    assert any(x.startswith("modt-malformed") for x in w), w


def test_validate_clean_modt_no_warning(tmp_path):
    p = _save_arma(tmp_path, struct.pack("<III", 2, 0, 0))   # valid empty MODT
    w = ube_patcher.validate_patch(p, check_nifs=False)
    assert not any(x.startswith("modt-malformed") for x in w), w


def test_validate_flags_out_of_range_kwda(tmp_path):
    # ARMO whose KWDA array references a master index past the master list.
    kwda = (struct.pack("<I", 0x06BBD9)            # in-range (Skyrim.esm idx 0)
            + struct.pack("<I", 0x05000001))       # top byte 5 >> own_byte 1
    payload = (encode_subrecord(b"EDID", encode_zstring("TestArmor"))
               + encode_subrecord(b"BOD2", struct.pack("<II", 1 << 2, 0))
               + encode_subrecord(b"FULL", encode_zstring("Test Armor"))
               + encode_subrecord(b"KSIZ", struct.pack("<I", 2))
               + encode_subrecord(b"KWDA", kwda)
               + encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0)))
    armo = esp.Record(sig=b"ARMO", flags=0, formid=(1 << 24) | 0x801,
                      timestamp_vc=0, version_unk=0x2C, payload=payload)
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"],
                                      next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO", records=[armo])])
    p = tmp_path / "t.esp"
    e.save(p)
    w = ube_patcher.validate_patch(p, check_nifs=False)
    assert any(x.startswith("formid-out-of-range") for x in w), w
