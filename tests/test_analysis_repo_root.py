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

"""Every analysis script must put the REPO ROOT on sys.path, not `scripts/`.

THE BUG THIS PINS. The census tooling was moved into `scripts/analysis/`, one
directory deeper, but `_REPO = Path(__file__).resolve().parent.parent` was left
alone -- so it pointed at `scripts/`, which owns neither `src/` nor `.pynifly/`.
Twenty of twenty-six scripts were affected. They did not fail loudly: they only
imported when the repo root HAPPENED to be on sys.path already (run as `-m` from
the root, or with PYTHONPATH set), and raised ModuleNotFoundError otherwise. A
harness that works from one directory and not another is the kind of thing that
gets diagnosed as "the tool is broken" for an hour.

Checked by PARSING, not importing: importing every analysis module would drag in
pynifly, scipy and a mods-root scan, which is exactly the cost this suite avoids.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ANALYSIS = _ROOT / "scripts" / "analysis"
_PAT = re.compile(
    r"^_REPO(?:_ROOT)?\s*=\s*Path\(__file__\)\.resolve\(\)((?:\.parent)+)\s*$",
    re.MULTILINE)


def _scripts_declaring_repo():
    out = []
    for p in sorted(_ANALYSIS.glob("*.py")):
        m = _PAT.search(p.read_text(encoding="utf-8"))
        if m:
            out.append((p, m.group(1).count(".parent")))
    return out


def test_some_scripts_declare_a_repo_root():
    """Control: if the pattern stops matching, every assertion below passes
    vacuously and this file would silently stop testing anything."""
    found = _scripts_declaring_repo()
    assert len(found) >= 15, (
        f"only {len(found)} scripts matched the _REPO pattern -- the pattern has "
        f"drifted and the checks below would be vacuous")


_NEEDS_ROOT = re.compile(
    r"^\s*(?:import\s+src\b|from\s+src\b|import\s+pyn\b|from\s+pyn\b)",
    re.MULTILINE)


def test_every_script_that_imports_src_declares_the_canonical_root():
    """THE FILTER IS THE POPULATION -- 2026-08-18 audit fix.

    The parametrized check below runs only over scripts that MATCH the _REPO
    pattern, so a script with a WRONG or differently-spelled root declaration
    was invisible to it: six scripts imported `src`/`pyn` off a root that
    pointed at `scripts/` (one of them then built
    `scripts/scripts/convert_one_armor.py` and could never have run). This
    test closes the gap from the other side: every script whose imports NEED
    the repo root must declare it in the one canonical, checkable form.
    """
    matched = {p for p, _ in _scripts_declaring_repo()}
    missing = []
    for p in sorted(_ANALYSIS.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if _NEEDS_ROOT.search(text) and p not in matched:
            missing.append(p.name)
    assert not missing, (
        "these scripts import src/pyn but do not declare the canonical "
        "`_REPO = Path(__file__).resolve().parent...` root (so the root check "
        "cannot see them, and a wrong root fails only at runtime): "
        + ", ".join(missing))


@pytest.mark.parametrize("path,levels", _scripts_declaring_repo(),
                         ids=lambda v: v.name if isinstance(v, Path) else str(v))
def test_repo_root_owns_src_and_pynifly(path, levels):
    resolved = path.resolve()
    for _ in range(levels):
        resolved = resolved.parent
    assert (resolved / "src").is_dir(), (
        f"{path.name}: _REPO resolves to {resolved}, which has no src/ -- "
        f"`import src.nif_convert` only works if the root is already on sys.path")
    assert (resolved / ".pynifly").is_dir(), (
        f"{path.name}: _REPO resolves to {resolved}, which has no .pynifly/")
    assert resolved == _ROOT, (
        f"{path.name}: _REPO is {resolved}, expected the repo root {_ROOT}")
