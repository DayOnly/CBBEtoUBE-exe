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

"""`_layered_cloth_shape_names` detects MULTI-LAYER cloth cuirass shapes (2+ sibling
shapes sharing a base stem + a short layer suffix, e.g. Cuirass_A/_B/_C). Every
body-follow graft pass skips them so the body's HDT-SMP jiggle bones aren't grafted
onto bone-driven cloth -> no equip CTD (New Leather crash 2026-07-09). This guards
the DETECTION: it must catch real layer groups but NOT single shapes, the body, or
unrelated names (a false positive keeps a shape on source skin = loses morph-follow).
#layered-cloth-skin"""
from src import nif_convert as nc


class _S:
    def __init__(self, name):
        self.name = name


def _names(*names):
    return nc._layered_cloth_shape_names([_S(n) for n in names])


def test_catches_letter_layer_group():
    """Cuirass_A/_B/_C -> all three (the New Leather cuirass pattern)."""
    out = _names("Cuirass_A", "Cuirass_B", "Cuirass_C", "Greaves", "BaseShape")
    assert out == {"Cuirass_A", "Cuirass_B", "Cuirass_C"}


def test_catches_numeric_layer_group():
    """Robe_01/_02 (2-digit suffixes) is also a layer group."""
    assert _names("Robe_01", "Robe_02") == {"Robe_01", "Robe_02"}


def test_space_separated_suffix():
    """A ' 1'/' 2' suffix (space, not underscore) still groups."""
    assert _names("boots shell 1", "boots shell 2") == {"boots shell 1", "boots shell 2"}


def test_single_layer_not_grouped():
    """One shape with a layer suffix is NOT a group -> not touched (keeps reskin)."""
    assert _names("Cuirass_A", "Greaves", "BaseShape") == set()


def test_no_suffix_not_grouped():
    """Plain names (no layer suffix) are never grouped -- normal armour still reskins."""
    assert _names("Cuirass", "Greaves", "Buckles", "BaseShape") == set()


def test_body_shapes_not_grouped():
    """The body must never be caught: '3BA' has no suffix; '3BA_Anus'/'3BA_Vagina'
    have a multi-char suffix (not a single letter / 1-2 digits) -> no match."""
    assert _names("3BA", "3BA_Anus", "3BA_Vagina", "BaseShape") == set()


def test_multichar_suffix_not_grouped():
    """A trailing WORD (not a single letter / 1-2 digits) is not a layer suffix, so
    e.g. Throwing_Knives / Cuirass_Front don't falsely group."""
    assert _names("Throwing_Knives", "Cuirass_Front", "Cuirass_Back") == set()


def test_multiple_independent_groups():
    """Two separate layer groups in one NIF are both caught; a lone sibling isn't."""
    out = _names("Cuirass_A", "Cuirass_B", "Buckles_01", "Buckles_02", "Lonely_A")
    assert out == {"Cuirass_A", "Cuirass_B", "Buckles_01", "Buckles_02"}


def test_escape_hatch_disables(monkeypatch):
    """CBBE2UBE_NO_LAYERED_CLOTH_SKIN=1 -> detector returns empty (reverts behaviour)."""
    monkeypatch.setattr(nc, "_LAYERED_CLOTH_SKIN", False)
    assert _names("Cuirass_A", "Cuirass_B", "Cuirass_C") == set()
