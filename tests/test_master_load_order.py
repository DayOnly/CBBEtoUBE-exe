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

r"""#espfe-is-not-a-master and #master-load-order -- GitHub issue #27.

The tool counted a plugin as a master (to be listed ahead of the regular
plugins) when its TES4 header carried the ESM flag OR the ESL flag. The ESL flag
alone does not make a master: an ESL-flagged `.esp` ("ESPFE") loads where it
sits among the regular plugins. So the merged Combined listed an ESPFE at load
position 400 ahead of ESMs that load before it, xEdit's Sort Masters then
rewrote the plugin, and the validator warned about the wrong things while its
postflight passed the real case (it used the same rule).

Pinned here:

1. Master-tier = a `.esm`/`.esl` file or an ESM-flagged plugin. ESL flag alone: no.
2. With the load order known, the Combined's masters are written in it.
3. A plugin the game loads is checked against the load order in the postflight;
   a per-source patch (never loaded) is not.
4. A per-source patch lists an ESM-flagged master ahead of UBE_AllRace and the
   other regular plugins.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import esp                                              # noqa: E402
from src import ube_patcher as up                                # noqa: E402
from tests.test_full_skypatcher import (BODY, HEAD, OWN, _arma, _armo,  # noqa: E402
                                        _mk_env)
from tests.test_resort_masters import _armo as _rm_armo          # noqa: E402

ESM, ESL = 0x1, 0x200


@pytest.fixture(autouse=True)
def _fresh():
    up.clear_esm_tier_cache()
    up.set_load_order(None)
    yield
    up.clear_esm_tier_cache()
    up.set_load_order(None)


def _plugin(folder: Path, name: str, flags: int, masters=("Skyrim.esm",)):
    folder.mkdir(parents=True, exist_ok=True)
    esp.ESP(header=esp.TES4Header(masters=list(masters), flags=flags,
                                  next_object_id=0x800), groups=[]).save(folder / name)


def _arma_ref(fid, masters, mesh="armor/a/body_0.nif"):
    """An ARMA that names every master past the first as an additional race, so
    the master list survives the tool's own pruning of unused masters. Alternate
    textures name them too: that is what a per-source patch carries over."""
    import struct
    from src.esp import encode_subrecord, encode_zstring
    refs = b"".join(encode_subrecord(b"MODL", struct.pack("<I", (k << 24) | 0x123))
                    for k in range(1, len(masters)))
    ents = [(f"Shape{k}", (k << 24) | 0x123, k) for k in range(1, len(masters))]
    alt = struct.pack("<I", len(ents)) + b"".join(
        struct.pack("<I", len(n.encode())) + n.encode() + struct.pack("<II", t, i)
        for n, t, i in ents)
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring("A"))
        + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x19))
        + refs + encode_subrecord(b"MOD3", encode_zstring(mesh))
        + encode_subrecord(b"MO3S", alt)))


# --- 1. what a master is ---------------------------------------------------------

def test_an_esl_flag_alone_does_not_make_a_master(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "FlaggedEsm.esp", ESM)
    _plugin(tmp_path, "Both.esp", ESM | ESL)
    _plugin(tmp_path, "Plain.esp", 0)
    got = {n: up._is_esm_tier_master(n, [tmp_path])
           for n in ("Espfe.esp", "FlaggedEsm.esp", "Both.esp", "Plain.esp")}
    assert got == {"Espfe.esp": False, "FlaggedEsm.esp": True,
                   "Both.esp": True, "Plain.esp": False}


def test_a_file_named_esm_or_esl_is_a_master_without_reading_it(tmp_path):
    assert up._is_esm_tier_master("Anything.esm", [tmp_path]) is True
    assert up._is_esm_tier_master("Anything.esl", [tmp_path]) is True


# --- 2. the written master list is in load order ---------------------------------

def _ordered(masters, load_order, data_dirs):
    up.set_load_order(load_order)
    e = esp.ESP(header=esp.TES4Header(masters=list(masters), next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMO", records=[
                    _rm_armo((len(masters) << 24) | 0x900, [])])])
    up.resort_masters(e, master_data_dirs=data_dirs)
    return e.header.masters


def test_an_espfe_that_loads_after_an_esm_is_listed_after_it(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Later.esm", ESM)
    got = _ordered(["Skyrim.esm", "Espfe.esp", "Later.esm"],
                   ["Skyrim.esm", "Later.esm", "Espfe.esp"], [tmp_path])
    assert got == ["Skyrim.esm", "Later.esm", "Espfe.esp"]


def test_an_espfe_between_regular_plugins_stays_where_it_loads(tmp_path):
    """The old rule moved it ahead of the regular plugin that loads before it."""
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Reg.esp", 0)
    got = _ordered(["Skyrim.esm", "Reg.esp", "Espfe.esp"],
                   ["Skyrim.esm", "Reg.esp", "Espfe.esp"], [tmp_path])
    assert got == ["Skyrim.esm", "Reg.esp", "Espfe.esp"]


def test_regular_plugins_follow_the_load_order_not_the_order_they_were_met(tmp_path):
    _plugin(tmp_path, "A.esp", 0)
    _plugin(tmp_path, "B.esp", 0)
    got = _ordered(["Skyrim.esm", "B.esp", "A.esp"],
                   ["Skyrim.esm", "A.esp", "B.esp"], [tmp_path])
    assert got == ["Skyrim.esm", "A.esp", "B.esp"]


def test_without_a_load_order_masters_sort_by_tier_alone(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Later.esm", ESM)
    got = _ordered(["Skyrim.esm", "Espfe.esp", "Later.esm"], None, [tmp_path])
    assert got == ["Skyrim.esm", "Later.esm", "Espfe.esp"]


def test_a_merge_unions_the_masters_in_load_order(tmp_path):
    """The union meets B before A (patch order); the load order says A first, and
    an ESPFE that loads last stays last."""
    _plugin(tmp_path, "A.esp", 0)
    _plugin(tmp_path, "B.esp", 0)
    _plugin(tmp_path, "Espfe.esp", ESL)
    up.set_load_order(["Skyrim.esm", "A.esp", "B.esp", "Espfe.esp"])
    patches = []
    for name, masters in (("P1.esp", ["Skyrim.esm", "Espfe.esp", "B.esp"]),
                          ("P2.esp", ["Skyrim.esm", "A.esp"])):
        e = esp.ESP(header=esp.TES4Header(masters=masters, next_object_id=0xFFFFFF),
                    groups=[esp.Group(label=b"ARMA", records=[
                        _arma_ref((len(masters) << 24) | 0x900, masters)])])
        e.save(tmp_path / name)
        patches.append(tmp_path / name)
    out = tmp_path / "Merged.esp"
    up.merge_patches(patches, out, master_data_dirs=[tmp_path])
    got = [m for m in esp.ESP.load(out).header.masters if m in ("A.esp", "B.esp", "Espfe.esp")]
    assert got == ["A.esp", "B.esp", "Espfe.esp"]


# --- 3. the postflight sees the load order ---------------------------------------

def _plugin_with_masters(path, masters):
    esp.ESP(header=esp.TES4Header(masters=masters, next_object_id=0xFFFFFF),
            groups=[esp.Group(label=b"ARMO", records=[
                _rm_armo((len(masters) << 24) | 0x900, [])])]).save(path)


def test_the_tier_check_no_longer_flags_an_espfe_after_a_regular_plugin(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Reg.esp", 0)
    p = tmp_path / "t.esp"
    _plugin_with_masters(p, ["Skyrim.esm", "Reg.esp", "Espfe.esp"])
    w = up.validate_patch(p, check_nifs=False, master_data_dirs=[tmp_path])
    assert not any(x.startswith("master-ordering") for x in w), w


def test_the_tier_check_still_flags_an_esm_after_a_regular_plugin(tmp_path):
    """CONTROL: the check can still fire, so the test above proves something."""
    _plugin(tmp_path, "Reg.esp", 0)
    _plugin(tmp_path, "Late.esm", ESM)
    p = tmp_path / "t.esp"
    _plugin_with_masters(p, ["Skyrim.esm", "Reg.esp", "Late.esm"])
    w = up.validate_patch(p, check_nifs=False, master_data_dirs=[tmp_path])
    assert any(x.startswith("master-ordering") for x in w), w


def test_a_loaded_plugin_with_masters_out_of_load_order_is_flagged(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Later.esm", ESM)
    up.set_load_order(["Skyrim.esm", "Later.esm", "Espfe.esp"])
    p = tmp_path / "t.esp"
    _plugin_with_masters(p, ["Skyrim.esm", "Espfe.esp", "Later.esm"])
    w = up.validate_patch(p, check_nifs=False, master_data_dirs=[tmp_path],
                          check_load_order=True)
    assert any(x.startswith("master-load-order") for x in w), w


def test_the_load_order_check_is_off_unless_asked_and_silent_without_an_order(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Later.esm", ESM)
    p = tmp_path / "t.esp"
    _plugin_with_masters(p, ["Skyrim.esm", "Espfe.esp", "Later.esm"])
    up.set_load_order(["Skyrim.esm", "Later.esm", "Espfe.esp"])
    w = up.validate_patch(p, check_nifs=False, master_data_dirs=[tmp_path])
    assert not any(x.startswith("master-load-order") for x in w), w        # not asked
    up.set_load_order(None)
    w = up.validate_patch(p, check_nifs=False, master_data_dirs=[tmp_path],
                          check_load_order=True)
    assert not any(x.startswith("master-load-order") for x in w), w        # no order


def test_a_correctly_ordered_loaded_plugin_is_clean(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Later.esm", ESM)
    up.set_load_order(["Skyrim.esm", "Later.esm", "Espfe.esp"])
    p = tmp_path / "t.esp"
    _plugin_with_masters(p, ["Skyrim.esm", "Later.esm", "Espfe.esp"])
    w = up.validate_patch(p, check_nifs=False, master_data_dirs=[tmp_path],
                          check_load_order=True)
    assert not any(x.startswith(("master-load-order", "master-ordering")) for x in w), w


def test_the_postflight_asks_for_the_load_order_check(tmp_path):
    _plugin(tmp_path, "Espfe.esp", ESL)
    _plugin(tmp_path, "Later.esm", ESM)
    up.set_load_order(["Skyrim.esm", "Later.esm", "Espfe.esp"])
    p = tmp_path / "Combined.esp"
    _plugin_with_masters(p, ["Skyrim.esm", "Espfe.esp", "Later.esm"])
    r = up.postflight_validate_combined(p, None, master_data_dirs=[tmp_path])
    assert any(w.startswith("master-load-order") for _n, w in r["soft"]), r


# --- 4. a per-source patch lists an ESM-flagged master before the regular ones --------

def test_a_per_source_patch_lists_an_esm_master_ahead_of_the_regular_ones(tmp_path):
    _mk_env(tmp_path)
    _plugin(tmp_path, "Early.esp", 0)
    _plugin(tmp_path, "Late.esm", ESM)
    smasters = ["Skyrim.esm", "Early.esp", "Late.esm"]       # a regular one listed first
    src = esp.ESP(header=esp.TES4Header(masters=smasters, num_records=0,
                                        next_object_id=0x900, version=1.7),
                  groups=[
        esp.Group(label=b"ARMA", records=[
            _arma_ref(OWN | 0x800, smasters, "armor/a/body_0.nif")]),
        esp.Group(label=b"ARMO", records=[
            _armo(OWN | 0x801, "Body", OWN | 0x800, BODY)])])
    src.save(tmp_path / "SrcMod.esp")
    out = tmp_path / "SrcMod UBE patch.esp"
    up.generate_ube_patch(tmp_path / "SrcMod.esp", out, master_data_dirs=[tmp_path],
                          converted_rel_paths={"armor/a/body_0.nif"})
    masters = esp.ESP.load(out).header.masters
    assert {"Early.esp", "Late.esm", "UBE_AllRace.esp"} <= set(masters), masters
    assert masters.index("Late.esm") < masters.index("Early.esp"), masters
    assert masters.index("Late.esm") < masters.index("UBE_AllRace.esp"), masters


# --- 5. the run hands the load order over ----------------------------------------

def test_the_run_records_the_load_order_before_it_converts(monkeypatch, tmp_path):
    import pytest as _pytest
    from src import auto_convert as ac
    from tests.test_output_folder_quotes import _Stop, _args, _stub_run
    _stub_run(monkeypatch, tmp_path)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered",
                        lambda lay: ["Skyrim.esm", "Mod.esp"])
    with _pytest.raises(_Stop):
        ac._cmd_auto(_args(tmp_path / "out"))
    assert up._LOAD_ORDER_INDEX == {"skyrim.esm": 0, "mod.esp": 1}

