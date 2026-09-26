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

"""Where simulated cloth RESTS against the body, through the node tree and the skin.

WHY THIS EXISTS. The first "cloth at rest inside the UBE body" numbers read each
vertex's STORED position. The engine draws a skinned vertex at

    rest(v) = sum_b  w(v,b) * G_b . S_b . v

with S_b the shape's skin-to-bone transform and G_b the bone's world transform
at rest. `#chain-rest-lift` moves chain ROOT NODES and leaves S_b and the
vertices alone, so a stored-position depth cannot see it: on the lift's own test
pieces it kept reporting the pre-lift depth. This tool measures `rest(v)`.

HOW G_b IS FOUND (the frame). FSMP merges the armour's node tree into the
actor's skeleton by name: a node the skeleton has IS the skeleton's node, and a
node it lacks is attached under its nearest ancestor the skeleton has, keeping
its local transform. So
  * a skeleton bone's G is the ACTOR SKELETON's global, never the armour's copy
    (the converter writes those flat, and the game does not use them);
  * any other node's G is the skeleton global of its nearest ancestor that the
    skeleton has, times the armour's LOCAL transforms from there down. A chain
    hanging straight off the armour's root hangs off the skeleton's root
    (identity): the armour root's own transform is not used.
The skeleton is the load-order winner of the converter's skeleton paths
(`find_skeleton`). The body goes through the same model, and its rest position
must equal its stored vertices; that is the frame control.

WHICH VERTICES ARE SIMULATED CLOTH. The census's bone-mass replay
(`physics_cloth_health.read_system`): a bone moves with the solver when FSMP
creates it with mass > 0, or when it hangs below such a bone in the armour's
node tree (a skeleton node never rides on an armour node). A vertex is cloth
when more than SIM_EPS of its skin weight is on such bones. EVERY skinned shape
counts, named in the XML or not -- rendered cloth that is no collision shape
still hangs on the chain -- except the injected body shapes. VISIBLE cloth is
ranked; collision proxies and helpers (`survey_motion_clipping.is_proxy`: a
proxy name, or a proxy token on a shape with no texture) are counted apart.

THE BODY. The user's BodySlide build of the UBE body, weight-matched
(`femalebody_tangent_0/_1`, the weight-0 file taken as the SIBLING of the
weight-1 one, never looked up again): the body `#chain-rest-lift` clears and
the player wears, preset included. A NIF with no `_0`/`_1` suffix is measured
against weight 1. Runtime body morphs (RaceMenu/OBody sliders) are not applied.

DEPTH. From each cloth vertex to the nearest POINT on the body (the closest point
on the triangles around its nearest body vertices), signed by the body's
interpolated outward normal there: POSITIVE = inside the body. Normals come from
the triangles (BodySlide bodies can ship zero normals) and are turned outward by
the mesh's signed volume. Controls (`Body.control`; any failure exits 3): known
inside joints read inside and far points outside, the skinned body equals the
stored one, and the body's own vertices moved CONTROL_U in and out read
+CONTROL_U / -CONTROL_U. The same depth at the BIND position (the skin as
stored, no node tree) is printed beside it, so the node tree's share of any
change is visible.

WHAT THE LIFT DID, read from the written file. For each ARMOUR bone with skin,
moving or kinematic (the lift moves a chain whether or not the XML simulates
it), and each moving skeleton bone: its rest position minus its BIND position
(where the skin was bound, F . inv(S_b), F the shape's skin frame taken from
its heaviest static skeleton bone). Bones that moved together (within
LIFT_MIN_U) are grouped (`chain_groups`). A LIFT is one rigid translation of a
node the converter lifts (`is_lift_root`: a garment node hanging off a
skeleton node): every skinned bone below that node moved by the same vector,
of LIFT_MIN_U up to the CHAIN_LIFT_MAX cap. Anything else off its bind -- an
offset growing along a chain, flipping sign, or past the cap -- is the node
tree disagreeing with the skin, counted apart and never called a lift.
`--lift-log` adds what the run's standoff_audit.jsonl recorded; that sink
appends across runs, so it is a cross-check, never the number.

LISTED APART (a FRAME DISAGREEMENT, both depths shown, NOT ranked):
  * a file the converter's own frame check refuses (`frame_check`, which runs
    `nc._chain_frame_ok` and its CHAIN_LIFT_FRAME_TOL on the chain bones' --
    every skin bone but a hard skeleton bone -- node-tree positions, the lift
    read back under them taken off, against the skin in the bind frame F).
    It reads every skinned shape of the written file, a body-named one
    included: a source's own body helper keeps its name there, and its skin
    can refuse the file. (The converter checks the SOURCE file, so the UBE
    body it injects later is never in its check; here it is, which can raise
    the printed bone count above the log's -- the verdicts match.)
    The converter lifts nothing on such a file, so it carries no lift;
    `checked` 0 (no chain bone with skin) refuses nothing here, as there is
    no chain to lift;
  * cloth resting more than FRAME_MAX_U from where it was skinned, which no
    pass does: the node tree or the actor skeleton disagrees with the skin
    (hard skeleton-named cloth bones, which the check does not cover) and
    the MODEL is in question.

Pieces whose cloth is moved by bones alone (the census's bone-driven rows: no
simulated collision shape) are measured and ranked like the rest, marked B.

Not modelled: runtime body morphs and the garment's own morphs; the body's
genital slit reads unreliably (the control leaves it out); what the solver does
once it runs -- this is the pose it starts from.

Usage:
    python scripts/analysis/physics_rest_depth.py <output meshes dir>
        [--body femalebody_tangent_1.nif] [--skeleton skeleton_female.nif]
        [--lift-log standoff_audit.jsonl] [--json out.json] [--top N] [--limit N]

Exit 0 with the table; 2 on usage, or when no body or skeleton is found, a
named one (--skeleton, CBBE2UBE_SKELETON_NIF, --body) is no file -- never
replaced by another -- or one cannot be used (unreadable, no shapes or nodes,
a body with no triangles, a body skin bone the skeleton lacks); 3 when nothing
was measured or a control failed. `--json` is written on every exit: "status"
("ok", "controls FAILED", "nothing measured", "input error" with the one-line
"reason", or "incomplete" -- written first, so a run that crashes leaves that
and never an earlier run's rows), the controls when they ran, and depth rows
only with status "ok" (exit 0).
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Canonical spelling so test_analysis_repo_root can verify the level.
_REPO = Path(__file__).resolve().parent.parent.parent
REPO = _REPO
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO))

import numpy as np                                                # noqa: E402
from scipy.spatial import cKDTree                                 # noqa: E402
from scripts.analysis import physics_cloth_health as pch          # noqa: E402
from scripts.analysis.mesh_penetration import (                   # noqa: E402
    closest_point_on_triangles)
from scripts.analysis.survey_motion_clipping import is_proxy      # noqa: E402
from src import nif_convert as nc                                 # noqa: E402

SIM_EPS = 0.05          # a vertex is cloth above this share on moving bones
BANDS = (0.5, 1.5)      # depth thresholds the table counts
LIFT_MIN_U = nc.CHAIN_LIFT_MIN
# A lift is capped at CHAIN_LIFT_MAX; the log rounds to 4 places.
LIFT_MAX_U = nc.CHAIN_LIFT_MAX + 1e-3
FRAME_MAX_U = nc.CHAIN_LIFT_MAX + nc.CHAIN_LIFT_FRAME_TOL
CONTROL_U = 0.5
CONTROL_TOL = 0.05
CONTROL_THIN_MAX = 0.25
# Joints deep inside a limb or the pelvis: the sign control's inside anchors
# (1.0-3.4u deep on a UBE build). NOT the upper spine or the clavicles, which
# sit at or behind the back's skin (Spine2 -1.3u, clavicles -1.5u), nor the
# hands and feet, which sit past the body mesh's open wrist and ankle.
INSIDE_BONES = tuple(pch._key(b) for b in (
    "NPC Pelvis [Pelv]", "NPC Spine [Spn0]",
    "NPC L Thigh [LThg]", "NPC R Thigh [RThg]",
    "NPC L Calf [LClf]", "NPC R Calf [RClf]",
    "NPC L UpperArm [LUar]", "NPC R UpperArm [RUar]",
    "NPC L Forearm [LLar]", "NPC R Forearm [RLar]"))
K_NEAREST = 8
CHUNK = 2048
BODY_SHAPES = {pch._key(n) for n in nc.UBE_BODY_INJECT_NAMES}
# The converter's own skeleton lookup (`_actor_skeleton_bone_names`).
SKELETON_PATTERNS = (
    "meshes/actors/character/character assets female/skeleton_female.nif",
    "meshes/actors/character/character assets/skeleton_female.nif",
    "meshes/actors/character/character assets/skeleton.nif",
)
NO_CLOTH_VERTS = "no vertex on a moving bone"
# The converter's own skip reason when `_chain_frame_ok` refuses a file.
FRAME_SKIP = "node-tree frame disagrees with the skinning"
VISIBLE, HIDDEN = "visible", "hidden"
NO_BODY = "no body at this weight"

_mat = nc._xf_matrix


def _pairs(pairs, n):
    pl = pairs.tolist() if hasattr(pairs, "tolist") else list(pairs)
    idx = np.array([int(i) for i, _w in pl], np.int64)
    w = np.array([float(x) for _i, x in pl], np.float64)
    ok = (idx >= 0) & (idx < n) & (w > 0)
    return idx[ok], w[ok]


def _nodes(nif):
    """{`_key`: node}. Engine strings: an XML's case is the skeleton's."""
    out = {}
    for name, node in (nif.nodes or {}).items():
        out.setdefault(pch._key(name), node)
    return out


def bone_rest(nodes, key, skel, memo):
    """4x4 world transform of bone `key` at rest in game, or None.

    A skeleton bone is the actor skeleton's own node. Any other node hangs from
    its nearest ancestor the skeleton has, by the armour's LOCAL transforms; one
    that reaches the armour's root hangs from the skeleton's root (identity)."""
    if key in memo:
        return memo[key]
    if key in skel:
        memo[key] = skel[key]
        return memo[key]
    node = nodes.get(key)
    out = None
    if node is not None:
        locals_ = []
        cur = node
        while (cur is not None and pch._key(cur.name) not in skel
               and cur.parent is not None):
            locals_.append(_mat(cur.transform))
            cur = cur.parent
        anchor = pch._key(cur.name) if cur is not None else ""
        out = skel[anchor].copy() if anchor in skel else np.eye(4)
        for local in reversed(locals_):
            out = out @ local
    memo[key] = out
    return out


def moving_bones(nodes, dynamic, skel):
    """Keys of the bones the solver moves: FSMP's dynamic bones and every
    armour node hanging below one. A skeleton node stops the walk -- in game it
    is the skeleton's node, parented by the skeleton, not by the armour."""
    out = set(dynamic)
    for key, node in nodes.items():
        cur = node
        while cur is not None:
            k = pch._key(cur.name)
            if k in dynamic:
                out.add(key)
                break
            if k in skel:
                break
            cur = cur.parent
    return out


