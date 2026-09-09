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

"""DEAD SLIDERS AT WEIGHT 100: does each `_0`/`_1` half name anything in its
own tri?  #pair-tri-names

    python -m scripts.analysis.weight_pair_tri_names <mod-dir> [--limit N]

A garment ships as two meshes and they share ONE tri, built from the `_0` half.
Authors routinely name the two halves' shapes differently (`BodyF_0`/`BodyF_1`,
`BootsF:0`/`BootsF:1`, `Robe_0`/`Robe_1`, `Dress`/`Dress1`), and the half the
tri was not built from then names NOTHING in it, so its sliders move nothing.
`_1` is the half that ships, since actors sit near weight 100.

THIS EXISTS SO THE PREDICTION CAN BE CHECKED. `#pair-tri-names` is default OFF;
when it is armed and a pack reconverted, this must go

    halves whose shape names DIFFER        54   (unchanged -- authored)
    pairs where a half loses its morph     54 -> 1

and the surviving 1 is the pair with 5 shapes vs 4, which `pair_shape_aliases`
refuses by design. It was written as a scratchpad probe on 2026-09-07 and
promoted immediately: a prediction whose checker lives in a gitignored folder
is a prediction nobody else can falsify.

Reads BODYTRI with `_census_common.bodytri_of` -- `extra_data()` stops at the
first unbuildable block and has produced two confidently wrong censuses here.

Exit 0 clean, 1 defects found, 3 nothing measured.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import logging  # noqa: E402
logging.disable(logging.WARNING)

from pyn.pynifly import NifFile                     # noqa: E402
from src.tri import TriFile                         # noqa: E402
from _census_common import bodytri_of, require_population  # noqa: E402


def pairs_under(ube_root: Path) -> dict:
    """{(dir, base) -> {"0": path, "1": path}} for every weight pair."""
    out: dict = {}
    for p in ube_root.rglob("*.nif"):
        n = p.name
        if len(n) > 6 and n[-6:-4] in ("_0", "_1"):
            out.setdefault((p.parent, n[:-6]), {})[n[-5]] = p
    return out


def tri_shape_names(ube_root: Path, bodytri: str, cache: dict):
    """Shape names in the tri a BODYTRI string points at, or None if unreadable.

    The string is stored game-relative (`!UBE\\...` or `meshes\\...`); strip
    whichever prefix it carries before joining it onto the output root.
    """
    if bodytri in cache:
        return cache[bodytri]
    rel = bodytri.replace("/", os.sep)
    for pre in ("meshes" + os.sep, "!UBE" + os.sep):
        if rel.lower().startswith(pre.lower()):
            rel = rel[len(pre):]
    f = ube_root / rel
    try:
        cache[bodytri] = {s.name for s in TriFile.load(str(f)).shapes}
    except Exception:
        cache[bodytri] = None
    return cache[bodytri]


def score(ube_root: Path, limit: int | None = None):
    """(stat Counter, [(rel, {half: "ALL"|"SOME"}, names0, names1), ...])."""
    stat: Counter = Counter()
    rows = []
    cache: dict = {}
    items = sorted(pairs_under(ube_root).items())
    if limit:
        items = items[:limit]
    for (d, base), have in items:
        if "0" not in have or "1" not in have:
            stat["excluded: single-weight (no pair)"] += 1
            continue
        stat["pairs examined"] += 1
        per = {}
        for w in ("0", "1"):
            try:
                nf = NifFile(str(have[w]))
            except Exception:
                per = None
                break
            per[w] = ({s.name for s in nf.shapes},
                      sorted({b for s in nf.shapes for b in bodytri_of(s)}))
        if per is None:
            stat["excluded: NIF unreadable"] += 1
            continue
        if not per["0"][1] and not per["1"][1]:
            stat["excluded: no BODYTRI on either half"] += 1
            continue
        stat["pairs sharing ONE tri" if per["0"][1] == per["1"][1]
             else "pairs with per-half tris"] += 1
        # THE NUMBER THE POPULATION FLOOR MUST USE. `pairs examined` counts
        # everything that reached this loop, exclusions included, so flooring
        # on it let a run where EVERY pair was excluded print "0 dead ... OK" --
        # 0/0 read as a pass, in a tool written the same week that rule was
        # being enforced everywhere else. Caught by its own test.
        stat["pairs SCORED"] += 1
        if per["0"][0] == per["1"][0]:
            stat["halves whose shape names AGREE"] += 1
            continue
        stat["halves whose shape names DIFFER"] += 1
        lost = {}
        for w in ("0", "1"):
            names, tris = per[w]
            seen, missing_tri = set(), False
            for b in tris:
                got = tri_shape_names(ube_root, b, cache)
                if got is None:
                    missing_tri = True
                else:
                    seen |= got
            if missing_tri:
                continue
            if not (names & seen):
                lost[w] = "ALL"
            elif names - seen:
                lost[w] = "SOME"
        if lost:
            stat["pairs where a half loses its morph"] += 1
            for w, kind in lost.items():
                stat["  half _%s dead: %s" % (w, kind)] += 1
            rows.append((str(d.relative_to(ube_root)) + os.sep + base, lost,
                         sorted(per["0"][0]), sorted(per["1"][0])))
    return stat, rows


def main(argv=None) -> int:
    args = [a for a in (argv if argv is not None else sys.argv[1:])
            if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    lim = None
    raw = argv if argv is not None else sys.argv[1:]
    if "--limit" in raw:
        try:
            lim = int(raw[raw.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit needs a number")
            return 2
    # Accept either the MOD dir or the `!UBE` root itself, and NOTHING ELSE.
    # An earlier draft fell back to the given directory when neither existed,
    # which walked whatever tree it was handed and then reported on it -- a
    # mistyped path would have produced a confident number about the wrong
    # files instead of a refusal.
    root = Path(args[0])
    for cand in (root / "meshes" / "!UBE", root):
        if cand.is_dir() and any(cand.rglob("*.nif")):
            ube = cand
            break
    else:
        print("no converted NIFs under %s -- expected <mod-dir> (with "
              "meshes/!UBE) or the !UBE root itself. 0/0 is not a pass." % root)
        return 3

    stat, rows = score(ube, lim)
    # On the SCORED pairs, not the examined ones -- see `pairs SCORED`.
    require_population(range(stat["pairs SCORED"]), "SCORED weight pairs")

    print("=" * 74)
    print("TRI SHAPE-NAME AGREEMENT ACROSS THE `_0`/`_1` PAIR   #pair-tri-names")
    print("=" * 74)
    for k in sorted(stat):
        print("  %-44s %d" % (k, stat[k]))
    print()
    print("PIECES WHERE ONE HALF'S MORPH IS DEAD      : %d%s"
          % (len(rows), "   <== dead sliders" if rows else "   OK"))
    for rel, lost, s0, s1 in rows[:40]:
        print("  %s" % rel)
        print("      _0 %-40s _1 %s"
              % (",".join(s0)[:38], ",".join(s1)[:38]))
        print("      dead: %s"
              % ", ".join("_%s=%s" % (w, k) for w, k in sorted(lost.items())))
    if len(rows) > 40:
        print("  ... and %d more" % (len(rows) - 40))
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
