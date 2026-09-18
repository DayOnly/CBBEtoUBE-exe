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

# Test helper (not a test module itself).
"""Worker fn for the _NifPool crash-recovery integration test.

Lives in its own tiny importable module so the spawn-mode pool worker can
re-import it cleanly without pulling in the heavy converter modules."""
import os
from dataclasses import dataclass


@dataclass
class R:
    src_path: str
    dst_path: str = None
    status: str = "converted (copy)"
    reason: str = ""


def echo_pid(item):
    """Where and when this item ran: `reason` = "<pid>:<perf_counter_ns>". The
    pair-unit test reads it to assert a `_1` and its `_0` shared a process and
    ran in order."""
    from time import perf_counter_ns
    return R(src_path=item[0], dst_path=str(item[1]),
             reason=f"{os.getpid()}:{perf_counter_ns()}")


def crash_or_echo(item):
    # item = (src, dst, ...). Simulate a native pynifly crash -- abrupt process
    # death the worker's own try/except can't catch -- for any "POISON" item.
    name = item[0]
    if "POISON" in str(name):
        os._exit(1)
    return R(src_path=name, dst_path=str(name) + ".out")


def warn_and_echo(item):
    """Prints the way the fitting passes do -- a WARN on stdout, a PASS FAILED on
    stderr -- then returns a result. #worker-output"""
    import sys
    name = item[0]
    print(f"WARN probe {name}")
    print(f"PASS FAILED probe {name}", file=sys.stderr)
    return R(src_path=name, dst_path=str(item[1]))


def warn_crash_or_echo(item):
    """`warn_and_echo`, except that a POISON item dies abruptly after printing."""
    name = item[0]
    print(f"WARN probe {name}")
    if "POISON" in str(name):
        os._exit(1)
    return R(src_path=name, dst_path=str(name) + ".out")
