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

"""#commit-headroom -- a memory death must not look like a clean finish.

Two defects, both found by auditing what a user could SEND after a failure
rather than by reading the conversion code:

  1. `sanitize_output_vertex_color_flags` wrapped its whole pool in a bare
     `except Exception` that restarted serially and returned a clean stats
     dict. The sweep runs at the very END of a multi-hour run and spawns a
     fresh pool that re-imports numpy in every process, so it is the likeliest
     moment in the run for a worker to be killed for memory -- and that death
     printed nothing, counted nothing, and left no entry in the failures file.
     The run reported SUCCESS.

  2. The run log is the ONLY artefact that survives a hard kill. It is
     line-buffered, so it holds everything up to the instant of death;
     conversion_report.json, conversion_settings.json and
     conversion_summary.txt are all written at the end of a batch and simply do
     not exist afterwards. Both the CLI entry point (`open(path, "w")`) and the
     GUI (`os.remove` before spawning the child) destroyed it -- so the tool's
     own advice after a memory death, "run again with fewer workers", erased
     the evidence of the failure it was reacting to."""
import importlib.util
import os
import tempfile

import pytest

from src import nif_convert_writer as ncw


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe", os.path.join(REPO, "cbbe_to_ube_main.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- 1. the sweep must report a dead worker ---------------------------------

class _DeadPool:
    """A pool whose map() dies the way an OOM-killed worker does."""
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, *a, **k):
        from concurrent.futures.process import BrokenProcessPool
        raise BrokenProcessPool("A process in the process pool was terminated")


def _sweep_one_file(monkeypatch, tmp_path, pool):
    """Run the sweep over enough files to use a pool, with the NIF loader stubbed.

    The serial fallback feeds every file to the native pynifly loader, and
    handing that 70 files of rubbish hangs the DLL rather than raising -- so the
    loader is stubbed to refuse. That keeps this test about the two things it
    names (was the death REPORTED, did the fallback RUN) instead of about NIF
    parsing, which other files cover."""
    import concurrent.futures
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", pool)
    from src import nif_io

    def _no(_p):
        raise OSError("stubbed: not a real nif")
    monkeypatch.setattr(nif_io, "load_nif", _no)
    d = tmp_path / "meshes"
    d.mkdir()
    for i in range(70):            # over the serial-bail threshold
        (d / f"x{i}.nif").write_bytes(b"not really a nif")
    return ncw.sanitize_output_vertex_color_flags(str(d), workers=2)


def test_a_dead_worker_in_the_sweep_is_reported(monkeypatch, tmp_path):
    """THE REGRESSION GUARD. Before the fix this returned a clean dict."""
    out = _sweep_one_file(monkeypatch, tmp_path, _DeadPool)
    assert out.get("pool_error"), (
        "a BrokenProcessPool in the vertex-colour sweep produced no "
        "pool_error, so the run reports success after a worker was killed")
    assert "worker died" in out["pool_error"]
    assert "Worker processes" in out["pool_error"], (
        "the message must name the lever that fixes it")


def test_the_sweep_still_falls_back_to_serial(monkeypatch, tmp_path):
    """Reporting must not cost the recovery: the output still gets swept."""
    out = _sweep_one_file(monkeypatch, tmp_path, _DeadPool)
    assert out["files"] == 70, "the serial fallback did not run"


def test_the_call_site_surfaces_it(monkeypatch):
    """A stats key nobody reads is not a fix. Pin the call site."""
    import inspect
    from src import auto_convert as ac
    src = inspect.getsource(ac._cmd_convert)
    i = src.find("sanitize_output_vertex_color_flags")
    assert i != -1
    after = src[i:i + 900]
    assert "pool_error" in after, (
        "the sanitize result's pool_error is never read, so a dead worker is "
        "still silent to the user")
    assert "_record_failure" in after, (
        "it must reach the failures file too -- that is the artefact the bug "
        "report asks for")


# --- 2. the run log must survive the next run -------------------------------

def test_the_run_log_is_rotated_not_truncated():
    m = _entry_module()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "CBBEtoUBE_last_run.log")
    with open(p, "w", encoding="utf-8") as f:
        f.write("the run that died")
    m._rotate_previous(p)
    prev = os.path.join(d, "CBBEtoUBE_last_run_previous.log")
    assert os.path.exists(prev), "the previous run's log was destroyed"
    with open(prev, encoding="utf-8") as f:
        assert f.read() == "the run that died"
    assert not os.path.exists(p), "the live path must be free for the new run"


def test_rotation_does_not_mangle_a_path_containing_the_extension():
    """`str.replace('.log','')` would corrupt this. The path is the user's own
    MO2 layout, so a directory called anything is possible."""
    m = _entry_module()
    d = tempfile.mkdtemp()
    sub = os.path.join(d, "my.logs")
    os.makedirs(sub)
    p = os.path.join(sub, "CBBEtoUBE_last_run.log")
    with open(p, "w", encoding="utf-8") as f:
        f.write("x")
    m._rotate_previous(p)
    assert os.path.isdir(sub), "the directory name was mangled"
    assert os.path.exists(os.path.join(sub, "CBBEtoUBE_last_run_previous.log"))


def test_rotation_is_a_no_op_when_there_is_nothing_to_rotate():
    m = _entry_module()
    d = tempfile.mkdtemp()
    m._rotate_previous(os.path.join(d, "absent.log"))   # must not raise
    assert os.listdir(d) == []


def test_the_gui_renames_the_log_instead_of_deleting_it():
    """The GUI is the path MO2 uses, and it deleted BOTH artefacts up front."""
    import inspect
    from src import gui
    src = inspect.getsource(gui)
    i = src.find('state["fail_path"] = fail_path')
    assert i != -1, "the GUI's failure-file bookkeeping moved"
    around = src[max(0, i - 1200):i + 700]
    assert "os.remove(log_path)" not in around, (
        "the GUI still deletes the previous run's log before spawning the "
        "child -- the one artefact that survives an out-of-memory death")
    assert "os.remove(fail_path)" not in around, (
        "the GUI still deletes the previous run's failure summary")
    assert "CBBEtoUBE_previous_run.log" in around
