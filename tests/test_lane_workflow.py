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

"""scripts/onboard.py and scripts/lane.py: the one-command clone setup and the
one-branch-one-worktree helper the hooks assume. #worktree-per-branch

Both run against throwaway repositories, with identity passed per command so a
CI runner that has none still runs them.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from scripts import repo_hygiene as H  # noqa: E402

IDENTITY = dict(GIT_AUTHOR_NAME="lane test", GIT_AUTHOR_EMAIL="noreply@example.invalid",
                GIT_COMMITTER_NAME="lane test", GIT_COMMITTER_EMAIL="noreply@example.invalid")


def _repo(tmp_path, *scripts):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("__init__.py", "repo_hygiene.py", *scripts):
        shutil.copy2(PROJ / "scripts" / name, repo / "scripts" / name)

    def git(*args, ok=0):
        r = subprocess.run(["git", "-C", str(repo), "-c", "core.longpaths=true", *args],
                           capture_output=True, text=True, env=dict(os.environ, **IDENTITY))
        assert r.returncode == ok, f"git {' '.join(args)}: {r.stderr}"
        return r.stdout.strip()

    git("init", "-q")
    return repo, git


def _run(repo, script, *args, **env_extra):
    return subprocess.run([sys.executable, str(repo / "scripts" / script), *args],
                          cwd=str(repo), capture_output=True, text=True,
                          env=dict(os.environ, **env_extra))


def test_onboard_configures_the_clone_and_reports_what_it_cannot_supply(tmp_path):
    repo, git = _repo(tmp_path, "onboard.py")
    (repo / ".githooks").mkdir()
    r = _run(repo, "onboard.py")
    assert r.returncode == 1, r.stdout + r.stderr          # no identity, no denylist yet
    for key, value in (("core.hooksPath", ".githooks"), ("core.autocrlf", "false"),
                       ("pull.ff", "only"), ("blame.ignoreRevsFile", ".git-blame-ignore-revs")):
        assert git("config", "--get", key) == value, key
    assert "user.email" in r.stdout and H.DENYLIST_FILE in r.stdout, r.stdout
    assert "lane.py" in r.stdout, "the next step is named"

    git("config", "user.email", "noreply@example.invalid")
    (repo / H.DENYLIST_FILE).write_text("re:sure\\s+heart\n", encoding="utf-8")
    r = _run(repo, "onboard.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "set " not in r.stdout, "a second run has nothing left to set"

    # --check reports and changes nothing
    git("config", "--unset", "pull.ff")
    r = _run(repo, "onboard.py", "--check")
    assert r.returncode == 1 and "MISSING pull.ff" in r.stdout, r.stdout
    assert git("config", "--get", "pull.ff", ok=1) == ""
    r = _run(repo, "onboard.py")
    assert r.returncode == 0 and git("config", "--get", "pull.ff") == "only"

    # a personal address is what the hook would refuse, and the fix is spelled out
    git("config", "user.email", "someone.personal" + "@" + "gmail.com")
    r = _run(repo, "onboard.py")
    assert r.returncode == 1 and "noreply" in r.stdout, r.stdout


def test_lane_new_and_rm_make_and_unmake_a_worktree_with_what_the_hooks_need(tmp_path):
    repo, git = _repo(tmp_path, "lane.py")
    (repo / "README.md").write_bytes(b"x\n")
    git("add", "README.md")
    git("commit", "-q", "-m", "base")
    (repo / H.DENYLIST_FILE).write_text("re:sure\\s+heart\n", encoding="utf-8")
    (repo / ".venv-build").mkdir()
    (repo / ".venv-build" / "marker.txt").write_bytes(b"the primary checkout's environment\n")

    r = _run(repo, "lane.py", "new", "feature-x", "--from", "HEAD", "--no-fetch")
    assert r.returncode == 0, r.stdout + r.stderr
    lane = tmp_path / "repo.feature-x"
    assert lane.is_dir() and (lane / "README.md").is_file(), "a worktree beside the checkout"
    head = subprocess.run(["git", "-C", str(lane), "symbolic-ref", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == "feature-x"
    assert (lane / H.DENYLIST_FILE).read_text(encoding="utf-8").startswith("re:"), (
        "the denylist is copied in: an untracked file does not travel with a worktree")
    assert (lane / ".venv-build" / "marker.txt").is_file(), (
        "the build environment is joined into the lane")
    assert "feature-x" in _run(repo, "lane.py", "list").stdout

    # the same name twice, and the branches that never get a lane
    assert _run(repo, "lane.py", "new", "feature-x", "--from", "HEAD", "--no-fetch").returncode != 0
    for name in (*H.INTEGRATION_BRANCHES, "archive/old", "../escape"):
        r = _run(repo, "lane.py", "new", name, "--from", "HEAD", "--no-fetch")
        assert r.returncode != 0, name
        assert not (tmp_path / f"repo.{name.replace('/', '-')}").exists(), name

    r = _run(repo, "lane.py", "rm", "feature-x")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not lane.exists(), "the worktree is gone"
    assert (repo / ".venv-build" / "marker.txt").is_file(), (
        "removing the lane must not reach through the join into the primary's environment")
    assert git("branch", "--list", "feature-x") == "", "the branch is gone"
