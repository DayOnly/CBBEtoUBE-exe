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

"""Skinning invariant over the converted output (#weight-write-invariant, P6).

Two things every shipped shape must satisfy:

  1. no vertex over the 4-influence format cap
  2. every weighted vertex sums to 1.0

Measured on the 2026-07-25 output BEFORE the P6 fix: (1) already holds -- the save
enforces it by truncating -- but (2) does NOT: weight sums span 0.831 to 1.039.
That is the damage. The save keeps the LARGEST 4 and does not renormalise, so a
row that overflowed ships over- or under-weighted, and the vertex is transformed
by an inflated sum of its bone matrices -- it drifts, dragging near-degenerate
triangles that flicker with view angle.

Run this on the OUTPUT (post-save), never on in-memory shapes: the truncation
happens during the write, which is exactly what an in-memory check cannot see.

    python scripts/analysis/verify_weight_invariant.py [--limit N] [--all]

Read-only. Resolves the output via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                    # noqa: E402
from src import paths                                      # noqa: E402
from src.weights import check_weight_invariant             # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")


def main() -> int:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    elif "--all" not in sys.argv:
        limit = 200                       # default sample: fast, representative

    lay = paths.discover_layout()
    root = (lay.mods_root / OUT_MOD / "meshes" / "!UBE") if lay.mods_root else None
    if root is None or not root.is_dir():
        print("output not found (set CBBE2UBE_MO2_INI + convert first).")
        return 1

    files = [f for f in glob.glob(str(root / "**" / "*_1.nif"), recursive=True)
             if "1stperson" not in f.lower()]
    if limit:
        files = files[:limit]
    print(f"checking {len(files)} mesh(es) under {root}\n", flush=True)

    tot_over = tot_bad = n_shapes = 0
    offenders: list = []
    worst_overall = 0.0
    for f in files:
        try:
            nf = pynifly.NifFile(filepath=f)
        except Exception:
            continue
        rel = f.replace("\\", "/").split("/!UBE/", 1)[-1]
        for s in nf.shapes:
            try:
                if not (s.bone_names or []):
                    continue
            except Exception:
                continue
            n_over, n_bad, worst = check_weight_invariant(s)
            n_shapes += 1
            tot_over += n_over
            tot_bad += n_bad
            worst_overall = max(worst_overall, worst)
            if n_over or n_bad:
                offenders.append((worst, n_over, n_bad, s.name, rel))

    offenders.sort(reverse=True)
    print(f"shapes checked                : {n_shapes}")
    print(f"verts over the 4-influence cap: {tot_over}")
    print(f"verts whose weights != 1.0    : {tot_bad}")
    print(f"worst deviation from 1.0      : {worst_overall:+.3f}\n")

    if offenders:
        print("worst shapes (deviation, over-cap verts, bad-sum verts):")
        for worst, n_over, n_bad, name, rel in offenders[:25]:
            print(f"   {worst:+.3f}  over={n_over:<5d} bad={n_bad:<6d} "
                  f"{name:<22} {rel}")
    else:
        print("clean -- every shape is within the cap and sums to 1.0.")
    # Non-zero exit makes this usable as a gate once P6 lands.
    return 1 if (tot_over or tot_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
