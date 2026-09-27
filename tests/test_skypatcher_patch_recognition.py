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

"""#skypatcher-patch-recognition -- a SkyPatcher-delivered UBE refit is
recognised whole, so its armour is left alone.

THE DEFECT. A follower's hand-made UBE refit attaches its armatures with an INI
in a SUBFOLDER, `SkyPatcher/armor/<plugin>/<plugin>.esp.ini` -- the layout
SkyPatcher recommends for a plugin-named INI. The check globbed `armor/*.ini`,
so 0 of its 11 lines were read and every piece was covered twice in game. Two of
those lines add armatures that keep the source mesh on the UBE races (a helmet
and a wig need no refit), so no `!UBE\\` path test could ever see them.

Measured on the live modlist: the recursive read finds 9 of the 11, the race
test the last 2, and neither moves any other armour.
"""
import struct
from pathlib import Path

from src import auto_convert as ac
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring

UBE_RACE = 0x005734          # UBE_AllRace.esp primary race (low 24)
DEFAULT = 0x000019           # Skyrim.esm DefaultRace
FEET = 1 << 7                # slot 37
HEAD = 1 << 12               # slot 42
CAPE = 1 << 16               # slot 46
OFF = "CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION"


def _save(path, masters, groups):
    path.parent.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=list(masters), num_records=0,
                          next_object_id=0x900, version=1.7, flags=0),
        groups=groups).save(path)


