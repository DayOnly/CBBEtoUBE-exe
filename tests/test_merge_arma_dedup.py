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

"""Merge-time RECORD dedup (MERGE_DEDUP_ARMAS): the patcher mints one UBE ARMA per
source armature, so many armors resolving to the SAME UBE mesh+slots+races yield
byte-identical minted ARMAs (only EDID differs). The merge collapses each set to
one record; ARMAs differing in ANY rendered field (mesh, slots) are NEVER merged.
#merge-arma-dedup
"""
import struct

import src.ube_patcher as up
from src import esp
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring


def _arma(local, tag, mesh, bod2=struct.pack("<II", 0x4, 4)):
    payload = (
        encode_subrecord(b"EDID", encode_zstring(f"ARMA_{tag}_UBE"))
        + encode_subrecord(b"BOD2", bod2)
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
        + encode_subrecord(b"MOD3", encode_zstring(mesh))
    )
    return Record(sig=b"ARMA", flags=0, formid=(2 << 24) | local,
                  timestamp_vc=0, version_unk=0x002C, payload=payload)


def _patch(path, records):
    ESP(header=TES4Header(masters=["Skyrim.esm", "UBE_AllRace.esp"],
                          num_records=0, next_object_id=0x900, version=1.7),
        groups=[Group(label=b"ARMA", records=records)]).save(path)
    return path


def _arma_meshes(esp_path):
    m = []
    for g in ESP.load(esp_path).groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            for sig, d in esp.iter_subrecords(r.payload):
                if sig == b"MOD3":
                    m.append(d.rstrip(b"\x00").decode())
    return m


def test_identical_minted_armas_collapse_to_one(tmp_path):
    # Two patches each mint an ARMA for the SAME mesh (identical except EDID) +
    # one distinct mesh -> 3 minted, deduped to 2 records.
    p1 = _patch(tmp_path / "a.esp",
                [_arma(0x800, "a", "!UBE/x.nif"), _arma(0x801, "a2", "!UBE/y.nif")])
    p2 = _patch(tmp_path / "b.esp", [_arma(0x800, "b", "!UBE/x.nif")])
    out = tmp_path / "m.esp"
    up.merge_patches([p1, p2], out, esl_flag=True)
    meshes = sorted(_arma_meshes(out))
    assert meshes == ["!UBE/x.nif", "!UBE/y.nif"], meshes   # x.nif deduped


def test_dedup_ignores_model_texture_hash(tmp_path):
    # Same mesh/slots/races; one ARMA carries a MO3T texture hash, the other does
    # not -> render-identical, so they STILL collapse to one record.
    def arma(local, tag, with_modt):
        payload = (
            encode_subrecord(b"EDID", encode_zstring(f"ARMA_{tag}_UBE"))
            + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
            + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
            + encode_subrecord(b"MOD3", encode_zstring("!UBE/x.nif"))
        )
        if with_modt:
            payload += encode_subrecord(b"MO3T", b"\xaa" * 24)
        return Record(sig=b"ARMA", flags=0, formid=(2 << 24) | local,
                      timestamp_vc=0, version_unk=0x002C, payload=payload)

    p1 = _patch(tmp_path / "a.esp", [arma(0x800, "a", True)])
    p2 = _patch(tmp_path / "b.esp", [arma(0x800, "b", False)])
    out = tmp_path / "m.esp"
    up.merge_patches([p1, p2], out, esl_flag=True)
    assert len(_arma_meshes(out)) == 1     # collapsed despite the MO3T difference


def test_dedup_keeps_different_alt_texture_sets(tmp_path):
    # Same mesh but different alt-texture SET (MO3S) -> different look -> NOT merged.
    def arma(local, tag, mo3s):
        payload = (
            encode_subrecord(b"EDID", encode_zstring(f"ARMA_{tag}_UBE"))
            + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
            + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
            + encode_subrecord(b"MOD3", encode_zstring("!UBE/x.nif"))
            + encode_subrecord(b"MO3S", mo3s)
        )
        return Record(sig=b"ARMA", flags=0, formid=(2 << 24) | local,
                      timestamp_vc=0, version_unk=0x002C, payload=payload)

    p1 = _patch(tmp_path / "a.esp", [arma(0x800, "a", struct.pack("<I", 0x111))])
    p2 = _patch(tmp_path / "b.esp", [arma(0x800, "b", struct.pack("<I", 0x222))])
    out = tmp_path / "m.esp"
    up.merge_patches([p1, p2], out, esl_flag=True)
    assert len(_arma_meshes(out)) == 2     # kept: alt-texture sets differ


