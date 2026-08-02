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


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True,
                          errors="replace").stdout


def main() -> int:
    staged = [p for p in _run("git", "diff", "--cached", "--name-only",
                              "--diff-filter=ACMR").splitlines() if p]
    problems: list[str] = []

    for path in staged:
        banned = H.path_is_never_tracked(path)
        if banned:
            problems.append(
                f"{path}: matches the never-track rule '{banned}' -- it names "
                "specific mods or a user's setup and this repo is public")

    for path in staged:
        if not path.lower().endswith(tuple(H.TEXT_SUFFIXES)):
            continue
        blob = _run("git", "show", f":{path}")
        if blob:
            problems.extend(H.scan_text(path, blob))

    ident = H.check_identity(_run("git", "config", "user.email").strip())
    if ident:
        problems.append(ident)

    if problems:
        sys.stderr.write("\nCOMMIT BLOCKED -- public-repo hygiene\n\n")
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
