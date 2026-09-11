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

"""#blas-thread-cap -- BLAS thread arenas are capped before numpy is imported.

WHY THIS FILE EXISTS. Users reported "memory errors" on 1.4. The cause was not
an algorithm: `import numpy` plus `from scipy.spatial import cKDTree` committed
1516.3 MB of Windows commit charge on a 24-thread box, of which ~97% was
OpenBLAS per-thread arenas that are committed at DLL init AND NEVER TOUCHED.
Measured the same day, same interpreter, via `PrivateUsage`:

    bare python                      5.9 MB commit    11.1 MB working set
    + numpy                        754.0 MB           23.1 MB
    + scipy.spatial               1516.3 MB           54.0 MB
    capped to 1 thread              37.9 MB           51.1 MB

The working set barely moves, which is why no working-set measurement ever
caught it. Windows fails an allocation against the COMMIT LIMIT (RAM + page
file), and every worker is a fresh spawn paying the full amount, so a pool of 8
plus the GUI and the child converter reserved ~15 GB before reading one mesh.
On a machine whose page file is disabled or pinned small -- widely repeated
"gaming performance" advice -- that alone exhausts a 16 GB box.

THE ORDER IS THE WHOLE FIX. Setting these variables after numpy loads is a
silent no-op, and would leave every assertion here passing on a broken build.
That is what `test_the_cap_is_set_before_numpy_is_imported` exists to prevent."""
import os
import subprocess
import sys
import textwrap

import pytest

from src import blas_env


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(code, env=None):
    e = dict(os.environ)
    for v in blas_env.BLAS_THREAD_VARS:      # start from a genuinely clean slate
        e.pop(v, None)
    e.update(env or {})
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                       cwd=REPO, env=e, capture_output=True, text=True)
    assert r.returncode == 0, f"probe failed rc={r.returncode}\n{r.stderr}"
    return r.stdout.strip()


def test_importing_auto_convert_sets_every_thread_var():
    out = _run("""
        import src.auto_convert, os
        print("|".join(os.environ.get(v, "UNSET")
                       for v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
                                 "MKL_NUM_THREADS")))
    """)
    assert out == "1|1|1", out


def test_the_cap_is_set_before_numpy_is_imported():
    """THE control. Setting these after numpy loads does nothing at all.

    Asserting only that the variables are set would pass on a build where the
    cap runs too late and saves nothing -- a detector that cannot fail on the
    defect it exists to catch. So assert the ORDER directly: at the moment
    `blas_env` runs, numpy must not yet be in sys.modules."""
    out = _run("""
        import sys
        assert "numpy" not in sys.modules, "numpy loaded before the app"
        import src.blas_env
        print("numpy" in sys.modules)
    """)
    assert out == "False", (
        "src.blas_env pulled in numpy itself -- it must stay dependency-free "
        "or the cap is applied after the arenas are already committed")


def test_the_frozen_entry_point_caps_at_module_level():
    """A pool worker RE-LAUNCHES the exe, so the entry point must cap on import.

    If the cap moved inside `if __name__ == '__main__'`, the process the user
    starts would be capped and every worker -- which is where the memory
    actually goes -- would not."""
    out = _run("""
        import runpy, os, sys
        sys.argv = ["cbbe_to_ube_main.py", "--help"]
        try:
            runpy.run_path(os.path.join(%r, "cbbe_to_ube_main.py"),
                           run_name="not_main")
        except SystemExit:
            pass
        print(os.environ.get("OPENBLAS_NUM_THREADS", "UNSET"))
    """ % REPO)
    assert out == "1", (
        "importing the entry point did not cap BLAS threads; a spawned worker "
        "re-imports this module and would commit ~1.5 GB of arena")


def test_an_explicit_setting_still_wins():
    """`setdefault`, so the cap can be A/B tested without a rebuild."""
    out = _run("""
        import src.auto_convert, os
        print(os.environ["OPENBLAS_NUM_THREADS"])
    """, env={"OPENBLAS_NUM_THREADS": "4"})
    assert out == "4"


@pytest.mark.skipif(os.name != "nt", reason="commit charge is a Windows notion")
def test_importing_the_converter_costs_little_commit_charge():
    """The measurement itself, as a regression guard.

    This is the number that decides whether a user's run dies, so it is asserted
    rather than described. Uncapped this process commits ~1.5 GB on a 24-thread
    box; the bound is deliberately loose (400 MB) because the arena scales with
    the CPU count of whatever machine runs the suite -- on a 64-thread runner
    the uncapped figure would be ~4 GB, so 400 MB cannot pass by accident."""
    out = _run("""
        import ctypes
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]
        import src.auto_convert
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        fn = k32.K32GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        print(c.PrivateUsage // (1024 * 1024))
    """)
    mb = int(out)
    assert mb < 400, (
        f"importing the converter commits {mb} MB; the BLAS thread cap is not "
        f"working. Every worker process pays this in full.")
