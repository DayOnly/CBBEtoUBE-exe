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

"""#pose-clearance -- how much clearance each armour vertex LOSES when the actor moves.

WHY THIS IS PER-ARMOUR AND NOT A CACHED BODY MAP. The obvious design is a per-body
"pose amplitude" map -- how far each body vertex travels -- cached once and reused,
mirroring `morph_amplitude` and `jiggle_amplitude`. It was built and it FAILED its
validation: the vertices that actually lose coverage are the ones the body deforms
LEAST (AUC 0.19-0.47, i.e. backwards, and on breast cases the failing vertices had
~0.00u of body deformation). Failure is RELATIVE motion, and a body-only map can only
see one side of it -- the garment was moving away from a body that was barely moving.

So the demand is computed from BOTH meshes posed together. That cannot be precomputed
per body, but it is affordable: the POSED BODY is identical for every armour in a run
and is cached here, leaving only the garment to pose per piece.

WHAT IT RETURNS. Per armour vertex, the worst clearance DEFICIT over the pose set:
how much closer the body gets under some pose than it is at bind. Adding that at bind
pre-pays what the pose will spend. Zero where nothing closes in, so a garment that
already survives every pose is untouched -- unlike a uniform outward push, which buys
poke-through resistance by trading gaps at the garment's edges (measured: it made one
armour's belly WORSE, 3.5% -> 3.9%).

THE LEVER IS SOUND, only the targeting was wrong. A uniform push cut breast exposure
11.0% -> 2.5% and butt 14.7% -> 6.9% under pose; this aims the same push.

STATUS 2026-07-28: NOT READY, and the reason is recorded here rather than rediscovered.
The demand it computes is far too BROAD -- it moves 35-74% of a garment's vertices by
a mean of 0.35-1.36u, with the cap saturated. That is the bagginess this term exists to
avoid, and a uniform push is rejected outright for exactly that look.

The cause is the demand SIGNAL, not the plumbing. "Clearance along the body normal
fell" fires wherever the two surfaces slide TANGENTIALLY across each other, which a
sprint or a crouch does over most of the torso, while real poke-through is only 3-15%
of a region's covered vertices. Fixing the correspondence (measure against the SAME
body vertex at bind and at pose, rather than a fresh nearest-neighbour lookup that
swaps reference points under rotation) was necessary and is kept -- but it did not
narrow the demand, which is what ruled the signal out rather than the plumbing.

NEXT: derive the demand from EXPOSURE, not clearance. Take the body vertices that are
covered at bind and exposed under a pose -- the harness's own measure, sparse by
construction -- and ask how far the nearby garment must move to re-cover them. That
optimises the quantity actually cared about instead of a proxy that is loose in
exactly the poses of interest.
"""
from __future__ import annotations

import os

import numpy as np

# Poses that matter for clearance, kept SMALL: each one costs a garment posing per
# armour. Chosen as the dominant failure pose per region measured by the multipose
# harness -- arms forward (upper chest), spine twist (breast), sprint (belly/breast),
# crouch (butt/thigh), deep stride (thigh).
POSE_SET_CLEARANCE = {
    "spine twist": [("NPC Spine [Spn0]", 'z', 10.0), ("NPC Spine1 [Spn1]", 'z', 12.0),
                    ("NPC Spine2 [Spn2]", 'z', 12.0)],
    "arms forward": [("NPC L UpperArm [LUar]", 'x', 40.0),
                     ("NPC R UpperArm [RUar]", 'x', 40.0)],
    "sprint": [("NPC Spine [Spn0]", 'x', 18.0), ("NPC Spine1 [Spn1]", 'x', 15.0),
               ("NPC Spine2 [Spn2]", 'x', 10.0),
               ("NPC L Thigh [LThg]", 'x', 40.0), ("NPC R Thigh [RThg]", 'x', -28.0)],
    "crouch": [("NPC L Thigh [LThg]", 'x', 40.0), ("NPC R Thigh [RThg]", 'x', 40.0),
               ("NPC L Calf [LClf]", 'x', 45.0), ("NPC R Calf [RClf]", 'x', 45.0)],
}

# Default OFF. Clearance work on this project has a long history of trading one flaw
# for another, and this needs calibrating against the pose census before it ships on.
POSE_CLEARANCE_ENABLED = os.environ.get(
    "CBBE2UBE_POSE_CLEARANCE", "").strip().lower() in ("1", "true", "yes", "on")
# Ceiling on what a pose may demand. Uncapped, a deep crouch would ask for several
# units on the thigh and inflate the garment into the bagginess failure mode.
POSE_CLEARANCE_MAX = float(
    os.environ.get("CBBE2UBE_POSE_CLEARANCE_MAX", "").strip() or "1.0")
# Fraction of the measured deficit to grant. 1.0 pays it in full; less is a hedge
# against the deficit being an overestimate at a single worst vertex.
POSE_CLEARANCE_GAIN = float(
    os.environ.get("CBBE2UBE_POSE_CLEARANCE_GAIN", "").strip() or "1.0")

_POSED_BODY_CACHE: dict = {}


