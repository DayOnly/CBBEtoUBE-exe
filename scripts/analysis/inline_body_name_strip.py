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

"""WHAT THE NAME-ONLY BODY TEST STRIPS  #body-name-prefix

    python -m scripts.analysis.inline_body_name_strip <out_dir> [--limit N]

`_looks_like_inline_body` matches `BODY_SHAPE_NAME_PREFIXES` with
`name.lower().startswith(...)` and returns True BEFORE any geometry or texture
gate. That is the opposite of the `3BA`-family branch a few lines below it,
whose own comment says the name must be paired with a full-body geometry gate
because a name alone over-fires -- and it does: on the shipped pack one garment
has its CUIRASS and its ARMS named `FemaleUnderwearBody:0` and
`...:0_1` by the author, and both are deleted and replaced with one injected
body. That half ships with no cuirass and no arms, and it is the `_1` half,
which is the one actors near weight 100 use.

WHAT THIS COUNTS. Every source NIF behind a converted output, resolved the way
the pipeline resolves it (`canonical_body.find_source`: garment names, with the
BSA index as the fallback), and in each one the shapes matching the prefixes,
split by whether they carry a BODY-SKIN diffuse. A body-skin one is a real
placeholder body and stripping it is correct; a garment diffuse is a wrong
strip. The two are separated by `_shape_diffuse_is_body_skin`, which the
detector's own general heuristic already uses -- so the fix is to gate the
prefix branch on the test that is already in the file, not to add a new rule.

LOOSE AND ARCHIVED ARE REPORTED APART, because an earlier census covered only
the sources the converter had STAGED out of BSAs and had to name the loose half
as an uncounted exclusion. A number over one half of a population is not a
number over the population.

Exit 0 = measured, no wrong strip; 1 = wrong strips found; 3 = nothing measured
(0/0 is not a pass).
"""
from __future__ import annotations

import collections
import glob
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from src import nif_convert as nc                              # noqa: E402
from src import nif_io                                         # noqa: E402
from scripts.analysis import canonical_body as cb              # noqa: E402
from scripts.analysis._census_common import require_population  # noqa: E402

BODY_NAMES = set(nc.UBE_BODY_INJECT_NAMES) | {"3BA"}
PROXY_NAMES = {"VirtualBody", "VirtualGround", "SkirtCol", "ButtCol"}


def is_body_skin(shape) -> bool:
    """The detector's OWN texture gate, called through the module so a change
    to the marker list cannot leave this census scoring the old rule."""
    return bool(nc._shape_diffuse_is_body_skin(shape))


def prefix_named(shapes) -> list:
    """Shapes the name-only branch would strip, whatever else is true of them."""
    return [s for s in shapes
            if (s.name or "").lower().startswith(nc.BODY_SHAPE_NAME_PREFIXES)]


def origin_of(path) -> str:
    """`bsa` when the file came out of the harness staging dir, else `loose`.

    The two are reported apart because a census over only one of them is a
    number about half a population.
    """
    staging = Path(tempfile.gettempdir()) / "cbbe2ube_harness_bsa"
    try:
        Path(path).relative_to(staging)
        return "bsa"
    except Exception:
        return "loose"


def scan(out_dir: str, limit: int = 0):
    """(rows, exclusions, sources_read).

    One row per prefix-named SOURCE shape. `sources_read` is the DENOMINATOR
    and is returned separately on purpose: rows exist only for sources that had
    a prefix hit, so a clean pack has zero rows -- which is a pass, not an empty
    population, and the two must never be confused.
    """
    rows: list = []
    dropped: collections.Counter = collections.Counter()
    meshes = os.path.join(out_dir, "meshes")
    mods_root = None
    seen_src: set = set()
    n = 0
    for p in sorted(glob.glob(os.path.join(meshes, "**", "*.nif"),
                              recursive=True)):
        rel = os.path.relpath(p, meshes)
        if not rel.lower().startswith("!ube" + os.sep):
            continue
        if limit and n >= limit:
            break
        n += 1
        try:
            nf = nif_io.open_nif_retry(p)
        except Exception:
            dropped["output NIF failed to load"] += 1
            continue
        want = [s.name for s in nf.shapes
                if s.name not in BODY_NAMES and s.name not in PROXY_NAMES]
        if not want:
            dropped["output NIF has no garment shape of its own"] += 1
            continue
        inner = rel.split(os.sep, 1)[1] if os.sep in rel else rel
        if mods_root is None:
            mods_root = cb._resolve_mods_root()
        src = cb.find_source(inner, want, mods_root)
        if src is None:
            dropped["no author source resolved"] += 1
            continue
        key = str(src).lower()
        # ONE SOURCE CAN BACK BOTH WEIGHT HALVES and several outputs. Counting
        # it twice would inflate the class by the pack's own file layout.
        if key in seen_src:
            dropped["source already counted for another output"] += 1
            continue
        seen_src.add(key)
        try:
            shapes = list(nif_io.open_nif_retry(str(src)).shapes)
        except Exception:
            dropped["author source failed to load"] += 1
            continue
        for s in prefix_named(shapes):
            try:
                nv = len(s.verts)
            except Exception:
                nv = -1
            rows.append({
                "rel": inner.replace("\\", "/"),
                "shape": s.name,
                "verts": nv,
                "body_skin": is_body_skin(s),
                # What the LIVE detector decides, which is the only thing that
                # deletes anything. Before the gate this equalled "the prefix
                # matched"; after it, a garment-diffuse shape is spared.
                "stripped": bool(nc._looks_like_inline_body(s)),
                "origin": origin_of(src),
            })
    return rows, dropped, len(seen_src)