def _armour_ancestry(nodes, key, skel):
    """`key` and its armour ancestors, nearest first, up to (not including) a
    skeleton node or the armour root. Empty for a skeleton bone."""
    out = []
    cur = nodes.get(key)
    while (cur is not None and cur.parent is not None
           and pch._key(cur.name) not in skel):
        out.append(pch._key(cur.name))
        cur = cur.parent
    return out


def is_lift_root(node):
    """The converter's own root rule (`_chain_root_subtrees(custom_only=True)`,
    the only nodes `#chain-rest-lift` translates): a garment node -- not
    `_is_skeleton_bone` -- whose parent is one."""
    par = node.parent
    return (par is not None and not nc._is_skeleton_bone(node.name)
            and nc._is_skeleton_bone(par.name))


def chain_groups(nodes, offsets, skel):
    """{key: {"root", "offset", "lift", "spread", "bones", "skeleton_bone",
    "lift_root"}}: the skinned bones grouped by the vector they rest off
    their bind by. The key is the group's root, or `root#n` when several
    groups share one root (siblings under one node that moved differently):
    every group is kept.

    A lift translates one ROOT node, so every skinned bone below it moves by
    the same vector, and a chain the lift did not touch moves by none. Bones
    under one top armour node that moved together (within LIFT_MIN_U) are one
    group; its root is their lowest common ancestor, climbed while the node
    above holds no other group's bones -- the node the lift moved, skinned or
    not (a `_00` root carries no weight). A skeleton bone is its own group:
    in game it is the skeleton's node.

    "lift_root" is the node a lift would have to have moved for this group to
    be one: the nearest node from the common ancestor up that the converter
    lifts (`is_lift_root`) and under which EVERY skinned bone is in the group
    -- one rigid translation of a whole subtree. None when there is no such
    node: bones whose offset grows along a chain or flips sign are each their
    own group, and none of them is a subtree moved as one."""
    groups = []
    for key in sorted(offsets):
        off = offsets[key]
        anc = _armour_ancestry(nodes, key, skel)
        top = anc[-1] if anc else key
        for g in groups:
            if (g["top"] == top and anc
                    and np.linalg.norm(g["ref"] - off) <= LIFT_MIN_U):
                g["bones"].append(key)
                g["offs"].append(off)
                break
        else:
            groups.append({"top": top, "ref": off, "bones": [key],
                           "offs": [off], "anc": anc})
    below = defaultdict(set)
    for key in offsets:
        for a in _armour_ancestry(nodes, key, skel):
            below[a].add(key)
    found = []
    for g in groups:
        members = set(g["bones"])
        lift_root = None
        if not g["anc"]:
            root = g["bones"][0]
        else:
            chains = [_armour_ancestry(nodes, b, skel) for b in g["bones"]]
            common = set(chains[0]).intersection(*chains[1:])
            path = chains[0]
            i = next(i for i, a in enumerate(path) if a in common)
            for a in path[i:]:
                if below[a] != members:
                    break             # a bone below it moved otherwise
                if is_lift_root(nodes[a]):
                    lift_root = a
                    break
            while i + 1 < len(path) and below[path[i + 1]] <= members:
                i += 1
            root = path[i]
        offs = np.array(g["offs"])
        med = np.median(offs, axis=0)
        found.append((root, {
            "root": root,
            "offset": [round(float(x), 4) for x in med],
            "lift": round(float(np.linalg.norm(med)), 4),
            "spread": round(float(np.linalg.norm(offs - med, axis=1).max()), 4),
            "bones": len(members), "skeleton_bone": not g["anc"],
            "lift_root": lift_root}))
    # Groups interleaved under one node share its root: each keeps its own
    # key (`root#1`, `root#2`, in bone order), so none replaces another.
    shared = Counter(root for root, _v in found)
    out, seen = {}, Counter()
    for root, v in found:
        seen[root] += 1
        out[root if shared[root] == 1 else f"{root}#{seen[root]}"] = v
    return out


