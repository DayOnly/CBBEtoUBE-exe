"""Verify the iron cuirass LAYERS held after the body-swap fix: per visible
layer (Cuirass / Skirt / Belt / Bag) -- vert count preserved vs source, bbox
(no collapse-to-point, no fling-to-origin), and body-penetration (% verts buried
INSIDE the UBE BaseShape = bad layering). Plus the converter's own
validate_dst_nif (z-fight / skinning sanity)."""
import sys
from pathlib import Path
import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from src import nif_convert as nc  # noqa: E402

CONV = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron\f")
SRC = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA\meshes\armor\iron\f")
NON_LAYER = {"BaseShape", "Collision", "Belt Col", "Bag Col", "PBelt Col",
             "VirtualGround", "Colision"}


def vnormals(verts, tris):
    n = np.zeros_like(verts)
    v = verts
    for a, b, c in tris:
        fn = np.cross(v[b] - v[a], v[c] - v[a])
        n[a] += fn; n[b] += fn; n[c] += fn
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1
    return n / ln


def load(p):
    nif = pynifly.NifFile(str(p))
    return {s.name: s for s in nif.shapes}, nif


def main():
    for name in ("cuirasslight_1.nif", "cuirassheavy_1.nif"):
        cp = CONV / name
        sp = SRC / name
        print(f"\n========== {name} ==========")
        if not cp.is_file():
            print("  converted MISSING"); continue
        conv, _ = load(cp)
        src = load(sp)[0] if sp.is_file() else {}

        base = conv.get("BaseShape")
        bverts = np.asarray(base.verts, dtype=np.float64)
        try:
            btris = np.asarray(base.tris, dtype=np.int64)
            bn = vnormals(bverts, btris)
        except Exception:
            bn = None
        tree = cKDTree(bverts)

        print(f"  {'layer':14} {'verts(conv/src)':16} {'bbox size':18} "
              f"{'ctr-dist-from-body':18} {'%buried':8}")
        for nm, s in conv.items():
            if nm in NON_LAYER:
                continue
            v = np.asarray(s.verts, dtype=np.float64)
            sv = len(src[nm].verts) if nm in src else "?"
            size = v.max(0) - v.min(0)
            ctr = v.mean(0)
            bctr = bverts.mean(0)
            ctrd = float(np.linalg.norm(ctr - bctr))
            dist, idx = tree.query(v)
            buried = "n/a"
            if bn is not None:
                signed = np.einsum("ij,ij->i", v - bverts[idx], bn[idx])
                buried = f"{100.0*np.mean(signed < -0.5):.1f}%"
            flag = ""
            if float(size.max()) < 1.0:
                flag += " COLLAPSE?"
            if ctrd > 200:
                flag += " FLUNG?"
            print(f"  {nm:14} {str(len(v))+'/'+str(sv):16} "
                  f"[{size[0]:.0f},{size[1]:.0f},{size[2]:.0f}]".ljust(18)
                  + f" {ctrd:8.1f}".ljust(18) + f" {buried:8}{flag}")

        warns = nc.validate_dst_nif(cp, src_path=sp if sp.is_file() else None)
        print(f"  validate_dst_nif: {len(warns)} warning(s)")
        for w in warns[:8]:
            print(f"     - {w}")


if __name__ == "__main__":
    main()
