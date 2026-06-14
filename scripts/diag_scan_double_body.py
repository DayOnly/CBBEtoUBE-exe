"""Refined double-body sweep. A LEFTOVER BODY = a shape that is either
named like a body (3BA*/femalebody*/femaleunderwearbody*) OR is full-character
height (Zspan>90) and large (>20k) and not BaseShape -- this excludes cloth
(towel ~49) and armor torsos (~47) and props (book ~8) that the loose >20k
test mis-flagged. Report only NIFs that have BaseShape AND a leftover body.
For each: FRESH/STALE (mtime) + how the XML references it (per-triangle=collider
path / per-vertex / via-bones=framework / not-in-xml)."""
import re
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes")
BODY_NAMES = {"3BA", "3BA_Anus", "3BA_Vagina"}
BODY_PREFIX = ("femalebody", "femaleunderwearbody")


def is_body_shape(s):
    nm = s.name
    if nm == "BaseShape":
        return False
    if nm in BODY_NAMES or nm.lower().startswith(BODY_PREFIX):
        return True
    try:
        v = len(s.verts)
        z = np.asarray(s.verts, dtype=np.float64)[:, 2]
        zspan = float(z.max() - z.min())
    except Exception:
        return False
    return v > 20000 and zspan > 90.0


def has_xml_sibling(nifp):
    stem = nifp.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-2]; break
    return nifp.with_name(stem + ".xml"), nifp.with_name(stem + ".xml").is_file()


def xml_path_of(xmlp, name):
    if not xmlp.is_file():
        return "no-xml"
    txt = xmlp.read_text(errors="ignore")
    if name in re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt):
        return "per-triangle(collider->missing path)"
    if name in re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt):
        return "per-vertex(softbody)"
    xb = set(re.findall(r'<bone\s+name="([^"]+)"', txt)) | set(
        re.findall(r'\bbody[AB]="([^"]+)"', txt))
    return "via-bones(framework)" if xb else "not-in-xml"


def main():
    t0 = time.time()
    hits = []
    scanned = 0
    for p in OUT.rglob("*.nif"):
        xmlp, has_xml = has_xml_sibling(p)
        if not has_xml:
            continue
        try:
            nif = pynifly.NifFile(str(p))
        except Exception:
            continue
        scanned += 1
        shapes = list(nif.shapes)
        has_base = any(s.name == "BaseShape" and len(s.verts) > 20000 for s in shapes)
        if not has_base:
            continue
        bodies = [s.name for s in shapes if is_body_shape(s)]
        if bodies:
            age = (time.time() - p.stat().st_mtime) / 60.0
            for b in bodies:
                hits.append((p, b, age, xml_path_of(xmlp, b)))

    print(f"scanned {scanned} HDT NIFs in {time.time()-t0:.0f}s\n")
    fresh = [h for h in hits if h[2] < 120]
    stale = [h for h in hits if h[2] >= 120]
    print(f"==== {len(hits)} leftover-body shapes in {len(set(h[0] for h in hits))} NIFs ====")
    print(f"  FRESH (<2h, this reconvert): {len(fresh)}")
    print(f"  STALE (>=2h, NOT reconverted): {len(stale)}\n")
    print("--- FRESH (real fix gap; deployed exe lacks sibling guard) ---")
    for p, b, age, path in fresh:
        print(f"  {p.relative_to(OUT)}  body={b!r}  {age:.0f}min  via {path}")
    print("\n--- STALE (not reconverted; source not picked up?) ---")
    for p, b, age, path in stale:
        print(f"  {p.relative_to(OUT)}  body={b!r}  {age:.0f}min  via {path}")


if __name__ == "__main__":
    main()
