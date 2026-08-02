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

"""Read `standoff_audit.jsonl` and say what the run actually did.

    python scripts/analysis/audit_sink.py <output-mod-root-or-jsonl> [--all-runs]

WHY THIS EXISTS. The converter writes frame corrections, chain verdicts,
standoff and per-band telemetry to a JSONL, and until now nothing in the repo
read any of it -- every analysis was hand-written, more than fifty times in a
single day, each one re-deciding which field to trust. That is how a wrong field
gets read twice.

FOUR THINGS IT REFUSES TO GET WRONG, each because it was got wrong before:

1. **`shipped`, never `final`.** On a rollback, `final` is the measurement that
   was REJECTED. Reading it once turned 101 actually-shipped exposed verts into
   174, and made a run with ZERO regressions read as "20 shapes ended worse".

2. **Errors are reported FIRST, not buried.** A `standoff_band_error` or an
   `unmeasurable` chain outcome means a shape lost its measurement entirely. A
   summary that leads with clean averages hides exactly the shapes that were
   never measured -- and a converter that measures nothing reports no defects.

3. **First-person viewmodels are excluded from standoff, and the count is
   printed.** They legitimately sit far off the body; including them put a 9.78u
   outlier at the top of a list and made 34.8% of shapes look over-inflated.
   Silently filtering is as bad as not filtering, so the number dropped is
   always stated.

4. **No invented ceilings.** Only the bust band has an anchor calibrated against
   an armour confirmed correct in game. The other bands print PERCENTILES and no
   verdict: standoff differs by band, so reusing the bust ceiling upward would
   manufacture failures. Percentiles are what a future ceiling gets calibrated
   from.

The sink APPENDS across runs. By default only records carrying `path` (written
by 1.2.2 and later) are summarised, so a stale earlier run cannot be mixed in;
`--all-runs` disables that.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

FIRST_PERSON = re.compile(r"1stperson|1stp|_1st|fpf", re.I)
BANDS = ("underbust", "bust", "upperchest", "strap")


def load(path: Path):
    """Records plus a torn-line count. The sink is appended by pool workers, so
    the last line can be mid-write; that is one lost record, not a failure."""
    rows, torn = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                torn += 1
    return rows, torn


def _pct(v, p):
    if not v:
        return float("nan")
    if len(v) < 3:
        return max(v)
    return st.quantiles(v, n=100)[p - 1]


def report(rows, torn=0, out=print) -> dict:
    kinds = Counter(r.get("kind", "standoff") for r in rows)
    out(f"{len(rows)} records ({torn} torn)")
    for k, v in kinds.most_common():
        out(f"   {k:<22} {v}")

    # 1. FAILURES FIRST -------------------------------------------------------
    errs = [r for r in rows if str(r.get("kind", "")).endswith("_error")
            or "err" in r or "error" in r]
    chain = [r for r in rows if r.get("kind") == "chain"]
    unmeasured = [r for r in chain if r.get("outcome") == "unmeasurable"]
    out("")
    if errs or unmeasured:
        out(f"!! MEASUREMENT FAILURES: {len(errs)} recorded error(s), "
            f"{len(unmeasured)} unmeasurable shape(s)")
        out("   these shapes have NO fit data -- they are not 'clean'")
        for r in errs[:5]:
            out(f"     {r.get('path') or r.get('nif')} :: {r.get('shape')}"
                f"  {str(r.get('error') or r.get('err'))[:90]}")
        for r in unmeasured[:5]:
            out(f"     unmeasurable  {r.get('path') or r.get('nif')} "
                f":: {r.get('shape')}")
    else:
        out("no recorded measurement failures")

    # 2. FRAME ----------------------------------------------------------------
    frame = [r for r in rows if r.get("kind") == "frame"]
    out(f"\nFRAME corrections (a discarded body-space offset): {len(frame)}")
    for r in frame:
        out(f"   {r.get('path') or r.get('nif')} :: {r.get('shape')}"
            f"  offset {r.get('offset')}"
            f"  raw {r.get('raw_reach')}u vs offset {r.get('offset_reach')}u")

    # 3. CHAIN ----------------------------------------------------------------
    res = {}
    if chain:
        oc = Counter(str(r.get("outcome", "")).split(" (")[0] for r in chain)
        rb = [r for r in chain if r.get("rolled_back_to")]
        meas = [r for r in chain
                if isinstance(r.get("entry"), int) and r["entry"] >= 0]
        shipped = [r.get("shipped", r.get("final")) for r in meas]
        shipped = [s for s in shipped if isinstance(s, int) and s >= 0]
        entry = [r["entry"] for r in meas]
        worse = sum(1 for r in meas
                    if isinstance(r.get("shipped"), int) and r["shipped"] >= 0
                    and r["shipped"] > r["entry"])
        out(f"\nCHAIN over {len(chain)} armed shape(s)")
        for k, v in oc.most_common():
            out(f"   {k:<32} {v}")
        out(f"   rollbacks: {len(rb)}")
        if entry and shipped:
            out(f"   exposed verts  entry {sum(entry)} -> SHIPPED {sum(shipped)}"
                f"  ({sum(shipped) - sum(entry):+d})")
            out(f"   shapes finishing at zero: "
                f"{sum(1 for s in shipped if s == 0)}/{len(shipped)}")
        out(f"   shapes that SHIPPED worse than entry: {worse}")
        res["chain_worse"] = worse

    # 4. STANDOFF BANDS -------------------------------------------------------
    band = [r for r in rows if r.get("kind") == "standoff_band"]
    if band:
        keep = [r for r in band
                if not FIRST_PERSON.search(str(r.get("path") or r.get("nif")))]
        out(f"\nSTANDOFF BY BAND  ({len(band) - len(keep)} first-person "
            f"record(s) excluded)")
        out(f"{'band':<12}{'shapes':>8}{'median':>9}{'p75':>8}{'p90':>8}"
            f"{'p95':>8}{'max':>8}")
        by = defaultdict(list)
        for r in keep:
            if isinstance(r.get("median"), (int, float)):
                by[r["band"]].append(r["median"])
        for name in BANDS:
            v = by.get(name)
            if not v:
                continue
            out(f"{name:<12}{len(v):>8}{st.median(v):>9.2f}{_pct(v, 75):>8.2f}"
                f"{_pct(v, 90):>8.2f}{_pct(v, 95):>8.2f}{max(v):>8.2f}")
        out("   no verdict on any band but `bust`: only it has an anchor")
        out("   calibrated against an armour confirmed correct in game.")
        res["bands"] = {k: len(v) for k, v in by.items()}

    # 5. LEGACY BUST RECORD (the calibrated one) ------------------------------
    legacy = [r for r in rows if "kind" not in r and "median" in r]
    if legacy:
        keep = [r for r in legacy
                if not FIRST_PERSON.search(str(r.get("path") or r.get("nif")))]
        over = [r for r in keep if r.get("over")]
        out(f"\nCALIBRATED BUST RECORD: {len(keep)} shape(s), "
            f"{len(over)} over the ceiling")
        for r in sorted(over, key=lambda x: -x["median"])[:8]:
            out(f"   med {r['median']:5.2f}  p90 {r['p90']:5.2f}  "
                f"{str(r.get('shape'))[:20]:<22}"
                f"{str(r.get('path') or r.get('nif'))[:48]}")
        res["over"] = len(over)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", type=Path,
                    help="output mod root, or the jsonl itself")
    ap.add_argument("--all-runs", action="store_true",
                    help="include records without `path` (pre-1.2.2 runs)")
    a = ap.parse_args(argv)
    p = a.target
    if p.is_dir():
        p = p / "standoff_audit.jsonl"
    if not p.is_file():
        print(f"no sink at {p}")
        return 2
    rows, torn = load(p)
    if not rows:
        print("sink is empty -- the run recorded NOTHING, which is not the "
              "same as a clean run")
        return 3
    if not a.all_runs:
        cur = [r for r in rows if "path" in r]
        if cur and len(cur) != len(rows):
            print(f"(summarising the {len(cur)} record(s) from the current "
                  f"run; {len(rows) - len(cur)} older ones skipped "
                  f"-- use --all-runs to include them)\n")
            rows = cur
    report(rows, torn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
