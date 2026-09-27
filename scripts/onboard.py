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

"""Make a fresh clone able to commit: one command, run from anywhere inside it.

    python scripts/onboard.py          # configure, then report
    python scripts/onboard.py --check  # report only, change nothing

Sets, in this clone's own config: core.hooksPath (the public-repo hooks in
.githooks/), core.autocrlf false (tracked text is LF-only), pull.ff only (the
integration branches move by fast-forward only) and blame.ignoreRevsFile. Then
reports what only the person can supply: a GitHub noreply commit identity, the
asset denylist the hooks read, and a Python that can run the suite.

Exit 0 when a commit made here would pass the hooks' identity and denylist
checks; 1 when something is still missing (the config is applied either way).
#worktree-per-branch
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import repo_hygiene as H  # noqa: E402

CONFIG = (
    ("core.hooksPath", ".githooks"),
    ("core.autocrlf", "false"),
    ("pull.ff", "only"),
    ("blame.ignoreRevsFile", ".git-blame-ignore-revs"),
)


def _git(*args, ok=(0,)):
    r = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    if r.returncode not in ok:
        raise SystemExit(f"onboard: git {' '.join(args)} failed ({r.returncode}): "
                         f"{r.stderr.strip()[:300]}")
    return r.stdout.strip()


def main(argv) -> int:
    check_only = "--check" in argv
    top = _git("rev-parse", "--show-toplevel")
    lines = []
    problems = 0

    for key, value in CONFIG:
        current = _git("config", "--get", key, ok=(0, 1))
        if current == value:
            lines.append(f"  ok      {key} = {value}")
        elif check_only:
            lines.append(f"  MISSING {key} (is {current!r}, needs {value!r})")
            problems += 1
        else:
            _git("config", key, value)
            lines.append(f"  set     {key} = {value}")

    email = _git("config", "--get", "user.email", ok=(0, 1))
    ident = H.check_identity(email)
    if ident:
        problems += 1
        lines.append(f"  TODO    identity: {ident}")
        lines.append("          git config user.email YOUR_USERNAME@users.noreply.github.com")
    else:
        lines.append(f"  ok      identity {email}")

    if (Path(top) / H.DENYLIST_FILE).is_file():
        lines.append(f"  ok      {H.DENYLIST_FILE} present")
    else:
        problems += 1
        lines.append(f"  TODO    {H.DENYLIST_FILE}: not in this checkout. It is never tracked; "
                     "ask the maintainer for it and put it at the repository root. Until "
                     f"then the hooks refuse to commit or push unless {H.NO_DENYLIST_ENV}=1 "
                     "acknowledges the gap for one command.")

    v = sys.version_info
    if v >= (3, 10):
        lines.append(f"  ok      python {v.major}.{v.minor} (3.10 is what the exe is frozen with)")
    else:
        problems += 1
        lines.append(f"  TODO    python {v.major}.{v.minor}: the converter and its suite need 3.10 or newer")
    if importlib.util.find_spec("pytest") is not None:
        lines.append("  ok      pytest importable")
    else:
        lines.append("  TODO    pip install -r requirements.txt pytest pyflakes vulture==2.16")

    print(f"onboard: {top}")
    print("\n".join(lines))
    print(f"next: work on a lane in its own worktree -- {H.LANE_COMMAND}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
