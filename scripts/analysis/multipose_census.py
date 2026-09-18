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

"""POSE-AWARE census: run the pose set over every converted armor.

    python scripts/analysis/multipose_census.py [--out FILE] [--limit N] [--sample N]

One JSON row per armor: per region, the worst pose and the fraction of bind-covered
verts it exposes, plus the properties a CLASS might be keyed on (garment shape count,
vert/tri counts, SMP rigging, bust/butt bone presence in the garment).

WHY THE EXTRA PROPERTIES ARE IN THE ROW. A ranked list of bad armours is not a work
plan -- per the standing rule, a fix has to apply to a CLASS identified by something
the converter can measure. Those candidate properties have to be in the same row as
the failure, or answering "what do the bad ones share" means rescanning every mesh.

Bind-pose exposure is NOT recorded as a headline: it is the metric that ranked the
butt least-affected while the user was watching it clip. Runtime ~17s/armor.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                          # noqa: E402
from src import nif_convert as nc, paths                         # noqa: E402
from src.nif_convert import UBE_BODY_INJECT_NAMES                # noqa: E402
from scripts.analysis.multipose_clip_test import analyse, load            # noqa: E402

PHYSICS_ONLY = {"collision", "proxy", "stabilizer"}


def properties(path):
    """Candidate CLASS keys, read straight off the mesh."""
    nif = pynifly.NifFile(str(path))
    body, gar = None, []
    for sh in nif.shapes:
        try:
            if not len(sh.verts):
                continue
        except Exception:
            continue
        if sh.name in UBE_BODY_INJECT_NAMES:
            body = sh
        elif sh.name.strip().lower() not in PHYSICS_ONLY:
            gar.append(sh)
    if body is None or not gar:
        return None
    bb = set(body.bone_names or [])
    gv = sum(len(s.verts) for s in gar)
    gt = 0
    for s in gar:
        try:
            gt += len(s.tris)
        except Exception:
            pass
    bones = set()
    for s in gar:
        bones |= set(s.bone_names or [])
    # Weighted bones only -- `bone_weights`, NOT get_weights (which does not exist
    # on BSTriShape and raises for every bone, reading as "zero weight everywhere").
    weighted = set()
    for s in gar:
        for b, pairs in (getattr(s, "bone_weights", None) or {}).items():
            if pairs is not None and any(w > 1e-4 for _i, w in pairs):
                weighted.add(b)
    low = {b.lower() for b in weighted}
    return {
        "garment_shapes": [s.name for s in gar],
        "n_shapes": len(gar),
        "garment_verts": int(gv), "garment_tris": int(gt),
        "smp_rigged_all": all(nc._shape_has_hdt_smp_rigging(s, bb) for s in gar),
        "smp_rigged_any": any(nc._shape_has_hdt_smp_rigging(s, bb) for s in gar),
        "n_bones": len(bones), "n_weighted_bones": len(weighted),
        "frac_bones_unknown_to_body": round(
            len(weighted - bb) / max(len(weighted), 1), 4),
        "has_breast_bones": any("breast" in b for b in low),
        "has_butt_bones": any("butt" in b for b in low),
        "has_chain_bones": any(("skirt" in b or "chain" in b) for b in low),
    }


def main():
    argv = sys.argv[1:]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    out = Path(opt("--out", "multipose_census.jsonl"))
    limit = int(opt("--limit", "0") or 0)
    sample = int(opt("--sample", "400"))
    lay = paths.discover_layout()
    root = (lay.mods_root / os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
            / "meshes" / "!UBE")
    if not root.is_dir():
        raise SystemExit(f"output not found: {root}")

    files = []
    for p in sorted(root.rglob("*_1.nif")):
        stem = p.stem.lower()
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        # viewmodels are arms-only: no meaningful body-vs-garment answer
        if "1st" in stem or "firstperson" in stem or stem.endswith("fp") \
                or "_fp_" in stem or "1person" in stem:
            continue
        files.append(p)
    if limit:
        files = files[:limit]
    print(f"{len(files)} armors, sample={sample}/region -- roughly "
          f"{len(files) * 17 / 60:.0f} min")

    t0, n, skipped = time.time(), 0, 0
    with out.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(files, 1):
            try:
                props = properties(p)
                if props is None:
                    skipped += 1
                    continue
                res, ident = analyse(p, sample=sample)
            except SystemExit:
                skipped += 1
                continue
            except Exception as exc:
                skipped += 1
                print(f"  SKIP {p.name}: {type(exc).__name__}: {exc}")
                continue
            if ident > 1e-4:
                # Skinning self-test failed: the numbers would be meaningless.
                print(f"  SKIP {p.name}: identity check {ident:.6f}u")
                skipped += 1
                continue
            row = {"armor": str(p.relative_to(root)).replace("\\", "/"),
                   "identity_check": ident, **props,
                   "regions": res,
                   "worst_region": max(res, key=lambda k: res[k]["worst_pct"]),
                   "worst_pct": max(r["worst_pct"] for r in res.values())}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            n += 1
            if i % 20 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(files)}  ({n} rows, {skipped} skipped, "
                      f"{el/60:.0f}m elapsed, ~{el/i*(len(files)-i)/60:.0f}m left)")
    print(f"\n{n} rows -> {out}  ({skipped} skipped, {(time.time()-t0)/60:.0f} min)")


if __name__ == "__main__":
    main()
