# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.  See <https://www.gnu.org/licenses/>.

"""Diagnostic: predict, OFFLINE, where a converted garment will clip the body
DURING JIGGLE -- no game run. Point it at one converted armor NIF and the known
UBE body; it reports per-shape motion-clip risk and the likely cause so you can
pick the remedy (weight transfer vs clearance vs SMP) before touching code.

    python scripts/diag_jiggle_predict.py <garment.nif> [--body <ube_body.nif>]
        [--weight _0|_1] [--excursion breast=2,butt=1.5,belly=1]
        [--max-dist 10] [--clip-eps 0.0]

The excursion numbers (E[]) are the ONE calibration input -- placeholders until
you measure them in-game (see src/jiggle_predict.calibrate_excursion). Until then
treat the COUNTS/CAUSE as the signal, not the absolute margins. Throwaway diag,
not shipped; imports converter internals on purpose.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.jiggle_predict import (  # noqa: E402
    DEFAULT_EXCURSION,
    jiggle_field,
    predict_clip,
    summarize,
)
from src.nif_convert import (  # noqa: E402
    _CONFORM_SKIP_NAMES,
    _body_normals_or_compute,
    _cached_ube_body_verts,
    _find_ube_femalebody,
    _pynifly,
    _shape_global_to_skin,
    _verts_skin_to_world,
)


def garment_world_verts(shape, body_tree):
    """Body-space verts for an OUTPUT garment shape. The converter resets the
    skin transform to identity on reskinned shapes, but some shapes (SMP/chain
    cloth) keep a non-identity global-to-skin that, if RE-applied to the already
    body-space output verts, flings the shape far off (seen: a fitted cuirass
    landing ~1900u away). So compute both placements and keep whichever lands
    closer to the body. Returns (verts, used_g2s: bool)."""
    v = np.asarray(shape.verts, np.float64)
    if len(v) == 0:
        return v, False
    try:
        g = _verts_skin_to_world(v, _shape_global_to_skin(shape))
    except Exception:
        return v, False
    dr, _ = body_tree.query(v)
    dg, _ = body_tree.query(g)
    return (g, True) if dg.mean() < dr.mean() else (v, False)


def _parse_excursion(s):
    if not s:
        return dict(DEFAULT_EXCURSION)
    out = dict(DEFAULT_EXCURSION)
    for part in s.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = float(v)
    # keep butt/glute in step unless the user set them apart
    return out


def _load_body(body_path, weight):
    p = Path(body_path) if body_path else _find_ube_femalebody(weight)
    if not p or not Path(p).is_file():
        sys.exit(f"!! UBE body not found (path={p}). Pass --body explicitly.")
    _, bv, bn = _cached_ube_body_verts(Path(p))
    pyn = _pynifly()
    nf = pyn.NifFile(filepath=str(p))
    body = max(nf.shapes, key=lambda s: len(s.verts))
    bw = body.bone_weights or {}
    nverts = len(bv) if bv is not None else len(body.verts)
    if len(body.verts) != nverts:
        # weights index the shape's verts; if the cached verts came from a
        # different shape, fall back to this shape's own geometry.
        bv = _verts_skin_to_world(
            np.asarray(body.verts, np.float64), _shape_global_to_skin(body))
        bn = _body_normals_or_compute(body)
        nverts = len(bv)
    return Path(p), np.asarray(bv, np.float64), np.asarray(bn, np.float64), bw, nverts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("garment", help="converted garment NIF")
    ap.add_argument("--body", default=None, help="UBE body NIF (else auto-find)")
    ap.add_argument("--weight", default=None, help="_0 or _1 (else match garment stem)")
    ap.add_argument("--excursion", default=None, help="breast=..,butt=..,belly=..")
    ap.add_argument("--max-dist", type=float, default=10.0)
    ap.add_argument("--clip-eps", type=float, default=0.0)
    a = ap.parse_args()

    gpath = Path(a.garment)
    if not gpath.is_file():
        sys.exit(f"!! garment not found: {gpath}")
    weight = a.weight or ("_0" if gpath.stem.lower().endswith("_0") else "_1")
    exc = _parse_excursion(a.excursion)

    bpath, bv, bn, bw, nverts = _load_body(a.body, weight)
    field = jiggle_field(bw, nverts, excursion=exc)
    print(f"BODY  {bpath.name}: {nverts} verts, jiggle field "
          f"max={field.max():.2f}u nonzero={int((field > 0).sum())} "
          f"(E={exc})")
    if field.max() <= 0:
        print("  !! body carries NO jiggle-bone weights under these keywords -- "
              "predictions will be empty. Check the body NIF / bone names.")

    from scipy.spatial import cKDTree
    btree = cKDTree(bv)
    pyn = _pynifly()
    nf = pyn.NifFile(filepath=str(gpath))
    print(f"GARMENT {gpath.name}: {len(nf.shapes)} shapes, weight {weight}\n")
    grand_eval = grand_clip = 0
    worst = []
    for s in nf.shapes:
        nm = (s.name or "")
        if any(k in nm.lower() for k in _CONFORM_SKIP_NAMES):
            continue
        if not (s.bone_names or []):
            continue
        try:
            gv, _used_g2s = garment_world_verts(s, btree)
        except Exception:
            continue
        if len(gv) == 0:
            continue
        pred = predict_clip(gv, s.bone_weights or {}, bv, bn, field,
                            excursion=exc, max_body_dist=a.max_dist)
        rep = summarize(pred, clip_eps=a.clip_eps)
        grand_eval += rep["n_eval"]
        grand_clip += rep["n_clip"]
        tag = "OK " if rep["n_clip"] == 0 else "CLIP"
        line = (f"  [{tag}] {nm[:40]:40s} eval={rep['n_eval']:6d} "
                f"clip={rep['n_clip']:6d} ({rep['clip_frac']*100:4.1f}%)")
        if rep["n_clip"]:
            line += (f"  cause={rep['cause']:13s} "
                     f"max={rep.get('max_margin', 0):.2f}u "
                     f"uw={rep.get('frac_underweight', 0)*100:.0f}%")
            worst.append((rep["n_clip"], nm, rep["cause"]))
        print(line)

    print(f"\nTOTAL eval={grand_eval} clip={grand_clip} "
          f"({(grand_clip/grand_eval*100) if grand_eval else 0:.1f}%)")
    if worst:
        worst.sort(reverse=True)
        c0 = worst[0][2]
        print(f"DOMINANT CAUSE (worst shape): {c0}")
        print("  under-weight  -> fix the jiggle-bone weight transfer "
              "(garment isn't following the body)")
        print("  standoff-tight-> the clearance lever: raise jiggle-zone "
              "clearance (watch for float-off)")
        print("\nNOTE: margins are only quantitative once E[] is calibrated "
              "in-game. Counts/cause are robust to E scale.")


if __name__ == "__main__":
    main()
