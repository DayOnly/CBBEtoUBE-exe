"""Preconditions for splitting `nif_convert.py` -- pinned BEFORE any function
moves (2026-09-01 audit, findings F005/F006/F007/F087).

1. The converter is a DECLARED list of files, and the list matches the file
   system: a new `src/nif_convert_*.py` that is not declared fails here, so
   it cannot fall outside the guards.
2. Every guard that walks "the converter" sees the whole list: the pass map
   keeps a floor of rows per entry point and the parity walker a floor of
   reachable helpers, so a move that drops passes out of a guard's view is
   loud, not silent.
3. Module-level state stays where its writers are: no `global` statement
   and no `from .nif_convert import <STATE>` in a sibling (a copied binding
   forks the cache -- the BUG-00 fail-open class, in reverse).
4. Every declared module imports on its own in a fresh interpreter (a
   circular import through a star-shim fails only in that order).
5. A ratchet on tests that read the WHOLE of nif_convert.py as text: they
   all break on a split, so the count may not grow.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _converter_sources as cs
from scripts import pass_map

REPO = cs.REPO
SRC = REPO / "src"

# Floors from 2026-09-01: convert_nif 135 rows, convert_nif_phase2 175 rows
# (the `_finalize_physics_and_motion_match` shared tail is its own section);
# parity reach 261 / 279. Set ~10% below so only a real loss trips them --
# and raise them when the truth grows.
PASS_MAP_ROW_FLOOR = {"convert_nif": 120, "convert_nif_phase2": 155,
                      # The shared tail BOTH paths call. It was the only entry
                      # with no floor, and it is the one that collapses hardest
                      # when a module goes undeclared: 29 rows -> 1.
                      "_finalize_physics_and_motion_match": 24}
# Rows carrying a `guarded by` value. That column is how the map answers "does
# this pass run at DEFAULTS", and it empties independently of the row count --
# it is read from flag constants, so it can go blank while every row survives.
PASS_MAP_GUARD_FLOOR = 70          # 85 on 2026-09-02
REACH_FLOOR = {"convert_nif": 235, "convert_nif_phase2": 250}
WHOLE_FILE_READ_CEILING = 20      # 18 on 2026-09-02; migrate, do not add.
# Was 45 against a stated 40. Re-counted with THIS file's own regex: 18.
# A ceiling 27 above the real count is not a ratchet -- it silently
# permits 27 new whole-file reads, every one of which breaks on the next
# extraction. Lower it whenever sites migrate; never raise it.

_STATE = re.compile(r"^(?:[A-Z][A-Z0-9_]+|_[A-Za-z0-9_]*_CACHE|_PIECE_HDT_XML_TEXT)$")


def test_declared_modules_match_the_file_system():
    declared = {Path(p).name for p in pass_map.CONVERTER_MODULES}
    on_disk = {p.name for p in SRC.glob("nif_convert*.py")}
    assert on_disk <= declared, (
        f"undeclared converter module(s): {sorted(on_disk - declared)} -- add them to "
        f"scripts/pass_map.CONVERTER_MODULES or the guards will not see them")
    for rel in pass_map.CONVERTER_MODULES:
        assert (REPO / rel).is_file(), rel


def test_pass_map_keeps_a_row_floor_per_entry():
    text = (REPO / "docs" / "PASS_MAP.md").read_text(encoding="utf-8")
    for entry, floor in PASS_MAP_ROW_FLOOR.items():
        sec = text.split(f"## `{entry}`", 1)[1].split("\n## ", 1)[0]
        rows = len(re.findall(r"^\| *\d+ *\|", sec, re.M))
        assert rows >= floor, (
            f"PASS_MAP `{entry}` has {rows} rows (< {floor}): a regeneration lost "
            f"passes -- did one move out of the declared modules?")


def test_parity_reach_keeps_a_floor_per_entry():
    from tests import test_convert_path_parity as tp
    g = tp._call_graph(tp._module_ast())
    got = {tp.ENTRY_A: len(tp._reachable(g, tp.ENTRY_A, stop=(tp.ENTRY_B,))),
           tp.ENTRY_B: len(tp._reachable(g, tp.ENTRY_B))}
    for entry, floor in REACH_FLOOR.items():
        assert got[entry] >= floor, (
            f"parity walker reaches {got[entry]} helpers from {entry} (< {floor}) "
            f"-- a moved helper has dropped out of the walk")


def test_siblings_carry_no_global_state_and_copy_none():
    bad = []
    for p, txt in cs.texts().items():
        if p.name == "nif_convert.py":
            continue
        mod = ast.parse(txt)
        for n in ast.walk(mod):
            if isinstance(n, ast.Global):
                bad.append(f"{p.name}:{n.lineno} global {', '.join(n.names)}")
            if (isinstance(n, ast.ImportFrom) and n.module
                    and n.module.endswith("nif_convert")):
                for a in n.names:
                    if _STATE.match(a.name):
                        bad.append(f"{p.name}:{n.lineno} from nif_convert import {a.name}")
    assert not bad, (
        "module state must stay with its writers; read it as `nc.NAME` at call "
        "time instead:\n  " + "\n  ".join(bad))


def test_reload_of_nif_convert_re_executes_every_split_module():
    """`importlib.reload(nc)` is how 78 test sites re-read a flag after
    changing the environment. The split modules must be re-executed by that
    reload, or a flag/cache that moved keeps its stale value."""
    import importlib
    from src import nif_convert as nc
    expected = tuple(Path(r).stem for r in pass_map.CONVERTER_MODULES
                     if Path(r).stem not in ("nif_convert", "fit_metrics"))
    assert tuple(nc._SPLIT_MODULES) == expected, (nc._SPLIT_MODULES, expected)
    from src import nif_convert_telemetry as tel
    before = tel._PASS_FAILURES_THIS_PIECE
    importlib.reload(nc)
    from src import nif_convert_telemetry as tel2
    assert tel2._PASS_FAILURES_THIS_PIECE is not before, "sibling was not re-executed"
    assert nc._PASS_FAILURES_THIS_PIECE is tel2._PASS_FAILURES_THIS_PIECE, "shim rebound to a stale object"


@pytest.mark.parametrize("rel", pass_map.CONVERTER_MODULES)
def test_each_declared_module_imports_alone(rel):
    mod = "src." + Path(rel).stem
    r = subprocess.run([sys.executable, "-c", f"import {mod}"], cwd=str(REPO),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"`import {mod}` alone failed:\n{r.stderr[-1500:]}"


_ALIAS = re.compile(r"(?:from src import nif_convert as (\w+)|import src\.nif_convert as (\w+))")


def _setattr_re(text: str):
    """setattr(<alias>, "name", ...) for every alias this test file binds to
    nif_convert (nc, nc_mod, _nc, ... -- one test used `nc_mod` and slipped
    past a fixed alias list)."""
    aliases = {a or b for a, b in _ALIAS.findall(text)} | {"nc", "nif_convert", "_nc"}
    al = "|".join(sorted(aliases))
    # monkeypatch.setattr(nc, "x", ...)  OR a bare `nc.x = spy` assignment
    return re.compile(r"setattr\(\s*(?:" + al + r")\s*,\s*[\"'](\w+)[\"']"
                      r"|^\s*(?:" + al + r")\.(\w+)\s*=[^=]", re.M)


def test_no_test_patches_a_moved_callee_on_nc():
    """A `monkeypatch.setattr(nc, "X", fake)` reaches only code that looks X
    up in nif_convert's namespace. If X was moved to a sibling module and is
    called from INSIDE that sibling, the patch is invisible there and the
    test measures the real function while believing it faked it (split step
    3 broke six shapedata tests exactly this way). Patch the sibling."""
    sib_calls = {}      # name -> sibling module that defines it AND calls it internally
    for p, txt in cs.texts().items():
        if p.name == "nif_convert.py":
            continue
        mod = ast.parse(txt)
        # names the sibling resolves in ITS OWN globals: its defs, and what it
        # binds by a module-level import (a replicated `from .x import y`)
        defs = {n.name for n in mod.body if isinstance(n, ast.FunctionDef)}
        for n in mod.body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    defs.add((al.asname or al.name).split(".")[0])
        for fn in mod.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            for c in ast.walk(fn):
                if (isinstance(c, ast.Name) and isinstance(c.ctx, ast.Load)
                        and c.id in defs and c.id != fn.name):
                    sib_calls[c.id] = p.name
    bad = []
    for t in sorted((REPO / "tests").glob("test_*.py")):
        text = t.read_text(encoding="utf-8")
        for m in _setattr_re(text).finditer(text):
            name = m.group(1) or m.group(2)
            if name in sib_calls:
                bad.append(f"{t.name}: nc.{name} -> patch src/{sib_calls[name]} instead")
    assert not bad, "patches that cannot reach their callee:\n  " + "\n  ".join(bad)
    assert sib_calls, "no intra-sibling calls found -- the hazard table is empty, check the walker"


_WHOLE_FILE = re.compile(
    r"inspect\.getsource\(nc\)|getfile\(nc\)\)\.read_text|getsourcefile\(nc\)|"
    r"nc\.__file__\)\.read_text|['\"]nif_convert\.py['\"]\)\.read_text")


def test_whole_file_text_reads_do_not_grow():
    n = 0
    for p in (REPO / "tests").glob("test_*.py"):
        n += len(_WHOLE_FILE.findall(p.read_text(encoding="utf-8")))
    assert n <= WHOLE_FILE_READ_CEILING, (
        f"{n} whole-file reads of nif_convert.py in tests (ceiling "
        f"{WHOLE_FILE_READ_CEILING}); every one breaks on a split -- use "
        f"tests/_converter_sources.py instead of adding another")
    assert n >= 10, "the pattern matched almost nothing; the ratchet is void"


def test_pass_map_keeps_its_guard_column():
    """A row floor cannot see the `guarded by` column emptying.

    `pass_map` fills it from the flag constants it can resolve, so a
    reorganisation that moves the flag bindings while leaving the passes put
    yields a full-length map in which nothing appears to be behind a flag --
    and 'does this pass run at defaults' is the question this project gets
    wrong most often (feedback_deployed_build_runs_at_defaults)."""
    text = (REPO / "docs" / "PASS_MAP.md").read_text(encoding="utf-8")
    guarded = sum(1 for ln in text.splitlines()
                  if re.match(r"^\| *\d+ *\|", ln)
                  and ln.rstrip().rstrip("|").rsplit("|", 1)[-1].strip())
    assert guarded >= PASS_MAP_GUARD_FLOOR, (
        f"only {guarded} pass-map rows name a guard (< {PASS_MAP_GUARD_FLOOR}): "
        "the flag constants stopped resolving, so the map now reports every "
        "pass as unconditional")


def test_a_pass_living_in_a_SIBLING_module_still_gets_a_row():
    """Cross-file resolution, proved on the case that already exists rather
    than on a throwaway module: `minimum_push` is defined in src/fit_metrics.py
    and called on the body-swap path. It had NO row in the map until
    fit_metrics was added to CONVERTER_MODULES."""
    text = (REPO / "docs" / "PASS_MAP.md").read_text(encoding="utf-8")
    assert "`minimum_push`" in text, (
        "the sibling-module pass lost its row -- cross-file resolution is the "
        "whole reason CONVERTER_MODULES is a list and not a filename")


def test_the_row_floors_can_actually_fire(monkeypatch):
    """CONTROL. A floor that has never been seen to fail is decoration.

    Undeclare every sibling module -- exactly what an extraction that forgets
    to add its new module looks like -- and re-render. Measured 2026-09-02:
    348 rows -> 122, a 65% loss, and all three floors fire. This is what makes
    the floors above load-bearing rather than aspirational, and it is the
    class 2026-08-02_AUDIT_MAIN_LAYOUT.md already paid for once ("Moving those 35 files
    silently removed 29 tests, and the suite stayed green")."""
    text = pass_map.SRC.read_text(encoding="utf-8")
    monkeypatch.setattr(pass_map, "CONVERTER_MODULES", ("src/nif_convert.py",))
    doc = pass_map.render(text)

    assert "`minimum_push`" not in doc, (
        "undeclaring fit_metrics no longer drops its pass, so the sibling test "
        "above proves nothing")
    fired = []
    for entry, floor in PASS_MAP_ROW_FLOOR.items():
        sec = doc.split(f"## `{entry}`", 1)[1].split("\n## ", 1)[0]
        if len(re.findall(r"^\| *\d+ *\|", sec, re.M)) < floor:
            fired.append(entry)
    assert set(fired) == set(PASS_MAP_ROW_FLOOR), (
        "undeclaring every sibling module did NOT push these entries under "
        f"their floor: {sorted(set(PASS_MAP_ROW_FLOOR) - set(fired))} -- the "
        "floor is set too low to catch the failure it exists for")
