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

"""A DOC MUST NOT TELL YOU TO RUN A TOOL THAT IS NOT IN THE REPO.

Three tools were found this way on 2026-09-06, each cited as standard practice
while living only inside a GITIGNORED scratchpad -- present on one machine and
nowhere else, so a fresh clone reads the instruction and finds nothing:

  * `morph_sweep`        -- half the consolidation plan's gate for A1/A2/A4
  * `damage_ledger`      -- the other half for A2/A4 (still not promoted; it
                            reads STAGE DUMPS, and the standing rule is to judge
                            the written NIF at the last stage)
  * `exe_parity_convert` -- cited by AUDIT_2026_09_01 as "rule 5", the standard
                            proof step, for finding after finding

That is a structural trap, not carelessness: the scratchpad is gitignored, so
nothing warns you. The same scan also found five references left behind by the
move into `scripts/analysis/` -- a reader following them gets "file not found".

EXISTENCE, not `git ls-files`. The working tree here is deliberately kept
uncommitted for long stretches, so index membership would fail for every new
tool and prove nothing. What matters to a reader is whether the file is there.

Scope is narrow on purpose -- an instruction to RUN something. Prose naming a
source module is not a claim that a runnable tool exists.
"""
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]

# `python -m scripts.analysis.foo`
MODULE = re.compile(r"python\s+-m\s+(scripts(?:\.[A-Za-z_][A-Za-z_0-9]*)+)")
# `scripts/analysis/foo.py`
SCRIPT = re.compile(r"(scripts/[A-Za-z_0-9/]+\.py)")

# Tools that are GONE and are named only as history. Each needs a reason: a
# doc recording what was once run is not a promise you can run it today.
KNOWN_HISTORICAL = {
    "scripts/diag_jiggle_batch.py":
        "deleted; named only by AUDIT_MAIN_HISTORY.md as past work",
    "scripts/fix_overlay_mod.py":
        "deleted; named only by AUDIT_MAIN_HISTORY.md as past work",
}


def _refs():
    """(doc, tool path) for every runnable reference in a doc."""
    for p in sorted((PROJ / "docs").rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        found = {m.group(1).replace(".", "/") + ".py"
                 for m in MODULE.finditer(text)}
        found |= {m.group(1) for m in SCRIPT.finditer(text)}
        for ref in sorted(found):
            yield p.relative_to(PROJ).as_posix(), ref


def test_every_runnable_tool_a_doc_names_exists():
    missing = [f"{doc} -> {ref}" for doc, ref in _refs()
               if ref not in KNOWN_HISTORICAL
               and not (PROJ / ref).is_file()]
    assert not missing, (
        "docs tell the reader to run tools that are not in the repo -- they may "
        "exist only in a gitignored scratchpad on one machine:\n  "
        + "\n  ".join(missing))


def test_no_runnable_tool_lives_only_in_a_gitignored_scratchpad():
    """THE TRAP ITSELF. A tool under a gitignored path is invisible to everyone
    else even though it runs fine here."""
    bad = []
    for doc, ref in _refs():
        p = PROJ / ref
        if p.is_file() and "scratchpad" in p.as_posix():
            bad.append(f"{doc} -> {ref}")
    assert not bad, ("a doc names a tool that lives in a scratchpad:\n  "
                     + "\n  ".join(bad))


def test_the_promoted_tools_are_present():
    """Named explicitly so a future move cannot quietly undo the promotion."""
    for name in ("scripts/analysis/morph_sweep.py",
                 "scripts/analysis/exe_parity_convert.py",
                 "scripts/analysis/acceptance.py",
                 "scripts/analysis/parity_convert.py"):
        assert (PROJ / name).is_file(), f"{name} is missing"


def test_the_known_list_has_no_stale_entries():
    """If a historical tool comes back, drop it from KNOWN rather than leaving a
    permanent exemption -- the same rule `test_no_undefined_names` uses."""
    back = [k for k in KNOWN_HISTORICAL if (PROJ / k).is_file()]
    assert not back, f"exists again, remove from KNOWN_HISTORICAL: {back}"


def test_the_guard_can_actually_fire():
    """The control. A doc naming a tool that does not exist must be caught."""
    text = "run `python -m scripts.analysis.no_such_tool` to check"
    refs = {m.group(1).replace(".", "/") + ".py" for m in MODULE.finditer(text)}
    assert refs == {"scripts/analysis/no_such_tool.py"}
    assert not (PROJ / "scripts/analysis/no_such_tool.py").is_file()
