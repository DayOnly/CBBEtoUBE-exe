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


def _inputs(warp):
    _, cV, cT = _body(False)
    (mV, mT), (pV, pT) = _split_hole(cV, cT)
    closed = (np.vstack([mV, pV]), np.vstack([mT, pT + len(mV)]))
    _, uV, uT = _body(True)
    return (mV, mT), closed, (uV, uT), warp


def _perfect(G, GT):
    return G


def _buried(G, GT):
    return G - 0.5 * _radial(G)


@pytest.fixture(scope="module")
def arms():
    cb, _, _ = _body(False)
    ub, _, _ = _body(True)
    g = _garment()
    return ssc.measure(cb, [g], par=PAR), ssc.measure(ub, [g], par=PAR)


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
    src_main, closed, (uV, uT), warp = _inputs(_perfect)
    far = uV + np.array([0.0, 0.0, 200.0])
    res = ssc.self_check(src_main, closed, (far, uT), warp)
    assert not res["ok"], res
    assert "0/0 is not a pass" in res["why"][0]


def _main(argv, warp):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ssc.main(argv, self_check_inputs=_inputs(warp))
    return rc, buf.getvalue()


def test_the_census_refuses_to_report_when_the_self_check_fails(tmp_path):
    rc, out = _main(["--pack", str(tmp_path), "--out", str(tmp_path / "rows.jsonl")], _buried)
    assert rc == ssc.SELF_CHECK_EXIT, out
    assert "REFUSED" in out
    assert not (tmp_path / "rows.jsonl").exists()


def test_the_self_check_alone_exits_clean_on_a_perfect_warp():
    rc, out = _main(["--self-check"], _perfect)
    assert rc == 0, out
    assert "SELF-CHECK PASS" in out
