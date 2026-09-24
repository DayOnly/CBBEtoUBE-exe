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

r"""#twin-path-strip-meshes -- a model spelt `meshes\X` is written `!UBE\X`.

THE DEFECT. An armature may spell its model `meshes\X.nif`; the engine reads
that as `meshes\X.nif`. The twin lookup (#coverage-ube-twin) took the `meshes\`
off and found the hand-made UBE mesh at `meshes\!UBE\X.nif`, but the rebuild
put `!UBE\` in front of the RAW path: `!UBE\meshes\X.nif`, which the engine
reads as `meshes\!UBE\meshes\X.nif` -- a file that exists nowhere, a minted
`!UBE\` path the missing-nif rule calls a load-crash cause. The piece validator
whitelisted the same broken string and the postflight resolver took the prefix
off again, so neither reported it. Live: 0 slots at default settings (the one
case is claimed by #claim-meshes-prefix), 1 armature / 2 slots with
`CBBE2UBE_NO_CLAIM_MESHES_PREFIX=1`.

`CBBE2UBE_NO_TWIN_PATH_STRIP_MESHES=1` writes the raw path again.
"""
import json
import struct

import pytest

from src import auto_convert as ac
from src import esp
from src import ube_patcher as up
from tests.test_coverage_ube_twin import (BODY, FEET, HEAD, _minted, _modlist,
                                          _world)

OFF = "CBBE2UBE_NO_TWIN_PATH_STRIP_MESHES"
REL = r"Armor\X\Boots_Female_1.nif"
SRC = "meshes\\" + REL                  # the source armature's spelling
GOOD = "!UBE\\" + REL                   # what the engine finds
BROKEN = "!UBE\\meshes\\" + REL         # what was written


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_COVERAGE_UBE_TWIN", raising=False)


def _twin(tmp_path, monkeypatch, files=("armor/x/boots_female_1.nif",)):
    mods = _modlist(tmp_path, monkeypatch, [("A UBE Patch", list(files))])
    return mods, ac._third_party_ube_twin_lookup(mods / "Out")


def _missing(warns):
    return [w for w in warns if w.startswith("missing-nif")]


def _body(tmp_path, models, slots, conv, twin):
    (tmp_path / "meshes").mkdir(parents=True, exist_ok=True)   # the validator checks our output
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, models, slots), converted_rel_paths=conv,
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True,
        ube_twin_exists=twin)
    return st, _minted(out)


def _nonbody(tmp_path, models, twin):
    (tmp_path / "meshes").mkdir(exist_ok=True)
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, models, HEAD), converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True, ube_twin_exists=twin)
    return st, _minted(out)


# ------------------------------------------------------------------ the switch

def test_it_is_on_unless_switched_off(monkeypatch):
    assert up._twin_path_strip_meshes() is True
    monkeypatch.setenv(OFF, "1")
    assert up._twin_path_strip_meshes() is False


def test_the_prefix_is_taken_off_either_slash_any_case():
    assert up._strip_meshes_prefix(SRC) == REL
    assert up._strip_meshes_prefix("Meshes/" + REL) == REL
    assert up._strip_meshes_prefix("\\MESHES\\" + REL) == REL
    assert up._strip_meshes_prefix(REL) == REL
    assert up._strip_meshes_prefix(r"armor\meshes\x_1.nif") == r"armor\meshes\x_1.nif"
    assert up._strip_meshes_prefix(r"meshesx\y_1.nif") == r"meshesx\y_1.nif"


# ------------------------------------------------------------------ the passes

def test_the_body_pass_writes_the_path_the_twin_is_at(tmp_path, monkeypatch):
    mods, twin = _twin(tmp_path, monkeypatch)
    st, minted = _body(tmp_path, {b"MOD3": SRC}, FEET, set(), twin)
    assert minted[0][b"MOD3"] == GOOD
    # The engine reads `!UBE\X` as meshes\!UBE\X: the twin mod ships it.
    assert (mods / "A UBE Patch" / "meshes" / GOOD.replace("\\", "/")).is_file()
    assert [d["path"] for d in st["ube_twin"]] == [GOOD], \
        "the validator whitelists the string written"
    assert _missing(st["validation_warnings"]) == []


def test_the_non_body_pass_writes_the_path_the_twin_is_at(tmp_path, monkeypatch):
    _mods, twin = _twin(tmp_path, monkeypatch, ("armor/x/helm_1.nif",))
    st, minted = _nonbody(tmp_path, {b"MOD3": r"meshes\armor\x\helm_1.nif"}, twin)
    assert minted[0][b"MOD3"] == r"!UBE\armor\x\helm_1.nif"
    assert [d["path"] for d in st["ube_twin"]] == [r"!UBE\armor\x\helm_1.nif"]
    assert _missing(st["validation_warnings"]) == []


def test_switched_off_the_raw_path_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _mods, twin = _twin(tmp_path, monkeypatch)
    st, minted = _body(tmp_path, {b"MOD3": SRC}, FEET, set(), twin)
    assert minted[0][b"MOD3"] == BROKEN
    assert [d["path"] for d in st["ube_twin"]] == [BROKEN]


