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

"""The public-repo rules, in ONE place.

This repository is public. Three things must never reach a commit: a file that
is local-only by policy (they name specific mods), an absolute path that
identifies a person or their modlist, and a personal email address.

**Why this module exists rather than the rules living in the test.** On
2026-08-02 an audit found all three already in `main`'s history. The working
notes had been deleted from the tip in a commit literally titled "Keep working
notes off main" -- the intent was right, but a file removed from the tip is
still fetched by every clone, and the test that was supposed to guard this only
ever asked "is it tracked *now*?". Undoing it needed a full history rewrite, a
force-push to a public default branch, and a support ticket for the objects
GitHub still serves by SHA afterwards.

So the rules moved here, where BOTH the test suite and the git hooks import
them. Two copies of one rule drift -- that is already a documented failure in
this project (`conform` had two body-detectors that disagreed, and the pass
silently never ran). One definition, two callers.

The hooks make the check fire BEFORE the object is written, which is the only
point at which this class of mistake is still cheap to fix.
"""
from __future__ import annotations

import fnmatch
import re

# --- files that are LOCAL ONLY by policy, not merely by convenience ----------
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
    # Coverage-scan reports a run drops in the REPO ROOT. Both name the mods
    # root and list mod names; neither was ignored, so `git add .` would have
    # staged them (2026-08-02 audit of main).
    "ube_providers.txt",
    "ube_replacers_to_disable.txt",
    # Per-user GUI state. Tracking it means a source run dirties the tree and a
    # clone carries someone else's chosen flags. Absent == all defaults, so
    # there is nothing to ship.
    "CBBEtoUBE_settings.json",
)

# The .gitignore lines that protect the set above. If one disappears the
# protection is gone even though nothing is tracked *yet* -- catch it then, not
# at the first accidental `git add .`.
REQUIRED_IGNORE_ENTRIES = NEVER_TRACKED + ("*.log",)

# --- absolute paths that identify a PERSON or a NAMED modlist ---------------
# A generic drive path (C:\Games\..., C:\mods\...) identifies nobody and appears
# legitimately in setup docs and fixtures, so it is deliberately NOT matched.
#
# Written WITHOUT re.VERBOSE on purpose: a trailing backslash in a verbose-mode
# comment escapes the newline and swallows the next alternative, which is how an
# earlier draft compiled to something matching almost nothing.
LOCAL_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/]"
    r"(?:Users[\\/][A-Za-z0-9._-]+[\\/]"
    r"|Modlists[\\/])",
    re.IGNORECASE)

# Stand-ins that are synthetic BY CONSTRUCTION -- a fixture or a doc example.
PLACEHOLDER = re.compile(
    r"<[^>]+>"
    r"|Users[\\/](?:someone|username|user|you|yourname|test|example)[\\/]"
    r"|path[\\/]to|your[\\/-]|example|MO2Root",
    re.IGNORECASE)

# --- email addresses --------------------------------------------------------
# Author identity is public on every commit of a public repo and is served by
# the API, so a personal address is harvestable. 13 commits carried one before
# the 2026-08-02 rewrite. Rather than denylisting the specific addresses --
# which would republish them in tracked content, the exact mistake the audit
# document itself made -- anything that is not demonstrably a throwaway is
# rejected.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_ALLOWED = re.compile(
    r"@users\.noreply\.github\.com$"
    # any `noreply@` local part -- the convention for an address that belongs to
    # a service rather than a person. Co-Authored-By trailers use these, and the
    # first version of this rule rejected its own commit message over one.
    r"|^noreply@"
    r"|@(?:example\.(?:com|org|net)|example\.invalid|invalid|localhost)$",
    re.IGNORECASE)

TEXT_SUFFIXES = frozenset((
    ".py", ".md", ".ps1", ".yml", ".yaml", ".json", ".txt", ".spec",
    ".cfg", ".toml", ".ini", ".bat", ".pas"))

# Paths exempt from the CONTENT rules. This module and its tests necessarily
# contain examples of what they match -- a rule is only trustworthy if something
# proves it still fires, and that proof has to hold the very strings the rule
# rejects. Scanning them would only ever report those.
#
# Keep this list SHORT and specific. Every entry is a hole, and the reason each
# one is safe is that the file's whole job is to be a control.
CONTENT_EXEMPT = (
    "scripts/repo_hygiene.py",
    "tests/test_public_repo_hygiene.py",
    "tests/test_repo_hygiene_hooks.py",
)

SKIP_PREFIXES = ("dist/", ".pynifly/")


def path_is_never_tracked(path: str) -> str | None:
    """The NEVER_TRACKED pattern `path` violates, or None."""
    path = path.replace("\\", "/")
    for banned in NEVER_TRACKED:
        if banned.endswith("/"):
            if path.startswith(banned) or ("/" + banned) in ("/" + path):
                return banned
        elif "*" in banned:
            name = path.rsplit("/", 1)[-1]
            if fnmatch.fnmatch(path, banned) or fnmatch.fnmatch(name, banned):
                return banned
        elif path == banned or path.endswith("/" + banned):
            return banned
    return None


def _exempt(path: str) -> bool:
    p = path.replace("\\", "/")
    return p.startswith(SKIP_PREFIXES) or p.endswith(CONTENT_EXEMPT)


def scan_text(path: str, text: str) -> list[str]:
    """Violations in one file's content, as human-readable lines."""
    if _exempt(path) or not path.lower().endswith(tuple(TEXT_SUFFIXES)):
        return []
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if LOCAL_PATH_RE.search(line) and not PLACEHOLDER.search(line):
            out.append(f"{path}:{n}: absolute local path -- {line.strip()[:90]}")
        for m in EMAIL_RE.finditer(line):
            if not EMAIL_ALLOWED.search(m.group()):
                out.append(f"{path}:{n}: personal email address in tracked content")
                break
    return out


def scan_message(msg: str) -> list[str]:
    """Violations in a commit message. Messages cannot be edited later without
    rewriting history, and no test covers them -- 11 carried a deploy path."""
    out = []
    for n, line in enumerate(msg.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue                      # git's own comment lines
        if LOCAL_PATH_RE.search(line) and not PLACEHOLDER.search(line):
            out.append(f"commit message line {n}: absolute local path")
        for m in EMAIL_RE.finditer(line):
            if not EMAIL_ALLOWED.search(m.group()):
                out.append(f"commit message line {n}: personal email address")
                break
    return out


def check_identity(email: str) -> str | None:
    """Reject a commit identity that is not a throwaway address."""
    if not email:
        return "git user.email is unset"
    if not EMAIL_ALLOWED.search(email.strip()):
        return ("git user.email is a personal address; use your GitHub noreply "
                "form so it is not published on every commit")
    return None
