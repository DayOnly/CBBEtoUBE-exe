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

"""Cap the BLAS thread pools BEFORE numpy/scipy are imported.  #blas-thread-cap

WHY THIS EXISTS, and why it is a separate module with no dependencies beyond
`os`: it must run before the FIRST numpy import in EVERY process, and it is
called from two places that cannot import each other -- the frozen entry point
`cbbe_to_ube_main.py` and the package's own `auto_convert`.

MEASURED on a 24-thread box, the repo's own interpreter, private commit charge
(`PrivateUsage` from `K32GetProcessMemoryInfo`), 2026-09-11:

    bare python                                    5.9 MB commit,  11.1 MB working set
    + import numpy                               754.0 MB          23.1 MB
    + from scipy.spatial import cKDTree         1516.3 MB          54.0 MB
    same, with these three vars set to "1"        37.9 MB          51.1 MB

A 97.5% cut in commit charge with the WORKING SET UNCHANGED. That is the whole
point, and it is why this never showed up in a working-set measurement: the
bytes are OpenBLAS per-thread arenas, committed at DLL init and never touched.
Two OpenBLAS copies ship in the bundle (numpy.libs and scipy.libs), each
reserving about 32 MB per hardware thread -- a slope of 64.3 MB per CPU thread
per process.

WHY IT IS A MEMORY *ERROR* AND NOT JUST WASTE. Windows fails an allocation
against the COMMIT LIMIT (physical RAM + page file), not against free RAM.
Untouched-but-committed bytes count in full. Every worker is a fresh spawn of
this exe -- nothing is shared copy-on-write -- so the pool multiplies the
figure above by (workers + the GUI + the child converter). On a 16 GB machine
with 16 threads and a DISABLED or small page file, the commit limit is about
the RAM, and 10 processes x 1.0 GB exhausts it before a single mesh is read.
A machine with a large page file merely pages, which is why this reproduces
for some users and not others on identical builds.

BLAS THREADS BUY NOTHING HERE. The pipeline is elementwise numpy and cKDTree
queries; it never calls a threaded BLAS kernel on an array large enough to
benefit. With N worker processes already saturating the CPU, per-worker BLAS
threading is pure oversubscription.

`setdefault`, not assignment: an explicit OPENBLAS_NUM_THREADS in the
environment still wins, so this can be A/B tested without a rebuild.
"""
import os

# OpenBLAS ships in both numpy.libs and scipy.libs; OMP covers the OpenMP
# runtime either of them may load; MKL covers a numpy built against it.
BLAS_THREAD_VARS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
)


def cap_blas_threads(value: str = "1") -> None:
    """Set every BLAS thread-count var to `value` unless already set.

    Idempotent, dependency-free, and safe to call after numpy is already
    imported -- it simply has no effect then, which is why every call site must
    be ABOVE its module's numpy import rather than inside a function."""
    for _var in BLAS_THREAD_VARS:
        os.environ.setdefault(_var, value)
