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

"""Guards for the child-content gates.

Child clothing is child-sized and made for child NPCs, so it is never armour
"for the player" -- the user's rule. Two gates, and BOTH are needed:

  MOD NAME  (_is_child_content_mod) drops whole folders like "Children Overhaul".
  ASSET     (_is_child_content_asset) drops individual armatures/meshes.

The asset gate exists because the mod-name gate cannot see two real cases found
on the live modlist:
  1. the VANILLA SWEEP, whose "mod" is the game Data dir -- and vanilla binds
     ChildrenTorsoV01AA / ChildrenShoesAA to DefaultRace (0x00000019), so they
     pass the DefaultRace gate legitimately and convert;
  2. adult-named quest mods that happen to ship child clothing (measured on the
     live modlist: large quest-expansion mods whose FOLDER NAME carries no child
     token at all, so only the asset name can catch them).
"""
from src import auto_convert
from src.auto_convert import (_camel_tokens, _is_child_content_asset,
                              _is_child_content_mod)


def test_camel_tokens_splits_case_and_separators():
    t = _camel_tokens(r"Clothes\ChildrenClothes\F\Torso01_1.nif")
    assert {"clothes", "childrenclothes", "children", "f"} <= t
    # the unsplit word is kept too, so plain substrings still match
    assert "torso01" in t or "torso" in t


def test_asset_gate_catches_vanilla_defaultrace_child_armatures():
    """The exact records that leaked: both bind DefaultRace, so only the asset
    name distinguishes them."""
    assert _is_child_content_asset("ChildrenTorsoV01AA")
    assert _is_child_content_asset("ChildrenShoesAA")
    assert _is_child_content_asset("ChildTorso01AA")
    assert _is_child_content_asset(r"Clothes\ChildrenClothes\F\Torso01_1.nif")
    assert _is_child_content_asset(r"Meshes/Clothes/ChildrenClothes/f/Mod_Shoes01_1.nif")


def test_asset_gate_does_not_catch_kidskin():
    """'kidskin' is a LEATHER. The mod-name gate protects it by whole-word
    matching; the asset gate must not undo that when it splits camelCase --
    which is why 'kid' is deliberately not an asset-gate word."""
    assert not _is_child_content_asset("Kidskin Gloves")
    assert not _is_child_content_asset("KidSkinGloves")
    assert not _is_child_content_asset(r"armor\kidskin\gloves_1.nif")
    assert not _is_child_content_mod("Kidskin Gloves")


def test_asset_gate_is_whole_word():
    assert not _is_child_content_asset(r"armor\Grandchild\x.nif")
    assert not _is_child_content_asset("ChildeArmorAA")
    # ...but a real separator-delimited "Kids" folder still counts
    assert _is_child_content_asset(r"armor\Kids Armor\x.nif")


def test_asset_gate_ignores_unrelated_armour():
    for name in (r"clothes\chef\f\chef_1.nif", "ClothesChefAA",
                 r"armor\ebony\cuirassf_1.nif", "EbonyCuirassAA"):
        assert not _is_child_content_asset(name), name


def test_mod_name_gate_unchanged():
    for n in ("Children Overhaul", "Kids of Skyrim", "The Child NPC Pack"):
        assert _is_child_content_mod(n), n
    # an adult-named quest mod: no child token in the folder name -> not gated
    for n in ("Ebony Armor", "The Ashen Vale - Quest Expansion"):
        assert not _is_child_content_mod(n), n


# ---- already-UBE meshes must never be re-converted ------------------------
# Converted output and UBE-native mods both live under `meshes\!UBE\`. Refitting
# such a mesh onto the UBE body a second time double-converts it and writes to
# `!UBE\!UBE\...`, which was found in real output on a live modlist.

def test_already_ube_model_detects_the_convention():
    f = auto_convert._is_already_ube_model
    assert f(r"!UBE\SomeAuthor\Outfit\Top_1.nif")
    assert f("!ube/someauthor/outfit/top_1.nif")     # case-insensitive
    assert f(r"/!UBE\a\b.nif")                       # leading separator
    assert f("!UBE/Body/femalebody_tangent_1.nif")


def test_already_ube_model_does_not_over_match():
    """Only the FIRST path segment counts. A mod folder or file that merely
    contains the letters is not already-UBE -- the retired name hint made
    exactly this mistake, excluding a 'Custom Cubemaps' mod."""
    f = auto_convert._is_already_ube_model
    assert not f(r"armor\cubemaps\x_1.nif")
    assert not f(r"armor\ube-style\x_1.nif")         # substring, not the segment
    assert not f(r"armor\studded\cuirassf_1.nif")
    # a leading "meshes\" IS tolerated: ARMA paths are meshes-relative,
    # but real mods ship the redundant prefix, so it must still be caught
    assert f(r"meshes\!UBE\x.nif")
    assert not f("") and not f(None)


def test_ube_is_matched_as_a_whole_path_part_in_esp_discovery():
    """_find_source_esps skips plugins in a UBE subfolder. Matching 'ube' as a
    SUBSTRING there also swallowed the containing mod folder, which silently
    made every '... - UBE' mod unreachable and duplicated the retired name
    hint. Whole-part matching keeps the intent without the collateral."""
    assert "ube" not in auto_convert._NONSOURCE_NAME_HINTS, (
        "the 'ube' name hint is retired; _is_already_ube_model replaces it")
