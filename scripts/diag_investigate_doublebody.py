"""Per-case investigation of the swept double-body NIFs: fresh-or-stale (mtime),
is the suspect shape really a body (texture/bones/Z-span), is it Hidden
(re-imported by _finalize), and HOW the XML references it (per-vertex/triangle
shape = collider/missing path; or via bones = framework path; or not at all)."""
import re
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes")
CASES = [
    (r"!UBE\clothes\farmclothes03\farmerrobef_1.nif", "3BA"),
    (r"!UBE\clothes\towels\towelbody_1.nif", "body"),
    (r"!UBE\armor\silverandleatherarmorbyxtudo\silverleatherarmortorso_1.nif", "Torso:0"),
    (r"!UBE\armor\[ELLE] Sparrows Mage\Top_1.nif", "SmallBook"),
    (r"!UBE\BDO Reborn Angelic Chorus\F\AC_Main_1.nif", "3BA"),
]


def main():
    for rel, suspect in CASES:
        p = OUT / rel
        print(f"\n===== {rel}  (suspect '{suspect}') =====")
        if not p.is_file():
            print("  MISSING"); continue
        age = (time.time() - p.stat().st_mtime) / 60.0
        print(f"  mtime {age:.0f} min ago  ({'FRESH' if age < 90 else 'STALE?'})")
        nif = pynifly.NifFile(str(p))
        shapes = {s.name: s for s in nif.shapes}
        for nm in ("BaseShape", suspect):
            s = shapes.get(nm)
            if s is None:
                print(f"  shape {nm!r}: ABSENT"); continue
            v = len(s.verts)
            fl = int(getattr(s, "flags", 0) or 0)
            tex = [k for k, x in (s.textures or {}).items() if x]
            bones = list(s.bone_names or [])
            z = np.asarray(s.verts, dtype=np.float64)[:, 2]
            print(f"  shape {nm!r}: verts={v} hidden={bool(fl & 1)} "
                  f"tex={tex or 'NONE'} bones={len(bones)} "
                  f"Zspan={float(z.max()-z.min()):.1f}")
        # XML
        stem = p.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-2]; break
        xmlp = p.with_name(stem + ".xml")
        if xmlp.is_file():
            txt = xmlp.read_text(errors="ignore")
            pv = re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt)
            pt = re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt)
            print(f"  XML per-vertex={pv}")
            print(f"  XML per-triangle={pt}")
            print(f"  suspect in XML shapes? {suspect in (pv+pt)}")
        else:
            print(f"  XML {xmlp.name}: MISSING (no _finalize XML?)")


if __name__ == "__main__":
    main()
