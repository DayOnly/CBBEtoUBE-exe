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

"""POSE-AWARE clip measurement -- the bind-pose blind spot, closed.

WHY THIS EXISTS. Every other offline metric here measures the BIND pose, but
"standing still" in-game is a standing IDLE ANIMATION and walking is a stride:
the thigh bones are far from bind. A garment can cover the body perfectly at bind
and still show skin in play. Measured on a common-clothes dress (clipping log F1):
bind pose said 0 exposed on the front of the leg, and 30 degrees of hip flexion
put 43 verts of bare thigh on screen. Bind-pose metrics cannot see that class,
and a fix aimed at a bind-pose metric made the mesh measurably worse.

WHAT IT DOES. Poses the injected UBE body and the garment with the SAME bone
deltas (what the engine does), then ray-casts to ask "is this skin visible".

Linear blend skinning identity used: with the mesh in bind pose,
bindWorld_b == inv(STB_b), so a posed vertex is just
    v' = sum_b w_b * (acc_b @ v)
with acc_b the pose DELTA (identity = bind). At identity this reproduces the bind
mesh exactly -- the harness's own self-test, which must print 0.000000u.

WHAT IT DOES NOT DO (yet). It poses but does not MORPH. The runtime body is
BaseShape + BodyMorph/OBody deltas, which can inflate thigh/butt past the bind
body. At-rest exposure that this harness cannot reproduce is the prime suspect
for the morph path. See the clipping log before drawing conclusions.

Usage:
  python posed_clip_test.py <converted.nif> [--skeleton <skeleton.nif>]
  python posed_clip_test.py <converted.nif> --sweep       # hip-flexion threshold
  python posed_clip_test.py <converted.nif> --regression  # covered@bind -> exposed@stride

PREFER --regression for batch/comparison work. Raw exposure counts do not compare
across garments: a vest or a 1st-person mesh does not cover the legs at all and
scores as fully "exposed" by design. The regression metric uses each garment's own
bind state as its baseline.
"""
import os
import sys
from pathlib import Path
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / ".pynifly"))
from pyn import pynifly                                          # noqa: E402

def _default_skeleton() -> str:
    """Locate a skeleton NIF without hardcoding anyone's drive layout.

    Order: CBBE2UBE_SKELETON_NIF -> the game Data dir (vanilla path, and any
    skeleton-replacer overriding it resolves there too) -> the MO2 mods tree.
    A hardcoded absolute path here would make this script work on exactly one
    machine, which is how two earlier dev scripts silently broke for everyone
    else. The bone HIERARCHY must come from a real skeleton: armor NIFs carry a
    FLAT bone list, so a thigh has no children and everything below the hip
    silently fails to pose."""
    env = os.environ.get("CBBE2UBE_SKELETON_NIF", "").strip()
    if env and Path(env).is_file():
        return env
    rel = Path("meshes/actors/character/character assets/skeleton.nif")
    for key in ("CBBE2UBE_GAME_DATA", "CBBE2UBE_MODS_ROOT"):
        root = os.environ.get(key, "").strip()
        if not root or not Path(root).is_dir():
            continue
        direct = Path(root) / rel
        if direct.is_file():
            return str(direct)
        for cand in Path(root).glob(f"*/{rel.as_posix()}"):
            return str(cand)
    return ""


DEFAULT_SKELETON = _default_skeleton()
LT, RT = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]"
LC, RC = "NPC L Calf [LClf]", "NPC R Calf [RClf]"


# ------------------------------------------------------------------ skin/pose

def tb_to_matrix(tb):
    m = np.eye(4)
    r = np.array([list(tb.rotation[0]), list(tb.rotation[1]), list(tb.rotation[2])])
    s = float(tb.scale) or 1.0
    m[:3, :3] = r * s
    m[:3, 3] = list(tb.translation)
    return m


def read_skin(shape):
    """-> (verts, tris, {bone: (weights[nverts], bind_origin[3])})"""
    v = np.array(shape.verts, dtype=np.float64)
    t = np.array(shape.tris, dtype=np.int64)
    out = {}
    for bone, pairs in (shape.bone_weights or {}).items():
        stb = shape.get_shape_skin_to_bone(bone)
        if stb is None:
            continue
        try:
            origin = np.linalg.inv(tb_to_matrix(stb))[:3, 3]   # bindWorld = inv(STB)
        except np.linalg.LinAlgError:
            continue
        w = np.zeros(len(v))
        for vi, wt in pairs:
            w[vi] = wt
        out[bone] = (w, origin)
    return v, t, out


