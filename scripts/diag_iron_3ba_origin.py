"""Did the deployed '3BA' body come from _finalize framework-reimport (=> would
recur with current code) or from classify failing to drop it (=> stale exe)?
Re-imported framework shapes are flagged Hidden (bit0); a classify-leftover body
is NOT hidden. Also: is any '3BA'-unique bone referenced by the HDT XML (the
only thing that would trigger a framework reimport)?"""
import re
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

DEPLOYED = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron\f\cuirasslight_1.nif")
XML = DEPLOYED.with_name("cuirasslight.xml")


def main():
    nif = pynifly.NifFile(str(DEPLOYED))
    shapes = {s.name: s for s in nif.shapes}
    three = shapes.get("3BA")
    if three is None:
        print("no '3BA' shape in deployed"); return
    fl = int(getattr(three, "flags", 0) or 0)
    print(f"deployed '3BA': flags={fl}  hidden(bit0)={bool(fl & 0x1)}  verts={len(three.verts)}")
    print("  => hidden TRUE  = _finalize framework-reimport (recurs w/ current code)")
    print("  => hidden FALSE = classify leftover (stale exe; current code drops it)")

    three_bones = set(three.bone_names or [])
    other_bones = set()
    for n, s in shapes.items():
        if n == "3BA":
            continue
        other_bones |= set(s.bone_names or [])
    unique = sorted(three_bones - other_bones)
    print(f"\n'3BA' carries {len(three_bones)} bones; {len(unique)} UNIQUE to it (not on any other shape):")
    print("  ", unique)

    if XML.is_file():
        txt = XML.read_text(errors="ignore")
        xb = set(re.findall(r'<bone\s+name="([^"]+)"', txt))
        xb |= set(re.findall(r'\bbody[AB]="([^"]+)"', txt))
        print(f"\nXML references {len(xb)} bones via <bone>/bodyA/bodyB:", sorted(xb))
        hit = sorted(set(unique) & xb)
        print("XML-referenced bones UNIQUE to '3BA' (would force reimport):", hit)
    else:
        print("\nXML missing:", XML)


if __name__ == "__main__":
    main()
