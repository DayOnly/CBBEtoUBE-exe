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

"""The three promoted harnesses behind the consolidation plan:
`acceptance` (the gate), `parity_convert` (the arm), `flag_surface` (the census).

What is pinned is the part that has produced a wrong verdict before, not the
arithmetic: an unparsed scorer row must FAIL rather than pass, an empty
population must exit 2, a corrupt arm must be refused, the arm harness must
scrub the shell and refuse an unlabelled default-recipe run, and none of the
three may carry a machine path or a mod name into tracked content.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analysis import acceptance as acc  # noqa: E402
from scripts.analysis import parity_convert as pc  # noqa: E402
from scripts.analysis import flag_surface as fs  # noqa: E402

_SRCS = {m.__name__: Path(m.__file__).read_text(encoding="utf-8")
         for m in (acc, pc, fs)}


# ------------------------------------------------------------- the gate table

def _base():
    c = {"ctrl": {"folds": 100.0, "inverted": 10.0, "tri_oob": 0.0,
                  "bodytri_on_body": 50.0, "untagged_cloth": 0.0,
                  "pair_mismatch": 0.0,
                  "tri_names_dead": 35.0, "tri_names_partial": 12.0,
                  "tri_names_scored": 2947.0}}
    c["cand"] = dict(c["ctrl"])
    g = {"gap_swap_control": 0.5, "gap_swap_candidate": 0.5,
         "gap_copy_control": 0.2, "gap_copy_candidate": 0.2,
         "pen_swap_control": 3.0, "pen_swap_candidate": 3.0,
         "pen_copy_control": 0.0, "pen_copy_candidate": 0.0}
    tp = {"tip_p50_control": 1.2, "tip_p50_candidate": 1.2,
          "tip_p05_control": 0.4, "tip_p05_candidate": 0.4, "tighter": 0.0}
    zw = {"newly_emptied": 0.0, "rescued": 0.0, "both_empty": 0.0}
    fo = {"follow_worst_delta": 0.0, "follow_note": ""}
    d = (0, 0.0, 0, 100, 0.0, [])
    return c, d, g, tp, zw, fo


def test_identical_arms_pass():
    c, d, g, tp, zw, fo = _base()
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False,
                                 st=_st_identical(), cl=_cl_identical())
    assert ok
    assert all(v in ("ok", "info") for _n, _a, _b, v in rows)


def test_an_unparsed_scorer_row_fails_the_gate():
    """The regex missing a line must never read as a pass."""
    c, d, g, tp, zw, fo = _base()
    tp["tip_p05_candidate"] = None
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False)
    assert not ok
    assert ("tip clearance p05", 0.4, None, "UNPARSED") in rows


def test_one_newly_emptied_bone_fails():
    c, d, g, tp, zw, fo = _base()
    zw["newly_emptied"] = 1.0
    _rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False)
    assert not ok


def test_more_folds_fails_and_fewer_passes():
    c, d, g, tp, zw, fo = _base()
    c["cand"]["folds"] = 101.0
    assert not acc.verdict_table(c, d, g, tp, zw, fo, False)[1]
    c["cand"]["folds"] = 99.0
    assert acc.verdict_table(c, d, g, tp, zw, fo, False)[1]


def test_closing_the_bust_gap_to_the_author_PASSES():
    """The row judges AUTHOR FIDELITY, and `gap = ours - author` is SIGNED.

    THIS TEST USED TO ASSERT THE OPPOSITE -- that a smaller gap FAILS -- which
    is what the `candidate >= control` rule did, and it is backwards on the
    project's own goal of restoring the authored distance. The old rule scored
    a real +0.498 -> +0.548 widening as ok.
    """
    c, d, g, tp, zw, fo = _base()
    g["gap_swap_candidate"] = 0.5 - 0.02           # CLOSER to the author
    assert acc.verdict_table(c, d, g, tp, zw, fo, False)[1]


def test_widening_the_bust_gap_from_the_author_FAILS():
    c, d, g, tp, zw, fo = _base()
    g["gap_swap_candidate"] = 0.5 + 0.02           # FURTHER from the author
    assert not acc.verdict_table(c, d, g, tp, zw, fo, False)[1]


def test_the_bust_gap_row_keeps_its_noise_tolerance_both_ways():
    """A float row must not fail on 0.005u of float noise in EITHER direction
    -- that tolerance is what makes a refactor arm reproducible."""
    c, d, g, tp, zw, fo = _base()
    for delta in (+0.004, -0.004):
        g["gap_swap_candidate"] = 0.5 + delta
        assert acc.verdict_table(c, d, g, tp, zw, fo, False)[1], delta


def test_crossing_past_the_author_is_judged_on_magnitude_not_sign():
    """|gap| is the rule, so overshooting to the OTHER side of the author by
    more than the control's distance fails, the same as drifting further out.
    Overshoot that actually penetrates is caught by the pen and tip rows."""
    c, d, g, tp, zw, fo = _base()
    g["gap_swap_candidate"] = -0.4                 # crossed, but CLOSER
    assert acc.verdict_table(c, d, g, tp, zw, fo, False)[1]
    g["gap_swap_candidate"] = -0.6                 # crossed, and FURTHER
    assert not acc.verdict_table(c, d, g, tp, zw, fo, False)[1]


def test_the_signed_move_stays_visible_beside_the_judged_magnitude():
    """|gap| alone cannot tell "closed toward the author" from "crossed past
    them", so the raw signed pair is reported as its own info row."""
    c, d, g, tp, zw, fo = _base()
    g["gap_swap_candidate"] = -0.4
    rows, _ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    signed = [r for r in rows if "signed move" in str(r[0])]
    assert len(signed) == 2, "one per path"
    swap = next(r for r in signed if "body-swap" in r[0])
    assert swap[1] == 0.5 and swap[2] == -0.4 and swap[3] == "info"


