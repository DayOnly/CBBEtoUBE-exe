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

"""Post-reconvert check for the body-match source-selection fix (#body-match-source).

Run AFTER a full reconvert. It (1) recomputes which meshes the body-match rule
re-sourced (build_mesh_index with the flag on vs off), then (2) measures each
re-sourced mesh's converted !UBE output for the breast-band STANDOFF that the fix
targets. A fixed piece should now sit close to the body (covered-mean well under the
pre-fix gap); anything still high is flagged for a look.

    python scripts/verify_bodymatch.py

Reads the live MO2 instance via CBBE2UBE_MO2_INI (or the auto-discovered layout) and
the converter's output mod. Read-only; safe to run any time after the reconvert.
"""
import os
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                       # noqa: E402
from src import paths, discovery              # noqa: E402
from src.body_zones import BREAST_Z               # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")


def _output_root(mods_root: Path) -> Path:
    return mods_root / OUT_MOD / "meshes" / "!UBE"


def _load_render(path: Path):
    """name -> (render-space verts, normals). Applies each shape's transform
    translation so a shifted-space body/band is measured where it actually renders."""
    nf = pynifly.NifFile(filepath=str(path))
    out = {}
    for s in nf.shapes:
        tr = getattr(s, "transform", None)
        t = getattr(tr, "translation", None) if tr is not None else None
        off = (np.array([float(t[0]), float(t[1]), float(t[2])])
               if t is not None else np.zeros(3))
        verts = np.asarray(s.verts, dtype=np.float64) + off
        norms = np.asarray(s.normals, dtype=np.float64) if s.normals else None
        out[s.name] = (verts, norms)
    return out


def _fit_metrics(shapes):
    """(breast_standoff, worst_torso_penetration) for a body-swap torso piece, or
    None if it isn't one. standoff = covered-mean gap at the breast band (armor sitting
    OFF the body -- the over-inflation the fix targets). penetration = deepest armor
    vertex INSIDE the body over the torso (armor cutting IN -- the opposite failure).
    Both are what a good fit minimises."""
    if "BaseShape" not in shapes or shapes["BaseShape"][1] is None:
        return None
    bv, bn = shapes["BaseShape"]
    arm = [v for k, (v, nz) in shapes.items()
           if k != "BaseShape" and nz is not None
           and not k.lower().startswith("col") and "virtualground" not in k.lower()]
    if not arm:
        return None
    av = np.vstack(arm)
    at = cKDTree(av)
    reg = np.where((bv[:, 2] >= BREAST_Z[0]) & (bv[:, 2] < BREAST_Z[1])
                   & (np.abs(bv[:, 0]) < 12) & (bv[:, 1] > 0))[0]
    if len(reg) < 15:
        return None
    gaps = []
    for i in reg:
        d, j = at.query(bv[i])
        if d < 6.0:
            gaps.append(float((av[j] - bv[i]) @ bn[i]))
    if len(gaps) < 10:
        return None
    # penetration: armor torso verts sitting inside the body. Report the COUNT of
    # verts cutting in > 0.5u (the cut-in AREA), not just the single deepest vertex --
    # a lone stray vert reads as a deep poke but isn't a visible clip. (worst kept too.)
    bt = cKDTree(bv)
    tm = (av[:, 2] >= 70) & (av[:, 2] <= 115)
    worst_pen = 0.0
    n_cut = 0
    if tm.any():
        d, j = bt.query(av[tm])
        sd = ((av[tm] - bv[j]) * bn[j]).sum(1)
        near = sd[d < 8.0]
        if near.size:
            worst_pen = float(min(0.0, near.min()))
            n_cut = int(np.sum(near < -0.5))
    return float(np.mean(gaps)), worst_pen, n_cut


def main():
    lay = paths.discover_layout()
    enabled = paths.enabled_mods_ordered(lay)
    out_root = _output_root(lay.mods_root)
    if not out_root.is_dir():
        print(f"output not found: {out_root}\n(run a reconvert first)")
        return 1

    rels = {p.as_posix().split("/!UBE/", 1)[1].lower()
            for p in out_root.rglob("*_1.nif")
            if "1stperson" not in p.as_posix().lower()}
    print(f"scanning {len(rels)} converted meshes; resolving re-sourced set...", flush=True)

    os.environ["CBBE2UBE_NO_BODYMATCH_SELECT"] = "1"
    base = discovery.build_mesh_index(lay.mods_root, enabled,
                                      target_keys=set(rels), skip_mods=(OUT_MOD,))
    os.environ.pop("CBBE2UBE_NO_BODYMATCH_SELECT", None)
    new = discovery.build_mesh_index(lay.mods_root, enabled,
                                     target_keys=set(rels), skip_mods=(OUT_MOD,))
    swapped = sorted(r for r in base if r in new and base[r] != new[r])
    print(f"body-match re-sourced {len(swapped)} meshes; measuring breast standoff on output...\n")

    rows = []
    for rel in swapped:
        outp = out_root / rel
        if not outp.is_file():
            continue
        try:
            m = _fit_metrics(_load_render(outp))
        except Exception:
            m = None
        if m is not None:
            rows.append((m[0], m[1], m[2], rel))
    rows.sort(reverse=True)
    print(f"{'standoff':>8} {'worst':>6} {'ncut':>5}  armor")
    for g, pen, ncut, rel in rows:
        flags = []
        if g > 1.5:
            flags.append("gap")
        if ncut >= 8:                       # AREA, not a lone deep vert
            flags.append(f"cut-in({ncut}v)")
        tag = ("  <-- " + ",".join(flags)) if flags else ""
        print(f"{g:8.2f} {pen:6.2f} {ncut:5d}  {rel}{tag}")
    hi_gap = [r for g, p, n, r in rows if g > 1.5]
    hi_pen = [r for g, p, n, r in rows if n >= 8]
    print(f"\n{len(rows)} torso pieces measured. standoff = breast gap "
          f"(pre-fix worst case +2.12u); worst/ncut = deepest vert / how many verts cut "
          f"in > 0.5u (the AREA -- a lone deep vert isn't a visible clip).")
    print(f"{len(hi_gap)} still gap > 1.5u, {len(hi_pen)} cut in over an area (>=8 verts). "
          f"Flagged ones want a closer look; the rest sit clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
