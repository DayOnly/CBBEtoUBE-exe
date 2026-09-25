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

"""#mutation-gate -- scripts/mutation_gate.py, on a throwaway repository, and
the seeded pairs against THIS tree.

The controls run in a tiny git repository built in tmp_path (one module, one
test file), so each is one deliberate pair: a mutation the tests catch, an
anchor that is absent, one that is miscounted, a replacement the tests cannot
see, a wrong expected id on a red run, and the checkout as the target. The
seeded pairs are never RUN here -- that is the gate's own job, minutes long --
but every one of their anchors must still match its declared count and every
expected test id must exist, so a refactor that moves an anchor fails the
suite rather than the next release."""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from scripts import mutation_gate as mg
from scripts.mutation_pairs import PAIRS

REPO = Path(__file__).resolve().parents[1]
_QUIET = lambda *a, **k: None  # noqa: E731


def _g(repo, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=gate", "-c", "user.email=gate",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=no-hooks", *args],
        capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


_THING = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
_TEST = ("import sys\nfrom pathlib import Path\n"
         "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
         "import thing\n\n\ndef test_f():\n    assert thing.f() == 1\n\n\n"
         "def test_g():\n    assert thing.g() == 1\n")


@pytest.fixture
def tiny(tmp_path):
    """A committed repository with one module and one test file."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "tiny"
    (repo / "tests").mkdir(parents=True)
    # Bytes, LF: text mode would write CRLF on Windows and no LF anchor would
    # ever match -- which the gate would rightly report as NOT_APPLIED.
    (repo / "thing.py").write_bytes(_THING.encode("utf-8"))
    (repo / "tests" / "test_thing.py").write_bytes(_TEST.encode("utf-8"))
    _g(repo, "init", "-q", "-b", "main")
    _g(repo, "config", "core.autocrlf", "false")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "tiny")
    return repo


def _pair(id="P1", *, anchor="def f():\n    return 1\n", repl="def f():\n    return 2\n",
          count=1, expect=("test_f",), file="thing.py", why="f returns the wrong thing",
          needs=()):
    return mg.Pair(id, why, ((file, anchor, repl, count),), ("tests/test_thing.py",),
                   expect, needs)


def _run(tiny, tmp_path, pairs):
    return mg.run_gate(tiny, pairs, worktree_dir=tmp_path / "wt", log=_QUIET)


# --- the controls -----------------------------------------------------------------

def test_a_mutation_the_tests_catch_reads_caught(tiny, tmp_path):
    rep = _run(tiny, tmp_path, [_pair()])
    assert rep["verdict"] == "PASS"
    assert rep["pairs"][0]["status"] == mg.CAUGHT and rep["pairs"][0]["failing"] == ["test_f"]
    assert rep["baseline"]["rc"] == 0 and rep["control_after"]["rc"] == 0


def test_an_absent_anchor_is_not_applied_and_fails_the_gate(tiny, tmp_path):
    """The population floor per pair: 'still green' on a mutation that was never
    applied is not a pass."""
    rep = _run(tiny, tmp_path, [_pair(anchor="def f():\n    return 9\n")])
    row = rep["pairs"][0]
    assert row["status"] == mg.NOT_APPLIED and "matched 0" in row["reason"]
    assert rep["verdict"] == "FAIL"


def test_a_miscounted_anchor_is_not_applied(tiny, tmp_path):
    """`    return 1` occurs twice (f and g); the pair declares one."""
    rep = _run(tiny, tmp_path, [_pair(anchor="    return 1\n", repl="    return 2\n")])
    row = rep["pairs"][0]
    assert row["status"] == mg.NOT_APPLIED and "matched 2" in row["reason"]
    assert rep["verdict"] == "FAIL"


def test_a_replacement_the_tests_cannot_see_reads_missed_and_fails_the_gate(tiny, tmp_path):
    rep = _run(tiny, tmp_path, [_pair(repl="def f():\n    return 1  # the same\n")])
    row = rep["pairs"][0]
    assert row["status"] == mg.MISSED and row["missing"] == ["test_f"] and row["failing"] == []
    assert rep["verdict"] == "FAIL"


def test_a_wrong_expected_id_reads_missed_even_on_a_red_run(tiny, tmp_path):
    """The mutation breaks g, but the pair claims f's test: a red run is not
    enough, the NAMED test must be the one that fails."""
    rep = _run(tiny, tmp_path, [_pair(anchor="def g():\n    return 1\n",
                                      repl="def g():\n    return 2\n", expect=("test_f",))])
    row = rep["pairs"][0]
    assert row["status"] == mg.MISSED and row["failing"] == ["test_g"] and row["missing"] == ["test_f"]
    assert rep["verdict"] == "FAIL"


def test_the_worktree_is_restored_between_pairs_and_removed_after(tiny, tmp_path):
    """Pair 2's anchor is the text pair 1 mutates: without the restore it would
    read NOT_APPLIED. Afterwards no worktree is left and the checkout is untouched."""
    rep = _run(tiny, tmp_path, [_pair(id="P1"), _pair(id="P2", why="again")])
    assert [r["status"] for r in rep["pairs"]] == [mg.CAUGHT, mg.CAUGHT], rep["pairs"]
    assert not (tmp_path / "wt").exists()
    assert len(_g(tiny, "worktree", "list").splitlines()) == 1, "a worktree was left behind"
    assert (tiny / "thing.py").read_text(encoding="utf-8") == _THING


def test_the_gate_refuses_to_run_in_the_checkout(tiny):
    with pytest.raises(mg.GateError, match="checkout"):
        mg.run_gate(tiny, [_pair()], worktree_dir=tiny, log=_QUIET)
    with pytest.raises(mg.GateError, match="checkout"):
        mg.run_gate(tiny, [_pair()], worktree_dir=tiny / "inside", log=_QUIET)
    assert (tiny / "thing.py").read_text(encoding="utf-8") == _THING


def test_a_created_file_pair_is_applied_and_cleaned_up(tiny, tmp_path):
    """Lens E's pair h: a planted test file that must not exist beforehand. The
    pair points pytest at the FOLDER, since a file that does not exist yet cannot
    be in the baseline."""
    planted = mg.Pair("P3", "a planted test",
                      (("tests/test_planted.py", None, "def test_planted():\n    assert False\n", 0),),
                      ("tests",), ("test_planted",))     # the folder: the file is not there yet
    rep = _run(tiny, tmp_path, [planted])
    assert rep["pairs"][0]["status"] == mg.CAUGHT and rep["verdict"] == "PASS"
    assert not (tiny / "tests" / "test_planted.py").exists()
    (tiny / "tests" / "test_planted.py").write_bytes(b"x = 1\n")
    _g(tiny, "add", "-A")
    _g(tiny, "commit", "-q", "-m", "the file exists now")
    rep = _run(tiny, tmp_path / "again", [planted])
    assert rep["pairs"][0]["status"] == mg.NOT_APPLIED and "exists" in rep["pairs"][0]["reason"]


def test_a_pair_needing_what_the_machine_lacks_is_not_judged(tiny, tmp_path, monkeypatch):
    monkeypatch.setitem(mg._NEEDS, "display", lambda: False)
    rep = _run(tiny, tmp_path, [_pair(needs=("display",)), _pair(id="P2", why="plain")])
    assert [r["status"] for r in rep["pairs"]] == [mg.NOT_JUDGED, mg.CAUGHT]
    assert rep["verdict"] == "PASS", "an unjudged pair is reported, not failed"
    with pytest.raises(mg.GateError, match="does not know"):
        _run(tiny, tmp_path / "x", [_pair(needs=("a-quantum-computer",))])


def test_a_red_baseline_judges_nothing(tiny, tmp_path):
    (tiny / "thing.py").write_bytes(
        _THING.replace("def f():\n    return 1", "def f():\n    return 2").encode("utf-8"))
    _g(tiny, "commit", "-qam", "break f")
    rep = _run(tiny, tmp_path, [_pair()])
    assert rep["verdict"] == "FAIL" and rep["pairs"] == [] and "baseline" in rep["reason"]


def test_the_cli_reports_and_exits_by_verdict(tiny, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mg, "REPO", tiny)
    monkeypatch.setattr(mg, "_seeded_pairs", lambda: [_pair()])
    temp_before = set(Path(tempfile.gettempdir()).glob("mutation-gate-*"))
    assert mg.main(["run", "--json", str(tmp_path / "r.json")]) == 0
    assert set(Path(tempfile.gettempdir()).glob("mutation-gate-*")) == temp_before, (
        "the gate left its temp folder behind (the worktree's wrapper, made by mkdtemp)")
    out = capsys.readouterr().out
    assert "VERDICT: PASS" in out and "1 caught" in out
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["verdict"] == "PASS"
    monkeypatch.setattr(mg, "_seeded_pairs",
                        lambda: [_pair(repl="def f():\n    return 1  # the same\n")])
    assert mg.main(["run"]) == 1
    assert "VERDICT: FAIL" in capsys.readouterr().out
    assert mg.main(["run", "--only", "nope"]) == 2
    assert mg.main(["list"]) == 0 and "P1" in capsys.readouterr().out


def test_failure_lines_are_read_whole_including_parametrized_ids():
    out = ("FAILED tests/t.py::test_x[USING.md-program files] - AssertionError: no\n"
           "ERROR tests/t.py::test_y\nFAILED tests/t.py::test_z - x - y\n")
    ids = sorted({m.group(1).split("::")[-1] for m in mg._FAIL_LINE.finditer(out)})
    assert ids == ["test_x[USING.md-program files]", "test_y", "test_z"]


# --- --jobs N: shards, aggregation, cleanup ------------------------------------------

def _ids(n):
    return [_pair(id=f"P{i}", why=f"pair {i}") for i in range(n)]


def test_shards_are_disjoint_complete_and_deterministic():
    pairs = _ids(10)
    shards = mg.shard_pairs(pairs, 3)
    got = [[p.id for p in s] for s in shards]
    assert got == [["P0", "P3", "P6", "P9"], ["P1", "P4", "P7"], ["P2", "P5", "P8"]]
    flat = [i for s in got for i in s]
    assert sorted(flat) == sorted(p.id for p in pairs) and len(flat) == len(set(flat))
    assert [[p.id for p in s] for s in mg.shard_pairs(pairs, 3)] == got, "not deterministic"
    assert [len(s) for s in mg.shard_pairs(pairs, 25)] == [1] * 10, "more shards than pairs"
    with pytest.raises(mg.GateError, match="--jobs"):
        mg.shard_pairs(pairs, 0)


def test_completeness_names_a_missing_a_duplicate_and_a_stray():
    assert mg.completeness(["A", "B", "C"], [["A", "C"], ["B"]]) == []
    out = mg.completeness(["A", "B", "C"], [["A", "C"], ["C", "X"]])
    assert len(out) == 3, out
    assert "judged by no shard: B" in out[0]
    assert "judged more than once: C" in out[1]
    assert "judged but never selected: X" in out[2]


def _row(pid, status=mg.CAUGHT):
    return {"id": pid, "why": pid, "status": status, "seconds": 1.0, "failing": ["test_f"],
            "missing": [] if status == mg.CAUGHT else ["test_f"]}


def _shard_result(k, rows, *, rc=0, verdict="PASS", after_rc=0, error=None, reason=None):
    if error:
        return {"shard": k, "rc": rc, "report": None, "error": error}
    rep = {"worktree": f"w{k}", "pairs": rows, "verdict": verdict,
           "baseline": {"rc": 0, "summary": "ok", "seconds": 1.0},
           "control_after": {"rc": after_rc, "summary": "ok", "seconds": 1.0}, "seconds": 2.0}
    if reason:
        rep["reason"] = reason
    return {"shard": k, "rc": rc, "report": rep, "error": None}


def test_aggregation_keeps_seed_order_and_passes_when_every_shard_passed():
    pairs = _ids(5)
    shard_ids = [["P0", "P2", "P4"], ["P1", "P3"]]
    # Shard 1 finishes first, and its rows arrive before shard 0's.
    results = [_shard_result(1, [_row("P1"), _row("P3")]),
               _shard_result(0, [_row("P0"), _row("P2"), _row("P4")])]
    rep = mg.aggregate(pairs, shard_ids, results)
    assert [r["id"] for r in rep["pairs"]] == ["P0", "P1", "P2", "P3", "P4"]
    assert rep["verdict"] == "PASS" and "reasons" not in rep
    assert rep["jobs"] == 2 and [s["shard"] for s in rep["shards"]] == [0, 1]


def test_a_crashed_shard_fails_the_verdict_and_is_named():
    pairs = _ids(4)
    results = [_shard_result(0, [_row("P0"), _row("P2")]),
               _shard_result(1, [], rc=3, error="exited 3; its log ends:\nMemoryError")]
    rep = mg.aggregate(pairs, [["P0", "P2"], ["P1", "P3"]], results)
    assert rep["verdict"] == "FAIL"
    assert any(r.startswith("shard 1 crashed") and "MemoryError" in r for r in rep["reasons"])
    assert [r["id"] for r in rep["pairs"]] == ["P0", "P2"], "the healthy shard's rows are kept"


def test_a_pair_no_shard_judged_fails_the_verdict():
    """Every shard says PASS and exits 0, yet one pair has no row: not a pass."""
    pairs = _ids(3)
    results = [_shard_result(0, [_row("P0")]), _shard_result(1, [_row("P1")])]
    rep = mg.aggregate(pairs, [["P0", "P2"], ["P1"]], results)
    assert rep["verdict"] == "FAIL"
    assert any("judged by no shard: P2" in r for r in rep["reasons"]), rep.get("reasons")


def test_a_missed_pair_in_one_shard_fails_the_combined_verdict():
    pairs = _ids(2)
    results = [_shard_result(0, [_row("P0")]),
               _shard_result(1, [_row("P1", mg.MISSED)], rc=1, verdict="FAIL")]
    rep = mg.aggregate(pairs, [["P0"], ["P1"]], results)
    assert rep["verdict"] == "FAIL" and rep["pairs"][1]["status"] == mg.MISSED
    assert "reasons" not in rep, "a MISSED row is the verdict's reason, shown in its row"


def test_a_shard_control_that_failed_or_disagrees_with_its_exit_code_fails():
    pairs = _ids(2)
    red_after = [_shard_result(0, [_row("P0")]),
                 _shard_result(1, [_row("P1")], rc=1, verdict="FAIL", after_rc=1)]
    rep = mg.aggregate(pairs, [["P0"], ["P1"]], red_after)
    assert rep["verdict"] == "FAIL" and any("control after" in r for r in rep["reasons"])
    red_base = [_shard_result(0, [_row("P0")]),
                _shard_result(1, [], rc=1, verdict="FAIL", reason="the baseline is not green")]
    rep = mg.aggregate(pairs, [["P0"], ["P1"]], red_base)
    assert any(r == "shard 1: the baseline is not green" for r in rep["reasons"])
    liar = [_shard_result(0, [_row("P0")]), _shard_result(1, [_row("P1")], rc=1)]
    rep = mg.aggregate(pairs, [["P0"], ["P1"]], liar)
    assert rep["verdict"] == "FAIL" and any("disagrees" in r for r in rep["reasons"])


def test_a_not_judged_pair_passes_as_it_does_in_a_single_run():
    pairs = _ids(2)
    results = [_shard_result(0, [_row("P0")]), _shard_result(1, [_row("P1", mg.NOT_JUDGED)])]
    assert mg.aggregate(pairs, [["P0"], ["P1"]], results)["verdict"] == "PASS"


def _worktrees(repo):
    return [ln for ln in _g(repo, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ")]


def test_jobs_gives_the_verdicts_a_single_run_gives(tiny, tmp_path):
    """Real child processes, one worktree each: the same rows, in the same
    order, with the same statuses as --jobs 1; nothing left behind."""
    pairs = [_pair(id="P1"),
             _pair(id="P2", why="invisible", repl="def f():\n    return 1  # the same\n"),
             _pair(id="P3", why="g", anchor="def g():\n    return 1\n",
                   repl="def g():\n    return 2\n", expect=("test_g",)),
             _pair(id="P4", why="absent", anchor="def h():\n")]
    single = _run(tiny, tmp_path, pairs)
    lines: list = []
    multi = mg.run_gate_jobs(tiny, pairs, jobs=2, log=lines.append)
    assert [(r["id"], r["status"]) for r in multi["pairs"]] == \
        [(r["id"], r["status"]) for r in single["pairs"]] == \
        [("P1", mg.CAUGHT), ("P2", mg.MISSED), ("P3", mg.CAUGHT), ("P4", mg.NOT_APPLIED)]
    assert multi["verdict"] == single["verdict"] == "FAIL"
    assert [s["ids"] for s in multi["shards"]] == [["P1", "P3"], ["P2", "P4"]]
    assert all(s["baseline"]["rc"] == 0 and s["control_after"]["rc"] == 0 for s in multi["shards"])
    trees = [Path(ln.split(" in ", 1)[1]) for ln in lines if " pair(s) in " in ln]
    assert len(trees) == 2 and len(set(trees)) == 2, lines
    assert not any(t.exists() for t in trees) and not trees[0].parent.exists()
    assert len(_worktrees(tiny)) == 1, "a shard worktree was left behind"
    assert (tiny / "thing.py").read_text(encoding="utf-8") == _THING


def _alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def test_kill_tree_stops_a_shard_and_what_it_started(tmp_path):
    """A shard's pytest is the shard's child: stopping the shard alone would
    leave it running, holding files in a worktree the parent is removing.
    Both processes sit in tmp_path, and whatever survives is stopped after,
    so a failing run holds no folder of the gate's own worktree."""
    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(c.pid, flush=True)\ntime.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                            cwd=str(tmp_path), **mg.own_group())
    grandchild = None
    try:
        grandchild = int(proc.stdout.readline())
        assert _alive(grandchild)
        mg._kill_tree(proc)
        assert proc.poll() is not None
        deadline = time.monotonic() + 15
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _alive(grandchild), "the shard's own child survived"
    finally:
        proc.stdout.close()
        for pid in (grandchild, proc.pid):
            if pid is not None:
                _stop(pid)
        proc.wait(timeout=30)


