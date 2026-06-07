"""Smoke tests for the geometry layer.

These don't need pynifly — they exercise numpy/scipy paths only.

Run: python -m pytest tests/  (or: python tests/test_correspondence.py)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.correspondence import MeshIndex, compute_deformation, project_to_mesh
from src.weights import transfer_weights


def _icosphere(radius=1.0, subdivisions=2):
    """Tiny icosphere builder so tests don't need an external mesh."""
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = np.array([
        [-1,  t,  0], [ 1,  t,  0], [-1, -t,  0], [ 1, -t,  0],
        [ 0, -1,  t], [ 0,  1,  t], [ 0, -1, -t], [ 0,  1, -t],
        [ t,  0, -1], [ t,  0,  1], [-t,  0, -1], [-t,  0,  1],
    ], dtype=np.float64)
    verts /= np.linalg.norm(verts, axis=1, keepdims=True)
    tris = np.array([
        [0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],
        [1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],
        [3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],
        [4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1],
    ], dtype=np.int64)
    for _ in range(subdivisions):
        cache = {}
        new_tris = []
        verts_list = verts.tolist()
        def midpoint(i, j):
            key = (min(i,j), max(i,j))
            if key in cache: return cache[key]
            mp = (verts[i] + verts[j]) / 2.0
            mp = mp / np.linalg.norm(mp)
            verts_list.append(mp.tolist())
            idx = len(verts_list) - 1
            cache[key] = idx
            return idx
        for a, b, c in tris:
            ab = midpoint(a, b); bc = midpoint(b, c); ca = midpoint(c, a)
            new_tris.extend([[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]])
        verts = np.array(verts_list)
        tris = np.array(new_tris, dtype=np.int64)
    return verts * radius, tris


def test_projection_on_sphere():
    """Projecting a point outside the sphere lands on the surface, the
    projection direction equals the surface normal."""
    verts, tris = _icosphere(radius=1.0, subdivisions=3)
    mesh = MeshIndex.build(verts, tris)

    # 50 random points well outside the sphere
    rng = np.random.default_rng(42)
    raw = rng.standard_normal((50, 3))
    pts = raw / np.linalg.norm(raw, axis=1, keepdims=True) * 2.5

    proj, _, normals = project_to_mesh(pts, mesh, k=12)

    # Projections should land near the surface (within tessellation tolerance)
    dist_from_origin = np.linalg.norm(proj, axis=1)
    assert np.allclose(dist_from_origin, 1.0, atol=0.05), dist_from_origin

    # The line from origin through proj is the surface normal direction
    ideal_normal = proj / dist_from_origin[:, None]
    cos = np.einsum("ij,ij->i", ideal_normal, normals)
    assert (cos > 0.95).all(), f"normals misaligned, min cos = {cos.min()}"


def test_deformation_sphere_to_scaled_sphere():
    """Armor a fixed offset above a unit sphere should be lifted to the same
    fixed offset above a sphere scaled to radius 1.5."""
    src_verts, src_tris = _icosphere(radius=1.0, subdivisions=3)
    tgt_verts, tgt_tris = _icosphere(radius=1.5, subdivisions=3)

    src = MeshIndex.build(src_verts, src_tris)
    tgt = MeshIndex.build(tgt_verts, tgt_tris)

    # Armor: 100 points on a sphere of radius 1.1 (offset 0.1 above CBBE)
    rng = np.random.default_rng(0)
    dirs = rng.standard_normal((100, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    armor = dirs * 1.1

    disp = compute_deformation(armor, src, tgt)
    new_armor = armor + disp

    # Expect new armor near r=1.6 (target radius 1.5 + offset 0.1)
    r = np.linalg.norm(new_armor, axis=1)
    assert np.allclose(r, 1.6, atol=0.05), f"radii off: mean={r.mean():.3f}, std={r.std():.3f}"


def test_weight_transfer_renormalizes():
    """Transferred weights for each vertex must sum to ~1."""
    # 50 ref body verts on a small grid, with two fake bones
    rng = np.random.default_rng(1)
    ref = rng.standard_normal((50, 3))
    bone_weights = {
        "Bone.A": np.column_stack([np.arange(50, dtype=np.float64), rng.random(50)]),
        "Bone.B": np.column_stack([np.arange(50, dtype=np.float64), rng.random(50)]),
    }
    # Renormalize ref weights so they sum to 1 per vertex
    a = bone_weights["Bone.A"][:, 1]
    b = bone_weights["Bone.B"][:, 1]
    s = a + b
    bone_weights["Bone.A"][:, 1] = a / s
    bone_weights["Bone.B"][:, 1] = b / s

    armor_verts = rng.standard_normal((20, 3))
    out = transfer_weights(armor_verts, ref, bone_weights, k=4)

    # Reconstruct per-vertex sum
    sums = np.zeros(20)
    for bn, pairs in out.items():
        idx = pairs[:, 0].astype(int)
        sums[idx] += pairs[:, 1]
    assert np.allclose(sums, 1.0, atol=1e-9), sums


if __name__ == "__main__":
    test_projection_on_sphere()
    test_deformation_sphere_to_scaled_sphere()
    test_weight_transfer_renormalizes()
    print("OK")
