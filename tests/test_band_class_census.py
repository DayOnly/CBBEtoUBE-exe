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

"""`band_class_census.py` -- the bust/butt CLASS census, promoted 2026-09-09.

WHY IT WAS PROMOTED. It is the only tool that produces the "clean at bind,
clipping morphed" class and its chord / follow-gap / physics-cloth split, and it
sat in a GITIGNORED scratchpad for three sessions while worklogs cited it as if
it were toolkit -- the exact failure `feedback_gitignored_scratchpad_tools`
names. `pack_census.py` cannot stand in for it: no band selection, no morph, no
ray, and it never labels a convert path.

WHAT BLOCKED IT, and what this pins:
  * `_REPO` was one machine's absolute path. Now derived from `__file__`;
    `test_analysis_repo_root` covers that for every analysis script, and
    `test_public_repo_hygiene` covers the path literal.
  * `--control` hardcoded a pack path AND a mod name. It now takes
    `--control-nif`, or walks the pack for pieces that ACTUALLY SCORE.
  * the floor message split "I measured nothing" across a line break, so the
    GENERATED tool map advertised the tool as UNFLOORED while it does exit 2 on
    exactly that condition. That is worse than an unfloored tool, because the
    index says someone checked.

EQUIVALENCE WAS PROVEN BEFORE PROMOTION, not assumed: old and new run over the
same 150-candidate sample produced 49 scored rows each, **0 rows differing**,
and identical summaries apart from the output filename.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import tool_map  # noqa: E402

TOOL = REPO / "scripts" / "analysis" / "band_class_census.py"
SRC = TOOL.read_text(encoding="utf-8")


def test_the_tool_is_tracked_and_readable():
    """0/0 is not a pass: if the file moved, every assertion below is vacuous."""
    assert TOOL.is_file(), "band_class_census.py is not where the map says"
    assert len(SRC.splitlines()) > 300, "suspiciously small: did it get truncated?"


def test_it_declares_a_floor_the_generated_map_can_see():
    """THE BUG THIS PINS. `tool_map._has_floor` probes the SOURCE TEXT for a
    contiguous marker. The floor message used to read

        f"... from 'I measured "
        f"nothing'"

    so the marker spanned a line break, `_has_floor` returned False, and
    `docs/TOOL_MAP.md` printed this census as having no population floor -- a
    map entry asserting the opposite of the truth. Reflowed, not reworded."""
    assert tool_map._has_floor(SRC), (
        "the generated tool map will advertise this census as UNFLOORED. It "
        "does have a floor (--floor, exit 2); the marker string is split "
        "across lines again")


def test_the_floor_is_a_real_refusal_not_just_a_message():
    """A message without a non-zero exit is a warning, not a floor."""
    assert "--floor" in SRC
    tree = ast.parse(SRC)
    codes = {n.value.value for n in ast.walk(tree)
             if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
             and isinstance(n.value.value, int)}
    assert 2 in codes, "no `return 2` -- the floor cannot gate anything"


def test_the_class_thresholds_are_the_documented_ones():
    """The class and its three-way split are the tool's whole product. A silent
    threshold change would move every share it has ever reported, and the
    numbers in the worklogs would stop meaning what they say."""
    assert 'r["bind"] <= 0.05 and r["morph"] > 0.05' in SRC, (
        "the CLASS definition (clean at bind, clipping morphed) moved")
    assert 'r["follow"] > 0.3 and r["chain_share"] < 0.3' in SRC, (
        "the CHORD-candidate split moved")
    assert 'r["follow"] <= 0.3' in SRC, "the follow-gap split moved"
    assert 'r["follow"] > 0.3 and r["chain_share"] >= 0.3' in SRC, (
        "the physics-cloth split moved")


def test_the_threshold_assertions_are_not_tautologies():
    """MUTATION CONTROL. The four assertions above are substring tests, which
    pass trivially if the substring is common. Assert a NEAR MISS is absent, so
    they are discriminating against the real text rather than agreeing with
    anything."""
    assert 'r["bind"] <= 0.06 and r["morph"] > 0.05' not in SRC
    assert 'r["follow"] > 0.4 and r["chain_share"] < 0.3' not in SRC


def test_the_control_refuses_to_reproduce_nothing():
    """A control that SKIPS every arm prints in the same shape as one that
    reproduced something. Taking simply the first two files in the pack found
    two that both skipped `band_not_covered` -- verification-looking output
    that verified nothing. The selection must filter on a scored row, and the
    tool must fail rather than print an empty control."""
    assert "if row is not None:" in SRC, (
        "the control's pack-walk no longer filters on a scored row")
    assert "proves nothing" in SRC, (
        "the control no longer refuses when it cannot reproduce an arm")
    assert "nothing was reproduced" in SRC, (
        "the post-hoc check that at least one arm scored is gone")


def test_the_control_takes_arms_from_the_caller():
    """`--control-nif` is what lets a caller reproduce a SPECIFIC piece without
    the tool knowing any local path."""
    assert '"--control-nif"' in SRC


def test_it_is_indexed_with_a_gate():
    """`docs/TOOL_MAP.md` is the index the standing rule points readers at, and
    a tool that is not in it is a tool nobody will find -- which is how this one
    ended up re-derived from a scratchpad three times."""
    rows = [r for r in tool_map.scan()
            if r["path"].endswith("band_class_census.py")]
    assert len(rows) == 1, "census not indexed exactly once: %d" % len(rows)
    assert rows[0]["exits"], "indexed with no gate -- it exits 2 and 3"
    assert rows[0]["floor"], "indexed as unfloored"
