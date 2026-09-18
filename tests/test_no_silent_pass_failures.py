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

"""A geometry/weight pass may not fail in SILENCE.

THE DEFECT CLASS. "A swallowed exception is indistinguishable from 'nothing
qualified'" -- one missing import once made an entire pass a silent no-op, and a
`NameError` inside a bare `except: pass` twice produced a byte-identical mesh
that read as "the change had no effect". The converter's answer is
`_note_pass_failure`, which surfaces the failure on three reporting surfaces.

THE SHARPER VERSION, censused 2026-08-22: a VOICED INNER HANDLER IS WORTHLESS
INSIDE A MUTE OUTER ONE. `_finalize_hdt_physics` printed a WARN and recorded per
collision proxy, while the enclosing handler was `except Exception: pass` -- so a
failure in the loop SETUP skipped every proxy without a word.
`_inject_ube_extremity_replacement` was silent at BOTH levels while defaulting
ON. Seven such handlers existed; all seven now record.

This test keeps that at zero. It is deliberately STRUCTURAL (an AST walk), not a
name list, so a newly-added pass is covered the day it is written.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src import nif_convert as nc

# Calls that mean "this try block was doing real fit/geometry/weight/IO work".
PASS_HINTS = (
    "_match_", "_conform", "_repair", "_weld", "_transfer_", "_graft", "_seed_",
    "_separate_", "_inflate", "_strip_", "_refresh_", "_ride_", "_rigidify",
    "_sync_", "_cap_", "clear_armor", "rebury_", "fit_armor", "bake_preset",
    "repair_collapsed", "_recompute_", "warp_", "_smooth", "_relax", "_push",
    "_bury", "_apply_", "_inject_", "_add_bone", "setShapeWeights",
    "atomic_nif_save", "_copy_shape", "generate_armor_tri",
    "_partial_rigid_panels",
    # XML and backup writes. Seven handlers swallowed a failed one: four
    # physics-XML rewrites shipped the old physics unrecorded, and three NIF
    # backup restores printed "RESTORED original" whether or not the restore
    # worked. #silent-xml-write
    "atomic_write_bytes",
)


def _tree():
    # All declared converter modules (split precondition A), not one file.
    from tests import _converter_sources as cs
    return cs.tree()


def _is_silent(handler):
    """True when the handler body DOES NOTHING: only pass/continue/break, or a
    bare return of a literal/name. Any call, assignment or raise records
    something and is not silent.

    Defining it this way matters. A call-based test ("does the handler call a
    recorder?") read `_inject_err = _e` and `result_reason = result_reason + ...`
    as silent and over-reported 15 handlers for 7 -- both of those ARE surfaced,
    one at `:6911` and one through `ConvertResult.reason`.
    """
    for st in handler.body:
        if isinstance(st, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(st, ast.Return) and (
                st.value is None
                or isinstance(st.value, (ast.Constant, ast.Name))
                or (isinstance(st.value, ast.Tuple)
                    and all(isinstance(e, (ast.Constant, ast.Name))
                            for e in st.value.elts))):
            continue
        return False
    return True


def _calls(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if nm:
                out.add(nm)
    return out


def _owner_map(tree):
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(fn.lineno, (fn.end_lineno or fn.lineno) + 1):
                prev = owner.get(ln)
                if prev is None or fn.lineno > prev[1]:
                    owner[ln] = (fn.name, fn.lineno)
    return owner


def _silent_pass_handlers(tree):
    owner = _owner_map(tree)
    out = []
    for t in ast.walk(tree):
        if not isinstance(t, ast.Try):
            continue
        guarded = sorted(c for c in _calls(ast.Module(body=t.body,
                                                      type_ignores=[]))
                         if any(h in c for h in PASS_HINTS))
        if not guarded:
            continue
        for h in t.handlers:
            broad = (h.type is None
                     or (isinstance(h.type, ast.Name)
                         and h.type.id in ("Exception", "BaseException")))
            if broad and _is_silent(h):
                out.append((h.lineno,
                            owner.get(h.lineno, ("<module>", 0))[0],
                            guarded[:3]))
    return out


def _silent_pass_handlers_by_module(texts):
    """[(repo-relative path, line, function, guarded)], walking EACH module on its
    own. The merged tree numbers every module from line 1, so a line-keyed owner
    map over it named functions from OTHER modules: measured 2026-09-15, all seven
    silent write handlers came back as 'analyse', '_bind', '_field_screen_scale'
    and the like, every one labelled nif_convert.py. #silent-xml-write"""
    from tests import _converter_sources as cs
    out = []
    for path, txt in texts.items():
        rel = Path(path).resolve().relative_to(cs.REPO).as_posix()
        out += [(rel, ln, fn, g) for ln, fn, g in _silent_pass_handlers(ast.parse(txt))]
    return out


def test_no_geometry_pass_fails_silently():
    from tests import _converter_sources as cs
    found = _silent_pass_handlers_by_module(cs.texts())
    assert not found, (
        "these broad handlers swallow a geometry/weight/save failure without "
        "recording it, so a BROKEN pass is indistinguishable from 'nothing "
        "qualified':\n  "
        + "\n  ".join(f"{rel}:{ln} {fn}()  guards {g}"
                      for rel, ln, fn, g in found)
        + "\nUse `_note_pass_failure(label, exc)` -- it never raises.")


def test_a_silent_write_is_reported_in_its_own_file_and_function():
    """Mute one physics-XML recorder in a copy of its module's text: the walk must
    name THAT file and THAT function, not a neighbour's."""
    from tests import _converter_sources as cs
    needle = '            _note_pass_failure("_make_chains_static/xml-write", _we, xml_path)\n'
    texts = cs.texts()
    hits = [p for p, t in texts.items() if needle in t]
    assert len(hits) == 1, "the mutation target moved; re-point this control"
    texts[hits[0]] = texts[hits[0]].replace(needle, "            pass\n", 1)
    found = _silent_pass_handlers_by_module(texts)
    assert [(rel, fn) for rel, _ln, fn, _g in found] == [
        ("src/nif_convert_physics.py", "_make_chains_static")], found


def test_the_check_can_actually_fail():
    """GUARD THE GUARD. Re-parse the module with one recorder replaced by
    `pass`; the check must then report that handler. Without this, a green
    result could mean 'the walk broke' rather than 'nothing is silent'."""
    from tests import _converter_sources as cs
    needle = '        _note_pass_failure("_carrier_is_body_conforming", _ce)'
    hits = [txt for txt in cs.texts().values() if needle in txt]
    assert len(hits) == 1, (
        "the mutation target moved; re-point this control at another recorder")
    mutated = hits[0].replace(needle, "        pass", 1)
    found = _silent_pass_handlers(ast.parse(mutated))
    assert any(fn == "_carrier_is_body_conforming" for _ln, fn, _g in found), (
        "muting a recorder did NOT make the check report it -- the check "
        "cannot fail and proves nothing")


def test_the_pass_hint_list_still_matches_real_helpers():
    """A prefix list that matches nothing makes the whole check vacuous -- the
    exact way the convert-path parity guard was silently defanged for months."""
    tree = _tree()
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    matched = {n for n in names if any(h in n for h in PASS_HINTS)}
    assert len(matched) >= 40, (
        f"only {len(matched)} helpers match PASS_HINTS; the list has drifted "
        f"away from the code and this check is close to vacuous")
