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

"""--plugins-only (ESP refresh): refresh_mod_esp regenerates a mod's patch
ESP(s) from the .espgen.json snapshot the full run wrote -- byte-identical to
the full run's patch (same inputs) -- and skips mods with no snapshot."""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import auto_convert as ac
from src import esp
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring
from src import ube_patcher as up

DEFAULT = 0x00000019
BODY = up._BIPED_SLOT_BODY_BIT
OWN = 1 << 24


def _mk_mod(tmp):
    (tmp / "MyMod").mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=[], num_records=0, next_object_id=0x900,
                          version=1.7, flags=0x1), groups=[]).save(tmp / "Skyrim.esm")
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[]).save(tmp / "UBE_AllRace.esp")
    arma = Record(sig=b"ARMA", flags=0, formid=OWN | 0x800, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring("BodyAA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MOD3", encode_zstring("armor/m/body_0.nif"))))
    armo = Record(sig=b"ARMO", flags=0, formid=OWN | 0x801, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring("Body"))
        + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MODL", struct.pack("<I", OWN | 0x800))
        + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))))
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[Group(label=b"ARMA", records=[arma]),
                Group(label=b"ARMO", records=[armo])]).save(
        tmp / "MyMod" / "MyMod.esp")
    return tmp / "MyMod"


def test_refresh_replays_snapshot_byte_identical(tmp_path):
    src_dir = _mk_mod(tmp_path)
    out_dir = tmp_path / "out"
    pdir = out_dir / "_unmerged_patches"
    pdir.mkdir(parents=True, exist_ok=True)
    patch = pdir / "MyMod UBE patch.esp"
    # simulate the full run: generate + write the espgen snapshot
    conv = {"armor/m/body_0.nif"}
    up.generate_ube_patch(src_dir / "MyMod.esp", patch,
                          master_data_dirs=[tmp_path],
                          converted_rel_paths=conv)
    Path(str(patch) + ".espgen.json").write_text(json.dumps({
        "source_esp": str(src_dir / "MyMod.esp"),
        "converted_rel_paths": sorted(conv),
        "body_mesh_rel_paths": []}), encoding="utf-8")
    orig = patch.read_bytes()
    patch.unlink()                              # refresh must recreate it
    r = ac.refresh_mod_esp(src_dir, out_dir, master_data_dirs=[tmp_path])
    assert not r.esp_gen_failures, r.esp_gen_failures
    assert patch.is_file(), "refresh must regenerate the patch"
    assert patch.read_bytes() == orig, "refresh output must be byte-identical"
    assert r.esp_stats is not None
    print("  test_refresh_replays_snapshot_byte_identical OK")


def test_refresh_skips_mod_without_snapshot(tmp_path):
    src_dir = _mk_mod(tmp_path)
    out_dir = tmp_path / "out2"
    r = ac.refresh_mod_esp(src_dir, out_dir, master_data_dirs=[tmp_path])
    assert not r.output_esps
    assert any("no espgen snapshot" in n for n in r.notes), r.notes
    print("  test_refresh_skips_mod_without_snapshot OK")
