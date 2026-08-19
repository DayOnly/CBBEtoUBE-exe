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

"""Physics-cloth health across the pack: WHY a simulated garment clips the body.

Three independent things must hold for SMP cloth not to sink into the body, and
they fail separately, so a single "it clips" report cannot tell you which to fix:

  1. COLLISION IS DECLARED. HDT-SMP collision is mutual -- either side naming the
     other's tag is enough -- so cloth naming no body-ish tag cannot collide with
     the body whatever the collider says.
  2. A BODY COLLIDER EXISTS in the NIF for it to collide against.
  3. THE REST POSE IS OUTSIDE THE BODY. This is the one nothing measured, and it
     is decisive: hdtSMP64's own maintainers call the sphere-triangle penetration
     path "obviously wrong", so cloth that STARTS inside the body is not reliably
     pushed out. Collision resolves approaching geometry, not existing overlap.

And one thing must NOT hold:

  4. NOT AN UNCONSTRAINED COLLISION PAIR (per-vertex + per-triangle + no
     generic-constraint). That combination diverges and takes FSMP's collision
     SIMD out of bounds -- an equip CTD. It is why (1) cannot simply be
     auto-fixed everywhere: adding body collision to unconstrained cloth CAUSES
     the crash.

Usage:
    python scripts/analysis/physics_cloth_health.py <output meshes dir> [limit]

Reports the population accounting first. A shrinking denominator is how a census
flatters itself, so every exclusion is counted and printed.
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# Canonical spelling so test_analysis_repo_root can verify the level.
_REPO = Path(__file__).resolve().parent.parent.parent
REPO = _REPO
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO))

import numpy as np                                     # noqa: E402
from scipy.spatial import cKDTree                      # noqa: E402
from src import nif_convert as nc                      # noqa: E402

BODY_TAGS = {"body", "body2", "colbody", "bodycol"}
PENETRATION_SAMPLE = 4000      # cap per shape; these meshes reach 30k+ verts


def _xml_for(nif_path: Path):
    """The physics XML a NIF references, resolved the way the converter does."""
    try:
        txt = nc._read_source_hdt_xml_text(nif_path)
    except Exception:
        return None
    return txt


def _parse(txt):
    try:
        root = ET.fromstring(txt)
    except Exception:
        return None
    if root.tag != "system":
        return None
    cloth = {}
    for sh in root.findall("per-vertex-shape"):
        name = sh.get("name") or "?"
        cloth[name] = {(t.text or "").strip().lower()
                       for t in sh.findall("can-collide-with-tag")}
    colliders = [sh.get("name") for sh in root.findall("per-triangle-shape")]
    return {
        "cloth": cloth,
        "colliders": [c for c in colliders if c],
        "constrained": root.find("generic-constraint") is not None,
    }


def _penetration(nif, cloth_names):
    """Worst rest-pose depth of each cloth INSIDE the injected body, in units.

    Signed along the body's outward normal at the nearest body vertex; positive
    means the cloth vertex sits inside. Returns {} when the NIF carries no
    injected body -- reported as UNKNOWN rather than counted clean.
    """
    shapes = {s.name: s for s in nif.shapes}
    body = shapes.get("BaseShape")
    if body is None:
        return None
    bv = np.array(body.verts, np.float64)
    bt = np.array(body.tris, np.int64)
    if len(bv) == 0 or len(bt) == 0:
        return None
    bn = nc._vertex_normals_from_tris(bv, bt)
    tree = cKDTree(bv)
    out = {}
    for name in cloth_names:
        s = shapes.get(name)
        if s is None:
            continue
        v = np.array(s.verts, np.float64)
        if len(v) == 0:
            continue
        if len(v) > PENETRATION_SAMPLE:
            step = max(1, len(v) // PENETRATION_SAMPLE)
            v = v[::step]
        _d, i = tree.query(v, k=1)
        signed = np.einsum("ij,ij->i", v - bv[i], bn[i])
        inside = -signed                      # positive = inside the body
        out[name] = (float(inside.max()), int((inside > 0.05).sum()), len(v))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    nifs = sorted(root.rglob("*.nif"))
    if limit:
        nifs = nifs[:limit]

    N = nc._pynifly().NifFile
    skip = Counter()
    rows = []
    for k, p in enumerate(nifs):
        if k % 400 == 0:
            print(f"  ...{k}/{len(nifs)}", flush=True)
        txt = _xml_for(p)
        if not txt:
            skip["no physics xml (not simulated cloth)"] += 1
            continue
        info = _parse(txt)
        if info is None:
            skip["xml unparseable"] += 1
            continue
        if not info["cloth"]:
            skip["xml has no per-vertex cloth"] += 1
            continue
        try:
            nif = N(str(p))
        except Exception:
            skip["nif unreadable"] += 1
            continue
        pen = _penetration(nif, list(info["cloth"]))
        shape_names = {s.name for s in nif.shapes}
        rows.append({
            "path": str(p.relative_to(root)).replace("\\", "/"),
            "constrained": info["constrained"],
            "collider_in_nif": any(c in shape_names for c in info["colliders"]),
            "cloth": info["cloth"],
            "pen": pen,
        })

    print("\n" + "=" * 72)
    print("POPULATION ACCOUNTING")
    print("=" * 72)
    print(f"  NIFs walked                : {len(nifs)}")
    for reason, n in skip.most_common():
        print(f"  EXCLUDED {reason:<34}: {n}")
    print(f"  MEASURED simulated pieces  : {len(rows)}")
    if not rows:
        return 0

    no_body_tag = [r for r in rows
                   if any(not (t & BODY_TAGS) for t in r["cloth"].values())]
    no_collider = [r for r in rows if not r["collider_in_nif"]]
    crash_class = [r for r in rows
                   if not r["constrained"] and r["collider_in_nif"]]
    pen_unknown = [r for r in rows if r["pen"] is None]
    pen_known = [r for r in rows if r["pen"]]
    penetrating = [r for r in pen_known
                   if any(v[1] > 0 for v in r["pen"].values())]

    print("\nFAULTS (a piece can carry more than one)")
    print(f"  cloth declares NO body collision : {len(no_body_tag):5d}"
          f"   ({100*len(no_body_tag)/len(rows):.1f}%)")
    print(f"     ...of those, CONSTRAINED (fixable safely) : "
          f"{sum(1 for r in no_body_tag if r['constrained'])}")
    print(f"     ...of those, unconstrained (fix = equip CTD): "
          f"{sum(1 for r in no_body_tag if not r['constrained'])}")
    print(f"  body collider named but ABSENT   : {len(no_collider):5d}")
    print(f"  unconstrained collision pair     : {len(crash_class):5d}"
          f"   <- known equip-CTD pattern")
    print(f"\n  REST POSE INSIDE THE BODY        : {len(penetrating):5d}"
          f"   of {len(pen_known)} measurable"
          + (f"   ({len(pen_unknown)} have no injected body -> UNKNOWN, "
             f"not counted clean)" if pen_unknown else ""))

    if penetrating:
        worst = []
        for r in penetrating:
            for nm, (mx, cnt, tot) in r["pen"].items():
                if cnt:
                    worst.append((mx, cnt, tot, nm, r["path"]))
        worst.sort(reverse=True)
        print("\n  WORST REST-POSE PENETRATION (this is what collision cannot fix)")
        for mx, cnt, tot, nm, path in worst[:20]:
            print(f"     {mx:6.2f}u  {cnt:5d}/{tot:<5d} verts inside  "
                  f"{nm[:18]:<18} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