class _XF:
    """A 4x4 as the TransformBuf `nc._xf_matrix` reads (scale folded in)."""

    def __init__(self, m):
        self.rotation = [list(r) for r in m[:3, :3]]
        self.translation = tuple(float(c) for c in m[:3, 3])
        self.scale = 1.0


class _BindFrameShape:
    """A shape seen in the bind frame the depth uses: its skin-to-bone
    transforms as stored, its global-to-skin taken as inv(F). The written
    file's own global-to-skin is not the frame the converter checked (its
    skeleton nodes are written flat, the source's were not); F puts the skin
    in the actor skeleton's frame, the one the node tree is placed in."""

    def __init__(self, shape, F):
        self._shape = shape
        self.bone_names = list(shape.bone_names or ())
        self.global_to_skin = _XF(np.linalg.inv(F))

    def get_shape_skin_to_bone(self, bone):
        return self._shape.get_shape_skin_to_bone(bone)


def frame_check(framed, nodes, skel, memo, lifted):
    """The converter's own frame check (`nc._chain_frame_ok`, tolerance
    `CHAIN_LIFT_FRAME_TOL`) on the written file: each chain bone's node-tree
    position at rest, with the lift read back under it taken off again,
    against where the skin binds it. `framed` is [(shape, F)]. Returns
    {"ok", "checked", "worst"}; `checked` 0 means no chain bone has skin.

    The chain bones are the converter's: every skin bone but a HARD skeleton
    bone (`_is_skeleton_bone` and not a soft-body one) the actor's skeleton
    has -- so a garment bone the skeleton happens to carry, and a soft-body
    bone, are checked where the game puts them, at the skeleton's node."""
    gpos = {}
    for s, _F in framed:
        for b in (s.bone_names or ()):
            k = pch._key(b)
            if b in gpos or (k in skel and nc._is_skeleton_bone(b)
                             and not nc._is_soft_body_physics_bone(b)):
                continue
            G = bone_rest(nodes, k, skel, memo)
            if G is None:
                continue
            p = G[:3, 3].copy()
            for a in _armour_ancestry(nodes, k, skel):
                if a in lifted:
                    p = p - np.asarray(lifted[a]["offset"], np.float64)
                    break
            gpos[b] = p

    class _Nif:
        shapes = [_BindFrameShape(s, F) for s, F in framed]
    ok, checked, worst = nc._chain_frame_ok(_Nif, gpos)
    return {"ok": bool(ok), "checked": int(checked),
            "worst": round(float(worst), 4)}