def _arma(formid, race_fid, slots, mod3):
    p = encode_subrecord(b"EDID", encode_zstring("RefitAA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", race_fid))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots):
    p = encode_subrecord(b"EDID", encode_zstring("FollowerPiece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=p)


def _modlist(root, *, addon_slots, target_slots, mod3, race=UBE_RACE,
             ship_mesh=True, nested=True, define_target=True):
    """A follower mod defining armour 0x801 in Follower.esp, and a refit mod whose
    Refit.esp (masters Skyrim.esm, UBE_AllRace.esp) defines addon 0x800 and whose
    INI adds it to the follower's armour."""
    fol = root / "Follower Mod"
    _save(fol / "Follower.esp", ["Skyrim.esm"],
          [Group(label=b"ARMO", records=[_armo(0x01000801, target_slots)])]
          if define_target else [])
    ref = root / "Refit UBE"
    race_master = 1 if race == UBE_RACE else 0
    _save(ref / "Refit.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
          [Group(label=b"ARMA", records=[
              _arma(0x02000800, (race_master << 24) | race, addon_slots, mod3)])])
    if ship_mesh:
        mesh = ref / "meshes" / mod3.replace("\\", "/")
        mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh.write_bytes(b"presence is all the check reads")
    ini_dir = ref / "SKSE/Plugins/SkyPatcher/armor"
    if nested:
        ini_dir = ini_dir / "Refit"
    ini_dir.mkdir(parents=True, exist_ok=True)
    (ini_dir / "Refit.esp.ini").write_text(
        "filterByArmors=Follower.esp|801:armorAddonsToAdd=Refit.esp|800\n",
        encoding="utf-8")
    return ("follower.esp", 0x801)


def test_a_nested_ini_is_read(tmp_path):
    """The reported layout: the INI in `armor/<plugin>/`, adding a `!UBE\\` refit."""
    t = _modlist(tmp_path, addon_slots=FEET, target_slots=FEET,
                 mod3=r"!UBE\refit\boots_1.nif")
    assert t in ac._third_party_ube_covered_armos(tmp_path)


def test_the_off_switch_reads_armor_ini_only(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    t = _modlist(tmp_path, addon_slots=FEET, target_slots=FEET,
                 mod3=r"!UBE\refit\boots_1.nif")
    assert t not in ac._third_party_ube_covered_armos(tmp_path)


def test_a_flat_ini_is_still_read_with_the_switch_off(tmp_path, monkeypatch):
    """Negative control for the test above: switched off, a flat INI still counts,
    so the off-switch test fails for the right reason."""
    monkeypatch.setenv(OFF, "1")
    t = _modlist(tmp_path, addon_slots=FEET, target_slots=FEET,
                 mod3=r"!UBE\refit\boots_1.nif", nested=False)
    assert t in ac._third_party_ube_covered_armos(tmp_path)


def test_a_ube_race_addon_with_the_source_mesh_counts(tmp_path):
    """The helmet: UBE_AllRace-primary, the source mesh, the same slot."""
    t = _modlist(tmp_path, addon_slots=HEAD, target_slots=HEAD,
                 mod3=r"follower\f\helmet_1.nif")
    assert t in ac._third_party_ube_covered_armos(tmp_path)


def test_the_race_test_is_off_with_the_switch(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    t = _modlist(tmp_path, addon_slots=HEAD, target_slots=HEAD,
                 mod3=r"follower\f\helmet_1.nif", nested=False)
    assert t not in ac._third_party_ube_covered_armos(tmp_path)


def test_a_vanilla_race_addon_does_not_count(tmp_path):
    """A cape or heels addon on the vanilla races is not a UBE patch."""
    t = _modlist(tmp_path, addon_slots=HEAD, target_slots=HEAD,
                 mod3=r"follower\f\helmet_1.nif", race=DEFAULT)
    assert t not in ac._third_party_ube_covered_armos(tmp_path)


def test_an_addon_short_of_the_armours_slots_does_not_count(tmp_path):
    """A UBE-race cape added to a helmet-and-cape armour covers one of its two
    slots; excluding the armour would leave the helmet uncovered."""
    t = _modlist(tmp_path, addon_slots=CAPE, target_slots=CAPE | HEAD,
                 mod3=r"follower\f\cape_1.nif")
    assert t not in ac._third_party_ube_covered_armos(tmp_path)


def test_an_addon_whose_mesh_is_missing_does_not_count(tmp_path):
    """SKIPPING IS THE DANGEROUS DIRECTION: an addon naming a mesh nobody ships
    draws nothing, and the armour would be invisible."""
    t = _modlist(tmp_path, addon_slots=HEAD, target_slots=HEAD,
                 mod3=r"follower\f\helmet_1.nif", ship_mesh=False)
    assert t not in ac._third_party_ube_covered_armos(tmp_path)


def test_slots_add_up_across_lines(tmp_path):
    """A patch may add a helmet-and-cape armour's two addons on two lines; the
    armour is covered when the lines together cover both slots."""
    t = _modlist(tmp_path, addon_slots=HEAD, target_slots=HEAD | CAPE,
                 mod3=r"follower\f\helmet_1.nif")
    ref = tmp_path / "Refit UBE"
    _save(ref / "Refit.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
          [Group(label=b"ARMA", records=[
              _arma(0x02000800, (1 << 24) | UBE_RACE, HEAD, r"follower\f\helmet_1.nif"),
              _arma(0x02000801, (1 << 24) | UBE_RACE, CAPE, r"follower\f\helmet_1.nif")])])
    ini = ref / "SKSE/Plugins/SkyPatcher/armor/Refit/Refit.esp.ini"
    ini.write_text("filterByArmors=Follower.esp|801:armorAddonsToAdd=Refit.esp|800\n"
                   "filterByArmors=Follower.esp|801:armorAddonsToAdd=Refit.esp|801\n",
                   encoding="utf-8")
    assert t in ac._third_party_ube_covered_armos(tmp_path)


def test_the_esp_route_keeps_the_path_test(tmp_path):
    """The race test is for SkyPatcher lines only. On the ESP route it would move
    37 other armours in the reported modlist, unverified -- an ARMO that lists a
    UBE-race addon with a non-`!UBE` mesh is still covered by us."""
    ref = tmp_path / "Refit UBE"
    arma = _arma(0x02000800, (1 << 24) | UBE_RACE, HEAD, r"follower\f\helmet_1.nif")
    armo = _armo(0x02000801, HEAD)
    armo.payload += encode_subrecord(b"MODL", struct.pack("<I", 0x02000800))
    _save(ref / "Refit.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
          [Group(label=b"ARMA", records=[arma]), Group(label=b"ARMO", records=[armo])])
    mesh = ref / "meshes" / "follower" / "f" / "helmet_1.nif"
    mesh.parent.mkdir(parents=True)
    mesh.write_bytes(b"x")
    assert ("refit.esp", 0x801) not in ac._third_party_ube_covered_armos(tmp_path)


def test_a_folder_that_cannot_be_walked_costs_only_its_own_mod(tmp_path, monkeypatch):
    """An error walking one mod's INI tree must not empty the whole exclusion set
    -- that would double-cover every third-party UBE patch."""
    import pathlib
    t = _modlist(tmp_path, addon_slots=FEET, target_slots=FEET,
                 mod3=r"!UBE\refit\boots_1.nif")
    (tmp_path / "Broken Mod").mkdir()
    real = pathlib.Path.glob

    def glob(self, pattern):
        if self.name == "Broken Mod" and "SkyPatcher" in pattern:
            raise FileNotFoundError("a path past MAX_PATH")
        return real(self, pattern)
    monkeypatch.setattr(pathlib.Path, "glob", glob)
    assert t in ac._third_party_ube_covered_armos(tmp_path)


def test_an_armour_no_plugin_defines_does_not_count(tmp_path):
    """Its slots cannot be checked, so it is left to be covered."""
    t = _modlist(tmp_path, addon_slots=HEAD, target_slots=HEAD,
                 mod3=r"follower\f\helmet_1.nif", define_target=False)
    assert t not in ac._third_party_ube_covered_armos(tmp_path)
