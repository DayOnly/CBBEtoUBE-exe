"""Sanity-check the production overlay correspondences (body vs hands). The
correspondence warps CBBE verts to UBE space by nearest-neighbor; if the two
region meshes don't align in 3D, NN distance blows up and the remap is garbage
-> misaligned overlays. Reports resolved meshes, picked shape, NN-distance
stats, and mean UV displacement, per region."""
import sys
from pathlib import Path
import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from src import overlay_transfer as ot  # noqa: E402


def shape_of(path, prefer):
    nf = pynifly.NifFile(str(path))
    s = next((x for x in nf.shapes if x.name in prefer), None) \
        or max(nf.shapes, key=lambda x: len(x.verts))
    return s.name, np.asarray(s.verts, np.float64), np.asarray(s.uvs, np.float64)


M = Path(r"D:\Modlists\ARR\mods")
PATHS = {
    "body":  (M / r"Authoria - Nevernude Female 3BA\meshes\Actors\Character\Character Assets\femalebody_1.nif",
              M / r"Authoria - Bodyslide Output - 3BA\meshes\!UBE\Body\femalebody_tangent_1.nif"),
    "hands": (M / r"CBBE 3BA (3BBB)\meshes\actors\character\character assets\femalehands_1.nif",
              M / r"Authoria - Bodyslide Output - 3BA\meshes\!UBE\Hands\femalehands_tangent_1.nif"),
    "feet":  (M / r"CBBE 3BA (3BBB)\meshes\actors\character\character assets\femalefeet_1.nif",
              M / r"Authoria - Bodyslide Output - 3BA\meshes\!UBE\Feet\femalefeet_tangent_1.nif"),
}


def main():
    for region in ("body", "hands", "feet"):
        print(f"\n===== region: {region} =====")
        cbbe, ube = PATHS[region]
        print(f"  CBBE mesh: {cbbe}")
        print(f"  UBE  mesh: {ube}")
        if not cbbe or not ube or not Path(cbbe).is_file() or not Path(ube).is_file():
            print("  -> one or both meshes MISSING (correspondence cannot build)")
            continue
        prefer = ("BaseShape", "3BA") if region == "body" else ("BaseShape",)
        cn, cbv, cbuv = shape_of(cbbe, prefer)
        un, ubv, ubuv = shape_of(ube, prefer)
        print(f"  CBBE shape={cn!r} verts={len(cbv)}  bbox-size={np.round(cbv.max(0)-cbv.min(0),1)} ctr={np.round(cbv.mean(0),1)}")
        print(f"  UBE  shape={un!r} verts={len(ubv)}  bbox-size={np.round(ubv.max(0)-ubv.min(0),1)} ctr={np.round(ubv.mean(0),1)}")
        d, nn = cKDTree(ubv).query(cbv, k=1)
        print(f"  NN dist (CBBE->UBE): mean={d.mean():.2f} median={np.median(d):.2f} p95={np.percentile(d,95):.2f} max={d.max():.2f}")
        print(f"     (body baseline ~0.5; >>2 means the meshes don't align -> bad correspondence)")
        duv = np.linalg.norm(cbuv - ubuv[nn], axis=1)
        print(f"  UV displacement (CBBE-uv vs NN UBE-uv): mean={duv.mean():.3f} p95={np.percentile(duv,95):.3f}")


if __name__ == "__main__":
    main()
