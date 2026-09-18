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

"""Which shapes the rendering repairs are allowed to touch.

The repairs (#winding-consistency, #seam-weld-self, degenerate-normal) fix how a
shape RENDERS. A collider is never rendered, so they can only cost there --
FSMP reads collider triangle orientation and vertex positions, and this project
has repeatedly caused equip CTDs by editing those shapes.
"""
from src import nif_convert as nc


class _Shape:
    def __init__(self, name):
        self.name = name


def test_rendered_shapes_are_repaired():
    for name in ("armor", "BaseShape", "Shoes", "Gauntlets", "Cuirass"):
        assert nc._geometry_repair_allowed(_Shape(name)) is True, name


def test_an_explicit_collider_flag_skips_the_repair():
    assert nc._geometry_repair_allowed(_Shape("anything"), True) is False


def test_softbody_and_layered_cloth_are_STILL_repaired():
    """REGRESSION. The gate used to key off `preserve_authored_skin`, which the
    re-author path sets for colliders, soft-body AND layered cloth alike. Those
    last two are SIMULATED but DRAWN, so a split seam shows on them like on any
    garment -- and blocking the repair there left most of the pack's remaining
    seam splits. Measured on a two-shape cuirass: the re-author handed ONE of the
    two shapes fresh unwelded verts with the repair disabled, overwriting the
    welded geometry, while its sibling got no override and stayed clean. Same
    NIF, opposite outcomes, one overloaded flag."""
    for name in ("BodyF_01", "ArmorF_0", "Robes", "HeavyArmorF_0"):
        assert nc._geometry_repair_allowed(_Shape(name)) is True, name


def test_collision_proxies_are_skipped_by_name():
    assert nc._geometry_repair_allowed(_Shape("VirtualBody")) is False
    assert nc._geometry_repair_allowed(_Shape("virtualbody")) is False
    assert nc._geometry_repair_allowed(
        _Shape("Cuirass" + nc._BUST_SPLIT_COL_SUFFIX)) is False


def test_every_virtual_helper_is_skipped():
    """"Virtual*" is the physics-helper convention: VirtualBody is the HDT
    collision proxy, VirtualGround a simulation plane. Neither renders. A real
    conversion left VirtualGround untouched only because its winding already
    agreed -- luck, not a rule, so name the class rather than the one case."""
    for name in ("VirtualBody", "VirtualGround", "virtualground"):
        assert nc._geometry_repair_allowed(_Shape(name)) is False, name


def test_a_name_merely_containing_col_is_still_repaired():
    """REGRESSION. The suffix is the two-letter "Col", so a substring test
    excludes real rendered geometry -- converted packs ship rendered shapes whose
    names merely CONTAIN those letters, e.g. a collar. Only a true suffix match
    may exclude; anything genuinely a collider arrives via the explicit flag."""
    for name in ("Collar", "ColSkirt", "ColBack", "Colossus", "Colour"):
        assert nc._geometry_repair_allowed(_Shape(name)) is True, name


def test_missing_or_odd_names_do_not_raise():
    class NoName:
        pass
    assert nc._geometry_repair_allowed(NoName()) is True
    assert nc._geometry_repair_allowed(_Shape(None)) is True
