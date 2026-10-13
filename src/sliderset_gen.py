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

"""Generate a per-armor BODYTRI (PIRT) file by propagating UBE body slider
deltas to armor verts.

Given:
  * UBE reference body verts (BaseShape with 29298 verts; canonical UBE topology)
  * UBE reference body OSD (per-slider, per-vertex deltas on that body)
  * A CBBE-authored armor NIF whose shapes sit on / around the body

Produces a TriFile that NioOverride loads at runtime: one TriShape per armor
shape, each carrying a TriMorph per UBE slider. Deltas are propagated from
the body to each armor vert via K=16 nearest-body-vertex IDW -- BUT read
`_BODY_MOTION_MATCH` before reasoning about that support: at stand-off
<= _MATCH_NEAR (4.0u) the IDW result is DISCARDED and the vert copies its
SINGLE nearest body vert verbatim, so on fitted clothing K never enters the
answer. There is no adaptive K; the "1 / 4 / 16 by stand-off" this docstring
used to claim was removed and only the exponent still adapts (see #bug-01).

This replaces the BodySlide build-time workflow with runtime morph
application, so users only need to ship the TRI + ESP patch rather than
.osd + .osp + a BodySlide build step.
"""
from __future__ import annotations


import numpy as np
from scipy.spatial import cKDTree

from .envflags import flag as _flag, knob as _knob
from .osd import OsdFile
from .tri import TriFile, TriShape, TriMorph


# ---------- BODYTRI (PIRT) writer — RaceMenu runtime morph format -------

# Default minimum delta for inclusion in TRI offsets. Body sliders that
# don't perceptibly affect an armor vert (because the vert is far from
# the body or the slider only touches a different region) produce tiny
# propagated deltas. Dropping them keeps the TRI compact — typical
# hand-authored TRIs are ~10MB; without filtering we'd produce 50MB+.
DEFAULT_TRI_MIN_DELTA = 0.005

# Substrings (lowercased) marking a shape as a rigid prop / metal piece
# that doesn't need body-morph tracking. These are sorted to the TAIL of
# the TRI as a soft authoring convention (cloth before rigid). After
# #139 there is no engine cap that drops them; ordering is cosmetic.
_RIGID_PROP_KEYWORDS = (
    "dagger", "scabbard", "sword", "bow", "arrow", "quiver", "shield",
    "pouch", "amulet", "ring", "necklace", "chain", "circlet", "crown",
    "earring", "nail", "gem", "stud", "buckle", "rivet", "clasp",
    "metal", "pauldron", "shoulder", "deco", "ornament", "spike",
    "collar", "button", "plate", "guard", "bracer", "vambrace",
    "armband", "armlet", "cuff",
)

# Of the rigid props, these ride a single weapon/jewelry bone and NEVER
# benefit from body morph — they're the very first to drop when the morph
# cap is tight. Body-worn rigid armor (pauldrons/shoulders/guards/bracers)
# is NOT in this set: it sits on the body and should keep a morph slot
# ahead of weapon/jewelry attachments (otherwise e.g. a shoulder pad stops
# scaling with the body when the cap cuts it — exactly what the 3-atlas
# split exposed). Priority tiers: cloth (0) < body-worn rigid (1) <
# weapon/jewelry attachment (2).
_ATTACHMENT_KEYWORDS = (
    "dagger", "scabbard", "sheath", "sword", "knife", "axe", "mace",
    "hammer", "staff", "bow", "arrow", "quiver", "shield",
    "pouch", "satchel",
    "amulet", "ring", "necklace", "circlet", "crown", "earring", "gem",
)


# ---- Body-motion match (armor tracks the body surface it covers) ------
# An armor vert must move exactly as the BODY SURFACE IT COVERS moves, so its
# clearance to the body is preserved under every slider. Then the body can never
# poke through, and the armor never balloons past it.
#
# The IDW loop below smooths each armor vert's delta over its K nearest body
# verts. For a vert standing off the body the exponent eases to 1 (wide flat
# average), which DILUTES the motion: measured 0.59-0.81x of the body on a fitted
# leather cuirass -- the body then bursts through it on a large preset. An earlier
# attempt topped verts up to the REGIONAL PEAK expansion instead; that overshot to
# 2.0-2.2x and ballooned the same cuirass. Both were measured in-game.
#
# The correct target is ratio 1.0: copy the body's delta at the vertex the armor
# covers (its nearest body vert). Wide averaging is still wanted for DRAPE cloth
# hanging far off the body (a skirt would otherwise snap rigidly to whichever leg
# vert is nearest), so blend: pure nearest-copy while the armor hugs the body
# (<= _MATCH_NEAR), easing to the smoothed IDW average out at drape distance
# (>= _MATCH_FAR). The blend weight depends only on stand-off, not the slider, so
# it is computed once per shape. Escape hatch: CBBE2UBE_NO_BODY_MOTION_MATCH=1.
_BODY_MOTION_MATCH = not _flag("CBBE2UBE_NO_BODY_MOTION_MATCH", False)
# Stand-off (units) at/below which the armor copies its covered body vert exactly.
_MATCH_NEAR = _knob("CBBE2UBE_MATCH_NEAR", 4.0)
# Stand-off at/above which we fall back to the smoothed IDW average (drape cloth).
_MATCH_FAR = _knob("CBBE2UBE_MATCH_FAR", 10.0)


# ---- #surface-morph-track (default ON) -----------------------
# Body-motion match makes every hugging VERTEX copy its nearest body vertex, so
# each vertex is right -- and the SURFACE between three of them is a linear
# blend of three distant donors. On a coarse garment over the breast the apex
# sits 3-6u from every corner; under a real preset it moves ~5u while the
# corners move 3.4-5.2u, so the flat triangle lags and the nipple comes through.
# Measured 10-12 on a 43-piece census under a bust-heavy preset: every piece that
# poked had triangles 2.6-8u over the tip, and a first-order (per-slider, bind
# hit point) model under-predicts the loss up to 7x there (the apex migrates
# under a neighbouring triangle; triangles stretch and tilt). No static clearance
# can absorb that without a visible bulge at rest.
#
# ON: per shape, choose the hugging corners' per-slider deltas so the
# barycentric SURFACE reproduces the body's delta at the body points it covers
# (least squares; each triangle's samples weigh 1 in total), regularised toward
# the nearest-donor delta by SURFACE_TRACK_REG. Linear in the slider, so a whole
# preset (a sum of sliders) is tracked as well as each slider. The normal matrix
# depends only on geometry, so it is factored ONCE per shape and reused for
# every slider. Restricted to `body_track_mask` (the caller passes the breast
# region) so the change stays local; a fine mesh, whose samples sit beside their
# donors, comes out close to the old answer.
_SURFACE_MORPH_TRACK = not _flag("CBBE2UBE_NO_SURFACE_MORPH_TRACK", False)
SURFACE_TRACK_REG = _knob("CBBE2UBE_SURFACE_TRACK_REG", 0.05)
# Smoothness of the CORRECTION over the garment's own edges. Without it each
# free vertex is fitted on its own, neighbouring corrections disagree, and the
# morphed surface crumples: measured on 5 pieces under a bust-heavy preset, +136
# triangles turning > 60 deg, a fine cuirass 99 -> 169. Non-hugging neighbours
# act as a zero-correction boundary, so the change fades out instead of tearing.
SURFACE_TRACK_SMOOTH = _knob("CBBE2UBE_SURFACE_TRACK_SMOOTH", 0.5)
# Stiffness of the open-boundary TANGENTIAL lock (see `_surface_track_solver`).
SURFACE_TRACK_RIM_LOCK = _knob("CBBE2UBE_SURFACE_TRACK_RIM_LOCK", 100.0)
# A body point counts as covered by a triangle when it projects inside it and
# sits within this distance of its plane, on either side -- the hugging range.
SURFACE_TRACK_DEPTH = 4.0


