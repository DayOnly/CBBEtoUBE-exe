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

"""A GATE THAT CANNOT FIRE -- documented, reviewed, and dead.

Written after `#coherence-kink` was found: a full comment block beside
`_repair_coherence_collapse` gave the rationale, a worked example with
measured numbers, and an instruction on how the repair must be applied --
while `kink` was assigned `False` and never set `True` anywhere. The branch
could not run. It read as a shipped feature for as long as nobody grepped for
the variable being SET rather than declared.

**A comment describing a measurement is not evidence the code performs it.**

Three shapes, all the same defect:

  DEAD LOCAL GATE     a local assigned only a constant (False/None/0) and then
                      read as a condition -- the branch is unreachable.
  DEFINED NEVER READ  a module constant bound from `_flag`/`_knob` that nothing
                      else references -- setting its env var does nothing.
  NOT IN THE REGISTRY a live constant with no `Setting()` row -- reachable only
                      by env var, so a real GUI run can never turn it on. This
                      is what shipped a set of verified fixes as unreachable:
                      the user's own build could not reproduce them.

Usage:

    python scripts/analysis/dead_gate_audit.py [--quiet]

Exit code is always 0: this reports, it does not gate. `tests/` pins the one
invariant that is currently clean (nothing defined-but-never-read).
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / "src"

# `_bsp` is DELIBERATELY dead: the body-only SkyPatcher pivot was removed and
# its comment says the remaining branches are pruned later. Listed so a real
# find is not lost in a known one.
KNOWN_DEAD = {"_bsp"}

CONST_DEAD = (False, None, 0)


class _FuncScan(ast.NodeVisitor):
    """Per-function: which names are only ever bound to a dead constant, and
    which names are read as a condition."""

    def __init__(self) -> None:
        self.assigns: "dict[str, list[tuple[int, bool]]]" = {}
        self.conditions: "set[str]" = set()
        self.excluded: "set[str]" = set()

    def _target(self, tgt, dead, lineno):
        if isinstance(tgt, ast.Name):
            self.assigns.setdefault(tgt.id, []).append((lineno, dead))
        else:
            for n in ast.walk(tgt):
                if isinstance(n, ast.Name):
                    self.excluded.add(n.id)

    def visit_Assign(self, node):
        dead = (isinstance(node.value, ast.Constant)
                and not isinstance(node.value.value, str)
                and node.value.value in CONST_DEAD)
        for t in node.targets:
            self._target(t, dead, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            dead = (isinstance(node.value, ast.Constant)
                    and not isinstance(node.value.value, str)
                    and node.value.value in CONST_DEAD)
            self._target(node.target, dead, node.lineno)
        self.generic_visit(node)

    def _exclude_targets(self, node_target):
        for n in ast.walk(node_target):
            if isinstance(n, ast.Name):
                self.excluded.add(n.id)

    def visit_AugAssign(self, node):
        self._exclude_targets(node.target)
        self.generic_visit(node)

    def visit_For(self, node):
        self._exclude_targets(node.target)
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self._exclude_targets(node.target)
        self.generic_visit(node)

    def visit_Global(self, node):
        self.excluded.update(node.names)
        self.generic_visit(node)

    def visit_Nonlocal(self, node):
        self.excluded.update(node.names)
        self.generic_visit(node)

    def visit_withitem(self, node):
        if node.optional_vars is not None:
            self._exclude_targets(node.optional_vars)
        self.generic_visit(node)

    def _cond(self, expr):
        for n in ast.walk(expr):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                self.conditions.add(n.id)

    def visit_If(self, node):
        self._cond(node.test)
        self.generic_visit(node)

    def visit_While(self, node):
        self._cond(node.test)
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self._cond(node.test)
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self._cond(node)
        self.generic_visit(node)


def _py_files():
    for root, _dirs, files in os.walk(SRC):
        if "__pycache__" in root:
            continue
        for f in sorted(files):
            if f.endswith(".py"):
                yield Path(root) / f


def dead_local_gates():
    """(file, function, name, lineno) for every unreachable local gate."""
    hits = []
    for p in _py_files():
        text = io.open(p, encoding="utf-8").read()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            s = _FuncScan()
            for st in node.body:
                s.visit(st)
            for name, records in s.assigns.items():
                if name in s.excluded or name in KNOWN_DEAD:
                    continue
                if name not in s.conditions or not records:
                    continue
                if not all(dead for _ln, dead in records):
                    continue
                hits.append((p.name, node.name, name, records[0][0]))
    return hits


def flag_constants():
    """CONST -> (file, lineno, env name) for every _flag/_knob binding."""
    out = {}
    for p in _py_files():
        try:
            tree = ast.parse(io.open(p, encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name):
                continue
            call = node.value
            if isinstance(call, ast.UnaryOp) and isinstance(call.op, ast.Not):
                call = call.operand
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            if call.func.id not in ("_flag", "_knob"):
                continue
            env = None
            if call.args and isinstance(call.args[0], ast.Constant):
                env = call.args[0].value
            out[tgt.id] = (p.name, node.lineno, env)
    return out


def defined_never_read(consts=None):
    """Constants bound from _flag/_knob that nothing else in src/ references."""
    consts = consts if consts is not None else flag_constants()
    blob = "\n".join(io.open(p, encoding="utf-8").read() for p in _py_files())
    out = []
    for const, (f, ln, env) in sorted(consts.items()):
        if len(re.findall(r"\b%s\b" % re.escape(const), blob)) <= 1:
            out.append((const, f, ln, env))
    return out


def unregistered(consts=None):
    """Live constants with no Setting() row -- env-var only."""
    consts = consts if consts is not None else flag_constants()
    gs = SRC / "gui_settings.py"
    reg = io.open(gs, encoding="utf-8").read() if gs.exists() else ""
    envs = set(re.findall(r'env="([^"]+)"', reg))
    out = []
    for const, (f, ln, env) in sorted(consts.items()):
        if not env or env in envs:
            continue
        alt = (env.replace("CBBE2UBE_NO_", "CBBE2UBE_") if "CBBE2UBE_NO_" in env
               else env.replace("CBBE2UBE_", "CBBE2UBE_NO_"))
        if alt in envs:
            continue
        out.append((const, f, ln, env))
    return out


def main() -> int:
    quiet = "--quiet" in sys.argv
    consts = flag_constants()
    gates = dead_local_gates()
    never = defined_never_read(consts)
    unreg = unregistered(consts)

    print("DEAD GATE AUDIT")
    print("=" * 74)
    print(f"  _flag/_knob constants : {len(consts)}")
    print()
    print(f"DEAD LOCAL GATES (assigned a constant, then read as a condition): "
          f"{len(gates)}")
    for f, fn, name, ln in gates:
        print(f"    {f}:{ln}  {fn}()  ->  `{name}` is never set otherwise")
    if not gates:
        print("    none")
    print()
    print(f"DEFINED BUT NEVER READ (its env var does nothing): {len(never)}")
    for const, f, ln, env in never:
        print(f"    {const:<38s} {f}:{ln}  env={env}")
    if not never:
        print("    none")
    print()
    print(f"NOT IN THE SETTINGS REGISTRY (env-var only): {len(unreg)} of {len(consts)}")
    if not quiet:
        for const, f, ln, env in unreg[:40]:
            print(f"    {const:<38s} {f}:{ln}  env={env}")
        if len(unreg) > 40:
            print(f"    ... and {len(unreg) - 40} more")
    print()
    print("  Being unregistered is not automatically wrong -- most of these are")
    print("  internal tuning. It IS wrong for anything a USER would need to")
    print("  reach: a fix nobody can turn on has not shipped.")
    if not consts:
        print("\n  !! 0 constants found -- 0/0 IS NOT A PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
