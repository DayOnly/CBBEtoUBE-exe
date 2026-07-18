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

"""Triage tool: find converted armors whose bust band stands OFF the body -- the
over-inflation / bundled-body-mismatch class (a band floating off the bust). Source-free
and thickness-gated so it targets TIGHT bands/bras that should hug, without false-
flagging voluminous plate (a plate stands off but isn't a thin shell).

    python scripts/find_overinflation.py

Per converted body-swap torso piece it measures, at the breast band:
  gap   = mean under-band air gap (body -> nearest armor, along the body normal)
  shell = mean armor thickness there (thin = a single-layer band, not a cuirass)
Flags gap > 1.2u AND shell < 1.8u, ranked worst-first. Read-only; run any time after a
reconvert. Complements verify_bodymatch.py (which checks only the re-sourced set).
"""
import os
import sys
import glob
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                        # noqa: E402
from src import paths                          # noqa: E402
from src.body_zones import BREAST_Z               # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
GAP_MIN = float(os.environ.get("CBBE2UBE_OVERINFLATE_GAP", "1.2"))
SHELL_MAX = float(os.environ.get("CBBE2UBE_OVERINFLATE_SHELL", "1.8"))


def _measure(path):
    nf = pynifly.NifFile(filepath=str(path))
    S = {}
    for s in nf.shapes:
        tr = getattr(s, "transform", None)
        t = getattr(tr, "translation", None) if tr is not None else None
        off = (np.array([float(t[0]), float(t[1]), float(t[2])])
               if t is not None else np.zeros(3))
        S[s.name] = (np.asarray(s.verts, np.float64) + off,
                     np.asarray(s.normals, np.float64) if s.normals else None)
    if "BaseShape" not in S or S["BaseShape"][1] is None:
        return None
    bv, bn = S["BaseShape"]
    arm = [v for k, (v, nz) in S.items()
           if k != "BaseShape" and nz is not None
           and not k.lower().startswith("col") and "virtualground" not in k.lower()
           and len(v) > 8]
    if not arm:
        return None
    av = np.vstack(arm)
    t = cKDTree(av)
    reg = np.where((bv[:, 2] >= BREAST_Z[0]) & (bv[:, 2] < BREAST_Z[1])
                   & (np.abs(bv[:, 0]) < 12) & (bv[:, 1] > 0))[0]
    gaps, shells = [], []
    for i in reg:
        d, j = t.query(bv[i])
        if d >= 6.0:
            continue
        nb = t.query_ball_point(bv[i], 3.5)
        if not nb:
            continue
        proj = (av[nb] - bv[i]) @ bn[i]
        gaps.append(float(proj.min() if proj.min() > -0.5 else np.median(proj)))
        shells.append(float(proj.max() - proj.min()))
    if len(gaps) < 15:
        return None
    return float(np.mean(gaps)), float(np.mean(shells)), len(gaps)


def main():
    lay = paths.discover_layout()
    root = lay.mods_root / OUT_MOD / "meshes" / "!UBE"
    if not root.is_dir():
        print(f"output not found: {root}\n(run a reconvert first)")
        return 1
    files = [f for f in glob.glob(str(root / "**" / "*_1.nif"), recursive=True)
             if "1stperson" not in f.lower()
             and "/m/" not in f.replace("\\", "/").lower()]
    print(f"scanning {len(files)} converted meshes for thin-band over-inflation...",
          flush=True)
    rows = []
    for n, f in enumerate(files, 1):
        try:
            m = _measure(f)
        except Exception:
            m = None
        if m and m[0] > GAP_MIN and m[1] < SHELL_MAX:
            rel = f.replace("\\", "/").split("/!UBE/", 1)[1]
            rows.append((m[0], m[1], rel))
        if n % 300 == 0:
            print(f"  ...{n}/{len(files)}, {len(rows)} flagged", flush=True)
    rows.sort(reverse=True)
    print(f"\n=== THIN-BAND OVER-INFLATION: {len(rows)} flagged of {len(files)} ===")
    print(f"{'gap':>5} {'shell':>5}  armor")
    for gap, shell, rel in rows:
        print(f"{gap:5.2f} {shell:5.2f}  {rel}")
    print(f"\ngap = under-band air gap (u); shell = band thickness (u, <{SHELL_MAX} = "
          f"thin). High gap + thin shell = a band standing off the body. A bundled-body "
          f"mismatch is fixed at the SOURCE (see scripts/verify_bodymatch.py); other "
          f"causes are per-armor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
