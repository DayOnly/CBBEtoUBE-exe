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


"""HOW FAR IS THE GARMENT FROM WHERE ITS AUTHOR PUT IT -- the one fit metric
that measures fidelity rather than a side effect.

Every other fit number here is a proxy: clipping says skin shows, standoff says
the cloth floats, folds say the surface crosses itself. None can say "this is
not where the author had it". This can, and it is the quantity
`CONFORM_BLEND_TIGHT`'s own comment names: mean |our standoff - the author's|.

MEASURED AS STANDOFF, NOT POSITION. The author's garment sits on a CBBE body
and ours on a UBE body, so their vertices are SUPPOSED to differ -- comparing
positions would score the body swap, not the fit. What must be preserved is how
far the cloth sits above the body beneath it. So the author's cloth is measured
against the CBBE base body, ours against the UBE body, and the error is the
difference between those two distances, per vertex.

FINDING THE AUTHOR IS THE HARD PART, and getting it wrong reads as "there is no
author". Pairing by filename inside the converted mod's own folder finds
nothing for any mod whose meshes are VFS-resolved: BodySlide writes the built
garment into its OWN output mod, so the converted mod ships only the
`1stperson` meshes. Measured 2026-09-03 on a clothes sample -- folder pairing
matched 31 shapes and ALL 31 were first-person; excluding those correctly left
zero, and the honest-looking conclusion "this sample has no author baseline"
was WRONG. Resolving the output's Data-relative path through the MO2 load
order, the way the CONVERTER finds its own source, pairs 234 shapes.

    python seat_error_vs_author.py <arm-root> [<arm-root> ...] \
        [--source-root <converted mod dir>]

Prints mean and median seat error per arm; lower is closer to the author.
Needs CBBE2UBE_MO2_INI. Exit 1 if NOTHING paired -- that is 0/0, not a pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# scripts/analysis/<this> -> repo root is three parents up. NEVER hardcode a
# machine path here: it publishes one user's layout and makes the harness
# useless everywhere else.
_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts" / "analysis"))

from src import nif_io, nif_convert as nc                 # noqa: E402
import standoff_audit as sa                               # noqa: E402
from scipy.spatial import cKDTree                         # noqa: E402


def _world(sh):
    return nc._verts_skin_to_world(np.asarray(sh.verts, np.float64),
                                   nc._shape_global_to_skin(sh))


def main(argv) -> int:
    roots, src_root, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--source-root":
            src_root = Path(argv[i + 1]); i += 2
        else:
            roots.append(Path(argv[i])); i += 1
    if not roots:
        print(__doc__)
        return 2

    bodies: dict = {}

    def body(kind, w):
        if (kind, w) not in bodies:
            p = (nc._find_ube_femalebody(w) if kind == "ube"
                 else nc._find_cbbe_base_body(weight=w))
            if p is None:
                return None
            b = max(nif_io.open_nif_retry(str(p)).shapes,
                    key=lambda s: len(s.verts))
            bodies[(kind, w)] = cKDTree(_world(b))
        return bodies[(kind, w)]

    ref = roots[0]
    files = [f for f in sa.output_nifs(ref) if "_bsa_staging" not in f.as_posix()]
    print(f"population: {len(files)} NIF(s) (both weights, no 1stperson, "
          f"no _bsa_staging)")
    rows: list = []
    resolved = 0
    for f in files:
        rel = f.relative_to(ref).as_posix()
        w = "_1" if Path(rel).stem.endswith("_1") else "_0"
        orig = rel.replace("meshes/!UBE/", "meshes/", 1)
        try:
            ap = nc._resolve_data_rel_in_vfs(
                orig, (src_root / "x.nif") if src_root else f)
            if ap is None or not Path(ap).is_file():
                continue
            anf = nif_io.open_nif_retry(str(ap))
        except Exception:
            continue
        resolved += 1
        ct, ut = body("cbbe", w), body("ube", w)
        if ct is None or ut is None:
            print("no reference body found -- set CBBE2UBE_MO2_INI")
            return 2
        for nm, a in {s.name: s for s in anf.shapes}.items():
            try:
                aV = _world(a)
                a_off, _ = ct.query(aV, k=1)
            except Exception:
                continue
            row, ok = {}, True
            for root in roots:
                p = root / rel
                if not p.is_file():
                    ok = False; break
                try:
                    m = {s.name: s for s in nif_io.open_nif_retry(str(p)).shapes}
                    if nm not in m:
                        ok = False; break
                    oV = _world(m[nm])
                    if len(oV) != len(aV):
                        ok = False; break     # retopologised: not comparable
                    o_off, _ = ut.query(oV, k=1)
                    row[root.name] = float(np.mean(np.abs(o_off - a_off)))
                except Exception:
                    ok = False; break
            if ok:
                rows.append(row)

    if not rows:
        print(f"NOTHING PAIRED against an author mesh ({resolved} NIF(s) "
              f"resolved) -- that is 0/0, not a pass.")
        return 1
    print(f"paired {len(rows)} shape(s) against the author\n")
    print(f"{'arm':<24} {'mean seat error':>16} {'median':>10}")
    for root in roots:
        v = np.array([r[root.name] for r in rows if root.name in r])
        print(f"{root.name:<24} {v.mean():>15.4f}u {np.median(v):>9.4f}u")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
