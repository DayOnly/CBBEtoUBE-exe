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

"""PACK CENSUS of the SINGLE-THIGH SWING class: garment vertices over the crotch
and gluteal band that carry ONE thigh's weight over skin that moves with neither,
so one leg swinging drags them into the buttock and the inner thigh.

    python scripts/analysis/single_swing_census.py [--out rows.jsonl] [--limit N]
                                                   [--workers N] [--pack <meshes/!UBE>]
    python scripts/analysis/single_swing_census.py --self-check

WHY THIS AND NOT THE POSE SET. The multipose harness judges a whole pose on a
sampled region with an all-triangle ray test; a crotch panel that swings 2.5u
into the cleft on one leg's stride read 0.75% there, because rays from the cleft
still hit the OTHER greave. This census measures the quantity the defect is in:
for every body vertex in the band (z 55-75, |x| < 8) covered by a garment vertex
within 6u on the outward side, the clearance LOSS to that same garment vertex
after the LEFT thigh alone flexes 45 degrees, then the RIGHT alone. Loss p90 per
piece, per side, plus the share of covered vertices that end inside the garment
having been outside at bind.

THE DISCRIMINATING PROPERTY is printed beside it: over body vertices that are
pelvis-only (Pelvis > 0.9), the covering garment vertex's one-sided thigh weight
|wL - wR|. Measured 2026-09-17 on 224 body-swap pieces: pieces with a p90 above
0.3 lose a median 1.00u, the rest 0.27u.

The SOURCE garment is scored the same way on the canonical CBBE/3BA body when a
source matches by garment shape names, so "inherited" and "introduced" can be
told apart per piece. Every excluded piece is counted under a named reason; a
run that scores nothing exits 2 rather than printing a clean table.

The canonical CBBE/3BA body is BodySlide's zeroed 3BA build since 2026-09-21
(canonical_body). The preset build it replaced overstated the SOURCE loss: over
the same n=200 source-matched pieces, source loss-p90 median 0.70 -> 0.56u and
source pieces over 1u 66 -> 39 (converted 38), so "better after conversion
(-0.3u)" fell 60 -> 37 and "worse (+0.3u)" rose 11 -> 28. The converted side
did not move.

THREE BANDS, AND WHY ONLY THE PAIRED ONE IS COMPARED ACROSS BODIES (2026-09-26).
Every arm is scored three times:

  full band (the row's top-level keys) -- LEGACY, unchanged byte for byte. The
      source arm is the main body shape ALONE. Never subtract it across CBBE and
      UBE: UBE models a dense midline slit (16% of the covered band vertices on
      1.3% of the area, sideways normals, 59.5% of band vertices in self-contact
      against 23% on CBBE), and a body->nearest-garment-vertex clearance reads
      CBBE-shaped cloth deeper there with NO conversion error: a perfect warped
      shell reads ours-author p05 -0.50u on this band (the shipped pack's
      median read -0.86u). The crotch
      band 'we bury it deeper than the author' lead was this artefact.
  visible band (the row's `vis` block) -- the same census over band skin whose
      outward normal ray leaves the body within 6u (`_census_common.
      visible_skin`: body-only, one rule on both bodies). The source body is
      CLOSED first: the zeroed 3BA body ships the vulva and anus as separate
      COMPANION shapes (`<body>_Vagina` 1905 verts, `<body>_Anus` 201), so the
      main shape alone is open there and its rim reads as skin nothing covers.
      Each arm is scored on its OWN visible skin, so this band is still NOT
      like-for-like: a source garment lying inside the companion vulva skin is
      scored there, and UBE has no such skin to score its conversion on.
  paired band (the row's `pair` block) -- skin visible on BOTH bodies
      (`paired_skin`): a source main-shape vertex visible on the closed body
      whose nearest UBE vertex (the correspondence the warp uses) is visible on
      UBE, and a UBE vertex visible on UBE whose nearest source vertex is paired.
      The ONLY band the source-vs-converted lines and the bind ours - author
      difference are read on; the other bands print them under a warning.

SOURCE VS CONVERTED IS COMPARED PER LEG as well as per worse side (`leg_compare`).
Each arm's worse leg can be a DIFFERENT leg: on the shipped pack (2026-09-26,
paired band) five sourced pieces gained 0.33-0.47u on one leg while the other
fell, and the worse-side line read them as unchanged. Each row carries `src_path`, the file the source arm
was scored on -- find_source pairs by path and garment shape names, and where
several mods ship the path that is not always the file the converter read.

THE SELF-CHECK runs on every invocation, before any piece is read, and the
tool REFUSES to report (exit 4) when it fails. A CLOSED shell 0.5u off the
source body's crotch region (holes capped with a fan, so it spans the vulva as
a garment does) is pushed through the converter's OWN warp
(`_cached_cbbe_to_ube_delta` + `warp_armor_by_body_delta`) and scored on UBE,
on the visible band AND the paired band. That is a perfect conversion by
construction, so at bind each band must read ours - author within 0.15u at p05
and p50, and the same shell pushed 0.5u into UBE must read at least 0.3u deeper
at p50, or the instrument cannot see burial. POSED: each arm's shell is skinned
with its own body's weights (the author's with the source body's, ours with
UBE's at the warp's correspondence), so it rides the body; the single 45-degree
swing loss p90 must read ours - author within 0.1u on each side, with the real
skeleton, and the same shell with its midline riding one thigh on our arm only
must read at least 0.3u worse (the "worse after conversion" line), or the
instrument cannot see a posed skew. The perfect-shell posed row holds only
while the two bodies' thigh painting is close (see `self_check`): past the
tested range it fails closed, never open. Measured 2026-09-26 (zeroed weight-1
bodies, real skeleton):

    visible  bind perfect -0.057 / -0.026   buried -0.280 / -0.511
             swing perfect L -0.009 R -0.007   one-thigh plant +1.651
    paired   bind perfect +0.000 / -0.014   buried -0.309 / -0.509
             swing perfect L -0.011 R -0.010   one-thigh plant +1.643
    the same shell KEEPING the source weights on UBE: L +0.073 R +0.075
             (information -- the two bodies' weight painting alone)

Planted and refused (exit 4), live: a shell not warped at all -0.35 / -0.31
and one scaled 2% -0.31 / +0.18 (bind, prototype); the author's shell with its
midline on the left thigh, swing L -1.248 (visible); the author arm without its
companion shapes, bind p05 -0.184 (visible -- the posed rows read -0.017 and do
not see it); bodies read with no thigh bones, one-thigh plant +0.000. The
census's older control (the UBE body shifted along its OWN normals) passed on
the artefact: no CBBE garment can follow the slit.

Read-only. Needs a skeleton NIF (CBBE2UBE_SKELETON_NIF, else the discovered
XPMSSE skeleton) -- an armour NIF's bone list is flat, so without a real
skeleton nothing below the hip poses and every piece reads clean.

WEIGHTS: `_1` only -- a DECLARED BLIND SPOT, and the one excluded population the
"counted under a named reason" rule above never counts. A piece here is one
FILE: its own verts, skin weights and injected body. That is not a rate the two
weights share, so a single-swing defect at weight 0 is invisible. The 224 pieces
cited above are weight-1 files.

DO NOT fix it by swapping the glob. The CONVERTED side is measured on the body
injected into the same NIF, which would be correct for a `_0` file -- but the
SOURCE side, which is what separates "inherited" from "introduced", is posed on
`canonical_cbbe()` called with no weight -- i.e. weight 1 -- and the module-level
`_CBBE` holds that one body for every file. A `_0` source garment would be posed
on the weight-1 CBBE body, and the source-vs-converted block would pool those
mismatched comparisons into a believable number.

To close it: pass each file's own weight suffix to `canonical_cbbe(weight=...)`
(it already resolves the zeroed body per weight) and cache `_CBBE` per weight,
keep this file's first-person stem rule on top of `output_nifs` (it is broader
than that helper's regex), then widen. This census feeds an open investigation;
widening it will move numbers that investigation has already recorded, so do it
as its own change with an old-vs-new comparison.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                             # noqa: E402
from src import nif_convert as nc                                   # noqa: E402
from src import paths                                               # noqa: E402
from scripts.analysis.posed_clip_test import (                      # noqa: E402
    read_skin, bone_parents, build_pose, apply_pose, DEFAULT_SKELETON)
from scripts.analysis.pose_set import LT, RT                         # noqa: E402
from scripts.analysis.canonical_body import (                        # noqa: E402
    converted_garment_names, find_source, canonical_cbbe, canonical_ube)
from scripts.analysis._census_common import visible_skin             # noqa: E402

LTH, RTH, PEL = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]"
BAND_Z = (55.0, 75.0)
BAND_X = 8.0
REACH = 6.0
SWING_DEG = 45.0
_PAR = None
_CBBE = None

# The self-check: a closed shell SHELL_D off the source crotch region, warped by
# the converter, must read ours - author within SELF_CHECK_TOL on the visible
# band; buried SELF_CHECK_BURY into UBE it must read SELF_CHECK_MUST_FAIL or
# deeper at p50. A failure exits SELF_CHECK_EXIT before any piece is read.
SHELL_D = 0.5
SHELL_Z = (45.0, 85.0)
SHELL_X = 16.0
HOLE_MARGIN = 2.0
SELF_CHECK_TOL = 0.15
SELF_CHECK_BURY = 0.5
SELF_CHECK_MUST_FAIL = -0.3
SELF_CHECK_EXIT = 4
# The POSED rows of the self-check: the same shell skinned with each arm's own
# body weights, carried by the warp's correspondence, must read ours - author
# swing loss p90 within SELF_CHECK_POSED_TOL on each side; the shell with its midline
# (|x| < POSED_PLANT_X over the band) riding the left thigh on OUR arm only must
# read at least SELF_CHECK_POSED_MUST_SEE worse -- the census's own "worse after
# conversion" line -- or the instrument cannot see a posed skew.
SELF_CHECK_POSED_TOL = 0.1
SELF_CHECK_POSED_MUST_SEE = 0.3
POSED_PLANT_X = 2.0
# A census body is matched to the reference body the paired mask was built on
# vertex for vertex; a band vertex farther than this from every reference
# vertex means a different body, and that arm gets no paired block.
PAIR_MATCH = 1e-3
COVER_SHARE = 0.9
# Source vs converted: a leg (or a worse side) "loses more" / "less" after
# conversion when it differs by more than this.
CMP_MARGIN = 0.3
_VIS: dict = {}
_PAIR: dict = {}


def _skeleton() -> str:
    skel = os.environ.get("CBBE2UBE_SKELETON_NIF", "").strip() or DEFAULT_SKELETON
    if not skel or not Path(skel).is_file():
        raise SystemExit("REFUSED: no skeleton NIF -- set CBBE2UBE_SKELETON_NIF")
    return skel


def _normals(V, T):
    N = np.zeros_like(V)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(b - a, c - a)
    for k in range(3):
        np.add.at(N, T[:, k], fn)
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return N / ln


def band_mask(V):
    z, x = V[:, 2], V[:, 0]
    return (z >= BAND_Z[0]) & (z <= BAND_Z[1]) & (np.abs(x) < BAND_X)


def body_shapes(nif, name):
    """The body shape `name` first, then its COMPANION shapes: every shape in the
    same file named `<name>_<anything>`. The zeroed 3BA body carries the vulva
    and the anus that way; a body without companions returns just itself."""
    main = [s for s in nif.shapes if s.name == name]
    return main + [s for s in nif.shapes if s.name.startswith(name + "_")]


def merged_skin(shapes):
    """`read_skin` over several shapes as ONE mesh: verts and tris concatenated,
    each bone's weights zero outside the shapes that carry it. A bone's bind
    origin (the pose pivot) is taken from the first shape that has it -- the
    main body, which `body_shapes` puts first."""
    parts = [read_skin(s) for s in shapes]
    if len(parts) == 1:
        return parts[0]
    n = sum(len(p[0]) for p in parts)
    W: dict = {}
    Vs, Ts, off = [], [], 0
    for v, t, w in parts:
        for bone, (wt, origin) in w.items():
            if bone not in W:
                W[bone] = (np.zeros(n), origin)
            W[bone][0][off:off + len(v)] = wt
        Vs.append(v)
        Ts.append(np.asarray(t, np.int64).reshape(-1, 3) + off)
        off += len(v)
    return np.concatenate(Vs), np.concatenate(Ts), W


def visible_band(V, T, N=None):
    """Band vertices of this body whose normal ray escapes the body (cached per
    body; the ray cast costs seconds and every piece shares a few bodies)."""
    V = np.ascontiguousarray(V, np.float64)
    T = np.ascontiguousarray(T, np.int64)
    key = hashlib.sha1(V.tobytes() + T.tobytes()).hexdigest()
    m = _VIS.get(key)
    if m is None:
        if N is None:
            N = _normals(V, T)
        m = visible_skin(V, T, N, band_mask(V), reach=REACH)
        _VIS[key] = m
    return m


def paired_skin(sV, cV, cT, uV, uT, uN=None):
    """Band skin VISIBLE ON BOTH BODIES -> (mask on the closed source body, mask
    on UBE). The like-for-like skin for any ours - author number.

    `sV` is the source MAIN shape, `cV, cT` that body closed with its companion
    shapes (main vertices first, as `merged_skin` orders them). A source vertex
    is paired when it is visible on the closed source body and its nearest UBE
    vertex -- the correspondence `_cached_cbbe_to_ube_delta` warps by -- is
    visible on UBE. A UBE vertex is paired when it is visible on UBE and its
    nearest source main-shape vertex is PAIRED (the round trip). Companion
    vertices have no correspondence and are never paired: the closed body only
    decides what they HIDE. Each arm keeps its own vertices, so neither body is
    resampled.

    Why the round trip: with "its nearest source vertex is visible" instead,
    90 UBE vertices on the slit mouth (x ~ 0, z 65-72) pass -- visible on UBE,
    nearest a visible source vertex, yet where no paired source vertex lands --
    and a perfect warped shell read ours - author p05 -0.146u there (2026-09-26,
    zeroed bodies); with the round trip it reads +0.000."""
    sV = np.asarray(sV, float)
    uV = np.asarray(uV, float)
    vc = visible_band(cV, cT)
    vu = visible_band(uV, uT, uN)
    n = len(sV)
    _, c2u = cKDTree(uV).query(sV)
    _, u2c = cKDTree(sV).query(uV)
    pc = np.zeros(len(vc), bool)
    pc[:n] = vc[:n] & vu[c2u]
    pu = vu & pc[:n][u2c]
    return pc, pu


def pair_mask_for(V, ref):
    """The paired mask `ref` = (reference verts, mask) carried onto the body `V`
    vertex for vertex, or None when `V` is not that body over the band (a
    partial or re-shaped injected body: its skin was never paired).

    A band vertex keeps the mask of its OWN index whenever the reference vertex
    at that index is as near as its nearest one (to PAIR_MATCH): on the
    reference body itself that is the mask by index, exactly what the
    self-check validated. The nearest vertex alone is not enough: 586 of the
    closed source body's band vertices and 639 of UBE's have a COINCIDENT seam
    twin, visibility is per vertex normal so twins can differ, and a KD query
    returns either one -- on the reference bodies it flipped 10 source and 6
    UBE vertices (2026-09-26). A body in another vertex order falls back to
    the nearest vertex."""
    if ref is None:
        return None
    rV, rm = ref
    rV = np.asarray(rV, float)
    rm = np.asarray(rm, bool)
    V = np.asarray(V, float)
    band = band_mask(V)
    if not band.any():
        return None
    idx = np.flatnonzero(band)
    d, j = cKDTree(rV).query(V[idx])
    if float(d.max()) > PAIR_MATCH:
        return None
    own = idx < len(rV)
    d_own = np.full(len(idx), np.inf)
    d_own[own] = np.linalg.norm(V[idx[own]] - rV[idx[own]], axis=1)
    j = np.where(d_own <= d + PAIR_MATCH, idx, j)
    m = np.zeros(len(V), bool)
    m[idx] = rm[j]
    return m


def _score(bV, bT, bW, gd, sel, par) -> "dict | None":
    """The census numbers over the body vertices in `sel`, or None when fewer
    than 50 of them are covered. `gd` is [(verts, tris, weights)] per garment
    shape; `par` the skeleton's parent map (empty: nothing poses)."""
    BN = _normals(bV, bT)
    GV = np.concatenate([d[0] for d in gd])
    gL = np.concatenate([d[2].get(LTH, (np.zeros(len(d[0])), None))[0] for d in gd])
    gR = np.concatenate([d[2].get(RTH, (np.zeros(len(d[0])), None))[0] for d in gd])
    bP = bW.get(PEL, (np.zeros(len(bV)), None))[0]
    idx0 = np.flatnonzero(sel)
    if len(idx0) < 50:
        return None
    tree = cKDTree(GV)
    dd, jj = tree.query(bV[idx0], k=8)
    cover = np.full(len(idx0), -1)
    c0 = np.full(len(idx0), np.nan)
    for r in range(len(idx0)):
        i = idx0[r]
        for d, j in zip(dd[r], jj[r]):
            if d > REACH:
                break
            c = float(np.dot(GV[j] - bV[i], BN[i]))
            if c > -1.5:
                cover[r] = j
                c0[r] = c
                break
    keep = cover >= 0
    idx, j, c0 = idx0[keep], cover[keep], c0[keep]
    if len(idx) < 50:
        return None
    origins = {b: o for b, (w, o) in bW.items()}
    ident = float(np.abs(apply_pose(bV, bW, build_pose(par, origins, [])) - bV).max())
    out = {"n": int(len(idx)), "identity": round(ident, 6),
           "bind_c_p05": round(float(np.percentile(c0, 5)), 3),
           "bind_c_p50": round(float(np.percentile(c0, 50)), 3)}
    pelvis_only = bP[idx] > 0.9
    asym = np.abs(gL[j] - gR[j])
    out["pelvis_only_n"] = int(pelvis_only.sum())
    if pelvis_only.sum() >= 10:
        out["asym_p90_over_pelvis_only"] = round(float(np.percentile(asym[pelvis_only], 90)), 3)
    else:
        out["asym_p90_over_pelvis_only"] = None
    for name, pl in (("L45", [(LT, 'x', SWING_DEG)]), ("R45", [(RT, 'x', SWING_DEG)])):
        acc = build_pose(par, origins, pl)
        PB = apply_pose(bV, bW, acc)
        PG = np.concatenate([apply_pose(v, w, acc) for (v, t, w) in gd])
        PN = _normals(PB, bT)
        c1 = ((PG[j] - PB[idx]) * PN[idx]).sum(1)
        loss = c0 - c1
        newly_in = (c0 >= 0) & (c1 < 0)
        out[f"{name}_loss_p90"] = round(float(np.percentile(loss, 90)), 3)
        out[f"{name}_newly_inside_pct"] = round(100.0 * float(newly_in.mean()), 2)
        out[f"{name}_over0p5_pct"] = round(100.0 * float((loss > 0.5).mean()), 2)
    return out


