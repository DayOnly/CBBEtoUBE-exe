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

"""What "Save diagnostics zip" adds from disk, without Tk. #diagnostics-zip

The zip held REPORT.txt, the GUI's log panel, settings, exclusions, the layout
and a fresh preflight -- and none of the files REPORTING.md tells a user to
send. After a run died and the GUI was restarted the log panel is empty, so the
zip carried no log at all; `CBBEtoUBE_previous_run.log`, "the one to attach if
a run DIED", was never in it. Nor were the failures files, the output mod's
conversion_report.json / conversion_settings.json, or whether the page file is
system-managed, a fixed size or off -- the first thing a memory error needs
answered (#commit-headroom).

`collect()` returns {name in the zip: bytes}: each of those files that exists,
and a machine.txt that is always there."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Beside the exe (the tool folder): what the last run and the one before left.
TOOL_FILES = ("CBBEtoUBE_last_run.log", "CBBEtoUBE_previous_run.log",
              "CBBEtoUBE_last_failures.json", "CBBEtoUBE_previous_failures.json")
# At the output mod root: what the last run wrote. Both are written before the
# first source, and the report again after every source, so a run that died
# leaves them too, the report marked incomplete. #report-checkpoint
OUTPUT_FILES = ("conversion_report.json", "conversion_settings.json")
MACHINE = "machine.txt"

_MEMORY_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
_THIS_MACHINE = object()


def read_paging_files() -> "list[str] | None":
    """The PagingFiles registry value, one string per page file, or None when
    it cannot be read (not Windows, or no access). Read-only."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MEMORY_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "PagingFiles")
    except OSError:
        return None
    if isinstance(value, str):
        value = [value]
    return [v for v in (value or []) if v and v.strip()]


def page_file_mode(paging_files) -> str:
    """One line for a person: how Windows sizes the page file.

    Each PagingFiles entry is `<path> <min MB> <max MB>`. A `?:` path means
    system-managed on every drive, `0 0` system-managed on that drive, two other
    numbers a fixed size, and no entry at all means there is no page file."""
    if paging_files is None:
        return "unknown (the page file setting could not be read)"
    if not paging_files:
        return "none -- the page file is OFF, so the commit limit is your RAM"
    managed, fixed = [], []
    for entry in paging_files:
        words = entry.split()
        path = words[0] if words else entry.strip()
        if path.startswith("?:"):
            return "system-managed on every drive"
        if len(words) >= 3 and not (words[1] == "0" and words[2] == "0"):
            fixed.append(f"{path} {words[1]}-{words[2]} MB")
        else:
            managed.append(path)
    if fixed and managed:
        return "mixed: " + "; ".join(fixed + [f"{p} system-managed" for p in managed])
    if fixed:
        return "fixed size: " + "; ".join(fixed)
    return "system-managed: " + "; ".join(managed)


def machine_text(memory, paging_files, cpu_threads=None) -> str:
    """machine.txt: RAM, commit limit, page file mode, CPU threads."""
    m = memory or {}

    def gb(key):
        v = m.get(key)
        return "?" if v is None else f"{v:.1f} GB"

    return "\n".join([
        f"RAM: {gb('total_gb')} total, {gb('avail_gb')} free",
        f"commit limit (RAM + page file): {gb('commit_limit_gb')}, "
        f"{gb('commit_free_gb')} free now",
        f"page file: {page_file_mode(paging_files)}",
        f"CPU threads: {cpu_threads if cpu_threads is not None else os.cpu_count()}",
    ]) + "\n"


def collect(tool_dir, output_dir, *, memory=_THIS_MACHINE,
            paging_files=_THIS_MACHINE) -> dict:
    """{name in the zip: bytes}: the run files that exist, and machine.txt.
    `memory` and `paging_files` default to this machine's."""
    out = {}
    for folder, names in ((tool_dir, TOOL_FILES), (output_dir, OUTPUT_FILES)):
        if not folder:
            continue
        for name in names:
            path = Path(folder) / name
            try:
                if path.is_file():
                    out[name] = path.read_bytes()
            except OSError:
                pass
    if memory is _THIS_MACHINE:
        try:
            from .auto_convert import _memory_status
            memory = _memory_status()
        except Exception:
            memory = None
    if paging_files is _THIS_MACHINE:
        paging_files = read_paging_files()
    out[MACHINE] = machine_text(memory, paging_files).encode("utf-8")
    return out