def test_a_weights_only_claim_is_held_to_zero_verts_moved():
    c, d, g, tp, zw, fo = _base()
    d = (3, 0.5, 10, 100, 0.1, [(10, "x.nif")])
    assert not acc.verdict_table(c, d, g, tp, zw, fo, weights_only=True)[1]
    assert acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False)[1], (
        "without the claim, moved verts are reported, not gated")


def test_posed_follow_is_a_named_skip_not_a_pass_when_unmeasured():
    c, d, g, tp, zw, fo = _base()
    fo = {"follow_worst_delta": None, "follow_note": "SKIPPED"}
    rows, _ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    assert ("posed follow", "-", "-", "SKIPPED") in rows


def test_an_empty_population_exits_2(tmp_path):
    (tmp_path / "a" / "meshes").mkdir(parents=True)
    (tmp_path / "b" / "meshes").mkdir(parents=True)
    assert acc.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 2


def test_a_tiny_nif_is_a_refused_arm(tmp_path):
    """The disk-full trap: a 1-byte NIF means the arm never finished."""
    for arm in ("a", "b"):
        d = tmp_path / arm / "meshes" / "!UBE" / "x"
        d.mkdir(parents=True)
        (d / "piece_1.nif").write_bytes(b"\0")
    assert acc.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 3


# ------------------------------------------------------ the repeat control

def _arm(root, files):
    """A synthetic arm, {path under meshes/: bytes}. Nothing here is a real NIF:
    every test below stubs the two steps that would open one."""
    for rel, data in files.items():
        p = root / "meshes" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return str(root)


_PIECES = {"!UBE/x/a_1.nif": b"A" * 2048, "!UBE/x/b_1.nif": b"B" * 2048,
           "!UBE/x/c_1.nif": b"C" * 2048}


def _three_arms(tmp_path, repeat_changes=None):
    ctrl = _arm(tmp_path / "ctrl", _PIECES)
    cand = _arm(tmp_path / "cand", _PIECES)
    repeat = _arm(tmp_path / "repeat", dict(_PIECES, **(repeat_changes or {})))
    return ctrl, cand, repeat


def _stub_scorers(monkeypatch):
    """Record every arm a scorer is handed, and score them as identical."""
    seen = []
    c, d, g, tp, zw, _fo = _base()

    def rec(value):
        def scorer(*arms, **_k):
            seen.extend(a for a in arms if isinstance(a, str))
            return value
        return scorer

    def delta(ctrl, cand, common):
        seen.extend([ctrl, cand])
        return d

    monkeypatch.setattr(acc, "_whole", lambda arm: [])
    monkeypatch.setattr(acc, "delta", delta)
    monkeypatch.setattr(acc, "census", rec(c["ctrl"]))
    monkeypatch.setattr(acc, "bust_gap", rec(g))
    monkeypatch.setattr(acc, "tip", rec(tp))
    monkeypatch.setattr(acc, "zero_weight", rec(zw))
    monkeypatch.setattr(acc, "stretch", rec(_st_identical()))
    monkeypatch.setattr(acc, "clip", rec(_cl_identical()))
    monkeypatch.setattr(acc, "_xml_race_at_risk", lambda arm: "0")
    return seen


def test_the_repeat_flag_takes_its_value_off_the_positionals():
    assert acc.positionals(["c", "d", "--repeat", "r", "--allow-noise"]) == ["c", "d"]


def test_noise_is_every_nif_the_two_control_arms_disagree_on(tmp_path):
    ctrl, _cand, repeat = _three_arms(tmp_path, {"!UBE/x/b_1.nif": b"b" * 2048,
                                                 "!UBE/x/d_1.nif": b"D" * 2048})
    assert acc.noise_set(ctrl, repeat) == ["!UBE/x/b_1.nif", "!UBE/x/d_1.nif"]


def test_a_filtered_arm_drops_only_the_noise(tmp_path):
    ctrl = _arm(tmp_path / "ctrl", _PIECES)
    (tmp_path / "ctrl" / "meshes" / "!UBE" / "x" / "b.xml").write_bytes(b"<xml/>")
    out = acc.filtered_arm(ctrl, ["!UBE/x/b_1.nif"], str(tmp_path / "f"))
    kept = acc._nifs(out)
    assert sorted(kept) == ["!UBE/x/a_1.nif", "!UBE/x/c_1.nif"]
    with open(kept["!UBE/x/a_1.nif"], "rb") as fh:
        assert fh.read() == _PIECES["!UBE/x/a_1.nif"]
    assert (tmp_path / "f" / "meshes" / "!UBE" / "x" / "b.xml").is_file()


def test_a_repeat_that_is_not_exact_names_the_noise_and_refuses(
        tmp_path, monkeypatch, capsys):
    """#repeat-control. Two arms of identical code differed on a garment's NIFs
    because a physics-XML lookup read a tree still being written. A gate that
    scores on top of that can hand the flag under test a coin flip."""
    ctrl, cand, repeat = _three_arms(tmp_path, {"!UBE/x/b_1.nif": b"b" * 2048})
    seen = _stub_scorers(monkeypatch)
    monkeypatch.setattr(acc, "_whole", lambda arm: pytest.fail("scored a noisy gate"))
    assert acc.main([ctrl, cand, "--repeat", repeat]) == 4
    out = capsys.readouterr().out
    assert "noise: !UBE/x/b_1.nif" in out and "NOT EXACT" in out
    assert seen == []


