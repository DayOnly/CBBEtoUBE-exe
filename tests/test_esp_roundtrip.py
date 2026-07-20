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

"""Correctness gate for the binary plugin WRITER.

This file previously collected ZERO tests: its logic sat at module level instead
of in `def test_*`, and it read fixtures from a `samples/` tree that is
gitignored and absent, so every path hit `continue` and printed SKIP. It looked
like coverage in any file listing while never executing -- and it guards the one
component whose output, when malformed, makes the game refuse to load. Fixtures
are now built in-process, so it runs in a clean checkout.

Round-trip alone is deliberately NOT the whole test. A reader/writer bug that is
symmetric in both directions -- a size field written and read at the same wrong
offset -- survives load->save->load unchanged and still ships a plugin the
engine rejects. So the byte-level tests below assert the on-disk layout against
the FORMAT, independently of our own parser:

  * a record's size field equals its real payload length;
  * a GRUP's size field spans its own 24-byte header plus every record;
  * the TES4 HEDR record count matches what was actually written.

Those are the fields that desync every following read when wrong.
"""
import os
import struct
import tempfile
from pathlib import Path

import pytest

from src import esp

GRUP_HEADER_SIZE = 24
REC_HEADER_SIZE = 24


def _armo(formid, edid=b"TestArmor"):
    payload = esp.encode_subrecord(b"EDID", edid + b"\x00")
    return esp.Record(sig=b"ARMO", flags=0, formid=formid,
                      timestamp_vc=0, version_unk=0, payload=payload)


def _plugin(masters=("Skyrim.esm",), n_records=3):
    recs = [_armo(0x01000800 + i, f"TestArmor{i}".encode())
            for i in range(n_records)]
    return esp.ESP(header=esp.TES4Header(masters=list(masters)),
                   groups=[esp.Group(label=b"ARMO", records=recs)])


# ---- round-trip: content survives load -> save -> load --------------------

def test_masters_groups_and_records_survive_roundtrip(tmp_path):
    src = _plugin(masters=("Skyrim.esm", "Update.esm", "Dawnguard.esm"),
                  n_records=4)
    p = tmp_path / "rt.esp"
    src.save(p)
    dst = esp.ESP.load(p)

    assert dst.header.masters == src.header.masters
    assert [g.label for g in dst.groups] == [g.label for g in src.groups]
    assert len(dst.groups[0].records) == 4
    for a, b in zip(src.groups[0].records, dst.groups[0].records):
        assert (a.sig, a.formid, a.flags) == (b.sig, b.formid, b.flags)
        assert list(esp.iter_subrecords(a.payload)) == \
               list(esp.iter_subrecords(b.payload))


def test_roundtrip_is_byte_stable_on_a_second_pass(tmp_path):
    """save -> load -> save must be byte-identical. A drifting field (a bad
    recount, a padded string) surfaces here even when content compares equal."""
    src = _plugin(n_records=3)
    p1, p2 = tmp_path / "a.esp", tmp_path / "b.esp"
    src.save(p1)
    esp.ESP.load(p1).save(p2)
    assert p1.read_bytes() == p2.read_bytes()


# ---- byte-level: the writer agrees with the FORMAT, not just with itself --

def test_record_size_field_matches_real_payload_length(tmp_path):
    """Every subsequent read is offset by this field. If it disagrees with the
    bytes actually written, the rest of the file is garbage."""
    p = tmp_path / "sz.esp"
    _plugin(n_records=2).save(p)
    data = p.read_bytes()

    off, seen = 0, 0
    while off < len(data):
        sig = data[off:off + 4]
        if sig == b"GRUP":
            off += GRUP_HEADER_SIZE
            continue
        size = struct.unpack_from("<I", data, off + 4)[0]
        body = data[off + REC_HEADER_SIZE: off + REC_HEADER_SIZE + size]
        assert len(body) == size, (
            f"{sig!r} declares {size} bytes but only {len(body)} remain")
        seen += 1
        off += REC_HEADER_SIZE + size
    assert seen >= 3, "expected TES4 + 2 ARMO records"


def test_grup_size_counts_its_own_header_and_all_records(tmp_path):
    """A GRUP's size includes its 24-byte header. Off-by-24 here is the classic
    way to make a plugin load as truncated."""
    p = tmp_path / "grup.esp"
    _plugin(n_records=3).save(p)
    data = p.read_bytes()

    gi = data.index(b"GRUP")
    size = struct.unpack_from("<I", data, gi + 4)[0]
    assert size == len(data) - gi, (
        "GRUP size must span its header plus every record, to end of file")

    body = data[gi + GRUP_HEADER_SIZE: gi + size]
    off, n = 0, 0
    while off < len(body):
        rsize = struct.unpack_from("<I", body, off + 4)[0]
        off += REC_HEADER_SIZE + rsize
        n += 1
    assert off == len(body), "records must exactly fill the declared GRUP size"
    assert n == 3


def test_hedr_record_count_matches_records_written(tmp_path):
    p = tmp_path / "hedr.esp"
    _plugin(n_records=5).save(p)
    reloaded = esp.ESP.load(p)
    assert reloaded.header.num_records == 5
    assert sum(len(g.records) for g in reloaded.groups) == 5


# ---- subrecord encoding --------------------------------------------------

def test_subrecord_encode_decode_roundtrip():
    pairs = [(b"EDID", b"Name\x00"),
             (b"MODL", struct.pack("<I", 0x01000ABC)),
             (b"DATA", b"\x00" * 8)]
    blob = b"".join(esp.encode_subrecord(s, d) for s, d in pairs)
    assert [(s, d) for s, d in esp.iter_subrecords(blob)] == pairs


def test_oversized_subrecord_is_refused_not_silently_truncated():
    """>0xFFFF will not fit the 16-bit length field. Refusing loudly is correct;
    writing a truncated length would corrupt the plugin silently."""
    with pytest.raises(Exception):
        esp.encode_subrecord(b"DATA", b"\x00" * 0x10000)


# ---- real-data round-trip (opt-in, skipped cleanly when unavailable) ------

def _real_plugin_path():
    env = os.environ.get("CBBE2UBE_TEST_ESP")
    return Path(env) if env and Path(env).is_file() else None


@pytest.mark.skipif(_real_plugin_path() is None,
                    reason="set CBBE2UBE_TEST_ESP to a real plugin to run")
def test_real_plugin_roundtrips_without_content_drift():
    """Synthetic fixtures cannot cover every quirk of an authored plugin. Point
    CBBE2UBE_TEST_ESP at a real one to exercise the writer against it."""
    path = _real_plugin_path()
    src = esp.ESP.load(path)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "rt.esp"
        src.save(out)
        dst = esp.ESP.load(out)
        assert dst.header.masters == src.header.masters
        assert {g.label for g in dst.groups} == {g.label for g in src.groups}
        for sg, dg in zip(src.groups, dst.groups):
            assert len(sg.records) == len(dg.records), sg.label
            for a, b in zip(sg.records, dg.records):
                assert (a.sig, a.formid) == (b.sig, b.formid)
                assert list(esp.iter_subrecords(a.payload)) == \
                       list(esp.iter_subrecords(b.payload))
