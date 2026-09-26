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

"""#fingerprint-skips-plumbing -- the survey of `CBBE2UBE_*` names cannot go
stale unseen.

Reviewed on 0081b20: the hand survey that decided which variables the
--incremental fingerprint leaves out claimed every name was classified, and
missed CBBE2UBE_ANTIPOKE_SURFACE_QUIET -- a switch that only silences two trace
lines, in none of the documented hashed groups. This parses every name under
src/ and the entry point (string constants and f-strings, so a comment is not a
read) and puts each one in the plumbing table or in ONE of
`auto_convert._FINGERPRINT_HASHED_GROUPS`. A new name in neither fails here.
The scan is negative-controlled with planted reads."""
import ast
import os
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from src import auto_convert as ac

REPO = Path(__file__).resolve().parent.parent
_NAME = re.compile(r"^CBBE2UBE_[A-Z0-9_{}]+$")


def _helper_aliases(tree) -> dict:
    """{local name: "_flag" | "_knob"} for every import of the envflags
    helpers, whatever they are called here (`_flag`, `_pflag`, ...)."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("envflags"):
            for a in node.names:
                if a.name in ("flag", "knob"):
                    out[a.asname or a.name] = "_" + a.name
    return out


# The survey's own tables name every name they classify; counted as a use,
# a table entry would keep itself alive after the code stopped reading it.
_SURVEY_TABLES = ("_FINGERPRINT_PLUMBING_WHY", "_FINGERPRINT_HASHED_GROUPS")


def names_in(path: Path) -> dict:
    """{name: {how it is read}} for one module: "_flag" / "_knob" through the
    helpers, else "named" (a raw read, a table entry, a message)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helpers = _helper_aliases(tree)
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    in_tables = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in _SURVEY_TABLES):
            in_tables.update(id(sub) for sub in ast.walk(node.value))
    found: dict = {}
    for node in ast.walk(tree):
        if id(node) in in_tables:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if isinstance(parent.get(id(node)), ast.JoinedStr):
                continue                       # a piece of an f-string, below
            name = node.value
        elif isinstance(node, ast.JoinedStr):
            name = "".join(v.value if isinstance(v, ast.Constant) else "{}"
                           for v in node.values)
        else:
            continue
        if not _NAME.match(name):
            continue
        how = "named"
        call = parent.get(id(node))
        if (isinstance(call, ast.Call) and call.args and call.args[0] is node
                and isinstance(call.func, ast.Name) and call.func.id in helpers):
            how = helpers[call.func.id]
        found.setdefault(name, set()).add(how)
    return found


def population() -> dict:
    files = sorted((REPO / "src").glob("*.py")) + [REPO / "cbbe_to_ube_main.py"]
    out: dict = {}
    for p in files:
        for name, hows in names_in(p).items():
            out.setdefault(name, set()).update(hows)
    return out


def _listed_only(name: str) -> bool:
    words = name[len("CBBE2UBE_"):].split("_")
    return any(w.startswith(tuple(ac._FINGERPRINT_LISTED_ONLY)) for w in words)


def classify(name: str, hows) -> "str | None":
    """'plumbing', a hashed group's name, or None when the survey does not
    say what this name is."""
    if name in ac._FINGERPRINT_PLUMBING_WHY:
        return "plumbing"
    groups = ac._FINGERPRINT_HASHED_GROUPS
    for group, spec in groups.items():
        if name in spec.get("names", ()):
            return group
    if _listed_only(name):
        return None
    for group, spec in groups.items():
        if spec.get("read") in hows:
            return group
    return None


# --- the survey --------------------------------------------------------------

