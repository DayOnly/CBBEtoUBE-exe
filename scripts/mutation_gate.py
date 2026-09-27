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

    python scripts/mutation_gate.py run [--only ID ...] [--json OUT] [--keep] [--jobs N]
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
  * a mutated .py file that no longer compiles reads INVALID and is never
    run: a SyntaxError fails every test that imports the file, the named ones
    among them, so such a row would read CAUGHT whatever the guard does;
  * a pair whose expected failures stay green reads MISSED, and so does one
    where something else fails instead; NOT_APPLIED, INVALID and MISSED fail
    the gate;
  * a pair that needs what this machine lacks (a display) reads NOT_JUDGED:
    reported, counted, never mistaken for caught;
  * the worktree is restored with `git checkout` after every pair and must be
    clean before the next; every targeted test file must be green before the
    first pair and after the last, or nothing is judged.

Exit codes: 0 every judged pair CAUGHT; 1 a pair MISSED, NOT_APPLIED or
INVALID, or a control run failed; 2 the gate could not run (no git, no worktree, the target
is the checkout). Run it before tagging (docs/RELEASING.md) and from the
mutation-gate workflow on demand; it is not a per-push job. MEASURED on the
release machine, 2026-09-16: 40 pairs in 879 s, of which the 21-file baseline (449 tests) and its repeat after the last pair took 79 s and 74 s.