def measure(body_shape, garments, companions=(), par=None, pair_ref=None) -> "dict | None":
    """One body shape + its visible garment shapes -> the census row, or None when
    fewer than 50 band vertices are covered (a gauntlet has no crotch).

    The top-level keys are the LEGACY full band on `body_shape` alone. `vis` is
    the same census over VISIBLE band skin of the body closed with its
    `companions` (None when fewer than 50 visible vertices are covered): each
    arm on its OWN visible skin, so not like-for-like across bodies. `pair` is
    the census over skin visible on BOTH bodies (`paired_skin`), carried onto
    this body from `pair_ref` = (reference verts, mask); None when there is no
    reference, this body is not it, or fewer than 50 paired vertices are
    covered. `par` defaults to the real skeleton's parent map."""
    global _PAR
    if par is None:
        if _PAR is None:
            _PAR = bone_parents(pynifly.NifFile(_skeleton()))
        par = _PAR
    bV, _, bW = read_skin(body_shape)
    bT = np.asarray(body_shape.tris, np.int64)
    if len(bT) == 0:
        return None
    gd = [read_skin(s) for s in garments]
    gd = [(v, t, w) for (v, t, w) in gd if len(v) >= 200]
    if not gd:
        return None
    band = band_mask(bV)
    out = _score(bV, bT, bW, gd, band, par)
    if out is None:
        return None
    out["band_n"] = int(band.sum())
    if companions:
        cV, cT, cW = merged_skin([body_shape, *companions])
    else:
        cV, cT, cW = bV, bT, bW
    vm = visible_band(cV, cT)
    vis = _score(cV, cT, cW, gd, vm, par)
    if vis is not None:
        vis["band_n"] = int(band_mask(cV).sum())
        vis["visible_n"] = int(vm.sum())
    out["vis"] = vis
    pm = pair_mask_for(cV, pair_ref)
    pair = _score(cV, cT, cW, gd, pm, par) if pm is not None else None
    if pair is not None:
        pair["paired_n"] = int(pm.sum())
    out["pair"] = pair
    return out


