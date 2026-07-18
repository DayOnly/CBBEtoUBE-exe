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

The colour-variant alt-texture bug: `_iter_formids_in_payload` +
`_rewrite_formids_in_payload` only walked the standard single/array FormID
subrecords, so when `prune_unused_masters` dropped a master and renumbered the
rest, the record header + normal refs shifted but the MO?S color-TXST FormID
kept its STALE master byte -> off-by-one -> the color TextureSet resolved to the
wrong (adjacent) plugin -> every color variant rendered the base texture.
"""
import struct
from pathlib import Path

from src import esp, ube_patcher as up


def test_reconcile_runs_on_all_esl_split_pieces(tmp_path, monkeypatch):
    # The color-variant bug: reconcile_alt_texture_indices ran ONLY on the
    # primary Combined.esp, but merge_patches_split overflows records (incl. their
    # alt-texture sets) into Combined2.esp/Combined3.esp -- so 174 overflow ARMAs
    # kept stale 3D indices. The _all wrapper must visit EVERY split piece.
    stem = "CBBE_to_UBE_Combined"
    for name in (f"{stem}.esp", f"{stem}2.esp", f"{stem}3.esp"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "Unrelated.esp").write_bytes(b"")        # must NOT be visited

    visited = []
    monkeypatch.setattr(up, "reconcile_alt_texture_indices",
                        lambda p, m: (visited.append(Path(p).name), 1)[1])
    n = up.reconcile_alt_texture_indices_all(tmp_path / f"{stem}.esp",
                                             tmp_path / "meshes")
    assert set(visited) == {f"{stem}.esp", f"{stem}2.esp", f"{stem}3.esp"}, visited
    assert "Unrelated.esp" not in visited
    assert n == 3                                         # summed across pieces


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
    # color TXST + the record both live in ColorVariants.esp at index 7.
    masters = ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm",
               "Dragonborn.esm", "UnusedMod.esp", "UBE_AllRace.esp", "ColorVariants.esp"]
    payload = (esp.encode_subrecord(b"EDID", b"variant\x00")
               + esp.encode_subrecord(b"MO2S", _mo2s(b"coat", 0x07000825)))
    rec = esp.Record(sig=b"ARMO", flags=0, formid=0x07000812,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    # reference ColorVariants via a normal FormID too so it isn't itself pruned
    e = esp.ESP(
        header=esp.TES4Header(masters=masters, author="t", description="t",
                              flags=0, version=1.7, num_records=0,
                              next_object_id=0x800),
        groups=[esp.Group(label=b"ARMO", records=[rec])])

    dropped = up.prune_unused_masters(e)
    assert "UnusedMod.esp" in dropped

    new_idx = e.header.masters.index("ColorVariants.esp")
    hdr_top = (rec.formid >> 24) & 0xFF
    txst_top = (_txst_of(rec.payload) >> 24) & 0xFF
    assert hdr_top == new_idx, f"header top {hdr_top:#x} != ColorVariants {new_idx:#x}"
    assert txst_top == new_idx, (
        f"MO2S TXST top {txst_top:#x} != ColorVariants {new_idx:#x} "
        f"-- alt-texture FormID not remapped with the master shift")
    # the low 24 bits (the TXST's local id) must be untouched
    assert _txst_of(rec.payload) & 0xFFFFFF == 0x825


def test_iter_formids_sees_mo2s_txst():
    # prune must COUNT the MO?S TXST's master as used, else it could drop the
    # plugin the color textures live in.
    payload = esp.encode_subrecord(b"MO2S", _mo2s(b"coat", 0x09001234))
    assert 0x09001234 in set(up._iter_formids_in_payload(payload))


def _multi_alt_payload(entries) -> bytes:
    """entries: list of (name_bytes, txst, index) -> raw MO?S payload."""
    out = struct.pack("<I", len(entries))
    for name, txst, index in entries:
        out += struct.pack("<I", len(name)) + name + struct.pack("<II", txst, index)
    return out


def _parse_alt_payload(data: bytes):
    n = struct.unpack_from("<I", data, 0)[0]; p = 4; out = []
    for _ in range(n):
        nl = struct.unpack_from("<I", data, p)[0]; p += 4
        nm = data[p:p+nl].split(b"\x00", 1)[0].decode("latin-1"); p += nl
        tx = struct.unpack_from("<I", data, p)[0]; p += 4
        ix = struct.unpack_from("<I", data, p)[0]; p += 4
        out.append((nm, tx, ix))
    return out


def test_reindex_alt_texture_case_insensitive_keeps_hood():
    # The recolor authored the hood entry as 'hood' but the converted NIF shape is
    # 'Hood'. The old case-SENSITIVE match dropped it (hood rendered base color);
    # the fix matches case-insensitively and reconciles its index. The correctly-
    # cased siblings must still reconcile to their new (BaseShape-shifted) indices.
    # Converted shape order: BaseShape=0, Hood=1, Inner Ribbon=7, Skirt=13.
    shape_index = {"BaseShape": 0, "Hood": 1, "Inner Ribbon": 7, "Skirt": 13}
    payload = _multi_alt_payload([
        (b"hood", 0x0A000001, 0),          # lowercase vs 'Hood' -> was dropped
        (b"Inner Ribbon", 0x0A000002, 6),  # source index 6 -> should become 7
        (b"Skirt", 0x0A000003, 13),        # unchanged
    ])
    out = up._reindex_alt_texture_payload(payload, shape_index)
    got = {nm: (tx, ix) for nm, tx, ix in _parse_alt_payload(out)}
    assert "hood" in got, "case-only mismatch dropped the hood recolor"
    assert got["hood"] == (0x0A000001, 1), "hood index not reconciled to 'Hood' (1)"
    assert got["Inner Ribbon"] == (0x0A000002, 7)
    assert got["Skirt"] == (0x0A000003, 13)


def test_reindex_alt_texture_drops_truly_missing_shape():
    # A shape genuinely absent from the converted NIF is still dropped.
    shape_index = {"BaseShape": 0, "Hood": 1}
    payload = _multi_alt_payload([(b"GoneShape", 0x0A000009, 4)])
    out = up._reindex_alt_texture_payload(payload, shape_index)
    assert _parse_alt_payload(out) == []
