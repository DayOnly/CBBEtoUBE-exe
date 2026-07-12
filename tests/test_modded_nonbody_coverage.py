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

"""Mod-defined non-body UBE coverage (the guard-helmet class).

An overhaul re-armatures a vanilla helmet with its OWN ArmorAddon listing only
vanilla races -> invisible on UBE actors, and runtime race dispatch never
touches a mod-defined ARMA. This pass must mint a UBE-PRIMARY ARMA (same mesh) for such an
item and emit a SkyPatcher line adding it to the target ARMO at runtime — with a
TINY master list (mint ESP masters = vanilla + UBE_AllRace, never the mod).
"""
import struct
from pathlib import Path

from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher


def _save(path, masters, groups, flags=0):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=flags),
        groups=groups).save(path)
    return path


def _arma(formid, edid, primary_race_fid, mesh, slots_bit, extra_races=()):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots_bit, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary_race_fid))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    p += encode_subrecord(b"MOD3", encode_zstring(mesh))
    for r in extra_races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    p += encode_subrecord(b"SNDD", struct.pack("<I", 0x00012345))  # must be dropped
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, edid, arma_fid, primary_race_fid, slots_bit, flags=0):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots_bit, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary_race_fid))
    p += encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
    p += encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))
    p += encode_subrecord(b"DNAM", struct.pack("<I", 0))
    return Record(sig=b"ARMO", flags=flags, formid=formid, payload=p)


def test_modded_nonbody_mints_ube_primary_and_skypatcher_line(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    # Fake Skyrim.esm (master) + UBE_AllRace.esp present by name only.
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)

    # Mod.esp: a HEAD-slot (non-deforming) helmet ARMO -> ARMA with DefaultRace
    # primary + only vanilla races (NO UBE). Own records: top byte = 1.
    own = 1 << 24
    arma_fid = own | 0x800
    armo_fid = own | 0x801
    DEFAULT = 0x00000019  # Skyrim.esm DefaultRace
    HEAD = 1 << 0
    mod = _save(
        tmp_path / "Mod.esp", ["Skyrim.esm"],
        [Group(label=b"ARMA", records=[
            _arma(arma_fid, "GuardHelmAA", DEFAULT, "armor/guard/helmet_0.nif",
                  HEAD, extra_races=(DEFAULT,))]),
         Group(label=b"ARMO", records=[
            _armo(armo_fid, "GuardHelm", arma_fid, DEFAULT, HEAD)])])

    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path])

    assert stats["minted_armas"] == 1, stats
    assert stats["armo_targets"] == 1, stats
    # Mint ESP must NOT master the mod — only vanilla DLC + UBE_AllRace.
    merged = ESP.load(out)
    assert "Mod.esp" not in merged.header.masters, merged.header.masters
    assert any(m.lower() == "ube_allrace.esp" for m in merged.header.masters)

    # The minted ARMA is UBE-PRIMARY (RNAM = UBE Breton), same mesh, no SNDD.
    arma = next(g for g in merged.groups if g.label == b"ARMA").records[0]
    ube_idx = next(i for i, m in enumerate(merged.header.masters)
                   if m.lower() == "ube_allrace.esp")
    rnam = None
    mesh = None
    has_sndd = False
    nraces = 0
    for s, d in iter_subrecords(arma.payload):
        if s == b"RNAM":
            rnam = struct.unpack("<I", d)[0]
        elif s == b"MOD3":
            mesh = d.split(b"\x00")[0].decode("latin1")
        elif s == b"MODL" and len(d) == 4:
            nraces += 1
        elif s == b"SNDD":
            has_sndd = True
    assert rnam == ((ube_idx << 24) | ube_patcher.UBE_PRIMARY_BRETON_FID_24), \
        f"minted ARMA must be UBE-primary, got RNAM={rnam:08X}"
    assert mesh == "armor/guard/helmet_0.nif", f"mesh changed: {mesh}"
    assert not has_sndd, "SNDD (mod-master ref) must be stripped"
    assert nraces == len(ube_patcher.UBE_RACE_FIDS_24)

    # SkyPatcher line targets the mod ARMO and adds the minted ARMA.
    lines = [l for l in stats["ini_lines"] if l.startswith("filterByArmors")]
    assert len(lines) == 1, lines
    assert "Mod.esp|000801" in lines[0], lines[0]
    assert "armorAddonsToAdd=UBE_ModNonBody_Coverage.esp|000800" in lines[0], lines[0]
    print("  test_modded_nonbody_mints_ube_primary_and_skypatcher_line OK")


