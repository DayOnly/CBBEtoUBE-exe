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

"""#third-party-ini-slot-check -- another mod's SkyPatcher line that adds a
UBE armature hides its target from coverage only when that armature will
actually be drawn over the whole armour.

THE DEFECT. A line adding a `!UBE\\` armature excluded every target outright. A
cape-only UBE addon on a cuirass hid the cuirass (UBE actors drew the cape and
no torso), and an addon from a plugin left unchecked in the load order hid its
targets too, although SkyPatcher adds nothing for it. The race test beside it
already required the addons to cover every slot of the armour; now both kinds
of UBE addon go through that test, and only addons whose plugin is loaded count.

Measured on the live modlist: 11 such lines, all loaded and slot-complete --
0 armours move."""
import argparse
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring

UBE_RACE = 0x005734          # UBE_AllRace.esp primary race (low 24)
DEFAULT = 0x000019           # Skyrim.esm DefaultRace
BODY = 1 << 2                # slot 32
HANDS = 1 << 3               # slot 33
CAPE = 1 << 16               # slot 46
OFF = "CBBE2UBE_NO_THIRD_PARTY_INI_SLOT_CHECK"
TARGET = ("owner.esp", 0x801)


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(ac, "_UBE_COVERED_CACHE", {})
    monkeypatch.setattr(ac, "_UBE_COVERED_BY_MOD", {})


def _save(path, masters, groups):
    path.parent.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=list(masters), num_records=0,
                          next_object_id=0x900, version=1.7, flags=0),
        groups=groups).save(path)


