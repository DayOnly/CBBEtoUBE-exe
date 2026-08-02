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

"""DEVELOPMENT TOOL -- not part of the shipped converter.

Skeletal posing for clip testing. Every measurement before this one posed the body
only by BREAST/BUTT JIGGLE on top of the bind pose -- which is an A-pose. The body a
player sees is animated: arms down, spine twisted, thigh raised. Clipping caused by
the POSE itself was invisible to every metric used until now, and an in-game report of
"clipping at standstill" that measured 0% exposed is exactly what that blind spot
looks like.

Linear blend skinning driven by the ACTOR SKELETON's hierarchy (XPMSSE
`skeleton_female.nif`, 648 nodes) so a rotation carries its descendants: rotating a
thigh moves the calf and foot with it. Bone pivots come from the skeleton's global
transforms; skin weights and bind transforms come from the mesh itself, so body and
armour deform through the same joints.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                          # noqa: E402
from pyn import pynifly                                     # noqa: E402
from src import nif_convert as nc                           # noqa: E402

_SKEL_CACHE: dict = {}


def load_skeleton(mods_root=None):
    """Parent map + world pivot for every skeleton node. Prefers XPMSSE.

    `mods_root` defaults to the SAME discovery the converter uses
    (CBBE2UBE_MODS_ROOT, else MO2 layout discovery). Never hardcode a
    developer's own modlist path: it makes the harness silently useless on
    every other machine, and publishes a local directory layout.
    """
    if "skel" in _SKEL_CACHE:
        return _SKEL_CACHE["skel"]
    if mods_root is None:
        from src import paths as _p
        root = _p.mods_root()
        if root is None:
            raise FileNotFoundError(
                "could not locate a mods root -- set CBBE2UBE_MODS_ROOT, or "
                "CBBE2UBE_MO2_INI to point at a ModOrganizer.ini")
        mods_root = str(root)
    cands = glob.glob(str(Path(mods_root) / "**" / "skeleton_female.nif"),
                      recursive=True)
    cands.sort(key=lambda p: (0 if "XPMSSE" in p or "XP32" in p else 1, len(p)))
    if not cands:
        raise FileNotFoundError("no skeleton_female.nif found")
    nf = pynifly.NifFile(filepath=cands[0])
    parent, pivot = {}, {}
    for name, node in nf.nodes.items():
        par = getattr(node, "parent", None)
        parent[name] = getattr(par, "name", None)
        try:
            xf = nf.get_node_xform_to_global(name)
            pivot[name] = np.array([xf.translation[0], xf.translation[1],
                                    xf.translation[2]], float)
        except Exception:
            pivot[name] = None
    _SKEL_CACHE["skel"] = (parent, pivot, cands[0])
    return _SKEL_CACHE["skel"]


def _rot(axis, deg):
    a = np.asarray(axis, float)
    a = a / (np.linalg.norm(a) or 1.0)
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    x, y, z = a
    return np.array([
        [c + x*x*(1-c),   x*y*(1-c) - z*s, x*z*(1-c) + y*s],
        [y*x*(1-c) + z*s, c + y*y*(1-c),   y*z*(1-c) - x*s],
        [z*x*(1-c) - y*s, z*y*(1-c) + x*s, c + z*z*(1-c)]], float)


def bone_transforms(bones, pose, skel):
    """4x4 per bone: the accumulated rotation of that bone AND its ancestors, each
    about its own pivot. Root-first composition, so a parent carries its children."""
    parent, pivot, _ = skel
    out = {}
    for b in bones:
        chain, cur = [], b
        while cur is not None and cur in parent:
            chain.append(cur)
            cur = parent[cur]
        T = np.eye(4)
        for anc in reversed(chain):          # root -> leaf
            if anc not in pose:
                continue
            axis, deg = pose[anc]
            o = pivot.get(anc)
            if o is None:
                continue
            R = np.eye(4)
            R[:3, :3] = _rot(axis, deg)
            A = np.eye(4); A[:3, 3] = o
            B = np.eye(4); B[:3, 3] = -o
            T = A @ R @ B @ T
        out[b] = T
    return out


def pose_shape(V, vw, pose, skel):
    """Linear blend skinning. Bones absent from the pose contribute identity, so a
    zero pose reproduces V exactly (asserted in tests)."""
    bones = sorted({b for d in vw for b in d})
    T = bone_transforms(bones, pose, skel)
    out = np.array(V, float, copy=True)
    moving = {b for b in bones if not np.allclose(T[b], np.eye(4), atol=1e-9)}
    if not moving:
        return out
    for i, d in enumerate(vw):
        if not any(b in moving for b in d):
            continue
        v = np.append(V[i], 1.0)
        acc = np.zeros(3)
        wsum = 0.0
        for b, w in d.items():
            if w <= 0:
                continue
            acc += w * (T[b] @ v)[:3]
            wsum += w
        if wsum > 1e-6:
            out[i] = acc / wsum
    return out


# Poses a player actually spends time in. Angles are deliberate but modest -- the
# point is that ANY of them can clip, not to find an extreme.
POSES = {
    "bind (A-pose)": {},
    "arms down": {"NPC L UpperArm [LUar]": ((0, 0, 1), -55),
                  "NPC R UpperArm [RUar]": ((0, 0, 1), 55)},
    "walk (thigh fwd)": {"NPC L Thigh [LThg]": ((1, 0, 0), 35),
                         "NPC R Thigh [RThg]": ((1, 0, 0), -25)},
    "torso twist": {"NPC Spine1 [Spn1]": ((0, 0, 1), 20),
                    "NPC Spine2 [Spn2]": ((0, 0, 1), 15)},
    "lean forward": {"NPC Spine [Spn0]": ((1, 0, 0), 20),
                     "NPC Spine1 [Spn1]": ((1, 0, 0), 15)},
    "combat crouch": {"NPC L Thigh [LThg]": ((1, 0, 0), 25),
                      "NPC R Thigh [RThg]": ((1, 0, 0), 25),
                      "NPC Spine [Spn0]": ((1, 0, 0), 15),
                      "NPC L UpperArm [LUar]": ((0, 0, 1), -40),
                      "NPC R UpperArm [RUar]": ((0, 0, 1), 40)},
}
