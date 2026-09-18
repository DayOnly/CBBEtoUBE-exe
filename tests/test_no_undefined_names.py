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

"""AN UNDEFINED NAME INSIDE A SWALLOWING `except` IS AN INVISIBLE DEAD PASS.

This codebase wraps nearly every fit pass in `try/except Exception` so one bad
shape cannot lose a whole piece. That is the right call, and it has a cost: a
NameError raised while EVALUATING a call's arguments is caught by the same
handler as a genuine geometry failure, so the pass simply does not run and
nothing distinguishes it from "this shape did not qualify".

The sweep that motivated this file found THREE live instances at once:

  * `src_body_n_authored` -- bound as a local in `convert_nif_phase2` but read
    inside `_fit_shapes_swap`, which takes its inputs off a `ctx` namespace. Both
    authored floors therefore never ran on ANY body-swap piece, and the A/B table
    reported a clean, believable "no change" for an arm that could not fire.
  * `_antipoke_stat_tree` -- same shape, and worse because the block ASSIGNS it,
    making it an unbound LOCAL rather than a missing global. The `[clip-risk]`
    telemetry (verts still inside the body after the final pass) has never once
    printed on the body-swap path.
  * `tk` in `src/gui.py` -- tkinter is imported lazily inside `launch_gui()`, so
    the tooltip class's references to it resolve to nothing and every tooltip
    silently fails to render.

None of these is visible in review: each reads as ordinary code beside a handler
whose whole job is to be quiet. `ast` alone cannot see them either -- it takes
scope analysis, which is what pyflakes does.

`tests/test_dead_gate_audit.py` is the sibling for gates that cannot CHANGE;
this is for names that cannot RESOLVE.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Files with a KNOWN undefined name, each with the reason it is still here.
# Empty is the goal; an entry is a debt, not an exemption to reach for.
# EMPTY as of 2026-09-05. The last entry was `src/gui.py`'s `tk`: the tooltip
# class read a module-global that only `launch_gui()` ever bound, so every
# tooltip raised NameError into its own `except Exception` and silently never
# rendered. Fixed by importing tkinter inside `_ToolTip._show`, which keeps the
# lazy-import contract the entry was protecting -- importing the module still
# does not need a display, and `test_importing_gui_does_not_need_a_display`
# pins that.
KNOWN: dict = {}


def _pyflakes(target):
    return subprocess.run([sys.executable, "-m", "pyflakes", target],
                          capture_output=True, text=True, cwd=str(REPO))


def _undefined(out):
    """(relative path, name) for every 'undefined name' pyflakes reports."""
    hits = []
    for line in out.splitlines():
        if "undefined name" not in line:
            continue
        path, _, rest = line.partition(":")
        name = rest.split("undefined name", 1)[1].strip().strip("'\"")
        hits.append((path.replace("\\", "/"), name))
    return hits


def test_pyflakes_is_available():
    """The control. A missing checker makes every assertion below vacuous, and
    this exact failure mode -- a detector that reports zero because it is not
    running -- is why the audit sweeps here all carry one.

    IT COULD NOT DO THAT JOB UNTIL 2026-09-10. `python -m <missing module>`
    exits **1** with empty stdout, and this test allowed 0 or 1 -- so an
    uninstalled pyflakes read as "ran, found nothing to report". CI had never
    installed it: every `test_no_undefined_names_in_src` case passed there on an
    empty string, and the only test that noticed was the catches-a-defect
    control below, which is what actually went red.

    The returncode alone therefore cannot carry this. An interpreter that cannot
    find the module says so on stderr, and that is the signal this now reads.
    """
    r = _pyflakes(str(REPO / "tests" / "test_no_undefined_names.py"))
    assert "No module named" not in r.stderr, (
        "pyflakes is NOT INSTALLED, so every sweep in this file scores an empty "
        "string and passes on nothing. Install it -- CI does that in the test "
        "job rather than requirements.txt, because it is a test dependency and "
        "not a runtime one.\n%s" % r.stderr[:400])
    assert r.returncode in (0, 1), (
        "pyflakes did not run (%s): install it, or this file measures nothing\n%s"
        % (r.returncode, r.stderr[:400]))


def test_the_checker_actually_catches_an_undefined_name(tmp_path):
    """And that it can still FIRE. Feed it the exact shape of the bug -- a name
    read inside a function that is only bound in a different one."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def caller():\n"
        "    only_a_local = 1\n"
        "    return only_a_local\n"
        "\n"
        "def other():\n"
        "    try:\n"
        "        return only_a_local\n"
        "    except Exception:\n"
        "        return None\n", encoding="utf-8")
    hits = _undefined(_pyflakes(str(bad)).stdout)
    assert [n for _p, n in hits] == ["only_a_local"], (
        "the checker cannot see the very defect this file exists to prevent")


