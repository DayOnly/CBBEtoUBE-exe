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

"""The converter's boolean flag surface, resolved from the CODE.

    python -m scripts.analysis.flag_surface [--unreachable] [--kill-switches]

One row per boolean pass flag bound through `_flag(...)` in `src/nif_convert.py`:
its constant name, the env name, whether it is a kill switch (`not _flag(NO_X)`
-- default ON, the env turns it OFF), the value it RESOLVES to at empty env, and
whether the GUI can reach it (a `Setting(...)` row carrying that env name).

WHY RESOLVE, NOT PARSE. A flag's default is a DATED claim everywhere but in the
running code: memories drift, comments drift, and four constants are reassigned
after declaration ([[project_flag_audit_2026_08_18]]'s shadow-force trap). This
imports the module in a scrubbed environment and reads the attribute.

WHY THE KILL-SWITCH COLUMN. The retirement rule for a flag is: default ON, an
in-game verdict on record, the switch unused since, and the OFF branch reproduces
a measured-WORSE state. Every candidate for that rule is a `not _flag(NO_X)`
binding, so this column is the census that rule starts from. Whether a given
flag MEETS the rule is a judgement against the record, never a count.

`--unreachable` lists only flags with no GUI row -- the ones no deployed run can
ever switch, which is the class that shipped a validated fix nobody could reach.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

# `_?` because 16 flags are private-prefixed (`_SRC_NORMAL_FIX`, `_TRI_WRITE_ONCE`,
# `_MORPHTRI_SCALE`, ...) and the first draft of this census silently missed
# every one of them -- 122 rows where the surface is 138. A census that cannot
# see the flag it exists to judge is the trap this file is built against.
_BIND = re.compile(
    r'^(_?[A-Z][A-Z0-9_]+)\s*=\s*\(?\s*(not\s+)?_flag\("([A-Z0-9_]+)",\s*(True|False)\)',
    re.M)


def declared(src_text: str) -> list[tuple[str, str, bool, bool]]:
    """(constant, env name, is_kill_switch, declared_default) per binding.

    THE REGEX FORM. Kept, and `declared_all` is checked against it: whatever
    this matches, the parse must also match. A control that can only ever agree
    is not a control, so the test asserts CONTAINMENT rather than equality.
    """
    out = []
    for name, neg, env, dflt in _BIND.findall(src_text):
        out.append((name, env, bool(neg), dflt == "True"))
    return out


def _discover_flag_modules() -> "tuple[str, ...]":
    """Every `src/` module that binds a pass flag, DISCOVERED rather than listed.

    `nif_convert.py` stopped being the whole surface at the 2026-09-01 split,
    and a census reading only the monolith reports the flag surface as smaller
    than it is. The replacement was a hard-coded four-module tuple, which is the
    same failure one release later: it was correct at the moment it was written
    and would have gone silently short the first time a flag was bound in a
    fifth module. Discovered, it cannot.
    """
    src = _REPO / "src"
    if not src.is_dir():
        return ()
    return tuple(sorted(
        p.stem for p in src.glob("*.py")
        if "_flag(" in p.read_text(encoding="utf-8", errors="replace")))


_FLAG_MODULES = _discover_flag_modules()


def declared_all() -> "list[dict]":
    """Every `_flag("ENV", default)` call in `src/`, PARSED rather than matched.

    WHY A PARSE, WHEN A REGEX ALREADY EXISTED. The single-line pattern missed 14
    of 152 flags for two unrelated reasons: eight live in modules the census
    never opened, and six are written in forms one line cannot hold -- a binding
    split across lines, an `X = (0.0 if _flag(...) else ...)` ternary, and a
    `_flag` read inline in a condition with no constant at all. That is the same
    class as the first draft missing 16 private-prefixed flags, which this
    file's own docstring calls the trap it is built against. One of the 14 is
    turned ON by the live recipe.

    `const` is None for a call READ INLINE and never bound -- a real flag with
    no constant to resolve, reported rather than dropped for not fitting the
    shape of the others.
    """
    import ast
    out = []
    for mod in _FLAG_MODULES:
        p = _REPO / "src" / (mod + ".py")
        if not p.is_file():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        # Every node mapped to the Assign that encloses it, so a `_flag` buried
        # in a ternary still reports the constant it ends up in.
        holder = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                for sub in ast.walk(node.value):
                    holder[id(sub)] = node.targets[0].id
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_flag"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            env = node.args[0].value
            if not isinstance(env, str):
                continue
            dflt = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                dflt = node.args[1].value
            out.append({"module": mod, "const": holder.get(id(node)),
                        "env": env, "declared_default": dflt})
    return out


def resolved_values(names: list[str], module: str = "nif_convert") -> dict:
    """Import the module in a SCRUBBED subprocess and read each constant.
    A subprocess, so this process's own env cannot leak into the answer."""
    code = (
        "import sys, json; sys.path.insert(0, %r)\n"
        "import src.%s as nc\n"
        "print(json.dumps({n: getattr(nc, n, None) for n in %r}))\n"
        % (str(_REPO), module, names))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, cwd=str(_REPO))
    if r.returncode != 0:
        raise SystemExit("could not import src.%s:\n%s"
                         % (module, r.stderr[-2000:]))
    import json
    return json.loads(r.stdout.strip().splitlines()[-1])


def gui_envs() -> set[str]:
    from src import gui_settings as gs
    return {s.env for s in gs.SETTINGS if getattr(s, "env", None)}


def main(argv: list[str]) -> int:
    only_unreachable = "--unreachable" in argv
    only_kill = "--kill-switches" in argv
    src = (_REPO / "src" / "nif_convert.py").read_text(encoding="utf-8")
    rows = declared(src)
    if not rows:
        print("0 flag bindings found -- the binding regex no longer matches; "
              "0/0 IS NOT A PASS")
        return 2
    values = resolved_values([r[0] for r in rows])
    reach = gui_envs()
    on = off = unreach = unreach_off = kill = 0
    print("%-40s %-44s %-5s %-5s %-4s" % ("constant", "env", "kill", "on", "gui"))
    for name, env, is_kill, _d in rows:
        v = values.get(name)
        if not isinstance(v, bool):
            continue
        g = env in reach
        on += v
        off += (not v)
        kill += is_kill
        if not g:
            unreach += 1
            unreach_off += (not v)
        if only_unreachable and g:
            continue
        if only_kill and not is_kill:
            continue
        print("%-40s %-44s %-5s %-5s %-4s"
              % (name, env, "yes" if is_kill else "", "ON" if v else "off",
                 "yes" if g else "NO"))
    print("")
    print("boolean pass flags       : %d   (ON %d / off %d)" % (on + off, on, off))
    print("kill-switch form         : %d   (default ON, env turns OFF)" % kill)
    print("unreachable from the GUI : %d   (of which default-off opt-ins: %d)"
          % (unreach, unreach_off))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
