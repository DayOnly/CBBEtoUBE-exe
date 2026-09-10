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


def patch(monkeypatch, name: str, value) -> int:
    """Fake `name` on EVERY declared converter module that binds it.

    After a split, a function may be called both from nif_convert (through
    its by-name import) and from inside the sibling that now defines it;
    each looks the name up in its own module globals, so a patch on one of
    them reaches only one caller. Returns how many modules were patched
    (0 means the name exists nowhere -- a typo, not a silent no-op)."""
    import importlib
    n = 0
    for rel in pass_map.CONVERTER_MODULES:
        mod = importlib.import_module("src." + Path(rel).stem)
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, value)
            n += 1
    assert n, f"{name} is bound on no converter module"
    return n


def set_all(name: str, value) -> int:
    """`patch()` without a monkeypatch: bare-assignment form for tests that
    set `nc.<name> = fake` themselves (same lifetime semantics as before --
    the caller restores, or does not, exactly as it did)."""
    import importlib
    n = 0
    for rel in pass_map.CONVERTER_MODULES:
        mod = importlib.import_module("src." + Path(rel).stem)
        if hasattr(mod, name):
            setattr(mod, name, value)
            n += 1
    assert n, f"{name} is bound on no converter module"
    return n


def source(obj) -> str:
    """`inspect.getsource(obj)` for the split converter: the whole declared
    source when `obj` is the nif_convert module, else the object's own text,
    with the call-time `_nc().` prefix stripped so a pin written as
    `if not FLAG:` still matches code that now reads `if not _nc().FLAG:`."""
    import inspect
    import types
    if isinstance(obj, types.ModuleType) and obj.__name__.endswith("nif_convert"):
        txt = whole_text()
    else:
        txt = inspect.getsource(obj)
    return txt.replace("_nc().", "")


def orchestrator_source(fn) -> str:
    """The source of an entry function AS IT READS: the per-shape loop that
    was lifted out of it (`_fit_shapes_copy` / `_fit_shapes_swap`, 2026-09-01)
    is spliced back in at its call site, so a pin that checks what comes
    before or after a stage still sees one ordered text. `_nc().` stripped."""
    import inspect
    import re
    from src import nif_convert as nc
    src = inspect.getsource(fn)
    for name in ("_fit_shapes_copy", "_fit_shapes_swap"):
        if name + "(" in src and hasattr(nc, name):
            lifted = inspect.getsource(getattr(nc, name))
            src, n = re.subn(r"^[ 	]*" + name + r"\(_types\.SimpleNamespace\([\s\S]*?^[ 	]*\)\)[ 	]*$",
                             lambda m: lifted, src, count=1, flags=re.M)
            assert n == 1, f"call site of {name} not found in {fn.__name__}"
    return src.replace("_nc().", "")


def whole_text() -> str:
    """All files' text joined -- for guards that grep rather than parse."""
    return "\n".join(texts().values())
