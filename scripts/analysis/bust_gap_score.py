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

"""BUST GAP vs the AUTHOR, over BOTH convert paths.

    python -m scripts.analysis.bust_gap_score <arm_dir>[=label] ...

Prints, per arm: our bust standoff, the gap to the author, the penetration
counter-metric, and an arm-fired assertion read off GEOMETRY -- then repeats the
whole thing PER PATH, because the copy and body-swap paths run different
clearance code and a pooled median lets whichever path has more shapes write the
verdict.

TWO DEFECTS IN `score_arms.py` THAT THIS EXISTS TO FIX, both of which shipped a
believable wrong answer rather than an error:

1. IT SCORED ONE PATH AND CALLED IT THE PACK. That harness builds its body
   reference from a `BaseShape` injected INSIDE the converted NIF, so its
   population is silently the body-swap pieces alone -- 35 files of 184 on the
   pack this was written against. A change landing mostly on the COPY path
   therefore scores as a flat line: measured directly, one arm changed 83 of 184
   files while only 7 of the 35 body-swap `_1` files moved, the medians came back
   identical to three decimals, and the harness printed "ARM DID NOT FIRE"
   against an arm that had plainly fired. Same trap as the shear census that read
   24% until the unreachable shapes were excluded: SCORE THE POPULATION THE
   CHANGE CAN REACH. Copy-path pieces carry no body, so they are measured against
   the external UBE body instead of being dropped.

2. IT READ THE AUTHOR'S STANDOFF AS 0.000u. The author's own source body can ship
   every normal zero-length (on the pack this was written against, 18436 of
   18436). Taking those raw makes `dot(cloth - body, n)` read 0 for every vertex,
   so the author's bust standoff comes out at zero and every "gap" silently
   becomes our own standoff -- a metric that can never say the author was looser.
   That is the BUG-00 fail-open shape, reproduced inside the tool measuring the
   converter fix for it. `_unit_body_normals` refuses instead: it takes the
   converter's HARDENED normals and aborts the run if they are still degenerate.

A cross-file comparison is void unless the frames are reconciled, so every vertex
is lifted to WORLD space first. The band is taken on the BODY
(`body_zones.breast_mask`) and read through each cloth vertex's nearest body
vertex, never as a z-slab over cloth -- a slab catches sleeves and collars that
happen to pass through the same heights.

NOTHING HERE IS HARDCODED TO ONE MACHINE OR ONE MOD. The bodies come from
`canonical_body`, and the author's own mesh for each converted piece is found by
`canonical_body.find_source`, which matches on GARMENT NAMES and falls back to
the BSA index -- so a source shipped inside an archive is scored rather than
silently dropped.

WEIGHTS: both weights, each against its OWN bodies, reported SEPARATELY. It is
defect 1 above again, on the weight axis: this scores shapes and counts
penetrating VERTICES, and a `_0` mesh is separately authored and fitted by its
own run of the clearance code, so it is scored too -- otherwise a change landing
mostly on `_0` geometry reads as "NOT FIRED".

Each weight is measured against the bodies THAT weight was built on:
  * body-swap pieces -- the body injected into the NIF itself, as always;
  * copy-path pieces -- `nc._find_ube_femalebody(weight)`, the converter's own
    resolver, i.e. exactly the body the converter fitted that weight to (every
    tier of it is weight-specific; it never returns the other weight);
  * the AUTHOR -- `canonical_body.canonical_cbbe(weight=...)`, whose weight 0 is
    the SIBLING of the weight-1 body and never falls back to it.

The GATED rows are still weight 1, byte-for-byte what this tool always printed:
`acceptance.py` reads the per-path `control|candidate bust p50 ... gap p50 ...
pen N` rows under the "body-swap only" / "copy path only" headers. Weight 0
follows in its own block, worded so the gate cannot read it (see
`_table_lines`). Gating on weight 0 is a separate change to `acceptance.py`.
"""
from __future__ import annotations

import collections
import os
import sys
import glob
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                              # noqa: E402
from scipy.spatial import cKDTree                               # noqa: E402

from src import nif_convert as nc                               # noqa: E402
from src import body_zones as bz, nif_io                        # noqa: E402
from scripts.analysis import canonical_body as cb               # noqa: E402

BODY_NAMES = set(nc.UBE_BODY_INJECT_NAMES) | {"3BA"}
PROXY_NAMES = {"VirtualBody", "VirtualGround", "SkirtCol", "ButtCol"}

# A shape that barely grazes the band says nothing about the bust, and its median
# is dominated by whichever few verts happened to land there.
MIN_BAND = 30

# What counts as "this shape moved" for the arm-fired assertion, in units of
# standoff. Well above serialization noise, well below any change worth shipping.
MOVED_EPS = 0.01


