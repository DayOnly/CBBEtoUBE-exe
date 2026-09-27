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

"""Lanes: one branch, one linked worktree, beside the primary checkout.

    python scripts/lane.py new <name> [--from origin/testing] [--no-fetch]
    python scripts/lane.py list
    python scripts/lane.py rm <name> [--force]

new: fetches, creates branch <name> at --from (origin/testing unless told
otherwise) and checks it out in <primary>.<name> next to the primary checkout.
It copies the asset denylist in, because an untracked file does not travel
with a worktree and the hooks refuse to run without it; and it joins the
primary checkout's build environment (.venv-build) into the lane, so a rebuild
there reproduces byte for byte -- a build from a fresh environment differs in
one file of pip's own metadata (docs/RELEASING.md).

rm: removes the join and the copy, removes the worktree, and deletes the
branch, which git refuses while it is unmerged (--force to insist). The join is
removed as a link, never followed: the primary checkout's environment stays.

Integration and archive branch names are refused; those never get a lane.
#worktree-per-branch
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import repo_hygiene as H  # noqa: E402

VENV = ".venv-build"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _git(*args, ok=(0,)):
    r = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    if r.returncode not in ok:
        raise SystemExit(f"lane: git {' '.join(args)} failed ({r.returncode}): "
                         f"{r.stderr.strip()[:400]}")
    return r.stdout.strip()


def primary_root() -> Path:
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent


def lane_dir(primary: Path, name: str) -> Path:
    return primary.parent / f"{primary.name}.{name.replace('/', '-')}"


def refuse_name(name: str) -> None:
    if not NAME_RE.match(name) or ".." in name:
        raise SystemExit(f"lane: {name!r} is not a branch name this tool creates")
    if name in H.INTEGRATION_BRANCHES or name.startswith("archive/"):
        raise SystemExit(f"lane: {name!r} is an integration or archive branch; "
                         "those never get a lane")


def _is_junction(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)
    return stat.S_ISDIR(st.st_mode) and getattr(st, "st_reparse_tag", 0) == tag


def join_venv(primary: Path, lane: Path) -> str:
    src, dst = primary / VENV, lane / VENV
    if not src.is_dir():
        return f"no {VENV} in the primary checkout; a build here would create its own"
    if dst.exists():
        return f"{VENV} already present"
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                           capture_output=True, text=True, errors="replace")
        if r.returncode:
            return f"could not join {VENV}: {(r.stderr or r.stdout).strip()}"
        return f"{VENV} joined (junction to the primary checkout's)"
    os.symlink(src, dst, target_is_directory=True)
    return f"{VENV} joined (symlink to the primary checkout's)"


def unjoin_venv(lane: Path) -> None:
    dst = lane / VENV
    if dst.is_symlink():
        dst.unlink()
    elif _is_junction(dst):
        os.rmdir(dst)          # removes the junction itself, never what it points at


def cmd_new(args) -> int:
    refuse_name(args.name)
    primary = primary_root()
    lane = lane_dir(primary, args.name)
    if lane.exists():
        raise SystemExit(f"lane: {lane} already exists")
    if not args.no_fetch:
        _git("fetch", "origin")
    _git("worktree", "add", str(lane), "-b", args.name, args.start)
    notes = [f"branch {args.name} at {args.start}, worktree {lane}"]
    deny = primary / H.DENYLIST_FILE
    if deny.is_file():
        shutil.copy2(deny, lane / H.DENYLIST_FILE)
        notes.append(f"{H.DENYLIST_FILE} copied in")
    else:
        notes.append(f"no {H.DENYLIST_FILE} in the primary checkout: the hooks refuse to "
                     f"commit here until it exists or {H.NO_DENYLIST_ENV}=1 acknowledges "
                     "the gap")
    notes.append(join_venv(primary, lane))
    print("\n".join("lane: " + n for n in notes))
    return 0


def cmd_list(_args) -> int:
    print(_git("worktree", "list"))
    return 0


def cmd_rm(args) -> int:
    refuse_name(args.name)
    primary = primary_root()
    lane = lane_dir(primary, args.name)
    if lane.exists():
        unjoin_venv(lane)
        copy = lane / H.DENYLIST_FILE
        if copy.is_file():
            copy.unlink()
        _git("worktree", "remove", *(["--force"] if args.force else []), str(lane))
    _git("worktree", "prune")
    _git("branch", "-D" if args.force else "-d", args.name)
    print(f"lane: {args.name} removed")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="lane", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="branch + worktree + denylist + build environment")
    new.add_argument("name")
    new.add_argument("--from", dest="start", default="origin/testing",
                     help="where the branch starts (default: origin/testing)")
    new.add_argument("--no-fetch", action="store_true", help="do not fetch origin first")
    new.set_defaults(fn=cmd_new)
    sub.add_parser("list", help="every worktree of this repository").set_defaults(fn=cmd_list)
    rm = sub.add_parser("rm", help="remove the worktree and delete the branch")
    rm.add_argument("name")
    rm.add_argument("--force", action="store_true", help="even if unmerged or dirty")
    rm.set_defaults(fn=cmd_rm)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
