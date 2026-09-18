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

"""How many pack shapes flip `fine_anim` True->False under
`#leg-garment-not-extremity`, and how much margin the classifier really has.

A CLASSIFIER CHANGE IS WHAT REGRESSED BOOTS IN JUNE, so this measures the REAL
predicate (`nif_convert._shape_has_fine_animation_bones`) rather than a
reimplementation, and reports the DISTRIBUTION around the decision boundary
rather than a count. The count says how much moved; only the distribution says
whether the next mesh will move too.

A/B IN ONE PROCESS, on the module global. The guard reads
`LEG_GARMENT_NOT_EXTREMITY` at CALL time, so both arms see the same shape
object and there is no "env flag read at import in a sibling process" trap. The
live value is printed beside the result.

CONTROLS (both ABORT):
  * NEGATIVE -- the full-length leg garment the guard was built for MUST flip.
    If the guard cannot fire on its own motivating case, the census is
    measuring nothing.
  * MUST-NOT-MOVE -- no real footwear mesh may flip. That is the June
    regression class.

RESULT 2026-08-05: 122 of 4269 shapes flip, and the must-not-move control
FAILED -- 20 footwear shapes moved, the closest at ratio 0.975 while a heel
survived at 1.02. Real footwear spans 0.59-1.02 across a boundary at 1.00.
Root cause of the asymmetry: HAND_FOOT_NAME_KEYWORDS protects HANDS by name
(hand/glove/gauntlet/...) and has no foot equivalent, so every boot must
survive on the weight-mass test alone.

Usage:  python scripts/analysis/fine_anim_flip_census.py [--control <substr>]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.analysis import _census_common as C     # noqa: E402
import numpy as np                                   # noqa: E402
from src import nif_convert as nc                    # noqa: E402
from src import nif_io                               # noqa: E402

# Path fragments that mark a mesh as REAL footwear/handwear -- the class that
# must not be reclassified. Deliberately path-level: a shape merely NAMED
# "boots" inside a full outfit is not footwear.
EXTREMITY_PATH_KEYS = ("boots", "boot", "gauntlets", "gauntlet", "gloves",
                       "glove", "shoes", "bracers")


def masses(shape):
    """(extremity mass, thigh+pelvis mass), exactly as the guard computes them."""
    ext_bones = {b for b in (shape.bone_names or [])
                 if any(k in b.lower() for k in nc.RESKIN_PRESERVE_BONE_KEYWORDS)}
    ext = up = 0.0
    for bn, pairs in (getattr(shape, "bone_weights", None) or {}).items():
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        s = sum(float(w) for _i, w in pl)
        if bn in ext_bones:
            ext += s
        low = bn.lower()
        if (("thigh" in low and "front" not in low and "rear" not in low)
                or "pelvis" in low):
            up += s
    return ext, up


def main() -> int:
    argv = sys.argv[1:]
    control = (argv[argv.index("--control") + 1]
               if "--control" in argv else "miraakrobes")

    mods, prof, ube = C.layout()
    print(f"guard default in this process: "
          f"LEG_GARMENT_NOT_EXTREMITY={nc.LEG_GARMENT_NOT_EXTREMITY}")
    wins, n_found = C.converted_population(mods, prof, ube)
    print(f"discovered           : {n_found} winning source .nif")
    print(f"  NOT in output pack : {n_found - len(wins)}  (EXCLUDED -- male "
          f"mesh under the female-only policy, filtered slot, coexistence "
          f"skip, or a hard failure)")
    print(f"  POPULATION         : {len(wins)} converted source .nif",
          flush=True)

    unreadable = n_shapes = name_hit = no_ext = below_cluster = at_risk = 0
    flips, kept = [], []
    for i, w in enumerate(wins, 1):
        if i % 400 == 0:
            print(f"  ...{i}/{len(wins)}", flush=True)
        try:
            shapes = list(nif_io.load_nif(w.source_path).shapes)
        except Exception:
            unreadable += 1
            continue
        rel = str(w.relative_path)
        for s in shapes:
            n_shapes += 1
            try:
                nv = len(s.verts)
            except Exception:
                nv = 0
            nc.LEG_GARMENT_NOT_EXTREMITY = False        # arm A: pre-session
            try:
                base = nc._shape_has_fine_animation_bones(s)
            except Exception:
                unreadable += 1
                continue
            nm = (getattr(s, "name", "") or "").lower()
            named = any(k in nm for k in nc.HAND_FOOT_NAME_KEYWORDS)
            if not base:
                if named:
                    name_hit += 1
                else:
                    eb = {b for b in (s.bone_names or [])
                          if any(k in b.lower()
                                 for k in nc.RESKIN_PRESERVE_BONE_KEYWORDS)}
                    no_ext += 1 if not eb else 0
                    below_cluster += 0 if not eb else 1
                continue
            if named or nv == 0:
                name_hit += 1        # guard unreachable: it sits after these
                continue
            at_risk += 1
            nc.LEG_GARMENT_NOT_EXTREMITY = True         # arm B: shipped
            guarded = nc._shape_has_fine_animation_bones(s)
            ext, up = masses(s)
            (flips if not guarded else kept).append((rel, s.name, nv, ext, up))
    nc.LEG_GARMENT_NOT_EXTREMITY = True

    acc = no_ext + below_cluster + name_hit + at_risk
    print()
    print("POPULATION ACCOUNTING (shapes)")
    print(f"  unreadable NIFs (excluded)     : {unreadable}")
    print(f"  shapes seen                    : {n_shapes}")
    print(f"    no extremity bone   -> False : {no_ext}")
    print(f"    below vert-cluster  -> False : {below_cluster}")
    print(f"    name-check / no verts -> True (guard unreachable): {name_hit}")
    print(f"    REACHED THE GUARD            : {at_risk}")
    print(f"      stayed True (extremity)    : {len(kept)}")
    print(f"      FLIPPED True->False        : {len(flips)}")
    print(f"  ---- accounted: {acc} / {n_shapes}"
          f"{'  OK' if acc == n_shapes else '  *** MISMATCH ***'}")

    by_piece = defaultdict(list)
    for rel, name, nv, ext, up in flips:
        by_piece[rel].append(name)
    print(f"\nFLIPPED: {len(flips)} shapes across {len(by_piece)} NIFs")

    # The guard is `ext > up`, so the boundary is ratio 1.00. A flip at 0.03 is
    # decided by the geometry; a flip at 0.97 is decided by the threshold.
    fr = sorted((ext / up if up > 0 else float("inf"), rel, name, nv)
                for rel, name, nv, ext, up in flips)
    print("\nflips at ratio >= 0.50 -- the ones the THRESHOLD decides:")
    for r, rel, name, nv in [f for f in fr if f[0] >= 0.50]:
        foot = any(k in rel.lower() or k in name.lower()
                   for k in EXTREMITY_PATH_KEYS)
        print(f"  x{r:5.3f}  {rel[:52]:52} {name[:18]:18} v{nv:6}"
              f"{'  <-- FOOTWEAR' if foot else ''}")

    mar = sorted((float("inf") if up <= 0 else ext / up, rel, name)
                 for rel, name, _nv, ext, up in kept)
    finite = [m for m in mar if m[0] != float("inf")]
    print(f"\nMARGIN on shapes that stayed True: {len(mar)-len(finite)} of "
          f"{len(mar)} have zero upper-leg mass (margin infinite)")
    if finite:
        q = np.percentile([m[0] for m in finite], [0, 10, 50, 90])
        print(f"  finite margins  min {q[0]:.2f}  p10 {q[1]:.2f}  "
              f"med {q[2]:.2f}  p90 {q[3]:.2f}")
        print("  tightest 10 (closest to flipping):")
        for r, rel, name in finite[:10]:
            print(f"    x{r:8.2f}  {rel[:52]:52} {name[:16]}")

    ctrl = [f for f in flips if control in f[0].lower()]
    boot = [f for f in flips
            if any(k in f[0].lower() for k in EXTREMITY_PATH_KEYS)]
    print()
    assert ctrl, (
        f"NEGATIVE CONTROL FAILED: nothing in a '{control}' mesh flipped -- "
        f"the guard did not fire on the piece it was built for, so this "
        f"census is measuring nothing")
    assert not boot, (
        f"MUST-NOT-MOVE CONTROL FAILED: {len(boot)} real footwear/handwear "
        f"shapes flipped. That is the June regression class. First few: "
        f"{[(b[0], b[1]) for b in boot[:5]]}")
    print(f"controls OK: negative control fired ({ctrl[0][1]}), "
          f"0 real footwear meshes moved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
