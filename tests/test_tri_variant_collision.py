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

"""#tri-variant-collision: ONE `.tri`, and it must have ONE writer.

`foo.nif`, `foo_0.nif` and `foo_1.nif` all derive `foo.tri`. `_0` beats `_1`
(measured -- see `_tri_is_owning_variant`), but a NO-SUFFIX stem does not end in
`_1`, so it and `_0` both claimed ownership: two writers, last one wins.

Where the no-suffix file is a lower-poly variant (first-person, world model) the
survivor's offsets address vertices it does not have. Found on the shipped pack:

    GauntletsF   1544 verts   TRI indexes to  3013
    Outfit       2467 verts   TRI indexes to 13705
    ClothF1st     495 verts   TRI indexes to  1544

THE FIRST FIX WAS INERT, and the tests below say why. It probed for the `_0`
sibling NEXT TO `src_path`, which finds nothing in the case that actually ships:
the competing variant lives in a DIFFERENT MOD, and a BSA-resolved source is
staged ALONE in its own directory. The same five entries shipped after it.

So the variant set is resolved where SOURCES are resolved -- the mod list / VFS
/ BSA chain in `auto_convert` -- and handed to the guards as `variant_sources`.
Traced on the real pack: a vanilla BSA ships the 2467-vert no-suffix model on
its own while a body-replacer mod ships a 13706-vert `_0`/`_1` pair, and both
converge on one output stem.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.nif_convert_trigen import (  # noqa: E402
    _tri_is_owning_variant, _tri_fits_variant, _tri_owner_path)
from tests.synthetic_nif import (  # noqa: E402
    TRIS, VERTS, build_shape_nif, pynifly_available)


def _touch(d: Path, name: str) -> Path:
    p = d / name
    p.write_bytes(b"")
    return p


# ---------------------------------------------------------------- ownership

def test_no_suffix_variant_does_not_own_a_tri_the_weight_pair_claims(tmp_path):
    """The regression itself. Both used to answer True."""
    bare = _touch(tmp_path, "foo.nif")
    zero = _touch(tmp_path, "foo_0.nif")
    _touch(tmp_path, "foo_1.nif")
    assert _tri_is_owning_variant(zero) is True, "`_0` owns the shared TRI"
    assert _tri_is_owning_variant(bare) is False, (
        "the no-suffix variant must NOT also write foo.tri -- two writers is "
        "how a low-poly variant ended up pointing at the pair's morphs")


def test_a_lone_no_suffix_piece_still_owns_its_own_tri(tmp_path):
    """A helmet or amulet with no weight pair must not silently lose morphs."""
    bare = _touch(tmp_path, "helmet.nif")
    assert _tri_is_owning_variant(bare) is True


def test_the_weight_pair_rule_is_unchanged(tmp_path):
    """`_0` beats `_1`, and a `_1`-only piece owns its own -- both measured
    before this change and not to be disturbed by it."""
    _touch(tmp_path, "a_0.nif")
    one = _touch(tmp_path, "a_1.nif")
    assert _tri_is_owning_variant(one) is False
    lone = _touch(tmp_path, "b_1.nif")
    assert _tri_is_owning_variant(lone) is True


def test_a_weight_pair_member_never_withholds_its_bodytri(tmp_path):
    """`_0` and `_1` are the same mesh at two weights and share the TRI by
    design, so the fit check must short-circuit for them -- without reading a
    NIF, which is also what keeps it cheap in the hot path."""
    _touch(tmp_path, "c_0.nif")
    one = _touch(tmp_path, "c_1.nif")
    assert _tri_fits_variant(one) is True
    assert _tri_fits_variant(tmp_path / "c_0.nif") is True


def test_a_lone_no_suffix_piece_keeps_its_bodytri(tmp_path):
    """Nothing else claims the TRI, so there is nothing to disagree with."""
    bare = _touch(tmp_path, "lone.nif")
    assert _tri_fits_variant(bare) is True


# ----------------------------------------------- the variant set, ACROSS MODS

def test_the_sibling_probe_is_blind_across_mods(tmp_path):
    """WHY THE FIRST FIX WAS INERT, pinned so it cannot come back.

    The staged BSA source sits alone; the pair it collides with is elsewhere.
    With nothing but `src_path` to go on, the guard cannot see the collision
    and both files claim the TRI -- exactly what shipped.
    """
    staged = tmp_path / "_bsa_staging" / "meshes" / "clothes" / "x"
    other = tmp_path / "SomeReplacerMod" / "meshes" / "clothes" / "x"
    staged.mkdir(parents=True)
    other.mkdir(parents=True)
    bare = _touch(staged, "outfit.nif")
    _touch(other, "outfit_0.nif")
    _touch(other, "outfit_1.nif")
    assert _tri_is_owning_variant(bare) is True, (
        "the sibling probe cannot see the other mod -- this is the inert "
        "behaviour the resolved variant set replaces")


def test_the_resolved_variant_set_sees_the_other_mod(tmp_path):
    """The fix. Same layout, but the variant set came from the mod list / VFS
    the way `src_path` itself did, so the pair's claim is visible."""
    staged = tmp_path / "_bsa_staging" / "meshes" / "clothes" / "x"
    other = tmp_path / "SomeReplacerMod" / "meshes" / "clothes" / "x"
    staged.mkdir(parents=True)
    other.mkdir(parents=True)
    bare = _touch(staged, "outfit.nif")
    zero = _touch(other, "outfit_0.nif")
    one = _touch(other, "outfit_1.nif")
    vs = {"": str(bare), "_0": str(zero), "_1": str(one)}
    assert _tri_is_owning_variant(bare, vs) is False
    assert _tri_is_owning_variant(one, vs) is False
    assert _tri_is_owning_variant(zero, vs) is True, (
        "`_0` still owns it -- that is measured, and picking `_1` cost an "
        "in-game nipple through a cuirass")