class _Proc:
    """A stand-in shard process: `rc` None runs forever; `boom` raises from poll."""
    def __init__(self, rc=None, boom=None):
        self.rc, self.boom, self.pid = rc, boom, -1

    def poll(self):
        if self.boom:
            raise self.boom
        return self.rc


def test_every_worktree_is_removed_when_a_launch_raises(tiny, monkeypatch):
    killed, made = [], []

    def launch(k, shard, tree, run_dir):
        made.append((tree, run_dir))
        if k == 1:
            raise RuntimeError("the second shard could not start")
        return _Proc()

    monkeypatch.setattr(mg, "_kill_tree", lambda proc: killed.append(proc))
    with pytest.raises(RuntimeError, match="second shard"):
        mg.run_gate_jobs(tiny, _ids(4), jobs=3, launch=launch, log=_QUIET, poll=0.01)
    assert len(killed) == 1, "the shard already running was not stopped"
    assert len(made) == 2 and not any(t.exists() for t, _ in made)
    assert not made[0][1].exists(), "the run folder was left behind"
    assert len(_worktrees(tiny)) == 1, "a shard worktree was left behind"


def test_ctrl_c_kills_the_shards_and_removes_every_worktree(tiny, monkeypatch):
    killed, made = [], []

    def launch(k, shard, tree, run_dir):
        made.append(tree)
        return _Proc(boom=KeyboardInterrupt()) if k == 2 else _Proc()

    monkeypatch.setattr(mg, "_kill_tree", lambda proc: killed.append(proc))
    with pytest.raises(KeyboardInterrupt):
        mg.run_gate_jobs(tiny, _ids(6), jobs=3, launch=launch, log=_QUIET, poll=0.01)
    assert len(killed) == 3, "every shard is stopped, the running ones included"
    assert len(made) == 3 and not any(t.exists() for t in made)
    assert len(_worktrees(tiny)) == 1


