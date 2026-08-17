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

"""Body-through-garment penetration.

READ THIS BEFORE USING `surface_penetration`. Deciding inside/outside from the NEAREST
TRIANGLE'S NORMAL IS A KNOWN-BAD METRIC in this project and its numbers are void --
`docs/METRICS.md`, "Signed distance via the nearest triangle's normal - REPLACED". A garment
is a SHELL with an outer and an inner face; a body vertex sitting safely inside the cup
is near BOTH, so whichever face happens to be nearest decides the sign and the sign is
essentially arbitrary there. It once reported 135/1110 nipple verts outside a cuirass
whose surface was 2.4u in FRONT of them; the truth was 0.

Rebuilding it (2026-07-27) reproduced the same failure from the other direction: a
~20-30% "poking" floor in EVERY body region pack-wide, which is the coin-flip, not
geometry. Winding was clean (agreement 0.99) -- orientation was never the problem. The
shell is.

**Use `ray_exposure`.** March each body vertex along its own outward normal; if no
garment triangle blocks it, that vertex is visible from outside -- which is both
unambiguous by construction and the thing a player actually sees. That is the metric
`docs/METRICS.md` lists as sound, with positive controls.

`surface_penetration` is kept ONLY for the unsigned DISTANCE it returns (how far the
garment surface is), which is sound. Do not use its sign.

WHY THIS EXISTS. The obvious metric -- take each body vertex's NEAREST GARMENT VERTEX
and project the offset onto the body normal, negative = "poking" -- is invalid wherever
the garment does not touch the body. Where cloth hangs away (skirts, robe backs) the
nearest vertex sits at a grazing angle and the dot goes negative with ZERO penetration.

Measured on real output: verts that metric called "poking" at the butt averaged 4.09u
from the nearest garment vertex, and only 0.2% were within 1.0u. It was reporting
DRAPE. Breast numbers from it were fine (tight coverage), which is how a bogus
"breast 2.3% vs butt 13.7%" asymmetry survived long enough to justify building a
feature that was then reverted.

WHAT THIS DOES INSTEAD, per body vertex:
  * exact distance to the nearest POINT ON A TRIANGLE of the garment (not a vertex),
  * the SIDE it falls on, from that triangle's geometric normal,
  * a CONTACT GATE, so vertices the garment merely drapes over are not judged at all.

A vertex counts as poking only when it is on the outward side AND within `contact`
of the surface. Loose drape then reads as "not covered", which is the truth.

ORIENTATION. Triangle normals come from winding, which a NIF does not guarantee to be
outward. `surface_side` resolves the global sign by majority vote against the shape's
stored per-vertex normals, and reports the agreement so a caller can refuse an
ambiguous mesh rather than silently invert the result.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

__all__ = ["closest_point_on_triangles", "surface_penetration", "ray_exposure",
           "boundary_points", "classify_exposure"]


def boundary_points(verts, tris):
    """Vertices on an OPEN boundary -- endpoints of edges used by exactly one triangle.

    A garment is an open shell; its rim is the neckline, hem, armhole. Distance to
    that rim is what separates "skin shows because the garment ENDS here" from "skin
    shows through the middle of the garment".
    """
    from collections import defaultdict
    T = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    cnt = defaultdict(int)
    for tri in T:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            cnt[(a, b) if a < b else (b, a)] += 1
    idx = sorted({v for (a, b), c in cnt.items() if c == 1 for v in (a, b)})
    return np.asarray(verts, dtype=np.float64)[idx] if idx else np.zeros((0, 3))


def classify_exposure(exposed, surf_dist, rim_dist, near=2.0, rim=4.0):
    """Split exposed body vertices into POKE / NECKLINE / UNCOVERED.

    Exposure alone is a COVERAGE measure, not a defect measure -- a bikini is exposed
    by design. Even "exposed AND garment nearby" is not enough: at a neckline the
    garment IS nearby, just below the rim. Both conditions are required:

      poke      exposed, garment surface within `near`, AND more than `rim` from any
                open boundary -- garment all around it and the body coming through.
      neckline  exposed, garment nearby, but close to the rim -- the garment ends here.
      uncovered exposed with no garment nearby at all -- bare by design.

    Measured on 187 flagged armors: poke 0.4% mean, neckline 13.6%, uncovered 30.0%.
    Using rim distance ALONE scores a towel and a bra at 100% poke, because that
    distance is large both deep inside coverage and completely outside the garment.
    """
    near_m = np.asarray(surf_dist) < near
    far_rim = np.asarray(rim_dist) > rim
    e = np.asarray(exposed, dtype=bool)
    return (e & near_m & far_rim), (e & near_m & ~far_rim), (e & ~near_m)


def ray_exposure(origins, dirs, verts, tris, tmax=25.0, chunk=None):
    """True where the ray from origins[i] along dirs[i] hits NO triangle.

    The sound penetration test: a body vertex whose outward normal escapes without
    crossing the garment is visible from outside. Unambiguous by construction -- there
    is no sign to guess and no dependence on which face of a shell is nearest.

    Möller-Trumbore, batched over rays so a whole region is one set of array ops
    rather than a Python loop per vertex.

    `chunk` defaults to `_auto_chunk`, exactly as in `ray_first_hit`. It used to be
    a FIXED 2048, which is the failure `_auto_chunk` was written for and which this
    function never received: the (R,T,3) temporaries are R*T*24 bytes, so 2048 rays
    against a 60k-triangle garment -- one piece's shapes merged for a union cast --
    allocate 2.9 GB EACH, several at once. Measured: one worker at a 6.7 GB resident
    set, paging so hard the run made no visible progress in 16 minutes. Batching is
    over rays and every ray is independent, so this changes cost, never a result.
    """
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    O = np.asarray(origins, dtype=np.float64)
    D = np.asarray(dirs, dtype=np.float64)
    n = np.linalg.norm(D, axis=1, keepdims=True)
    D = np.divide(D, np.where(n > 1e-12, n, 1.0))
    if not len(T) or not len(O):
        return np.ones(len(O), dtype=bool)
    if chunk is None:
        chunk = _auto_chunk(len(T))

    a = V[T[:, 0]]
    e1 = V[T[:, 1]] - a
    e2 = V[T[:, 2]] - a
    out = np.zeros(len(O), dtype=bool)
    for s in range(0, len(O), chunk):
        o = O[s:s + chunk]
        d = D[s:s + chunk]
        p = np.cross(d[:, None, :], e2[None, :, :])            # (R,T,3)
        det = np.einsum("rtj,tj->rt", p, e1)
        ok = np.abs(det) > 1e-9
        inv = np.zeros_like(det)
        np.divide(1.0, det, out=inv, where=ok)
        t0 = o[:, None, :] - a[None, :, :]
        u = np.einsum("rtj,rtj->rt", t0, p) * inv
        q = np.cross(t0, e1[None, :, :])
        v = np.einsum("rtj,rj->rt", q, d) * inv
        t = np.einsum("rtj,tj->rt", q, e2) * inv
        hit = (ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
               & (t > 1e-4) & (t < tmax))
        out[s:s + chunk] = hit.any(axis=1)
    return ~out                                                # True = EXPOSED


def _auto_chunk(n_tris, budget_bytes=2.5e8):
    """Rays per batch that keep the (R,T,3) temporaries inside a memory budget.

    The inner arrays are R*T*3 float64 = R*T*24 bytes. A fixed chunk is fine for
    a 2k-triangle garment and allocates GIGABYTES against a 58k-triangle body,
    which is what the body-occlusion test below casts at. Scales the batch
    instead of the budget.
    """
    n_tris = max(1, int(n_tris))
    return int(np.clip(budget_bytes / (n_tris * 24.0), 16, 2048))


def ray_first_hit(origins, dirs, verts, tris, tmax=25.0, chunk=None, tmin=1e-4):
    """Distance to the NEAREST triangle hit along each ray; inf where none.

    `ray_exposure` answers "is there any hit", which cannot distinguish a garment
    sitting just under the skin from one hit on the FAR SIDE of the body after
    the ray has crossed the whole torso. Ordering the hits is what separates
    them, so this returns t rather than a bool.

    `tmin` ignores hits nearer than that. Needed when casting a body ray from a
    body vertex: the triangles sharing that vertex sit at t ~ 0 and would read as
    the far wall, rejecting everything. Raising the ORIGIN instead would silently
    drop genuine poke-throughs shallower than the offset, so the cut is made on
    the hit distance rather than on the origin.
    """
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    O = np.asarray(origins, dtype=np.float64)
    D = np.asarray(dirs, dtype=np.float64)
    nrm = np.linalg.norm(D, axis=1, keepdims=True)
    D = np.divide(D, np.where(nrm > 1e-12, nrm, 1.0))
    best = np.full(len(O), np.inf)
    if not len(T) or not len(O):
        return best
    if chunk is None:
        chunk = _auto_chunk(len(T))

    a = V[T[:, 0]]
    e1 = V[T[:, 1]] - a
    e2 = V[T[:, 2]] - a
    for s in range(0, len(O), chunk):
        o = O[s:s + chunk]
        d = D[s:s + chunk]
        p = np.cross(d[:, None, :], e2[None, :, :])
        det = np.einsum("rtj,tj->rt", p, e1)
        ok = np.abs(det) > 1e-9
        inv = np.zeros_like(det)
        np.divide(1.0, det, out=inv, where=ok)
        t0 = o[:, None, :] - a[None, :, :]
        u = np.einsum("rtj,rtj->rt", t0, p) * inv
        q = np.cross(t0, e1[None, :, :])
        v = np.einsum("rtj,rj->rt", q, d) * inv
        t = np.einsum("rtj,tj->rt", q, e2) * inv
        hit = (ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
               & (t > tmin) & (t < tmax))
        tt = np.where(hit, t, np.inf)
        best[s:s + chunk] = tt.min(axis=1)
    return best


def closest_point_on_triangles(pts, a, b, c):
    """Closest point on each triangle (a,b,c) to each point. Vectorised, exact.

    pts (N,3); a/b/c (N,3) -- one triangle per point (broadcast the candidates
    yourself). Standard region-based solve: vertex, edge, then interior.
    """
    ab, ac, ap = b - a, c - a, pts - a
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = pts - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = pts - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)

    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v_int = np.where(np.abs(denom) > 1e-20, vb / denom, 0.0)
        w_int = np.where(np.abs(denom) > 1e-20, vc / denom, 0.0)
    out = a + ab * v_int[:, None] + ac * w_int[:, None]

    # Region overrides. The interior formula above is the UNCLAMPED projection
    # onto the triangle's PLANE, so any region that fails to fire does not
    # degrade gracefully -- it returns a point that can be arbitrarily far
    # outside the triangle, and the caller reads that as a near-zero distance.
    # Measured on a shipped piece: a body vertex on the SHIN came back 0.098u
    # from a corset panel whose lowest vertex is 59u above it, because the
    # panel is near-planar and its plane runs down the front of the legs.
    #
    # Applied last-to-first of Ericson's sequence (a, b, ab, c, ac, bc,
    # interior) so that earlier regions win: `vertex c` must therefore be
    # written BEFORE `edge ab`, not after it.
    with np.errstate(divide="ignore", invalid="ignore"):
        w_ac = np.where((d2 - d6) != 0, d2 / (d2 - d6), 0.0)
        w_bc = np.where(((d4 - d3) + (d5 - d6)) != 0,
                        (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0)
        v_ab = np.where((d1 - d3) != 0, d1 / (d1 - d3), 0.0)

    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)         # edge bc
    out[m] = (b + (c - b) * w_bc[:, None])[m]
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)                       # edge ac
    out[m] = (a + ac * w_ac[:, None])[m]
    # `(d5 > 0) & (d6 >= d5)` here was WRONG and is the defect above: it is a
    # strict SUBSET of the real condition, so every vertex-c point with d5 <= 0
    # fell through to the plane projection.
    m = (d6 >= 0) & (d5 <= d6)                                  # vertex c
    out[m] = c[m]
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)                       # edge ab
    out[m] = (a + ab * v_ab[:, None])[m]
    m = (d3 >= 0) & (d4 <= d3)                                  # vertex b
    out[m] = b[m]
    m = (d1 <= 0) & (d2 <= 0)                                   # vertex a
    out[m] = a[m]
    return out


def surface_penetration(body_verts, garment_verts, garment_tris,
                        garment_normals=None, k=24, contact=1.5):
    """Signed distance from each body vertex to the garment surface.

    Returns (signed, dist, covered, agree):
      signed   (N,) +ve = body is on the OUTWARD side of the garment (poking),
                    -ve = body is inside the garment shell (correct).
      dist     (N,) unsigned distance to the nearest point on the surface.
      covered  (N,) bool, dist <= contact -- the gate. Judge ONLY these; far from an
               OPEN surface (a cuirass is not a closed shell) the nearest triangle is
               some hem and its sign is meaningless.

    NOTE ON `contact`: it bounds the measurable DEPTH as well as the coverage, because
    a vertex poking `d` through the surface is itself at distance `d`. Set it too tight
    and the deepest penetrations are gated OUT rather than reported -- at 1.5u every
    max-depth on a real pack pinned to exactly 1.49-1.50, which is the gate, not the
    geometry. Keep it comfortably above the deepest penetration worth seeing (5.0 is
    the census default) and treat `max_depth == contact` as truncated.
      agree    float in [0.5, 1.0], or None when the mesh ships no normals: how
               strongly triangle winding agreed with the stored normals after the
               global flip. Near 0.5 means the orientation is ambiguous and the SIGN
               should not be trusted -- refuse the mesh rather than report from it.

    `contact` is deliberately small. A garment 4u away is not containing that vertex
    in any meaningful sense, and counting it is precisely the bug this replaces.
    """
    gv = np.asarray(garment_verts, dtype=np.float64)
    tris = np.asarray(garment_tris, dtype=np.int64).reshape(-1, 3)
    bv = np.asarray(body_verts, dtype=np.float64)

    a, b, c = gv[tris[:, 0]], gv[tris[:, 1]], gv[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    ln = np.linalg.norm(fn, axis=1)
    keep = ln > 1e-12                       # drop degenerate triangles
    a, b, c, fn, tris = a[keep], b[keep], c[keep], fn[keep] / ln[keep, None], tris[keep]
    if not len(a):
        # EVERY triangle was degenerate, so nothing could be measured. The
        # signed distance returned here is ZERO, and zero on a penetration
        # metric reads as "flush against the body, no clipping" -- a caller
        # that looks only at the depth cannot tell this from a clean garment.
        # The distance channel is the tell: it comes back +inf, which no real
        # surface produces. CHECK `dist` (or the None in slot 4) before
        # trusting a zero depth out of this function.
        #
        # Left as zeros DELIBERATELY: this is the reference implementation
        # that `tests/test_fit_metrics_matches_reference.py` pins
        # `src/fit_metrics` against, so changing these return values would
        # move the anchor rather than fix a defect. The honest fix belongs in
        # the callers, which is why the tell is documented instead.
        z = np.zeros(len(bv))
        return z, np.full(len(bv), np.inf), np.zeros(len(bv), dtype=bool), None

    # Resolve winding->outward once, by majority vote against stored vertex normals.
    flip = 1.0
    agree = None
    if garment_normals is not None:
        gn = np.asarray(garment_normals, dtype=np.float64)
        if len(gn) == len(gv):
            vote = np.einsum("ij,ij->i", fn, gn[tris].mean(axis=1))
            agree = float((vote > 0).mean())
            if agree < 0.5:
                flip, agree = -1.0, 1.0 - agree
    fn = fn * flip

    cent = (a + b + c) / 3.0
    kk = min(k, len(cent))
    _d, idx = cKDTree(cent).query(bv, k=kk)
    if kk == 1:
        idx = idx[:, None]

    n = len(bv)
    best = np.full(n, np.inf)
    best_signed = np.zeros(n)
    rows = np.arange(n)
    for j in range(kk):
        t = idx[:, j]
        cp = closest_point_on_triangles(bv, a[t], b[t], c[t])
        off = bv - cp
        d = np.linalg.norm(off, axis=1)
        better = d < best
        if better.any():
            s = np.einsum("ij,ij->i", off, fn[t])
            best[better] = d[better]
            best_signed[better] = np.sign(s[better]) * d[better]
        _ = rows
    return best_signed, best, best <= float(contact), agree


# --- Resolution: what a delta has to beat to mean anything --------------------
#
# `ray_exposure` is a HARD boolean -- the ray hits a triangle or it does not. A body
# vert sitting on the coverage boundary therefore flips on hundredths of a unit, and a
# whole-census delta can be pure churn. Measured on one piece: jittering the garment by
# 0.010u flips ~11 verts (net +4.8) and by 0.020u flips ~12 (net +2.4) -- while a real
# code change under evaluation flipped 13 for a net of +3. That "regression" was
# indistinguishable from noise, and was nearly acted on as if it were a defect.
#
# So: never report a bare exposure delta. Use `exposure_with_margin` for a count that
# does not churn, and `noise_floor` to state the resolution alongside the number.

# Deterministic offsets, so a re-run reproduces exactly (a random jitter here would
# reintroduce the very instability this is meant to remove).
_MARGIN_OFFSETS = np.array([
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0], [0.0, -1.0, 0.0],
    [0.0, 0.0, 1.0], [0.0, 0.0, -1.0],
], dtype=np.float64)


def exposure_with_margin(origins, normals, verts, tris, *, margin=0.02, tmax=25.0):
    """Three-way exposure that survives a `margin`-sized perturbation.

    Returns (exposed, covered, ambiguous) boolean arrays. A vert is only called
    exposed or covered if the answer holds with the ray origin displaced by
    `margin` along each of six axes -- equivalent to perturbing the garment, since
    only relative motion matters. Verts whose answer flips are AMBIGUOUS and belong
    in neither count.

    Report `exposed` for a stable trend and `ambiguous` as the width of the band the
    trend is measured through. margin=0 reproduces plain `ray_exposure` exactly.
    """
    O = np.asarray(origins, dtype=np.float64)
    N = np.asarray(normals, dtype=np.float64)
    if margin <= 0.0:
        e = ray_exposure(O, N, verts, tris, tmax=tmax)
        return e, ~e, np.zeros(len(O), dtype=bool)
    hits = np.zeros(len(O), dtype=np.int64)
    for off in _MARGIN_OFFSETS:
        esc = ray_exposure(O + off * margin, N, verts, tris, tmax=tmax)
        hits += (~esc).astype(np.int64)
    n = len(_MARGIN_OFFSETS)
    covered = hits == n
    exposed = hits == 0
    return exposed, covered, ~(covered | exposed)


def noise_floor(origins, normals, verts, tris, *, amps=(0.005, 0.01, 0.02),
                trials=5, seed=0, tmax=25.0):
    """Resolution of a plain exposure count, by jittering the GARMENT.

    Returns {amp: {"flipped": mean, "net": mean}}. A reported delta smaller than the
    flip count at the amplitude of the change under test is not a result. Cheap
    enough to print next to every census number, which is the point.
    """
    rng = np.random.default_rng(seed)
    V = np.asarray(verts, dtype=np.float64)
    base = ray_exposure(origins, normals, V, tris, tmax=tmax)
    out = {}
    for amp in amps:
        fl, net = [], []
        for _ in range(trials):
            e = ray_exposure(origins, normals,
                             V + rng.normal(scale=amp, size=V.shape), tris, tmax=tmax)
            fl.append(int((e ^ base).sum()))
            net.append(int(e.sum()) - int(base.sum()))
        out[amp] = {"flipped": float(np.mean(fl)), "net": float(np.mean(net))}
    return out


# --- THE validated clipping test ----------------------------------------------
#
# FOUR REQUIREMENTS this test satisfies, each of which changed a conclusion in
# practice when an earlier metric ignored it (they outlived `poke_report`, the
# superseded statistic they were first written for):
#
# 1. ONE SHAPE IS NOT THE GARMENT. 49% of converted pieces render more than one
#    garment shape (Top+Skirt, Corset+dress, ...). Scoring a single shape counts
#    skin covered by its SIBLING as exposed. The union is the garment.
# 2. VERTS ARE NOT AREA. Body vertex density is not uniform, so a vert count
#    over-weights dense regions. The eye sees AREA; weight each vert by its
#    one-ring share.
# 3. A HARD SIGN TEST IS NOISE-DOMINATED. `signed > 0` counts +0.001u the same
#    as +1.5u, and a vert on the surface flips on rounding. This test is not a
#    sign test at all, which is why it does not churn.
# 4. A CONTACT GATE IS A CLIFF. A vert at 2.01u unjudged and at 1.99u judged
#    makes a figure that only exists at one gate look like a finding.
#
# Calibrated against user-supplied in-game ground truth on 2026-07-29: reads
# 0.0% on the armour the user reports CLEAN (hide CuirassLight) and 8.9% on the
# one they report CLIPPING (hide CuirassMedium). Any change to this function
# must preserve that separation or it is wrong.
#
# It replaced signed-distance (`surface_penetration`, still here because its
# DISTANCE is sound and the census uses it -- only its SIGN was bad) and the ray
# cone (`containment`, DELETED 2026-07-29 along with `poke_report`; see
# docs/METRICS.md for the census that discredited it). Both scored the CLEAN armour
# WORSE than the clipping one at every depth threshold, because neither can
# separate "skin is outside the garment SURFACE" from "skin is outside the
# garment's COVERAGE" -- so a small revealing garment scores terribly by
# design and the figure is dominated by rim geometry. They are ANTI-correlated
# with ground truth; no threshold rescues them.
#
# The question this asks instead is the one that defines clipping: IS THE
# GARMENT BEHIND THE SKIN? If a ray along the body's outward normal escapes but
# the ray along the INWARD normal hits garment, then the garment lies between
# the skin and the body interior -- the skin has come through it. Skin merely
# beside an open edge escapes in both directions and is simply uncovered.
# DEPTH BANDS. One clipping percentage conflates defects that need different
# fixes -- separating them by hand changed the conclusion every time it was
# done, and on one shipped cuirass it immediately split four "3-9% bust
# clipping" pieces into two that are 90% shallow poke-through and one that is
# 100% sub-0.2u.
#
# WHAT THE SHALLOW BAND IS NOT: z-fighting. That was the working theory when
# these bands were introduced (2026-08-01) and the ZOOM TEST FALSIFIED IT the
# same day -- the user reports the sub-0.2u hip clipping is equally visible with
# the camera right up against it, and z-fighting by definition is not. In
# hindsight the theory never held numerically either: 0.2u is ~3mm of real
# separation, orders of magnitude above depth-buffer precision at close range.
# So a vert at 0.06u is the body genuinely coming through the garment by a
# small amount, it is VISIBLE, and a clearance pass CAN act on it -- do not
# dismiss this band as cosmetic. See docs and CLIPPING_LOG entry R7.
#
# What the bands are for is the SIZE of the correction, which differs by more
# than an order of magnitude across them: the same hip band is 67% of verts
# under 0.2u (median 0.123u) and 18% over 1u (up to 4.02u). A single push
# budget cannot be right for both, and an average over them is right for
# neither.
#
# The edges are not tuned to make a result: 0.2u is comfortably above the
# ray-cast noise floor (0.005-0.02u jitter, see `noise_floor`) and comfortably
# below the smallest push any pass applies, and 1.0u is the standoff the
# clean-armour anchor sits at (median 1.15u on CuirassLight).
CLIP_COINCIDENT = 0.2
CLIP_BURIED = 1.0

# Body-occlusion cast parameters, MODULE-level because a second implementation
# of this gate exists (`standoff_audit.ClipTester.report`) and the two must not
# be able to pick different numbers. See that method for what happened when the
# gate lived in only one of them.
BODY_TMAX = 200.0
BODY_EPS = 0.05


def clipping_report(body_verts, body_tris, body_normals, garments, *,
                    tmax=5.0, mask=None, body_occlusion=True, eps=BODY_EPS,
                    body_tmax=BODY_TMAX):
    """Area-weighted covered / CLIPPING / uncovered for one armour.

    `garments`: list of (verts, tris) for every VISIBLE garment shape -- the
    union is the garment. `mask`: optional bool array of body verts to score.
    Percentages are of the masked skin AREA (vertex density is not uniform, so
    a vert count over-weights dense regions).

    `body_occlusion` (default ON) rejects an inward hit that lies BEYOND the
    body's own far wall. Without it the inward ray keeps going after leaving the
    body and reports whatever it meets on the other side as clipping -- measured
    on a shipped cuirass, 162 of 257 flagged hip verts (63%) were the skirt seen
    across the gap between the legs, at a median 3.22u through the body interior.
    A genuine poke-through is a fraction of a unit and is hit BEFORE any body
    wall. The same test on the upper back, where the body is thick, rejected
    NOTHING (0 of 38), which is what makes this a targeted fix and not a general
    suppression: it only fires where a ray can actually escape.

    Turn it off to reproduce pre-fix numbers. Anything recorded before
    2026-08-01 was measured without it and reads HIGH in thin-geometry regions
    (hip, inner thigh, armpit, between the breasts).

    `eps`: minimum hit distance for the BODY cast, so the origin's own triangles
    are not counted as the far wall.

    `body_tmax` is deliberately far larger than `tmax`. The garment is looked for
    within a few units; the far wall of a torso is a MEDIAN 12.8u away, so a body
    cast sharing tmax=5 found no wall for 2409 of 3602 hip verts and the gate sat
    inert on two thirds of the band. The two casts answer different questions and
    need different ranges.
    """
    bV = np.asarray(body_verts, dtype=np.float64)
    bT = np.asarray(body_tris, dtype=np.int64).reshape(-1, 3)
    n = np.asarray(body_normals, dtype=np.float64)
    n = n / np.clip(np.linalg.norm(n, axis=1, keepdims=True), 1e-9, None)
    idx = (np.flatnonzero(mask) if mask is not None
           else np.arange(len(bV), dtype=np.int64))

    a = bV[bT[:, 0]]
    tri_area = 0.5 * np.linalg.norm(
        np.cross(bV[bT[:, 1]] - a, bV[bT[:, 2]] - a), axis=1)
    va = np.zeros(len(bV))
    for k in range(3):
        np.add.at(va, bT[:, k], tri_area / 3.0)

    out_hit = np.zeros(len(idx), bool)
    in_hit = np.zeros(len(idx), bool)
    # Depth of the inward garment hit, kept in BOTH branches so the bands do not
    # exist only on the default path. `~ray_exposure` and `isfinite(first_hit)`
    # test the identical predicate over the identical bounds (t > 1e-4, t < tmax)
    # -- swapping one for the other returns the same `in_hit` and additionally
    # yields the distance, which the bool form throws away.
    t_gar = np.full(len(idx), np.inf)
    if not body_occlusion:
        for gv, gt in garments:
            out_hit |= ~ray_exposure(bV[idx], n[idx], gv, gt, tmax=tmax)
            t_gar = np.minimum(
                t_gar, ray_first_hit(bV[idx], -n[idx], gv, gt, tmax=tmax))
        in_hit = np.isfinite(t_gar)
    else:
        # Both casts share the TRUE origin so the distances are directly
        # comparable. Only the BODY cast skips the first `eps`, to step over the
        # origin's own triangles; the garment cast keeps full range so a shallow
        # poke-through is still seen.
        o_in = bV[idx]
        d_in = -n[idx]
        # Honoured as given, NOT clamped up to `tmax`: a silent override would
        # hide exactly the inert-gate failure this parameter exists to prevent.
        # Setting it below `tmax` disables the gate wherever the wall is further
        # than that, which is the caller's choice to make explicitly.
        t_body = ray_first_hit(o_in, d_in, bV, bT, tmax=body_tmax, tmin=eps)
        for gv, gt in garments:
            out_hit |= ~ray_exposure(bV[idx], n[idx], gv, gt, tmax=tmax)
            t_gar = np.minimum(
                t_gar, ray_first_hit(o_in, d_in, gv, gt, tmax=tmax))
        # Behind the SKIN, not behind the whole BODY.
        in_hit = np.isfinite(t_gar) & (t_gar < t_body)
    clip = in_hit & ~out_hit
    unc = ~in_hit & ~out_hit
    A = va[idx].sum()
    if A <= 0:
        return {"area": 0.0, "covered_pct": None, "clipping_pct": None,
                "uncovered_pct": None, "n_verts": len(idx),
                **depth_bands(np.empty(0), np.empty(0), 0.0)}
    return {
        "area": float(A),
        "covered_pct": float(100.0 * va[idx][out_hit].sum() / A),
        "clipping_pct": float(100.0 * va[idx][clip].sum() / A),
        "uncovered_pct": float(100.0 * va[idx][unc].sum() / A),
        "clip_verts": int(clip.sum()),
        "n_verts": len(idx),
        # Every clipping vert has a FINITE depth by construction -- `in_hit` is
        # exactly `isfinite(t_gar)` on both branches -- so the three bands
        # partition the clip set and their percentages sum to `clipping_pct`.
        **depth_bands(t_gar[clip], va[idx][clip], A),
    }


def depth_bands(depths, areas, total_area) -> dict:
    """Split a clipping set by how far behind the skin the garment sits.

    Separate because the empty case has to produce the SAME keys as the full
    one. A report that omits a key when a band is empty forces every consumer
    to guess whether a missing band means zero or means "this build predates
    the split", and one of those is a silent wrong answer.
    """
    d = np.asarray(depths, dtype=np.float64)
    a = np.asarray(areas, dtype=np.float64)
    coin = d < CLIP_COINCIDENT
    bur = d >= CLIP_BURIED
    sha = ~coin & ~bur
    pct = (lambda m: float(100.0 * a[m].sum() / total_area)
           if total_area > 0 else 0.0)
    return {
        "clip_coincident_pct": pct(coin),
        "clip_shallow_pct": pct(sha),
        "clip_buried_pct": pct(bur),
        "clip_coincident_verts": int(coin.sum()),
        "clip_shallow_verts": int(sha.sum()),
        "clip_buried_verts": int(bur.sum()),
        "clip_depth_median": float(np.median(d)) if len(d) else None,
        "clip_depth_p90": float(np.percentile(d, 90)) if len(d) else None,
        "clip_depth_max": float(d.max()) if len(d) else None,
    }
