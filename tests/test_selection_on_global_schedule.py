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

"""The source-selection fixes on the batch-wide schedule -- what the merge of
the selection lane into the #global-schedule integration had to reconcile.

The selection lane wrote its per-source changes into the one-source loop of
`auto_convert_mod`. The integration had moved that loop: every source is
planned first, all NIFs run on one schedule, and each source's patch is written
at its finish by `_write_source_patches` (#global-schedule). Pinned here, on the
batch-wide path AND one source at a time:

- #loaded-source-plugins: the batch's per-source patch names reach every
  source (`_source_kwargs`), the first writer keeps a name on the batch-wide
  schedule too, and a vanilla sweep whose finish fails gives back the names it
  claimed there before its serial retry writes them;
- #esp-report-pairing: a patch written at a source's finish is paired with the
  plugin that made it;
- #female-slot-pairs x #stale-output-sweep: the male model the female-only rule
  leaves out gives its "female-only" reason per slot pair, and the male a dead
  pair keeps gives none.
"""
import json
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import paths
from tests.test_female_slot_pairs import FULL, _mod
from tests.test_global_schedule import _Pool, _item, _setup_batch
from tests.test_loaded_source_plugins import NEW, _masters, _plugin

SCHEDULE = "CBBE2UBE_NO_GLOBAL_SCHEDULE"
LOADED = "CBBE2UBE_NO_LOADED_SOURCE_PLUGINS"
PAIRS = "CBBE2UBE_NO_FEMALE_SLOT_PAIRS"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for k in (SCHEDULE, LOADED, PAIRS, "CBBE2UBE_FEMALE_SLOT_ABSENT_KEEPS_MALE"):
        monkeypatch.delenv(k, raising=False)
    ac._LOADED_PLUGIN_INDEX.clear()
    yield
    ac._LOADED_PLUGIN_INDEX.clear()


# ------------------------------------------------------ the batch's patch names

def _claims_steps(record, *, fail_finish=None):
    """Fake `_auto_convert_mod_steps`: records the patch-name set each source
    is handed, and claims '<source>-patch' at its finish as `_patch_name_taken`
    would. `fail_finish(name, kw)` -> that finish raises after its claim."""
    def _steps(source_dir, output_dir, **kw):
        name = Path(source_dir).name
        cp = kw.get("claimed_patch_paths")
        record.append(("plan", name, kw.get("nif_pool") is not None, cp,
                       sorted(cp) if cp is not None else None))
        res = ac.AutoConvertResult(source_dir=Path(source_dir),
                                   output_dir=Path(output_dir))
        yield ac._NifPhase(res, [_item(name + "_piece", name)], 1)
        if cp is not None:
            cp.add(f"{name}-patch")
        if fail_finish and fail_finish(name, kw):
            raise BrokenPipeError("worker pool died at the finish")
        return res
    return _steps


@pytest.mark.parametrize("switched_off", [False, True],
                         ids=["one-schedule", "switched-off"])
def test_every_source_gets_the_batchs_patch_names(tmp_path, monkeypatch, capsys,
                                                  switched_off):
    """One set for the whole batch, on either schedule: without it no source
    knows a name an earlier one wrote."""
    if switched_off:
        monkeypatch.setenv(SCHEDULE, "1")
    mods = [tmp_path / "ModA", tmp_path / "ModB"]
    for m in mods:
        m.mkdir()
    ns = _setup_batch(tmp_path, monkeypatch, _Pool(), mods)
    record = []
    monkeypatch.setattr(ac, "_auto_convert_mod_steps", _claims_steps(record))
    rc = ac._cmd_convert(ns)
    log = capsys.readouterr().out
    plans = [r for r in record if r[0] == "plan"]
    assert [p[1] for p in plans] == ["ModA", "ModB"], record
    assert all(isinstance(p[3], set) for p in plans), plans
    assert plans[0][3] is plans[1][3], "one set for the whole batch"
    assert plans[0][3] == {"ModA-patch", "ModB-patch"}
    assert rc == 0, log


def test_a_sweep_whose_finish_fails_gives_back_its_patch_names(tmp_path, monkeypatch,
                                                               capsys):
    """The sweep claims its names at its finish on the batch-wide schedule. A
    finish that fails there is retried serially; the retry must not find its
    own names claimed and skip every patch it would write. An earlier source's
    names are kept (the control)."""
    from tests.test_vanilla_sweep import _mk_data_dir
    mod = tmp_path / "SomeMod"
    mod.mkdir()
    data = _mk_data_dir(tmp_path, [], [])
    ns = _setup_batch(tmp_path, monkeypatch, _Pool(), [mod, data])
    record = []
    monkeypatch.setattr(ac, "_auto_convert_mod_steps", _claims_steps(
        record, fail_finish=lambda n, kw: n == "Data" and kw.get("nif_pool") is not None))
    rc = ac._cmd_convert(ns)
    log = capsys.readouterr().out
    assert "retrying SERIALLY" in log and "serial retry SUCCEEDED" in log, log
    retry = [r for r in record if r[:3] == ("plan", "Data", False)]
    assert len(retry) == 1, record
    assert retry[0][4] == ["SomeMod-patch"], (
        "the retry sees the earlier source's name and not its own")
    assert rc == 0, log


