"""Can we tell a BODY overlay from a HEAD overlay by CONTENT (no names)? That
only works if the body mesh's UV footprint and the head mesh's UV footprint
occupy DIFFERENT regions of 0-1. Rasterize each mesh's UV coverage into a grid
and measure overlap (IoU) + how much of the head's UV is body-free (where a head
overlay's alpha would be unambiguously 'head'). High overlap => content can't
distinguish; low overlap => it can."""
import sys
from pathlib import Path
import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

BODY = Path(r"D:\Modlists\ARR\mods\Authoria - Nevernude Female 3BA\meshes\Actors\Character\Character Assets\femalebody_1.nif")
HEAD = Path(r"D:\Modlists\ARR\mods\Expressive Facegen Morphs SE\meshes\actors\character\character assets\femalehead.nif")
N = 512


def uv_mask(path):
    """Rasterized UV coverage of every shape in the mesh (triangle fill)."""
    nif = pynifly.NifFile(str(path))
    m = np.zeros((N, N), bool)
    for s in nif.shapes:
        uv = np.asarray(s.uvs, np.float64)
        tris = np.asarray(s.tris, np.int64)
        px = np.clip((uv * (N - 1)).astype(int), 0, N - 1)
        # scanline-free cheap fill: mark each tri's bbox-sampled barycentric hits
        for a, b, c in tris:
            pa, pb, pc = px[a], px[b], px[c]
            x0, y0 = min(pa[0], pb[0], pc[0]), min(pa[1], pb[1], pc[1])
            x1, y1 = max(pa[0], pb[0], pc[0]), max(pa[1], pb[1], pc[1])
            if (x1 - x0) * (y1 - y0) > 4000:   # skip huge degenerate spans
                m[y0:y1 + 1, x0:x1 + 1] = True
                continue
            for yy in range(y0, y1 + 1):
                for xx in range(x0, x1 + 1):
                    # barycentric inside-test
                    d = ((pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1]))
                    if d == 0:
                        continue
                    l1 = ((pb[1] - pc[1]) * (xx - pc[0]) + (pc[0] - pb[0]) * (yy - pc[1])) / d
                    l2 = ((pc[1] - pa[1]) * (xx - pc[0]) + (pa[0] - pc[0]) * (yy - pc[1])) / d
                    if l1 >= -0.01 and l2 >= -0.01 and (l1 + l2) <= 1.01:
                        m[yy, xx] = True
    return m


def main():
    print("body:", BODY.is_file(), "head:", HEAD.is_file())
    b = uv_mask(BODY)
    h = uv_mask(HEAD)
    inter = (b & h).sum()
    union = (b | h).sum()
    print(f"body UV coverage: {100*b.mean():.1f}% of 0-1")
    print(f"head UV coverage: {100*h.mean():.1f}% of 0-1")
    print(f"IoU(body,head): {inter/union:.3f}  (high => can't distinguish by content)")
    print(f"head texels NOT covered by body: {100*((h & ~b).sum()/max(h.sum(),1)):.1f}% of head")
    print(f"body texels NOT covered by head: {100*((b & ~h).sum()/max(b.sum(),1)):.1f}% of body")


if __name__ == "__main__":
    main()