def test_the_owner_is_picked_by_priority_not_by_the_caller_order():
    """`_0` > `_1` > no-suffix, whatever order the resolver happened to fill in."""
    vs = {"": "z/outfit.nif", "_1": "z/outfit_1.nif", "_0": "z/outfit_0.nif"}
    assert _tri_owner_path("z/outfit.nif", vs).name == "outfit_0.nif"
    assert _tri_owner_path("z/outfit.nif", {"": "z/o.nif", "_1": "z/o_1.nif"}
                           ).name == "o_1.nif"
    assert _tri_owner_path("z/outfit.nif", {"": "z/o.nif"}).name == "o.nif"


def test_a_no_suffix_file_yields_to_a_one_only_pair(tmp_path):
    """A hole the sibling probe could not close AT ALL: with no `_0` anywhere,
    neither `foo.nif` nor `foo_1.nif` ends in a claimed suffix, so both wrote
    `foo.tri`. The resolved set picks one owner by priority."""
    bare = _touch(tmp_path, "d.nif")
    one = _touch(tmp_path, "d_1.nif")
    assert _tri_is_owning_variant(bare) is True   # the probe: two writers
    assert _tri_is_owning_variant(one) is True
    vs = {"": str(bare), "_1": str(one)}
    assert [_tri_is_owning_variant(p, vs) for p in (bare, one)] == [False, True]


def test_an_unresolved_variant_set_falls_back_to_the_probe(tmp_path):
    """`None` means "nobody resolved this" (a single-file convert, a test), and
    must behave exactly as before -- never as "no other variant exists"."""
    bare = _touch(tmp_path, "e.nif")
    _touch(tmp_path, "e_0.nif")
    assert _tri_is_owning_variant(bare, None) is False
    assert _tri_is_owning_variant(bare, {}) is False


# ------------------------------------------------- the fit check, ACROSS MODS

@pytest.mark.skipif(
    not pynifly_available(), reason="pynifly native lib not available")
def test_a_low_poly_variant_in_another_mod_is_denied_the_bodytri(tmp_path):
    """The defect end to end, on REAL NIFs with real vertex counts.

    A 4-vertex no-suffix model and an 8-vertex `_0`/`_1` pair share a stem from
    two different mods. The pair owns the TRI, so its offsets index up to 7 --
    past the end of a 4-vertex shape. Pointing at it is worse than having no
    morphs, so the BODYTRI is withheld.
    """
    staged = tmp_path / "_bsa_staging" / "meshes" / "armor" / "y"
    other = tmp_path / "ReplacerMod" / "meshes" / "armor" / "y"
    staged.mkdir(parents=True)
    other.mkdir(parents=True)
    big_verts = VERTS + [(2.0, 0.0, 0.0), (0.0, 2.0, 0.0),
                         (0.0, 0.0, 2.0), (2.0, 2.0, 2.0)]
    bare = staged / "piece.nif"
    zero = other / "piece_0.nif"
    one = other / "piece_1.nif"
    build_shape_nif(bare, name="GauntletsF", verts=VERTS, tris=TRIS)
    for p in (zero, one):
        build_shape_nif(p, name="GauntletsF", verts=big_verts, tris=TRIS)
    vs = {"": str(bare), "_0": str(zero), "_1": str(one)}
    assert _tri_fits_variant(bare) is True, (
        "the sibling probe passes it -- the inert behaviour")
    assert _tri_fits_variant(bare, vs) is False, (
        "4 verts cannot carry an 8-vert TRI's offsets")
    assert _tri_fits_variant(zero, vs) is True
    assert _tri_fits_variant(one, vs) is True, (
        "the weight pair shares one mesh by design and must keep its morphs")


@pytest.mark.skipif(
    not pynifly_available(), reason="pynifly native lib not available")
def test_a_matching_no_suffix_variant_keeps_its_bodytri(tmp_path):
    """The guard withholds on DISAGREEMENT, not on the mere existence of a
    pair. A world model that happens to share the pair's topology still morphs.
    """
    staged = tmp_path / "_bsa_staging" / "meshes" / "armor" / "y"
    other = tmp_path / "ReplacerMod" / "meshes" / "armor" / "y"
    staged.mkdir(parents=True)
    other.mkdir(parents=True)
    bare = staged / "same.nif"
    zero = other / "same_0.nif"
    build_shape_nif(bare, name="ArmorF", verts=VERTS, tris=TRIS)
    build_shape_nif(zero, name="ArmorF", verts=VERTS, tris=TRIS)
    vs = {"": str(bare), "_0": str(zero)}
    assert _tri_fits_variant(bare, vs) is True


@pytest.mark.skipif(
    not pynifly_available(), reason="pynifly native lib not available")
def test_no_shared_shape_name_is_not_a_disagreement(tmp_path):
    """Nothing to compare -> nothing to deny. The name-agreement problem is a
    separate, milder class (DEAD sliders, not out-of-bounds ones) and is scored
    by the pack census, not fixed here."""
    staged = tmp_path / "_bsa_staging" / "meshes" / "armor" / "y"
    other = tmp_path / "ReplacerMod" / "meshes" / "armor" / "y"
    staged.mkdir(parents=True)
    other.mkdir(parents=True)
    bare = staged / "diff.nif"
    zero = other / "diff_0.nif"
    build_shape_nif(bare, name="ClothF1st", verts=VERTS, tris=TRIS)
    build_shape_nif(zero, name="Top", verts=VERTS + [(9.0, 9.0, 9.0)],
                    tris=TRIS)
    vs = {"": str(bare), "_0": str(zero)}
    assert _tri_fits_variant(bare, vs) is True
