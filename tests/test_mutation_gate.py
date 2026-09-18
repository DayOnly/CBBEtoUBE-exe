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
import shutil
import subprocess
import tempfile
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
