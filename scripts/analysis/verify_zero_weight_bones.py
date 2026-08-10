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

"""Find ZERO-WEIGHT BONES in the converted output (#zeroweight-bone-desync).

A bone that is present in a shape's bone list but carries no weight above the
write threshold is left out of the regenerated skin-partition palette, so a
per-vertex bone index can run past the palette -- an equip CTD. Every pass that
calls `add_bone` is supposed to gate on the bone actually emitting a weight.

This measures whether any survive on disk, and groups them by bone name so the
pattern points at the pass responsible: jiggle bones implicate the jiggle graft,
leg-deform bones the rigid-leg conform, chain bones (`... 1_00`) the physics
framework re-import.

    python scripts/analysis/verify_zero_weight_bones.py [--limit N] [--all]

Read-only. Resolves the output via the live MO2 instance (CBBE2UBE_MO2_INI).
Exit 1 if any are found, so it can gate a run.
"""
from __future__ import annotations

import collections
import glob
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                    # noqa: E402
from src import paths                                      # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
WRITE_MIN = 1e-4


def main() -> int:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    elif "--all" not in sys.argv:
        limit = 300

    lay = paths.discover_layout()
    root = (lay.mods_root / OUT_MOD / "meshes" / "!UBE") if lay.mods_root else None
    if root is None or not root.is_dir():
        print("output not found (set CBBE2UBE_MO2_INI + convert first).")
        return 1

    files = [f for f in glob.glob(str(root / "**" / "*.nif"), recursive=True)]
    if limit:
        files = files[:limit]
    print(f"scanning {len(files)} mesh(es) under {root}\n", flush=True)

    by_bone: collections.Counter = collections.Counter()
    offenders: list = []
    n_shapes = 0
    for f in files:
        try:
            nf = pynifly.NifFile(filepath=f)
        except Exception:
            continue
        rel = f.replace("\\", "/").split("/!UBE/", 1)[-1]
        for s in nf.shapes:
            try:
                bones = list(s.bone_names or [])
            except Exception:
                continue
            if not bones:
                continue
            n_shapes += 1
            empties = []
            for b in bones:
                try:
                    pairs = s.bone_weights[b]
                except Exception:
                    continue
                if not any(w > WRITE_MIN for _, w in pairs):
                    empties.append(b)
                    by_bone[b] += 1
            if empties:
                offenders.append((len(empties), s.name, rel, empties))

    offenders.sort(reverse=True)
    total = sum(by_bone.values())
    print(f"shapes scanned          : {n_shapes}")
    print(f"zero-weight bones found : {total}")
    print(f"shapes affected         : {len(offenders)}\n")

    if by_bone:
        print("by bone name (the pattern names the responsible pass):")
        for b, k in by_bone.most_common(20):
            print(f"   {k:5d}  {b}")
        print("\nworst shapes:")
        for k, shape, rel, empties in offenders[:15]:
            print(f"   {k:3d}  {shape:<22} {rel}")
            print(f"        {', '.join(empties[:6])}"
                  + (" ..." if len(empties) > 6 else ""))
    else:
        print("clean -- every bone in every shape carries a written weight.")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
