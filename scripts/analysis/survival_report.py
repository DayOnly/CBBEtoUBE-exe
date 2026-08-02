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
"""
import argparse
import json
import sys
from pathlib import Path

COLS = ("pass", "moved_verts", "moved_mean", "moved_max", "survival",
        "frac_cancelled", "frac_kept")


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
