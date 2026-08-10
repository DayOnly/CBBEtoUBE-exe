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

"""STANDOFF ASSERTION -- how far off the body a garment ends up.

WHY. The converter's push passes (`clear_armor_outside_body`,
`inflate_armor_outward`, `_inflate_cloth_over_bust_butt`,
`conform_to_source_standoff`, adaptive clearance) are each individually bounded
by their own `max_push`. A cap bounds what ONE pass adds; it does not bound
where the vertex ends up, and several of them run in sequence over the same
verts. Individually bounded, jointly unbounded -- and nothing measured the
result. A bust probe tuned against clipping alone was reported IN GAME as
overinflated, twice, because clipping has no upper bound: leather three units
too far off the body scores a perfect 0.0%.

This measures the finished geometry, after the whole push stack.

CALIBRATION -- the mask is fixed IN THIS FILE, not by the caller, which is what
makes a constant ceiling comparable across pieces. Measured on the bust front
(z 90-102, y > 2) over covered skin only:

    Hide\\F\\CuirassLight_1.nif   user-confirmed CLEAN and correctly fitted
                                  median 1.15u   p90 1.52u   clipping 0.00%
    Hide\\F\\CuirassMedium_1.nif  shipped, clips 8.87%
                                  median 0.34u   p90 0.83u
    the overinflated probe        median 2.88u   p90 3.84u   clipping 0.21%

The clean armour is the reference; the ceilings sit above it with headroom, so
a normally-fitted piece never trips and a ballooned one always does.

MEASURE ONLY. Nothing here changes geometry. It records, so that a regression
is visible in the output rather than inferred from a log -- see the sink note
below.

THE SINK IS A FILE, NOT A PRINT. The converter fans NIF work across a process
pool, and in the frozen WINDOWED exe a worker's stdout can be None, in which
case `print()` is silently discarded. Every WARN emitted from a worker may
therefore never reach the run log. Records go to a JSONL beside the output so
`scripts/postflight_1_2.py` (check E) and any later audit can read them.
EXCEPTIONS ARE RECORDED TOO: a measurement that fails must not look the same as
one that found nothing.

Off with CBBE2UBE_NO_STANDOFF_AUDIT=1.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

# The bust front, over covered skin. Fixed here on purpose: standoff is a
# distribution over whichever skin the mask selects, so a ceiling is only
# meaningful against a mask that cannot drift between callers.
BAND_Z = (90.0, 102.0)
BAND_Y_MIN = 2.0
TMAX = 12.0            # deliberately past the clip test's 5u: a ballooned
                       # garment sits further out than that, and truncating
                       # there would hide exactly what this is for
# Ray-cast tuning. All three are EXACT optimisations -- they change how fast a
# measurement runs, never its verdict -- so they are env-toggleable only so a
# suspected regression can be bisected against the slow path.
TIER_PCTS = (50.0, 90.0, 99.0)   # triangle-radius buckets for the ball query
CAST_CULL = os.environ.get("CBBE2UBE_NO_CAST_CULL") != "1"
CAST_CULL_MIN = 4096   # below this the cull costs more than the test it saves
# Rays are cast in CHUNKS. `_pairs` emits one row per (ray, triangle) candidate,
# so cost scales with rays x candidates -- on a dense garment that reached
# 36,061,621 pairs and a MemoryError trying to allocate 825 MiB for one array.
# The sparse formulation is far cheaper than the dense one it replaced, but it
# was never BOUNDED, which is what calling it "memory-safe" wrongly implied.
# Chunking cannot change a verdict: every ray is independent, so a chunk
# boundary is invisible to the result.
RAY_CHUNK = int(os.environ.get("CBBE2UBE_RAY_CHUNK", "512"))

MIN_HITS = 40          # below this the garment does not cover the bust and
                       # the distribution is a handful of stray rays
CEIL_MEDIAN = float(os.environ.get("CBBE2UBE_STANDOFF_CEIL_MEDIAN", "1.60"))
CEIL_P90 = float(os.environ.get("CBBE2UBE_STANDOFF_CEIL_P90", "2.20"))


def _enabled() -> bool:
    return os.environ.get("CBBE2UBE_NO_STANDOFF_AUDIT") != "1"


def standoff(body_verts, body_normals, garment_verts, garment_tris,
             idx, tmax: float = TMAX, chunk: int = 512):
    """Distance along +normal from each body vert in `idx` to the garment.

    Möller-Trumbore, batched. Returns only the finite hits -- a ray that
    escapes means that skin is not covered there, which is not a fit fault.
    """
    V = np.asarray(garment_verts, np.float64)
    T = np.asarray(garment_tris, np.int64).reshape(-1, 3)
    if not len(T) or not len(idx):
        return np.empty(0)
    O = np.asarray(body_verts, np.float64)[idx]
    D = np.asarray(body_normals, np.float64)[idx]
    D = D / np.clip(np.linalg.norm(D, axis=1, keepdims=True), 1e-9, None)
    a = V[T[:, 0]]
    e1 = V[T[:, 1]] - a
    e2 = V[T[:, 2]] - a
    out = np.full(len(O), np.inf)
    for s in range(0, len(O), chunk):
        o, d = O[s:s + chunk], D[s:s + chunk]
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
               & (t > 1e-4) & (t < tmax))
        tt = np.where(hit, t, np.inf)
        out[s:s + chunk] = tt.min(axis=1)
    return out[np.isfinite(out)]


# ---------------------------------------------------------------- CLIP TEST
# The validated skin-through-armour test, ported into src/ so a conversion pass
# can use it (src must not import from scripts/). The authority on its
# correctness is `tests/test_fit_metrics_matches_reference.py`, which asserts
# equality against `scripts/mesh_penetration.clipping_report` on the calibration
# pair -- a runtime import would be the wrong coupling, a test is the right one.
#
# ORIENTATION GATE is ON for the pass. The base test calls a body vert clipping
# when its outward ray escapes and its inward ray hits garment within tmax. That
# is sound at bind, and develops a false positive under a large morph: a
# downward sag carries skin past the cut rim of a cup and the inward ray strikes
# the FAR SIDE of the garment from inside. Requiring the hit triangle to face
# the same way as the skin removes that without moving the calibration pair
# (all 640 real hits on the shipped mesh face the same way).
class _ClipTester:
    """Ray test against one garment, reusable as that garment moves.

    Three exact optimisations, each verified to leave the verdict unchanged:
      * the inward ray is cast ONLY where the outward ray escaped -- it cannot
        change the verdict elsewhere, and typical coverage exceeds 90%;
      * candidates are pruned by each triangle's OWN centroid radius (a
        mesh-wide maximum lets a few large triangles inflate the search ball for
        every ray -- that version measured only 1.3x);
      * the triangle index is rebuilt by `set_garment`, not per call.
    Measured 4.5x on one report and 3.7x on a six-iteration solve.
    """

    def __init__(self, gV, gT, tmax: float = 5.0):
        self.gT = np.asarray(gT, np.int64).reshape(-1, 3)
        self.tmax = float(tmax)
        self.set_garment(gV)

    def set_garment(self, gV) -> None:
        self.gV = np.asarray(gV, np.float64)
        tv = self.gV[self.gT]
        self.cent = tv.mean(axis=1)
        self.trad = np.linalg.norm(tv - self.cent[:, None, :], axis=2).max(1)
        self.ctree = cKDTree(self.cent)
        self.reach = float(self.tmax + self.trad.max())
        self._fn = None
        self._tiers = None

    def tiers(self):
        """Triangles bucketed by radius, each with its own search ball.

        A single large triangle sets `reach` for the WHOLE mesh (6.2u of radius
        on top of a 5u ray here, so every ray searched 11.2u to find candidates
        that are then thrown away). Bucketing by radius lets the small
        triangles -- the overwhelming majority -- be queried at a much tighter
        radius. Exact: the union of the buckets is every triangle, and each is
        still filtered by its own radius afterwards.
        """
        if self._tiers is None:
            tiers = []
            if len(self.trad):
                edges = np.unique(np.concatenate(
                    ([0.0], np.percentile(self.trad, TIER_PCTS), [np.inf])))
                lo = -np.inf
                for hi in edges[1:]:
                    sel = np.flatnonzero((self.trad > lo) & (self.trad <= hi))
                    lo = hi
                    if len(sel):
                        tiers.append((sel, cKDTree(self.cent[sel]),
                                      float(self.tmax + self.trad[sel].max())))
            self._tiers = tiers
        return self._tiers

    def face_normals(self):
        if self._fn is None:
            a = self.gV[self.gT[:, 0]]
            n = np.cross(self.gV[self.gT[:, 1]] - a, self.gV[self.gT[:, 2]] - a)
            self._fn = n / np.clip(np.linalg.norm(n, axis=1, keepdims=True),
                                   1e-12, None)
        return self._fn

    def _pairs(self, O):
        """Candidate (ray, triangle) pairs. Was 61% of a measurement.

        `query_ball_point` hands back a list of Python lists; turning 5249 of
        them into index arrays cost more than the ray casting itself.
        `sparse_distance_matrix(output_type='ndarray')` does the same search
        entirely in C and returns the centre distance we were recomputing with
        a separate norm over 676k pairs. Measured 4.0x, and combined with the
        radius tiers 7.0x, with a bit-identical pair set both times.
        """
        O = np.asarray(O, np.float64)
        if not len(O) or not len(self.gT):
            return np.empty(0, np.int64), np.empty(0, np.int64)
        otree = cKDTree(O)
        ri, ti = [], []
        for sel, ktree, reach in self.tiers():
            m = otree.sparse_distance_matrix(ktree, reach,
                                             output_type="ndarray")
            if not len(m):
                continue
            # copy=False: the sparse-matrix indices already come back as a
            # platform int, so this is a no-op on Windows rather than a full
            # copy of every candidate pair.
            a = m["i"].astype(np.int64, copy=False)
            b = sel[m["j"].astype(np.int64, copy=False)]
            keep = m["v"] <= self.tmax + self.trad[b]
            if keep.any():
                ri.append(a[keep])
                ti.append(b[keep])
        if not ri:
            return np.empty(0, np.int64), np.empty(0, np.int64)
        return np.concatenate(ri), np.concatenate(ti)

    def _cast(self, O, D, ray_i, tri_i, n_rays, want_tri=False):
        out = np.full(n_rays, np.inf)
        who = np.full(n_rays, -1, np.int64)
        if not len(ray_i):
            return (out, who) if want_tri else out
        V, T = self.gV, self.gT
        # RAY-LINE CULL. A ray along unit D can only hit a triangle whose
        # centroid lies within that triangle's own radius of the ray LINE, so
        # one cross product rejects what the full intersection test would spend
        # far more to reject. The ball query is centred on the ray ORIGIN and so
        # keeps everything in a sphere; most of that sphere is nowhere near the
        # ray. Measured: 4.0% of pairs survive, and _cast runs 2.0x faster with
        # bit-identical hits. Conservative -- it can only drop pairs that could
        # not have intersected.
        if CAST_CULL and len(ray_i) > CAST_CULL_MIN:
            d_r = D[ray_i]
            w = self.cent[tri_i] - O[ray_i]
            # SQUARED form, via the Lagrange identity:
            #     |w x d|^2 == |w|^2 |d|^2 - (w.d)^2
            # The test is |w x d| / |d| <= trad, and both sides are
            # non-negative, so squaring preserves it exactly. This replaces a
            # cross product and two norms -- each allocating an (n,3) temporary
            # and a sqrt over EVERY candidate pair, before 96% of them are
            # rejected -- with three dot products over (n,) arrays.
            #
            # Still divides by |d| rather than assuming 1: every caller today
            # passes unit normals, but an unnormalised direction would shrink
            # the cull radius and drop real hits, and a MISSED hit reads as "no
            # clipping", which no downstream number could tell from a garment
            # that genuinely does not clip.
            #
            # Cancellation in `ww*dd - wd*wd` is harmless HERE: it only bites
            # when w is nearly parallel to d, which is the small-perp case that
            # is kept anyway. Erring small can only admit a pair, never drop one.
            ww = np.einsum("ij,ij->i", w, w)
            dd = np.einsum("ij,ij->i", d_r, d_r)
            wd = np.einsum("ij,ij->i", w, d_r)
            perp2 = ww * dd - wd * wd
            lim = self.trad[tri_i] * np.sqrt(np.where(dd > 1e-24, dd, 1.0)) + 1e-9
            k = perp2 <= lim * lim
            ray_i, tri_i = ray_i[k], tri_i[k]
            if not len(ray_i):
                return (out, who) if want_tri else out
        a = V[T[tri_i, 0]]
        e1 = V[T[tri_i, 1]] - a
        e2 = V[T[tri_i, 2]] - a
        d = D[ray_i]
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", p, e1)
        ok = np.abs(det) > 1e-9
        inv = np.zeros_like(det)
        np.divide(1.0, det, out=inv, where=ok)
        t0 = O[ray_i] - a
        u = np.einsum("ij,ij->i", t0, p) * inv
        q = np.cross(t0, e1)
        v = np.einsum("ij,ij->i", q, d) * inv
        t = np.einsum("ij,ij->i", q, e2) * inv
        hit = (ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
               & (t > 1e-4) & (t < self.tmax))
        np.minimum.at(out, ray_i[hit], t[hit])
        if want_tri:
            sel = hit & (t <= out[ray_i] + 1e-12)
            who[ray_i[sel]] = tri_i[sel]
            return out, who
        return out

    def clipping(self, bV, bN, idx, oriented: bool = True, chunk: int = 0):
        """(clip_mask, in_t) over `idx`. clip = out escapes AND in hits.

        CHUNKED. Rays are independent, so splitting them is identical to casting
        all at once while bounding peak memory. Unchunked, a dense garment
        raised MemoryError in here and `exposed()` swallowed it into a -1, which
        the chain contract reported as "unmeasurable" -- the only trace that a
        shape had lost its diagnosis entirely. Pass chunk<0 to force one batch.
        """
        idx = np.asarray(idx)
        _n = chunk if chunk else RAY_CHUNK
        if _n > 0 and len(idx) > _n:
            masks, ts = [], []
            for _i in range(0, len(idx), _n):
                _m, _t = self.clipping(bV, bN, idx[_i:_i + _n], oriented,
                                       chunk=-1)
                masks.append(_m)
                ts.append(_t)
            return np.concatenate(masks), np.concatenate(ts)
        O, N = np.asarray(bV)[idx], np.asarray(bN)[idx]
        ray_i, tri_i = self._pairs(O)
        o = self._cast(O, N, ray_i, tri_i, len(O))
        i_t = np.full(len(O), np.inf)
        who = np.full(len(O), -1, np.int64)
        esc = ~np.isfinite(o)
        if esc.any() and len(ray_i):
            sel = esc[ray_i]
            eidx = np.flatnonzero(esc)
            remap = np.full(len(O), -1, np.int64)
            remap[eidx] = np.arange(len(eidx))
            i_t[esc], who[esc] = self._cast(
                O[esc], -N[esc], remap[ray_i[sel]], tri_i[sel], len(eidx),
                want_tri=True)
        same = np.ones(len(idx), bool)
        if oriented:
            fn = self.face_normals()
            same = np.zeros(len(idx), bool)
            g = who >= 0
            same[g] = np.einsum("ij,ij->i", fn[who[g]], N[g]) > 0.0
        return (np.isfinite(i_t) & ~np.isfinite(o) & same), i_t


def _rim_edges(T):
    """Edges used by exactly one triangle -- the garment's own cut boundary."""
    ecount = {}
    for t in np.asarray(T, np.int64).reshape(-1, 3):
        for a_, b_ in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            k = (min(int(a_), int(b_)), max(int(a_), int(b_)))
            ecount[k] = ecount.get(k, 0) + 1
    return np.array([k for k, v in ecount.items() if v == 1], np.int64)