def test_an_unprefixed_path_is_unchanged(tmp_path, monkeypatch):
    """Negative control: only a `meshes\\` spelling moves."""
    _mods, twin = _twin(tmp_path, monkeypatch)
    st, minted = _body(tmp_path, {b"MOD3": REL}, FEET, set(), twin)
    assert minted[0][b"MOD3"] == GOOD and [d["path"] for d in st["ube_twin"]] == [GOOD]


def test_our_converted_mesh_is_found_for_a_prefixed_path(tmp_path, monkeypatch):
    """The converted-mesh check asks the path the twin lookup asks: a torso
    whose mesh we converted is admitted and drawn with it, not left out."""
    torso = r"armor\x\cuirass_1.nif"
    conv = {torso.replace("\\", "/").lower()}
    st, minted = _body(tmp_path / "on", {b"MOD3": "meshes\\" + torso}, BODY, conv,
                       lambda p: None)
    assert [m[b"MOD3"] for m in minted] == ["!UBE\\" + torso]
    assert st["ube_twin"] == [], "ours is not a twin"
    monkeypatch.setenv(OFF, "1")
    st, minted = _body(tmp_path / "off", {b"MOD3": "meshes\\" + torso}, BODY, conv,
                       lambda p: None)
    assert minted == [], "switched off, the prefixed torso is not admitted"


def test_the_converted_check_normalises_like_the_lookup():
    crp = {"armor/x/boots_female_1.nif"}
    assert up._converted_model_exists(SRC, crp, strip_meshes=True)
    assert not up._converted_model_exists(SRC, crp)
    assert up._converted_model_exists(REL, crp, strip_meshes=True)


# ------------------------------------------------------------------ the postflight

def _resolver(tmp_path, monkeypatch, files):
    """The real resolver over the real lookup, on a modlist of one patch."""
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_NUDE_SKIN", "1")
    mods = _modlist(tmp_path, monkeypatch, [("A UBE Patch", list(files))])
    return ac._outside_ube_mesh_resolver(mods / "Out")


def test_the_postflight_judges_the_string_written(tmp_path, monkeypatch):
    res = _resolver(tmp_path, monkeypatch, ["armor/x/boots_female_1.nif"])
    assert res(GOOD)
    assert not res(BROKEN), "meshes\\!UBE\\meshes\\... exists nowhere"


def test_the_postflight_finds_a_file_really_at_the_written_path(tmp_path, monkeypatch):
    """`as written` is a lookup, not a refusal: a mod that ships the file at
    meshes\\!UBE\\meshes\\X resolves `!UBE\\meshes\\X`."""
    res = _resolver(tmp_path, monkeypatch, ["meshes/armor/x/boots_female_1.nif"])
    assert res(BROKEN)
    assert not res(GOOD)


def test_switched_off_the_postflight_passes_the_broken_path(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    res = _resolver(tmp_path, monkeypatch, ["armor/x/boots_female_1.nif"])
    assert res(BROKEN)


# ------------------------------------------------------------------ the female re-check

def _fallback_patch(tmp_path):
    male = r"armor\x\cuirass_m_1.nif"
    p = esp.encode_subrecord(b"EDID", esp.encode_zstring("TestArma"))
    p += esp.encode_subrecord(b"RNAM", struct.pack("<I", 0x19))
    p += esp.encode_subrecord(b"MOD2", esp.encode_zstring(male))
    p += esp.encode_subrecord(b"MOD3", esp.encode_zstring(r"meshes\armor\x\cuirass_f_1.nif"))
    log: list = []
    payload = up.rebuild_arma_payload(
        p, new_primary_rnam=0x12345678, new_additional_race_fids=[],
        converted_nif_exists=lambda q: q == male, male_fallback_log=log)
    patches = tmp_path / "_unmerged_patches"
    patches.mkdir()
    patch = patches / "Test UBE patch.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[esp.Record(
                sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                version_unk=0x002C, payload=payload)])]).save(patch)
    (patches / (patch.name + ".male_fallbacks.json")).write_text(
        json.dumps([dict(fid=0x01000800, **e) for e in log]), encoding="utf-8")
    mesh = tmp_path / "meshes" / "!UBE" / "armor" / "x" / "cuirass_f_1.nif"
    mesh.parent.mkdir(parents=True)
    mesh.write_bytes(b"NIF")
    return patches, patch


def _mod3(patch):
    rec = esp.ESP.load(patch).group(b"ARMA").records[0]
    return next(d.rstrip(b"\x00").decode() for s, d in esp.iter_subrecords(rec.payload)
                if s == b"MOD3")


def test_the_female_recheck_writes_the_mesh_it_found(tmp_path):
    patches, patch = _fallback_patch(tmp_path)
    assert up.restore_female_models(patches, tmp_path)["models_restored"] == 1
    assert _mod3(patch) == r"!UBE\armor\x\cuirass_f_1.nif"


def test_switched_off_the_female_recheck_writes_the_raw_path(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    patches, patch = _fallback_patch(tmp_path)
    assert up.restore_female_models(patches, tmp_path)["models_restored"] == 1
    assert _mod3(patch) == r"!UBE\meshes\armor\x\cuirass_f_1.nif"