def rot_matrix(axis, deg):
    a = np.deg2rad(float(deg))
    c, s = np.cos(a), np.sin(a)
    M = np.eye(4)
    if axis == 'x':
        M[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
    elif axis == 'y':
        M[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    else:
        M[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return M


def descendants(parents, root):
    """Every node under `root`. MUST come from the SKELETON's hierarchy: a mesh's own
    bone list is FLAT, so a calf never resolves as a child of a thigh and the pose
    silently does nothing below the joint."""
    kids: dict = {}
    for c, p in parents.items():
        kids.setdefault(p, []).append(c)
    out, stack = set(), [root]
    while stack:
        b = stack.pop()
        if b in out:
            continue
        out.add(b)
        stack.extend(kids.get(b, []))
    return out


def build_pose(parents, origins, specs):
    """specs ordered ROOT -> LEAF so a knee bend composes on top of a hip swing."""
    acc: dict = {}
    for bone, axis, deg in specs:
        if bone not in origins:
            continue
        cur = acc.get(bone, np.eye(4))
        pivot = (cur @ np.append(origins[bone], 1.0))[:3]
        T = np.eye(4)
        T[:3, 3] = pivot
        Ti = np.eye(4)
        Ti[:3, 3] = -pivot
        M = T @ rot_matrix(axis, deg) @ Ti
        for b in descendants(parents, bone):
            acc[b] = M @ acc.get(b, np.eye(4))
    return acc


def apply_pose(verts, weights, acc):
    """Linear blend skinning. With the mesh at bind, bindWorld == inv(STB), so a posed
    vertex is sum_b w_b * (acc_b @ v); identity reproduces the bind mesh exactly."""
    v = np.asarray(verts, dtype=np.float64)
    if not acc:
        return v.copy()
    out = np.zeros_like(v)
    tot = np.zeros(len(v), dtype=np.float64)
    for bone, (w, _origin) in weights.items():
        M = acc.get(bone)
        if M is None:
            continue
        m = w > 1e-6
        if not m.any():
            continue
        out[m] += (v[m] @ M[:3, :3].T + M[:3, 3]) * w[m, None]
        tot[m] += w[m]
    rest = tot < 1.0 - 1e-6
    out[rest] += v[rest] * (1.0 - tot[rest])[:, None]
    return out


def _nearest_clearance(armor_v, body_v, body_n, tree):
    """Signed clearance of each armour vert from the body, along the BODY normal at
    its nearest body vertex. Positive = armour outside the body."""
    _d, i = tree.query(armor_v, k=1)
    return np.einsum("ij,ij->i", armor_v - body_v[i], body_n[i]), i


def pose_clearance_demand(armor_verts, armor_weights, body_verts, body_weights,
                          body_normals, parents, origins, poses=None,
                          gain=None, cap=None, body_key=None):
    """Per armour vertex: the worst clearance DEFICIT over the pose set, in units.

    deficit = max over poses of (clearance_at_bind - clearance_at_pose), floored at 0.
    Granting it at bind pre-pays what the pose will consume. Zero where nothing closes
    in, so garments that already survive every pose are left alone -- which is the
    whole difference from a uniform push.

    The POSED BODY is the same for every armour in a run, so it is cached on
    `body_key`; only the garment is posed per piece.
    """
    from scipy.spatial import cKDTree

    av = np.asarray(armor_verts, dtype=np.float64)
    bv = np.asarray(body_verts, dtype=np.float64)
    bn = np.asarray(body_normals, dtype=np.float64)
    gain = POSE_CLEARANCE_GAIN if gain is None else float(gain)
    cap = POSE_CLEARANCE_MAX if cap is None else float(cap)
    if not len(av) or not len(bv):
        return np.zeros(len(av), dtype=np.float64)

    # CORRESPONDENCE IS FIXED AT BIND and reused for every pose. Re-running a nearest
    # neighbour search on the POSED body compares each armour vertex against a
    # DIFFERENT body vertex, so a torso that merely rotates reads as "closing in" and
    # the demand smears over the whole garment: measured, that moved 35-61% of every
    # garment by 0.4-1.3u with the cap saturated -- the bagginess this term exists to
    # avoid. Tracking the same material point makes the deficit a real approach.
    base, corr = _nearest_clearance(av, bv, bn, cKDTree(bv))
    worst = np.zeros(len(av), dtype=np.float64)
    for name, specs in (poses or POSE_SET_CLEARANCE).items():
        acc = build_pose(parents, origins, specs)
        if not acc:
            continue
        ck = (body_key, name)
        if body_key is not None and ck in _POSED_BODY_CACHE:
            pbv, pbn, ptree = _POSED_BODY_CACHE[ck]
        else:
            pbv = apply_pose(bv, body_weights, acc)
            # Re-derive normals by rotating each vertex's normal with its DOMINANT
            # bone: recomputing from triangles would need the body's topology, and
            # the dominant bone's rotation is what the surface actually follows.
            pbn = bn.copy()
            for bone, (w, _o) in body_weights.items():
                M = acc.get(bone)
                if M is None:
                    continue
                m = w > 0.5
                if m.any():
                    pbn[m] = bn[m] @ M[:3, :3].T
            nl = np.linalg.norm(pbn, axis=1)
            pbn = pbn / np.where(nl > 1e-9, nl, 1.0)[:, None]
            ptree = cKDTree(pbv)
            if body_key is not None:
                _POSED_BODY_CACHE[ck] = (pbv, pbn, ptree)
        pav = apply_pose(av, armor_weights, acc)
        # Same body vertex as at bind -- a material point, not a fresh lookup.
        cur = np.einsum("ij,ij->i", pav - pbv[corr], pbn[corr])
        np.maximum(worst, base - cur, out=worst)
    return np.clip(worst * gain, 0.0, cap)
