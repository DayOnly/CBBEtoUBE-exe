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


def crash_or_echo(item):
    # item = (src, dst, ...). Simulate a native pynifly crash -- abrupt process
    # death the worker's own try/except can't catch -- for any "POISON" item.
    name = item[0]
    if "POISON" in str(name):
        os._exit(1)
    return R(src_path=name, dst_path=str(name) + ".out")