def bone_parents(nif):
    par = {}
    for name, node in (nif.nodes or {}).items():
        try:
            p = node.parent
            par[name] = p.name if p is not None else None
        except Exception:
            par[name] = None
    return par


def descendants(par, root):
    """NOTE: take `par` from the real SKELETON nif. Armor NIFs carry a FLAT bone
    list (every bone parented to the root), so a calf never resolves as a child
    of a thigh and the pose silently does nothing below the hip."""
    kids = {}
    for c, p in par.items():
        kids.setdefault(p, []).append(c)
    out, stack = set(), [root]
    while stack:
        b = stack.pop()
        if b in out:
            continue
        out.add(b)
        stack.extend(kids.get(b, []))
    return out


def rot_matrix(axis, deg):
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    R = np.eye(4)
    if axis == 'x':                       # measured: hip swing fwd/back
        R[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
    elif axis == 'y':                     # measured: abduct/adduct
        R[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    else:                                 # twist
        R[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return R


def build_pose(par, origins, specs):
    """specs: [(bone, axis, degrees), ...] ordered ROOT -> LEAF, so a knee bend
    composes on top of a hip swing."""
    acc = {}
    for bone, axis, deg in specs:
        if bone not in origins:
            continue
        cur = acc.get(bone, np.eye(4))
        pivot = (cur @ np.append(origins[bone], 1.0))[:3]
        T = np.eye(4); T[:3, 3] = pivot
        Ti = np.eye(4); Ti[:3, 3] = -pivot
        M = T @ rot_matrix(axis, deg) @ Ti
        for b in descendants(par, bone):
            acc[b] = M @ acc.get(b, np.eye(4))
    return acc


def apply_pose(verts, weights, acc):
    v = np.asarray(verts, dtype=np.float64)
    out = np.zeros_like(v)
    tot = np.zeros(len(v))
    h = np.hstack([v, np.ones((len(v), 1))])
    for bone, (w, _o) in weights.items():
        nz = w > 0
        if not nz.any():
            continue
        M = acc.get(bone)
        contrib = h[nz] if M is None else h[nz] @ M.T
        out[nz] += contrib[:, :3] * w[nz, None]
        tot[nz] += w[nz]
    good = tot > 1e-6
    out[good] /= tot[good, None]
    out[~good] = v[~good]
    return out


# ------------------------------------------------------------------ visibility

def vert_normals(v, t):
    nrm = np.zeros_like(v)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    for i in range(3):
        np.add.at(nrm, t[:, i], fn)
    ln = np.linalg.norm(nrm, axis=1)
    ln[ln == 0] = 1.0
    return nrm / ln[:, None]


def rays_hit(P, N, V, T, tmax=40.0, eps=1e-3, chunk=2000):
    """Moller-Trumbore, all rays vs all triangles, chunked over rays.

    EXACT range cull first. The rays of one call are a body REGION -- a tight
    cluster -- while T is the whole garment, so on a full-length dress most
    triangles cannot be reached by any ray in the batch. Dropping them is a pure
    speedup, NOT an approximation:

        c = centre of the ray origins,  r = max |origin - c|
        a hit point p lies within tmax of its origin, so
            |p - c| <= |p - o| + |o - c| <= tmax + r
        for any point p of a triangle and any vertex v of it,
            |p - c| >= |v - c| - |p - v| >= min_v|v - c| - L
        where L is the triangle's longest edge (= its diameter, so |p - v| <= L).
        Hence min_v|v - c| > r + tmax + L  =>  no point of the triangle is in range.

    Using the PER-TRIANGLE L rather than a global maximum keeps the bound tight
    while staying conservative. Verified to reproduce the un-culled result exactly.
    """
    T = np.asarray(T, dtype=np.int64).reshape(-1, 3)
    if len(P) and len(T):
        c = (P.min(axis=0) + P.max(axis=0)) * 0.5
        r = float(np.linalg.norm(P - c, axis=1).max())
        tv3 = V[T]                                             # (nT, 3, 3)
        dmin = np.linalg.norm(tv3 - c, axis=2).min(axis=1)      # nearest vertex
        L = np.linalg.norm(tv3 - tv3[:, [1, 2, 0], :], axis=2).max(axis=1)
        T = T[dmin <= r + tmax + L]
        if not len(T):
            return np.zeros(len(P), dtype=bool)
    A = V[T[:, 0]]; B = V[T[:, 1]]; C = V[T[:, 2]]
    e1 = B - A; e2 = C - A
    hit = np.zeros(len(P), dtype=bool)
    # Block over TRIANGLES as well as rays, and drop rays that have already hit.
    # Most body verts under a garment ARE blocked, so the active set collapses
    # after the first block or two and the remaining blocks cost almost nothing.
    # Without this, a 90k-triangle garment builds (rays x 90000 x 3) temporaries
    # for every ray including the ones already resolved -- hundreds of MB of
    # memory traffic to re-answer a question already answered.
    tblock = 8192
    for i in range(0, len(P), chunk):
        o = P[i:i + chunk]; dv = N[i:i + chunk]
        live = np.arange(len(o))
        for j in range(0, len(T), tblock):
            if not len(live):
                break
            ol, dl = o[live], dv[live]
            A2, e1b, e2b = A[j:j + tblock], e1[j:j + tblock], e2[j:j + tblock]
            pv = np.cross(dl[:, None, :], e2b[None, :, :])
            det = np.einsum('tk,rtk->rt', e1b, pv)
            ok = np.abs(det) > 1e-9
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tv = ol[:, None, :] - A2[None, :, :]
            u = np.einsum('rtk,rtk->rt', tv, pv) * inv
            qv = np.cross(tv, e1b[None, :, :])
            vv = np.einsum('rk,rtk->rt', dl, qv) * inv
            t = np.einsum('tk,rtk->rt', e2b, qv) * inv
            good = (ok & (u >= 0) & (u <= 1) & (vv >= 0) & (u + vv <= 1)
                    & (t > eps) & (t < tmax)).any(axis=1)
            if good.any():
                hit[i + live[good]] = True
                live = live[~good]
    return hit


def visible_skin(body, bn, GV, GT, zlo=38.0, zhi=78.0, xmax=20.0, xmin=2.5):
    """Body verts in the band whose outward ray escapes the garment.
    Two exclusions, both established by measurement rather than taste:
      |x| > xmax -> the ARMS (bare by design on most dresses).
      |x| < xmin -> the MIDLINE CREVICE (crotch / gluteal cleft). Rays there
        escape downward between the legs and out under the hem, so they read as
        exposed in EVERY pose including bind -- a constant ~250-350 vert
        background that buries the real signal. Measured: |x| median 0.05.
    Without these two filters this metric false-positives badly; see clipping
    log F1, where an unfiltered version produced a retracted root cause."""
    m = ((body[:, 2] >= zlo) & (body[:, 2] <= zhi)
         & (np.abs(body[:, 0]) < xmax) & (np.abs(body[:, 0]) > xmin))
    idx = np.where(m)[0]
    return idx, ~rays_hit(body[idx], bn[idx], GV, GT)


# ----------------------------------------------------------------------- main

def _posed(data, garments, acc):
    body_v, body_t, body_w = data['BaseShape']
    pb = apply_pose(body_v, body_w, acc)
    GV, GT, off = [], [], 0
    for g in garments:
        gv, gt, gw = data[g]
        GV.append(apply_pose(gv, gw, acc)); GT.append(gt + off); off += len(gv)
    return pb, vert_normals(pb, body_t), np.vstack(GV), np.vstack(GT)


def main():
    argv = [a for a in sys.argv[1:]]
    sweep = '--sweep' in argv
    if sweep:
        argv.remove('--sweep')
    regression = '--regression' in argv
    if regression:
        argv.remove('--regression')
    skel = DEFAULT_SKELETON
    if '--skeleton' in argv:
        i = argv.index('--skeleton'); skel = argv[i + 1]; del argv[i:i + 2]
    if not argv:
        print(__doc__)
        sys.exit(1)
    path = argv[0]

    if not skel or not Path(skel).is_file():
        print("No skeleton NIF found. Set CBBE2UBE_SKELETON_NIF (or "
              "CBBE2UBE_GAME_DATA / CBBE2UBE_MODS_ROOT), or pass --skeleton.\n"
              "It must be a real skeleton: armor NIFs carry a FLAT bone list, so "
              "without the hierarchy nothing below the hip poses.")
        sys.exit(2)
    nif = pynifly.NifFile(path)
    data = {s.name: read_skin(s) for s in nif.shapes}
    if 'BaseShape' not in data:
        print("no injected BaseShape in this nif -- nothing to measure against")
        sys.exit(2)
    par = bone_parents(pynifly.NifFile(skel))
    garments = [n for n in data if n != 'BaseShape']
    body_v, body_t, body_w = data['BaseShape']
    origins = {b: o for b, (w, o) in body_w.items()}

    print(f"=== {Path(path).name}")
    print(f"    body {len(body_v)} verts, garment shapes {garments}")
    chk = apply_pose(body_v, body_w, {})
    print(f"    SELF-TEST identity pose: max delta {np.abs(chk - body_v).max():.6f}u"
          f"  {'OK' if np.abs(chk - body_v).max() < 1e-6 else 'FAIL'}")

    if sweep:
        print(f"\n{'L-hip flexion':>15}{'visible':>9}{'band':>7}{'%':>7}   front-of-leg (z38-66)")
        for deg in (0, 5, 10, 15, 20, 25, 30, 40):
            acc = build_pose(par, origins, [(LT, 'x', float(deg))])
            pb, pbn, GV, GT = _posed(data, garments, acc)
            idx, exp = visible_skin(pb, pbn, GV, GT)
            P = pb[idx][exp]
            leg = int(((P[:, 2] >= 38) & (P[:, 2] < 66) & (P[:, 1] > 0)).sum()) if len(P) else 0
            print(f"{deg:>13}deg{int(exp.sum()):>9}{len(idx):>7}{100 * exp.mean():>7.2f}   {leg}")
        return

    if regression:
        # SELF-BASELINING metric, and the one to prefer for batch runs: count body
        # verts that are COVERED at bind but EXPOSED under stride. Raw exposure
        # counts are useless across garments -- a vest or a 1st-person mesh does
        # not cover the legs at all and scores 100% "exposed" by design. The
        # bind state is each garment's own baseline, so only a genuine
        # pose-induced regression is counted.
        def state(acc):
            pb, pbn, GV, GT = _posed(data, garments, acc)
            idx, exp = visible_skin(pb, pbn, GV, GT)
            e = np.zeros(len(body_v), dtype=bool); e[idx[exp]] = True
            s = np.zeros(len(body_v), dtype=bool); s[idx] = True
            return e, s
        e0, s0 = state({})
        e1, s1 = state(build_pose(par, origins, [(LT, 'x', 30.0), (RT, 'x', -20.0)]))
        cov = s0 & ~e0
        new = cov & s1 & e1
        pct = 100.0 * new.sum() / max(cov.sum(), 1)
        print(f"\n  covered at bind : {int(cov.sum())}")
        print(f"  NEWLY exposed   : {int(new.sum())}  ({pct:.2f}%)  under stride "
              f"(L fwd 30 / R back 20)")
        return

    POSES = {
        "bind (reference)": [],
        "legs together (adduct 10)": [(LT, 'y', -10.0), (RT, 'y', 10.0)],
        "stride (L fwd 30, R back 20)": [(LT, 'x', 30.0), (RT, 'x', -20.0)],
        "L knee bend (hip 20/knee 35)": [(LT, 'x', 20.0), (LC, 'x', 35.0)],
        "deep stride (L fwd 45)": [(LT, 'x', 45.0)],
    }
    print(f"\n{'pose':32}{'visible skin':>13}{'of band':>9}{'%':>7}")
    for label, specs in POSES.items():
        acc = build_pose(par, origins, specs)
        pb, pbn, GV, GT = _posed(data, garments, acc)
        idx, exp = visible_skin(pb, pbn, GV, GT)
        print(f"{label:32}{int(exp.sum()):>13}{len(idx):>9}{100 * exp.mean():>7.2f}")
        if exp.any():
            P = pb[idx][exp]
            for zl, zh, nm in ((38, 50, 'knee  '), (50, 58, 'lowthigh'),
                               (58, 66, 'upthigh'), (66, 72, 'hip   '), (72, 78, 'waist ')):
                mm = (P[:, 2] >= zl) & (P[:, 2] < zh)
                if mm.sum() > 5:
                    q = P[mm]
                    print(f"      {nm:9} z{zl}-{zh}: n={int(mm.sum()):4d}"
                          f"  front {int((q[:, 1] > 0).sum()):4d}  back {int((q[:, 1] <= 0).sum()):4d}"
                          f"  |x|med {np.median(np.abs(q[:, 0])):5.2f}")


if __name__ == "__main__":
    main()
