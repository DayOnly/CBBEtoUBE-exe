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

"""pre-commit body: block a staged change that would leak.

Reads the STAGED content (`git show :path`), not the working tree, so a partial
`git add -p` is judged on what is actually about to be committed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import repo_hygiene as H  # noqa: E402

BUNDLE_EXE = "dist/CBBEtoUBE/CBBEtoUBE.exe"


class GitFailed(RuntimeError):
    """A git command a hook reads through exited with an error. #hook-fail-closed

    Both helpers used to return whatever stdout held, so a failing call read as
    "nothing staged" or "nothing to push" and the hook passed. Measured
    2026-09-15: under a checkout path past the 260-character limit every git call
    the pre-push hook makes exited 128 ("Filename too long"), and the hook
    returned 0 on a commit it has to refuse."""


def _checked(args, r, ok):
    if r.returncode not in ok:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise GitFailed(f"`{' '.join(args)}` exited {r.returncode}: {err.strip()[:400]}")
    return r.stdout


def _run(*args: str, ok=(0,)) -> str:
    return _checked(args, subprocess.run(args, capture_output=True, text=True,
                                         errors="replace"), ok)


def _run_bytes(*args: str) -> bytes:
    """Raw staged bytes. Text mode would decode a .pyd/.dll into replacement
    characters before `is_binary` ever got to see the NULs."""
    return _checked(args, subprocess.run(args, capture_output=True), (0,))


def git_failed_report(what: str, verb: str, exc: GitFailed) -> str:
    return (f"\n{what} BLOCKED -- a git command this hook needs failed, so NOTHING "
            f"was checked\n\n  {exc}\n\nA check that could not read the repository "
            "is not a pass. Fix the cause above,\n"
            f"or `git {verb} --no-verify` if you are certain.\n\n")


def path_problems(paths, read_bytes, denylist) -> "list[str]":
    """Every per-file rule the hooks enforce, over `paths` read through
    `read_bytes(path)`: the index for pre-commit, one commit at a time for
    pre-push. One function, so the two hooks cannot judge a file differently.
    #prepush-range"""
    problems: list[str] = []
    for path in paths:
        banned = H.path_is_never_tracked(path)
        if banned:
            problems.append(
                f"{path}: matches the never-track rule '{banned}' -- it names "
                "specific mods or a user's setup and this repo is public")

    for path in paths:
        # `H.should_scan` is the ONLY gate. This loop used to keep its own copy
        # of the suffix test, so widening coverage in repo_hygiene silently left
        # the hook behind -- the duplicate-list mistake, in the one place where
        # it matters most: the hook is what actually blocks a leaking commit.
        if not H.should_scan(path):
            continue
        raw = read_bytes(path)
        if not raw or H.is_binary(raw):
            continue
        problems.extend(H.scan_text(path, raw.decode("utf8", "replace")))

    # Line endings (#lf-only), on the STAGED bytes like everything else here.
    for path in paths:
        if not H.checks_line_endings(path):
            continue
        problem = H.line_ending_problem(path, read_bytes(path))
        if problem:
            problems.append(problem)

    # Asset names, from the untracked denylist. The PATH is checked even for
    # a file whose content is not scannable -- two of the 2026-09-09 leaks
    # named the mod in the FILENAME and nowhere else.
    if denylist is not None:
        for path in paths:
            raw = read_bytes(path)
            text = "" if (not raw or H.is_binary(raw)) else raw.decode(
                "utf8", "replace")
            problems.extend(H.scan_names(path, text, denylist))

        # The exe's own bundled modules. The PYZ is compressed per module, so no
        # text rule above ever sees inside it, and a docstring holding a name
        # shipped that way. #bundle-names
        if BUNDLE_EXE in paths:
            from scripts import release_gate as _rg
            try:
                for module, found in _rg.bundle_name_hits(
                        read_bytes(BUNDLE_EXE), denylist):
                    problems.append(f"{BUNDLE_EXE}: the bundled module {module} "
                                    f"carries a denylisted asset name ({found!r})")
            except _rg.GateError as e:
                sys.stderr.write(f"  NOT CHECKED: the exe's bundled modules "
                                 f"could not be read ({e})\n")

    return problems


def main() -> int:
    try:
        return _check_staged()
    except GitFailed as exc:
        sys.stderr.write(git_failed_report("COMMIT", "commit", exc))
        return 1


def _check_staged() -> int:
    staged = [p for p in _run("git", "diff", "--cached", "--name-only",
                              "--diff-filter=ACMR").splitlines() if p]
    root = Path(__file__).resolve().parent.parent
    denylist, _deny_n = H.load_denylist(root)
    problems = path_problems(staged, lambda p: _run_bytes("git", "show", f":{p}"),
                             denylist)

    # exit 1 means unset, which check_identity reports itself
    ident = H.check_identity(_run("git", "config", "user.email", ok=(0, 1)).strip())
    if ident:
        problems.append(ident)

    if problems:
        sys.stderr.write("\nCOMMIT BLOCKED -- public-repo hygiene\n\n")
        if denylist is None:
            sys.stderr.write(
                f"  (no {H.DENYLIST_FILE} on this machine, so NO "
                "asset-name check ran -- zero coverage, not a pass)"
                + chr(10) * 2)
        for p in problems:
            sys.stderr.write(f"  {p}\n")
        sys.stderr.write(
            "\nThis repository is public. A file committed here is fetched by "
            "every clone\nand stays reachable by SHA even after it is deleted "
            "-- removing one already\ncost a full history rewrite.\n\n"
            "Fix it, or `git commit --no-verify` if you are certain.\n\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
