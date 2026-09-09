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

"""HOW MANY PIECES RESOLVE THEIR PHYSICS XML BY A STEM MATCH  #hdt-xml-race

    python -m scripts.analysis.hdt_xml_resolution_census <out_dir> [--limit N]

The bust-collider split was found to be a COIN FLIP: two arms of identical code
produced different meshes because `_find_hdt_xml_for_armor` takes a same-stem
XML from ANY directory when exactly one exists, and the tree it searches is the
DESTINATION -- which the run is still writing into. `_mod_xml_index` then
memoises that glob per worker, so each one freezes a different moment.

**THIS COUNTS THE EXPOSURE, which is the number a fix decision needs.** Three
repairs are available and they are not equivalent (refuse a cross-directory stem
match; refuse to index a destination root; drop the memo for the destination),
and picking between them without knowing how many pieces currently depend on
that fallback is guessing.

WHO IS EXPOSED, per output NIF:

  pointer     the NIF names its own XML in extra-data. Deterministic, safe --
              resolution never reaches the stem match.
  own-dir     no pointer, but an XML sits beside it. `same_dir_exact` wins and
              is unaffected by what else exists. Safe.
  AT RISK     no pointer, no XML beside it, and at least one XML ELSEWHERE in
              the tree shares its stem. This piece's answer depends on how much
              of the tree had been written when its worker looked.
  no-match    no pointer, no neighbour, no same-stem XML anywhere. Resolves to
              nothing however the run is ordered.

The `_0`/`_1` suffix is stripped before matching, exactly as the resolver does,
so a weight pair counts once per half and the garment count is reported beside
the file count.

Exit 0 = measured and nothing at risk, 1 = pieces at risk, 3 = nothing measured.
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

from src import nif_io                                          # noqa: E402
from scripts.analysis._census_common import require_population   # noqa: E402

_HDT = "HDT Skinned Mesh Physics Object"


def stem_of(path) -> str:
    """The resolver's own key: the NIF stem with a `_0`/`_1` suffix removed."""
    s = Path(path).stem.lower()
    for suf in ("_0", "_1"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def declares_xml(nif) -> bool:
    """True when the NIF names its own physics XML in root extra-data.

    `extra_data()` STOPS at the first block it cannot build, so this is a lower
    bound -- a hidden pointer reads as absent and the piece is counted at risk
    when it may not be. Stated rather than silently assumed: the direction of
    the error is toward OVER-reporting, which is the safe way round for an
    exposure count.
    """
    try:
        for ed in nif.rootNode.extra_data():
            if getattr(ed, "name", None) == _HDT:
                return True
    except Exception:
        pass
    return False


def scan(out_dir: str, limit: int = 0):
    meshes = os.path.join(out_dir, "meshes")
    xml_by_stem: dict = collections.defaultdict(set)
    for x in glob.glob(os.path.join(meshes, "**", "*.xml"), recursive=True):
        if "_bsa_staging" in x:
            continue
        xml_by_stem[Path(x).stem.lower()].add(os.path.dirname(x))

    rows, dropped = [], collections.Counter()
    n = 0
    for p in sorted(glob.glob(os.path.join(meshes, "**", "*.nif"),
                              recursive=True)):
        rel = os.path.relpath(p, meshes)
        if not rel.lower().startswith("!ube" + os.sep):
            continue
        if "_bsa_staging" in rel:
            continue
        if limit and n >= limit:
            break
        n += 1
        try:
            nf = nif_io.open_nif_retry(p)
        except Exception:
            dropped["NIF failed to load"] += 1
            continue
        stem = stem_of(p)
        dirs = xml_by_stem.get(stem, set())
        own = os.path.dirname(p)
        if declares_xml(nf):
            cls = "pointer"
        elif own in dirs:
            cls = "own-dir"
        elif dirs:
            cls = "AT RISK"
        else:
            cls = "no-match"
        rows.append({"rel": rel.replace("\\", "/"), "stem": stem,
                     "cls": cls, "elsewhere": len(dirs - {own})})
    return rows, dropped


def report(rows, dropped, out=print) -> int:
    by = collections.Counter(r["cls"] for r in rows)
    risk = [r for r in rows if r["cls"] == "AT RISK"]
    garments = {(Path(r["rel"]).parent.as_posix(), r["stem"]) for r in risk}
    out("=" * 74)
    out("PHYSICS-XML RESOLUTION, PER PIECE   #hdt-xml-race")
    out("=" * 74)
    out("  NIFs scanned                              : %d" % len(rows))
    for reason, k in sorted(dropped.items(), key=lambda kv: -kv[1]):
        out("    not scored %5d  %s" % (k, reason))
    out("")
    for cls, why in (("pointer", "names its own XML -- deterministic"),
                     ("own-dir", "an XML sits beside it -- deterministic"),
                     ("no-match", "no same-stem XML anywhere -- resolves to none"),
                     ("AT RISK", "resolved by a CROSS-DIRECTORY stem match")):
        out("  %-9s %6d   %s" % (cls, by.get(cls, 0), why))
    out("")
    out("  AT RISK, as garments (both weight halves are one)  : %d"
        % len(garments))
    out("      ^ their answer depends on how much of the destination tree had")
    out("        been written when their worker globbed it.")
    if risk:
        out("")
        out("  the stems involved, and how many OTHER directories share each:")
        seen = collections.Counter()
        for r in risk:
            seen[(r["stem"], r["elsewhere"])] += 1
        for (stem, n_else), k in sorted(seen.items(), key=lambda kv: -kv[1])[:20]:
            out("    %-28s %3d NIF(s), %d other dir(s)" % (stem, k, n_else))
    return 1 if risk else 0


def main(argv=None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    limit, args, i = 0, [], 0
    while i < len(raw):
        if raw[i] == "--limit" and i + 1 < len(raw):
            limit = int(raw[i + 1]); i += 2; continue
        if raw[i].startswith("--"):
            i += 1; continue
        args.append(raw[i]); i += 1
    if len(args) != 1:
        print(__doc__)
        return 2
    if not os.path.isdir(os.path.join(args[0], "meshes")):
        print("MISSING: %s (expected a dir with meshes/!UBE)" % args[0])
        return 2
    rows, dropped = scan(args[0], limit)
    require_population(rows, "converted NIFs")
    return report(rows, dropped)


if __name__ == "__main__":
    sys.exit(main())
