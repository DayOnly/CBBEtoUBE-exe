"""No test may leave a converter module changed for the tests after it.

A test that fakes a converter function by bare assignment (`nc.f = fake`),
or by a helper that assigns it on every split sibling while the test restores
only `nc.f`, leaves the fake in place for every later test in the process.
Those tests then pass or fail by the ORDER the suite happens to run in; one
such leak hid a failing regression guard behind the default file order.

`conftest.py` snapshots every loaded `src.*` and `scripts.*` module's
namespace before each test (`snapshot()`), and after the test's own fixtures
are torn down -- `monkeypatch` has undone its patches by then -- `settle()`
puts back every attribute the test rebound, added or deleted and returns one
line per leak, so the test that leaked FAILS and one leak cannot cascade.

Only REBINDING is judged, by identity: a module cache (dict/list/set) filled
in place during a test is the same object and never trips it. A replaced
`sys.modules` entry is a leak too. These are legitimate and are not leaks:

* `__dunder__` names (`__warningregistry__` is written by `warnings`), and a
  submodule bound on its package by an import (`scripts.analysis.x`).

* a name the module's own code rebinds at run time -- declared `global` in
  that module, or assigned as `_nc().NAME = ...` by a converter sibling
  (a lazily-built cache such as the skeleton bone list, or the physics XML
  bound per piece). Found by reading the source, so a new one needs no edit
  here; `run_time_names()` lists them. The converter rebinding them is not a
  test's fault -- any test that converts a piece in-process leaves them set --
  so they never FAIL a test, but they are put back like everything else: a
  cache built from one test's fake skeleton, or one piece's physics XML, must
  not reach the next test.
* an `importlib.reload` of the module, told by the fresh `__spec__` a reload
  binds before it runs the source again. Only in a module that was reloaded
  are functions, classes and other objects judged by what they ARE (same
  code, same class name, same type) and a fresh EMPTY container accepted (a
  reload rebuilding `_CACHE = {}`). Plain values -- the switches -- are judged
  by equality either way, so a reload left under a non-default environment is
  still a leak. Without a reload, a `functools.wraps` spy (same code once
  unwrapped) or an emptied table is a leak like any other rebinding.
* a same-typed container or value that is EQUAL to the one it replaced.

Every rebinding, legitimate or not, except dunders and submodules is put back
to the snapshot, so the next test starts from the objects the process started
with. Blind spots, by design: a module first imported INSIDE a test (watched
from the next one), a cache MUTATED in place with a fake entry (identity
cannot see it), a spy installed in a module the same test also reloaded, and
`os.environ`.
"""
from __future__ import annotations

import operator
import re
import sys
import types
from pathlib import Path

WATCHED_PREFIXES = ("src.", "scripts.")

_MISSING = object()
_PLAIN = (bool, int, float, complex, str, bytes, type(None), tuple, frozenset,
          range)
_CONTAINERS = (dict, list, set)
_RUN_TIME_NAMES: dict[str, frozenset] = {}


def _watched(name: str) -> bool:
    return name.startswith(WATCHED_PREFIXES)


_GLOBAL_RE = re.compile(r"^[ \t]*global[ \t]+([\w \t,]+)", re.M)
# `_nc().NAME = / += ...` -- an assignment, never `==`, `<=`, `!=`.
_NC_ASSIGN_RE = re.compile(r"\b_nc\(\)\.(\w+)[ \t]*[-+*/|&]?=(?!=)")


def _text(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError):
        return ""


def _source_names(mod) -> frozenset:
    """Names `mod`'s own source rebinds at run time (`global NAME`), plus the
    `_nc().NAME = ...` targets any converter sibling assigns on nif_convert.
    Read as text, not parsed: this runs inside a test's teardown."""
    got: set[str] = set()
    path = getattr(mod, "__file__", None)
    if path and path.endswith(".py"):
        for m in _GLOBAL_RE.finditer(_text(path)):
            got.update(n.strip() for n in m.group(1).split(",") if n.strip())
    if mod.__name__ == "src.nif_convert":
        src = Path(__file__).resolve().parents[1] / "src"
        for p in sorted(src.glob("*.py")):
            got.update(_NC_ASSIGN_RE.findall(_text(p)))
    return frozenset(got)


def run_time_names(mod) -> frozenset:
    names = _RUN_TIME_NAMES.get(mod.__name__)
    if names is None:
        names = _RUN_TIME_NAMES[mod.__name__] = _source_names(mod)
    return names


def snapshot():
    """(name, module, namespace copy) for every loaded watched module."""
    return [(name, mod, dict(mod.__dict__))
            for name, mod in list(sys.modules.items())
            if _watched(name) and isinstance(mod, types.ModuleType)]


def _code_of(obj):
    seen = 0
    while hasattr(obj, "__wrapped__") and seen < 8:
        obj = obj.__wrapped__
        seen += 1
    return getattr(obj, "__code__", None)


def _same_value(a, b) -> bool:
    try:
        import numpy as np
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
            return (isinstance(a, np.ndarray) and isinstance(b, np.ndarray)
                    and a.dtype == b.dtype and np.array_equal(a, b))
    except ImportError:
        pass
    try:
        return bool(a == b)
    except Exception:
        return False


