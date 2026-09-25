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

"""#golden-jobs -- `golden_output.py capture|check --jobs N`.

The golden harness converted its pieces one after another: 4-6 minutes per
capture or check, and a lane verdict needs four of them. `--jobs N` converts
them in N spawned worker PROCESSES. What must not change is the answer: the
baseline files and the verdict are the sequential run's, pieces are recorded
and compared in piece order, and a piece lost in a worker fails BY NAME rather
than vanishing from the run (a capture that quietly lacks a piece reads as a
SKIP, and every later check passes without looking at that class).

The pool tests run in a child interpreter with PYTHONHASHSEED pinned, the way
the tool itself runs: the workers must hash exactly as their parent does, and
the parent here is that child, not this test process. The unit of work is
replaced by `_fake_measure` below -- the real one converts a mesh from the
user's mod list.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from scripts import golden_output as go

_REPO = Path(__file__).resolve().parent.parent
_PLANT = "GOLDEN_JOBS_TEST_PLANT"


def _piece(label):
    return (label, "armor/test/f", label, go.SLOT_BODY, "test piece")


def _fake_measure(piece, work_root):
    """Stands in for `golden_output._measure` inside a worker process. The
    first piece is the SLOWEST, so completion order is not piece order."""
    label = piece[0]
    plant = os.environ.get(_PLANT, "").split(",")
    if label.endswith("-first"):
        time.sleep(1.5)
    if f"raise:{label}" in plant:
        raise RuntimeError(f"planted failure in {label}")
    if f"die:{label}" in plant:
        os._exit(3)
    if label.startswith("missing"):
        return {"src": None, "fp": None}
    k = float(sum(map(ord, label)))
    fp = {"body": {"verts": np.arange(12, dtype=np.float32).reshape(4, 3) + k,
                   "bones": ["NPC Pelvis [Pelv]", "NPC Spine [Spn0]"],
                   "wsum": np.array([1.5, k])},
          "trim": {"verts": np.ones((2, 3), np.float32) * k,
                   "bones": ["NPC Pelvis [Pelv]"], "wsum": np.array([2.0])}}
    return {"src": Path(work_root) / "mods" / "SomeMod" / f"{label}_1.nif",
            "source_sig": f"sig-{label}", "fp": fp,
            "sidecars": {f"{label}.tri": "d" * 32},
            "pid": os.getpid(), "hash": hash(go._HASH_PROBE),
            "blas": os.environ.get("OPENBLAS_NUM_THREADS")}


# Runs in a child interpreter (PYTHONHASHSEED pinned). Each scenario is caught
# on its own, so one broken scenario cannot blank the others' results.
_DRIVER = r'''
import contextlib, io, json, os, sys, traceback
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import numpy as np
from scripts import golden_output as go
from tests import test_golden_jobs as t

tmp = Path(sys.argv[2])
go._measure = t._fake_measure
go.ac.default_worker_count = lambda: 8
out = {"parent_pid": os.getpid(), "parent_hash": hash(go._HASH_PROBE),
       "parent_blas": os.environ.get("OPENBLAS_NUM_THREADS")}

def run(name, fn):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = fn()
        out[name] = {"res": res, "text": buf.getvalue()}
    except BaseException:
        out[name] = {"error": traceback.format_exc(), "text": buf.getvalue()}
    # After EVERY scenario: a planted worker death that took this process
    # down with it (a pool of threads) must not erase the earlier results.
    print("@@RESULT@@" + json.dumps(out), flush=True)

def order():
    pieces = [t._piece(x) for x in ("a-first", "b", "c", "d", "e")]
    rows = list(go._measured(pieces, tmp / "order", 3))
    return [[p[0], err, (r or {}).get("pid"), (r or {}).get("hash"),
             (r or {}).get("blas")] for p, r, err in rows]

def baseline(gdir, labels, jobs):
    go.GOLDEN = tmp / gdir
    go.PIECES = [t._piece(x) for x in labels]
    return go.capture(jobs)

def read_baseline(gdir):
    g = tmp / gdir
    files = sorted(p.name for p in g.iterdir()) if g.is_dir() else []
    arrays = {}
    for f in files:
        if f.endswith(".npz"):
            z = np.load(g / f, allow_pickle=True)
            arrays[f] = [[k, repr(z[k].dtype), list(z[k].shape),
                          [str(x) for x in z[k].ravel()]] for k in z.files]
    man = (g / "manifest.json").read_text(encoding="utf-8") \
        if (g / "manifest.json").is_file() else None
    return {"files": files, "arrays": arrays, "manifest": man}

LABELS = ["a-first", "b", "missing-c", "d", "e"]

def capture_same():
    b1, b3 = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(b1):
        rc1 = baseline("seq", LABELS, 1)
    with contextlib.redirect_stdout(b3):
        rc3 = baseline("par", LABELS, 3)
    return {"rc1": rc1, "rc3": rc3, "seq": read_baseline("seq"),
            "par": read_baseline("par"),
            "text1": b1.getvalue().replace(str(tmp / "seq"), "<golden>"),
            "text3": b3.getvalue().replace(str(tmp / "par"), "<golden>")}

def check_verdicts():
    rc0 = baseline("chk", ["a-first", "bad", "c"], 1)
    rc_ok = go.check(1e-4, 3)
    os.environ[t._PLANT] = "raise:bad"
    try:
        rc_bad = go.check(1e-4, 3)
    finally:
        del os.environ[t._PLANT]
    return {"capture": rc0, "ok": rc_ok, "bad": rc_bad}

def check_worker_dies():
    rc0 = baseline("die", ["a-first", "dies"], 1)
    os.environ[t._PLANT] = "die:dies"
    try:
        return {"capture": rc0, "rc": go.check(1e-4, 2)}
    finally:
        del os.environ[t._PLANT]

def capture_fails():
    os.environ[t._PLANT] = "raise:bad"
    try:
        rc = baseline("capfail", ["a-first", "bad", "c"], 3)
    finally:
        del os.environ[t._PLANT]
    return {"rc": rc, "baseline": read_baseline("capfail")}

run("order", order)
run("capture_same", capture_same)
run("check_verdicts", check_verdicts)
run("capture_fails", capture_fails)
run("check_worker_dies", check_worker_dies)
'''


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("golden_jobs")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CBBE2UBE_") and k != _PLANT}
    env["PYTHONHASHSEED"] = "7"
    r = subprocess.run([sys.executable, "-B", "-c", _DRIVER, str(_REPO), str(tmp)],
                       cwd=str(_REPO), env=env, capture_output=True, text=True,
                       timeout=600)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    out = json.loads(lines[-1][len("@@RESULT@@"):]) if lines else {}
    out["_driver"] = f"driver rc {r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}"
    return out


def _scenario(pool, name):
    assert name in pool, f"scenario {name} never reported:\n{pool['_driver']}"
    s = pool[name]
    assert "error" not in s, f"{name} raised:\n{s['error']}\n{s['text']}"
    return s


# ------------------------------------------------------------- the pool itself

def test_results_come_back_in_piece_order_not_completion_order(pool):
    """The first piece finishes last; the rows must still be in piece order,
    because capture writes and check prints in the order it receives them."""
    rows = _scenario(pool, "order")["res"]
    assert [r[0] for r in rows] == ["a-first", "b", "c", "d", "e"]
    assert all(r[1] is None for r in rows), rows


def test_pieces_run_in_worker_processes_not_threads(pool):
    """The converter is not thread-safe and pynifly holds process state."""
    rows = _scenario(pool, "order")["res"]
    pids = {r[2] for r in rows}
    assert pool["parent_pid"] not in pids, "a piece ran in the parent process"
    assert len(pids) >= 2, f"all pieces ran in one worker: {pids}"


def test_workers_hash_and_cap_blas_like_their_parent(pool):
    rows = _scenario(pool, "order")["res"]
    assert {r[3] for r in rows} == {pool["parent_hash"]}
    assert {r[4] for r in rows} == {pool["parent_blas"]}
    assert pool["parent_blas"] == "1"


# ---------------------------------------------------------------- capture

def test_a_parallel_capture_records_what_a_sequential_one_does(pool):
    s = _scenario(pool, "capture_same")["res"]
    assert s["rc1"] == 0 and s["rc3"] == 0
    seq, par = s["seq"], s["par"]
    assert seq["files"] == par["files"]
    assert "missing-c.npz" not in seq["files"] and "a-first.npz" in seq["files"]
    assert seq["arrays"] == par["arrays"]
    # The manifest differs only in where it was written from, which it does not
    # record: byte-identical, piece order included.
    assert seq["manifest"] == par["manifest"]
    assert list(json.loads(par["manifest"])["pieces"]) == ["a-first", "b", "d", "e"]


def test_a_parallel_capture_prints_what_a_sequential_one_does(pool):
    s = _scenario(pool, "capture_same")["res"]
    assert s["text1"] == s["text3"]
    assert "SKIP missing-c" in s["text1"] and "captured e" in s["text1"]


def test_a_piece_that_fails_in_a_worker_leaves_the_baseline_unwritten(pool):
    s = _scenario(pool, "capture_fails")
    assert s["res"]["rc"] == 1
    assert "FAIL bad" in s["text"] and "planted failure in bad" in s["text"]
    assert "baseline NOT written" in s["text"]
    assert "manifest.json" not in s["res"]["baseline"]["files"]
    assert not [f for f in s["res"]["baseline"]["files"] if f.endswith(".npz")]


# ------------------------------------------------------------------ check

def test_a_parallel_check_passes_an_unchanged_tree(pool):
    s = _scenario(pool, "check_verdicts")
    assert s["res"]["capture"] == 0 and s["res"]["ok"] == 0
    assert "PASS: output identical" in s["text"]


def test_a_piece_that_fails_in_a_worker_fails_the_check_by_name(pool):
    s = _scenario(pool, "check_verdicts")
    assert s["res"]["bad"] == 1
    lines = s["text"].splitlines()
    bad = [ln for ln in lines if ln.strip().startswith("bad ")]
    assert bad and any("FAIL  worker failed" in ln and "planted failure in bad" in ln
                       for ln in bad), bad
    assert "FAIL: 1 piece(s) regressed" in s["text"]
    # Its neighbours are still judged, in piece order -- in the failing run,
    # which is the second verdict in the text.
    second = s["text"].split("PASS: output identical")[1]
    judged = [ln.split()[0] for ln in second.splitlines() if ln.startswith("  ")]
    assert judged == ["a-first", "bad", "c"], second


def test_a_worker_that_dies_fails_the_check_by_name(pool):
    s = _scenario(pool, "check_worker_dies")
    assert s["res"]["capture"] == 0 and s["res"]["rc"] == 1
    assert any(ln.strip().startswith("dies ") and "FAIL  worker failed" in ln
               for ln in s["text"].splitlines()), s["text"]


# --------------------------------------------------- in-process decisions

def test_a_worker_with_a_different_environment_refuses(tmp_path):
    same = go._env_signature()
    ran = []

    def measure(piece, work_root):
        ran.append(piece[0])
        return {"ok": True}

    assert go._worker_measure(_piece("x"), tmp_path, same, measure) == {"ok": True}
    seed = dict(same, env=dict(same["env"], PYTHONHASHSEED="999"))
    with pytest.raises(RuntimeError, match="PYTHONHASHSEED"):
        go._worker_measure(_piece("y"), tmp_path, seed, measure)
    flag = dict(same, env=dict(same["env"], CBBE2UBE_NO_SOMETHING="1"))
    with pytest.raises(RuntimeError, match="CBBE2UBE_NO_SOMETHING"):
        go._worker_measure(_piece("y"), tmp_path, flag, measure)
    hashed = dict(same, hash=same["hash"] + 1)
    with pytest.raises(RuntimeError, match="effective hash seed"):
        go._worker_measure(_piece("y"), tmp_path, hashed, measure)
    assert ran == ["x"]


def test_jobs_are_capped_by_pieces_and_by_the_batch_worker_count(monkeypatch):
    monkeypatch.setattr(go.ac, "default_worker_count", lambda: 4)
    assert go._effective_jobs(8, 3) == 3
    assert go._effective_jobs(8, 15) == 4
    assert go._effective_jobs(5, 15) == 4
    assert go._effective_jobs(3, 15) == 3
    assert go._effective_jobs(1, 15) == 1
    monkeypatch.setattr(go.ac, "default_worker_count", lambda: 0)
    assert go._effective_jobs(5, 15) == 1


def test_jobs_refuse_an_unpinned_hash_seed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(go, "GOLDEN", tmp_path / "g")
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    assert go.capture(3) == 2
    assert go.check(1e-4, 3) == 2
    assert "PYTHONHASHSEED" in capsys.readouterr().out
    assert not (tmp_path / "g").exists()
    monkeypatch.setenv("PYTHONHASHSEED", "random")
    assert go._jobs_refusal(3)
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    assert go._jobs_refusal(3) is None
    monkeypatch.delenv("PYTHONHASHSEED")
    assert go._jobs_refusal(1) is None


def test_numpy_is_imported_under_the_blas_cap():
    """The parent converts every piece at --jobs 1 and must do it under the
    same BLAS limits as a worker. It imported numpy before `src` set them."""
    code = r'''
import importlib.util, os, sys
for v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.pop(v, None)
seen = []
class Spy:
    def find_spec(self, name, path=None, target=None):
        if name == "numpy" and not seen:
            seen.append(os.environ.get("OPENBLAS_NUM_THREADS"))
        return None
sys.meta_path.insert(0, Spy())
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
spec.loader.exec_module(importlib.util.module_from_spec(spec))
print("@@" + repr(seen))
'''
    r = subprocess.run([sys.executable, "-B", "-c", code,
                        str(_REPO / "scripts" / "golden_output.py")],
                       cwd=str(_REPO), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    seen = [ln for ln in r.stdout.splitlines() if ln.startswith("@@")]
    assert seen == ["@@['1']"], (seen, r.stderr[-2000:])


def test_a_bad_jobs_value_is_a_usage_error(monkeypatch, capsys):
    for argv in (["check", "--jobs", "0"], ["capture", "--jobs", "x"],
                 ["check", "--jobs"]):
        monkeypatch.setattr(sys, "argv", ["golden_output.py", *argv])
        assert go.main() == 2
    assert "--jobs takes a whole number" in capsys.readouterr().out