def _arma(formid, race_fid, slots, mod3):
    p = encode_subrecord(b"EDID", encode_zstring("PatchAA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", race_fid))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots):
    p = encode_subrecord(b"EDID", encode_zstring("OwnedPiece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=p)


def _modlist(root, *, addons, target_slots=BODY, define_target=True,
             target="Owner.esp|801"):
    """An owner mod defining armour 0x801 (Owner.esp), and a patch mod whose
    Patch.esp defines the addons given as {formid: (slots, mod3, race)} and
    whose INI adds all of them to `target` on one line."""
    own = root / "Owner Mod"
    _save(own / "Owner.esp", ["Skyrim.esm"],
          [Group(label=b"ARMO", records=[_armo(0x01000801, target_slots)])]
          if define_target else [])
    pat = root / "Patch UBE"
    recs = []
    for fid, (slots, mod3, race) in sorted(addons.items()):
        race_ref = ((1 << 24) | race) if race == UBE_RACE else race
        recs.append(_arma(0x02000000 | fid, race_ref, slots, mod3))
        mesh = pat / "meshes" / mod3.replace("\\", "/")
        mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh.write_bytes(b"presence is all the check reads")
    _save(pat / "Patch.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
          [Group(label=b"ARMA", records=recs)])
    ini_dir = pat / "SKSE/Plugins/SkyPatcher/armor"
    ini_dir.mkdir(parents=True, exist_ok=True)
    adds = ",".join(f"Patch.esp|{fid:X}" for fid in sorted(addons))
    (ini_dir / "Patch.ini").write_text(
        f"filterByArmors={target}:armorAddonsToAdd={adds}\n", encoding="utf-8")


def _ube(slots, name="piece"):
    return (slots, rf"!UBE\patch\{name}_1.nif", DEFAULT)


def _covered(root, **k):
    return ac._third_party_ube_covered_armos(root, **k)


# ------------------------------------------------------------------ the slots

def test_a_cape_only_ube_addon_does_not_hide_the_cuirass(tmp_path):
    """The reported shape: a UBE cape added to a cuirass. The cuirass still
    needs its torso armature on UBE actors."""
    _modlist(tmp_path, addons={0x800: _ube(CAPE, "cape")})
    assert TARGET not in _covered(tmp_path)


def test_a_ube_addon_covering_the_armour_still_hides_it(tmp_path):
    """Negative control: the same patch with a body addon is a whole UBE refit."""
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET in _covered(tmp_path)


def test_addons_on_one_line_add_up_their_slots(tmp_path):
    """A refit may attach a body and a hands armature to a two-slot piece."""
    _modlist(tmp_path, target_slots=BODY | HANDS,
             addons={0x800: _ube(BODY), 0x801: _ube(HANDS, "hands")})
    assert TARGET in _covered(tmp_path)


def test_one_slot_short_keeps_the_armour_covered(tmp_path):
    _modlist(tmp_path, target_slots=BODY | HANDS, addons={0x800: _ube(BODY)})
    assert TARGET not in _covered(tmp_path)


def test_the_armour_slots_are_read_with_patch_recognition_off(
        tmp_path, monkeypatch):
    """Each switch turns off only its own feature: with the nested-INI and race
    recognition off, a whole UBE refit on a flat INI still hides its target."""
    monkeypatch.setenv("CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION", "1")
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET in _covered(tmp_path)


def test_an_armour_no_plugin_defines_stays_covered(tmp_path):
    """Its slots cannot be read (vanilla, not overridden): the safe direction is
    to keep covering it."""
    _modlist(tmp_path, addons={0x800: _ube(BODY)}, define_target=False)
    assert TARGET not in _covered(tmp_path)


def test_switched_off_any_ube_addon_hides_its_target(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _modlist(tmp_path, addons={0x800: _ube(CAPE, "cape")})
    assert TARGET in _covered(tmp_path)


def test_switched_off_an_undefined_armour_is_hidden_too(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _modlist(tmp_path, addons={0x800: _ube(BODY)}, define_target=False)
    assert TARGET in _covered(tmp_path)


# ------------------------------------------------------------ the load order

def test_an_addon_from_an_unloaded_plugin_does_not_count(tmp_path):
    """SkyPatcher skips an addon whose plugin is not loaded: nothing is drawn."""
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET not in _covered(tmp_path, active_plugins=["Owner.esp"])


def test_an_addon_from_a_loaded_plugin_counts(tmp_path):
    """Negative control: loaded (any case), the same patch hides its target."""
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET in _covered(tmp_path, active_plugins=["Owner.esp", "PATCH.ESP"])


def test_an_unknown_load_order_is_not_checked(tmp_path):
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET in _covered(tmp_path, active_plugins=None)


def test_a_ube_race_addon_from_an_unloaded_plugin_does_not_count(tmp_path):
    """The race test's addons are added by SkyPatcher the same way."""
    _modlist(tmp_path, addons={0x800: (BODY, r"owner\f\piece_1.nif", UBE_RACE)})
    assert TARGET in _covered(tmp_path, active_plugins=["patch.esp"])
    ac._UBE_COVERED_CACHE.clear()
    assert TARGET not in _covered(tmp_path, active_plugins=["owner.esp"])


def test_switched_off_an_unloaded_addon_still_counts(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET in _covered(tmp_path, active_plugins=["Owner.esp"])


def test_the_load_order_keys_the_cache(tmp_path):
    """The long-lived GUI process can change the load order between two scans."""
    _modlist(tmp_path, addons={0x800: _ube(BODY)})
    assert TARGET in _covered(tmp_path, active_plugins=["patch.esp"])
    assert TARGET not in _covered(tmp_path, active_plugins=["owner.esp"])


# ------------------------------------------------------------------ callers

def test_the_coverage_step_hands_over_the_load_order(tmp_path, monkeypatch):
    from tests.test_coverage_third_party_drawn import _emit
    seen, _m, _t = _emit(tmp_path, monkeypatch)
    assert seen["active"] == [["X UBE patch.esp"]]


def test_the_conversion_planner_hands_over_the_load_order(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.delenv("CBBE2UBE_MODS_ROOT", raising=False)
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda *a, **k: ac.paths.Layout())
    monkeypatch.setattr(ac.paths, "mods_root", lambda *a, **k: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered",
                        lambda lay: ["Loaded.esp"])
    got = []

    def _record(*a, **k):
        got.append(k.get("active_plugins"))
        return set()
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", _record)
    monkeypatch.setattr(ac, "auto_convert_mod", lambda source_dir, *a, **k:
                        ac.AutoConvertResult(source_dir=Path(source_dir),
                                             output_dir=tmp_path / "out"))
    src = tmp_path / "SomeMod"
    src.mkdir()
    ref = tmp_path / "femalebody_1.nif"
    ref.write_bytes(b"")
    ac._cmd_convert(argparse.Namespace(
        sources=[src], output=tmp_path / "out", esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=ref,
        workers=1, unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False))
    assert got and got[0] == ["Loaded.esp"]
