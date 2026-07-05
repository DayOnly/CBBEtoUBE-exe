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

"""CBBE2UBE_BODY_SKYPATCHER (full-SkyPatcher body pivot): the per-source builder
must SUPPRESS torso body (slot 32) ARMO overrides when the flag is on (the
coverage pass owns them) while leaving non-body accessories (helmets etc.)
untouched. Flag OFF must be byte-identical to today."""
import os
import struct
from pathlib import Path

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.ube_patcher import generate_ube_patch, _BIPED_SLOT_BODY_BIT

DEFAULT = 0x00000019
BODY = _BIPED_SLOT_BODY_BIT          # slot 32
HEAD = 1 << (30 - 30)                # slot 30 (non-deforming accessory)
OWN = 1 << 24                        # source own byte (masters=[Skyrim.esm])


def _arma(fid, edid, slot, mesh):
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x002C, payload=(
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MOD3", encode_zstring(mesh))))


def _armo(fid, edid, arma_fid, slot):
    return esp.Record(sig=b"ARMO", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x002C, payload=(
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
        + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))))


def _source(tmp):
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]), groups=[
        esp.Group(label=b"ARMA", records=[
            _arma(OWN | 0x800, "BodyAA", BODY, "armor/test/body_0.nif"),
            _arma(OWN | 0x802, "HelmAA", HEAD, "armor/test/helm_0.nif")]),
        esp.Group(label=b"ARMO", records=[
            _armo(OWN | 0x801, "Body", OWN | 0x800, BODY),
            _armo(OWN | 0x803, "Helm", OWN | 0x802, HEAD)])])
    p = tmp / "Mod.esp"
    e.save(p)
    return p


def _override_slots(out):
    """Slot bits of every ARMO override record in the output patch."""
    patch = esp.ESP.load(out)
    og = patch.group(b"ARMO")
    slots = []
    for r in (og.records if og else []):
        for s, d in esp.iter_subrecords(r.payload):
            if s in (b"BOD2", b"BODT") and len(d) >= 4:
                slots.append(struct.unpack_from("<I", d, 0)[0])
                break
    return slots


def _run(tmp, flag):
    src = _source(tmp)
    if flag:
        os.environ["CBBE2UBE_BODY_SKYPATCHER"] = "1"
    else:
        os.environ.pop("CBBE2UBE_BODY_SKYPATCHER", None)
    try:
        generate_ube_patch(
            src, tmp / "out.esp", master_data_dirs=[tmp],
            converted_rel_paths={"armor/test/body_0.nif",
                                 "armor/test/helm_0.nif"})
    finally:
        os.environ.pop("CBBE2UBE_BODY_SKYPATCHER", None)
    return _override_slots(tmp / "out.esp")


def test_flag_off_keeps_body_override(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    slots = _run(tmp_path, flag=False)
    assert any(s & BODY for s in slots), f"body override missing: {slots}"
    assert any(s & HEAD and not (s & BODY) for s in slots), f"helm missing: {slots}"
    print("  test_flag_off_keeps_body_override OK")


def test_flag_on_suppresses_body_keeps_nonbody(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    slots = _run(tmp_path, flag=True)
    assert not any(s & BODY for s in slots), \
        f"body override must be suppressed under the flag: {slots}"
    # the non-body helmet override is untouched.
    assert any(s & HEAD and not (s & BODY) for s in slots), \
        f"non-body helm override must remain: {slots}"
    print("  test_flag_on_suppresses_body_keeps_nonbody OK")


SLOT49 = 1 << (49 - 30)   # a body armature registered on a non-body ARMA slot


def _source_slot_mismatch(tmp):
    # ARMO occupies slot 32 (body), but its ArmorAddon is registered on slot 49.
    # Coverage keys on the ARMO slot (32) -> it owns this item; the mint-site
    # (ARMA-slot) gate alone would miss it, so the ARMO-slot gate must suppress.
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]), groups=[
        esp.Group(label=b"ARMA", records=[
            _arma(OWN | 0x800, "CuirassAA", SLOT49, "armor/test/body_0.nif")]),
        esp.Group(label=b"ARMO", records=[
            _armo(OWN | 0x801, "Cuirass", OWN | 0x800, BODY)])])
    p = tmp / "Mod.esp"
    e.save(p)
    return p


