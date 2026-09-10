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
and that the ignore entries protecting them stay present. This file used to end
here, recording "no third-party mod is ever named in tracked content" as the one
rule a test could not enforce, because a denylist of mod names would itself be
tracked content naming mods.

That is enforced too now (2026-09-09), by putting the NAMES outside the repo in
a gitignored `.asset-denylist` and tracking only the mechanism -- see the last
section. It stopped being a review judgement because review demonstrably could
not do it: a hand sweep before the 1.4 push found fifteen live leaks, four of
them already public.
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
# An absolute local path is the leak class this section enforces: it is
# objective, it names a machine and a user, and a pattern for it does not
# itself have to name anything. Mod names need a denylist and so are handled
# separately, in the last section of this file.

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
        # Ask the shared gate BEFORE any IO -- exempt trees cost nothing.
        if not H.should_scan(rel):
            continue
        # Read BYTES and drop binaries first. Scanning is no longer gated on
        # suffix (BUG-05(a)), so `dist/` .pyd/.dll members reach here now, and
        # decoding those with errors="replace" would match the rules against
        # noise -- the same call the hook makes, for the same reason.
        #
        # PROBE FIRST, then read the rest. Slurping every member whole made this
        # test read 137 MB per run -- including two 20 MB OpenBLAS DLLs read in
        # full only to discover a NUL in their first 8 KiB -- and added ~50s to
        # the suite. `is_binary` never looks past 8 KiB, so neither do we.
        try:
            with p.open("rb") as fh:
                head = fh.read(8192)
                if H.is_binary(head):
                    continue
                raw = head + fh.read()
        except OSError:
            continue
        offenders.extend(H.scan_text(rel, raw.decode("utf8", "replace")))
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


# --- third-party asset names, from an UNTRACKED denylist --------------------
# The module docstring above used to end "naming mods stays a review judgement".
# It stopped being one on 2026-09-09: sweeping tracked content by hand before
# the 1.4 push found FIFTEEN live mod-name leaks, four of them already public,
# and review had let every one through. Two of the fifteen had the name ONLY in
# the FILENAME, which no content grep would have found.
#
# What made it enforceable is that the NAMES live outside the repo
# (`.asset-denylist`, gitignored and in NEVER_TRACKED) and only the MECHANISM is
# tracked. The check is therefore real on the author's machine and absent on a
# fresh clone -- so it reports which of the two it got, out loud.


@needs_git
def test_no_denylisted_asset_name_in_tracked_content():
    """No tracked file may name a real third-party asset, in its path or body.

    Uses `repo_hygiene.scan_names` -- the same call the pre-commit hook makes --
    so a commit the hook would block cannot pass here.
    """
    pattern, count = H.load_denylist(REPO_ROOT)
    if pattern is None:
        pytest.skip(
            f"no {H.DENYLIST_FILE} in this checkout, so this test scanned "
            "NOTHING. That is zero coverage, not a pass: the file is "
            "gitignored by design and exists only on a machine that has one. "
            "Build it from the tables in LOCAL_ASSET_SAMPLES.md.")
    assert count >= 5, (
        f"{H.DENYLIST_FILE} holds only {count} entr(y/ies) -- a denylist that "
        "small is not covering the substitution tables it is built from")

    offenders = []
    for rel in tracked:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        text = ""
        if H.should_scan(rel):
            try:
                with p.open("rb") as fh:
                    head = fh.read(8192)
                    if not H.is_binary(head):
                        text = (head + fh.read()).decode("utf8", "replace")
            except OSError:
                pass
        # The PATH is checked even when the content is not scannable.
        offenders.extend(H.scan_names(rel, text, pattern))
    assert not offenders, (
        f"tracked content names a real third-party asset ({count} names "
        "checked). Substitute it and record the row in "
        "LOCAL_ASSET_SAMPLES.md:\n  " + "\n  ".join(offenders[:10]))


def test_the_denylist_check_can_actually_fail(tmp_path):
    """Control, and it runs WITHOUT a local denylist -- so the mechanism stays
    proven on a fresh clone where the test above can only skip.

    The word-start rule is the whole design: a real asset name is routinely
    a PREFIX of a longer identifier, so the obvious `\\b...\\b` fence would
    have missed both 2026-09-02 leaks -- while a fence-free substring match
    turns an ordinary English word into a hit. Both halves are asserted
    below, with a SYNTHETIC name: real ones live only in the untracked
    denylist, because this file is scanned too.
    """
    (tmp_path / H.DENYLIST_FILE).write_text(
        "# comment\n\nsure\nre:^fit[ _]\n", encoding="utf-8")
    pattern, count = H.load_denylist(tmp_path)
    assert count == 2, "comments and blank lines must not become entries"

    # catches the name as a PREFIX, in content and in a filename alike
    assert H.scan_names("src/x.py", "the SureheartCuirass piece", pattern)
    assert H.scan_names("docs/worklog/SUREHEART_NOTES.md", "", pattern)
    # ...and not mid-word, which is what a fence-free match would do
    assert not H.scan_names("src/x.py", "the measured value", pattern)
    # a clean file stays clean, and no denylist means no findings at all
    assert not H.scan_names("src/x.py", "nothing to see", pattern)
    assert not H.scan_names("src/x.py", "the SureheartCuirass piece", None)


def test_an_absent_denylist_is_reported_as_absent_not_as_clean():
    """`(None, 0)` is the ONLY absent signal, and every caller branches on it.
    A loader that returned an empty pattern instead would make every caller
    silently report a clean tree -- the 0/0 failure, wearing a green tick."""
    pattern, count = H.load_denylist(tmp_path_that_does_not_exist())
    assert pattern is None and count == 0


def tmp_path_that_does_not_exist():
    return REPO_ROOT / "no_such_directory_for_the_denylist_control"
