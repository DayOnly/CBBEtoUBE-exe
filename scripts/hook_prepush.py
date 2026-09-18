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

"""pre-push body: check every commit the push would publish. #prepush-range

pre-commit and commit-msg run when a commit is CREATED. `git rebase` replays
commits without either, `git cherry-pick` runs neither, a merge commit runs
commit-msg but not pre-commit, and `--no-verify` skips both -- so a lane
committed or rebased on a machine without the denylist reaches `git push`
unchecked. This applies the same rules to each commit about to leave: its
message, its author and committer addresses, and every file it adds or changes,
read AT that commit (a name added in one commit and deleted in the next is still
published history).

The denylist is looked up the way pre-commit looks it up, and a push from a
checkout that has none anywhere is refused unless the gap is acknowledged for
this one command (#hook-fail-closed): a name pushed to a lane is public the
moment it lands.

git passes the remote's name and URL as arguments, and one line per ref on
stdin: `<local ref> <local sha> <remote ref> <remote sha>`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import hook_precommit as P  # noqa: E402
from scripts import repo_hygiene as H  # noqa: E402

ZERO = "0" * 40


def commits_to_push(local_sha: str, remote: str) -> "list[str]":
    """Commits reachable from `local_sha` that no ref of `remote` already has,
    oldest first -- for a new branch, its own commits, not the whole history."""
    out = P._run("git", "rev-list", "--reverse", local_sha, "--not", f"--remotes={remote}")
    return [c for c in out.split() if c]


def commit_problems(sha: str, denylist) -> "list[str]":
    short = sha[:12]
    msg = P._run("git", "log", "-1", "--format=%B", sha)
    problems = [f"commit {short}: {p}" for p in H.scan_message(msg, denylist)]
    for email in sorted(set(P._run("git", "log", "-1", "--format=%ae%n%ce", sha).split())):
        ident = H.check_identity(email)
        if ident:
            problems.append(f"commit {short}: {email} -- {ident}")
    paths = [p for p in P._run("git", "diff-tree", "--root", "--no-commit-id", "--name-only",
                               "-r", "--diff-filter=ACMR", sha).splitlines() if p]

    def read(path):
        return P._run_bytes("git", "show", f"{sha}:{path}")

    problems += [f"commit {short}: {p}" for p in P.path_problems(paths, read, denylist)]
    return problems


def main(argv, stdin=None) -> int:
    remote = argv[1] if len(argv) > 1 else "origin"
    problems, seen = [], set()
    denylist = P.denylist_for_hook(Path(__file__).resolve().parent.parent, problems)
    try:
        for line in (stdin or sys.stdin).read().splitlines():
            parts = line.split()
            if len(parts) != 4 or parts[1] == ZERO:
                continue              # a deleted ref publishes nothing
            for sha in commits_to_push(parts[1], remote):
                if sha not in seen:
                    seen.add(sha)
                    problems += commit_problems(sha, denylist)
    except P.GitFailed as exc:        # an unreadable range is not an empty one
        sys.stderr.write(P.git_failed_report("PUSH", "push", exc))
        return 1
    if not problems:
        return 0
    sys.stderr.write("\nPUSH BLOCKED -- public-repo hygiene\n\n")
    for p in problems:
        sys.stderr.write(f"  {p}\n")
    sys.stderr.write(
        "\nEach commit above would become public on push, and stays reachable by SHA\n"
        "even after it is removed. Fix the commits (amend or rebase), or\n"
        "`git push --no-verify` if you are certain.\n\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
