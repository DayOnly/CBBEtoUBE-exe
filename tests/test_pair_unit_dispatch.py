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

"""#pair-unit-dispatch -- a weight pair is one unit of pool work.

A `_0` and its `_1` share one physics XML and one `.tri` at the destination,
and the weight passes read that XML mid-conversion to learn which shapes are
colliders. Two workers converting the pair concurrently made the answer depend
on interleaving: three IDENTICAL 16-worker arms of the 184-NIF acceptance
population differed on 5 collider shapes (all `_1`), each arm odd on a
different subset, the same 0.8486 worst delta every time, 0 vertices moved --
while `--workers 1` is byte-identical run to run.

The pool now dispatches every weight variant of a base to ONE worker as a unit,
in list order. What is pinned here is the contract that removes the race, not
the converter: grouping, one submit per unit, one result per item, in-order
recovery when a unit's worker dies, and (end to end, real subprocesses) that a
pair shares a process and runs `_1` before `_0`.
"""
import sys
from concurrent.futures import Future
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auto_convert import _NifPool, _pair_units, _run_unit  # noqa: E402


def _item(name):
    return (name, Path("out") / "meshes" / "!UBE" / "m" / f"{name}.nif")


def _echo(item):
    from src.nif_convert import ConvertResult
    return ConvertResult(src_path=item[0], dst_path=str(item[1]),
                         status="converted (copy)")


def _done(value=None, exc=None):
    """A real Future (so `as_completed` accepts it), already settled."""
    f = Future()
    if exc is not None:
        f.set_exception(exc)
    else:
        f.set_result(value)
    return f


class _UnitPool:
    """Runs submits inline and records them. `submit(_run_unit, fn, unit)` is
    the parallel shape, `submit(fn, item)` the isolated one; a unit (or item)
    holding a poison item gets a broken future, like a worker that died
    mid-unit."""

    def __init__(self, crash_on=()):
        self.crash_on = set(crash_on)
        self.submits = []            # (fn, [item names]) in submit order

    def submit(self, fn, *args):
        from concurrent.futures.process import BrokenProcessPool
        batch = args[-1] if isinstance(args[-1], list) else [args[-1]]
        self.submits.append((fn, [it[0] for it in batch]))
        if any(it[0] in self.crash_on for it in batch):
            return _done(exc=BrokenProcessPool("simulated crash"))
        return _done(value=fn(*args))

    def shutdown(self, wait=True):
        pass


def test_units_group_every_weight_variant_in_list_order():
    """`_1`, `_0` AND the no-suffix model of one base form one unit, in the
    order they were listed; bases keep first-appearance order."""
    items = [_item("a_1"), _item("b_1"), _item("a_0"), _item("a"),
             _item("b_0"), _item("c")]
    units = _pair_units(items)
    assert [[it[0] for it in u] for u in units] == [
        ["a_1", "a_0", "a"], ["b_1", "b_0"], ["c"]]


def test_units_are_keyed_on_the_destination_not_the_source():
    """The rival variant routinely comes from another mod, so the SOURCE path
    says nothing about sharing; the destination is what the pair writes."""
    a = ("modA/x_1.nif", Path("out/meshes/!UBE/m/x_1.nif"))
    b = ("modB/other/x_0.nif", Path("out/meshes/!UBE/m/x_0.nif"))
    assert [[it[0] for it in u] for u in _pair_units([a, b])] == [[a[0], b[0]]]


def test_a_unit_is_one_submit_and_delivers_one_result_per_item():
    pool = _UnitPool()
    mgr = _NifPool(2, pool_factory=lambda: pool)
    items = [_item("a_1"), _item("a_0"), _item("b_1"), _item("b_0")]
    results = []
    mgr.run_batch(items, results.append, fn=_echo)
    assert [names for _fn, names in pool.submits] == [["a_1", "a_0"],
                                                      ["b_1", "b_0"]]
    assert all(fn is _run_unit for fn, _n in pool.submits)
    assert sorted(r.src_path for r in results) == ["a_0", "a_1", "b_0", "b_1"]
    assert mgr.rebuilds == 0


def test_run_unit_keeps_order_and_returns_one_result_per_item():
    unit = [_item("a_1"), _item("a_0"), _item("a")]
    out = _run_unit(_echo, unit)
    assert [r.src_path for r in out] == ["a_1", "a_0", "a"]


def test_a_crashed_unit_reruns_in_order_and_errors_only_the_crasher():
    """A worker dying mid-unit takes its unit-mates' results with it; the
    isolated re-run walks that unit in LIST order (the pair stays sequential)
    and the other units are untouched."""
    pool = _UnitPool(crash_on={"a_0"})
    mgr = _NifPool(2, pool_factory=lambda: pool)
    items = [_item("a_1"), _item("a_0"), _item("b_1"), _item("b_0")]
    results = []
    mgr.run_batch(items, results.append, fn=_echo)
    by = {r.src_path: r for r in results}
    assert len(results) == 4
    assert by["a_1"].status.startswith("converted")
    assert by["a_0"].status == "error" and "died" in by["a_0"].reason
    assert by["b_1"].status.startswith("converted")
    assert by["b_0"].status.startswith("converted")
    isolated = [names for fn, names in pool.submits if fn is not _run_unit]
    assert isolated == [["a_1"], ["a_0"]], "list order, one at a time"
    assert mgr.rebuilds == 2, "once for the unit, once for the crasher"


def test_real_subprocess_pair_shares_a_process_and_runs_in_order():
    """End to end with REAL spawned workers: each pair lands on one PID and its
    `_1` finishes before its `_0` starts -- serial's order for the files that
    share state."""
    from tests._crash_worker import echo_pid
    mgr = _NifPool(2)
    try:
        items = [_item("x_1"), _item("x_0"), _item("y_1"), _item("y_0"),
                 _item("z_1"), _item("z_0")]
        results = []
        mgr.run_batch(items, results.append, fn=echo_pid)
    finally:
        mgr.shutdown()
    by = {r.src_path: r.reason.split(":") for r in results}
    assert len(by) == 6
    for base in ("x", "y", "z"):
        pid1, t1 = by[f"{base}_1"]
        pid0, t0 = by[f"{base}_0"]
        assert pid1 == pid0, f"{base}: the pair must share a worker"
        assert int(t1) < int(t0), f"{base}: `_1` runs before `_0`"
