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

"""WHICH PASSES PAY, across many shapes at once.

The chain is largely damage-then-repair: a pass moves the mesh, a later pass
cleans up after it. Per-shape that is visible in a stage dump; what it needs to
drive a decision is the same ledger AGGREGATED, so a pass is judged on its
whole population rather than on the one piece someone happened to open.

For every pass boundary, per shape, this scores the geometry it produced:

    FIT       |offset from the UBE body - the author's offset from THEIR body|
              -- "closer to the original", the thing being bought
    ROUGH     area-weighted dihedral -- the surface's own smoothness
    STRETCH   99th-percentile edge ratio vs the author -- the distortion that
              reads as "broken" on screen
    SPIKES    verts >1.0u from the mean of their edge neighbours
    FOLDS     verts whose own incident faces disagree by >90deg -- the surface
              passing through itself

and reports, per pass, the MEDIAN change it makes to each. A pass whose FIT
column is ~0 and whose ROUGH/STRETCH columns are positive is doing damage that
something later has to undo -- which is the case worth acting on.

FOLDS was added 2026-08-12 because none of the four columns above could see the
defect the user was actually reporting. All of them score how far a vertex
MOVED; a fold is about the surface's own consistency, so a mesh can be folded
through itself while every fidelity number reads clean. It named the
responsible passes on its first run -- `inflate` +296, `antipoke` +179, with
`groove_smooth` -249 cleaning up after them. Gate and controls live in
`scripts/analysis/fold_census.py`; this imports them rather than restating them,
so the two tools cannot drift apart.

CLOSE THE LEDGER ON THE WRITTEN NIF. `_copy_shape` re-runs three geometry
repairs at WRITE time, after the last stage checkpoint, so a ledger built from
stage dumps alone measures what each pass CREATES, not what survives: on one
piece the stages ended at 221 folded verts where the shipped mesh has 92.

Reads `CBBE2UBE_STAGE_DUMP` directories, so one conversion per piece produces
the whole ledger and passes that cancel each other are attributed correctly.

    python scripts/analysis/pass_ledger.py <stage-dump-dir> --source-root <dir>
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                      # noqa: E402
from scipy.spatial import cKDTree                       # noqa: E402
from pyn import pynifly                                 # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fold_census import score as _fold_score            # noqa: E402

BODY_NAMES = {"baseshape", "3ba", "cbbe", "femalebody", "body", "ubebody"}


def _fn(v, t):
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a[:, None], 1e-12), 0.5 * a


def _vnorm(v, t):
    f, a = _fn(v, t)
    acc = np.zeros_like(v)
    for k in range(3):
        np.add.at(acc, t[:, k], f * a[:, None])
    return acc / np.maximum(np.linalg.norm(acc, axis=1)[:, None], 1e-12)


def _dihedral(v, t):
    f, a = _fn(v, t)
    e2t = defaultdict(list)
    for ti, (x, y, z) in enumerate(t):
        for e in ((x, y), (y, z), (x, z)):
            e2t[(min(e), max(e))].append(ti)
    p = [q for q in e2t.values() if len(q) == 2]
    if not p:
        return 0.0
    p = np.asarray(p)
    i, j = p[:, 0], p[:, 1]
    ang = np.degrees(np.arccos(np.clip(np.einsum('ij,ij->i', f[i], f[j]),
                                       -1, 1)))
    w = a[i] + a[j]
    return float((ang * w).sum() / max(w.sum(), 1e-9))


def _edges(t):
    return np.unique(np.sort(np.vstack(
        [t[:, [0, 1]], t[:, [1, 2]], t[:, [0, 2]]]), axis=1), axis=0)


def _stretch(sv, v, e):
    ls = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
    lo = np.linalg.norm(v[e[:, 0]] - v[e[:, 1]], axis=1)
    m = ls > 1e-6
    return float(np.percentile(lo[m] / ls[m], 99)) if m.any() else float("nan")


def _spikes(v, e):
    acc = np.zeros_like(v)
    cnt = np.zeros(len(v))
    for a, b in ((e[:, 0], e[:, 1]), (e[:, 1], e[:, 0])):
        np.add.at(acc, a, v[b])
        np.add.at(cnt, a, 1)
    ok = cnt > 0
    d = np.zeros(len(v))
    d[ok] = np.linalg.norm(v[ok] - acc[ok] / cnt[ok, None], axis=1)
    return int((d > 1.0).sum())


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    dumps = Path(argv[0])
    src_root = Path(argv[argv.index("--source-root") + 1]) \
        if "--source-root" in argv else None
    out_root = Path(argv[argv.index("--output-root") + 1]) \
        if "--output-root" in argv else None
    if src_root is None or out_root is None:
        print("need --source-root (the winning source meshes) and "
              "--output-root (the converted output), to get the author's mesh "
              "and the UBE body")
        return 2

    # one UBE body for every piece: the converted output's BaseShape
    body = None
    for p in sorted(out_root.rglob("*.nif")):
        try:
            nf = pynifly.NifFile(filepath=str(p))
        except Exception:
            continue
        for s in nf.shapes:
            if s.name.lower() in BODY_NAMES and len(s.verts) > 20000:
                v = np.asarray(s.verts, float)
                body = (v, _vnorm(v, np.asarray(s.tris).reshape(-1, 3)),
                        cKDTree(v))
                break
        if body:
            break
    if body is None:
        print("no UBE body found under --output-root; every FIT number would "
              "be meaningless, so stopping rather than reporting zeros")
        return 2
    bv, bn, btree = body

    srcs = {}
    for p in src_root.rglob("*.nif"):
        srcs.setdefault(p.name.lower(), p)

    per_pass = defaultdict(lambda: defaultdict(list))
    shapes = 0
    for f in sorted(dumps.glob("*.npz")):
        nif_stem, _, shape = f.stem.partition("__")
        sp = srcs.get(nif_stem.lower() + ".nif")
        if sp is None:
            continue
        try:
            snf = pynifly.NifFile(filepath=str(sp))
        except Exception:
            continue
        sh = next((s for s in snf.shapes if s.name == shape), None)
        sbody = next((s for s in snf.shapes
                      if s.name.lower() in BODY_NAMES), None)
        if sh is None or sbody is None:
            continue
        sv = np.asarray(sh.verts, float)
        sbv = np.asarray(sbody.verts, float)
        sbn = _vnorm(sbv, np.asarray(sbody.tris).reshape(-1, 3))
        _, si = cKDTree(sbv).query(sv, workers=-1)
        authored = np.einsum('ij,ij->i', sv - sbv[si], sbn[si])

        z = np.load(f)
        st = sorted(k for k in z.files if k.startswith("s") and k[1:3].isdigit())
        if len(st) < 2 or "tris" not in z.files:
            continue
        t = z["tris"].reshape(-1, 3)
        e = _edges(t)
        shapes += 1
        prev = None
        for k in st:
            v = z[k].astype(float)
            if v.shape != sv.shape:
                continue
            _, i = btree.query(v, workers=-1)
            fit = float(np.median(np.abs(
                np.einsum('ij,ij->i', v - bv[i], bn[i]) - authored)))
            folded, _, _ = _fold_score(v, t)
            cur = (fit, _dihedral(v, t), _stretch(sv, v, e), _spikes(v, e),
                   int(folded.sum()))
            if prev is not None:
                name = k[4:]
                for idx, key in enumerate(("fit", "rough", "stretch", "spike",
                                           "fold")):
                    per_pass[name][key].append(cur[idx] - prev[idx])
            prev = cur

    print(f"pass ledger over {shapes} shapes\n")
    print(f"  {'pass':22s} {'n':>4s} {'dFIT':>8s} {'dROUGH':>8s} "
          f"{'dSTRETCH':>9s} {'dSPIKES':>8s} {'dFOLDS':>8s}   reading")
    order = ["bake_preset", "warp", "inflate", "conform", "groove_smooth",
             "unified_floor", "snap_legacy", "antipoke", "softcloth",
             "chain_blend", "min_push", "seam_weld", "coherence_repair",
             "strap_scale", "short_edge"]
    for name in order + [k for k in per_pass if k not in order]:
        d = per_pass.get(name)
        if not d:
            continue
        f_ = float(np.median(d["fit"]))
        r_ = float(np.median(d["rough"]))
        s_ = float(np.median(d["stretch"]))
        k_ = float(np.median(d["spike"]))
        o_ = float(np.sum(d["fold"]))
        if o_ > 0:
            # A fold is the surface through itself. It outranks the fidelity
            # columns because a pass can read "buys fit" while folding the
            # mesh, which is exactly how this went unnoticed.
            read = "FOLDS the surface"
        elif f_ < -0.002:
            read = "buys fit"
        elif r_ > 0.05 or s_ > 0.01 or k_ > 0:
            read = "DAMAGE -- something later repairs this"
        elif abs(f_) <= 0.002 and abs(r_) <= 0.05:
            read = "inert here"
        else:
            read = ""
        print(f"  {name:22s} {len(d['fit']):4d} {f_:+8.4f} {r_:+8.3f} "
              f"{s_:+9.4f} {k_:+8.1f} {o_:+8.0f}   {read}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
