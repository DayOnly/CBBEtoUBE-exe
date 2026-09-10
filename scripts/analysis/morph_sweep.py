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

"""THE MORPH AXIS of an A/B -- bind-pose numbers cannot see it.

    python -m scripts.analysis.morph_sweep <ctrl_dir> <cand_dir>
        [--band bust|butt] [--jobs N] [--limit N] [--tol U]

Every clip number in the acceptance gate is taken at BIND POSE, and bind pose is
not what ships: in game the body is morphed by the player's sliders. A garment
that clears the bind body perfectly can still be punched through by the morphed
one -- and, the case that motivated the underlying tool, a change that RESTORES
morph following moves no vertex at all, so a bind-pose metric reads 0.000 and
looks like nothing happened. This is the axis that REJECTED the early-clearance
consolidation after the bind-pose numbers had already passed it.

WHY THIS EXISTS AS A TRACKED TOOL. The consolidation plan names `morph_sweep`
as half the gate for its three biggest geometry items (A1 conform target, A2
warp field, A4 the write-time double run) -- but until 2026-09-06 the only
`morph_sweep` was an UNTRACKED scratch script under a 2026-08-13 handoff folder,
carrying hardcoded machine paths, keyed to a `runs/<arm>__<piece>` layout that
nothing else produces, and marked "Not tracked, not tested" by its own README.
So the plan's stated gate could not actually be run. This is that tool rebuilt
against the ARM DIRECTORIES `parity_convert` produces, so it drops straight into
the same workflow as `acceptance`.

It shells out per NIF to `morph_clip_test.py`, which is the tracked instrument
and owns the model (body morphs from the UBE body's own sliders, garment from
its own `.tri` by the SAME slider name). This layer only pairs and aggregates.

THE THREE TRAPS IT IS BUILT AROUND, all of which have produced a wrong answer
in this project before:

  * **0/0 IS NOT A PASS.** A garment that does not reach the band scores 0.000
    clip because nothing of it is there. A piece is scored only when BOTH arms
    cover at least `MIN_COVER` of the band, and every exclusion is COUNTED and
    printed. An arm that failed to convert half the sample must not read as a
    clean win.
  * **PAIR BY PATH, NEVER BY NAME.** Pieces are matched on their path relative
    to the arm root. Two mods ship `cuirass_1.nif`.
  * **PER PIECE, NOT POOLED.** A pooled mean is carried by whichever garment has
    the most verts in the band; the damage ledger's own lesson is that one
    garment counted twice can invert a pack-wide verdict. Reported as
    better/worse/unchanged counts plus the median and worst per-piece delta.

`_1` only: `_0` and `_1` are one garment at two weights, so scoring both would
double-count it (and `morph_clip_test` reads the high weight, which is what the
sliders drive). Exit 0 when the candidate is no worse beyond `--tol`, 1 when it
is worse, 2 when the population is empty.
"""
from __future__ import annotations

import os
import re
import statistics as st
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent

# clip% coinc shal buried cover%
ROW = re.compile(r"^(BIND|MORPH)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+"
                 r"([\d.]+)\s+([\d.]+)", re.M)

MIN_COVER = 2.0        # percent of the band the garment must actually cover
DEFAULT_TOL = 0.05     # clip% a candidate may be worse by and still pass


def _nifs(root: str) -> "dict[str, Path]":
    """Every `_1` mesh under an arm, keyed by its path RELATIVE to the arm."""
    base = Path(root)
    out: "dict[str, Path]" = {}
    for p in base.rglob("*_1.nif"):
        if "_bsa_staging" in p.parts:
            continue
        out[p.relative_to(base).as_posix().lower()] = p
    return out


def measure(args):
    """One `morph_clip_test` run. Returns (rel, arm, dict|None)."""
    rel, arm, nif, band = args
    env = dict(os.environ)
    env.setdefault("PYTHONHASHSEED", "1")
    try:
        p = subprocess.run(
            [sys.executable,
             str(_REPO / "scripts" / "analysis" / "morph_clip_test.py"),
             str(nif), "--band", band, "--strength", "1.0"],
            capture_output=True, text=True, cwd=str(_REPO), env=env,
            timeout=600)
    except Exception:
        return rel, arm, None
    got = {m.group(1): (float(m.group(2)), float(m.group(6)))
           for m in ROW.finditer(p.stdout or "")}
    if "BIND" not in got or "MORPH" not in got:
        return rel, arm, None
    return rel, arm, {"bind": got["BIND"][0], "morph": got["MORPH"][0],
                      "bind_cov": got["BIND"][1], "morph_cov": got["MORPH"][1]}


