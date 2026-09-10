"""A function that is itself a `_note_pass_failure` label may not swallow its
own body in silence.

The recorder pattern is "voice at the CALLER": `try: x = _helper(...)
except Exception as e: _note_pass_failure("_helper", e)`. That is worthless
when `_helper` wraps its whole body in `except Exception: return <input>` --
the caller's handler can never fire, and the 2026-09-01 audit found three
such helpers (`_smooth_push_field`, `_smooth_vertex_field`,
`_smooth_warp_grooves`) plus two more whose `None` return the caller read as
"no demand". `tests/test_no_silent_pass_failures.py` cannot see them: its
PASS_HINTS filter looks at what the try body CALLS, and these bodies call
only numpy.

Rule pinned here: if a function's name appears as a `_note_pass_failure`
label anywhere in nif_convert.py, then every broad handler of a top-level
try spanning >= 50% of that function must itself call `_note_pass_failure`
(or re-raise).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src import nif_convert as nc

SPAN = 0.5


def _tree(src: str | None = None):
    """The whole converter (every declared module), or a given source text."""
    if src is not None:
        return ast.parse(src)
    from tests import _converter_sources as cs
    return cs.tree()


def _labels(tree) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_note_pass_failure" and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)):
            out.add(n.args[0].value.split("/")[0].split(":")[0])
    return out


def _voiced(handler) -> bool:
    for n in ast.walk(handler):
        if isinstance(n, ast.Raise):
            return True
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_note_pass_failure"):
            return True
    return False


def _broad(handler) -> bool:
    return handler.type is None or (
        isinstance(handler.type, ast.Name)
        and handler.type.id in ("Exception", "BaseException"))


def offenders(tree) -> list[tuple[str, int]]:
    labels = _labels(tree)
    out = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name not in labels:
            continue
        fn_len = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        for st in fn.body:
            if not isinstance(st, ast.Try):
                continue
            try_len = (st.end_lineno or st.lineno) - st.lineno + 1
            if try_len < SPAN * fn_len:
                continue
            for h in st.handlers:
                if _broad(h) and not _voiced(h):
                    out.append((fn.name, h.lineno))
    return out


def test_label_named_functions_do_not_swallow_their_own_body():
    tree = _tree()
    labels = _labels(tree)
    assert len(labels) >= 40, "label census collapsed -- the check is void"
    found = offenders(tree)
    assert not found, (
        "these functions are `_note_pass_failure` labels yet swallow their "
        "own body, so the caller-side recorder can never fire:\n  "
        + "\n  ".join(f"nif_convert.py:{ln} {fn}()" for fn, ln in found)
        + "\nVoice the handler inside the function itself.")


def test_the_check_can_actually_fail():
    """Control: re-parse a mutated copy where one label-named function's
    handler is muted again; the check must report exactly that function."""
    from tests import _converter_sources as cs
    needle = ('    except Exception as _e:\n'
              '        _note_pass_failure("_smooth_push_field", _e)\n'
              '        return push')
    hits = [(p, t) for p, t in cs.texts().items() if t.count(needle) == 1]
    assert len(hits) == 1, "mutation anchor moved; update the control"
    _p, src = hits[0]
    mutated = src.replace(needle, '    except Exception:\n        return push')
    # labels are collected from the same text, so the mutated module alone is
    # a valid population: `_smooth_push_field` is a label there (its own caller
    # wrapper) -- if it is not, the control has lost its footing.
    found = offenders(_tree(mutated))
    assert [fn for fn, _ in found] == ["_smooth_push_field"], found