# ------------------------------------------------------------------ self-check

def closed_shell(V, T, d=SHELL_D, sources=False):
    """A CLOSED garment `d` off the body's crotch region: the region's triangles
    (z SHELL_Z, |x| < SHELL_X) welded, every HOLE capped with a fan from its
    centroid, each vertex moved `d` along the welded normal. A hole is a closed
    boundary loop lying at least HOLE_MARGIN inside the region box -- the loops
    where the region was cut out of the body all touch the box. Returns
    (verts, tris, holes capped), plus with `sources` the body vertex each shell
    vertex was made from (a cap centre: the loop vertex nearest it) -- what
    skins the shell with the body's own weights."""
    V = np.asarray(V, float)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    reg = (V[:, 2] > SHELL_Z[0]) & (V[:, 2] < SHELL_Z[1]) & (np.abs(V[:, 0]) < SHELL_X)
    _, inv = np.unique(np.round(V, 4), axis=0, return_inverse=True)
    inv = np.asarray(inv).ravel()
    P = np.zeros((inv.max() + 1, 3))
    P[inv] = V
    WT = inv[T[np.all(reg[T], axis=1)]]
    WT = WT[(WT[:, 0] != WT[:, 1]) & (WT[:, 1] != WT[:, 2]) & (WT[:, 0] != WT[:, 2])]
    directed = WT[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    und = np.sort(directed, axis=1)
    _, which, counts = np.unique(und, axis=0, return_inverse=True, return_counts=True)
    which = np.asarray(which).ravel()
    nxt = {int(a): int(b) for a, b in directed[counts[which] == 1]}
    loops, seen = [], set()
    for s in list(nxt):
        if s in seen:
            continue
        lp = [s]
        seen.add(s)
        c = nxt[s]
        while c != s and c in nxt and c not in seen:
            lp.append(c)
            seen.add(c)
            c = nxt[c]
        if c == s and len(lp) >= 3:
            loops.append(lp)
    holes = []
    for lp in loops:
        q = P[lp]
        if (q[:, 2].min() > SHELL_Z[0] + HOLE_MARGIN and q[:, 2].max() < SHELL_Z[1] - HOLE_MARGIN
                and np.abs(q[:, 0]).max() < SHELL_X - HOLE_MARGIN):
            holes.append(lp)
    used = np.unique(WT)
    remap = -np.ones(len(P), np.int64)
    remap[used] = np.arange(len(used))
    SV = P[used].copy()
    first = np.empty(len(P), np.int64)
    order = np.arange(len(inv))[::-1]
    first[inv[order]] = order                  # the lowest body index per welded vertex
    src = list(first[used])
    tris = [remap[WT]]
    for lp in holes:
        ci = len(SV)
        c = P[lp].mean(0)
        SV = np.vstack([SV, c])
        src.append(int(first[lp[int(np.argmin(np.linalg.norm(P[lp] - c, axis=1)))]]))
        # the boundary runs the way its own face winds; the cap runs it backwards
        tris.append(np.array([(remap[lp[(i + 1) % len(lp)]], remap[lp[i]], ci)
                              for i in range(len(lp))], np.int64))
    ST = np.vstack(tris)
    out = SV + d * _normals(SV, ST)
    if sources:
        return out, ST, len(holes), np.asarray(src, np.int64)
    return out, ST, len(holes)


def _ride_one_thigh(gw, mask, origin):
    """The shell weights `gw` with the `mask` vertices moved wholly onto the
    left thigh -- a one-thigh gusset, the class this census exists for."""
    out = {b: (np.where(mask, 0.0, w), o) for b, (w, o) in gw.items()}
    w0, o = out.get(LTH, (np.zeros(len(mask)), origin))
    out[LTH] = (np.where(mask, 1.0, w0), o)
    return out


def self_check(src_main, src_closed, ube, warp, par, d=SHELL_D) -> dict:
    """Score a perfect conversion by construction, at bind AND posed, and the
    planted defects the instrument must see.

    src_main: (V, T, W) of the source body shape the warp is keyed to (W as
    `read_skin` returns it); src_closed: (V, T, W) of that body with its
    companion shapes; ube: (V, T, W) of the UBE body; warp: (garment verts,
    tris) -> warped verts; par: the skeleton's parent map.

    A perfect conversion rides the body it sits on. The author's shell is
    skinned with the source body's own weights (each vertex those of the body
    vertex it was made from); ours with UBE's own weights, carried by the warp:
    each vertex those of the UBE vertex its source vertex corresponds to (the
    nearest-vertex correspondence `_cached_cbbe_to_ube_delta` warps by). That
    reads ~0 swing difference only while the two bodies' thigh painting is
    close enough: on the synthetic bodies (source handing the band skin to the
    thigh from |x| = 1 over 5u) UBE handovers starting at |x| 0 to 5.5 read
    within 0.08u, and one starting at |x| 6 or later reads -0.13u and FAILS the
    check -- a false alarm, but closed (exit 4), never a silent pass. The real
    zeroed bodies read -0.011u. The
    same shell keeping the SOURCE weights on UBE is reported beside it as
    information: what the two bodies' weight painting alone costs a garment the
    converter does not re-weight. Each check runs on the VISIBLE band (each arm
    its own visible skin) and on the PAIRED band (skin visible on both bodies,
    `paired_skin`). Returns the readings plus `ok` and the reasons it is not,
    and `pair_masks` for the census."""
    sV, sT, sW = src_main
    cV, cT, cW = src_closed
    uV, uT, uW = ube
    sV = np.asarray(sV, float)
    uV = np.asarray(uV, float)
    uT = np.asarray(uT, np.int64)
    G, GT, holes, src = closed_shell(sV, sT, d, sources=True)
    W = np.asarray(warp(G, GT), float)
    uN = _normals(uV, uT)
    gw = {b: (np.asarray(w, float)[src], o) for b, (w, o) in sW.items()}
    _, c2u = cKDTree(uV).query(sV)
    gw_ours = {b: (np.asarray(w, float)[c2u[src]], o) for b, (w, o) in uW.items()}
    origin = next((w[LTH][1] for w in (uW, sW) if LTH in w), np.zeros(3))
    mid = (np.abs(W[:, 0]) < POSED_PLANT_X) & (W[:, 2] >= BAND_Z[0]) & (W[:, 2] <= BAND_Z[1])
    gw_skew = _ride_one_thigh(gw_ours, mid, origin)
    _, nn = cKDTree(uV).query(W)
    buried = W - SELF_CHECK_BURY * uN[nn]

    def score(bV, bT, bW, X, gwx, sel):
        return _score(np.asarray(bV, float), np.asarray(bT, np.int64), bW, [(X, GT, gwx)], sel, par)

    pc, pu = paired_skin(sV, cV, cT, uV, uT, uN)
    res = {"holes_capped": holes, "shell_verts": int(len(G)), "why": [],
           "pair_masks": (pc, pu)}
    for name, sa, so in (("visible", visible_band(cV, cT), visible_band(uV, uT, uN)),
                         ("paired", pc, pu)):
        a = score(cV, cT, cW, G, gw, sa)
        o = score(uV, uT, uW, W, gw_ours, so)
        b = score(uV, uT, uW, buried, gw_ours, so)
        k = score(uV, uT, uW, W, gw_skew, so)
        if a is None or o is None or b is None or k is None:
            res["why"].append(f"the {name} band covered fewer than 50 vertices on an arm "
                              "-- 0/0 is not a pass")
            continue
        r = {"author": (a["n"], a["bind_c_p05"], a["bind_c_p50"]),
             "ours": (o["n"], o["bind_c_p05"], o["bind_c_p50"]),
             "dp05": round(o["bind_c_p05"] - a["bind_c_p05"], 3),
             "dp50": round(o["bind_c_p50"] - a["bind_c_p50"], 3),
             "buried_dp05": round(b["bind_c_p05"] - a["bind_c_p05"], 3),
             "buried_dp50": round(b["bind_c_p50"] - a["bind_c_p50"], 3),
             "loss_author": (a["L45_loss_p90"], a["R45_loss_p90"]),
             "loss_ours": (o["L45_loss_p90"], o["R45_loss_p90"]),
             "dL": round(o["L45_loss_p90"] - a["L45_loss_p90"], 3),
             "dR": round(o["R45_loss_p90"] - a["R45_loss_p90"], 3),
             "skew_d": round(max(k["L45_loss_p90"] - a["L45_loss_p90"],
                                 k["R45_loss_p90"] - a["R45_loss_p90"]), 3)}
        if name == "paired":
            e = score(uV, uT, uW, W, gw, so)
            if e is not None:
                r["carried_dL"] = round(e["L45_loss_p90"] - a["L45_loss_p90"], 3)
                r["carried_dR"] = round(e["R45_loss_p90"] - a["R45_loss_p90"], 3)
        res[name] = r
        if abs(r["dp05"]) > SELF_CHECK_TOL or abs(r["dp50"]) > SELF_CHECK_TOL:
            res["why"].append(
                f"a perfect warped shell reads ours - author p05 {r['dp05']:+.3f} p50 "
                f"{r['dp50']:+.3f}u on {name} skin (allowed {SELF_CHECK_TOL}u): the warp, "
                "the bodies or the mask is wrong")
        if r["buried_dp50"] > SELF_CHECK_MUST_FAIL:
            res["why"].append(
                f"a shell buried {SELF_CHECK_BURY}u reads only p50 {r['buried_dp50']:+.3f}u on "
                f"{name} skin (must be {SELF_CHECK_MUST_FAIL} or deeper): the instrument cannot "
                "see burial")
        if abs(r["dL"]) > SELF_CHECK_POSED_TOL or abs(r["dR"]) > SELF_CHECK_POSED_TOL:
            res["why"].append(
                f"a perfect shell riding the body reads ours - author swing loss p90 L "
                f"{r['dL']:+.3f} R {r['dR']:+.3f}u on {name} skin (allowed "
                f"{SELF_CHECK_POSED_TOL}u): the weights, the skeleton or the mask is wrong")
        if r["skew_d"] < SELF_CHECK_POSED_MUST_SEE:
            res["why"].append(
                f"a shell whose midline rides one thigh on our arm only reads {r['skew_d']:+.3f}u "
                f"worse on {name} skin (must be {SELF_CHECK_POSED_MUST_SEE} or more): the "
                "instrument cannot see a posed skew")
    la = score(sV, sT, sW, G, gw, band_mask(sV))
    lo = score(uV, uT, uW, W, gw_ours, band_mask(uV))
    if la is not None and lo is not None:
        res["legacy_dp05"] = round(lo["bind_c_p05"] - la["bind_c_p05"], 3)
        res["legacy_dp50"] = round(lo["bind_c_p50"] - la["bind_c_p50"], 3)
    res["ok"] = not res["why"]
    return res


def _self_check_inputs(mods_root):
    """The real bodies with their weights, the converter's own warp and the real
    skeleton, at weight 1."""
    try:
        par = bone_parents(pynifly.NifFile(_skeleton()))
    except SystemExit as e:
        raise RuntimeError(str(e)) from None
    cp, cname = canonical_cbbe(str(mods_root) if mods_root else None)
    up, uname = canonical_ube()
    cnf = pynifly.NifFile(str(cp))
    shapes = body_shapes(cnf, cname)
    sV, sT, sW = read_skin(shapes[0])
    cV, cT, cW = merged_skin(shapes)
    ushape = next(s for s in pynifly.NifFile(str(up)).shapes if s.name == uname)
    uV, uT, uW = read_skin(ushape)
    bV, delta = nc._cached_cbbe_to_ube_delta(Path(cp), Path(up))
    if bV is None or len(bV) != len(sV):
        raise RuntimeError("the converter's body delta is not keyed to the canonical source body")
    ubN = nc._body_normals_or_compute(ushape)

    def warp(G, GT):
        return nc.warp_armor_by_body_delta(
            G, bV, delta, ube_body_verts=uV, ube_body_normals=ubN,
            min_standoff=nc.ARMOR_TO_SKIN_BUFFER, tris=GT).astype(float)
    return (sV, sT, sW), (cV, cT, cW), (uV, uT, uW), warp, par


def _print_self_check(res):
    print(f"SELF-CHECK: a closed shell {SHELL_D}u off the source crotch "
          f"({res['holes_capped']} holes capped, {res['shell_verts']} verts) through the warp, "
          "each arm's shell skinned with its own body's weights")
    for name, label in (("visible", "VISIBLE band (each arm its own visible skin)"),
                        ("paired", "PAIRED band (skin visible on both bodies)")):
        r = res.get(name)
        if r is None:
            continue
        print(f"  {label}:")
        print(f"    bind, perfect: ours - author p05 {r['dp05']:+.3f} p50 {r['dp50']:+.3f}u "
              f"(author n={r['author'][0]}, ours n={r['ours'][0]}; allowed +-{SELF_CHECK_TOL})")
        print(f"    bind, buried {SELF_CHECK_BURY}u: p05 {r['buried_dp05']:+.3f} "
              f"p50 {r['buried_dp50']:+.3f}u (p50 must be {SELF_CHECK_MUST_FAIL} or deeper)")
        print(f"    swing loss p90, perfect: author L {r['loss_author'][0]:.3f} R {r['loss_author'][1]:.3f}, "
              f"ours L {r['loss_ours'][0]:.3f} R {r['loss_ours'][1]:.3f}; ours - author "
              f"L {r['dL']:+.3f} R {r['dR']:+.3f}u (allowed +-{SELF_CHECK_POSED_TOL})")
        print(f"    swing, midline riding one thigh on our arm only: {r['skew_d']:+.3f}u worse "
              f"(must be {SELF_CHECK_POSED_MUST_SEE} or more)")
        if "carried_dL" in r:
            print(f"    swing, our shell keeping the SOURCE body's weights instead: ours - author "
                  f"L {r['carried_dL']:+.3f} R {r['carried_dR']:+.3f}u -- what the two bodies' "
                  "weight painting alone costs a garment that is not re-weighted (information)")
    if "legacy_dp05" in res:
        print(f"  legacy FULL band on the same perfect shell: p05 {res['legacy_dp05']:+.3f} "
              f"p50 {res['legacy_dp50']:+.3f}u -- the floor of the cross-body artefact")
    print("  SELF-CHECK " + ("PASS" if res["ok"] else "FAIL: " + "; ".join(res["why"])))


# ------------------------------------------------------------------ the census

def _seed_vis(masks, pair=None):
    _VIS.update(masks)
    _PAIR.update(pair or {})


def _one(args):
    path, pack, mods_root = args
    p = Path(path)
    rel = str(p.relative_to(pack)).replace("\\", "/")
    try:
        nf = pynifly.NifFile(str(p))
        shapes = {s.name: s for s in nf.shapes}
        body = next((shapes[n] for n in nc.UBE_BODY_INJECT_NAMES if n in shapes), None)
        if body is None:
            return {"piece": rel, "skip": "no injected body"}
        gnames = converted_garment_names(p)
        conv = measure(body, [shapes[n] for n in gnames if n in shapes],
                       pair_ref=_PAIR.get("ube"))
        if conv is None:
            return {"piece": rel, "skip": "no covered crotch band"}
        row = {"piece": rel, "conv": conv}
        src = find_source(rel, gnames, mods_root) if mods_root else None
        if src is not None:
            # the file the source arm was scored on: find_source pairs by path and
            # shape names, which is not always the file the converter read
            row["src_path"] = str(src)
            try:
                global _CBBE
                if _CBBE is None:
                    _CBBE = canonical_cbbe(str(mods_root))
                cb = pynifly.NifFile(str(_CBBE[0]))
                cbody, *companions = body_shapes(cb, _CBBE[1])
                snf = pynifly.NifFile(str(src))
                sshapes = {s.name: s for s in snf.shapes}
                row["src"] = measure(cbody, [sshapes[n] for n in gnames if n in sshapes],
                                     companions, pair_ref=_PAIR.get("cbbe"))
            except Exception as e:                       # noqa: BLE001
                row["src_error"] = repr(e)[:120]
        return row
    except Exception as e:                               # noqa: BLE001
        return {"piece": rel, "error": repr(e)[:160]}


def _worst(arm):
    return max(arm["L45_loss_p90"], arm["R45_loss_p90"])


def leg_compare(pairs, margin=CMP_MARGIN) -> dict:
    """Source vs converted PER LEG: each arm's left-swing loss p90 against the
    other's LEFT, right against RIGHT. `pairs` = [(piece, conv arm, src arm)],
    both arms scored on one band.

    The worse-side comparison (each arm's own worse leg) can subtract maxima
    that sit on OPPOSITE legs: a conversion that adds 0.35u to the leg its
    source kept low while the other leg falls reads "not worse" there. Here a
    piece counts as worse when EITHER leg loses more than `margin` more than
    the source's same leg; `flagged` lists those pieces, `seen` saying whether
    the worse-side count also has them. The `*_any` counts use no margin."""
    out = {"n": len(pairs), "margin": margin, "legs_worse": 0, "legs_better": 0,
           "pieces_leg_better": 0, "side_worse": 0, "side_better": 0,
           "side_more_any": 0, "side_less_any": 0, "side_equal": 0,
           "legs_more_any": 0, "legs_less_any": 0, "legs_equal": 0, "flagged": []}
    for piece, c, s in pairs:
        dl = c["L45_loss_p90"] - s["L45_loss_p90"]
        dr = c["R45_loss_p90"] - s["R45_loss_p90"]
        dw = _worst(c) - _worst(s)
        for d in (dl, dr):
            out["legs_worse"] += d > margin
            out["legs_better"] += d < -margin
            out["legs_more_any"] += d > 0
            out["legs_less_any"] += d < 0
            out["legs_equal"] += d == 0
        out["side_worse"] += dw > margin
        out["side_better"] += dw < -margin
        out["side_more_any"] += dw > 0
        out["side_less_any"] += dw < 0
        out["side_equal"] += dw == 0
        out["pieces_leg_better"] += min(dl, dr) < -margin
        if max(dl, dr) > margin:
            out["flagged"].append({"piece": piece, "dL": round(dl, 3), "dR": round(dr, 3),
                                   "d_worse_side": round(dw, 3), "seen": dw > margin,
                                   "conv": (c["L45_loss_p90"], c["R45_loss_p90"]),
                                   "src": (s["L45_loss_p90"], s["R45_loss_p90"])})
    out["pieces_leg_worse"] = len(out["flagged"])
    out["flagged"].sort(key=lambda f: -max(f["dL"], f["dR"]))
    return out


def _loss_block(scored, get, label, like_for_like=False):
    """One band's converted loss table. The SOURCE vs CONVERTED line subtracts
    the two arms piece by piece, which only means something when both arms were
    scored on the same skin (`like_for_like`, the paired band); on the other
    bands it is printed under a warning, for the record."""
    rows = [r for r in scored if get(r["conv"]) is not None]
    print(f"\n{label}: n={len(rows)} of {len(scored)} scored")
    if not rows:
        print("  nothing covered on this band")
        return
    worst = np.array([_worst(get(r["conv"])) for r in rows])
    asym = np.array([get(r["conv"])["asym_p90_over_pelvis_only"]
                     if get(r["conv"])["asym_p90_over_pelvis_only"] is not None else np.nan
                     for r in rows])
    print(f"  CONVERTED, single {SWING_DEG:.0f}-degree thigh swing, band z{BAND_Z[0]:.0f}-{BAND_Z[1]:.0f} |x|<{BAND_X:.0f}")
    print(f"  loss p90 (worse side): p50 {np.median(worst):.2f} p90 {np.percentile(worst, 90):.2f} max {worst.max():.2f}u")
    print(f"  pieces over 1.0u: {int((worst > 1.0).sum())}   over 0.5u: {int((worst > 0.5).sum())}")
    ok = np.isfinite(asym)
    if ok.any():
        hi, lo = ok & (asym > 0.3), ok & (asym <= 0.3)
        print(f"  one-sided thigh weight over pelvis-only skin, p90 > 0.3: {int(hi.sum())} pieces, "
              f"median loss {np.median(worst[hi]) if hi.any() else float('nan'):.2f}u; "
              f"the rest {int(lo.sum())} pieces, median loss {np.median(worst[lo]) if lo.any() else float('nan'):.2f}u")
    both = [r for r in rows if r.get("src") and get(r["src"]) is not None]
    if both:
        sw = np.array([_worst(get(r["src"])) for r in both])
        cw = np.array([_worst(get(r["conv"])) for r in both])
        lc = leg_compare([(r["piece"], get(r["conv"]), get(r["src"])) for r in both])
        m = lc["margin"]
        warn = ("  " if like_for_like else
                "  NOT LIKE-FOR-LIKE (the arms cover different skin; compare on the PAIRED band): ")
        print(warn
              + f"SOURCE on the canonical CBBE/3BA body vs CONVERTED, n={len(both)}: "
              f"loss p90 src p50 {np.median(sw):.2f} / conv p50 {np.median(cw):.2f}; "
              f"src over 1u {int((sw > 1).sum())}, conv over 1u {int((cw > 1).sum())}; "
              f"WORSE SIDE (each arm's own worse leg) worse after conversion by >{m}u "
              f"{lc['side_worse']}, better by >{m}u {lc['side_better']}")
        print(f"    PER LEG (left vs left, right vs right): legs worse by >{m}u {lc['legs_worse']}, "
              f"better by >{m}u {lc['legs_better']}, of {2 * lc['n']}; pieces with a leg worse by "
              f">{m}u {lc['pieces_leg_worse']} (the worse-side count has "
              f"{sum(f['seen'] for f in lc['flagged'])} of them), with a leg better by >{m}u "
              f"{lc['pieces_leg_better']}")
        print(f"    by ANY margin: worse side loses less {lc['side_less_any']} / more "
              f"{lc['side_more_any']} / equal {lc['side_equal']}; legs less {lc['legs_less_any']} / "
              f"more {lc['legs_more_any']} / equal {lc['legs_equal']}")
        if like_for_like and lc["flagged"]:
            src_of = {r["piece"]: r.get("src_path", "?") for r in both}
            print(f"  PIECES WITH A LEG WORSE BY >{m}u (same leg, same skin; source = the file "
                  "find_source paired):")
            for f in lc["flagged"]:
                print(f"    L {f['dL']:+.2f} R {f['dR']:+.2f}  conv L {f['conv'][0]:.2f} R {f['conv'][1]:.2f}"
                      f"  src L {f['src'][0]:.2f} R {f['src'][1]:.2f}  worse side {f['d_worse_side']:+.2f}"
                      f"{'' if f['seen'] else '  (NOT in the worse-side count)'}  {f['piece']}"
                      f"  <- {src_of.get(f['piece'])}")
    print("  TOP 15 by loss p90 (worse side):")
    for k in np.argsort(-worst)[:15]:
        c = get(rows[k]["conv"])
        print(f"    {worst[k]:5.2f}u  L {c['L45_loss_p90']:5.2f} R {c['R45_loss_p90']:5.2f}  "
              f"newly inside {max(c['L45_newly_inside_pct'], c['R45_newly_inside_pct']):5.1f}%  "
              f"asym {c['asym_p90_over_pelvis_only']}  {rows[k]['piece']}")


def _bind_block(scored):
    """ours - author bind clearance per piece, both arms covering at least
    COVER_SHARE of their full band (the filter the recorded numbers used)."""
    pairs = [r for r in scored if r.get("src")
             and r["src"]["n"] >= COVER_SHARE * r["src"]["band_n"]
             and r["conv"]["n"] >= COVER_SHARE * r["conv"]["band_n"]]
    print(f"\nBIND ours - author, both arms >= {COVER_SHARE:.0%} band cover: n={len(pairs)}")
    for label, get in (("FULL band (legacy; NOT comparable across CBBE and UBE)", lambda a: a),
                       ("VISIBLE band (each arm its own visible skin; NOT like-for-like)",
                        lambda a: a.get("vis")),
                       ("PAIRED band (skin visible on both bodies; like-for-like)",
                        lambda a: a.get("pair"))):
        q = [(get(r["conv"]), get(r["src"])) for r in pairs
             if get(r["conv"]) is not None and get(r["src"]) is not None]
        if not q:
            print(f"  {label}: nothing paired")
            continue
        d05 = np.array([c["bind_c_p05"] - s["bind_c_p05"] for c, s in q])
        d50 = np.array([c["bind_c_p50"] - s["bind_c_p50"] for c, s in q])
        print(f"  {label}: n={len(q)}  p05 {np.median(d05):+.3f} "
              f"(deeper/shallower by 0.1u {int((d05 < -0.1).sum())}/{int((d05 > 0.1).sum())})  "
              f"p50 {np.median(d50):+.3f} (closer/looser {int((d50 < -0.1).sum())}/{int((d50 > 0.1).sum())})")


def main(argv=None, self_check_inputs=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    lay = paths.discover_layout()
    mods_root = lay.mods_root
    if self_check_inputs is None:
        try:
            self_check_inputs = _self_check_inputs(mods_root)
        except Exception as e:                           # noqa: BLE001
            print(f"REFUSED: the self-check could not load its bodies, the skeleton or the warp "
                  f"({e!r:.200}). Without it no number from this census is sound -- set "
                  "CBBE2UBE_MO2_INI and CBBE2UBE_SKELETON_NIF.")
            return 4                                     # SELF_CHECK_EXIT, literal for tool_map
    res = self_check(*self_check_inputs)
    _print_self_check(res)
    if not res["ok"]:
        print("REFUSED: the self-check failed, so nothing below would mean anything.")
        return 4                                         # SELF_CHECK_EXIT
    if "--self-check" in argv:
        return 0
    pc, pu = res["pair_masks"]
    _PAIR["cbbe"] = (np.asarray(self_check_inputs[1][0], float), pc)
    _PAIR["ube"] = (np.asarray(self_check_inputs[2][0], float), pu)

    out = Path(opt("--out", "single_swing_census.jsonl"))
    limit = int(opt("--limit", "0") or 0)
    workers = int(opt("--workers", "6"))
    pack = Path(opt("--pack", "")) if opt("--pack") else (
        (mods_root / os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto") / "meshes" / "!UBE")
        if mods_root else None)
    if pack is None or not pack.is_dir():
        print("REFUSED: no pack -- pass --pack <meshes/!UBE> or set CBBE2UBE_MO2_INI")
        return 2
    _skeleton()
    files = [p for p in sorted(pack.rglob("*_1.nif"))
             if not any(k in p.stem.lower() for k in ("1st", "firstperson", "1person"))]
    if limit:
        files = files[:limit]
    t0 = time.time()
    tally: Counter = Counter()
    scored = []
    with out.open("w", encoding="utf-8") as fh, \
            Pool(workers, initializer=_seed_vis, initargs=(dict(_VIS), dict(_PAIR))) as pool:
        for row in pool.imap_unordered(_one, [(str(p), pack, mods_root) for p in files], chunksize=4):
            key = ("skip: " + row["skip"] if "skip" in row
                   else "error" if "error" in row else "scored")
            tally[key] += 1
            if "conv" in row:
                scored.append(row)
            fh.write(json.dumps(row) + "\n")
    print(f"{len(files)} `_1` NIFs seen in {time.time() - t0:.0f}s -> {dict(tally)}")
    print(f"  rows -> {out}")
    if not scored:
        print("  NOTHING WAS SCORED -- 0/0 is not a clean result")
        return 2
    _loss_block(scored, lambda a: a,
                "FULL BAND (legacy; the source body is its main shape alone -- "
                "never subtract across CBBE and UBE)")
    _loss_block(scored, lambda a: a.get("vis"),
                "VISIBLE BAND (normal ray escapes the body; source body closed with its companion shapes)")
    _loss_block(scored, lambda a: a.get("pair"),
                "PAIRED BAND (skin visible on BOTH bodies through the warp's correspondence)",
                like_for_like=True)
    nopair = sum(1 for r in scored if r["conv"].get("pair") is None)
    print(f"  converted arms with no paired block: {nopair} (a body that is not the reference "
          "UBE body, or under 50 paired vertices covered)")
    _bind_block(scored)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
