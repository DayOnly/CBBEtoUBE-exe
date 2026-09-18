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

"""Mutation gate: prove that the guards can fail. #mutation-gate

    python scripts/mutation_gate.py run [--only ID ...] [--json OUT] [--keep]
    python scripts/mutation_gate.py list

Every guard in this repository was proven by hand with a throwaway driver, and
those hand-runs are what found the guards that could not fail: two on
2026-09-15 (E-01) and one in the 1.4.1 review. This is that driver, tracked,
seeded with the pairs that were measured (scripts/mutation_pairs.py), and able
to refuse. Each pair mutates one guarded thing and names the tests that must
go red:

  * it runs in a detached git WORKTREE it creates and removes itself, never in
    the checkout, and it refuses a target that is the checkout or inside it;
  * an anchor that matches anything but its declared count reads NOT_APPLIED,
    never "still green" -- the population floor per pair, because a test that
    stays green on a mutation that was never applied is not a pass;
  * a pair whose expected failures stay green reads MISSED, and so does one
    where something else fails instead; NOT_APPLIED and MISSED fail the gate;
  * a pair that needs what this machine lacks (a display) reads NOT_JUDGED:
    reported, counted, never mistaken for caught;
  * the worktree is restored with `git checkout` after every pair and must be
    clean before the next; every targeted test file must be green before the
    first pair and after the last, or nothing is judged.

Exit codes: 0 every judged pair CAUGHT; 1 a pair MISSED or NOT_APPLIED, or a
control run failed; 2 the gate could not run (no git, no worktree, the target
is the checkout). Run it before tagging (docs/RELEASING.md) and from the
mutation-gate workflow on demand; it is not a per-push job. MEASURED on the
release machine, 2026-09-16: 40 pairs in 879 s, of which the 21-file baseline (449 tests) and its repeat after the last pair took 79 s and 74 s."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CAUGHT, MISSED, NOT_APPLIED, NOT_JUDGED = "CAUGHT", "MISSED", "NOT_APPLIED", "NOT_JUDGED"
_FAIL_LINE = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - .*)?$", re.M)
_SUMMARY = re.compile(r"^(?:=+ )?(\d+ (?:passed|failed|error).*?) in [\d.]+s", re.M)


class GateError(RuntimeError):
    """The gate could not run at all (exit 2). Never a verdict."""


@dataclass(frozen=True)
class Pair:
    """One mutation and the tests that must catch it.

    `edits`: ((file, anchor, replacement, count), ...). `anchor` is exact text
    that must occur `count` times in the file, or None to CREATE the file with
    `replacement` (it must not exist). `tests`: the files or folders to run
    (a folder, when a pair creates a test file). `expect`: the
    test ids that must be among the failures -- the function name, with its
    [param] where the test is parametrized. `needs`: what the machine must
    have for the pair to be judged ("display")."""
    id: str
    why: str
    edits: tuple
    tests: tuple
    expect: tuple
    needs: tuple = ()


# ------------------------------------------------------------------ the machine

def _has_display() -> bool:
    """The same question tests/test_gui_smoke.py asks before a GUI test."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


_NEEDS = {"display": _has_display}


def unmet_needs(pair: Pair) -> list:
    out = []
    for need in pair.needs:
        probe = _NEEDS.get(need)
        if probe is None:
            raise GateError(f"{pair.id} needs {need!r}, which this gate does not know how to check")
        if not probe():
            out.append(need)
    return out


# ---------------------------------------------------------------------- git

def _git(cwd, *args, ok=(0,)) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise GateError("git is not installed") from e
    if r.returncode not in ok:
        raise GateError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r


def make_worktree(repo: Path, where) -> Path:
    """A detached worktree of HEAD at `where` (a fresh temp folder by default).
    Refuses the checkout itself, anything inside it, and a folder that exists."""
    top = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if where is None:
        where = Path(tempfile.mkdtemp(prefix="mutation-gate-")) / "tree"
    where = Path(where).resolve()
    if where == top or top in where.parents:
        raise GateError(f"refusing to run in the checkout: {where} is {top} or inside it")
    if where.exists():
        raise GateError(f"refusing to reuse a folder that exists: {where}")
    _git(repo, "worktree", "add", "--detach", str(where), "HEAD")
    return where


