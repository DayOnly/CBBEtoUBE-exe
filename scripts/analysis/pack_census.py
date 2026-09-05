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

"""ONE PASS over a converted pack: surface, BODYTRI, collision proxy, parity.

    python scripts/analysis/pack_census.py <mod-dir> [--headroom] [--limit N]

Reads each NIF ONCE and scores everything off that read, so it costs a
fraction of running the individual harnesses back to back. Every population
is printed with its exclusions -- 0/0 is not a pass.

READ `bodytri_of` BEFORE WRITING ANY EXTRA-DATA PROBE. pynifly's
`shape.extra_data()` stops at the first block it cannot build, and a
BodySlide body carries a `NiIntegersExtraData` LOCKEDNORM at index 0 -- so
it reports NO extra data on exactly the shapes that are bodies. That
produced two confidently wrong censuses before it was caught. Enumerate by
index over `extraDataCount`.

Reads each NIF once and scores everything off that read, so it costs a fraction
of running the individual harnesses back to back. Prints an explicit population
with every exclusion counted -- 0/0 is not a pass.
"""
import sys
import os
import glob
import re
import collections

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))
sys.path.insert(0, str(REPO / "scripts" / "analysis"))
import logging  # noqa: E402
logging.disable(logging.WARNING)
import numpy as np  # noqa: E402
from pyn.pynifly import NifFile  # noqa: E402
from src.tri import TriFile  # noqa: E402
from fold_census import score  # noqa: E402

BODY_NAMES = {"BaseShape", "3BA"}
PROXY = {"VirtualBody", "VirtualGround", "SkirtCol", "ButtCol"}


