# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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

# Below this a written weight is not worth a palette slot. Matches the graft
# passes' own write threshold.
WEIGHT_WRITE_MIN = 1e-4


def plan_weight_writes(new_weights, rows, bone_names, had,
                       max_influences: int = SKYRIM_MAX_INFLUENCES,
                       write_min: float = WEIGHT_WRITE_MIN):
    """Turn a pass's INTENDED per-vertex weights into the exact `setShapeWeights`
    calls that make them survive the save. Returns {bone_name: [(vert, w), ...]},
    including explicit **0.0** entries for influences that must be removed.

    Why this exists (#weight-write-invariant). `setShapeWeights` MERGES: a bone
    you leave out keeps its previous value, so a weight cannot be cleared by
    omission. But the NIF format holds only 4 influences per vertex, and the save
    resolves an overflow ITSELF. Measured 2026-07-25 on a real shape:

      * the file never holds >4 -- the save truncates
      * it keeps the **largest 4** (a .05 and a .19 were dropped for two .20s)
      * it does **NOT** renormalise afterwards -- that row came back summing 1.160

    An over-weighted row is transformed by an inflated sum of its bone matrices,
    so the vertex drifts and drags near-degenerate triangles that flicker with
    view angle. And because the grafted weights are typically the SMALL ones, a
    graft that overflows is silently discarded -- the pass appears to run and
    changes nothing.

    So: choose the surviving palette here rather than letting the save choose.
    Keep the largest `max_influences`, renormalise them to exactly 1.0, and emit
    an explicit 0.0 for every bone the vertex HAD that did not survive -- a zero
    write genuinely removes the influence (verified: the zeroed bone is absent
    after save+reload).

    Args:
      new_weights: (V, B) intended weights; only `rows` are read.
      rows:        vertex indices this pass actually touched. ONLY these are
                   written -- every other vertex must keep relying on merge
                   semantics, since no pass intended to change them.
      bone_names:  length-B column labels.
      had:         (V, B) bool -- bones the vertex already carried. Used solely
                   to know which dropped influences need an explicit zero; a
                   bone that was never there needs no write.
      max_influences / write_min: format cap and the significance floor.
    """
    out: "dict[str, list]" = {}
    rows = np.asarray(rows, dtype=np.int64)
    if rows.size == 0:
        return out
    W = np.array(np.asarray(new_weights, dtype=np.float64)[rows], copy=True)
    H = np.asarray(had, dtype=bool)[rows]
    W[W < write_min] = 0.0

    # Keep the largest `max_influences` per row; zero the rest.
    if W.shape[1] > max_influences:
        cut = np.argsort(W, axis=1)[:, :-max_influences]
        np.put_along_axis(W, cut, 0.0, axis=1)

    # Renormalise the survivors to exactly 1.0. A row with nothing left is left
    # alone entirely (writing zeros everywhere would unskin the vertex and skin
    # it to the origin -- a visible spike, far worse than the drift we fix).
    tot = W.sum(axis=1)
    live = tot > write_min
    W[live] /= tot[live, None]

    keep = W >= write_min
    for j, bone in enumerate(bone_names):
        pairs = []
        for r in range(rows.shape[0]):
            if not live[r]:
                continue
            if keep[r, j]:
                pairs.append((int(rows[r]), float(W[r, j])))
            elif H[r, j]:
                # Had it, no longer wants it: state the removal, don't omit it.
                pairs.append((int(rows[r]), 0.0))
        if pairs:
            out[bone] = pairs
    return out


def check_weight_invariant(shape, eps: float = 1e-3):
    """Verify a saved shape's skinning: no vertex over the influence cap, and
    every weighted vertex sums to 1.0.

    Returns (n_over, n_bad_sum, worst_sum). MUST be run on a shape loaded FROM
    DISK after a save -- in-memory weights are exactly what this bug hides
    behind, since the truncation happens during the write.
    """
    counts: "dict[int, int]" = {}
    sums: "dict[int, float]" = {}
    for bone in (shape.bone_names or []):
        try:
            pairs = shape.bone_weights[bone]
        except Exception:
            continue
        for vi, w in pairs:
            if w <= WEIGHT_WRITE_MIN:
                continue
            counts[vi] = counts.get(vi, 0) + 1
            sums[vi] = sums.get(vi, 0.0) + float(w)
    n_over = sum(1 for c in counts.values() if c > SKYRIM_MAX_INFLUENCES)
    bad = [s for s in sums.values() if abs(s - 1.0) > eps]
    worst = max((abs(s - 1.0) for s in sums.values()), default=0.0)
    return n_over, len(bad), worst


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
