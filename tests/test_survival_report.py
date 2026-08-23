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

"""The survival summary must not let one many-shape garment write the answer.

THE FAILURE BEING PINNED, in full: the 2026-08-17 pass-usefulness audit reported
"`inflate` is 69% UNDONE by `conform`" from a row-pooled median of 0.31. A
survival record is per SHAPE, and one garment (`AsurasCombined`) contributed 116
of 442 rows at median 0.103. Weighted per PIECE the same data reads 0.69 and the
two convert paths agree. The headline stood six days, was quoted as settled, and
was never true.

So `summarise()` reports both numbers and names any dominating piece. These
tests fail if it stops doing either.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import survival_report as sr  # noqa: E402


def _rec(piece, shape, surv, weight="_1", moved=0.5, **kw):
    r = {"kind": "survival", "pass": "inflate", "shape": shape,
         "nif": f"{piece}{weight}.nif", "path": f"!UBE/mod/{piece}{weight}.nif",
         "survival": surv, "moved_mean": moved, "moved_max": moved * 2,
         "moved_verts": 100, "frac_cancelled": 0.0, "frac_kept": 1.0}
    r.update(kw)
    return r


def test_one_many_shape_garment_cannot_own_the_median():
    """THE REGRESSION TEST. A hostile-but-real shape: one piece with many rows
    at LOW survival, several pieces with few rows at HIGH survival."""
    rows = [_rec("bighulk", f"s{i}", 0.10) for i in range(30)]
    for p in ("alpha", "bravo", "charlie", "delta"):
        rows += [_rec(p, "only", 0.90)]
    s = {r["pass"]: r for r in sr.summarise(rows)}["inflate"]
    assert s["surv"] < 0.4, "row-pooled median should be dragged down"
    assert s["survPC"] > 0.6, "per-piece median should not be"
    assert s["dom_piece"] and "bighulk" in s["dom_piece"]
    assert s["dom_share"] > 0.25
    assert s["pieces"] == 5


def test_a_balanced_population_is_not_flagged():
    """ANTI-VACUITY. If the dominance note fired on everything it would be
    ignored, and a checker people ignore is worse than none."""
    rows = []
    for p in ("alpha", "bravo", "charlie", "delta", "echo"):
        rows += [_rec(p, f"s{i}", 0.80) for i in range(4)]
    s = {r["pass"]: r for r in sr.summarise(rows)}["inflate"]
    assert abs(s["surv"] - s["survPC"]) < 0.05
    assert s["dom_share"] <= 0.25, "no piece dominates a balanced population"


def test_weight_variants_fold_into_one_piece():
    """`_0` and `_1` are the same GARMENT. Counting them as two would halve
    every piece's weight and quietly re-open the pooling gap."""
    rows = [_rec("dress", "top", 0.5, weight="_0"),
            _rec("dress", "top", 0.5, weight="_1")]
    s = {r["pass"]: r for r in sr.summarise(rows)}["inflate"]
    assert s["pieces"] == 1, "the two weights are one piece"


def test_low_signal_rows_are_excluded_AND_counted():
    """A ratio on motion below the floor is arithmetic about nothing -- but
    dropping it silently is how an exclusion becomes an invisible assumption."""
    rows = [_rec("alpha", "a", 0.9),
            _rec("alpha", "b", 47.0, low_signal=True),
            _rec("bravo", "c", 0.9)]
    s = {r["pass"]: r for r in sr.summarise(rows)}["inflate"]
    assert s["low"] == 1, "the excluded row must be counted"
    assert s["scored"] == 2
    assert abs(s["surv"] - 0.9) < 1e-6, "the 47.0 must not reach the median"


def test_moved_nothing_is_counted_separately_from_low_signal():
    """Two different facts. Conflating them once labelled `chain_blend` -- which
    moves 0.437u -- as inert."""
    rows = [_rec("alpha", "a", 0.9),
            _rec("alpha", "b", 0.0, moved_verts=0),
            _rec("bravo", "c", 0.5, low_signal=True)]
    s = {r["pass"]: r for r in sr.summarise(rows)}["inflate"]
    assert s["nomove"] == 1 and s["low"] == 1


def test_the_pseudo_stage_is_not_a_pass():
    """`(after last pass)` is a post-chain edit the analyser appends, not a
    pass. Counting it would invent a row in every report."""
    rows = [_rec("alpha", "a", 0.9)]
    rows.append(dict(rows[0], **{"pass": "(after last pass)"}))
    names = {r["pass"] for r in sr.summarise(rows)}
    assert names == {"inflate"}
