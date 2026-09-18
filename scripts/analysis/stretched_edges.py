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

"""STRETCHED EDGES IN THE WRITTEN NIF, against the author's own mesh.

    python -m scripts.analysis.stretched_edges <arm_dir>[=label] ...
                                               [--ratio R] [--limit N]

THE GATE GAP THIS CLOSES. `damage_ledger` measured the fit chain by reading
STAGE DUMPS and counting, per pass, edges stretched past 1.5x their ENTRY
length. That is a FLOW ledger, and on this project flow and outcome have
already disagreed outright: `SURFACE_WARP_FIELD` cut the stretch `warp`
CREATES by 77% and the shipped mesh came out 26% MORE stretched, because
`conform` did more work downstream. The standing rule is therefore to judge the
WRITTEN NIF at the LAST stage, and no tracked tool did that -- so A2/A4-shaped
consolidations had no stretch row in the gate at all. This is that row.

The reference is the AUTHOR'S mesh, not the previous stage: an edge that leaves
the chain 1.4x its authored length is stretched whether one pass did it or six
did a little each.

WHY EDGE LENGTH. A garment BENDS around a bigger body, and bending preserves
every edge length; it must not STRETCH. So `|edge_out| / |edge_src|` separates
the legitimate change from the defect, needs no reference frame, and does not
degenerate on a 2u-wide strap -- where every Kabsch/Procrustes fit on this
project has, because the 1-ring is nearly collinear and the fitted rotation is
noise. See [[project_edge_length_metric]].

FOUR POPULATION TRAPS, each of which has already produced a wrong number here,
and what this tool does about each:

  * A POOLED TOTAL IS CARRIED BY ONE BIG MESH. Reading the `_SRC_NORMAL_FIX`
    ledger pooled said 1031 -> 1327, "worse"; per shape it was 6 better, 6
    worse, 22 unchanged, and ONE garment carried the whole delta. Every judged
    row here is a per-shape RATE (stretched edges / edges scored); the pooled
    count is printed as info and never judged.
  * `_0` AND `_1` ARE ONE GARMENT COUNTED TWICE. Both halves ship, so both are
    scored -- but the distinct-garment count is printed beside the shape count,
    because an "n shapes better" claim is n/2 garments.
  * DIVIDING BY THE AUTHORED LENGTH LETS SHORT EDGES LIE. 1% of one belt's
    edges inflated its mean deviation by 75%. The count row keeps the ledger's
    1.5x definition so the numbers stay comparable; the deviation row beside it
    is LENGTH-WEIGHTED, which needs no cutoff. Judge both -- if they disagree,
    the count is the one contaminated.
  * AN UNPAIRED POPULATION MAKES A MISSING SHAPE LOOK LIKE A WIN. Only shapes
    scored in EVERY arm are compared, and the drop reasons are counted and
    printed rather than left to shrink the denominator silently.

The author's own mesh for each converted piece comes from
`canonical_body.find_source`, which matches on GARMENT NAMES and falls back to
the BSA index, so a source shipped inside an archive is scored rather than
dropped. Body and physics-proxy shapes are excluded by name before the source
is looked up: the injected `BaseShape` is ours, the author never had it, and
asking `find_source` for it would refuse every body-swap piece in the pack.

Exit 0 = measured and printed, 2 = bad arguments, 3 = nothing measured
(0/0 is not a pass).
"""
from __future__ import annotations

import collections
import glob
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                              # noqa: E402

from src import nif_convert as nc                               # noqa: E402
from src import nif_io                                          # noqa: E402
from scripts.analysis import canonical_body as cb               # noqa: E402
from scripts.analysis._census_common import require_population   # noqa: E402

BODY_NAMES = set(nc.UBE_BODY_INJECT_NAMES) | {"3BA"}
PROXY_NAMES = {"VirtualBody", "VirtualGround", "SkirtCol", "ButtCol"}

# The ledger's own threshold, kept so a last-stage number can be read against
# the per-pass ones it replaces.
STRETCH_RATIO = 1.5

# An authored edge shorter than this is a degenerate/welded pair, and dividing
# by it produces a ratio in the thousands from sub-micron noise. Counted as an
# exclusion, never silently skipped.
MIN_SRC_EDGE = 1e-6

# A shape with almost no edges cannot carry a rate: one stretched edge out of
# nine is 11%, which would swing a median that a real mesh moves by 0.001.
MIN_EDGES = 64


def _renders(shape):
    """A collision proxy carries no texture and must not be scored as garment."""
    return any(v for v in (shape.textures or {}).values())


