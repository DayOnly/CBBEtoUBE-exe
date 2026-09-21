"""The weight-0 block must be invisible to the gate -- and this parser is the
dangerous one.

`acceptance.bust_gap` walks `bust_gap_score`'s output line by line. A line with
"body-swap only" / "copy path only" switches a section that STAYS switched for
every later line; a matching `control|candidate bust p50 ... gap p50 ... pen N`
row OVERWRITES the value read before it (last match wins, not first); and a
line matching `SKIP_NOTE` marks that path as unmeasured. So a weight-0 row that
matched would silently REPLACE a gated weight-1 value. These tests build both
blocks with the tool's real `_table_lines` and read them with the real parser.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import acceptance as acc          # noqa: E402
from scripts.analysis import bust_gap_score as bgs      # noqa: E402

ARMS = [("control", "ctrl_dir"), ("candidate", "cand_dir")]


def _inputs(n_swap=6, n_copy=6, shift=0.0):
    """One table's inputs: body-swap and copy-path shapes, distinct per arm.
    `shift` moves every number, so weight 0 cannot coincide with weight 1."""
    keys = ["!UBE/p%02d_0.nif|s" % i for i in range(n_swap + n_copy)]
    swap = {k: i < n_swap for i, k in enumerate(keys)}
    data = {"control": {k: (1.0 + 0.01 * i + shift, 2, -0.1)
                        for i, k in enumerate(keys)},
            "candidate": {k: (0.9 + 0.01 * i + shift, 3, -0.2)
                          for i, k in enumerate(keys)}}
    a_vals = np.array([0.8 + 0.01 * i for i in range(len(keys))])
    return data, keys, a_vals, swap


def _w1_text():
    return "\n".join(bgs._table_lines(ARMS, *_inputs()))


def _w0_text(**kw):
    return "\n".join(bgs._table_lines(ARMS, *_inputs(shift=0.5, **kw), w0=True))


def _parse(text, monkeypatch):
    monkeypatch.setattr(acc, "_run", lambda *a, **k: text)
    return acc.bust_gap("ctrl", "cand")


def _w1(parsed):
    """The weight-1 reads. The gate may read weight 0 as well -- into keys of
    its own, `w0_...` -- but it must never let weight 0 touch THESE."""
    return {k: v for k, v in parsed.items() if not k.startswith("w0_")}


def test_appending_weight0_changes_nothing_the_gate_reads(monkeypatch):
    alone = _parse(_w1_text(), monkeypatch)
    # CONTROL: the weight-1 table really is parsed, or "unchanged" means nothing.
    assert {"gap_swap_control", "gap_copy_candidate",
            "pen_swap_candidate"} <= set(alone)
    assert "w0 control" in _w0_text()          # and weight 0 really has rows
    both = _parse(_w1_text() + "\n" + _w0_text(), monkeypatch)
    assert _w1(both) == _w1(alone)


def test_a_weight0_too_few_note_does_not_mark_a_path_unmeasured(monkeypatch):
    alone = _parse(_w1_text(), monkeypatch)
    both = _parse(_w1_text() + "\n" + _w0_text(n_swap=2, n_copy=2), monkeypatch)
    assert _w1(both) == _w1(alone)
    assert not any(k.startswith("skipped_") for k in both)


def test_no_weight0_line_switches_the_parsers_section():
    for line in (_w0_text() + "\n" + _w0_text(n_swap=2, n_copy=2)).splitlines():
        assert "body-swap only" not in line, line
        assert "copy path only" not in line, line


def test_measure_arm_scores_weight0_files_against_the_weight0_body(
        tmp_path, monkeypatch):
    """The copy-path body is looked up for the file's OWN weight."""
    d = tmp_path / "meshes" / "!UBE" / "a"
    d.mkdir(parents=True)
    (d / "piece_0.nif").write_bytes(b"x")
    (d / "piece_1.nif").write_bytes(b"x")
    opened, asked = [], []
    monkeypatch.setattr(bgs.nif_io, "open_nif_retry",
                        lambda p: opened.append(Path(p).name) or object())

    def fake_body_for(_nf, weight):
        asked.append(weight)
        return None, False

    monkeypatch.setattr(bgs, "_body_for", fake_body_for)
    bgs.measure_arm(str(tmp_path), "_0")
    assert opened == ["piece_0.nif"]
    assert asked == ["_0"]


def test_author_baseline_uses_the_weight0_author_body(monkeypatch):
    asked = []

    def fake_cbbe(mods_root=None, weight="_1"):
        asked.append(weight)
        raise FileNotFoundError("stop here")

    monkeypatch.setattr(bgs.cb, "canonical_cbbe", fake_cbbe)
    with pytest.raises(FileNotFoundError):
        bgs.author_baseline({"!UBE/x_0.nif|s"}, mods_root="root", weight="_0")
    assert asked == ["_0"]
