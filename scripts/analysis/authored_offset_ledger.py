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

"""Does each pass move the garment TOWARD the author's fit, and what does it
cost in smoothness?

The author specified one thing per vertex: how far it stands off their body,
along that body's surface normal. Reproducing that offset on the new body IS
the job -- "closer to the original" stated as a number:

    error = (offset from the UBE body now) - (offset from the source body then)

    error < 0  the vertex sits CLOSER to the body than the author put it
    error > 0  it sits further off

which matters because `conform_to_source_standoff` is PULL-IN ONLY
(`move = min(target - s_cur, 0)`, target keyed on `min(s_src, s_cur)`), so it
can only ever fix error > 0. Everything on the error < 0 side is left to an
additive push that does not know the authored value, and to a floor.

Paired with the two costs a push pass can impose, so no pass can look good here
by wrecking the surface:

    dihedral   the surface's own smoothness, area-weighted
    edge dev   fitting distortion, LENGTH-weighted (an unweighted version
               inflated one score 75% off 1% of the edges)

Reads the `CBBE2UBE_STAGE_DUMP` directory, so one conversion produces the whole
ledger and passes that cancel each other are attributed correctly.

Usage:
  python scripts/analysis/authored_offset_ledger.py --stages <dir>
      --source <source.nif> --output <converted.nif> [--shape NAME]...
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                      # noqa: E402
from scipy.spatial import cKDTree                       # noqa: E402
from pyn import pynifly                                 # noqa: E402

# Shape names that are a BODY rather than a garment.
BODY_NAMES = {"baseshape", "3ba", "cbbe", "femalebody", "body", "ubebody"}


def _load(p):
    nf = pynifly.NifFile(filepath=str(p))
    out = {}
    for s in nf.shapes:
        v = np.asarray(s.verts, float)
        t = s.transform
        R = np.asarray(t.rotation, float).reshape(3, 3)
        M = R * float(getattr(t, "scale", 1.0))
        if abs(np.linalg.det(M)) > 1e-9:
            v = v @ M.T + np.asarray(t.translation, float)
        out[s.name] = (v, np.asarray(s.tris).reshape(-1, 3))
    return out


def vertex_normals(v, t):
    """From the TRIANGLES. A source body routinely ships every stored normal
    zero, which reads as a clean +0.000 offset for every vertex."""
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    fn = n / np.maximum(a[:, None], 1e-12)
    acc = np.zeros_like(v)
    for k in range(3):
        np.add.at(acc, t[:, k], fn * a[:, None])
    return acc / np.maximum(np.linalg.norm(acc, axis=1)[:, None], 1e-12)


class Body:
    """Signed offset of a point from a body surface, along its normal."""

    def __init__(self, v, t):
        self.v, self.n, self.tree = v, vertex_normals(v, t), cKDTree(v)

    def offset(self, pts):
        _, i = self.tree.query(pts, workers=-1)
        return np.einsum('ij,ij->i', pts - self.v[i], self.n[i])


def dihedral(v, t):
    from collections import defaultdict
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    fn = n / np.maximum(a[:, None], 1e-12)
    ar = 0.5 * a
    e2t = defaultdict(list)
    for ti, (x, y, z) in enumerate(t):
        for e in ((x, y), (y, z), (x, z)):
            e2t[(min(e), max(e))].append(ti)
    p = np.asarray([q for q in e2t.values() if len(q) == 2])
    if not len(p):
        # NOT 0.0. Zero on a ROUGHNESS score reads as "perfectly smooth", so a
        # shape with no manifold edge pairs would out-score every real surface
        # in the table. NaN prints as `nan` and cannot be misread as a good
        # result.
        return float("nan")
    i, j = p[:, 0], p[:, 1]
    ang = np.degrees(np.arccos(np.clip(np.einsum('ij,ij->i', fn[i], fn[j]),
                                       -1, 1)))
    w = ar[i] + ar[j]
    return float((ang * w).sum() / max(w.sum(), 1e-9))


def edge_dev(sv, ov, t):
    e = np.unique(np.sort(np.vstack(
        [t[:, [0, 1]], t[:, [1, 2]], t[:, [0, 2]]]), axis=1), axis=0)
    ls = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
    lo = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
    m = ls > 1e-6
    r, w = lo[m] / ls[m], ls[m]
    return float((w * np.abs(r - np.median(r))).sum() / w.sum())


def main() -> int:
    argv = sys.argv[1:]
    names = set()
    while "--shape" in argv:
        i = argv.index("--shape")
        names.add(argv[i + 1])
        del argv[i:i + 2]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    stages = Path(opt("--stages", ""))
    src_nif, out_nif = opt("--source"), opt("--output")
    if not (stages.is_dir() and src_nif and out_nif):
        print(__doc__)
        return 2

    src, out = _load(src_nif), _load(out_nif)
    src_body = next((k for k in src if k.lower() in BODY_NAMES), None)
    out_body = next((k for k in out if k.lower() in BODY_NAMES), None)
    if not src_body or not out_body:
        print(f"no body shape found (source={src_body} output={out_body}); "
              f"without both, every offset below would be meaningless")
        return 2
    print(f"source body {src_body} ({len(src[src_body][0])} verts)   "
          f"target body {out_body} ({len(out[out_body][0])} verts)")
    SB = Body(*src[src_body])
    OB = Body(*out[out_body])

    for f in sorted(stages.glob("*.npz")):
        shape = f.stem.split("__", 1)[-1]
        if (names and shape not in names) or shape not in src:
            continue
        z = np.load(f)
        st = sorted(k for k in z.files if k.startswith("s") and k[1:3].isdigit())
        if len(st) < 2 or "tris" not in z.files:
            continue
        t = z["tris"].reshape(-1, 3)
        sv = src[shape][0]
        authored = SB.offset(sv)
        print(f"\n=== {f.stem}   {len(sv)} verts")
        print(f"    authored offset: p50 {np.median(authored):+.3f}  "
              f"p10 {np.percentile(authored, 10):+.3f}  "
              f"p90 {np.percentile(authored, 90):+.3f}")
        print(f"    {'pass':20s} {'|err| p50':>9s} {'p90':>7s} "
              f"{'too CLOSE':>10s} {'(of which >0.2u)':>17s} "
              f"{'dihedral':>9s} {'edge dev':>9s}")
        for k in st:
            v = z[k].astype(float)
            if v.shape != sv.shape:
                continue
            err = OB.offset(v) - authored
            close = err < 0
            print(f"    {k[4:]:20s} {np.median(np.abs(err)):9.3f} "
                  f"{np.percentile(np.abs(err), 90):7.3f} "
                  f"{100 * close.mean():9.1f}% "
                  f"{100 * (err < -0.2).mean():16.1f}% "
                  f"{dihedral(v, t):9.2f} {edge_dev(sv, v, t):9.4f}")
        # the shipped mesh, which includes every cross-shape and on-disk pass
        if shape in out and out[shape][0].shape == sv.shape:
            ov = out[shape][0]
            err = OB.offset(ov) - authored
            print(f"    {'SHIPPED':20s} {np.median(np.abs(err)):9.3f} "
                  f"{np.percentile(np.abs(err), 90):7.3f} "
                  f"{100 * (err < 0).mean():9.1f}% "
                  f"{100 * (err < -0.2).mean():16.1f}% "
                  f"{dihedral(ov, t):9.2f} {edge_dev(sv, ov, t):9.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