def unique_edges(tris) -> np.ndarray:
    """The undirected edge set of a triangle list, each edge once.

    Shared edges appear in two triangles; counting them twice would weight the
    interior of a mesh against its boundary for no reason.
    """
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e = np.sort(e, axis=1)
    return np.unique(e, axis=0)


def edge_stats(src_verts, out_verts, tris, ratio: float = STRETCH_RATIO):
    """(stretched, scored, rate, weighted_deviation) for one shape.

    `rate` is stretched/scored -- the per-shape normalisation, because a pooled
    count is a mesh-size ranking. `weighted_deviation` is
    `sum(|r-1| * L_src) / sum(L_src)`: a long edge counts for what it is worth
    visually, and no arbitrary short-edge cutoff is needed.

    Returns None when too few authored edges survive to carry a rate.
    """
    s = np.asarray(src_verts, dtype=np.float64)
    o = np.asarray(out_verts, dtype=np.float64)
    if s.shape != o.shape or s.ndim != 2 or s.shape[1] != 3:
        return None
    e = unique_edges(tris)
    if len(e) == 0:
        return None
    ls = np.linalg.norm(s[e[:, 0]] - s[e[:, 1]], axis=1)
    keep = ls >= MIN_SRC_EDGE
    if int(keep.sum()) < MIN_EDGES:
        return None
    ls = ls[keep]
    lo = np.linalg.norm(o[e[keep][:, 0]] - o[e[keep][:, 1]], axis=1)
    r = lo / ls
    stretched = int(np.count_nonzero(r > ratio))
    scored = int(len(r))
    dev = float(np.sum(np.abs(r - 1.0) * ls) / np.sum(ls))
    return stretched, scored, stretched / scored, dev


def duplicate_names(shapes) -> set:
    """Names carried by more than one shape in the same NIF."""
    seen, dup = set(), set()
    for s in shapes:
        if s.name in seen:
            dup.add(s.name)
        seen.add(s.name)
    return dup


def _garment(rel: str) -> str:
    """`a/b/robe_1.nif` -> `a/b/robe`. Both weight halves are ONE garment."""
    stem, ext = os.path.splitext(rel)
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def measure_arm(root: str, ratio: float = STRETCH_RATIO, limit: int = 0):
    """`rel|shape` -> (stretched, scored, rate, dev), plus counted exclusions."""
    out: dict = {}
    dropped: collections.Counter = collections.Counter()
    meshes = os.path.join(root, "meshes")
    # RESOLVED LAZILY, on the first NIF that actually needs an author source.
    # Resolving it up front turns an EMPTY arm into a machine-configuration
    # crash, which hides the one thing the caller has to be able to tell apart:
    # "nothing measured" (exit 3) is not "measured and clean".
    mods_root: "str | None" = None
    files = sorted(glob.glob(os.path.join(meshes, "**", "*.nif"), recursive=True))
    n = 0
    for p in files:
        rel = os.path.relpath(p, meshes)
        if not rel.lower().startswith("!ube" + os.sep):
            continue
        if "1stperson" in os.path.basename(p).lower():
            continue
        if limit and n >= limit:
            break
        n += 1
        try:
            nf = nif_io.open_nif_retry(p)
        except Exception:
            dropped["output NIF failed to load"] += 1
            continue
        # Two shapes of one name in one NIF: a `name -> shape` map keeps
        # whichever came last and loses the rest WITHOUT a count. Shapes here
        # are matched to the author BY NAME, so an ambiguous name is not
        # resolvable either way -- drop every one of them, visibly.
        dupes = duplicate_names(nf.shapes)
        ours = {}
        for s in nf.shapes:
            if s.name in BODY_NAMES or s.name in PROXY_NAMES:
                dropped["body or physics-proxy shape (ours, not the author's)"] += 1
                continue
            if not _renders(s):
                dropped["renders nothing (no texture)"] += 1
                continue
            if s.name in dupes:
                dropped["duplicate shape name in the NIF (ambiguous match)"] += 1
                continue
            ours[s.name] = s
        if not ours:
            continue
        # `rel` is `!UBE/<path>`; the author ships it without our output prefix.
        inner = rel.split(os.sep, 1)[1] if os.sep in rel else rel
        if mods_root is None:
            mods_root = cb._resolve_mods_root()
        src = cb.find_source(inner, list(ours), mods_root)
        if src is None:
            dropped["no author source resolved for the NIF"] += len(ours)
            continue
        try:
            have = {s.name: s for s in nif_io.open_nif_retry(str(src)).shapes}
        except Exception:
            dropped["author source failed to load"] += len(ours)
            continue
        for name, s in ours.items():
            a = have.get(name)
            if a is None:
                dropped["no author shape of that name"] += 1
                continue
            try:
                av, at = np.asarray(a.verts), a.tris
                ov = np.asarray(s.verts)
            except Exception:
                dropped["vertices unreadable"] += 1
                continue
            if av.shape != ov.shape:
                dropped["author vert count differs (retopologised)"] += 1
                continue
            st = edge_stats(av, ov, at, ratio)
            if st is None:
                dropped["under %d authored edges" % MIN_EDGES] += 1
                continue
            out[rel + "|" + name] = st
    return out, dropped