def drop_worktree(repo: Path, where: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(where), ok=(0, 128))
    _git(repo, "worktree", "prune", ok=(0, 128))
    shutil.rmtree(where, ignore_errors=True)
    # The temp folder mkdtemp made around the tree: ours by prefix, and only
    # when nothing else is in it. MEASURED after the first day: 33 empty ones.
    parent = Path(where).parent
    if parent.name.startswith("mutation-gate-") and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


# -------------------------------------------------------------------- a pair

def check_anchors(root, pair: Pair) -> "str | None":
    """The NOT_APPLIED reason, or None when every edit applies exactly."""
    for file, anchor, _replacement, count in pair.edits:
        p = Path(root) / file
        if anchor is None:
            if p.exists():
                return f"{file}: exists, so it cannot be created"
            continue
        if not p.is_file():
            return f"{file}: no such file"
        n = p.read_bytes().decode("utf-8", "replace").count(anchor)
        if n != count:
            head = anchor.strip().splitlines()[0][:60] if anchor.strip() else anchor[:60]
            return f"{file}: anchor matched {n} time(s), declared {count}: {head!r}"
    return None


def apply_pair(root, pair: Pair) -> None:
    """Apply every edit; bytes in, bytes out, so line endings are untouched."""
    for file, anchor, replacement, _count in pair.edits:
        p = Path(root) / file
        if anchor is None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(replacement.encode("utf-8"))
            continue
        text = p.read_bytes().decode("utf-8")
        p.write_bytes(text.replace(anchor, replacement).encode("utf-8"))


def restore(root, pair: Pair) -> None:
    """Undo the pair with git, then refuse to go on unless the worktree is clean."""
    tracked = sorted({f for f, a, _r, _c in pair.edits if a is not None})
    created = sorted({f for f, a, _r, _c in pair.edits if a is None})
    if tracked:
        _git(root, "checkout", "--", *tracked)
    for f in created:
        try:
            (Path(root) / f).unlink()
        except FileNotFoundError:
            pass
    dirty = _git(root, "status", "--porcelain").stdout.strip()
    if dirty:
        raise GateError(f"the worktree is not clean after restoring {pair.id}: {dirty[:300]}")


