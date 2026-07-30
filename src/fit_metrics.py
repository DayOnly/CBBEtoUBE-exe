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

    def face_normals(self):
        if self._fn is None:
            a = self.gV[self.gT[:, 0]]
            n = np.cross(self.gV[self.gT[:, 1]] - a, self.gV[self.gT[:, 2]] - a)
            self._fn = n / np.clip(np.linalg.norm(n, axis=1, keepdims=True),
                                   1e-12, None)
        return self._fn

    def _pairs(self, O):
        balls = self.ctree.query_ball_point(O, self.reach)
        cnt = np.fromiter((len(b) for b in balls), np.int64, len(balls))
        if not cnt.sum():
            return np.empty(0, np.int64), np.empty(0, np.int64)
        ray_i = np.repeat(np.arange(len(O), dtype=np.int64), cnt)
        tri_i = np.concatenate([np.asarray(b, np.int64)
                                for b in balls if len(b)])
        d = np.linalg.norm(self.cent[tri_i] - O[ray_i], axis=1)
        keep = d <= self.tmax + self.trad[tri_i]
        return ray_i[keep], tri_i[keep]

    def _cast(self, O, D, ray_i, tri_i, n_rays, want_tri=False):
        out = np.full(n_rays, np.inf)
        who = np.full(n_rays, -1, np.int64)
        if not len(ray_i):
            return (out, who) if want_tri else out
        V, T = self.gV, self.gT
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

    def clipping(self, bV, bN, idx, oriented: bool = True):
        """(clip_mask, in_t) over `idx`. clip = out escapes AND in hits."""
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
PUSH_Z_LO = BAND_Z[0] - 4.0
PUSH_Z_HI = BAND_Z[1]
PUSH_Y_MIN = -2.0
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
    rim_d = _rim_distance(bV, gV, _rim_edges(gT))
    reach, _nn = cKDTree(gV).query(bV, k=1)
    region &= (rim_d > PUSH_RIM_MARGIN) & (reach <= PUSH_MAX_REACH)
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


PASS_TRACE = os.environ.get("CBBE2UBE_PASS_TRACE") == "1"


class PassTracer:
    """MEASURE every pass, REVERT nothing. Default OFF.

    Separates measurement from enforcement, deliberately. Guarding all twelve
    corrective passes is not affordable -- measured, not guessed: one region
    measurement is 1.004s (5249 body verts x 3480 tris), so before+after on
    twelve passes is 24s per shape, ~96s for a four-shape piece, and 102 hours
    over 3800 output NIFs. Sharing measurements (pass N's "after" IS pass N+1's
    "before") brings it to 13 per shape, still ~55 hours pack-wide.

    Reverting broadly is also WRONG, not merely slow. The criterion here is
    bust-region clipping, and several passes optimise for things it cannot see
    (groove smoothing fixes crinkles, seam welding fixes gaps, inflation fixes
    z-fighting). Worse, `conform_to_source_standoff` pulls IN by design, which
    can raise clipping until the anti-poke pushes back out -- reverting it would
    systematically bias every garment looser, which is the over-inflation the
    user reported twice.

    So: enforce only on terminal passes (see FitGuard), and use this to LEARN,
    on a bounded sample, which passes ever regress fit and by how much. Nothing
    is reverted here, so there is no correctness risk in running it.

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


class FitGuard:
    """DIAGNOSE -> TREAT -> VERIFY for a single shape: reject any pass whose
    output measures worse than its input.

    WHY. The chain is speculative. Twelve passes key off proximity-to-body
    against a constant, so they move leather whether or not skin is exposed, and
    nothing between them measures the result -- a pass that makes fit WORSE is
    invisible unless it happens to run last. That is how an over-inflated mesh
    reached the user twice, and how a 40u frame error corrupted twelve passes in
    silence.

    Each guarded pass now states its own outcome: kept, or reverted because it
    regressed. The count of clipping verts is the criterion (the validated test,
    orientation-gated), not a proxy.

    COST CONTROL, deliberately conservative: the guard builds its region ONCE
    per shape and refuses to arm at all unless the shape covers enough of the
    measured band to judge. On a piece the metric cannot see, guarding would
    spend ray casts to learn nothing -- exactly the "measured nothing" failure
    in a new costume. `armed` says whether it is doing anything.
    """

    def __init__(self, body_verts, body_normals, garment_tris,
                 min_region: int = PUSH_MIN_REGION):
        self.armed = False
        self.log = []
        try:
            self.bV = np.asarray(body_verts, np.float64)
            self.bN = np.asarray(body_normals, np.float64)
            self.bN = self.bN / np.clip(
                np.linalg.norm(self.bN, axis=1, keepdims=True), 1e-9, None)
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
        """Clipping vert count for `verts`. -1 when it cannot be measured."""
        if not self.armed:
            return -1
        try:
            clip, _ = _ClipTester(np.asarray(verts, np.float64),
                                  self.gT).clipping(self.bV, self.bN, self.idx)
            return int(clip.sum())
        except Exception:
            return -1

    def guard(self, label, before, after, tol: int = 0):
        """Return (verts, outcome). Reverts to `before` if `after` is worse.

        `tol` allows a pass a small budget when it trades measured fit for
        something this metric cannot see (a seam weld, a z-fight separation).
        Default 0: no pass gets to make fit worse silently.
        """
        if not self.armed or after is None:
            return after, "unguarded"
        b = self.exposed(before)
        a = self.exposed(after)
        if b < 0 or a < 0:
            return after, "unmeasurable"
        if a > b + tol:
            self.log.append((label, b, a, "REVERTED"))
            return before, f"reverted ({b}->{a})"
        self.log.append((label, b, a, "kept"))
        return after, f"kept ({b}->{a})"

    def record(self, dst_path, shape_name) -> None:
        if not self.log or not _enabled():
            return
        for label, b, a, verdict in self.log:
            _append(dst_path, {"kind": "pass", "nif": str(Path(dst_path).name),
                               "shape": str(shape_name), "pass": label,
                               "exposed_before": b, "exposed_after": a,
                               "verdict": verdict})


def record_standoff(dst_path, shape_name, garment_verts, garment_tris,
                    body_verts, body_normals) -> "dict | None":
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
        s = standoff(body_verts, body_normals, garment_verts, garment_tris,
                     idx)
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