`--jobs N` (default 1, the run above unchanged) splits the pairs into N
disjoint shards -- pair i to shard i mod N, in seed order -- and judges each in
its OWN fresh worktree, in a child process of its own, all at once. Each shard
keeps its own baseline and control-after: those controls say "THIS worktree
was green before its first pair and after its last", and a control run in some
other worktree cannot vouch for that. The shards' rows come back as one report
in the usual order, every selected pair exactly once, and one verdict: FAIL
when any shard crashed, could not run or failed a control (named), when a pair
was judged by no shard or by two, or when any pair was MISSED, NOT_APPLIED or
INVALID. Every worktree is removed on success, failure and Ctrl+C (the parent kills the
shards' process trees first). Each shard is a pytest process of its own, with
the ~1.5 GB BLAS arena every converter test process commits: N shards commit
about N x 1.5 GB at once. An interrupted run exits 2, with no verdict."""
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
INVALID = "INVALID"
FAILING = (MISSED, NOT_APPLIED, INVALID)   # the statuses that fail the gate
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


def compile_problem(root, pair: Pair) -> "str | None":
    """The INVALID reason, or None when every .py file the pair edits or
    creates still compiles once all of its edits are made -- the text
    apply_pair would write, built in memory. A mutation must change what the
    code does, not stop it loading: LOW-f's edit commented out a line's
    closing parentheses, every test that loads the file failed on the
    SyntaxError, and the row read CAUGHT with its guard untested (2026-09-26).
    Call it after check_anchors has passed."""
    import warnings
    texts: dict = {}
    for file, anchor, replacement, _count in pair.edits:
        if anchor is None:
            texts[file] = replacement
            continue
        if file not in texts:
            texts[file] = (Path(root) / file).read_bytes().decode("utf-8")
        texts[file] = texts[file].replace(anchor, replacement)
    for file, text in texts.items():
        if not file.endswith(".py"):
            continue
        try:
            with warnings.catch_warnings():      # a SyntaxWarning is not a failure
                warnings.simplefilter("ignore")
                compile(text, file, "exec", dont_inherit=True)
        except SyntaxError as e:
            return f"{file}: the mutation does not compile: {e.msg} (line {e.lineno})"
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
    out = {"rc": r.returncode, "seconds": round(time.perf_counter() - t0, 1),
           "summary": summaries[-1] if summaries else r.stdout[-300:].strip(),
           "failing": failing}
    if r.returncode != 0:
        # Keep WHY: a red control whose report holds only a test name cannot be
        # diagnosed once the shard's worktree is gone (2026-09-27).
        out["tail"] = (r.stdout + r.stderr)[-6000:]
    return out


# ------------------------------------------------------------------- the gate

def _seeded_pairs() -> list:
    from scripts.mutation_pairs import PAIRS
    return list(PAIRS)


def _row_line(row: dict) -> str:
    line = f"{row['status']:<11} {row['id']:<7} {row.get('seconds', 0.0):>6.1f}s  {row['why']}"
    if row["status"] in (NOT_APPLIED, INVALID):
        line += f"\n            {row['reason']}"
    elif row["status"] == NOT_JUDGED:
        line += f"\n            needs {', '.join(row['needs'])}, which this machine lacks"
    else:
        line += f"\n            failed: {row['failing']}"
        if row.get("missing"):
            line += f"\n            expected and NOT among the failures: {row['missing']}"
    return line


def _select(pairs, only) -> list:
    """The pairs to run, in seed order; refuses nothing, duplicates, unknown ids."""
    pairs = list(pairs)
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
    return pairs


def run_gate(repo=REPO, pairs=None, *, only=None, worktree_dir=None, keep=False,
             log=print) -> dict:
    """Apply every pair in a fresh worktree and judge each. Returns the report;
    raises GateError when nothing could be judged at all."""
    repo = Path(repo)
    pairs = _select(_seeded_pairs() if pairs is None else pairs, only)
    tree = make_worktree(repo, worktree_dir)
    report: dict = {"worktree": str(tree), "pairs": [], "verdict": None}
    t_all = time.perf_counter()
    try:
        judge(tree, pairs, report, log)
        return report
    finally:
        report["seconds"] = round(time.perf_counter() - t_all, 1)
        if keep:
            log(f"worktree kept at {tree}")
        else:
            drop_worktree(repo, tree)


def judge(tree, pairs, report: dict, log=print) -> None:
    """The controls and every pair, in `tree`, into `report` (its "pairs" list,
    "baseline", "control_after", "verdict" and, on a red baseline, "reason")."""
    files = sorted({f for p in pairs for f in p.tests})
    base = run_tests(tree, files)
    report["baseline"] = base
    log(f"baseline: rc={base['rc']} {base['summary']} ({base['seconds']} s, {len(files)} file(s))")
    if base["rc"] != 0:
        report["verdict"] = "FAIL"
        report["reason"] = "the baseline is not green, so nothing can be judged"
        log("CONTROL FAILED -- " + report["reason"])
        return
    for pair in pairs:
        row: dict = {"id": pair.id, "why": pair.why}
        lacking = unmet_needs(pair)
        reason = check_anchors(tree, pair)
        broken = None if lacking or reason else compile_problem(tree, pair)
        if lacking:
            row.update(status=NOT_JUDGED, needs=lacking, seconds=0.0, failing=[])
        elif reason:
            row.update(status=NOT_APPLIED, reason=reason, seconds=0.0, failing=[])
        elif broken:
            row.update(status=INVALID, reason=broken, seconds=0.0, failing=[])
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
    bad = [r for r in report["pairs"] if r["status"] in FAILING]
    report["verdict"] = "PASS" if not bad and after["rc"] == 0 else "FAIL"


# --------------------------------------------------------------- --jobs N

def shard_pairs(pairs, jobs: int) -> list:
    """Pair i to shard i mod `jobs`, in seed order: deterministic, disjoint,
    complete. The pairs carry no recorded durations, and the seed lists each
    guard's pairs side by side (one test file, one cost), so round-robin deals
    every costly family across all the shards. MEASURED 2026-09-24: 674 pairs in
    6 round-robin shards finished in 945-1087 s each."""
    if jobs < 1:
        raise GateError(f"--jobs must be 1 or more, not {jobs}")
    pairs = list(pairs)
    jobs = min(jobs, len(pairs))
    shards = [pairs[k::jobs] for k in range(jobs)]
    problems = completeness([p.id for p in pairs], [[p.id for p in s] for s in shards])
    if problems:
        raise GateError("the shards do not hold every pair once: " + "; ".join(problems))
    return shards


def completeness(ids, judged) -> list:
    """What is wrong with `judged` (one id list per shard) as a partition of
    `ids`: a pair no shard holds, one two shards hold, one nobody asked for.
    Empty when every id is there exactly once."""
    seen = Counter(i for part in judged for i in part)
    wanted = set(ids)
    out = []
    none = [i for i in ids if seen[i] == 0]
    twice = [i for i in ids if seen[i] > 1]
    stray = sorted(i for i in seen if i not in wanted)
    for label, got in (("judged by no shard", none), ("judged more than once", twice),
                       ("judged but never selected", stray)):
        if got:
            more = f" (+{len(got) - 12} more)" if len(got) > 12 else ""
            out.append(f"{len(got)} pair(s) {label}: {', '.join(got[:12])}{more}")
    return out


def _pair_to_json(pair: Pair) -> dict:
    return {"id": pair.id, "why": pair.why, "edits": [list(e) for e in pair.edits],
            "tests": list(pair.tests), "expect": list(pair.expect), "needs": list(pair.needs)}


def _pair_from_json(d: dict) -> Pair:
    return Pair(d["id"], d["why"], tuple(tuple(e) for e in d["edits"]), tuple(d["tests"]),
                tuple(d["expect"]), tuple(d["needs"]))


def _launch_shard(k: int, shard, tree: Path, run_dir: Path):
    """One child process judging `shard` in `tree`, its output in run_dir.
    It gets a process group of its own, so Ctrl+C reaches the parent only and
    the parent decides: it kills the whole tree and removes the worktree."""
    spec = run_dir / f"shard{k}.pairs.json"
    spec.write_text(json.dumps([_pair_to_json(p) for p in shard]), encoding="utf-8")
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "shard", "--tree", str(tree),
           "--pairs", str(spec), "--json", str(run_dir / f"shard{k}.json")]
    # Unbuffered, so the shard's log shows each row as it lands (a full run
    # is long, and a redirected stdout would otherwise hold it all to the end).
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with open(run_dir / f"shard{k}.log", "wb") as fh:
        return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, **own_group())


def own_group() -> dict:
    """Popen keywords for a process group of its own, which _kill_tree can stop whole."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(proc) -> None:
    """Stop a shard and everything it started (its pytest and whatever that runs)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def _collect(k: int, rc: int, run_dir: Path) -> dict:
    """A finished shard: its report, or why there is none (with its log's tail)."""
    out = {"shard": k, "rc": rc, "report": None, "error": None}
    try:
        out["report"] = json.loads((run_dir / f"shard{k}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        out["error"] = f"exited {rc} with no readable report ({type(e).__name__})"
    if out["error"] is None and rc not in (0, 1):
        out["error"] = f"exited {rc}"
    if out["error"]:
        try:
            tail = (run_dir / f"shard{k}.log").read_bytes().decode("utf-8", "replace")
        except OSError:
            tail = ""
        tail = "\n".join(tail.strip().splitlines()[-15:])
        if tail:
            out["error"] += "; its log ends:\n" + tail
    return out


def aggregate(pairs, shard_ids, results) -> dict:
    """One report from the shards': every row once, in `pairs` order; one verdict.

    PASS needs every shard to have run and passed its own controls with an exit
    code that agrees with its verdict, every pair judged by exactly one shard,
    and no pair MISSED, NOT_APPLIED or INVALID. NOT_JUDGED passes, as it does in a
    single run: it is reported, never counted as caught."""
    order = [p.id for p in pairs]
    by_k = {r["shard"]: r for r in results}
    reasons: list = []
    shards: list = []
    rows: dict = {}
    judged: list = []
    for k, ids in enumerate(shard_ids):
        res = by_k.get(k) or {"shard": k, "rc": None, "report": None,
                              "error": "never reported back"}
        rep = res.get("report") or {}
        entry = {"shard": k, "ids": list(ids), "rc": res.get("rc"),
                 "verdict": rep.get("verdict"), "worktree": rep.get("worktree"),
                 "seconds": rep.get("seconds"), "baseline": rep.get("baseline"),
                 "control_after": rep.get("control_after")}
        if res.get("error"):
            entry["error"] = res["error"]
            reasons.append(f"shard {k} crashed or could not run: {res['error']}")
        else:
            if rep.get("reason"):
                reasons.append(f"shard {k}: {rep['reason']}")
            elif (rep.get("control_after") or {}).get("rc", 0) != 0:
                reasons.append(f"shard {k}: the control after its last pair is not green")
            if (res.get("rc") == 0) != (rep.get("verdict") == "PASS"):
                reasons.append(f"shard {k}: exit code {res.get('rc')} disagrees with "
                               f"its verdict {rep.get('verdict')}")
        mine = []
        for row in rep.get("pairs") or []:
            mine.append(row["id"])
            rows.setdefault(row["id"], row)
        judged.append(mine)
        shards.append(entry)
    reasons += completeness(order, judged)
    combined = [rows[i] for i in order if i in rows]
    bad = [r for r in combined if r["status"] in FAILING]
    report = {"jobs": len(shard_ids), "shards": shards, "pairs": combined,
              "verdict": "PASS" if not reasons and not bad else "FAIL"}
    if reasons:
        report["reasons"] = reasons
    return report


def _drop_all(repo: Path, trees, log) -> list:
    """Remove every shard worktree and return the ones that could not be
    removed. A just-killed process can hold a file for a moment on Windows,
    so a tree that is still there is tried again."""
    left: list = []
    for tree in trees:
        for attempt in range(5):
            try:
                drop_worktree(repo, tree)
            except GateError as e:
                if attempt == 4:
                    log(f"could not remove {tree}: {e}")
            if not Path(tree).exists():
                break
            time.sleep(1.0)
        else:
            log(f"LEFT BEHIND: {tree} -- remove it with git worktree remove --force")
            left.append(str(tree))
    return left


def run_gate_jobs(repo=REPO, pairs=None, *, jobs: int, only=None, keep=False, log=print,
                  launch=None, poll=0.5) -> dict:
    """The gate over `jobs` shards at once, each in its own fresh worktree and
    child process; see the module docstring. `launch(k, shard, tree, run_dir)`
    starts one shard and returns its process (tests pass a stand-in)."""
    repo = Path(repo)
    pairs = _select(_seeded_pairs() if pairs is None else pairs, only)
    shards = shard_pairs(pairs, jobs)
    launch = launch or _launch_shard
    run_dir = Path(tempfile.mkdtemp(prefix="mutation-gate-jobs-"))
    trees: list = []
    procs: dict = {}
    results: list = []
    left_behind: list = []
    t_all = time.perf_counter()
    try:
        for k in range(len(shards)):
            trees.append(make_worktree(repo, run_dir / f"shard{k}"))
        log(f"{len(shards)} shard(s); each writes its rows as they land to {run_dir}"
            f"{os.sep}shard<k>.log")
        for k, shard in enumerate(shards):
            procs[k] = launch(k, shard, trees[k], run_dir)
            log(f"shard {k}: {len(shard)} pair(s) in {trees[k]}")
        left = dict(procs)
        while left:
            for k, proc in list(left.items()):
                rc = proc.poll()
                if rc is None:
                    continue
                del left[k]
                res = _collect(k, rc, run_dir)
                results.append(res)
                rep = res["report"] or {}
                log(f"shard {k} finished: rc={rc} {rep.get('verdict') or 'NO REPORT'} "
                    f"({rep.get('seconds', '?')} s)")
            if left:
                time.sleep(poll)
    except BaseException:
        for proc in procs.values():
            _kill_tree(proc)
        raise
    finally:
        if keep:
            log(f"worktrees and shard logs kept in {run_dir}")
        else:
            left_behind = _drop_all(repo, trees, log)
            if not left_behind:
                shutil.rmtree(run_dir, ignore_errors=True)
    report = aggregate(pairs, [[p.id for p in s] for s in shards], results)
    report["seconds"] = round(time.perf_counter() - t_all, 1)
    # A worktree the run could not remove is a run that did not clean up, as
    # in --jobs 1 (its cleanup failure exits 2): `main` exits 2 on it, and the
    # JSON names the trees (the run folder is kept so they stay findable).
    report["left_behind"] = left_behind
    for s in report["shards"]:
        b, a = s.get("baseline") or {}, s.get("control_after") or {}
        log(f"shard {s['shard']}: {len(s['ids'])} pair(s), rc={s['rc']}; "
            f"baseline rc={b.get('rc')} {b.get('summary')} ({b.get('seconds')} s); "
            f"control after rc={a.get('rc')} {a.get('summary')} ({a.get('seconds')} s)")
    for row in report["pairs"]:
        log(_row_line(row))
    for reason in report.get("reasons", []):
        log("FAILED -- " + reason)
    return report


def _check_shard_tree(tree: Path) -> None:
    """A shard mutates files, so it runs only in what `run --jobs` made: a
    `shard<k>` folder inside a mutation-gate-jobs-* run folder, a linked
    worktree, detached, clean, and not this checkout."""
    tree = Path(tree).resolve()
    top = Path(_git(REPO, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if tree == top or top in tree.parents:
        raise GateError(f"refusing to run in the checkout: {tree}")
    git_dir, common = _git(tree, "rev-parse", "--git-dir", "--git-common-dir").stdout.splitlines()[:2]
    if (tree / git_dir).resolve() == (tree / common).resolve():
        raise GateError(f"{tree} is a main checkout, not a worktree the gate made")
    if _git(tree, "symbolic-ref", "-q", "HEAD", ok=(0, 1)).returncode == 0:
        raise GateError(f"{tree} has a branch checked out; the gate's worktrees are detached")
    if _git(tree, "status", "--porcelain").stdout.strip():
        raise GateError(f"{tree} is not clean")
    if not (tree.name.startswith("shard")
            and tree.parent.name.startswith("mutation-gate-jobs-")):
        raise GateError(f"{tree} is not a shard worktree `run --jobs` made "
                        "(<temp>/mutation-gate-jobs-*/shard<k>)")


def _run_shard(tree: Path, spec: Path, out: Path) -> int:
    """The child side of --jobs: judge the pairs in `spec` in `tree`, write `out`."""
    _check_shard_tree(tree)
    pairs = [_pair_from_json(d) for d in json.loads(spec.read_text(encoding="utf-8"))]
    report: dict = {"worktree": str(tree), "pairs": [], "verdict": None}
    t0 = time.perf_counter()
    try:
        judge(tree, pairs, report)
    finally:
        report["seconds"] = round(time.perf_counter() - t0, 1)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0 if report["verdict"] == "PASS" else 1


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
    r.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="judge N shards at once, each in its own worktree (default 1); "
                        "every shard commits about 1.5 GB")
    sub.add_parser("list", help="the seeded pairs")
    s = sub.add_parser("shard", help="(run --jobs starts these) judge a shard in its worktree")
    s.add_argument("--tree", type=Path, required=True)
    s.add_argument("--pairs", type=Path, required=True)
    s.add_argument("--json", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "shard":
            return _run_shard(args.tree, args.pairs, args.json)
        pairs = _seeded_pairs()
        if args.cmd == "list":
            for pair in pairs:
                need = f"  (needs {', '.join(pair.needs)})" if pair.needs else ""
                print(f"{pair.id:<7} {pair.why}{need}")
                print(f"        {', '.join(pair.tests)} -> {', '.join(pair.expect)}")
            print(f"{len(pairs)} pair(s)")
            return 0
        if args.jobs == 1:
            report = run_gate(REPO, pairs, only=args.only, keep=args.keep)
        else:
            report = run_gate_jobs(REPO, pairs, jobs=args.jobs, only=args.only, keep=args.keep,
                                   log=lambda line: print(line, flush=True))
    except GateError as e:
        print(f"mutation gate could not run: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if args.cmd != "run" or args.jobs == 1:
            raise
        print("mutation gate interrupted: every shard stopped, no verdict", file=sys.stderr)
        return 2
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    counts = Counter(row["status"] for row in report["pairs"])
    shards = f" over {report['jobs']} shards" if "jobs" in report else ""
    print(f"VERDICT: {report['verdict']} -- {counts.get(CAUGHT, 0)} caught, "
          f"{counts.get(MISSED, 0)} missed, {counts.get(NOT_APPLIED, 0)} not applied, "
          f"{counts.get(INVALID, 0)} invalid, "
          f"{counts.get(NOT_JUDGED, 0)} not judged here; {report.get('seconds', 0)} s{shards}")
    if report.get("left_behind"):
        print(f"mutation gate could not clean up: {len(report['left_behind'])} worktree(s) "
              f"left behind: {', '.join(report['left_behind'])}", file=sys.stderr)
        return 2
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
