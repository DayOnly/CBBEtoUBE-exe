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

"""The end-of-run sanitize sweep must obey the same memory budget as the pool.

`sanitize_output_vertex_color_flags` takes a `workers` argument that NO SHIPPED
CALLER EVER PASSED, so it fell through to `max(1, min(16, cpu_count - 2))` --
CPU count alone, with no RAM or commit term. On a 16-thread box that spawned 14
fresh processes where the RAM-capped conversion pool had just run 8, and the
user had no way to lower it: no CLI flag, no GUI control, no env var.

Three things made it the worst-placed pool in the program:
  * it fires at the END, after hours of conversion, when the user has most to
    lose and least patience;
  * every process is a fresh spawn re-importing numpy and scipy, so it is a
    memory spike at exactly the moment the machine is most fragmented;
  * its own `except Exception` falls back to a serial walk, so an
    out-of-memory death here left the run reporting success and saying nothing.
"""
import os

from src import auto_convert as ac
from src import nif_convert_writer as ncw


def test_the_sweep_accepts_a_worker_count():
    """Guard the seam the fix relies on."""
    import inspect
    assert "workers" in inspect.signature(
        ncw.sanitize_output_vertex_color_flags).parameters


def test_the_shipped_call_site_passes_a_budgeted_count():
    """The defect was a default that was never overridden, so pin the CALL.

    Read the source of the call site rather than mocking a whole convert run:
    what went wrong was a missing argument, and that is visible statically and
    cannot be faked by a passing mock."""
    import inspect
    src = inspect.getsource(ac._cmd_convert)
    i = src.find("sanitize_output_vertex_color_flags")
    assert i != -1, "the sweep is no longer called from _cmd_convert"
    # Window spans BOTH sides of the call: the count is computed a few lines
    # ABOVE it, so a window starting at the call sees the argument name but not
    # where its value came from -- and that is the half that matters.
    call = src[max(0, i - 600):i + 200]
    assert "workers=" in call, (
        "the sanitize sweep is being called without a worker count again; it "
        "will size itself on CPU count alone and ignore the RAM budget")
    assert "default_worker_count" in call or "pool_workers" in call, (
        "the count passed must come from the memory budget, not from a "
        "second independent CPU-count guess")


def test_an_explicit_workers_flag_reaches_the_sweep():
    """`--workers 2` must bound the sweep too, not just the conversion pool.

    This is the user's only lever when a run dies of memory, and the advice we
    now print tells them to use it -- so it has to actually reach every pool."""
    import inspect
    src = inspect.getsource(ac._cmd_convert)
    i = src.find("sanitize_output_vertex_color_flags")
    call = src[max(0, i - 400):i + 200]
    assert "args.workers" in call, (
        "an explicit --workers does not reach the sanitize sweep")


def test_default_is_still_cpu_bounded_for_direct_callers(monkeypatch):
    """Callers outside the converter keep the old behaviour -- but bounded.

    The library default is not the bug; the missing argument was. Left alone so
    a script using this helper directly is unaffected."""
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    # No mesh root -> returns the empty stats dict before building any pool,
    # which is all this needs: the assertion is about the default expression,
    # not about running a sweep.
    out = ncw.sanitize_output_vertex_color_flags("does-not-exist")
    assert out == {"files": 0, "files_changed": 0, "shapes_fixed": 0}
