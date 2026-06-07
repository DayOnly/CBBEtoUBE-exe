"""M3 phase 2 experiment: prototype `copy_shape` deep-copy via pynifly.

Approach: pynifly has no `deleteBlock` API, so we can't remove the inline
3BA body from a CBBE NIF. Inverted approach: start from the UBE reference
body NIF (has BaseShape + VirtualBody), then copy each non-body shape
from the CBBE source into it via `createShapeFromData` + manual skin
transfer.

This script:
  1. Round-trips a single boots shape (CBBE source -> fresh empty NIF -> save)
     to validate the deep-copy preserves skin / partitions / alpha / textures.
  2. If round-trip passes, performs the full body-swap on Druchii Top:
     start from UBE ref, copy non-body shapes from CBBE Druchii Top.

Phase 2 is high-risk because there are many small things pynifly might
not faithfully preserve. This script surfaces what works and what
doesn't on real data before committing the approach.
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / ".pynifly"))

from pyn import pynifly


def summarize_shape(s, prefix="    "):
    print(f"{prefix}name      : {s.name!r}")
    print(f"{prefix}blockname : {s.blockname}")
    print(f"{prefix}V/T       : {len(s.verts)} verts / {len(s.tris)} tris")
    print(f"{prefix}bones     : {len(s.bone_names)} (first 3: {s.bone_names[:3]})")
    print(f"{prefix}weights   : {len(s.bone_weights)} bones have weights")
    has_alpha = s.has_alpha_property
    print(f"{prefix}alpha     : {has_alpha}")
    print(f"{prefix}textures  : {[t for t in s.textures if t][:3]}...")
    print(f"{prefix}partitions: {len(s.partitions) if s.partitions else 0}")
    print(f"{prefix}partition_tris: {'set' if s.partition_tris is not None else 'none'}")


def copy_shape(src_shape, dst_nif, parent=None):
    """Deep-copy a shape from one NIF to another via pynifly.

    Carries: geometry, textures, alpha, skin instance (bones, weights,
    skin-to-bone transforms, global-to-skin, partitions).
    """
    # 1. Create the new shape with geometry + properties
    new_shape = dst_nif.createShapeFromData(
        src_shape.name,
        list(src_shape.verts),
        list(src_shape.tris),
        list(src_shape.uvs) if src_shape.uvs is not None else [],
        list(src_shape.normals) if src_shape.normals is not None else None,
        props=src_shape.properties,
        parent=parent,
    )

    # 2. Textures (set each slot we have)
    for i, tex in enumerate(src_shape.textures or []):
        if tex:
            new_shape.set_texture(i, tex)

    # 3. Skin instance, if any
    if src_shape.bone_names:
        new_shape.skin()
        # Add bones (with skin-to-bone transforms)
        for bn in src_shape.bone_names:
            xform = src_shape.get_shape_skin_to_bone(bn)
            new_shape.add_bone(bn, xform=xform)
        # Global-to-skin
        if src_shape.has_global_to_skin:
            new_shape.set_global_to_skin(src_shape.global_to_skin)
        # Weights
        for bn, pairs in (src_shape.bone_weights or {}).items():
            new_shape.setShapeWeights(bn, [(int(i), float(w)) for i, w in pairs])
        # Partitions
        if src_shape.partitions and src_shape.partition_tris is not None:
            new_shape.set_partitions(src_shape.partitions, src_shape.partition_tris)

    # 4. Alpha
    if src_shape.has_alpha_property:
        new_shape.save_alpha_property()

    # 5. Shader attrs (re-save in case createShapeFromData didn't pick them up)
    try:
        new_shape.save_shader_attributes()
    except Exception:
        pass

    return new_shape


def round_trip_one_shape(src_path: Path, shape_name: str, out_path: Path):
    """Load src NIF, copy a single shape into a fresh empty NIF, save."""
    print(f"\n--- round-trip {shape_name!r} from {src_path.name} ---")
    src_nif = pynifly.NifFile(filepath=str(src_path))
    src_shape = src_nif.shape_dict.get(shape_name)
    if src_shape is None:
        print(f"  shape {shape_name!r} not in source (have: {[s.name for s in src_nif.shapes]})")
        return False
    print("  source:")
    summarize_shape(src_shape)

    # Create empty target NIF
    dst_nif = pynifly.NifFile()
    dst_nif.initialize("SKYRIMSE", str(out_path))
    new_shape = copy_shape(src_shape, dst_nif)

    dst_nif.save()

    # Reload to confirm what made it through
    rt_nif = pynifly.NifFile(filepath=str(out_path))
    rt_shape = rt_nif.shape_dict.get(shape_name)
    if rt_shape is None:
        print(f"  FAIL: shape lost after save+reload")
        return False
    print("  reloaded:")
    summarize_shape(rt_shape)

    return True


def full_body_swap(cbbe_path: Path, ube_ref_path: Path, out_path: Path,
                   body_names=("3BA", "3BA_Vagina", "3BA_Anus")):
    """Run the full phase-2 conversion: UBE ref body + CBBE armor shapes."""
    print(f"\n--- body-swap {cbbe_path.name} ---")
    cbbe_nif = pynifly.NifFile(filepath=str(cbbe_path))
    ube_nif  = pynifly.NifFile(filepath=str(ube_ref_path))

    print(f"  CBBE shapes: {[s.name for s in cbbe_nif.shapes]}")
    print(f"  UBE shapes : {[s.name for s in ube_nif.shapes]}")

    # Start fresh — we'll add UBE body + CBBE armor manually
    dst = pynifly.NifFile()
    dst.initialize("SKYRIMSE", str(out_path))

    # Inject UBE body shapes (BaseShape + VirtualBody if present)
    for s in ube_nif.shapes:
        print(f"  copying UBE shape {s.name!r}...")
        try:
            copy_shape(s, dst)
            print(f"    OK")
        except Exception as e:
            print(f"    FAIL: {e!r}")

    # Inject CBBE armor shapes (skip body shapes)
    for s in cbbe_nif.shapes:
        if s.name in body_names:
            print(f"  SKIP body shape {s.name!r}")
            continue
        print(f"  copying armor shape {s.name!r}...")
        try:
            copy_shape(s, dst)
            print(f"    OK")
        except Exception as e:
            print(f"    FAIL: {e!r}")

    dst.save()
    print(f"\n  saved: {out_path}  ({out_path.stat().st_size} bytes)")

    # Reload to verify
    rt = pynifly.NifFile(filepath=str(out_path))
    print(f"  final shapes: {[s.name for s in rt.shapes]}")
    for s in rt.shapes:
        print(f"    {s.name!r}: V={len(s.verts)} T={len(s.tris)} bones={len(s.bone_names)}")


def main():
    out_root = PROJ / "output" / "m3p2_exp"
    out_root.mkdir(parents=True, exist_ok=True)

    # 1. Round-trip a non-body shape with skin (Kozakowy belt - we know its structure)
    kozakowy_belt = PROJ / "samples" / "m1" / "kozakowy_vampire" / "cbbe" \
        / "[TOTOxKozakowy] Kozakowy's Vampire Armor 3BA" \
        / "meshes" / "clothes" / "Kozakowy" / "VampireArmor" / "Belt_1.nif"
    if kozakowy_belt.is_file():
        round_trip_one_shape(kozakowy_belt, "belt",
                             out_root / "rt_belt.nif")

    # 2. Round-trip a body-containing NIF's armor shape (Druchii Top "5FabricTits")
    druchii_top = Path(r"<MODLIST>\mods\Obi's Druchii Armor MAIN FILE 3Ba"
                       r"\meshes\Obicnii\DruchiiArmor\Druchii Top_1.nif")
    if druchii_top.is_file():
        round_trip_one_shape(druchii_top, "5FabricTits",
                             out_root / "rt_5fabrictits.nif")

    # 3. Full body-swap on Druchii Top
    ube_ref = Path(r"<MODLIST>\mods\Bodyslide Output"
                   r"\meshes\!UBE\Body\femalebody_tangent_1.nif")
    if druchii_top.is_file() and ube_ref.is_file():
        full_body_swap(druchii_top, ube_ref, out_root / "DruchiiTop_swapped.nif")


main()
