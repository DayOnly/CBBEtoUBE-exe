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

"""Read the `survival` records out of a run's audit sink and table them.

    CBBE2UBE_SURVIVAL_TRACE=1 python scripts/convert_one_armor.py ... <out>
    python scripts/analysis/survival_report.py <out>/standoff_audit.jsonl [--nif body_1]
    python scripts/analysis/survival_report.py A.jsonl B.jsonl        # A/B two runs

`survival` is the least-squares scale of a pass's own displacement still present
in the shipped verts: 1.0 intact, 0.0 put back exactly, negative overshot past
the start. It is |d|**2-weighted, so `cancel%` -- the share of moved verts whose
OWN survival is under 10% -- is the column that catches a fully-pinned
SUBPOPULATION hiding inside a healthy aggregate.

EXIT CODES so this can gate a run rather than only inform one: 0 clean, 1 at
least one pass CANCELLED, 2 no survival records at all. The third is the one
that matters -- an empty sink means the trace never armed, and reading that as
"no pass was cancelled" is the failure this whole tool exists to prevent.

READ THE `survPC` COLUMN, NOT `surv`. A record is per SHAPE, and shapes per
garment vary by two orders of magnitude, so pooling rows lets ONE many-shape
piece write the answer. That is not hypothetical: the 2026-08-17 audit reported
"`inflate` is 69% UNDONE by `conform`" from a row-pooled 0.31, and the number
came from a single 116-row garment (median 0.103) inside 442
rows. Weighted per PIECE the same data reads 0.69, and the two convert paths
agree. That headline stood for six days and was never true.

So the summary reports BOTH and flags the gap: `surv` is row-pooled, `survPC` is
the median of per-piece medians, and a `DOMINATED BY` note names any single
piece contributing more than `_DOMINANCE` of a pass's rows. When the two columns
disagree, believe `survPC` and go look at the named piece.
"""
import argparse
import json
import statistics as _stats
import sys
from collections import defaultdict
from pathlib import Path

COLS = ("pass", "moved_verts", "moved_mean", "moved_max", "survival",
        "frac_cancelled", "frac_kept")

# Below this mean motion a survival ratio is arithmetic about nothing -- the
# same floor `fit_metrics.SURVIVAL_MIN_MOTION` applies when it sets
# `low_signal`. Excluded from the medians and COUNTED, never silently dropped.
_LOW_SIGNAL_KEY = "low_signal"
# One piece holding more than this share of a pass's rows can move the pooled
# median on its own, so say its name.
_DOMINANCE = 0.25


def piece_of(r):
    """The GARMENT a record belongs to -- `_0`/`_1` folded together.

    `nif` is a bare filename and filenames repeat across mods, so the record's
    `path` tail is used when present (that is why `_append` stamps it)."""
    nif = str(r.get("nif", "?"))
    stem = nif[:-4] if nif.lower().endswith(".nif") else nif
    if stem.endswith("_0") or stem.endswith("_1"):
        stem = stem[:-2]
    where = str(r.get("path", ""))
    where = where.rsplit("/", 1)[0] if "/" in where else ""
    return f"{where}/{stem}" if where else stem


def _median(xs):
    return _stats.median(xs) if xs else float("nan")


def summarise(rows):
    """Per PASS: row-pooled vs piece-weighted, with the exclusions counted."""
    by = defaultdict(list)
    for r in rows:
        if r.get("pass") == "(after last pass)":
            continue
        by[r["pass"]].append(r)
    out = []
    for name, rs in by.items():
        scored = [r for r in rs
                  if "survival" in r and not r.get(_LOW_SIGNAL_KEY)]
        low = sum(1 for r in rs if r.get(_LOW_SIGNAL_KEY))
        nomove = sum(1 for r in rs if r.get("moved_verts") == 0)
        per_piece = defaultdict(list)
        for r in scored:
            per_piece[piece_of(r)].append(r["survival"])
        piece_meds = [_median(v) for v in per_piece.values() if v]
        worst_piece, worst_share = None, 0.0
        if scored:
            counts = defaultdict(int)
            for r in scored:
                counts[piece_of(r)] += 1
            worst_piece, n = max(counts.items(), key=lambda kv: kv[1])
            worst_share = n / len(scored)
        out.append({
            "pass": name, "rows": len(rs), "scored": len(scored),
            "pieces": len(piece_meds),
            "moved": _median([r["moved_mean"] for r in scored]),
            "surv": _median([r["survival"] for r in scored]),
            "survPC": _median(piece_meds),
            "low": low, "nomove": nomove,
            "dom_piece": worst_piece, "dom_share": worst_share,
        })
    return out


