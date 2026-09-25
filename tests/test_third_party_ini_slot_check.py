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
0 armours move.

#third-party-ini-winner-slots: the armour's slots are read from its load-order
winner, wherever that plugin lives (the game's Data folder included). A
complete UBE refit of a vanilla armour no mod overrides read as "slots
unknown" and was covered again -- two bodies. When the winner still cannot be
read, a `!UBE\\` addon is trusted whole, as before the slot check."""
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


def test_a_cape_only_addon_is_judged_with_patch_recognition_off(
        tmp_path, monkeypatch):
    """The armour's slots are read with recognition off too: a cape-only UBE
    addon does not hide the cuirass (unread slots would trust the addon)."""
    monkeypatch.setenv("CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION", "1")
    _modlist(tmp_path, addons={0x800: _ube(CAPE, "cape")})
    assert TARGET not in _covered(tmp_path)


@pytest.mark.parametrize("override_mod", ["Override Mod", "Zz Override Mod"],
                         ids=["override_read_first", "override_read_last"])
def test_an_addon_two_plugins_read_counts_only_the_slots_both_give(
        tmp_path, override_mod):
    """Patch.esp's UBE armature is cape-only; another mod's plugin overrides it
    as body+cape. Whichever plugin is read first, the cuirass is not taken as
    covered (a union would credit the torso the winning record may not draw)."""
    _modlist(tmp_path, addons={0x800: _ube(CAPE, "cape")})
    _save(tmp_path / override_mod / "Override.esp",
          ["Skyrim.esm", "UBE_AllRace.esp", "Patch.esp"],
          [Group(label=b"ARMA", records=[
              _arma(0x02000800, DEFAULT, BODY | CAPE, r"!UBE\patch\cape_1.nif")])])
    assert TARGET not in _covered(tmp_path)


# ------------------------------------------ #third-party-ini-winner-slots

WINNER_OFF = "CBBE2UBE_NO_THIRD_PARTY_INI_WINNER_SLOTS"
VANILLA = ("skyrim.esm", 0x12E49)


def _vanilla(tmp_path, addons, slots=BODY, overrides=(), skyrim=True):
    """A modlist whose INI adds `addons` to Skyrim.esm|12E49, an armour only
    the game's Data folder defines (`slots`), plus mod plugins overriding it:
    `overrides` = [(plugin name, slots)]. -> (mods root, index, load order)."""
    mods = tmp_path / "mods"
    _modlist(mods, addons=addons, define_target=False, target="Skyrim.esm|12E49")
    data = tmp_path / "Data"
    sky = data / "Skyrim.esm"
    if skyrim:
        _save(sky, [], [Group(label=b"ARMO", records=[_armo(0x00012E49, slots)])])
    else:
        sky.parent.mkdir(parents=True, exist_ok=True)
        sky.write_bytes(b"TES4 but not a plugin")
    index = {"skyrim.esm": sky, "patch.esp": mods / "Patch UBE" / "Patch.esp",
             "owner.esp": mods / "Owner Mod" / "Owner.esp"}
    for name, o_slots in overrides:
        p = mods / f"{name} Mod" / name
        _save(p, ["Skyrim.esm"], [Group(label=b"ARMO", records=[
            _armo(0x00012E49, o_slots)])])
        index[name.lower()] = p
    order = ["Skyrim.esm", "UBE_AllRace.esp", "Owner.esp", "Patch.esp"]
    return mods, index, order


def test_a_complete_refit_of_a_vanilla_armour_hides_it(tmp_path):
    """The regression: no mod plugin defines the armour, so its slots were
    unknown and a whole UBE refit was covered again -- two bodies."""
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(BODY)})
    unchecked = []
    assert VANILLA in _covered(mods, active_plugins=order, plugin_index=index,
                               unchecked=unchecked)
    assert unchecked == []


def test_a_cape_only_refit_of_a_vanilla_armour_keeps_it_covered(tmp_path):
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(CAPE, "cape")})
    assert VANILLA not in _covered(mods, active_plugins=order, plugin_index=index)


def test_the_last_override_in_the_load_order_decides(tmp_path):
    """Two mods override the vanilla armour: the later plugin's slots count."""
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(BODY)}, slots=BODY,
                                  overrides=[("Wide.esp", BODY | HANDS),
                                             ("Narrow.esp", BODY)])
    assert VANILLA in _covered(mods, active_plugins=order + ["Wide.esp", "Narrow.esp"],
                               plugin_index=index)
    assert VANILLA not in _covered(mods, active_plugins=order + ["Narrow.esp", "Wide.esp"],
                                   plugin_index=index)


def test_an_override_that_is_not_loaded_does_not_decide(tmp_path):
    """An unchecked plugin's record is not the one the game uses."""
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(BODY)}, slots=BODY | HANDS,
                                  overrides=[("Narrow.esp", BODY)])
    assert VANILLA not in _covered(mods, active_plugins=order, plugin_index=index)
    assert VANILLA in _covered(mods, active_plugins=order + ["Narrow.esp"],
                               plugin_index=index)


