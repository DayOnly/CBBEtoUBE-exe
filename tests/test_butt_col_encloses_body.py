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

"""#butt-col-encloses-body -- the butt collider's SURFACE must enclose the skin.

`#derived-butt-standoff` offset the decimated patch's kept VERTICES by the
cloth-derived standoff, floored at 0. The patch's triangles are chords between
those vertices, and a chord of a convex surface lies inside it, so with a
standoff below the decimation's sag the collider surface sat UNDER the skin
while every vertex was outside. Measured on two converted robes: standoff 0.12
-> 36% of the buttock poking through the collider, 0.00 -> 73%.

The geometry here is a spherical cap facing -y (a buttock stand-in) decimated
by the pass's own `_decimate`. The enclosure is judged by an INDEPENDENT test --
brute-force radial rays from the sphere's centre through every source vertex
against every collider triangle -- not by the `_ClipTester` the pass uses, so
the pass cannot grade its own homework.
"""
import importlib

import numpy as np
import pytest

import src.nif_convert as nc
from src import nif_convert_physics as ph
from tests import _converter_sources as _cs

R = 6.0


def _cap(R=R, half=np.radians(70), n_th=40, n_ph=60):
    """A sphere cap about -y with outward winding: 2341 verts, 4620 tris --
    the size of the body's rear buttock patch."""
    verts = [np.array([0.0, -R, 0.0])]
    rings = []
    for t in np.linspace(0, half, n_th)[1:]:
        ring = []
        for k in range(n_ph):
            a = 2 * np.pi * k / n_ph
            ring.append(len(verts))
            verts.append(R * np.array([np.sin(t) * np.cos(a), -np.cos(t),
                                       np.sin(t) * np.sin(a)]))
        rings.append(ring)
    tris = [(0, rings[0][k], rings[0][(k + 1) % n_ph]) for k in range(n_ph)]
    for ra, rb in zip(rings[:-1], rings[1:]):
        for k in range(n_ph):
            a0, a1 = ra[k], ra[(k + 1) % n_ph]
            b0, b1 = rb[k], rb[(k + 1) % n_ph]
            tris += [(a0, b0, b1), (a0, b1, a1)]
    v = np.asarray(verts)
    t = np.asarray(tris, np.int64)
    n = nc._vertex_normals_from_tris(v, t)
    if (np.einsum("ij,ij->i", n, v) < 0).mean() > 0.5:
        t = t[:, ::-1].copy()
        n = nc._vertex_normals_from_tris(v, t)
    return v, t, n


@pytest.fixture(scope="module")
def patch():
    v, t, n = _cap()
    reps, _o2n, ct = ph._decimate(v, t, nc._BUTT_COL_TARGET)
    assert len(reps) > 300 and len(ct) > 300, "the decimation shape changed"
    return v, n, reps, ct