def test_modded_nonbody_skips_already_covered_and_deforming(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    own = 1 << 24
    DEFAULT = 0x00000019
    BODY = 1 << 2   # deforming slot -> must be skipped
    HEAD = 1 << 0
    # A BODY-slot item (deforming) must be skipped; an already-UBE item too.
    body_arma = own | 0x800
    body_armo = own | 0x801
    mod = _save(
        tmp_path / "Mod.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
        [Group(label=b"ARMA", records=[
            _arma(body_arma, "BodyAA", DEFAULT, "armor/body_0.nif", BODY)]),
         Group(label=b"ARMO", records=[
            _armo(body_armo, "Body", body_arma, DEFAULT, BODY)])])
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path])
    assert stats["minted_armas"] == 0, "body-slot item must be skipped"
    assert stats["armo_targets"] == 0
    print("  test_modded_nonbody_skips_already_covered_and_deforming OK")


def test_modded_nonbody_redirects_converted_mesh_to_ube(tmp_path):
    """A non-body item whose OWN mesh WAS converted must point at the !UBE\\ mesh,
    NOT source (the FVO-pants class: minted through the non-body pass but its mesh
    was actually converted). A genuine non-body item (helmet, not in
    converted_rel_paths) still keeps its source mesh. #mnb-converted-redirect"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    own = 1 << 24
    DEFAULT = 0x00000019
    HEAD = 1 << 0
    conv_arma, conv_armo = own | 0x800, own | 0x801
    src_arma, src_armo = own | 0x802, own | 0x803
    mod = _save(
        tmp_path / "Mod.esp", ["Skyrim.esm"],
        [Group(label=b"ARMA", records=[
            _arma(conv_arma, "ConvAA", DEFAULT, "armor/fvo/pants_1.nif", HEAD,
                  extra_races=(DEFAULT,)),
            _arma(src_arma, "SrcAA", DEFAULT, "armor/guard/helmet_1.nif", HEAD,
                  extra_races=(DEFAULT,))]),
         Group(label=b"ARMO", records=[
            _armo(conv_armo, "ConvItem", conv_arma, DEFAULT, HEAD),
            _armo(src_armo, "SrcItem", src_arma, DEFAULT, HEAD)])])
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        converted_rel_paths={"armor/fvo/pants_1.nif"},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path])
    assert stats["minted_armas"] == 2, stats
    merged = ESP.load(out)
    meshes = []
    for arma in next(g for g in merged.groups if g.label == b"ARMA").records:
        for s, d in iter_subrecords(arma.payload):
            if s == b"MOD3":
                meshes.append(d.split(b"\x00")[0].decode("latin1"))
    conv_mesh = next(m for m in meshes if "pants_1" in m)
    src_mesh = next(m for m in meshes if "helmet_1" in m)
    assert conv_mesh.lower().startswith("!ube"), \
        f"converted non-body mesh must redirect to !UBE, got {conv_mesh!r}"
    assert not src_mesh.lower().startswith("!ube"), \
        f"non-converted mesh must keep source, got {src_mesh!r}"
    print("  test_modded_nonbody_redirects_converted_mesh_to_ube OK")


test_modded_nonbody_mints_ube_primary_and_skypatcher_line(
    Path(__file__).resolve().parent / "_tmp_modnonbody")
test_modded_nonbody_skips_already_covered_and_deforming(
    Path(__file__).resolve().parent / "_tmp_modnonbody2")
test_modded_nonbody_redirects_converted_mesh_to_ube(
    Path(__file__).resolve().parent / "_tmp_modnonbody3")
