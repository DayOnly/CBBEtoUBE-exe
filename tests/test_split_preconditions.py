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
PASS_MAP_ROW_FLOOR = {"convert_nif": 120, "convert_nif_phase2": 155}
REACH_FLOOR = {"convert_nif": 235, "convert_nif_phase2": 250}
WHOLE_FILE_READ_CEILING = 45      # 40 on 2026-09-01; migrate, do not add

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


@pytest.mark.parametrize("rel", pass_map.CONVERTER_MODULES)
def test_each_declared_module_imports_alone(rel):
    mod = "src." + Path(rel).stem
    r = subprocess.run([sys.executable, "-c", f"import {mod}"], cwd=str(REPO),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"`import {mod}` alone failed:\n{r.stderr[-1500:]}"


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
