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

"""Semantic A/B diff of two converted NIFs: verts, bone sets, per-vert weights.

BYTE comparison is useless here -- the NIF writer is nondeterministic -- so
compare the DATA. Use this as the nondeterminism control on any flag A/B:
convert the SAME arm twice and diff; every field must read zero before a
cross-arm delta means anything.

Usage:  python scripts/analysis/nif_semantic_diff.py <a.nif> <b.nif>
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                       # noqa: E402
from pyn import pynifly                  # noqa: E402


def load(p):
    nf = pynifly.NifFile(filepath=str(p))
    out = {}
    for s in nf.shapes:
        w = {}
        for bn, pairs in (getattr(s, "bone_weights", None) or {}).items():
            pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
            w[bn] = {int(i): float(x) for i, x in pl}
        out[s.name] = (np.asarray(s.verts, float), set(s.bone_names or []), w)
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a, b = load(Path(sys.argv[1])), load(Path(sys.argv[2]))
    if set(a) != set(b):
        print(f"SHAPE SET DIFFERS: only A {sorted(set(a)-set(b))}  "
              f"only B {sorted(set(b)-set(a))}")
    drift = 0
    for n in sorted(set(a) & set(b)):
        va, ba, wa = a[n]
        vb, bb, wb = b[n]
        if va.shape != vb.shape:
            print(f"  {n:22} VERT COUNT {len(va)} -> {len(vb)}")
            drift += 1
            continue
        d = np.linalg.norm(va - vb, axis=1)
        moved = int((d > 1e-4).sum())
        nvw = 0
        mx = 0.0
        for bn in set(wa) | set(wb):
            da, db = wa.get(bn, {}), wb.get(bn, {})
            for i in set(da) | set(db):
                dd = abs(da.get(i, 0.0) - db.get(i, 0.0))
                if dd > 1e-6:
                    nvw += 1
                    mx = max(mx, dd)
        drift += moved + nvw + len(ba ^ bb)
        print(f"  {n:22} v{len(va):6}  moved {moved:6} max {d.max():7.4f}u | "
              f"bones A{len(ba):3} B{len(bb):3} onlyA {sorted(ba-bb)} "
              f"onlyB {sorted(bb-ba)} | weight entries changed {nvw} "
              f"max {mx:.4f}")
    print(f"\ntotal drift units: {drift}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
