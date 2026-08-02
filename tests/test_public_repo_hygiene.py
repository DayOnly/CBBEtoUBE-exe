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

# Files/dirs that are LOCAL ONLY by policy, not merely by convenience.
# ARMOR_WORKLIST.md names specific mods and modlists; the rest are per-machine
# run products whose paths and mod names would leak a user's setup.
NEVER_TRACKED = (
    "ARMOR_WORKLIST.md",
    "CBBEtoUBE_last_failures.json",
    "output/",
    "samples/",
    # In-game working notes. CLIPPING_LOG.md alone carries 17 mod-naming lines;
    # the audit/design notes quote measurements BY armour name, which is exactly
    # what makes them useful locally and unpublishable.
    "CLIPPING_LOG.md",
    # The map from the synthetic names in tracked fixtures back to the REAL
    # assets. Publishing it would undo every substitution it records.
    "LOCAL_ASSET_SAMPLES.md",
    "AUDIT_REDUNDANCY_*.md",
    "CONVERTER_AUDIT_*.md",
    "DESIGN_JIGGLE_PLAN.md",
    "DESIGN_P5_*.md",
    "DESIGN_P6_*.md",
    # Golden baseline AND its piece inventory: the manifest records which MOD each
    # source mesh came from, and golden/pieces.json is that list by definition.
    "golden/",
    # Measurement censuses. Every row is keyed by an armor's mesh path, so these
    # name mods by construction -- there is no mod-agnostic version of them.
    "fit_dataset*.jsonl",
    "fit_census*.jsonl",
    "penetration_census*.jsonl",
    "multipose_census*.jsonl",
    "source_delta_census*.jsonl",
)

# The .gitignore lines that protect the set above. If one disappears, the
# protection is gone even though nothing is tracked *yet* -- catch it then,
# not at the first accidental `git add .`.
REQUIRED_IGNORE_ENTRIES = NEVER_TRACKED + ("*.log",)


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

_TEXT_SUFFIXES = {".py", ".md", ".ps1", ".yml", ".yaml", ".json", ".txt",
                  ".spec", ".cfg", ".toml", ".ini"}

# Fires only on a path that identifies a PERSON or a NAMED modlist -- those are
# what leak. A generic drive path (C:\Games\..., C:\mods\...) identifies nobody
# and appears legitimately in setup docs and test fixtures, so it is not matched.
#
# Written WITHOUT re.VERBOSE on purpose: a trailing backslash in a verbose-mode
# comment escapes the newline and swallows the next alternative, which is how
# the first draft of this compiled to nonsense and matched almost nothing.
_LOCAL_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/]"
    r"(?:Users[\\/][A-Za-z0-9._-]+[\\/]"
    r"|Modlists[\\/])",
    re.IGNORECASE)

# Stand-in names that are synthetic BY CONSTRUCTION -- a fixture or a doc
# example, not a real machine.
_PLACEHOLDER = re.compile(
    r"<[^>]+>"
    r"|Users[\\/](?:someone|username|user|you|yourname|test|example)[\\/]"
    r"|path[\\/]to|your[\\/-]|example|MO2Root",
    re.IGNORECASE)


@needs_git
def test_no_absolute_local_paths_in_tracked_text():
    """No tracked text file may hardcode a path to one machine.

    Two reasons, and the second is the one that bites: it publishes a user's
    directory layout from a PUBLIC repo, and it makes whatever uses it silently
    useless on every other machine -- a harness defaulting to someone else's
    mods folder finds nothing and reports zero rather than failing.

    Resolve through `src.paths` (CBBE2UBE_MODS_ROOT, else MO2 discovery)
    instead.
    """
    offenders = []
    for rel in tracked:
        p = REPO_ROOT / rel
        if p.suffix.lower() not in _TEXT_SUFFIXES or not p.is_file():
            continue
        if rel.startswith("dist/") or rel.startswith(".pynifly/"):
            continue                      # vendored/built, not authored here
        if rel.endswith("test_public_repo_hygiene.py"):
            # This file DEFINES the rule, so it necessarily contains examples of
            # what the rule catches. Its own control test is what proves the
            # pattern still fires; scanning it here would only ever report them.
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            m = _LOCAL_PATH_RE.search(line)
            if m and not _PLACEHOLDER.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:90]}")
    assert not offenders, (
        "tracked files hardcode an absolute local path (public repo + breaks "
        "on other machines):\n  " + "\n  ".join(offenders[:10]))


def test_the_local_path_rule_actually_catches_one():
    """Control. A content rule that matches nothing reads as compliance, and
    this one shipped matching almost nothing on its first draft."""
    # the real leaks this was written for -- all four were Modlists paths
    assert _LOCAL_PATH_RE.search(r'root = r"<MODLIST_ROOT>\mods"')
    assert _LOCAL_PATH_RE.search(r"OUT = Path('<MODLIST_ROOT>/mods/x')")
    assert _LOCAL_PATH_RE.search(r"C:\Users\realname\Downloads\thing")

    # and it must NOT fire on things that identify nobody
    def clean(line):
        return not (_LOCAL_PATH_RE.search(line)
                    and not _PLACEHOLDER.search(line))

    assert clean(r"set it to C:\Games\Skyrim\Data")     # generic drive path
    assert clean(r"e.g. <MO2Root>\mods")                # placeholder
    assert clean(r'"output_mod": r"C:\mods\CBBEtoUBE Auto"')   # fixture
    assert clean(r'f(r"C:\Users\someone\.ssh\id_rsa")')        # fixture name
