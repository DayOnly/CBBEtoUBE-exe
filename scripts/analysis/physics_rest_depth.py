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

WHAT THE LIFT DID, read from the written file. For each moving bone with skin:
its rest position minus its BIND position (where the skin was bound,
F . inv(S_b), F the shape's skin frame taken from its heaviest static skeleton
bone). A lift translates one root node, so every bone below it moves by one
vector: bones that moved together (within LIFT_MIN_U) are grouped under the
node that was moved (`chain_groups`), and an armour group that moved counts as
lifted, by that vector. `--lift-log` adds what the run's standoff_audit.jsonl
recorded; that sink appends across runs, so it is a cross-check, never the
number.

Cloth resting more than FRAME_MAX_U from where it was skinned is nothing a pass
does -- the lift is a translation capped at CHAIN_LIFT_MAX -- so there the node
tree or the actor skeleton disagrees with the skin and the MODEL is in
question: the piece is listed apart as a FRAME DISAGREEMENT with both depths and
is NOT ranked.

Not modelled: runtime body morphs and the garment's own morphs; the body's
genital slit reads unreliably (the control leaves it out); what the solver does
once it runs -- this is the pose it starts from.

Usage:
    python scripts/analysis/physics_rest_depth.py <output meshes dir>
        [--body femalebody_tangent_1.nif] [--skeleton skeleton_female.nif]
        [--lift-log standoff_audit.jsonl] [--json out.json] [--top N] [--limit N]

Exit 0 with the table; 2 on usage, or when no body or skeleton is found; 3 when
nothing was measured or a control failed.
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


def chain_groups(nodes, offsets, skel):
    """{root: {"offset", "lift", "spread", "bones", "skeleton_bone"}}: the
    moving bones grouped by the vector they rest off their bind by.

    A lift translates one ROOT node, so every skinned bone below it moves by
    the same vector, and a chain the lift did not touch moves by none. Bones
    under one top armour node that moved together (within LIFT_MIN_U) are one
    group; its root is their lowest common ancestor, climbed while the node
    above holds no other group's bones -- the node the lift moved, skinned or
    not (a `_00` root carries no weight). A skeleton bone is its own group:
    in game it is the skeleton's node."""
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
    out = {}
    for g in groups:
        members = set(g["bones"])
        if not g["anc"]:
            root = g["bones"][0]
        else:
            chains = [_armour_ancestry(nodes, b, skel) for b in g["bones"]]
            common = set(chains[0]).intersection(*chains[1:])
            path = chains[0]
            i = next(i for i, a in enumerate(path) if a in common)
            while i + 1 < len(path) and below[path[i + 1]] <= members:
                i += 1
            root = path[i]
        offs = np.array(g["offs"])
        med = np.median(offs, axis=0)
        out[root] = {
            "offset": [round(float(x), 4) for x in med],
            "lift": round(float(np.linalg.norm(med)), 4),
            "spread": round(float(np.linalg.norm(offs - med, axis=1).max()), 4),
            "bones": len(members), "skeleton_bone": not g["anc"]}
    return out


def shape_rest(nodes, shape, skel, moving, memo):
    """Rest and bind world positions of one skinned shape.

    Returns {"rest", "bind", "share" (weight share on moving bones),
    "measurable" (every weighted bone resolves), "frame_from_g2s" (no static
    skeleton bone: the bind frame F is the shape's global-to-skin instead),
    "offsets" {moving bone: rest position - bind position}}. F is taken from
    the static skeleton bone carrying the most weight."""
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
            mats[key] = (G, S)
        elif key in skel:
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
            "frame_from_g2s": from_g2s, "offsets": offsets}


class Body:
    """A body surface to measure depth against, in rest (world) positions."""

    def __init__(self, verts, tris, stored=None, path=None, inside=()):
        self.path = path
        # Points known to lie inside the body, independent of its normals.
        self.inside = np.asarray(inside, np.float64).reshape(-1, 3)
        self.V = np.asarray(verts, np.float64)
        self.T = np.asarray(tris, np.int64).reshape(-1, 3)
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
    memo, shapes = {}, []
    rest, bind = defaultdict(list), defaultdict(list)
    per_bone = defaultdict(list)
    unresolved = 0
    for s in nif.shapes:
        if (pch._key(s.name) in BODY_SHAPES or not len(s.verts)
                or not s.bone_weights):
            continue
        r = shape_rest(nodes, s, skel, moving, memo)
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
    return {
        "rest": pooled(rest[VISIBLE]), "bind": pooled(bind[VISIBLE]),
        "hidden_rest": pooled(rest[HIDDEN]),
        "hidden_bind": pooled(bind[HIDDEN]),
        "chains": chain_rows,
        # The lift's signature: an ARMOUR chain moved off its bind as one.
        "lifted": {k: v for k, v in chain_rows.items()
                   if not v["skeleton_bone"] and v["lift"] >= LIFT_MIN_U},
        "max_move": max_move,
        "shapes": shapes, "unresolved_verts": unresolved,
        # No pass moves cloth this far: the lift is a translation capped at
        # CHAIN_LIFT_MAX, so beyond that the node tree or the actor skeleton
        # disagrees with the skin, and the model is what is in question.
        "frame_disagrees": bool(max_move > FRAME_MAX_U),
    }, None