def _under_skin(v, n, reps, ct, off):
    """INDEPENDENT enclosure check. Returns (covered, under): how many source
    verts a radial ray from the centre carries onto the collider at all, and
    how many of those meet the collider BELOW the skin (the body pokes
    through it). Brute force Moller-Trumbore, every ray against every tri."""
    cv = v[reps] + n[reps] * off
    a, b, c = cv[ct[:, 0]], cv[ct[:, 1]], cv[ct[:, 2]]
    e1, e2 = b - a, c - a
    r_skin = np.linalg.norm(v, axis=1)
    d_all = v / r_skin[:, None]
    covered = under = 0
    for s in range(0, len(v), 256):
        d = d_all[s:s + 256][:, None, :]                  # (k,1,3)
        p = np.cross(d, e2[None])                         # (k,m,3)
        det = np.einsum("kmj,mj->km", p, e1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        t0 = -a[None]                                     # origin = centre
        u = np.einsum("kmj,kmj->km", np.broadcast_to(t0, p.shape), p) * inv
        q = np.cross(np.broadcast_to(t0, p.shape), e1[None])
        w = np.einsum("kmj,kmj->km", q, np.broadcast_to(d, q.shape)) * inv
        tt = np.einsum("kmj,mj->km", q, e2) * inv
        hit = ok & (u >= -1e-9) & (w >= -1e-9) & (u + w <= 1 + 1e-9) & (tt > 0)
        tmin = np.where(hit, tt, np.inf).min(axis=1)
        has = np.isfinite(tmin)
        covered += int(has.sum())
        under += int((has & (tmin < r_skin[s:s + 256] - 1e-6)).sum())
    return covered, under


def test_PRE_FIX_vertex_offset_leaves_the_surface_under_the_skin(patch):
    """What `#derived-butt-standoff` alone shipped: the floored cloth-derived
    offset (0.00 on Miraak, 0.12 on Taron) applied to the VERTICES. The chords
    sag under the skin, so the same assertion the fix passes fails here."""
    v, n, reps, ct = patch
    covered, under = _under_skin(v, n, reps, ct, 0.0)
    assert covered > 0.9 * len(v)
    assert under > 0.3 * covered, (covered, under)   # measured 1520 of 2301 (66%)
    covered, under = _under_skin(v, n, reps, ct, 0.05)
    assert under > 0, (covered, under)               # measured 262 (11%)


def test_enclosing_offset_puts_the_decimated_surface_OUTSIDE_the_skin(patch):
    v, n, reps, ct = patch
    off, at0, atN = ph._butt_col_enclosing_offset(
        v, n, reps, ct, np.arange(len(v)), 0.0, nc._BUTT_COL_OFFSET)
    assert at0[0] > 0, "the pass's own test must see the pre-fix clip"
    assert atN == (0, 0.0)
    covered, under = _under_skin(v, n, reps, ct, off)
    assert covered > 0.9 * len(v), "vacuous: the rays missed the collider"
    assert under == 0, (off, covered, under)


def test_it_is_the_SMALLEST_enclosing_offset_not_the_cap(patch):
    """Close-fitting cloth is why the standoff was derived in the first place
    (the flat 0.6 inflated the rear). The raise must stop where the surface
    first clears the skin, within a step's slack, not jump to the cap."""
    v, n, reps, ct = patch
    off, _a0, _aN = ph._butt_col_enclosing_offset(
        v, n, reps, ct, np.arange(len(v)), 0.0, nc._BUTT_COL_OFFSET)
    lo, hi = 0.0, off                 # bisect the true minimum, independently
    for _ in range(12):
        mid = 0.5 * (lo + hi)
        if _under_skin(v, n, reps, ct, mid)[1]:
            lo = mid
        else:
            hi = mid
    assert off < nc._BUTT_COL_OFFSET
    assert hi <= off <= hi + 0.05, (hi, off)


def test_it_only_RAISES_and_respects_the_cap(patch):
    v, n, reps, ct = patch
    idx = np.arange(len(v))
    off, at0, _aN = ph._butt_col_enclosing_offset(v, n, reps, ct, idx, 0.5, 0.6)
    assert off == 0.5 and at0 == (0, 0.0), "an enclosing start is kept as is"
    off, _a0, atN = ph._butt_col_enclosing_offset(v, n, reps, ct, idx, 0.0, 0.03)
    assert off == pytest.approx(0.03)
    assert atN[0] > 0, "a capped raise must report what is still under"


def _sheet(y, normal_y, n=12, step=0.5):
    """A flat n x n grid in the xz plane at `y`, wound to face `normal_y`."""
    xs = np.arange(n) * step
    v = np.array([(x, y, z) for z in xs for x in xs], np.float64)
    t = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b, d, e = r * n + c, r * n + c + 1, (r + 1) * n + c, (r + 1) * n + c + 1
            t += [(a, b, e), (a, e, d)]
    t = np.asarray(t, np.int64)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    if fn[0, 1] * normal_y < 0:
        t = t[:, ::-1].copy()
    return v, t, np.tile([0.0, float(normal_y), 0.0], (len(v), 1))


def test_a_hit_BEHIND_the_bodys_own_far_wall_is_not_a_poke_through():
    """THE BUG THIS PINS, found on the first real A/B. In the gluteal cleft the
    inward ray leaves the body within 0.02-0.5u and meets the OTHER cheek's
    collider behind it; without the far-wall test those hits (up to 1.89u) read
    as the body poking through, stayed "under" at 0.6, and every piece hit the
    cap. Day's `clipping_report` rejects them (body_occlusion=True).

    Here: skin at y=0 facing -y, the body's own far wall at y=0.3, and a
    collider-source sheet at y=1.0 facing -y (the other cheek's collider)."""
    sv, st, sn = _sheet(0.0, -1.0)
    wv, wt, wn = _sheet(0.3, +1.0)
    cv, ct, cn = _sheet(1.0, -1.0)
    v = np.vstack([sv, wv, cv])
    n = np.vstack([sn, wn, cn])
    k1, k2 = len(sv), len(sv) + len(wv)
    body_tris = np.vstack([st, wt + k1])
    rep_old = np.arange(k2, len(v))
    idx = np.arange(k1)
    off, at0, atN = ph._butt_col_enclosing_offset(v, n, rep_old, ct, idx, 0.0, 0.6)
    assert at0[0] > 0 and off == pytest.approx(0.6) and atN[0] > 0, (
        "control: without occlusion the far-side sheet reads as under the skin")
    off, at0, atN = ph._butt_col_enclosing_offset(
        v, n, rep_old, ct, idx, 0.0, 0.6, body_tris=body_tris)
    assert off == 0.0 and at0 == (0, 0.0)


def test_wired_after_the_derived_standoff_and_behind_its_switch():
    src = _cs.source(nc._add_butt_collider_patch)
    i_derive = src.index("derived from this piece's own rear cloth")
    i_enc = src.index("_butt_col_enclosing_offset(")
    i_build = src.index("pv = bv[rep_old] + bn[rep_old] * _off")
    assert i_derive < i_enc < i_build
    assert "if BUTT_COLLIDER_ENCLOSE:" in src
    assert "_cap = float(_BUTT_COL_OFFSET)" in src
    assert "body_tris=bt" in src, "the pass must test with body occlusion"


def test_switch_defaults_on_and_opts_out(monkeypatch):
    assert nc.BUTT_COLLIDER_ENCLOSE is True
    monkeypatch.setenv("CBBE2UBE_NO_BUTT_COLLIDER_ENCLOSE", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.BUTT_COLLIDER_ENCLOSE is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_BUTT_COLLIDER_ENCLOSE", raising=False)
        importlib.reload(nc)
