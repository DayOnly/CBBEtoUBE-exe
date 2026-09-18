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

"""The one way a warning reaches a user. #user-warnings

A run's log is read by the person who ran it, in a chat window, usually with
the question "what does this mean?". Measured on 2026-09-01 over the 62 warning
lines the batch could print: 25 used internal vocabulary, 31 said nothing about
what to do next, 8 did not name the mod or file, and 28 printed a Python repr
-- `OSError(13, 'Permission denied')` -- to someone who launched the tool from a
window. The same repr reached the end-of-run popup through the failures file.

So every warning is written here in one shape, the shape the two good ones
already had (the SkyPatcher banner and the low-memory banner):

    !! WHAT happened -- WHERE (the mod, the file, the folder)
       what it means for the run (CONSEQUENCE)
       FIX: what to do next

`warn` prints it and returns the text, so the same words can go into the
failures file and the popup. `plain_error` turns an exception into
"PermissionError: [Errno 13] Permission denied" -- the type and the message,
never a repr.
docs/WARNINGS.md lists every warning the converter can print, with its
consequence and fix, generated from these calls by scripts/warning_surface.py.
"""
from __future__ import annotations

import sys

#: The marker every problem line starts with. USING.md tells the user to look
#: for it, and tests count it, so it is a constant, not a spelling.
PROBLEM = "!!"
#: For information that needs no action.
NOTE = "NOTE:"


def plain_error(exc: BaseException) -> str:
    """`OSError: Permission denied`: the type and the message, never a repr.
    An exception with no message reads as its type alone."""
    msg = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def warn(what: str, *, where: str = "", consequence: str = "", fix: str = "",
         level: str = PROBLEM, indent: str = "  ", file=None) -> str:
    """Print one warning in the house shape and return its text.

    `what` is the fact, in words the reader has (a mod, a file, a folder, a
    count); `where` names the place when the fact does not; `consequence` says
    what it means for the run; `fix` says what to do next. `indent` is the
    marker line's prefix (a leading newline separates a block from the log
    above it); the other lines sit three columns in from the marker so the
    block reads as one warning."""
    head = f"{indent}{level} {what}"
    if where:
        head += f" -- {where}"
    pad = indent.lstrip("\n") + "   "
    lines = [head]
    if consequence:
        lines.append(f"{pad}{consequence}")
    if fix:
        lines.append(f"{pad}FIX: {fix}")
    text = "\n".join(lines)
    print(text, file=file if file is not None else sys.stdout)
    return text.lstrip("\n")
