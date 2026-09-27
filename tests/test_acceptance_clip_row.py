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

"""The gate's MORPHED-CLIP rows, and the four defects an adversarial review of
them found. Three of the four would have produced a wrong verdict.

WHY THE ROWS EXIST. Verified 2026-09-09: the gate ran six scorers and NOT ONE
applied a slider, so it had no row for body-through-garment under a body preset
-- the form every in-game bust report arrives in -- and a candidate that
IMPROVED morphed clipping while costing any surface row could only ever FAIL,
the benefit having nowhere to appear.

WHAT IS PINNED HERE, each item a defect that was actually present:

  1. THE POPULATION IS THE INTERSECTION. Each arm's census re-applies its own
     per-piece exclusions to its OWN geometry, so a candidate that pushes
     garments off the body drops the clipping pieces from its own denominator
     and the share improves for a reason that is not quality. Measured before
     the fix: control 92 scored against candidate 41 scored read **ok on all
     three rows**.
  2. COUNTS, NOT SHARES -- which is only safe BECAUSE the denominator is fixed.
     `add()` applies its 0.005 tolerance to non-integer floats, and a share is
     an exact k/n ratio with no float noise to absorb, so on a share that
     tolerance swallowed whole pieces once the population passed ~200.
  3. EVERY REFUSAL SKIPS LOUDLY AND NEVER READS AS ok -- and the tags
     DISTINGUISH an unset preset from a configured-but-broken one. They did not:
     "is not a file" contains "CLIP_PRESET", so a renamed file rendered exactly
     like an unconfigured machine and the rows would stay dark run after run.
  4. THE CENSUS'S OWN BIAS WARNING IS CARRIED THROUGH. It prints "HIGH: treat
     every share below as a biased subset" and still reports; nothing parsed it,
     so a biased subset read as a clean row.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.analysis import acceptance as acc          # noqa: E402


def _rows(n, clipping=0, morph_hi=0, bind=0, prefix="p"):
    """n pieces, of which `clipping` clip morphed, `morph_hi` clip > 1.0%."""
    out = {}
    for i in range(n):
        out["%s/%d_1.nif" % (prefix, i)] = {
            "morph": (2.0 if i < morph_hi else
                      (0.5 if i < clipping else 0.0)),
            "bind": (1.0 if i < bind else 0.0),
        }
    return out


def _arm(rows, scored=None, biased=False):
    return {"rows": rows, "scored": scored if scored is not None else len(rows),
            "biased": biased}


def _clip_from(a, b, monkeypatch, tmp_path, preset_ok=True):
    """Drive clip() with two prepared arms, stubbing the census subprocess."""
    preset = tmp_path / "p.xml"
    if preset_ok:
        preset.write_text("<x/>", encoding="utf-8")
    monkeypatch.setenv("CBBE2UBE_CLIP_PRESET", str(preset))
    monkeypatch.delenv("CBBE2UBE_CLIP_EVERY", raising=False)
    seq = [a, b]

    def fake(mod, *args):
        arm = seq.pop(0)
        if "skip" in arm:
            return "ABORT: " + arm["skip"] + "\n"
        out = args[args.index("--out") + 1]
        doc = {"scored": arm["scored"],
               "rows": [dict(r, nif=str(Path(args[args.index("--pack") + 1])
                                         / k)) for k, r in arm["rows"].items()]}
        Path(out).write_text(json.dumps(doc), encoding="utf-8")
        return ("band bust\nexamined 1 SCORED %d\n" % arm["scored"]) + (
            "  errored ... HIGH: treat every share below as a biased subset\n"
            if arm.get("biased") else "")

    monkeypatch.setattr(acc, "_run", fake)
    return acc.clip("ctrlarm", "candarm")


# ---------------------------------------------------------------- population

def test_a_candidate_cannot_improve_by_shrinking_its_own_denominator(
        monkeypatch, tmp_path):
    """THE HIGH-SEVERITY DEFECT, pinned. Control scores 92 pieces of which 46
    clip; the candidate drops the 51 WORST pieces from its own census and keeps
    41, of which 5 clip. Scored as shares that read ok on every row. Counted
    over the intersection it is judged on the 41 shared pieces only."""
    ctrl_rows = _rows(92, clipping=46, morph_hi=34, bind=23)
    keep = sorted(ctrl_rows)[51:]                 # the candidate lost 51
    cand_rows = {k: dict(ctrl_rows[k]) for k in keep}
    got = _clip_from(_arm(ctrl_rows), _arm(cand_rows), monkeypatch, tmp_path)
    assert got["common"] == len(keep) == 41
    assert got["scored_ctrl"] == 92 and got["scored_cand"] == 41
    # counted over the SHARED pieces, both arms describe the same 41
    assert got["ctrl"]["morph05"] == got["cand"]["morph05"]


def test_a_divergent_population_is_surfaced_as_an_info_row(monkeypatch, tmp_path):
    ctrl_rows = _rows(50, clipping=10)
    cand_rows = {k: dict(v) for k, v in list(ctrl_rows.items())[:40]}
    cl = _clip_from(_arm(ctrl_rows), _arm(cand_rows), monkeypatch, tmp_path)
    rows, _ok = _table(cl)
    info = [r for r in rows if "census scored per arm" in r[0]]
    assert info and info[0][1] == 50 and info[0][2] == 40, (
        "a population divergence must be visible in the table")


def test_too_few_shared_pieces_refuses(monkeypatch, tmp_path):
    a = _rows(30, prefix="a")
    b = _rows(30, prefix="b")            # disjoint on purpose
    got = _clip_from(_arm(a), _arm(b), monkeypatch, tmp_path)
    assert "skip" in got and "0/0 IS NOT A PASS" in got["skip"]


def test_the_census_bias_warning_is_carried_through(monkeypatch, tmp_path):
    r = _rows(50, clipping=10)
    got = _clip_from(_arm(r), _arm(dict(r), biased=True), monkeypatch, tmp_path)
    assert got["biased"] is True
    rows, _ok = _table(got)
    assert any("BIASED" in r0[0] for r0 in rows), (
        "the census called its own sample biased and the table did not say so")


# ------------------------------------------------------------------- refusals

def test_unset_and_broken_preset_are_DIFFERENT(monkeypatch, tmp_path):
    """They rendered identically: "is not a file" contains "CLIP_PRESET", so a
    renamed file read exactly like an unconfigured machine."""
    monkeypatch.delenv("CBBE2UBE_CLIP_PRESET", raising=False)
    unset = acc.clip("a", "b")
    monkeypatch.setenv("CBBE2UBE_CLIP_PRESET", str(tmp_path / "gone.xml"))
    missing = acc.clip("a", "b")
    assert "CLIP_PRESET_UNSET" in unset["skip"]
    assert "CLIP_PRESET_MISSING" in missing["skip"]
    tags = {_table(x)[0][-1][2] for x in (unset, missing)}
    a_tag = [r[2] for r in _table(unset)[0] if "clip >" in r[0]][0]
    b_tag = [r[2] for r in _table(missing)[0] if "clip >" in r[0]][0]
    assert a_tag == "no preset" and b_tag == "preset missing", (a_tag, b_tag)
    assert a_tag != b_tag, "two different operator errors still render the same"


def test_an_asymmetric_refusal_skips_BOTH_and_names_the_arm(monkeypatch, tmp_path):
    r = _rows(50, clipping=10)
    got = _clip_from(_arm(r), {"skip": "no UBE template body"}, monkeypatch,
                     tmp_path)
    assert "skip" in got and "candidate" in got["skip"]


# ----------------------------------------------------------------- the table

def _table(cl):
    c = {"ctrl": {}, "cand": {}}
    for k in ("folds", "inverted", "tri_oob", "bodytri_on_body",
              "untagged_cloth", "pair_mismatch", "tri_names_partial",
              "tri_names_dead", "tri_names_scored"):
        c["ctrl"][k] = c["cand"][k] = 0
    d = (0, 0.0, 0, 0, 0.0, [])
    g = {"skip": {}, "w0_bust_skipped": True}
    tp = {"tip_skipped": True, "tip_p50_control": None,
          "w0_tip_skipped": True}
    zw = {"newly_emptied": 0, "rescued": 0, "both_empty": 0}
    fo = {"follow_worst_delta": None, "follow_note": "SKIPPED"}
    return acc.verdict_table(c, d, g, tp, zw, fo, False, False, None, cl)


def _cl(ctrl, cand, common=92):
    base = {"morph_p50": 0.0, "morph_p90": 0.0, "bind_p90": 0.0}
    return {"preset": "p.xml", "every": "1", "common": common,
            "scored_ctrl": common, "scored_cand": common, "biased": False,
            "ctrl": dict(base, **ctrl), "cand": dict(base, **cand)}


# ------------------------------------------------------- magnitude vs counts

def test_the_counts_ALONE_cannot_see_a_piece_clipping_LESS(monkeypatch, tmp_path):
    """THE DEFECT THE MAGNITUDE ROWS EXIST FOR. Every piece halves its clip and
    none crosses a threshold: prevalence is identical, severity is halved.
    Measured for real -- `#layer-order-last` cut the reported piece 33% and the
    gate's count rows read 46/46, 34/34, 23/23."""
    ctrl = {"a/%d_1.nif" % i: {"morph": 8.0, "bind": 4.0} for i in range(30)}
    cand = {k: {"morph": 4.0, "bind": 2.0} for k in ctrl}
    got = _clip_from(_arm(ctrl), _arm(cand), monkeypatch, tmp_path)
    assert got["ctrl"]["morph05"] == got["cand"]["morph05"] == 30, (
        "the COUNT must be blind here -- that is the premise")
    assert got["ctrl"]["morph10"] == got["cand"]["morph10"] == 30
    # ... and the magnitude must not be.
    assert got["ctrl"]["morph_p50"] == 8.0 and got["cand"]["morph_p50"] == 4.0
    assert got["ctrl"]["bind_p90"] == 4.0 and got["cand"]["bind_p90"] == 2.0


