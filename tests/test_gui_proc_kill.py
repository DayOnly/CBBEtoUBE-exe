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

"""Guard: closing/cancelling the GUI must TREE-kill the conversion subprocess so
the ProcessPoolExecutor workers (each a re-launched CBBEtoUBE.exe under spawn)
don't orphan and lock the exe. On Windows a plain proc.terminate() leaves them."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import gui


class _FakeProc:
    def __init__(self, alive=True, pid=4321):
        self._alive = alive
        self.pid = pid
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True


def test_none_is_noop():
    gui._kill_proc_tree(None)   # must not raise


def test_windows_uses_taskkill_tree(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    calls = []
    # fire-and-forget: the tree-kill uses Popen (non-blocking), not run.
    monkeypatch.setattr(gui.subprocess, "Popen",
                        lambda *a, **k: calls.append((a, k)))
    p = _FakeProc(alive=True, pid=999)
    gui._kill_proc_tree(p)
    assert calls, "taskkill was not invoked"
    argv = calls[0][0][0]
    assert argv[0] == "taskkill" and "/T" in argv and "/F" in argv
    assert "999" in argv                       # targets the parent PID
    assert not p.terminated                    # tree-kill, not bare terminate
    print("  test_windows_uses_taskkill_tree OK")


def test_non_windows_falls_back_to_terminate(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    p = _FakeProc(alive=True)
    gui._kill_proc_tree(p)
    assert p.terminated
    print("  test_non_windows_falls_back_to_terminate OK")


def test_taskkill_failure_falls_back(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    def _boom(*a, **k):
        raise OSError("taskkill missing")
    monkeypatch.setattr(gui.subprocess, "Popen", _boom)
    p = _FakeProc(alive=True)
    gui._kill_proc_tree(p)
    assert p.terminated                        # fell back to terminate()
    print("  test_taskkill_failure_falls_back OK")
