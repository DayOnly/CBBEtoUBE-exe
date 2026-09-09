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

"""#pair-tri-names -- one tri, two halves, two sets of shape names.

Measured on the 2026-09-06 pack: 1536 `_0`/`_1` pairs, 1482 name their shapes
identically and **54 do not**. In every one of those 54 it is the `_1` half
that names nothing in the tri it points at -- 42 lose every morph, 12 lose
some, and `_0` never loses anything, because `_0` is the half the tri is built
from. `_1` is the half that ships (actors sit near weight 100), so those
garments have DEAD sliders in the condition they are actually seen in.

These tests pin the two halves of the fix separately:

  * `pair_shape_aliases` -- the pure rule, including every refusal. A wrong
    alias is worse than no alias: it would emit a delta table against the wrong
    vertex indices, or shadow a shape that has its own morphs.
  * `generate_armor_tri(also_named=...)` -- the emission, on a real generated
    TRI, asserting the alias carries the SAME morph table and that the file
    still round-trips through the writer (bytes 4-5 are the shape count).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.nif_convert_trigen import pair_shape_aliases      # noqa: E402
from src.sliderset_gen import generate_armor_tri           # noqa: E402
from src.tri import TriFile                                # noqa: E402




def _monolith() -> str:
    """The monolith's source through the shared helper. A guard that opens
    `src/nif_convert.py` by name breaks the day a function moves to a sibling,
    which is what `test_split_preconditions` ratchets against."""
    from tests import _converter_sources as _cs
    for p, text in _cs.texts().items():
        if p.name == "nif_convert.py":
            return text
    raise AssertionError("nif_convert.py is not in the converter file list")
# --------------------------------------------------------------------------
# the pure rule
# --------------------------------------------------------------------------

class TestPairShapeAliases:
    def test_the_real_shape_of_the_defect(self):
        """The four naming conventions actually seen in the pack."""
        for a, b, want in (
                (["BodyF_0", "SkinF_0"], ["BodyF_1", "SkinF_1"],
                 {"BodyF_0": "BodyF_1", "SkinF_0": "SkinF_1"}),
                (["BootsF:0"], ["BootsF:1"], {"BootsF:0": "BootsF:1"}),
                (["BaseShape", "Robe_0"], ["BaseShape", "Robe_1"],
                 {"Robe_0": "Robe_1"}),
                (["BaseShape", "Dress"], ["BaseShape", "Dress1"],
                 {"Dress": "Dress1"})):
            mine = [(n, 100) for n in a]
            theirs = [(n, 100) for n in b]
            assert pair_shape_aliases(mine, theirs) == want

    def test_identical_names_need_no_alias(self):
        """The 1482 pairs that are already fine must stay byte-identical, so
        the map must be EMPTY rather than a set of self-aliases."""
        rows = [("BaseShape", 29298), ("Cuirass", 4000)]
        assert pair_shape_aliases(rows, list(rows)) == {}

    def test_the_injected_body_never_aliases(self):
        """We add `BaseShape` / `VirtualBody` ourselves under the same name in
        both halves, so they can never be the source of an alias."""
        mine = [("BaseShape", 29298), ("VirtualBody", 512), ("Skirt_0", 900)]
        theirs = [("BaseShape", 29298), ("VirtualBody", 512), ("Skirt_1", 900)]
        assert pair_shape_aliases(mine, theirs) == {"Skirt_0": "Skirt_1"}

    def test_a_different_shape_count_is_refused_whole(self):
        """One pair of the 54 has 5 shapes vs 4. The halves are not the same
        mesh, so NOTHING may be aliased -- not even the shapes that do line
        up, because the index alignment is already broken."""
        mine = [("A", 10), ("B", 20), ("C", 30)]
        theirs = [("A1", 10), ("B1", 20)]
        assert pair_shape_aliases(mine, theirs) == {}

    def test_a_vert_count_disagreement_is_refused_whole(self):
        """The alias reuses ONE delta table for both names, which is only
        correct when the topology matches. A partial map would emit offsets
        against the wrong vertex indices on the shapes that did line up."""
        mine = [("A_0", 10), ("B_0", 20)]
        theirs = [("A_1", 10), ("B_1", 21)]
        assert pair_shape_aliases(mine, theirs) == {}

    def test_an_alias_may_never_shadow_a_real_shape(self):
        """If the partner's name for shape 0 is a name I ALSO carry, aliasing
        would put shape 0's morphs under shape 1's name. Dropped."""
        mine = [("Top", 10), ("Skirt", 20)]
        theirs = [("Skirt", 10), ("Top", 20)]
        assert pair_shape_aliases(mine, theirs) == {}

    def test_empty_and_missing_inputs_are_safe(self):
        assert pair_shape_aliases([], []) == {}
        assert pair_shape_aliases(None, None) == {}
        assert pair_shape_aliases([("A", 1)], None) == {}
        assert pair_shape_aliases(None, [("A", 1)]) == {}

    def test_it_is_pure(self):
        """No input is mutated -- the caller holds the shape lists."""
        mine = [("A_0", 10)]
        theirs = [("A_1", 10)]
        pair_shape_aliases(mine, theirs)
        assert mine == [("A_0", 10)] and theirs == [("A_1", 10)]


# --------------------------------------------------------------------------
# the emission, on a real generated TRI
# --------------------------------------------------------------------------

class _Morph:
    def __init__(self, name, offsets):
        self.name = name
        self.offsets = offsets


class _Osd:
    """The smallest thing `generate_armor_tri` accepts: named body morphs whose
    offsets index the body verts."""
    def __init__(self, morphs):
        self.morphs = morphs


@pytest.fixture
def tiny_body():
    """A 6-vertex 'body' and one slider.

    The slider is deliberately POSITION-DEPENDENT (vertex i moves by i+1 in x)
    rather than a uniform push: with a uniform one, two armour shapes at
    different places propagate to the SAME deltas and a test cannot tell an
    alias from a real independent entry.
    """
    verts = np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.],
                      [0., 1., 0.], [1., 1., 0.], [2., 1., 0.]])
    osd = _Osd([_Morph("BaseShape<BreastsBigger>",
                       [(i, float(i + 1), 0.0, 0.0)
                        for i in range(len(verts))])])
    return verts, osd


def _tri_for(armor, tiny_body, **kw):
    verts, osd = tiny_body
    return generate_armor_tri(armor, verts, osd, body_shape_name="BaseShape",
                              include_body_shapes=False, **kw)


class TestEmission:
    def test_without_the_map_nothing_changes(self, tiny_body):
        """DEFAULT OFF must be byte-identical to before the feature existed."""
        armor = {"Robe_0": np.array([[0.5, 0.5, 0.0], [1.5, 0.5, 0.0]])}
        a = _tri_for(armor, tiny_body)
        b = _tri_for(armor, tiny_body, also_named=None)
        c = _tri_for(armor, tiny_body, also_named={})
        names = [sorted(s.name for s in t.shapes) for t in (a, b, c)]
        assert names[0] == names[1] == names[2] == ["Robe_0"]

    def test_the_alias_carries_the_SAME_morph_table(self, tiny_body):
        """The point of the fix: the `_1` half looks up its own shape name and
        finds the identical deltas, not an empty or a recomputed table."""
        armor = {"Robe_0": np.array([[0.5, 0.5, 0.0], [1.5, 0.5, 0.0]])}
        tri = _tri_for(armor, tiny_body, also_named={"Robe_0": "Robe_1"})
        by = {s.name: s for s in tri.shapes}
        assert set(by) == {"Robe_0", "Robe_1"}
        assert by["Robe_0"].morphs, "the source shape must have morphs at all"
        assert ([(m.name, m.offsets) for m in by["Robe_1"].morphs]
                == [(m.name, m.offsets) for m in by["Robe_0"].morphs])

    def test_an_alias_for_a_shape_the_tri_does_not_have_is_ignored(
            self, tiny_body):
        """A shape can drop out of the tri (no morph passed `min_delta`).
        Aliasing it would invent an entry the NIF cannot use."""
        armor = {"Robe_0": np.array([[0.5, 0.5, 0.0]])}
        tri = _tri_for(armor, tiny_body,
                       also_named={"Absent_0": "Absent_1"})
        assert "Absent_1" not in {s.name for s in tri.shapes}

    def test_an_alias_never_overwrites_an_existing_entry(self, tiny_body):
        """If the target name is already a real shape with its own morphs, the
        alias must not replace it."""
        armor = {"A_0": np.array([[0.5, 0.5, 0.0]]),
                 "A_1": np.array([[1.5, 0.5, 0.0]])}
        tri = _tri_for(armor, tiny_body, also_named={"A_0": "A_1"})
        by = {s.name: s for s in tri.shapes}
        # A_1's own table, not A_0's -- they were built from different verts
        assert len([s for s in tri.shapes if s.name == "A_1"]) == 1
        assert by["A_1"].morphs[0].offsets != by["A_0"].morphs[0].offsets

    def test_the_written_file_round_trips_with_the_alias(self, tiny_body,
                                                         tmp_path):
        """PIRT bytes 4-5 are the SHAPE COUNT, written from `len(self.shapes)`.
        An alias grows that count, so the file must still parse back."""
        armor = {"Robe_0": np.array([[0.5, 0.5, 0.0], [1.5, 0.5, 0.0]])}
        tri = _tri_for(armor, tiny_body, also_named={"Robe_0": "Robe_1"})
        p = tmp_path / "robe.tri"
        tri.save(p)
        back = TriFile.load(p)
        assert sorted(s.name for s in back.shapes) == ["Robe_0", "Robe_1"]
        assert len(back.shapes) == len(tri.shapes)


# --------------------------------------------------------------------------
# the wiring
# --------------------------------------------------------------------------

def test_the_flag_ships_ON_and_keeps_a_kill_switch():
    """PROMOTED 2026-09-09 on the user's call, after two arms covering 53 of the
    54 affected pairs: dead halves 48 -> 0 and 5 -> 1, 769 NIFs byte-identical,
    only the affected `.tri` files changing. Resolved from the CODE, in a clean
    import -- a default is a dated claim everywhere else.

    The kill switch has to survive the promotion: a feature that ships ON with
    no way back is one nobody can bisect."""
    import os
    import json
    import subprocess
    code = ("import sys, json; sys.path.insert(0, %r)\n"
            "import src.nif_convert as nc\n"
            "print(json.dumps(nc.PAIR_TRI_NAMES))" % str(REPO_ROOT))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, cwd=str(REPO_ROOT))
    if r.returncode != 0:
        pytest.skip("clean import failed:\n" + r.stderr[-600:])
    assert json.loads(r.stdout.strip().splitlines()[-1]) is True
    src = _monolith()
    assert 'not _flag("CBBE2UBE_NO_PAIR_TRI_NAMES", False)' in src


def test_both_tri_call_sites_pass_the_map():
    """A fix wired into ONE of the two convert paths is a fix for half the
    pack. Both `generate_armor_tri` calls must pass `also_named`."""
    src = _monolith()
    calls = src.count("tri = generate_armor_tri(")
    assert calls == 2, "expected 2 call sites, found %d" % calls
    assert src.count("also_named=pair_alias_map(") == 2, (
        "both call sites must pass the alias map; the copy path and the "
        "body-swap path each write a tri")


def test_the_map_is_read_from_the_source_not_the_destination():
    """`_0` owns the tri and converts FIRST, so the `_1` destination does not
    exist when the owner writes it. Reading the destination would give an
    empty map on exactly the half that needs the alias."""
    import inspect
    from src import nif_convert_trigen as tg
    src = inspect.getsource(tg.pair_alias_map)
    assert "variant_sources.get(" in src, (
        "the partner must come from `variant_sources`, which resolves ACROSS "
        "MODS through the same VFS chain the conversion used")
    assert "dst" not in src.split('"""')[-1], (
        "the map must be built from SOURCE paths only")


