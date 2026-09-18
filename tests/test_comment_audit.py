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

"""A comment that names a flag, helper or file that does not exist is a wrong
instruction to the next reader. #comment-rot

Two names in src/nif_convert.py told a reader to arm a pair of floors with
environment variables that no code reads: both floors had become default ON
behind kill switches, and an unread variable is a silent no-op. The audit that
found them, scripts/analysis/comment_audit.py, lived untracked until 2026-09-15.

This file RATCHETS checks A, B and C. Each ceiling is the count on the day it
was last lowered; a ceiling above the real count silently permits that many new
ghosts (WHOLE_FILE_READ_CEILING in test_split_preconditions.py is the
precedent). Lower a ceiling when a fix lands; never raise it.

Check D (ghost FILES) is reported by the tool but NOT ratcheted, because its
answer depends on the checkout rather than the code: a reference counts as
present if a file of that name exists anywhere under the repo, generated
outputs and untracked scripts included. Measured on 2026-09-15 it read 6 on
the maintainer's working tree, 23 on a clean checkout of the tracked files and
24 without docs/worklog -- a ceiling on it would pass on one machine and fail
in CI on another. A, B and C read the same on all three.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts.analysis import comment_audit as ca  # noqa: E402

# 2026-09-15, after the two ghost env names were corrected (B was 2).
CEILING = {"A": 0, "B": 0, "C": 80}
# Population floors. 32,939 comment lines on 2026-09-15; 135 scored flag blocks
# on 2026-09-16, once check A read `_flag` bindings (it read the raw
# `os.environ.get` spelling before, and scored 4 -- then 0 when the last raw
# reads moved to the helper).
MIN_COMMENT_LINES = 25000
MIN_POLARITY_SCORED = 100


@pytest.fixture(scope="module")
def text_checks():
    """B and C read text and syntax trees only, so they run in-process."""
    return ca.collect(REPO, "BC")


def test_the_audit_sees_the_tree(text_checks):
    assert text_checks["files"] >= ca.MIN_FILES, text_checks["files"]
    assert text_checks["comments"] >= MIN_COMMENT_LINES, (
        f"only {text_checks['comments']} comment lines read -- the reader has "
        "stopped seeing the tree; 0/0 is not a pass")


def test_no_text_check_exceeds_its_ceiling(text_checks):
    over = []
    for k, rows in text_checks["findings"].items():
        if len(rows) <= CEILING[k]:
            continue
        shown = "\n".join(
            f"    {p.relative_to(REPO).as_posix()}:{ln}  {what} -- {why}"
            for p, ln, what, why in sorted(rows, key=lambda r: (str(r[0]), r[1]))[:12])
        over.append(f"  {k}. {ca.TITLES[k]}: {len(rows)} > ceiling {CEILING[k]}\n{shown}")
    assert not over, (
        "the comment audit found MORE than its ratchet allows. Fix the comment "
        "(the code is the authority); do not raise the ceiling:\n" + "\n".join(over))


def test_the_polarity_check_finds_nothing():
    """Check A imports AND reloads src modules, which would leave other tests
    holding stale classes -- so it runs in its own process, and must say how
    many flag comment blocks it judged."""
    r = subprocess.run([sys.executable, "-m", "scripts.analysis.comment_audit",
                        "--check", "A"], cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "could not import" not in r.stderr, (
        "check A could not import a module, so it judged flags it never read:\n"
        + r.stderr[-2000:])
    scored = re.search(r"^check A scored (\d+) flag comment block", r.stdout, re.M)
    assert scored and int(scored.group(1)) >= MIN_POLARITY_SCORED, (
        "check A judged too few flag comment blocks to mean anything; 0/0 is not "
        "a pass:\n" + r.stdout[-2000:])
    found = re.search(r"^A\. .*\((\d+) finding\(s\)\)", r.stdout, re.M)
    assert found and int(found.group(1)) <= CEILING["A"], r.stdout[-3000:]


def test_a_new_ghost_is_caught(tmp_path):
    """Positive control on a copy: one added comment naming a helper that exists
    nowhere and a variable no code reads must be found -- each by name, and
    nothing else may change."""
    for d in ("src", "scripts"):
        shutil.copytree(REPO / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__"))
    before = ca.collect(tmp_path, "BC")["findings"]
    (tmp_path / "src" / "zz_ghost_control.py").write_bytes(
        b"# see `_no_such_helper_anywhere` and set CBBE2UBE_NO_SUCH_SWITCH_ANYWHERE=1\n")
    after = ca.collect(tmp_path, "BC")["findings"]
    assert [r[2] for r in after["C"] if r[0].name == "zz_ghost_control.py"] == [
        "_no_such_helper_anywhere"]
    assert [r[2] for r in after["B"] if r[0].name == "zz_ghost_control.py"] == [
        "CBBE2UBE_NO_SUCH_SWITCH_ANYWHERE"]
    assert (len(after["B"]), len(after["C"])) == (len(before["B"]) + 1, len(before["C"]) + 1)


def test_the_audit_refuses_an_empty_tree(tmp_path):
    """The tool's own floor: on a tree with no source it must refuse, not print
    TOTAL 0."""
    tool = tmp_path / "scripts" / "analysis" / "comment_audit.py"
    tool.parent.mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "analysis" / "comment_audit.py", tool)
    r = subprocess.run([sys.executable, str(tool), "--check", "BCD"],
                       cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 2 and "measured NOTHING" in r.stdout, r.stdout + r.stderr
