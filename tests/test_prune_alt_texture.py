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

"""Regression: master-byte remap (prune / ESL-split / fold) must also remap the
TXST FormIDs embedded in alt-texture (MO?S) subrecords.

The Ballad-of-Bards / DDV-Ruby bug: `_iter_formids_in_payload` +
`_rewrite_formids_in_payload` only walked the standard single/array FormID
subrecords, so when `prune_unused_masters` dropped a master and renumbered the
rest, the record header + normal refs shifted but the MO?S color-TXST FormID
kept its STALE master byte -> off-by-one -> the color TextureSet resolved to the
wrong (adjacent) plugin -> every color variant rendered the base texture.
"""
import struct

from src import esp, ube_patcher as up


def _mo2s(name: bytes, txst: int, index: int = 0) -> bytes:
    return (struct.pack("<I", 1) + struct.pack("<I", len(name)) + name
            + struct.pack("<II", txst, index))


def _txst_of(payload: bytes) -> int:
    d = next(d for s, d in esp.iter_subrecords(payload) if s == b"MO2S")
    o = 4
    nl = struct.unpack_from("<I", d, o)[0]; o += 4 + nl
    return struct.unpack_from("<I", d, o)[0]


def test_prune_remaps_mo2s_txst_in_lockstep_with_header():
    # 8 masters; index 5 (UnusedMod) is referenced by nothing -> pruned. The
    # color TXST + the record both live in TheBard.esp at index 7.
    masters = ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm",
               "Dragonborn.esm", "UnusedMod.esp", "UBE_AllRace.esp", "TheBard.esp"]
    payload = (esp.encode_subrecord(b"EDID", b"variant\x00")
               + esp.encode_subrecord(b"MO2S", _mo2s(b"coat", 0x07000825)))
    rec = esp.Record(sig=b"ARMO", flags=0, formid=0x07000812,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    # reference TheBard via a normal FormID too so it isn't itself pruned
    e = esp.ESP(
        header=esp.TES4Header(masters=masters, author="t", description="t",
                              flags=0, version=1.7, num_records=0,
                              next_object_id=0x800),
        groups=[esp.Group(label=b"ARMO", records=[rec])])

    dropped = up.prune_unused_masters(e)
    assert "UnusedMod.esp" in dropped

    new_idx = e.header.masters.index("TheBard.esp")
    hdr_top = (rec.formid >> 24) & 0xFF
    txst_top = (_txst_of(rec.payload) >> 24) & 0xFF
    assert hdr_top == new_idx, f"header top {hdr_top:#x} != TheBard {new_idx:#x}"
    assert txst_top == new_idx, (
        f"MO2S TXST top {txst_top:#x} != TheBard {new_idx:#x} "
        f"-- alt-texture FormID not remapped with the master shift")
    # the low 24 bits (the TXST's local id) must be untouched
    assert _txst_of(rec.payload) & 0xFFFFFF == 0x825


def test_iter_formids_sees_mo2s_txst():
    # prune must COUNT the MO?S TXST's master as used, else it could drop the
    # plugin the color textures live in.
    payload = esp.encode_subrecord(b"MO2S", _mo2s(b"coat", 0x09001234))
    assert 0x09001234 in set(up._iter_formids_in_payload(payload))
