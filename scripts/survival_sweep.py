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

"""Run the displacement-survival trace over MANY pieces, one arm of an A/B.

    python scripts/survival_sweep.py <out_dir> [--pieces golden|<json>]
        [--env K=V]... [--limit N] [--jobs N]

One piece measured is an anecdote. A pass that looks cancelled on a chain-welded
cuirass may be doing necessary work on a rigid one, and acting on the single
piece is the failure mode this project has hit repeatedly -- so this exists to
make the population the unit of measurement rather than the piece.

PARITY. Every conversion goes through `scripts/convert_one_armor.py` as a
subprocess (#single-vs-batch-parity), which is the only harness that builds the
same work item the batch builds. Recipe flags are read AT IMPORT by the
converter, so they are passed as ENVIRONMENT to each child rather than set here.

WHAT IT REFUSES TO DO QUIETLY. A piece whose source will not resolve, a
conversion that fails, and a conversion that produced NO survival rows are three
different things and all three are reported per piece. A sweep that converted
nothing at all exits 2: an empty sink and a clean sink are indistinguishable
downstream, and reading one as the other has cost this project several sessions.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))


def _resolve_source(sub: str, stem: str):
    """LAST enabled mod providing meshes/<sub>/<stem>_1.nif -- the VFS winner."""
    from src import paths
    lay = paths.discover_layout()
    if lay.mods_root is None:
        raise SystemExit("no MO2 layout -- set CBBE2UBE_MO2_INI")
    en = paths.enabled_mods(lay)
    hit = None
    for d in sorted(lay.mods_root.iterdir()):
        if not d.is_dir() or "CBBEtoUBE" in d.name or "UBE Converter" in d.name:
            continue
        if en is not None and d.name not in en:
            continue
        if (d / "meshes" / sub.replace("/", os.sep) / f"{stem}_1.nif").is_file():
            hit = d
    return hit


def _load_pieces(spec: str):
    """[(label, subdir, stem, slot_mask), ...]"""
    if spec == "golden":
        raw = json.loads((REPO / "golden" / "pieces.json").read_text("utf-8"))
    else:
        raw = json.loads(Path(spec).read_text("utf-8"))
    return [(r[0], r[1], r[2], int(r[3])) for r in raw]


def _run_one(piece, out_root, env, sink):
    label, sub, stem, slots = piece
    src = _resolve_source(sub, stem)
    if src is None:
        return {"piece": label, "status": "no source"}
    out = out_root / label
    e = dict(os.environ)
    e.update(env)
    e["CBBE2UBE_SURVIVAL_TRACE"] = "1"
    # ONE sink for the whole sweep. `_append` stamps each record with the tail
    # of its output path, so records stay attributable without a file per piece.
    e["CBBE2UBE_STANDOFF_LOG"] = str(sink)
    t0 = time.time()
    # slots=0 means "let convert_one_armor resolve them from the plugins", which
    # is what the batch does. PASSING --slots 0x0 would be actively wrong: a
    # slots=0 run silently disables every slot-gated pass (anti-poke, slot-aware
    # inflation, reskin band) and has produced two false findings already.
    cmd = [sys.executable, str(REPO / "scripts" / "convert_one_armor.py"),
           str(src), sub, stem, str(out)]
    if slots:
        cmd += ["--slots", hex(slots)]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=str(REPO), env=e)
    dt = time.time() - t0
    if p.returncode != 0:
        return {"piece": label, "status": "FAILED", "secs": round(dt, 1),
                "err": (p.stderr or p.stdout or "")[-400:]}
    # The converter records a pass exception and CARRIES ON, so a broken pass
    # reads as a clean run unless this is checked. #shape-copy-errors
    bad = [ln for ln in (p.stdout + p.stderr).splitlines()
           if "errors during shape copy" in ln.lower()]
    return {"piece": label, "status": "ok", "secs": round(dt, 1),
            "shape_copy_errors": bad[:3], "out": str(out)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--pieces", default="golden")
    ap.add_argument("--env", action="append", default=[],
                    help="K=V passed to every child converter")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=1,
                    help="parallel converts; keep at 1 while the game is up")
    args = ap.parse_args()

    env = {}
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v
    pieces = _load_pieces(args.pieces)
    if args.limit:
        pieces = pieces[:args.limit]
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    sink = out_root / "survival.jsonl"
    if sink.exists():
        sink.unlink()      # a stale sink would be silently merged into this run

    print(f"{len(pieces)} piece(s) -> {out_root}"
          f"{'  env ' + str(env) if env else ''}")
    results = []
    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(_run_one, p, out_root, env, sink) for p in pieces]
            for f in futs:
                r = f.result()
                results.append(r)
                print(f"  {r['piece']:<22}{r['status']:<10}{r.get('secs', '')}")
    else:
        for p in pieces:
            r = _run_one(p, out_root, env, sink)
            results.append(r)
            print(f"  {r['piece']:<22}{r['status']:<10}{r.get('secs', '')}")

    # Per-piece row counts. A piece that converted but produced no survival rows
    # is NOT the same as a piece with nothing to report, and the difference is
    # invisible in an aggregate.
    counts = {}
    if sink.exists():
        for line in sink.read_text("utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "survival":
                counts[r.get("path", "?")] = counts.get(r.get("path", "?"), 0) + 1
    ok = [r for r in results if r["status"] == "ok"]
    print(f"\n{len(ok)}/{len(results)} converted, "
          f"{sum(counts.values())} survival row(s) over {len(counts)} mesh(es)")
    for r in results:
        if r["status"] != "ok":
            print(f"  !! {r['piece']}: {r['status']} {r.get('err', '')}")
        elif r.get("shape_copy_errors"):
            print(f"  !! {r['piece']}: {r['shape_copy_errors']}")
    (out_root / "sweep.json").write_text(
        json.dumps({"env": env, "results": results}, indent=1), encoding="utf-8")
    if not counts:
        print("NO survival rows -- this sweep measured NOTHING.", file=sys.stderr)
        return 2
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
