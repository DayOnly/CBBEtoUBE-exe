# LIVE hot-fix: Sand Snake shoulder straps don't conform to body because the
# converter skipped BODYTRI injection (the only shape, "shoulder straps", was
# disqualified as a morph carrier by the "shoulder" rigid-prop keyword). The
# .tri files were generated fine; they just never load at runtime without a
# BODYTRI extra-data reference on a shape. Mirror exactly what the (now-fixed)
# converter does: reset morph flags + add BODYTRI on the carrier shape, then
# collapse partitions on disk. Idempotent (skips a NIF that already has BODYTRI).
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, r"C:\Users\Sam\Downloads\cbbe-to-ube\.pynifly")

from pyn import pynifly
from pyn.pynifly import NiStringExtraData
from src import nif_convert as nc

OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\Sand Snake\armor")
PIECES = [
    "RB's Sand Snake shoulder straps",
    "RB's Sand Snake shoulder straps n2",
    "RB's Sand Snake shoulder straps n3",
]
CARRIER_SHAPE = "shoulder straps"


def has_bodytri(shape):
    for ed in shape.extra_data():
        if getattr(ed, "name", "") == "BODYTRI":
            return True
    return False


for piece in PIECES:
    tri_rel = f"!UBE\\Sand Snake\\armor\\{piece}.tri"
    for w in ("_0", "_1"):
        p = OUT / f"{piece}{w}.nif"
        if not p.is_file():
            print(f"MISSING {p}")
            continue
        nf = pynifly.NifFile(filepath=str(p))
        carrier = next((s for s in nf.shapes if s.name == CARRIER_SHAPE), None)
        if carrier is None:
            # fall back to the largest textured shape
            cand = [s for s in nf.shapes if (s.textures or {})]
            carrier = max(cand, key=lambda s: len(s.verts)) if cand else None
        if carrier is None:
            print(f"NO CARRIER in {p.name}")
            continue
        if has_bodytri(carrier):
            print(f"skip (already has BODYTRI): {p.name}")
            continue
        nc._reset_morph_flags(carrier)
        NiStringExtraData.New(
            nf, name="BODYTRI", string_value=tri_rel, parent=carrier)
        nf.filepath = str(p)
        nf.save()
        collapsed = nc._normalize_partitions_on_disk(p)
        print(f"FIXED {p.name}: BODYTRI -> {tri_rel}  (carrier={carrier.name!r}, "
              f"partitions collapsed={collapsed})")

# verify
print("\n--- verify ---")
for piece in PIECES:
    for w in ("_0", "_1"):
        p = OUT / f"{piece}{w}.nif"
        nf = pynifly.NifFile(filepath=str(p))
        got = None
        for s in nf.shapes:
            for ed in s.extra_data():
                if getattr(ed, "name", "") == "BODYTRI":
                    got = (s.name, ed.string_data if hasattr(ed, "string_data")
                           else None)
        print(f"  {p.name}: {got}")