def print_summary(rows, title):
    s = summarise(rows)
    if not s:
        return
    print(f"\n=== SUMMARY BY PASS -- {title} ===")
    print(f"{'pass':<20}{'rows':>5}{'pcs':>5}{'moved':>8}{'surv':>8}"
          f"{'survPC':>8}  {'excluded':<31}{'dominated by':<34}")
    print("-" * 150)
    for r in s:
        ex = []
        if r["nomove"]:
            ex.append(f"{r['nomove']} moved nothing")
        if r["low"]:
            ex.append(f"{r['low']} below signal floor")
        dom = ""
        if r["dom_share"] > _DOMINANCE and r["pieces"] > 1:
            # Last two path parts only: the full tail overflowed the column and
            # ran into the next one, which is how a readable table stops being
            # read at all.
            short = "/".join(str(r["dom_piece"]).split("/")[-2:])
            dom = f"{short} {100 * r['dom_share']:.0f}%"
        gap = abs(r["surv"] - r["survPC"])
        flag = " <-- POOLED vs PER-PIECE DISAGREE" if gap > 0.15 else ""
        exs = ", ".join(ex) if ex else "-"
        if len(exs) > 30:
            exs = exs[:27] + "..."
        print(f"{r['pass']:<20}{r['rows']:>5}{r['pieces']:>5}{r['moved']:>8.3f}"
              f"{r['surv']:>8.3f}{r['survPC']:>8.3f}  "
              f"{exs:<31}{dom:<34}{flag}")
    print("  `surv` pools every SHAPE row; `survPC` is the median of per-PIECE "
          "medians.\n  Where they disagree, believe survPC -- see this file's "
          "docstring for the\n  headline that a pooled median invented and held "
          "for six days.")


def load(path, nif=None, shape=None):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue          # a torn line costs one record, not the file
            if r.get("kind") != "survival":
                continue
            if nif and nif not in r.get("nif", ""):
                continue
            if shape and shape not in r.get("shape", ""):
                continue
            rows.append(r)
    return rows


def _fmt(r):
    if "survival" not in r:
        return f"  {r.get('note') or r.get('skipped') or '?'}"
    flag = ""
    if r.get("verdict") == "CANCELLED":
        flag = "  <-- CANCELLED"
    elif r.get("low_signal"):
        flag = "  (low signal)"
    if r.get("attrib_complete") is False:
        flag += "  [attrib truncated]"
    by = r.get("cancelled_by")
    return (f"{r['moved_verts']:>7} {r['moved_mean']:>7.3f} {r['moved_max']:>7.3f}"
            f" {r['survival']:>8.3f} {100 * r['frac_cancelled']:>6.0f}%"
            f" {100 * r['frac_kept']:>5.0f}%  "
            f"{(by + ' ' + format(r.get('cancelled_frac', 0), '.2f')) if by else '-':<22}"
            f"{flag}")


def table(rows, title):
    print(f"\n=== {title} ===")
    print(f"{'pass':<18}{'moved':>7} {'mean':>7} {'max':>7} {'surv':>8} "
          f"{'cancl':>6} {'kept':>6}  {'cancelled by':<22}")
    print("-" * 100)
    seen = None
    for r in rows:
        key = (r.get("nif"), r.get("shape"))
        if key != seen:
            print(f"  -- {key[0]} :: {key[1]}")
            seen = key
        print(f"{r['pass']:<18}{_fmt(r)}")


def ab(a, b, label_a, label_b):
    """Same pass, two runs, side by side. Keyed on (nif, shape, pass)."""
    ka = {(r.get("nif"), r.get("shape"), r["pass"]): r for r in a}
    kb = {(r.get("nif"), r.get("shape"), r["pass"]): r for r in b}
    print(f"\n=== A/B  A={label_a}  B={label_b} ===")
    print(f"{'nif::shape::pass':<52}{'movedA':>7}{'movedB':>7}"
          f"{'survA':>9}{'survB':>9}{'cancA':>7}{'cancB':>7}")
    print("-" * 98)
    for k in sorted(set(ka) | set(kb)):
        ra, rb = ka.get(k), kb.get(k)
        name = f"{k[0]}::{k[1]}::{k[2]}"

        def g(r, f, d="-"):
            return d if (r is None or f not in r) else r[f]
        print(f"{name:<52}{g(ra, 'moved_verts'):>7}{g(rb, 'moved_verts'):>7}"
              f"{g(ra, 'survival'):>9}{g(rb, 'survival'):>9}"
              f"{g(ra, 'frac_cancelled'):>7}{g(rb, 'frac_cancelled'):>7}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sink", nargs="+", help="one sink to table, two to A/B")
    ap.add_argument("--nif")
    ap.add_argument("--shape")
    args = ap.parse_args()

    sets = [load(p, args.nif, args.shape) for p in args.sink]
    if not any(sets):
        # LOUD. An empty result here is not a clean result, and the whole point
        # of this tool is that a silent nothing reads exactly like a pass.
        print("NO survival records found -- was CBBE2UBE_SURVIVAL_TRACE=1 set?",
              file=sys.stderr)
        return 2
    if len(sets) == 2:
        ab(sets[0], sets[1], Path(args.sink[0]).parent.name,
           Path(args.sink[1]).parent.name)
    for p, rows in zip(args.sink, sets):
        table(rows, Path(p).parent.name or p)
        # AFTER the per-shape detail, deliberately: the detail is what you read
        # to understand ONE piece, the summary is what you quote. Quoting the
        # detail's aggregate is how the row-pooling artifact got out.
        print_summary(rows, Path(p).parent.name or p)
    cancelled = [r for rows in sets for r in rows
                 if r.get("verdict") == "CANCELLED"]
    if cancelled:
        print(f"\n{len(cancelled)} pass instance(s) CANCELLED downstream:")
        for r in cancelled:
            print(f"  {r['nif']}::{r['shape']}::{r['pass']}"
                  f" survival {r['survival']} by {r.get('cancelled_by')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
