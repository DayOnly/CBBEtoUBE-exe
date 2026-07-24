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

"""The repo is public and tracked content is kept mod-agnostic.

That rule lives in a .gitignore comment and in two commits that had to fix
violations after the fact (d409f05, fb3abb6). Nothing enforced it: .gitignore
only guards against ACCIDENTAL adds, and one `git add -f` -- or a gitignore
edit that drops an entry -- re-creates the leak with no signal. These tests
make the suite itself the guard, so the violation fails CI on the same push
that introduces it instead of surfacing in a public diff later.

Scope note: a test can enforce that the KNOWN local-only files stay untracked
and that the ignore entries protecting them stay present. It cannot enforce
"no third-party mod is ever named in tracked content" in general -- a denylist
of mod names would itself be tracked content naming mods. That last line stays
a review judgement; these tests fence everything mechanical around it.
"""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files/dirs that are LOCAL ONLY by policy, not merely by convenience.
# ARMOR_WORKLIST.md names specific mods and modlists; the rest are per-machine
# run products whose paths and mod names would leak a user's setup.
NEVER_TRACKED = (
    "ARMOR_WORKLIST.md",
    "CBBEtoUBE_last_failures.json",
    "output/",
    "samples/",
)

# The .gitignore lines that protect the set above. If one disappears, the
# protection is gone even though nothing is tracked *yet* -- catch it then,
# not at the first accidental `git add .`.
REQUIRED_IGNORE_ENTRIES = (
    "ARMOR_WORKLIST.md",
    "CBBEtoUBE_last_failures.json",
    "output/",
    "samples/",
    "*.log",
)


def _tracked_files():
    """Everything git tracks, or None when git is unavailable (sdist/zip)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.splitlines()


tracked = _tracked_files()
needs_git = pytest.mark.skipif(
    tracked is None, reason="not a git checkout (source archive?)")


@needs_git
@pytest.mark.parametrize("banned", NEVER_TRACKED)
def test_local_only_file_is_not_tracked(banned):
    if banned.endswith("/"):
        hits = [f for f in tracked if f.startswith(banned)]
    else:
        hits = [f for f in tracked if f == banned or f.endswith("/" + banned)]
    assert not hits, (
        f"{banned} is tracked ({hits[:3]}) -- it is local-only by policy: "
        "the repo is public and this file names specific mods or a user's "
        "setup. Untrack it with `git rm --cached` before pushing.")


@pytest.mark.parametrize("entry", REQUIRED_IGNORE_ENTRIES)
def test_gitignore_still_protects_the_local_only_set(entry):
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in gitignore.splitlines()]
    assert entry in lines, (
        f".gitignore lost its '{entry}' entry. That entry is policy, not "
        "housekeeping -- without it the next `git add .` stages a file that "
        "must never appear in this public repo.")
