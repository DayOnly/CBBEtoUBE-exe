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

"""IS THE CLOTHING AS SNUG AS ITS AUTHOR MADE IT? -- author-relative, per path.

THE QUESTION. `#phase1-conform` states the asymmetry in the source itself:
"Phase 1 carries TWO `inflate_armor_outward` call sites and NO conform, while
phase 2 has both. So a phase-1 piece gets clearance ADDED with nothing to reel
it back to the author's fit -- it inflates and never returns. Phase 1 is the
large majority of files, so this asymmetry ... is why a tightly-fitted piece
ships standing off the body." `PHASE1_CONFORM` is DEFAULT OFF "until the census
+ clipping A/B justify it". This is that census.

THE METRIC IS AUTHOR-RELATIVE, NOT ABSOLUTE. Scoring standoff against zero says
a voluminous plate is "wrong" and a bodysuit is "right"; scoring it against the
AUTHOR says whether WE changed the fit ([[feedback_preserve_authored_relationships]],
[[feedback_baseline_not_author]]). So per garment vertex:

    authored = distance(source vert,  CBBE base body)
    shipped  = distance(output vert,  UBE body)
    ratio    = shipped / authored          1.0 = fit preserved, >1 = looser

VERTEX PAIRING IS BY INDEX and only valid because the copy path MOVES vertices
rather than rebuilding them -- the vert count is asserted equal per shape, and a
shape whose count changed is EXCLUDED and counted, never silently index-paired.

THE FRAME IS CHOSEN BY EVIDENCE, NOT ASSUMED -- and the first version of this
file assumed, which made every number it printed void. SOURCE nifs store verts in
the shape's SKIN frame and need `_verts_skin_to_world`; the converter's OUTPUT
already stores WORLD verts, so applying the same rule to both transforms the
output twice and throws it hundreds of units off the body. Each shape is
therefore measured in BOTH candidate frames and the one landing nearer its body
wins. When the two frames AGREE (an identity transform, which is most shapes)
either will do -- a guard that excluded "ambiguous" shapes cut a 20-shape sample
to 3, because agreement was being read as ambiguity ([[project_phase1_conform_verdict]]).

NEAREST-VERTEX distance, not a closest-point-on-plane: the plane form produced
void numbers here once ([[project_closest_point_plane_bug]]). Nearest-vertex is
a slight over-estimate on coarse bodies, but it is the SAME estimator on both
sides of the ratio, so the bias cancels in the comparison -- which is the number
being read.

WEIGHTS: `_1` only -- a DECLARED BLIND SPOT, not a correct scope. The unit is
PER SHAPE (a median ratio over its hugging verts), not a per-garment rate: the
converter fits each weight separately against its own body, so the two halves'
standoff is not one shared quantity, and a lost fit at weight 0 is invisible.

DO NOT fix it by swapping the glob. BOTH bodies in the ratio above are pinned
to weight 1 at their call sites: `canonical_cbbe(weight="_1")` and
`canonical_ube(weight="_1")`. A `_0` garment would be read against weight-1
bodies on both sides, and the hug mask and the ambiguous-frame exclusion are
taken against the same pinned bodies. Unlike the estimator bias above, that
error does NOT cancel -- it is a different body on each side for every `_0` row.

The fix is small, because both helpers already take a weight; only these two
call sites pass the literal `"_1"`. Resolve both from each file's own suffix,
then widen. This census feeds an open investigation; widening it moves numbers
already recorded there, so do it as its own change with an old-vs-new
comparison.

REFERENCE BODIES: BodySlide's zeroed builds as the game loads them
(src/zeroed_body.py, via canonical_body). The AUTHOR side used to be the
converter's `_find_cbbe_base_body`, which picked the 3BA mod folder's own
preset femalebody -- up to 1.97u off the body the garments were built on --
so every authored standoff here was read against the wrong body. Re-run on
the zeroed body 2026-09-21 (1917 shapes scored, was 1957): body-swap median
ratio 1.202 -> 1.058 (p90 1.934 -> 1.540), copy 1.150 -> 1.141, and the printed
reading went from "LOOSER than authored" to "fit preserved" on both paths
(copy by 0.009 under the 1.15 line).

NO HELPER EXCLUSION -- an open gap. A body-reference or collision copy that
sits ON the body passes every filter this census applies; on the right body its
authored standoff is ~0, and the 0.05u floor in the ratio turns a 0.3-0.7u
shipped offset into a ratio of 5-15. On that run 17 of the 40 loosest shapes
carry a proxy-token name (seat_error_vs_author's `_is_proxy_name`: `3BA Ref`,
`VirtualBody`, `collision body`), and 18 scored shapes have an authored median
under 0.05u (none did on the old body). Dropping all 85 proxy-named shapes moves
neither median by more than 0.004, so the per-path table stands -- but read the
LOOSEST list as helpers first.
"""
import argparse
import collections
import json
import os
import statistics as stats
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from src import paths                                    # noqa: E402
_lay = paths.discover_layout()
paths.export_to_env(_lay)
from pyn import pynifly                                  # noqa: E402
from src import nif_convert as nc                        # noqa: E402
from scripts.analysis import standoff_audit as sa        # noqa: E402
from scripts.analysis.canonical_body import (canonical_cbbe,  # noqa: E402
                                             canonical_ube)