def _rim_distance(bV, gV, rim_e, chunk: int = 2048):
    if not len(rim_e):
        return np.full(len(bV), np.inf)
    a, b = gV[rim_e[:, 0]], gV[rim_e[:, 1]]
    ab = b - a
    L = np.clip(np.einsum("mj,mj->m", ab, ab), 1e-12, None)
    out = np.full(len(bV), np.inf)
    for s in range(0, len(bV), chunk):
        p = bV[s:s + chunk]
        ap = p[:, None, :] - a[None, :, :]
        t = np.clip(np.einsum("nmj,mj->nm", ap, ab) / L, 0.0, 1.0)
        q = a[None, :, :] + t[:, :, None] * ab[None, :, :]
        out[s:s + chunk] = np.linalg.norm(p[:, None, :] - q, axis=2).min(1)
    return out


def band_index(body_verts) -> np.ndarray:
    bV = np.asarray(body_verts, np.float64)
    z = bV[:, 2]
    return np.flatnonzero((z >= BAND_Z[0]) & (z <= BAND_Z[1])
                          & (bV[:, 1] > BAND_Y_MIN))


# ------------------------------------------------------------ MINIMUM PUSH
# Region: front + side + underside of the bust, NOT the back. Derived, not
# guessed:
#   * front-only (what the first build used) left the UNDERSIDE untreated, and
#     the underside is exactly where a reported in-game clip appeared;
#   * z reaches only 4u below the breast band. At 8u below, the nearest garment
#     vertex is already 4.6-5.4u away -- at the ray's own reach -- so those are
#     not pokes, they are skin near the edge of where thin fabric reaches.
#     Pushing there fixed 0 of 49 and created 73 NEW pokes on neighbours;
#   * back is excluded on purpose: it was never a reported defect and the
#     original gap probe measured it trading +2 points on the back for -13 on
#     the front.
# The three gates are env-overridable so the front/back trade recorded above can
# be RE-MEASURED without editing source. Defaults are unchanged, so the shipped
# region is exactly what it was: front + side + underside of the bust, z 86-102.
# The "back excluded" decision rests on two premises and one has since expired --
# it IS now a reported defect (upper back, z 95-112, where half the covered skin
# measured under 0.5u of clearance). The other premise, that including the back
# cost the FRONT 13 points to gain 2 on the back, is still live and is why any
# re-measurement must treat the FRONT as a hard counter-metric.
PUSH_Z_LO = float(os.environ.get("CBBE2UBE_PUSH_Z_LO", "") or BAND_Z[0] - 4.0)
PUSH_Z_HI = float(os.environ.get("CBBE2UBE_PUSH_Z_HI", "") or BAND_Z[1])
PUSH_Y_MIN = float(os.environ.get("CBBE2UBE_PUSH_Y_MIN", "") or -2.0)
# Skin within this of the garment's own cut edge is EXCLUDED. On the
# user-confirmed CLEAN armour, 35 of 35 flagged underside verts sat within
# 1.33u of a hem (median 0.76u) -- hem-adjacency noise, not a defect, and
# pushing one such vert can flip a neighbour's equally-noisy reading the other
# way. This is the check that took the clean-armour control from 37 verts moved
# to 0.
PUSH_RIM_MARGIN = float(os.environ.get("CBBE2UBE_PUSH_RIM_MARGIN", "2.0"))
# A body vert whose nearest garment vertex is further than this cannot be fixed
# by moving a vertex; attempting it perturbs sparse mesh and creates new pokes.
PUSH_MAX_REACH = float(os.environ.get("CBBE2UBE_PUSH_MAX_REACH", "3.0"))
PUSH_ITERS = int(os.environ.get("CBBE2UBE_PUSH_ITERS", "8"))
PUSH_DAMP = float(os.environ.get("CBBE2UBE_PUSH_DAMP", "0.5"))
PUSH_KNN = int(os.environ.get("CBBE2UBE_PUSH_KNN", "3"))
PUSH_MARGIN = float(os.environ.get("CBBE2UBE_PUSH_MARGIN", "0.05"))
PUSH_MAX_TOTAL = float(os.environ.get("CBBE2UBE_PUSH_MAX_TOTAL", "2.00"))
# A single vert's requirement is capped: anything demanding more is the
# unreachable cut-rim tail, and chasing it produced a 12.83u spike once.
PUSH_REQ_CAP = float(os.environ.get("CBBE2UBE_PUSH_REQ_CAP", "1.50"))
PUSH_MIN_REGION = 50          # too little covered skin to judge
PUSH_ENABLED = os.environ.get("CBBE2UBE_NO_MIN_PUSH") != "1"
# NO neighbour-smoothing of the push field. Measured: with one smoothing pass a
# residual set plateaus and oscillates without ever being reached; without it,
# convergence is monotonic. The smoothing came from the follow-WEIGHT feather,
# where spreading a weight is right; here it smears a per-vert push REQUIREMENT
# sideways onto verts that do not need it and dilutes the ones that do.