def test_with_allow_noise_the_noise_leaves_every_row(tmp_path, monkeypatch, capsys):
    """THE EXCLUSION: every arm a scorer is handed must lack the noise NIF."""
    ctrl, cand, repeat = _three_arms(tmp_path, {"!UBE/x/b_1.nif": b"b" * 2048})
    seen = _stub_scorers(monkeypatch)
    assert acc.main([ctrl, cand, "--repeat", repeat, "--allow-noise"]) == 0
    assert seen, "no scorer ran -- this check would be vacuous"
    for arm in seen:
        assert sorted(acc._nifs(arm)) == ["!UBE/x/a_1.nif", "!UBE/x/c_1.nif"], arm
    assert "excluded from every row below: 1 NIF(s)" in capsys.readouterr().out


def test_an_exact_repeat_scores_the_same_arms_as_two_arms(tmp_path, monkeypatch, capsys):
    ctrl, cand, repeat = _three_arms(tmp_path)
    seen = _stub_scorers(monkeypatch)
    assert acc.main([ctrl, cand, "--repeat", repeat]) == 0
    assert set(seen) == {ctrl, cand}
    assert "differing  : 0" in capsys.readouterr().out


def test_the_race_info_row_counts_at_risk_pieces(monkeypatch):
    from scripts.analysis import hdt_xml_resolution_census as hx
    monkeypatch.setattr(hx, "scan", lambda arm, limit: (
        [{"cls": "AT RISK"}, {"cls": "pointer"}, {"cls": "AT RISK"}], {}))
    assert acc._xml_race_at_risk("arm") == "2"


# --------------------------------------------------------------- the arm

def test_the_arm_scrubs_the_shell_and_echoes_its_override(tmp_path, capsys):
    """A stale export from an earlier experiment must not leak into an arm --
    that trap has produced four arms that were the same arm."""
    js = tmp_path / "s.json"
    js.write_text("{}", encoding="utf-8")
    base = {"PATH": "x", "CBBE2UBE_STALE_EXPORT": "1"}
    env = pc.build_env(js, None, ["CBBE2UBE_NO_THING=1"], base=base)
    assert "CBBE2UBE_STALE_EXPORT" not in env
    assert env["CBBE2UBE_NO_THING"] == "1"
    assert env["PYTHONHASHSEED"] == "1"
    assert "OVERRIDE : CBBE2UBE_NO_THING=1" in capsys.readouterr().out


def test_the_arm_refuses_an_unlabelled_default_recipe(tmp_path, monkeypatch):
    """No settings json -> no arm. Measuring code defaults is a DIFFERENT arm
    from the live recipe and must be asked for by name."""
    monkeypatch.delenv("CBBE2UBE_CONFIG", raising=False)
    monkeypatch.setattr(pc.gs, "config_path", lambda: tmp_path / "absent.json")
    with pytest.raises(SystemExit):
        pc.main([str(tmp_path / "out"), "some-mod"])


def test_a_malformed_override_is_rejected(tmp_path):
    js = tmp_path / "s.json"
    js.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        pc.build_env(js, None, ["NOT_AN_ASSIGNMENT"], base={})


def test_the_arm_forwards_workers_to_the_converter(tmp_path, monkeypatch):
    """`--workers 1` is the canonical WEIGHT arm (a pair shares its XML and
    tri; see #pair-unit-dispatch). The flag must reach the CLI and must not be
    read as an override."""
    js = tmp_path / "s.json"
    js.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(js))
    monkeypatch.setenv("CBBE2UBE_MO2_INI", str(tmp_path / "ModOrganizer.ini"))
    monkeypatch.setattr(pc, "build_env", lambda *a, **k: {"PYTHONHASHSEED": "1"})
    seen = []
    monkeypatch.setattr(pc.subprocess, "call",
                        lambda cmd, **k: seen.append(list(cmd)) or 0)
    assert pc.main([str(tmp_path / "out"), "some-mod", "--workers", "1"]) == 0
    assert seen[0][-2:] == ["--workers", "1"]
    assert pc.main([str(tmp_path / "out"), "some-mod"]) == 0
    assert "--workers" not in seen[1]


# ------------------------------------------------------------- the census

def test_flag_binding_regex_reads_both_polarities():
    src = ('A_ON = not _flag("CBBE2UBE_NO_A", False)\n'
           'B_OFF = _flag("CBBE2UBE_B", False)\n'
           'C_MULTI = (\n    not _flag("CBBE2UBE_NO_C", False))\n'
           '_D_PRIVATE = _flag("CBBE2UBE_D", True)\n')
    rows = fs.declared(src)
    assert ("A_ON", "CBBE2UBE_NO_A", True, False) in rows
    assert ("B_OFF", "CBBE2UBE_B", False, False) in rows
    assert ("C_MULTI", "CBBE2UBE_NO_C", True, False) in rows, "multi-line binding"
    assert ("_D_PRIVATE", "CBBE2UBE_D", False, True) in rows, (
        "a private-prefixed flag -- the first census missed all 16 of them")


def test_the_census_sees_the_real_surface():
    """Floor, not an exact count -- the surface moves. The 2026-09-06 count
    was 138 bindings (16 of them private-prefixed); a census reading fewer than
    the public ones alone has lost the regex."""
    src = (Path(fs._REPO) / "src" / "nif_convert.py").read_text(encoding="utf-8")
    rows = fs.declared(src)
    assert len(rows) >= 120
    assert any(n.startswith("_") for n, _e, _k, _d in rows)


