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

"""Armor clip/crinkle/gap diagnostic — catches the classes my per-vert-delta and
body-poke checks MISSED this session (crinkle = local push-field unevenness at a
boundary; gap = skin visible between two shapes both outside the body; off-target
= a change bleeding into a region it should have left alone).

Usage:
  python armor_clip_diag.py <candidate.nif> [source.nif]    # single-armor scan
  python armor_clip_diag.py --verify <candidate.nif> <baseline.nif>
  python armor_clip_diag.py --cases <dir> [stem]            # sweep the experiment set

For --cases, <dir> is the folder holding the converted `<stem>_1.nif` experiments
and the `<stem>_1.nif.looksgood` baseline. It may also be supplied as
CBBE2UBE_DIAG_DIR, with the mesh stem as CBBE2UBE_DIAG_STEM (default "cuirass").
Calibrated 2026-07-08 against the day's known-good (.looksgood) + known-bad experiments.
"""
import sys, numpy as np
from pathlib import Path

# This script lives in <repo>/scripts/, so the repo root is its parent's parent.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / ".pynifly"))
from pyn import pynifly
from scipy.spatial import cKDTree


def load(path):
    n = pynifly.NifFile(filepath=path)
    out = {}
    for s in n.shapes:
        v = np.asarray(s.verts, dtype=np.float64)
        nr = np.asarray(s.normals, dtype=np.float64) if s.normals else None
        tris = np.asarray(s.tris) if getattr(s, "tris", None) is not None else None
        bw = s.bone_weights or {}
        bwt = {b: float(sum(w for _, w in pr)) for b, pr in bw.items()}
        out[s.name] = dict(v=v, n=nr, tris=tris, bwt=bwt)
    return out


def weight_delta(cand, base, shape, thresh=1.0):
    """Per-bone total-weight change vs baseline. Catches skinning-only edits a
    position detector can't see (jiggle-sync moving Butt/Belly weight)."""
    if shape not in base:
        return []
    cb, bb = cand[shape]["bwt"], base[shape]["bwt"]
    out = [(b, cb.get(b, 0.0) - bb.get(b, 0.0)) for b in set(cb) | set(bb)]
    return sorted([x for x in out if abs(x[1]) > thresh], key=lambda x: -abs(x[1]))


def layer_penetration(cand, base=None):
    """Max depth any waist cloth shape sits INSIDE another (A behind B's surface).
    With baseline: report the INCREASE (a new/worse layer clip from a change)."""
    def pens(m):
        S = {k: (m[k]["v"], m[k]["n"]) for k in m
             if k not in ("BaseShape",) and m[k]["n"] is not None}
        worst = {}
        for a in S:
            va = S[a][0]
            am = (va[:, 2] >= 60) & (va[:, 2] <= 100)
            if am.sum() < 5:
                continue
            for b in S:
                if a == b:
                    continue
                vb, nb = S[b]
                d, idx = cKDTree(vb).query(va[am])
                near = d < 2.0
                if near.sum():
                    sg = np.einsum('ij,ij->i', va[am][near] - vb[idx[near]], nb[idx[near]])
                    worst[(a, b)] = max(worst.get((a, b), 0.0), float(max(0.0, -sg.min())))
        return worst
    cw = pens(cand)
    if base is None:
        return sorted(cw.items(), key=lambda x: -x[1])[:5]
    bw = pens(base)
    return sorted([(k, cw[k] - bw.get(k, 0.0)) for k in cw if cw[k] - bw.get(k, 0.0) > 0.15],
                  key=lambda x: -x[1])[:5]


def _edges(tris):
    e = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e.sort(axis=1)
    return np.unique(e, axis=0)


def crinkle(cand, base, shape):
    """Local unevenness of the displacement field = crinkle. A smooth push has
    ~equal displacement on adjacent verts (small edge gradient); a crease has
    adjacent verts pushed very differently (large edge gradient).

    DISAMBIGUATION (learned on first live use 2026-07-08): a big SMOOTH morph
    (e.g. the exe applying a source BodySlide body-shape) also raises max_jump,
    so max_jump alone false-alarms. Read it WITH moved_mean:
      high max_jump + HIGH moved_mean (broad, all z-bands) -> a whole-shape MORPH,
        probably benign (verify in-game);
      high max_jump + LOW moved_mean (a still surface) -> a real CRINKLE/spike.
    The `spikiness` field = max_jump / (moved_mean+eps) captures this; a real
    crinkle spikes it, a uniform morph keeps it near ~1-2."""
    if shape not in base or len(cand[shape]["v"]) != len(base[shape]["v"]):
        return None
    disp = cand[shape]["v"] - base[shape]["v"]
    tris = cand[shape]["tris"]
    if tris is None:
        return None
    e = _edges(tris)
    dg = np.linalg.norm(disp[e[:, 0]] - disp[e[:, 1]], axis=1)  # per-edge disp jump
    moved = np.linalg.norm(disp, axis=1)
    mm = float(moved.mean())
    return dict(max_jump=float(dg.max()), p99_jump=float(np.percentile(dg, 99)),
                n_sharp=int((dg > 0.3).sum()), moved_max=float(moved.max()),
                moved_mean=mm, spikiness=float(dg.max() / (mm + 0.05)),
                worst_z=float(cand[shape]["v"][e[np.argmax(dg), 0], 2]))