def test_a_shard_that_exits_without_a_report_is_a_named_crash(tiny):
    def launch(k, shard, tree, run_dir):
        if k == 0:
            rep = {"worktree": str(tree), "pairs": [_row(p.id) for p in shard], "verdict": "PASS",
                   "baseline": {"rc": 0}, "control_after": {"rc": 0}, "seconds": 1.0}
            (run_dir / "shard0.json").write_text(json.dumps(rep), encoding="utf-8")
            return _Proc(rc=0)
        (run_dir / f"shard{k}.log").write_bytes(b"Traceback\nMemoryError\n")
        return _Proc(rc=3221225477)

    rep = mg.run_gate_jobs(tiny, _ids(4), jobs=2, launch=launch, log=_QUIET, poll=0.01)
    assert rep["verdict"] == "FAIL"
    assert [r["id"] for r in rep["pairs"]] == ["P0", "P2"]
    crash = [r for r in rep["reasons"] if r.startswith("shard 1 crashed")]
    assert crash and "MemoryError" in crash[0], rep["reasons"]
    assert any("judged by no shard: P1, P3" in r for r in rep["reasons"])
    assert len(_worktrees(tiny)) == 1


def test_the_shard_command_refuses_a_tree_it_did_not_make(tiny, tmp_path, capsys):
    spec = tmp_path / "pairs.json"
    spec.write_text(json.dumps([mg._pair_to_json(_pair())]), encoding="utf-8")
    out = tmp_path / "out.json"
    assert mg.main(["shard", "--tree", str(tiny), "--pairs", str(spec), "--json", str(out)]) == 2
    _g(tiny, "worktree", "add", "-q", "-b", "lane", str(tmp_path / "lane"))
    assert mg.main(["shard", "--tree", str(tmp_path / "lane"), "--pairs", str(spec),
                    "--json", str(out)]) == 2
    err = capsys.readouterr().err
    assert "main checkout" in err and "branch" in err
    assert not out.exists()
    assert (tiny / "thing.py").read_text(encoding="utf-8") == _THING


