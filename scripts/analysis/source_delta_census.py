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

"""What did the CONVERTER make worse? Source-vs-converted pose delta, pack-wide.

    python scripts/analysis/source_delta_census.py [--out FILE] [--limit N] [--sample N]
                                          [--gate PCT]

WHY A DELTA. Every level-based metric confounds "the converter broke it" with "the
author made it that way", and that confound produced four wrong conclusions in one day
-- including a 30-armour worst-offender list that turned out to be mostly armour which
already behaved that way. Posing the SOURCE garment on its OWN body and the converted
one on UBE, then differencing the coverage loss, cancels authored behaviour: a skirt
that parts in a crouch does so on both sides and reads ~0. Only what WE changed
survives, so every positive row is actionable by construction.

Measured on the first five: three authored (delta +0.7 / +1.2 / +4.5), one where the
converter IMPROVED the garment (-18.4), one real regression (+12.1) -- by the first
version (2026-07-27), which posed each source on the body its own NIF bundled, so
not comparable with a run on today's canonical bodies.

SOURCE SELECTION IS PART OF THE METRIC. Not "the last mod providing this mesh path" --
two mods can ship the same path with different geometry, and picking the wrong one
inverted one armour from +0.7 to +83.3, i.e. from "authored" to "our fault". The source
is chosen by GARMENT SHAPE-NAME OVERLAP with the output, which ties both sides to the
same garment.

WEIGHTS: both weights, each on its OWN bodies. The delta is per file and a `_0`
mesh is separately authored, so the converter can regress a garment at one
weight and not the other. Every file is posed on the bodies matching its own
suffix -- `canonical_cbbe(weight=w)` for the SOURCE side and
`canonical_ube(weight=w)` for the CONVERTED side, whose weight 0 is the sibling
of the weight-1 body, never the weight-1 body itself. The pose pivots, the
arm-weight mask and the region vertices are read off that body, so they follow
the weight with it. A file whose weight-0 body is missing is counted and
skipped, never posed on weight 1. Every row carries `weight`, and the summary
reports each weight on its own line; `--limit` counts FILES, so it now covers
about half as many garments, each at both weights.

The region constants were measured on the UBE body without a stated weight;
weight moves the radius more than the height, so the z-bands carry over, but
the lateral arm cut is worth a look on `_0`.

COMPARING TWO RUNS: region for region, never by the counts. The MIN_COV floor is
JOINT -- a region is scored only when BOTH sides cover it -- so a change to either
side's body changes WHICH regions are scored, not just their deltas. Moving the
source side onto the body the game loads (2026-09-21) left the converted side
identical on all 2431 shared regions but brought 37 regions in and dropped 11
(weight-1 source breast coverage +125%: the old body grew through the garments),
so regressions >5pt went 114 -> 121 over a different population.

Read-only. Resolves the output via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                          # noqa: E402
from src.nif_convert import UBE_BODY_INJECT_NAMES, weight_suffix_of  # noqa: E402
from src import paths                                            # noqa: E402
from scripts.analysis import standoff_audit as sa                # noqa: E402
from scripts.analysis.multipose_clip_test import analyse_with_body        # noqa: E402
from scripts.analysis.canonical_body import (canonical_cbbe, canonical_ube,  # noqa: E402
                                    converted_garment_names, find_source)

MIN_COV = 30


def main():
    argv = sys.argv[1:]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    out = Path(opt("--out", "source_delta_census.jsonl"))
    limit = int(opt("--limit", "0") or 0)
    sample = int(opt("--sample", "300"))
    # A REGRESSION needs conv > src >= 0, so an armour whose converted loss is already
    # below the gate cannot produce a meaningful positive delta. Skipping the SOURCE
    # analysis for those halves the cost on a pack that is mostly clean. Exact for
    # finding regressions; it does deliberately NOT measure improvements below the
    # gate, which is stated in the row as gated=true rather than left to be guessed.
    gate = float(opt("--gate", "3.0"))
    global MIN_COV
    MIN_COV = int(opt("--min-cov", "30"))

    lay = paths.discover_layout()
    mods_root = lay.mods_root
    root = (mods_root / os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
            / "meshes" / "!UBE")
    if not root.is_dir():
        raise SystemExit(f"output not found: {root}")

    files = []
    # BOTH weights. First-person is NOT dropped by `output_nifs` here: this
    # file's own stem rule just below is broader and stays the authority.
    for p in sa.output_nifs(root, exclude_first_person=False):
        stem = p.stem.lower()
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if ("1st" in stem or "firstperson" in stem or stem.endswith("fp")
                or "_fp_" in stem or "1person" in stem):
            continue
        # BODY-COVERING PIECES ONLY. Without this the sweep is 1290 meshes -- boots,
        # gauntlets, pauldrons, scarves, pouches -- none of which meaningfully cover a
        # torso or leg region. Worse than slow: an accessory GRAZES a region on the
        # larger UBE body and misses it entirely on CBBE, so it scores a huge false
        # delta off a source coverage of ZERO (a boot read +6.8 at the thigh with
        # coverage 207/0; a pouch read +100 off a single vertex). The converter marks
        # body-covering pieces by injecting a body into them, so that is the filter.
        try:
            if not any(s.name in UBE_BODY_INJECT_NAMES
                       for s in pynifly.NifFile(str(p)).shapes):
                continue
        except Exception:
            continue
        files.append(p)
    if limit:
        files = files[:limit]

    bodies = {}

    def bodies_for(w):
        """(cbbe_path, cbbe_shape, ube_path, ube_shape) for weight `w`, or None
        when its weight-0 sibling is missing -- then its files are counted and
        skipped, never posed on the weight-1 bodies."""
        if w not in bodies:
            try:
                cp, cs = canonical_cbbe(str(mods_root), weight=w)
                up, us = canonical_ube(weight=w)
                bodies[w] = (cp, cs, up, us)
            except FileNotFoundError as e:
                print(f"  weight {w[1]}: NO reference body, its files are "
                      f"skipped -- {e}")
                bodies[w] = None
        return bodies[w]

    print(f"{len(files)} armors (both weights), sample={sample}, gate={gate}%")
    for w in ("_1", "_0"):
        b = bodies_for(w)
        if b:
            # <mod>/meshes/actors/character/character assets/<body>.nif: the
            # mod folder is FIVE levels up (four printed "meshes").
            print(f"  weight {w[1]} source body : {b[1]}  "
                  f"{Path(b[0]).parent.parent.parent.parent.parent.name}  "
                  f"({Path(b[0]).name})")
            print(f"  weight {w[1]} target body : {b[3]}  {Path(b[2]).name}")

    t0, n, reg, gated, nosrc = time.time(), 0, 0, 0, 0
    nogar = failed = selfint = nobody = 0
    per_w = collections.defaultdict(collections.Counter)
    with out.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(files, 1):
            rel = str(p.relative_to(root)).replace("\\", "/")
            w = weight_suffix_of(p)
            b = bodies_for(w)
            if b is None:
                nobody += 1
                continue
            cbbe_path, cbbe_shape, ube_path, ube_shape = b
            try:
                gnames = converted_garment_names(p)
                if not gnames:
                    nogar += 1
                    continue
                conv, ic = analyse_with_body(p, gnames, ube_path, ube_shape,
                                             sample=sample)
            # SystemExit is NO LONGER caught here. It used to be lumped in
            # with Exception, so a helper calling sys.exit() to report that
            # the reference body was unusable was swallowed whole and the
            # census carried on publishing rates over whatever survived. A
            # deliberate hard stop has to stop this too.
            except Exception as e:
                # COUNTED, never silent -- a piece dropped without a tally
                # shrinks the denominator of every rate printed below.
                failed += 1
                if failed <= 10:
                    print(f"  dropped {rel}: {e!r}")
                continue
            if ic > 1e-4:
                selfint += 1
                continue
            worst = max((r["worst_pct"] for r in conv.values()), default=0.0)
            row = {"armor": rel, "weight": w, "conv_worst": round(worst, 3)}
            if worst < gate:
                row["gated"] = True
                gated += 1
            else:
                src = find_source(rel, gnames, mods_root)
                if src is None:
                    row["source"] = None
                    nosrc += 1
                else:
                    try:
                        s, isrc = analyse_with_body(src, gnames, cbbe_path,
                                                    cbbe_shape, sample=sample)
                    except (Exception, SystemExit):
                        s, isrc = None, 1.0
                    if s is None or isrc > 1e-4:
                        row["source"] = None
                        nosrc += 1
                    else:
                        row["source"] = str(src.parent.parent.parent.name)
                        d = {}
                        for rg, cv in conv.items():
                            pose = cv["worst_pose"]
                            sp = next((x["pct"] for x in s[rg]["per_pose"]
                                       if x["pose"] == pose), None)
                            if sp is None:
                                continue
                            # BOTH sides need a real denominator. A delta between
                            # 207 covered verts and 0 is not a regression, it is two
                            # different questions; and "100% of 1 vertex" is noise.
                            if (cv["covered_at_bind"] < MIN_COV
                                    or s[rg]["covered_at_bind"] < MIN_COV):
                                continue
                            d[rg] = {"pose": pose, "conv": cv["worst_pct"],
                                     "src": sp, "delta": round(cv["worst_pct"] - sp, 3),
                                     "cov_conv": cv["covered_at_bind"],
                                     "cov_src": s[rg]["covered_at_bind"]}
                        row["regions"] = d
                        best = max((v["delta"] for v in d.values()), default=0.0)
                        row["worst_delta"] = round(best, 3)
                        if best > 5.0:
                            reg += 1
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            n += 1
            pw = per_w[w]
            pw["scored"] += 1
            if row.get("gated"):
                pw["gated"] += 1
            elif row.get("source") is None:
                pw["nosrc"] += 1
            elif row.get("worst_delta", 0.0) > 5.0:
                pw["reg"] += 1
            if i % 20 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(files)}  {reg} regressions, {gated} gated, "
                      f"{nosrc} no-source, {el/60:.0f}m, "
                      f"~{el/i*(len(files)-i)/60:.0f}m left", flush=True)
    print(f"\n{n} rows -> {out}   {reg} regressions >5pt, {gated} gated clean, "
          f"{nosrc} without a matched source   ({(time.time()-t0)/60:.0f} min)")
    # THE DENOMINATOR, stated. Every rate above is over `n`, and these are the
    # pieces that never reached it. Printing the excluded counts is what makes
    # `n` readable as a population rather than as "everything there was".
    print(f"  population: {len(files)} armors seen -> {n} scored")
    print(f"    {nogar:5d} no converted garment shapes")
    print(f"    {failed:5d} dropped on an error while analysing")
    print(f"    {selfint:5d} skipped: reference body self-intersects")
    print(f"    {nobody:5d} skipped: no reference body for that weight")
    # PER WEIGHT. A pooled count mixes two separately authored halves; these
    # are the numbers to compare.
    for w in ("_1", "_0"):
        pw = per_w[w]
        print(f"  weight {w[1]}: {pw['scored']} scored, {pw['reg']} regressions "
              f">5pt, {pw['gated']} gated clean, {pw['nosrc']} without a "
              f"matched source")
    if n == 0:
        print("  NOTHING WAS SCORED -- 0/0 is not a clean result")


if __name__ == "__main__":
    main()
