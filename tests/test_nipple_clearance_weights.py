"""The weight-0 block must be invisible to the gate.

`nipple_clearance` now also scores the `_0` files, on the weight-0 body, and
prints them after the gated weight-1 report. `acceptance.py` reads that output
with three patterns: the first `^control` / `^candidate` line carrying three
numbers, the FIRST `tighter N`, and any "0/0 IS NOT A PASS", which marks the
whole tip row unmeasured. A weight-0 line matching the first two would re-gate
the run on the wrong weight; the third would switch the tip gate off for
weight 1 too. These tests run the REAL parser, not a copy of its patterns.
"""
import re
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import acceptance as acc          # noqa: E402
from scripts.analysis import nipple_clearance as ncl    # noqa: E402

LABELS = ["control", "candidate"]


def _rows():
    """Two pieces, negatives included -- a negative tip clearance IS the
    poke-through, and an unsigned pattern once stopped matching exactly those."""
    return [("a_0.nif", {"control": np.array([-0.4, 0.2, 0.9]),
                         "candidate": np.array([-0.6, 0.1, 0.8])}),
            ("b_0.nif", {"control": np.array([0.5, 1.1, 1.3]),
                         "candidate": np.array([0.3, 1.0, 1.2])})]


def _w0(rows):
    return "\n".join(ncl._weight0_lines(LABELS, rows, 40, len(rows), 0, 0))


def _w1_report():
    return (
        "tip vertices cast              : 40\n"
        "pieces covering the nipple in EVERY arm: 2\n\n"
        f"{'arm':<22} {'tip clearance p50':>18} {'p05':>8} {'min':>8}\n"
        f"{'control':<22} {1.100:>18.3f} {0.500:>8.3f} {-0.100:>8.3f}\n"
        f"{'candidate':<22} {1.050:>18.3f} {0.480:>8.3f} {-0.200:>8.3f}\n\n"
        "per piece, candidate minus control (negative = LESS room over the "
        "nipple):\n"
        "  tighter 3   looser 2   flat 5   median delta -0.0100u\n")


def test_no_weight0_line_parses_as_a_gated_arm_row():
    text = _w0(_rows())
    for lab in LABELS:
        assert re.search(acc._TIP_ROW % lab, text, re.M) is None, lab


def test_the_weight0_block_never_says_tighter():
    assert acc._num(_w0(_rows()), r"tighter\s+(\d+)") is None


def test_an_empty_weight0_never_marks_the_tip_row_unmeasured():
    assert "0/0 IS NOT A PASS" not in _w0([])


def test_the_gate_reads_weight1_with_weight0_appended(monkeypatch):
    """End to end through acceptance.tip: both weight-0 blocks -- measured and
    empty -- appended after the weight-1 report change nothing it reads."""
    text = _w1_report() + "\n" + _w0(_rows()) + "\n" + _w0([])
    monkeypatch.setattr(acc, "_run", lambda *a, **k: text)
    out = acc.tip("ctrl", "cand")
    assert out["tip_p50_control"] == 1.1
    assert out["tip_p50_candidate"] == 1.05
    assert out["tip_min_candidate"] == -0.2
    assert out["tighter"] == 3
    assert not out.get("tip_skipped")


def test_the_weight0_block_asks_for_the_weight0_body(monkeypatch):
    """Scoring `_0` files on the weight-1 rays is the defect this closes."""
    asked = []

    def fake_tip_rays(weight="_1"):
        asked.append(weight)
        raise FileNotFoundError("no weight-0 body")

    monkeypatch.setattr(ncl, "tip_rays", fake_tip_rays)
    lines = ncl._weight0_block([("arm", "control")])
    assert asked == ["_0"]
    assert any("NOT MEASURED" in line for line in lines)