def test_a_worse_magnitude_FAILS_even_when_every_count_is_flat():
    cl = _cl({"bind": 23, "morph05": 46, "morph10": 34,
              "morph_p50": 1.0, "morph_p90": 5.0, "bind_p90": 2.0},
             {"bind": 23, "morph05": 46, "morph10": 34,
              "morph_p50": 2.0, "morph_p90": 9.0, "bind_p90": 4.0})
    rows, ok = _table(cl)
    v = {r[0]: r[3] for r in rows if "clip p" in r[0]}
    assert set(v.values()) == {"FAIL"}, v
    assert {r[3] for r in rows if "clip >" in r[0]} == {"ok"}, (
        "the counts should still read ok -- that is what makes the magnitude "
        "rows load-bearing")
    assert ok is False


def test_a_better_magnitude_passes_and_is_reported():
    cl = _cl({"bind": 23, "morph05": 46, "morph10": 34,
              "morph_p50": 1.0, "morph_p90": 5.0, "bind_p90": 2.0},
             {"bind": 23, "morph05": 46, "morph10": 34,
              "morph_p50": 0.6, "morph_p90": 3.2, "bind_p90": 1.1})
    rows, _ok = _table(cl)
    assert {r[3] for r in rows if "clip p" in r[0]} == {"ok"}
    assert len([r for r in rows if "clip p" in r[0]]) == 3