def run_tests(root, files, *, timeout=1800) -> dict:
    """pytest over `files` in `root`: no bytecode written, no cache, every
    failure and skip named. `failing` holds the test ids after the `::`."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.setdefault("PYTHONHASHSEED", "1")
    t0 = time.perf_counter()
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "-rfEs", *files], cwd=str(root), capture_output=True,
                           text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": 99, "seconds": round(time.perf_counter() - t0, 1),
                "summary": f"TIMEOUT after {timeout} s (a hang, not a verdict)", "failing": []}
    failing = sorted({m.group(1).split("::")[-1].strip() for m in _FAIL_LINE.finditer(r.stdout)})
    summaries = _SUMMARY.findall(r.stdout)
    return {"rc": r.returncode, "seconds": round(time.perf_counter() - t0, 1),
            "summary": summaries[-1] if summaries else r.stdout[-300:].strip(),
            "failing": failing}


# ------------------------------------------------------------------- the gate

def _seeded_pairs() -> list:
    from scripts.mutation_pairs import PAIRS
    return list(PAIRS)


def _row_line(row: dict) -> str:
    line = f"{row['status']:<11} {row['id']:<7} {row.get('seconds', 0.0):>6.1f}s  {row['why']}"
    if row["status"] == NOT_APPLIED:
        line += f"\n            {row['reason']}"
    elif row["status"] == NOT_JUDGED:
        line += f"\n            needs {', '.join(row['needs'])}, which this machine lacks"
    else:
        line += f"\n            failed: {row['failing']}"
        if row.get("missing"):
            line += f"\n            expected and NOT among the failures: {row['missing']}"
    return line


def run_gate(repo=REPO, pairs=None, *, only=None, worktree_dir=None, keep=False,
             log=print) -> dict:
    """Apply every pair in a fresh worktree and judge each. Returns the report;
    raises GateError when nothing could be judged at all."""
    repo = Path(repo)
    pairs = list(_seeded_pairs() if pairs is None else pairs)
    if only:
        wanted = set(only)
        pairs = [p for p in pairs if p.id in wanted]
        if not pairs:
            raise GateError(f"no pair has an id in {sorted(wanted)}")
    ids = [p.id for p in pairs]
    if len(set(ids)) != len(ids):
        raise GateError("pair ids are not unique: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
    if not pairs:
        raise GateError("no pairs to run -- a gate over nothing proves nothing")
    tree = make_worktree(repo, worktree_dir)
    report: dict = {"worktree": str(tree), "pairs": [], "verdict": None}
    t_all = time.perf_counter()
    try:
        files = sorted({f for p in pairs for f in p.tests})
        base = run_tests(tree, files)
        report["baseline"] = base
        log(f"baseline: rc={base['rc']} {base['summary']} ({base['seconds']} s, {len(files)} file(s))")
        if base["rc"] != 0:
            report["verdict"] = "FAIL"
            report["reason"] = "the baseline is not green, so nothing can be judged"
            log("CONTROL FAILED -- " + report["reason"])
            return report
        for pair in pairs:
            row: dict = {"id": pair.id, "why": pair.why}
            lacking = unmet_needs(pair)
            reason = check_anchors(tree, pair)
            if lacking:
                row.update(status=NOT_JUDGED, needs=lacking, seconds=0.0, failing=[])
            elif reason:
                row.update(status=NOT_APPLIED, reason=reason, seconds=0.0, failing=[])
            else:
                apply_pair(tree, pair)
                try:
                    res = run_tests(tree, pair.tests)
                finally:
                    restore(tree, pair)
                missing = sorted(t for t in pair.expect if t not in res["failing"])
                caught = res["rc"] != 0 and not missing
                row.update(status=CAUGHT if caught else MISSED, seconds=res["seconds"],
                           summary=res["summary"], failing=res["failing"], missing=missing)
            report["pairs"].append(row)
            log(_row_line(row))
        after = run_tests(tree, files)
        report["control_after"] = after
        log(f"control after: rc={after['rc']} {after['summary']} ({after['seconds']} s)")
        bad = [r for r in report["pairs"] if r["status"] in (MISSED, NOT_APPLIED)]
        report["verdict"] = "PASS" if not bad and after["rc"] == 0 else "FAIL"
        return report
    finally:
        report["seconds"] = round(time.perf_counter() - t_all, 1)
        if keep:
            log(f"worktree kept at {tree}")
        else:
            drop_worktree(repo, tree)


# --------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="mutation_gate",
        description="Apply every seeded mutation in a fresh worktree; each must turn its tests red.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="apply every pair in a fresh worktree and judge each")
    r.add_argument("--only", nargs="+", metavar="ID", help="run these pair ids only")
    r.add_argument("--json", type=Path, help="write the full report here")
    r.add_argument("--keep", action="store_true", help="leave the worktree in place")
    sub.add_parser("list", help="the seeded pairs")
    args = p.parse_args(argv)
    try:
        pairs = _seeded_pairs()
        if args.cmd == "list":
            for pair in pairs:
                need = f"  (needs {', '.join(pair.needs)})" if pair.needs else ""
                print(f"{pair.id:<7} {pair.why}{need}")
                print(f"        {', '.join(pair.tests)} -> {', '.join(pair.expect)}")
            print(f"{len(pairs)} pair(s)")
            return 0
        report = run_gate(REPO, pairs, only=args.only, keep=args.keep)
    except GateError as e:
        print(f"mutation gate could not run: {e}", file=sys.stderr)
        return 2
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    counts = Counter(row["status"] for row in report["pairs"])
    print(f"VERDICT: {report['verdict']} -- {counts.get(CAUGHT, 0)} caught, "
          f"{counts.get(MISSED, 0)} missed, {counts.get(NOT_APPLIED, 0)} not applied, "
          f"{counts.get(NOT_JUDGED, 0)} not judged here; {report.get('seconds', 0)} s")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