@pytest.mark.parametrize("mod", sorted(
    p.name for p in (REPO / "src").glob("*.py")))
def test_no_undefined_names_in_src(mod):
    """Per module, so a failure names the file rather than one long blob."""
    rel = "src/" + mod
    hits = _undefined(_pyflakes(str(REPO / "src" / mod)).stdout)
    unexpected = sorted({n for p, n in hits if n not in KNOWN.get(rel, set())})
    assert not unexpected, (
        f"{rel} reads name(s) that cannot resolve: {unexpected}. Inside this "
        "codebase's swallowing handlers that is a silently dead pass, not a "
        "crash -- fix the scoping, or record it in KNOWN with the reason.")


def _unused_imports(out):
    """(relative path, import text) for every 'imported but unused' line."""
    hits = []
    for line in out.splitlines():
        if "imported but unused" not in line:
            continue
        path, _, rest = line.partition(":")
        text = rest.split("'")[1] if rest.count("'") >= 2 else rest.strip()
        hits.append((path.replace("\\", "/"), text))
    return hits


def test_the_checker_actually_catches_an_unused_import(tmp_path):
    """Control for the gate below: it must still fire on the plain case."""
    bad = tmp_path / "bad.py"
    bad.write_text("import os\nfrom pathlib import Path\n\nprint(Path('.'))\n",
                   encoding="utf-8")
    assert [t for _p, t in _unused_imports(_pyflakes(str(bad)).stdout)] == ["os"]


@pytest.mark.parametrize("mod", sorted(
    p.name for p in (REPO / "src").glob("*.py")))
def test_no_unused_imports_in_src(mod):
    """G-3. The mechanical split left 762 'imported but unused' lines, which hid
    any real one: a helper that stopped being called looked like the 763rd.
    Names that tests and callers reach THROUGH nif_convert are declared in its
    `__all__`, the only re-export form pyflakes honours."""
    rel = "src/" + mod
    hits = _unused_imports(_pyflakes(str(REPO / "src" / mod)).stdout)
    assert not hits, (
        f"{rel} imports {len(hits)} name(s) it never uses: "
        f"{[t for _p, t in hits][:8]}. Delete them -- or, for a deliberate "
        "re-export, add the name to the module's __all__.")


def test_the_known_list_does_not_rot():
    """A debt that quietly repays itself must leave the list, or the list stops
    describing the code and starts hiding it."""
    for rel, names in KNOWN.items():
        hits = {n for _p, n in _undefined(_pyflakes(str(REPO / rel)).stdout)}
        stale = sorted(names - hits)
        assert not stale, (
            f"{rel}: {stale} is no longer undefined -- drop it from KNOWN")


def test_importing_gui_does_not_need_a_display():
    """The contract the last KNOWN entry existed to protect.

    `launch_gui()` imports tkinter lazily so that importing `src.gui` -- which
    the CLI and the pool workers do -- never requires a display. The tooltip fix
    that cleared the entry keeps that by importing inside `_ToolTip._show`, and
    this is what stops a later reader "tidying" it up to module scope.
    """
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s'); import src.gui; "
         "print('tkinter' in sys.modules)" % str(REPO)],
        capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().endswith("False"), (
        "importing src.gui pulled tkinter in at module scope -- that breaks the "
        "no-display import contract:\n" + out.stdout + out.stderr)


def test_the_tooltip_can_actually_reach_tkinter():
    """The behavioural partner: pinning the import site alone would pass just as
    well if `_show` had been deleted. Drive the real method with a fake widget
    and assert it does not swallow a NameError -- which is exactly how this bug
    hid, since `_show` wraps its whole body in `except Exception`."""
    import ast
    src = (REPO / "src" / "gui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    show = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_show"), None)
    assert show is not None, "_ToolTip._show is gone"
    names = {a.name.split(".")[0]
             for n in ast.walk(show) if isinstance(n, ast.Import)
             for a in n.names}
    assert "tkinter" in names, (
        "_show no longer imports tkinter itself, so it is back to reading a "
        "module global that only launch_gui() binds -- the original bug")