def test_same_mesh_different_slots_not_collapsed(tmp_path):
    # Same mesh but different BOD2 slot flags -> distinct render -> NOT merged.
    p1 = _patch(tmp_path / "a.esp",
                [_arma(0x800, "a", "!UBE/x.nif", bod2=struct.pack("<II", 0x4, 4))])
    p2 = _patch(tmp_path / "b.esp",
                [_arma(0x800, "b", "!UBE/x.nif",
                       bod2=struct.pack("<II", 0x4 | 0x100, 4))])
    out = tmp_path / "m.esp"
    up.merge_patches([p1, p2], out, esl_flag=True)
    assert len(_arma_meshes(out)) == 2   # both survive (different slots)


def test_dedup_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_MERGE_DEDUP", "1")
    import importlib
    mod = importlib.reload(up)
    try:
        p1 = _patch(tmp_path / "a.esp", [mod_arma(mod, 0x800, "a", "!UBE/x.nif")])
        p2 = _patch(tmp_path / "b.esp", [mod_arma(mod, 0x800, "b", "!UBE/x.nif")])
        out = tmp_path / "m.esp"
        mod.merge_patches([p1, p2], out, esl_flag=True)
        assert len(_arma_meshes(out)) == 2   # not deduped when disabled
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_MERGE_DEDUP", raising=False)
        importlib.reload(up)


def test_esl_dense_repack_when_dedup_brings_under_cap(tmp_path, monkeypatch):
    # A single patch mints MORE than the ESL cap, but dedup collapses it BELOW the
    # cap. FormIDs are allocated PRE-dedup (dense 0x800.. with gaps after dedup) and
    # `as_esl` is decided on the pre-dedup total -> the piece was wrongly downgraded
    # to a full ESP and its own-FormID max could exceed 0xFFF. The merge must re-pack
    # emitted own FormIDs densely and flag the piece ESL. This is the unified-coverage
    # "Combined.esp not ESL-flagged" bug. #esl-repack
    monkeypatch.setattr(up, "ESL_MAX_OWN_RECORDS", 3)
    recs = [_arma(0x800, "a", "!UBE/x.nif"), _arma(0x801, "b", "!UBE/y.nif"),
            _arma(0x802, "c", "!UBE/z.nif"), _arma(0x803, "d", "!UBE/x.nif"),
            _arma(0x804, "e", "!UBE/y.nif")]          # 5 minted, x/y dup -> 3 kept
    out = tmp_path / "m.esp"
    up.merge_patches([_patch(tmp_path / "big.esp", recs)], out, esl_flag=True)
    e = ESP.load(out)
    own = len(e.header.masters)
    fids = sorted(r.formid & 0xFFFFFF for g in e.groups if g.label == b"ARMA"
                  for r in g.records if ((r.formid >> 24) & 0xFF) == own)
    assert len(fids) == 3, fids                        # deduped under the cap
    assert e.header.flags & up.TES4_FLAG_ESL, "must be ESL after post-dedup re-pack"
    assert fids == [0x800, 0x801, 0x802], \
        f"expected dense re-pack, got {[hex(f) for f in fids]}"
    assert max(fids) <= 0xFFF


def mod_arma(mod, local, tag, mesh):
    payload = (
        encode_subrecord(b"EDID", encode_zstring(f"ARMA_{tag}_UBE"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
        + encode_subrecord(b"MOD3", encode_zstring(mesh))
    )
    return Record(sig=b"ARMA", flags=0, formid=(2 << 24) | local,
                  timestamp_vc=0, version_unk=0x002C, payload=payload)
