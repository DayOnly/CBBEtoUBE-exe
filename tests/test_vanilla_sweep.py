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

"""VANILLA SWEEP: the game Data dir as the last (lowest-priority) source.

Vanilla armor coverage used to be incidental -- a vanilla mesh converted only
when some mod carried an override of its ARMA -- so a piece nobody overrides
was never converted, got no UBE armature, and rendered invisible on UBE
actors. The sweep passes the Data dir through the normal per-mod pipeline
with the vanilla/DLC masters as its source plugins."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import esp
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring
from src import ube_patcher as up
from src.auto_convert import (_vanilla_sweep_esps, _player_armor_mesh_bases,
                              _BsaMeshIndex, auto_convert_mod,
                              _preflight_vanilla_sweep)

DEFAULT_RACE = 0x00000019
BODY = 1 << 2      # biped slot 32
HANDS = 1 << 3     # biped slot 33
_ARMO_NONPLAYABLE = 0x00000004


def _arma(fid, edid, slot, female):
    return Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
        + encode_subrecord(b"MOD3", encode_zstring(female))))


def _armo(fid, edid, arma_fid, slot, flags=0):
    return Record(sig=b"ARMO", flags=flags, formid=fid, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
        + encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
        + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))))


def _mk_vanilla_esm(path, *groups):
    """A synthetic masterless Skyrim.esm-shaped plugin (ESM flag, no masters):
    its RNAM master byte is a SELF reference (mi == len(masters) == 0)."""
    ESP(header=TES4Header(masters=[], num_records=0, next_object_id=0x900,
                          version=1.7, flags=0x1),
        groups=list(groups)).save(path)
    return path


def _mk_data_dir(tmp, arma_recs, armo_recs):
    data = tmp / "Data"
    data.mkdir()
    _mk_vanilla_esm(data / "Skyrim.esm",
                    Group(label=b"ARMA", records=list(arma_recs)),
                    Group(label=b"ARMO", records=list(armo_recs)))
    # UBE_AllRace so generate_ube_patch's race discovery works from data dir.
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[]).save(data / "UBE_AllRace.esp")
    return data


# --- _vanilla_sweep_esps detection -----------------------------------------

def test_sweep_detects_data_dir_and_orders_masters(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    # Present out of canonical order on disk; result must follow load order.
    for n in ("Dragonborn.esm", "Skyrim.esm", "Dawnguard.esm"):
        _mk_vanilla_esm(data / n)
    esps = _vanilla_sweep_esps(data)
    assert [p.name for p in esps] == ["Skyrim.esm", "Dawnguard.esm",
                                      "Dragonborn.esm"]


def test_sweep_ignores_normal_mod_folder(tmp_path):
    mod = tmp_path / "SomeArmorMod"
    mod.mkdir()
    _mk_vanilla_esm(mod / "Dawnguard.esm")   # no Skyrim.esm at the root
    assert _vanilla_sweep_esps(mod) == []


# --- masterless self-RNAM gate in _player_armor_mesh_bases ------------------

def test_vanilla_esm_armas_pass_default_race_gate(tmp_path):
    # Skyrim.esm has an EMPTY master list: RNAM's master byte is a SELF
    # reference. The gate must map it to the plugin's own name, not reject
    # mi >= len(masters). This is the bug that made a naive Data-dir source
    # plan ZERO meshes.
    data = _mk_data_dir(
        tmp_path,
        [_arma(0x036197, "GlovesAA", HANDS, r"armor\x\gauntletsf_1.nif")],
        [_armo(0x013958, "Gauntlets", 0x036197, HANDS)])
    bases = _player_armor_mesh_bases(data)
    assert "armor/x/gauntletsf" in bases, bases


def test_mod_self_race_still_rejected(tmp_path):
    # A NON-vanilla masterless plugin whose RNAM low24 happens to equal
    # DefaultRace: self reference resolves to the mod's own name -> rejected.
    mod = tmp_path / "WeirdMod"
    mod.mkdir()
    ESP(header=TES4Header(masters=[], num_records=0, next_object_id=0x900,
                          version=1.7),
        groups=[Group(label=b"ARMA", records=[
            _arma(0x036197, "GlovesAA", HANDS, r"armor\x\gauntletsf_1.nif")])]
        ).save(mod / "weirdmod.esp")
    assert _player_armor_mesh_bases(mod) == set()


def test_nonplayable_vanilla_skin_excluded(tmp_path):
    # SkinNaked-style setup: the ARMA's only referencing ARMO is non-playable
    # -> the sweep must not convert the nude body.
    data = _mk_data_dir(
        tmp_path,
        [_arma(0x000D64, "NakedTorso", BODY, r"actors\character\femalebody_1.nif")],
        [_armo(0x000D65, "SkinNaked", 0x000D64, BODY,
               flags=_ARMO_NONPLAYABLE)])
    assert _player_armor_mesh_bases(data) == set()


# --- BSA index contains() ----------------------------------------------------

def test_bsa_contains_is_lookup_only(tmp_path):
    idx = _BsaMeshIndex([], tmp_path / "staging")
    assert idx.contains("armor/x/gauntletsf_1.nif") is False   # empty scan
    idx._index["armor/x/gauntletsf_1.nif"] = (tmp_path / "fake.bsa", "f")
    assert idx.contains("armor/x/gauntletsf_1.nif") is True
    assert not (tmp_path / "staging").exists()                  # no extraction


# --- auto_convert_mod on a Data-dir source ----------------------------------

def test_auto_convert_mod_uses_vanilla_masters_as_sources(tmp_path):
    data = _mk_data_dir(
        tmp_path,
        [_arma(0x036197, "GlovesAA", HANDS, r"armor\x\gauntletsf_1.nif")],
        [_armo(0x013958, "Gauntlets", 0x036197, HANDS)])
    out = tmp_path / "out"
    r = auto_convert_mod(data, out, master_data_dirs=[data], nif_workers=1)
    # The vanilla master IS the source plugin (not skipped as a vanilla ESM).
    assert [p.name for p in (r.source_esps or [])] == ["Skyrim.esm"]
    assert any("vanilla sweep source" in n for n in r.notes), r.notes


# --- sweep preflight: fail EARLY (before the batch), not at source #last ----

def test_preflight_ok_on_plannable_data_dir(tmp_path):
    data = _mk_data_dir(
        tmp_path,
        [_arma(0x036197, "GlovesAA", HANDS, r"armor\x\gauntletsf_1.nif")],
        [_armo(0x013958, "Gauntlets", 0x036197, HANDS)])
    ok, why = _preflight_vanilla_sweep(data)
    assert ok, why
    assert "armour mesh base(s)" in why


def test_preflight_rejects_missing_skyrim_esm(tmp_path):
    d = tmp_path / "NotData"
    d.mkdir()
    ok, why = _preflight_vanilla_sweep(d)
    assert not ok and "no Skyrim.esm" in why


def test_preflight_rejects_corrupt_esm(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    (data / "Skyrim.esm").write_bytes(b"\x00" * 64)   # not a TES4 plugin
    ok, why = _preflight_vanilla_sweep(data)
    assert not ok


def test_preflight_rejects_no_plannable_armas(tmp_path):
    # Parses, has an (empty) ARMA group, but nothing DefaultRace-equippable.
    data = _mk_data_dir(tmp_path, [], [])
    ok, why = _preflight_vanilla_sweep(data)
    assert not ok and "no DefaultRace" in why


# --- sweep = separate pass: its failure must never block the merge ----------

def _convert_ns(sources, output):
    import argparse
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


def _run_cmd_convert_with_crash(tmp_path, monkeypatch, capsys, crash_sweep):
    from src import auto_convert as ac
    mod = tmp_path / "SomeMod"
    mod.mkdir()
    data = _mk_data_dir(tmp_path, [], [])
    out = tmp_path / "out"
    # Pin the failure-summary file into tmp (it is written next to the run
    # log) so tests never write into the repo root.
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))

    def _boom(source_dir, *a, **k):
        source_dir = Path(source_dir)
        is_sweep = (source_dir / "Skyrim.esm").is_file()
        if is_sweep == crash_sweep:
            raise FileNotFoundError(2, "The system cannot find the path specified")
        return ac.AutoConvertResult(source_dir=source_dir, output_dir=Path(out))

    monkeypatch.setattr(ac, "auto_convert_mod", _boom)
    rc = ac._cmd_convert(_convert_ns([mod, data], out))
    return rc, capsys.readouterr().out


def _read_failures(tmp_path):
    import json
    return json.loads((tmp_path / "CBBEtoUBE_last_failures.json")
                      .read_text(encoding="utf-8"))["failures"]


def test_sweep_failure_does_not_block_merge(tmp_path, monkeypatch, capsys):
    rc, log = _run_cmd_convert_with_crash(tmp_path, monkeypatch, capsys,
                                          crash_sweep=True)
    assert "VANILLA SWEEP FAILED" in log
    assert "auto-merge SKIPPED" not in log       # merge gate NOT tripped
    assert "VANILLA SWEEP pass" in log           # ran as its own pass
    assert rc != 0                                # still a loud, failing exit
    # Failure summary (drives the GUI end-of-run popup): the sweep failure is
    # recorded, and no merge-skipped entry exists (merge was not blocked).
    fails = _read_failures(tmp_path)
    kinds = {f["kind"] for f in fails}
    assert "vanilla sweep failed" in kinds, fails
    assert "merge skipped" not in kinds, fails


def test_mod_failure_still_blocks_merge(tmp_path, monkeypatch, capsys):
    rc, log = _run_cmd_convert_with_crash(tmp_path, monkeypatch, capsys,
                                          crash_sweep=False)
    assert "auto-merge SKIPPED" in log           # mod failures still gate
    assert "VANILLA SWEEP FAILED" not in log
    assert rc != 0
    fails = _read_failures(tmp_path)
    kinds = {f["kind"] for f in fails}
    assert "source failed" in kinds, fails
    assert "merge skipped" in kinds, fails
    srcs = {f["source"] for f in fails if f["kind"] == "source failed"}
    assert "SomeMod" in srcs, fails


def test_sweep_pool_failure_self_heals_serially(tmp_path, monkeypatch, capsys):
    # The pooled attempt dies (the one component with environment-sensitive
    # failure modes); the sweep must self-heal by retrying SERIALLY and the
    # run must finish clean — no user-visible failure at all.
    from src import auto_convert as ac
    mod = tmp_path / "SomeMod"
    mod.mkdir()
    data = _mk_data_dir(tmp_path, [], [])
    out = tmp_path / "out"
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))

    class _FakePool:
        def __init__(self, *a, **k): pass
        def prewarm(self): pass
        def shutdown(self): pass
    monkeypatch.setattr(ac, "_NifPool", _FakePool)

    calls = []

    def _flaky(source_dir, *a, **k):
        source_dir = Path(source_dir)
        is_sweep = (source_dir / "Skyrim.esm").is_file()
        calls.append((is_sweep, k.get("nif_pool"), k.get("nif_workers")))
        if is_sweep and k.get("nif_pool") is not None:
            raise BrokenPipeError("worker pool died")
        return ac.AutoConvertResult(source_dir=source_dir, output_dir=Path(out))

    monkeypatch.setattr(ac, "auto_convert_mod", _flaky)
    ns = _convert_ns([mod, data], out)
    ns.workers = 4                     # pooled path (fake pool above)
    rc = ac._cmd_convert(ns)
    log = capsys.readouterr().out
    assert "retrying SERIALLY" in log
    assert "serial retry SUCCEEDED" in log
    assert "VANILLA SWEEP FAILED" not in log     # self-healed, not failed
    assert rc == 0, log
    # The retry ran the sweep WITHOUT the pool, serially.
    sweep_calls = [c for c in calls if c[0]]
    assert len(sweep_calls) == 2
    assert sweep_calls[1][1] is None and sweep_calls[1][2] == 1


def test_sweep_never_converts_the_whole_data_tree(tmp_path):
    # No qualifying armour ARMA -> the ESP-less "convert every NIF the folder
    # ships" fallback must NOT engage for a Data-dir source (it would sweep in
    # every loose vanilla mesh: skeletons, clutter, creatures).
    data = _mk_data_dir(tmp_path, [], [])
    junk = data / "meshes" / "clutter"
    junk.mkdir(parents=True)
    (junk / "junk.nif").write_bytes(b"\x00" * 16)
    out = tmp_path / "out"
    r = auto_convert_mod(data, out, master_data_dirs=[data], nif_workers=1)
    assert any("nothing planned" in n for n in r.notes), r.notes
    assert not (out / "meshes").exists() or \
        not list((out / "meshes").rglob("junk.nif"))
