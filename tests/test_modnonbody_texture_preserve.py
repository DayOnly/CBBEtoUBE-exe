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

"""SkyPatcher NON-BODY coverage with preserve_textures=True.

Regression for the alt-texture "both variants look identical" bug: two ARMOs
share ONE mesh but differ only in an alt-texture set (MO?S) on their armature --
a plain vs a patterned variant. The non-body mint used to STRIP MO?S, so both
minted UBE armatures became byte-identical, the merge deduped them to one, and
the patterned variant lost its look. preserve_textures=True must keep MO?S on the
minted armature (remapped, mastering the mod) so the two mints stay DISTINCT.
The default (strip) path must still drop them (minimal master list)."""
import struct
from pathlib import Path

from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher

DEFAULT = 0x00000019   # Skyrim.esm DefaultRace (top byte 0)
NONBODY = 1 << 14      # non-deforming, non-hair biped slot (slot 44)
OWN = 1 << 24          # Mod.esp own top byte (masters=[Skyrim.esm])
TXST_MOS = OWN | 0x000ABC   # mod-owned alt-texture TXST (the "patterned" look)
MESH = "armor/recolor/stocking_1.nif"   # BOTH variants share this one mesh


def _save(path, masters, groups, flags=0):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=flags),
        groups=groups).save(path)
    return path


def _mos(sig, name, txst_fid, index):
    data = struct.pack("<I", 1)
    data += struct.pack("<I", len(name)) + name + struct.pack("<II", txst_fid, index)
    return encode_subrecord(sig, data)


def _arma(formid, edid, mesh, *, alttex):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", NONBODY, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    p += encode_subrecord(b"MOD3", encode_zstring(mesh))
    if alttex:
        p += _mos(b"MO3S", b"Stocking\x00", TXST_MOS, 0)   # the patterned variant
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, edid, arma_fid):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", NONBODY, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
    p += encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))
    p += encode_subrecord(b"DNAM", struct.pack("<I", 0))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=p)


def _build(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    aa_plain = OWN | 0x800
    aa_patt = OWN | 0x801
    armo_v1 = OWN | 0x810
    armo_v2 = OWN | 0x811
    mod = _save(
        tmp_path / "Mod.esp", ["Skyrim.esm"],
        [Group(label=b"ARMA", records=[
            _arma(aa_plain, "PlainAA", MESH, alttex=False),
            _arma(aa_patt, "PattAA", MESH, alttex=True)]),
         Group(label=b"ARMO", records=[
            _armo(armo_v1, "PlainVariant", aa_plain),
            _armo(armo_v2, "PatternedVariant", aa_patt)])])
    return sky, ube, mod


def _has_mos(payload):
    return any(s in (b"MO2S", b"MO3S", b"MO4S", b"MO5S")
               for s, _ in iter_subrecords(payload))


def test_nonbody_preserve_keeps_variant_distinct(tmp_path):
    sky, ube, mod = _build(tmp_path)
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        converted_rel_paths={MESH}, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True)

    assert stats["minted_armas"] == 2, stats           # one per source armature
    assert stats["textures_preserved"] == 2, stats
    assert stats["texture_fallbacks"] == 0, stats

    merged = ESP.load(out)
    masters = merged.header.masters
    assert any(m.lower() == "mod.esp" for m in masters), masters
    mod_idx = next(i for i, m in enumerate(masters) if m.lower() == "mod.esp")

    armas = next(g for g in merged.groups if g.label == b"ARMA").records
    with_mos = [r for r in armas if _has_mos(r.payload)]
    without = [r for r in armas if not _has_mos(r.payload)]
    # EXACTLY one keeps the alt-texture (patterned) and one does not (plain) --
    # so the two mints are DISTINCT and won't dedup-collapse to one look.
    assert len(with_mos) == 1 and len(without) == 1, (len(with_mos), len(without))
    assert with_mos[0].payload != without[0].payload, "variants must be distinct"
    # the kept MO?S remaps its TXST onto Mod.esp with the original low24.
    mos_fids = []
    for s, d in iter_subrecords(with_mos[0].payload):
        if s == b"MO3S":
            ube_patcher._remap_alt_texture_payload(
                d, lambda f: (mos_fids.append(f) or f))
    assert len(mos_fids) == 1
    assert (mos_fids[0] >> 24) & 0xFF == mod_idx, f"{mos_fids[0]:08X}"
    assert mos_fids[0] & 0xFFFFFF == 0x000ABC, f"{mos_fids[0]:08X}"
    print("  test_nonbody_preserve_keeps_variant_distinct OK")


def test_nonbody_default_strips_and_collapses(tmp_path):
    sky, ube, mod = _build(tmp_path)
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        converted_rel_paths={MESH}, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True)  # preserve_textures=False

    assert stats["minted_armas"] == 2, stats
    assert stats.get("textures_preserved", 0) == 0, stats
    merged = ESP.load(out)
    # default keeps a minimal master list -- never the mod.
    assert not any(m.lower() == "mod.esp" for m in merged.header.masters), \
        merged.header.masters
    armas = next(g for g in merged.groups if g.label == b"ARMA").records
    assert not any(_has_mos(r.payload) for r in armas), "default must strip MO?S"
    # both stripped mints are byte-identical except EDID -> this is exactly the
    # collapse the preserve path avoids (documents the old behavior).
    def _no_edid(p):
        return b"".join(encode_subrecord(s, d)
                        for s, d in iter_subrecords(p) if s != b"EDID")
    assert _no_edid(armas[0].payload) == _no_edid(armas[1].payload)
    print("  test_nonbody_default_strips_and_collapses OK")


test_nonbody_preserve_keeps_variant_distinct(
    Path(__file__).resolve().parent / "_tmp_mnbtex")
test_nonbody_default_strips_and_collapses(
    Path(__file__).resolve().parent / "_tmp_mnbtex2")
