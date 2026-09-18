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

"""The WIRING half of #tri-variant-collision -- which is the half that failed.

`_tri_is_owning_variant` was correct in the first fix; what was wrong was where
it got its answer from. It probed for a sibling NEXT TO `src_path`, and the
competing variant is in a DIFFERENT MOD, so the guard saw nothing and the same
five out-of-bounds entries shipped. So the map that feeds it is pinned here
separately from the predicate, on the real shape of the data: a BSA-staged
no-suffix source in one directory and a replacer mod's `_0`/`_1` pair in
another, both landing on ONE output stem.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auto_convert import _variant_sources_by_base  # noqa: E402

BSA = Path(r"X:/out/_bsa_staging/meshes/clothes/x/outfit.nif")
MOD = Path(r"X:/mods/Replacer/meshes/clothes/x")


def test_variants_from_two_different_mods_land_on_one_stem():
    """The case that ships. `resolved_pairs` came out of the mod list / VFS /
    BSA chain, so it sees across mods where a sibling probe cannot."""
    pairs = [
        (BSA, "clothes/x/outfit.nif"),
        (MOD / "outfit_0.nif", "clothes/x/outfit_0.nif"),
        (MOD / "outfit_1.nif", "clothes/x/outfit_1.nif"),
    ]
    got = _variant_sources_by_base(pairs)
    assert set(got) == {"clothes/x/outfit"}, (
        "all three variants must group onto ONE base -- they derive one .tri")
    assert set(got["clothes/x/outfit"]) == {"", "_0", "_1"}
    assert got["clothes/x/outfit"]["_0"] == str(MOD / "outfit_0.nif")
    assert got["clothes/x/outfit"][""] == str(BSA)


def test_the_key_is_case_and_separator_insensitive():
    """`rel` reaches here in the ARMA's original case with either separator; the
    two halves of a stem must not miss each other over that."""
    pairs = [(Path("a/Armor/Piece.nif"), "Armor/Piece.nif"),
             (Path("b/armor/piece_0.nif"), "armor\\piece_0.nif")]
    got = _variant_sources_by_base(pairs)
    assert list(got) == ["armor/piece"]
    assert set(got["armor/piece"]) == {"", "_0"}


def test_an_already_ube_model_is_not_a_claimant():
    """It converts nothing, so it writes no TRI. Counting it would make the
    no-suffix file defer to a destination that never appears -- morphs lost
    outright, which is worse than the collision it is avoiding."""
    pairs = [(Path("a/meshes/!UBE/armor/p_0.nif"), "!UBE/armor/p_0.nif"),
             (Path("b/meshes/armor/p.nif"), "armor/p.nif")]
    got = _variant_sources_by_base(pairs)
    assert set(got) == {"armor/p"}
    assert set(got["armor/p"]) == {""}, (
        "the already-UBE `_0` must not claim the TRI")


def test_unrelated_stems_do_not_share_an_entry():
    pairs = [(Path("a/x_0.nif"), "armor/x_0.nif"),
             (Path("a/y_0.nif"), "armor/y_0.nif")]
    got = _variant_sources_by_base(pairs)
    assert set(got) == {"armor/x", "armor/y"}


def test_no_pairs_is_an_empty_map_not_a_crash():
    assert _variant_sources_by_base([]) == {}
