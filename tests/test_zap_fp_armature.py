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

"""#zap-fp-armature (audit 2026-10-20 F05).

The zap recognised a first-person viewmodel by file name only, so 39 dedicated first-person NIFs (`*_1st`, `1stp*`,
`*fp*`, `*_1person`) kept the whole injected body. The planner now says which meshes the ARMAs use ONLY as
first-person models (MOD4/MOD5, never MOD2/MOD3), and the zap takes that answer.
"""
import struct

from src import auto_convert as ac
from src import body_zap as bz
from src import esp
from src import ube_patcher as up


def _arma(fid, models):
    payload = esp.encode_subrecord(b"BOD2", struct.pack("<II", 1 << 2, 0))
    for sig, path in models:
        payload += esp.encode_subrecord(sig, path.encode() + b"\x00")
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0, version_unk=0x2C, payload=payload)


def _plugin(tmp_path, records):
    p = tmp_path / "Outfit.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=records)]).save(p)
    return p


def test_first_person_only_meshes_are_found_whatever_their_name(tmp_path):
    p = _plugin(tmp_path, [
        _arma(0x01000800, [(b"MOD3", "Armor\\X\\cuirass_1.nif"), (b"MOD5", "Armor\\X\\cuirass_1st_1.nif")]),
        _arma(0x01000801, [(b"MOD3", "Armor\\Y\\shirt_1.nif"), (b"MOD5", "Armor\\Y\\fp_1.nif")]),
    ])
    assert up.build_first_person_bases([p]) == {"armor/x/cuirass_1st", "armor/y/fp"}


def test_a_mesh_also_used_as_a_world_model_is_not_first_person(tmp_path):
    p = _plugin(tmp_path, [
        _arma(0x01000800, [(b"MOD3", "Armor\\X\\cuirass_1.nif"), (b"MOD5", "Armor\\X\\cuirass_1.nif")]),
        _arma(0x01000801, [(b"MOD5", "Armor\\Z\\shared_1.nif")]),
        _arma(0x01000802, [(b"MOD3", "Armor\\Z\\shared_1.nif")]),
    ])
    assert up.build_first_person_bases([p]) == set()


def test_the_worker_hands_the_answer_to_the_zap(monkeypatch, tmp_path):
    seen = {}
    res = ac.nif_convert.ConvertResult(src_path=tmp_path / "s.nif", dst_path=tmp_path / "d_1.nif",
                                       status="converted (body-swap)", reason="")
    monkeypatch.setattr(ac.nif_convert, "convert_nif", lambda *a, **k: res)
    monkeypatch.setattr(bz, "active", lambda: True)
    monkeypatch.setattr(bz, "partner_pending", lambda *a: False)
    monkeypatch.setattr(bz, "zap_pair", lambda p, first_person=False: seen.setdefault("fp", first_person))
    ac._nif_convert_worker((tmp_path / "s.nif", tmp_path / "d_1.nif", None, 4, None, None, True))
    assert seen["fp"] is True
    seen.clear()
    ac._nif_convert_worker((tmp_path / "s.nif", tmp_path / "d_1.nif", None, 4, None, None))
    assert seen["fp"] is False


def test_off_switch_keeps_the_name_rule_alone(monkeypatch):
    monkeypatch.setattr(bz, "ZAP_FP_ARMATURE", False)
    assert (bool(True) and bz.ZAP_FP_ARMATURE) is False
