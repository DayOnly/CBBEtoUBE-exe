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

"""`_discover_ube_races` must return only races a plugin ORIGINATES, never its
OVERRIDES of another plugin's races. Attributing an override to the overriding
plugin's master byte ships a DANGLING race ref (the "FExxxxxx <Could not be
resolved>" bug) and duplicates the originating plugin's race (usually a
UBE_AllRace race already covered by UBE_RACE_FIDS_24). #ube-race-override
"""
import src.ube_patcher as up
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring


def _race(formid, edid):
    return Record(sig=b"RACE", flags=0, formid=formid, timestamp_vc=0,
                  version_unk=0x002C,
                  payload=encode_subrecord(b"EDID", encode_zstring(edid)))


def _plugin(path, masters, records):
    ESP(header=TES4Header(masters=masters, num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[Group(label=b"RACE", records=records)]).save(path)


def test_override_races_are_skipped(tmp_path):
    masters = ["Skyrim.esm"]
    own = len(masters)                       # own top byte == 1
    _plugin(tmp_path / "z_racepatch.esp", masters, [
        _race((own << 24) | 0x800, "00UBE_CustomTestRace"),  # ORIGINATED -> keep
        _race((0 << 24) | 0x5734, "00UBE_BretonRace"),       # OVERRIDE (byte 0) -> skip
    ])
    up._UBE_RACES_CACHE.clear()
    res = up._discover_ube_races([tmp_path])
    edids = [e for _, _, e in res]
    assert "00UBE_CustomTestRace" in edids          # originated race kept
    assert "00UBE_BretonRace" not in edids          # override dropped
    up._UBE_RACES_CACHE.clear()


def test_originated_race_with_hardcoded_low_still_discovered(tmp_path):
    # A plugin that ORIGINATES its own race (own top byte) is kept even if its
    # low id happens to collide with a hardcoded value -- the emission guard (not
    # discovery) handles hardcoded-low dedup; discovery only drops overrides.
    masters = ["Skyrim.esm"]
    own = len(masters)
    _plugin(tmp_path / "z_ownrace.esp", masters, [
        _race((own << 24) | 0x5A200, "00UBE_ArgonianCustomRace"),
    ])
    up._UBE_RACES_CACHE.clear()
    res = up._discover_ube_races([tmp_path])
    assert "00UBE_ArgonianCustomRace" in [e for _, _, e in res]
    up._UBE_RACES_CACHE.clear()
