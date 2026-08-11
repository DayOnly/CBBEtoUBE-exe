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

"""#layer-group-canonical -- `_0` and `_1` must agree on WHICH shapes stack.

`#layer-follow-divergence` groups shapes by geometry. `_0` and `_1` are the same
garment fitted to different bodies, so a coverage fraction sitting near the
threshold flips between them.

MEASURED over a full reconvert: of 812 pieces that form a stack, **51 (6.3%)
grouped differently at `_0` than at `_1`** -- one guard cuirass partitioned
(0,1,2)+(3,4,5) at `_1` and (0,1,3,4,5) at `_0`. The engine MORPHS BETWEEN the
two meshes, so skinning them on different groupings is a per-weight leak of
exactly the kind the postflight parity check exists to catch.

THE SPLIT THAT FIXES IT. Two different questions were being answered from one
source of truth:
  * WHICH shapes stack -- a property of the GARMENT. Decided once, on the `_1`
    source, and reused by both weights.
  * WHERE their shared anchor sits -- a property of the BODY WEIGHT. Still
    resolved per file, because that one genuinely differs.

Source-side is also the more defensible frame: whether two layers overlap is
authored, not a consequence of which body they were fitted to.
"""
import inspect

from src import nif_convert as nc


def test_both_weights_resolve_to_the_same_canonical_file(tmp_path):
    """The whole fix in one assertion: `_0` and `_1` must read the SAME file."""
    seen = []

    class _FakeNif:
        shapes = []

    def _fake_pyn():
        class _P:
            @staticmethod
            def NifFile(filepath=None):
                seen.append(str(filepath))
                return _FakeNif()
        return _P

    (tmp_path / "robe_0.nif").write_bytes(b"x")
    (tmp_path / "robe_1.nif").write_bytes(b"x")
    orig = nc._pynifly
    nc._pynifly = _fake_pyn
    nc._STACK_NAME_GROUP_CACHE.clear()
    try:
        nc._canonical_stack_name_groups(tmp_path / "robe_0.nif", set())
        nc._STACK_NAME_GROUP_CACHE.clear()
        nc._canonical_stack_name_groups(tmp_path / "robe_1.nif", set())
    finally:
        nc._pynifly = orig
    assert len(seen) == 2
    assert seen[0] == seen[1], (
        f"the two weights read different files: {seen}")
    assert seen[0].endswith("robe_1.nif"), "the `_1` weight is the canonical one"


def test_no_source_falls_back_rather_than_grouping_nothing():
    """Returning an empty list would silently disable the guard on every piece
    with no resolvable source. None means "decide from the file in hand"."""
    assert nc._canonical_stack_name_groups(None, set()) is None
    assert nc._canonical_stack_name_groups(
        "Z:/definitely/not/here_1.nif", set()) is None


def test_the_caller_distinguishes_None_from_empty():
    """`if _names is not None` -- an empty list is a real answer ("this garment
    has no stacks"), None is "could not decide". Written as `if _names:` the
    two collapse and a stackless piece would re-derive in-hand every time."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "if _names is not None" in src, (
        "empty-vs-None must be distinguished, or the fallback fires on every "
        "garment that legitimately has no stacked group")


def test_dst_groups_need_two_MEMBERS_PRESENT_HERE():
    """A name the canonical weight grouped may be absent from this file. A
    group down to one member is not a stack here and must not be planned."""
    class _S:
        def __init__(self, name, n=8):
            self.name = name
            self.verts = [(float(i), 0.0, 0.0) for i in range(n)]
    orig_w, orig_g = nc._verts_skin_to_world, nc._shape_global_to_skin
    nc._verts_skin_to_world = lambda sv, xf: __import__("numpy").asarray(sv, float)
    nc._shape_global_to_skin = lambda s: None
    try:
        got = nc._dst_groups_for_names(
            [_S("a"), _S("b")], [{"a", "b"}, {"a", "missing"}], set())
    finally:
        nc._verts_skin_to_world, nc._shape_global_to_skin = orig_w, orig_g
    assert len(got) == 1, "the group with only one present member must be dropped"
    assert {m[0] for m in got[0]} == {"a", "b"}


def test_excluded_names_never_enter_a_rebuilt_group():
    """Colliders and injected body parts are excluded when the canonical set is
    built; the rebuild must honour the same exclusion, or one path re-admits
    what the other refused."""
    class _S:
        def __init__(self, name):
            self.name = name
            self.verts = [(0.0, 0.0, 0.0)] * 4
    orig_w, orig_g = nc._verts_skin_to_world, nc._shape_global_to_skin
    nc._verts_skin_to_world = lambda sv, xf: __import__("numpy").asarray(sv, float)
    nc._shape_global_to_skin = lambda s: None
    try:
        got = nc._dst_groups_for_names(
            [_S("a"), _S("ColBody")], [{"a", "ColBody"}], {"ColBody"})
    finally:
        nc._verts_skin_to_world, nc._shape_global_to_skin = orig_w, orig_g
    assert got == [], "an excluded shape must not be rebuilt into a group"


def test_the_anchor_is_still_resolved_PER_WEIGHT():
    """Only the GROUPING is canonical. The anchor must keep coming from the
    shapes in hand -- it is a position on THIS body, and freezing it to one
    weight would put every `_0` anchor on the `_1` body."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i = src.index("_dst_groups_for_names(")
    assert "nf.shapes" in src[i:i + 200], (
        "the rebuilt groups must carry THIS file's shapes, not the source's")
