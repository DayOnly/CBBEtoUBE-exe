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

"""ONE target offset per vertex, solved once, applied once.  #unified-offset

WHY THIS EXISTS -- the four clearance passes are not four tunings of one idea,
they are three DIFFERENT OPERATORS that fight by type:

    inflate_armor_outward         ADDITIVE  offset += magnitude * falloff
    conform_to_source_standoff    ABSOLUTE  offset := source offset (capped)
    clear_armor_outside_body      FLOOR     offset := max(offset, clear)
    _inflate_cloth_over_bust_butt FLOOR     (soft-cloth variant)

An ADDITIVE push followed by an ABSOLUTE target means the absolute one wins
outright -- whatever the additive pass added is simply overwritten. That is not
a tuning conflict to be balanced, it is a type conflict, and it is exactly what
the displacement-survival trace measured over 38 shapes: `inflate` CANCELLED by
`conform` on 12 of 32 instances, every cancellation naming conform, at -1.06 to
-5.36; and `conform` itself never surviving above 0.847 on any shape.

Read as a constraint instead, the disagreement disappears. Every pass is really
asserting one of three things about the FINAL offset:

    target   where the garment should sit  (the author's drape -- conform)
    floor    how close it may ever get     (clearance for a body that grows
                                            at runtime -- inflate, anti-poke)
    ceiling  how far off it may sit        (over-inflation, which clipping
                                            cannot see: 3u off scores 0.0%)

    offset_final = clip(target, floor, ceiling)

`inflate`'s job -- headroom for a morphing body -- is a FLOOR, not a push. Stated
that way it cannot be cancelled by a later absolute target, because it is part
of the same solve.

FOUR PROPERTIES THIS BUYS, each tied to a logged defect:

1. ORDER-INDEPENDENT. One field cannot self-conflict, so no pass silently
   overwrites another and no ordering decides the winner.
2. FEATHERED ONCE. Sequential feathered pushes compound distortion -- the
   layer-ride / spiky-vert class. Here the MOVE field is smoothed once, before
   anything is applied.
3. ONE BUDGET PER VERT instead of ANTIPOKE_FLAT_CLEAR, ANTIPOKE_BUST_CLEAR,
   SMP_ANTIPOKE_MAX_PUSH, the inflate magnitude, the soft-cloth clears and the
   rear/calf/thigh standoffs -- each of which today
   carries a separately measured optimum that is invisible to the others.
4. PINNING IS A WEIGHT, not a restore. `_physics_chain_nowarp_blend` currently
   moves chain verts and then puts them back; here a pinned vert has weight 0
   and is never moved, so nothing is done and discarded.

NOT IN SCOPE. This solves POSITION only. Weight/skinning passes stay separate --
conflating the two domains is the `_shape_has_hdt_smp_rigging` bug that cost a
session. `groove_smooth` also stays: it is a smoothing operator, not a target.
"""
from __future__ import annotations

import numpy as np


def current_offset(verts, body_verts, body_normals, tree=None):
    """Signed offset of each garment vert from the body, along the body normal.

    Nearest body VERTEX, projected on that vertex's outward normal -- the same
    basis `conform_to_source_standoff` and `clear_armor_outside_body` already
    use, so a target expressed here means what they mean.

    Returns (offset, nearest_index, distance).

    CAUTION for callers measuring rather than solving: for a vert whose nearest
    body vertex lies off to the side, `v - body[j]` is largely TANGENTIAL and
    this projection collapses toward zero however far away the vert really is.
    That is fine for CLEARANCE (we only care about the normal component) and
    wrong for "how far is this garment from the body" -- use a surface cast for
    that. Reading a median drape of exactly 0.000 is the tell.
    """
    from scipy.spatial import cKDTree
    V = np.asarray(verts, np.float64)
    B = np.asarray(body_verts, np.float64)
    N = np.asarray(body_normals, np.float64)
    N = N / np.clip(np.linalg.norm(N, axis=1, keepdims=True), 1e-9, None)
    if tree is None:
        tree = cKDTree(B)
    d, j = tree.query(V, k=1)
    return np.einsum("ij,ij->i", V - B[j], N[j]), j, d