def stripped_wrongly(rows) -> list:
    """Shapes the LIVE detector strips that are NOT body skin -- real armour
    deleted. Distinct from `at_risk`: that is what a NAME-ONLY test WOULD take,
    which is the hazard the gate exists to stop and does not shrink when the
    gate lands (the sources are unchanged). Keying the exit on the hazard makes
    a fixed checker fail forever, and a checker that always fails is one nobody
    reads."""
    return [r for r in rows if r.get("stripped") and not r["body_skin"]]


def report(rows, dropped, sources, out=print) -> int:
    wrong = [r for r in rows if not r["body_skin"]]
    right = [r for r in rows if r["body_skin"]]
    out("=" * 74)
    out("WHAT THE NAME-ONLY BODY TEST STRIPS   #body-name-prefix")
    out("=" * 74)
    out("  distinct author sources read              : %d" % sources)
    for reason, k in sorted(dropped.items(), key=lambda kv: -kv[1]):
        out("    not scored %5d  %s" % (k, reason))
    out("")
    out("  shapes matching BODY_SHAPE_NAME_PREFIXES  : %d" % len(rows))
    for org in ("loose", "bsa"):
        o = [r for r in rows if r["origin"] == org]
        ow = [r for r in o if not r["body_skin"]]
        out("    from %-5s sources                     : %d   (%d WRONG)"
            % (org, len(o), len(ow)))
    out("")
    out("  body-skin diffuse -> CORRECT strip        : %d" % len(right))
    out("  garment diffuse   -> AT RISK from a name-only test : %d" % len(wrong))
    for r in sorted(wrong, key=lambda x: (x["rel"], x["shape"]))[:40]:
        out("      %-46s %-26s verts %6d  [%s]"
            % (r["rel"][:46], r["shape"], r["verts"], r["origin"]))
    if len(wrong) > 40:
        out("      ... and %d more" % (len(wrong) - 40))
    out("      ^ the HAZARD, not a defect. These sources do not change when the")
    out("        gate lands, so this number stays put -- it is what would be")
    out("        deleted if the texture test were ever removed.")
    out("")
    bad = stripped_wrongly(rows)
    out("  the LIVE detector strips it anyway        : %d%s"
        % (len(bad), "   <== real armour deleted" if bad else "   OK, gate holding"))
    for r in sorted(bad, key=lambda x: (x["rel"], x["shape"]))[:40]:
        out("      %-46s %-26s verts %6d  [%s]"
            % (r["rel"][:46], r["shape"], r["verts"], r["origin"]))
    return 1 if bad else 0


def main(argv=None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    limit, args = 0, []
    i = 0
    while i < len(raw):
        # A VALUED FLAG MUST EAT ITS VALUE, or a valid invocation prints help.
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
    rows, dropped, sources = scan(args[0], limit)
    # THE FLOOR IS ON SOURCES READ, never on prefix hits: a clean pack has zero
    # rows and that is a PASS, while a run that resolved no source at all has
    # measured nothing. Confusing the two is exactly the 0/0 bug the pair-tri
    # checker shipped with for a day.
    require_population(range(sources), "author sources read")
    return report(rows, dropped, sources)


if __name__ == "__main__":
    sys.exit(main())
