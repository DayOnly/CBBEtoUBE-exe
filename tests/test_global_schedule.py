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

r"""#global-schedule -- one NIF schedule for the whole batch.

One source at a time, the run waited at every source for its slowest piece:
on the 09-24 All-mods run the 15 workers were busy 24.7% of the 71-minute NIF
phase. Every source is now planned first (claims and the patch ESP are decided
before any NIF), all their units run largest first on the shared pool, and
each source's post-conversion steps run once its own units are in, in source
order.

Pinned here, on synthetic multi-source plans with a scripted pool: a weight
pair split across two sources is never on two workers at once and its later
half waits for the earlier source to FINISH; every source finishes exactly
once, after its last unit, in source order; what a source receives does not
depend on the pool's size or order; largest first; a heavy unit waits for
memory while light ones fill the pool; a MemoryError unit is re-run alone and
only the re-run is delivered; a dead worker's units are re-run in isolation;
the batch path, its GUI markers and the sweep's self-heal; the supersede guard;
and the switch, which restores one source at a time.
"""
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import gui
from src.nif_convert import ConvertResult
from tests.test_npc_worn_nonplayable import load_order  # noqa: F401 (fixture)

SWITCH = "CBBE2UBE_NO_GLOBAL_SCHEDULE"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)


def _item(name, mod="m"):
    return (name, Path("out") / "meshes" / "!UBE" / mod / f"{name}.nif")


class _Fut(Future):
    """A settled future that knows when the scheduler has taken its answer --
    until then its unit counts as running."""

    def __init__(self, pool, names):
        super().__init__()
        self._pool = pool
        self.names = names

    def result(self, timeout=None):
        self._pool.open.discard(self)
        return super().result(timeout)


class _Pool:
    """Runs every submit inline. Records (kind, item names, names running at
    that moment). `oom_once` items answer with a MemoryError the first time;
    `crash_on` items break the pool like a worker that died."""

    def __init__(self, crash_on=(), oom_once=(), oom_reason=None):
        self.crash_on = set(crash_on)
        self.oom_left = set(oom_once)
        self.oom_reason = oom_reason or "error: MemoryError: cannot allocate"
        self.open = set()
        self.submits = []

    def submit(self, fn, *args):
        batch = args[-1] if isinstance(args[-1], list) else [args[-1]]
        names = [it[0] for it in batch]
        running = sorted(n for f in self.open for n in f.names)
        kind = "unit" if fn is ac._run_unit else "alone"
        self.submits.append((kind, names, running))
        f = _Fut(self, names)
        self.open.add(f)
        if any(n in self.crash_on for n in names):
            f.set_exception(BrokenProcessPool("simulated crash"))
        else:
            f.set_result(fn(*args))
        return f

    def shutdown(self, wait=True):
        pass


def _worker(pool):
    def _convert(item):
        if item[0] in pool.oom_left:
            pool.oom_left.discard(item[0])
            return ConvertResult(src_path=item[0], dst_path=None, status="error",
                                 reason=pool.oom_reason)
        return ConvertResult(src_path=item[0], dst_path=str(item[1]),
                             status="converted (copy)")
    return _convert


def _schedule(per_source, pool, *, workers=4, sizes=None, memory=None,
              budget_gb=2.0):
    """Run the batch-wide schedule over `per_source` ([items] per source).
    Returns (events, delivered): events is the ordered ('deliver', k, name) /
    ('finish', k) log; delivered maps source -> [ConvertResult]."""
    sizes = sizes or {}
    units = ac._global_units(list(enumerate(per_source)),
                             size_of=lambda n: sizes.get(n, 0.0))
    events, delivered = [], {k: [] for k in range(len(per_source))}

    def _deliver(k, r):
        events.append(("deliver", k, r.src_path))
        delivered[k].append(r)

    mgr = ac._NifPool(workers, pool_factory=lambda: pool)
    sched = ac._GlobalNifSchedule(
        mgr, units, len(per_source), _deliver,
        lambda k: events.append(("finish", k)), fn=_worker(pool),
        memory=memory or (lambda: {"avail_gb": 64.0, "commit_free_gb": 64.0}),
        budget_gb=budget_gb)
    sched.run()
    return events, delivered, sched


# ------------------------------------------------------------------ the units

def test_a_base_split_across_sources_is_one_unit_per_source_chained():
    units = ac._global_units([(0, [_item("a_1"), _item("c")]),
                              (1, [_item("a_0"), _item("b_1")])])
    got = [([it[0] for it in u.items], u.source, u.after) for u in units]
    assert got == [(["a_1"], 0, None), (["a_0"], 1, 0), (["c"], 0, None),
                   (["b_1"], 1, None)]


