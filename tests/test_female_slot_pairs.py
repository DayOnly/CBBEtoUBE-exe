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

"""#female-slot-pairs -- the female-only rule's dead-path exception is judged
per slot pair: world (female MOD3 over male MOD2) and first-person (MOD5 over
MOD4) apart.

THE DEFECT. The male models were skipped when ANY female model resolved, MOD3
and MOD5 together. An armature whose female world mesh is a dead path but whose
female first-person mesh ships never converted its male world mesh, although
the rule's own exception -- a dead female path keeps the male, so the armature
can point at a converted male -- applies to that pair. Live: 6 male world
meshes (12 files) of one clothes replacer. Nothing the old rule kept is dropped.
`CBBE2UBE_NO_FEMALE_SLOT_PAIRS=1` judges the pairs together again.

#female-slot-absent (OFF, a policy call): with
`CBBE2UBE_FEMALE_SLOT_ABSENT_KEEPS_MALE=1` a pair with NO female model keeps
its male when the other pair's female is live (the engine draws that male).
"""
import struct

import pytest

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.auto_convert import _player_armor_mesh_bases

OFF = "CBBE2UBE_NO_FEMALE_SLOT_PAIRS"
ABSENT = "CBBE2UBE_FEMALE_SLOT_ABSENT_KEEPS_MALE"
DEFAULT_RACE = 0x00000019
BODY = 1 << 2

MW = "armor/x/bobworld_1.nif"      # male world (MOD2)
FW = "armor/x/aliceworld_1.nif"    # female world (MOD3)
M1 = "armor/x/bobarms_1.nif"       # male first-person (MOD4)
F1 = "armor/x/alicearms_1.nif"     # female first-person (MOD5)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(ABSENT, raising=False)


def _mod(tmp_path, **models):
    payload = (encode_subrecord(b"EDID", encode_zstring("CuirassAA"))
               + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE)))
    for sig in ("MOD2", "MOD3", "MOD4", "MOD5"):
        if sig in models:
            payload += encode_subrecord(sig.encode(), encode_zstring(models[sig]))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                     version_unk=0x2C, payload=payload)
    mod = tmp_path / "Mod"
    mod.mkdir()
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(mod / "Mod.esp")
    return mod


def _bases(mod, live):
    """Names of the planned meshes; `live` = the stems that resolve."""
    got = _player_armor_mesh_bases(
        mod, mesh_resolves=lambda b: b.rsplit("/", 1)[-1] in live)
    return sorted(b.rsplit("/", 1)[-1] for b in got)


FULL = dict(MOD2=MW, MOD3=FW, MOD4=M1, MOD5=F1)


def test_a_dead_female_world_mesh_keeps_the_male_world_mesh(tmp_path):
    """The reported case: MOD3 dead, MOD5 live -> MOD2 converts, MOD4 does not."""
    assert _bases(_mod(tmp_path, **FULL), {"alicearms", "bobworld", "bobarms"}) == [
        "alicearms", "aliceworld", "bobworld"]


def test_a_dead_female_first_person_mesh_keeps_the_male_one(tmp_path):
    assert _bases(_mod(tmp_path, **FULL), {"aliceworld", "bobworld", "bobarms"}) == [
        "alicearms", "aliceworld", "bobarms"]


def test_live_female_meshes_still_skip_both_males(tmp_path):
    assert _bases(_mod(tmp_path, **FULL), {"aliceworld", "alicearms"}) == [
        "alicearms", "aliceworld"]


def test_all_female_meshes_dead_keeps_every_male_as_before(tmp_path):
    assert _bases(_mod(tmp_path, **FULL), set()) == [
        "alicearms", "aliceworld", "bobarms", "bobworld"]


def test_every_female_dead_keeps_a_male_even_without_its_pairs_female(tmp_path):
    """No MOD5 and a dead MOD3: the old rule kept both males; still so."""
    mod = _mod(tmp_path, MOD2=MW, MOD3=FW, MOD4=M1)
    assert _bases(mod, {"bobworld", "bobarms"}) == ["aliceworld", "bobarms", "bobworld"]


def test_a_missing_female_first_person_model_changes_nothing(tmp_path):
    """No MOD5: the old answer (female world only) stands -- the absent-pair
    rule is off."""
    mod = _mod(tmp_path, MOD2=MW, MOD3=FW, MOD4=M1)
    assert _bases(mod, {"aliceworld", "bobworld", "bobarms"}) == ["aliceworld"]


def test_switched_off_the_pairs_are_judged_together(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    assert _bases(_mod(tmp_path, **FULL), {"alicearms", "bobworld", "bobarms"}) == [
        "alicearms", "aliceworld"]


def test_opted_in_a_pair_without_a_female_model_keeps_its_male(tmp_path, monkeypatch):
    monkeypatch.setenv(ABSENT, "1")
    mod = _mod(tmp_path, MOD2=MW, MOD3=FW, MOD4=M1)
    assert _bases(mod, {"aliceworld", "bobworld", "bobarms"}) == [
        "aliceworld", "bobarms"]
    # ...and never without the pairs rule.
    monkeypatch.setenv(OFF, "1")
    assert _bases(mod, {"aliceworld", "bobworld", "bobarms"}) == ["aliceworld"]