def test_the_percentiles_come_from_the_SHARED_pieces_only(monkeypatch, tmp_path):
    """A candidate must not improve its percentile by dropping bad pieces from
    its own census -- the same defect the counts were fixed for."""
    # NOTE the sizes: clip() refuses below 20 SHARED pieces, so the candidate
    # has to keep at least that many or this exercises the refusal instead.
    ctrl = {"a/%02d_1.nif" % i: {"morph": float(i), "bind": 0.0}
            for i in range(40)}
    cand = {k: dict(v) for k, v in sorted(ctrl.items())[:30]}  # lost the worst 10
    got = _clip_from(_arm(ctrl), _arm(cand), monkeypatch, tmp_path)
    assert "skip" not in got, got.get("skip")
    assert got["common"] == 30
    # scored over the same 12 pieces, so both arms report the same percentile
    assert got["ctrl"]["morph_p90"] == got["cand"]["morph_p90"]


def test_one_extra_clipping_piece_FAILS(monkeypatch):
    """THE TOLERANCE DEFECT. As a share this read ok once the population passed
    ~200 because TOL=0.005 is applied to non-integer floats. As a count over a
    fixed denominator the comparison is exact."""
    cl = _cl({"bind": 23, "morph05": 46, "morph10": 34},
             {"bind": 23, "morph05": 47, "morph10": 34}, common=768)
    rows, ok = _table(cl)
    v = {r[0]: r[3] for r in rows if "clip >" in r[0]}
    assert v["morph clip > 0.05% (pieces)"] == "FAIL", v
    assert ok is False


def test_a_better_candidate_passes():
    cl = _cl({"bind": 23, "morph05": 46, "morph10": 34},
             {"bind": 22, "morph05": 45, "morph10": 32})
    rows, _ok = _table(cl)
    assert {r[3] for r in rows if "clip >" in r[0]} == {"ok"}


def test_the_rows_never_vanish_and_never_read_as_ok_when_skipped():
    for cl in (None, {"skip": "SKIPPED: CLIP_PRESET_UNSET -- set it"}):
        rows, _ok = _table(cl)
        clip_rows = [r for r in rows if "clip >" in r[0]]
        assert len(clip_rows) == 3
        assert all(r[3] == "SKIPPED" for r in clip_rows)


def test_the_skip_cell_stays_short():
    long = ("SKIPPED: control census -- only 19 scored, floor is 40 -- this "
            "census cannot distinguish 'nothing is wrong' from 'I measured "
            "nothing'")
    rows, _ok = _table({"skip": long})
    assert {r[2] for r in rows if "clip >" in r[0]} == {"census floor"}
    assert all(len(str(r[2])) < 20 for r in rows if "clip >" in r[0])


def test_the_full_reason_is_printed_not_dropped():
    src = (REPO / "scripts" / "analysis" / "acceptance.py").read_text(
        encoding="utf-8")
    assert 'elif cl and "skip" in cl:' in src
    assert 'print("  morph clip: " + cl["skip"])' in src


def test_the_docstring_does_not_call_all_three_rows_morphed():
    """One of the three is a BIND-pose number and the header said otherwise --
    the lying-comment class this project treats as expensive."""
    doc = acc.__doc__ or ""
    assert "THE ONLY ROWS HERE TAKEN UNDER A BODY MORPH" not in doc
    assert "bind  clip > 0.05% (pieces)" in doc
    for token in ("CBBE2UBE_CLIP_PRESET", "--no-clip", "scored in BOTH",
                  "morph clip p50 / p90", "PREVALENCE", "MAGNITUDE"):
        assert token in doc, "the header stopped naming %r" % token


def test_no_clip_does_not_leak_into_the_positionals():
    assert acc.positionals(["ctrl", "cand", "--no-clip"]) == ["ctrl", "cand"]