def test_one_sources_pair_stays_one_unit_in_list_order():
    units = ac._global_units([(0, [_item("a_1"), _item("b_1"), _item("a_0")])])
    assert [[it[0] for it in u.items] for u in units] == [["a_1", "a_0"], ["b_1"]]


# ------------------------------------------------------------ the scheduling

def test_a_split_pair_never_runs_concurrently_and_waits_for_the_earlier_finish():
    """The `_0` a later source plans shares the XML and `.tri` with the `_1` an
    earlier one planned: it starts only after that source has finished (its
    post-conversion steps included), and never beside the `_1`."""
    pool = _Pool()
    events, _d, _s = _schedule(
        [[_item("a_1"), _item("c_1"), _item("c_0")], [_item("a_0"), _item("d")]],
        pool, sizes={"a_0": 9.0, "d": 1.0})
    sub = {names[0]: running for _k, names, running in pool.submits}
    assert "a_1" not in sub["a_0"]
    assert events.index(("finish", 0)) < events.index(("deliver", 1, "a_0"))
    # control: without the chain the larger `a_0` would have gone first
    assert [n for _k, names, _r in pool.submits for n in names][0] != "a_0"


def test_each_source_finishes_once_after_its_last_unit_in_source_order():
    pool = _Pool()
    per_source = [[_item("s0a")], [], [_item("s2a"), _item("s2b")],
                  [_item("s3a")]]
    # The last source's unit is the biggest, so it runs FIRST.
    events, _d, _s = _schedule(per_source, pool, workers=2,
                               sizes={"s3a": 50.0, "s0a": 0.1, "s2a": 3.0})
    finishes = [e[1] for e in events if e[0] == "finish"]
    assert finishes == [0, 1, 2, 3], "exactly once each, in source order"
    for k in range(4):
        last = max((i for i, e in enumerate(events)
                    if e[0] == "deliver" and e[1] == k), default=-1)
        assert events.index(("finish", k)) > last
    assert pool.submits[0][1] == ["s3a"], "largest first, whatever its source"


@pytest.mark.parametrize("workers", [1, 2, 7])
def test_what_each_source_receives_does_not_depend_on_the_pool(workers):
    per_source = [[_item("a_1"), _item("a_0"), _item("b")],
                  [_item("c_1"), _item("a_0", mod="x"), _item("d_1")],
                  [_item("e")]]
    _e, delivered, _s = _schedule(per_source, _Pool(), workers=workers,
                                  sizes={"b": 4.0, "e": 2.0, "c_1": 1.0})
    got = {k: sorted(r.src_path for r in v) for k, v in delivered.items()}
    assert got == {0: ["a_0", "a_1", "b"], 1: ["a_0", "c_1", "d_1"], 2: ["e"]}


def test_largest_units_start_first():
    pool = _Pool()
    _schedule([[_item("small")], [_item("big"), _item("mid")]], pool, workers=1,
              sizes={"small": 0.5, "big": 5.0, "mid": 3.0})
    assert [names for _k, names, _r in pool.submits] == [["big"], ["mid"],
                                                         ["small"]]


def test_a_heavy_unit_waits_for_memory_while_light_ones_fill_the_pool():
    """20 MB and 18 MB sources predict ~5.1 and ~4.6 GB; 8 GB free less the
    reserve fits one of them. The second waits and the light units run."""
    sizes = {"h1": 20.0, "h2": 18.0, "l1": 1.0, "l2": 0.5}
    tight = _Pool()
    _schedule([[_item("h1"), _item("l1")], [_item("h2"), _item("l2")]], tight,
              sizes=sizes,
              memory=lambda: {"avail_gb": 8.0, "commit_free_gb": 60.0})
    started = {names[0]: running for _k, names, running in tight.submits}
    assert [n[1][0] for n in tight.submits[:3]] == ["h1", "l1", "l2"]
    assert "h1" not in started["h2"], "the second heavy unit waited"
    roomy = _Pool()
    _schedule([[_item("h1"), _item("l1")], [_item("h2"), _item("l2")]], roomy,
              sizes=sizes,
              memory=lambda: {"avail_gb": 30.0, "commit_free_gb": 60.0})
    started = {names[0]: running for _k, names, running in roomy.submits}
    assert "h1" in started["h2"], "control: with room both heavy units run together"


def test_commit_bounds_a_heavy_unit_like_ram_does():
    sizes = {"h1": 20.0, "h2": 18.0}
    pool = _Pool()
    _schedule([[_item("h1")], [_item("h2")]], pool, sizes=sizes,
              memory=lambda: {"avail_gb": 30.0, "commit_free_gb": 8.0})
    started = {names[0]: running for _k, names, running in pool.submits}
    assert "h1" not in started["h2"]


