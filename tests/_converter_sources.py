"""The converter's source files, as ONE surface, for every structural guard.

Six guards used to resolve "the converter" to the single file
`src/nif_convert.py`. A function moved to a sibling module would silently
leave the pass map, both parity reach sets and the three swallowed-handler
scans -- green suite, smaller guard. This helper is the one place that says
which files make up the converter; `scripts/pass_map.py` owns the list and
`tests/test_split_preconditions.py` pins it against the file system.

Use `tree()` for an AST over all of them (each top-level node carries
`_file`), `texts()` when a guard scans source text, and `lines_of(file)`
for the slice-by-lineno idiom that `pass_map._segment` needs.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import pass_map  # noqa: E402


def files() -> list[Path]:
    return [REPO / rel for rel in pass_map.CONVERTER_MODULES]


def texts() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in files()}


def lines_of(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def tree() -> ast.Module:
    """One Module whose body is every declared file's body, in list order.
    Top-level nodes get `_file` (the repo-relative path) for messages."""
    body = []
    for p, txt in texts().items():
        mod = ast.parse(txt, filename=str(p))
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        for n in mod.body:
            n._file = rel
        body.extend(mod.body)
    return ast.Module(body=body, type_ignores=[])


def whole_text() -> str:
    """All files' text joined -- for guards that grep rather than parse."""
    return "\n".join(texts().values())
