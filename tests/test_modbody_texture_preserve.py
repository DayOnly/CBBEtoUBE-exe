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

"""SkyPatcher body coverage with preserve_textures=True.

A recolor variant carries an alt-texture set (MO?S) / skin swap (NAM0-3) that
points at a mod-OWNED TXST. The full-SkyPatcher body path must keep those on the
minted UBE armature (remapped into the coverage ESP's master space, declaring the
mod as a master) so the recolor keeps its look. The default (strip) path must
still drop them and NOT master the mod (minimal master list)."""
import struct
from pathlib import Path

from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher

DEFAULT = 0x00000019   # Skyrim.esm DefaultRace (top byte 0)
BODY = 1 << 2          # deforming body slot (slot 32)
OWN = 1 << 24          # Mod.esp own top byte (masters=[Skyrim.esm])
TXST_MOS = OWN | 0x000ABC   # mod-owned alt-texture TXST
TXST_NAM = OWN | 0x000DEF   # mod-owned skin TXST
MESH = "armor/recolor/body_0.nif"


def _save(path, masters, groups, flags=0):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=flags),
        groups=groups).save(path)
    return path


def _mos(sig, name, txst_fid, index):
    data = struct.pack("<I", 1)
    data += struct.pack("<I", len(name)) + name + struct.pack("<II", txst_fid, index)
    return encode_subrecord(sig, data)


def _arma(formid, edid, mesh, slots_bit):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots_bit, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    p += encode_subrecord(b"MOD3", encode_zstring(mesh))
    p += _mos(b"MO3S", b"Body\x00", TXST_MOS, 0)          # recolor alt-texture
    p += encode_subrecord(b"NAM0", struct.pack("<I", TXST_NAM))  # skin swap
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"SNDD", struct.pack("<I", OWN | 0x123))  # must drop
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, edid, arma_fid, slots_bit):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots_bit, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
    p += encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))
    p += encode_subrecord(b"DNAM", struct.pack("<I", 0))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=p)


def _build(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    arma_fid = OWN | 0x800
    armo_fid = OWN | 0x801
    mod = _save(
        tmp_path / "Mod.esp", ["Skyrim.esm"],
        [Group(label=b"ARMA", records=[_arma(arma_fid, "RecolorAA", MESH, BODY)]),
         Group(label=b"ARMO", records=[_armo(armo_fid, "Recolor", arma_fid, BODY)])])
    return sky, ube, mod


def _minted_txsts(arma_payload):
    """(sig -> resolved 24-bit low, source-space top byte) for MO?S + NAM0-3."""
    mos, nam = [], []
    for s, d in iter_subrecords(arma_payload):
        if s in (b"MO2S", b"MO3S", b"MO4S", b"MO5S"):
            ube_patcher._remap_alt_texture_payload(d, lambda f: (mos.append(f) or f))
        elif s in (b"NAM0", b"NAM1", b"NAM2", b"NAM3") and len(d) == 4:
            nam.append(struct.unpack("<I", d)[0])
    return mos, nam


def test_preserve_textures_keeps_and_remaps_mos_and_nam(tmp_path):
    sky, ube, mod = _build(tmp_path)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = ube_patcher.generate_modded_body_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        converted_rel_paths={MESH}, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True)

    assert stats["minted_armas"] == 1, stats
    assert stats["armo_targets"] == 1, stats
    assert stats["textures_preserved"] == 1, stats
    assert stats["texture_fallbacks"] == 0, stats

    merged = ESP.load(out)
    masters = merged.header.masters
    # preserve declares the mod as a master (its TXSTs are mod-owned).
    assert any(m.lower() == "mod.esp" for m in masters), masters
    mod_idx = next(i for i, m in enumerate(masters) if m.lower() == "mod.esp")

    arma = next(g for g in merged.groups if g.label == b"ARMA").records[0]
    mos, nam = _minted_txsts(arma.payload)
    assert len(mos) == 1 and len(nam) == 1, (mos, nam)
    # both TXSTs resolve to Mod.esp with their original low24 (correct target).
    assert (mos[0] >> 24) & 0xFF == mod_idx and mos[0] & 0xFFFFFF == 0x000ABC, \
        f"MO?S TXST mis-remapped: {mos[0]:08X}"
    assert (nam[0] >> 24) & 0xFF == mod_idx and nam[0] & 0xFFFFFF == 0x000DEF, \
        f"NAM0 TXST mis-remapped: {nam[0]:08X}"
    # SNDD (mod-master ref, not a texture) is still dropped.
    assert not any(s == b"SNDD" for s, _ in iter_subrecords(arma.payload))
    print("  test_preserve_textures_keeps_and_remaps_mos_and_nam OK")


def test_default_strips_textures_and_omits_mod_master(tmp_path):
    sky, ube, mod = _build(tmp_path)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = ube_patcher.generate_modded_body_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        converted_rel_paths={MESH}, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True)  # preserve_textures=False

    assert stats["minted_armas"] == 1, stats
    assert stats.get("textures_preserved", 0) == 0, stats
    merged = ESP.load(out)
    # default keeps a tiny master list: vanilla DLC + UBE_AllRace, never the mod.
    assert not any(m.lower() == "mod.esp" for m in merged.header.masters), \
        merged.header.masters
    arma = next(g for g in merged.groups if g.label == b"ARMA").records[0]
    mos, nam = _minted_txsts(arma.payload)
    assert not mos and not nam, "default path must strip MO?S/NAM"
    print("  test_default_strips_textures_and_omits_mod_master OK")


test_preserve_textures_keeps_and_remaps_mos_and_nam(
    Path(__file__).resolve().parent / "_tmp_mbtex")
test_default_strips_textures_and_omits_mod_master(
    Path(__file__).resolve().parent / "_tmp_mbtex2")
