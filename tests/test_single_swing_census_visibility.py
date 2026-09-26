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

"""single_swing_census across two bodies: the full band vs visible skin.

The crotch-band 'we bury it deeper than the author' reading was a cross-body
artefact: UBE models a dense midline slit, CBBE does not, and a body->nearest-
garment-vertex clearance reads any CBBE-shaped cloth negative on the slit
walls with no conversion error at all. These synthetic bodies are one arc of
skin, identical except for a narrow V slit on the second, and the garment is
the first body offset 0.5u along its own normals -- a perfect conversion. The
full band must reproduce the artefact; the visible band must read ~0; the
built-in self-check must pass the perfect warp and refuse a wrong one.

The self-check also guards the POSED rows: a shell riding each body on its own
weights must read ~0 swing difference, and a planted skew -- a shell weighted
to the wrong bone on one arm, or bodies that cannot swing -- must refuse the
census (exit 4). Source-vs-converted numbers are compared on the PAIRED band,
skin visible on both bodies, so both arms cover the same skin.
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analysis import single_swing_census as ssc  # noqa: E402

LTH, RTH, PEL = ssc.LTH, ssc.RTH, ssc.PEL
R, S, A, H = 10.0, 0.25, 0.1, 1.0      # arc radius, column spacing, slit half-mouth, depth
Z = np.arange(44.0, 86.01, 1.0)
PIVOT = {PEL: (0.0, 0.0, 50.0), LTH: (0.0, 0.0, 50.0), RTH: (0.0, 0.0, 50.0)}
PAR = {PEL: None, LTH: PEL, RTH: PEL}
D = 0.5


def _profile(slit):
    xs = np.arange(S / 2, 8.0, S)
    xs = np.concatenate([-xs[::-1], xs])
    pts = [(x, np.sqrt(R * R - x * x)) for x in xs]
    if not slit:
        return np.array(pts)
    k = len(xs) // 2
    yt = np.sqrt(R * R - A * A)
    t = np.linspace(0.0, 1.0, 6)
    left = [(-A + A * u, yt - H * u) for u in t]
    right = [(A * u, yt - H + H * u) for u in t][1:]
    return np.array(pts[:k] + left + right + pts[k:])


def _extrude(P):
    K = len(P)
    V = np.array([(x, y, z) for z in Z for (x, y) in P])
    T = []
    for r in range(len(Z) - 1):
        for k in range(K - 1):
            a, b, c, d = r * K + k, r * K + k + 1, (r + 1) * K + k + 1, (r + 1) * K + k
            T += [(a, b, c), (a, c, d)]
    T = np.array(T, np.int64)
    top = np.argmin(np.abs(V[:, 0] - 4.0) + np.abs(V[:, 2] - 65.0))
    if ssc._normals(V, T)[top, 1] < 0:          # outward is +y on this arc
        T = T[:, ::-1].copy()
    return V, T


class _Tb:
    def __init__(self, origin):
        self.rotation = np.eye(3)
        self.translation = -np.asarray(origin, float)
        self.scale = 1.0


class _Shape:
    def __init__(self, name, V, T, weights=None):
        self.name = name
        self.verts = [tuple(v) for v in V]
        self.tris = [tuple(t) for t in T]
        self.bone_weights = weights or {}

    def get_shape_skin_to_bone(self, bone):
        return _Tb(PIVOT.get(bone, (0.0, 0.0, 0.0)))


def _pelvis(n):
    return {PEL: [(i, 1.0) for i in range(n)], LTH: [], RTH: []}


def _body(slit, name="Body"):
    V, T = _extrude(_profile(slit))
    return _Shape(name, V, T, _pelvis(len(V))), V, T


def _garment():
    """The slit-free body 0.5u out along its own normals, the strip over the
    midline riding the left thigh (the rest the pelvis)."""
    _, V, T = _body(False)
    G = V + D * ssc._normals(V, T)
    thigh = np.abs(G[:, 0]) < 0.2
    w = {LTH: [(i, 1.0) for i in np.flatnonzero(thigh)],
         PEL: [(i, 1.0) for i in np.flatnonzero(~thigh)]}
    return _Shape("Garment", G, T, w)


def _split_hole(V, T):
    """The slit-free body with a hole over the midline, and the hole's faces as
    a separate COMPANION shape -- the way the zeroed 3BA body ships its vulva."""
    c = V[T].mean(1)
    hole = (np.abs(c[:, 0]) < 0.6) & (c[:, 2] > 62.0) & (c[:, 2] < 68.0)
    used = np.unique(T[hole])
    remap = -np.ones(len(V), np.int64)
    remap[used] = np.arange(len(used))
    return (V, T[~hole]), (V[used], remap[T[hole]])


def _radial(G):
    r = G.copy()
    r[:, 2] = 0.0
    return r / np.linalg.norm(r, axis=1, keepdims=True)


def _skin(V, midline_on_thigh=False, handover=(1.0, 5.0)):
    """`read_skin`-shaped weights for a synthetic body: the pelvis, handing over
    to the thigh on its own side from |x| = handover[0] over handover[1] units,
    and down the band. With `midline_on_thigh` the skin over |x| < 2 rides the
    LEFT thigh instead -- a body weighted to the wrong bone there."""
    x, z = V[:, 0], V[:, 2]
    t = (np.clip((np.abs(x) - handover[0]) / handover[1], 0.0, 1.0)
         * np.clip((70.0 - z) / 15.0, 0.0, 1.0))
    wl = np.where(x < 0, t, 0.0)
    wr = np.where(x >= 0, t, 0.0)
    if midline_on_thigh:
        mid = np.abs(x) < 2.0
        wl = np.where(mid, 1.0, wl)
        wr = np.where(mid, 0.0, wr)
    wp = 1.0 - wl - wr
    return {PEL: (wp, np.array(PIVOT[PEL])), LTH: (wl, np.array(PIVOT[LTH])),
            RTH: (wr, np.array(PIVOT[RTH]))}


def _inputs(warp, par=PAR, skewed_source_shell=False, ube_handover=(1.0, 5.0)):
    """Self-check inputs on the synthetic bodies. With `skewed_source_shell` the
    source MAIN shape -- whose weights skin the author's shell -- carries the
    midline on the left thigh while the closed body the author arm is posed on
    does not: a shell weighted to the wrong bone, on one arm only.
    `ube_handover` paints the UBE body's thigh handover differently."""
    _, cV, cT = _body(False)
    (mV, mT), (pV, pT) = _split_hole(cV, cT)
    CV = np.vstack([mV, pV])
    closed = (CV, np.vstack([mT, pT + len(mV)]), _skin(CV))
    _, uV, uT = _body(True)
    main = (mV, mT, _skin(mV, midline_on_thigh=skewed_source_shell))
    return main, closed, (uV, uT, _skin(uV, handover=ube_handover)), warp, par


