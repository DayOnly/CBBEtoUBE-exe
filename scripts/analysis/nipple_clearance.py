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

"""How much ROOM is there over the body's nipple, per arm?

    python -m scripts.analysis.nipple_clearance <arm_dir>[=label] ...

MEASURE THE MARGIN, NOT THE BREACH. Binary exposure only fires once the margin
has already gone, so a pass/fail exposure count cannot warn you before a report
arrives -- and one did: "leather armor now has nipples poking through again but
by the smallest amount", against a build whose exposure counters were clean. The
margin is what the player's slider or preset eats into before anything shows.

So: cast each nipple-TIP body vertex along its own outward normal and take the
distance to the FIRST garment triangle. Reported as p50 / p05 / min, because the
median moves with the whole pack while p05 is where the next report comes from.

A RAY, NOT A NEAREST-POINT NORMAL. Nearest-point normals FLIP on curved
geometry and fabricated a 226-vertex "defect" at 4.633u on a glove that the ray
scores at 0.000% -- see `project_extremity_digit_refit_gap`. `ray_first_hit` is
the same primitive the containment metric settled on.

The tip mask is `_body_nipple_weight >= 0.75 * max`, which is the SAME mask
`#authored-nipple-exempt` exempts in the converter. That is deliberate: this
harness has to score the exact region the fix acts on, or a fix that moved a
neighbouring band would read as a win here.

EXCLUSIONS ARE COUNTED, never silent. A piece is scored only where at least 20
tip rays hit garment within 12u -- below that the garment does not cover the
nipple at all (a boot, a gauntlet, an open-chested corset) and a "clearance" for
it would be measuring the room over bare skin. Pieces missing from any arm are
dropped too, so every arm is scored on ONE population.

Nothing here is hardcoded to a machine or a mod: the body comes from
`canonical_body.canonical_ube()`.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import logging                                                  # noqa: E402
logging.disable(logging.WARNING)

import numpy as np                                              # noqa: E402
from pyn.pynifly import NifFile                                 # noqa: E402

from src import nif_convert as nc                               # noqa: E402
from src.nif_convert_bust import _body_nipple_weight            # noqa: E402
from scripts.analysis.canonical_body import canonical_ube       # noqa: E402
from scripts.analysis.mesh_penetration import ray_first_hit     # noqa: E402

# Fraction of the peak nipple weight that counts as the TIP.
TIP_WEIGHT_FRAC = 0.75
# A hit further than this is another garment layer or the far side of the body,
# not the cloth sitting over this nipple.
MAX_HIT = 12.0
# Below this many hits the garment does not cover the nipple at all.
MIN_TIP_HITS = 20

BODY_NAMES = set(nc.UBE_BODY_INJECT_NAMES) | {"3BA"}
PROXY_NAMES = {"virtualbody", "virtualground", "skirtcol", "buttcol"}


def tip_rays():
    """(origins, directions) for the body's nipple-tip vertices, world space."""
    body_path, body_shape_name = canonical_ube()
    shape = next(s for s in NifFile(str(body_path)).shapes
                 if s.name == body_shape_name)
    verts = np.asarray(shape.verts, np.float64)
    normals = np.asarray(nc._body_normals_or_compute(shape), np.float64)
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    if not np.isfinite(lens).all() or float(lens.max()) <= 0.0:
        raise SystemExit(
            "body normals are degenerate -- the ray has no direction to cast. "
            "Refusing rather than reporting 0.000u clearance everywhere.")
    normals = normals / np.clip(lens, 1e-9, None)
    weight = np.asarray(_body_nipple_weight(shape), np.float64)
    if not weight.size or float(weight.max()) <= 0.0:
        raise SystemExit("no nipple weight on the canonical body -- "
                         "0/0 IS NOT A PASS")
    tip = weight >= TIP_WEIGHT_FRAC * float(weight.max())
    return verts[tip], normals[tip]


def garment_mesh(path):
    """Every rendering garment shape of one NIF, welded, in BODY space."""
    verts, tris, offset = [], [], 0
    for s in NifFile(str(path)).shapes:
        name = (s.name or "").strip()
        if name in BODY_NAMES or name.lower() in PROXY_NAMES:
            continue
        v = np.asarray(s.verts, np.float64) + nc.shape_body_offset(s)
        t = np.asarray(s.tris, np.int64).reshape(-1, 3)
        if len(v) and len(t):
            verts.append(v)
            tris.append(t + offset)
            offset += len(v)
    if not verts:
        return None, None
    return np.vstack(verts), np.vstack(tris)


def clearance(path, origins, directions):
    """Tip clearances for one piece, or None when it does not cover the nipple."""
    verts, tris = garment_mesh(path)
    if verts is None:
        return None
    d = np.asarray(ray_first_hit(origins, directions, verts, tris), np.float64)
    hit = np.isfinite(d) & (d > 0) & (d < MAX_HIT)
    return d[hit] if int(hit.sum()) >= MIN_TIP_HITS else None


def main(argv) -> int:
    arms = [(a.split("=", 1) + [a.split("=", 1)[0]])[:2] for a in argv]
    if not arms:
        print(__doc__)
        return 2
    origins, directions = tip_rays()
    base = arms[0][0]
    rels = [os.path.relpath(p, os.path.join(base, "meshes"))
            for p in sorted(glob.glob(
                os.path.join(base, "meshes", "**", "*_1.nif"), recursive=True))
            if "1stperson" not in os.path.basename(p).lower()]

    rows, skip_missing, skip_uncovered = [], 0, 0
    for rel in rels:
        vals = {}
        for arm_dir, label in arms:
            p = os.path.join(arm_dir, "meshes", rel)
            if not os.path.isfile(p):
                skip_missing += 1
                vals = {}
                break
            c = clearance(p, origins, directions)
            if c is None:
                skip_uncovered += 1
                vals = {}
                break
            vals[label] = c
        if len(vals) == len(arms):
            rows.append((rel, vals))

    print(f"tip vertices cast              : {len(origins)}")
    print(f"`_1` pieces found in arm 1     : {len(rels)}")
    print(f"  not present in every arm     : {skip_missing}")
    print(f"  garment does not cover the tip: {skip_uncovered}")
    print(f"pieces covering the nipple in EVERY arm: {len(rows)}")
    if not rows:
        print("  0/0 IS NOT A PASS")
        return 1

    labels = [lab for _d, lab in arms]
    print(f"\n{'arm':<22} {'tip clearance p50':>18} {'p05':>8} {'min':>8}")
    for lab in labels:
        allv = np.concatenate([v[lab] for _r, v in rows])
        print(f"{lab:<22} {np.median(allv):>18.3f} "
              f"{np.percentile(allv, 5):>8.3f} {allv.min():>8.3f}")

    if len(labels) < 2:
        return 0
    a, b = labels[0], labels[-1]
    print(f"\nper piece, {b} minus {a} (negative = LESS room over the nipple):")
    deltas = [(float(np.median(v[b]) - np.median(v[a])), r) for r, v in rows]
    tighter = [d for d, _ in deltas if d < -0.005]
    looser = [d for d, _ in deltas if d > 0.005]
    print(f"  tighter {len(tighter)}   looser {len(looser)}   flat "
          f"{len(deltas) - len(tighter) - len(looser)}   median delta "
          f"{np.median([d for d, _ in deltas]):+.4f}u")
    for d, r in sorted(deltas)[:10]:
        print(f"   {d:+.4f}u  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
