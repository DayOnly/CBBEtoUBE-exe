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

"""#workers-die-with-parent -- pool workers must not outlive the conversion.

MEASURED on the 1.4.1 exe: a parent-only kill of a two-worker run left both
workers alive minutes later, holding 811-944 MB of commit each and the exe
locked, and 8 converted NIFs were written up to 52 s after the parent died. The
GUI's own Cancel kills the whole tree and was never affected; every other way
the parent can end was.

The behavioural test reproduces the mechanism with nothing but a real
spawn-mode pool and the job: a proxy parent starts a worker and is then killed
with TerminateProcess -- no shutdown, no atexit, no finally -- and the worker
must be gone. The structural tests pin that both places the converter makes a
process pool tie the process first."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_PROXY_PARENT = '''
import os, sys, time
sys.path.insert(0, {repo!r})
from concurrent.futures import ProcessPoolExecutor
from src import child_lifetime

if __name__ == "__main__":
    print("tied", child_lifetime.tie_children_to_this_process(), flush=True)
    pool = ProcessPoolExecutor(max_workers=1)
    print("worker", pool.submit(os.getpid).result(), flush=True)
    time.sleep(300)
'''


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_a_killed_parent_takes_its_workers_with_it(tmp_path):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    synchronize, process_terminate, wait_object_0 = 0x00100000, 0x0001, 0

    script = tmp_path / "proxy_parent.py"
    script.write_text(_PROXY_PARENT.format(repo=str(REPO)), encoding="utf-8")
    out = tmp_path / "proxy.out"
    with open(out, "w", encoding="utf-8") as fh:
        proxy = subprocess.Popen([sys.executable, str(script)], stdout=fh,
                                 stderr=subprocess.STDOUT, cwd=str(tmp_path))
    worker = handle = None
    try:
        deadline = time.monotonic() + 90
        while worker is None and time.monotonic() < deadline:
            for line in out.read_text(encoding="utf-8").splitlines():
                if line.startswith("worker "):
                    worker = int(line.split()[1])
            if worker is None and proxy.poll() is not None:
                break
            time.sleep(0.2)
        text = out.read_text(encoding="utf-8")
        assert worker is not None, f"the proxy never reported a worker:\n{text}"
        assert "tied True" in text, f"the job could not be joined here:\n{text}"
        # Open the worker BEFORE the kill: the handle pins that exact process,
        # so a recycled pid can never answer for it.
        handle = k32.OpenProcess(synchronize | process_terminate, False, worker)
        assert handle, "could not open the worker process"
        proxy.kill()                  # TerminateProcess on the parent only
        proxy.wait(30)
        exited = k32.WaitForSingleObject(handle, 15000) == wait_object_0
        assert exited, "the worker outlived its killed parent by 15 s -- an orphan"
    finally:
        if proxy.poll() is None:
            proxy.kill()
        if handle:
            if k32.WaitForSingleObject(handle, 0) != wait_object_0:
                k32.TerminateProcess(handle, 1)   # never leak an orphan
            k32.CloseHandle(handle)


def _recorder(order):
    def _tie():
        order.append("tie")
        return True
    return _tie


def test_the_batch_pool_ties_the_process_before_it_exists(monkeypatch):
    """Including the pool rebuilt after a worker crash."""
    from src import auto_convert as ac
    from src import child_lifetime
    order = []
    monkeypatch.setattr(child_lifetime, "tie_children_to_this_process",
                        _recorder(order))

    class _Pool:
        def __init__(self, *a, **k):
            order.append("pool")

        def shutdown(self, wait=True):
            pass

    monkeypatch.setattr(ac, "ProcessPoolExecutor", _Pool)
    mgr = ac._NifPool(2)
    assert order == ["tie", "pool"], order
    mgr._rebuild()
    assert order[-2:] == ["tie", "pool"], order
    mgr.shutdown()


def test_an_injected_test_pool_leaves_the_process_alone(monkeypatch):
    """Fakes must not put the process running the tests into a job."""
    from src import auto_convert as ac
    from src import child_lifetime
    order = []
    monkeypatch.setattr(child_lifetime, "tie_children_to_this_process",
                        _recorder(order))
    ac._NifPool(2, pool_factory=lambda: object())
    assert order == []


def test_the_end_of_run_sweep_pool_ties_the_process_first(monkeypatch,
                                                           tmp_path):
    """The vertex-colour sweep builds a fresh pool at the very end of a run."""
    import concurrent.futures
    from src import child_lifetime
    from src import nif_convert_writer as ncw
    order = []
    monkeypatch.setattr(child_lifetime, "tie_children_to_this_process",
                        _recorder(order))

    class _Pool:
        def __init__(self, *a, **k):
            order.append("pool")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, fn, items, chunksize=1):
            return [0 for _ in items]

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", _Pool)
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    for i in range(70):                  # over the serial-bail threshold
        (meshes / f"x{i}.nif").write_bytes(b"not really a nif")
    out = ncw.sanitize_output_vertex_color_flags(str(meshes), workers=2)
    assert order == ["tie", "pool"], order
    assert out.get("pool_error") is None


def test_tying_is_tried_once_and_never_raises(monkeypatch, capsys):
    """Counted, not raised: the function swallows every exception by design,
    so a second-call stub that RAISES could never fail this test -- a mutation
    that removed the once-only guard passed it that way."""
    from src import child_lifetime as cl
    monkeypatch.setattr(cl, "_STATE", None)
    monkeypatch.setattr(cl, "_JOB", None)
    attempts = []

    def _refused():
        attempts.append("attempt")
        raise OSError("refused")

    monkeypatch.setattr(cl, "_create_kill_on_close_job_and_join", _refused)
    assert cl.tie_children_to_this_process() is False
    assert cl.tie_children_to_this_process() is False
    if sys.platform == "win32":
        assert attempts == ["attempt"], f"tying was attempted {len(attempts)}x"
        assert capsys.readouterr().out.count("could not be tied") == 1
    else:
        assert attempts == [], "tying was attempted off Windows"