# --------------------------------------------------------------- hygiene

@pytest.mark.parametrize("name", sorted(_SRCS))
def test_no_machine_paths_or_mod_names_in_tracked_harness(name):
    body = "\n".join(l for l in _SRCS[name].splitlines()
                     if not l.lstrip().startswith("#"))
    assert not re.search(r"[A-Za-z]:\\\\|[A-Za-z]:/", body), name
    assert "Modlists" not in body and "Users\\" not in body, name


# ------------------------------------------- the DIRECTION of a follow change

def _fo(worst, net, better=0, worse=0):
    return {"follow_worst_delta": worst, "follow_net_ideal": net,
            "follow_better": better, "follow_worse": worse, "follow_note": ""}


def test_default_mode_fails_any_follow_change_regardless_of_direction():
    """The no-change guard, unchanged. A refactor claiming to move nothing must
    still fail the moment follow moves at all."""
    c, d, g, tp, zw, _ = _base()
    rows, ok = acc.verdict_table(c, d, g, tp, zw, _fo(0.063, -0.9), False)
    assert not ok
    assert ("posed follow, worst median delta", 0.0, 0.063, "FAIL") in rows


def test_geometry_mode_judges_the_signed_net_not_the_bare_change():
    """#src-normal-fix measured worst 0.063 / net -0.027: direction-blind that
    is a flat FAIL, signed it is a small improvement. A geometry item must be
    judged on which WAY follow moved."""
    c, d, g, tp, zw, _ = _base()
    rows, ok = acc.verdict_table(c, d, g, tp, zw, _fo(0.063, -0.027, 79, 89),
                                 False, geometry=True)
    assert ok
    assert ("posed follow, worst median delta", 0.0, 0.063, "info") in rows
    assert ("posed follow, net distance from ideal", 0.0, -0.027, "ok") in rows


def test_geometry_mode_still_fails_a_real_follow_regression():
    """The mode must not be a way to wave a change through: a net move AWAY
    from ideal follow fails, however small the worst single cell."""
    c, d, g, tp, zw, _ = _base()
    rows, ok = acc.verdict_table(c, d, g, tp, zw, _fo(0.002, +0.31), False,
                                 geometry=True)
    assert not ok
    assert ("posed follow, net distance from ideal", 0.0, 0.31, "FAIL") in rows


def test_geometry_mode_does_not_loosen_any_other_row():
    """It changes ONE row's rule. Everything else -- folds, penetration, tip,
    newly-emptied -- is judged exactly as before."""
    c, d, g, tp, zw, _ = _base()
    c["cand"]["folds"] = 101.0
    _rows, ok = acc.verdict_table(c, d, g, tp, zw, _fo(0.0, 0.0), False,
                                  geometry=True)
    assert not ok


# ----------------------------------------------- the gate reads a REAL census

# Verbatim SUMMARY lines from a real `pack_census` run (the 2026-09-07 pack,
# the one the current build wrote). Only
# the summary rows are kept -- the per-file detail lines carry mod and armour
# names, which must not enter tracked content.
#
# WHY THIS FIXTURE EXISTS: the gate reads the census by REGEX over its printed
# output, so a reworded census row silently stops matching. `add()` turns an
# unmatched row into UNPARSED + FAIL rather than a false pass, which is the
# right failure -- but it surfaces at VERDICT time, on an arm that cost half an
# hour to produce. Pinning the regexes against real output moves that to the
# suite, where it costs nothing.
_REAL_CENSUS = """
==============================================================================
PACK CENSUS
==============================================================================
  NIFs read (no 1stperson, no _bsa_staging) : 2960
  failed to load                            : 0
  _0/_1 pairs                               : 1220

SURFACE
  vertices judged                           : 33437054
  FOLDED                                    : 244550
  INVERTED                                  : 21554

BODYTRI  (the slider fix)
  body-slot NIFs                            : 591
  BODYTRI present on the body               : 591/591   OK
  NIFs with an untagged cloth shape         : 0   OK

GENERATED COLLISION PROXY  (the physics fix)
  pieces still shipping a SkirtCol          : 4
  of those, tagged ground/body              : 0   OK

WEIGHT-PAIR PARITY
  pairs with a vert-count mismatch          : 1   <== should be 0
  pairs >1 DAY apart AND differing           : 0   OK
    excluded: >1 day apart but BYTE-IDENTICAL: 1   (current copies)
  TRI offsets out of bounds                 : 0   OK
  NIFs with no BODYTRI (cannot morph, not at risk) : 13
  BODYTRI pointing at a MISSING tri         : 0   OK

TRI / NIF SHAPE-NAME AGREEMENT  (a tri whose names the NIF lacks is
  NIFs scored (their own BODYTRI resolved)  : 2947
  every tri shape named in the NIF          : 2900
  SOME tri shapes the NIF does not have     : 12   <== dead sliders
  NO tri shape the NIF has                  : 35   <== whole tri is dead
"""


