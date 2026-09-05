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
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.nif_convert_trigen import (  # noqa: E402
    _tri_is_owning_variant, _tri_fits_variant)


def _touch(d: Path, name: str) -> Path:
    p = d / name
    p.write_bytes(b"")
    return p


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