def _pair_refs():
    """The paired masks of the arms fixture's two bodies, as `main` seeds them."""
    _, cV, cT = _body(False)
    _, uV, uT = _body(True)
    pc, pu = ssc.paired_skin(cV, cV, cT, uV, uT)
    return (cV, pc), (uV, pu)


def _perfect(G, GT):
    return G


def _buried(G, GT):
    return G - 0.5 * _radial(G)


@pytest.fixture(scope="module")
def arms():
    cb, _, _ = _body(False)
    ub, _, _ = _body(True)
    g = _garment()
    rc, ru = _pair_refs()
    return (ssc.measure(cb, [g], par=PAR, pair_ref=rc),
            ssc.measure(ub, [g], par=PAR, pair_ref=ru))


def test_the_slit_reads_as_burial_on_the_full_band_and_not_on_visible_skin(arms):
    author, ours = arms
    assert ours["bind_c_p05"] - author["bind_c_p05"] < -0.4, (author, ours)
    va, vo = author["vis"], ours["vis"]
    assert abs(vo["bind_c_p05"] - va["bind_c_p05"]) < 0.05, (va, vo)
    assert abs(vo["bind_c_p50"] - va["bind_c_p50"]) < 0.05, (va, vo)


def test_the_slit_walls_are_not_visible_skin(arms):
    _, ours = arms
    assert ours["vis"]["band_n"] == ours["band_n"]
    assert ours["vis"]["visible_n"] < 0.95 * ours["band_n"], ours["vis"]