def _surface_track_solver(av, tris, free, body_verts, tree, mask):
    """Precompute the #surface-morph-track system for one shape.

    Returns (solve, free_idx, samples, sample_w, A_free, fixed_w, corners) or None.
    `solve(rhs)` maps a right-hand side over the free verts to their CORRECTION
    from the donor deltas (data + SURFACE_TRACK_REG + SURFACE_TRACK_SMOOTH).
    """
    from scipy.sparse import csr_matrix, identity
    from scipy.sparse.linalg import factorized
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if not len(t) or not free.any():
        return None
    # OPEN-BOUNDARY verts keep the donor delta. An opening (neckline, cleavage
    # cut, a chainmail panel edge) must track the body exactly as before, or the
    # tip slips out from under the edge: measured 0 -> 13 tip rays with NO
    # garment along them on a mail cuirass under a bust-heavy preset.
    _e = np.sort(np.r_[t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]], axis=1)
    _u, _c = np.unique(_e, axis=0, return_counts=True)
    _bnd = np.zeros(len(av), dtype=bool)
    _bnd[_u[_c == 1].ravel()] = True
    # ...but only TANGENTIALLY. Pinning them outright gave the whole win back on
    # the coarse pieces (a reported cuirass -0.18 -> -1.99u): the corners over
    # the tip sit on an opening, and moving them OUTWARD is what clears it. The
    # tip slips out when an edge SLIDES; it is covered when the edge LIFTS. So a
    # boundary vert keeps only the normal part of its correction (see the loop).
    _vn = np.zeros_like(av)
    _fn = np.cross(av[t[:, 1]] - av[t[:, 0]], av[t[:, 2]] - av[t[:, 0]])
    for _k in range(3):
        np.add.at(_vn, t[:, _k], _fn)
    _vn /= np.linalg.norm(_vn, axis=1, keepdims=True) + 1e-12
    t = t[free[t].any(axis=1)]
    if not len(t):
        return None
    A, B, C = av[t[:, 0]], av[t[:, 1]], av[t[:, 2]]
    cen = (A + B + C) / 3.0
    R = np.sqrt(np.max(np.stack([((A - cen) ** 2).sum(1), ((B - cen) ** 2).sum(1),
                                 ((C - cen) ** 2).sum(1)], 1), axis=1))
    fn = np.cross(B - A, C - A)
    fl = np.linalg.norm(fn, axis=1)
    ok_t = fl > 1e-9
    fn = fn / np.where(fl > 1e-9, fl, 1.0)[:, None]
    best = {}                       # body vert -> (|plane dist|, tri row, wA, wB, wC)
    reach = np.sqrt(R ** 2 + SURFACE_TRACK_DEPTH ** 2)
    for r, cand in enumerate(tree.query_ball_point(cen, reach)):
        if not ok_t[r] or not cand:
            continue
        cand = np.asarray(cand, dtype=np.int64)
        cand = cand[mask[cand]]
        if not len(cand):
            continue
        P = body_verts[cand]
        v0, v1, v2 = B[r] - A[r], C[r] - A[r], P - A[r]
        d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-12:
            continue
        d20, d21 = v2 @ v0, v2 @ v1
        wb = (d11 * d20 - d01 * d21) / den
        wc = (d00 * d21 - d01 * d20) / den
        ins = (wb >= 0) & (wc >= 0) & (wb + wc <= 1)
        h = np.abs(v2 @ fn[r])
        ins &= h <= SURFACE_TRACK_DEPTH
        for b, hh, bb, cc in zip(cand[ins], h[ins], wb[ins], wc[ins]):
            cur = best.get(int(b))
            if cur is None or hh < cur[0]:
                best[int(b)] = (float(hh), r, 1.0 - bb - cc, bb, cc)
    if not best:
        return None
    samples = np.fromiter(best.keys(), dtype=np.int64)
    rows = np.array([best[int(b)][1] for b in samples])
    W = np.array([best[int(b)][2:] for b in samples])          # (S, 3)
    # each triangle's samples weigh 1 in total
    cnt = np.bincount(rows, minlength=len(t)).astype(np.float64)
    sw = np.sqrt(1.0 / cnt[rows])
    free_idx = np.flatnonzero(free)
    col = -np.ones(len(av), dtype=np.int64)
    col[free_idx] = np.arange(len(free_idx))
    corners = t[rows]                                           # (S, 3)
    ci = col[corners]
    isfree = ci >= 0
    r_i = np.repeat(np.arange(len(samples)), 3).reshape(-1, 3)
    Af = csr_matrix(((W * sw[:, None])[isfree], (r_i[isfree], ci[isfree])),
                    shape=(len(samples), len(free_idx)))
    # fixed (non-hugging) corners move by their already-propagated delta: RHS
    fixed_w = np.where(isfree, 0.0, W) * sw[:, None]
    touched = np.asarray((abs(Af).sum(axis=0) > 0)).ravel()
    if not touched.any():
        return None
    # edge-difference operator on the correction: an edge with one fixed end
    # pins the free end toward zero correction (the boundary)
    tt = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    e = np.unique(np.sort(np.r_[tt[:, [0, 1]], tt[:, [1, 2]], tt[:, [2, 0]]], axis=1), axis=0)
    ce = col[e]
    e = e[(ce >= 0).any(axis=1)]
    ce = col[e]
    ne = len(e)
    rr = np.r_[np.arange(ne), np.arange(ne)]
    cc = np.r_[ce[:, 0], ce[:, 1]]
    # GRADIENT, not difference: weight 1/L so the energy is sum (dc/L)^2. A coarse
    # triangle needs corners 6u apart to take very different corrections and a
    # fine one does not -- a flat per-edge penalty forbids both equally (measured:
    # it removed the crumple and most of the poke win with it).
    _L = np.linalg.norm(av[e[:, 0]] - av[e[:, 1]], axis=1)
    _g = 1.0 / np.maximum(_L, 0.05)
    vv = np.r_[_g, -_g]
    okc = cc >= 0
    Dm = csr_matrix((vv[okc], (rr[okc], cc[okc])), shape=(ne, len(free_idx)))
    lam = float(SURFACE_TRACK_REG)
    mu = float(SURFACE_TRACK_SMOOTH)
    M = (Af.T @ Af + lam * identity(len(free_idx), format="csr")
         + mu * (Dm.T @ Dm))
    # The boundary lock goes INSIDE the solve. Projecting the boundary
    # corrections afterwards broke the smoothness the solve had just bought: on
    # a cuirass made of many small plates (most verts on some plate's rim) the
    # locked rims sheared against their interiors, +2205 triangles turning >60
    # deg under a preset. So the three axes become ONE system with a stiff
    # penalty on each rim vert's TANGENTIAL correction, beta * c^T (I - n n^T) c.
    from scipy.sparse import block_diag, coo_matrix
    F = len(free_idx)
    _bl = np.flatnonzero(_bnd[free_idx])
    K = block_diag((M, M, M), format="csr")
    if len(_bl):
        nb = _vn[free_idx[_bl]]
        beta = float(SURFACE_TRACK_RIM_LOCK)
        rows, cols, vals = [], [], []
        for a in range(3):
            for b in range(3):
                rows.append(_bl + a * F)
                cols.append(_bl + b * F)
                vals.append(beta * ((1.0 if a == b else 0.0) - nb[:, a] * nb[:, b]))
        K = K + coo_matrix((np.concatenate(vals), (np.concatenate(rows),
                                                   np.concatenate(cols))),
                           shape=(3 * F, 3 * F)).tocsr()
    solve = factorized(K.tocsc())
    return solve, free_idx, samples, sw, Af, fixed_w, corners



