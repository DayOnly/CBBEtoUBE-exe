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

"""CBBE-to-UBE spatial correspondence.

The core idea: armor was modeled to sit just outside the CBBE body surface.
We preserve that local offset and reattach the armor to the UBE surface.

For each armor vertex `v`:

  1. Project `v` onto the CBBE mesh — get (tri index, barycentric coords),
     a 3D surface point Pc, and a surface normal Nc.
  2. The armor's offset from CBBE is `D = v - Pc`. We decompose D into the
     CBBE surface frame (Nc, plus two tangent axes) so the offset can be
     re-expressed in the UBE frame at the corresponding location.
  3. Find the closest point on the UBE mesh to Pc — get Pu and Nu.
  4. Rebuild the offset in the UBE frame and set `v' = Pu + D_in_ube_frame`.

The displacement returned is `v' - v`.

CBBE and UBE have different topology, so we can't index by vertex. Everything
is surface-projection based.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class MeshIndex:
    """Spatial index for closest-point-on-mesh queries."""
    verts: np.ndarray          # (V, 3)
    tris: np.ndarray           # (T, 3) int
    centroids: np.ndarray      # (T, 3)
    tri_normals: np.ndarray    # (T, 3) unit normal per triangle
    vert_normals: np.ndarray   # (V, 3) area-weighted per-vertex normal
    kdtree: cKDTree

    @classmethod
    def build(cls, verts: np.ndarray, tris: np.ndarray) -> "MeshIndex":
        verts = np.asarray(verts, dtype=np.float64)
        tris = np.asarray(tris, dtype=np.int64)
        a = verts[tris[:, 0]]
        b = verts[tris[:, 1]]
        c = verts[tris[:, 2]]
        centroids = (a + b + c) / 3.0

        edge1 = b - a
        edge2 = c - a
        face_n = np.cross(edge1, edge2)
        face_area = np.linalg.norm(face_n, axis=1, keepdims=True)
        # area-weighted normals: keep magnitude proportional to area, normalize after vertex accumulation
        with np.errstate(invalid="ignore"):
            tri_normals = face_n / np.where(face_area > 0, face_area, 1.0)

        vert_normals = np.zeros_like(verts)
        for k in range(3):
            np.add.at(vert_normals, tris[:, k], face_n)
        n = np.linalg.norm(vert_normals, axis=1, keepdims=True)
        vert_normals = vert_normals / np.where(n > 0, n, 1.0)

        return cls(
            verts=verts,
            tris=tris,
            centroids=centroids,
            tri_normals=tri_normals,
            vert_normals=vert_normals,
            kdtree=cKDTree(centroids),
        )


def _closest_point_on_triangle(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray):
    """Vectorized closest point on triangle. p, a, b, c are all (M, 3)."""
    ab = b - a
    ac = c - a
    ap = p - a

    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)

    # Region 1: vertex A
    mask_a = (d1 <= 0) & (d2 <= 0)

    bp = p - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    mask_b = (d3 >= 0) & (d4 <= d3)

    cp = p - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    mask_c = (d6 >= 0) & (d5 <= d6)

    vc = d1 * d4 - d3 * d2
    mask_ab_edge = (vc <= 0) & (d1 >= 0) & (d3 <= 0) & ~(mask_a | mask_b)

    vb = d5 * d2 - d1 * d6
    mask_ac_edge = (vb <= 0) & (d2 >= 0) & (d6 <= 0) & ~(mask_a | mask_c)

    va = d3 * d6 - d5 * d4
    mask_bc_edge = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0) & ~(mask_b | mask_c)

    denom = va + vb + vc
    # Default barycentric (inside-triangle case)
    safe = np.where(denom != 0, denom, 1.0)
    v = vb / safe
    w = vc / safe
    closest = a + v[:, None] * ab + w[:, None] * ac

    # Override per region
    # A
    closest = np.where(mask_a[:, None], a, closest)
    # B
    closest = np.where(mask_b[:, None], b, closest)
    # C
    closest = np.where(mask_c[:, None], c, closest)
    # AB edge: closest = a + (d1/(d1-d3)) * ab
    t_ab = np.where((d1 - d3) != 0, d1 / np.where((d1 - d3) != 0, (d1 - d3), 1.0), 0.0)
    closest_ab = a + t_ab[:, None] * ab
    closest = np.where(mask_ab_edge[:, None], closest_ab, closest)
    # AC edge: closest = a + (d2/(d2-d6)) * ac
    t_ac = np.where((d2 - d6) != 0, d2 / np.where((d2 - d6) != 0, (d2 - d6), 1.0), 0.0)
    closest_ac = a + t_ac[:, None] * ac
    closest = np.where(mask_ac_edge[:, None], closest_ac, closest)
    # BC edge: closest = b + t * (c - b)
    denom_bc = (d4 - d3) + (d5 - d6)
    t_bc = np.where(denom_bc != 0, (d4 - d3) / np.where(denom_bc != 0, denom_bc, 1.0), 0.0)
    closest_bc = b + t_bc[:, None] * (c - b)
    closest = np.where(mask_bc_edge[:, None], closest_bc, closest)

    return closest


def project_to_mesh(points: np.ndarray, mesh: MeshIndex, k: int = 8) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each point, find the closest point on the mesh surface.

    Returns:
        projections: (M, 3) closest points
        tri_indices: (M,) which triangle each projection lies on
        normals:     (M, 3) interpolated surface normal at the projection
    """
    points = np.asarray(points, dtype=np.float64)
    M = points.shape[0]
    # Get k nearest triangle centroids per point as candidates
    _, cand = mesh.kdtree.query(points, k=k)
    if cand.ndim == 1:
        cand = cand[:, None]

    best_dist2 = np.full(M, np.inf)
    best_proj = np.zeros((M, 3))
    best_tri = np.zeros(M, dtype=np.int64)

    for j in range(cand.shape[1]):
        tri_idx = cand[:, j]
        tri = mesh.tris[tri_idx]
        a = mesh.verts[tri[:, 0]]
        b = mesh.verts[tri[:, 1]]
        c = mesh.verts[tri[:, 2]]
        proj = _closest_point_on_triangle(points, a, b, c)
        d2 = np.einsum("ij,ij->i", proj - points, proj - points)
        better = d2 < best_dist2
        best_dist2 = np.where(better, d2, best_dist2)
        best_proj = np.where(better[:, None], proj, best_proj)
        best_tri = np.where(better, tri_idx, best_tri)

    # Interpolated normal at projection: use triangle face normal (good enough for offset rotation)
    normals = mesh.tri_normals[best_tri]
    return best_proj, best_tri, normals


