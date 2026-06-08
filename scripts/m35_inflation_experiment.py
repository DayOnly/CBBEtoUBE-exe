"""M3.5 inflation experiment: leg-volume clipping fix.

Approach: for each armor vert, push outward (along CBBE surface normal) by
the amount UBE body extends past CBBE body at that point. Never push
inward. Only push verts close to the body.

Measures CLIPPING (body verts outside boot surface) as the primary metric,
not distance to hand-authored. The Druchii hand-authored has a +2.75 Z
shift that pollutes per-vert distance metrics but isn't relevant to
clipping avoidance.
"""
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / ".pynifly"))

import numpy as np
from pyn import pynifly

from src.correspondence import MeshIndex, project_to_mesh


CBBE_BOOTS = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Obi's Druchii Armor MAIN FILE 3Ba"
                  r"\meshes\Obicnii\DruchiiArmor\Druchii Boots_1.nif")
HAND_BOOTS = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output"
                  r"\Meshes\!UBE\Obicnii\DruchiiArmor\Druchii Boots_1.nif")
CBBE_TOP = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Obi's Druchii Armor MAIN FILE 3Ba"
                r"\meshes\Obicnii\DruchiiArmor\Druchii Top_1.nif")  # has inline 3BA
UBE_BODY = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output"
                r"\Meshes\!UBE\Obicnii\DruchiiArmor\Druchii Top_1.nif")  # has BaseShape


def inflate_outward(armor_verts, cbbe_idx, ube_idx,
                    *, close_threshold=3.0, full_threshold=0.5,
                    max_push=2.0):
    """Push armor verts outward where UBE body extends past CBBE body.

    Algorithm:
      1. Project each armor vert to CBBE body → get projection + normal
      2. Project each armor vert to UBE body → get projection
      3. Compute outward push = max(0, dot(ube_proj - cbbe_proj, cbbe_normal))
         (only positive = UBE further out; ignore inward shrinkage)
      4. Weight by distance to body (close = full push, far = none)
      5. Apply push along CBBE outward normal
    """
    verts = np.asarray(armor_verts, dtype=np.float64)
    cbbe_proj, cbbe_tri, cbbe_normal = project_to_mesh(verts, cbbe_idx)
    ube_proj, _, _ = project_to_mesh(verts, ube_idx)

    # Project (ube_proj - cbbe_proj) onto cbbe_normal — signed distance
    # in the outward direction
    delta = ube_proj - cbbe_proj
    outward_amount = np.einsum("ij,ij->i", delta, cbbe_normal)
    # Only push outward, never inward
    outward_amount = np.clip(outward_amount, 0.0, max_push)

    # Distance to CBBE body (for weighting — close verts get full push)
    cbbe_dist = np.linalg.norm(verts - cbbe_proj, axis=1)
    band = max(close_threshold - full_threshold, 1e-6)
    weight = np.clip((close_threshold - cbbe_dist) / band, 0, 1)

    push = cbbe_normal * (outward_amount * weight)[:, None]
    return (verts + push).astype(np.float32), outward_amount, weight


def count_clipping(armor_verts, armor_tris, body_verts, body_tris,
                   near_threshold=2.0):
    """Estimate body verts clipping through the armor.

    Only consider body verts NEAR the armor surface (within `near_threshold`
    units). Among those, count ones on the OUTSIDE of armor — those are the
    "muscle bulging through fabric" case. Lower is better.
    """
    body_verts = np.asarray(body_verts, dtype=np.float64)
    armor_idx = MeshIndex.build(armor_verts.astype(np.float64), armor_tris)
    armor_proj, _, armor_normal = project_to_mesh(body_verts, armor_idx)

    # Distance from each body vert to nearest armor surface
    dist = np.linalg.norm(body_verts - armor_proj, axis=1)
    near_mask = dist < near_threshold
    if not near_mask.any():
        return 0, 0

    # Outside = positive in direction of armor's outward normal
    rel = body_verts[near_mask] - armor_proj[near_mask]
    outside = np.einsum("ij,ij->i", rel, armor_normal[near_mask])
    clipping = int(np.sum(outside > 0.05))  # body protrudes >0.05 units beyond armor
    return clipping, int(near_mask.sum())


def main():
    # Load source CBBE boots + body shapes
    boots_nf = pynifly.NifFile(filepath=str(CBBE_BOOTS))
    top_nf = pynifly.NifFile(filepath=str(CBBE_TOP))
    cbbe_body = top_nf.shape_dict["3BA"]
    ube_body_nf = pynifly.NifFile(filepath=str(UBE_BODY))
    ube_body = ube_body_nf.shape_dict["BaseShape"]

    cbbe_idx = MeshIndex.build(
        np.asarray(cbbe_body.verts, dtype=np.float64),
        np.asarray(cbbe_body.tris, dtype=np.int64),
    )
    ube_idx = MeshIndex.build(
        np.asarray(ube_body.verts, dtype=np.float64),
        np.asarray(ube_body.tris, dtype=np.int64),
    )

    # Load hand-authored boots for reference
    hand_nf = pynifly.NifFile(filepath=str(HAND_BOOTS))

    ube_body_v = np.asarray(ube_body.verts, dtype=np.float64)
    ube_body_t = np.asarray(ube_body.tris, dtype=np.int64)

    print(f"{'shape':<25} {'verbatim_clip':>13} {'inflated_clip':>13}"
          f" {'hand_clip':>10} {'inflation_mean':>15}")
    print("-" * 85)

    for shape_name in ("8FabricLeg", "8LeatherBoots", "8GemBoots", "8MetalBootArmor"):
        s = boots_nf.shape_dict.get(shape_name)
        if not s: continue
        src_v = np.asarray(s.verts, dtype=np.float64)
        src_t = np.asarray(s.tris, dtype=np.int64)

        # Verbatim — count body verts clipping outside the armor
        verb_clip, total = count_clipping(src_v, src_t, ube_body_v, ube_body_t)

        # Inflated
        inflated_v, outward, weight = inflate_outward(src_v, cbbe_idx, ube_idx)
        infl_clip, _ = count_clipping(inflated_v, src_t, ube_body_v, ube_body_t)
        infl_mean = float(outward[outward > 0].mean()) if (outward > 0).any() else 0.0

        # Hand-authored as reference
        hand_s = hand_nf.shape_dict.get(shape_name)
        if hand_s:
            hand_v = np.asarray(hand_s.verts, dtype=np.float64)
            hand_clip, _ = count_clipping(hand_v, src_t, ube_body_v, ube_body_t)
        else:
            hand_clip = -1

        print(f"{shape_name:<25} {verb_clip:>5}/{total:<5}"
              f"   {infl_clip:>5}/{total:<5}    {hand_clip:>5}/{total:<5}"
              f"   {infl_mean:>10.3f}")


main()