def test_the_posed_loss_is_scored_over_visible_skin(arms):
    """The thigh-weighted strip covers the slit walls: a swing drags it across
    them, so the full band loses >1u at p90. On visible skin it covers two
    columns of many, and the p90 does not move."""
    _, ours = arms
    assert ours["L45_loss_p90"] > 1.0, ours
    assert ours["vis"]["L45_loss_p90"] < 0.05, ours["vis"]


def test_the_source_body_is_closed_with_its_companion_shapes():
    _, V, T = _body(False)
    (mV, mT), (pV, pT) = _split_hole(V, T)
    main = _Shape("Body", mV, mT, _pelvis(len(mV)))
    patch = _Shape("Body_Patch", pV, pT, _pelvis(len(pV)))
    other = _Shape("Body2", pV, pT, _pelvis(len(pV)))

    class _Nif:
        shapes = [other, patch, main]
    assert [s.name for s in ssc.body_shapes(_Nif, "Body")] == ["Body", "Body_Patch"]
    row = ssc.measure(main, [_garment()], companions=[patch], par=PAR)
    in_band = int(ssc.band_mask(pV).sum())
    assert in_band > 0
    assert row["band_n"] == int(ssc.band_mask(mV).sum())
    assert row["vis"]["band_n"] == row["band_n"] + in_band


def test_the_shell_caps_an_interior_hole_and_not_the_region_cut():
    _, V, T = _body(False)
    (mV, mT), _ = _split_hole(V, T)
    assert ssc.closed_shell(V, T)[2] == 0
    G, GT, holes = ssc.closed_shell(mV, mT)
    assert holes == 1
    # the cap's centre sits D outside the middle of the hole, as a garment spanning it would
    assert np.allclose(G[-1], (0.0, R + D, 65.0), atol=0.05), G[-1]


def test_the_self_check_passes_a_perfect_warp():
    res = ssc.self_check(*_inputs(_perfect))
    assert res["ok"], res
    assert res["holes_capped"] == 1
    assert res["legacy_dp05"] < -0.4, res


def test_the_self_check_fails_a_planted_wrong_warp():
    res = ssc.self_check(*_inputs(_buried))
    assert not res["ok"], res
    assert "perfect warped shell" in res["why"][0]


def test_the_self_check_fails_when_it_cannot_see_burial(monkeypatch):
    monkeypatch.setattr(ssc, "SELF_CHECK_BURY", 0.0)
    res = ssc.self_check(*_inputs(_perfect))
    assert not res["ok"], res
    assert any("cannot see burial" in w for w in res["why"]), res


def test_the_self_check_fails_when_nothing_is_visible():
    src_main, closed, (uV, uT, uW), warp, par = _inputs(_perfect)
    far = uV + np.array([0.0, 0.0, 200.0])
    res = ssc.self_check(src_main, closed, (far, uT, uW), warp, par)
    assert not res["ok"], res
    assert "0/0 is not a pass" in res["why"][0]


# ------------------------------------------------------------ the posed rows

def test_the_self_check_scores_the_posed_rows_of_a_perfect_shell():
    """Each arm's shell rides its own body: ~0 swing difference on both bands,
    and the planted one-thigh midline on our arm reads far worse."""
    res = ssc.self_check(*_inputs(_perfect))
    assert res["ok"], res
    for band in ("visible", "paired"):
        r = res[band]
        assert abs(r["dL"]) <= ssc.SELF_CHECK_POSED_TOL and abs(r["dR"]) <= ssc.SELF_CHECK_POSED_TOL, r
        assert r["skew_d"] > 1.0, r


