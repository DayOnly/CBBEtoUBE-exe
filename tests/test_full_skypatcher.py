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

"""CBBE2UBE_FULL_SKYPATCHER (Tier 2): the per-source patch mints the SAME
armatures but emits NO ARMO overrides -- links go to a .skypatcher.json sidecar,
and the merge turns them into armorAddonsToAdd INI lines against final Combined
FormIDs. Flag OFF stays byte-identical (ARMO overrides as before)."""
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import esp
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher as up

DEFAULT = 0x00000019
BODY = up._BIPED_SLOT_BODY_BIT
HEAD = 1 << (30 - 30)
OWN = 1 << 24


def _arma(fid, edid, slot, mesh):
    return Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MOD3", encode_zstring(mesh))))


def _armo(fid, edid, arma_fid, slot):
    return Record(sig=b"ARMO", flags=0, formid=fid, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
        + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))))


def _mk_env(tmp):
    tmp.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=[], num_records=0, next_object_id=0x900,
                          version=1.7, flags=0x1), groups=[]).save(tmp / "Skyrim.esm")
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[]).save(tmp / "UBE_AllRace.esp")


def _mk_source(tmp, name, mesh_stem):
    src = ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                                next_object_id=0x900, version=1.7), groups=[
        Group(label=b"ARMA", records=[
            _arma(OWN | 0x800, "BodyAA", BODY, f"armor/{mesh_stem}/body_0.nif"),
            _arma(OWN | 0x802, "HelmAA", HEAD, f"armor/{mesh_stem}/helm_0.nif")]),
        Group(label=b"ARMO", records=[
            _armo(OWN | 0x801, "Body", OWN | 0x800, BODY),
            _armo(OWN | 0x803, "Helm", OWN | 0x802, HEAD)])])
    src.save(tmp / name)
    return tmp / name


def _gen(tmp, name, mesh_stem, flag):
    src = _mk_source(tmp, name, mesh_stem)
    out = tmp / (name.replace(".esp", "") + " UBE patch.esp")
    # full SkyPatcher is the product DEFAULT; conftest pins the session to the
    # legacy path (CBBE2UBE_NO_SKYPATCHER=1), so clear that pin to exercise it.
    if flag:
        os.environ.pop("CBBE2UBE_NO_SKYPATCHER", None)
        os.environ["CBBE2UBE_FULL_SKYPATCHER"] = "1"
    else:
        os.environ["CBBE2UBE_NO_SKYPATCHER"] = "1"
    try:
        stats = up.generate_ube_patch(
            src, out, master_data_dirs=[tmp],
            converted_rel_paths={f"armor/{mesh_stem}/body_0.nif",
                                 f"armor/{mesh_stem}/helm_0.nif"})
    finally:
        os.environ.pop("CBBE2UBE_FULL_SKYPATCHER", None)
        os.environ["CBBE2UBE_NO_SKYPATCHER"] = "1"   # restore the conftest pin
    return out, stats


def test_flag_on_no_overrides_sidecar_links(tmp_path):
    _mk_env(tmp_path)
    out, stats = _gen(tmp_path, "ModA.esp", "a", flag=True)
    e = ESP.load(out)
    assert e.group(b"ARMO") is None, "full-SP patch must carry NO ARMO overrides"
    assert e.group(b"ARMA") is not None and len(e.group(b"ARMA").records) >= 2
    assert stats["armo_override_count"] == 0
    assert stats["skypatcher_link_targets"] == 2          # Body + Helm ARMOs
    sc = json.loads(Path(str(out) + ".skypatcher.json").read_text("utf-8"))
    targets = {tuple(x["armo"]) for x in sc}
    assert ("moda.esp", 0x801) in targets and ("moda.esp", 0x803) in targets
    # every linked fid exists in the patch's ARMA group
    fids = {r.formid for r in e.group(b"ARMA").records}
    for x in sc:
        for a in x["adds"]:
            assert a["fid"] in fids, "sidecar fid must match post-prune records"
    print("  test_flag_on_no_overrides_sidecar_links OK")


def test_flag_off_unchanged_and_clears_stale_sidecar(tmp_path):
    _mk_env(tmp_path)
    out, _ = _gen(tmp_path, "ModB.esp", "b", flag=True)     # writes sidecar
    assert Path(str(out) + ".skypatcher.json").is_file()
    out2, stats = _gen(tmp_path, "ModB.esp", "b", flag=False)
    e = ESP.load(out2)
    assert e.group(b"ARMO") is not None, "flag OFF must emit overrides as before"
    assert stats["armo_override_count"] >= 2
    assert not Path(str(out2) + ".skypatcher.json").is_file(), \
        "stale sidecar must be removed on a flag-OFF re-run"
    print("  test_flag_off_unchanged_and_clears_stale_sidecar OK")


def test_merge_emits_final_ini_lines(tmp_path):
    _mk_env(tmp_path)
    p1, _ = _gen(tmp_path, "ModA.esp", "a", flag=True)
    p2, _ = _gen(tmp_path, "ModB.esp", "b", flag=True)
    comb = tmp_path / "Combined.esp"
    stats = up.merge_patches_split([p1, p2], comb, master_data_dirs=[tmp_path])
    lines = stats.get("skypatcher_ini_lines") or []
    assert stats.get("skypatcher_targets") == 4, stats.get("skypatcher_targets")
    assert len(lines) == 4, lines
    e = ESP.load(comb)
    fids = {r.formid & 0xFFFFFF for g in e.groups if g.label == b"ARMA"
            for r in g.records}
    for l in lines:
        assert l.startswith("filterByArmors=mod"), l
        add = l.split("armorAddonsToAdd=", 1)[1]
        for part in add.split(","):
            plug, fid = part.rsplit("|", 1)
            assert plug == comb.name, part
            assert int(fid, 16) in fids, "INI fid must exist in the Combined"
    # both source ARMOs of each mod covered
    tgts = {l.split("=", 1)[1].split(":", 1)[0] for l in lines}
    assert tgts == {"moda.esp|000801", "moda.esp|000803",
                    "modb.esp|000801", "modb.esp|000803"}, tgts
    print("  test_merge_emits_final_ini_lines OK")


def test_coverage_excludes_ini_linked_armos(tmp_path):
    # #fsp-dedup: an ARMO the Combined INI already links must NOT be re-covered
    # by the fallback coverage (double armature = body renders twice /
    # UBE-primary hands mint = invisible gauntlets).
    _mk_env(tmp_path)
    src = _mk_source(tmp_path, "ModC.esp", "c")
    res = up.generate_modded_body_ube_coverage_patch(
        tmp_path / "Cov.esp", [tmp_path / "Skyrim.esm",
                               tmp_path / "UBE_AllRace.esp", src],
        converted_rel_paths={"armor/c/body_0.nif"},
        exclude_names={"cov.esp"}, master_data_dirs=[tmp_path])
    assert res["armo_targets"] >= 1, res            # covered without exclusion
    res2 = up.generate_modded_body_ube_coverage_patch(
        tmp_path / "Cov2.esp", [tmp_path / "Skyrim.esm",
                                tmp_path / "UBE_AllRace.esp", src],
        converted_rel_paths={"armor/c/body_0.nif"},
        exclude_armo_abs={("modc.esp", 0x801)},
        exclude_names={"cov2.esp"}, master_data_dirs=[tmp_path])
    assert res2["armo_targets"] == res["armo_targets"] - 1, \
        (res["armo_targets"], res2["armo_targets"])
    print("  test_coverage_excludes_ini_linked_armos OK")
