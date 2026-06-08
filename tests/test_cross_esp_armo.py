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

"""Guard for cross-ESP ARMO coverage (#176): an ARMO in an ADD-ON plugin whose
armature references an ARMA in a BASE (master) plugin must still get a UBE
armature minted + linked, as long as that ARMA's mesh was converted. This is
the Twilight 'black' cloth-cloak invisibility (base-color cloak ARMOs live in
the alt-textures add-on but reference the base plugin's cloak ARMA)."""
import struct
from pathlib import Path
from src import esp
from src.ube_patcher import generate_ube_patch
from src.esp import encode_subrecord, encode_zstring

DEFAULT_RACE = 0x00000019            # Skyrim.esm DefaultRace
SLOT_46 = 1 << (46 - 30)             # cloak/back biped slot
BASE_ARMA_FID = 0x00000811           # cloak ARMA's own FormID in the base plugin


def _base_plugin(tmp: Path) -> Path:
    """Base plugin: defines the cloak ARMA (slot 46, DefaultRace, MOD3 mesh)."""
    arma_payload = (
        encode_subrecord(b"EDID", encode_zstring("ClkA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", SLOT_46, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
        + encode_subrecord(b"MOD3", encode_zstring("Twi\\Cloak_1.nif"))
    )
    arma = esp.Record(sig=b"ARMA", flags=0, formid=BASE_ARMA_FID,
                      timestamp_vc=0, version_unk=0x002C, payload=arma_payload)
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=[arma])])
    p = tmp / "TwiBase.esp"
    e.save(p)
    return p


def _addon_plugin(tmp: Path) -> Path:
    """Add-on plugin: has its OWN ARMA (like the real Twilight addon's RED
    cloak ARMAs, so generate_ube_patch doesn't bail on a missing ARMA group),
    plus an ARMO whose armature references the BASE plugin's ARMA (master
    byte 1 = TwiBase) -- the cross-ESP case."""
    own_arma = esp.Record(
        sig=b"ARMA", flags=0, formid=0x01000A00, timestamp_vc=0,
        version_unk=0x002C,
        payload=(encode_subrecord(b"EDID", encode_zstring("AddonOwn"))
                 + encode_subrecord(b"BOD2", struct.pack("<II", SLOT_46, 4))
                 + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
                 + encode_subrecord(b"MOD3", encode_zstring("Twi\\Other_1.nif"))))
    arma_ref = (1 << 24) | BASE_ARMA_FID   # master-byte 1 -> TwiBase.esp
    armo_payload = (
        encode_subrecord(b"EDID", encode_zstring("ClothCloak"))
        + encode_subrecord(b"MODL", struct.pack("<I", arma_ref))   # armature
        + encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0))  # value+weight
    )
    armo = esp.Record(sig=b"ARMO", flags=0, formid=0x01000900,
                      timestamp_vc=0, version_unk=0x002C, payload=armo_payload)
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm", "TwiBase.esp"]),
                groups=[esp.Group(label=b"ARMA", records=[own_arma]),
                        esp.Group(label=b"ARMO", records=[armo])])
    p = tmp / "TwiAddon.esp"
    e.save(p)
    return p


def _armo_armature_fids(payload: bytes) -> list[int]:
    return [struct.unpack("<I", d)[0]
            for sig, d in esp.iter_subrecords(payload)
            if sig == b"MODL" and len(d) == 4]


def test_cross_esp_armo_gets_ube_armature(tmp_path):
    _base_plugin(tmp_path)
    addon = _addon_plugin(tmp_path)
    out = tmp_path / "TwiAddon UBE patch.esp"
    # The cloak mesh WAS converted -> _converted_nif_exists True for it.
    stats = generate_ube_patch(
        addon, out,
        master_data_dirs=[tmp_path],
        converted_rel_paths={"twi/cloak_1.nif"},
    )
    assert out.is_file(), stats
    patch = esp.ESP.load(out)
    arma_grp = patch.group(b"ARMA")
    armo_grp = patch.group(b"ARMO")

    # 1) A UBE ARMA was minted for the cross-ESP base cloak ARMA.
    assert arma_grp is not None and len(arma_grp.records) >= 1, "no minted ARMA"
    minted_edids = []
    minted_fids = set()
    for r in arma_grp.records:
        for sig, d in esp.iter_subrecords(r.payload):
            if sig == b"EDID":
                minted_edids.append(d.rstrip(b"\x00").decode())
        minted_fids.add(r.formid)
    assert "ClkA_UBE" in minted_edids, minted_edids

    # 2) The add-on's ARMO override exists and LINKS the minted UBE armature.
    assert armo_grp is not None and len(armo_grp.records) >= 1, "ARMO not covered"
    linked = any(
        set(_armo_armature_fids(r.payload)) & minted_fids
        for r in armo_grp.records)
    assert linked, "cross-ESP ARMO does not link the minted UBE armature"


def test_cross_esp_skips_when_mesh_not_converted(tmp_path):
    # Same setup, but the mesh is NOT in the converted set -> must NOT mint/cover
    # (pointing an ARMA at a non-existent !UBE NIF would CTD).
    _base_plugin(tmp_path)
    addon = _addon_plugin(tmp_path)
    out = tmp_path / "TwiAddon UBE patch.esp"
    stats = generate_ube_patch(
        addon, out,
        master_data_dirs=[tmp_path],
        converted_rel_paths=set(),     # nothing converted
    )
    patch = esp.ESP.load(out)
    arma_grp = patch.group(b"ARMA")
    # no ClkA_UBE minted
    edids = [d.rstrip(b"\x00").decode()
             for r in (arma_grp.records if arma_grp else [])
             for sig, d in esp.iter_subrecords(r.payload) if sig == b"EDID"]
    assert "ClkA_UBE" not in edids, edids
