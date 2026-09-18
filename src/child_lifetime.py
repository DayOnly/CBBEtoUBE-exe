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

"""Pool workers die with the process that started them. #workers-die-with-parent

A ProcessPoolExecutor worker on Windows waits in a blocking read of its call
queue, and it holds its own duplicate of that queue's WRITE handle -- so when
its parent disappears without shutting the pool down, the read never reaches
end of file and the worker waits forever. Measured on the 1.4.1 exe: after a
parent-only kill (Task Manager's End task, Stop-Process, a launcher's kill, a
native crash), both workers of a two-worker run were still alive minutes later,
holding 811-944 MB of commit each and keeping CBBEtoUBE.exe locked against a
redeploy -- and 8 converted NIFs were written 7.7-52.4 s AFTER the parent had
died, into an output no report describes.

The GUI's own Cancel was never affected: it kills the whole process tree. This
covers every other way the conversion process can end.

The fix is a Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. The
conversion process puts ITSELF into the job before it creates a pool, so every
worker it spawns afterwards -- a pool rebuilt after a crash and the pre-warm
manager included -- is born inside it. The job handle is private to this
process (not inheritable), so the moment this process ends, however it ends,
Windows closes the handle and terminates everything still in the job.

Call it only from a process whose children must never outlive it. NEVER from
the GUI window: what it opens (a browser, an Explorer window) would die with
it."""
from __future__ import annotations

import sys

_JOB = None       # the job handle, held open for the life of the process
_STATE = None     # None = not tried yet; True / False = the outcome


def tie_children_to_this_process() -> bool:
    """Put this process into a kill-on-close job, once. True if the children it
    starts from now on will die with it.

    Never raises: tying is a safety net, not a precondition, so a refusal prints
    one note and the run carries on exactly as it did before this existed."""
    global _JOB, _STATE
    if _STATE is not None:
        return _STATE
    if sys.platform != "win32":
        _STATE = False
        return _STATE
    try:
        _JOB = _create_kill_on_close_job_and_join()
        _STATE = True
    except Exception as e:
        _STATE = False
        print(f"  note: worker processes could not be tied to this one ({e}); "
              "if this process is killed, end any leftover CBBEtoUBE "
              "processes in Task Manager", flush=True)
    return _STATE


def _create_kill_on_close_job_and_join():
    """Create the job, set kill-on-close, and assign the current process to it.
    Returns the job handle; raises OSError on any refusal."""
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x2000

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    # A pseudo-handle: without the restype it is truncated to a C int and the
    # assignment fails with "handle is invalid".
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    # NULL security attributes: the handle is NOT inheritable. That matters --
    # a worker holding its own copy would keep the job open after this process
    # died, and nothing would be killed.
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    ok = k32.SetInformationJobObject(job, job_object_extended_limit_information,
                                     ctypes.byref(info), ctypes.sizeof(info))
    if ok:
        ok = k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
    if not ok:
        err = ctypes.WinError(ctypes.get_last_error())
        k32.CloseHandle(job)
        raise err
    return job