def _reloaded(before: dict, now: dict) -> bool:
    """True when `importlib.reload` ran the module again during the test:
    reload binds a freshly found `__spec__` on the module before it executes
    the source, and nothing else rebinds it. Not a guess from the functions
    -- a spy that unwraps to the original's code is not a reload."""
    return now.get("__spec__", _MISSING) is not before.get("__spec__", _MISSING)


def _equivalent(a, b, reloaded: bool, depth: int = 0) -> bool:
    """Is `b` (after the test) a legitimate rebinding of `a` (before)?
    `reloaded`: the module was re-executed, so its objects are new copies."""
    if a is b:
        return True
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and a != a and b != b:
        return True                                  # nan
    if not reloaded:
        # Nothing re-created the module's objects, so only an EQUAL value is
        # harmless. A wraps spy, a function or class swapped for a namesake,
        # an emptied table: each is a rebinding the next test would inherit.
        if (isinstance(a, (types.FunctionType, type, types.ModuleType))
                or _code_of(a) is not None):
            return False
        return _same_value(a, b)
    if depth < 3 and isinstance(a, (tuple, list)) and len(a) == len(b):
        return all(_equivalent(x, y, reloaded, depth + 1) for x, y in zip(a, b))
    if depth < 3 and isinstance(a, dict) and len(b):
        return (list(a) == list(b)
                and all(_equivalent(a[k], b[k], reloaded, depth + 1) for k in a))
    if isinstance(a, _PLAIN):
        return _same_value(a, b)
    if isinstance(a, types.FunctionType) or _code_of(a) is not None:
        # A re-executed `def` has equal code; its defaults are rebuilt too (a
        # default `lambda` is a new object), so they are judged the same way.
        ca, cb = _code_of(a), _code_of(b)
        return (ca is not None and ca == cb and depth < 3
                and _equivalent(getattr(a, "__defaults__", None),
                                getattr(b, "__defaults__", None), True, depth + 1)
                and _equivalent(getattr(a, "__kwdefaults__", None),
                                getattr(b, "__kwdefaults__", None), True, depth + 1))
    if isinstance(a, type):
        return (a.__module__, a.__qualname__) == (b.__module__, b.__qualname__)
    if isinstance(a, types.ModuleType):
        return a.__name__ == b.__name__
    if isinstance(a, _CONTAINERS):
        return len(b) == 0 or _same_value(a, b)
    # Anything else a re-executed module rebuilds (a lock, a logger adapter,
    # a compiled pattern): the same type is the most that can be asked.
    return True


def settle(snaps) -> list[str]:
    """Put every watched module back as `snaps` recorded it. Returns one
    `module.attr: what happened` line per leak (empty when clean)."""
    leaks: list[str] = []
    for name, mod, before in snaps:
        if sys.modules.get(name) is not mod:
            leaks.append(f"{name}: sys.modules entry replaced or removed")
            sys.modules[name] = mod
        now = mod.__dict__
        if (len(now) == len(before)
                and all(map(operator.is_, now.keys(), before.keys()))
                and all(map(operator.is_, now.values(), before.values()))):
            continue
        reloaded = _reloaded(before, now)
        run_time = run_time_names(mod)
        for k in [k for k in now if k not in before]:
            w = now[k]
            if _is_dunder(k) or _is_submodule(name, k, w):
                continue            # interpreter bookkeeping / an import
            if k not in run_time:
                leaks.append(f"{name}.{k}: added and left behind")
            del now[k]
        for k, v in before.items():
            w = now.get(k, _MISSING)
            if w is v or _is_dunder(k):
                continue
            if k in run_time:
                # The converter's own run-time state: its code may rebind it,
                # so no test is failed for it, but it does not outlive the test.
                now[k] = v
                continue
            if w is not _MISSING and _equivalent(v, w, reloaded):
                now[k] = v          # a reload's copy: back to the original
                continue
            if w is _MISSING:
                leaks.append(f"{name}.{k}: deleted and not put back")
            else:
                leaks.append(f"{name}.{k}: rebound to {_describe(w)} "
                             f"(was {_describe(v)}) and not restored")
            now[k] = v
    return leaks


def _is_dunder(k: str) -> bool:
    """`__warningregistry__`, `__spec__` ...: written by the interpreter."""
    return k.startswith("__") and k.endswith("__")


def _is_submodule(pkg: str, k: str, v) -> bool:
    """Importing `pkg.k` binds `k` on the package: an import, not a patch."""
    return isinstance(v, types.ModuleType) and v.__name__ == f"{pkg}.{k}"


def _describe(v) -> str:
    q = getattr(v, "__qualname__", None)
    mod = getattr(v, "__module__", None)
    if q:
        name = f"{mod}.{q}" if mod else q
        # functools.wraps copies the name onto a spy: say it is a wrapper,
        # or the leak line would read "rebound to X (was X)".
        if getattr(v, "__wrapped__", None) is not None:
            return f"a wrapper of {name}"
        return name
    r = repr(v)
    return type(v).__name__ + " " + (r if len(r) <= 60 else r[:57] + "...")
