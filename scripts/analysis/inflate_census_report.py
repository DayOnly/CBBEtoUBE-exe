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

"""Read `inflate_census.py`'s JSON and answer: does removing inflate pay?

PAIRED differences, per shape, because the arms differ only in that one flag --
so a shape is its own control and the pack's enormous spread in size and style
cancels out. Reported as MEDIANS and as the share of shapes better/worse, never
as a bare mean: a handful of 18k-vertex plates would otherwise decide a number
covering thousands of shapes.

The verdict rule is stated up front so it cannot be fitted to the result:
inflate exists to leave headroom for a body that MORPHS at runtime. Removing it
is justified only if the clearance counters (standoff p10, verts inside, verts
grazing) DO NOT materially worsen. Fidelity and smoothness gains do not buy the
right to lose clearance, because bind-pose fidelity is visible in a screenshot
and lost clearance is visible only once the player moves a slider.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def col(rows, side, key):
    return np.array([r[side].get(key) if r[side].get(key) is not None else np.nan
                     for r in rows], float)


def band(label, on, off, higher_is_better, unit=""):
    d = off - on
    ok = ~np.isnan(d)
    if not ok.any():
        print(f"  {label:26s}  no data")
        return
    d = d[ok]
    better = (d > 0).mean() if higher_is_better else (d < 0).mean()
    arrow = "OFF better" if (
        (np.median(d) > 0) == higher_is_better) else "OFF WORSE"
    print(f"  {label:26s} ON {np.nanmedian(on[ok]):8.4f}   "
          f"OFF {np.nanmedian(off[ok]):8.4f}   "
          f"delta p50 {np.median(d):+8.4f}{unit}   "
          f"{100 * better:5.1f}% of shapes improve   {arrow}")


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text())
    rows = data["rows"]
    print(f"POPULATION  {data['mods_scored']}/{data['mods_total']} mods scored, "
          f"{len(rows)} shape-pairs")
    # Which change is being measured. The columns are named ON/OFF after the two
    # arms, and reading them as "inflate on/off" when the arms are something
    # else would invert every verdict below.
    if data.get("arm_on") or data.get("arm_off"):
        print(f"  ARM 'ON'  = {data.get('arm_on')}")
        print(f"  ARM 'OFF' = {data.get('arm_off')}")
    ex = data.get("exclusions") or {}
    if ex:
        print("EXCLUSIONS (a shrinking denominator is not a clean result):")
        for k, v in sorted(ex.items(), key=lambda kv: -kv[1]):
            print(f"    {v:6d}  {k}")
    else:
        print("EXCLUSIONS: none")
    if not rows:
        print("\n0 shape-pairs is not a pass. Nothing measured.")
        return 1

    # A shape inflate never touched contributes an exact 0 to every paired
    # difference, and most shapes are like that -- so a median over ALL shapes
    # reads +0.0000 no matter how large the effect is where the pass fires.
    # That is dilution, not a null result. Report the TOUCHED subset too, and
    # print how big it is so the reader can see which denominator is which.
    def touched(r):
        for k in ("standoff_p10", "standoff_p50", "edge_dev", "dihedral"):
            a, b = r["on"].get(k), r["off"].get(k)
            if a is not None and b is not None and abs(a - b) > 1e-6:
                return True
        return r["on"].get("inside") != r["off"].get("inside")

    n_touch = sum(1 for r in rows if touched(r))
    print(f"\nINFLATE FIRED on {n_touch}/{len(rows)} shapes "
          f"({100 * n_touch / max(len(rows), 1):.1f}%) -- the rest are "
          f"identical in both arms and would dilute every median below.")

    for phase in (None, 2, 1, "touched"):
        if phase == "touched":
            sub = [r for r in rows if touched(r)]
            name = "SHAPES INFLATE ACTUALLY MOVED"
        else:
            sub = rows if phase is None else [
                r for r in rows if r["on"].get("phase") == phase]
            name = "ALL" if phase is None else f"PHASE {phase}"
        if not sub:
            continue
        print(f"\n=== {name}   {len(sub)} shape-pairs "
              f"({sum(r['on']['verts'] for r in sub):,} verts)")
        print("  TARGET -- fidelity to the author")
        band("authored-offset error", col(sub, "on", "auth_err"),
             col(sub, "off", "auth_err"), higher_is_better=False, unit="u")
        band("edge deviation (shape)", col(sub, "on", "edge_dev"),
             col(sub, "off", "edge_dev"), higher_is_better=False)
        band("dihedral (roughness)", col(sub, "on", "dihedral"),
             col(sub, "off", "dihedral"), higher_is_better=False)
        print("  COUNTER -- clearance, which is what inflate exists for")
        band("standoff p10", col(sub, "on", "standoff_p10"),
             col(sub, "off", "standoff_p10"), higher_is_better=True, unit="u")
        band("standoff p50", col(sub, "on", "standoff_p50"),
             col(sub, "off", "standoff_p50"), higher_is_better=True, unit="u")
        ion, ioff = col(sub, "on", "inside"), col(sub, "off", "inside")
        gon, goff = col(sub, "on", "grazing"), col(sub, "off", "grazing")
        vt = col(sub, "on", "verts")
        print(f"  {'verts INSIDE the body':26s} ON {int(np.nansum(ion)):8d}   "
              f"OFF {int(np.nansum(ioff)):8d}   "
              f"({100 * np.nansum(ion) / max(np.nansum(vt), 1):.3f}% -> "
              f"{100 * np.nansum(ioff) / max(np.nansum(vt), 1):.3f}% of verts)")
        print(f"  {'verts GRAZING (<0.1u)':26s} ON {int(np.nansum(gon)):8d}   "
              f"OFF {int(np.nansum(goff)):8d}   "
              f"({100 * np.nansum(gon) / max(np.nansum(vt), 1):.3f}% -> "
              f"{100 * np.nansum(goff) / max(np.nansum(vt), 1):.3f}% of verts)")
        worse = int((ioff > ion).sum())
        print(f"  {'shapes that gained INSIDE verts':26s} {worse} "
              f"({100 * worse / len(sub):.1f}%)   "
              f"shapes that lost them: {int((ioff < ion).sum())}")

    # The pieces that would regress worst, so the verdict is not a mean.
    d = col(rows, "off", "inside") - col(rows, "on", "inside")
    order = np.argsort(-np.nan_to_num(d))[:12]
    print("\nWORST REGRESSIONS by verts newly inside the body")
    print(f"  {'delta':>7s}  {'shape':20s} {'mod / nif'}")
    for i in order:
        if not np.isfinite(d[i]) or d[i] <= 0:
            break
        r = rows[int(i)]
        print(f"  {int(d[i]):+7d}  {r['on']['shape'][:20]:20s} "
              f"{r['mod']} / {Path(r['nif']).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