# ---- #morph-surface-sample (default ON) ----------------------
# Body-motion match makes a hugging garment vert copy the delta of its NEAREST
# body VERTEX. Two neighbouring garment verts can latch onto body verts that
# move very differently (a vert jump across a crease, a fold, a fast-growing
# patch), and the triangle between them shears. Measured on a fine top under
# three presets, triangles whose shape changed > 1.5x beyond the body's own
# stretch under them: 3.6% / 11% / 37% (worst 1%: 2.6x / 4.9x / 11x) -- and the
# shipped .tri reproduces the nearest-vertex copy exactly.
#
# ON: the matched delta is the body's delta INTERPOLATED at the closest point
# on the body SURFACE (barycentric on the closest body triangle), so the field
# is continuous and the garment moves exactly as the skin under it. Same
# offline measurement: 0.5% / 2.8% / 12.6% (1.3x / 2.5x / 3.9x).
_MORPH_SURFACE_SAMPLE = not _flag("CBBE2UBE_NO_MORPH_SURFACE_SAMPLE", False)


def _closest_surface_bary(P, body_verts, body_tris, k=12):
    """(corner vert indices (n, 3), barycentric weights (n, 3)) of the closest
    point on the body surface to each P, searched over the k triangles whose
    centroids are nearest."""
    BT = np.asarray(body_tris, dtype=np.int64).reshape(-1, 3)
    B = np.asarray(body_verts, dtype=np.float64)
    P = np.asarray(P, dtype=np.float64)
    k = min(k, len(BT))
    _, cand = cKDTree(B[BT].mean(axis=1)).query(P, k=k)
    if k == 1:
        cand = cand[:, None]
    best = np.full(len(P), np.inf)
    bt = np.zeros(len(P), dtype=np.int64)
    bw = np.zeros((len(P), 3))
    for j in range(k):
        t = BT[cand[:, j]]
        A, Bv, C = B[t[:, 0]], B[t[:, 1]], B[t[:, 2]]
        ab, ac, ap = Bv - A, C - A, P - A
        d00 = (ab * ab).sum(1); d01 = (ab * ac).sum(1); d11 = (ac * ac).sum(1)
        d20 = (ap * ab).sum(1); d21 = (ap * ac).sum(1)
        den = d00 * d11 - d01 * d01
        den = np.where(np.abs(den) > 1e-12, den, 1e-12)
        v = np.clip((d11 * d20 - d01 * d21) / den, 0.0, 1.0)
        w = np.clip((d00 * d21 - d01 * d20) / den, 0.0, 1.0)
        sm = v + w
        over = sm > 1.0
        v[over] /= sm[over]
        w[over] /= sm[over]
        u = 1.0 - v - w
        Q = u[:, None] * A + v[:, None] * Bv + w[:, None] * C
        d = np.linalg.norm(P - Q, axis=1)
        b = d < best
        best[b] = d[b]
        bt[b] = cand[b, j]
        bw[b] = np.c_[u, v, w][b]
    return BT[bt], bw


# ---- #nipple-morph-bridge (default ON) -----------------------
# A garment vert copies the delta of the NEAREST body vert. Over a nipple that
# is the wrong sample: from a plate 1.4u off the skin, the nearest body vert to
# every plate vert within ~1.5u of the apex is the apex itself, so the whole
# patch takes the apex's delta and the verts just outside take the areola's.
# Measured on a reported top under a preset setting an inverted-nipple /
# puffy-areola slider to 0.5: body apex -0.36u, areola rim +0.27u, both inside
# 1.5u; the plate sank -0.44u over a 1.5u disc and rose +0.29u just outside it
# -- a sunken disc with a raised edge, seen in game as a circle. A first
# version (the breast ring's MEAN delta over the core, fading out by 2u) left
# that edge in place and cratered under a large preset.
#
# ON: for a NIPPLE-LOCAL slider (see NIPPLE_MORPH_LOCAL_RATIO) a garment vert
# near an apex takes the body's delta LOW-PASSED over a footprint as wide as
# its own stand-off -- an area-weighted Gaussian around the body point it
# covers. A surface that far off the skin cannot carry detail finer than that;
# the nearest vert pretends it can. Full inside R_FULL of the apex, fading to
# the plain transfer by R_OUT, where a nipple-local slider no longer moves the
# body. Breast-SHAPE sliders keep the existing transfer untouched.
_NIPPLE_MORPH_BRIDGE = not _flag("CBBE2UBE_NO_NIPPLE_MORPH_BRIDGE", False)
NIPPLE_MORPH_BRIDGE_R_FULL = 4.0
NIPPLE_MORPH_BRIDGE_R_OUT = 5.0
# footprint sigma = the vert's stand-off, clamped: never sharper than the
# nipple core, never wider than the areola
NIPPLE_MORPH_SIGMA_MIN = 0.75
NIPPLE_MORPH_SIGMA_MAX = _knob("CBBE2UBE_NIPPLE_MORPH_SIGMA_MAX", 3.0)
# footprint width as a multiple of the stand-off (1.0 = the stand-off itself)
NIPPLE_MORPH_SIGMA_SCALE = _knob("CBBE2UBE_NIPPLE_MORPH_SIGMA_SCALE", 1.5)
# A slider is NIPPLE-LOCAL when the body barely moves 4-6u from the apex
# compared with how it moves within 1.5u of it (mean |delta| ratio). Measured
# on the UBE body: every nipple/areola slider <= 0.01, nipple-POSITION sliders
# 0.14-0.22, every breast-SHAPE slider >= 0.40. A share-of-motion test inside a
# radius could not separate them: big breast sliders move the apex most, so at
# 3u they read as "local" (0.6) and were low-passed -- a nipple 2u through a top
# under a large preset. Breast-shape motion must be followed exactly.
NIPPLE_MORPH_CORE_R = 1.5
NIPPLE_MORPH_FAR_R = (4.0, 6.0)
NIPPLE_MORPH_LOCAL_RATIO = 0.1
# Only a garment FINE enough to trace a nipple is low-passed: median edge of its
# triangles within NIPPLE_MORPH_EDGE_R of an apex -- the NIPPLE's own scale: a
# 3u zone averaged a torso that is 1.29u over the breast but 1.60u over the tip
# into the fine class, and it poked under a large preset. Measured over the 44-piece
# set: every piece at <= 1.4u lost its outline and stayed covered under a large
# preset; coarser ones (1.8-3.2u) gained no outline -- they cannot draw one --
# and two of them then poked under it (+0.05 -> -0.46u). Same rule and limit as
# the rest-pose bridge (NIPPLE_BRIDGE_MAX_EDGE).
NIPPLE_MORPH_MAX_EDGE = 1.5
NIPPLE_MORPH_EDGE_R = 1.5


def _vertex_areas(v, t):
    """One third of the incident triangle area per vertex."""
    t = np.asarray(t, dtype=np.int64).reshape(-1, 3)
    fa = 0.5 * np.linalg.norm(np.cross(v[t[:, 1]] - v[t[:, 0]],
                                       v[t[:, 2]] - v[t[:, 0]]), axis=1)
    a = np.zeros(len(v))
    for k in range(3):
        np.add.at(a, t[:, k], fa / 3.0)
    return a


def _nipple_morph_fine_enough(av, tris, body_verts, nb):
    """True when the garment's triangles near an apex are fine enough to trace
    a nipple (median edge <= NIPPLE_MORPH_MAX_EDGE). No triangles -> False:
    without them the class cannot be told, and the plain transfer is the safe
    answer."""
    if tris is None:
        return False
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if not len(t):
        return False
    cen = av[t].mean(axis=1)
    near = np.zeros(len(t), dtype=bool)
    for ap, nA, _area in nb:
        rel = cen - body_verts[ap]
        h = rel @ nA
        r = np.linalg.norm(rel - np.outer(h, nA), axis=1)
        near |= (r < NIPPLE_MORPH_EDGE_R) & (h > -1.0) & (h < 4.0)
    if not near.any():
        return False
    tn = t[near]
    el = np.linalg.norm(av[tn[:, [0, 1, 2]]] - av[tn[:, [1, 2, 0]]], axis=2)
    return float(np.median(el)) <= NIPPLE_MORPH_MAX_EDGE


