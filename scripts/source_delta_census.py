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

    python scripts/source_delta_census.py [--out FILE] [--limit N] [--sample N]
                                          [--gate PCT]

WHY A DELTA. Every level-based metric confounds "the converter broke it" with "the
author made it that way", and that confound produced four wrong conclusions in one day
-- including a 30-armour worst-offender list that turned out to be mostly armour which
already behaved that way. Posing the SOURCE garment on its OWN body and the converted
one on UBE, then differencing the coverage loss, cancels authored behaviour: a skirt
that parts in a crouch does so on both sides and reads ~0. Only what WE changed
survives, so every positive row is actionable by construction.

Measured on the first five: three authored (delta +0.7 / +1.2 / +4.5), one where the
converter IMPROVED the garment (-18.4), one real regression (+12.1).

SOURCE SELECTION IS PART OF THE METRIC. Not "the last mod providing this mesh path" --
two mods can ship the same path with different geometry, and picking the wrong one
inverted one armour from +0.7 to +83.3, i.e. from "authored" to "our fault". The source
is chosen by GARMENT SHAPE-NAME OVERLAP with the output, which ties both sides to the
same garment.

Read-only. Resolves the output via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                          # noqa: E402
from src import paths                                            # noqa: E402
from scripts.multipose_clip_test import analyse_with_body        # noqa: E402
from scripts.canonical_body import (canonical_cbbe, canonical_ube,  # noqa: E402
                                    converted_garment_names, find_source)

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

    lay = paths.discover_layout()
    mods_root = lay.mods_root
    root = (mods_root / os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
            / "meshes" / "!UBE")
    if not root.is_dir():
        raise SystemExit(f"output not found: {root}")

    files = []
    for p in sorted(root.rglob("*_1.nif")):
        stem = p.stem.lower()
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if ("1st" in stem or "firstperson" in stem or stem.endswith("fp")
                or "_fp_" in stem or "1person" in stem):
            continue
        files.append(p)
    if limit:
        files = files[:limit]
    cbbe_path, cbbe_shape = canonical_cbbe(str(mods_root))
    ube_path, ube_shape = canonical_ube()
    print(f"{len(files)} armors, sample={sample}, gate={gate}%")
    print(f"  source body : {cbbe_shape}  {Path(cbbe_path).parent.parent.parent.parent.name}")
    print(f"  target body : {ube_shape}  {Path(ube_path).name}")

    t0, n, reg, gated, nosrc = time.time(), 0, 0, 0, 0
    with out.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(files, 1):
            rel = str(p.relative_to(root)).replace("\\", "/")
            try:
                gnames = converted_garment_names(p)
                if not gnames:
                    continue
                conv, ic = analyse_with_body(p, gnames, ube_path, ube_shape,
                                             sample=sample)
            except (Exception, SystemExit):
                continue
            if ic > 1e-4:
                continue
            worst = max((r["worst_pct"] for r in conv.values()), default=0.0)
            row = {"armor": rel, "conv_worst": round(worst, 3)}
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
            if i % 20 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(files)}  {reg} regressions, {gated} gated, "
                      f"{nosrc} no-source, {el/60:.0f}m, "
                      f"~{el/i*(len(files)-i)/60:.0f}m left", flush=True)
    print(f"\n{n} rows -> {out}   {reg} regressions >5pt, {gated} gated clean, "
          f"{nosrc} without a matched source   ({(time.time()-t0)/60:.0f} min)")


if __name__ == "__main__":
    main()
