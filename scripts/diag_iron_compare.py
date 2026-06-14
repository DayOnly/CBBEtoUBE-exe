"""Ground-truth the iron-cuirass CTD: compare the DEPLOYED (crashing) NIF/XML
against a FRESH reconvert (convert_nif runs _finalize_hdt_physics, which
re-imports + hides colliders). For each: body-shape count, collider presence +
Hidden flag (bit0=0x1), and dangling HDT-XML refs.
"""
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from src import nif_convert  # noqa: E402

SRC = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA\meshes\armor\iron\f\cuirasslight_1.nif")
DEPLOYED = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron\f\cuirasslight_1.nif")
UBE_BODY = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA\meshes\!UBE\Body\femalebody_tangent_1.nif")
COLLIDERS = ("Collision", "Belt Col", "Bag Col")


def report(tag, nif_path, xml_path):
    print(f"\n===== {tag} =====")
    print("NIF:", nif_path, "exists=", nif_path.is_file())
    if not nif_path.is_file():
        return
    nif = pynifly.NifFile(str(nif_path))
    shapes = {s.name: s for s in nif.shapes}
    bodies = []
    for name, s in shapes.items():
        try:
            v = len(s.verts)
        except Exception:
            v = -1
        if v > 20000:
            bodies.append((name, v))
    print(f"shapes({len(shapes)}):", list(shapes))
    print("BODY shapes (>20k verts):", bodies, "  <= expect exactly 1")
    for c in COLLIDERS:
        if c in shapes:
            fl = int(getattr(shapes[c], "flags", 0) or 0)
            print(f"  collider {c!r}: present  flags={fl}  hidden(bit0)={bool(fl & 0x1)}")
        else:
            print(f"  collider {c!r}: ABSENT")
    if xml_path and xml_path.is_file():
        txt = xml_path.read_text(errors="ignore")
        refs = set(re.findall(r'<per-(?:vertex|triangle)-shape\s+name="([^"]+)"', txt))
        print("XML refs:", sorted(refs))
        print("DANGLING:", sorted(refs - set(shapes)))
    else:
        print("XML:", xml_path, "MISSING")


def main():
    report("DEPLOYED (crashing)", DEPLOYED, DEPLOYED.with_name("cuirasslight.xml"))

    out = Path(tempfile.mkdtemp()) / "cuirasslight_1.nif"
    res = nif_convert.convert_nif(SRC, out, ube_body_ref_path=UBE_BODY, biped_slots=0x4)
    print("\n[reconvert status:", res.status, "]")
    report("FRESH RECONVERT (current code)", out, out.with_name("cuirasslight.xml"))


if __name__ == "__main__":
    main()
