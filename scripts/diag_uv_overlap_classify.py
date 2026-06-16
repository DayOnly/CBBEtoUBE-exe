"""Test whether the UV-overlap EXCLUSIVE regions can classify an overlay as
body vs head. Build the CBBE body-UV mask + head-UV mask, take head-exclusive
(head & ~body) and body-exclusive (body & ~head), then for each test overlay
measure what fraction of its design (alpha) lands in each region. Hypothesis: a
BODY overlay has ~0 alpha in head-exclusive texels; a FACE overlay has notable
alpha there. If it separates the known body vs known face overlays, we have a
name-free detector."""
import sys
import tempfile
from pathlib import Path
import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly                       # noqa: E402
from src import overlay_transfer as ot        # noqa: E402

BODY = Path(r"D:\Modlists\ARR\mods\Authoria - Nevernude Female 3BA\meshes\Actors\Character\Character Assets\femalebody_1.nif")
HEAD = Path(r"D:\Modlists\ARR\mods\Expressive Facegen Morphs SE\meshes\actors\character\character assets\femalehead.nif")
TEXCONV = r"D:\Modlists\ARR\tools\xEdit\Edit Scripts\Texconvx64.exe"
N = 256
TESTS = [
    ("BODY  WNB/Arcolis", r"D:\Modlists\ARR\mods\Weathered Nordic Bodypaints SE\textures\actors\character\Overlays\WNB\Arcolis\Arcolis 1.dds"),
    ("FACE  FMS/Blush",   r"D:\Modlists\ARR\mods\Female Makeup Suite - Face\textures\actors\character\Overlays\FMS\Blush\Anime Blush 1.dds"),
    ("FACE  Miggyluv",    r"D:\Modlists\ARR\mods\Miggyluv's Facepaint for Men\Textures\Actors\Character\Overlays\Miggyluv\Mig_Face_DarkKnight_Eyes.dds"),
]


def uv_mask(path):
    nif = pynifly.NifFile(str(path))
    m = np.zeros((N, N), bool)
    for s in nif.shapes:
        uv = np.asarray(s.uvs, np.float64)
        tris = np.asarray(s.tris, np.int64)
        px = np.clip((uv * (N - 1)).astype(int), 0, N - 1)
        for a, b, c in tris:
            pa, pb, pc = px[a], px[b], px[c]
            x0, y0 = min(pa[0], pb[0], pc[0]), min(pa[1], pb[1], pc[1])
            x1, y1 = max(pa[0], pb[0], pc[0]), max(pa[1], pb[1], pc[1])
            if (x1 - x0) > N // 3 or (y1 - y0) > N // 3:
                continue                       # skip seam-spanning degenerate tris
            d = ((pb[1]-pc[1])*(pa[0]-pc[0]) + (pc[0]-pb[0])*(pa[1]-pc[1]))
            if d == 0:
                continue
            for yy in range(y0, y1 + 1):
                for xx in range(x0, x1 + 1):
                    l1 = ((pb[1]-pc[1])*(xx-pc[0]) + (pc[0]-pb[0])*(yy-pc[1]))/d
                    l2 = ((pc[1]-pa[1])*(xx-pc[0]) + (pa[0]-pc[0])*(yy-pc[1]))/d
                    if l1 >= -0.02 and l2 >= -0.02 and (l1 + l2) <= 1.02:
                        m[yy, xx] = True
    return m


def main():
    work = Path(tempfile.mkdtemp())
    bmask = uv_mask(BODY); hmask = uv_mask(HEAD)
    head_excl = hmask & ~bmask
    body_excl = bmask & ~hmask
    print(f"head-exclusive texels: {head_excl.sum()}  body-exclusive: {body_excl.sum()}\n")
    for label, dds in TESTS:
        if not Path(dds).is_file():
            print(f"{label}: MISSING {dds}"); continue
        rgba = ot.dds_to_rgba(dds, TEXCONV, work)
        H, W = rgba.shape[:2]
        a = rgba[..., 3] > 16
        ys, xs = np.nonzero(a)
        if len(ys) == 0:
            print(f"{label}: no alpha"); continue
        # try both V orientations; report the one with more total mask hits
        for flip, vname in ((False, "v"), (True, "1-v")):
            vv = (H - 1 - ys) if flip else ys
            mu = np.clip((xs / W * (N - 1)).astype(int), 0, N - 1)
            mv = np.clip((vv / H * (N - 1)).astype(int), 0, N - 1)
            he = head_excl[mv, mu].mean()
            be = body_excl[mv, mu].mean()
            sh = (bmask & hmask)[mv, mu].mean()
            print(f"{label:20} [{vname:3}] alpha%: head-excl={100*he:5.1f}  body-excl={100*be:5.1f}  shared={100*sh:5.1f}")
        print()


if __name__ == "__main__":
    main()