def test_slots_that_cannot_be_read_keep_the_ube_addon_trusted(tmp_path):
    """No load order or index to find the winner: a `!UBE\\` addon still hides
    its target, as before the slot check -- and the run says so."""
    _modlist(tmp_path, addons={0x800: _ube(BODY)}, define_target=False)
    unchecked = []
    assert TARGET in _covered(tmp_path, unchecked=unchecked)
    assert unchecked == [TARGET]


def test_an_unreadable_winning_plugin_keeps_the_ube_addon_trusted(tmp_path):
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(CAPE, "cape")},
                                  skyrim=False)
    unchecked = []
    assert VANILLA in _covered(mods, active_plugins=order, plugin_index=index,
                               unchecked=unchecked)
    assert unchecked == [VANILLA]


def test_an_unreadable_plugin_above_the_record_is_not_skipped(tmp_path):
    """A plugin loaded after Skyrim.esm that cannot be read may hold the
    winning override: the slots are unknown, not Skyrim.esm's."""
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(CAPE, "cape")})
    broken = tmp_path / "Data" / "Broken.esp"
    broken.write_bytes(b"not a plugin at all")
    unchecked = []
    assert VANILLA in _covered(mods, active_plugins=order + ["Broken.esp"],
                               plugin_index={**index, "broken.esp": broken},
                               unchecked=unchecked)
    assert unchecked == [VANILLA]


def test_our_own_output_is_not_the_winner(tmp_path):
    """Our merged plugin may override the armour; the slots are the ones the
    third-party refit was made for."""
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(BODY)})
    ours = mods / "Our Output" / "Ours.esp"
    _save(ours, ["Skyrim.esm"], [Group(label=b"ARMO", records=[
        _armo(0x00012E49, BODY | CAPE)])])
    assert VANILLA in _covered(mods, skip_mods={"Our Output"},
                               active_plugins=order + ["Ours.esp"],
                               plugin_index={**index, "ours.esp": ours})


def test_a_race_addon_on_an_unreadable_armour_stays_covered(tmp_path):
    """The race test never trusted an addon without the armour's slots."""
    _modlist(tmp_path, addons={0x800: (BODY, r"owner\f\piece_1.nif", UBE_RACE)},
             define_target=False)
    unchecked = []
    assert TARGET not in _covered(tmp_path, unchecked=unchecked)
    assert unchecked == []


def test_switched_off_a_vanilla_armour_is_not_read(tmp_path, monkeypatch):
    """The switch restores reading enabled mods' plugins only: the refit of an
    armour none of them defines is covered again."""
    monkeypatch.setenv(WINNER_OFF, "1")
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(BODY)})
    unchecked = []
    assert VANILLA not in _covered(mods, active_plugins=order, plugin_index=index,
                                   unchecked=unchecked)
    assert unchecked == []


def test_the_plugin_index_keys_the_cache(tmp_path):
    mods, index, order = _vanilla(tmp_path, {0x800: _ube(BODY)}, slots=CAPE)
    assert VANILLA not in _covered(mods, active_plugins=order, plugin_index=index)
    wide = tmp_path / "Other" / "Skyrim.esm"
    _save(wide, [], [Group(label=b"ARMO", records=[_armo(0x00012E49, BODY)])])
    assert VANILLA in _covered(mods, active_plugins=order,
                               plugin_index={**index, "skyrim.esm": wide})


def test_the_unchecked_ones_are_reported(capsys):
    ac._print_unchecked_ube([("skyrim.esm", 0x12E49)])
    out = capsys.readouterr().out
    assert "1 of them not slot-checked" in out and "skyrim.esm|012E49" in out
    ac._print_unchecked_ube([])
    assert capsys.readouterr().out == ""


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


def test_the_coverage_step_hands_over_the_plugin_files(tmp_path, monkeypatch):
    """#third-party-ini-winner-slots: the winner's file, wherever it lives."""
    from tests.test_coverage_third_party_drawn import _emit
    seen, _m, theirs = _emit(tmp_path, monkeypatch)
    assert seen["index"] == [{"x ube patch.esp": theirs}]


def test_the_coverage_step_reports_the_unchecked_ones(tmp_path, monkeypatch, capsys):
    from tests.test_coverage_third_party_drawn import _emit
    _emit(tmp_path, monkeypatch, unchecked=[VANILLA])
    out = capsys.readouterr().out
    assert "1 armor(s) already have a UBE patch" in out
    assert "1 of them not slot-checked" in out


def test_the_conversion_planner_hands_over_the_load_order(tmp_path, monkeypatch,
                                                         capsys):
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
    files = {"loaded.esp": tmp_path / "Loaded.esp"}
    monkeypatch.setattr(ac.paths, "plugin_file_index", lambda lay: files)
    got = []

    def _record(*a, **k):
        got.append(k.get("active_plugins"))
        got.append(k.get("plugin_index"))       # #third-party-ini-winner-slots
        k["unchecked"].append(VANILLA)
        return {VANILLA}
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
    assert got[1] is files
    assert "1 of them not slot-checked" in capsys.readouterr().out