# ------------------------------------------- real patches on the batch-wide schedule

def _two_sources_global(tmp_path, monkeypatch):
    """Two folders outside any modlist ship Quest.esp (so both are read), run
    through the batch-wide schedule in order. Returns (first, results, out)."""
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: [])
    first = _plugin(tmp_path / "srcA" / "Quest.esp", "armor/q/a_1.nif")
    second = _plugin(tmp_path / "srcB" / "Quest.esp", "armor/q/b_1.nif")
    out = tmp_path / "out"
    ref = tmp_path / "ref.nif"
    ref.write_bytes(b"x")
    mdd = [_masters(tmp_path / "data")]
    mgr = ac._NifPool(1, pool_factory=lambda: _Pool())
    claimed_dst: set = set()
    claimed: set = set()
    results: list = []
    ac._convert_sources_global(
        [first.parent, second.parent], results, mgr, claimed_dst,
        make_steps=lambda s: ac._auto_convert_mod_steps(
            s, out, batch_schedule=True, nif_pool=mgr, claimed_dst_paths=claimed_dst,
            claimed_patch_paths=claimed, ube_body_ref_path=ref, master_data_dirs=mdd),
        convert_serial=lambda s: pytest.fail("no serial retry here"),
        checkpoint=lambda view, progress: None,
        claimed_patch_paths=claimed)
    assert [e for _s, _r, e in results] == [None, None], results
    return first, second, [r for _s, r, _e in results], out


def _snapshot_source(out):
    return Path(json.loads((out / "_unmerged_patches" / ("Quest" + NEW + ".espgen.json"))
                           .read_text(encoding="utf-8"))["source_esp"])


def test_the_first_writer_keeps_its_patch_on_the_batch_wide_schedule(tmp_path,
                                                                     monkeypatch):
    first, _second, (ra, rb), out = _two_sources_global(tmp_path, monkeypatch)
    assert [p.name for p in ra.output_esps] == ["Quest" + NEW]
    assert rb.output_esps == [] and rb.esp_patched == []
    assert any("already written this run" in n for n in rb.notes), rb.notes
    assert _snapshot_source(out) == first
    # #esp-report-pairing: the patch written at the finish names its plugin.
    assert [(s, p.name) for s, p, _st in ra.esp_patched] == [(first, "Quest" + NEW)]


def test_switched_off_the_later_source_overwrites_on_the_batch_wide_schedule(
        tmp_path, monkeypatch):
    monkeypatch.setenv(LOADED, "1")
    _first, second, (_ra, rb), out = _two_sources_global(tmp_path, monkeypatch)
    assert [p.name for p in rb.output_esps] == ["Quest" + NEW]
    assert _snapshot_source(out) == second


# ------------------------------------------ the stale-output sweep's reasons

def _reasons(mod, live):
    why: dict = {}
    got = ac._player_armor_mesh_bases(
        mod, mesh_resolves=lambda b: b.rsplit("/", 1)[-1] in live, drop_reasons=why)
    return (sorted(b.rsplit("/", 1)[-1] for b in got),
            {k.rsplit("/", 1)[-1]: v for k, v in why.items()})


def test_the_male_of_a_live_pair_gives_its_reason_and_a_kept_one_none(tmp_path):
    """MOD5 dead, MOD3 live: the first-person male (MOD4) is kept -- planned,
    so no reason -- and the world male (MOD2) is left out as female-only. Before
    the merge the pairs branch gave no reason at all, so the sweep could never
    move an old conversion of a male the rule now leaves out."""
    got, why = _reasons(_mod(tmp_path, **FULL), {"aliceworld", "bobworld", "bobarms"})
    assert got == ["alicearms", "aliceworld", "bobarms"]
    assert why == {"bobworld": "female-only"}


def test_switched_off_both_males_give_the_female_only_reason(tmp_path, monkeypatch):
    """The pairs judged together (the old rule): a live female model leaves
    both males out, and both say why."""
    monkeypatch.setenv(PAIRS, "1")
    got, why = _reasons(_mod(tmp_path, **FULL), {"aliceworld", "bobworld", "bobarms"})
    assert got == ["alicearms", "aliceworld"]
    assert why == {"bobworld": "female-only", "bobarms": "female-only"}
