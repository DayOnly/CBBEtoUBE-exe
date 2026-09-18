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

"""Did #chain-body-shift actually move the cloth? A/B two converted NIFs.

WHY THIS EXISTS. The pass moves BONES, not vertices. `shape.verts` is
BIT-IDENTICAL whether it fired or not, so every ordinary clip/standoff test
reports nothing and the pass reads as inert. That has already caused the shift
to be judged twice from numbers that could not see it.

What the engine draws is the SKINNED position:

    rendered(v) = sum_b  w(v,b) * BoneGlobal[b] . SkinToBone[b] . v

Move a bone and `rendered` moves even though `v` did not. This computes that for
both files and reports the difference, split by whether a vertex is chain-driven
(the verts the shift is FOR) or body-driven (which must not move).

    python scripts/analysis/verify_chain_shift.py <with_shift.nif> <without.nif>

Both files must be the same piece converted two ways -- same shapes, same vert
counts. The absolute frame does not matter: chain nodes are parent-local and
skeleton bones are (0,0,0) placeholders, so `global_transform` returns
pelvis-relative coordinates -- but the frame is the SAME on both sides, so it
cancels in the difference. Do not read the absolute numbers as world positions.
"""
import sys
from pathlib import Path

# Canonical spelling so test_analysis_repo_root can verify the level.
_REPO = Path(__file__).resolve().parent.parent.parent
REPO = _REPO
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO))

import numpy as np                                     # noqa: E402
from src import nif_convert as nc                      # noqa: E402


def _mat(xf):
    """TransformBuf -> 4x4. rotation is row-major 3x3, scale uniform."""
    m = np.eye(4)
    r = np.array([[float(c) for c in row] for row in xf.rotation], float)
    m[:3, :3] = r * float(getattr(xf, "scale", 1.0) or 1.0)
    m[:3, 3] = [float(c) for c in xf.translation]
    return m


def _bone_global(nif, name, cache):
    if name in cache:
        return cache[name]
    node = nif.nodes.get(name)
    cache[name] = _mat(node.global_transform) if node is not None else np.eye(4)
    return cache[name]


def rendered(nif, shape):
    """Skinned bind position per vertex, plus each vertex's chain-weight share."""
    v = np.array(shape.verts, np.float64)
    n = len(v)
    vh = np.c_[v, np.ones(n)]
    out = np.zeros((n, 3))
    tot = np.zeros(n)
    chain_w = np.zeros(n)
    cache = {}
    for bone, pairs in (shape.bone_weights or {}).items():
        try:
            stb = _mat(shape.get_shape_skin_to_bone(bone))
        except Exception:
            continue
        M = _bone_global(nif, bone, cache) @ stb
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        idx = np.array([int(i) for i, _w in pl], dtype=np.int64)
        w = np.array([float(x) for _i, x in pl], dtype=np.float64)
        ok = (idx >= 0) & (idx < n)
        idx, w = idx[ok], w[ok]
        if not len(idx):
            continue
        out[idx] += (vh[idx] @ M.T)[:, :3] * w[:, None]
        tot[idx] += w
        if not nc._is_skeleton_bone(bone) and not nc._is_soft_body_physics_bone(bone):
            chain_w[idx] += w
    good = tot > 1e-9
    out[good] /= tot[good][:, None]
    return out, np.clip(chain_w, 0.0, 1.0), good


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    N = nc._pynifly().NifFile
    a_nif, b_nif = N(sys.argv[1]), N(sys.argv[2])
    A = {s.name: s for s in a_nif.shapes}
    B = {s.name: s for s in b_nif.shapes}

    print(f"A (with shift) : {sys.argv[1]}")
    print(f"B (without)    : {sys.argv[2]}\n")
    print(f"{'shape':<24}{'verts':>7}{'chain':>8}"
          f"{'rendered move: chain':>24}{'body-driven':>14}")
    print("-" * 78)
    any_move = False
    for name, sa in A.items():
        sb = B.get(name)
        if sb is None or len(sa.verts) != len(sb.verts):
            print(f"{name[:23]:<24}{'-':>7}  not comparable")
            continue
        ra, fa, ga = rendered(a_nif, sa)
        rb, _fb, gb = rendered(b_nif, sb)
        ok = ga & gb
        d = np.linalg.norm(ra - rb, axis=1)
        chain = ok & (fa > 0.5)
        body = ok & (fa <= 0.5)
        cm = float(d[chain].mean()) if chain.any() else 0.0
        cx = float(d[chain].max()) if chain.any() else 0.0
        bm = float(d[body].max()) if body.any() else 0.0
        if cx > 1e-4:
            any_move = True
        print(f"{name[:23]:<24}{len(d):>7}{int(chain.sum()):>8}"
              f"{cm:>14.4f}u max{cx:>6.3f}{bm:>13.4f}u")

    # STATED, not implied: verts identical + rendered identical means the pass
    # did not fire, NOT that it fired and did nothing.
    print()
    if not any_move:
        print("NO chain-driven vertex moved. Either the pass was off in BOTH "
              "builds, or no chain was eligible -- check the chain_shift "
              "records in standoff_audit.jsonl to tell those apart.")
    else:
        print("Chain-driven cloth MOVED; body-driven verts should read ~0 "
              "above (they are not what the shift is for).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
