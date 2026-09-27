"""The gate judges weight 0.

Until 2026-09-21 `bust_gap_score` and `nipple_clearance` scored `_1` files only,
so this gate could not see the weight-0 half of the pack: a change that
regressed it PASSED, and one that repaired it had nowhere to show. Both now
print a weight-0 block and the gate reads it into rows of its own, under the
same rules as weight 1.

Two kinds of test here. The verdict tests pin the payoff -- a candidate worse
at weight 0 ALONE fails -- and the "never ok, never FAIL" treatment of an
unmeasured weight. The round-trip tests build the weight-0 text with each
scorer's REAL formatter and read it with the gate's REAL parser, so a rewording
on either side breaks here instead of quietly dropping the row.
"""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import acceptance as acc          # noqa: E402
from scripts.analysis import bust_gap_score as bgs      # noqa: E402
from scripts.analysis import nipple_clearance as ncl    # noqa: E402
from tests.test_acceptance_gate import _base            # noqa: E402


def _judge(c, d, g, tp, zw, fo):
    return acc.verdict_table(c, d, g, tp, zw, fo, False)


def _row(rows, name):
    return next(r for r in rows if r[0] == name)


# ------------------------------------------------------------- the verdicts

def test_identical_arms_still_pass_with_weight0_judged():
    rows, ok = _judge(*_base())
    assert ok
    assert _row(rows, "tip clearance p50, weight 0")[3] == "ok"
    assert _row(rows, "bust-band pen, copy path, weight 0")[3] == "ok"


def test_less_tip_room_at_weight0_alone_FAILS_the_gate():
    c, d, g, tp, zw, fo = _base()
    tp["w0_tip_p50_candidate"] = tp["w0_tip_p50_control"] - 0.2
    rows, ok = _judge(c, d, g, tp, zw, fo)
    assert not ok
    assert _row(rows, "tip clearance p50, weight 0")[3] == "FAIL"
    assert _row(rows, "tip clearance p50")[3] == "ok"      # weight 1 is fine


def test_a_piece_losing_tip_room_at_weight0_FAILS():
    c, d, g, tp, zw, fo = _base()
    tp["w0_less_room"] = 2.0
    rows, ok = _judge(c, d, g, tp, zw, fo)
    assert not ok
    assert _row(rows, "pieces with less room at the tip, weight 0")[3] == "FAIL"


def test_more_bust_penetration_at_weight0_alone_FAILS_the_gate():
    c, d, g, tp, zw, fo = _base()
    g["w0_pen_swap_candidate"] = g["w0_pen_swap_control"] + 40.0
    rows, ok = _judge(c, d, g, tp, zw, fo)
    assert not ok
    assert _row(rows, "bust-band pen, body-swap, weight 0")[3] == "FAIL"
    assert _row(rows, "bust-band pen, body-swap")[3] == "ok"


def test_an_unmeasured_weight0_is_SKIPPED_never_ok_never_FAIL():
    c, d, g, tp, zw, fo = _base()
    for k in [k for k in tp if k.startswith("w0_")]:
        tp.pop(k)
    tp["w0_tip_skipped"] = True
    for k in [k for k in g if k.startswith("w0_")]:
        g.pop(k)
    g["w0_bust_skipped"] = True
    rows, ok = _judge(c, d, g, tp, zw, fo)
    assert ok, "an unmeasured weight must not fail the arm"
    w0 = [r for r in rows if "weight 0" in r[0] and r[3] != "info"]
    assert w0 and all(r[3] == "SKIPPED" for r in w0)


def test_a_weight0_row_that_vanished_without_a_refusal_still_FAILS():
    """Only the scorer's EXPLICIT refusal earns SKIPPED."""
    c, d, g, tp, zw, fo = _base()
    tp.pop("w0_tip_p50_candidate")
    _rows, ok = _judge(c, d, g, tp, zw, fo)
    assert not ok


# ------------------------------------------- round trips: scorer -> gate

LABELS = ["control", "candidate"]


def test_the_gate_reads_nipple_clearances_weight0_block(monkeypatch):
    ctrl = np.array([0.2, 0.5, 0.9])
    cand = np.array([0.1, 0.4, 0.8])
    rows = [("a_0.nif", {"control": ctrl, "candidate": cand})]
    text = "\n".join(ncl._weight0_lines(LABELS, rows, 40, 1, 0, 0))
    monkeypatch.setattr(acc, "_run", lambda *a, **k: text)
    out = acc.tip("ctrl", "cand")
    assert out["w0_tip_p50_control"] == round(float(np.median(ctrl)), 3)
    assert out["w0_tip_p50_candidate"] == round(float(np.median(cand)), 3)
    assert out["w0_tip_p05_candidate"] == round(float(np.percentile(cand, 5)), 3)
    assert out["w0_less_room"] == 1
    assert not out.get("w0_tip_skipped")


def test_the_gate_reads_an_unmeasured_nipple_weight0_as_skipped(monkeypatch):
    for text in ("\n".join(ncl._weight0_lines(LABELS, [], 40, 0, 0, 0)),
                 "weight 0 NOT MEASURED -- no weight-0 body"):
        monkeypatch.setattr(acc, "_run", lambda *a, t=text, **k: t)
        assert acc.tip("ctrl", "cand").get("w0_tip_skipped"), text


def test_the_gate_reads_bust_gap_scores_weight0_block(monkeypatch):
    keys = ["!UBE/p%02d_0.nif|s" % i for i in range(12)]
    swap = {k: i < 6 for i, k in enumerate(keys)}
    data = {"control": {k: (1.0 + 0.01 * i, 2, -0.1) for i, k in enumerate(keys)},
            "candidate": {k: (0.9 + 0.01 * i, 3, -0.2) for i, k in enumerate(keys)}}
    a_vals = np.array([0.8 + 0.01 * i for i in range(12)])
    arms = [("control", "c"), ("candidate", "d")]
    text = "\n".join(bgs._table_lines(arms, data, keys, a_vals, swap, w0=True))
    monkeypatch.setattr(acc, "_run", lambda *a, **k: text)
    out = acc.bust_gap("ctrl", "cand")
    sw = np.array([data["control"][k][0] for k in keys[:6]]) - a_vals[:6]
    assert out["w0_gap_swap_control"] == round(float(np.median(sw)), 3)
    assert out["w0_pen_swap_candidate"] == 18.0          # 6 shapes x 3
    assert out["w0_pen_copy_control"] == 12.0            # 6 shapes x 2
    assert not any(k.startswith("gap_") or k.startswith("pen_") for k in out), (
        "weight-0 text must never fill a weight-1 key")


def test_the_gate_reads_a_too_small_bust_weight0_path_as_skipped(monkeypatch):
    keys = ["!UBE/p%02d_0.nif|s" % i for i in range(8)]
    swap = {k: i < 2 for i, k in enumerate(keys)}          # 2 body-swap: too few
    data = {lab: {k: (1.0, 1, 0.0) for k in keys} for lab in LABELS}
    arms = [("control", "c"), ("candidate", "d")]
    text = "\n".join(bgs._table_lines(arms, data, keys, np.full(8, 0.8), swap,
                                      w0=True))
    monkeypatch.setattr(acc, "_run", lambda *a, **k: text)
    out = acc.bust_gap("ctrl", "cand")
    assert out.get("w0_skipped_swap") == 2
    assert "skipped_swap" not in out