def test_unknown_memory_runs_heavy_units_one_at_a_time():
    sizes = {"h1": 20.0, "h2": 18.0, "l1": 1.0}
    pool = _Pool()
    _schedule([[_item("h1"), _item("h2"), _item("l1")]], pool, sizes=sizes,
              memory=lambda: None)
    started = {names[0]: running for _k, names, running in pool.submits}
    assert "h1" not in started["h2"] and "h1" in started["l1"]


@pytest.mark.parametrize("reason", [
    "error: MemoryError: cannot allocate",
    "PASS FAILED min_push_memory (MemoryError: )",
], ids=["raised", "caught-in-a-pass"])
def test_a_memory_error_unit_is_rerun_alone_and_only_the_rerun_is_delivered(reason):
    pool = _Pool(oom_once={"m_0"}, oom_reason=reason)
    events, delivered, sched = _schedule(
        [[_item("m_1"), _item("m_0"), _item("x")], [_item("y")]], pool,
        sizes={"m_1": 3.0, "m_0": 3.0})
    got = sorted((r.src_path, r.status) for r in delivered[0])
    assert got == [("m_0", "converted (copy)"), ("m_1", "converted (copy)"),
                   ("x", "converted (copy)")], "the out-of-memory answer is dropped"
    alone = [(names, running) for kind, names, running in pool.submits
             if kind == "alone"]
    assert alone == [(["m_1"], []), (["m_0"], [])], (
        "the whole unit, in list order, with nothing else running")
    assert sched.memory_retries == 1
    assert events.index(("finish", 0)) > max(
        i for i, e in enumerate(events) if e[:2] == ("deliver", 0))


def test_a_worker_death_reruns_the_units_in_flight_in_isolation():
    pool = _Pool(crash_on={"x_0"})
    _e, delivered, sched = _schedule(
        [[_item("x_1"), _item("x_0")], [_item("y_1"), _item("y_0")]], pool,
        workers=1, sizes={"x_1": 2.0})
    by = {r.src_path: r for rs in delivered.values() for r in rs}
    assert sorted(by) == ["x_0", "x_1", "y_0", "y_1"]
    assert by["x_0"].status == "error" and "died" in by["x_0"].reason
    assert by["x_1"].status.startswith("converted")
    assert by["y_0"].status.startswith("converted")
    assert sum(len(v) for v in delivered.values()) == 4, "each answered once"
    assert sched.crash_retries == 1


# ------------------------------------------------------------ the batch path

def _setup_batch(tmp_path, monkeypatch, pool, sources):
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(tmp_path))

    class _TestPool(ac._NifPool):
        def __init__(self, n, *a, **k):
            super().__init__(n, pool_factory=lambda: pool)

        def prewarm(self):
            pass

    monkeypatch.setattr(ac, "_NifPool", _TestPool)
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker(pool))
    from tests.test_vanilla_sweep import _convert_ns
    ns = _convert_ns(sources, tmp_path / "out")
    ns.workers = 2
    return ns


def _steps_recording(record, items_for, fail_planning=None):
    def _steps(source_dir, output_dir, **kw):
        name = Path(source_dir).name
        record.append(("plan", name, kw.get("nif_pool") is not None,
                       sorted(str(p) for p in kw["claimed_dst_paths"])))
        if fail_planning and fail_planning(name, kw):
            kw["claimed_dst_paths"].add(Path("claimed-by-the-failed-attempt"))
            raise BrokenPipeError("worker pool died")
        res = ac.AutoConvertResult(source_dir=Path(source_dir),
                                   output_dir=Path(output_dir))
        run_here = yield ac._NifPhase(res, items_for(name), 1)
        record.append(("finish", name, run_here,
                       sorted(r.src_path for r in res.nif_results)))
        return res
    return _steps


def _run_batch(tmp_path, monkeypatch, capsys, switched_off):
    if switched_off:
        monkeypatch.setenv(SWITCH, "1")
    a, b = tmp_path / "ModA", tmp_path / "ModB"
    a.mkdir()
    b.mkdir()
    pool = _Pool()
    ns = _setup_batch(tmp_path, monkeypatch, pool, [a, b])
    record = []
    items = {"ModA": [_item("a1", "a"), _item("a2", "a")],
             "ModB": [_item("b1", "b")]}
    monkeypatch.setattr(ac, "_auto_convert_mod_steps",
                        _steps_recording(record, lambda n: items[n]))
    rc = ac._cmd_convert(ns)
    return rc, record, capsys.readouterr().out


def test_by_default_every_source_is_planned_before_any_nif(tmp_path, monkeypatch, capsys):
    rc, record, log = _run_batch(tmp_path, monkeypatch, capsys, switched_off=False)
    assert [r[:2] for r in record] == [("plan", "ModA"), ("plan", "ModB"),
                                      ("finish", "ModA"), ("finish", "ModB")]
    assert record[2][2:] == (False, ["a1", "a2"]), "the schedule converted them"
    assert record[3][2:] == (False, ["b1"])
    assert rc == 0, log


