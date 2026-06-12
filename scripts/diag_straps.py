# Diag: Sand Snake shoulder straps "not conforming to body".
# Measures: (1) how far conversion moved the verts vs source, (2) signed
# clearance source-vs-CBBE-base and converted-vs-UBE-template per morph zone,
# (3) BODYTRI extra-data presence, (4) TRI morph coverage for the straps,
# (5) rigid classification (dominant-bone fraction).
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))
os.environ.setdefault("CBBE2UBE_MODS_ROOT", r"D:\Modlists\ARR\mods")

from pyn import pynifly  # noqa: E402
from src import nif_convert as nc  # noqa: E402
from src.tri import TriFile  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

UBE_DIR = Path(r"D:\Modlists\ARR\mods\UBE 2.0 U. 0.7\CalienteTools"
               r"\Bodyslide\ShapeData\UBE SE 2.0 Release Body")
TEMPLATE = UBE_DIR / "UBE SE 2.0 Release Body.nif"
OSD = UBE_DIR / "UBE SE 2.0 Release Body.osd"
SRC = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA"
           r"\meshes\Sand Snake\armor\RB's Sand Snake shoulder straps_1.nif")
OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE"
           r"\Sand Snake\armor\RB's Sand Snake shoulder straps_1.nif")
TRI = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE"
           r"\Sand Snake\armor\RB's Sand Snake shoulder straps.tri")


def body(nif_path):
    nf = pynifly.NifFile(filepath=str(nif_path))
    s = max(nf.shapes, key=lambda x: len(x.verts))
    v = np.asarray(s.verts, dtype=np.float64)
    t = np.asarray(s.tris, dtype=np.int64)
    return v, nc._vertex_normals_from_tris(v, t)


ube_v, ube_n = body(TEMPLATE)
amp = nc._cached_body_morph_amplitude(OSD, ube_n, len(ube_v))
ube_tree = cKDTree(ube_v)

cbbe_path = nc._find_cbbe_base_body("_1")
print(f"CBBE base body: {cbbe_path}")
cbbe_v, cbbe_n = body(cbbe_path)
cbbe_tree = cKDTree(cbbe_v)
# map each CBBE body vert to UBE amp (zones must align across bodies)
_, c2u = ube_tree.query(cbbe_v, k=1)
cbbe_amp = amp[c2u]


def signed_clear(av, tree, bv, bn):
    d, i = tree.query(av, k=1)
    sign = np.sign(((av - bv[i]) * bn[i]).sum(axis=1))
    return d * np.where(sign == 0, 1, sign), i


for label, path, btree, bv, bn, bamp in (
        ("SRC vs CBBE", SRC, cbbe_tree, cbbe_v, cbbe_n, cbbe_amp),
        ("OUT vs UBE ", OUT, ube_tree, ube_v, ube_n, amp)):
    nf = pynifly.NifFile(filepath=str(path))
    for s in nf.shapes:
        av = np.asarray(s.verts, dtype=np.float64)
        g2s = nc._shape_global_to_skin(s)
        av = nc._verts_skin_to_world(av, g2s)
        sc, i = signed_clear(av, btree, bv, bn)
        za = bamp[i]
        zones = (("static", za < 0.3), ("mid", (za >= 0.3) & (za <= 2.25)),
                 ("breast", za > 2.25))
        line = f"{label} {s.name[:24]:24s}"
        for zl, m in zones:
            if m.sum() < 15:
                line += f" | {zl}: n<15"
            else:
                line += (f" | {zl}: n={m.sum():4d} p50={np.percentile(sc[m],50):+.2f}"
                         f" p95={np.percentile(sc[m],95):+.2f} pen={(sc[m]<0).sum()}")
        print(line)
        # rigid classification + dominant bone
        bw = getattr(s, "bone_weights", None) or {}
        tot = {b: sum(w for _, w in prs) for b, prs in bw.items()}
        ttl = sum(tot.values()) or 1.0
        top = sorted(tot.items(), key=lambda kv: -kv[1])[:4]
        print(f"    bones: " + ", ".join(f"{b}={w/ttl:.2f}" for b, w in top)
              + f"  rigid={nc._is_rigid_attachment(bw)}")

# displacement OUT vs SRC (same topology)
sn = pynifly.NifFile(filepath=str(SRC))
on = pynifly.NifFile(filepath=str(OUT))
sv = {s.name: np.asarray(s.verts, dtype=np.float64) for s in sn.shapes}
ov = {s.name: np.asarray(s.verts, dtype=np.float64) for s in on.shapes}
for name in sv:
    if name in ov and len(sv[name]) == len(ov[name]):
        d = np.linalg.norm(ov[name] - sv[name], axis=1)
        print(f"\ndisplacement OUT-SRC {name!r}: mean={d.mean():.2f} "
              f"p50={np.percentile(d,50):.2f} p95={np.percentile(d,95):.2f} "
              f"max={d.max():.2f}")

# BODYTRI extra data
def dump_extra(nf, label):
    found = []
    try:
        for k, v in (nf.string_data or []):
            found.append((k, v))
    except Exception:
        pass
    for s in nf.shapes:
        try:
            for k, v in (s.string_data or []):
                found.append((f"{s.name}:{k}", v))
        except Exception:
            pass
    print(f"{label} string extra-data: {found if found else 'NONE'}")


dump_extra(on, "\nOUT")

# TRI morph coverage
tri = TriFile.load(TRI)
print(f"\nTRI shapes: {[t.name for t in tri.shapes]}")
for t in tri.shapes:
    breast = [m for m in t.morphs if "breast" in m.name.lower()]
    mags = []
    for m in breast:
        a = np.asarray(m.offsets, dtype=np.float64)
        if len(a):
            mags.append((m.name, float(np.linalg.norm(a[:, 1:4], axis=1).max()),
                         len(a)))
    mags.sort(key=lambda x: -x[1])
    print(f"  shape {t.name!r}: {len(t.morphs)} morphs, "
          f"breast morphs={len(breast)}; top: "
          + ", ".join(f"{n}={v:.2f}u(n={c})" for n, v, c in mags[:4]))
