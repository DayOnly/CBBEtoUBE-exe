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

"""POSE AMPLITUDE: how far each body vertex TRAVELS across the pose envelope.

WHY. `clear_armor_outside_body` already derives its clearance requirement from body
state -- `morph_amplitude` adds room where the body GROWS at runtime, and
`jiggle_amplitude` adds room where it BOUNCES. Both exist because a bind-pose fit is
not the fit a player sees. **There is no equivalent term for POSE**: clearance is
solved once, against a body whose spine and arms never move. Measured consequence, on
armour that is clean at bind: 11.0% of covered breast exposed under a spine twist,
14.7% of covered butt under a sprint.

That clearance is the right lever for those two was verified before building this --
pushing the garment out uniformly cut breast exposure 11.0% -> 2.5% and butt
14.7% -> 6.9%. It is NOT the lever for the belly (4.8% -> 2.4% on one armour, 3.5% ->
3.9% on another, i.e. worse), so this map is not a licence to inflate everything: a
uniform push trades poke-through for gaps at garment edges.

This map is the targeting information a uniform push lacks. It depends only on the
BODY and the skeleton, not on any armour, so it is computed once per body reference
and cached -- the per-armour cost is a lookup.

Amplitude is the MAX displacement over the pose set, not the mean: clearance has to
survive the worst pose, and averaging hides exactly the pose that fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                              # noqa: E402

from pyn import pynifly                                          # noqa: E402
from scripts.analysis.posed_clip_test import (                            # noqa: E402
    read_skin, bone_parents, build_pose, apply_pose, DEFAULT_SKELETON)
from scripts.analysis.pose_set import POSE_SET                            # noqa: E402

_CACHE: dict = {}


def _skeleton_path(skeleton=None):
    skel = skeleton or DEFAULT_SKELETON
    if not skel or not Path(skel).is_file():
        try:
            from scripts.analysis.pose_engine import load_skeleton
            skel = load_skeleton()[2]
        except Exception:
            skel = None
    if not skel or not Path(skel).is_file():
        raise SystemExit("no skeleton NIF found")
    return skel


def pose_amplitude(body_path, body_shape=None, skeleton=None, poses=None,
                   per_pose=False):
    """(V,) max displacement of each body vertex over the pose set, in units.

    `per_pose=True` also returns {pose: (V,) displacement} for diagnostics -- which
    pose drives a region is the difference between a targeted term and a blunt one.
    """
    skel = _skeleton_path(skeleton)
    key = (str(body_path), body_shape, skel,
           tuple(sorted(poses or POSE_SET)), bool(per_pose))
    if key in _CACHE:
        return _CACHE[key]

    nif = pynifly.NifFile(str(body_path))
    sh = (next((s for s in nif.shapes if s.name == body_shape), None)
          if body_shape else max(nif.shapes, key=lambda s: len(s.verts)))
    if sh is None:
        raise SystemExit(f"body shape {body_shape!r} not in {Path(body_path).name}")
    verts, _tris, weights = read_skin(sh)
    verts = np.asarray(verts, dtype=np.float64)
    par = bone_parents(pynifly.NifFile(skel))
    origins = {b: o for b, (w, o) in weights.items()}

    # SELF-TEST: the identity pose must reproduce the bind mesh, or the skinning is
    # wrong and every amplitude below is noise.
    ident = float(np.abs(apply_pose(verts, weights, {}) - verts).max())
    if ident > 1e-4:
        raise SystemExit(f"identity pose moved the body by {ident:.6f}u -- skinning "
                         "is wrong, amplitudes would be meaningless")

    # DOMINANT BONE per vertex. The amplitude that matters is DEFORMATION, not
    # travel: if a vertex and the garment over it both ride one bone rigidly they
    # move together and nothing can clip. Raw displacement measures the opposite --
    # it peaked at 55u on a body whose worst real failure is a couple of units,
    # because a thigh swinging 40 degrees carries the knee 25u with no deformation
    # at all. Subtracting the vertex's OWN dominant bone's rigid motion leaves only
    # the part a garment bound to that bone cannot follow.
    # `read_skin` hands back DENSE per-vertex weight arrays, not (vert, weight) pairs.
    bone_list = list(weights.keys())
    if not bone_list:
        raise SystemExit("body shape carries no skin weights")
    W = np.stack([weights[b][0] for b in bone_list], axis=0)     # (B, V)
    dom = np.argmax(W, axis=0)

    amp = np.zeros(len(verts), dtype=np.float64)
    each = {}
    for name in (poses or POSE_SET):
        specs = POSE_SET.get(name)
        if not specs:                       # 'bind' is the identity; contributes 0
            continue
        acc = build_pose(par, origins, specs)
        moved = apply_pose(verts, weights, acc)
        # Rigid prediction: pose each vertex by its DOMINANT bone alone.
        rigid = verts.copy()
        for bi, b in enumerate(bone_list):
            m = dom == bi
            if not m.any():
                continue
            M = acc.get(b)
            if M is None:                   # bone not moved by this pose
                continue
            v = verts[m]
            rigid[m] = v @ M[:3, :3].T + M[:3, 3]
        d = np.linalg.norm(moved - rigid, axis=1)
        if per_pose:
            each[name] = d
        np.maximum(amp, d, out=amp)         # worst pose, not the average

    out = (amp, each) if per_pose else amp
    _CACHE[key] = out
    return out


def _main():
    from scripts.analysis.canonical_body import canonical_ube
    argv = sys.argv[1:]
    if argv:
        body, shape = argv[0], (argv[1] if len(argv) > 1 else None)
    else:
        body, shape = canonical_ube()
    amp, each = pose_amplitude(body, shape, per_pose=True)
    nif = pynifly.NifFile(str(body))
    sh = next((s for s in nif.shapes if s.name == shape), None) if shape else \
        max(nif.shapes, key=lambda s: len(s.verts))
    v = np.asarray(sh.verts, dtype=np.float64)
    z, ny = v[:, 2], np.zeros(len(v))
    print(f"{Path(body).name} / {sh.name}: {len(v)} verts, {len(each)} poses")
    print(f"  amplitude  mean {amp.mean():5.2f}u  p95 {np.percentile(amp,95):5.2f}u  "
          f"max {amp.max():5.2f}u")
    REG = (("breast", (z >= 90) & (z <= 102)), ("upper_chest", (z >= 99) & (z <= 112)),
           ("belly", (z >= 75) & (z < 90)), ("butt", (z >= 55) & (z <= 75)),
           ("thigh", (z >= 35) & (z < 55)))
    print(f"\n  {'region':<13}{'mean':>7}{'p95':>7}{'max':>7}   dominant pose")
    for name, m in REG:
        if not m.any():
            continue
        dom = max(each, key=lambda p: float(np.percentile(each[p][m], 95)))
        print(f"  {name:<13}{amp[m].mean():>7.2f}{np.percentile(amp[m],95):>7.2f}"
              f"{amp[m].max():>7.2f}   {dom}")


if __name__ == "__main__":
    _main()
