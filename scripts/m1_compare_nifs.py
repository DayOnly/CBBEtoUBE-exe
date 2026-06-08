"""M1 — Compare CBBE-source vs UBE-built versions of Obi's Druchii Top.

Goal: document the exact structural diff our auto-converter has to produce.
We compare the input we'd receive (CBBE source) against the target we'd emit
(UBE built). The diff spec drives M3 (NIF surgery).

Inputs:
  - CBBE source : Obi's Druchii Armor MAIN FILE 3Ba\meshes\...\Druchii Top_1.nif
  - UBE target  : Bodyslide Output\meshes\!UBE\Obicnii\DruchiiArmor\Druchii Top_1.nif
"""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.pynifly'))

from pyn import pynifly


CBBE_SRC = os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Obi's Druchii Armor MAIN FILE 3Ba\meshes\Obicnii\DruchiiArmor\Druchii Top_1.nif"
UBE_TGT  = os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output\meshes\!UBE\Obicnii\DruchiiArmor\Druchii Top_1.nif"


def summarize_nif(path):
    if not Path(path).is_file():
        return None
    nf = pynifly.NifFile(path)
    info = {
        "path": path,
        "size_bytes": Path(path).stat().st_size,
        "shape_count": len(nf.shapes),
        "shapes": [],
    }
    for s in nf.shapes:
        shape_info = {
            "name": s.name,
            "block_type": s.__class__.__name__,
            "verts": len(s.verts),
            "tris": len(s.tris),
            "bone_count": len(getattr(s, "bone_names", []) or []),
            "has_normals": bool(getattr(s, "normals", None)),
            "has_colors": bool(getattr(s, "colors", None)),
        }
        # Bone names (truncate display)
        bones = getattr(s, "bone_names", None) or []
        if bones:
            shape_info["first_bones"] = bones[:5]
            shape_info["last_bones"]  = bones[-3:] if len(bones) > 5 else []
        info["shapes"].append(shape_info)
    return info


def print_summary(label, info):
    print(f"\n==== {label} ====")
    if info is None:
        print("  (file missing)")
        return
    print(f"  path:   {info['path']}")
    print(f"  size:   {info['size_bytes']:,} bytes")
    print(f"  shapes: {info['shape_count']}")
    for sh in info["shapes"]:
        print(f"  - {sh['name']!r:30s} {sh['block_type']:25s}"
              f"  {sh['verts']:6d}v {sh['tris']:6d}t"
              f"  {sh['bone_count']:3d} bones")
        if sh.get("first_bones"):
            print(f"      first 5 bones: {sh['first_bones']}")


def diff(cbbe, ube):
    print("\n==== diff (CBBE -> UBE) ====")
    if cbbe is None or ube is None:
        print("  one or both files missing; can't diff")
        return

    cbbe_shapes = {sh["name"]: sh for sh in cbbe["shapes"]}
    ube_shapes  = {sh["name"]: sh for sh in ube["shapes"]}

    only_cbbe = set(cbbe_shapes) - set(ube_shapes)
    only_ube  = set(ube_shapes)  - set(cbbe_shapes)
    common    = set(cbbe_shapes) & set(ube_shapes)

    print(f"\n  shapes REMOVED (CBBE-only): {sorted(only_cbbe) if only_cbbe else 'none'}")
    print(f"  shapes ADDED (UBE-only):    {sorted(only_ube)  if only_ube  else 'none'}")
    print(f"  shapes COMMON to both:      {sorted(common)    if common    else 'none'}")

    print(f"\n  per-shape changes in COMMON shapes:")
    for name in sorted(common):
        c = cbbe_shapes[name]; u = ube_shapes[name]
        delta = []
        if c["verts"] != u["verts"]: delta.append(f"verts {c['verts']} -> {u['verts']}")
        if c["tris"] != u["tris"]:   delta.append(f"tris {c['tris']} -> {u['tris']}")
        if c["bone_count"] != u["bone_count"]: delta.append(f"bones {c['bone_count']} -> {u['bone_count']}")
        if c["block_type"] != u["block_type"]: delta.append(f"type {c['block_type']} -> {u['block_type']}")
        if delta:
            print(f"    {name}: {', '.join(delta)}")
        else:
            print(f"    {name}: identical structure")


cbbe = summarize_nif(CBBE_SRC)
ube  = summarize_nif(UBE_TGT)

print_summary("CBBE source (input)",  cbbe)
print_summary("UBE built (target)", ube)
diff(cbbe, ube)