def choose_aligned(candidates, body_verts, region_mask):
    """Pick whichever candidate garment-vert array actually sits ON the body.

    WHY THIS IS NEEDED, measured not guessed. `shape_body_offset` adds a shape's
    `NiAVObject.transform.translation` to its verts so warp/conform maths runs in
    body space. That is right for a shape authored in a shifted space, and WRONG
    for a skinned shape whose verts are already in body space and whose transform
    is inert at render time. One real cuirass carries translation [-40, 0, 0]
    with an IDENTITY global_to_skin and verts already correctly placed
    (x -23.3..19.9); adding the offset moves it to x -63.3..-20.1, i.e. 40 units
    off the body. Measured reach from bust skin to that garment: median 29.6u,
    against 1.6u for a sibling piece whose offset is zero.

    Rather than guess at NIF semantics, this asks the geometry: the candidate
    whose nearest-vertex distance to the region's skin is smallest is the one in
    the body's frame. Self-correcting, and it cannot mis-handle a shape that
    genuinely needs the offset -- for that shape the offset candidate is the
    closer one and wins.

    Returns (label, verts, median_reach).
    """
    bV = np.asarray(body_verts, np.float64)
    idx = np.flatnonzero(region_mask)
    if not len(idx):
        label, V = candidates[0]
        return label, V, float("inf")
    best = None
    for label, V in candidates:
        V = np.asarray(V, np.float64)
        if V.ndim != 2 or len(V) < 3:
            continue
        reach, _ = cKDTree(V).query(bV[idx], k=1)
        med = float(np.median(reach))
        if best is None or med < best[2]:
            best = (label, V, med)
    if best is None:
        label, V = candidates[0]
        return label, np.asarray(V, np.float64), float("inf")
    return best