def test_every_name_is_plumbing_or_in_a_hashed_group():
    pop = population()
    assert len(pop) >= 400, f"only {len(pop)} names found -- the scan lost its population"
    loose = {n: sorted(h) for n, h in pop.items() if classify(n, h) is None}
    assert not loose, (
        "a CBBE2UBE_* name the fingerprint survey does not classify. Put it in "
        "auto_convert._FINGERPRINT_PLUMBING_WHY if it cannot change a mesh (a "
        "log, a launch detail, a switch that only silences lines), else list it "
        f"in a _FINGERPRINT_HASHED_GROUPS group, with the reason: {loose}")


def test_the_quiet_switch_is_left_out_and_the_diagnostics_are_not():
    pop = population()
    assert classify("CBBE2UBE_ANTIPOKE_SURFACE_QUIET",
                    pop["CBBE2UBE_ANTIPOKE_SURFACE_QUIET"]) == "plumbing"
    assert classify("CBBE2UBE_PASS_TRACE", pop["CBBE2UBE_PASS_TRACE"]) == "diagnostic"
    assert classify("CBBE2UBE_NO_CONFORM", pop["CBBE2UBE_NO_CONFORM"]) == "switch"


def test_the_schedule_switch_is_hashed_for_the_files_it_keeps_not_the_maths(monkeypatch):
    """CBBE2UBE_NO_GLOBAL_SCHEDULE is read through `_flag`, so the survey put
    it in the switch group -- 'the mesh maths' -- although every NIF comes out
    the same byte for byte either way. It stays hashed (it decides whether a
    base an earlier source converted this run stays in meshes\\), under the
    reason that is true."""
    pop = population()
    name = "CBBE2UBE_NO_GLOBAL_SCHEDULE"
    assert "_flag" in pop[name], "control: it is read through the switch helper"
    assert classify(name, pop[name]) == "which files stay"
    assert "meshes" in ac._FINGERPRINT_HASHED_GROUPS["which files stay"]["why"]
    import types
    _clean_env(monkeypatch)
    args = types.SimpleNamespace()
    base = ac._nif_config_fingerprint(args)
    monkeypatch.setenv(name, "1")
    assert ac._nif_config_fingerprint(args) != base, "it must stay hashed"


def test_no_listed_name_has_gone_from_the_code():
    """A table entry for a name nothing reads any more is a claim about
    nothing -- drop it."""
    pop = population()
    listed = set(ac._FINGERPRINT_PLUMBING_WHY)
    for spec in ac._FINGERPRINT_HASHED_GROUPS.values():
        listed |= set(spec.get("names", ()))
    stale = sorted(listed - set(pop))
    assert not stale, f"listed but no longer in src/: {stale}"


def test_a_name_is_left_out_or_hashed_never_both():
    seen: dict = {n: "plumbing" for n in ac._FINGERPRINT_PLUMBING_WHY}
    for group, spec in ac._FINGERPRINT_HASHED_GROUPS.items():
        for n in spec.get("names", ()):
            assert n not in seen, f"{n} is in {seen[n]} and {group}"
            seen[n] = group


def test_every_group_says_why_and_how_a_name_joins_it():
    for group, spec in ac._FINGERPRINT_HASHED_GROUPS.items():
        assert str(spec.get("why", "")).strip(), group
        assert ("read" in spec) != ("names" in spec), group


# --- the negative control ----------------------------------------------------