def _nipple_morph_bridge_prep(av, body_verts, nb, tree=None):
    """Per garment vert near an apex: (rows, footprint matrix, blend weight),
    or None. `nb` = [(apex index, apex unit normal, body vertex areas)].
    Row k of the (sparse) footprint matrix maps a body delta field to the
    low-passed delta for garment vert rows[k]."""
    from scipy.sparse import csr_matrix
    if tree is None:
        tree = cKDTree(body_verts)
    rows, wts, ri, ci, vals = [], [], [], [], []
    seen = set()
    for ap, nA, area in nb:
        A = body_verts[ap]
        rel = av - A
        h = rel @ nA
        r = np.linalg.norm(rel - np.outer(h, nA), axis=1)
        sel = np.flatnonzero((r < NIPPLE_MORPH_BRIDGE_R_OUT) & (h > -1.0) & (h < 4.0))
        if not len(sel):
            continue
        dn, inn = tree.query(av[sel], k=1)
        span = max(NIPPLE_MORPH_BRIDGE_R_OUT - NIPPLE_MORPH_BRIDGE_R_FULL, 1e-6)
        for j, i in enumerate(sel):
            if int(i) in seen:
                continue
            t = min(max((NIPPLE_MORPH_BRIDGE_R_OUT - r[i]) / span, 0.0), 1.0)
            w = t * t * (3.0 - 2.0 * t)
            if w <= 0.0:
                continue
            sig = min(max(float(dn[j]) * float(NIPPLE_MORPH_SIGMA_SCALE),
                          NIPPLE_MORPH_SIGMA_MIN), float(NIPPLE_MORPH_SIGMA_MAX))
            F = body_verts[inn[j]]
            nbrs = np.asarray(tree.query_ball_point(F, 3.0 * sig), dtype=np.int64)
            if not len(nbrs):
                continue
            d2 = ((body_verts[nbrs] - F) ** 2).sum(1)
            g = np.exp(-d2 / (2.0 * sig * sig)) * np.asarray(area)[nbrs]
            tot = float(g.sum())
            if tot <= 1e-12:
                continue
            k = len(rows)
            seen.add(int(i))
            rows.append(int(i))
            wts.append(w)
            ri.append(np.full(len(nbrs), k))
            ci.append(nbrs)
            vals.append(g / tot)
    if not rows:
        return None
    M = csr_matrix((np.concatenate(vals), (np.concatenate(ri), np.concatenate(ci))),
                   shape=(len(rows), len(body_verts)))
    return np.asarray(rows), M, np.asarray(wts)


def _nipple_morph_bridge_apply(prop, prep, delta_buf, local_idx, breast_idx):
    """`local_idx` = body verts within NIPPLE_MORPH_CORE_R of an apex,
    `breast_idx` = body verts in the NIPPLE_MORPH_FAR_R band. For a
    NIPPLE-LOCAL slider only, the prepared verts take the body's
    footprint-averaged delta instead of the nearest vert's (blended by weight)."""
    if local_idx is None or breast_idx is None or not len(breast_idx) \
            or not len(local_idx):
        return prop
    mc = float(np.linalg.norm(delta_buf[local_idx], axis=1).mean())
    if mc <= 1e-9:
        return prop
    mf = float(np.linalg.norm(delta_buf[breast_idx], axis=1).mean())
    if mf / mc >= NIPPLE_MORPH_LOCAL_RATIO:
        return prop
    rows, M, wts = prep
    out = prop.copy()
    lp = M @ delta_buf
    out[rows] = prop[rows] + wts[:, None] * (lp - prop[rows])
    return out


# ---- #nipple-morph-envelope (default ON) ----------------------
# The COARSE side of the nipple-local sliders. A garment too coarse to trace a
# nipple (it fails `_nipple_morph_fine_enough`) spans it with a few large
# triangles; their corners sit up to an edge length from the apex, so however
# the delta is sampled -- nearest vert or the surface under each corner -- the
# corners rise less than the nipple does and the flat triangle between them is
# overtaken. Measured on a coarse shirt (2.7u edges) under a large preset that
# stacks nipple sliders at 200%: tip +0.05u with nearest-vert copying (whose
# nearest corner happened to copy the apex), -0.03u once the corners sampled
# the skin actually under them.
#
# ON: for a NIPPLE-LOCAL slider, a coarse garment vert near an apex rises at
# least as far (along the apex normal) as the fastest-rising body point within
# one of its own edge lengths. Every corner of a triangle spanning the apex is
# within that reach, so the triangle clears the apex's growth. Only ever adds
# outward motion; never touches breast-SHAPE sliders or fine garments.
_NIPPLE_MORPH_ENVELOPE = not _flag("CBBE2UBE_NO_NIPPLE_MORPH_ENVELOPE", False)
NIPPLE_MORPH_ENV_R = 4.0       # lateral reach from an apex (u)


def _nipple_local(delta_buf, local_idx, far_idx):
    """True when this slider is NIPPLE-LOCAL (see NIPPLE_MORPH_LOCAL_RATIO)."""
    if local_idx is None or far_idx is None or not len(far_idx) \
            or not len(local_idx):
        return False
    mc = float(np.linalg.norm(delta_buf[local_idx], axis=1).mean())
    if mc <= 1e-9:
        return False
    mf = float(np.linalg.norm(delta_buf[far_idx], axis=1).mean())
    return mf / mc < NIPPLE_MORPH_LOCAL_RATIO


def _nipple_morph_envelope_prep(av, tris, body_verts, nb, tree):
    """(rows, per-row body vert indices, per-row apex normal) or None.

    A vert's footprint is the body points its OWN triangles cover, seen along
    the apex normal -- not a ball one edge wide. The ball lifted every corner
    near the apex by the apex's growth whether or not its triangles spanned
    the apex (tip-patch stand-off +0.21u median under a mild preset over the
    44-piece set); only the triangles that actually cover the nipple need it."""
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if not len(t):
        return None
    B = np.asarray(body_verts, dtype=np.float64)
    foot: dict = {}
    nrm_of: dict = {}
    for ap, nA, _area in nb:
        nA = np.asarray(nA, dtype=np.float64)
        ea = np.cross(nA, [0.0, 0.0, 1.0])
        if np.linalg.norm(ea) < 1e-6:
            ea = np.cross(nA, [1.0, 0.0, 0.0])
        ea /= np.linalg.norm(ea)
        eb = np.cross(nA, ea)
        A = B[ap]
        gu, gw, gh = (av - A) @ ea, (av - A) @ eb, (av - A) @ nA
        gr = np.hypot(gu, gw)
        near_v = (gr < NIPPLE_MORPH_ENV_R) & (gh > -1.0) & (gh < 6.0)
        tc = t[near_v[t].any(axis=1)]
        if not len(tc):
            continue
        reach = float(np.linalg.norm(av[tc[:, [0, 1, 2]]] - av[tc[:, [1, 2, 0]]],
                                     axis=2).max())
        bu, bw, bh = (B - A) @ ea, (B - A) @ eb, (B - A) @ nA
        bsel = np.flatnonzero((np.hypot(bu, bw) < NIPPLE_MORPH_ENV_R + reach)
                              & (bh > -4.0) & (bh < 2.0))
        if not len(bsel):
            continue
        pu, pw = bu[bsel], bw[bsel]
        for tri in tc:
            x, y = gu[tri], gw[tri]
            v0 = (x[1] - x[0], y[1] - y[0])
            v1 = (x[2] - x[0], y[2] - y[0])
            den = v0[0] * v1[1] - v0[1] * v1[0]
            if abs(den) < 1e-12:
                continue
            qx, qy = pu - x[0], pw - y[0]
            wb = (qx * v1[1] - qy * v1[0]) / den
            wc = (v0[0] * qy - v0[1] * qx) / den
            inside = (wb >= -0.05) & (wc >= -0.05) & (wb + wc <= 1.05)
            if not inside.any():
                continue
            cov = bsel[inside]
            for vi in tri:
                vi = int(vi)
                if not near_v[vi]:
                    continue
                foot.setdefault(vi, []).append(cov)
                nrm_of.setdefault(vi, nA)
    if not foot:
        return None
    rows = sorted(foot)
    lists = [np.unique(np.concatenate(foot[i])) for i in rows]
    nrm = np.asarray([nrm_of[i] for i in rows])
    return np.asarray(rows), lists, nrm


