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

"""Gore/effect armor (Next-Gen Decapitations, dismemberment, etc.) binds real
DefaultRace body-slot ARMAs but flags the ARMO NON-PLAYABLE -- it's applied by
script, never equipped. _player_armor_mesh_bases must NOT select those meshes
for conversion, while still selecting genuine equippable armor."""
import struct

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.auto_convert import _player_armor_mesh_bases

DEFAULT_RACE = 0x00000019
BODY = 1 << 2
ARMO_NONPLAYABLE = 0x04


def _arma(fid, edid, mesh, slot=BODY):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
               + encode_subrecord(b"MOD3", encode_zstring(mesh)))
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _armo(fid, edid, arma_fid, nonplayable, slot=BODY):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + encode_subrecord(b"FULL", encode_zstring(edid))
               + encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
               + encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0)))
    flags = ARMO_NONPLAYABLE if nonplayable else 0
    return esp.Record(sig=b"ARMO", flags=flags, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _write_mod(tmp_path, name, armas, armos):
    mod = tmp_path / name
    mod.mkdir()
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=armas),
                        esp.Group(label=b"ARMO", records=armos)])
    e.save(mod / (name.lower() + ".esp"))
    return mod


def test_gore_excluded_real_armor_kept(tmp_path):
    mod = _write_mod(
        tmp_path, "MixedMod",
        armas=[_arma(0x01000800, "GoodArmorAA", "armor/test/good_1.nif"),
               _arma(0x01000801, "GoreLimbAA", "gore/severed_1.nif",
                     slot=BODY | (1 << 3) | (1 << 4) | (1 << 7))],
        armos=[_armo(0x01000900, "GoodArmor", 0x01000800, nonplayable=False),
               _armo(0x01000901, "GoreLimb", 0x01000801, nonplayable=True)])
    bases = _player_armor_mesh_bases(mod)
    assert any("good" in b for b in bases), bases          # equippable -> kept
    assert not any("severed" in b for b in bases), bases   # gore -> excluded


def test_gore_only_mod_yields_no_armor(tmp_path):
    # A pure gore/decapitation mod (all ARMOs non-playable) selects nothing,
    # so _find_armor_mod_dirs won't pick it as a source at all.
    mod = _write_mod(
        tmp_path, "GoreOnly",
        armas=[_arma(0x01000800, "DecapBodyAA", "ngd/headhumanoid_1.nif",
                     slot=BODY | (1 << 3) | (1 << 7))],
        armos=[_armo(0x01000900, "DecapBody", 0x01000800, nonplayable=True)])
    assert _player_armor_mesh_bases(mod) == set()


def test_armo_in_master_not_dropped(tmp_path):
    # A vanilla-replacer ARMA whose ARMO lives in a MASTER is referenced by NO
    # same-plugin ARMO -> must NOT be treated as gore (kept).
    mod = _write_mod(
        tmp_path, "Replacer",
        armas=[_arma(0x01000800, "IronCuirassAA", "armor/iron/f/cuirass_1.nif")],
        armos=[])                       # ARMO is in Skyrim.esm, not here
    bases = _player_armor_mesh_bases(mod)
    assert any("cuirass" in b for b in bases), bases
