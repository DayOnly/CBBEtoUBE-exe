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

"""How much ROOM is there over the body's nipple, per arm?

    python -m scripts.analysis.nipple_clearance <arm_dir>[=label] ...

MEASURE THE MARGIN, NOT THE BREACH. Binary exposure only fires once the margin
has already gone, so a pass/fail exposure count cannot warn you before a report
arrives -- and one did: "leather armor now has nipples poking through again but
by the smallest amount", against a build whose exposure counters were clean. The
margin is what the player's slider or preset eats into before anything shows.

So: cast each nipple-TIP body vertex along its own outward normal and take the
distance to the FIRST garment triangle. Reported as p50 / p05 / min, because the
median moves with the whole pack while p05 is where the next report comes from.

A RAY, NOT A NEAREST-POINT NORMAL. Nearest-point normals FLIP on curved
geometry and fabricated a 226-vertex "defect" at 4.633u on a glove that the ray
scores at 0.000% -- see `project_extremity_digit_refit_gap`. `ray_first_hit` is
the same primitive the containment metric settled on.

The tip mask is `_body_nipple_weight >= 0.75 * max`. It is NARROWER than the
region `#authored-nipple-exempt` protects, and this docstring used to claim the
two were the SAME mask -- they are not, in three independent ways. Measured on
the UBE body (peak weight 0.5635) on 2026-09-09:

    >= 0.75 * max   TIP_WEIGHT_FRAC, what this harness SCORES     1020 verts
    >= 0.50 * max   AUTHORED_NIPPLE_EXEMPT, where the converter's
                    exemption STARTS                              1294 verts
    the 0.50-0.75 band, scored by NEITHER                          274 (+27%)

and the converter then DILATES its mask by `AUTHORED_NIPPLE_RADIUS` (2.0u) and
`AUTHORED_NIPPLE_RINGS` (2) over the garment's own topology, so the protected
SURFACE is wider again.

SO A CHANGE CONFINED TO THAT BAND MOVES THE FIX AND NOT THIS ROW. Read the tip
figures as "the innermost 1020 verts", never as "the region the exemption acts
on". The constant is deliberately NOT moved to 0.5: every tip number on record
was taken at 0.75, and silently redefining the metric would void them all
without saying so. `tests/test_nipple_clearance_mask.py` pins both constants so
they cannot drift apart unnoticed again.

EXCLUSIONS ARE COUNTED, never silent. A piece is scored only where at least 20
tip rays hit garment within 12u -- below that the garment does not cover the
nipple at all (a boot, a gauntlet, an open-chested corset) and a "clearance" for
it would be measuring the room over bare skin. Pieces missing from any arm are
dropped too, so every arm is scored on ONE population.

Nothing here is hardcoded to a machine or a mod: the body comes from
`canonical_body.canonical_ube()`.

WEIGHTS: both weights, each on its OWN body, reported SEPARATELY. Tip clearance
is measured per file and a `_0` mesh is separately authored, so the weight-0
half is scored too: `_0` files against the tip rays of the weight-0 body
(`canonical_ube(weight="_0")`, the sibling of the weight-1 body -- never the
weight-1 body itself, which would be the wrong frame).

The GATED rows are still weight 1, byte-for-byte what this tool always printed:
`acceptance.py` gates tip p50, tip p05 and "pieces tighter at the tip" by
parsing them, and those baselines keep their meaning. Weight 0 follows in its
own block, worded so the gate cannot mistake it for those rows (see
`_weight0_lines`). Gating on weight 0 is a separate change to `acceptance.py`.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import logging                                                  # noqa: E402
logging.disable(logging.WARNING)

import numpy as np                                              # noqa: E402
from pyn.pynifly import NifFile                                 # noqa: E402

from src import nif_convert as nc                               # noqa: E402
from src.nif_convert_bust import _body_nipple_weight            # noqa: E402
from scripts.analysis.canonical_body import canonical_ube       # noqa: E402
from scripts.analysis.mesh_penetration import ray_first_hit     # noqa: E402

# Fraction of the peak nipple weight that counts as the TIP.
TIP_WEIGHT_FRAC = 0.75
# A hit further than this is another garment layer or the far side of the body,
# not the cloth sitting over this nipple.
MAX_HIT = 12.0
# Below this many hits the garment does not cover the nipple at all.
MIN_TIP_HITS = 20

BODY_NAMES = set(nc.UBE_BODY_INJECT_NAMES) | {"3BA"}
PROXY_NAMES = {"virtualbody", "virtualground", "skirtcol", "buttcol"}


def tip_rays(weight: str = "_1"):
    """(origins, directions) for the body's nipple-tip vertices, world space.

    `weight` picks the body: weight 1 as always, or its weight-0 sibling. A
    missing weight-0 body raises FileNotFoundError rather than quietly reusing
    the weight-1 rays."""
    body_path, body_shape_name = canonical_ube(weight)
    shape = next(s for s in NifFile(str(body_path)).shapes
                 if s.name == body_shape_name)
    verts = np.asarray(shape.verts, np.float64)
    normals = np.asarray(nc._body_normals_or_compute(shape), np.float64)
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    if not np.isfinite(lens).all() or float(lens.max()) <= 0.0:
        raise SystemExit(
            "body normals are degenerate -- the ray has no direction to cast. "
            "Refusing rather than reporting 0.000u clearance everywhere.")
    normals = normals / np.clip(lens, 1e-9, None)
    weight = np.asarray(_body_nipple_weight(shape), np.float64)
    if not weight.size or float(weight.max()) <= 0.0:
        raise SystemExit("no nipple weight on the canonical body -- "
                         "0/0 IS NOT A PASS")
    tip = weight >= TIP_WEIGHT_FRAC * float(weight.max())
    return verts[tip], normals[tip]


def garment_mesh(path):
    """Every rendering garment shape of one NIF, welded, in BODY space."""
    verts, tris, offset = [], [], 0
    for s in NifFile(str(path)).shapes:
        name = (s.name or "").strip()
        if name in BODY_NAMES or name.lower() in PROXY_NAMES:
            continue
        v = np.asarray(s.verts, np.float64) + nc.shape_body_offset(s)
        t = np.asarray(s.tris, np.int64).reshape(-1, 3)
        if len(v) and len(t):
            verts.append(v)
            tris.append(t + offset)
            offset += len(v)
    if not verts:
        return None, None
    return np.vstack(verts), np.vstack(tris)


def clearance_full(path, origins, directions):
    """(distance, hit) per ray -- NOTHING dropped, so two arms stay PAIRED.

    `clearance()` below returns only the rays that struck garment, which is the
    right input for a single arm's distribution and the WRONG one for comparing
    two: a ray that misses in one arm and hits in the other silently leaves the
    sample, so a per-piece delta of medians compares two different populations.
    Measured on a reported cuirass, floors ON vs OFF: 371 rays in front against
    473, and the per-piece delta reads -0.1424u where the SAME rays read
    -0.3665u. The direction survived; the size did not.
    """
    verts, tris = garment_mesh(path)
    if verts is None:
        return None, None
    d = np.asarray(ray_first_hit(origins, directions, verts, tris), np.float64)
    hit = np.isfinite(d) & (d > 0) & (d < MAX_HIT)
    return d, hit


def clearance(path, origins, directions):
    """Tip clearances for one piece, or None when it does not cover the nipple."""
    d, hit = clearance_full(path, origins, directions)
    if d is None:
        return None
    return d[hit] if int(hit.sum()) >= MIN_TIP_HITS else None


def _population(base, weight):
    """Arm 1's `weight` NIFs, meshes-relative, first-person dropped by name.

    For weight 1 this is exactly the population the tool has always scored --
    the same glob and the same `1stperson` filter -- so the gated rows stay
    comparable with every recorded run."""
    return [os.path.relpath(p, os.path.join(base, "meshes"))
            for p in sorted(glob.glob(
                os.path.join(base, "meshes", "**", "*" + weight + ".nif"),
                recursive=True))
            if "1stperson" not in os.path.basename(p).lower()]


def _score(arms, rels, origins, directions):
    """(rows, skip_missing, skip_uncovered, full) over `rels` in EVERY arm.

    A piece missing from, or not covering the tip in, ANY arm is dropped from
    all of them, so every arm is scored on one population."""
    rows, skip_missing, skip_uncovered = [], 0, 0
    full = {}          # rel -> {label: (distance, hit)}, for the PAIRED block
    for rel in rels:
        vals = {}
        for arm_dir, label in arms:
            p = os.path.join(arm_dir, "meshes", rel)
            if not os.path.isfile(p):
                skip_missing += 1
                vals = {}
                break
            d, hit = clearance_full(p, origins, directions)
            if d is None or int(hit.sum()) < MIN_TIP_HITS:
                skip_uncovered += 1
                vals = {}
                break
            vals[label] = d[hit]
            full.setdefault(rel, {})[label] = (d, hit)
        if len(vals) == len(arms):
            rows.append((rel, vals))
    return rows, skip_missing, skip_uncovered, full


# Every weight-0 arm row starts with this, so no weight-0 line can match the
# `^control ...` / `^candidate ...` pattern acceptance.py reads the gate from.
W0_PREFIX = "w0 "


def _weight0_lines(labels, rows, n_rays, n_rels, skip_missing, skip_uncovered):
    """The weight-0 report, as lines. Pure, so its wording can be tested.

    WORDED SO acceptance.py CANNOT READ IT AS THE GATED ROWS, because it reads
    three things out of this tool's whole output: the first `^control` and
    `^candidate` line carrying three numbers, the FIRST `tighter N`, and ANY
    "0/0 IS NOT A PASS" -- which marks the entire tip row as unmeasured. So each
    arm row here is prefixed `w0 `, the per-piece line says "less room" / "more
    room", and an empty weight 0 says so in other words. That last one matters
    most: printed here, the 0/0 phrase would silently switch off the gate for
    weight 1 as well.
    """
    out = [f"tip vertices cast, weight 0   : {n_rays}",
           f"`_0` pieces found in arm 1     : {n_rels}",
           f"  not present in every arm     : {skip_missing}",
           f"  garment does not cover the tip: {skip_uncovered}",
           f"pieces covering the nipple in every arm, weight 0: {len(rows)}"]
    if not rows:
        out.append("  weight 0: no piece covers the tip in every arm, so "
                   "nothing was measured at weight 0")
        return out
    out.append(f"{'arm, weight 0':<22} {'tip clearance p50':>18} "
               f"{'p05':>8} {'min':>8}")
    for lab in labels:
        allv = np.concatenate([v[lab] for _r, v in rows])
        out.append(f"{W0_PREFIX + lab:<22} {np.median(allv):>18.3f} "
                   f"{np.percentile(allv, 5):>8.3f} {allv.min():>8.3f}")
    if len(labels) >= 2:
        a, b = labels[0], labels[-1]
        deltas = [float(np.median(v[b]) - np.median(v[a])) for _r, v in rows]
        less = len([d for d in deltas if d < -0.005])
        more = len([d for d in deltas if d > 0.005])
        out.append(f"  per piece at weight 0, {b} minus {a}: less room {less}"
                   f"   more room {more}   same {len(deltas) - less - more}"
                   f"   median delta {np.median(deltas):+.4f}u")
    return out


def _weight0_block(arms):
    """Score the `_0` files on the weight-0 body and return the report lines.

    INFO in this tool: it never changes the exit code, which stays the gated
    weight-1 verdict. A weight-0 body that cannot be found, or that the ray
    setup refuses, is reported as NOT MEASURED instead of aborting the run --
    by the time this runs the gated report has already printed, and an abort
    here would hand acceptance.py a failing exit for a weight it does not gate.
    """
    out = ["", "=== WEIGHT 0: `_0` files on the weight-0 body "
               "(info; acceptance.py does not gate on this block yet) ==="]
    try:
        origins, directions = tip_rays("_0")
    except (FileNotFoundError, SystemExit) as e:
        out.append(f"weight 0 NOT MEASURED -- {e}")
        return out
    rels = _population(arms[0][0], "_0")
    rows, skip_missing, skip_uncovered, _full = _score(
        arms, rels, origins, directions)
    return out + _weight0_lines([lab for _d, lab in arms], rows, len(origins),
                                len(rels), skip_missing, skip_uncovered)


def main(argv) -> int:
    arms = [(a.split("=", 1) + [a.split("=", 1)[0]])[:2] for a in argv]
    if not arms:
        print(__doc__)
        return 2
    # The exit codes are spelled out HERE, not passed through: `tool_map` reads
    # a tool's gate from the literal returns of `main`, and `return rc` would
    # hide that this tool exits 1 when no piece covers the tip.
    rc = _report_weight1(arms)
    for line in _weight0_block(arms):
        print(line)
    if rc == 1:
        return 1                   # 0/0 at weight 1 is not a pass
    return 0


def _report_weight1(arms) -> int:
    """The GATED report: weight 1, byte-for-byte what this tool always printed.

    `acceptance.py` parses its arm rows, its first `tighter N` and its 0/0
    line, so nothing here may change without changing the gate with it."""
    origins, directions = tip_rays()
    base = arms[0][0]
    rels = _population(base, "_1")
    rows, skip_missing, skip_uncovered, full = _score(
        arms, rels, origins, directions)

    print(f"tip vertices cast              : {len(origins)}")
    print(f"`_1` pieces found in arm 1     : {len(rels)}")
    print(f"  not present in every arm     : {skip_missing}")
    print(f"  garment does not cover the tip: {skip_uncovered}")
    print(f"pieces covering the nipple in EVERY arm: {len(rows)}")
    if not rows:
        print("  0/0 IS NOT A PASS")
        return 1

    labels = [lab for _d, lab in arms]
    print(f"\n{'arm':<22} {'tip clearance p50':>18} {'p05':>8} {'min':>8}")
    for lab in labels:
        allv = np.concatenate([v[lab] for _r, v in rows])
        print(f"{lab:<22} {np.median(allv):>18.3f} "
              f"{np.percentile(allv, 5):>8.3f} {allv.min():>8.3f}")

    if len(labels) < 2:
        return 0
    a, b = labels[0], labels[-1]
    print(f"\nper piece, {b} minus {a} (negative = LESS room over the nipple):")
    deltas = [(float(np.median(v[b]) - np.median(v[a])), r) for r, v in rows]
    tighter = [d for d, _ in deltas if d < -0.005]
    looser = [d for d, _ in deltas if d > 0.005]
    print(f"  tighter {len(tighter)}   looser {len(looser)}   flat "
          f"{len(deltas) - len(tighter) - len(looser)}   median delta "
          f"{np.median([d for d, _ in deltas]):+.4f}u")
    for d, r in sorted(deltas)[:10]:
        print(f"   {d:+.4f}u  {r}")

    # ---- PAIRED, on rays in front in BOTH arms. INFO -- NOT the gated row.
    #
    # The block above is a difference of medians over each arm's OWN surviving
    # rays, so a piece whose garment COVERS MORE of the nipple admits more
    # (tighter) rays and its median falls -- it scores as "tighter" for getting
    # better. This block removes that by comparing the SAME rays, and prints the
    # coverage change that causes it.
    #
    # DELIBERATELY DIFFERENT WORDS. `acceptance.py` greps `tighter\\s+(\\d+)`
    # over this whole output and takes the FIRST match, so a second line saying
    # "tighter N" would silently re-gate the run on whichever came first.
    paired, cov = [], {a: 0, b: 0}
    for rel, per in full.items():
        if a not in per or b not in per:
            continue
        (da, ha), (db, hb) = per[a], per[b]
        cov[a] += int(ha.sum())
        cov[b] += int(hb.sum())
        both = ha & hb
        if int(both.sum()) < MIN_TIP_HITS:
            continue
        paired.append((float(np.median(db[both]) - np.median(da[both])), rel))
    if paired:
        vals = [d for d, _r in paired]
        closer = len([d for d in vals if d < -0.005])
        further = len([d for d in vals if d > 0.005])
        print(f"\nPAIRED on rays in front in BOTH arms -- INFO, not the gated "
              f"row ({len(paired)} of {len(rows)} pieces):")
        print(f"  per piece: closer {closer}   further {further}   level "
              f"{len(vals) - closer - further}   median paired "
              f"{np.median(vals):+.4f}u")
        print(f"  tip rays with garment IN FRONT: {a} {cov[a]}  "
              f"{b} {cov[b]}  (a rise means the garment covers MORE of the "
              f"nipple, which DROPS the unpaired median above)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
