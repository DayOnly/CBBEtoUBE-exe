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

r"""#arma-path-bytes -- armature model paths keep their bytes.

THE DEFECT. `rebuild_arma_payload` read every MOD2-5 path as UTF-8 with errors
ignored and wrote it back as UTF-8, also where the path was not changed. The
game reads these strings as cp1252, so an accented byte vanished: a source
armature naming `Armor\Café\cuirass_1.nif` (0xE9) was minted naming
`Armor\Caf\cuirass_1.nif`, a mesh that exists nowhere, and the converted-mesh
lookup was asked about that wrong path too. `restore_female_models` compared
and rewrote through the same round trip. Live census: 0 such paths.
`CBBE2UBE_NO_ARMA_PATH_BYTES=1` restores the old round trip.
"""
import json
import struct

import pytest

from src import esp
from src import ube_patcher as up

OFF = "CBBE2UBE_NO_ARMA_PATH_BYTES"
CAFE = "Armor\\Café\\cuirass_1.nif"
CAFE_B = CAFE.encode("cp1252")                       # ...Caf\xe9\...
CAFE_F = "Armor\\Café\\cuirass_f_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _payload(**models):
    p = esp.encode_subrecord(b"EDID", esp.encode_zstring("AA"))
    p += esp.encode_subrecord(b"RNAM", struct.pack("<I", 0x19))
    for sig, raw in models.items():
        p += esp.encode_subrecord(sig.encode(), raw + b"\x00")
    return p


def _models(payload):
    return {s.decode(): d for s, d in esp.iter_subrecords(payload)
            if s in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")}


def _rebuild(payload, converted=lambda p: False, **kw):
    return up.rebuild_arma_payload(payload, new_primary_rnam=0x19,
                                   new_additional_race_fids=[],
                                   converted_nif_exists=converted, **kw)


def test_an_unconverted_path_keeps_its_bytes():
    out = _rebuild(_payload(MOD3=CAFE_B))
    assert _models(out)["MOD3"] == CAFE_B + b"\x00"


def test_a_converted_path_is_the_prefix_plus_its_bytes():
    asked = []
    out = _rebuild(_payload(MOD2=CAFE_B),
                   converted=lambda p: asked.append(p) or True)
    assert asked == [CAFE], "the lookup must see the path the game reads"
    assert _models(out)["MOD2"] == b"!UBE\\" + CAFE_B + b"\x00"


def test_a_byte_cp1252_leaves_undefined_survives():
    raw = b"Armor\\x\x81y\\boots_1.nif"
    out = _rebuild(_payload(MOD4=raw))
    assert _models(out)["MOD4"] == raw + b"\x00"


def test_the_male_fallback_carries_the_bytes():
    log = []
    out = _rebuild(_payload(MOD2=CAFE_B), converted=lambda p: p == CAFE,
                   male_fallback_log=log)
    m = _models(out)
    assert m["MOD2"] == m["MOD3"] == b"!UBE\\" + CAFE_B + b"\x00"
    assert log == [{"slot": "MOD3", "orig": None, "to": "!UBE\\" + CAFE}]


def test_switched_off_the_byte_is_dropped_as_before(monkeypatch):
    monkeypatch.setenv(OFF, "1")
    out = _rebuild(_payload(MOD3=CAFE_B))
    assert _models(out)["MOD3"] == b"Armor\\Caf\\cuirass_1.nif\x00"


def test_an_ascii_path_is_written_as_before(monkeypatch):
    raw = b"Armor\\Iron\\cuirass_1.nif"
    on = _rebuild(_payload(MOD2=raw, MOD3=raw), converted=lambda p: True)
    monkeypatch.setenv(OFF, "1")
    off = _rebuild(_payload(MOD2=raw, MOD3=raw), converted=lambda p: True)
    assert on == off


def _patch_with_fallback(tmp_path):
    """A per-source patch whose female slot fell back to the converted male
    mesh, its sidecar, and the female mesh converted since."""
    log = []
    payload = _rebuild(_payload(MOD2=CAFE_B, MOD3=CAFE_F.encode("cp1252")),
                       converted=lambda p: p == CAFE, male_fallback_log=log)
    patches = tmp_path / "_unmerged_patches"
    patches.mkdir()
    patch = patches / "Test UBE patch.esp"
    fid = 0x01000800
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[esp.Record(
                sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                version_unk=0x002C, payload=payload)])]).save(patch)
    (patches / (patch.name + ".male_fallbacks.json")).write_text(
        json.dumps([dict(fid=fid, **e) for e in log]), encoding="utf-8")
    mesh = tmp_path / "meshes" / "!UBE" / "Armor" / "Café" / "cuirass_f_1.nif"
    mesh.parent.mkdir(parents=True)
    mesh.write_bytes(b"NIF")
    return patches, patch


def test_the_female_model_is_restored_with_its_bytes(tmp_path):
    patches, patch = _patch_with_fallback(tmp_path)
    st = up.restore_female_models(patches, tmp_path)
    assert st["models_restored"] == 1
    m = _models(esp.ESP.load(patch).group(b"ARMA").records[0].payload)
    assert m["MOD3"] == b"!UBE\\" + CAFE_F.encode("cp1252") + b"\x00"
    assert m["MOD2"] == b"!UBE\\" + CAFE_B + b"\x00"


def test_a_dead_female_slot_draws_the_male_path_with_its_bytes():
    """#coverage-female-standin: a dead female slot with no stand-in draws the
    unconverted male path as it is -- the bytes it was written with, the one
    cp1252 leaves undefined included (the lookups read it as a replacement
    character)."""
    raw = b"Armor\\x\x81y\\boots_1.nif"
    out = _rebuild(_payload(MOD2=raw, MOD3=b"Armor\\gone_1.nif"),
                   keep_named_female=True, female_mesh_exists=lambda p: False,
                   dead_female_male_as_is=lambda p: True, declined_log=[])
    assert _models(out)["MOD3"] == raw + b"\x00"


def test_a_redirected_skin_slot_is_written_in_the_game_codepage():
    out = up._redirect_mod3(_payload(MOD3=b"x_1.nif"), CAFE)
    assert _models(out)["MOD3"] == CAFE_B + b"\x00"
