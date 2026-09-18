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

"""ESP.load must FAIL LOUD, not hang, on a malformed top-level GRUP whose
declared size is < the 24-byte header (a zeroed/truncated size field makes
Group.parse return the same offset -> the load loop would otherwise spin
forever, hanging the whole batch). #esp-nonadvancing-grup
"""
import struct

import pytest

from src import esp
from src.esp import encode_subrecord, encode_zstring


def _one_group_esp(tmp_path):
    payload = encode_subrecord(b"EDID", encode_zstring("X"))
    rec = esp.Record(sig=b"ARMO", flags=0, formid=(1 << 24) | 0x800,
                     timestamp_vc=0, version_unk=0x2C, payload=payload)
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"],
                                      next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO", records=[rec])])
    p = tmp_path / "bad.esp"
    e.save(p)
    return p


def test_zero_size_grup_raises_not_hangs(tmp_path):
    p = _one_group_esp(tmp_path)
    data = bytearray(p.read_bytes())
    gi = data.find(b"GRUP")
    assert gi != -1
    struct.pack_into("<I", data, gi + 4, 0)      # GRUP size = 0 -> non-advancing
    p.write_bytes(data)
    with pytest.raises(ValueError):
        esp.ESP.load(p)                          # must raise, must not hang


def test_valid_grup_still_loads(tmp_path):
    p = _one_group_esp(tmp_path)
    loaded = esp.ESP.load(p)
    assert len(loaded.groups) == 1
    assert loaded.groups[0].records[0].sig == b"ARMO"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
