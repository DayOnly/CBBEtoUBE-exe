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
`METRICS.md`, "Signed distance via the nearest triangle's normal - REPLACED". A garment
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
`METRICS.md` lists as sound, with positive controls.

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


def ray_exposure(origins, dirs, verts, tris, tmax=25.0, chunk=2048):
    """True where the ray from origins[i] along dirs[i] hits NO triangle.

    The sound penetration test: a body vertex whose outward normal escapes without
    crossing the garment is visible from outside. Unambiguous by construction -- there
    is no sign to guess and no dependence on which face of a shell is nearest.

    Möller-Trumbore, batched over rays so a whole region is one set of array ops
    rather than a Python loop per vertex.
    """
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    O = np.asarray(origins, dtype=np.float64)
    D = np.asarray(dirs, dtype=np.float64)
    n = np.linalg.norm(D, axis=1, keepdims=True)
    D = np.divide(D, np.where(n > 1e-12, n, 1.0))
    if not len(T) or not len(O):
        return np.ones(len(O), dtype=bool)

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

    # region overrides, applied last-to-first so earlier regions win
    with np.errstate(divide="ignore", invalid="ignore"):
        w_ac = np.where((d2 - d6) != 0, d2 / (d2 - d6), 0.0)
        w_bc = np.where(((d4 - d3) + (d5 - d6)) != 0,
                        (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0)
        v_ab = np.where((d1 - d3) != 0, d1 / (d1 - d3), 0.0)

    m = (va < 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)          # edge bc
    out[m] = (b + (c - b) * w_bc[:, None])[m]
    m = (vb < 0) & (d2 >= 0) & (d6 <= 0)                        # edge ac
    out[m] = (a + ac * w_ac[:, None])[m]
    m = (vc < 0) & (d1 >= 0) & (d3 <= 0)                        # edge ab
    out[m] = (a + ab * v_ab[:, None])[m]
    m = (d5 > 0) & (d6 >= d5)                                   # vertex c
    out[m] = c[m]
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


def cone_dirs(normals, half_deg=50.0, k=10):
    """k directions spread on a cone of `half_deg` about each normal."""
    n = np.asarray(normals, dtype=np.float64)
    n = n / np.linalg.norm(n, axis=1, keepdims=True)
    a = np.array([0.0, 0.0, 1.0])
    t = np.where((np.abs(n @ a) > 0.9)[:, None], np.array([1.0, 0.0, 0.0]), a)
    u = np.cross(n, t)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    v = np.cross(n, u)
    th = np.deg2rad(half_deg)
    return [n * np.cos(th) + (u * np.cos(ph) + v * np.sin(ph)) * np.sin(th)
            for ph in (2 * np.pi * i / k for i in range(k))]


def containment(exposed_pts, exposed_normals, verts, tris, half_deg=50.0, k=10):
    """DISCREDITED AND ORPHANED (2026-07-29). Do not use for fit work.

    Measured against user-supplied in-game ground truth, this scored the
    CONFIRMED-CLEAN armour WORSE than the one the user can see clipping
    (32.4% vs 23.9%), at every depth threshold -- ANTI-CORRELATED. It cannot
    separate "skin is outside the garment SURFACE" from "skin is outside the
    garment's COVERAGE", so a small revealing garment scores terribly by
    design and the number is dominated by rim geometry. An entire evening of
    parameter sweeps tuned against it was tuning noise.

    Superseded by `clipping_report` in this module (0.00% on that clean
    armour, 8.87% on the clipping one), and by `scripts/standoff_audit.py`
    which pairs it with the STANDOFF counter-metric -- necessary because
    clipping has no upper bound and cannot see an over-inflated garment.

    Call sites: NONE as of 2026-07-29 (`bust_verdict.py` was migrated off it).
    Kept, not deleted, only because this repo has a documented history of
    dead-code false positives -- verify before removing. If you are reaching
    for this, you almost certainly want `standoff_audit.clip_stats`.

    Original docstring follows.

    How much garment surrounds each exposed body vertex, as blocked/k.

    THE SOUND POKE TEST, and it replaces the rim-distance rule in
    `classify_exposure`, which is UNSOUND on boundary-heavy garments: that rule calls
    a vertex "neckline" when it is within a few units of the garment's open boundary,
    but a cuirass can have 61% of its vertices ON a boundary, so the rule can never
    return "poke" and reported 0.0% on armour the body was visibly coming through.

    This asks the actual question -- is there garment AROUND this skin -- by casting a
    cone of rays about the outward normal. Surrounded means the skin is inside the
    garment's coverage and is poking through it; nothing blocked means it is genuinely
    outside coverage (bare by design).
    """
    if not len(exposed_pts):
        return np.zeros(0, dtype=np.int64)
    blocked = np.zeros(len(exposed_pts), dtype=np.int64)
    for d in cone_dirs(exposed_normals, half_deg, k):
        blocked += (~ray_exposure(exposed_pts, d, verts, tris)).astype(np.int64)
    return blocked


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


# --- The refined statistic -----------------------------------------------------
#
# `surface_penetration` answers "is this body vert outside THIS garment surface".
# Four things stand between that and an accurate figure for "how much skin is
# coming through the armour", each of which changed a conclusion in practice:
#
# 1. ONE SHAPE IS NOT THE GARMENT. 49% of converted pieces render more than one
#    garment shape (Top+Skirt, Corset+dress, ...). Scoring a single shape counts
#    skin covered by its SIBLING as exposed. The union is the garment.
# 2. VERTS ARE NOT AREA. Body vertex density is not uniform, so a vert count
#    over-weights dense regions. The eye sees AREA; weight each vert by its
#    one-ring share.
# 3. A HARD SIGN TEST IS NOISE-DOMINATED. `signed > 0` counts +0.001u the same
#    as +1.5u, and a vert on the surface flips on rounding. Report a depth
#    distribution and a count above a depth that means something.
# 4. THE CONTACT GATE IS A CLIFF. A vert at 2.01u is unjudged, at 1.99u judged.
#    Report the figure at several gates so a number that only exists at one
#    gate is visible as such.
def poke_report(body_verts, body_tris, garments, *, contacts=(1.5, 2.0, 3.0),
                depth_min=0.05, mask=None):
    """SUPERSEDED AND ORPHANED (2026-07-29). Prefer `clipping_report`.

    Built on `surface_penetration`'s signed distance, which shares
    `containment`'s defect: it cannot tell "skin outside the garment SURFACE"
    from "skin outside the garment's COVERAGE". The 2026-07-29 bust probe was
    solved against this family of requirement and produced a mesh the user
    reported OVERINFLATED in game (standoff 2.88u where their clean armour
    sits at 1.15u) -- the metric's noise baked into geometry.

    Use `clipping_report` (validated 0.00% clean / 8.87% clipping on the
    user's own ground-truth pair) and pair it with the standoff check in
    `scripts/standoff_audit.py`, because clipping alone has NO UPPER BOUND and
    scores a garment three units too large as perfect.

    Call sites: NONE as of 2026-07-29. Kept rather than deleted per this
    repo's dead-code false-positive history -- verify before removing.

    Original docstring follows.

    Area-weighted skin-through-armour statistic against the WHOLE garment.

    `garments`: list of (verts, tris, normals|None) -- every VISIBLE garment
    shape of the piece. A body vert inside ANY of them is covered.
    `mask`: optional bool array selecting the body verts to report on.
    Returns {contact: {...}} plus 'area_total'.
    """
    bV = np.asarray(body_verts, dtype=np.float64)
    bT = np.asarray(body_tris, dtype=np.int64).reshape(-1, 3)
    sel = np.ones(len(bV), bool) if mask is None else np.asarray(mask, bool)

    # per-vert area = a third of each incident triangle (one-ring share)
    a = bV[bT[:, 0]]
    tri_area = 0.5 * np.linalg.norm(
        np.cross(bV[bT[:, 1]] - a, bV[bT[:, 2]] - a), axis=1)
    vert_area = np.zeros(len(bV))
    for k in range(3):
        np.add.at(vert_area, bT[:, k], tri_area / 3.0)

    # nearest surface across ALL garment shapes; inside any -> covered
    best_d = np.full(len(bV), np.inf)
    best_s = np.full(len(bV), np.inf)
    inside_any = np.zeros(len(bV), bool)
    for gv, gt, gn in garments:
        s, d, _cov, agree = surface_penetration(
            bV, gv, gt, garment_normals=gn, contact=float(max(contacts)))
        if agree is not None and agree < 0.7:
            continue            # orientation ambiguous -> its sign is unusable
        closer = d < best_d
        best_d = np.where(closer, d, best_d)
        best_s = np.where(closer, s, best_s)
        inside_any |= (s < 0) & (d <= float(max(contacts)))

    out = {"area_total": float(vert_area[sel].sum())}
    for c in contacts:
        judged = sel & (best_d <= c)
        poking = judged & (best_s > 0) & ~inside_any
        deep = poking & (best_s > depth_min)
        ja = vert_area[judged].sum()
        out[c] = {
            "judged_verts": int(judged.sum()),
            "judged_area": float(ja),
            "poke_area_pct": float(100.0 * vert_area[poking].sum() / ja) if ja else None,
            "poke_vert_pct": float(100.0 * poking.sum() / max(1, judged.sum())),
            "deep_area_pct": float(100.0 * vert_area[deep].sum() / ja) if ja else None,
            "depth_p50": float(np.percentile(best_s[poking], 50)) if poking.any() else 0.0,
            "depth_p90": float(np.percentile(best_s[poking], 90)) if poking.any() else 0.0,
            "depth_max": float(best_s[poking].max()) if poking.any() else 0.0,
        }
    return out


# --- THE validated clipping test ----------------------------------------------
#
# Calibrated against user-supplied in-game ground truth on 2026-07-29: reads
# 0.0% on the armour the user reports CLEAN (hide CuirassLight) and 8.9% on the
# one they report CLIPPING (hide CuirassMedium). Any change to this function
# must preserve that separation or it is wrong.
#
# It replaces signed-distance (`surface_penetration`) and the ray cone
# (`containment`) for health questions. Both of those scored the CLEAN armour
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
def clipping_report(body_verts, body_tris, body_normals, garments, *,
                    tmax=5.0, mask=None):
    """Area-weighted covered / CLIPPING / uncovered for one armour.

    `garments`: list of (verts, tris) for every VISIBLE garment shape -- the
    union is the garment. `mask`: optional bool array of body verts to score.
    Percentages are of the masked skin AREA (vertex density is not uniform, so
    a vert count over-weights dense regions).
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
    for gv, gt in garments:
        out_hit |= ~ray_exposure(bV[idx], n[idx], gv, gt, tmax=tmax)
        in_hit |= ~ray_exposure(bV[idx], -n[idx], gv, gt, tmax=tmax)
    clip = in_hit & ~out_hit
    unc = ~in_hit & ~out_hit
    A = va[idx].sum()
    if A <= 0:
        return {"area": 0.0, "covered_pct": None, "clipping_pct": None,
                "uncovered_pct": None, "n_verts": len(idx)}
    return {
        "area": float(A),
        "covered_pct": float(100.0 * va[idx][out_hit].sum() / A),
        "clipping_pct": float(100.0 * va[idx][clip].sum() / A),
        "uncovered_pct": float(100.0 * va[idx][unc].sum() / A),
        "clip_verts": int(clip.sum()),
        "n_verts": len(idx),
    }
