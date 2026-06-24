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

"""Female-only mesh selection in _player_armor_mesh_bases.

UBE is a female body, so a piece WITH a (resolving) female model converts ONLY the
female mesh -- the male mesh is skipped (a female actor never renders it, and refitting
it to the female body would be wrong). The male mesh is kept only for a male-only piece,
or when the female model is a DEAD PATH (#174) so the female ARMA can redirect to the
converted male. With no resolver supplied, the legacy 'convert both' is kept."""
import struct

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.auto_convert import _player_armor_mesh_bases

DEFAULT_RACE = 0x00000019
BODY = 1 << 2


def _arma_mf(fid, edid, male=None, female=None, slot=BODY):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE)))
    if male is not None:
        payload += encode_subrecord(b"MOD2", encode_zstring(male))
    if female is not None:
        payload += encode_subrecord(b"MOD3", encode_zstring(female))
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _mod(tmp_path, name, *armas):
    mod = tmp_path / name
    mod.mkdir()
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=list(armas))])
    e.save(mod / (name.lower() + ".esp"))
    return mod


# distinct, non-overlapping mesh stems (avoid substring collisions like
# "malecuirass" inside "feMALEcuirass").
M = "armor/x/bob_1.nif"      # male model
F = "armor/x/alice_1.nif"    # female model
C = "armor/x/carl_1.nif"     # male-only piece


def test_female_resolves_male_skipped(tmp_path):
    mod = _mod(tmp_path, "MF", _arma_mf(0x01000800, "CuirassAA", male=M, female=F))
    bases = _player_armor_mesh_bases(mod, mesh_resolves=lambda b: "alice" in b)
    assert any("alice" in b for b in bases), bases
    assert not any("bob" in b for b in bases), bases           # male skipped


def test_dead_female_keeps_male_fallback(tmp_path):
    # #174: the female model is a dead path that won't convert; the male is the real
    # mesh the female ARMA gets redirected to -> it MUST still be selected.
    mod = _mod(tmp_path, "Dead", _arma_mf(0x01000800, "CuirassAA", male=M, female=F))
    bases = _player_armor_mesh_bases(mod, mesh_resolves=lambda b: False)
    assert any("bob" in b for b in bases), bases               # male fallback kept


def test_male_only_piece_converted(tmp_path):
    # No female model at all -> convert the male mesh (a female actor renders it).
    mod = _mod(tmp_path, "MaleOnly", _arma_mf(0x01000800, "GauntletAA", male=C))
    bases = _player_armor_mesh_bases(mod, mesh_resolves=lambda b: True)
    assert any("carl" in b for b in bases), bases


def test_no_resolver_is_legacy_both(tmp_path):
    mod = _mod(tmp_path, "Legacy", _arma_mf(0x01000800, "CuirassAA", male=M, female=F))
    bases = _player_armor_mesh_bases(mod)   # no resolver -> legacy 'convert both'
    assert any("bob" in b for b in bases), bases
    assert any("alice" in b for b in bases), bases