# NO HARD-CODED MODLIST PATH. This repo is public, and the deployed output dir
# moves with the MO2 instance -- resolve it, or take --pack.
_ap = argparse.ArgumentParser()
_ap.add_argument("--pack", default=None,
                 help="converted pack root (default: the deployed output)")
_ap.add_argument("--limit", type=int, default=0)
_ap.add_argument("--out", default="snugness.json")
ARGS = _ap.parse_args()
PACK = (Path(ARGS.pack) if ARGS.pack
        else Path(os.environ.get("CBBE2UBE_OUTPUT_DIR", "")) / "meshes" / "!UBE")
if not PACK.is_dir():
    raise SystemExit(f"pack not found: {PACK} -- pass --pack <dir>")
_en = paths.enabled_mods(_lay)
_srcmods = [d for d in sorted(_lay.mods_root.iterdir())
            if d.is_dir() and (_en is None or d.name in _en)
            and "CBBEtoUBE" not in d.name and "UBE Converter" not in d.name]

MIN_SCORED = 25          # below this the run measured nothing worth reading
MIN_VERTS = 200          # a stud or buckle says nothing about snugness
HUG_MAX = 3.0            # authored standoff above this is drape, not a hug


def body_tree(path):
    """Tree + vert count, in whichever frame puts the body at human height.

    The same evidence rule as `pick_frame`, applied to the reference itself: a
    body whose transform is identity must NOT be re-transformed. Asserting the
    result stands upright is cheap and catches a silently mis-framed reference,
    which would corrupt EVERY row rather than one.
    """
    nf = pynifly.NifFile(filepath=str(path))
    best, n = None, -1
    for s in nf.shapes:
        if len(s.verts) > n:
            best, n = s, len(s.verts)
    raw = np.asarray(best.verts, np.float64)
    w = nc._verts_skin_to_world(raw, nc._shape_global_to_skin(best))
    upright = (raw[:, 2].max() - raw[:, 2].min()) > 50.0 and raw[:, 2].min() > -20
    v = raw if upright else w
    if not ((v[:, 2].max() - v[:, 2].min()) > 50.0 and v[:, 2].min() > -20):
        raise SystemExit(f"reference body {Path(path).name} is not upright in "
                         f"either frame -- every distance below would be void")
    return cKDTree(v), n


AMBIGUOUS = 3.0          # a MATERIAL frame disagreement must be this decisive
AGREE_U = 0.25           # closer than this and the two frames agree


def pick_frame(shape, tree):
    """(verts, which, margin) -- the frame that lands nearer `tree`."""
    raw = np.asarray(shape.verts, np.float64)
    try:
        w = nc._verts_skin_to_world(raw, nc._shape_global_to_skin(shape))
    except Exception:
        return raw, "raw", float("inf")
    # THE RULE LIVES IN `standoff_audit`, ONCE. This file is where the rule was
    # first worked out, and its docstring above still records why -- but a rule
    # kept in three places is one that gets changed in one of them. The margin
    # this file needs for its AMBIGUOUS exclusion is what `with_margin` is for.
    return sa.pick_frame(raw, w, tree, agree_u=AGREE_U, with_margin=True)


def source_for(rel: Path):
    sub = rel.parent.as_posix()
    hit = None
    for d in _srcmods:
        if (d / "meshes" / sub.replace("/", os.sep) / rel.name).is_file():
            hit = d
    return (hit / "meshes" / sub.replace("/", os.sep) / rel.name) if hit else None