def frame_report(shape_verts, offset, body_verts) -> dict:
    """PRECONDITION for every body-space fit pass: is this shape in the body's
    frame at all?

    Twelve phase-2 passes (bake_preset, warp, inflate, conform, groove smooth,
    snap, anti-poke, softcloth, ...) all compute against the body and all ASSUME
    the garment has been placed in body space. Nothing asserted it. When that
    assumption broke -- one cuirass with transform translation [-40,0,0] and an
    identity global_to_skin, so the offset displaced it 40u -- all twelve
    silently computed against a garment that was not where the body is, and the
    piece shipped at 8.87% bust clipping. It took a pass with a reach gate
    (`minimum_push`) to notice, by accident of design rather than policy.

    This makes it policy: report the frame BEFORE the passes run, and say so
    when the chosen frame is not the raw one. Cheap (two KD queries), and the
    record is what turns "some pieces mysteriously resist fitting" into a
    grep-able list.

    Reports rather than raises: a frame anomaly is a diagnostic, not a reason to
    fail a user's conversion, and `shape_body_offset` already corrects the case
    it can prove.
    """
    out = {"raw_reach": None, "offset_reach": None, "chosen": "raw",
           "corrected": False, "offset": None, "suspect": False}
    try:
        v = np.asarray(shape_verts, np.float64)
        bv = np.asarray(body_verts, np.float64)
        off = np.asarray(offset, np.float64).reshape(3)
        if v.ndim != 2 or len(v) < 3 or bv.ndim != 2 or len(bv) < 3:
            return out
        out["offset"] = [round(float(x), 2) for x in off]
        tree = cKDTree(bv)
        d_raw = float(np.median(tree.query(v, k=1)[0]))
        out["raw_reach"] = round(d_raw, 3)
        if not np.any(np.abs(off) > 1e-6):
            out["chosen"] = "raw (no offset)"
            return out
        d_off = float(np.median(tree.query(v + off, k=1)[0]))
        out["offset_reach"] = round(d_off, 3)
        if d_off <= d_raw + 0.5:
            out["chosen"] = "offset"          # offset brings it onto the body
        else:
            out["chosen"] = "raw"
            out["corrected"] = True           # offset would have displaced it
        # Neither frame near the body: not necessarily wrong (a hat, a cape hem,
        # a weapon) but worth recording, because it is also what a genuine frame
        # error looks like and nothing else in the chain will say so.
        if min(d_raw, d_off) > 10.0:
            out["suspect"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def push_region_mask(body_verts) -> np.ndarray:
    """The band this pass is licensed to touch (front + side + underside)."""
    bV = np.asarray(body_verts, np.float64)
    z = bV[:, 2]
    return (z >= PUSH_Z_LO) & (z <= PUSH_Z_HI) & (bV[:, 1] > PUSH_Y_MIN)


def minimum_push(garment_verts, garment_tris, garment_normals,
                 body_verts, body_tris, body_normals, is_chain=None):
    """Smallest outward move that clears measured skin-through-armour.

    NOT "push until nothing clips". That objective has no upper bound -- a
    garment three units too far off the body scores a perfect 0.0% -- and a
    probe tuned against it alone was reported in game as OVERINFLATED twice.
    Every vert here is moved by the measured deficit plus a small margin and
    nothing more, and the result is reported so the standoff assertion can
    catch it if it ever drifts.

    CONDITIONAL BY CONSTRUCTION. A pack census found only 4 of 72 judged pieces
    (6%) clip above 1% at the bust. So the first thing this does is measure; a
    piece with nothing exposed exits having moved ZERO vertices, which is also
    what keeps the cost off the other 94%. The clean-armour negative control in
    the test suite asserts exactly that.

    Simulated (chain-driven) verts are never moved: their positions are the
    rest pose the SMP solver starts from, and displacing them is the documented
    jitter/launch class.

    Returns (verts, stats). `verts` is the input array when nothing moved.
    """
    gV = np.asarray(garment_verts, np.float64)
    gT = np.asarray(garment_tris, np.int64).reshape(-1, 3)
    bV = np.asarray(body_verts, np.float64)
    bT = np.asarray(body_tris, np.int64).reshape(-1, 3)
    bN = np.asarray(body_normals, np.float64)
    bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True), 1e-9, None)
    stats = {"moved": 0, "iters": 0, "exposed_before": 0, "exposed_after": 0,
             "max_push": 0.0, "skipped": None}
    if not PUSH_ENABLED:
        stats["skipped"] = "disabled"
        return gV, stats
    if len(gT) == 0 or len(bV) == 0 or len(gV) < 3:
        stats["skipped"] = "degenerate input"
        return gV, stats

    chain = (np.zeros(len(gV), bool) if is_chain is None
             else np.asarray(is_chain, bool))
    if chain.shape != (len(gV),):
        chain = np.zeros(len(gV), bool)
    not_chain = (~chain).astype(np.float64)
    if not not_chain.any():
        stats["skipped"] = "all simulated"
        return gV, stats

    z = bV[:, 2]
    region = (z >= PUSH_Z_LO) & (z <= PUSH_Z_HI) & (bV[:, 1] > PUSH_Y_MIN)
    if int(region.sum()) < PUSH_MIN_REGION:
        stats["skipped"] = "region too small"
        return gV, stats
    # Rim distance and reach are only ever read through `region`, so compute
    # them for the region's verts instead of the whole body. Identical
    # arithmetic on ~5.6x fewer points: `_rim_distance` is O(verts x rim edges)
    # and was the single most expensive function in a profiled conversion --
    # 44.0s of a 176.3s file, 25% of the whole thing -- almost all of it spent
    # on body verts that the z-band had already excluded.
    _r = np.flatnonzero(region)
    rim_d = _rim_distance(bV[_r], gV, _rim_edges(gT))
    reach, _nn = cKDTree(gV).query(bV[_r], k=1)
    region[_r] &= (rim_d > PUSH_RIM_MARGIN) & (reach <= PUSH_MAX_REACH)
    idx = np.flatnonzero(region)
    if len(idx) < PUSH_MIN_REGION:
        stats["skipped"] = "no judgeable skin after rim/reach gating"
        return gV, stats

    gN = np.asarray(garment_normals, np.float64)
    if gN.shape != gV.shape:
        stats["skipped"] = "no garment normals"
        return gV, stats
    gN = gN / np.clip(np.linalg.norm(gN, axis=1, keepdims=True), 1e-9, None)

    tester = _ClipTester(gV, gT)
    clip, _in_t = tester.clipping(bV, bN, idx)
    stats["exposed_before"] = int(clip.sum())
    if not clip.any():
        stats["exposed_after"] = 0
        return gV, stats                      # the 94% path: nothing moved

    push = np.zeros(len(gV))
    for it in range(PUSH_ITERS):
        cur = gV + gN * push[:, None]
        tester.set_garment(cur)
        clip, in_t = tester.clipping(bV, bN, idx)
        stats["iters"] = it + 1
        if not clip.any():
            break
        bad = idx[clip]
        depth = in_t[clip]
        depth = np.where(np.isfinite(depth), depth, 0.0)
        want = np.clip(depth + PUSH_MARGIN, 0.0, PUSH_REQ_CAP)
        _d, nn = cKDTree(cur).query(bV[bad], k=PUSH_KNN)
        step = np.zeros(len(gV))
        np.maximum.at(step, np.atleast_2d(nn).reshape(-1),
                      np.repeat(want, PUSH_KNN))
        step = np.clip(step * not_chain * PUSH_DAMP, 0.0, PUSH_MAX_TOTAL)
        if step.max() <= 1e-3:
            break
        push = np.clip(push + step, 0.0, PUSH_MAX_TOTAL)

    out = gV + gN * push[:, None]
    out[chain] = gV[chain]
    tester.set_garment(out)
    clip_after, _ = tester.clipping(bV, bN, idx)
    moved = np.linalg.norm(out - gV, axis=1) > 1e-4
    stats.update(moved=int(moved.sum()), exposed_after=int(clip_after.sum()),
                 max_push=float(np.linalg.norm(out - gV, axis=1).max()))
    # Never hand back a REGRESSION. The measurement is the authority, and a
    # push that increased exposure is worse than no push -- keep the input.
    if stats["exposed_after"] > stats["exposed_before"]:
        stats["skipped"] = (f"reverted: exposure rose "
                            f"{stats['exposed_before']} -> "
                            f"{stats['exposed_after']}")
        stats["moved"] = 0
        return gV, stats
    return out, stats


def sink_path(dst_path) -> Path:
    """JSONL beside the converted mesh tree, one file for the whole run."""
    override = os.environ.get("CBBE2UBE_STANDOFF_LOG")
    if override:
        return Path(override)
    p = Path(dst_path).resolve()
    for parent in p.parents:
        if parent.name.lower() == "meshes":
            return parent.parent / "standoff_audit.jsonl"
    return p.parent / "standoff_audit.jsonl"