def test_every_gate_regex_names_a_line_pack_census_actually_prints():
    """Drift-proof companion to the fixture above.

    The fixture can only pin the formats someone remembered to paste into it.
    This reads the LITERAL text out of each gate regex and requires it to
    appear in `pack_census`'s own source, so renaming a census row breaks the
    suite at the rename rather than at the next verdict.
    """
    from scripts.analysis import pack_census as _pcen
    census_src = Path(_pcen.__file__).read_text(encoding="utf-8")
    checked = 0
    for pat in re.findall(r'r"([^"]+)"', _census_body()):
        # the ROW LABEL: everything the pattern requires before the colon
        label = pat.split(r"\s*:")[0]
        label = label.lstrip("^").replace(r"\s*", "").replace("\\", "")
        assert label, pat
        assert label in census_src, (
            "the gate looks for a row labelled %r but pack_census never prints "
            "it -- a census row was renamed and the gate reads UNPARSED" % label)
        checked += 1
    assert checked >= 9, "expected every census row's pattern, got %d" % checked

    # MUTATION CONTROL. A check that cannot fail is not a check: rename one row
    # out of the census source and the same loop must reject it.
    mutated = census_src.replace("NO tri shape the NIF has", "NO tri shapes")
    assert mutated != census_src, "the mutation did not apply"
    missed = [p.split(r"\s*:")[0].lstrip("^").replace(r"\s*", "").replace("\\", "")
              for p in re.findall(r'r"([^"]+)"', _census_body())]
    assert any(lbl not in mutated for lbl in missed), (
        "renaming a census row did NOT trip the check -- it is inert")


def _census_body() -> str:
    """The source of `acceptance.census`, where every census regex lives."""
    src = _SRCS[acc.__name__]
    return src.split("def census(")[1].split("\ndef ")[0]


def test_every_census_regex_matches_real_output_exactly_once():
    """Each pattern the gate uses must hit, and hit ONE line.

    A pattern that matches nothing gives UNPARSED; one that matches two lines
    silently reads whichever comes first, which is how a gate starts scoring
    the wrong number without failing.
    """
    body = _census_body()
    pats = re.findall(r'r"([^"]+)"', body)
    assert len(pats) >= 9, "expected every census row's pattern, got %d" % len(pats)
    for p in pats:
        hits = re.findall(p, _REAL_CENSUS, re.M)
        assert len(hits) == 1, "%r matched %d lines, want exactly 1" % (p, len(hits))


def test_the_gate_reads_the_dead_slider_rows_off_real_output():
    """The numbers, not just the match -- a regex can hit the right line and
    capture the wrong group."""
    body = _census_body()

    def one(pat):
        m = re.search(pat, _REAL_CENSUS, re.M)
        return float(m.group(1)) if m else None

    pats = dict(re.findall(r'"(\w+)":\s*_num\(\s*\n?\s*t,\s*r"([^"]+)"', body))
    assert one(pats["tri_names_dead"]) == 35.0
    assert one(pats["tri_names_partial"]) == 12.0
    assert one(pats["tri_names_scored"]) == 2947.0
    assert one(pats["folds"]) == 244550.0
    assert one(pats["bodytri_on_body"]) == 591.0


def test_dead_sliders_are_a_JUDGED_row_not_just_reported():
    """The class this row exists for shipped through every verdict the gate
    had ever given, because the gate could not see it."""
    c, d, g, tp, zw, fo = _base()
    c["cand"]["tri_names_dead"] = 36.0
    assert not acc.verdict_table(c, d, g, tp, zw, fo, False)[1]
    c["cand"]["tri_names_dead"] = 34.0
    assert acc.verdict_table(c, d, g, tp, zw, fo, False)[1]
    c["cand"]["tri_names_partial"] = 13.0
    assert not acc.verdict_table(c, d, g, tp, zw, fo, False)[1]


# --------------------------------------- "not measured" is not "failed"

def test_a_path_the_scorer_refused_reads_SKIPPED_not_FAIL():
    """`bust_gap_score` declines a path with fewer than 5 shapes and prints
    "only N shapes -- not reported" instead of a table.

    Before this, the gate saw None for that path, called the row UNPARSED and
    FAILED the arm -- a false FAIL on a population that was simply too small.
    That is the same distinction `require_population` draws with exit 3: "0/0
    is not a pass" cuts BOTH ways.
    """
    c, d, g, tp, zw, fo = _base()
    for k in ("gap_copy_control", "gap_copy_candidate",
              "pen_copy_control", "pen_copy_candidate"):
        g.pop(k)
    g["skipped_copy"] = 3
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, False,
                                 st=_st_identical(), cl=_cl_identical())
    assert ok, "a path that could not be measured must not fail the arm"
    skipped = [r for r in rows if r[3] == "SKIPPED"]
    assert len(skipped) == 2, "the gap AND the penetration row for that path"
    assert all("copy path" in r[0] for r in skipped)
    assert all("3 shapes" in str(r[2]) for r in skipped)


def test_a_skipped_path_never_reads_as_ok():
    """It must stay VISIBLE -- a silent pass on an unjudged path is exactly
    how a regression ships."""
    c, d, g, tp, zw, fo = _base()
    for k in ("gap_copy_control", "gap_copy_candidate",
              "pen_copy_control", "pen_copy_candidate"):
        g.pop(k)
    g["skipped_copy"] = 4
    rows, _ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    copy_rows = [r for r in rows if "copy path" in r[0] and r[3] != "info"]
    assert copy_rows and all(r[3] == "SKIPPED" for r in copy_rows)


def test_a_MISSING_row_with_no_skip_note_still_FAILS():
    """The mutation control on the change above: only an EXPLICIT refusal from
    the scorer earns SKIPPED. A row that vanished for any other reason -- a
    reworded table, a crashed scorer -- must still fail."""
    c, d, g, tp, zw, fo = _base()
    g.pop("gap_copy_candidate")
    _rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    assert not ok


