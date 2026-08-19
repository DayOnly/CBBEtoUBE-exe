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

"""How far do the `#morphtri-no-leg-graft` gates reach, and did any gated shape
NEED the body-follow it lost?

THE GATE: a shape whose source mod ships a sibling `<stem>.tri` naming that
shape keeps its authored skin. Four passes skip it -- the leg-detail graft, the
jiggle transfer, and the three limb-motion matches (gated once in the shared
`_match_limb_motion_to_body`). Reach is measured with the REAL
`_source_morph_tri_shape_names`, never a reimplementation.

WHAT "NEEDED IT" MEANS. The morph TRI gives PRESET morph -- BodyMorph moves the
shape to the player's slider values. It does NOT give per-animation jiggle;
that came from the grafted breast/butt/belly bones. So a gated shape has really
lost something only where it BOTH occupies a jiggle region AND carries no
jiggle bone of its own there. Where the author already weighted the region, the
graft was redundant and the gate costs nothing.

This is an UPPER BOUND: each gated pass has preconditions of its own, so being
gated is not proof the pass would have fired. `morphtri_gate_ab.py` settles that
on the artefact.

CONTROLS (both ABORT):
  * NEGATIVE -- the heavy cuirass the gate was built on MUST be gated.
  * MUST-NOT-FIRE -- a source with NO sibling .tri must NEVER be gated. If one
    is, the reader is matching something other than the TRI.

RESULT 2026-08-05 (on a 1500-NIF population; rerun wide for current figures):
88.2% of shapes gated, because 94.3% of a BodySlide-built modlist ships a morph
TRI. 1184 distinct shapes occupy a jiggle region with no authored jiggle bone
there. The design concern: the gate keys on "owns a morph TRI" where the
project's source-follow work says the axis should be "did the AUTHOR weight
this region" -- those disagree on exactly those 1184 shapes.

Usage:  python scripts/analysis/morphtri_gate_census.py [--control <substr>]
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.analysis import _census_common as C     # noqa: E402
import numpy as np                                   # noqa: E402
from src import body_zones as bz                     # noqa: E402
from src import nif_convert as nc                    # noqa: E402
from src import nif_io                               # noqa: E402

MIN_REGION_VERTS = 25       # a shape "occupies" a region at this many verts
MIN_BONE_WEIGHT = 1e-3      # a bone counts as authored above this
REGION_MASK = {"breast": bz.breast_mask, "butt": bz.butt_mask,
               "belly": bz.belly_mask}


def main() -> int:
    argv = sys.argv[1:]
    control = (argv[argv.index("--control") + 1]
               if "--control" in argv else "orcish")
    out_json = Path(argv[argv.index("--out") + 1]
                    if "--out" in argv else "morphtri_gated.json")

    mods, prof, ube = C.layout()
    wins, n_found = C.converted_population(mods, prof, ube)
    print(f"discovered           : {n_found}")
    print(f"  NOT in output pack : {n_found - len(wins)}  (EXCLUDED)")
    print(f"  POPULATION         : {len(wins)} converted source .nif",
          flush=True)

    unreadable = bake_fail = n_shapes = nif_tri = nif_no_tri = ungated = 0
    gated, no_tri_but_gated = [], []
    region_counts, at_risk = Counter(), defaultdict(list)

    for i, w in enumerate(wins, 1):
        if i % 300 == 0:
            print(f"  ...{i}/{len(wins)}", flush=True)
        src = Path(w.source_path)
        try:
            shapes = list(nif_io.load_nif(src).shapes)
        except Exception:
            unreadable += 1
            continue
        tri_names = nc._source_morph_tri_shape_names(src)      # THE REAL GATE
        stem = src.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        has_tri = (src.parent / (stem + ".tri")).is_file()
        nif_tri += 1 if has_tri else 0
        nif_no_tri += 0 if has_tri else 1
        rel = str(w.relative_path)
        for s in shapes:
            n_shapes += 1
            if s.name not in tri_names:
                ungated += 1 if has_tri else 0
                continue
            if not has_tri:
                no_tri_but_gated.append((rel, s.name))
                continue
            try:
                v = C.baked_verts(s)
            except Exception as e:
                # NEVER roll this into an exclusion bucket. A swallowed bake
                # failure once made this census report a gate reaching ZERO
                # shapes, and only the negative control exposed it.
                bake_fail += 1
                if bake_fail <= 3:
                    print(f"  BAKE FAILED {rel} / {s.name}: "
                          f"{type(e).__name__}: {e}", flush=True)
                continue
            authored = set()
            for bn, pairs in (getattr(s, "bone_weights", None) or {}).items():
                r = nc._jiggle_region_of(bn)
                if r is None:
                    continue
                pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
                if any(float(x) > MIN_BONE_WEIGHT for _i, x in pl):
                    authored.add(r)
            occupied = set()
            for r, fn in REGION_MASK.items():
                try:
                    if int(np.asarray(fn(v)).sum()) >= MIN_REGION_VERTS:
                        occupied.add(r)
                except Exception:
                    pass
            gated.append((rel, s.name, len(v), occupied, authored))
            for r in occupied:
                region_counts[r] += 1
                if r not in authored:
                    at_risk[r].append((rel, s.name, len(v)))

    # Data BEFORE assertions: a control failure must never destroy the run.
    json.dump({"gated": [{"rel": r, "shape": s, "verts": n,
                          "occupies": sorted(o), "authored": sorted(a)}
                         for r, s, n, o, a in gated]},
              open(out_json, "w"), indent=1)

    print()
    print("POPULATION ACCOUNTING")
    print(f"  unreadable (excluded)            : {unreadable}")
    print(f"  transform bake FAILED (excluded) : {bake_fail}")
    print(f"    ships a sibling morph TRI      : {nif_tri}")
    print(f"    ships NO morph TRI             : {nif_no_tri}")
    print(f"  shapes seen                      : {n_shapes}")
    print(f"    in a TRI-owning NIF, not named in it (ungated): {ungated}")
    print(f"    GATED                          : {len(gated)}")
    print(f"    in a NIF with no TRI           : "
          f"{n_shapes - ungated - len(gated) - bake_fail}")
    if n_shapes:
        print(f"\nTHE GATE'S REACH: {len(gated)} / {n_shapes} shapes = "
              f"{100.0*len(gated)/n_shapes:.1f}% of the converted pack")
        print(f"  {nif_tri} / {len(wins)} NIFs = "
              f"{100.0*nif_tri/max(len(wins),1):.1f}% ship a morph TRI")

    print()
    print("DID THEY NEED THE FOLLOW? (per jiggle region)")
    print(f"{'region':8} {'gated & occupies':>17} {'already authored':>17} "
          f"{'LOST FOLLOW':>12}")
    for r in ("breast", "butt", "belly"):
        occ, risk = region_counts[r], len(at_risk[r])
        print(f"{r:8} {occ:17} {occ-risk:17} {risk:12}")
    lost = {(rel, sh) for r in at_risk for rel, sh, _n in at_risk[r]}
    print(f"\ndistinct gated shapes occupying a jiggle region with NO authored "
          f"jiggle bone there: {len(lost)}")
    for r in ("breast", "butt", "belly"):
        if not at_risk[r]:
            continue
        print(f"\n  worst 10 by vert count -- {r.upper()} follow lost:")
        for rel, sh, nv in sorted(at_risk[r], key=lambda x: -x[2])[:10]:
            print(f"    {rel[:56]:56} {sh[:18]:18} v{nv:6}")

    ctrl = [g for g in gated if control in g[0].lower()]
    print()
    assert ctrl, (
        f"NEGATIVE CONTROL FAILED: no '{control}' shape is gated -- the gate "
        f"does not reach the piece it was built on, so this census is "
        f"measuring nothing")
    assert not no_tri_but_gated, (
        f"MUST-NOT-FIRE CONTROL FAILED: {len(no_tri_but_gated)} shapes gated "
        f"in a NIF with no sibling .tri -- {no_tri_but_gated[:5]}")
    print(f"controls OK: negative control gated ({ctrl[0][1]}), 0 shapes "
          f"gated without a sibling TRI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
