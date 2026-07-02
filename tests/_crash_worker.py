# CBBEtoUBE - test helper (not a test module itself).
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