def test_the_skip_note_is_read_off_the_scorers_real_wording():
    """Pinned against the exact line `bust_gap_score` prints, so rewording one
    side without the other is caught here rather than on an arm."""
    from scripts.analysis import bust_gap_score as bgs
    src = Path(bgs.__file__).read_text(encoding="utf-8")
    assert "only %d shapes -- not reported" in src, (
        "the scorer no longer prints the line the gate reads")
    for path, n in (("copy path", 3), ("body-swap", 4)):
        m = acc.SKIP_NOTE.match("  %s: only %d shapes -- not reported"
                                % (path, n))
        assert m, path
        assert m.group(1) == path and int(m.group(2)) == n
    # a real table row must NOT be mistaken for a skip note
    assert acc.SKIP_NOTE.match(
        "    control        bust p50  1.234   gap p50 +0.682   pen 12") is None


# ------------------------------- the tip row, and the sign that broke it

_TIP_TABLE = """
pieces covering the nipple in EVERY arm: 43

arm                     tip clearance p50      p05      min
control                            1.244    0.454   -0.123
candidate                          1.220    0.441    0.017

per piece, candidate minus control (negative = LESS room over the nipple):
  tighter 6   looser 3   flat 34   median delta +0.0031u
"""


def test_a_NEGATIVE_tip_parses_at_all():
    """THE BUG THIS PINS. `nipple_clearance` prints `arm p50 p05 min`, and a
    negative `min` is exactly the poke-through the row exists to catch -- but
    the pattern required an UNSIGNED number per column, so ONE piece with a
    tip inside the body made the whole line fail to match: p50 and p05 went
    missing,
    both rows read UNPARSED, and the gate failed the arm with a parse error
    INSTEAD OF the defect. It was blind precisely when it mattered.
    """
    m = re.search(acc._TIP_ROW % "control", _TIP_TABLE, re.M)
    assert m, "a negative minimum must not stop the row parsing"
    assert float(m.group(1)) == 1.244
    assert float(m.group(2)) == 0.454
    assert float(m.group(3)) == -0.123


def test_the_tip_pattern_still_reads_an_all_positive_row():
    m = re.search(acc._TIP_ROW % "candidate", _TIP_TABLE, re.M)
    assert m and float(m.group(3)) == 0.017


def test_the_tip_pattern_matches_the_scorers_real_format():
    """Pinned against `nipple_clearance`'s own header, so renaming a column
    breaks the suite rather than an arm."""
    from scripts.analysis import nipple_clearance as ncl
    src = Path(ncl.__file__).read_text(encoding="utf-8")
    assert "'tip clearance p50'" in src and "'p05'" in src and "'min'" in src
    assert "0/0 IS NOT A PASS" in src, (
        "the gate reads this exact string to tell 'unjudged' from 'failed'")


def test_the_worst_single_tip_is_reported_and_gated():
    """The percentiles can BOTH improve while one piece goes through the
    surface, so the extreme is judged in its own right."""
    c, d, g, tp, zw, fo = _base()
    tp["tip_min_control"] = 0.10
    tp["tip_min_candidate"] = 0.20
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    assert ok
    worst = next(r for r in rows if "worst single tip" in r[0])
    assert worst[3] == "info"

    tp["tip_min_candidate"] = -0.05          # one piece now pokes through
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    assert not ok, "a tip that went INSIDE the body must fail the arm"
    worst = next(r for r in rows if "worst single tip" in r[0])
    assert worst[3] == "FAIL"


def test_no_piece_covering_the_tip_is_SKIPPED_not_FAILED():
    c, d, g, tp, zw, fo = _base()
    for k in list(tp):
        tp.pop(k)
    tp["tip_skipped"] = True
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    assert ok, "an empty tip population must not fail the arm"
    sk = [r for r in rows if r[3] == "SKIPPED" and "tip" in r[0]]
    assert len(sk) == 3, "p50, p05 and the tighter count"


def test_a_missing_tip_row_with_no_refusal_still_FAILS():
    """Mutation control: only the scorer's EXPLICIT refusal earns SKIPPED."""
    c, d, g, tp, zw, fo = _base()
    tp["tip_p50_control"] = None
    _rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, False)
    assert not ok


def test_the_tip_pattern_is_checked_against_the_printers_own_layout():
    """Stronger than a hand-typed fixture: build the line from the SAME format
    spec `nipple_clearance` uses, so column widths cannot silently drift apart.

    All-negative is included deliberately -- p50 and p05 can go negative too on
    a bad arm, and an unsigned pattern would have lost the whole row.
    """
    from scripts.analysis import nipple_clearance as ncl
    src = Path(ncl.__file__).read_text(encoding="utf-8")
    assert "{lab:<22}" in src and ":>18.3f}" in src and ":>8.3f}" in src, (
        "the printer's column layout changed; rebuild this test from it")
    for lab, p50, p05, mn in (("control", 1.244, 0.454, -0.123),
                              ("candidate", 1.220, 0.441, 0.017),
                              ("control", -0.5, -1.25, -9.999)):
        line = "%-22s %18.3f %8.3f %8.3f" % (lab, p50, p05, mn)
        m = re.search(acc._TIP_ROW % lab, line, re.M)
        assert m, line
        assert (float(m.group(1)), float(m.group(2)), float(m.group(3))) \
            == (p50, p05, mn)


# ------------------------------- the follow row, and the sign that hid cells

def _follow_row_re():
    """The pattern `acceptance.follow` builds, rebuilt from its own pieces."""
    return re.compile(r"^\s*(\w[\w ]*?)\s{2,}(.+?)\s{2,}(\d+)\s+"
                      + acc._NUMCOL + r"\s+" + acc._NUMCOL + r"\s+")


