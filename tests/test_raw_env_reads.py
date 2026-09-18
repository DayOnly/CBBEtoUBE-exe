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

"""#env-through-helpers -- every boolean and numeric CBBE2UBE_* read goes
through src/envflags.py, and the raw reads that remain are the listed
paths and strings, no more and no fewer.

MEASURED 2026-09-16 before the change: 83 raw reads of 77 names in 15
modules bypassed the helpers -- 27 booleans, 44 numerics and 12 paths -- so
`flag_surface`, `flag_retirement` and `dead_gate_audit`, which parse only
`_flag(...)` / `_knob(...)`, could not see them (the retirement census printed
11 switches as NOT COUNTED), and the bypassed reads kept the old edge-value
spellings the helper module exists to retire (`!= "1"`, `== "1"`, any
non-empty string as true).

The scanner is a PARSE, not a grep: a read split across lines, `os.getenv`,
and an `_os` alias are all reads. It is negative-controlled: a copy of a
module with three planted raw reads must be flagged, or it proves nothing.
The allowlist is exact in both directions -- a raw read that is not listed
fails, and a listed name that is no longer read raw fails too, so the list
cannot outlive the reads it excuses.
"""
import ast
import importlib
import json
import subprocess
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analysis import flag_retirement, flag_surface   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

#: The raw reads that stay raw, with the reason: each is a PATH or a STRING
#: (a file, a folder, a tool location, a display name), which the two helpers
#: do not cover. A name rendered with `{…}` is an f-string read.
ALLOWLIST = {
    "CBBE2UBE_GLOW_LOG": "path of the glow debug log",
    "CBBE2UBE_RUN_LOG": "path of the run log, pinned by the window",
    "CBBE2UBE_EXCLUSIONS": "path of the exclusions file",
    "CBBE2UBE_STANDOFF_LOG": "path of the standoff audit log",
    "CBBE2UBE_STAGE_DUMP": "folder for stage dumps",
    "CBBE2UBE_CONFIG": "path of the settings file",
    "CBBE2UBE_BACK_DUMP_DISP": "a display-name filter for the back-residual dump",
    "CBBE2UBE_CBBE_BODY{…}": "path of a CBBE body reference, per weight",
    "CBBE2UBE_UBE_BODY{…}": "path of a UBE body reference, per weight",
    "CBBE2UBE_UBE_BODY": "path of the UBE body reference",
    "CBBE2UBE_TEXCONV": "path of the texconv tool",
    "CBBE2UBE_PAPYRUS_COMPILER": "path of the Papyrus compiler",
}

#: Switches the retirement census could not see before, now `_flag` bindings.
MIGRATED_SWITCHES = (
    "CBBE2UBE_NO_VANILLA_SWEEP", "CBBE2UBE_NO_WEIGHT_PARITY_CHECK",
    "CBBE2UBE_NO_BODYMATCH_SELECT", "CBBE2UBE_NO_CAST_CULL",
    "CBBE2UBE_NO_CHAIN_GUARD", "CBBE2UBE_NO_MIN_PUSH",
    "CBBE2UBE_NO_STANDOFF_AUDIT", "CBBE2UBE_NO_SELFINT_REPAIR",
    "CBBE2UBE_NO_BODY_MOTION_MATCH", "CBBE2UBE_NO_MERGE_DEDUP",
)


def _env_name(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{…}"
                       for v in node.values)
    return None


def _is_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id in ("os", "_os"))