def _nipple_morph_envelope_apply(prop, prep, delta_buf, local_idx, far_idx):
    if not _nipple_local(delta_buf, local_idx, far_idx):
        return prop
    rows, lists, nrm = prep
    out = prop
    for k, i in enumerate(rows):
        n = nrm[k]
        env = float((delta_buf[lists[k]] @ n).max())
        cur = float(prop[i] @ n)
        if env > cur + 1e-9:
            if out is prop:
                out = prop.copy()
            out[i] = out[i] + n * (env - cur)
    return out


# #drape-morph-headroom. A DRAPE vert (stand-off beyond _MATCH_NEAR) hangs in
# free air: the body growing under a slider only reaches it once the growth
# exceeds the gap it hangs at. The IDW average gave it the body's FULL growth
# regardless -- a skirt flap 10u off the thigh moved out by the thigh's whole
# 3-5u under a runtime preset and tilted visibly outward (in game, a fur-skirt
# cuirass). So scale a drape vert's delta by the share of the body's outward
# growth that is left after closing its gap (minus a contact clearance), and
# give a SHRINKING body nothing to pull: hanging cloth does not follow skin in.
# Blended in over _MATCH_NEAR.._MATCH_FAR so hugging cloth keeps the exact copy.
# Off with CBBE2UBE_NO_DRAPE_MORPH_HEADROOM=1 (scratch control only).
_DRAPE_MORPH_HEADROOM = not _flag("CBBE2UBE_NO_DRAPE_MORPH_HEADROOM", False)
_DRAPE_CONTACT_CLEAR = _knob("CBBE2UBE_DRAPE_CONTACT_CLEAR", 0.5)


