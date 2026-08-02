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

"""#skip-already-ube -- an armor another mod has ALREADY UBE-patched must not be
converted at all.

The coexistence check used to run only at the coverage/link stage: the meshes
were converted and shipped, then suppressed, so the output carried orphan meshes
with no SkyPatcher link (observed on a real modlist: 34 NIFs converted for a mod
whose armors were all third-party-patched, 0 INI lines emitted for them).
`_player_armor_mesh_bases` now takes the covered-armor set and drops those
pieces during discovery, before any conversion work happens."""
import struct

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.auto_convert import _player_armor_mesh_bases

DEFAULT_RACE = 0x00000019
BODY = 1 << 2


def _arma(fid, edid, mesh, slot=BODY):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
               + encode_subrecord(b"MOD3", encode_zstring(mesh)))
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _armo(fid, edid, arma_fids, slot=BODY):
    if isinstance(arma_fids, int):
        arma_fids = [arma_fids]
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + encode_subrecord(b"FULL", encode_zstring(edid)))
    for af in arma_fids:
        payload += encode_subrecord(b"MODL", struct.pack("<I", af))
    payload += encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0))
    return esp.Record(sig=b"ARMO", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _write_mod(tmp_path, name, armas, armos, masters=("Skyrim.esm",)):
    mod = tmp_path / name
    mod.mkdir()
    e = esp.ESP(header=esp.TES4Header(masters=list(masters)),
                groups=[esp.Group(label=b"ARMA", records=armas),
                        esp.Group(label=b"ARMO", records=armos)])
    e.save(mod / (name.lower() + ".esp"))
    return mod


def test_already_ube_patched_armor_is_not_converted(tmp_path):
    # The ARMO is new in this plugin, so its defining plugin is the plugin
    # itself -- identity ("coveredmod.esp", low24).
    mod = _write_mod(
        tmp_path, "CoveredMod",
        armas=[_arma(0x01000800, "BodysuitAA", "armorpack/bodysuit_1.nif")],
        armos=[_armo(0x01000900, "Bodysuit", 0x01000800)])
    covered = {("coveredmod.esp", 0x000900)}
    assert _player_armor_mesh_bases(mod, ube_covered_armos=covered) == set()


def test_uncovered_armor_in_the_same_mod_still_converts(tmp_path):
    mod = _write_mod(
        tmp_path, "MixedMod",
        armas=[_arma(0x01000800, "BeltAA", "armorpack/belt_1.nif"),
               _arma(0x01000801, "GlovesAA", "fs/gloves_1.nif")],
        armos=[_armo(0x01000900, "Belt", 0x01000800),
               _armo(0x01000901, "Gloves", 0x01000801)])
    covered = {("mixedmod.esp", 0x000900)}          # only the belt is patched
    bases = _player_armor_mesh_bases(mod, ube_covered_armos=covered)
    assert not any("belt" in b for b in bases), bases
    assert any("gloves" in b for b in bases), bases


def test_mesh_shared_with_an_uncovered_armor_still_converts(tmp_path):
    # One ARMA referenced by BOTH a covered and an uncovered ARMO. Skipping it
    # would leave the uncovered armor unconverted, so it must survive.
    mod = _write_mod(
        tmp_path, "SharedMod",
        armas=[_arma(0x01000800, "SharedAA", "shared/cuirass_1.nif")],
        armos=[_armo(0x01000900, "PatchedArmor", 0x01000800),
               _armo(0x01000901, "PlainArmor", 0x01000800)])
    covered = {("sharedmod.esp", 0x000900)}
    bases = _player_armor_mesh_bases(mod, ube_covered_armos=covered)
    assert any("cuirass" in b for b in bases), bases


def test_no_covered_set_converts_everything(tmp_path):
    # Back-compat: callers that pass nothing (preflight, eligibility scans)
    # must see the pre-change behaviour.
    mod = _write_mod(
        tmp_path, "PlainMod",
        armas=[_arma(0x01000800, "CuirassAA", "plain/cuirass_1.nif")],
        armos=[_armo(0x01000900, "Cuirass", 0x01000800)])
    assert _player_armor_mesh_bases(mod) != set()
    assert _player_armor_mesh_bases(mod, ube_covered_armos=set()) != set()
    assert _player_armor_mesh_bases(mod, ube_covered_armos=None) != set()


def test_covered_identity_uses_the_defining_master_for_an_override(tmp_path):
    # An ARMO OVERRIDING a master's record: formid high byte indexes the master
    # list, so the identity is the MASTER's name, not this plugin's. A set keyed
    # on the master must match (this is how a vanilla-replacer patch appears).
    mod = _write_mod(
        tmp_path, "OverrideMod",
        armas=[_arma(0x01000800, "IronAA", "armor/iron/f/cuirass_1.nif")],
        armos=[_armo(0x00000900, "IronCuirass", 0x01000800)],
        masters=("Skyrim.esm",))
    covered = {("skyrim.esm", 0x000900)}
    assert _player_armor_mesh_bases(mod, ube_covered_armos=covered) == set()
    # ...and the plugin's own name must NOT match that override.
    assert _player_armor_mesh_bases(
        mod, ube_covered_armos={("overridemod.esp", 0x000900)}) != set()


# --- wiring (the tests above only cover the selection function) --------------

def test_batch_computes_the_covered_set_and_threads_it_into_conversion():
    """WIRING GUARD. Every test above calls `_player_armor_mesh_bases` directly, so
    deleting the `ube_covered_armos=` kwarg at the conversion call site would leave
    them all green while the gate silently did nothing. A pass that is written but
    never called is the exact failure that left the UBE-native backstop dead for
    weeks -- pin the wiring, not just the logic."""
    import inspect
    from src import auto_convert as ac
    src = inspect.getsource(ac)
    # computed ONCE at batch level, not per-mod (the scan reads every enabled plugin)
    assert "batch_ube_covered = _third_party_ube_covered_armos(" in src
    # ...and actually handed to the converter
    assert "ube_covered_armos=batch_ube_covered" in src
    # ...and the selection function accepts it
    assert "ube_covered_armos" in inspect.signature(
        ac._player_armor_mesh_bases).parameters
    assert "ube_covered_armos" in inspect.signature(
        ac.auto_convert_mod).parameters


def test_covered_scan_failure_converts_everything_rather_than_nothing():
    """FAIL-SAFE DIRECTION. If the third-party scan raises, the gate must fall back
    to converting everything (the old behaviour). Failing the other way would skip
    real armour and ship invisible pieces with no diagnostic."""
    import inspect
    from src import auto_convert as ac
    src = inspect.getsource(ac)
    i = src.index("batch_ube_covered = _third_party_ube_covered_armos(")
    tail = src[i:i + 900]
    assert "batch_ube_covered = None" in tail, (
        "the except branch must clear the set, not leave a partial one")
    assert "converting everything" in tail, (
        "and must say so -- a silently-empty exclusion set looks identical to "
        "'nothing to skip'")
