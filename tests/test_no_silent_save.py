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

"""A pass's FINAL SAVE may never fail silently.

Most fit passes end `return 0` / `return total`, where 0 already means "no rows
qualified". So an `except Exception: return 0` around the save makes a LOST
WRITE indistinguishable from a clean no-op: the pass reports success, the report
shows nothing unusual, and the piece ships unmodified.

This is not hypothetical. Two defects reached the user through exactly this
shape -- the ray pairing that raised `NameError` on every call and produced a
byte-identical mesh, and the chain-node drop that shipped a class of skirts
stretched to the floor. Audited 2026-08-12: 16 handlers wrapped a pass-final
`atomic_nif_save` and 7 of them were silent.

The project already tracks the silent-swallow count as a worsening trend, so
this is a RATCHET: the save surface specifically may not regress.
"""
import ast
import inspect
from pathlib import Path

from src import nif_convert as nc

# A handler is "speaking" if it reports through any channel the run surfaces.
REPORTERS = ("_note_pass_failure", "print", "raise", "warn", "log",
             "result_reason", "_inject_err")


def _save_handlers():
    """(lineno, function, handler-source) for every except wrapping a save."""
    path = Path(inspect.getsourcefile(nc))
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    fn_of = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                fn_of[id(n)] = fn.name
    out = []
    for t in ast.walk(tree):
        if not isinstance(t, ast.Try):
            continue
        lo = t.body[0].lineno
        hi = max(getattr(n, "end_lineno", n.lineno) for n in t.body)
        if "atomic_nif_save" not in " ".join(lines[lo - 1:hi]):
            continue
        for h in t.handlers:
            body = "\n".join(lines[n.lineno - 1] for n in h.body
                             if getattr(n, "lineno", None))
            out.append((h.lineno, fn_of.get(id(h), "<module>"), body))
    return out


def test_the_audit_actually_finds_the_save_handlers():
    """A ratchet that matches nothing passes forever. Pin that the scan still
    locates a realistic number of save sites, so a refactor that renames
    `atomic_nif_save` cannot silently disarm this test."""
    handlers = _save_handlers()
    assert len(handlers) >= 12, (
        f"only {len(handlers)} save handlers found -- the scan has probably "
        "stopped matching, not the code stopped saving")


def test_no_pass_final_save_fails_silently():
    """THE RATCHET. Every except around a pass-final save must report."""
    silent = [(ln, fn) for ln, fn, body in _save_handlers()
              if not any(r in body for r in REPORTERS)]
    assert not silent, (
        "silent handler(s) around a pass-final save -- a lost write would be "
        "indistinguishable from 'nothing qualified': "
        + ", ".join(f"{fn}() line {ln}" for ln, fn in silent))


def test_reporting_is_routed_through_the_project_recorder():
    """`print` alone is not enough for a pass: a pool worker's stdout can be
    discarded by the frozen exe (#worker-prints-invisible), while
    _note_pass_failure also records into the per-piece list the report reads."""
    handlers = _save_handlers()
    noted = [1 for _ln, _fn, body in handlers if "_note_pass_failure" in body]
    assert len(noted) >= 7, (
        "the pass-final saves should report via _note_pass_failure, not print "
        "alone -- a worker print can vanish in the frozen exe")
