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

"""Triage tool for "armor clips in morph areas": find converted body armors that COVER
a morph zone (breast/butt/belly) but lack the scale-bone weight to FOLLOW it -- so when
the body is morphed (slider / _0<->_1 / physics), the body pushes through the armor.

Per converted body-swap torso piece, for each morph zone it covers:
  cover% = fraction of body-zone verts with armor within 4u
  follow = mean scale-bone weight (Breast/Butt/Belly) on that covering armor
Flags a zone with cover > 40% AND follow < 0.15 (covers it, but won't move with it),
worst-first. Read-only; run after a reconvert. Complements find_overinflation.py
(armor OFF body) and verify_bodymatch.py (re-sourced set).

WEIGHTS: both weights, via `standoff_audit.output_nifs`. The morph named in the
first paragraph IS the weight slider -- `_0<->_1` -- so a `_1`-only scan was
measuring the follow-through of one silhouette and reporting it as the pack's.
Cover and follow are computed per SHAPE against the body injected into that same
NIF, so a `_0` file carries its own weight-0 body and its own separately
authored garment: a self-consistent, independent observation, not a duplicate of
its `_1` partner. The `/m/` male-path exclusion is the female-only policy and is
KEPT; only the weight scoping changed.

    python scripts/analysis/find_morph_follow_gaps.py
"""
import os
import sys
import numpy as np
from pathlib import Path
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                        # noqa: E402
from src import paths                          # noqa: E402
from src.body_zones import BREAST_Z, BELLY_Z, BUTT_Z   # noqa: E402
from scripts.analysis import standoff_audit as sa      # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
# morph zone: (z-lo, z-hi, front/back sign for the body normal, jiggle-bone keyword)
ZONES = [("breast", BREAST_Z[0], BREAST_Z[1], +1, "breast"),
         ("belly", BELLY_Z[0], BELLY_Z[1], +1, "belly"),
         ("butt", BUTT_Z[0], BUTT_Z[1], -1, "butt")]


def _load(path):
    n = pynifly.NifFile(filepath=str(path))
    out = {}
    for s in n.shapes:
        tr = getattr(s, "transform", None)
        t = getattr(tr, "translation", None) if tr is not None else None
        off = (np.array([float(t[0]), float(t[1]), float(t[2])])
               if t is not None else np.zeros(3))
        verts = np.asarray(s.verts, np.float64) + off
        norms = np.asarray(s.normals, np.float64) if s.normals else None
        # per-vertex scale-bone weight, split by keyword
        n_v = len(verts)
        wt = {"breast": np.zeros(n_v), "belly": np.zeros(n_v), "butt": np.zeros(n_v)}
        for b, pairs in (s.bone_weights or {}).items():
            lb = b.lower()
            key = ("breast" if "breast" in lb else "belly" if "belly" in lb
                   else "butt" if "butt" in lb else None)
            if key is None:
                continue
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < n_v:
                    wt[key][iv] += float(w)
        out[s.name] = (verts, norms, wt)
    return out


def _analyse(path):
    S = _load(path)
    if "BaseShape" not in S or S["BaseShape"][1] is None:
        return None
    bv, bn, _ = S["BaseShape"]
    arm = [(v, w) for k, (v, nz, w) in S.items()
           if k != "BaseShape" and nz is not None
           and not k.lower().startswith("col") and "virtualground" not in k.lower()]
    if not arm:
        return None
    av = np.vstack([v for v, _ in arm])
    aw = {key: np.concatenate([w[key] for _, w in arm]) for key in ("breast", "belly", "butt")}
    t = cKDTree(av)
    rows = []
    for name, zlo, zhi, sign, key in ZONES:
        reg = np.where((bv[:, 2] >= zlo) & (bv[:, 2] <= zhi)
                       & (np.abs(bv[:, 0]) < 14) & (np.sign(bv[:, 1]) == sign))[0]
        if len(reg) < 20:
            continue
        d, j = t.query(bv[reg])
        covered = d < 4.0
        cov = float(covered.mean())
        if cov < 0.40:
            continue
        follow = float(np.mean(aw[key][j[covered]])) if covered.any() else 0.0
        if follow < 0.15:
            rows.append((cov, follow, name))
    return rows


def main():
    lay = paths.discover_layout()
    root = lay.mods_root / OUT_MOD / "meshes" / "!UBE" if lay.mods_root else None
    if root is None or not root.is_dir():
        print("output not found (set CBBE2UBE_MO2_INI + reconvert first).")
        return 1
    # BOTH weights -- see WEIGHTS: above. `output_nifs` owns the first-person
    # rule (regex `1stperson|1stp`, wider than the bare `1stperson` test that
    # was here); the `/m/` male-path exclusion is kept as-is.
    files = [str(p) for p in sa.output_nifs(root)
             if "/m/" not in p.as_posix().lower()]
    print(f"scanning {len(files)} meshes for morph-follow gaps...", flush=True)
    hits = []
    for i, f in enumerate(files, 1):
        try:
            r = _analyse(f)
        except Exception:
            r = None
        if r:
            rel = f.replace("\\", "/").split("/!UBE/", 1)[1]
            for cov, follow, zone in r:
                hits.append((cov * (0.15 - follow), cov, follow, zone, rel))
        if i % 300 == 0:
            print(f"  ...{i}/{len(files)}, {len(hits)} flags", flush=True)
    hits.sort(reverse=True)
    print(f"\n=== MORPH-FOLLOW GAPS: {len(hits)} zone-flags ===")
    print(f"{'cover%':>6} {'follow':>6}  zone    armor")
    for _, cov, follow, zone, rel in hits[:60]:
        print(f"{cov*100:6.0f} {follow:6.2f}  {zone:6s}  {rel}")
    print("\ncover% = body morph-zone covered by armor; follow = scale-bone weight on that "
          "cover (<0.15 = won't move with the morph -> body pokes through when morphed). "
          "NOTE: multi-layer cloth kept on source skin (layered-cloth CTD fix) shows low "
          "follow BY DESIGN -- that's the crash-vs-follow trade-off, not a new bug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