def test_switched_off_each_source_converts_its_own_nifs_first(tmp_path, monkeypatch, capsys):
    rc, record, log = _run_batch(tmp_path, monkeypatch, capsys, switched_off=True)
    assert [r[:3] for r in record] == [("plan", "ModA", True), ("finish", "ModA", True),
                                      ("plan", "ModB", True), ("finish", "ModB", True)]
    assert "[progress] 1 2 ModA" in log and "[progress] 2 2 ModB" in log
    assert rc == 0, log


def test_the_window_gets_one_bar_for_the_whole_nif_phase(tmp_path, monkeypatch, capsys):
    """The per-source "[progress] i N" markers assumed one source at a time. The
    batch-wide phase prints ONE mod marker and per-file markers counting every
    source's files, so the window's bar fills once, from 0 to the end."""
    _rc, _record, log = _run_batch(tmp_path, monkeypatch, capsys, switched_off=False)
    mods = [m.groups() for m in gui._PROG_RX.finditer(log)]
    files = [tuple(map(int, m.groups())) for m in gui._NIF_RX.finditer(log)]
    assert mods == [("1", "1", "NIF conversion (all sources)")]
    assert files and files[-1] == (3, 3), files
    assert gui._bar_value(1, *files[-1]) == 1.0


def test_the_sweeps_planning_failure_self_heals_serially(tmp_path, monkeypatch, capsys):
    from tests.test_vanilla_sweep import _mk_data_dir
    mod = tmp_path / "SomeMod"
    mod.mkdir()
    data = _mk_data_dir(tmp_path, [], [])
    pool = _Pool()
    ns = _setup_batch(tmp_path, monkeypatch, pool, [mod, data])
    record = []
    monkeypatch.setattr(ac, "_auto_convert_mod_steps", _steps_recording(
        record, lambda n: [_item(n + "_piece")],
        fail_planning=lambda n, kw: n == "Data" and kw.get("nif_pool") is not None))
    rc = ac._cmd_convert(ns)
    log = capsys.readouterr().out
    assert "retrying SERIALLY" in log and "serial retry SUCCEEDED" in log
    assert "VANILLA SWEEP FAILED" not in log
    plans = [r for r in record if r[0] == "plan" and r[1] == "Data"]
    assert [p[2] for p in plans] == [True, False], "pooled, then no pool"
    assert "claimed-by-the-failed-attempt" not in plans[1][3], (
        "the retry must not skip its own meshes as collisions")
    assert ("finish", "Data", True, []) in record, "the retry converts in-process"
    assert rc == 0, log


# ------------------------------------------------------------ the supersede guard

def test_a_base_an_earlier_source_claimed_is_held_back(tmp_path):
    root = tmp_path / "out" / "meshes" / "!UBE"
    claimed = {(root / "armor" / "set" / "cuirass_1.nif").resolve()}
    move, held = ac._split_claimed_supersedes(
        ["armor/set/cuirass_0.nif", "armor/set/boots_1.nif"], root, claimed)
    assert (move, held) == (["armor/set/boots_1.nif"], ["armor/set/cuirass_0.nif"])


def test_switched_off_every_supersede_moves(tmp_path, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    root = tmp_path / "out" / "meshes" / "!UBE"
    claimed = {(root / "armor" / "set" / "cuirass_1.nif").resolve()}
    assert ac._split_claimed_supersedes(
        ["armor/set/cuirass_0.nif"], root, claimed) == (["armor/set/cuirass_0.nif"], [])


@pytest.mark.parametrize("switched_off", [False, True], ids=["default", "switched-off"])
def test_the_planner_keeps_an_earlier_sources_conversion(
        load_order, tmp_path, monkeypatch, switched_off):
    """A later source that leaves a base to its builder must not move out the
    copy an EARLIER source converted in this run. Switched off it does (the old
    order-dependent behaviour), which is the control."""
    from tests.test_coverage_ube_twin import _twin_of
    if switched_off:
        monkeypatch.setenv(SWITCH, "1")

    class _First(BaseException):
        pass
    dress = ["armor/outfit/dress_1.nif", "armor/outfit/dress_0.nif"]
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, r) for r in dress]
                        + [(src, "armor/outfit/boots_1.nif")])

    def _stop(item):
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _stop)
    out = tmp_path / "out"
    fresh = out / "meshes" / "!UBE" / "armor" / "outfit" / "dress_1.nif"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"this run")
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    with pytest.raises(_First):
        ac.auto_convert_mod(load_order, out, ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1,
                            claimed_dst_paths={fresh.resolve()},
                            built_ube_twin=_twin_of(*dress, mod="Outfit UBE"))
    assert fresh.exists() is not switched_off