def aggregate(common, res, tol=DEFAULT_TOL):
    """Pair the per-arm measurements into scored rows + counted exclusions.

    PURE, and separate from `main` on purpose: the traps this tool exists to
    avoid all live here, and a test must be able to reach them without spawning
    a single `morph_clip_test`. Returns `(rows, unreadable, thin)` where a row
    is `(rel, ctrl_morph, cand_morph, delta, ctrl_bind, cand_bind)`.
    """
    unreadable = thin = 0
    rows = []
    for rel in common:
        ca, cb = res.get(rel, {}).get("ctrl"), res.get(rel, {}).get("cand")
        if ca is None or cb is None:
            unreadable += 1
            continue
        # BOTH arms must really cover the band. A garment that is not there
        # scores a perfect 0.000 clip, which is the "0/0 is not a pass" trap.
        if ca["morph_cov"] < MIN_COVER or cb["morph_cov"] < MIN_COVER:
            thin += 1
            continue
        rows.append((rel, ca["morph"], cb["morph"], cb["morph"] - ca["morph"],
                     ca["bind"], cb["bind"]))
    return rows, unreadable, thin


# Flags that consume the NEXT argument. Filtering on `startswith("--")` alone
# leaves their VALUES in the positional list, so `--band bust` reads as two
# extra positionals and the tool prints its help instead of running. Cheap
# mistake, and it makes a working invocation look like a usage error.
_VALUED = ("--band", "--jobs", "--limit", "--tol")


def positionals(argv: "list[str]") -> "list[str]":
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a in _VALUED:
            skip = True
            continue
        if a.startswith("--"):
            continue
        out.append(a)
    return out


def main(argv: "list[str]") -> int:
    args = positionals(argv)
    if len(args) != 2:
        print(__doc__)
        return 2
    ctrl, cand = args
    band = "bust"
    if "--band" in argv:
        band = argv[argv.index("--band") + 1]
    jobs = 8
    if "--jobs" in argv:
        jobs = int(argv[argv.index("--jobs") + 1])
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    tol = DEFAULT_TOL
    if "--tol" in argv:
        tol = float(argv[argv.index("--tol") + 1])

    a, b = _nifs(ctrl), _nifs(cand)
    common = sorted(set(a) & set(b))
    if limit:
        common = common[:limit]

    print("POPULATION  (band: %s)" % band)
    print(f"  control    : {len(a)} `_1` mesh(es)")
    print(f"  candidate  : {len(b)} `_1` mesh(es)")
    print(f"  common     : {len(common)}")
    print(f"  unpaired   : {len(set(a) ^ set(b))}   (excluded, counted)")
    if not common:
        print("  NOTHING TO COMPARE -- 0/0 IS NOT A PASS")
        return 2

    work = ([(rel, "ctrl", a[rel], band) for rel in common]
            + [(rel, "cand", b[rel], band) for rel in common])
    res: "dict[str, dict]" = {}
    done = 0
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for rel, arm, got in ex.map(measure, work):
            res.setdefault(rel, {})[arm] = got
            done += 1
            if done % 50 == 0:
                print(f"    ... {done}/{len(work)} measured", flush=True)

    unreadable = thin = 0
    rows = []
    for rel in common:
        ca, cb = res.get(rel, {}).get("ctrl"), res.get(rel, {}).get("cand")
        if ca is None or cb is None:
            unreadable += 1
            continue
        if ca["morph_cov"] < MIN_COVER or cb["morph_cov"] < MIN_COVER:
            thin += 1
            continue
        rows.append((rel, ca["morph"], cb["morph"], cb["morph"] - ca["morph"],
                     ca["bind"], cb["bind"]))

    print("\nEXCLUSIONS (counted, never silently dropped)")
    print(f"  unreadable / no BIND+MORPH row : {unreadable}")
    print(f"  under {MIN_COVER:.1f}% band coverage in an arm : {thin}")
    print(f"  SCORED                         : {len(rows)}")
    if not rows:
        print("  NOTHING SCORED -- 0/0 IS NOT A PASS")
        return 2

    worse = [r for r in rows if r[3] > tol]
    better = [r for r in rows if r[3] < -tol]
    same = len(rows) - len(worse) - len(better)
    deltas = [r[3] for r in rows]

    print("\nPER PIECE, MORPHED clip%  (candidate - control)")
    print(f"  {'piece':<52}{'ctrl':>8}{'cand':>8}{'delta':>9}")
    for rel, ca, cb, d, _ba, _bb in sorted(rows, key=lambda r: -abs(r[3]))[:15]:
        print(f"  {rel[-52:]:<52}{ca:>8.3f}{cb:>8.3f}{d:>+9.3f}")

    print("\nSUMMARY")
    print(f"  better (> {tol}) : {len(better)}")
    print(f"  worse  (> {tol}) : {len(worse)}")
    print(f"  unchanged        : {same}")
    print(f"  median delta     : {st.median(deltas):+.4f}")
    print(f"  worst  delta     : {max(deltas):+.4f}")
    print(f"  best   delta     : {min(deltas):+.4f}")
    ok = not worse
    print("\nVERDICT: " + ("MORPH AXIS OK -- no piece worse beyond tolerance"
                           if ok else
                           f"WORSE ON {len(worse)} PIECE(S) UNDER MORPH"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