def _drape_headroom_scale(propagated, body_delta, outward, gap):
    """Per-vert scale in [0, 1] for a drape vert's morph delta: the fraction of
    the body's outward growth (body_delta . outward) left once the gap, less a
    contact clearance, is closed. Hugging verts (gap <= _MATCH_NEAR) get 1."""
    grow = np.einsum("ij,ij->i", body_delta, outward)
    room = np.maximum(gap - _DRAPE_CONTACT_CLEAR, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(grow > 1e-9, np.clip((grow - room) / grow, 0.0, 1.0), 0.0)
    span = max(_MATCH_FAR - _MATCH_NEAR, 1e-6)
    h = np.clip((gap - _MATCH_NEAR) / span, 0.0, 1.0)
    return (1.0 - h) + h * s


def _motion_match_weight(nearest_dist: np.ndarray) -> np.ndarray:
    """Per-vert weight on the nearest-body-vert delta: 1 when hugging (copy the
    body exactly, ratio 1.0), easing to 0 at drape distance (keep IDW smoothing)."""
    span = max(_MATCH_FAR - _MATCH_NEAR, 1e-6)
    return np.clip((_MATCH_FAR - np.asarray(nearest_dist)) / span, 0.0, 1.0)


def generate_armor_tri(
    armor_shapes: dict[str, np.ndarray],
    body_verts: np.ndarray,
    body_osd: OsdFile,
    *,
    body_shape_name: str = "BaseShape",
    include_body_shapes: bool | set[str] = True,
    min_delta: float = DEFAULT_TRI_MIN_DELTA,
    carrier_shape_name: str | None = None,
    extra_body_osds: "dict[str, OsdFile] | None" = None,
    armor_vert_extremity_fractions: "dict[str, np.ndarray] | None" = None,
    also_named: "dict[str, str] | None" = None,
    armor_shape_tris: "dict[str, np.ndarray] | None" = None,
    body_track_mask: "np.ndarray | None" = None,
    nipple_bridge: "list | None" = None,
    body_tris: "np.ndarray | None" = None,
) -> TriFile:  # noqa: docstring continues below
    """Build a BODYTRI for the armor by propagating UBE body slider deltas.

    Performance note: KDTree is built once per armor shape and K-NN queried
    once; per-morph propagation is vectorized numpy (gather + multiply + sum).
    This is ~10x faster than the naive per-shape-per-morph rebuild.

    `armor_shapes`: {shape_name -> verts_array (N, 3)} for armor pieces
                    in the destination NIF. Body deltas get propagated
                    to these verts via K-nearest-body-vertex IDW.
    `body_verts`: UBE body verts (used both for KD lookup and to copy
                  body-shape morphs verbatim when include_body_shapes=True)
    `body_osd`: parsed UBE body OSD (morph names like
                'BaseShape<SliderName>')
    `body_shape_name`: prefix in OSD morph names — stripped to produce
                      the slider name RaceMenu uses to look up morphs
    `include_body_shapes`: which body-region shapes to embed verbatim
                          (carrying body OSD morphs unmodified). Pass
                          a set of shape names to include only those
                          (e.g. {"BaseShape"} when the dest NIF has
                          BaseShape but not VirtualBody). Pass True
                          for the default {"BaseShape"}. Pass False
                          to omit body shapes entirely (correct for
                          armors like a slot-49 no-body cloth armor whose NIF has no
                          BaseShape — hand-built TRIs for these only
                          contain armor-piece entries).
    `extra_body_osds`: {shape_name -> OsdFile} for additional body-
                       region shapes (Hands, Feet) that have their
                       own OSDs. The morphs are emitted verbatim as
                       extra TriShapes in the per-armor TRI, so
                       NioOverride applies the same slider deltas to
                       e.g. an injected Hands shape inside an
                       equipped armor NIF that it applies to the
                       nude Hands NIF via its own TRI. Without this,
                       Hands/Feet inside armor stay at default while
                       nude Hands/Feet morph per RaceMenu sliders.

    `also_named`: {shape name -> a SECOND name to emit the same morph
                   table under}. One tri serves both halves of a `_0`/`_1`
                   pair, and the author may have named the two halves'
                   shapes differently, in which case the half the tri was
                   not built from names nothing in it and morphs nothing.
                   Built by `nif_convert_trigen.pair_shape_aliases`, which
                   is where the topology precondition is proven; here it is
                   a pure alias. #pair-tri-names

    Output TRI: one TriShape per armor shape (plus optional BaseShape).
    Each TriShape has TriMorphs named after the body slider — e.g.
    'BreastsBigger', 'BigButt'. Per-shape morphs share the slider name,
    which is what NioOverride looks up at runtime.
    """
    prefix = body_shape_name
    body_verts_arr = np.asarray(body_verts, dtype=np.float64)
    body_n = len(body_verts_arr)

    tree = cKDTree(body_verts_arr)

    # If a shape appears in both armor_shapes and extra_body_osds, keep only the
    # OSD path (authoritative) to avoid double-emitting a TriShape.
    extra_names = set(extra_body_osds.keys()) if extra_body_osds else set()
    # Keep the original set so the extras-emit pass still recognizes "present in dst".
    original_armor_names = set(armor_shapes)
    armor_shapes = {
        name: verts for name, verts in armor_shapes.items()
        if name not in extra_names
    }

    # Precompute per-shape K-NN data with K=16 neighbors. K is FIXED -- there is
    # no adaptive-K selection anywhere; what varies per vert is (a) the IDW
    # exponent below and (b) the body-motion-match blend, which at stand-off
    # <= _MATCH_NEAR REPLACES the whole IDW average with neighbors[:, 0] alone.
    # So for any hugging vert this support set is computed and then thrown away.
    # Do not reason about QUERY_K as the lever for cross-layer morph shear
    # (#bug-01): both layers of a fitted garment sit inside the pure-copy zone,
    # where widening or narrowing K changes NOTHING. Measured 2026-08-22 on the
    # real UBE body: inner layer stand-off 1.26u, outer 3.12u, match_w 1.000 on
    # 100% of both.
    QUERY_K = 16
    qk = min(QUERY_K, body_n)
    shape_knn: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for armor_shape_name, av in armor_shapes.items():
        av_arr = np.asarray(av, dtype=np.float64)
        dists, neighbors = tree.query(av_arr, k=qk)
        if qk == 1:
            dists = dists[:, None]
            neighbors = neighbors[:, None]
        # Distance from each armor vert to its nearest body vert
        # — used per-vert below to decide which K to use.
        nearest_dist = dists[:, 0]
        shape_knn[armor_shape_name] = (neighbors, dists, nearest_dist)

    # Outward direction per armor vert (from its nearest body vert), for
    # #drape-morph-headroom. Per shape, slider-independent.
    shape_outward: dict[str, np.ndarray] = {}
    for armor_shape_name, (neighbors, dists, nearest_dist) in shape_knn.items():
        _av = np.asarray(armor_shapes[armor_shape_name], dtype=np.float64)
        _u = _av - body_verts_arr[neighbors[:, 0]]
        _u /= np.maximum(np.linalg.norm(_u, axis=1, keepdims=True), 1e-9)
        shape_outward[armor_shape_name] = _u

    # Dense body delta buffer reused across morphs (avoids re-allocating per shape×morph).
    morph_delta_buf = np.zeros((body_n, 3), dtype=np.float64)
    # Per-shape morph accumulator.
    shape_morphs: dict[str, list[TriMorph]] = {
        name: [] for name in armor_shapes
    }

    # Body-motion match: per-shape weight on the nearest-body-vert delta, so a
    # hugging armor vert copies the body surface it covers (ratio 1.0, clearance
    # preserved) while drape cloth keeps the smoothed IDW average. Depends only
    # on stand-off, not the slider -- computed once per shape, reused per morph.
    shape_match: dict[str, np.ndarray] = {}
    if _BODY_MOTION_MATCH:
        for armor_shape_name, (neighbors, dists, nearest_dist) in shape_knn.items():
            shape_match[armor_shape_name] = _motion_match_weight(nearest_dist)[:, None]
    # #morph-surface-sample: where the matched delta is sampled (see the flag).
    shape_bary: dict = {}
    # the triangles must index THIS body's verts (a reference body with another
    # vert count would sample the wrong skin)
    if (_BODY_MOTION_MATCH and _MORPH_SURFACE_SAMPLE and body_tris is not None
            and len(body_tris) and int(np.max(body_tris)) < body_n):
        for armor_shape_name in shape_knn:
            try:
                shape_bary[armor_shape_name] = _closest_surface_bary(
                    np.asarray(armor_shapes[armor_shape_name], dtype=np.float64),
                    body_verts_arr, body_tris)
            except Exception as _e:
                from .nif_convert_telemetry import _note_pass_failure
                _note_pass_failure("morph-surface-sample", _e)

    # The IDW weights are per-SHAPE too, for the same reason: `p` and `w` depend
    # only on the K-NN distances, which do not change when the slider changes.
    # They were being rebuilt INSIDE the morph loop -- a `np.power` with a
    # per-row exponent plus a normalisation over every armour vert, recomputed
    # once per slider (~40x on a real body OSD) to produce identical numbers
    # every time.
    shape_w: dict[str, np.ndarray] = {}
    for armor_shape_name, (neighbors, dists, nearest_dist) in shape_knn.items():
        # Continuous adaptive IDW: exponent p eases from 2 (body-hugging,
        # nearest dominates) to 1 (stand-off/drape, wide average). This avoids
        # the crease artifacts that discrete threshold zones produced at their
        # boundaries. p = clip(2 - nearest_dist, 1, 2).
        _p = np.clip(2.0 - nearest_dist, 1.0, 2.0)
        _w = 1.0 / (np.power(dists, _p[:, None]) + 1e-9)
        _w /= _w.sum(axis=1, keepdims=True)
        shape_w[armor_shape_name] = _w

    # #surface-morph-track: per-shape system, built once (see the constants).
    # Needs the triangles and a region; without them it is a no-op.
    shape_track: dict = {}
    if (_SURFACE_MORPH_TRACK and _BODY_MOTION_MATCH and armor_shape_tris
            and body_track_mask is not None
            and len(body_track_mask) == body_n):
        _mask = np.asarray(body_track_mask, dtype=bool)
        for armor_shape_name, (neighbors, dists, nearest_dist) in shape_knn.items():
            _t = armor_shape_tris.get(armor_shape_name)
            if _t is None:
                continue
            _free = (shape_match[armor_shape_name][:, 0] >= 1.0) & _mask[neighbors[:, 0]]
            try:
                _s = _surface_track_solver(
                    np.asarray(armor_shapes[armor_shape_name], dtype=np.float64),
                    _t, _free, body_verts_arr, tree, _mask)
            except Exception as _e:
                from .nif_convert_telemetry import _note_pass_failure
                _note_pass_failure("surface-morph-track", _e)
                _s = None
            if _s is not None:
                shape_track[armor_shape_name] = _s

    # #nipple-morph-bridge: per-shape prep, built once (see the constants).
    shape_nmb: dict = {}
    _nmb_local = _nmb_breast = None
    shape_env: dict = {}
    if (_NIPPLE_MORPH_BRIDGE or _NIPPLE_MORPH_ENVELOPE) and nipple_bridge:
        # body verts at the nipple core vs the band around the areola
        _dm = np.full(len(body_verts_arr), np.inf)
        for _ap, _nA, _area in nipple_bridge:
            _dm = np.minimum(_dm, np.linalg.norm(
                body_verts_arr - body_verts_arr[_ap], axis=1))
        _nmb_tree = cKDTree(body_verts_arr)
        _nmb_local = np.flatnonzero(_dm < NIPPLE_MORPH_CORE_R)
        _nmb_breast = np.flatnonzero((_dm >= NIPPLE_MORPH_FAR_R[0])
                                     & (_dm < NIPPLE_MORPH_FAR_R[1]))
        for armor_shape_name in shape_knn:
            try:
                _av = np.asarray(armor_shapes[armor_shape_name], dtype=np.float64)
                _p = None
                _st = (armor_shape_tris or {}).get(armor_shape_name)
                if _nipple_morph_fine_enough(_av, _st, body_verts_arr,
                                             nipple_bridge):
                    if _NIPPLE_MORPH_BRIDGE:
                        _p = _nipple_morph_bridge_prep(
                            _av, body_verts_arr, nipple_bridge, tree=_nmb_tree)
                elif _NIPPLE_MORPH_ENVELOPE and _st is not None:
                    _e = _nipple_morph_envelope_prep(
                        _av, _st, body_verts_arr, nipple_bridge, _nmb_tree)
                    if _e is not None:
                        shape_env[armor_shape_name] = _e
            except Exception as _e:
                from .nif_convert_telemetry import _note_pass_failure
                _note_pass_failure("nipple-morph-bridge", _e)
                _p = None
            if _p is not None:
                shape_nmb[armor_shape_name] = _p

    for body_m in body_osd.morphs:
        if not len(body_m):
            continue
        slider_name = (body_m.name[len(prefix):]
                       if body_m.name.startswith(prefix)
                       else body_m.name)

        idxs = body_m.idx.astype(np.int64)
        deltas = body_m.delta.astype(np.float64)
        valid = (idxs >= 0) & (idxs < body_n)
        morph_delta_buf.fill(0.0)
        morph_delta_buf[idxs[valid]] = deltas[valid]

        for armor_shape_name, (neighbors, dists, nearest_dist) in shape_knn.items():
            w = shape_w[armor_shape_name]          # hoisted: see above
            # einsum fuses the weight-multiply and the sum. The previous form
            # built an (n, K, 3) temporary for the product and then reduced it,
            # so it allocated the gathered array twice over.
            propagated = np.einsum("nkj,nk->nj", morph_delta_buf[neighbors], w)
            # Body-motion match: blend toward the delta of the body vertex this
            # armor vert COVERS, so hugging armor moves exactly as that surface
            # does (clearance preserved -> the body can't poke through, and the
            # armor can't balloon past it). The IDW average alone dilutes to
            # 0.59-0.81x on a fitted cuirass. Drape cloth (far stand-off) keeps
            # the smoothed average. See _motion_match_weight.
            match_w = shape_match.get(armor_shape_name)
            if match_w is not None:
                _by = shape_bary.get(armor_shape_name)
                if _by is not None:
                    nearest_delta = np.einsum(
                        "nk,nkj->nj", _by[1], morph_delta_buf[_by[0]])
                else:
                    nearest_delta = morph_delta_buf[neighbors[:, 0]]
                propagated = match_w * nearest_delta + (1.0 - match_w) * propagated
            # #drape-morph-headroom (see the constant): drape cloth moves only
            # by the body growth that actually reaches it.
            if _DRAPE_MORPH_HEADROOM:
                _od = shape_outward.get(armor_shape_name)
                if _od is not None:
                    propagated = propagated * _drape_headroom_scale(
                        propagated, morph_delta_buf[neighbors[:, 0]], _od,
                        nearest_dist)[:, None]
            # #surface-morph-track: the hugging corners take the deltas that make
            # the SURFACE follow the body points it covers.
            _tk = shape_track.get(armor_shape_name)
            if _tk is not None:
                _solve, _fi, _smp, _sw, _Af, _fw, _cor = _tk
                # residual of the DONOR answer at the samples; solve for the
                # smooth correction that removes it
                _r = (morph_delta_buf[_smp] * _sw[:, None]
                      - np.einsum("sk,skj->sj", _fw, propagated[_cor])
                      - _Af @ propagated[_fi])
                _rhs = _Af.T @ _r
                propagated = propagated.copy()
                # one coupled solve over the three axes (the rim lock couples them)
                _corr = _solve(np.ascontiguousarray(_rhs.T.ravel())).reshape(3, -1).T
                propagated[_fi] += _corr
            _nb = shape_nmb.get(armor_shape_name)
            if _nb is not None:
                propagated = _nipple_morph_bridge_apply(
                    propagated, _nb, morph_delta_buf, _nmb_local, _nmb_breast)
            _ev = shape_env.get(armor_shape_name)
            if _ev is not None:
                propagated = _nipple_morph_envelope_apply(
                    propagated, _ev, morph_delta_buf, _nmb_local, _nmb_breast)
            # Extremity dampening: scale propagated deltas by (1 - extremity_fraction)
            # so sleeve/calf verts get full body-morph response while
            # finger/toe verts stay near-zero (matching the separate nude Hands/Feet
            # mesh that doesn't body-morph). No-op when fractions not supplied.
            if armor_vert_extremity_fractions is not None:
                ef = armor_vert_extremity_fractions.get(armor_shape_name)
                if ef is not None and len(ef) == len(propagated):
                    propagated = propagated * np.clip(
                        1.0 - np.asarray(ef, dtype=np.float64), 0.0, 1.0
                    )[:, None]
            magnitudes = np.linalg.norm(propagated, axis=1)
            keep = np.where(magnitudes >= min_delta)[0]
            if len(keep) == 0:
                continue
            kept_d = propagated[keep]
            # The arrays go straight into the morph: no tuple per vertex, and
            # the writer quantises these exact float64 values. #osd-columnar
            shape_morphs[armor_shape_name].append(
                TriMorph(name=slider_name, idx=keep, delta=kept_d))

    # ---- Overlay-band morph-sync ----
    # A thin band lifted above a larger cloth layer must morph in lockstep with
    # that layer; otherwise it re-sinks under body sliders. For each thin band
    # sitting clearly outside another cloth shape, replace its per-slider deltas
    # with the under-layer's nearest-vertex deltas. Fully gated: if the band
    # can't be classified it no-ops and the base lift still stands.
    #
    # NOT for body-hugging bands. This pass exists because the bare IDW diluted
    # each shape by its OWN stand-off, so a band and its layer moved by different
    # amounts and the band re-sank. Body-motion match removes THAT cause: every
    # hugging shape now copies the body exactly (ratio 1.0). Re-syncing them here
    # instead OVERWRITES the exact match with the under-layer's delta sampled at a
    # different body location -- measured on a steel cuirass: the breast plates
    # (small enough to look like bands) dropped to 0.85x while the one shape too
    # large to qualify held 1.00x, and the body poked through the breasts. Only
    # bands genuinely lifted off the body (beyond the pure-copy zone) still need it.
    #
    # CORRECTION 2026-08-22 -- this comment used to go one step further and claim
    # that because each hugging shape copies the body exactly, "a hugging band and
    # its hugging layer already move in lockstep". THAT INFERENCE IS FALSE and it
    # is #bug-01. Ratio 1.0 each does NOT imply lockstep with each other: the two
    # layers copy DIFFERENT body verts, and where the outer layer's nearest-vert
    # lookup flips to another patch of anatomy the two deltas diverge by the full
    # difference between those patches (measured up to 4.5u under the big-breast
    # sliders). The gate below is still RIGHT -- syncing hugging bands regressed a
    # real cuirass in game -- but it is right for the dilution/poke reason above,
    # NOT because hugging layers track each other. Do not cite lockstep here.
    try:
        from scipy.spatial import cKDTree as _cKDTree
        _OM_SIZE_FRAC, _OM_R, _OM_MIN, _OM_THRESH, _OM_SYNC_R = 0.40, 3.0, 30, 0.20, 5.0
        _names = list(armor_shapes)
        _maxv = max((len(np.asarray(v)) for v in armor_shapes.values()), default=0)
        for A in _names:
            av = np.asarray(armor_shapes[A], dtype=np.float64)
            if _maxv <= 0 or len(av) >= _OM_SIZE_FRAC * _maxv or A not in shape_knn:
                continue   # large body-conforming piece -> keep own morph
            a_nd = shape_knn[A][2]   # A's per-vert clearance to the body
            if _BODY_MOTION_MATCH and float(np.median(a_nd)) <= _MATCH_NEAR:
                continue   # hugging band already tracks the body exactly
            under_v, under_tag = [], []
            for B in _names:
                if B == A or B not in shape_knn:
                    continue
                bv = np.asarray(armor_shapes[B], dtype=np.float64)
                b_nd = shape_knn[B][2]
                dd, ui = _cKDTree(bv).query(av, k=1, distance_upper_bound=_OM_R)
                ov = np.isfinite(dd)
                if int(ov.sum()) < _OM_MIN:
                    continue
                if float(np.median((a_nd - b_nd[np.where(ov, ui, 0)])[ov])) < _OM_THRESH:
                    continue   # A does not sit clearly outside B
                under_v.append(bv)
                under_tag.extend((B, int(i)) for i in range(len(bv)))
            if not under_v:
                continue
            ddu, idu = _cKDTree(np.vstack(under_v)).query(
                av, k=1, distance_upper_bound=_OM_SYNC_R)
            okv = np.isfinite(ddu)
            if not okv.any():
                continue
            dense = {}
            for Bn in {t[0] for t in under_tag}:
                dB = {}
                for m in shape_morphs.get(Bn, []):
                    a2 = np.zeros((len(armor_shapes[Bn]), 3), dtype=np.float64)
                    _in = (m.idx >= 0) & (m.idx < len(a2))
                    a2[m.idx[_in]] = m.delta[_in]
                    dB[m.name] = a2
                dense[Bn] = dB
            # A's OWN propagated per-slider deltas, preserved where A does NOT
            # overlap an under-layer. A garment caught by the size gate but only
            # PARTLY overlapping (e.g. a shirt whose sleeves sit over undersleeves
            # but whose chest has no cloth under-layer) must keep its own
            # chest/breast morphs -- replacing ALL of them with the (arm-only)
            # under-layer sync was silently dropping the chest conform.
            own_dense = {}
            for m in shape_morphs.get(A, []):
                a2 = np.zeros((len(av), 3), dtype=np.float64)
                _in = (m.idx >= 0) & (m.idx < len(a2))
                a2[m.idx[_in]] = m.delta[_in]
                own_dense[m.name] = a2
            sync_idx = np.where(okv)[0]
            all_sliders = set(own_dense)
            for Bn in dense:
                all_sliders |= set(dense[Bn])
            new_morphs = []
            for sl in sorted(all_sliders):  # sorted: #deterministic-set-iteration
                base_arr = own_dense.get(sl)
                arr = (base_arr.copy() if base_arr is not None
                       else np.zeros((len(av), 3), dtype=np.float64))
                # MERGE: sync ONLY the overlapping verts to the under-layer
                # (lockstep); a vert whose under-layer lacks this slider follows
                # it to zero. Non-overlapping verts keep A's own morph.
                for vi in sync_idx:
                    Bn, Bi = under_tag[idu[vi]]
                    da = dense.get(Bn, {}).get(sl)
                    arr[vi] = da[Bi] if da is not None else 0.0
                mag = np.linalg.norm(arr, axis=1)
                keep = np.where(mag >= min_delta)[0]
                if len(keep):
                    new_morphs.append(TriMorph(name=sl, idx=keep, delta=arr[keep]))
            if new_morphs:
                shape_morphs[A] = new_morphs
    except Exception:
        pass

    # Order: carrier first (NiStringExtraData convention), then cloth by
    # descending morph magnitude, then rigid props, then weapon/jewelry attachments.
    # If the per-NIF morph cap ever fires, low-impact rigid props are dropped first.
    def _max_morph_mag(name: str) -> float:
        mags = [
            float(np.linalg.norm(m.delta, axis=1).max())
            for m in shape_morphs.get(name, []) if len(m)
        ]
        return max(mags) if mags else 0.0

    def _priority_key(name: str):
        nlow = name.lower()
        if any(kw in nlow for kw in _ATTACHMENT_KEYWORDS):
            tier = 2
        elif any(kw in nlow for kw in _RIGID_PROP_KEYWORDS):
            tier = 1
        else:
            tier = 0
        return (tier, -_max_morph_mag(name))

    non_carrier = [n for n in armor_shapes if n != carrier_shape_name]
    non_carrier.sort(key=_priority_key)
    ordered_names: list[str] = []
    if carrier_shape_name is not None and carrier_shape_name in armor_shapes:
        ordered_names.append(carrier_shape_name)
    ordered_names.extend(non_carrier)

    tri_shapes: list[TriShape] = []
    for armor_shape_name in ordered_names:
        morphs = shape_morphs[armor_shape_name]
        if morphs:
            tri_shapes.append(TriShape(
                name=armor_shape_name, morphs=morphs))

    # Include body-region shapes verbatim from the OSD only when the dest NIF
    # actually contains them. Adding a shape the NIF lacks confuses NioOverride lookups.
    body_shape_set: set[str]
    if include_body_shapes is True:
        body_shape_set = {"BaseShape"}
    elif include_body_shapes is False:
        body_shape_set = set()
    else:
        body_shape_set = set(include_body_shapes)
    if body_shape_set:
        body_morphs: list[TriMorph] = []
        for body_m in body_osd.morphs:
            if body_m.name.startswith(prefix):
                slider_name = body_m.name[len(prefix):]
            else:
                slider_name = body_m.name
            if not len(body_m):
                continue
            # Bounds-filter verbatim OSD indices to the target vert count, like
            # the K-NN path (`valid = idxs < body_n`). body_verts shares the
            # injected BaseShape's topology, so an OSD index >= body_n is a stale
            # OSD/mesh mismatch that would write an out-of-range vertex into the
            # TRI -> NioOverride reads past the buffer at runtime (CTD / silent
            # bad morph). Drop the OOB entries. #osd-bounds
            _in = body_m.idx < body_n            # uint16: never negative
            if not _in.any():
                continue
            body_morphs.append(TriMorph(name=slider_name,
                                        idx=body_m.idx[_in], delta=body_m.delta[_in]))
        if body_morphs:
            if "BaseShape" in body_shape_set:
                tri_shapes.insert(0, TriShape(
                    name="BaseShape", morphs=body_morphs))

    # Extra body-region OSDs (Hands/Feet): emit a TriShape per shape with morphs
    # renamed to strip the prefix so slider lookups match the nude actor's TRI.
    if extra_body_osds:
        for shape_name, extra_osd in extra_body_osds.items():
            if (shape_name not in original_armor_names
                    and shape_name not in body_shape_set):
                continue  # no matching shape in the dst NIF
            extra_prefix = shape_name  # OSD morphs prefix == shape name
            # Bound OSD indices to the injected shape's vert count when we have it
            # (the dst Hands/Feet shape is in armor_shapes); else fall back to the
            # uint16 ceiling TriFile.save enforces. #osd-bounds
            _cap = (len(armor_shapes[shape_name])
                    if shape_name in armor_shapes else None)
            extra_morphs: list[TriMorph] = []
            for m in extra_osd.morphs:
                if not len(m):
                    continue
                slider_name = (m.name[len(extra_prefix):]
                               if m.name.startswith(extra_prefix)
                               else m.name)
                _in = (m.idx < _cap) if _cap is not None else np.ones(len(m), dtype=bool)
                if not _in.any():
                    continue
                extra_morphs.append(TriMorph(
                    name=slider_name, idx=m.idx[_in], delta=m.delta[_in]))
            if extra_morphs:
                tri_shapes.append(TriShape(
                    name=shape_name, morphs=extra_morphs))

    # #pair-tri-names -- the `_0`/`_1` halves of one garment are one mesh saved
    # twice, but the author may have named their shapes differently. ONE tri
    # serves both halves, so a shape whose partner carries a different name
    # needs its morph table under BOTH names or that half morphs nothing. The
    # caller has already proven the topology matches index for index (see
    # `nif_convert_trigen.pair_shape_aliases`), so the same delta table is
    # correct for both -- this is an ALIAS, not a second computation.
    if also_named:
        by_name = {sh.name: sh for sh in tri_shapes}
        for mine, theirs in sorted(also_named.items()):
            sh = by_name.get(mine)
            if sh is None or theirs in by_name:
                continue                 # nothing to alias, or it would shadow
            alias = TriShape(name=theirs, morphs=list(sh.morphs))
            tri_shapes.append(alias)
            by_name[theirs] = alias

    # The `version` kwarg is effectively cosmetic: bytes 4-5 are the shape count
    # written from `len(self.shapes)` by TriFile.save() regardless.
    from .tri import TRI_VERSION
    return TriFile(version=TRI_VERSION, shapes=tri_shapes)
