"""Verify the FRESHLY reconverted iron cuirass output (real pipeline, post-fix).
For every iron cuirass NIF in the deployed output: mtime (fresh?), shapes, body
count (must be 1 = BaseShape, no '3BA'), colliders present + Hidden, and any
dangling HDT-XML refs."""
import re
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron")
COLLIDERS = ("Collision", "Belt Col", "Bag Col")


def check(nifp):
    nif = pynifly.NifFile(str(nifp))
    shapes = {s.name: s for s in nif.shapes}
    bodies = []
    body_named = []
    for n, s in shapes.items():
        try:
            v = len(s.verts)
        except Exception:
            v = -1
        if v > 20000:
            bodies.append((n, v))
        if n in ("3BA", "3BA_Anus", "3BA_Vagina") or n.lower().startswith(
                ("femalebody", "femaleunderwearbody")):
            body_named.append((n, v))
    age = (time.time() - nifp.stat().st_mtime) / 60.0
    rel = nifp.relative_to(OUT.parent.parent)
    ok = (len(bodies) == 1 and bodies[0][0] == "BaseShape" and not body_named)
    print(f"\n{'OK ' if ok else 'XX '}{rel}  (mtime {age:.0f} min ago)")
    print(f"   shapes: {list(shapes)}")
    print(f"   body(>20k): {bodies}   leftover body-named: {body_named or 'NONE'}")
    cols = []
    for c in COLLIDERS:
        if c in shapes:
            fl = int(getattr(shapes[c], "flags", 0) or 0)
            cols.append(f"{c}(hidden={bool(fl & 1)})")
    print(f"   colliders: {cols or 'none'}")
    # XML beside it (strip _0/_1)
    stem = nifp.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-2]; break
    xmlp = nifp.with_name(stem + ".xml")
    if xmlp.is_file():
        refs = set(re.findall(r'<per-(?:vertex|triangle)-shape\s+name="([^"]+)"',
                              xmlp.read_text(errors="ignore")))
        dang = sorted(refs - set(shapes))
        print(f"   XML {xmlp.name}: dangling={dang or 'NONE'}")
    return ok


def main():
    nifs = sorted(OUT.rglob("*cuirass*.nif"))
    print(f"found {len(nifs)} iron cuirass NIFs under {OUT}")
    results = [check(p) for p in nifs]
    print(f"\n==== {sum(results)}/{len(results)} single-body OK ====")


if __name__ == "__main__":
    main()