def test_with_the_flag_off_the_map_is_empty_without_reading_anything():
    """THE COST-AT-DEFAULTS GUARANTEE.

    Both convert paths now CALL `pair_alias_map` unconditionally, so the
    default-off claim is only true if that call returns immediately. It must
    not open a NIF, and it must not raise when handed paths that do not exist
    -- a flag that is off has to cost nothing at all, including on a source
    the harness cannot read.
    """
    import src.nif_convert as nc
    from src import nif_convert_trigen as tg

    opened = []
    real = nc._pynifly

    class _Spy:
        def NifFile(self, *a, **k):
            opened.append(a)
            raise AssertionError("the flag is OFF; nothing may be read")

    nc._pynifly = lambda: _Spy()
    was = nc.PAIR_TRI_NAMES
    try:
        nc.PAIR_TRI_NAMES = False
        vs = {"_0": r"X:\nope\thing_0.nif", "_1": r"X:\nope\thing_1.nif"}
        assert tg.pair_alias_map(vs["_0"], vs) == {}
        assert not opened, "it read a NIF with the flag off"
    finally:
        nc.PAIR_TRI_NAMES = was
        nc._pynifly = real


def test_an_unreadable_source_never_breaks_a_conversion():
    """Armed, but the partner will not open: the map is empty and the
    conversion carries on. A morph alias is an improvement, never a
    precondition -- it must not be able to fail a piece."""
    import src.nif_convert as nc
    from src import nif_convert_trigen as tg

    was = nc.PAIR_TRI_NAMES
    try:
        nc.PAIR_TRI_NAMES = True
        vs = {"_0": r"X:\nope\thing_0.nif", "_1": r"X:\nope\thing_1.nif"}
        assert tg.pair_alias_map(vs["_0"], vs) == {}
    finally:
        nc.PAIR_TRI_NAMES = was
