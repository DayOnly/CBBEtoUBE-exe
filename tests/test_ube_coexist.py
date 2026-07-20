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

"""Coexistence with armors another mod ALREADY patched for UBE.

Adding our armature to an armor that already has a UBE armature makes the actor
render two bodies for that slot (z-fighting / doubled cloth), and a hand-made
UBE patch is a better fit than an automatic conversion anyway. So we detect
existing coverage and leave those armors entirely alone.

The hard part is not detection, it is not mistaking OUR OWN output for a
third-party patch -- see test_legacy_output_from_an_older_build_is_still_ours
for what that costs.
"""
import struct
from pathlib import Path
from src import auto_convert, esp


def _plugin(path, arma_model, armo_refs_arma=True, masters=("Skyrim.esm",)):
    """A mod plugin defining one ARMA (given model path) + one ARMO using it."""
    arma = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                      version_unk=0,
                      payload=esp.encode_subrecord(b"MOD3",
                                                   arma_model.encode() + bytes(1)))
    modl = esp.encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
    armo = esp.Record(sig=b"ARMO", flags=0, formid=0x00012E46, timestamp_vc=0,
                      version_unk=0,
                      payload=(esp.encode_subrecord(b"EDID", b"TestArmor" + bytes(1))
                               + (modl if armo_refs_arma else b"")))
    e = esp.ESP(header=esp.TES4Header(masters=list(masters)),
                groups=[esp.Group(label=b"ARMO", records=[armo]),
                        esp.Group(label=b"ARMA", records=[arma])])
    e.save(path)


def test_detects_an_armor_another_mod_already_patched_for_ube(tmp_path):
    mod = tmp_path / "SomeArmor UBE"
    mod.mkdir()
    _plugin(mod / "p.esp", r"!UBE\armor\some\cuirass_1.nif")
    got = auto_convert._third_party_ube_covered_armos(tmp_path)
    assert ("skyrim.esm", 0x012E46) in got, got


def test_ignores_a_normal_cbbe_mod(tmp_path):
    mod = tmp_path / "SomeArmor 3BA"
    mod.mkdir()
    _plugin(mod / "p.esp", r"armor\some\cuirass_1.nif")
    assert auto_convert._third_party_ube_covered_armos(tmp_path) == set()


def test_ignores_a_ube_arma_no_armo_points_at(tmp_path):
    """A UBE ARMA nothing references covers no armor, so it must not suppress."""
    mod = tmp_path / "Orphan UBE"
    mod.mkdir()
    _plugin(mod / "p.esp", r"!UBE\armor\x_1.nif", armo_refs_arma=False)
    assert auto_convert._third_party_ube_covered_armos(tmp_path) == set()


def test_detects_skypatcher_delivery_too(tmp_path):
    """Another converter covers via armorAddonsToAdd, not ARMO records."""
    ini = tmp_path / "Other UBE" / "SKSE/Plugins/SkyPatcher/armor"
    ini.mkdir(parents=True)
    (ini / "x.ini").write_text(
        "filterByArmors=Skyrim.esm|0209A6,Other.esp|000001:"
        "armorAddonsToAdd=Other.esp|000800\n", encoding="utf-8")
    got = auto_convert._third_party_ube_covered_armos(tmp_path)
    assert ("skyrim.esm", 0x0209A6) in got
    assert ("other.esp", 0x000001) in got


def test_skip_mods_is_honoured(tmp_path):
    """Our own output must never count as third-party coverage."""
    mod = tmp_path / "CBBEtoUBE Auto"
    mod.mkdir()
    _plugin(mod / "p.esp", r"!UBE\armor\some\cuirass_1.nif")
    assert auto_convert._third_party_ube_covered_armos(
        tmp_path, skip_mods={"CBBEtoUBE Auto"}) == set()


def test_disabled_mods_do_not_count(tmp_path):
    mod = tmp_path / "Disabled UBE"
    mod.mkdir()
    _plugin(mod / "p.esp", r"!UBE\armor\x_1.nif")
    assert auto_convert._third_party_ube_covered_armos(
        tmp_path, enabled_names={"Something Else"}) == set()


def test_our_own_output_is_never_third_party(tmp_path):
    """A leftover output mod from an earlier run sits in the modlist under a
    USER-CHOSEN name, so name-based skipping cannot catch it. Reading it as a
    third-party provider would suppress our own coverage wholesale."""
    old = tmp_path / "Some Old Converted Output"
    ini = old / "SKSE/Plugins/SkyPatcher/armor"
    ini.mkdir(parents=True)
    (ini / "CBBE_to_UBE_Combined.ini").write_text(
        f"; {auto_convert.SKYPATCHER_INI_MARKER} FULL SKYPATCHER: adds each converted\n"
        "filterByArmors=Skyrim.esm|0209A6:armorAddonsToAdd=X.esp|000DEA\n",
        encoding="utf-8")
    assert auto_convert._is_our_own_output(old)
    assert auto_convert._third_party_ube_covered_armos(tmp_path) == set()