def test_a_NEGATIVE_follow_cell_is_not_silently_dropped():
    """THE WORST OF THE THREE SIGN BUGS, because it failed SILENTLY.

    `follow_bands` computes `dot(garment_move, body_dir) / |body_move|` -- a
    dot product over a magnitude, so a garment travelling AGAINST the body
    gives a negative ratio. The gate's row pattern required an unsigned number,
    so such a line did not fail, it simply did not MATCH: the cell never
    entered the comparison, `scored` quietly shrank, and the gate reported a
    clean number over a population it had trimmed. The cells most likely to be
    negative are the pathological ones, so it was discarding its own worst
    evidence.
    """
    row = _follow_row_re()
    # `follow_bands` prints: f"  {band:<11}{pose:<16}{n:>6}{med:>9.3f}{p10:>8.3f}"
    line = "  %-11s%-16s%6d%9.3f%8.3f  " % ("butt", "run_fwd", 88, -0.123, -0.400)
    m = row.match(line)
    assert m, "a negative follow median must still parse"
    assert m.group(1).strip() == "butt"
    assert float(m.group(4)) == -0.123

    # the OLD pattern, kept here as the control: it must NOT match
    old = re.compile(r"^\s*(\w[\w ]*?)\s{2,}(.+?)\s{2,}(\d+)\s+([\d.]+)\s+([\d.]+)\s+")
    assert old.match(line) is None, (
        "if the old unsigned pattern matches this, the bug being pinned is "
        "not the bug that existed")


def test_positive_follow_cells_are_unaffected():
    """The fix must not change what the gate already read correctly."""
    row = _follow_row_re()
    for band, pose, n, med, p10 in (("bust", "T-pose", 412, 0.987, 0.850),
                                    ("thigh", "sit", 1234, 1.250, 1.100)):
        line = "  %-11s%-16s%6d%9.3f%8.3f  " % (band, pose, n, med, p10)
        m = row.match(line)
        assert m and float(m.group(4)) == med, line


def test_the_follow_pattern_matches_follow_bands_own_layout():
    from scripts.analysis import follow_bands as fb
    src = Path(fb.__file__).read_text(encoding="utf-8")
    assert "{band:<11}{pname:<16}{n:>6}{med:>9.3f}{p10:>8.3f}" in src, (
        "follow_bands' column layout changed; rebuild this test from it")
    assert "dot(garment_displacement, body_direction)" in src, (
        "the ratio is a DOT PRODUCT over a magnitude -- that is WHY it is "
        "signed; if that changed, re-derive whether the sign is still possible")


# ------------------------------------------- THE LAST-STAGE STRETCH ROW

# Verbatim from a real `stretched_edges` run over the two `_SRC_NORMAL_FIX`
# arms (2026-09-08, the 184-NIF acceptance population). The fixture is the
# point: a regex is only pinned against output someone actually produced.
_REAL_STRETCH = """\
  BSA fallback index: scanned 366 mesh archive(s) -> 60946 mesh path(s) available
  measured control                  598 shapes
  measured candidate                598 shapes
    not scored in control           90  body or physics-proxy shape (ours, not the author's)
    not scored in control           48  renders nothing (no texture)
    not scored in control            8  under 64 authored edges
    not scored in control            6  author vert count differs (retopologised)

==============================================================================
STRETCHED EDGES vs THE AUTHOR, LAST STAGE   (ratio > 1.50x)
==============================================================================
  shapes scored in EVERY arm                : 598
  distinct garments behind them             : 299

  arm              pooled   rate p50   rate p90   rate max    dev p50
  control           24141    0.1495%    2.0217%   45.2396%     0.0331
  candidate         23866    0.1589%    2.0468%   52.3323%     0.0289
                                                  shapes better / worse / same : 88 / 97 / 413
"""


def _stretch_of(text: str) -> dict:
    """`acceptance.stretch`'s parsing, without the subprocess."""
    out = {}
    for lab in ("control", "candidate"):
        m = re.search(acc._STRETCH_ROW % lab, text, re.M)
        if m:
            out[f"pooled_{lab}"] = float(m.group(1))
            out[f"rate_p50_{lab}"] = float(m.group(2))
            out[f"rate_p90_{lab}"] = float(m.group(3))
            out[f"rate_max_{lab}"] = float(m.group(4))
            out[f"dev_p50_{lab}"] = float(m.group(5))
    out["scored"] = acc._num(text, r"shapes scored in EVERY arm\s*:\s*(\d+)")
    out["garments"] = acc._num(text, r"distinct garments behind them\s*:\s*(\d+)")
    return out


def _cl_identical() -> dict:
    """A morphed-clip dict for two arms that measured the SAME thing.

    Without it the clip rows read SKIPPED (no preset configured in a test env),
    and the identical-arms assertions below would have to be loosened to accept
    SKIPPED -- which would stop them checking the new rows at all.
    """
    one = {"bind": 23, "morph05": 46, "morph10": 34,
           "morph_p50": 0.4, "morph_p90": 3.1, "bind_p90": 1.2}
    return {"preset": "p.xml", "every": "1", "common": 92,
            "scored_ctrl": 92, "scored_cand": 92, "biased": False,
            "ctrl": dict(one), "cand": dict(one)}


def _st_identical() -> dict:
    """A stretch dict for two arms that measured the SAME thing."""
    st = _stretch_of(_REAL_STRETCH)
    for k in ("pooled", "rate_p50", "rate_p90", "rate_max", "dev_p50"):
        st[k + "_candidate"] = st[k + "_control"]
    return st