def exposed_skin(cand, zlo=35, zhi=82, xmax=22):
    """Body verts in the leg/torso region NOT covered by the OUTERMOST nearby
    armor along the body normal = skin visible (poke-through OR gap between
    layers). Correct sign: armor OUTSIDE the body has (A-B).n > 0."""
    if "BaseShape" not in cand:
        return None
    bv, bn = cand["BaseShape"]["v"], cand["BaseShape"]["n"]
    arm = [k for k in cand if k not in ("BaseShape",) and cand[k]["v"] is not None
           and cand[k]["n"] is not None and "throw" not in k.lower()]
    if not arm:
        return None
    allv = np.vstack([cand[k]["v"] for k in arm])
    t = cKDTree(allv)
    reg = np.where((bv[:, 2] >= zlo) & (bv[:, 2] <= zhi) & (np.abs(bv[:, 0]) < xmax))[0]
    exposed = 0
    zc = []
    for i in reg:
        nb = t.query_ball_point(bv[i], 3.5)
        if not nb:
            exposed += 1; zc.append(bv[i, 2]); continue
        outermost = ((allv[nb] - bv[i]) @ bn[i]).max()
        if outermost < -0.25:      # even the outermost armor is inside the body
            exposed += 1; zc.append(bv[i, 2])
    return dict(n_exposed=exposed, n_region=len(reg),
                frac=exposed / max(1, len(reg)),
                z_med=float(np.median(zc)) if zc else -1)


def off_target(cand, base, protect_zlo, protect_zhi, label):
    """Displacement inside a region that a change should have LEFT ALONE."""
    worst = 0.0
    for k in cand:
        if k not in base or len(cand[k]["v"]) != len(base[k]["v"]):
            continue
        v = base[k]["v"]
        m = (v[:, 2] >= protect_zlo) & (v[:, 2] <= protect_zhi)
        if m.sum() == 0:
            continue
        d = np.linalg.norm(cand[k]["v"][m] - v[m], axis=1).max()
        worst = max(worst, d)
    return dict(region=label, worst_move=worst)


def thin_clearance(cand, thresh=0.7):
    """BASELINE-FREE pose-clip risk: armor verts sitting < thresh off the body at
    bind -> a bent/spread pose punches the body through. Reports by region."""
    if "BaseShape" not in cand:
        return []
    bv, bn = cand["BaseShape"]["v"], cand["BaseShape"]["n"]
    t = cKDTree(bv)
    hits = []
    for k in cand:
        if k in ("BaseShape",) or cand[k]["n"] is None:
            continue
        v, nrm = cand[k]["v"], cand[k]["n"]
        d, idx = t.query(v)
        clr = np.einsum('ij,ij->i', v - bv[idx], bn[idx])  # +out
        for zlo, zhi, lbl in [(20, 42, "calf"), (42, 64, "thigh"),
                              (64, 82, "butt/hip"), (82, 110, "torso")]:
            m = (v[:, 2] >= zlo) & (v[:, 2] <= zhi) & (clr < thresh) & (clr > -3)
            if m.sum() >= 8:
                # medial (inner) verts are the real pose-clip risk
                med = m & (np.abs(nrm[:, 0]) > 0.4)
                tag = " INNER" if med.sum() >= 5 else ""
                hits.append((k, lbl, int(m.sum()), float(clr[m].min()), tag))
    return sorted(hits, key=lambda x: x[3])[:8]


def _src_pairs(src_path):
    """Which shape pairs (A into B) penetrate in the SOURCE mesh (own coords).
    A converted penetration also present here is the ARMOR's own overlap ->
    'inherent' (leave it, like the belt-back); absent -> converter-introduced."""
    try:
        s = load(src_path)
        return {k for k, v in layer_penetration(s) if v > 0.4}
    except Exception:
        return None


def diagnose(path, source_path=None):
    """Single-armor, no-baseline problem scan for a fresh conversion. If a SOURCE
    nif is given, tag penetrations inherent (armor's own) vs NEW (converter)."""
    c = load(path)
    src = _src_pairs(source_path) if source_path else None
    print(f"=== diagnose {path} ===")
    tc = thin_clearance(c)
    print("THIN-CLEARANCE (pose-clip risk, <0.7u bind):")
    for k, lbl, n, mn, tag in tc:
        print(f"   {k:14s} {lbl:9s}{tag:6s} {n:4d} verts  min={mn:+.2f}u")
    lp = layer_penetration(c)
    print("LAYER PENETRATION (waist, one shape inside another):")
    for (a, b), depth in lp:
        tag = ""
        if src is not None:
            tag = "  [inherent-source]" if (a, b) in src else "  [NEW - converter]"
        print(f"   {a} into {b}: {depth:.2f}u{tag}")
    ex = exposed_skin(c)
    if ex:
        print(f"EXPOSED body region (bare-limb baseline ~25%): {100*ex['frac']:.1f}% "
              f"(z-median {ex['z_med']:.0f})")