def test_a_real_third_party_ini_without_our_marker_still_counts(tmp_path):
    """The self-check must key on OUR marker only -- not on 'has a SkyPatcher
    INI', which every other patcher mod also has."""
    other = tmp_path / "Another Patcher"
    ini = other / "SKSE/Plugins/SkyPatcher/armor"
    ini.mkdir(parents=True)
    (ini / "z.ini").write_text(
        "; some other tool\n"
        "filterByArmors=Skyrim.esm|0209A6:armorAddonsToAdd=X.esp|000DEA\n",
        encoding="utf-8")
    assert not auto_convert._is_our_own_output(other)
    assert ("skyrim.esm", 0x0209A6) in \
        auto_convert._third_party_ube_covered_armos(tmp_path)


def test_legacy_output_from_an_older_build_is_still_ours(tmp_path):
    """The self-check must not depend on the CURRENT header marker: a modlist
    can hold output from an OLDER build that stamped a different one. Measured
    on a real modlist -- one such stale mod pushed the exclusion set from 336 to
    8873 and would have suppressed 8318 of 10056 links, i.e. converted
    everything and delivered almost none of it. The structural report file is
    what catches output older than any marker."""
    old = tmp_path / "Some Older Output"
    ini = old / "SKSE/Plugins/SkyPatcher/armor"
    ini.mkdir(parents=True)
    (ini / "x.ini").write_text(
        "; Generated by a previous build -- keep-tier coverage\n"
        "filterByArmors=Skyrim.esm|0209A6:armorAddonsToAdd=X.esp|000800\n",
        encoding="utf-8")
    (old / "conversion_report.json").write_text("{}", encoding="utf-8")

    assert auto_convert._is_our_own_output(old)
    assert auto_convert._third_party_ube_covered_armos(tmp_path) == set()


def test_per_source_text_reports_also_identify_our_output(tmp_path):
    """Output whose JSON report was deleted still carries the per-source text
    reports, so those identify it too."""
    old = tmp_path / "Output Without Json"
    old.mkdir()
    (old / "conversion_report_Some Source Mod.txt").write_text(
        "x", encoding="utf-8")
    assert auto_convert._is_our_own_output(old)


def test_a_hand_made_ube_patch_is_not_mistaken_for_our_output(tmp_path):
    """The point of the feature: a hand-made UBE patch must be RESPECTED, so it
    must never trip the self-check and get scanned as convertible."""
    mod = tmp_path / "Handmade UBE Patch"
    mod.mkdir()
    _plugin(mod / "p.esp", r"!UBE\armor\x_1.nif")
    assert not auto_convert._is_our_own_output(mod)
    assert ("skyrim.esm", 0x012E46) in \
        auto_convert._third_party_ube_covered_armos(tmp_path)


# ---- the WIRING, not just the detector ------------------------------------
# Detection is worthless if the result never reaches the coverage generators.
# Proven necessary by mutation: reverting either `exclude_armo_abs=_ube_excl`
# to `None` left all 804 other tests passing while the feature was dead.

def test_detected_exclusions_reach_both_coverage_generators(tmp_path,
                                                            monkeypatch):
    sentinel = {("skyrim.esm", 0x0209A6)}
    seen = {}

    def _fake_nonbody(out, ordered, **kw):
        seen["nonbody"] = kw.get("exclude_armo_abs")
        return {"armo_targets": 1}

    def _fake_body(out, ordered, **kw):
        seen["body"] = kw.get("exclude_armo_abs")
        return {"armo_targets": 1}

    monkeypatch.setattr(auto_convert, "_third_party_ube_covered_armos",
                        lambda *a, **k: sentinel)
    monkeypatch.setattr(auto_convert.ube_patcher,
                        "generate_modded_nonbody_ube_coverage_patch",
                        _fake_nonbody)
    monkeypatch.setattr(auto_convert.ube_patcher,
                        "generate_modded_body_ube_coverage_patch", _fake_body)
    monkeypatch.setattr(auto_convert.paths, "discover_layout", lambda: None)
    monkeypatch.setattr(auto_convert.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(auto_convert.paths, "mods_root", lambda: str(tmp_path))
    monkeypatch.setattr(auto_convert.paths, "active_plugins_ordered",
                        lambda lay: ["Skyrim.esm"])
    plug = tmp_path / "Skyrim.esm"
    plug.write_bytes(b"")
    monkeypatch.setattr(auto_convert.paths, "plugin_file_index",
                        lambda lay: {"skyrim.esm": str(plug)})

    out = tmp_path / "out"
    patches = tmp_path / "patches"
    ube = out / "meshes" / "!UBE"
    ube.mkdir(parents=True)
    # the BODY pass only runs when converted meshes exist, so give it one --
    # otherwise this test silently only covers the non-body generator
    (ube / "armor" / "x").mkdir(parents=True)
    (ube / "armor" / "x" / "cuirass_1.nif").write_bytes(b"")
    patches.mkdir()
    auto_convert._emit_unified_coverage_patches(
        out, patches, None, "CBBE_to_UBE_Combined.esp")

    assert seen.get("nonbody") == sentinel, (
        "non-body coverage did not receive the already-UBE exclusions")
    assert seen.get("body") == sentinel, (
        "body coverage did not receive the already-UBE exclusions")
