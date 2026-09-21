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
`canonical_body.canonical_cbbe()`: it globs `femalebody_1.nif` only and caches
one body with no weight in the key. A `_0` source garment would be posed on the
weight-1 CBBE body, and the source-vs-converted block would pool those
mismatched comparisons into a believable number.

To close it: resolve the source body from each file's own weight suffix
(`canonical_cbbe` needs a weight and a per-weight cache key), keep this file's
first-person stem rule on top of `output_nifs` (it is broader than that helper's
regex), then widen. This census feeds an open investigation; widening it will
move numbers that investigation has already recorded, so do it as its own
change with an old-vs-new comparison.
"""
from __future__ import annotations

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
    converted_garment_names, find_source, canonical_cbbe)

LTH, RTH, PEL = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]"
BAND_Z = (55.0, 75.0)
BAND_X = 8.0
REACH = 6.0
SWING_DEG = 45.0
_PAR = None
_CBBE = None


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


def measure(body_shape, garments) -> "dict | None":
    """One body shape + its visible garment shapes -> the census row, or None when
    fewer than 50 band vertices are covered (a gauntlet has no crotch)."""
    global _PAR
    if _PAR is None:
        _PAR = bone_parents(pynifly.NifFile(_skeleton()))
    bV, _, bW = read_skin(body_shape)
    bT = np.asarray(body_shape.tris, np.int64)
    if len(bT) == 0:
        return None
    BN = _normals(bV, bT)
    gd = [read_skin(s) for s in garments]
    gd = [(v, t, w) for (v, t, w) in gd if len(v) >= 200]
    if not gd:
        return None
    GV = np.concatenate([d[0] for d in gd])
    gL = np.concatenate([d[2].get(LTH, (np.zeros(len(d[0])), None))[0] for d in gd])
    gR = np.concatenate([d[2].get(RTH, (np.zeros(len(d[0])), None))[0] for d in gd])
    bP = bW.get(PEL, (np.zeros(len(bV)), None))[0]
    z, x = bV[:, 2], bV[:, 0]
    band = (z >= BAND_Z[0]) & (z <= BAND_Z[1]) & (np.abs(x) < BAND_X)
    idx0 = np.flatnonzero(band)
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
    ident = float(np.abs(apply_pose(bV, bW, build_pose(_PAR, origins, [])) - bV).max())
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
        acc = build_pose(_PAR, origins, pl)
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
        conv = measure(body, [shapes[n] for n in gnames if n in shapes])
        if conv is None:
            return {"piece": rel, "skip": "no covered crotch band"}
        row = {"piece": rel, "conv": conv}
        src = find_source(rel, gnames, mods_root) if mods_root else None
        if src is not None:
            try:
                global _CBBE
                if _CBBE is None:
                    _CBBE = canonical_cbbe(str(mods_root))
                cb = pynifly.NifFile(str(_CBBE[0]))
                cbody = next(s for s in cb.shapes if s.name == _CBBE[1])
                snf = pynifly.NifFile(str(src))
                sshapes = {s.name: s for s in snf.shapes}
                row["src"] = measure(cbody, [sshapes[n] for n in gnames if n in sshapes])
            except Exception as e:                       # noqa: BLE001
                row["src_error"] = repr(e)[:120]
        return row
    except Exception as e:                               # noqa: BLE001
        return {"piece": rel, "error": repr(e)[:160]}


def main() -> int:
    argv = sys.argv[1:]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    out = Path(opt("--out", "single_swing_census.jsonl"))
    limit = int(opt("--limit", "0") or 0)
    workers = int(opt("--workers", "6"))
    lay = paths.discover_layout()
    mods_root = lay.mods_root
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
    with out.open("w", encoding="utf-8") as fh, Pool(workers) as pool:
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
    worst = np.array([max(r["conv"]["L45_loss_p90"], r["conv"]["R45_loss_p90"]) for r in scored])
    asym = np.array([r["conv"]["asym_p90_over_pelvis_only"]
                     if r["conv"]["asym_p90_over_pelvis_only"] is not None else np.nan
                     for r in scored])
    print(f"\nCONVERTED, single {SWING_DEG:.0f}-degree thigh swing, band z{BAND_Z[0]:.0f}-{BAND_Z[1]:.0f} |x|<{BAND_X:.0f}: n={len(scored)}")
    print(f"  loss p90 (worse side): p50 {np.median(worst):.2f} p90 {np.percentile(worst, 90):.2f} max {worst.max():.2f}u")
    print(f"  pieces over 1.0u: {int((worst > 1.0).sum())}   over 0.5u: {int((worst > 0.5).sum())}")
    ok = np.isfinite(asym)
    if ok.any():
        hi, lo = ok & (asym > 0.3), ok & (asym <= 0.3)
        print(f"  one-sided thigh weight over pelvis-only skin, p90 > 0.3: {int(hi.sum())} pieces, "
              f"median loss {np.median(worst[hi]) if hi.any() else float('nan'):.2f}u; "
              f"the rest {int(lo.sum())} pieces, median loss {np.median(worst[lo]) if lo.any() else float('nan'):.2f}u")
    both = [r for r in scored if r.get("src")]
    if both:
        sw = np.array([max(r["src"]["L45_loss_p90"], r["src"]["R45_loss_p90"]) for r in both])
        cw = np.array([max(r["conv"]["L45_loss_p90"], r["conv"]["R45_loss_p90"]) for r in both])
        print(f"\nSOURCE on the canonical CBBE/3BA body vs CONVERTED, n={len(both)}: "
              f"loss p90 src p50 {np.median(sw):.2f} / conv p50 {np.median(cw):.2f}; "
              f"src over 1u {int((sw > 1).sum())}, conv over 1u {int((cw > 1).sum())}; "
              f"worse after conversion (+0.3u) {int(((cw - sw) > 0.3).sum())}, better (-0.3u) {int(((sw - cw) > 0.3).sum())}")
    print("\nTOP 15 by loss p90 (worse side):")
    for k in np.argsort(-worst)[:15]:
        c = scored[k]["conv"]
        print(f"  {worst[k]:5.2f}u  L {c['L45_loss_p90']:5.2f} R {c['R45_loss_p90']:5.2f}  "
              f"newly inside {max(c['L45_newly_inside_pct'], c['R45_newly_inside_pct']):5.1f}%  "
              f"asym {c['asym_p90_over_pelvis_only']}  {scored[k]['piece']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