def _world(shape):
    """Shape verts in WORLD space -- the only frame two files may be compared in."""
    return nc._verts_skin_to_world(np.asarray(shape.verts, np.float64),
                                   nc._shape_global_to_skin(shape))


def _unit_body_normals(shape):
    """Unit outward normals for a BODY shape, via the converter's HARDENED fetch.

    NEVER the stored array. See defect 2 in the module docstring: a source body
    with all-zero normals reads as "the author fitted everything skin-tight", and
    the gap column then measures nothing at all. Abort rather than print it.
    """
    n = nc._body_normals_or_compute(shape)
    if n is None:
        raise SystemExit("body normals unrecoverable -- measurement would be VOID")
    n = np.asarray(n, np.float64)
    if float((np.linalg.norm(n, axis=1) < 1e-6).mean()) > 0.5:
        raise SystemExit("body normals still degenerate -- measurement VOID")
    return n / np.clip(np.linalg.norm(n, axis=1, keepdims=True), 1e-9, None)


def _make_ref(shape):
    """(verts, normals, tree, breast mask) for a body, in world space."""
    bv = _world(shape)
    return (bv, _unit_body_normals(shape), cKDTree(bv),
            np.asarray(bz.breast_mask(bv), bool))


_EXT_BODY: dict = {}


def _external_body(weight):
    """The UBE body a COPY-path piece was fitted to. Cached per weight."""
    if weight not in _EXT_BODY:
        p = nc._find_ube_femalebody(weight)
        if p is None:
            _EXT_BODY[weight] = None
        else:
            nf = nif_io.open_nif_retry(str(p))
            _EXT_BODY[weight] = _make_ref(
                max(nf.shapes, key=lambda s: len(s.verts)))
    return _EXT_BODY[weight]


def _body_for(nf, weight):
    """(ref, is_body_swap). A piece with no injected body is COPY path."""
    inj = next((s for s in nf.shapes
                if s.name in BODY_NAMES and len(s.verts) > 20000), None)
    if inj is None:
        return _external_body(weight), False
    return _make_ref(inj), True


def band_stats(garment_world, ref):
    """(median |standoff|, penetrating verts, worst) over the bust band, or None.

    Read through each cloth vertex's NEAREST BODY VERTEX, so the band is a
    property of the body rather than of the cloth's own height.
    """
    bv, bn, tree, bmask = ref
    _d, idx = tree.query(garment_world)
    sel = bmask[idx]
    if int(sel.sum()) < MIN_BAND:
        return None
    gsel, isel = garment_world[sel], idx[sel]
    signed = np.einsum('ij,ij->i', gsel - bv[isel], bn[isel])
    return (float(np.median(np.abs(signed))),
            int((signed < -1e-4).sum()),
            float(signed.min()))


def _renders(shape):
    """A collision proxy carries no texture and must not be scored as garment."""
    return any(v for v in (shape.textures or {}).values())


def measure_arm(root, weight="_1"):
    """`rel|shape` -> (bust median, penetrating verts, worst), plus a path map.

    `weight` picks the files (`*_1.nif` or `*_0.nif`) AND the copy-path body
    they are measured against, which must be the one that weight was fitted to.

    Also returns a tally of every shape NOT scored and why. 0/0 is not a pass,
    and neither is a population whose exclusions are invisible: `VirtualBody` is
    in the body-name set and a 137-vert one is a physics proxy rather than a
    body, which is a judgement worth showing rather than making silently.
    """
    out, is_swap = {}, {}
    dropped = collections.Counter()
    meshes = os.path.join(root, "meshes")
    for p in sorted(glob.glob(os.path.join(meshes, "**", "*" + weight + ".nif"),
                              recursive=True)):
        rel = os.path.relpath(p, meshes)
        if not rel.lower().startswith("!ube" + os.sep):
            continue
        if "1stperson" in os.path.basename(p).lower():
            continue
        try:
            nf = nif_io.open_nif_retry(p)
        except Exception:
            continue
        ref, swap = _body_for(nf, weight)
        if ref is None:
            dropped["no body reference could be resolved"] += 1
            continue
        for s in nf.shapes:
            if s.name in BODY_NAMES or s.name in PROXY_NAMES:
                dropped["body or physics-proxy shape"] += 1
                continue
            if not _renders(s):
                dropped["renders nothing (no texture)"] += 1
                continue
            r = band_stats(_world(s), ref)
            if r is None:
                dropped["under %d verts in the bust band" % MIN_BAND] += 1
                continue
            key = rel + "|" + s.name
            out[key], is_swap[key] = r, swap
    return out, is_swap, dropped


