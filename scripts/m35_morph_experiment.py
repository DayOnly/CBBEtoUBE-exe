"""M3.5 experiment: cross-topology body morph propagation to armor verts.

Goal: armor designed for CBBE 3BA proportions, refit to user's UBE preset
body proportions. NOT a surface-frame transfer (those overshot in M3
phase 2 attempts). Just per-vertex inverse-distance weighted shifts.

Tests three approaches against hand-authored Druchii UBE Top_1 as ground
truth. Whichever beats verbatim-copy baseline (0.226 mean per-vert) wins.

Approaches tested:
  A. Vertex-nearest weighted shift via CBBE→UBE closest-vert correspondence
  B. Surface-projection shift (project_to_mesh on cbbe_proj then ube_idx)
  C. Surface-projection shift (project_to_mesh on vert directly to both)
  D. Vertex-nearest with K=4
  E. Vertex-nearest with K=16
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / ".pynifly"))

import numpy as np
from scipy.spatial import cKDTree
from pyn import pynifly

from src.correspondence import MeshIndex, project_to_mesh


# Test fixture: Druchii Top_1 — its 5FabricTits is the canary
CBBE_SRC = Path(r"<MODLIST>\mods\Obi's Druchii Armor MAIN FILE 3Ba"
                r"\meshes\Obicnii\DruchiiArmor\Druchii Top_1.nif")
HAND_REAL = Path(r"<MODLIST>\mods\Bodyslide Output"
                 r"\Meshes\!UBE\Obicnii\DruchiiArmor\Druchii Top_1.nif")
# Reference bodies — the CBBE template (slider-zero) is inline in CBBE_SRC.
# User's UBE preset body lives in BodySlide Output.
CBBE_TEMPLATE_FROM_SRC = True  # use the inline 3BA from CBBE_SRC
UBE_PRESET_NIF = Path(r"<MODLIST>\mods\Bodyslide Output"
                      r"\meshes\!UBE\Body\femalebody_tangent_1.nif")


def load_shape(path: Path, name: str):
    nf = pynifly.NifFile(filepath=str(path))
    return nf.shape_dict.get(name)


def vertex_nearest_morph(armor_verts, cbbe_body_verts, ube_body_verts, k=8):
    """Approach A — vertex-nearest correspondence.

    Step 1: per CBBE body vert, find nearest UBE body vert → shift vector.
    Step 2: per armor vert, find k nearest CBBE body verts → weighted shift.
    """
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    cbbe_body_verts = np.asarray(cbbe_body_verts, dtype=np.float64)
    ube_body_verts = np.asarray(ube_body_verts, dtype=np.float64)

    # CBBE→UBE per-vertex correspondence (closest UBE vert to each CBBE vert)
    ube_tree = cKDTree(ube_body_verts)
    _, ube_match = ube_tree.query(cbbe_body_verts, k=1)
    cbbe_to_ube_shift = ube_body_verts[ube_match] - cbbe_body_verts  # (N_cbbe, 3)

    # Per armor vert: k nearest CBBE body verts, inverse-distance weighted shift
    cbbe_tree = cKDTree(cbbe_body_verts)
    dists, idxs = cbbe_tree.query(armor_verts, k=k)
    weights = 1.0 / (dists + 1e-6)
    weights /= weights.sum(axis=1, keepdims=True)
    armor_shifts = (cbbe_to_ube_shift[idxs] * weights[..., None]).sum(axis=1)

    return (armor_verts + armor_shifts).astype(np.float32)


def surface_proj_via_cbbe_morph(armor_verts, cbbe_idx, ube_idx):
    """Approach B — old M3.5 attempt #1. Project armor vert to CBBE, then
    project that CBBE surface point onto UBE."""
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    cbbe_proj, _, _ = project_to_mesh(armor_verts, cbbe_idx)
    ube_proj, _, _ = project_to_mesh(cbbe_proj, ube_idx)
    return (armor_verts + (ube_proj - cbbe_proj)).astype(np.float32)


def surface_proj_direct(armor_verts, cbbe_idx, ube_idx):
    """Approach C — old M3.5 attempt #2. Project armor vert to both
    bodies independently; difference of projections is the shift."""
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    cbbe_proj, _, _ = project_to_mesh(armor_verts, cbbe_idx)
    ube_proj, _, _ = project_to_mesh(armor_verts, ube_idx)
    return (armor_verts + (ube_proj - cbbe_proj)).astype(np.float32)


def report(label, ours, hand_real, baseline):
    """Compare per-vertex distance from `ours` to hand-authored ground truth."""
    d = np.linalg.norm(ours - hand_real, axis=1)
    mean = float(d.mean()); maxd = float(d.max()); p95 = float(np.percentile(d, 95))
    improvement = (baseline - mean) / baseline * 100
    print(f"  {label:<35} mean={mean:.4f}  max={maxd:.4f}  p95={p95:.4f}  "
          f"vs baseline ({baseline:.4f}): {improvement:+.1f}%")
    return mean


def main():
    # Load source CBBE armor + body
    src_nf = pynifly.NifFile(filepath=str(CBBE_SRC))
    cbbe_body = src_nf.shape_dict["3BA"]
    cbbe_v = np.asarray(cbbe_body.verts, dtype=np.float64)
    cbbe_t = np.asarray(cbbe_body.tris, dtype=np.int64)

    # Load user's UBE preset body
    ube_nf = pynifly.NifFile(filepath=str(UBE_PRESET_NIF))
    ube_body = ube_nf.shape_dict["BaseShape"]
    ube_v = np.asarray(ube_body.verts, dtype=np.float64)
    ube_t = np.asarray(ube_body.tris, dtype=np.int64)

    print(f"CBBE body: {len(cbbe_v)} verts  Y range=({cbbe_v[:,1].min():.2f}..{cbbe_v[:,1].max():.2f})")
    print(f"UBE  body: {len(ube_v)} verts  Y range=({ube_v[:,1].min():.2f}..{ube_v[:,1].max():.2f})")
    print()

    cbbe_idx = MeshIndex.build(cbbe_v, cbbe_t)
    ube_idx = MeshIndex.build(ube_v, ube_t)

    # Load hand-authored ground truth
    hand_nf = pynifly.NifFile(filepath=str(HAND_REAL))

    # Also load the BOOTS pair — leg pieces are where user reports clipping
    boots_src_nf = pynifly.NifFile(filepath=str(CBBE_SRC.parent / "Druchii Boots_1.nif"))
    boots_hand_nf = pynifly.NifFile(filepath=str(HAND_REAL.parent / "Druchii Boots_1.nif"))

    print(f"Per-shape comparison ours vs hand-authored:")
    print(f"  (baseline = verbatim copy; lower is better)")
    print()

    overall = {}
    cases = [
        # (label, src_nf, hand_nf, shape_name)
        ("Top:5FabricTits",         src_nf, hand_nf, "5FabricTits"),
        ("Top:3MetalCollar",        src_nf, hand_nf, "3MetalCollar"),
        ("Top:3MetalPauldrons",     src_nf, hand_nf, "3MetalPauldrons"),
        ("Top:3MetalDecoPauldron",  src_nf, hand_nf, "3MetalDecoPauldron"),
        ("Top:3LeatherBeltArms",    src_nf, hand_nf, "3LeatherBeltArms"),
        # BOOTS — leg armor (user's actual complaint)
        ("Boots:8FabricLeg",        boots_src_nf, boots_hand_nf, "8FabricLeg"),
        ("Boots:8LeatherBoots",     boots_src_nf, boots_hand_nf, "8LeatherBoots"),
        ("Boots:8GemBoots",         boots_src_nf, boots_hand_nf, "8GemBoots"),
        ("Boots:8MetalBootArmor",   boots_src_nf, boots_hand_nf, "8MetalBootArmor"),
    ]
    for case_label, csrc_nf, chand_nf, shape_name in cases:
        src_shape = csrc_nf.shape_dict.get(shape_name)
        hand_shape = chand_nf.shape_dict.get(shape_name)
        if src_shape is None or hand_shape is None: continue
        src_verts = np.asarray(src_shape.verts, dtype=np.float64)
        hand_verts = np.asarray(hand_shape.verts, dtype=np.float64)
        if src_verts.shape != hand_verts.shape:
            print(f"  {shape_name}: vert count mismatch CBBE={src_verts.shape} vs HAND={hand_verts.shape} — SKIP")
            continue

        baseline = float(np.linalg.norm(src_verts - hand_verts, axis=1).mean())
        print(f"--- {case_label} (V={len(src_verts)}, baseline {baseline:.4f}) ---")

        results = {}
        results['A. vertex-nearest k=8 '] = vertex_nearest_morph(src_verts, cbbe_v, ube_v, k=8)
        results['B. proj cbbe->ube     '] = surface_proj_via_cbbe_morph(src_verts, cbbe_idx, ube_idx)
        results['C. proj vert->both    '] = surface_proj_direct(src_verts, cbbe_idx, ube_idx)
        results['D. vertex-nearest k=4 '] = vertex_nearest_morph(src_verts, cbbe_v, ube_v, k=4)
        results['E. vertex-nearest k=16'] = vertex_nearest_morph(src_verts, cbbe_v, ube_v, k=16)
        for label, verts in results.items():
            mean = report(label, verts, hand_verts, baseline)
            overall.setdefault(label, []).append(mean - baseline)
        print()

    print("Summary (average delta from baseline; negative = better than verbatim):")
    for label, deltas in overall.items():
        avg = sum(deltas) / len(deltas)
        verdict = "BETTER" if avg < 0 else "WORSE "
        print(f"  {label}  avg delta = {avg:+.4f}  {verdict}")


main()
