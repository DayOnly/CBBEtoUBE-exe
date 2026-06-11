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

"""Regression: the ARMA base skin-texture refs (NAM0 male / NAM1 female /
NAM2 male-1st / NAM3 female-1st) are TXST FormIDs and MUST be master-byte
remapped by the merge / ESL-split / prune, exactly like RNAM/SNDD.

The bard MO?S bug's sibling (MEASURED 2026-06-10 on the real Combined): NAM0-3
were NOT in FORMID_SINGLE_SUBRECORD_SIGS, so the remap skipped them. A NAM ref
to a non-index-0 master broke (Kaidan kaiPrisonRags NAM0 UBE_AllRace.esp:000004
-> wrong DAc0da.esm:000004) -> the armor's exposed skin used the wrong
TextureSet. Refs to Skyrim.esm survived ONLY because it is always master
index 0 (top byte 0x00 is right without remapping), which is what masked the
bug for most armors.
"""
import struct

from src import esp, ube_patcher as up


def _nam(sig: bytes, txst: int) -> bytes:
    return esp.encode_subrecord(sig, struct.pack("<I", txst))


def _nam_of(payload: bytes, sig: bytes) -> int:
    d = next(d for s, d in esp.iter_subrecords(payload) if s == sig)
    return struct.unpack("<I", d)[0]


def test_prune_remaps_nam0_skin_txst_to_nonzero_master():
    # UBE_AllRace.esp (index 6) holds the skin TXST; UnusedMod (index 5) is
    # referenced by nothing -> pruned, shifting UBE_AllRace 6 -> 5. NAM0 must
    # follow. (Skyrim.esm at index 0 would survive even un-remapped, so this
    # deliberately uses a non-index-0 master -- the case that actually broke.)
    masters = ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm",
               "Dragonborn.esm", "UnusedMod.esp", "UBE_AllRace.esp", "TheArmor.esp"]
    payload = (esp.encode_subrecord(b"EDID", b"kaiRagsAA_UBE\x00")
               + _nam(b"NAM0", 0x06000004)    # UBE_AllRace skin TXST
               + _nam(b"NAM1", 0x06000808))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x07000812,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    e = esp.ESP(
        header=esp.TES4Header(masters=masters, author="t", description="t",
                              flags=0, version=1.7, num_records=0,
                              next_object_id=0x800),
        groups=[esp.Group(label=b"ARMA", records=[rec])])

    dropped = up.prune_unused_masters(e)
    assert "UnusedMod.esp" in dropped
    # NAM0/1 kept UBE_AllRace alive (the iter must SEE them) and so it was NOT
    # pruned, then both got remapped to its new index.
    new_idx = e.header.masters.index("UBE_AllRace.esp")
    for sig, low in ((b"NAM0", 0x004), (b"NAM1", 0x808)):
        fid = _nam_of(rec.payload, sig)
        assert (fid >> 24) & 0xFF == new_idx, (
            f"{sig.decode()} top {(fid >> 24) & 0xFF:#x} != UBE_AllRace "
            f"{new_idx:#x} -- skin TXST FormID not remapped with the master shift")
        assert fid & 0xFFFFFF == low, f"{sig.decode()} local id changed"


def test_iter_formids_sees_nam_skin_refs():
    # prune must COUNT the NAM TXST master as used, else it could drop the
    # plugin the skin textures live in (and then the ref dangles).
    payload = _nam(b"NAM0", 0x09001234) + _nam(b"NAM3", 0x0900ABCD)
    seen = set(up._iter_formids_in_payload(payload))
    assert 0x09001234 in seen and 0x0900ABCD in seen
