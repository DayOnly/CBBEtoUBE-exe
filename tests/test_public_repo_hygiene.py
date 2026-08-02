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
import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The rules now live in scripts/repo_hygiene.py so the git HOOKS enforce the
# exact same set before a commit object exists. Two copies of one rule drift --
# that is a documented failure in this project already. Imported, not restated.
from scripts import repo_hygiene as H  # noqa: E402

NEVER_TRACKED = H.NEVER_TRACKED
REQUIRED_IGNORE_ENTRIES = H.REQUIRED_IGNORE_ENTRIES
_LOCAL_PATH_RE = H.LOCAL_PATH_RE
_PLACEHOLDER = H.PLACEHOLDER
_TEXT_SUFFIXES = H.TEXT_SUFFIXES


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
    elif "*" in banned:
        hits = [f for f in tracked
                if fnmatch.fnmatch(f, banned) or fnmatch.fnmatch(f, "*/" + banned)]
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


# --- content, not just filenames -------------------------------------------
# The tests above check WHICH FILES are tracked. They never look INSIDE one, and
# that is how a developer's absolute modlist path reached three harness scripts
# and a doc on a public branch: the files themselves are perfectly legitimate.
#
# An absolute local path is the leak class worth enforcing mechanically -- it is
# objective, it names a machine and a user, and (unlike a mod name) a pattern
# for it does not itself have to name anything. Naming mods stays a review
# judgement, as the module docstring says.

# Fires only on a path that identifies a PERSON or a NAMED modlist -- those are
# what leak. A generic drive path (C:\Games\..., C:\mods\...) identifies nobody
# and appears legitimately in setup docs and test fixtures, so it is not matched.
#
# Written WITHOUT re.VERBOSE on purpose: a trailing backslash in a verbose-mode
# comment escapes the newline and swallows the next alternative, which is how
# the first draft of this compiled to nonsense and matched almost nothing.
# Stand-in names that are synthetic BY CONSTRUCTION -- a fixture or a doc
# example, not a real machine.

@needs_git
def test_no_absolute_local_paths_in_tracked_text():
    """No tracked text file may hardcode a path to one machine.

    Two reasons, and the second is the one that bites: it publishes a user's
    directory layout from a PUBLIC repo, and it makes whatever uses it silently
    useless on every other machine -- a harness defaulting to someone else's
    mods folder finds nothing and reports zero rather than failing.

    Resolve through `src.paths` (CBBE2UBE_MODS_ROOT, else MO2 discovery)
    instead.

    Scans via `repo_hygiene.scan_text` -- the same call the pre-commit hook
    makes -- so a file the hook would refuse cannot pass here, and vice versa.
    The skip and exemption lists live there too: keeping a second copy here is
    exactly how this test silently stopped covering a newly added file.
    """
    offenders = []
    for rel in tracked:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        offenders.extend(H.scan_text(rel, text))
    assert not offenders, (
        "tracked files hardcode an absolute local path (public repo + breaks "
        "on other machines):\n  " + "\n  ".join(offenders[:10]))


def test_the_local_path_rule_actually_catches_one():
    """Control. A content rule that matches nothing reads as compliance, and
    this one shipped matching almost nothing on its first draft."""
    # the real leaks this was written for -- all four were Modlists paths
    assert _LOCAL_PATH_RE.search(r'root = r"D:\Modlists\Somelist\mods"')
    assert _LOCAL_PATH_RE.search(r"OUT = Path('D:/Modlists/Somelist/mods/x')")
    assert _LOCAL_PATH_RE.search(r"C:\Users\realname\Downloads\thing")

    # and it must NOT fire on things that identify nobody
    def clean(line):
        return not (_LOCAL_PATH_RE.search(line)
                    and not _PLACEHOLDER.search(line))

    assert clean(r"set it to C:\Games\Skyrim\Data")     # generic drive path
    assert clean(r"e.g. <MO2Root>\mods")                # placeholder
    assert clean(r'"output_mod": r"C:\mods\CBBEtoUBE Auto"')   # fixture
    assert clean(r'f(r"C:\Users\someone\.ssh\id_rsa")')        # fixture name