def test_the_scan_sees_planted_names_in_every_form(tmp_path):
    """A new verbosity switch read through `_flag` is NOT absorbed by the
    switch group; a new knob is; a raw path read and an aliased helper are
    both seen."""
    copy = tmp_path / "overlay_transfer.py"
    shutil.copy(REPO / "src" / "overlay_transfer.py", copy)
    before = names_in(copy)
    copy.write_bytes(copy.read_bytes() + (
        b'\n\nfrom .envflags import flag as _pf\n'
        b'_Q = _knob("CBBE2UBE_PLANTED_REACH", 1.0)\n'
        b'_S = _pf("CBBE2UBE_PLANTED_SWITCH", False)\n'
        b'_V = _pf(\n    "CBBE2UBE_PLANTED_VERBOSE", False)\n'
        b'_P = os.environ.get("CBBE2UBE_PLANTED_PATH", "")\n'
        b'_W = os.environ.get(f"CBBE2UBE_PLANTED_W{_S}")\n'))
    after = names_in(copy)
    new = {n: after[n] for n in set(after) - set(before)}
    assert set(new) == {"CBBE2UBE_PLANTED_REACH", "CBBE2UBE_PLANTED_SWITCH",
                        "CBBE2UBE_PLANTED_VERBOSE", "CBBE2UBE_PLANTED_PATH",
                        "CBBE2UBE_PLANTED_W{}"}, new
    assert classify("CBBE2UBE_PLANTED_REACH", new["CBBE2UBE_PLANTED_REACH"]) == "knob"
    assert classify("CBBE2UBE_PLANTED_SWITCH", new["CBBE2UBE_PLANTED_SWITCH"]) == "switch"
    assert classify("CBBE2UBE_PLANTED_VERBOSE", new["CBBE2UBE_PLANTED_VERBOSE"]) is None
    assert classify("CBBE2UBE_PLANTED_PATH", new["CBBE2UBE_PLANTED_PATH"]) is None
    assert classify("CBBE2UBE_PLANTED_W{}", new["CBBE2UBE_PLANTED_W{}"]) is None


def test_a_comment_or_docstring_is_not_a_name(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('"""Set CBBE2UBE_IN_A_DOCSTRING=1 to ..."""\n'
                 '# CBBE2UBE_IN_A_COMMENT\nX = 1\n', encoding="utf-8")
    assert names_in(p) == {}


# --- what leaving the quiet switch out rests on ---------------------------------

def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.upper().startswith("CBBE2UBE_"):
            monkeypatch.delenv(k, raising=False)


def test_the_quiet_switch_does_not_invalidate_the_cache(monkeypatch):
    import types
    _clean_env(monkeypatch)
    args = types.SimpleNamespace()
    base = ac._nif_config_fingerprint(args)
    monkeypatch.setenv("CBBE2UBE_ANTIPOKE_SURFACE_QUIET", "1")
    assert ac._nif_config_fingerprint(args) == base
    monkeypatch.setenv("CBBE2UBE_NO_FINGERPRINT_SKIPS_PLUMBING", "1")
    with_switch = ac._nif_config_fingerprint(args)
    monkeypatch.delenv("CBBE2UBE_ANTIPOKE_SURFACE_QUIET")
    assert ac._nif_config_fingerprint(args) != with_switch, (
        "the off-switch must hash it again")


@pytest.mark.parametrize("apex_y,with_tris", [(2.6, True), (0.0, True), (2.6, False)],
                         ids=["fires", "no-deficit", "inert"])
def test_the_quiet_switch_only_silences_lines(monkeypatch, capsys, apex_y, with_tris):
    """The claim the plumbing entry makes, run: with the surface pass armed,
    the garment comes out the same with the trace on or off, and only the
    printed lines differ."""
    from src import nif_convert  # noqa: F401  (fitgeom resolves it at call time)
    from src import nif_convert_fitgeom as fg
    from tests.test_antipoke_surface_req import _one_triangle
    v, tris, bv, bn, nip = _one_triangle(apex_y)
    monkeypatch.setattr(fg, "ANTIPOKE_SURFACE_REQ", True)
    out = {}
    said = {}
    for trace in (True, False):
        monkeypatch.setattr(fg, "_ANTIPOKE_SURFACE_TRACE", trace)
        capsys.readouterr()
        out[trace] = np.asarray(fg.clear_armor_outside_body(
            v, bv, bn, body_nipple=nip, tris=tris if with_tris else None), np.float64)
        said[trace] = capsys.readouterr().out
    assert np.array_equal(out[True], out[False])
    assert "[antipoke-surface]" in said[True], said[True]
    assert "[antipoke-surface]" not in said[False], said[False]