def solve(offset, *, target=None, floor=None, ceiling=None):
    """The whole disagreement, as one clip.

    `target` defaults to the CURRENT offset, which is what "no opinion" means --
    a vert nobody asserts anything about does not move. Each of `target`,
    `floor`, `ceiling` may be a scalar or a per-vert array; None means absent.

    Order of application is deliberate and is NOT symmetric: the FLOOR wins over
    the ceiling. Clearance exists to stop the body coming through the garment,
    and a garment slightly too far off the body is a cosmetic complaint while
    skin through steel is the defect that gets reported. Where the two disagree
    the floor is the one that must hold.
    """
    o = np.asarray(offset, np.float64)
    t = o if target is None else np.broadcast_to(
        np.asarray(target, np.float64), o.shape).astype(np.float64)
    out = t.copy()
    if ceiling is not None:
        out = np.minimum(out, np.broadcast_to(
            np.asarray(ceiling, np.float64), o.shape))
    if floor is not None:
        out = np.maximum(out, np.broadcast_to(
            np.asarray(floor, np.float64), o.shape))
    return out


def feather(move, tris, iters=2, weight=None):
    """Smooth the MOVE field over the mesh -- once, before anything is applied.

    Smoothing the move rather than the positions is what keeps this from
    reopening a poke: a vert's neighbours can only pull its move toward theirs,
    and every neighbour's move already satisfies the same solve.

    A pinned vert (weight 0) neither moves nor drags its neighbours' moves,
    which is what stops a pinned chain from flattening the cloth beside it.
    """
    m = np.asarray(move, np.float64).copy()
    if tris is None or iters <= 0 or not len(m):
        return m
    T = np.asarray(tris, np.int64).reshape(-1, 3)
    if not len(T):
        return m
    w = (np.ones(len(m)) if weight is None
         else np.clip(np.asarray(weight, np.float64), 0.0, 1.0))
    e = np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    e = np.vstack([e, e[:, ::-1]])
    for _ in range(int(iters)):
        acc = np.zeros(len(m))
        cnt = np.zeros(len(m))
        np.add.at(acc, e[:, 0], m[e[:, 1]] * w[e[:, 1]])
        np.add.at(cnt, e[:, 0], w[e[:, 1]])
        avg = np.where(cnt > 0, acc / np.maximum(cnt, 1e-9), m)
        m = np.where(cnt > 0, 0.5 * (m + avg), m)
    return m


def apply(verts, body_normals, nearest, offset, target, *,
          max_move=None, weight=None, tris=None, feather_iters=2):
    """Move each vert ONCE, along the body normal, to its solved offset.

    `weight` is the pin (property 4): 0 leaves a vert exactly where it is, so a
    simulated/chain vert is never moved and then restored. `max_move` is the
    single budget (property 3).

    The cap is applied BEFORE the feather so a clamped vert cannot be dragged
    past its budget by a neighbour, and the feather runs on the MOVE so a vert
    that needed nothing still moves smoothly with its neighbourhood instead of
    leaving a step.
    """
    V = np.asarray(verts, np.float64)
    N = np.asarray(body_normals, np.float64)
    N = N / np.clip(np.linalg.norm(N, axis=1, keepdims=True), 1e-9, None)
    move = np.asarray(target, np.float64) - np.asarray(offset, np.float64)
    if max_move is not None:
        move = np.clip(move, -float(max_move), float(max_move))
    if weight is not None:
        move = move * np.clip(np.asarray(weight, np.float64), 0.0, 1.0)
    move = feather(move, tris, feather_iters, weight)
    if weight is not None:
        # Re-assert the pin AFTER the feather: smoothing must not leak motion
        # into a vert the caller pinned, or the chain drifts off its bones.
        move = move * np.clip(np.asarray(weight, np.float64), 0.0, 1.0)
    return V + N[np.asarray(nearest, np.int64)] * move[:, None]
