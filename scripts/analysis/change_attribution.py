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

"""WHICH CHANGE TOUCHED WHICH PIECE -- read after a run, used after a verdict.

    python scripts/analysis/change_attribution.py <run.log|report.json>...
    python scripts/analysis/change_attribution.py <dir>          # *.log under it
    python scripts/analysis/change_attribution.py ... --tag '#collider-declared-bones'

When a piece looks wrong in game, the question is which of the build's changes
could have done it. Guessing has cost this project real time: the 2026-08-22
build shipped two behaviour changes and its notes could only say "these are the
only two candidates, in order of blast radius". With three or more that stops
working.

Each change now records itself via `nif_convert._note_pass_effect`, which rides
the per-piece `reason` string -- the ONLY channel that crosses the worker-pool
boundary, for the same reason `count_pass_failures` reads `reason` rather than
the module counters. This rolls those markers up.

REPORTS FAILURES SEPARATELY FROM EFFECTS ON PURPOSE. "What broke" and "what
changed" are different questions and blurring them is exactly the confusion the
pair was built to end -- a change with 0 failures and 300 effects is working as
intended; one with 0 effects is not reaching anything.
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

EFFECT = re.compile(r"CHANGED BY (#[\w-]+)(?: \(([^)]*)\))?")
FAILED = re.compile(r"PASS FAILED ([\w/-]+)")
# `  <stem>_0: converted (copy)  -- <reason>` and the batch report's own lines.
PIECE = re.compile(r"([^\s/\\]+_[01]\.nif|[^\s/\\]+_[01]):")


def scan(text):
    effects = collections.defaultdict(set)
    failures = collections.Counter()
    detail = collections.defaultdict(list)
    piece = "?"
    for line in text.splitlines():
        m = PIECE.search(line)
        if m:
            piece = m.group(1)
        for tag, det in EFFECT.findall(line):
            effects[tag].add(piece)
            if det:
                detail[tag].append(f"{piece}: {det}")
        for lbl in FAILED.findall(line):
            failures[lbl] += 1
    return effects, failures, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--tag", help="list every piece this change touched")
    ap.add_argument("--max-detail", type=int, default=12)
    args = ap.parse_args()

    files = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(p.rglob("*.log")) + sorted(p.rglob("*.txt"))
        elif p.is_file():
            files.append(p)
    if not files:
        print("no readable input", file=sys.stderr)
        return 2

    effects = collections.defaultdict(set)
    failures = collections.Counter()
    detail = collections.defaultdict(list)
    for f in files:
        try:
            e, fa, d = scan(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for k, v in e.items():
            effects[k] |= v
        failures.update(fa)
        for k, v in d.items():
            detail[k] += v

    print(f"scanned {len(files)} file(s)")
    if args.tag:
        pieces = sorted(effects.get(args.tag, ()))
        print(f"\n{args.tag} touched {len(pieces)} piece(s):")
        for p in pieces:
            print(f"    {p}")
        for line in detail.get(args.tag, [])[:args.max_detail]:
            print(f"      {line}")
        return 0

    print("\n=== CHANGES THAT TOUCHED SOMETHING (attribution) ===")
    if not effects:
        # A CHANGE THAT RECORDS NOTHING IS NOT A CHANGE THAT DID NOTHING -- it
        # may simply not be wired to record. Say which, rather than printing a
        # reassuring zero.
        print("    none recorded. Either no change fired, or none of the "
              "changes in this build call `_note_pass_effect` -- check before "
              "reading this as 'nothing changed'.")
    for tag, pieces in sorted(effects.items(), key=lambda kv: -len(kv[1])):
        print(f"    {len(pieces):5}  {tag}")
        for line in detail.get(tag, [])[:3]:
            print(f"             e.g. {line}")
    print("\n=== FAILURES (a different question: what BROKE) ===")
    if not failures:
        print("    none")
    for lbl, n in failures.most_common():
        print(f"    {n:5}  {lbl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