if __name__ == "__main__":
    import os
    a = sys.argv[1:]
    if a and a[0] == "--verify" and len(a) == 3:   # verify candidate vs baseline
        cand, base = load(a[1]), load(a[2])
        worst = max((crinkle(cand, base, k) or {"spikiness": 0, "max_jump": 0}
                     for k in cand if cand[k]["tris"] is not None),
                    key=lambda r: r["spikiness"])
        kind = ("CRINKLE" if worst["spikiness"] > 8 else
                "broad-morph(benign)" if worst["max_jump"] > 0.5 else "clean")
        print(f"crinkle max-jump={worst['max_jump']:.2f}u  moved_mean="
              f"{worst.get('moved_mean', 0):.2f}u  spikiness={worst['spikiness']:.1f}"
              f"  -> {kind}")
        print(f"new-clips={layer_penetration(cand, base)}")
        sys.exit()
    if a and a[0] != "--cases":                    # diagnose <armor.nif> [source.nif]
        diagnose(a[0], a[1] if len(a) > 1 else None); sys.exit()
    # --cases <dir> [stem], or CBBE2UBE_DIAG_DIR / CBBE2UBE_DIAG_STEM. No default:
    # the experiment set lives wherever the caller converted it to.
    D = (a[1] if len(a) > 1 else "") or os.environ.get("CBBE2UBE_DIAG_DIR", "")
    S = (a[2] if len(a) > 2 else "") or os.environ.get("CBBE2UBE_DIAG_STEM", "cuirass")
    if not D:
        print(__doc__)
        print("ERROR: no experiment directory given. Pass `--cases <dir> [stem]` or set\n"
              "       CBBE2UBE_DIAG_DIR to the folder holding the converted meshes.")
        sys.exit(2)
    base = load(f"{D}/{S}_1.nif.looksgood")
    T = os.environ["TEMP"]
    cases = [("looksgood(GOOD)", f"{D}/{S}_1.nif.looksgood"),
             ("ithigh2(calf wrinkle)", f"{T}/ithigh2/{S}_1.nif"),
             ("ithigh3(calf broken)", f"{T}/ithigh3/{S}_1.nif"),
             ("gap35(thigh gaps)", f"{T}/gap35/{S}_1.nif"),
             ("cinf(pants moved)", f"{T}/cinf/{S}_1.nif"),
             ("cinf2(per-vtx inflate)", f"{T}/cinf2/{S}_1.nif"),
             ("jsync_test(full replace)", f"{T}/jsync_test/{S}_1.nif"),
             ("jsync2(jiggle-only)", f"{T}/jsync2/{S}_1.nif")]
    # crinkle is checked on EVERY reskinned cloth shape, not just Greaves
    def worst_crinkle(c):
        best = (0.0, "", -1)
        for k in c:
            if k in ("BaseShape",) or c[k]["tris"] is None:
                continue
            r = crinkle(c, base, k)
            if r and r["max_jump"] > best[0]:
                best = (r["max_jump"], k, r["worst_z"])
        return best

    print(f"{'CASE':26s} {'CRINKLE(jump@z,shape)':26s} {'calfMove':9s} {'pantsMove':10s} {'VERDICT'}")
    for name, path in cases:
        try:
            c = load(path)
        except Exception as e:
            print(f"{name:26s} LOAD FAIL {e}"); continue
        mj, ms, mz = worst_crinkle(c)
        calf = off_target(c, base, 25, 46, "calf")["worst_move"]
        pants = off_target(c, base, 46, 60, "pants")["worst_move"]
        wd = weight_delta(c, base, "Greaves") + weight_delta(c, base, "Cuirass_B")
        lp = layer_penetration(c, base)
        flags = []
        if mj > 0.5:
            flags.append(f"CRINKLE({ms} {mj:.1f}u@z{mz:.0f})")
        if calf > 0.3:
            flags.append(f"calf {calf:.1f}u")
        if pants > 0.3:
            flags.append(f"pants {pants:.1f}u")
        if wd:
            flags.append("wt:" + ",".join(f"{b.split()[-1]}{d:+.0f}" for b, d in wd[:2]))
        if lp:
            flags.append(f"newclip {lp[0][0][0]}>{lp[0][0][1]} +{lp[0][1]:.1f}u")
        verdict = "CLEAN" if not flags else " ; ".join(flags)
        print(f"{name:26s} {verdict}")
