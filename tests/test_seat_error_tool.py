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

"""`seat_error_vs_author` must FAIL when it pairs nothing.

The tool exists because "there is no author baseline for this sample" was
written down as a finding on 2026-09-03 and was WRONG -- the authors were
VFS-resolved and folder pairing simply could not see them. A fidelity metric
that prints a clean empty table when it finds no author reproduces exactly that
mistake, one layer down, so the empty case is the behaviour worth pinning.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "analysis"))

import seat_error_vs_author as se   # noqa: E402


def test_no_args_explains_itself_and_does_not_exit_zero():
    assert se.main([]) == 2


def test_an_empty_population_is_a_FAILURE_not_an_empty_table(tmp_path, capsys):
    """0/0 is not a pass (feedback_census_population)."""
    empty = tmp_path / "arm"
    (empty / "meshes").mkdir(parents=True)
    rc = se.main([str(empty)])
    out = capsys.readouterr().out
    assert rc == 1, "an empty pairing must exit non-zero, not report success"
    assert "0/0" in out or "NOTHING PAIRED" in out, (
        "the operator must be told the run measured nothing; a silent empty "
        "table reads as 'no error found'")


def test_it_scores_standoff_difference_not_raw_position():
    """The author's cloth sits on a CBBE body and ours on a UBE body, so a
    position diff would score the body swap. Pin the two-body comparison in the
    source, since getting it wrong still produces plausible-looking numbers."""
    src = Path(se.__file__).read_text(encoding="utf-8")
    assert 'canonical_cbbe(weight=w)' in src and 'canonical_ube(weight=w)' in src, (
        "the metric must measure the author against the CBBE body and ours "
        "against the UBE body, each at the file's own weight")
    assert "o_off - a_off" in src, (
        "the error must be the difference of two STANDOFFS, not of positions")