def _append(dst_path, rec: dict) -> None:
    # One json line per write, opened in append mode: short writes under the
    # pipe-buffer size are atomic enough for pool workers, and a torn line
    # costs one record rather than the file.
    try:
        # `nif` is a BARE FILENAME, and filenames repeat across mods -- three
        # different mods in one modlist ship a `cuirassmedium_1.nif`. A record
        # naming only the basename cannot be traced back to the piece it
        # describes, which cost real time when a frame correction had to be
        # matched to one of three candidates. Carry enough of the tail to
        # disambiguate without recording an absolute path.
        rec.setdefault("path", "/".join(Path(dst_path).parts[-4:]))
        with open(sink_path(dst_path), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def record_frame(dst_path, shape_name, report: dict) -> None:
    """Append a frame-precondition anomaly to the run's sink."""
    if not _enabled():
        return
    rec = {"kind": "frame", "nif": str(Path(dst_path).name),
           "shape": str(shape_name)}
    rec.update(report)
    _append(dst_path, rec)


def record_chain_shift(dst_path, report: dict) -> None:
    """Append one #chain-body-shift decision to the run's sink.

    This pass is unusually easy to misjudge, in both directions. It moves BONES,
    not vertices, so a clip test on `shape.verts` shows nothing and the pass
    reads as inert whether or not it worked. A print is no substitute: a pool
    worker's stdout can be discarded outright in the frozen exe, so a run can
    look silent while every chain moved. Recording the decision -- including the
    SKIPPED ones and why -- is what makes the pass verifiable over a pack
    instead of one piece at a time.
    """
    if not _enabled():
        return
    rec = {"kind": "chain_shift", "nif": str(Path(dst_path).name)}
    rec.update(report)
    _append(dst_path, rec)


PASS_TRACE = os.environ.get("CBBE2UBE_PASS_TRACE") == "1"


class PassTracer:
    """MEASURE every pass, REVERT nothing. Default OFF.

    Separates measurement from enforcement, deliberately. Guarding all twelve
    corrective passes is not affordable -- measured, not guessed. Even after
    the ray-cast work took a region measurement from 1.004s to ~0.12s, guarding
    every pass is 11 measurements per shape against the chain contract's 2.

    Reverting broadly is also WRONG, not merely slow, and the data from this
    tracer is what established that. The criterion is bust-region clipping, and
    several passes optimise for things it cannot see (groove smoothing fixes
    crinkles, seam welding fixes gaps, inflation fixes z-fighting). Worse,
    `conform_to_source_standoff` pulls IN by design: over 48 traced shapes it
    was the ONLY pass that ever regressed fit, all 5 of its regressions were
    recovered downstream, and 0 of 48 shapes ended worse than they started.
    Reverting it would have blocked a correct pass and biased every garment
    looser -- the over-inflation the user reported twice.

    So: enforce at the CHAIN level (see ChainGuard), and use this to LEARN, on a
    bounded sample, which passes ever regress fit and by how much. Nothing is
    reverted here, so there is no correctness risk in running it.

    Each `mark()` measures once and reports the delta against the previous mark
    -- that is the shared-measurement scheme, so N passes cost N+1 measurements.
    """

    def __init__(self, body_verts, body_normals, garment_tris, *,
                 enabled=None, min_region: int = PUSH_MIN_REGION):
        self.enabled = PASS_TRACE if enabled is None else bool(enabled)
        self.armed = False
        self.rows = []
        self._prev_label = None
        self._prev_count = None
        if not self.enabled:
            return
        try:
            self.bV = np.asarray(body_verts, np.float64)
            bn = np.asarray(body_normals, np.float64)
            self.bN = bn / np.clip(np.linalg.norm(bn, axis=1, keepdims=True),
                                   1e-9, None)
            self.gT = np.asarray(garment_tris, np.int64).reshape(-1, 3)
            if len(self.gT) == 0 or len(self.bV) < 3:
                return
            self.idx = np.flatnonzero(push_region_mask(self.bV))
            if len(self.idx) < min_region:
                return
            self.armed = True
        except Exception:
            self.armed = False

    def mark(self, label, verts) -> None:
        """Measure the shape as it stands after `label`, log the delta."""
        if not self.armed or verts is None:
            return
        try:
            clip, _ = _ClipTester(np.asarray(verts, np.float64),
                                  self.gT).clipping(self.bV, self.bN, self.idx)
            n = int(clip.sum())
        except Exception:
            return
        if self._prev_label is not None:
            self.rows.append({"pass": label, "before": self._prev_count,
                              "after": n, "delta": n - self._prev_count,
                              "after_of": self._prev_label})
        self._prev_label, self._prev_count = label, n

    def flush(self, dst_path, shape_name) -> None:
        if not self.armed or not self.rows or not _enabled():
            return
        for r in self.rows:
            _append(dst_path, {"kind": "trace",
                               "nif": str(Path(dst_path).name),
                               "shape": str(shape_name), **r})


# ------------------------------------------ DISPLACEMENT SURVIVAL (opt-in)
# Did a pass's work reach the shipped verts, or did a later pass undo it?
#
# Every other diagnostic in this file measures the SHAPE after a pass. None of
# them can tell "this pass did nothing" from "this pass did the right thing and
# a later pass put it back", because both read as an unchanged final number.
# That distinction cost a session. The chain anti-poke moved the hip band
# exactly as intended and `_physics_chain_nowarp_blend`, four passes later and
# BY DESIGN, pins every chain vert back to its source position -- so raising the
# push budget from 1.0 to 2.0 produced an identical 7.30%. Probing that pass
# offline, where nothing runs after it, claimed a fix seven times larger than
# the pipeline delivered.
#
# The question is geometric and needs NO body, NO rays and NO region: it asks
# what is left of the pass's own displacement field at the end. So unlike
# ChainGuard and PassTracer this arms on EVERY shape rather than only the ones
# the bust metric can see -- the pass it was written to catch acts on the HIP,
# a band those two never arm for, which is precisely how it stayed hidden.
SURVIVAL_TRACE = os.environ.get("CBBE2UBE_SURVIVAL_TRACE") == "1"
SURVIVAL_EPS = 1e-4        # below this a vert was not moved by the pass
SURVIVAL_CANCELLED = 0.10  # <10% of the motion left = cancelled downstream
SURVIVAL_KEPT = 0.90       # >90% left = the pass owns this vert's position
# A RATIO NEEDS A DENOMINATOR WORTH DIVIDING BY. The first real run produced
# `bake_preset survival 46.9` off 8 verts moved a mean of 0.014u -- arithmetically
# correct and completely meaningless, because later passes moved those verts
# units in the same direction and the ratio just measures them. Read as a finding
# it says a pass was amplified 47x. The floor sits well under the smallest push
# any pass applies (~0.15u) and far above vertex quantisation, so it excludes
# only motion too small for the question to mean anything.
SURVIVAL_MIN_MOTION = 0.05


class DisplacementSurvival:
    """Per pass: how much of what it moved is still in the final verts.

    For pass k with snapshots S(k-1) -> S(k), its displacement is D = S(k)-S(k-1)
    and what is LEFT of it at the end is R = F - S(k-1), F being the shipped
    geometry. Survival is the least-squares scale of D that R contains::

        survival = sum(R.D) / sum(D.D)

    1.0 = the pass's motion is intact, 0.0 = something put those verts exactly
    back, negative = a later pass overshot past the starting point, >1 = a later
    pass pushed further the same way. It is |D|**2-weighted by construction, so
    the verts a pass actually moved dominate and the untouched majority cannot
    dilute the number -- an unweighted mean over a mostly-zero field is noise.

    Motion PERPENDICULAR to D neither adds nor subtracts here. That is the
    intended reading: the question is whether this pass's contribution is still
    present, not whether the vertex is still where this pass left it.

    A single aggregate hides a fully-pinned SUBPOPULATION -- a pass that moves
    500 chain verts and 500 free ones, with only the chain half restored, reads
    a healthy 0.5. So `frac_cancelled` reports the share of moved verts whose
    OWN survival is under the threshold, and that is the number that would have
    named the hip band immediately.

    Attribution comes out of the same snapshots for free: the contribution of a
    later pass m is sum(D(m).D(k)) / sum(D(k).D(k)) over the verts k moved, and
    those contributions sum with 1.0 to exactly the survival above (the
    displacements telescope). The most negative one names the canceller.
    """

    def __init__(self, *, enabled=None, max_passes: int = 64):
        self.enabled = SURVIVAL_TRACE if enabled is None else bool(enabled)
        self.armed = bool(self.enabled)
        self._snaps = []
        self._max = int(max_passes)
        self.dropped = 0

    def checkpoint(self, label, verts) -> None:
        """Remember the geometry after `label`. An array copy, no measurement."""
        if not self.armed or verts is None:
            return
        try:
            v = np.array(verts, dtype=np.float64, copy=True)
        except Exception:
            return
        if v.ndim != 2 or v.shape[1] != 3:
            return
        if len(self._snaps) >= self._max:
            # Keep the PREFIX contiguous rather than dropping a middle snapshot
            # the way ChainGuard does. Merging two passes under one label would
            # make the record attribute motion to the wrong pass, and a
            # diagnostic that lies about which pass moved something is worse
            # than one that admits it stopped early.
            self.dropped += 1
            return
        self._snaps.append((str(label), v))

    def analyse(self, final_verts) -> list:
        """One row per pass. An EMPTY list means nothing was measured, which
        must never be read as 'nothing was cancelled' -- callers that care
        should assert on it."""
        if not self.armed or final_verts is None or len(self._snaps) < 2:
            return []
        try:
            F = np.asarray(final_verts, dtype=np.float64)
        except Exception:
            return []
        snaps = list(self._snaps)
        # The SHIPPED geometry is the authority, not the last checkpoint: a
        # chain rollback and any edit after the final stage both land here, and
        # a pass whose work is discarded by a rollback has not survived either.
        if (F.shape == snaps[-1][1].shape
                and not np.array_equal(F, snaps[-1][1])):
            snaps.append(("(after last pass)", F))
        n = len(snaps)
        rows = []
        for k in range(1, n):
            label, S1 = snaps[k]
            S0 = snaps[k - 1][1]
            if S0.shape != S1.shape:
                rows.append({"pass": label,
                             "skipped": f"verts {len(S0)} -> {len(S1)}"})
                continue
            if F.shape != S0.shape:
                rows.append({"pass": label,
                             "skipped": f"final verts {len(F)} != {len(S0)}"})
                continue
            D = S1 - S0
            mag2 = np.einsum("ij,ij->i", D, D)
            moved = mag2 > (SURVIVAL_EPS * SURVIVAL_EPS)
            if not moved.any():
                rows.append({"pass": label, "moved_verts": 0,
                             "note": "pass moved nothing"})
                continue
            Dm = D[moved]
            m2 = mag2[moved]
            denom = float(m2.sum())
            proj = np.einsum("ij,ij->i", F[moved] - S0[moved], Dm)
            per = proj / m2
            surv = float(proj.sum() / denom)
            worst_lbl, worst_val = None, 0.0
            # EVERY later pass's contribution, not just the worst. "Who else
            # touched this pass's work" is a different question from "who undid
            # the most of it", and the second cannot answer the first: a
            # consolidation is justified by two passes FIGHTING, so it needs the
            # pairwise number, and reading the worst-canceller column instead
            # would credit a fight to whichever pass happened to win.
            contrib = {}
            for m in range(k + 1, n):
                Sm, Sp = snaps[m][1], snaps[m - 1][1]
                if Sm.shape != S1.shape or Sp.shape != S1.shape:
                    continue
                c = float(np.einsum("ij,ij->i",
                                    (Sm - Sp)[moved], Dm).sum() / denom)
                if abs(c) >= 0.01:
                    contrib[snaps[m][0]] = round(c, 4)
                if c < worst_val:
                    worst_lbl, worst_val = snaps[m][0], c
            mean_mag = float(np.sqrt(m2).mean())
            low = mean_mag < SURVIVAL_MIN_MOTION
            row = {"pass": label,
                   "moved_verts": int(moved.sum()),
                   "moved_mean": round(mean_mag, 4),
                   "moved_max": round(float(np.sqrt(m2.max())), 4),
                   "survival": round(surv, 4),
                   "frac_cancelled": round(
                       float((per < SURVIVAL_CANCELLED).mean()), 4),
                   "frac_kept": round(float((per > SURVIVAL_KEPT).mean()), 4),
                   # Survival above is exact regardless; only the ATTRIBUTION
                   # needs every later pass, so say when the tail is missing.
                   "attrib_complete": self.dropped == 0}
            if low:
                row["low_signal"] = True
            if contrib:
                row["contrib"] = contrib
            if worst_lbl is not None:
                row["cancelled_by"] = worst_lbl
                row["cancelled_frac"] = round(worst_val, 4)
            # No verdict on a pass that barely moved: "cancelled" on 0.014u of
            # motion is an alarm about nothing, and alarms about nothing are
            # how a real one gets ignored.
            if surv < SURVIVAL_CANCELLED and not low:
                row["verdict"] = "CANCELLED"
            rows.append(row)
        return rows

    def flush(self, dst_path, shape_name, final_verts) -> list:
        """Analyse and record. Returns the rows so a caller can assert it
        measured something."""
        rows = self.analyse(final_verts)
        if rows and _enabled():
            for r in rows:
                _append(dst_path, {"kind": "survival",
                                   "nif": str(Path(dst_path).name),
                                   "shape": str(shape_name), **r})
        return rows

    def release(self) -> None:
        """Drop snapshots -- a torso holds several passes' worth of verts."""
        self._snaps = []


CHAIN_GUARD = os.environ.get("CBBE2UBE_NO_CHAIN_GUARD") != "1"
CHAIN_TOL = int(os.environ.get("CBBE2UBE_CHAIN_TOL", "0"))

# ---------------------------------------------------- STANDOFF TRACE (opt-in)
# Which pass pushed the garment OFF the body, and where up the torso.
#
# The pass trace answers "which pass left skin exposed"; it is blind to the
# opposite defect. A gap reported in game at the strap line was invisible to
# every number this module produced: the ceiling guards z 90-102 only, and a
# nine-arm kill-switch bisect moved it by 0.02u, so no toggleable pass owns it.
# Measuring STANDOFF at the checkpoints the chain already keeps names the
# responsible pass directly, without new conversions or guesswork.
#
# SLABS, not one window. A single median over z 105-114 read identically for all
# nine bisect arms because hit density varies ~10x across it and the dense lower
# slabs pin the median. Same failure as judging the whole torso by the bust
# band. Slabs are narrow enough that the number describes one place.
STANDOFF_TRACE = os.environ.get("CBBE2UBE_STANDOFF_TRACE") == "1"
TRACE_SLABS = ((90.0, 102.0), (102.0, 105.0), (105.0, 108.0),
               (108.0, 111.0), (111.0, 114.0))
TRACE_NY_MIN = 0.25    # front-facing skin; normal-based so it generalises up
TRACE_X_MAX = 18.0     # torso front, clear of the arms
TRACE_TMAX = 14.0      # a gap is measured in units the bust ceiling never sees
TRACE_MIN_HITS = 8


def garment_reaches(garment_verts, body_verts, idx, margin: float = TMAX):
    """Could this garment possibly be hit by rays from `idx`? Bounding box only.

    THE HOT-PATH GATE. Arming previously tested only the BODY region size, which
    is a constant -- the UBE bust band is 5249 verts against a floor of 50 -- so
    EVERY phase-2 shape armed and paid the full measurement cost: two chain
    measurements, a standoff record and up to four band records. A belt, a bag,
    a boot and a skirt each paid seven ray casts to discover they are nowhere
    near the bust.

    A ray from a band vert travels at most `margin`, so anything that can be hit
    lies within `margin` of the band. Comparing BOUNDING BOXES is conservative
    by construction -- a garment's box contains all of its triangles, so a box
    that does not overlap cannot contain a triangle that does. It can only ever
    admit work, never skip a real hit. O(n) over verts, no triangle touched.

    The margin is the full ray reach rather than something tuned: pushes are
    capped in the low single digits of units, so 12u leaves ample slack for a
    pass moving a garment INTO the band after this is evaluated.
    """
    try:
        g = np.asarray(garment_verts, np.float64)
        if g.ndim != 2 or not len(g):
            return False
        b = np.asarray(body_verts, np.float64)
        if idx is None or not len(idx):
            return False
        band = b[idx]
        lo = band.min(axis=0) - margin
        hi = band.max(axis=0) + margin
        return bool(np.all(g.max(axis=0) >= lo) and np.all(g.min(axis=0) <= hi))
    except Exception:
        return True      # never let the gate itself drop a measurement


def front_slab(body_verts, body_normals, z_lo, z_hi,
               ny_min: float = TRACE_NY_MIN, x_max: float = TRACE_X_MAX):
    """Front-facing torso skin in a z slab."""
    v = np.asarray(body_verts, np.float64)
    n = np.asarray(body_normals, np.float64)
    return np.flatnonzero((n[:, 1] > ny_min) & (np.abs(v[:, 0]) < x_max)
                          & (v[:, 2] >= z_lo) & (v[:, 2] < z_hi))


class _TorsoCast:
    """ONE cast over the union of every torso ray set, sliced per consumer.

    `record_standoff` and the four `record_torso_bands` slabs run on the SAME
    geometry, at the same moment, with the same `tmax`, over ray sets that
    overlap heavily -- the calibrated bust mask and the `bust` slab cover
    largely the same skin. That was five separate casts of the same garment.

    Rays are independent, so casting the union once and slicing per consumer is
    arithmetically identical; `tests/test_torso_dedupe.py` asserts that against
    the per-consumer path rather than assuming it.

    Not shared with the chain contract or `minimum_push`: those cast at
    DIFFERENT points in the pass chain, so the geometry genuinely differs and
    reusing a result would be wrong, not merely stale.
    """

    __slots__ = ("all", "ok", "t")

    def __init__(self, gV, gT, bV, bN):
        self.ok = False
        self.all = np.empty(0, np.int64)
        self.t = None
        try:
            sets = [band_index(bV)]
            for _n, lo, hi in TORSO_BANDS:
                sets.append(front_slab(bV, bN, lo, hi))
            sets = [s for s in sets if len(s)]
            if not sets:
                return
            self.all = np.unique(np.concatenate(sets))
            if not garment_reaches(gV, bV, self.all):
                return
            tester = _ClipTester(np.asarray(gV, np.float64), gT, tmax=TMAX)
            self.t = cast_chunked(tester, bV[self.all], bN[self.all],
                                  finite_only=False)
            self.ok = True
        except Exception:
            self.ok = False

    def hits(self, idx):
        """Finite outward distances for the given BODY-vert indices."""
        if not self.ok or idx is None or not len(idx):
            return np.empty(0)
        loc = np.searchsorted(self.all, np.asarray(idx))
        loc = loc[(loc >= 0) & (loc < len(self.all))]
        if not len(loc):
            return np.empty(0)
        v = self.t[loc]
        return v[np.isfinite(v)]


def cast_chunked(tester, O, N, chunk: int = 0, finite_only: bool = True):
    """Outward distances for rays `O` along `N`, in bounded-memory chunks.

    Returns only the FINITE hits, like `standoff()`. Rays are independent, so
    chunking caps how many (ray, triangle) pairs exist at once and changes
    nothing else. Unchunked, a dense garment produced 36,061,621 pairs and a
    MemoryError; the sparse path is far cheaper than the dense one it replaced,
    but it was never bounded.
    """
    O = np.asarray(O, np.float64)
    N = np.asarray(N, np.float64)
    if not len(O):
        return np.empty(0)
    n = max(chunk if chunk > 0 else RAY_CHUNK, 1)
    out = []
    for i in range(0, len(O), n):
        o, d = O[i:i + n], N[i:i + n]
        t = tester._cast(o, d, *tester._pairs(o), len(o))
        out.append(t[np.isfinite(t)] if finite_only else t)
    return np.concatenate(out) if out else np.empty(0)


def slab_standoff(tester, body_verts, body_normals, idx,
                  min_hits: int = TRACE_MIN_HITS):
    """(median outward distance, hit count) over `idx`. NaN when too few hits.

    Goes through `_ClipTester`, whose tiered ball query and ray-line cull keep
    only candidate pairs, and casts them in bounded chunks -- the sparse path is
    cheap but not bounded, and a dense garment hit MemoryError without this.
    """
    O = np.asarray(body_verts, np.float64)[idx]
    N = np.asarray(body_normals, np.float64)[idx]
    if not len(O):
        return float("nan"), 0
    f = cast_chunked(tester, O, N)
    if len(f) < min_hits:
        return float("nan"), int(len(f))
    return float(np.median(f)), int(len(f))


class ChainGuard:
    """DIAGNOSE -> TREAT -> VERIFY at the level where the criterion is valid.

    The obvious contract -- reject any pass that measures worse than its input
    -- is WRONG here, and the trace says so rather than a hunch. Over 48 shapes
    exactly one pass ever regressed bust fit (`conform`, 5 times), every one of
    those 5 was fully recovered later in the chain, and 0 of 48 shapes ended
    worse than they started. `conform_to_source_standoff` pulls IN by design and
    the passes after it push back out; reverting it would have blocked a
    correct pass five times and biased every garment looser -- which is the
    over-inflation that reached the user twice. Intermediate regressions are
    part of how the chain works.

    So the contract is applied to the CHAIN, not to each pass:

      begin(v)        one measurement; what was wrong before anything ran
      checkpoint(l,v) a snapshot -- an array copy, NO measurement, ~free
      finish(v)       one measurement; if the chain as a whole made this shape
                      worse, walk the snapshots back and ship the best one

    Two measurements per armed shape in the normal case, against eleven for
    per-pass guarding. Snapshots only cost measurements when the verify FAILS,
    which on the traced sample was never -- the price is paid on the shapes
    that are actually broken.

    What this deliberately does NOT do is skip passes because the entry
    measurement looks clean. Bind-pose clipping is blind to animation: "at rest"
    in game is an animated pose, and the anti-poke exists for morphs and motion
    this metric cannot see. Gating passes on a bind-pose number would trade a
    measurable defect for an unmeasurable one.
    """

    def __init__(self, body_verts, body_normals, garment_tris, *,
                 enabled=None, min_region: int = PUSH_MIN_REGION,
                 max_checkpoints: int = 16):
        self.enabled = CHAIN_GUARD if enabled is None else bool(enabled)
        self.armed = False
        self.entry = -1
        self.final = -1
        self.shipped = -1     # what was SHIPPED; differs from
                              # `final` whenever a rollback fired
        self.outcome = "unarmed"
        self.rolled_back_to = None
        self.extra_measurements = 0
        self._snaps = []
        self._max = int(max_checkpoints)
        if not self.enabled:
            return
        try:
            self.bV = np.asarray(body_verts, np.float64)
            bn = np.asarray(body_normals, np.float64)
            self.bN = bn / np.clip(np.linalg.norm(bn, axis=1, keepdims=True),
                                   1e-9, None)
            self.gT = np.asarray(garment_tris, np.int64).reshape(-1, 3)
            if len(self.gT) == 0 or len(self.bV) < 3:
                return
            self.idx = np.flatnonzero(push_region_mask(self.bV))
            if len(self.idx) < min_region:
                return
            self.armed = True
        except Exception:
            self.armed = False

    def exposed(self, verts) -> int:
        if not self.armed or verts is None:
            return -1
        try:
            clip, _ = _ClipTester(np.asarray(verts, np.float64),
                                  self.gT).clipping(self.bV, self.bN, self.idx)
            return int(clip.sum())
        except Exception:
            return -1

    def begin(self, verts, known: "int | None" = None) -> int:
        """Entry diagnosis. `known` reuses a count already measured this shape
        rather than paying for it twice."""
        if not self.armed:
            return -1
        if verts is not None and not garment_reaches(verts, self.bV, self.idx):
            # Nowhere near the measured band: the criterion would read
            # 0->0 and the two measurements would buy nothing. Disarming
            # here is what stops a belt paying a torso shape's bill.
            self.armed = False
            self.outcome = "out of band"
            return -1
        self.entry = int(known) if known is not None else self.exposed(verts)
        if self.entry >= 0:
            self.checkpoint("entry", verts, count=self.entry)
        return self.entry

    def checkpoint(self, label, verts, count: "int | None" = None) -> None:
        """Remember the geometry after `label`. No measurement -- an array copy
        is ~microseconds against ~120ms for a measurement, so the chain can
        afford to remember every pass and measure only if it has to."""
        if not self.armed or verts is None:
            return
        try:
            v = np.array(verts, dtype=np.float64, copy=True)
        except Exception:
            return
        self._snaps.append((str(label), v, count))
        if len(self._snaps) > self._max:
            # keep entry, drop the oldest middle: the useful fallbacks are the
            # start and the recent ones
            del self._snaps[1]

    def trace_standoff(self, dst_path, shape_name, final_verts=None) -> list:
        """Per-pass standoff up the torso. Default OFF, measure-only.

        Costs one measurement per slab per checkpoint, so it is a diagnostic to
        aim at a piece, never something a pack run turns on. It reads the
        snapshots the chain already keeps, so it needs no re-conversion and adds
        nothing to the hot path when disabled.

        MUST run before `release()`. Returns the rows it recorded so a caller
        can assert it measured something -- an empty list means the shape was
        unarmed or every slab fell below the hit floor, which must not be
        mistaken for "no pass moved anything".
        """
        if not (STANDOFF_TRACE and self.armed):
            return []
        snaps = list(self._snaps)
        if final_verts is not None:
            snaps.append(("final", np.asarray(final_verts, np.float64), None))
        rows = []
        try:
            idx = [(lo, hi, front_slab(self.bV, self.bN, lo, hi))
                   for lo, hi in TRACE_SLABS]
            prev = {}
            for label, v, _c in snaps:
                tester = _ClipTester(v, self.gT, tmax=TRACE_TMAX)
                for lo, hi, ii in idx:
                    med, hits = slab_standoff(tester, self.bV, self.bN, ii)
                    key = (lo, hi)
                    d = (med - prev[key]) if (key in prev
                                              and np.isfinite(med)
                                              and np.isfinite(prev[key])) \
                        else float("nan")
                    rows.append({"kind": "standoff_trace",
                                 "nif": str(Path(dst_path).name),
                                 "shape": str(shape_name), "pass": label,
                                 "z_lo": lo, "z_hi": hi,
                                 "median": None if not np.isfinite(med)
                                 else round(med, 3),
                                 "delta": None if not np.isfinite(d)
                                 else round(d, 3),
                                 "hits": hits})
                    if np.isfinite(med):
                        prev[key] = med
        except Exception as e:
            _append(dst_path, {"kind": "standoff_trace_error",
                               "nif": str(Path(dst_path).name),
                               "shape": str(shape_name),
                               "error": f"{type(e).__name__}: {e}"})
            return rows
        if _enabled():
            for r in rows:
                _append(dst_path, r)
        return rows

    def finish(self, verts):
        """Verify the finished shape. Returns (verts, outcome)."""
        if not self.armed or verts is None:
            self.outcome = "unarmed"
            return verts, self.outcome
        self.final = self.exposed(verts)
        if self.entry < 0 or self.final < 0:
            self.outcome = "unmeasurable"
            return verts, self.outcome
        if self.final <= self.entry + CHAIN_TOL:
            self.shipped = self.final
            self.outcome = f"ok ({self.entry}->{self.final})"
            return verts, self.outcome
        # The chain made this shape worse. Find the best snapshot we have.
        best_v, best_n, best_l = verts, self.final, "final"
        for label, v, count in reversed(self._snaps):
            if count is None:
                count = self.exposed(v)
                self.extra_measurements += 1
            if count < 0:
                continue
            if count < best_n:
                best_v, best_n, best_l = v, count, label
            if best_n <= self.entry:
                break            # good enough: no worse than we started
        if best_l == "final":
            self.shipped = self.final
            self.outcome = f"REGRESSED unrecoverable ({self.entry}->{self.final})"
            return verts, self.outcome
        self.rolled_back_to = best_l
        self.shipped = best_n
        self.outcome = (f"ROLLED BACK to {best_l} "
                        f"({self.entry}->{self.final}, kept {best_n})")
        return best_v, self.outcome

    def release(self) -> None:
        """Drop snapshots. A torso can be tens of thousands of verts and the
        converter holds several shapes at once."""
        self._snaps = []

    def record(self, dst_path, shape_name) -> None:
        if not self.armed or not _enabled():
            return
        _append(dst_path, {"kind": "chain", "nif": str(Path(dst_path).name),
                           "shape": str(shape_name), "entry": self.entry,
                           "final": self.final, "shipped": self.shipped, "outcome": self.outcome,
                           "rolled_back_to": self.rolled_back_to,
                           "extra_measurements": self.extra_measurements})


# ------------------------------------------------- TORSO BANDS (measure-only)
# The ceiling above guards z 90-102 -- the bust FRONT -- and nothing else has
# ever been recorded pack-wide. A gap reported in game sat at z 108-114, which
# no record could see: "1.31u median, within ceiling" was an accurate statement
# about a region the user was not looking at. The under-bust (z 78-90) has been
# an open lead for just as long with no numbers behind it.
#
# SEPARATE BANDS, NOT ONE WIDER ONE. Merging them would hide exactly what this
# is for: hit density varies ~10x up the torso, so a single median is pinned by
# whichever slab has the most covered skin. A bisect that aggregated z 105-114
# read IDENTICALLY for all nine of its arms for precisely that reason.
#
# NO VERDICT on the new bands. `over` stays on the bust record alone, because
# that is the only band with a calibrated anchor (a piece confirmed correct in
# game). A garment legitimately stands further off at the strap line than at the
# apex, so reusing the bust ceiling there would manufacture failures. These
# bands ship as DATA; a ceiling can be calibrated once there are numbers to
# calibrate it against, which is what this run produces.
#
# Measure-only, like everything else here: it records, it never moves a vertex.
TORSO_BANDS = (("underbust", 78.0, 90.0), ("bust", 90.0, 102.0),
               ("upperchest", 102.0, 108.0), ("strap", 108.0, 114.0))


def record_torso_bands(dst_path, shape_name, garment_verts, garment_tris,
                       body_verts, body_normals, cast=None) -> list:
    """Standoff per torso band. Additive: the calibrated bust record is
    unchanged and still written by `record_standoff`.

    Uses the sparse `_ClipTester` path rather than `standoff()`, which builds a
    dense (rays x ALL triangles) array -- that reached 15 GB on a five-shape
    cuirass when measuring several bands. `tests/test_torso_bands.py` asserts
    the two agree on the same index, so the mixed implementation is justified
    rather than assumed.
    """
    if not _enabled():
        return []
    out = []
    try:
        if body_verts is None or body_normals is None:
            return []
        bV = np.asarray(body_verts, np.float64)
        bN = np.asarray(body_normals, np.float64)
        gv = np.asarray(garment_verts, np.float64)
        tester = None if cast is not None else _ClipTester(
            gv, garment_tris, tmax=TMAX)
        for name, lo, hi in TORSO_BANDS:
            idx = front_slab(bV, bN, lo, hi)
            if len(idx) < TRACE_MIN_HITS:
                continue
            if not garment_reaches(gv, bV, idx):
                continue          # cannot be hit; do not cast
            f = (cast.hits(idx) if cast is not None
                 else cast_chunked(tester, bV[idx], bN[idx]))
            if len(f) < TRACE_MIN_HITS:
                continue
            med, hits = float(np.median(f)), int(len(f))
            rec = {"kind": "standoff_band",
                   "nif": str(Path(dst_path).name),
                   "shape": str(shape_name), "band": name,
                   "z_lo": lo, "z_hi": hi,
                   "n": int(hits), "skin": int(len(idx)),
                   "covered_pct": round(100.0 * hits / len(idx), 1),
                   "median": round(float(med), 3),
                   "p90": round(float(np.percentile(f, 90)), 3),
                   "max": round(float(f.max()), 3)}
            _append(dst_path, rec)
            out.append(rec)
        return out
    except Exception as e:
        _append(dst_path, {"kind": "standoff_band_error",
                           "nif": str(Path(dst_path).name),
                           "shape": str(shape_name),
                           "error": f"{type(e).__name__}: {e}"})
        return out


def record_standoff(dst_path, shape_name, garment_verts, garment_tris,
                    body_verts, body_normals,
                    cast=None) -> "dict | None":
    """Measure the finished shape and record it. Never raises, never edits.

    Returns the record, or None when there was nothing to measure. A failure
    is recorded with its exception rather than dropped: a measurement that
    could not run must not be indistinguishable from one that found nothing.
    """
    if not _enabled():
        return None
    try:
        if body_verts is None or body_normals is None:
            return None
        idx = band_index(body_verts)
        if len(idx) < MIN_HITS:
            return None
        # Cheap gate BEFORE any ray work. This ran the full measurement and
        # then threw it away for every shape that does not cover the bust.
        if not garment_reaches(garment_verts, body_verts, idx):
            return None
        # SPARSE path. This used the dense `standoff()` -- the formulation that
        # reached 15 GB measuring several bands on one cuirass -- on every
        # armed shape. `tests/test_torso_bands.py` asserts the two agree to
        # 1e-6 on the same index, so the anchor is unmoved.
        _t = None if cast is not None else _ClipTester(
            np.asarray(garment_verts, np.float64), garment_tris,
            tmax=TMAX)
        s = (cast.hits(idx) if cast is not None else
             cast_chunked(_t, np.asarray(body_verts, np.float64)[idx],
                          np.asarray(body_normals, np.float64)[idx]))
        if len(s) < MIN_HITS:
            return None                      # does not cover the bust
        rec = {
            "nif": str(Path(dst_path).name),
            "dir": str(Path(dst_path).parent.name),
            "shape": str(shape_name),
            "n": int(len(s)),
            "covered_pct": round(100.0 * len(s) / len(idx), 1),
            "median": round(float(np.median(s)), 3),
            "p90": round(float(np.percentile(s, 90)), 3),
            "max": round(float(s.max()), 3),
        }
        rec["over"] = bool(rec["median"] > CEIL_MEDIAN
                           or rec["p90"] > CEIL_P90)
        _append(dst_path, rec)
        return rec
    except Exception as e:
        _append(dst_path, {"nif": str(Path(dst_path).name),
                           "shape": str(shape_name),
                           "err": f"{type(e).__name__}: {e}"})
        return None
