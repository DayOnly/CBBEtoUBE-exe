"""Which source did the deployed iron cuirass come from? Survey every candidate
source NIF's shapes (body name + does it have the HDT cloth/colliders), and
print the deployed 3BA shape's vert count."""
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

CANDS = [
    r"D:\Modlists\ARR\mods\HDT-SMP Vanilla Armors\meshes\armor\iron\f\cuirasslight_1.nif",
    r"D:\Modlists\ARR\mods\CBBE 3BA Vanilla Outfits Redone - Prebuilt\meshes\armor\iron\f\cuirasslight_1.nif",
    r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA\meshes\armor\iron\f\cuirasslight_1.nif",
    r"D:\Modlists\ARR\mods\Authoria - Vanilla Bodyslides\meshes\armor\iron\f\cuirasslight_1.nif",
]
DEPLOYED = r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron\f\cuirasslight_1.nif"


def survey(p):
    p = Path(p)
    if not p.is_file():
        print(f"  MISSING: {p}")
        return
    nif = pynifly.NifFile(str(p))
    rows = []
    for s in nif.shapes:
        try:
            v = len(s.verts)
        except Exception:
            v = -1
        tex = bool({k: x for k, x in (s.textures or {}).items() if x})
        rows.append(f"{s.name}({v}{'' if tex else ',NOTEX'})")
    print(f"  {p.parent.parent.parent.parent.name}:")
    print(f"     {', '.join(rows)}")


def main():
    print("DEPLOYED:")
    survey(DEPLOYED)
    print("\nCANDIDATE SOURCES (priority order in modlist may differ):")
    for c in CANDS:
        survey(c)


if __name__ == "__main__":
    main()
