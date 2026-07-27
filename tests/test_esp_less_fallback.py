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

"""#esp-less-fallback-only -- "convert every NIF the folder ships" is for
PLUGIN-LESS sources only.

`_resolve_armor_meshes` reads an empty `armor_bases` as "no ESP to classify by".
That is right for a loose-mesh replacer and wrong for a mod whose plugin WAS
readable, where empty means "the ESP was read and selected nothing".

Everything `_player_armor_mesh_bases` filters out lives in that difference: the
DefaultRace/RNAM gate, the body-slot allowlist, the nude-body-skin and
child-content filters, the female-only policy, and armors another mod has
already UBE-patched. Keying the guard on "no armour bases" alone inverted the
coexistence gate -- a mod whose armors were ALL third-party-covered converted its
ENTIRE meshes tree instead of nothing, with the female-only policy bypassed."""
import struct

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.auto_convert import (_find_source_esps, _player_armor_mesh_bases,
                              _skip_esp_less_fallback)

DEFAULT_RACE = 0x00000019
BODY = 1 << 2


def _arma(fid, edid, mesh):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
               + encode_subrecord(b"MOD3", encode_zstring(mesh)))
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _armo(fid, edid, arma_fid):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
               + encode_subrecord(b"FULL", encode_zstring(edid))
               + encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
               + encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0)))
    return esp.Record(sig=b"ARMO", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _mod(tmp_path, name, with_esp=True):
    mod = tmp_path / name
    (mod / "meshes" / "armor").mkdir(parents=True)
    (mod / "meshes" / "armor" / "cuirass_1.nif").write_bytes(b"nif")
    if with_esp:
        e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                    groups=[esp.Group(label=b"ARMA",
                                      records=[_arma(0x01000800, "AA",
                                                     "armor/cuirass_1.nif")]),
                            esp.Group(label=b"ARMO",
                                      records=[_armo(0x01000900, "A",
                                                     0x01000800)])])
        e.save(mod / (name.lower() + ".esp"))
    return mod


def test_plugin_less_mod_still_reaches_the_convert_everything_fallback(tmp_path):
    """The fallback's legitimate case: a loose-mesh replacer has no plugin to
    classify by, so every NIF it ships is fair game."""
    mod = _mod(tmp_path, "LooseReplacer", with_esp=False)
    assert _find_source_esps(mod) == []
    assert _player_armor_mesh_bases(mod) == set()


def test_plugin_bearing_mod_is_classifiable(tmp_path):
    """Control: with a readable plugin the ESP selects the armour, so the guard
    never engages and the normal path runs."""
    mod = _mod(tmp_path, "NormalMod", with_esp=True)
    assert _find_source_esps(mod), "fixture must ship a plugin"
    assert _player_armor_mesh_bases(mod), "ESP should select the cuirass"


def test_fully_covered_mod_selects_nothing_but_keeps_its_plugin(tmp_path):
    """THE REGRESSION. Every ARMO already UBE-patched -> bases empty. The mod
    still HAS a readable plugin, so this must read as 'nothing to convert', not
    as the plugin-less 'convert everything' case."""
    mod = _mod(tmp_path, "CoveredMod", with_esp=True)
    covered = {("coveredmod.esp", 0x000900)}
    assert _player_armor_mesh_bases(mod, ube_covered_armos=covered) == set()
    assert _find_source_esps(mod), (
        "an empty base set here means 'ESP selected nothing', NOT 'no ESP' -- "
        "the convert-everything fallback must not fire")


def test_the_two_empty_cases_are_distinguishable(tmp_path):
    """Both mods yield an empty set; only the plugin tells them apart. If this
    ever stops discriminating, the guard has no signal to key on."""
    covered_mod = _mod(tmp_path, "Covered2", with_esp=True)
    loose_mod = _mod(tmp_path, "Loose2", with_esp=False)
    covered = {("covered2.esp", 0x000900)}
    assert _player_armor_mesh_bases(covered_mod,
                                    ube_covered_armos=covered) == set()
    assert _player_armor_mesh_bases(loose_mod) == set()
    assert bool(_find_source_esps(covered_mod)) is True
    assert bool(_find_source_esps(loose_mod)) is False


# --- the guard itself (the tests above only pin its INPUTS) ------------------

def test_guard_truth_table(tmp_path):
    """Exercise the decision, not just the signals feeding it. Every one of the
    tests above passes with the guard reverted, so this is the one that fails."""
    mod = _mod(tmp_path, "GuardMod", with_esp=True)
    esps = _find_source_esps(mod)
    assert esps
    # empty bases + a plugin that PARSES -> suppress the fallback
    assert _skip_esp_less_fallback(set(), esps) is True
    # empty bases + no plugin at all -> the fallback is legitimate
    assert _skip_esp_less_fallback(set(), []) is False
    # non-empty bases -> the guard is irrelevant, normal path
    assert _skip_esp_less_fallback({"armor/x"}, esps) is False


def test_unparseable_plugin_is_treated_as_plugin_less(tmp_path):
    """REGRESSION. `_find_source_esps` is a bare glob and never opens a byte,
    while `_player_armor_mesh_bases` swallows a parse failure and returns an
    empty set. Keying the guard on file PRESENCE would convert zero meshes for a
    mod with a truncated plugin -- silent total loss -- where it previously
    converted every NIF it ships."""
    mod = tmp_path / "CorruptMod"
    (mod / "meshes" / "armor").mkdir(parents=True)
    (mod / "meshes" / "armor" / "cuirass_1.nif").write_bytes(b"nif")
    (mod / "corrupt.esp").write_bytes(b"not a plugin at all")
    esps = _find_source_esps(mod)
    assert esps, "the glob still finds the file"
    assert _player_armor_mesh_bases(mod) == set(), "parse failure -> empty"
    assert _skip_esp_less_fallback(set(), esps) is False, (
        "an unparseable plugin must fall back to converting what it ships, "
        "not silently convert nothing")


def test_one_good_plugin_among_several_still_counts_as_read(tmp_path):
    mod = _mod(tmp_path, "MixedPlugins", with_esp=True)
    (mod / "broken.esp").write_bytes(b"garbage")
    assert _skip_esp_less_fallback(set(), _find_source_esps(mod)) is True