def shape_rest(nodes, shape, skel, moving, memo):
    """Rest and bind world positions of one skinned shape.

    Returns {"rest", "bind", "share" (weight share on moving bones),
    "measurable" (every weighted bone resolves), "frame_from_g2s" (no static
    skeleton bone: the bind frame F is the shape's global-to-skin instead),
    "frame" F, "offsets" {bone: rest position - bind position}}. F is taken
    from the static skeleton bone carrying the most weight. The offsets cover
    every ARMOUR bone with skin, moving or kinematic -- the lift moves a chain
    root whether or not the XML simulates the chain -- and every moving
    skeleton bone."""
    V = np.asarray(shape.verts, np.float64)
    n = len(V)
    rest = np.zeros((n, 3))
    wsum = np.zeros(n)
    wmiss = np.zeros(n)
    wmov = np.zeros(n)
    frames, mats = [], {}
    for bone, pairs in (shape.bone_weights or {}).items():
        idx, w = _pairs(pairs, n)
        if not len(idx):
            continue
        key = pch._key(bone)
        try:
            S = _mat(shape.get_shape_skin_to_bone(bone))
        except Exception:
            S = None
        G = bone_rest(nodes, key, skel, memo)
        if S is None or G is None:
            wmiss[idx] += w
            continue
        M = G @ S
        rest[idx] += (V[idx] @ M[:3, :3].T + M[:3, 3]) * w[:, None]
        wsum[idx] += w
        if key in moving:
            wmov[idx] += w
        if key in moving or key not in skel:
            mats[key] = (G, S)
        else:
            frames.append((float(w.sum()), M))
    good = wsum > 1e-9
    rest[good] /= wsum[good][:, None]
    if frames:
        F = max(frames, key=lambda f: f[0])[1]
        from_g2s = False
    else:
        try:
            F = np.linalg.inv(_mat(shape.global_to_skin))
        except Exception:
            F = np.eye(4)
        from_g2s = True
    bind = V @ F[:3, :3].T + F[:3, 3]
    offsets = {}
    for key, (G, S) in mats.items():
        try:
            at_bind = (F @ np.linalg.inv(S))[:3, 3]
        except np.linalg.LinAlgError:
            continue
        offsets[key] = G[:3, 3] - at_bind
    total = wsum + wmiss
    share = np.divide(wmov, total, out=np.zeros(n), where=total > 1e-9)
    return {"rest": rest, "bind": bind, "share": share,
            "measurable": good & (wmiss <= 1e-9),
            "frame_from_g2s": from_g2s, "frame": F, "offsets": offsets}


