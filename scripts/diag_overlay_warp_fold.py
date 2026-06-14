"""Measure folding/degeneracy in the overlay correspondence's NN-snap warp
(cbbe_in_ube = ubv[NN(cbv)] over CBBE topology). A folded/collapsed triangle
makes project_to_mesh pick the wrong CBBE UV -> the sampled overlay smears
('garbled' thin-line patterns). Reports % degenerate + % normal-flipped tris,
and where (Z band) they concentrate."""
import sys
from pathlib import Path
import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

M = Path(r"D:\Modlists\ARR\mods")
CASES = {
    "body": (M / r"Authoria - Nevernude Female 3BA\meshes\Actors\Character\Character Assets\femalebody_1.nif", ("BaseShape", "3BA")),
    "hands": (M / r"CBBE 3BA (3BBB)\meshes\actors\character\character assets\femalehands_1.nif", ("BaseShape",)),
}
UBE = {
    "body": M / r"Authoria - Bodyslide Output - 3BA\meshes\!UBE\Body\femalebody_tangent_1.nif",
    "hands": M / r"Authoria - Bodyslide Output - 3BA\meshes\!UBE\Hands\femalehands_tangent_1.nif",
}


def load(path, prefer):
    nf = pynifly.NifFile(str(path))
    s = next((x for x in nf.shapes if x.name in prefer), None) or max(nf.shapes, key=lambda x: len(x.verts))
    return np.asarray(s.verts, np.float64), np.asarray(s.tris, np.int64)


def tri_normals_areas(v, t):
    a = v[t[:, 0]]; b = v[t[:, 1]]; c = v[t[:, 2]]
    n = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(n, axis=1)
    ln = np.linalg.norm(n, axis=1, keepdims=True); ln[ln == 0] = 1
    return n / ln, area


def degen_pct(cbv, cbt, warped):
    n0, _ = tri_normals_areas(cbv, cbt)
    n1, a1 = tri_normals_areas(warped, cbt)
    med0 = np.median(tri_normals_areas(cbv, cbt)[1])
    degen = a1 < 0.05 * med0
    flipped = np.einsum("ij,ij->i", n0, n1) < 0.0
    return 100 * (degen | flipped).mean(), 100 * degen.mean(), 100 * flipped.mean()


def main():
    from src.correspondence import MeshIndex, project_to_mesh
    for region, (cbbe_path, prefer) in CASES.items():
        cbv, cbt = load(cbbe_path, prefer)
        ubv, ubt = load(UBE[region], ("BaseShape",))
        print(f"\n===== {region} ({len(cbt)} tris) =====")
        # OLD: nearest-VERTEX snap
        _, nn = cKDTree(ubv).query(cbv, k=1)
        b, d, f = degen_pct(cbv, cbt, ubv[nn])
        uniq = len(set(nn.tolist()))
        print(f"  NN-SNAP (current):    bad={b:.2f}%  (degen {d:.2f}% + folded {f:.2f}%)"
              f"  collapse={100*(1-uniq/len(nn)):.1f}%")
        # NEW a: surface projection (continuous)
        proj, _, _ = project_to_mesh(cbv, MeshIndex.build(ubv, ubt))
        b2, d2, f2 = degen_pct(cbv, cbt, proj)
        print(f"  SURFACE-PROJ:         bad={b2:.2f}%  (degen {d2:.2f}% + folded {f2:.2f}%)")
        # NEW b: smooth k-NN distance-weighted average
        dk, nk = cKDTree(ubv).query(cbv, k=6)
        w = 1.0 / (dk ** 2 + 1e-8); w /= w.sum(1, keepdims=True)
        knn = (ubv[nk] * w[..., None]).sum(1)
        b3, d3, f3 = degen_pct(cbv, cbt, knn)
        print(f"  kNN-AVG(k6):          bad={b3:.2f}%  (degen {d3:.2f}% + folded {f3:.2f}%)")


if __name__ == "__main__":
    main()
