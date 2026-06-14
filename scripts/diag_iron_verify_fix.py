"""Simulate _finalize's FULL re-import decision with BOTH fixes, on the real
data. Confirm: (1) colliders STILL re-imported (missing list), (2) the '3BA'
body is NOT re-imported (framework scan), so the output stays single-body.
Mirrors the patched code exactly."""
import re
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from src import nif_convert as nc  # noqa: E402

SRC = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA\meshes\armor\iron\f\cuirasslight_1.nif")
DEPLOYED = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron\f\cuirasslight_1.nif")
XML = DEPLOYED.with_name("cuirasslight.xml")
CONVERT_OUTPUT = {"BaseShape", "Skirt Rear", "Belt", "Bag", "Skirt Front", "Cuirass"}


def main():
    dep = pynifly.NifFile(str(DEPLOYED))
    # present = convert_nif output (before _finalize re-imports anything)
    present = set(CONVERT_OUTPUT)
    present_bones = set()
    for s in dep.shapes:
        if s.name in CONVERT_OUTPUT:
            present_bones |= set(s.bone_names or [])

    txt = XML.read_text(errors="ignore")
    col_names = set(re.findall(r'<per-(?:triangle|vertex)-shape\s+name="([^"]+)"', txt))
    xml_bones = set(re.findall(r'<bone\s+name="([^"]+)"', txt))
    xml_bones |= set(re.findall(r'\bbody[AB]="([^"]+)"', txt))

    missing = [n for n in col_names if n not in present]
    print("colliders/proxy missing -> re-import (correct):", sorted(missing))

    # FIX (b): drop skeleton + 'NPC ' bones
    skel = {b.lower() for b in nc._actor_skeleton_bone_names()}
    needed = {b for b in (xml_bones - present_bones)
              if b.lower() not in skel and not b.lower().startswith("npc ")}
    print("\nneeded_bones (after fix b):", sorted(needed))

    # call the REAL helper (what _finalize now uses)
    snf = pynifly.NifFile(str(SRC))
    reimported = nc._select_framework_bone_carriers(
        xml_bones, present_bones,
        [(s.name, list(s.bone_names or [])) for s in snf.shapes],
        skel_bones=nc._actor_skeleton_bone_names(),
        exclude_names=present | set(missing))
    print("framework shapes re-imported (REAL helper):", reimported)

    final_shapes = present | set(m for m in missing if m in {s.name for s in snf.shapes}) | set(reimported)
    print("\nFINAL output shapes:", sorted(final_shapes))
    print("'3BA' in output? ", "3BA" in final_shapes, "  <= must be False")
    body_ct = sum(1 for n in final_shapes if n in nc.BODY_SHAPE_NAMES
                  or n.lower().startswith(nc.BODY_SHAPE_NAME_PREFIXES) or n == "BaseShape")
    print("body-ish shape count:", body_ct, "  <= must be 1 (BaseShape)")


if __name__ == "__main__":
    main()
