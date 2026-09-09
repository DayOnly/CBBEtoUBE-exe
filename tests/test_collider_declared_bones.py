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

"""#collider-declared-bones on the bust-split clone.

A shape the piece's physics XML REGISTERS must not carry a bone that XML never
DECLARES -- FSMP cannot resolve the influence and the piece free-falls. The bust
split CREATES such a shape by cloning a garment, so every undeclared bone on the
clone is ours.

THE BUG THESE TESTS EXIST FOR is not the redirect itself but WHERE THE
DECLARATIONS ARE READ FROM. Bust-split pass 1 runs BEFORE the physics finalize
installs the output XML, so reading the DESTINATION pointer returns nothing and
the whole repair silently no-ops -- measured: it reported
`split_col_declared_bones_UNCHECKED` on every piece it was meant to fix, while
looking exactly like "there was nothing to do".
"""
import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))

from src import nif_convert as nc  # noqa: E402

from tests import _converter_sources as _cs  # noqa: E402
SRC = _cs.whole_text()          # every declared converter module (the bust split moved these)


def _func(name):
    tree = ast.parse(SRC)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} no longer exists")


def _calls_in(name):
    out = set()
    for n in ast.walk(_func(name)):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name)
                    else getattr(f, "attr", None))
    return out


def test_default_is_on():
    """A safety invariant ships ON. If this flips, a registered collider can
    carry an unresolvable bone again."""
    assert nc.SPLIT_COL_DECLARED_BONES is True


def test_split_reads_the_shared_resolution_not_the_destination_alone():
    """THE REGRESSION GUARD. `_split_bust_collider_shape` must resolve the XML
    through `_bust_split_xml_text`, which prefers the AUTHORED copy.

    Calling `_read_source_hdt_xml_text(dst_path)` directly here is the bug: at
    pass-1 time the destination XML does not exist yet, so the declared set comes
    back empty and every clone is cloned unrepaired."""
    calls = _calls_in("_split_bust_collider_shape")
    assert "_bust_split_xml_text" in calls, (
        "the bust split no longer resolves its XML through the shared helper; "
        "reading the destination alone silently disables the repair")


def test_candidates_and_redirect_share_one_resolution():
    """Both consumers must use the SAME helper. Two copies of the resolution is
    how one side silently sees a different XML than the other -- the duplicate
    -list mistake `repo_hygiene.should_scan` exists to prevent."""
    assert "_bust_split_xml_text" in _calls_in("_bust_split_candidates")
    assert "_bust_split_xml_text" in _calls_in("_split_bust_collider_shape")


def test_xml_text_prefers_authored_over_destination(monkeypatch, tmp_path):
    authored = tmp_path / "authored.xml"
    authored.write_text('<a><bone name="NPC L UpperArm [LUar]"/></a>',
                        encoding="utf-8")
    _cs.patch(monkeypatch, "_read_source_hdt_xml_disk", lambda p: authored)
    _cs.patch(monkeypatch, "_read_source_hdt_xml_text",
                        lambda p, nif=None: "<DESTINATION/>")
    monkeypatch.setattr(nc, "CHAIN_TO_SOFTBODY", False)
    got = nc._bust_split_xml_text(tmp_path / "x_1.nif", None,
                                  src_path=tmp_path / "src_1.nif")
    assert "NPC L UpperArm" in got
    assert "DESTINATION" not in got


def test_xml_text_falls_back_to_destination_when_no_authored(monkeypatch,
                                                             tmp_path):
    """The fallback must survive: a piece whose XML resolves only from the
    output side must still be checkable."""
    _cs.patch(monkeypatch, "_read_source_hdt_xml_disk", lambda p: None)
    _cs.patch(monkeypatch, "_read_source_hdt_xml_text",
                        lambda p, nif=None: "<DESTINATION/>")
    monkeypatch.setattr(nc, "CHAIN_TO_SOFTBODY", False)
    got = nc._bust_split_xml_text(tmp_path / "x_1.nif", None,
                                  src_path=tmp_path / "src_1.nif")
    assert got == "<DESTINATION/>"


def test_ancestor_walk_logic(monkeypatch):
    """The WALK itself, on an injected parent map.

    Separate from the real-skeleton test below, which skips where MO2 is not
    available -- and a test that only ever skips proves nothing. This one always
    runs. It does NOT pretend to say anything about real bone names; it says the
    walk climbs, stops at the first declared-AND-available ancestor, and gives up
    rather than looping.
    """
    chain = {"finger": "hand", "hand": "forearm", "forearm": "upperarm",
             "upperarm": None}
    _cs.patch(monkeypatch, "_actor_skeleton_bone_parents", lambda: chain)
    f = nc._nearest_declared_ancestor
    assert f("finger", {"hand"}, {"hand"}) == "hand"          # nearest wins
    assert f("finger", {"upperarm"}, {"upperarm"}) == "upperarm"   # climbs past
    assert f("finger", {"hand"}, set()) is None               # not weightable
    assert f("finger", set(), set()) is None                  # nothing declared
    # A cycle must terminate, not hang: the walk is `seen`-guarded.
    _cs.patch(monkeypatch, "_actor_skeleton_bone_parents",
                        lambda: {"a": "b", "b": "a"})
    assert f("a", {"zzz"}, {"zzz"}) is None


def test_nearest_declared_ancestor_relabels_and_declines():
    """The two outcomes the fix depends on, against the REAL actor skeleton.

    Skipped rather than faked when the skeleton is unavailable: a fake parent map
    would make this pass while proving nothing about the bones that actually
    occur ([[feedback_method_traps]] -- a fake can make an assertion vacuous)."""
    parents = nc._actor_skeleton_bone_parents()
    if len(parents) < 100:
        pytest.skip(
            "actor skeleton not loaded -- set CBBE2UBE_SKELETON_NIF to a "
            "skeleton_female.nif (and CBBE2UBE_MO2_INI to the live instance) "
            "and this test RUNS AND PASSES. Verified 2026-09-06: the bare "
            "suite skips it, the gate environment does not. A skip message "
            "that does not say how to un-skip becomes permanent.")
    hand = "NPC L Hand [LHnd]"
    finger = "NPC L Finger00 [LF00]"
    if parents.get(finger) is None:
        pytest.skip("this skeleton does not carry the finger bone")
    # RELABEL: a finger on a shape that also has the hand redirects to it.
    assert nc._nearest_declared_ancestor(finger, {hand}, {hand}) == hand
    # DECLINE: nothing declared anywhere up the chain -> None, caller declines.
    assert nc._nearest_declared_ancestor(finger, {"NoSuchBone"},
                                         {"NoSuchBone"}) is None
    # AVAILABILITY IS PER SHAPE: declared but not weightable on this shape is
    # still a decline. Getting this wrong over-reports what can be repaired.
    assert nc._nearest_declared_ancestor(finger, {hand}, set()) is None
