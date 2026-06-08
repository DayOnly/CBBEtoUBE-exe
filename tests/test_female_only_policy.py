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

"""Guard for the UBE female-only ARMA policy (#UBE-female-only-policy).

UBE is a female-only body, so:
  * an armature with ONLY a male 3rd-person model (MOD2, no MOD3) must come out
    with a FEMALE model (MOD3) pointing at the converted male mesh, so a female
    UBE actor renders it instead of nothing (the male-only invisibility class);
  * female availability (DNAM Female Priority) is forced non-zero;
  * BUT the synthesised female model is only emitted when the converted !UBE
    mesh actually exists -- never point an ARMA at a missing NIF (load CTD).
"""
import sys
import struct
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.esp import encode_subrecord, encode_zstring, iter_subrecords
from src.ube_patcher import rebuild_arma_payload, _force_female_priority


def _by_sig(payload):
    out = {}
    for s, d in iter_subrecords(payload):
        out.setdefault(s, []).append(d)
    return out


def test_force_female_priority():
    # byte0=male prio, byte1=female prio. Female must end >= male and >= 1.
    assert _force_female_priority(bytes([5, 0, 2, 2] + [0] * 8))[1] == 5   # 0 -> 5
    assert _force_female_priority(bytes([0, 0]))[1] == 1                   # both 0 -> 1
    assert _force_female_priority(bytes([3, 9]))[1] == 9                   # already higher -> kept
    assert _force_female_priority(b"\x01") == b"\x01"                      # too short -> untouched


def test_male_only_arma_gets_female_model_and_priority():
    male = "Armor\\Foo\\foo_m_1.nif"
    payload = (
        encode_subrecord(b"EDID", encode_zstring("FooAA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"DNAM", bytes([5, 0, 2, 2] + [0] * 8))   # female prio 0
        + encode_subrecord(b"MOD2", encode_zstring(male))           # male-only, no MOD3
    )
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734, new_additional_race_fids=[0x19000801],
        converted_nif_exists=lambda p: True)
    by = _by_sig(out)
    assert b"MOD3" in by, "female model not synthesised from male-only ARMA"
    assert by[b"MOD3"][0].rstrip(b"\x00").decode() == "!UBE\\" + male
    assert by[b"DNAM"][0][1] >= max(by[b"DNAM"][0][0], 1)          # female priority forced


def test_male_only_unconverted_no_synthesised_model():
    # No converted male mesh -> must NOT synthesise a MOD3 (would be a missing
    # !UBE NIF -> load CTD). The male model also stays un-prefixed.
    payload = (
        encode_subrecord(b"EDID", encode_zstring("FooAA"))
        + encode_subrecord(b"MOD2", encode_zstring("Armor\\Foo\\foo_m_1.nif"))
    )
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734, new_additional_race_fids=[],
        converted_nif_exists=lambda p: False)
    assert b"MOD3" not in _by_sig(out)


def test_existing_female_model_not_duplicated():
    # An ARMA that already has a female model keeps exactly one (no synth dup).
    payload = (
        encode_subrecord(b"MOD2", encode_zstring("a\\m_1.nif"))
        + encode_subrecord(b"MOD3", encode_zstring("a\\f_1.nif"))
    )
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734, new_additional_race_fids=[],
        converted_nif_exists=lambda p: True)
    assert len(_by_sig(out).get(b"MOD3", [])) == 1


def test_helmet_mint_ube_primary_keeps_vanilla_path():
    # #132: a vanilla helmet ARMA minted as a UBE-primary variant must set RNAM
    # to the new primary AND keep the ORIGINAL (vanilla) model path when the mesh
    # isn't converted (converted_nif_exists -> False) -- helmets aren't mesh-
    # converted, so the UBE-primary ARMA points at the real vanilla mesh (no
    # missing-!UBE CTD). This is what makes a vanilla helmet render on a UBE actor.
    helmet = "Armor\\Studded\\Female\\helmet_1.nif"
    payload = (
        encode_subrecord(b"EDID", encode_zstring("StuddedHelmetAA"))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))   # DefaultRace
        + encode_subrecord(b"MOD2", encode_zstring("Armor\\Studded\\Male\\helmet_1.nif"))
        + encode_subrecord(b"MOD3", encode_zstring(helmet))
    )
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734,
        new_additional_race_fids=[0x19000801, 0x19000802],
        converted_nif_exists=lambda p: False, path_prefix="!UBE\\")
    by = _by_sig(out)
    assert struct.unpack("<I", by[b"RNAM"][0])[0] == 0x19005734    # RNAM -> UBE primary
    assert by[b"MOD3"][0].rstrip(b"\x00").decode() == helmet       # vanilla path kept (no !UBE)
    assert len(by.get(b"MODL", [])) == 2                           # UBE additional races added


def test_unconverted_female_model_redirected_to_converted_male():
    # #174 (Penitus invisible): the MALE model converts (!UBE mesh exists) but
    # the female model did NOT (its mesh name doesn't match the converted one /
    # is a dead path). UBE is female-only, so the female actor uses MOD3 -> dead
    # path -> INVISIBLE. Fix: redirect MOD3 to the converted male mesh and drop
    # its now-mismatched texture hash, so the female actor always renders a valid
    # converted mesh.
    payload = (
        encode_subrecord(b"EDID", encode_zstring("PenitusCuirassAA"))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MOD2", encode_zstring("Armor\\Foo\\malearmor_1.nif"))
        + encode_subrecord(b"MOD3", encode_zstring("Armor\\GeneralTulius\\penitusF_1.nif"))
        + encode_subrecord(b"MO3T", b"\x00" * 24)   # female tex-hash for the dead mesh
    )
    conv = lambda p: "malearmor" in p.lower()        # only the male mesh converted
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734, new_additional_race_fids=[0x19000801],
        converted_nif_exists=conv, path_prefix="!UBE\\")
    by = _by_sig(out)
    assert by[b"MOD3"][0].rstrip(b"\x00").decode() == "!UBE\\Armor\\Foo\\malearmor_1.nif"
    assert len(by[b"MOD3"]) == 1                      # no duplicate from missing-MOD3 synth
    assert b"MO3T" not in by, "mismatched female tex-hash must be dropped"


def test_converted_female_model_not_redirected():
    # Guard: when the female model DID convert, keep it (don't clobber with male).
    payload = (
        encode_subrecord(b"MOD2", encode_zstring("a\\m_1.nif"))
        + encode_subrecord(b"MOD3", encode_zstring("a\\f_1.nif"))
    )
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734, new_additional_race_fids=[],
        converted_nif_exists=lambda p: True, path_prefix="!UBE\\")
    by = _by_sig(out)
    assert by[b"MOD3"][0].rstrip(b"\x00").decode() == "!UBE\\a\\f_1.nif"  # female kept


def test_ensure_female_false_is_legacy_behaviour():
    # With the policy off, a male-only ARMA is NOT given a female model.
    payload = encode_subrecord(b"MOD2", encode_zstring("a\\m_1.nif"))
    out = rebuild_arma_payload(
        payload, new_primary_rnam=0x19005734, new_additional_race_fids=[],
        converted_nif_exists=lambda p: True, ensure_female=False)
    assert b"MOD3" not in _by_sig(out)