def author_baseline(keys, mods_root=None, weight="_1"):
    """The SAME shapes from the author's own build, against the body they were
    fitted to -- the authored relationship, measured in the author's own frame.

    The source is located by `canonical_body.find_source`, which scores mods by
    how many of the converted GARMENT names they carry and requires every one of
    them. "The last mod providing this path" is not good enough: two mods can
    ship one path with different geometry, and picking the wrong one has already
    inverted a measured delta from +0.7 to +83.3 -- from "the author did it" to
    "we did it".
    """
    if mods_root is None:
        mods_root = cb._resolve_mods_root()
    bpath, bname = cb.canonical_cbbe(mods_root, weight=weight)
    b3 = nif_io.open_nif_retry(str(bpath))
    ref = _make_ref(next(s for s in b3.shapes if s.name == bname))

    by_rel: dict = {}
    for k in keys:
        rel, shape = k.split("|", 1)
        by_rel.setdefault(rel, []).append(shape)

    out: dict = {}
    for rel, shapes in by_rel.items():
        # `rel` is `!UBE.../path`; the author ships it without our output prefix.
        inner = rel.split(os.sep, 1)[1] if os.sep in rel else rel
        src = cb.find_source(inner, shapes, mods_root)
        if src is None:
            continue
        try:
            have = {s.name: s for s in nif_io.open_nif_retry(str(src)).shapes}
        except Exception:
            continue
        for shape in shapes:
            sh = have.get(shape)
            if sh is None:
                continue
            r = band_stats(_world(sh), ref)
            if r is not None:
                out[rel + "|" + shape] = r[0]
    return out


# Every weight-0 arm label starts with this, so no weight-0 row can match the
# `control|candidate bust p50 ...` pattern acceptance.py reads the gate from.
W0_PREFIX = "w0 "


def _table_lines(arms, data, common, a_vals, swap, w0=False):
    """The score table, as lines. Weight 1 is exactly what this always printed.

    `w0=True` WORDS IT SO acceptance.py CANNOT READ IT. That parser walks the
    whole output line by line: "body-swap only" / "copy path only" switch a
    section that then STAYS switched for every later line, a matching
    `control|candidate bust p50 ... gap p50 ... pen N` row OVERWRITES the value
    it read before (last match wins, not first), and a line matching its
    `SKIP_NOTE` marks that path's row as unmeasured. A weight-0 row matching
    any of them would silently replace or switch off a gated weight-1 value. So
    weight 0 prefixes every label with `w0 `, names its paths "weight 0 /
    body-swap" and "weight 0 / copy path", and words its too-few note
    differently.
    """
    tag = W0_PREFIX if w0 else ""
    out = []
    hdr = ("%-26s %9s %9s %9s %10s %10s %9s  %-12s"
           % ("arm, weight 0" if w0 else "arm", "bust p50", "gap p50",
              "gap p90", "shapes >", "pen verts", "worst", "moved"))
    out.append(hdr)
    out.append("-" * len(hdr))
    ctrl = base_pen = None
    for lab, _d in arms:
        m = data[lab]
        b = np.array([m[k][0] for k in common])
        gap = b - a_vals
        pen = sum(m[k][1] for k in common)
        worst = min([m[k][2] for k in common] + [0.0])
        if ctrl is None:
            ctrl, base_pen, moved = b, pen, "control"
        else:
            n = int((np.abs(b - ctrl) > MOVED_EPS).sum())
            moved = "%d/%d" % (n, len(common)) if n else "0 <== NOT FIRED"
        mark = "" if pen <= base_pen else "  (+%d)" % (pen - base_pen)
        out.append("%-26s %9.3f %+9.3f %+9.3f %10d %10d %9.3f  %-12s%s"
                   % (tag + lab, np.median(b), np.median(gap),
                      np.percentile(gap, 90), int((gap > 0.05).sum()), pen,
                      worst, moved, mark))

    for want, name in ((True, "body-swap"), (False, "copy path")):
        idx = [i for i, k in enumerate(common) if bool(swap.get(k)) is want]
        if len(idx) < 5:
            if w0:
                out.append("\n  weight 0 / %s: %d shapes, too few to report"
                           % (name, len(idx)))
            else:
                out.append("\n  %s: only %d shapes -- not reported"
                           % (name, len(idx)))
            continue
        if w0:
            out.append("\n  weight 0 / %s: %d shapes" % (name, len(idx)))
        else:
            out.append("\n  %s only (%d shapes)" % (name, len(idx)))
        av = a_vals[idx]
        for lab, _d in arms:
            b = np.array([data[lab][k][0] for k in common])[idx]
            pen = sum(data[lab][common[i]][1] for i in idx)
            out.append("    %-24s bust p50 %6.3f   gap p50 %+6.3f   pen %d"
                       % (tag + lab, np.median(b), np.median(b - av), pen))
    return out


def _print_table(arms, data, common, a_vals, swap):
    for line in _table_lines(arms, data, common, a_vals, swap):
        print(line)