def _build_surface_frame(normals: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Pick two orthonormal tangent axes given unit normals (M, 3).

    Uses a robust 'world up' fallback to avoid the degenerate case where
    normal == reference axis.
    """
    normals = np.asarray(normals, dtype=np.float64)
    # A degenerate source triangle yields a ZERO normal -> the cross products
    # below collapse to zero tangents (norm-clip keeps them zero), so the
    # offset's tangential components get projected onto a zero frame and are
    # silently dropped, mis-placing the vert. Substitute world-Z for any ~zero
    # normal so the frame stays orthonormal (a degenerate tri has no meaningful
    # normal anyway). No-op for well-formed meshes.
    if normals.size:
        degenerate = np.linalg.norm(normals, axis=1) < 1e-9
        if degenerate.any():
            normals = normals.copy()
            normals[degenerate] = np.array([0.0, 0.0, 1.0])
    ref = np.tile(np.array([0.0, 0.0, 1.0]), (normals.shape[0], 1))
    # When normal is too close to world-Z, fall back to world-X
    parallel = np.abs(np.einsum("ij,ij->i", normals, ref)) > 0.95
    ref[parallel] = np.array([1.0, 0.0, 0.0])
    t1 = np.cross(normals, ref)
    t1 /= np.linalg.norm(t1, axis=1, keepdims=True).clip(min=1e-12)
    t2 = np.cross(normals, t1)
    return t1, t2


def compute_deformation(
    armor_verts: np.ndarray,
    src: MeshIndex,
    tgt: MeshIndex,
    candidates_k: int = 8,
) -> np.ndarray:
    """Returns the per-vertex displacement to move armor from src body to tgt body.

    armor_verts: (N, 3)
    returns: (N, 3)
    """
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    src_proj, _src_tri, src_n = project_to_mesh(armor_verts, src, k=candidates_k)

    # Local offset in CBBE surface frame
    offset = armor_verts - src_proj
    t1_src, t2_src = _build_surface_frame(src_n)
    off_n = np.einsum("ij,ij->i", offset, src_n)
    off_t1 = np.einsum("ij,ij->i", offset, t1_src)
    off_t2 = np.einsum("ij,ij->i", offset, t2_src)

    # Find corresponding UBE surface point
    tgt_proj, _tgt_tri, tgt_n = project_to_mesh(src_proj, tgt, k=candidates_k)
    t1_tgt, t2_tgt = _build_surface_frame(tgt_n)

    new_offset = (
        off_n[:, None] * tgt_n
        + off_t1[:, None] * t1_tgt
        + off_t2[:, None] * t2_tgt
    )
    new_verts = tgt_proj + new_offset
    return (new_verts - armor_verts).astype(np.float32)
