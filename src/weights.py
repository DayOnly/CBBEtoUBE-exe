"""Bone weight transfer from UBE reference body to a refit armor mesh.

The armor's original bone weights were authored against CBBE's bones. UBE may
have a different bone set (e.g. extra collision bones, renamed chains). We
discard the source weights entirely and resample from UBE's vertex weights,
since the only thing that matters for animation is that the armor moves with
the body it sits on.

Approach: K-nearest-neighbor weighted blend. For each armor vertex, take the
K nearest UBE vertices (by distance to the deformed armor position), blend
their bone weights with inverse-distance weighting, renormalize.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree


SKYRIM_MAX_INFLUENCES = 4


def transfer_weights(
    deformed_verts: np.ndarray,
    tgt_ref_verts: np.ndarray,
    tgt_ref_bone_weights: dict[str, np.ndarray],
    *,
    k: int = 4,
    eps: float = 1e-6,
    max_influences: int = SKYRIM_MAX_INFLUENCES,
) -> dict[str, np.ndarray]:
    """Resample bone weights at deformed_verts from tgt_ref_verts.

    Args:
        deformed_verts: (N, 3) target positions of armor vertices.
        tgt_ref_verts: (V, 3) UBE reference body vertices.
        tgt_ref_bone_weights: { bone_name -> (M, 2) array of [vert_idx, weight] }
            for the UBE reference body. Sparse — only nonzero entries.
        k: number of nearest UBE verts to blend per armor vert.
        max_influences: cap influences per output vertex (Skyrim uses 4).

    Returns:
        { bone_name -> (P, 2) array of [vert_idx, weight] } sparse weights for
        the deformed armor, suitable for write-back via nif_io.save_nif.
    """
    deformed_verts = np.asarray(deformed_verts, dtype=np.float64)
    tgt_ref_verts = np.asarray(tgt_ref_verts, dtype=np.float64)
    N = deformed_verts.shape[0]
    V = tgt_ref_verts.shape[0]

    tree = cKDTree(tgt_ref_verts)
    dists, idx = tree.query(deformed_verts, k=k)  # (N, k)
    if k == 1:
        dists = dists[:, None]
        idx = idx[:, None]

    inv = 1.0 / (dists + eps)
    inv_sum = inv.sum(axis=1, keepdims=True)
    blend = inv / inv_sum  # (N, k) blend weights per armor vertex

    # Build dense (N, num_bones) weight matrix in-memory.
    # For Skyrim-sized armors (10k-50k verts) and ~70 bones, that's < 30 MB.
    bone_list = list(tgt_ref_bone_weights.keys())
    bone_to_col = {b: i for i, b in enumerate(bone_list)}
    B = len(bone_list)

    # Per-vertex dense weights for the UBE reference body
    ube_dense = np.zeros((V, B), dtype=np.float64)
    for bn, pairs in tgt_ref_bone_weights.items():
        if pairs.size == 0:
            continue
        vi = pairs[:, 0].astype(np.int64)
        w = pairs[:, 1]
        ube_dense[vi, bone_to_col[bn]] = w

    # Sample at K nearest UBE verts and blend
    sampled = ube_dense[idx]                  # (N, k, B)
    armor_dense = (sampled * blend[..., None]).sum(axis=1)  # (N, B)

    # Cap at max_influences per vertex, keeping the largest by magnitude
    if max_influences < B:
        keep = np.argpartition(-armor_dense, max_influences, axis=1)[:, :max_influences]
        mask = np.zeros_like(armor_dense, dtype=bool)
        rows = np.arange(N)[:, None]
        mask[rows, keep] = True
        armor_dense = np.where(mask, armor_dense, 0.0)

    # Renormalize per vertex
    row_sum = armor_dense.sum(axis=1, keepdims=True)
    armor_dense = armor_dense / np.where(row_sum > 0, row_sum, 1.0)

    # Repack as sparse {bone -> [(vert_idx, weight), ...]}
    out: dict[str, np.ndarray] = {}
    for bn, col in bone_to_col.items():
        w = armor_dense[:, col]
        nz = np.flatnonzero(w > 0)
        if nz.size == 0:
            continue
        out[bn] = np.column_stack([nz.astype(np.float64), w[nz]])
    return out