def main():
    try:
        cb = canonical_cbbe(weight="_1")[0]
        ub = canonical_ube(weight="_1")[0]
    except FileNotFoundError as e:
        raise SystemExit(f"cannot locate both bodies -- census impossible: {e}")
    cbt, cbn = body_tree(cb)
    ubt, ubn = body_tree(ub)
    print(f"CBBE base : {cbn} verts  {Path(cb).name}")
    print(f"UBE body  : {ubn} verts  {Path(ub).name}")

    tot = collections.Counter()
    frames = collections.Counter()
    per_path = collections.defaultdict(list)   # path -> [per-shape median ratio]
    per_piece = collections.defaultdict(lambda: collections.defaultdict(list))
    worst = []
    nifs = sorted(PACK.rglob("*_1.nif"))
    if ARGS.limit:
        nifs = nifs[:ARGS.limit]
    print(f"{len(nifs)} `_1` pack NIF(s)", flush=True)
    for i, p in enumerate(nifs):
        if i % 300 == 0:
            print(f"  ...{i}", flush=True)
        try:
            dn = pynifly.NifFile(filepath=str(p))
        except Exception:
            tot["pack nif unreadable (EXCLUDED)"] += 1
            continue
        # BaseShape present => the body-swap path. The status line is
        # authoritative during a run; on a finished pack this is the marker.
        path = "body-swap" if any(s.name == "BaseShape" for s in dn.shapes) \
            else "copy"
        rel = p.relative_to(PACK)
        src = source_for(rel)
        if src is None:
            tot["source NOT resolvable (EXCLUDED)"] += 1
            continue
        try:
            sn = pynifly.NifFile(filepath=str(src))
        except Exception:
            tot["source unreadable (EXCLUDED)"] += 1
            continue
        srcs = {s.name: s for s in sn.shapes}
        for s in dn.shapes:
            a = srcs.get(s.name)
            if a is None:
                tot["shape not in source (injected/renamed, EXCLUDED)"] += 1
                continue
            if len(s.verts) != len(a.verts):
                tot["vert count changed (EXCLUDED, cannot pair by index)"] += 1
                continue
            if len(s.verts) < MIN_VERTS:
                tot["too few verts to judge (EXCLUDED)"] += 1
                continue
            av, aw, am = pick_frame(a, cbt)
            ov, ow, om = pick_frame(s, ubt)
            if min(am, om) < AMBIGUOUS:
                tot["frame AMBIGUOUS (EXCLUDED, not guessed)"] += 1
                continue
            frames[f"source={aw} output={ow}"] += 1
            authored, _ = cbt.query(av)
            shipped, _ = ubt.query(ov)
            hug = authored <= HUG_MAX
            if hug.sum() < MIN_VERTS:
                tot["no hugging region (drape/plate, out of scope)"] += 1
                continue
            tot[f"shapes scored: {path}"] += 1
            r = float(np.median(shipped[hug] / np.maximum(authored[hug], 0.05)))
            d = float(np.median(shipped[hug] - authored[hug]))
            per_path[path].append(r)
            per_piece[path][str(rel)].append(r)
            worst.append((r, d, path, str(rel), s.name,
                          float(np.median(authored[hug])),
                          float(np.median(shipped[hug]))))

    print("\n=== POPULATION (every exclusion counted) ===")
    for k, v in tot.most_common():
        print(f"  {v:6}  {k}")

    print("\n=== SNUGNESS vs THE AUTHOR, per convert path ===")
    print(f"{'path':<12}{'shapes':>8}{'pieces':>8}{'median ratio':>14}"
          f"{'p90 ratio':>11}   reading")
    print("-" * 92)
    for pth in ("copy", "body-swap"):
        v = per_path.get(pth) or []
        if not v:
            continue
        v.sort()
        med = stats.median(v)
        p90 = v[int(0.9 * (len(v) - 1))]
        read = ("fit preserved" if med < 1.15 else
                "LOOSER than authored" if med < 1.6 else
                "MUCH looser than authored")
        print(f"{pth:<12}{len(v):>8}{len(per_piece[pth]):>8}{med:>14.3f}"
              f"{p90:>11.3f}   {read}")
    print("  ratio = shipped standoff / authored standoff, hugging verts only "
          "(authored <= %.1fu)" % HUG_MAX)

    worst.sort(reverse=True)
    print("\n=== LOOSEST SHAPES (worst 15) ===")
    print(f"{'ratio':>7}{'delta':>8}  {'path':<10}{'authored':>9}{'shipped':>9}"
          f"  piece :: shape")
    for r, d, pth, rel, nm, a, sh in worst[:15]:
        print(f"{r:>7.2f}{d:>8.2f}  {pth:<10}{a:>9.2f}{sh:>9.2f}  "
              f"{rel[:52]} :: {nm[:22]}")
    out = Path(ARGS.out)
    out.write_text(json.dumps(
        [{"ratio": r, "delta": d, "path": pth, "nif": rel, "shape": nm,
          "authored": a, "shipped": sh} for r, d, pth, rel, nm, a, sh in worst],
        indent=1), encoding="utf-8")
    print(f"\nwrote {out.name} ({len(worst)} scored shape(s))")
    # A census that scored almost nothing is not a clean bill of health, and
    # the difference is invisible in the table above. 0/0 is not a pass.
    if len(worst) < MIN_SCORED:
        print(f"ONLY {len(worst)} shape(s) scored (floor {MIN_SCORED}) -- this "
              f"census measured essentially NOTHING.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
