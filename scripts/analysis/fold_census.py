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

"""DOES THE SURFACE PASS THROUGH ITSELF? -- the defect class every
position-fidelity metric in this repo is blind to.

Found 2026-08-12, after three fixes aimed at stretch and push divergence all
failed to touch a defect the user could see in four screenshots. Every probe
written that day read the bind-pose vertex array and scored how far vertices
had MOVED. The defect is not a position error, so all of them said "clean":

    bind-pose skinning        0 flung verts (with real bone globals)
    weight sums               0 bad
    added physics bones       correct skin-to-bone, resolvable, conservative
    .tri morphs               no out-of-range index, no spikes
    _0/_1 weight blend        0 blend spikes
    _0/_1 triangle lists      differ, but same SET -- reordering, harmless

Two things it IS, both measured here, both with the author's own mesh as the
control (on the piece that prompted this: 194 folds vs the author's 9, and
15 inverted normals vs 0):

  FOLD      a vertex whose own incident faces disagree with each other by more
            than 90 degrees. POSITIONS ONLY -- no normals involved -- so it is
            computable at any pass boundary, which is how the responsible pass
            was named (`inflate` +296, `antipoke` +179).

  INVERTED  the stored normal contradicts the one the geometry implies. Those
            shade as if lit from behind: dark angular shards on geometry that
            is in exactly the right place.

DETERMINACY GATE -- fan >= 3 triangles AND coherence >= 0.5.

Both terms are required, and getting this wrong overstated the class by 103
pieces on its first run. Coherence alone -- |sum(area*n)| / sum(area) -- scores
a vertex belonging to ONE triangle as perfectly coherent, because there is no
second face for it to disagree with. Isolated tabs and ribbon rims therefore
sailed through and were counted as defects: on one shape, 24 of 27 hits were
such vertices. Coherence measures its own blind spot without the fan term.

Every excluded vertex is counted and printed. A defect rate on a shrinking
denominator understates the class, and 0/0 is not a pass.

    python scripts/analysis/fold_census.py <output-root> [--source-root <dir>]
        [--limit N] [--worst N]

With `--source-root`, each shape is paired with the author's mesh of the same
name and both are scored, so a defect the author shipped is not charged to the
converter.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                      # noqa: E402
from pyn import pynifly                                 # noqa: E402

FAN_MIN = 3
COHERENCE_MIN = 0.5


def score(verts, tris, normals=None):
    """Returns (folded, inverted, undetermined) boolean arrays.

    `folded` and `undetermined` need no normals; `inverted` is all-False when
    none are supplied, so a shape without stored normals still reports its
    folds instead of being skipped.
    """
    v = np.asarray(verts, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    n = len(v)
    if not len(t) or not n:
        return (np.zeros(n, bool),) * 3

    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    area = np.linalg.norm(fn, axis=1)
    unit = fn / np.maximum(area[:, None], 1e-12)

    acc = np.zeros((n, 3))          # area-weighted, for coherence + implied
    plain = np.zeros((n, 3))        # unweighted, for the fold comparison
    area_sum = np.zeros(n)
    fan = np.zeros(n)
    for k in range(3):
        np.add.at(acc, t[:, k], unit * area[:, None])
        np.add.at(plain, t[:, k], unit)
        np.add.at(area_sum, t[:, k], area)
        np.add.at(fan, t[:, k], 1.0)

    mag = np.linalg.norm(acc, axis=1)
    coherence = mag / np.maximum(area_sum, 1e-12)
    determined = (fan >= FAN_MIN) & (coherence >= COHERENCE_MIN)

    # FOLD: the worst disagreement between a vertex's faces and their mean.
    mean = plain / np.maximum(np.linalg.norm(plain, axis=1)[:, None], 1e-12)
    worst = np.ones(n)
    for k in range(3):
        np.minimum.at(worst, t[:, k],
                      np.einsum('ij,ij->i', unit, mean[t[:, k]]))
    folded = (fan >= FAN_MIN) & (worst < 0.0)

    inverted = np.zeros(n, bool)
    if normals is not None:
        nr = np.asarray(normals, dtype=np.float64)
        if nr.shape == v.shape:
            ln = np.linalg.norm(nr, axis=1)
            implied = acc / np.maximum(mag[:, None], 1e-12)
            dot = np.einsum('ij,ij->i', nr / np.maximum(ln[:, None], 1e-12),
                            implied)
            inverted = determined & (ln > 1e-6) & (dot < 0.0)
    return folded, inverted, ~determined


def _shapes(path):
    nf = pynifly.NifFile(filepath=str(path))
    out = {}
    for s in nf.shapes:
        try:
            out[s.name] = (np.asarray(s.verts, float),
                           np.asarray(s.tris).reshape(-1, 3),
                           np.asarray(s.normals, float)
                           if s.normals is not None else None)
        except Exception:
            continue
    return out


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    root = Path(argv[0])
    src_root = Path(opt("--source-root")) if opt("--source-root") else None
    limit = int(opt("--limit", "0") or 0)
    worst_n = int(opt("--worst", "15") or 15)
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    srcs = {}
    if src_root:
        for p in src_root.rglob("*.nif"):
            srcs.setdefault(p.name.lower(), p)

    files = sorted(root.rglob("*_1.nif"))
    if limit:
        files = files[:limit]
    if not files:
        print(f"no *_1.nif under {root} -- nothing measured, which is NOT a "
              f"pass")
        return 2

    tot = defaultdict(int)
    worst = []
    for p in files:
        try:
            shapes = _shapes(p)
        except Exception as e:
            tot["pieces unreadable"] += 1
            print(f"  unreadable: {p.name}: {e!r}")
            continue
        tot["pieces"] += 1
        pf = pi = 0
        for name, (v, t, nr) in shapes.items():
            folded, inverted, undet = score(v, t, nr)
            tot["shapes"] += 1
            tot["verts"] += len(v)
            tot["excluded"] += int(undet.sum())
            tot["folded"] += int(folded.sum())
            tot["inverted"] += int(inverted.sum())
            pf += int(folded.sum())
            pi += int(inverted.sum())
            if src_root:
                sp = srcs.get(p.name.lower())
                if sp is None:
                    tot["shapes: no author mesh to compare"] += 1
                    continue
                try:
                    a = _shapes(sp).get(name)
                except Exception:
                    a = None
                if a is None or a[0].shape != v.shape:
                    tot["shapes: author shape absent or different"] += 1
                    continue
                af, ai, _ = score(a[0], a[1], a[2])
                tot["author folded"] += int(af.sum())
                tot["author inverted"] += int(ai.sum())
                tot["shapes compared"] += 1
        if pf or pi:
            worst.append((pf, pi, str(p.relative_to(root))))
        if pf:
            tot["pieces with folds"] += 1
        if pi:
            tot["pieces with inverted"] += 1

    judged = tot["verts"] - tot["excluded"]
    print(f"\npieces                    {tot['pieces']}")
    print(f"shapes                    {tot['shapes']}")
    print(f"vertices                  {tot['verts']}")
    print(f"  excluded (undetermined) {tot['excluded']}  "
          f"({100 * tot['excluded'] / max(tot['verts'], 1):.2f}%)")
    print(f"  judged                  {judged}")
    print(f"  FOLDED                  {tot['folded']}")
    print(f"  INVERTED                {tot['inverted']}")
    print(f"\npieces with folds         {tot['pieces with folds']} / "
          f"{tot['pieces']}")
    print(f"pieces with inverted      {tot['pieces with inverted']} / "
          f"{tot['pieces']}")
    if src_root:
        print(f"\nAUTHOR CONTROL over {tot['shapes compared']} paired shapes")
        print(f"  author folded           {tot['author folded']}")
        print(f"  author inverted         {tot['author inverted']}")
        print("  (a defect the author shipped is not the converter's)")
    skipped = {k: v for k, v in tot.items()
               if k.startswith(("shapes:", "pieces unreadable"))}
    if skipped:
        print("\nEXCLUSIONS")
        for k, v in sorted(skipped.items()):
            print(f"  {k:40s} {v}")
    if worst:
        print(f"\nworst {worst_n} pieces (folded, inverted)")
        for f_, i_, name in sorted(worst, reverse=True)[:worst_n]:
            print(f"  {f_:6d} {i_:6d}  {name[:72]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
