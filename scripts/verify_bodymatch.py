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
    reg = np.where((bv[:, 2] >= 100) & (bv[:, 2] <= 108)
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
    # penetration: armor torso verts sitting inside the body (signed dist < 0)
    bt = cKDTree(bv)
    tm = (av[:, 2] >= 70) & (av[:, 2] <= 115)
    worst_pen = 0.0
    if tm.any():
        d, j = bt.query(av[tm])
        sd = ((av[tm] - bv[j]) * bn[j]).sum(1)
        near = sd[d < 8.0]
        if near.size:
            worst_pen = float(min(0.0, near.min()))
    return float(np.mean(gaps)), worst_pen


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
            rows.append((m[0], m[1], rel))
    rows.sort(reverse=True)
    print(f"{'standoff':>8} {'pen':>6}  armor")
    for g, pen, rel in rows:
        flags = []
        if g > 1.5:
            flags.append("gap")
        if pen < -1.0:
            flags.append("cut-in")
        tag = ("  <-- " + ",".join(flags)) if flags else ""
        print(f"{g:8.2f} {pen:6.2f}  {rel}{tag}")
    hi_gap = [r for g, p, r in rows if g > 1.5]
    hi_pen = [r for g, p, r in rows if p < -1.0]
    print(f"\n{len(rows)} torso pieces measured. standoff = breast gap "
          f"(pre-fix Fur Cuirass +2.12u); pen = deepest armor-into-body over the torso.")
    print(f"{len(hi_gap)} still gap > 1.5u, {len(hi_pen)} cut in > 1.0u. Clean pieces "
          f"sit near the body without cutting -- flagged ones want a closer look.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
