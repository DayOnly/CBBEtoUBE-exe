"""Verify the fix BEFORE coding it: recompute _finalize's needed_bones on the
real data. present_bones = bones of the convert_nif output (BaseShape + cloth +
Cuirass; no colliders/3BA yet). needed_bones_OLD = xml_bones - present_bones
(triggers 3BA reimport). needed_bones_NEW excludes skeleton bones (loaded skel
OR 'NPC ' prefix). If NEW is empty => no spurious body reimport => single body.
"""
import re
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from src import nif_convert  # noqa: E402

DEPLOYED = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes\!UBE\armor\iron\f\cuirasslight_1.nif")
XML = DEPLOYED.with_name("cuirasslight.xml")
# shapes present in the convert_nif output, BEFORE _finalize re-imports anything
CONVERT_OUTPUT = {"BaseShape", "Skirt Rear", "Belt", "Bag", "Skirt Front", "Cuirass"}


def main():
    nif = pynifly.NifFile(str(DEPLOYED))
    present_bones = set()
    for s in nif.shapes:
        if s.name in CONVERT_OUTPUT:
            present_bones |= set(s.bone_names or [])
    txt = XML.read_text(errors="ignore")
    xml_bones = set(re.findall(r'<bone\s+name="([^"]+)"', txt))
    xml_bones |= set(re.findall(r'\bbody[AB]="([^"]+)"', txt))

    needed_old = xml_bones - present_bones
    skel = {b.lower() for b in nif_convert._actor_skeleton_bone_names()}
    print(f"actor skeleton bones loaded: {len(skel)}")
    needed_new = {b for b in needed_old
                  if b.lower() not in skel and not b.lower().startswith("npc ")}

    print(f"\nneeded_bones OLD ({len(needed_old)}):", sorted(needed_old))
    print(f"\nneeded_bones NEW ({len(needed_new)}):", sorted(needed_new))
    print("\n=> OLD non-empty triggers 3BA reimport (double body).")
    print("=> NEW should be EMPTY (or only true custom bones) => single body.")
    # which of the OLD are skeleton vs 'NPC ' vs custom
    only_npc = {b for b in needed_old if b.lower().startswith("npc ") and b.lower() not in skel}
    in_skel = {b for b in needed_old if b.lower() in skel}
    print(f"\nof OLD: in loaded skel={len(in_skel)}, 'NPC '-prefixed-not-in-skel={len(only_npc)}")
    if only_npc:
        print("  'NPC '-only (relying on prefix heuristic):", sorted(only_npc))


if __name__ == "__main__":
    main()
