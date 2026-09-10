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

"""`#clearance-term-audit` -- WHICH TERM SETS `req` ON THE BUST.

Five clearance knobs have been measured INERT on this population, and each time
the reason was worked out afterwards: the knob's term was never the argmax. This
telemetry answers that BEFORE an arm is spent. It moves no vertex, so the tests
that matter are: it ships OFF, it costs nothing when off, and its arithmetic
says what it claims.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from src import nif_convert_fitgeom as fg   # noqa: E402


def _stats(terms, in_bust, req, pre, cap, worst):
    return fg.clearance_term_stats(terms, in_bust, req, pre, cap, worst)


# ------------------------------------------------------------- ships OFF

def test_the_flag_ships_off_in_a_scrubbed_environment():
    """Resolved from CODE in a subprocess with every CBBE2UBE_* removed --
    reading the module in THIS process would inherit whatever a harness set."""
    src = (
        "import os,sys;"
        "[os.environ.pop(k) for k in list(os.environ) if k.startswith('CBBE2UBE_')];"
        "sys.path.insert(0, r'%s');"
        "from src import nif_convert_fitgeom as f;"
        "print(f.CLEARANCE_TERM_AUDIT)" % REPO_ROOT
    )
    out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                         text=True, cwd=str(REPO_ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", out.stdout


def test_the_pass_allocates_nothing_when_the_flag_is_off():
    """The OFF path must not copy `req` or build the term dict. Pinned on the
    SOURCE, because a telemetry array allocated per shape on every convert is a
    cost nobody asked for."""
    src = Path(fg.__file__).read_text(encoding="utf-8")
    assert "_terms = {} if CLEARANCE_TERM_AUDIT else None" in src
    assert "_req_prerelax = req.copy() if _terms is not None else None" in src
    # every capture site must be guarded
    for site in ('_terms["amp-ramp"]', '_terms["morph-diff"]',
                 '_terms["bust-floor"]'):
        i = src.index(site)
        assert "if _terms is not None:" in src[max(0, i - 400):i], site


# --------------------------------------------------------- the arithmetic

def test_the_argmax_names_the_term_that_actually_won():
    in_bust = np.array([True, True, True])
    terms = {
        "amp-ramp":   np.array([0.9, 0.2, 0.2]),
        "morph-diff": np.array([0.2, 0.9, 0.2]),
        "bust-floor": np.array([0.2, 0.2, 0.9]),
    }
    s = _stats(terms, in_bust, np.full(3, 0.9), np.full(3, 0.9), 1.1,
               np.zeros(3))
    assert s["counts"] == {"amp-ramp": 1, "morph-diff": 1, "bust-floor": 1}
    assert s["bust"] == 3


def test_verts_outside_the_bust_band_do_not_enter_the_count():
    in_bust = np.array([True, False, False])
    terms = {"amp-ramp": np.array([0.9, 0.1, 0.1]),
             "morph-diff": np.array([0.1, 0.9, 0.9])}
    s = _stats(terms, in_bust, np.full(3, 0.9), np.full(3, 0.9), 1.1,
               np.zeros(3))
    assert s["bust"] == 1
    assert s["counts"] == {"amp-ramp": 1, "morph-diff": 0}


def test_at_cap_counts_the_verts_no_base_or_factor_knob_can_move():
    """If the winning term sits ON the cap, only the CAP can lower it. That is
    the whole reason five knobs measured inert, so it is counted explicitly."""
    in_bust = np.ones(4, dtype=bool)
    terms = {"amp-ramp": np.array([1.1, 1.1, 0.5, 0.5])}
    s = _stats(terms, in_bust, np.full(4, 1.0), np.full(4, 1.0), 1.1,
               np.zeros(4))
    assert s["at_cap"] == 2


def test_a_term_the_shape_never_computed_is_REPORTED_not_defaulted():
    """A missing term is an answer about that shape, not a zero to average in."""
    s = _stats({"amp-ramp": np.array([0.5])}, np.array([True]),
               np.array([0.5]), np.array([0.5]), 1.1, np.zeros(1))
    assert s["missing"] == ["morph-diff", "bust-floor"]
    assert "morph-diff" not in s["counts"]


def test_the_relaxation_column_is_what_the_authored_floor_TOOK_OFF():
    """`relax_p50` is pre-relax minus final. Zero means the authored floor did
    not bind here -- which is a finding, and must not read as "not measured"."""
    s = _stats({"amp-ramp": np.array([1.0, 1.0])}, np.ones(2, dtype=bool),
               np.array([0.7, 0.6]), np.array([1.0, 1.0]), 1.1, np.zeros(2))
    assert s["relax_p50"] == pytest.approx(0.35)
    s2 = _stats({"amp-ramp": np.array([1.0])}, np.array([True]),
                np.array([1.0]), np.array([1.0]), 1.1, np.zeros(1))
    assert s2["relax_p50"] == 0.0


# ------------------------------------------------------------- refusals

def test_no_bust_vertex_returns_None_rather_than_a_clean_zero():
    """0/0 IS NOT A PASS. A shape with nothing in the band must not report a
    term distribution, and the caller says `bust=0` in words."""
    assert _stats({"amp-ramp": np.zeros(3)}, np.zeros(3, dtype=bool),
                  np.zeros(3), np.zeros(3), 1.1, np.zeros(3)) is None
    assert _stats({"amp-ramp": np.zeros(3)}, None,
                  np.zeros(3), np.zeros(3), 1.1, np.zeros(3)) is None


def test_no_terms_at_all_returns_None():
    assert _stats({}, np.ones(2, dtype=bool), np.zeros(2), np.zeros(2), 1.1,
                  np.zeros(2)) is None


def test_the_reader_and_the_printer_share_one_term_list():
    """A renamed term must break the suite, not turn up as a silently missing
    column on an arm that cost half an hour."""
    assert fg.CLEARANCE_TERMS == ("amp-ramp", "morph-diff", "bust-floor")
    src = Path(fg.__file__).read_text(encoding="utf-8")
    for t in fg.CLEARANCE_TERMS:
        assert '_terms["%s"]' % t in src, t


# --------------------------------------------- the reader and the printer

def _emit(stats, capsys):
    fg._clearance_term_report(stats)
    return capsys.readouterr().out


def test_the_reader_parses_the_printers_own_line(capsys):
    """Built from a line the PRINTER produced, not one hand-typed here -- the
    two drifting apart is how a gate row silently becomes UNPARSED."""
    from scripts.analysis import clearance_terms as ct
    s = _stats({"amp-ramp": np.array([1.1, 0.2]),
                "morph-diff": np.array([0.2, 1.0]),
                "bust-floor": np.array([0.1, 0.1])},
               np.ones(2, dtype=bool), np.array([1.1, 1.0]),
               np.array([1.1, 1.0]), 1.1, np.zeros(2))
    line = _emit(s, capsys)
    rows, empty, _heads, _ex = ct.parse(line)
    assert len(rows) == 1 and empty == 0
    assert rows[0]["bust"] == 2
    assert rows[0]["at_cap"] == 1
    assert rows[0]["counts"] == {"amp-ramp": 1, "morph-diff": 1, "bust-floor": 0}
    assert rows[0]["missing"] == []


def test_a_shape_with_no_bust_vertex_is_counted_as_an_EXCLUSION(capsys):
    from scripts.analysis import clearance_terms as ct
    line = _emit(None, capsys)
    rows, empty, _heads, _ex = ct.parse(line)
    assert rows == [] and empty == 1


def test_the_reader_records_a_term_the_shape_never_computed(capsys):
    from scripts.analysis import clearance_terms as ct
    s = _stats({"amp-ramp": np.array([0.5])}, np.array([True]),
               np.array([0.5]), np.array([0.5]), 1.1, np.zeros(1))
    rows, _empty, _heads, _ex = ct.parse(_emit(s, capsys))
    assert rows[0]["missing"] == ["morph-diff", "bust-floor"]


def test_no_audit_line_at_all_exits_3_not_0(tmp_path, capsys):
    """0/0 IS NOT A PASS: an unarmed arm must not read as "no bust clearance"."""
    from scripts.analysis import clearance_terms as ct
    p = tmp_path / "log.txt"
    p.write_bytes(b"  active flags (5): ...\n  nothing to see\n")
    with pytest.raises(SystemExit) as ex:
        ct.main([str(p)])
    assert ex.value.code == 3


def test_a_missing_input_file_is_refused(tmp_path):
    from scripts.analysis import clearance_terms as ct
    assert ct.main([str(tmp_path / "nope.txt")]) == 2
    assert ct.main([]) == 2


# ---------------------------------- WHY the authored relaxation took what it took

def _split(exempt, bound, pre, fin, in_bust=None, worst=None, authored=None,
           amp_room=None, bust=None):
    """`bound` is what the pass compares against; the four components say WHY.

    Omitting the components exercises the coarse path an older arm's line
    produces -- it must fall back to one `no_bind` bucket, not crash.
    """
    n = len(pre)
    m = np.ones(n, dtype=bool) if in_bust is None else np.asarray(in_bust)
    relax = {"exempt": np.asarray(exempt, dtype=bool),
             "bound": np.asarray(bound, dtype=float)}
    for k, val in (("worst", worst), ("authored", authored),
                   ("amp_room", amp_room), ("bust", bust)):
        if val is not None:
            relax[k] = np.asarray(val, dtype=float)
    return fg.clearance_relax_split(
        relax, m, np.asarray(pre, dtype=float), np.asarray(fin, dtype=float))


def _lo(n):
    return [-9.9] * n


def test_the_tip_exemption_is_told_apart_from_a_floor_that_cannot_bind():
    """THE WHOLE POINT. A bare `relaxed 0.0000u` covers situations that want
    OPPOSITE work: the tip exemption refusing it (by design) and the floor
    sitting above the requirement. They must never be one number."""
    s = _split(exempt=[True, False], bound=[0.0, 9.9], pre=[1.0, 1.0],
               fin=[1.0, 1.0])
    assert s["exempt"] == 1 and s["no_bind"] == 1 and s["relaxed"] == 0


def test_a_relaxation_that_actually_lowered_the_requirement_is_counted():
    s = _split(exempt=[False], bound=[0.5], pre=[1.0], fin=[0.5])
    assert s["relaxed"] == 1 and s["no_bind"] == 0


def test_an_unexplained_residue_is_kept_visible_as_other():
    """Allowed, changed nothing, and nothing was high enough -- that should not
    happen, so it must not be rounded into a `floor_*` bucket."""
    s = _split(exempt=[False], bound=[0.1], pre=[1.0], fin=[1.0])
    assert s["other"] == 1 and s["no_bind"] == 0 and s["relaxed"] == 0


def test_the_authored_block_never_running_is_a_FOURTH_answer():
    """`None` is not `no_bind`: the floor did not decline, it never ran."""
    assert fg.clearance_relax_split(None, np.array([True]), np.array([1.0]),
                                    np.array([1.0])) is None


def test_the_split_only_counts_the_bust_band():
    s = _split(exempt=[True, True], bound=[0.0, 0.0], pre=[1.0, 1.0],
               fin=[1.0, 1.0], in_bust=[True, False])
    assert s["exempt"] == 1


# ------------------- "allowed but changed nothing" is FOUR answers, not one

def test_a_garment_already_out_far_enough_is_NOT_a_defect():
    """`worst >= req` means the push is zero anyway. Counting that as a floor
    that could not bind inflates the defect by whatever fraction of the band is
    already clear -- which is most of a loose garment."""
    s = _split(exempt=[False], bound=[2.0], pre=[1.0], fin=[1.0],
               worst=[2.0], authored=_lo(1), amp_room=_lo(1), bust=_lo(1))
    assert s == {"exempt": 0, "relaxed": 0, "other": 0, "already_out": 1,
                 "floor_auth": 0, "floor_bust": 0, "floor_amp": 0,
                 "floor_none": 0}


def test_the_author_being_looser_than_our_need_is_NOT_a_defect():
    """Nothing to relax toward: we are not proud of the author here."""
    s = _split(exempt=[False], bound=[1.5], pre=[1.0], fin=[1.0],
               worst=[0.0], authored=[1.5], amp_room=_lo(1), bust=_lo(1))
    assert s["floor_auth"] == 1 and s["floor_amp"] == 0


def test_the_bust_ramp_blocking_it_is_BY_DESIGN_and_counted_separately():
    """`#authored-keeps-the-bust-floor` exists so the relaxation cannot take the
    bust below the fixed path. That is not the morph-headroom defect."""
    s = _split(exempt=[False], bound=[1.5], pre=[1.0], fin=[1.0],
               worst=[0.0], authored=_lo(1), amp_room=_lo(1), bust=[1.5])
    assert s["floor_bust"] == 1 and s["floor_amp"] == 0


def test_the_morph_headroom_floor_is_credited_ONLY_when_it_is_the_sole_reason():
    """`amp_room` is scored LAST on purpose. If the author or the bust ramp
    would have held the vertex anyway, crediting amp_room invents a defect."""
    sole = _split(exempt=[False], bound=[1.5], pre=[1.0], fin=[1.0],
                  worst=[0.0], authored=_lo(1), amp_room=[1.5], bust=_lo(1))
    assert sole["floor_amp"] == 1
    shared = _split(exempt=[False], bound=[1.5], pre=[1.0], fin=[1.0],
                    worst=[0.0], authored=[1.5], amp_room=[1.5], bust=_lo(1))
    assert shared["floor_amp"] == 0 and shared["floor_auth"] == 1


def test_the_buckets_partition_the_band_exactly_once():
    """Every bust vertex lands in exactly one bucket. A decomposition whose
    parts do not sum to the whole is how a share gets double-counted."""
    n = 6
    s = _split(exempt=[True, False, False, False, False, False],
               bound=[0.0, 2.0, 1.5, 1.5, 1.5, 0.1],
               pre=[1.0] * n, fin=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
               worst=[0.0, 2.0, 0.0, 0.0, 0.0, 0.0],
               authored=[-9.9, -9.9, 1.5, -9.9, -9.9, -9.9],
               amp_room=[-9.9, -9.9, -9.9, -9.9, 1.5, -9.9],
               bust=[-9.9, -9.9, -9.9, 1.5, -9.9, -9.9])
    assert sum(s.values()) == n
    assert s["exempt"] == 1 and s["already_out"] == 1
    assert s["floor_auth"] == 1 and s["floor_bust"] == 1
    assert s["floor_amp"] == 1 and s["other"] == 1


def test_a_line_without_the_components_still_reports_a_coarse_bucket():
    """An arm produced before the components existed must not crash the split;
    it collapses to `no_bind` and the reader labels it as unreported."""
    s = _split(exempt=[False], bound=[9.9], pre=[1.0], fin=[1.0])
    assert s["no_bind"] == 1
    assert "floor_amp" not in s


def test_the_reader_parses_the_split_off_the_printers_line(capsys):
    from scripts.analysis import clearance_terms as ct
    s = _stats({"amp-ramp": np.array([1.0, 1.0])}, np.ones(2, dtype=bool),
               np.array([1.0, 0.5]), np.array([1.0, 1.0]), 1.1, np.zeros(2))
    s["split"] = {"exempt": 1, "no_bind": 0, "other": 0, "relaxed": 1}
    fg._clearance_term_report(s)
    rows, _empty, _heads, _ex = ct.parse(capsys.readouterr().out)
    assert rows[0]["split"] == {"exempt": 1, "no_bind": 0, "other": 0,
                                "relaxed": 1}
    assert rows[0]["relax_off"] is False


def test_the_reader_reads_relax_off(capsys):
    from scripts.analysis import clearance_terms as ct
    s = _stats({"amp-ramp": np.array([1.0])}, np.array([True]),
               np.array([1.0]), np.array([1.0]), 1.1, np.zeros(1))
    assert s["split"] is None
    fg._clearance_term_report(s)
    rows, _empty, _heads, _ex = ct.parse(capsys.readouterr().out)
    assert rows[0]["relax_off"] is True and rows[0]["split"] == {}


def test_the_capture_site_is_guarded_by_the_flag():
    src = Path(fg.__file__).read_text(encoding="utf-8")
    i = src.index('_relax = {')
    assert "if _terms is not None:" in src[max(0, i - 700):i]
    assert "_relax = None" in src


# --------------------------- the budget a BUST-FLOOR knob has, before an arm

def _head(bust, amp, in_bust=None, bust_clear=1.0):
    n = len(bust)
    m = np.ones(n, dtype=bool) if in_bust is None else np.asarray(in_bust)
    return fg.bust_floor_headroom(
        {"bust-floor": np.asarray(bust, dtype=float),
         "amp-ramp": np.asarray(amp, dtype=float)}, m, bust_clear)


def test_the_headroom_is_the_distance_down_to_the_RUNNER_UP():
    """A bust-floor knob cannot lower `req` past the next-highest term, so that
    distance is the WHOLE budget every such knob shares. Measuring it costs no
    arm; five knobs were each declared inert by spending one."""
    h = _head(bust=[1.0, 1.0], amp=[0.6, 0.9])
    assert h["wins"] == 2
    assert h["head_sum"] == pytest.approx(0.5)
    assert h["head_p50"] == pytest.approx(0.25)


def test_verts_the_bust_floor_does_NOT_win_carry_no_budget():
    """Where another term is already higher, lowering the bust floor changes
    nothing at all -- those verts must not inflate the budget."""
    h = _head(bust=[0.5, 1.0], amp=[0.9, 0.6])
    assert h["wins"] == 1
    assert h["head_sum"] == pytest.approx(0.4)


def test_a_bust_floor_that_never_wins_reports_a_zero_budget_not_None():
    """Zero budget is a measurement. None would read as 'not measured'."""
    h = _head(bust=[0.1], amp=[0.9])
    assert h == {"wins": 0, "head_p50": 0.0, "head_sum": 0.0, "at_ceiling": 0}


def test_at_ceiling_says_which_knob_is_even_live():
    """A vert pinned at BUST_CLEAR has had the ramp clipped away, so FLAT_CLEAR
    and NIPPLE_GAIN cannot reach it and only the ceiling can."""
    h = _head(bust=[1.0, 0.9], amp=[0.1, 0.1], bust_clear=1.0)
    assert h["wins"] == 2 and h["at_ceiling"] == 1


def test_the_headroom_needs_a_runner_up_to_measure_against():
    assert fg.bust_floor_headroom({"bust-floor": np.array([1.0])},
                                   np.array([True]), 1.0) is None
    assert fg.bust_floor_headroom({"amp-ramp": np.array([1.0])},
                                   np.array([True]), 1.0) is None


def test_an_arm_without_the_head_line_reads_NOT_MEASURED(capsys):
    """Not measured is not a zero budget -- the same distinction every other
    row in this toolkit keeps."""
    from scripts.analysis import clearance_terms as ct
    ct.report([{"bust": 1, "at_cap": 0, "counts": {}, "req_p50": 1.0,
                "relax_p50": 0.0, "worst_p50": 0.0, "missing": [],
                "relax_off": True, "split": {}}], 0, heads=(), exheads=())
    assert "BUST-FLOOR KNOB BUDGET                    : NOT MEASURED" in \
        capsys.readouterr().out


def test_the_nipple_gain_is_a_named_knob_now():
    """It was a BARE LITERAL, so it could not be swept at all -- an A/B can only
    move a NAMED env. Same prerequisite the conform retune needed."""
    src = Path(fg.__file__).read_text(encoding="utf-8")
    assert 'ANTIPOKE_NIPPLE_GAIN = _knob("CBBE2UBE_NIPPLE_GAIN", 1.5)' in src


def test_the_three_bust_floor_knobs_all_resolve_to_their_documented_defaults():
    """Resolved from CODE in a scrubbed subprocess -- a default read in THIS
    process would inherit whatever the harness exported."""
    src = (
        "import os,sys;"
        "[os.environ.pop(k) for k in list(os.environ) if k.startswith('CBBE2UBE_')];"
        "sys.path.insert(0, r'%s');"
        "from src import nif_convert_fitgeom as f;"
        "print(f.ANTIPOKE_FLAT_CLEAR, f.ANTIPOKE_BUST_CLEAR, f.ANTIPOKE_NIPPLE_GAIN)"
        % REPO_ROOT
    )
    out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                         text=True, cwd=str(REPO_ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["0.8", "1.0", "1.5"], out.stdout


# ------------------- the TIP EXEMPTION: a share is not a budget

def _ex(exempt, bound, pre, in_bust=None):
    n = len(pre)
    m = np.ones(n, dtype=bool) if in_bust is None else np.asarray(in_bust)
    return fg.exempt_headroom(
        {"exempt": np.asarray(exempt, dtype=bool),
         "bound": np.asarray(bound, dtype=float)},
        m, np.asarray(pre, dtype=float))


def test_an_exempt_vert_the_relaxation_would_not_have_moved_costs_NOTHING():
    """THE POINT. The exemption covers most of the bust band, and a share is not
    a budget: where the relaxation would have changed nothing, refusing it is
    free. Counting those as protected is the `no_bind` mistake again."""
    h = _ex(exempt=[True], bound=[2.0], pre=[1.0])
    assert h == {"real": 0, "moot": 1, "head_p50": 0.0, "head_sum": 0.0}


def test_the_budget_is_what_the_exemption_actually_refused():
    h = _ex(exempt=[True, True], bound=[0.5, 0.9], pre=[1.0, 1.0])
    assert h["real"] == 2 and h["moot"] == 0
    assert h["head_sum"] == pytest.approx(0.6)
    assert h["head_p50"] == pytest.approx(0.3)


def test_non_exempt_verts_are_not_in_the_exemption_budget():
    """They were relaxed or held for some other reason; either way the
    exemption did not refuse them."""
    h = _ex(exempt=[False, True], bound=[0.1, 0.5], pre=[1.0, 1.0])
    assert h["real"] == 1 and h["head_sum"] == pytest.approx(0.5)


def test_a_shape_with_no_exempt_vert_reports_a_zero_budget_not_None():
    h = _ex(exempt=[False], bound=[0.1], pre=[1.0])
    assert h == {"real": 0, "moot": 0, "head_p50": 0.0, "head_sum": 0.0}


def test_the_exemption_budget_only_counts_the_bust_band():
    h = _ex(exempt=[True, True], bound=[0.5, 0.5], pre=[1.0, 1.0],
            in_bust=[True, False])
    assert h["real"] == 1


def test_an_arm_without_the_exempt_line_reads_NOT_MEASURED(capsys):
    from scripts.analysis import clearance_terms as ct
    ct.report([{"bust": 1, "at_cap": 0, "counts": {}, "req_p50": 1.0,
                "relax_p50": 0.0, "worst_p50": 0.0, "missing": [],
                "relax_off": True, "split": {}}], 0, heads=(), exheads=())
    assert "TIP-EXEMPTION BUDGET                      : NOT MEASURED" in \
        capsys.readouterr().out


def test_the_reader_parses_the_exempt_line_off_the_printer(capsys):
    from scripts.analysis import clearance_terms as ct
    s = _stats({"amp-ramp": np.array([1.0, 1.0])}, np.ones(2, dtype=bool),
               np.array([1.0, 1.0]), np.array([1.0, 1.0]), 1.1, np.zeros(2))
    s["exhead"] = {"real": 3, "moot": 4, "head_p50": 0.25, "head_sum": 0.75}
    fg._clearance_term_report(s)
    _rows, _empty, _heads, ex = ct.parse(capsys.readouterr().out)
    assert ex == [{"real": 3, "moot": 4, "head_p50": 0.25, "head_sum": 0.75}]


def test_both_exemption_knobs_resolve_to_their_documented_defaults():
    src = (
        "import os,sys;"
        "[os.environ.pop(k) for k in list(os.environ) if k.startswith('CBBE2UBE_')];"
        "sys.path.insert(0, r'%s');"
        "from src import nif_convert as n;"
        "print(n.AUTHORED_NIPPLE_EXEMPT, n.AUTHORED_NIPPLE_RADIUS)" % REPO_ROOT
    )
    out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                         text=True, cwd=str(REPO_ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["0.5", "2.0"], out.stdout
