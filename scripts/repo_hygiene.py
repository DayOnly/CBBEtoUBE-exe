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

This repository is public. Four things must never reach a commit: a file that
is local-only by policy (they name specific mods), an absolute path that
identifies a person or their modlist, a personal email address, and the name of
a real third-party asset -- in a file's CONTENT or in its FILENAME.

The fourth is the newest (2026-09-09) and works differently from the other
three: its input is a denylist of real names, which cannot itself be tracked
here, so only the mechanism lives in this file and the names live in an
untracked `.asset-denylist`. See `load_denylist`.

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
from pathlib import Path

# --- files that are LOCAL ONLY by policy, not merely by convenience ----------
# ARMOR_WORKLIST.md names specific mods and modlists; the rest are per-machine
# run products whose paths and mod names would leak a user's setup.
NEVER_TRACKED = (
    "ARMOR_WORKLIST.md",
    "CBBEtoUBE_last_failures.json",
    "CBBEtoUBE_previous_failures.json",
    "output/",
    "samples/",
    # In-game working notes. CLIPPING_LOG.md alone carries 17 mod-naming lines;
    # the audit/design notes quote measurements BY armour name, which is exactly
    # what makes them useful locally and unpublishable.
    "CLIPPING_LOG.md",
    # The map from the synthetic names in tracked fixtures back to the REAL
    # assets. Publishing it would undo every substitution it records.
    "LOCAL_ASSET_SAMPLES.md",
    # The denylist the name check below reads. It IS a list of mod names, so it
    # is the one input to a public-repo guard that can never be public itself.
    ".asset-denylist",
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

# The path a LOCAL_PATH_RE match starts: up to whitespace or a quote, a whole
# `<...>` marker counting as part of it even when it holds spaces. The placeholder
# test looks INSIDE this token. It used to look at the whole line, so a worklog
# line quoting a real instance path scanned clean because the same line also
# named `$Mo2Root` and a `<local-path>` marker. #placeholder-token
_PATH_TOKEN = re.compile(r"(?:<[^<>\r\n]*>|[^\s\"'`])+")


def unexempt_local_path(line: str) -> bool:
    """True when `line` holds an absolute local path whose own token carries no
    placeholder -- a path that identifies a person or a named modlist."""
    for m in LOCAL_PATH_RE.finditer(line):
        tok = _PATH_TOKEN.match(line, m.start())
        if not PLACEHOLDER.search(tok.group() if tok else m.group()):
            return True
    return False

# --- third-party asset names, from an UNTRACKED denylist --------------------
# The docstring of tests/test_public_repo_hygiene.py used to record this as the
# one rule a test could not enforce: "a denylist of mod names would itself be
# tracked content naming mods". It can be enforced if the NAMES live outside the
# repo and only the MECHANISM is tracked -- which is what this is.
#
# Why it exists: on 2026-09-09, sweeping tracked content by hand before the 1.4
# push found FIFTEEN live mod-name leaks. Four were already public. Review had
# been the only guard, and review had missed every one -- including two whose
# only occurrence was in the FILENAME, which no content grep would ever see.
#
# Format, one entry per line, `#` comments and blanks ignored:
#     sure                  a literal name; matched case-insensitively at a WORD
#                           START, so it catches `SureheartCuirass` and does NOT
#                           catch `measured`
#     re:^fit[ _]           a raw regex, for a name too short or too common to
#                           fence with a word start alone
#
# **A BARE ENTRY IS BLIND TO A NAME INSIDE A LONGER IDENTIFIER.** Word START
# means the character in front of it has to be a non-word character, so a bare
# `sure` matches `Sureheart` and NOT `ArmorSureheartF`. Two commit messages
# carrying exactly that shape scanned clean on 2026-09-10, one push from
# permanent. Use a fence-free `re:` entry for any name distinctive enough not
# to collide with English, and keep the bare form only for the ones that do --
# fence-free, one real entry here matches an ordinary English word 60+ times
# across the tree, which is what the fence exists for.
#
# (Those two are synthetic. Real names go in the file, never in this comment --
# this module is itself scanned, see `should_scan_names`.)
#
# Build it from the substitution tables in LOCAL_ASSET_SAMPLES.md, which is the
# record of what every synthetic name in this repo stands in for.
DENYLIST_FILE = ".asset-denylist"


def load_denylist(root) -> tuple[re.Pattern | None, int]:
    """(compiled pattern, entry count) from the untracked denylist file.

    Returns (None, 0) when the file is absent -- a fresh clone or CI has no
    local names to check against. **Absent is not a pass**, and every caller
    must say which of the two it got: a check that silently degrades to zero
    coverage is the 0/0 failure this project has been bitten by repeatedly.
    """
    path = Path(root) / DENYLIST_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, 0
    parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            parts.append(line[3:])
        else:
            # Word START only, never a trailing boundary: a real asset name is
            # routinely a PREFIX of a longer identifier (`<name>Combined`,
            # `<name>F_1.nif`), and requiring a trailing boundary would miss
            # exactly the concatenated forms that leak most often. Both of the
            # 2026-09-02 leaks were of that shape.
            parts.append(r"\b" + re.escape(line))
    if not parts:
        return None, 0
    return re.compile("|".join(parts), re.IGNORECASE), len(parts)


def should_scan_names(path: str) -> bool:
    """Whether `path`'s CONTENT is subject to the ASSET-NAME rule.

    Deliberately NOT `should_scan`. That gate exempts this module and its own
    tests, and the reason it gives is specific to the other two rules: a control
    for the path and email patterns has to HOLD a string those patterns reject.
    A control for this rule does not -- it builds a synthetic denylist and
    matches a made-up name against it -- so the exemption here would be a pure
    hole. It was one for about twenty minutes on 2026-09-09: the first draft
    reused `should_scan`, and the real asset names sitting in this file's own
    comments as illustration scanned clean.

    Vendored code (`SKIP_PREFIXES`) stays exempt: it is upstream's content,
    replaced wholesale, and not this repo's leak surface.
    """
    p = path.replace("\\", "/")
    if p.startswith(SKIP_PREFIXES):
        return False
    return p.startswith(SCAN_ANY_SUFFIX_UNDER) or p.lower().endswith(
        tuple(TEXT_SUFFIXES))


def scan_names(path: str, text: str, pattern: re.Pattern | None) -> list[str]:
    """Denylisted asset names in one file's PATH and content.

    The path is checked whether or not the content is scannable: two of the
    2026-09-09 leaks were filenames over bodies that never named the mod at all.
    """
    if pattern is None:
        return []
    out = []
    m = pattern.search(path.replace("\\", "/"))
    if m:
        out.append(f"{path}: FILENAME carries a denylisted asset name "
                   f"({m.group()!r})")
    if not should_scan_names(path):
        return out
    # The WHOLE text, not line by line: a name wrapped across a line break
    # passed the per-line scan, while the compiled docstring holding it is one
    # string -- and shipped in the exe that way. #bundle-names
    for m in pattern.finditer(text):
        n = text.count("\n", 0, m.start()) + 1
        out.append(f"{path}:{n}: denylisted asset name ({m.group()!r}) -- "
                   "substitute it and add the row to LOCAL_ASSET_SAMPLES.md")
    return out


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

# Trees scanned REGARDLESS of suffix. BUG-05(a): `dist/` was un-exempted in the
# 08-18 audit, but the suffix gate above still made it mostly symbolic -- the
# rules reached 6 of 1127 tracked files, because PyInstaller writes most members
# with no suffix at all (631 extensionless, plus .pyd/.enc/.tcl/.msg/.dll).
# This is OUR committed build output, exactly where a build-machine path bakes
# in, so scannability here is decided by CONTENT, not by filename.
SCAN_ANY_SUFFIX_UNDER = ("dist/",)

# Vendored third-party trees inside that build output. Upstream's own author
# addresses are not this repo's leak surface and they are rewritten wholesale on
# every rebuild, so flagging them would fail hygiene on each one -- the same
# reason `.pynifly/` is skipped entirely.
#
# Scoped to the EMAIL rule ONLY, which is what makes this an exemption rather
# than another hole: the local-path rule still applies to every one of these
# files, and that is the rule that actually protects `dist/`. Measured
# 2026-08-22 over all 937 text-decodable members: 14 vendor emails in 12 files,
# and ZERO local-path hits -- so the path rule is switched on here at no cost.
#
# All 14 are accounted for by these three trees: Tk's script library, Tcl's
# module library, and installed-package METADATA. The first version of this list
# omitted `/tcl8/` because it was built from a census printout truncated to ten
# rows -- the 14th hit failed the suite immediately, which is the only reason
# the omission did not ship.
# 2026-09-16: `_internal/licenses/` joined them when the build started shipping
# the licence texts the notice promises (#bundled-licences). CPython's carries the
# addresses of the people who wrote what CPython bundles -- MEASURED 9 lines, and
# 0 local-path lines; SciPy's and Tcl/Tk's carry neither. The hook refused the
# rebuilt bundle on those 9, which is the rule doing its job: an upstream licence
# is exactly the case this exemption exists for, and the path rule stays on.
VENDOR_EMAIL_TREES = ("/_tk_data/", "/tcl8/", ".dist-info/", "/_internal/licenses/")

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

# .pynifly/ ONLY: vendored third-party code, updated wholesale -- upstream's
# own author emails / doc examples are not this repo's leak surface, and
# flagging them would fail hygiene on every vendor update. dist/ is NOT
# exempt (2026-08-18 audit): it is OUR committed PyInstaller output, exactly
# where a build-machine path would bake in, and it was skipped wholesale with
# no per-entry reason -- 1,126 tracked files invisible to the content rules.
# Binary members are already excluded by TEXT_SUFFIXES; the text members are
# few and cheap to scan.
SKIP_PREFIXES = (".pynifly/",)


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


def should_scan(path: str) -> bool:
    """Whether `path`'s CONTENT is subject to the rules.

    The single place that decides. The pre-commit hook used to keep its own copy
    of the suffix test, which is precisely how a newly covered tree stays
    invisible on one side -- the same duplicate-list mistake this module's
    exemption comment warns about.
    """
    p = path.replace("\\", "/")
    if _exempt(p):
        return False
    return p.startswith(SCAN_ANY_SUFFIX_UNDER) or p.lower().endswith(
        tuple(TEXT_SUFFIXES))


def is_binary(blob: bytes) -> bool:
    """A NUL in the first 8 KiB -- git's own heuristic. Needed now that scanning
    is not gated on suffix: `dist/` holds .pyd/.dll members whose bytes would
    otherwise be decoded into noise and matched against."""
    return b"\x00" in blob[:8192]


# LINE ENDINGS: tracked text is LF-only outside the build and vendor trees.
# #lf-only (G-1, 2026-09-15). `.gitattributes` says `* -text`, which PRESERVES
# whatever a tool writes; it never stopped a flip. Whole-file CRLF->LF flips
# buried 41 real lines of src/nif_convert.py under 28,745 changed ones
# (2026-09-02), and src/gui_settings.py, src/auto_convert.py and src/gui.py went
# the same way within nine days. The last 28 CRLF files were converted in one
# commit of line endings only; this rule keeps CRLF from coming back.
#
# dist/ is PyInstaller's output and .pynifly/ is vendored: both are replaced
# wholesale from upstream bytes, so their endings are not ours to choose.
LF_ONLY_SKIP_PREFIXES = ("dist/", ".pynifly/")


def checks_line_endings(path: str) -> bool:
    """Whether `path` must be LF-only. Asked before any read, so the hook never
    pulls a dist/ member's bytes for this rule."""
    return not path.replace("\\", "/").startswith(LF_ONLY_SKIP_PREFIXES)


def line_ending_problem(path: str, raw: bytes) -> str | None:
    """One line naming the CRLFs in a file that must be LF-only, else None.
    Binary content (a NUL in the first 8 KiB) is never judged."""
    if not checks_line_endings(path) or is_binary(raw):
        return None
    crlf = raw.count(b"\r\n")
    if not crlf:
        return None
    return (f"{path}: {crlf} CRLF line ending(s) -- tracked text is LF-only "
            "outside dist/ and .pynifly/; convert the bytes, not through a "
            "text-mode write (Python's text mode on Windows writes CRLF)")


def scan_text(path: str, text: str) -> list[str]:
    """Violations in one file's content, as human-readable lines."""
    if not should_scan(path):
        return []
    p = path.replace("\\", "/")
    vendor = any(t in p for t in VENDOR_EMAIL_TREES)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if unexempt_local_path(line):
            out.append(f"{path}:{n}: absolute local path -- {line.strip()[:90]}")
        if vendor:
            continue           # upstream authorship metadata; see VENDOR_EMAIL_TREES
        for m in EMAIL_RE.finditer(line):
            if not EMAIL_ALLOWED.search(m.group()):
                out.append(f"{path}:{n}: personal email address in tracked content")
                break
    return out


def scan_message(msg: str, pattern: re.Pattern | None = None) -> list[str]:
    """Violations in a commit message. Messages cannot be edited later without
    rewriting history, and no test covers them -- 11 carried a deploy path.

    `pattern` is the asset-name denylist. It is optional only because the
    other three rules need no input; pass it whenever you have one. The
    commit that ADDED the name rule leaked a real asset name in its own
    message, because this function checked paths and emails and the name
    rule had been wired into content scanning alone.
    """
    out = []
    for n, line in enumerate(msg.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue                      # git's own comment lines
        if unexempt_local_path(line):
            out.append(f"commit message line {n}: absolute local path")
        for m in EMAIL_RE.finditer(line):
            if not EMAIL_ALLOWED.search(m.group()):
                out.append(f"commit message line {n}: personal email address")
                break
    if pattern is not None:
        # The whole message, git's comment lines blanked, so a name wrapped
        # across a line break is caught too. #bundle-names
        kept = "\n".join("" if ln.lstrip().startswith("#") else ln
                         for ln in msg.splitlines())
        for m in pattern.finditer(kept):
            n = kept.count("\n", 0, m.start()) + 1
            out.append(f"commit message line {n}: denylisted asset "
                       f"name ({m.group()!r})")
    return out


def check_identity(email: str) -> str | None:
    """Reject a commit identity that is not a throwaway address."""
    if not email:
        return "git user.email is unset"
    if not EMAIL_ALLOWED.search(email.strip()):
        return ("git user.email is a personal address; use your GitHub noreply "
                "form so it is not published on every commit")
    return None
