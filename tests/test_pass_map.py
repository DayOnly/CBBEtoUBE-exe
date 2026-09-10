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

"""`docs/PASS_MAP.md` must match the code it describes.

A map of what runs when is only worth having if it cannot go stale, and this
project has already paid for the opposite: comments advertising an "adaptive-K"
design that no longer existed seeded a whole dead line of work
(`project_comment_audit_2026_08_17`). So the map is generated and this test
regenerates it and compares.

If this fails, the fix is `python scripts/pass_map.py` -- and then READ the
diff, because a changed pass map means the conversion order changed.
"""
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import pass_map  # noqa: E402


def test_pass_map_is_current():
    text = pass_map.SRC.read_text(encoding="utf-8")
    want = pass_map.render(text)
    have = pass_map.OUT.read_text(encoding="utf-8")
    assert have == want, (
        "docs/PASS_MAP.md is out of date. Regenerate with "
        "`python scripts/pass_map.py` and read the diff -- a changed pass map "
        "means the conversion order changed.")


def test_check_mode_exits_zero_when_current():
    """The --check path is what a hook would call, so it is exercised too."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "pass_map.py"),
                        "--check"], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr


def test_detector_can_still_fire():
    """A checker that reports 'current' is the kind most likely to be broken.

    Feed the generator a MUTATED source and assert the output changes. Without
    this, a render() that silently returned a constant would pass every test
    above ([[feedback_method_traps]]: prove it can fire before believing the
    zero)."""
    text = pass_map.SRC.read_text(encoding="utf-8")
    base = pass_map.render(text)
    mutated = text.replace("_stage_p1('warp', snapped)",
                           "_stage_p1('WARP_MUTANT', snapped)", 1)
    assert mutated != text, "mutation target not found -- update this test"
    assert pass_map.render(mutated) != base, (
        "the generator ignored a real change to the pass chain")


def test_both_entry_points_are_covered():
    # POPULATION FLOOR. Every assertion below is inside the loop, so an empty
    # (or shrunken) `ENTRIES` would make this pass while checking nothing --
    # "0/0 is not a pass", applied to the test itself. The name says BOTH.
    assert len(pass_map.ENTRIES) >= 2, (
        f"ENTRIES has {len(pass_map.ENTRIES)} entry point(s); the per-entry "
        "assertions below would be vacuous")
    tree = ast.parse(pass_map.SRC.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for entry in pass_map.ENTRIES:
        assert entry in names, f"{entry} no longer exists in nif_convert.py"
        assert f"## `{entry}`" in pass_map.OUT.read_text(encoding="utf-8")


def test_traced_stage_labels_match_the_code():
    """The Stage column must list exactly the labels the code checkpoints.

    This is the column the survival trace is read against, so a drift here
    would mis-name a measured pass -- the `sNN_` index trap in a different
    costume."""
    text = pass_map.SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    in_code = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lbl = pass_map.stage_label(node, text)
        if lbl:
            in_code.add(lbl)
    doc = pass_map.OUT.read_text(encoding="utf-8")
    for lbl in sorted(in_code):
        assert f"**{lbl}**" in doc, f"stage {lbl!r} missing from the pass map"
