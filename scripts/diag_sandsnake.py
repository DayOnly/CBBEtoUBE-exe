# Diag: Sand Snake vambraces (hands invisible) + shoulder straps (not
# conforming). Compare source (bodyslide output) vs converted NIF shapes.
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))

from pyn import pynifly  # noqa: E402

SRC = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA"
           r"\meshes\Sand Snake\armor")
OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE"
           r"\Sand Snake\armor")

PIECES = [
    "RB's Sand Snake vambraces",
    "RB's Sand Snake vambraces n2",
    "RB's Sand Snake shoulder straps",
    "RB's Sand Snake shoulder straps n2",
]


def describe(p):
    nf = pynifly.NifFile(filepath=str(p))
    rows = []
    for s in nf.shapes:
        v = np.asarray(s.verts, dtype=np.float64)
        bones = list(s.get_used_bones()) if hasattr(s, "get_used_bones") else []
        try:
            bones = [b for b in (s.bone_names or [])]
        except Exception:
            pass
        flags = None
        try:
            flags = hex(s.properties.flags)
        except Exception:
            pass
        # vert bbox to spot off-body flings
        bb = (v.min(axis=0).round(1).tolist(), v.max(axis=0).round(1).tolist()) if len(v) else None
        rows.append((s.name, len(v), len(bones), flags, bb))
    return rows


for piece in PIECES:
    for w in ("_1",):
        print(f"\n===== {piece}{w} =====")
        for label, base in (("SRC", SRC), ("OUT", OUT)):
            p = base / f"{piece}{w}.nif"
            if not p.is_file():
                print(f"  {label}: MISSING {p}")
                continue
            print(f"  {label}:")
            for name, nv, nb, flags, bb in describe(p):
                print(f"    {name[:36]:36s} verts={nv:6d} bones={nb:3d} "
                      f"flags={flags} bbox={bb}")
