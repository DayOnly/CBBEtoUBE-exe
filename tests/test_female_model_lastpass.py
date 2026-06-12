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

"""#female-model-priority: the male-only fallback must be UNDOABLE as a LAST
step. generate_ube_patch redirects/synthesises an ARMA's FEMALE model from the
converted MALE mesh when no converted female mesh is visible AT PATCH TIME
(#174 / #UBE-female-only-policy) — but patches are generated per-mod DURING
conversion, so a female mesh converted by ANOTHER mod (or later in the run)
is invisible to that decision. restore_female_models() runs after ALL mods
have converted and re-points the fallback'd slots at the converted FEMALE
mesh wherever one now exists, so a male model never overwrites a female one
that exists anywhere in the list."""
import struct
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import esp
from src.ube_patcher import rebuild_arma_payload, restore_female_models


MALE = "armor\\x\\cuirass_m_1.nif"
FEMALE = "armor\\x\\cuirass_f_1.nif"
UBE_MALE = "!UBE\\" + MALE
UBE_FEMALE = "!UBE\\" + FEMALE


def _arma_payload(*subrecords):
    out = b""
    for sig, data in subrecords:
        out += esp.encode_subrecord(sig, data)
    return out


def _src_payload(with_female=True):
    subs = [
        (b"EDID", esp.encode_zstring("TestArma")),
        (b"RNAM", struct.pack("<I", 0x00000019)),
        (b"MOD2", esp.encode_zstring(MALE)),
    ]
    if with_female:
        subs.append((b"MOD3", esp.encode_zstring(FEMALE)))
    return _arma_payload(*subs)


def _models(payload):
    out = {}
    for sig, data in esp.iter_subrecords(payload):
        if sig in (b"MOD2", b"MOD3"):
            out[sig] = data.rstrip(b"\x00").decode("utf-8")
    return out


def _rebuild(payload, log):
    return rebuild_arma_payload(
        payload,
        new_primary_rnam=0x12345678,
        new_additional_race_fids=[],
        converted_nif_exists=lambda p: p == MALE,  # only the MALE converted
        male_fallback_log=log,
    )


def test_redirect_is_logged_with_original_female_path():
    log = []
    out = _rebuild(_src_payload(with_female=True), log)
    models = _models(out)
    assert models[b"MOD3"] == UBE_MALE, "female slot must male-fallback"
    assert log == [{"slot": "MOD3", "orig": FEMALE, "to": UBE_MALE}], log


def test_synth_is_logged_with_orig_none():
    log = []
    out = _rebuild(_src_payload(with_female=False), log)
    models = _models(out)
    assert models[b"MOD3"] == UBE_MALE, "female slot must be synthesised"
    assert log == [{"slot": "MOD3", "orig": None, "to": UBE_MALE}], log


def _write_patch(tmp_path, payload, fid=0x01000800):
    patches = tmp_path / "_unmerged_patches"
    patches.mkdir(exist_ok=True)
    p = patches / "Test UBE patch.esp"
    e = esp.ESP(
        header=esp.TES4Header(masters=["Skyrim.esm"]),
        groups=[esp.Group(label=b"ARMA", records=[esp.Record(
            sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
            version_unk=0x002C, payload=payload)])],
    )
    e.save(p)
    return patches, p, fid


def test_lastpass_restores_female_when_mesh_exists(tmp_path):
    log = []
    payload = _rebuild(_src_payload(with_female=True), log)
    patches, patch_path, fid = _write_patch(tmp_path, payload)
    import json
    (patches / (patch_path.name + ".male_fallbacks.json")).write_text(
        json.dumps([dict(fid=fid, **e) for e in log]), encoding="utf-8")

    # The female mesh converts LATER (another mod in the list) -> on disk.
    mesh = tmp_path / "meshes" / "!UBE" / "armor" / "x" / "cuirass_f_1.nif"
    mesh.parent.mkdir(parents=True)
    mesh.write_bytes(b"NIF")

    stats = restore_female_models(patches, tmp_path)
    assert stats["models_restored"] == 1 and stats["patches_changed"] == 1

    models = _models(esp.ESP.load(patch_path).group(b"ARMA").records[0].payload)
    assert models[b"MOD3"] == UBE_FEMALE, (
        "female model must be restored once its converted mesh exists")
    assert models[b"MOD2"] == UBE_MALE, "male model untouched"

    # Idempotent: slot no longer carries the recorded male path -> no-op.
    stats2 = restore_female_models(patches, tmp_path)
    assert stats2["models_restored"] == 0


def test_lastpass_keeps_fallback_when_no_female_mesh(tmp_path):
    log = []
    payload = _rebuild(_src_payload(with_female=True), log)
    patches, patch_path, fid = _write_patch(tmp_path, payload)
    import json
    (patches / (patch_path.name + ".male_fallbacks.json")).write_text(
        json.dumps([dict(fid=fid, **e) for e in log]), encoding="utf-8")

    stats = restore_female_models(patches, tmp_path)  # no mesh on disk
    assert stats["models_restored"] == 0 and stats["patches_changed"] == 0
    models = _models(esp.ESP.load(patch_path).group(b"ARMA").records[0].payload)
    assert models[b"MOD3"] == UBE_MALE, (
        "male fallback must stand when no converted female mesh exists")


def test_lastpass_skips_synth_entries(tmp_path):
    log = []
    payload = _rebuild(_src_payload(with_female=False), log)
    patches, patch_path, fid = _write_patch(tmp_path, payload)
    import json
    (patches / (patch_path.name + ".male_fallbacks.json")).write_text(
        json.dumps([dict(fid=fid, **e) for e in log]), encoding="utf-8")

    stats = restore_female_models(patches, tmp_path)
    assert stats["models_restored"] == 0
    models = _models(esp.ESP.load(patch_path).group(b"ARMA").records[0].payload)
    assert models[b"MOD3"] == UBE_MALE, "synth (orig=None) has nothing to restore"
