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

"""Untrusted-input security audit regression tests (2026-06-23).

Each test exercises a hardening fix by feeding the malicious input it defends
against and asserting it's refused/bounded. See ROBUSTNESS_AUDIT_2026-06-22.md
(Security section) for the findings (S1-S6)."""
import os
import struct
import zlib
from pathlib import Path

import pytest


# --- S1: path-traversal containment (BSA zip-slip / output / overlay) --------

def test_is_within_dir_blocks_traversal(tmp_path):
    from src.paths import is_within_dir
    base = tmp_path / "out" / "meshes"
    base.mkdir(parents=True)
    # legit relative paths stay inside
    assert is_within_dir(base, base / "armor" / "cuirass_1.nif")
    assert is_within_dir(base, base)
    # `..` traversal escapes -> rejected
    assert not is_within_dir(base, base / ".." / ".." / "evil.nif")
    assert not is_within_dir(base, base / "a" / ".." / ".." / ".." / ".." / "x")
    # absolute path escape -> rejected
    abs_escape = (Path("C:/Windows/System32/evil.dll")
                  if os.name == "nt" else Path("/etc/passwd"))
    assert not is_within_dir(base, abs_escape)


# --- S3: ESP record decompression bomb ---------------------------------------

def _compressed_record(uncomp_size, raw):
    from src.esp import FLAG_COMPRESSED
    comp = struct.pack("<I", uncomp_size) + zlib.compress(raw)
    return (b"ARMO" + struct.pack("<IIIII", len(comp), FLAG_COMPRESSED,
                                  0x01000800, 0, 0x2C) + comp)


def test_esp_decompression_bomb_rejected():
    from src.esp import Record
    # (a) declared size over the cap -> rejected before inflating
    with pytest.raises(ValueError):
        Record.parse(_compressed_record(200 * 1024 * 1024, b"x"), 0)
    # (b) inflates far past its (small) declared size -> rejected
    with pytest.raises(ValueError):
        Record.parse(_compressed_record(10, b"\x00" * 200_000), 0)


def test_esp_honest_compressed_record_still_parses():
    from src.esp import Record
    body = b"EDID\x03\x00AB\x00"
    rec, _ = Record.parse(_compressed_record(len(body), body), 0)
    assert rec.payload == body  # the bound never trips on a valid record


# --- S4: TRI shape-count explosion -------------------------------------------

def test_tri_shape_count_capped():
    from src.tri import TriFile, TRI_MAGIC
    shape = b"\x01A\x00\x00"   # name_len=1, "A", num_morphs=0 (4 bytes/shape)
    data = TRI_MAGIC + struct.pack("<H", 1) + shape * 100_050
    tf = TriFile.parse(data)
    assert len(tf.shapes) == 100_000   # capped, not 100_050


# --- S5: HDT XML entity-expansion (billion laughs) ---------------------------

def test_validate_hdt_xml_rejects_doctype(tmp_path):
    from src.hdt_xml_gen import validate_armor_hdt_xml
    bomb = tmp_path / "bomb.xml"
    bomb.write_bytes(
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [<!ENTITY a "AAAAAAAAAA">]>\n'
        b'<system>&a;</system>')
    warns = validate_armor_hdt_xml(bomb, [])   # bone list unused: guard returns early
    assert any(("DOCTYPE" in w or "ENTITY" in w or "entity" in w)
               for w in warns), warns


# --- S6: OSD morph_count explosion -------------------------------------------

def test_osd_morph_count_clamped():
    from src.osd import OsdFile, OSD_MAGIC
    # huge declared count, no actual morph data -> must clamp, not hang/OOM
    data = OSD_MAGIC + struct.pack("<II", 4, 0xFFFFFFFF)
    osd = OsdFile.parse(data)
    assert osd.morphs == []