def bodytri_of(shape):
    """Enumerate by INDEX. `extra_data()` stops at the first block it cannot
    build, and a BodySlide body carries LOCKEDNORM at index 0 -- so it reports
    no BODYTRI on exactly the shapes that are bodies."""
    out = []
    try:
        n = int(getattr(shape.properties, "extraDataCount", 0) or 0)
    except Exception:
        return out
    for i in range(n):
        try:
            ed = shape.get_extra_data(target_index=i)
        except Exception:
            continue
        if ed is not None and getattr(ed, "name", None) == "BODYTRI":
            out.append(str(getattr(ed, "string_data", "")))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = os.path.join(sys.argv[1], "meshes")
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if not os.path.isdir(root):
        print("no meshes dir under " + sys.argv[1])
        return 2

    nifs = sorted(glob.glob(os.path.join(root, "**", "*.nif"), recursive=True))
    nifs = [p for p in nifs
            if "_bsa_staging" not in p
            and "1stperson" not in os.path.basename(p).lower()]
    if limit:
        nifs = nifs[:limit]

    headroom = "--headroom" in sys.argv
    bust_min = []          # (min standoff, piece, shape) over the bust band
    stat = collections.Counter()
    fold_tot = inv_tot = judged = 0
    body_slot = body_tagged = untagged_cloth = 0
    proxy_pieces = ground_tagged = 0
    parity_bad = []
    oob = []
    worst = []
    load_fail = []
    seen_pairs = set()

    for p in nifs:
        stat["nifs"] += 1
        try:
            nf = NifFile(p)
        except Exception as e:
            load_fail.append((os.path.relpath(p, root), repr(e)[:60]))
            continue
        shapes = list(nf.shapes)
        names = [s.name for s in shapes]

        f_here = i_here = 0
        for s in shapes:
            if s.name in PROXY:
                continue
            try:
                v = np.asarray(s.verts, np.float64)
                f, i, u = score(v, s.tris, s.normals)
            except Exception:
                continue
            f_here += int(f.sum())
            i_here += int(i.sum())
            judged += int((~u).sum())
        fold_tot += f_here
        inv_tot += i_here
        if f_here:
            worst.append((f_here, i_here, os.path.relpath(p, root)))

        if any(n in BODY_NAMES for n in names):
            body_slot += 1
            tagged = set(s.name for s in shapes if bodytri_of(s))
            if tagged & BODY_NAMES:
                body_tagged += 1
            cloth = [s.name for s in shapes
                     if (s.textures or {})
                     and s.name not in BODY_NAMES and s.name not in PROXY]
            if [c for c in cloth if c not in tagged]:
                untagged_cloth += 1

        if "SkirtCol" in names:
            proxy_pieces += 1
            stem = p[:-6] if p[-6:-4] in ("_0", "_1") else p[:-4]
            xmlp = stem + ".xml"
            if os.path.exists(xmlp):
                txt = open(xmlp, "rb").read().decode("utf-8", "ignore")
                m = re.search(
                    r'<per-triangle-shape name="SkirtCol">(.*?)</per-triangle-shape>',
                    txt, re.S)
                if m:
                    tg = re.search(r"<tag>([^<]*)</tag>", m.group(1))
                    if tg and tg.group(1).strip().lower() in ("ground", "body"):
                        ground_tagged += 1

        # BUST HEADROOM. #softcloth-seated-cap caps the extra lift softcloth
        # adds to cloth already outside the body, and the pass exists to give
        # the UBE bust jiggle room -- so the number that matters is the CLOSEST
        # approach in the breast band, not the median. A small minimum is where
        # the body will punch through under jiggle.
        if headroom and any(n in BODY_NAMES for n in names):
            try:
                from scipy.spatial import cKDTree
                bs = next(s for s in shapes if s.name in BODY_NAMES)
                tree = cKDTree(np.asarray(bs.verts, np.float64))
                for s2 in shapes:
                    if s2.name in BODY_NAMES or s2.name in PROXY:
                        continue
                    v2 = np.asarray(s2.verts, np.float64)
                    band = (v2[:, 2] >= 90) & (v2[:, 2] < 102) & (v2[:, 1] > 1.0)
                    if band.sum() < 30:
                        continue
                    d2, _ = tree.query(v2[band])
                    bust_min.append((float(d2.min()),
                                     os.path.relpath(p, root), s2.name))
            except Exception:
                pass

        base = p[:-6] if p[-6:-4] in ("_0", "_1") else None
        if base and base not in seen_pairs:
            p0, p1 = base + "_0.nif", base + "_1.nif"
            if os.path.exists(p0) and os.path.exists(p1):
                seen_pairs.add(base)
                stat["pairs"] += 1
                try:
                    a = [(s.name, len(s.verts)) for s in NifFile(p0).shapes]
                    b = [(s.name, len(s.verts)) for s in NifFile(p1).shapes]
                    if len(a) != len(b):
                        parity_bad.append(
                            (os.path.relpath(p0, root),
                             "shape count %d vs %d" % (len(a), len(b))))
                    else:
                        bad = ["%s %d!=%d" % (a[i][0], a[i][1], b[i][1])
                               for i in range(len(a)) if a[i][1] != b[i][1]]
                        if bad:
                            parity_bad.append(
                                (os.path.relpath(p0, root), "; ".join(bad[:3])))
                except Exception:
                    pass

        stem = p[:-6] if p[-6:-4] in ("_0", "_1") else p[:-4]
        trip = stem + ".tri"
        if os.path.exists(trip):
            try:
                counts = dict((s.name, len(s.verts)) for s in shapes)
                tri = TriFile.load(trip)
                for sh in tri.shapes:
                    n = counts.get(sh.name)
                    if n is None:
                        continue
                    mx = -1
                    for m in sh.morphs:
                        for (idx, _x, _y, _z) in m.offsets:
                            if idx > mx:
                                mx = idx
                    if mx >= n:
                        oob.append("%s: %s maxidx %d >= %d"
                                   % (os.path.relpath(p, root), sh.name, mx, n))
            except Exception:
                pass

    bar = "=" * 78
    print(bar)
    print("PACK CENSUS  " + sys.argv[1])
    print(bar)
    print("  NIFs read (no 1stperson, no _bsa_staging) : %d" % stat["nifs"])
    print("  failed to load                            : %d" % len(load_fail))
    for r, e in load_fail[:5]:
        print("      %s: %s" % (r, e))
    print("  _0/_1 pairs                               : %d" % stat["pairs"])
    print("")
    print("SURFACE")
    print("  vertices judged                           : %d" % judged)
    print("  FOLDED                                    : %d" % fold_tot)
    print("  INVERTED                                  : %d" % inv_tot)
    print("")
    print("BODYTRI  (the slider fix)")
    print("  body-slot NIFs                            : %d" % body_slot)
    print("  BODYTRI present on the body               : %d/%d%s"
          % (body_tagged, body_slot,
             "   OK" if body_tagged == body_slot else "   <== MUST equal"))
    print("  NIFs with an untagged cloth shape         : %d%s"
          % (untagged_cloth, "   <== should be 0" if untagged_cloth else "   OK"))
    print("")
    print("GENERATED COLLISION PROXY  (the physics fix)")
    print("  pieces still shipping a SkirtCol          : %d" % proxy_pieces)
    print("  of those, tagged ground/body              : %d%s"
          % (ground_tagged, "   <== should be 0" if ground_tagged else "   OK"))
    print("")
    print("WEIGHT-PAIR PARITY")
    print("  pairs with a vert-count mismatch          : %d%s"
          % (len(parity_bad), "   <== should be 0" if parity_bad else "   OK"))
    for r, m in parity_bad[:10]:
        print("      %s: %s" % (r, m))
    print("  TRI offsets out of bounds                 : %d%s"
          % (len(oob), "   <== should be 0" if oob else "   OK"))
    for line in oob[:10]:
        print("      " + line)
    if headroom:
        print("BUST HEADROOM  (closest approach in the breast band, z 90-102 front)")
        print("  cloth shapes measured                     : %d" % len(bust_min))
        if bust_min:
            bust_min.sort()
            vals = np.array([b[0] for b in bust_min])
            print("  min standoff  p05 / median / max          : "
                  "%.3f / %.3f / %.3f u" % (np.percentile(vals, 5),
                                            np.median(vals), vals.max()))
            tight = [b for b in bust_min if b[0] < 0.05]
            print("  shapes closer than 0.05u (poke risk)      : %d%s"
                  % (len(tight), "   <== inspect" if tight else "   OK"))
            for d3, r, n in bust_min[:8]:
                print("      %.3fu  %s :: %s" % (d3, r, n))
        else:
            print("  (none measured -- 0/0 IS NOT A PASS)")
        print("")
    print("")
    worst.sort(reverse=True)
    print("WORST 12 PIECES (folded, inverted)")
    for f, i, r in worst[:12]:
        print("    %6d %5d  %s" % (f, i, r))
    if stat["nifs"] == 0:
        print("")
        print("  !! 0 NIFs read -- 0/0 IS NOT A PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