class Body:
    """A body surface to measure depth against, in rest (world) positions."""

    def __init__(self, verts, tris, stored=None, path=None, inside=()):
        self.path = path
        # Points known to lie inside the body, independent of its normals.
        self.inside = np.asarray(inside, np.float64).reshape(-1, 3)
        self.V = np.asarray(verts, np.float64).reshape(-1, 3)
        self.T = np.asarray(tris, np.int64).reshape(-1, 3)
        # A surface needs triangles: without them there is nothing to
        # measure against, and the controls would index an empty list.
        if not len(self.T):
            raise ValueError(f"body {path}: no triangles")
        # The frame control: the body's rest positions against what it stores.
        self.frame_error = (0.0 if stored is None else float(np.abs(
            self.V - np.asarray(stored, np.float64)).max(initial=0.0)))
        a, b, c = (self.V[self.T[:, i]] for i in range(3))
        volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0
        self.flipped = volume < 0
        self.N = nc._vertex_normals_from_tris(self.V, self.T)
        if self.flipped:
            self.N = -self.N
        # vertex -> incident triangles, padded with -1
        corner = self.T.reshape(-1)
        order = np.argsort(corner, kind="stable")
        counts = np.bincount(corner, minlength=len(self.V))
        width = int(counts.max(initial=1))
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
        rank = np.arange(len(corner)) - np.repeat(starts, counts)
        self.vt = np.full((len(self.V), width), -1, np.int64)
        self.vt[corner[order], rank] = order // 3
        self.tree = cKDTree(self.V)

    @classmethod
    def load(cls, path, skel):
        nif = nc._pynifly().NifFile(str(path))
        shapes = [s for s in nif.shapes if len(s.verts)]
        if not shapes:
            raise ValueError(f"body {path}: no shape with vertices")
        body = next((s for s in shapes if pch._key(s.name) in BODY_SHAPES),
                    None) or max(shapes, key=lambda s: len(s.verts))
        r = shape_rest(_nodes(nif), body, skel, set(), {})
        if not r["measurable"].all():
            raise ValueError(f"body {path}: a skin bone the skeleton lacks")
        inside = [skel[k][:3, 3] for k in INSIDE_BONES if k in skel]
        return cls(r["rest"], body.tris, stored=body.verts, path=path,
                   inside=inside)

    def depth(self, P):
        """Signed depth of each point: positive = inside the body."""
        return self.closest(P)[0]

    def closest(self, P):
        """(signed depth, unit outward normal at the nearest surface point)."""
        P = np.asarray(P, np.float64).reshape(-1, 3)
        out = np.empty(len(P))
        near = np.empty((len(P), 3))
        k = min(K_NEAREST, len(self.V))
        for s in range(0, len(P), CHUNK):
            p = P[s:s + CHUNK]
            _d, nn = self.tree.query(p, k=k)
            nn = np.asarray(nn).reshape(len(p), -1)
            cand = self.vt[nn].reshape(len(p), -1)
            valid = cand >= 0
            tri = np.where(valid, cand, 0)
            corners = self.T[tri]                          # (m, C, 3)
            a, b, c = (self.V[corners[..., i]] for i in range(3))
            pts = np.broadcast_to(p[:, None, :], a.shape)
            q = closest_point_on_triangles(
                pts.reshape(-1, 3), a.reshape(-1, 3), b.reshape(-1, 3),
                c.reshape(-1, 3)).reshape(a.shape)
            d2 = ((pts - q) ** 2).sum(-1)
            d2[~valid] = np.inf
            j = np.argmin(d2, axis=1)
            r = np.arange(len(p))
            qb, cb = q[r, j], corners[r, j]
            va, vb, vc = (self.V[cb[:, i]] for i in range(3))
            e0, e1, e2 = vb - va, vc - va, qb - va
            d00 = np.einsum("ij,ij->i", e0, e0)
            d01 = np.einsum("ij,ij->i", e0, e1)
            d11 = np.einsum("ij,ij->i", e1, e1)
            d20 = np.einsum("ij,ij->i", e2, e0)
            d21 = np.einsum("ij,ij->i", e2, e1)
            den = d00 * d11 - d01 * d01
            ok = np.abs(den) > 1e-18
            bv = np.where(ok, (d11 * d20 - d01 * d21) / np.where(ok, den, 1), 0)
            bw = np.where(ok, (d00 * d21 - d01 * d20) / np.where(ok, den, 1), 0)
            bu = 1.0 - bv - bw
            nrm = (bu[:, None] * self.N[cb[:, 0]] + bv[:, None] * self.N[cb[:, 1]]
                   + bw[:, None] * self.N[cb[:, 2]])
            side = np.einsum("ij,ij->i", p - qb, nrm)
            out[s:s + CHUNK] = np.where(side > 0, -1.0, 1.0) * np.sqrt(d2[r, j])
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            near[s:s + CHUNK] = nrm / np.where(ln > 1e-12, ln, 1.0)
        return out, near

    def control(self):
        """(ok, worst offset error, thin share). Three checks, and the first
        two do not use the normals the depth is signed by, so a globally
        inverted surface cannot pass them:
          * every INSIDE anchor (INSIDE_BONES, the actor skeleton's joints deep
            in the pelvis and limbs) reads inside, and the corners of the
            body's box grown 10u read outside;
          * the body's own skinned rest equals its stored vertices (`frame_error`);
          * its vertices moved CONTROL_U in along the normals read +CONTROL_U,
            moved out -CONTROL_U (median error <= CONTROL_TOL).
        In the last, a vertex whose nearest surface faces another way (normals
        at less than 60 degrees agreement) sits in a feature thinner than the
        push and is left out and counted. Measured on a UBE build: 8.5% (in)
        and 12.4% (out) of the vertices, over 90% of them on the genital
        midline slit at z 60-70. More than CONTROL_THIN_MAX of them and the
        control has not been run."""
        lo, hi = self.V.min(axis=0) - 10.0, self.V.max(axis=0) + 10.0
        box = np.array([[x, y, z] for x in (lo[0], hi[0])
                        for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        anchored = (len(self.inside) > 0 and bool(
            (self.depth(self.inside) > 0).all())
            and bool((self.depth(box) < 0).all()))
        errs, thin = [], 0.0
        for sgn in (1.0, -1.0):
            d, nq = self.closest(self.V - sgn * CONTROL_U * self.N)
            own = np.einsum("ij,ij->i", nq, self.N) > 0.5
            thin = max(thin, float(1.0 - own.mean()))
            if not own.any():
                return False, float("inf"), 1.0
            errs.append(float(np.median(np.abs(d[own] - sgn * CONTROL_U))))
        worst = max(errs)
        return (anchored and worst <= CONTROL_TOL
                and thin <= CONTROL_THIN_MAX
                and self.frame_error <= CONTROL_TOL), worst, thin


def _stats(d):
    if not len(d):
        return None
    return {"n": int(len(d)), "p50": float(np.median(d)),
            "p95": float(np.percentile(d, 95)), "max": float(d.max()),
            **{f"over_{b}": int((d > b).sum()) for b in BANDS}}


def weight_of(rel: str) -> str:
    stem = Path(rel).stem.lower()
    return "_0" if stem.endswith("_0") else "_1"


def measure(nif, row, skel, body):
    """One piece's rest-depth record, or (None, reason)."""
    nodes = _nodes(nif)
    moving = moving_bones(nodes, row["dynamic_bones"], skel)
    xml_names = {pch._key(s["name"]) for s in row.get("shapes", ())}
    memo, shapes, framed = {}, [], []
    rest, bind = defaultdict(list), defaultdict(list)
    per_bone = defaultdict(list)
    unresolved = 0
    for s in nif.shapes:
        if not len(s.verts) or not s.bone_weights:
            continue
        r = shape_rest(nodes, s, skel, moving, memo)
        # The frame check reads EVERY shape of the written file, a body-named
        # one too (a source's own body helper keeps its name and its skin; the
        # injected UBE body, which the converter never checks, adds bones but
        # no refusal): it is checked, never measured or read back.
        framed.append((s, r["frame"]))
        if pch._key(s.name) in BODY_SHAPES:
            continue
        cloth = r["share"] > SIM_EPS
        unresolved += int((cloth & ~r["measurable"]).sum())
        cloth &= r["measurable"]
        for k, off in r["offsets"].items():
            per_bone[k].append(off)
        if not cloth.any():
            continue
        move = np.linalg.norm(r["rest"][cloth] - r["bind"][cloth], axis=1)
        kind = HIDDEN if is_proxy(s) else VISIBLE
        d_rest = body.depth(r["rest"][cloth])
        d_bind = body.depth(r["bind"][cloth])
        rest[kind].append(d_rest)
        bind[kind].append(d_bind)
        shapes.append({"name": s.name, "cloth_verts": int(cloth.sum()),
                       "in_xml": pch._key(s.name) in xml_names,
                       "visible": kind == VISIBLE,
                       "rest": _stats(d_rest), "bind": _stats(d_bind),
                       "max_move": round(float(move.max()), 4),
                       "frame_from_g2s": r["frame_from_g2s"]})
    if not shapes:
        return None, NO_CLOTH_VERTS
    chain_rows = chain_groups(nodes, {
        k: np.median(np.array(offs), axis=0) for k, offs in per_bone.items()},
        skel)

    def pooled(parts):
        return _stats(np.concatenate(parts)) if parts else None
    max_move = max(s["max_move"] for s in shapes)
    # The lift's signature: ONE rigid translation of a whole subtree under a
    # node the converter lifts, no longer than its cap.
    lifted = {v["lift_root"]: v for v in chain_rows.values()
              if v["lift_root"] and LIFT_MIN_U <= v["lift"] <= LIFT_MAX_U}
    check = frame_check(framed, nodes, skel, memo, lifted)
    if not check["ok"] and lifted:
        # The converter refuses the whole file or lifts nothing on it, so a
        # file its check refuses carries no lift: read it as it stands.
        lifted, check = {}, frame_check(framed, nodes, skel, memo, {})
    refused = check["checked"] > 0 and not check["ok"]
    taken = {id(v) for v in lifted.values()}
    far = bool(max_move > FRAME_MAX_U)
    return {
        "rest": pooled(rest[VISIBLE]), "bind": pooled(bind[VISIBLE]),
        "hidden_rest": pooled(rest[HIDDEN]),
        "hidden_bind": pooled(bind[HIDDEN]),
        "chains": chain_rows,
        "lifted": lifted,
        # Armour bones off their bind in any other shape: the node tree and
        # the skin disagree there, which is no pass's doing.
        "disagree": {k: v for k, v in chain_rows.items()
                     if not v["skeleton_bone"] and id(v) not in taken
                     and v["lift"] >= LIFT_MIN_U},
        "frame_check": check,
        "max_move": max_move,
        "shapes": shapes, "unresolved_verts": unresolved,
        "bone_driven": not row.get("cloth") and bool(row.get("moved")),
        # Listed apart, never ranked: the converter's own frame check refuses
        # the file (its node tree is in a frame the skin does not share), or
        # cloth rests further from its bind than a lift can move it (the
        # model -- skeleton or node tree -- is what is in question).
        "frame_refused": refused,
        "far_from_bind": far,
        "frame_disagrees": refused or far,
    }, None


class LiftLog(defaultdict):
    """`read_lift_log`'s mapping, plus `torn`: the lines that did not parse."""
    torn = 0


def read_lift_log(path):
    """{path tail (lower-case, '/'): {"moved": {root: [lifts]}, "skipped":
    Counter(reason)}} from the run's `chain-rest-lift` records. The sink
    appends across runs, so every distinct lift is kept, not one. A sink
    written before #atomic-audit-append can hold lines two pool workers
    spliced: each is dropped and COUNTED in `.torn` -- a torn line is a record
    of unknown kind that is missing, and the cross-check says how many."""
    out = LiftLog(lambda: {"moved": defaultdict(set), "skipped": Counter()})
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                out.torn += 1           # a blank line is a splice artefact too
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                out.torn += 1
                continue
            if not isinstance(rec, dict) or rec.get("pass_") != "chain-rest-lift":
                continue
            tail = str(rec.get("path") or "").replace("\\", "/").lower()
            if not tail.endswith(".nif"):
                continue                      # a temp file the run renamed
            ent = out[tail]
            if rec.get("moved"):
                ent["moved"][str(rec.get("root"))].add(
                    round(float(rec.get("lift", 0.0)), 4))
            elif rec.get("skipped"):
                ent["skipped"][str(rec["skipped"])] += 1
    return out


def log_key(rel: str) -> str:
    """The sink's `path` for a NIF `rel` to the meshes folder: the last four
    parts of the written file's path, so a shallow piece's still starts with
    `meshes`."""
    parts = ("meshes",) + Path(rel.replace("\\", "/")).parts
    return "/".join(parts[-4:]).lower()


def find_skeleton(arg=None):
    """The skeleton the game loads: `arg`, then CBBE2UBE_SKELETON_NIF, then
    the load-order WINNER of the converter's skeleton paths (overwrite, then
    the MO2 profile's mods highest priority first, then the game's Data).
    The converter's own lookup takes the first mod ALPHABETICALLY, which on a
    list with two skeleton mods can be the one the game does not load; it is
    the fallback only when there is no profile.

    A skeleton NAMED by `arg` or the variable that is not a file raises
    FileNotFoundError: measuring against another skeleton than the one asked
    for would be a silent substitution."""
    for label, cand in (("--skeleton", arg),
                        ("CBBE2UBE_SKELETON_NIF",
                         os.environ.get("CBBE2UBE_SKELETON_NIF"))):
        if cand:
            if not Path(cand).is_file():
                raise FileNotFoundError(f"{label} names no file: {cand}")
            return Path(cand)
    from src import paths as _p
    lay = _p.discover_layout()
    order = _p.enabled_mods_ordered(lay)
    root = _p.mods_root()
    if order is not None and root is not None:
        dirs = [_p.overwrite_dir(lay)] + [root / m for m in order]
        dirs += list(lay.game_data_dirs or ())
        for pat in SKELETON_PATTERNS:
            for d in dirs:
                if d is not None and (d / pat).is_file():
                    return d / pat
    for pat in SKELETON_PATTERNS:
        try:
            p = nc._glob_first_in_mods(pat)
        except Exception:
            p = None
        if p:
            return Path(p)
    return None


def load_skeleton(path):
    nif = nc._pynifly().NifFile(str(path))
    out = {pch._key(name): _mat(node.global_transform)
           for name, node in (nif.nodes or {}).items()}
    if not out:
        raise ValueError(f"skeleton {path}: no nodes")
    return out


def scan(nifs, root, skel, bodies, open_nif=None):
    """(rows, skip Counter). `bodies` is {"_0": Body or None, "_1": Body}."""
    open_nif = open_nif or pch._open_nif
    rows, skip = [], Counter()
    for k, p in enumerate(nifs):
        if k % 400 == 0:
            print(f"  ...{k}/{len(nifs)}", flush=True)
        rel = str(Path(p).relative_to(root)).replace("\\", "/")
        try:
            if pch._MARKER not in Path(p).read_bytes().lower():
                skip[pch.NO_POINTER] += 1
                continue
            nif = open_nif(p)
        except Exception:
            skip[pch.NIF_UNREADABLE] += 1
            continue
        row, reason = pch.classify(Path(p), nif)
        if row is None:
            skip[pch.MARKER_ONLY if reason == pch.NO_POINTER else reason] += 1
            continue
        body = bodies.get(weight_of(rel))
        if body is None:
            skip[NO_BODY] += 1
            continue
        rec, reason = measure(nif, row, skel, body)
        if rec is None:
            skip[reason] += 1
            continue
        rec.update(path=rel, garment=pch.garment_of(rel),
                   weight=weight_of(rel))
        rows.append(rec)
    return rows, skip


def _fmt(st):
    if st is None:
        return f"{'-':>6} {'-':>6} {'-':>6} {0:5d} {0:5d}"
    return (f"{st['p50']:6.2f} {st['p95']:6.2f} {st['max']:6.2f} "
            f"{st['over_0.5']:5d} {st['over_1.5']:5d}")


def _over(st, band):
    return st[f"over_{band}"] if st else 0


def _max(st):
    return st["max"] if st else float("-inf")


def _mx(st, width):
    return f"{st['max']:{width}.2f}" if st else f"{'-':>{width}}"


def report(rows, skip, walked, bodies, controls, lift_log=None, top=30):
    print("\n" + "=" * 78)
    print("REST-POSE DEPTH OF SIMULATED CLOTH (node tree + skinning)")
    print("=" * 78)
    for w, b in sorted(bodies.items()):
        if b is not None:
            print(f"  body {w}: {b.path}")
    for w, (ok, err, thin, frame, flipped) in sorted(controls.items()):
        print(f"  control {w}: {'PASS' if ok else 'FAIL'}  +-{CONTROL_U}u "
              f"offset error {err:.4f}u ({100 * thin:.1f}% of vertices in "
              f"features thinner than that, left out), skinned-vs-stored "
              f"{frame:.4f}u"
              + ("  (winding inverted; normals turned outward)"
                 if flipped else ""))
    print(f"\n  NIFs walked: {walked}")
    for reason, n in skip.most_common():
        print(f"  EXCLUDED {reason:<44}: {n:5d}")
    ranked = [r for r in rows if not r["frame_disagrees"]]
    apart = [r for r in rows if r["frame_disagrees"]]
    print(f"  FRAME DISAGREEMENT (listed apart, not ranked)     : "
          f"{len(apart):5d}")
    print(f"    the converter's frame check refuses the file    : "
          f"{sum(r['frame_refused'] for r in apart):5d}")
    print(f"    cloth rests further from its bind than a lift   : "
          f"{sum(r['far_from_bind'] for r in apart):5d}")
    print(f"  MEASURED                                          : "
          f"{len(ranked):5d} / {len({r['garment'] for r in ranked})} garments")
    bd = [r for r in ranked if r["bone_driven"]]
    print(f"    of which moved by bones alone (no collision shape): "
          f"{len(bd)} / {len({r['garment'] for r in bd})} garments")
    if any(not ok for ok, *_ in controls.values()):
        print("\n!! A CONTROL FAILED: the depths below are not measurements.")
        return 3
    if not ranked:
        from scripts.analysis._census_common import require_population
        require_population(ranked, "piece(s) with simulated cloth")
        return 3

    for kind, key in ((VISIBLE, "rest"), (HIDDEN, "hidden_rest")):
        inside = [r for r in ranked if _over(r[key], 0.5)]
        deep = [r for r in ranked if _over(r[key], 1.5)]
        print(f"  {kind} cloth deeper than 0.5u at rest: {len(inside)} "
              f"pieces / {len({r['garment'] for r in inside})} garments; "
              f"deeper than 1.5u: {len(deep)} / "
              f"{len({r['garment'] for r in deep})}")
    lift_summary(ranked + apart)

    ranked.sort(key=lambda r: (-_over(r["rest"], 0.5), -_max(r["rest"]),
                               -_over(r["hidden_rest"], 0.5)))
    print(f"\nRANKED by VISIBLE cloth verts deeper than 0.5u at REST (depth u, "
          f"positive = inside; hidden = collision proxies and helpers; "
          f"B = moved by bones alone)")
    print(f"  {'rest p50':>8} {'p95':>6} {'max':>6} {'>0.5':>5} {'>1.5':>5} |"
          f" {'bind max':>8} {'>0.5':>5} | {'hidden max':>10} {'>0.5':>5} |"
          f" {'lift':>5} {'n':>2} | {'verts':>6}  piece")
    for r in ranked[:top]:
        lifts = [c["lift"] for c in r["lifted"].values()]
        n = r["rest"]["n"] if r["rest"] else 0
        print(f"  {_fmt(r['rest'])} | {_mx(r['bind'], 8)} "
              f"{_over(r['bind'], 0.5):5d} | {_mx(r['hidden_rest'], 10)} "
              f"{_over(r['hidden_rest'], 0.5):5d} | "
              f"{max(lifts) if lifts else 0.0:5.2f} {len(lifts):2d} | "
              f"{n:6d} {'B' if r['bone_driven'] else ' '} {r['path']}")
    if apart:
        print(f"\nFRAME DISAGREEMENT (not ranked: the converter's frame check "
              f"-- node tree against the skin, tolerance "
              f"{nc.CHAIN_LIFT_FRAME_TOL}u -- refuses the file, or cloth rests "
              f"more than {FRAME_MAX_U:.2f}u from where it was skinned, which "
              f"no pass does)")
        for r in sorted(apart, key=lambda r: -r["max_move"]):
            fc = r["frame_check"]
            why = ("refused" if r["frame_refused"] else "far    ")
            shape = max(r["shapes"], key=lambda s: s["max_move"])["name"]
            print(f"  {why} | frame check worst {fc['worst']:6.2f}u over "
                  f"{fc['checked']} bones | cloth moved {r['max_move']:6.2f}u "
                  f"on {shape[:18]!r} | rest max {_mx(r['rest'], 6)} bind max "
                  f"{_mx(r['bind'], 6)} hidden {_mx(r['hidden_rest'], 6)} | "
                  f"{r['path']}")
    if lift_log is not None:
        lift_log_check(rows, lift_log, top)
    return 0


def lift_summary(rows):
    """What `#chain-rest-lift` did, read from the written files."""
    lifts = [c["lift"] for r in rows for c in r["lifted"].values()]
    pieces = [r for r in rows if r["lifted"]]
    capped = sum(1 for x in lifts if x >= nc.CHAIN_LIFT_MAX - 1e-3)
    print(f"  pieces with a lifted chain (one rigid translation of a chain "
          f"root, {LIFT_MIN_U}-{nc.CHAIN_LIFT_MAX}u): {len(pieces)} / "
          f"{len({r['garment'] for r in pieces})} garments, {len(lifts)} "
          f"chains, median "
          f"{float(np.median(lifts)) if lifts else 0.0:.2f}u, {capped} at "
          f"the {nc.CHAIN_LIFT_MAX}u cap")
    dis = [r for r in rows if r["disagree"]]
    print(f"  node tree off the skin in no lift's shape (growing along a "
          f"chain, flipping sign, or past the cap): "
          f"{sum(len(r['disagree']) for r in dis)} groups on {len(dis)} "
          f"pieces")


def lift_log_check(rows, lift_log, top):
    """The run log beside the file: the chains each names as lifted, and the
    files the converter's frame check refused. The sink appends across runs,
    so a piece can carry records of several conversions."""
    agree, differ, unlogged = 0, [], 0
    for r in rows:
        ent = lift_log.get(log_key(r["path"]))
        logged = {pch._key(x) for x in ent["moved"]} if ent else set()
        seen = set(r["lifted"])
        if not logged and not seen:
            continue
        if not logged:
            unlogged += 1
        elif logged == seen:
            agree += 1
        else:
            differ.append((r["path"], sorted(logged - seen),
                           sorted(seen - logged)))
    print(f"\nLIFT LOG cross-check (the log appends across runs, so it is "
          f"not the number): {agree} piece(s) lifted exactly the chains "
          f"the log names, {len(differ)} differ, {unlogged} lifted with "
          f"no log record")
    torn = getattr(lift_log, "torn", 0)
    if torn:
        print(f"  {torn} torn line(s) in the log were skipped: records two "
              f"workers spliced (a sink written before whole-record appends), "
              f"any of which could be a lift this check reads as missing")
    for path, log_only, file_only in differ[:top]:
        print(f"  log only {log_only}  file only {file_only}  {path}")
    refused_log = {log_key(r["path"]) for r in rows
                   if (lift_log.get(log_key(r["path"])) or {}).get(
                       "skipped", {}).get(FRAME_SKIP)}
    both = sum(1 for r in rows
               if r["frame_refused"] and log_key(r["path"]) in refused_log)
    tool_only = [r["path"] for r in rows if r["frame_refused"]
                 and log_key(r["path"]) not in refused_log]
    log_only = [r["path"] for r in rows if not r["frame_refused"]
                and log_key(r["path"]) in refused_log
                and not lift_log[log_key(r["path"])]["moved"]]
    print(f"  frame check: {both} refused piece(s) the log records refused "
          f"too, {len(tool_only)} with no refusal record, {len(log_only)} the "
          f"log records ONLY as refused that the written file passes")
    for p in (tool_only + log_only)[:top]:
        print(f"    {p}")


class _QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _json_arg(argv):
    """The --json path alone, read before the full parse so a usage error
    can still be recorded there; None when it cannot be read."""
    pre = _QuietParser(add_help=False)
    pre.add_argument("--json")
    try:
        return pre.parse_known_args(argv)[0].json
    except ValueError:
        return None


def write_status(path, status, reason):
    """A run that measured nothing: "status", the one-line "reason", and no
    rows -- written over whatever an earlier run left at `path`."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"status": status, "reason": reason, "controls": {},
                   "rows": [], "skip": {}}, f, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("meshes")
    ap.add_argument("--body")
    ap.add_argument("--skeleton")
    ap.add_argument("--lift-log")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0)
    json_p = _json_arg(argv)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        if e.code == 2 and json_p:
            try:
                write_status(json_p, STATUS_INPUT, "usage: the arguments do "
                             "not parse")
            except OSError:          # argparse has said what is wrong
                pass
        raise
    if a.json:
        # Until the run ends, the file says so: a crash leaves this, never
        # an earlier run's rows. A path that cannot be written is an input
        # error like any other: one line, exit 2.
        try:
            write_status(a.json, STATUS_INCOMPLETE, "the run did not finish")
        except OSError as e:
            print(f"cannot write --json {a.json}: {type(e).__name__}: "
                  f"{e.strerror or e}")
            return 2

    def refuse(reason):
        print(reason)
        if a.json:
            write_status(a.json, STATUS_INPUT, reason)
        return 2

    root = Path(a.meshes)
    if not root.is_dir():
        return refuse(f"not a directory: {root}")
    try:
        skel_p = find_skeleton(a.skeleton)
    except FileNotFoundError as e:
        return refuse(f"cannot use the skeleton: {e}")
    body_p = Path(a.body) if a.body else nc._find_user_preset_body("_1")
    if skel_p is None or body_p is None or not Path(body_p).is_file():
        return refuse("need a skeleton (--skeleton / CBBE2UBE_SKELETON_NIF) "
                      "and the weight-1 UBE body build (--body)")
    print(f"skeleton: {skel_p}")
    from scripts.analysis.canonical_body import weight_sibling
    try:
        skel = load_skeleton(skel_p)
        bodies = {"_1": Body.load(body_p, skel)}
        try:                         # no sibling: weight 0 goes unmeasured
            w0 = weight_sibling(body_p, "_0")
        except (FileNotFoundError, ValueError) as e:
            print(f"  no weight-0 body: {e}")
            w0 = None
        bodies["_0"] = Body.load(w0, skel) if w0 is not None else None
    except Exception as e:           # an unreadable or unusable input file
        return refuse(f"cannot use the skeleton or body: {type(e).__name__}: "
                      f"{str(e).splitlines()[0] if str(e) else ''}")
    controls = {}
    for w, b in bodies.items():
        if b is not None:
            ok, err, thin = b.control()
            controls[w] = (ok, err, thin, b.frame_error, b.flipped)
    nifs = sorted(root.rglob("*.nif"))
    if a.limit:
        nifs = nifs[:a.limit]
    rows, skip = scan(nifs, root, skel, bodies)
    log = read_lift_log(a.lift_log) if a.lift_log else None
    rc = None
    try:
        rc = report(rows, skip, len(nifs), bodies, controls, log, a.top)
    except SystemExit as e:          # nothing measured exits 3 from inside
        rc = e.code
        raise
    finally:
        if a.json:
            write_json(a.json, rows, skip, bodies, controls, skel_p, rc)
    return rc


STATUS_INPUT = "input error"
STATUS_INCOMPLETE = "incomplete"


def write_json(path, rows, skip, bodies, controls, skel_p, rc):
    """The run as JSON. Depth rows are written only when every control
    passed and the report ran to its end (exit 0): otherwise "status" says
    why and "rows" is empty, so a reader of the file alone cannot take
    depths the controls rejected for measurements. `rc` None: the report
    raised, and the run is "incomplete"."""
    failed = any(not c[0] for c in controls.values())
    status = ("controls FAILED" if failed else
              "ok" if rc == 0 else
              STATUS_INCOMPLETE if rc is None else "nothing measured")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"status": status,
                   "controls": {w: {"ok": bool(ok), "offset_error": err,
                                    "thin_share": thin,
                                    "skinned_vs_stored": frame,
                                    "winding_inverted": bool(flipped)}
                                for w, (ok, err, thin, frame, flipped)
                                in controls.items()},
                   "rows": rows if status == "ok" else [],
                   "skip": dict(skip),
                   "body": {w: str(b.path) if b else None
                            for w, b in bodies.items()},
                   "skeleton": str(skel_p)}, f, indent=1)


if __name__ == "__main__":
    raise SystemExit(main())
