"""Safety check before dropping '3BA' in the fresh leftover-body cases: is the
'3BA' a HIDDEN re-imported body (safe to drop -> BaseShape covers it) or VISIBLE
garment geometry (dropping it = invisible clothing)? List EVERY shape per NIF
with verts/hidden/tex/Zspan, and whether non-body visible clothing remains."""
import sys
from pathlib import Path
import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes")
CASES = [
    r"!UBE\clothes\farmclothes01\torsof_1.nif",
    r"!UBE\clothes\farmclothes03\farmerrobefB_1.nif",
    r"!UBE\clothes\farmclothes03\farmerrobefplus_1.nif",
    r"!UBE\clothes\farmclothes04\robefB_1.nif",
    r"!UBE\BDO Reborn Angelic Chorus\F\AC_Main_1.nif",
    r"!UBE\Kreis\KSA\ksws03\BodyAF_1.nif",
]


def main():
    for rel in CASES:
        p = OUT / rel
        print(f"\n===== {rel} =====")
        if not p.is_file():
            print("  MISSING"); continue
        nif = pynifly.NifFile(str(p))
        visible_nonbody = []
        for s in nif.shapes:
            v = len(s.verts)
            fl = int(getattr(s, "flags", 0) or 0)
            hidden = bool(fl & 1)
            tex = [k for k, x in (s.textures or {}).items() if x]
            z = np.asarray(s.verts, dtype=np.float64)[:, 2]
            zspan = float(z.max() - z.min())
            tag = ""
            if s.name in ("3BA", "3BA_Anus", "3BA_Vagina") or s.name == "BaseShape":
                tag = " <-- body"
            elif not hidden and v > 200:
                visible_nonbody.append(s.name)
            print(f"   {s.name!r:18} verts={v:<6} hidden={hidden} "
                  f"Zspan={zspan:5.1f} tex={tex or 'NONE'}{tag}")
        print(f"   => visible non-body shapes (the actual garment): "
              f"{visible_nonbody or 'NONE !!'}")


if __name__ == "__main__":
    main()
