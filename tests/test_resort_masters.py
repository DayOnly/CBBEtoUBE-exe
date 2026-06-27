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

"""resort_masters: repair a Combined whose master list ended up mis-sorted (a
master-tier .esm/.esl after a regular .esp = load-order/FormID-resolution crash)
WITHOUT a re-merge. Re-sorts in place and remaps every FormID's master byte; the
master COUNT is unchanged so own-record FormIDs (top byte == len(masters)) are
left alone."""
import struct

from src import esp, ube_patcher
from src.esp import encode_subrecord, encode_zstring, iter_subrecords


def _armo(formid, kwda_fids):
    kwda = b"".join(struct.pack("<I", f) for f in kwda_fids)
    payload = (encode_subrecord(b"EDID", encode_zstring("T"))
               + encode_subrecord(b"KSIZ", struct.pack("<I", len(kwda_fids)))
               + encode_subrecord(b"KWDA", kwda))
    return esp.Record(sig=b"ARMO", flags=0, formid=formid,
                      timestamp_vc=0, version_unk=0x2C, payload=payload)


def _top(fid):
    return (fid >> 24) & 0xFF


def test_resort_master_tier_first_and_remaps_formids():
    # mis-ordered: AMaster.esm (master-tier) listed AFTER ZReg.esp (regular).
    masters = ["Skyrim.esm", "ZReg.esp", "AMaster.esm"]   # idx 0,1,2; own byte = 3
    override = _armo((2 << 24) | 0x800, [(1 << 24) | 0x123])  # refs AMaster + ZReg(kwda)
    own = _armo((3 << 24) | 0x900, [(3 << 24) | 0x901])       # own record (top byte 3)
    e = esp.ESP(header=esp.TES4Header(masters=masters, next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO", records=[override, own])])

    assert ube_patcher.resort_masters(e, master_data_dirs=None) is True
    # vanilla first, then master-tier, then regular
    assert e.header.masters == ["Skyrim.esm", "AMaster.esm", "ZReg.esp"]
    # override's own formid: AMaster moved 2 -> 1
    assert _top(e.groups[0].records[0].formid) == 1
    # override's KWDA ref: ZReg moved 1 -> 2
    kwda = next(d for s, d in iter_subrecords(e.groups[0].records[0].payload)
                if s == b"KWDA")
    assert _top(struct.unpack("<I", kwda[:4])[0]) == 2
    # own record (top byte 3) is UNCHANGED (count unchanged)
    assert _top(e.groups[0].records[1].formid) == 3


def test_resort_noop_when_already_ordered():
    masters = ["Skyrim.esm", "AMaster.esm", "ZReg.esp"]   # already tier-sorted
    e = esp.ESP(header=esp.TES4Header(masters=masters, next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO",
                                  records=[_armo((1 << 24) | 0x800, [])])])
    assert ube_patcher.resort_masters(e, master_data_dirs=None) is False


def test_resort_roundtrips_and_clears_master_ordering_warning(tmp_path):
    masters = ["Skyrim.esm", "ZReg.esp", "AMaster.esm"]
    e = esp.ESP(header=esp.TES4Header(masters=masters, next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO",
                                  records=[_armo((2 << 24) | 0x800,
                                                 [(1 << 24) | 0x5])])])
    ube_patcher.resort_masters(e, master_data_dirs=None)
    p = tmp_path / "t.esp"
    e.save(p)
    reloaded = esp.ESP.load(p)
    assert reloaded.header.masters == ["Skyrim.esm", "AMaster.esm", "ZReg.esp"]
    w = ube_patcher.validate_patch(p, check_nifs=False)
    assert not any(x.startswith("master-ordering") for x in w), w


def test_resort_masters_all_globs_and_heals_all_pieces(tmp_path):
    def _mk(name):
        e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm", "ZReg.esp",
                                                   "AMaster.esm"],
                                          next_object_id=0xFFFFFF),
                    groups=[esp.Group(label=b"ARMO",
                                      records=[_armo((2 << 24) | 0x800,
                                                     [(1 << 24) | 0x5])])])
        e.save(tmp_path / name)
    _mk("Combined.esp")
    _mk("Combined2.esp")    # split piece -> globbed by Combined*.esp
    n = ube_patcher.resort_masters_all(tmp_path / "Combined.esp",
                                       master_data_dirs=None)
    assert n == 2
    for name in ("Combined.esp", "Combined2.esp"):
        ms = esp.ESP.load(tmp_path / name).header.masters
        assert ms == ["Skyrim.esm", "AMaster.esm", "ZReg.esp"]
    # idempotent: a correctly-ordered family is a no-op
    assert ube_patcher.resort_masters_all(tmp_path / "Combined.esp",
                                          master_data_dirs=None) == 0


def test_resort_masters_all_clears_stale_tier_cache(tmp_path):
    # A stale ESM-tier verdict cached during an earlier phase must not survive into
    # the self-heal (it would mis-classify a .esp ESM-flag and re-sort it wrong).
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm", "ZReg.esp",
                                               "AMaster.esm"],
                                      next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO",
                                  records=[_armo((2 << 24) | 0x800, [])])])
    e.save(tmp_path / "Combined.esp")
    ube_patcher._ESM_TIER_CACHE["somestale.esp"] = False   # poison
    ube_patcher.resort_masters_all(tmp_path / "Combined.esp", master_data_dirs=None)
    assert "somestale.esp" not in ube_patcher._ESM_TIER_CACHE   # cleared fresh