def test_the_stretch_row_matches_real_output_exactly_once():
    """Once per arm. A pattern that hits two lines reads whichever comes
    first, which is how a gate starts scoring the wrong number silently."""
    for lab in ("control", "candidate"):
        hits = re.findall(acc._STRETCH_ROW % lab, _REAL_STRETCH, re.M)
        assert len(hits) == 1, "%s matched %d lines, want 1" % (lab, len(hits))


def test_the_stretch_row_captures_the_right_numbers():
    """A regex can hit the right line and capture the wrong group."""
    st = _stretch_of(_REAL_STRETCH)
    assert st["pooled_control"] == 24141.0
    assert st["rate_p50_control"] == 0.1495
    assert st["rate_p90_candidate"] == 2.0468
    assert st["dev_p50_candidate"] == 0.0289
    assert st["scored"] == 598.0
    assert st["garments"] == 299.0


def test_the_stretch_pattern_matches_the_printers_own_layout():
    """Drift proof: rebuild this test if the scorer's columns move."""
    from scripts.analysis import stretched_edges as _se
    src = Path(_se.__file__).read_text(encoding="utf-8")
    for lbl in ("shapes scored in EVERY arm", "distinct garments behind them"):
        assert lbl in src, (
            "the gate looks for %r but stretched_edges never prints it" % lbl)
    assert '"  %-12s %10d %10s %10s %10s %10.4f"' in src, (
        "the arm row's layout changed; rebuild _REAL_STRETCH from a real run")
    # MUTATION CONTROL -- a check that cannot fail is not a check.
    assert "shapes scored in EVERY arm" not in src.replace(
        "shapes scored in EVERY arm", "shapes scored")


def test_a_worse_stretch_rate_fails_the_gate():
    c, d, g, tp, zw, fo = _base()
    st = _stretch_of(_REAL_STRETCH)
    st["rate_p50_candidate"] = st["rate_p50_control"] + 0.5
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False, st=st)
    assert not ok
    assert any(n.startswith("stretch rate p50") and v == "FAIL"
               for n, _a, _b, v in rows)


def test_a_worse_weighted_deviation_fails_the_gate():
    """The count and the deviation are different claims, so both are judged."""
    c, d, g, tp, zw, fo = _base()
    st = _stretch_of(_REAL_STRETCH)
    st["rate_p50_candidate"] = st["rate_p50_control"]
    st["rate_p90_candidate"] = st["rate_p90_control"]
    st["dev_p50_candidate"] = st["dev_p50_control"] + 0.5
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False, st=st)
    assert not ok
    assert any(n.startswith("edge deviation p50") and v == "FAIL"
               for n, _a, _b, v in rows)


def test_a_POOLED_regression_alone_does_NOT_fail_the_gate():
    """THE TRAP THIS ROW EXISTS TO AVOID.

    Reading the pooled count on the `_SRC_NORMAL_FIX` ledger said 1031 -> 1327,
    "worse". Per shape it was 6 better, 6 worse, 22 unchanged, and ONE garment
    carried the whole delta -- at BOTH its body weights, so it was counted
    twice. The pooled number is a mesh-size ranking and is reported only.
    """
    c, d, g, tp, zw, fo = _base()
    st = _stretch_of(_REAL_STRETCH)
    for k in ("rate_p50", "rate_p90", "dev_p50"):
        st[k + "_candidate"] = st[k + "_control"]
    st["pooled_candidate"] = st["pooled_control"] * 10
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False, st=st)
    assert ok, "a pooled-only regression must not fail the gate"
    assert any("pooled stretched edges" in n and v == "info"
               for n, _a, _b, v in rows)


def test_an_unparsed_stretch_row_fails_the_gate():
    c, d, g, tp, zw, fo = _base()
    st = _stretch_of(_REAL_STRETCH)
    st["rate_p90_candidate"] = None
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False, st=st)
    assert not ok
    assert any(n.startswith("stretch rate p90") and v == "UNPARSED"
               for n, _a, _b, v in rows)


def test_an_unrun_stretch_scorer_reads_SKIPPED_never_ok():
    """"Not measured" is not "failed" -- and it is not a pass either. The row
    must stay visible so a gate that lost its stretch scorer says so."""
    c, d, g, tp, zw, fo = _base()
    rows, ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False)
    hit = [r for r in rows if r[0] == "stretched edges vs author"]
    assert hit and hit[0][3] == "SKIPPED"
    assert ok, "a skip is not a failure"


def test_an_empty_stretch_population_reads_SKIPPED_not_ok():
    c, d, g, tp, zw, fo = _base()
    rows, _ok = acc.verdict_table(c, d, g, tp, zw, fo, weights_only=False,
                                  st={"skipped": True})
    hit = [r for r in rows if r[0] == "stretched edges vs author"]
    assert hit and hit[0][3] == "SKIPPED"
    assert "no shape scored in both arms" in str(hit[0][2])


def test_the_gate_actually_calls_the_stretch_scorer():
    """A row wired into the table but never fed is a row that always SKIPs."""
    src = _SRCS[acc.__name__]
    assert "st = stretch(ctrl, cand)" in src
    assert "weights_only, geometry," in src and "st, cl)" in src


def test_the_gate_actually_calls_the_clip_scorer():
    """Same guard for the morphed-clip rows -- they are the only ones taken
    under a body preset, and a row wired in but never fed always SKIPs."""
    src = _SRCS[acc.__name__]
    assert "cl = None if no_clip else clip(ctrl, cand)" in src
    assert "st, cl)" in src
