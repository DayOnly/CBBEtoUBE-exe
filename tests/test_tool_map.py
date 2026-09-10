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

"""`docs/TOOL_MAP.md` must match the tools it indexes.

THE RULE THIS PROTECTS. "Check before hand-rolling a probe" is only actionable
if the index it points at exists and is complete. It was pointed at
`docs/PASS_MAP.md`, which indexes conversion passes and names no tool at all, so
a reader following the rule found nothing and would reasonably conclude nothing
existed. A cross-reference the reader cannot follow still reads as corroboration
-- the class `project_comment_audit_2026_08_17` found six of.

So the index is generated, and this regenerates it and compares. If this fails,
run `python scripts/tool_map.py` and read the diff: a changed tool map means a
measurement tool was added, removed, or changed what it reads.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import repo_hygiene  # noqa: E402
from scripts import tool_map  # noqa: E402


def test_tool_map_is_current():
    want = tool_map.render(tool_map.scan())
    have = tool_map.OUT.read_text(encoding="utf-8")
    assert have == want, (
        "docs/TOOL_MAP.md is out of date. Regenerate with "
        "`python scripts/tool_map.py` and read the diff -- a changed tool map "
        "means the measurement surface changed.")


def test_check_mode_exits_zero_when_current():
    """The --check path is what a hook would call, so it is exercised too."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "tool_map.py"),
                        "--check"], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_index_is_not_empty():
    """A POPULATION FLOOR on the index itself.

    This file's whole subject is tools that cannot tell "nothing is wrong" from
    "I measured nothing", so the index must not be able to make that mistake
    about itself. If `scan()` silently stopped finding tools -- a moved
    directory, a glob typo -- every other test here would still pass against an
    empty map."""
    entries = tool_map.scan()
    assert len(entries) >= 40, (
        f"only {len(entries)} tools found; the walk has probably stopped "
        f"seeing scripts/analysis")


def test_every_tracked_analysis_tool_is_indexed():
    """No tool may go missing from the index. Silence is the failure mode."""
    on_disk = {p.relative_to(REPO).as_posix()
               for p in (REPO / "scripts" / "analysis").glob("*.py")}
    on_disk -= tool_map.NOT_A_TOOL | tool_map.DOC_GENERATORS
    indexed = {e["path"] for e in tool_map.scan()}
    missing = sorted(on_disk - indexed)
    assert not missing, f"analysis tools absent from the index: {missing}"


def test_the_excluded_generators_are_still_named():
    """Excluding a script must not make it invisible.

    `pass_map.py` and `tool_map.py` are skipped as doc generators rather than
    measurements. An exclusion nobody can see is how a tool disappears from the
    record entirely, which is the defect this index exists to fix -- so the
    rendered map has to name them."""
    doc = tool_map.OUT.read_text(encoding="utf-8")
    for p in tool_map.DOC_GENERATORS:
        assert p in doc, f"{p} is excluded but not named in the map"


def test_the_generator_does_not_classify_itself():
    """THE BUG THIS CAUGHT ON ITS FIRST RUN.

    `READS` is a table of substrings searched over each tool's whole source, so
    the file DEFINING that table matches every marker in it -- the first render
    filed `tool_map.py` under "reads the run log". Excluding the generators
    fixes it; this pins the fix, because the same trap returns the moment
    someone re-adds a generator to the walk."""
    entries = tool_map.scan()
    assert not [e for e in entries if e["path"] in tool_map.DOC_GENERATORS]


def test_a_boilerplate_disclaimer_is_not_a_purpose():
    """THE THIRD BUG THE FIRST RENDER SHIPPED.

    Six tools open with the house disclaimer, so the index described six
    different tools as "DEVELOPMENT TOOL -- not part of the shipped converter"
    -- an index that says nothing about six of its entries. The disclaimer is
    deliberate and stays in those files; knowing it is not a purpose belongs
    here.

    Both shapes are pinned because conflating them is what broke the first fix:
    a PREFIX can carry the real purpose after it, a WHOLE-PARAGRAPH disclaimer
    never does, and stripping the latter's opening words left three tools
    described as ", which PyInstaller does not bundle"."""
    carries_purpose = ("DEVELOPMENT TOOL -- golden-output regression harness.\n"
                       "\nMore prose here.\n")
    assert tool_map._purpose(carries_purpose) == "golden-output regression harness"

    pure_boilerplate = (
        "DEVELOPMENT TOOL -- not part of the shipped converter.\n"
        "\n"
        "Lives in `scripts/`, which PyInstaller does not bundle, and has no "
        "GUI setting or CLI surface in the exe.\n"
        "\n"
        "Sweep the output and record one row per shape.\n")
    assert tool_map._purpose(pure_boilerplate) == \
        "Sweep the output and record one row per shape"

    # And the real files must not regress to boilerplate either.
    bad = [e["path"] for e in tool_map.scan()
           if "DEVELOPMENT TOOL" in e["purpose"]
           or "PyInstaller" in e["purpose"]
           or e["purpose"].startswith(",")]
    assert not bad, f"these tools are described by boilerplate, not purpose: {bad}"


def test_a_bool_return_is_not_an_exit_code():
    """THE SECOND BUG THE FIRST RENDER SHIPPED.

    `isinstance(True, int)` is True in Python, and the extractor took a return
    from ANY function, so `fit_audit.py`'s helper `return True  # can't tell`
    was published as its exit-code contract: `| fit_audit.py | True | ...`.
    A gate column that prints a boolean invites chaining a tool that has no
    gate at all."""
    import ast
    tree = ast.parse(
        "import sys\n"
        "def helper():\n"
        "    return True\n"
        "def other():\n"
        "    return 7\n"          # not main(): not a contract either
        "def main():\n"
        "    if helper():\n"
        "        return 3\n"
        "    sys.exit(2)\n")
    assert tool_map._exits(tree) == [2, 3]


def test_render_changes_when_a_tool_changes():
    """A generator that ignored its input would pass every test above.

    Prove it can fire before believing any "current" it reports."""
    entries = tool_map.scan()
    base = tool_map.render(entries)
    mutated = [dict(e) for e in entries]
    mutated[0]["purpose"] = "MUTATED PURPOSE FOR THE DETECTOR TEST"
    assert tool_map.render(mutated) != base, (
        "the generator ignored a real change to a tool's description")


def test_the_rendered_map_carries_no_local_paths_or_emails():
    """This repo is public and has already shipped both by accident.

    The map renders other files' docstrings verbatim, so it is exactly the kind
    of generated content that can launder a local path into a commit. The
    generator refuses to write when this trips; the test states the rule."""
    bad = repo_hygiene.scan_text("docs/TOOL_MAP.md",
                                 tool_map.render(tool_map.scan()))
    assert not bad, "the tool map would leak local content: " + "; ".join(bad)


def test_the_floor_detector_finds_the_known_cases():
    """`scripts/tool_audit.py` names three tools that DO assert a population
    floor. If the `floor` column misses them it is not measuring what the
    column header claims, and a reader would under-trust three good tools."""
    by_path = {e["path"]: e for e in tool_map.scan()}
    for p in ("scripts/analysis/survival_report.py",
              "scripts/analysis/survival_sweep.py",
              "scripts/analysis/snugness_census.py"):
        assert by_path[p]["floor"], f"{p} asserts a floor but was not detected"