def test_a_shell_weighted_to_the_wrong_bone_on_one_arm_fails_the_self_check():
    res = ssc.self_check(*_inputs(_perfect, skewed_source_shell=True))
    assert not res["ok"], res
    assert any("swing loss" in w for w in res["why"]), res
    assert res["visible"]["dL"] < -1.0, res["visible"]
    # the bind rows cannot see it: only the posed check does
    assert abs(res["visible"]["dp05"]) <= ssc.SELF_CHECK_TOL, res["visible"]


def test_a_perfect_shell_rides_each_body_however_it_is_painted():
    """UBE hands the skin to the thigh later and more steeply here. A perfect
    conversion rides the body it sits on, so it still reads ~0; the same shell
    KEEPING the source weights on UBE is what reads the painting difference,
    and it is reported beside the check, not failed by it."""
    res = ssc.self_check(*_inputs(_perfect, ube_handover=(4.0, 1.0)))
    assert res["ok"], res
    r = res["paired"]
    assert max(r["carried_dL"], r["carried_dR"]) > 3 * ssc.SELF_CHECK_POSED_TOL, r


def _no_thighs(arm):
    V, T, W = arm
    return V, T, {PEL: (np.ones(len(V)), W[PEL][1])}


def test_bodies_that_cannot_swing_fail_the_self_check():
    """Bodies read with no thigh bones pose nothing: both arms lose 0 and the
    perfect rows pass, so only the planted one-thigh skew can refuse them."""
    main, closed, ube, warp, par = _inputs(_perfect)
    res = ssc.self_check(_no_thighs(main), _no_thighs(closed), _no_thighs(ube), warp, par)
    assert not res["ok"], res
    assert all("cannot see a posed skew" in w for w in res["why"]), res