def test_a_pair_survives_the_trip_to_a_shard_unchanged():
    planted = mg.Pair("P3", "planted", (("tests/t.py", None, "x\n", 0), ("a.py", "b", "c", 2)),
                      ("tests",), ("test_x[a-b]",), ("display",))
    assert mg._pair_from_json(json.loads(json.dumps(mg._pair_to_json(planted)))) == planted


def test_the_cli_runs_jobs_and_refuses_zero(tiny, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mg, "REPO", tiny)
    monkeypatch.setattr(mg, "_seeded_pairs", lambda: [_pair(id="P1"), _pair(id="P2", why="b")])
    assert mg.main(["run", "--jobs", "2", "--json", str(tmp_path / "r.json")]) == 0
    assert "VERDICT: PASS -- 2 caught" in capsys.readouterr().out
    rep = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert rep["jobs"] == 2 and [r["id"] for r in rep["pairs"]] == ["P1", "P2"]
    assert mg.main(["run", "--jobs", "0"]) == 2
    assert "--jobs" in capsys.readouterr().err


# --- the seeded pairs, against this tree -------------------------------------------

def test_every_seeded_anchor_matches_its_declared_count():
    bad = [(p.id, mg.check_anchors(REPO, p)) for p in PAIRS]
    bad = [b for b in bad if b[1]]
    assert not bad, ("a seeded pair no longer finds its anchor -- a refactor moved it; "
                     "re-anchor the pair or retire it: " + str(bad[:6]))
    assert len(PAIRS) >= 30, f"the seed shrank to {len(PAIRS)}"


def test_every_seeded_pair_names_tests_that_exist():
    for p in PAIRS:
        assert p.edits and p.tests and p.expect, p.id
        defined = set()
        for t in p.tests:
            assert (REPO / t).is_file(), (p.id, t)
            tree = ast.parse((REPO / t).read_text(encoding="utf-8"))
            defined |= {n.name for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
        for e in p.expect:
            assert e.split("[")[0] in defined, (p.id, e)
        for need in p.needs:
            assert need in mg._NEEDS, (p.id, need)


def test_seeded_ids_are_unique_and_named():
    ids = [p.id for p in PAIRS]
    assert len(set(ids)) == len(ids)
    assert all(p.why for p in PAIRS)
