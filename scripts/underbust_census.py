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

"""Narrow-band containment census over the BREAST UNDER-CURVE.

WHY A NARROW BAND. The region-level containment census dilutes this defect into
nothing. The under-curve is 484 of the 3674 verts the `breast` region selector
accepts -- 13.2%, a ~7.6x dilution -- so a defect confined to it reads as ~1-2% at
region level. On the one piece confirmed by eye in game, the region census scored
`exposed=40 poke=2` while a targeted under-curve pass on the SAME mesh found
155 exposed / 14 strictly surrounded. Region percentages rank; they do not size.

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

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.multipose_clip_test import load, _posed              # noqa: E402
from scripts.posed_clip_test import rays_hit                      # noqa: E402
from scripts.mesh_penetration import (cone_dirs,                  # noqa: E402
                                      exposure_with_margin, noise_floor)

OUT = Path(r"<MODLIST_ROOT>/mods/CBBEtoUBE Auto/meshes/!UBE")

# Empirically located above. Kept as module constants so a caller can widen the
# band and see the number move, rather than editing a lambda buried in a loop.
Z_LO, Z_HI = 90.0, 94.0
NZ_MAX = -0.30          # surface faces DOWN
NY_MIN = 0.10           # ...and still somewhat forward: the under-curve, not the back
ARM_X, MID_X = 20.0, 2.5
K, HALF_DEG, TMAX = 10, 50.0, 6.0
SURROUNDED = 5          # >=5 of 10 cone rays blocked

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


def score(body, normals, gv, gt, margin=MARGIN):
    """Under-curve exposure + containment, with the boundary band split off.

    `exposed` stays the raw hard-threshold count so old rows remain comparable.
    `exposed_firm` / `ambiguous` are the stable pair: a delta in `exposed_firm`
    means something, a delta smaller than `ambiguous` does not.
    """
    m = under_bust(body, normals)
    idx = np.flatnonzero(m)
    if len(idx) < 20:
        return None
    exposed = ~rays_hit(body[idx], normals[idx], gv, gt)
    e = idx[exposed]
    firm, _cov, amb = exposure_with_margin(body[idx], normals[idx], gv, gt,
                                           margin=margin)
    d = {"n": int(len(idx)), "exposed": int(exposed.sum()),
         "exposed_firm": int(firm.sum()), "ambiguous": int(amb.sum()),
         "margin": margin, "surrounded": 0, "partial": 0, "bare": 0}
    if len(e):
        # rays_hit returns True = HIT, and blocked IS the hit. Do NOT invert:
        # `~rays_hit` counts rays that ESCAPED and reads as "everything is
        # surrounded" -- the exact bug that voided the first census.
        blocked = np.zeros(len(e), dtype=np.int64)
        for dirv in cone_dirs(normals[e], HALF_DEG, K):
            blocked += rays_hit(body[e], dirv, gv, gt, tmax=TMAX).astype(np.int64)
        d["surrounded"] = int((blocked >= SURROUNDED).sum())
        d["partial"] = int(((blocked >= 1) & (blocked < SURROUNDED)).sum())
        d["bare"] = int((blocked == 0).sum())
    return d


def _controls(rigid):
    """A garment that cannot reach the chest MUST read 100% bare here.

    Chosen from the population by geometry (no garment vert above z 80) rather than
    hardcoded, so the control survives a repack.
    """
    picked = []
    for r in rigid:
        try:
            data, gar, par, orig, bn = load(OUT / r["armor"])
            pb, pbn, gv, gt = _posed(data, gar, {}, bn)
        except Exception:
            continue
        if len(gv) and gv[:, 2].max() < 80.0:
            d = score(pb, pbn, gv, gt)
            if d and d["exposed"]:
                picked.append((r["armor"], d))
        if len(picked) == 2:
            break
    return picked


def main():
    rows = [json.loads(l) for l in
            open(_REPO / "multipose_census.jsonl", encoding="utf-8")]
    rigid = [r for r in rows if not r["smp_rigged_any"]]
    print(f"{len(rigid)} fully-rigid armors of {len(rows)}", flush=True)

    print("\nNEGATIVE CONTROLS (garment entirely below z 80 -> must be 100% bare):",
          flush=True)
    ok = True
    for name, d in _controls(rigid):
        pct = 100 * d["bare"] / d["exposed"]
        ok &= d["surrounded"] == 0
        print(f"  {'PASS' if d['surrounded'] == 0 else 'FAIL'}  {name[:44]:<46}"
              f"exposed={d['exposed']:<5} bare={pct:.1f}%", flush=True)
    if not ok:
        raise SystemExit("negative control FAILED -- metric is inverted, stop.")

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
            d = score(pb, pbn, gv, gt)
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
