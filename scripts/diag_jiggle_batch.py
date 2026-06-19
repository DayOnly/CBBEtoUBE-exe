# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# Free software under the GNU GPL v3+. See <https://www.gnu.org/licenses/>.

"""Batch jiggle-clip predictor: load the body ONCE, sweep N body-fitted armors
from CUSTOM mod folders (vanilla/DLC/patch trees and first-person meshes are
excluded by path/name pattern, no mod names hardcoded), print a per-armor table
with the dominant cause. Throwaway diag; imports converter internals on purpose.

    python scripts/diag_jiggle_batch.py [--root <meshes/!UBE>] [--body <nif>]
        [--n 10] [--excursion breast=2,butt=1.5,belly=1]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from src.jiggle_predict import jiggle_field, predict_clip, summarize  # noqa: E402
from src.nif_convert import (  # noqa: E402
    _CONFORM_SKIP_NAMES,
    _shape_global_to_skin,
    _verts_skin_to_world,
    _pynifly,
)
from diag_jiggle_predict import (  # noqa: E402
    _load_body, _parse_excursion, garment_world_verts)
from scipy.spatial import cKDTree  # noqa: E402

# Path tokens that mark a vanilla/DLC/patch replacer tree (NOT a custom mod).
_VANILLA = ("/armor/", "/clothes/", "/dlc01/", "/dlc02/", "/creationclub/",
            "/actors/", "/usleep/", "/ussep/", "/uskp/", "/udgp/", "/skyrim/",
            "/campfire/", "/wetandcold/", "/dbm resources/")
# Name tokens for loose / non-body / first-person meshes to skip.
_SKIP_NAME = ("1stperson", "firstperson", "1stp", "1st_", "cape", "cloak",
              "skirt", "dress", "robe", "boot", "shoe", "glove", "gauntlet",
              "hood", "helm", "hair", "/head", "hand", "feet", "ground",
              "sheath", "weap", "_0.nif")
# Prefer meshes whose name suggests a body-fitted torso/suit piece.
_PREFER = ("body", "cuirass", "suit", "corset", "bikini", "chest", "outfit",
           "torso", "armor", "top", "pants", "leg")


def discover(root: Path, n: int):
    """One body-fitted '_1.nif' per custom mod folder, up to n."""
    picks, seen = [], set()
    cands = sorted(root.rglob("*_1.nif"))
    for stage in (True, False):   # pass 1: name-preferred; pass 2: any leftover
        for p in cands:
            low = str(p).replace("\\", "/").lower()
            if any(v in low for v in _VANILLA):
                continue
            if any(s in low for s in _SKIP_NAME):
                continue
            try:
                mod = p.relative_to(root).parts[0]
            except Exception:
                continue
            if mod in seen:
                continue
            if stage and not any(k in p.name.lower() for k in _PREFER):
                continue
            picks.append(p)
            seen.add(mod)
            if len(picks) >= n:
                return picks
    return picks


def run_one(path, bv, bn, field, exc, max_dist, btree):
    pyn = _pynifly()
    nf = pyn.NifFile(filepath=str(path))
    tot_eval = tot_static = tot_jiggle = 0
    cause_clip = {"under-weight": 0, "standoff-tight": 0}
    max_margin = 0.0
    for s in nf.shapes:
        nm = (s.name or "")
        if any(k in nm.lower() for k in _CONFORM_SKIP_NAMES):
            continue
        if not (s.bone_names or []):
            continue
        try:
            gv, _ = garment_world_verts(s, btree)
        except Exception:
            continue
        if len(gv) == 0:
            continue
        pred = predict_clip(gv, s.bone_weights or {}, bv, bn, field,
                            excursion=exc, max_body_dist=max_dist)
        rep = summarize(pred)
        tot_eval += rep["n_eval"]
        tot_static += rep["n_static"]
        tot_jiggle += rep["n_jiggle"]
        if rep["n_jiggle"] and rep["cause"] in cause_clip:
            cause_clip[rep["cause"]] += rep["n_jiggle"]
            max_margin = max(max_margin, rep.get("max_jiggle_margin", 0.0))
    cause = max(cause_clip, key=cause_clip.get) if tot_jiggle else "-"
    return tot_eval, tot_static, tot_jiggle, cause, max_margin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--body", default=None)
    ap.add_argument("--weight", default="_1")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--excursion", default=None)
    ap.add_argument("--max-dist", type=float, default=10.0)
    a = ap.parse_args()

    root = Path(a.root) if a.root else Path(
        r"D:/Modlists/ARR/mods/CBBEtoUBE Auto/meshes/!UBE")
    exc = _parse_excursion(a.excursion)
    bpath, bv, bn, bw, nverts = _load_body(a.body, a.weight)
    field = jiggle_field(bw, nverts, excursion=exc)
    btree = cKDTree(bv)
    print(f"BODY {bpath.name}: {nverts}v, jiggle field max={field.max():.2f}u "
          f"nonzero={int((field>0).sum())}  E={exc}\n")

    picks = discover(root, a.n)
    print(f"{'MOD / armor':46s} {'eval':>7s} {'static':>7s} {'JIGGLE':>7s} "
          f"{'jig%':>5s} {'cause':>14s} {'maxJ':>7s}")
    print("(static = rest-pose penetration / concave-normal artifact, the "
          "anti-poke's domain; JIGGLE = the motion-clip target)")
    print("-" * 100)
    agg_eval = agg_static = agg_jiggle = 0
    for p in picks:
        try:
            te, ts, tj, cause, mm = run_one(p, bv, bn, field, exc, a.max_dist, btree)
        except Exception as e:
            print(f"{str(p.relative_to(root))[:46]:46s}  !! {type(e).__name__}: {e}")
            continue
        agg_eval += te
        agg_static += ts
        agg_jiggle += tj
        jpct = (tj / te * 100) if te else 0.0
        label = str(p.relative_to(root))
        if len(label) > 46:
            label = "..." + label[-43:]
        print(f"{label:46s} {te:7d} {ts:7d} {tj:7d} {jpct:4.1f}% "
              f"{cause:>14s} {mm:6.2f}u")
    print("-" * 100)
    print(f"{'TOTAL':46s} {agg_eval:7d} {agg_static:7d} {agg_jiggle:7d} "
          f"{(agg_jiggle/agg_eval*100) if agg_eval else 0:4.1f}%")
    print("\nNOTE: worst-case kinematic predictor. Margins quantitative only "
          "after E[] is calibrated in-game; counts/cause are robust to E scale.")


if __name__ == "__main__":
    main()