def read_lift_log(path):
    """{path tail (lower-case, '/'): {"moved": {root: [lifts]}, "skipped":
    Counter(reason)}} from the run's `chain-rest-lift` records. The sink
    appends across runs and pool workers can splice lines, so a torn line is
    dropped and every distinct lift is kept, not one."""
    out = defaultdict(lambda: {"moved": defaultdict(set),
                               "skipped": Counter()})
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
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
    the fallback only when there is no profile."""
    for cand in (arg, os.environ.get("CBBE2UBE_SKELETON_NIF")):
        if cand and Path(cand).is_file():
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
    return {pch._key(name): _mat(node.global_transform)
            for name, node in nif.nodes.items()}


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
    print(f"  MEASURED                                          : "
          f"{len(ranked):5d} / {len({r['garment'] for r in ranked})} garments")
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
    lifted = [r for r in ranked + apart if r["lifted"]]
    print(f"  pieces with a lifted chain (node-tree offset >= {LIFT_MIN_U}u): "
          f"{len(lifted)}")

    ranked.sort(key=lambda r: (-_over(r["rest"], 0.5), -_max(r["rest"]),
                               -_over(r["hidden_rest"], 0.5)))
    print(f"\nRANKED by VISIBLE cloth verts deeper than 0.5u at REST (depth u, "
          f"positive = inside; hidden = collision proxies and helpers)")
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
              f"{n:6d}  {r['path']}")
    if apart:
        print(f"\nFRAME DISAGREEMENT (not ranked: cloth rests more than "
              f"{FRAME_MAX_U:.2f}u from where it was skinned, which no pass "
              f"does)")
        for r in sorted(apart, key=lambda r: -r["max_move"]):
            worst = max(r["chains"].items(), key=lambda kv: kv[1]["lift"],
                        default=(None, {"lift": 0.0, "spread": 0.0}))
            shape = max(r["shapes"], key=lambda s: s["max_move"])["name"]
            print(f"  moved {r['max_move']:6.2f}u on {shape[:18]!r} | rest max "
                  f"{_mx(r['rest'], 6)} bind max {_mx(r['bind'], 6)} "
                  f"hidden {_mx(r['hidden_rest'], 6)}"
                  f" | worst chain {worst[0]} {worst[1]['lift']:.2f}u "
                  f"(spread {worst[1]['spread']:.2f}u) | {r['path']}")
    if lift_log is not None:
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
        for path, log_only, file_only in differ[:top]:
            print(f"  log only {log_only}  file only {file_only}  {path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("meshes")
    ap.add_argument("--body")
    ap.add_argument("--skeleton")
    ap.add_argument("--lift-log")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    root = Path(a.meshes)
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2
    skel_p = find_skeleton(a.skeleton)
    body_p = Path(a.body) if a.body else nc._find_user_preset_body("_1")
    if skel_p is None or body_p is None or not Path(body_p).is_file():
        print("need a skeleton (--skeleton / CBBE2UBE_SKELETON_NIF) and the "
              "weight-1 UBE body build (--body)")
        return 2
    print(f"skeleton: {skel_p}")
    skel = load_skeleton(skel_p)
    from scripts.analysis.canonical_body import weight_sibling
    bodies, controls = {"_1": Body.load(body_p, skel)}, {}
    try:
        bodies["_0"] = Body.load(weight_sibling(body_p, "_0"), skel)
    except (FileNotFoundError, ValueError) as e:
        print(f"  no weight-0 body: {e}")
        bodies["_0"] = None
    for w, b in bodies.items():
        if b is not None:
            ok, err, thin = b.control()
            controls[w] = (ok, err, thin, b.frame_error, b.flipped)
    nifs = sorted(root.rglob("*.nif"))
    if a.limit:
        nifs = nifs[:a.limit]
    rows, skip = scan(nifs, root, skel, bodies)
    log = read_lift_log(a.lift_log) if a.lift_log else None
    rc = report(rows, skip, len(nifs), bodies, controls, log, a.top)
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"rows": rows, "skip": dict(skip),
                       "body": {w: str(b.path) if b else None
                                for w, b in bodies.items()},
                       "skeleton": str(skel_p)}, f, indent=1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