def raw_env_reads(path: Path) -> list:
    """(line, name) for every raw CBBE2UBE_* read in one module: `os.environ.get`,
    `os.getenv`, `os.environ[...]` as a load, also through an `_os` alias."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            if f.attr == "get" and _is_environ(f.value) and node.args:
                name = _env_name(node.args[0])
            elif (f.attr == "getenv" and isinstance(f.value, ast.Name)
                  and f.value.id in ("os", "_os") and node.args):
                name = _env_name(node.args[0])
            else:
                continue
            if name and name.startswith("CBBE2UBE_"):
                out.append((node.lineno, name))
        elif (isinstance(node, ast.Subscript) and _is_environ(node.value)
              and isinstance(node.ctx, ast.Load)):
            name = _env_name(node.slice)
            if name and name.startswith("CBBE2UBE_"):
                out.append((node.lineno, name))
    return sorted(out)


def raw_env_reads_in_src(src_dir: Path = SRC) -> dict:
    """{name: [module:line, ...]} over every module but the helper itself."""
    found = {}
    for p in sorted(src_dir.glob("*.py")):
        if p.stem == "envflags":
            continue
        for line, name in raw_env_reads(p):
            found.setdefault(name, []).append(f"{p.name}:{line}")
    return found


def test_only_the_listed_paths_and_strings_are_read_raw():
    found = raw_env_reads_in_src()
    unlisted = {k: v for k, v in found.items() if k not in ALLOWLIST}
    assert not unlisted, (
        "a CBBE2UBE_* read bypasses src/envflags.py; route a boolean through "
        "_flag and a number through _knob, or list a path/string here with its "
        f"reason: {unlisted}")
    stale = sorted(set(ALLOWLIST) - set(found))
    assert not stale, f"listed but no longer read raw -- drop from the allowlist: {stale}"
    assert len(found) >= 10, f"only {len(found)} raw reads found -- the scan lost its population"


def test_the_scanner_catches_three_planted_reads(tmp_path):
    """The negative control: a plain read, a read through `os.getenv`, and a
    read through an `_os` alias split across lines."""
    copy = tmp_path / "fit_metrics.py"
    shutil.copy(SRC / "fit_metrics.py", copy)
    before = {n for _, n in raw_env_reads(copy)}
    copy.write_bytes(copy.read_bytes() + (
        b'\n\nimport os as _os\n'
        b'_P1 = os.environ.get("CBBE2UBE_PLANTED_ONE", "")\n'
        b'_P2 = os.getenv("CBBE2UBE_PLANTED_TWO")\n'
        b'_P3 = _os.environ.get(\n    "CBBE2UBE_PLANTED_THREE", "").strip()\n'
        b'_P4 = _os.environ["CBBE2UBE_PLANTED_FOUR"]\n'))
    after = {n for _, n in raw_env_reads(copy)}
    assert after - before == {"CBBE2UBE_PLANTED_ONE", "CBBE2UBE_PLANTED_TWO",
                              "CBBE2UBE_PLANTED_THREE", "CBBE2UBE_PLANTED_FOUR"}, after - before


def test_a_write_is_not_a_read():
    """The window sets CBBE2UBE_NO_PAUSE for its child; that is not a read."""
    p = SRC / "gui.py"
    assert 'os.environ["CBBE2UBE_NO_PAUSE"] = "1"' in p.read_text(encoding="utf-8")
    assert not any(n == "CBBE2UBE_NO_PAUSE" for _, n in raw_env_reads(p))


# --- the censuses see the migrated reads -------------------------------------------

def test_the_retirement_census_has_nothing_left_uncounted():
    """Before: 11 switches read by raw os.environ (10 real, and the window's
    WRITE of CBBE2UBE_NO_PAUSE mistaken for one)."""
    assert flag_retirement.raw_env_kill_switches() == []


def test_the_surface_census_sees_every_migrated_switch():
    seen = set()
    for row in flag_surface.declared_all():
        for v in (row.values() if isinstance(row, dict) else row):
            if isinstance(v, str) and v.startswith("CBBE2UBE_"):
                seen.add(v)
    missing = sorted(set(MIGRATED_SWITCHES) - seen)
    assert not missing, f"the surface census does not see: {missing}"


# --- the resolved defaults did not move ----------------------------------------------

#: (module, attribute, value the old raw read resolved to with nothing set).
DEFAULTS = [
    ("src.fit_metrics", "CAST_CULL", True), ("src.fit_metrics", "PUSH_ENABLED", True),
    ("src.fit_metrics", "CHAIN_GUARD", True), ("src.fit_metrics", "PASS_TRACE", False),
    ("src.fit_metrics", "SURVIVAL_TRACE", False), ("src.fit_metrics", "STANDOFF_TRACE", False),
    ("src.fit_metrics", "RAY_CHUNK", 512), ("src.fit_metrics", "CEIL_MEDIAN", 1.6),
    ("src.fit_metrics", "CEIL_P90", 2.2), ("src.fit_metrics", "PUSH_Z_LO", 86.0),
    ("src.fit_metrics", "PUSH_Z_HI", 102.0), ("src.fit_metrics", "PUSH_Y_MIN", -2.0),
    ("src.fit_metrics", "PUSH_RIM_MARGIN", 2.0), ("src.fit_metrics", "PUSH_MAX_REACH", 3.0),
    ("src.fit_metrics", "PUSH_ITERS", 8), ("src.fit_metrics", "PUSH_DAMP", 0.5),
    ("src.fit_metrics", "PUSH_KNN", 3), ("src.fit_metrics", "PUSH_MARGIN", 0.05),
    ("src.fit_metrics", "PUSH_MAX_TOTAL", 2.0), ("src.fit_metrics", "PUSH_REQ_CAP", 1.5),
    ("src.fit_metrics", "CHAIN_TOL", 0),
    ("src.nif_convert", "SELFINT_REPAIR", True), ("src.nif_convert", "_SELFINT_SMOOTH", 4),
    ("src.nif_convert", "_COINCIDENT_SKIN_TOL", 0.15), ("src.nif_convert", "_COINCIDENT_SKIN_GATE", 0.10),
    ("src.nif_convert", "_COINCIDENT_SKIN_MIN_SHARE", 0.75), ("src.nif_convert", "_RIGID_PART_GATE", 0.15),
    ("src.nif_convert", "_RIGID_PART_MIN_VERTS", 8), ("src.nif_convert", "_PART_PAIR_NEAR", 1.0),
    ("src.nif_convert", "_PART_PAIR_MARGIN", 0.10), ("src.nif_convert", "_AUTHOR_DEV_MARGIN", 0.15),
    ("src.nif_convert", "_AUTHOR_DEV_MIN_PALETTE", 0.5), ("src.nif_convert", "_LAYER_RIDE_K", 8),
    ("src.nif_convert", "_LAYER_RIDE_BARY_CAND", 6), ("src.nif_convert", "_LAYER_ORDER_SMOOTH", 2),
    ("src.nif_convert", "PANEL_RIGIDITY_MIN_VERTS", 24), ("src.nif_convert", "WARP_STANDOFF_SMOOTH_ITERS", 2),
    ("src.nif_convert", "WARP_DELTA_SMOOTH_ITERS", 0), ("src.nif_convert", "WARP_DELTA_SMOOTH_WEIGHT", 0.5),
    ("src.nif_convert", "SMP_ANTIPOKE_MAX_PUSH", 1.0),
    ("src.nif_convert_fitgeom", "ANTIPOKE_BUST_CLEAR", 1.0),
    ("src.nif_convert_layers", "_SEAM_WELD_TOL", 0.05), ("src.nif_convert_layers", "_LAYER_RIDE_MAX", 2.0),
    ("src.nif_convert_layers", "_LAYER_ORDER_NEAR", 2.0), ("src.nif_convert_layers", "_LAYER_ORDER_ITERS", 2),
    ("src.nif_convert_layers", "_GLOW_RIDE_MAX", 2.0),
    ("src.sliderset_gen", "_BODY_MOTION_MATCH", True), ("src.sliderset_gen", "_MATCH_NEAR", 4.0),
    ("src.sliderset_gen", "_MATCH_FAR", 10.0),
    ("src.ube_patcher", "MERGE_DEDUP_ARMAS", True),
]

_ENV_OF = {
    "CAST_CULL": "CBBE2UBE_NO_CAST_CULL", "PUSH_ENABLED": "CBBE2UBE_NO_MIN_PUSH",
    "CHAIN_GUARD": "CBBE2UBE_NO_CHAIN_GUARD", "SELFINT_REPAIR": "CBBE2UBE_NO_SELFINT_REPAIR",
    "_BODY_MOTION_MATCH": "CBBE2UBE_NO_BODY_MOTION_MATCH", "MERGE_DEDUP_ARMAS": "CBBE2UBE_NO_MERGE_DEDUP",
    "SMP_ANTIPOKE_MAX_PUSH": "CBBE2UBE_SMP_ANTIPOKE_PUSH", "ANTIPOKE_BUST_CLEAR": "CBBE2UBE_BUST_CLEAR",
    "WARP_STANDOFF_SMOOTH_ITERS": "CBBE2UBE_WARP_SMOOTH_ITERS",
    "WARP_DELTA_SMOOTH_ITERS": "CBBE2UBE_WARP_DELTA_SMOOTH",
    "WARP_DELTA_SMOOTH_WEIGHT": "CBBE2UBE_WARP_DELTA_SMOOTH_W",
    "CEIL_MEDIAN": "CBBE2UBE_STANDOFF_CEIL_MEDIAN", "CEIL_P90": "CBBE2UBE_STANDOFF_CEIL_P90",
}


def _env_for(attr: str) -> str:
    return _ENV_OF.get(attr, "CBBE2UBE_" + attr.lstrip("_"))


@pytest.fixture(scope="module")
def clean_defaults():
    """Every DEFAULTS attribute as a FRESH interpreter binds it with no
    CBBE2UBE_* variable set. Read in this process the table would answer for
    whatever the suite had done to the module first: another test's
    monkeypatch plus a reload leaves the binding flipped for everyone after it
    (measured 2026-09-16: `_BODY_MOTION_MATCH` read False once
    tests/test_body_motion_match.py had run), and the shell's own variables
    would turn rows into skips. One subprocess, one import each."""
    code = (
        "import importlib, json, sys\n"
        "rows = json.loads(sys.argv[1])\n"
        "out = {m + '.' + a: getattr(importlib.import_module(m), a) for m, a in rows}\n"
        "print('DEFAULTS ' + json.dumps(out))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run([sys.executable, "-c", code, json.dumps([[m, a] for m, a, _ in DEFAULTS])],
                       cwd=str(REPO), capture_output=True, text=True, env=env, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    line = next(l for l in reversed(r.stdout.splitlines()) if l.startswith("DEFAULTS "))
    return json.loads(line[len("DEFAULTS "):])


@pytest.mark.parametrize("module,attr,expected", DEFAULTS,
                         ids=[f"{m.split('.')[-1]}.{a}" for m, a, _ in DEFAULTS])
def test_the_resolved_default_is_what_the_raw_read_resolved_to(clean_defaults, module, attr, expected):
    got = clean_defaults[f"{module}.{attr}"]
    assert got == expected and type(got) is type(expected), (attr, got, expected)


def test_an_int_knob_keeps_its_cast_and_a_float_knob_its_type(monkeypatch):
    """A set value must come back as the type the old `int(...)`/`float(...)`
    call produced -- a dropped cast would hand `8.0` to a loop count."""
    from src import fit_metrics as fm, sliderset_gen as sg
    monkeypatch.setenv("CBBE2UBE_PUSH_ITERS", "9")
    monkeypatch.setenv("CBBE2UBE_RAY_CHUNK", "256")
    monkeypatch.setenv("CBBE2UBE_MATCH_NEAR", "3")
    try:
        importlib.reload(fm)
        importlib.reload(sg)
        assert fm.PUSH_ITERS == 9 and type(fm.PUSH_ITERS) is int
        assert fm.RAY_CHUNK == 256 and type(fm.RAY_CHUNK) is int
        assert sg._MATCH_NEAR == 3.0 and type(sg._MATCH_NEAR) is float
    finally:
        monkeypatch.delenv("CBBE2UBE_PUSH_ITERS", raising=False)
        monkeypatch.delenv("CBBE2UBE_RAY_CHUNK", raising=False)
        monkeypatch.delenv("CBBE2UBE_MATCH_NEAR", raising=False)
        importlib.reload(fm)
        importlib.reload(sg)
    assert fm.PUSH_ITERS == 8 and sg._MATCH_NEAR == 4.0
