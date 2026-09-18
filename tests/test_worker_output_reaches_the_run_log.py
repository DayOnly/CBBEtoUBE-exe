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

"""#worker-output -- what a pool worker prints reaches the parent, and so the run log.

READ: the run log is a tee installed in the MAIN process after freeze_support(),
which a spawned pool worker never reaches, and under the GUI the child -- and so
every worker -- has stdout and stderr on a null device. The WARN prints in the
fitting passes therefore reached no log in the launch mode users use, and a mesh
that raised crossed the pool as one line with no frames.

pytest's `capsys` sees only THIS process's sys.stdout, never a spawned worker's --
the same property the run log has -- so a line counted here is a line the log
would hold."""
import pickle
from pathlib import Path

from src import auto_convert as ac


def _item(name):
    return (name, Path("out") / "meshes" / "!UBE" / "m" / f"{name}.nif")


def test_a_real_worker_pool_hands_its_prints_to_the_parent(capsys):
    from tests._crash_worker import warn_and_echo
    mgr = ac._NifPool(2)
    names = ("a_1", "a_0", "b_1", "c")
    try:
        results = []
        mgr.run_batch([_item(n) for n in names], results.append, fn=warn_and_echo)
    finally:
        mgr.shutdown()
    out = capsys.readouterr().out
    assert sorted(r.src_path for r in results) == sorted(names)
    for name in names:
        assert out.count(f"WARN probe {name}\n") == 1, out
        assert out.count(f"PASS FAILED probe {name}\n") == 1, out


def test_the_isolated_rerun_after_a_crash_hands_its_prints_over_too(capsys):
    """Every item here shares one destination key, so they form ONE unit: the
    crash breaks it and every innocent is re-run on the isolated path. A parallel
    attempt's output dies with its worker, so each innocent prints exactly once."""
    from tests._crash_worker import warn_crash_or_echo
    mgr = ac._NifPool(2)
    try:
        results = []
        items = [("ok0", 1), ("ok1", 1), ("POISON", 1), ("ok2", 1), ("ok3", 1)]
        mgr.run_batch(items, results.append, fn=warn_crash_or_echo)
    finally:
        mgr.shutdown()
    out = capsys.readouterr().out
    assert mgr.rebuilds >= 1, "nothing crashed, so the isolated path never ran"
    for name in ("ok0", "ok1", "ok2", "ok3"):
        assert out.count(f"WARN probe {name}\n") == 1, (name, out)


def test_a_mesh_that_raises_leaves_its_traceback_in_the_output(monkeypatch):
    def boom(*a, **k):
        raise ValueError("a shape with no vertices")

    monkeypatch.setattr(ac.nif_convert, "convert_nif", boom)
    out = ac._run_unit(ac._nif_convert_worker,
                       [("mod/armor/x_1.nif", "out/x_1.nif", None, None)])
    assert out[0].status == "error"
    assert out[0].reason == "error: ValueError: a shape with no vertices", (
        "the reason must stay one line: it goes to the report and the failures file")
    assert "Traceback (most recent call last)" in out.output, out.output
    assert "ValueError: a shape with no vertices" in out.output, out.output


def test_captured_output_survives_the_trip_between_processes():
    def noisy(it):
        # The class through the module, at call time: 30 test files reload the
        # converter modules, and a ConvertResult imported at the top of this file
        # is then a different class from the one pickle finds by name.
        print("WARN probe", it[0])
        return ac.nif_convert.ConvertResult(src_path=it[0], dst_path=None, status="skipped")

    unit = ac._run_unit(noisy, [_item("a_1")])
    back = pickle.loads(pickle.dumps(unit))
    assert [r.src_path for r in back] == ["a_1"] and back.output == "WARN probe a_1\n"
    one = ac._run_one(noisy, _item("b"))
    back_one = pickle.loads(pickle.dumps(one))
    assert back_one.src_path == "b" and back_one.worker_output == "WARN probe b\n"
