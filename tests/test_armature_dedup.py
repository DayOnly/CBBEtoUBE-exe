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

"""Dedup redundant converter-minted UBE armature refs in an ARMO.

The bug: a body-armor ARMO ends up with TWO of the patch's own UBE ARMAs that
share the same race + meshes; a UBE wearer resolves both, so the body-swap mesh
renders twice (doubled / blown-out / double-morphed = "doesn't fit/conform")."""
import struct

from src import esp, ube_patcher
from src.esp import encode_subrecord, encode_zstring


def _arma(fid, race, m, f, extra=False):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{fid:X}"))
    p += encode_subrecord(b"RNAM", struct.pack("<I", race))
    p += encode_subrecord(b"MOD2", encode_zstring(m))
    p += encode_subrecord(b"MOD3", encode_zstring(f))
    if extra:
        p += encode_subrecord(b"DNAM", b"\x00" * 12)   # extra subrecord -> "more complete"
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, version_unk=0x2C, payload=p)


def _armo(fid, arma_refs):
    p = encode_subrecord(b"EDID", encode_zstring(f"AO{fid:X}"))
    p += encode_subrecord(b"FULL", encode_zstring("X"))
    for a in arma_refs:
        p += encode_subrecord(b"MODL", struct.pack("<I", a))
    return esp.Record(sig=b"ARMO", flags=0, formid=fid, version_unk=0x2C, payload=p)


def _refs(rec):
    return [struct.unpack("<I", d)[0]
            for sig, d in esp.iter_subrecords(rec.payload) if sig == b"MODL"]


def test_dedup_armo_armature_refs(tmp_path):
    OWN_A = 0x01000800    # most complete (extra subrecord) -> the keeper
    OWN_B = 0x01000801    # same race+meshes as A -> redundant
    VANILLA = 0x00012345  # master ARMA -> must never be touched

    armas = [_arma(OWN_A, 0x19, "m\\x_1.nif", "f\\x_1.nif", extra=True),
             _arma(OWN_B, 0x19, "m\\x_1.nif", "f\\x_1.nif")]
    # museum case: [vanilla, A, B]; same-fid-twice case: [A, A]
    armo1 = _armo(0x01000900, [VANILLA, OWN_A, OWN_B])
    armo2 = _armo(0x01000901, [OWN_A, OWN_A])
    p = tmp_path / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=armas),
                    esp.Group(label=b"ARMO", records=[armo1, armo2])]).save(p)

    removed = ube_patcher.dedup_armo_armature_refs(p)
    assert removed == 2, removed   # OWN_B from armo1 + one dup OWN_A from armo2

    grp = esp.ESP.load(p).group(b"ARMO")
    r1 = next(r for r in grp.records if r.formid == 0x01000900)
    r2 = next(r for r in grp.records if r.formid == 0x01000901)
    # vanilla untouched, A (keeper) kept, B dropped:
    assert _refs(r1) == [VANILLA, OWN_A], _refs(r1)
    # same-fid-twice collapses to ONE survivor -- NOT zero (would be invisible):
    assert _refs(r2) == [OWN_A], _refs(r2)


def test_dedup_leaves_distinct_armatures_alone(tmp_path):
    # Two own-ARMAs with DIFFERENT meshes are NOT duplicates -> both kept.
    A = 0x01000800
    B = 0x01000801
    armas = [_arma(A, 0x19, "m\\a_1.nif", "f\\a_1.nif"),
             _arma(B, 0x19, "m\\b_1.nif", "f\\b_1.nif")]   # different meshes
    armo = _armo(0x01000900, [A, B])
    p = tmp_path / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=armas),
                    esp.Group(label=b"ARMO", records=[armo])]).save(p)
    assert ube_patcher.dedup_armo_armature_refs(p) == 0
    grp = esp.ESP.load(p).group(b"ARMO")
    assert _refs(grp.records[0]) == [A, B]