def test_the_census_refuses_to_report_a_planted_posed_skew(tmp_path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ssc.main(["--pack", str(tmp_path), "--out", str(tmp_path / "rows.jsonl")],
                      self_check_inputs=_inputs(_perfect, skewed_source_shell=True))
    out = buf.getvalue()
    assert rc == ssc.SELF_CHECK_EXIT, out
    # refused for the posed reason itself, not only by some other check
    fail = next(s for s in out.splitlines() if "SELF-CHECK FAIL" in s)
    assert "a perfect shell riding the body reads ours - author swing loss" in fail, out
    assert "REFUSED" in out
    assert not (tmp_path / "rows.jsonl").exists()


def test_the_shell_knows_the_body_vertex_each_vertex_came_from():
    _, V, T = _body(False)
    (mV, mT), _ = _split_hole(V, T)
    G, GT, holes, src = ssc.closed_shell(mV, mT, sources=True)
    assert len(src) == len(G)
    lift = np.linalg.norm(G[:-holes] - mV[src[:-holes]], axis=1)
    assert np.allclose(lift, D, atol=1e-6), lift.max()
    # the cap centre takes a vertex on the hole's rim, not one across the body
    assert np.abs(mV[src[-1], 0]) < 1.0 and 62.0 <= mV[src[-1], 2] <= 68.0, mV[src[-1]]


# ------------------------------------------------------------ like-for-like skin

def test_the_paired_band_scores_both_arms_on_the_same_skin(arms):
    """The garment is a perfect conversion: on skin visible on BOTH bodies the
    two arms read the same bind and the same swing."""
    author, ours = arms
    pa, po = author["pair"], ours["pair"]
    assert pa is not None and po is not None
    assert abs(po["bind_c_p05"] - pa["bind_c_p05"]) < 0.05, (pa, po)
    assert abs(po["bind_c_p50"] - pa["bind_c_p50"]) < 0.05, (pa, po)
    assert abs(po["L45_loss_p90"] - pa["L45_loss_p90"]) < 0.05, (pa, po)


def test_the_slit_walls_are_never_paired():
    """The walls face each other; only the bottom of the V (x = 0) looks
    straight out of the mouth, and that is visible skin on UBE."""
    (cV, pc), (uV, pu) = _pair_refs()
    x, y = np.abs(uV[:, 0]), uV[:, 1]
    walls = ssc.band_mask(uV) & (x < 0.12) & (x > 0.01) & (y < np.sqrt(R * R - A * A) - 0.05)
    assert walls.any()
    assert not pu[walls].any()
    assert pu.sum() > 0 and pc.sum() > 0


def test_a_ube_vertex_pairs_only_through_a_paired_source_vertex(monkeypatch):
    """u2 is visible and its nearest source vertex s1 is visible -- but s1's own
    UBE vertex u1 is hidden, so s1 is not paired and neither is u2 (the slit
    mouth case: visible on UBE, where no paired source vertex lands)."""
    sV = np.array([(0.0, 0.0, 60.0), (1.0, 0.0, 60.0), (5.0, 0.0, 60.0)])
    uV = np.array([(0.0, 0.0, 60.0), (1.1, 0.0, 60.0), (1.4, 0.0, 60.0)])
    vs, vu = np.array([True, True, False]), np.array([True, False, True])
    monkeypatch.setattr(ssc, "visible_band", lambda V, T, N=None: vs if V[1][0] == 1.0 else vu)
    tri = np.array([(0, 1, 2)])
    pc, pu = ssc.paired_skin(sV, sV, tri, uV, tri)
    assert pc.tolist() == [True, False, False]
    assert pu.tolist() == [True, False, False]


def test_a_body_that_is_not_the_reference_gets_no_paired_block():
    (cV, pc), _ = _pair_refs()
    assert ssc.pair_mask_for(cV, (cV, pc)) is not None
    assert ssc.pair_mask_for(cV + np.array([0.0, 0.01, 0.0]), (cV, pc)) is None
    assert ssc.pair_mask_for(cV, None) is None


def test_only_the_paired_band_prints_a_source_vs_converted_line_as_like_for_like():
    arm = {"n": 60, "band_n": 60, "bind_c_p05": 0.1, "bind_c_p50": 0.3,
           "L45_loss_p90": 0.2, "R45_loss_p90": 0.2, "L45_newly_inside_pct": 0.0,
           "R45_newly_inside_pct": 0.0, "asym_p90_over_pelvis_only": 0.0}
    rows = [{"piece": "p_1.nif", "conv": dict(arm), "src": dict(arm)}]
    for lfl in (False, True):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ssc._loss_block(rows, lambda a: a, "BAND", like_for_like=lfl)
        line = next(s for s in buf.getvalue().splitlines() if "SOURCE on the canonical" in s)
        assert ("NOT LIKE-FOR-LIKE" in line) is (not lfl), line


def test_the_ray_filter_keeps_a_hit_just_past_reach():
    """Rays start 0.01u off the skin, so a surface 6.005u out along the normal
    is within a 6u ray and hides the vertex."""
    from scripts.analysis._census_common import visible_skin
    V = np.array([(0.0, 0.0, 0.0), (-5.0, -5.0, 6.005), (5.0, -5.0, 6.005), (0.0, 5.0, 6.005)])
    T = np.array([(1, 2, 3)])
    N = np.array([(0.0, 0.0, 1.0)] * 4)
    sel = np.array([True, False, False, False])
    assert not visible_skin(V, T, N, sel, reach=6.0)[0]
    assert visible_skin(V, T, N, sel, reach=5.9)[0]


def _main(argv, warp):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ssc.main(argv, self_check_inputs=_inputs(warp))
    return rc, buf.getvalue()


def test_the_census_refuses_to_report_when_the_self_check_fails(tmp_path):
    rc, out = _main(["--pack", str(tmp_path), "--out", str(tmp_path / "rows.jsonl")], _buried)
    assert rc == ssc.SELF_CHECK_EXIT, out
    # refused for the bind reason itself, not only by some other check
    fail = next(s for s in out.splitlines() if "SELF-CHECK FAIL" in s)
    assert "a perfect warped shell reads ours - author" in fail, out
    assert "REFUSED" in out
    assert not (tmp_path / "rows.jsonl").exists()


def test_the_self_check_alone_exits_clean_on_a_perfect_warp():
    rc, out = _main(["--self-check"], _perfect)
    assert rc == 0, out
    assert "SELF-CHECK PASS" in out
