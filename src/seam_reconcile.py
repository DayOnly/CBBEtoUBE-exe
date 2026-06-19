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

"""Cross-shape SEAM RECONCILIATION.

The converter warps/inflates each shape of an armor INDEPENDENTLY (parallel
per-shape jobs, adaptive morph-aware clearance keyed to the nearest body vert).
When two shapes of one armor are WELDED at a shared seam (their boundary verts
coincide in the source) but cover regions with different clearance demand -- e.g.
a torso 'top' welded at the waist to a 'skirt/pants' shape that covers the high-
morph belly/hip/butt -- the two seam edges get pushed out by DIFFERENT amounts
and the seam opens into a visible see-through gap (measured on one cuirass: a
source gap of ~0.7u opening to ~1.7u mean, 3.2u peak, across the front waist).

This pass closes such seams. It finds vert pairs from DIFFERENT shapes that were
coincident in the SOURCE, and where that welded group DIVERGED in the output it
welds the group shut by moving every member onto the MOST-OUTWARD member (max
body distance). That is PUSH-OUT ONLY -- a seam vert only ever moves away from
the body, never toward it -- so it can never introduce body poke-through, matching
the converter's standing 'push-out only; never pull cloth in' rule. It touches
only armor geometry; the body is never modified.

Pure (arrays in, arrays out) so it is unit-testable without pynifly. The on-disk
NIF driver lives in scripts/fix_cross_shape_seams.py; the eventual converter hook
calls reconcile_seam_groups on the finished per-shape verts.
"""
from __future__ import annotations

import numpy as np

# Source gap (game units) below which two cross-shape verts count as one welded
# seam point. Daedric cuirass seam boundary pairs sat at 0.3-0.7u in source.
SEAM_COINCIDE = 0.6
# Output spread (max pairwise, game units) above which a welded group counts as
# OPENED and is reconciled. Below this the seam is still effectively closed.
SEAM_MIN_SPREAD = 0.4


class _DSU:
    """Tiny union-find over vertex slots."""
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def reconcile_seam_groups(out_verts, src_verts, body_verts=None, *,
                          coincide: float = SEAM_COINCIDE,
                          min_spread: float = SEAM_MIN_SPREAD):
    """Close cross-shape seams opened by differing per-shape inflation.

    Args:
        out_verts: {shape_name -> (N,3)} OUTPUT verts in a COMMON (body) space.
        src_verts: {shape_name -> (N,3)} SOURCE verts in that same space; same
                   index per shape (the converter preserves vert count/order).
        body_verts: (V,3) body verts; the most-outward member is the one farthest
                   from the body. If None, falls back to the member that moved
                   farthest from its source position (inflation is outward).
        coincide:  source gap to treat two cross-shape verts as one seam point.
        min_spread: output spread above which a welded group is reconciled.

    Returns (new_out_verts dict (copies; inputs untouched), stats dict).
    """
    from scipy.spatial import cKDTree
    names = [n for n in out_verts if n in src_verts]
    chunks, owner = [], []
    for si, nm in enumerate(names):
        sv = np.asarray(src_verts[nm], dtype=np.float64)
        chunks.append(sv)
        owner.extend((si, li) for li in range(len(sv)))
    new_out = {nm: np.array(out_verts[nm], dtype=np.float64).copy() for nm in names}
    stats = {"groups_welded": 0, "verts_moved": 0, "max_close": 0.0,
             "shapes": len(names)}
    if len(chunks) < 2:
        return new_out, stats
    S = np.concatenate(chunks, axis=0)
    owner = np.asarray(owner, dtype=np.int64)

    dsu = _DSU(len(S))
    pairs = cKDTree(S).query_pairs(coincide, output_type="ndarray")
    for a, b in pairs:
        if owner[a, 0] != owner[b, 0]:        # weld only ACROSS shapes
            dsu.union(int(a), int(b))

    btree = cKDTree(np.asarray(body_verts, dtype=np.float64)) \
        if body_verts is not None else None

    groups: dict[int, list[int]] = {}
    for gi in range(len(S)):
        groups.setdefault(dsu.find(gi), []).append(gi)

    for members in groups.values():
        if len(members) < 2:
            continue
        if len({owner[m, 0] for m in members}) < 2:   # must span >=2 shapes
            continue
        opos = np.array([new_out[names[owner[m, 0]]][owner[m, 1]]
                         for m in members])
        # spread = max pairwise distance among the group's output positions
        spread = float(np.linalg.norm(opos[:, None, :] - opos[None, :, :],
                                      axis=2).max())
        if spread <= min_spread:
            continue
        if btree is not None:
            bd, _ = btree.query(opos)
            tgt = opos[int(np.argmax(bd))]            # most outward = farthest
        else:
            sdisp = [float(np.linalg.norm(
                new_out[names[owner[m, 0]]][owner[m, 1]] - S[m]))
                for m in members]
            tgt = opos[int(np.argmax(sdisp))]         # moved farthest = outward
        for m in members:
            nm, li = names[owner[m, 0]], owner[m, 1]
            mv = float(np.linalg.norm(new_out[nm][li] - tgt))
            if mv > 1e-9:
                new_out[nm][li] = tgt
                stats["verts_moved"] += 1
                stats["max_close"] = max(stats["max_close"], mv)
        stats["groups_welded"] += 1
    return new_out, stats
