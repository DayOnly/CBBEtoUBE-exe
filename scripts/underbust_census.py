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

"""Narrow-band CLIPPING census over the BREAST UNDER-CURVE.

METRIC RETIRED 2026-07-29. This used to classify each exposed vert with a cone
of rays -- `surrounded` (>=5 of 10 blocked) / `partial` / `bare` -- the same
statistic `mesh_penetration.containment` implemented. That statistic is
ANTI-CORRELATED with in-game ground truth: it scored the armour the user
confirmed CLEAN *worse* than the one that visibly clips, because a ray cone
cannot separate "skin is outside the garment SURFACE" from "skin is outside the
garment's COVERAGE", so a small or open garment scores terribly by design. No
threshold rescues it; `containment` was deleted rather than re-tuned.

It now uses the validated test (`clipping_report`): a body vert is CLIPPING when
the outward ray escapes but the INWARD ray hits garment -- the garment is behind
the skin. Skin merely beside an open edge escapes both ways and is UNCOVERED,
which is a cut, not a defect. Calibrated 0.0% on the clean armour, 8.9% on the
clipping one.

The `surrounded` / `partial` / `bare` columns are gone. Rows written before this
date carry them and are NOT comparable to rows written after -- the old columns
answered a different, discredited question.

WHY A NARROW BAND. The region-level census dilutes this defect into nothing. The
under-curve is 484 of the 3674 verts the `breast` region selector accepts --
13.2%, a ~7.6x dilution -- so a defect confined to it reads as ~1-2% at region
level. Region percentages rank; they do not size.

WHERE THE BAND IS -- measured on the canonical UBE body, not assumed. Sweeping
2u z-slabs and asking where the surface turns from facing forward to facing DOWN:

     z-band    mean nz   mean ny   down&fwd    max y
     88- 90     -0.276    +0.104     15.5%      6.87
     90- 92     -0.410    +0.180     38.6%      7.12   <- under-curve
     92- 94     -0.388    +0.162     41.1%      8.08   <- under-curve
     94- 96     -0.106    +0.317      4.4%      8.16   <- apex (max protrusion)
     96- 98     +0.172    +0.416      6.7%      8.07

so UNDER_BUST = z 90-94 with nz < -0.30 and ny > 0.10. The apex at z ~95 agrees
with the independently pinned "UBE breast z 90-102, apex ~95".

NOT the cause, though it looks like one: the `breast` selector's `ny > 0.3` keeps
78.8% of the under-curve. The band's mean ny of +0.18 is over the whole z-slab, not
over the under-curve subset. Dilution is the whole story.

CONTROLS RUN EVERY TIME, and the negative one is not optional -- the ray-sense
inversion that produced "99.6% surrounded everywhere" is invisible to a positive
control. See METRICS.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.multipose_clip_test import load, _posed              # noqa: E402
from scripts.posed_clip_test import rays_hit                      # noqa: E402
from scripts.mesh_penetration import (clipping_report,            # noqa: E402
                                      exposure_with_margin, noise_floor)

OUT = Path(r"<MODLIST_ROOT>/mods/CBBEtoUBE Auto/meshes/!UBE")

# Empirically located above. Kept as module constants so a caller can widen the
# band and see the number move, rather than editing a lambda buried in a loop.
Z_LO, Z_HI = 90.0, 94.0
NZ_MAX = -0.30          # surface faces DOWN
NY_MIN = 0.10           # ...and still somewhat forward: the under-curve, not the back
ARM_X, MID_X = 20.0, 2.5
TMAX = 6.0              # ray reach for the clipping test

# A plain exposure count is a HARD ray-hit boolean, so a vert on the coverage boundary
# flips on hundredths of a unit. Measured here: jittering the garment 0.02u flips ~13
# band verts. A bare delta of +/-3 is therefore not a result -- and one was nearly
# acted on as a defect. `MARGIN` splits off those boundary verts as AMBIGUOUS so the
# reported count does not churn; the run also prints its own resolution.
MARGIN = 0.02


def under_bust(v, n):
    """Boolean mask for the breast under-curve on a posed body."""
    return ((v[:, 2] >= Z_LO) & (v[:, 2] < Z_HI)
            & (n[:, 2] < NZ_MAX) & (n[:, 1] > NY_MIN)
            & (np.abs(v[:, 0]) < ARM_X) & (np.abs(v[:, 0]) > MID_X))


def score(body, body_tris, normals, gv, gt, margin=MARGIN):
    """Under-curve exposure + CLIPPING, with the boundary band split off.

    `exposed` stays the raw hard-threshold count so old rows remain comparable
    on that column. `exposed_firm` / `ambiguous` are the stable pair: a delta in
    `exposed_firm` means something, a delta smaller than `ambiguous` does not.

    `clipping` is the validated test and is the column to judge on. Note it is
    NOT a subset of `exposed` by construction -- exposure asks "did the outward
    ray escape", clipping additionally asks "does the garment lie behind the
    skin". A garment can leave skin exposed without any of it clipping (an open
    neckline), which is exactly the distinction the retired cone metric could
    not make.
    """
    idx = np.flatnonzero(under_bust(body, normals))
    if len(idx) < 20:
        return None
    mask = np.zeros(len(body), bool)
    mask[idx] = True
    exposed = ~rays_hit(body[idx], normals[idx], gv, gt)
    firm, _cov, amb = exposure_with_margin(body[idx], normals[idx], gv, gt,
                                           margin=margin)
    rep = clipping_report(body, body_tris, normals, [(gv, gt)],
                          tmax=TMAX, mask=mask)
    return {"n": int(len(idx)), "exposed": int(exposed.sum()),
            "exposed_firm": int(firm.sum()), "ambiguous": int(amb.sum()),
            "margin": margin,
            "clipping": int(rep.get("clip_verts") or 0),
            "clipping_pct": rep.get("clipping_pct"),
            "covered_pct": rep.get("covered_pct"),
            "uncovered_pct": rep.get("uncovered_pct")}


def _controls(rigid, scan=40):
    """Both controls, chosen from the population by GEOMETRY, not hardcoded.

    NEGATIVE -- a garment entirely below z 80 must read 0 clipping and ~100%
    uncovered. Catches "everything reads covered/clipping".

    POSITIVE -- a garment that densely covers the band must read mostly COVERED.
    This is the one that catches a transposed ray sense, and the negative
    control provably cannot: a garment nowhere near the band produces no ray
    hits in EITHER direction, so swapping the two directions leaves 0 clipping
    and 100% uncovered untouched. Verified by running the census with the
    normals negated -- the negative control passed regardless; only the
    positive one collapsed. A control that cannot fail is not a control, and
    the ray-sense inversion that produced "99.6% surrounded everywhere" is
    exactly the bug this pair exists to catch.

    `scan` bounds the search: posing every armor to pick a control would cost
    more than the census.
    """
    neg, pos, best = [], None, -1.0
    for r in rigid[:scan]:
        try:
            data, gar, par, orig, bn = load(OUT / r["armor"])
            pb, pbn, gv, gt = _posed(data, gar, {}, bn)
        except Exception:
            continue
        if not len(gv):
            continue
        if gv[:, 2].max() < 80.0:
            if len(neg) < 2:
                d = score(pb, data[bn][1], pbn, gv, gt)
                if d:
                    neg.append((r["armor"], d))
            continue
        # coverage by geometry alone: how much of the band has garment close by
        idx = np.flatnonzero(under_bust(pb, pbn))
        if len(idx) < 20:
            continue
        near = cKDTree(gv).query(pb[idx], k=1)[0]
        frac = float((near < 2.0).mean())
        if frac > best:
            d = score(pb, data[bn][1], pbn, gv, gt)
            if d and d["covered_pct"] is not None:
                best, pos = frac, (r["armor"], d, frac)
    return neg, pos


def main():
    rows = [json.loads(l) for l in
            open(_REPO / "multipose_census.jsonl", encoding="utf-8")]
    rigid = [r for r in rows if not r["smp_rigged_any"]]
    print(f"{len(rigid)} fully-rigid armors of {len(rows)}", flush=True)

    neg, pos = _controls(rigid)
    if not neg or pos is None:
        raise SystemExit(
            "controls could not be built -- the census would run with NOTHING "
            "checking the metric's sense. Stop.")
    ok = True
    print("\nNEGATIVE CONTROLS (garment below z 80 -> 0 clipping, ~100% "
          "uncovered):", flush=True)
    for name, d in neg:
        unc = d["uncovered_pct"]
        good = d["clipping"] == 0 and unc is not None and unc > 99.0
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {name[:44]:<46}"
              f"clipping={d['clipping']:<5} uncovered="
              f"{'n/a' if unc is None else f'{unc:.1f}%'}", flush=True)
    print("POSITIVE CONTROL (densest band coverage -> mostly COVERED). This is "
          "the one that catches a transposed ray sense:", flush=True)
    name, d, frac = pos
    good = d["covered_pct"] is not None and d["covered_pct"] > 50.0
    ok &= good
    print(f"  {'PASS' if good else 'FAIL'}  {name[:44]:<46}"
          f"covered={d['covered_pct']:.1f}%  (garment within 2u of "
          f"{100*frac:.0f}% of the band)", flush=True)
    if not ok:
        raise SystemExit("control FAILED -- metric is inverted, stop.")

    # State the resolution BEFORE any result, so no delta gets read as a finding
    # without the number it has to beat sitting next to it.
    for r in rigid:
        try:
            data, gar, par, orig, bn = load(OUT / r["armor"])
            pb, pbn, gv, gt = _posed(data, gar, {}, bn)
        except Exception:
            continue
        i = np.flatnonzero(under_bust(pb, pbn))
        if len(i) < 20:
            continue
        nf = noise_floor(pb[i], pbn[i], gv, gt, amps=(0.01, MARGIN))
        print("\nRESOLUTION (garment jitter -> band verts that flip):", flush=True)
        for amp, v in nf.items():
            print(f"  {amp:>5}u  flipped {v['flipped']:5.1f}  net {v['net']:+5.1f}",
                  flush=True)
        print(f"  -> a reported delta below ~{nf[MARGIN]['flipped']:.0f} verts is NOT "
              f"a result; use exposed_firm.", flush=True)
        break

    out = []
    dest = Path(__file__).with_name("underbust_census.json")
    for i, r in enumerate(rigid, 1):
        try:
            data, gar, par, orig, bn = load(OUT / r["armor"])
            pb, pbn, gv, gt = _posed(data, gar, {}, bn)
            d = score(pb, data[bn][1], pbn, gv, gt)
        except Exception:
            continue
        if d:
            out.append({"armor": r["armor"], **d})
        if i % 20 == 0:
            print(f"  {i}/{len(rigid)}", flush=True)
            dest.write_text(json.dumps(out), encoding="utf-8")
    dest.write_text(json.dumps(out), encoding="utf-8")
    print(f"done: {len(out)} armors with a scorable under-curve", flush=True)


if __name__ == "__main__":
    main()