def main(argv=None):
    args = [a for a in (argv if argv is not None else sys.argv[1:])
            if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    arms = []
    for a in args:
        d, _, lab = a.partition("=")
        arms.append((lab or os.path.basename(d.rstrip("/\\")), d))
    # The exit codes are spelled out HERE, not passed through: `tool_map` reads
    # a tool's gate from the literal returns of `main`, and `return rc` would
    # hide that this tool exits 1 on an empty population.
    rc = _report_weight1(arms)
    if rc == 2:
        return 2                   # an arm dir is missing; nothing to add
    for line in _weight0_block(arms):
        print(line)
    if rc == 1:
        return 1                   # 0/0 at weight 1 is not a pass
    return 0


def _weight0_block(arms):
    """Score the `_0` files and return the report lines.

    INFO in this tool: it never changes the exit code, which stays the gated
    weight-1 verdict. A weight-0 author body that cannot be resolved is reported
    as NOT MEASURED rather than aborting -- the gated report has already printed
    by the time this runs. Every line is worded for `_table_lines(w0=True)`'s
    reason: nothing here may match what acceptance.py parses.
    """
    out = ["", "=== WEIGHT 0: `_0` files, each against the bodies weight 0 was "
               "built on (info; acceptance.py does not gate on this yet) ==="]
    data, swap_of, drops = {}, {}, {}
    for lab, d in arms:
        data[lab], swap_of[lab], drops[lab] = measure_arm(d, "_0")
        out.append("  weight 0: measured %-24s %d shapes"
                   % (lab, len(data[lab])))
    first = arms[0][0]
    for reason, n in sorted(drops[first].items(), key=lambda kv: -kv[1]):
        out.append("    weight 0: not scored in %-14s %5d  %s"
                   % (first, n, reason))
    common = set.intersection(*[set(v) for v in data.values()])
    try:
        auth = author_baseline(common, weight="_0")
    except FileNotFoundError as e:
        out.append("weight 0 NOT MEASURED -- %s" % e)
        return out
    dropped = len(common) - len(set(common) & set(auth))
    common = sorted(k for k in common if k in auth)
    swap = swap_of[first]
    n_swap = sum(1 for k in common if swap.get(k))
    out.append("")
    out.append("WEIGHT-0 POPULATION: %d shapes in every arm AND in the author's "
               "weight-0 build" % len(common))
    out.append("  weight 0: dropped, no matching author shape : %d" % dropped)
    out.append("  weight 0: body-swap / copy path             : %d / %d"
               % (n_swap, len(common) - n_swap))
    if not common:
        out.append("  weight 0: nothing measured")
        return out
    a_vals = np.array([auth[k] for k in common])
    out.append("  weight 0: AUTHOR bust standoff p50          : %.3fu"
               % np.median(a_vals))
    out.append("")
    out += _table_lines(arms, data, common, a_vals, swap, w0=True)
    return out


def _report_weight1(arms):
    """The GATED report: weight 1, byte-for-byte what this tool always printed.

    `acceptance.py` parses its per-path rows, so nothing here may change
    without changing the gate with it."""
    data, swap_of, drops = {}, {}, {}
    for lab, d in arms:
        if not os.path.isdir(os.path.join(d, "meshes")):
            print("MISSING: %s" % d)
            return 2
        data[lab], swap_of[lab], drops[lab] = measure_arm(d)
        print("  measured %-24s %d shapes" % (lab, len(data[lab])))
    first = arms[0][0]
    for reason, n in sorted(drops[first].items(), key=lambda kv: -kv[1]):
        print("    not scored in %-14s %5d  %s" % (first, n, reason))

    common = set.intersection(*[set(v) for v in data.values()])
    auth = author_baseline(common)
    dropped = len(common) - len(set(common) & set(auth))
    common = sorted(k for k in common if k in auth)
    swap = swap_of[arms[0][0]]
    n_swap = sum(1 for k in common if swap.get(k))
    print()
    print("POPULATION: %d shapes in EVERY arm AND in the author's build"
          % len(common))
    print("  dropped, no matching author shape       : %d" % dropped)
    print("  of the population: body-swap / copy path: %d / %d"
          % (n_swap, len(common) - n_swap))
    if not common:
        print("  !! 0/0 IS NOT A PASS")
        return 1
    a_vals = np.array([auth[k] for k in common])
    print("  AUTHOR bust standoff p50                : %.3fu"
          % np.median(a_vals))
    print()
    _print_table(arms, data, common, a_vals, swap)
    print()
    print("  gap = ours - author, each against the body it was fitted to,")
    print("        both lifted to WORLD space.")
    print("  pen verts / worst = the COUNTER-METRIC: cloth behind the skin.")
    print("  moved = shapes whose bust standoff differs from the control by")
    print("          >%.2fu -- the arm-fired assertion, read off geometry."
          % MOVED_EPS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