def _pct(x) -> str:
    return "%.4f%%" % (100.0 * x)


def _print_table(arms, data, common):
    print("  %-12s %10s %10s %10s %10s %10s"
          % ("arm", "pooled", "rate p50", "rate p90", "rate max", "dev p50"))
    base = None
    for lab, _ in arms:
        rates = np.array([data[lab][k][2] for k in common])
        devs = np.array([data[lab][k][3] for k in common])
        pooled = int(sum(data[lab][k][0] for k in common))
        print("  %-12s %10d %10s %10s %10s %10.4f"
              % (lab, pooled, _pct(np.median(rates)), _pct(np.percentile(rates, 90)),
                 _pct(rates.max()), float(np.median(devs))))
        if base is None:
            base = rates
        else:
            better = int(np.count_nonzero(rates < base - 1e-12))
            worse = int(np.count_nonzero(rates > base + 1e-12))
            print("  %-12s %10s %10s %10s   shapes better / worse / same"
                  " : %d / %d / %d"
                  % ("", "", "", "", better, worse, len(common) - better - worse))


def main(argv=None):
    raw = list(argv if argv is not None else sys.argv[1:])
    ratio, limit, args = STRETCH_RATIO, 0, []
    i = 0
    while i < len(raw):
        a = raw[i]
        # A VALUED FLAG MUST EAT ITS VALUE. Leaving it in the positionals made
        # two other tools in this toolkit print help and exit 2 on a valid
        # invocation -- the value was read as an arm directory.
        if a == "--ratio" and i + 1 < len(raw):
            ratio = float(raw[i + 1]); i += 2; continue
        if a == "--limit" and i + 1 < len(raw):
            limit = int(raw[i + 1]); i += 2; continue
        if a.startswith("--"):
            i += 1; continue
        args.append(a); i += 1
    if not args:
        print(__doc__)
        return 2

    arms = []
    for a in args:
        d, _, lab = a.partition("=")
        arms.append((lab or os.path.basename(d.rstrip("/\\")), d))

    data, drops = {}, {}
    for lab, d in arms:
        if not os.path.isdir(os.path.join(d, "meshes")):
            print("MISSING: %s" % d)
            return 2
        data[lab], drops[lab] = measure_arm(d, ratio, limit)
        print("  measured %-24s %d shapes" % (lab, len(data[lab])))
    first = arms[0][0]
    for reason, n in sorted(drops[first].items(), key=lambda kv: -kv[1]):
        print("    not scored in %-14s %5d  %s" % (first, n, reason))

    common = sorted(set.intersection(*[set(v) for v in data.values()]))
    garments = {_garment(k.split("|", 1)[0]) + "|" + k.split("|", 1)[1]
                for k in common}
    print()
    print("=" * 78)
    print("STRETCHED EDGES vs THE AUTHOR, LAST STAGE   (ratio > %.2fx)" % ratio)
    print("=" * 78)
    print("  shapes scored in EVERY arm                : %d" % len(common))
    print("  distinct garments behind them             : %d" % len(garments))
    # `_0` and `_1` are one garment at two weights. An "n shapes worse" claim
    # over this population is n/2 garments, and one large mesh counted twice
    # has already carried a pooled total on its own.
    require_population(common, "shapes scored in every arm")
    print()
    _print_table(arms, data, common)
    print()
    print("  rate    = edges over %.2fx their AUTHORED length / edges scored," % ratio)
    print("            per shape. JUDGED. The pooled count is mesh size.")
    print("  dev p50 = median length-weighted mean |ratio-1|. JUDGED, and it")
    print("            needs no short-edge cutoff, unlike the count.")
    print("  pooled  = info only: one big mesh carries it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