def test_flag_on_suppresses_body_when_arma_slot_differs(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = _source_slot_mismatch(tmp_path)
    os.environ["CBBE2UBE_BODY_SKYPATCHER"] = "1"
    try:
        generate_ube_patch(src, tmp_path / "out.esp", master_data_dirs=[tmp_path],
                           converted_rel_paths={"armor/test/body_0.nif"})
    finally:
        os.environ.pop("CBBE2UBE_BODY_SKYPATCHER", None)
    slots = _override_slots(tmp_path / "out.esp")
    assert not any(s & BODY for s in slots), \
        f"slot-32 ARMO must be suppressed even when its ARMA is slot 49: {slots}"
    print("  test_flag_on_suppresses_body_when_arma_slot_differs OK")


def _rec(sig, formid, payload):
    return esp.Record(sig=sig, flags=0, formid=formid, timestamp_vc=0,
                      version_unk=0x002C, payload=payload)


def test_flag_on_suppresses_when_skyrim_not_master0(tmp_path):
    # DefaultRace must be resolved THROUGH the master table: a Requiem patch ESP
    # lists Skyrim.esm at a non-zero index, so DefaultRace RNAM has a non-zero top
    # byte. A raw (rnam>>24)==0 check misses it -> the body ARMO is NOT suppressed
    # while coverage DOES cover it -> double coverage. Skyrim.esm at index 1 here.
    tmp_path.mkdir(parents=True, exist_ok=True)
    R = 0x01000019                      # DefaultRace via master index 1
    own = 2 << 24                       # masters=[Update.esm, Skyrim.esm] -> own byte 2
    arma = _rec(b"ARMA", own | 0x800,
                encode_subrecord(b"EDID", encode_zstring("BodyAA"))
                + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
                + encode_subrecord(b"RNAM", struct.pack("<I", R))
                + encode_subrecord(b"MOD3", encode_zstring("armor/test/body_0.nif")))
    armo = _rec(b"ARMO", own | 0x801,
                encode_subrecord(b"EDID", encode_zstring("Body"))
                + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
                + encode_subrecord(b"RNAM", struct.pack("<I", R))
                + encode_subrecord(b"MODL", struct.pack("<I", own | 0x800))
                + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))
                + encode_subrecord(b"DNAM", struct.pack("<I", 0)))
    esp.ESP(header=esp.TES4Header(masters=["Update.esm", "Skyrim.esm"],
                                  num_records=0, next_object_id=0x900, version=1.7),
            groups=[esp.Group(label=b"ARMA", records=[arma]),
                    esp.Group(label=b"ARMO", records=[armo])]).save(tmp_path / "Mod.esp")
    os.environ["CBBE2UBE_BODY_SKYPATCHER"] = "1"
    try:
        generate_ube_patch(tmp_path / "Mod.esp", tmp_path / "out.esp",
                           master_data_dirs=[tmp_path],
                           converted_rel_paths={"armor/test/body_0.nif"})
    finally:
        os.environ.pop("CBBE2UBE_BODY_SKYPATCHER", None)
    slots = _override_slots(tmp_path / "out.esp")
    assert not any(s & BODY for s in slots), \
        f"body ARMO must be suppressed when Skyrim.esm isn't master 0: {slots}"
    print("  test_flag_on_suppresses_when_skyrim_not_master0 OK")


test_flag_off_keeps_body_override(
    Path(__file__).resolve().parent / "_tmp_bspflag")
test_flag_on_suppresses_body_keeps_nonbody(
    Path(__file__).resolve().parent / "_tmp_bspflag2")
test_flag_on_suppresses_body_when_arma_slot_differs(
    Path(__file__).resolve().parent / "_tmp_bspflag3")
test_flag_on_suppresses_when_skyrim_not_master0(
    Path(__file__).resolve().parent / "_tmp_bspflag4")
