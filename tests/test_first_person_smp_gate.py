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

"""A first-person viewmodel must never get an auto-generated HDT-SMP config.

FSMP merges every `<per-vertex-shape name="...">` into the ACTOR's physics system by
SHAPE NAME, and a first-person NIF carries the same shape names as its third-person twin
(one armour's `1st.nif` and `dcuirass.nif` both hold `Cuirass_A/_B/_C`). So the
first-person XML drives the THIRD-person shapes as skin-stripped cloth with nothing
constraining it -- FSMP's soft body diverges, its collision SIMD reads out of bounds, and
the game dies on equip. Deleting exactly those two XMLs is what fixed it in-game.

Detection needs name AND structure. Neither alone is safe, and this is the whole test:
  * name only  -> `1stexplorersgarb_f` is an ITEM named "First Explorer's Garb", a
    third-person body armor that would silently lose its physics.
  * structure only -> cloaks, boots and gloves also carry no injected BaseShape, and
    they DO want physics. An earlier gate keyed on "is this layered cloth" instead and
    stripped physics from a cloak, a book and two armors to fix one first-person mesh.
#first-person-smp-gate"""
from src.nif_convert import _is_first_person_mesh, UBE_BODY_INJECT_NAMES


class _Shape:
    def __init__(self, name):
        self.name = name


class _Nif:
    def __init__(self, *names):
        self.shapes = [_Shape(n) for n in names]


_BODY = sorted(UBE_BODY_INJECT_NAMES)[0]      # e.g. "BaseShape"


def test_first_person_mesh_is_gated():
    """The literal crash source: 1st.nif carrying the third-person shape names."""
    assert _is_first_person_mesh("narmor/leathersuitn/1st_1.nif",
                                 _Nif("Cuirass_A", "Cuirass_B", "Cuirass_C"))


def test_prefixed_first_person_variant_is_gated():
    """`d1st` (the dark variant) has no word boundary before '1st' -- a regex keyed on
    one would miss it, and it was the second crashing file."""
    assert _is_first_person_mesh("narmor/leathersuitn/d1st_0.nif",
                                 _Nif("Cuirass_A", "Cuirass_B"))


def test_spelled_out_first_person_is_gated():
    assert _is_first_person_mesh("armor/x/0cce_dress1_firstperson_1.nif", _Nif("Dress"))
    assert _is_first_person_mesh("ModArmor/1stpersoncuirassF_1.nif", _Nif("Cuirass"))


def test_item_named_first_is_not_gated():
    """`1strangerscoat_f` is "First Ranger's Coat" -- a THIRD-person body armor. It
    carries an injected body, which a viewmodel never does. Name alone would strip it."""
    nif = _Nif(_BODY, "Shirt", "Pants")
    assert not _is_first_person_mesh("modarmor/female/1strangerscoat_f_1.nif", nif)


def test_bodyless_third_person_pieces_keep_physics():
    """A cloak has no injected body either -- structure alone would gate it. Its NAME
    is what saves it. This is the regression the previous gate caused."""
    assert not _is_first_person_mesh("stormbear/stormbearcloakf_1.nif",
                                     _Nif("Cloak 1", "Cloak 2"))
    assert not _is_first_person_mesh("narmor/leathersuitn/dboots_1.nif", _Nif("Boots"))


def test_third_person_body_armor_is_not_gated():
    assert not _is_first_person_mesh("narmor/leathersuitn/dcuirass_1.nif",
                                     _Nif(_BODY, "Cuirass_A", "Cuirass_B"))


def test_weight_suffix_is_stripped_before_matching():
    """_0 / _1 must not defeat the stem match."""
    for p in ("x/1st_0.nif", "x/1st_1.nif", "x/1st.nif"):
        assert _is_first_person_mesh(p, _Nif("Cuirass_A"))


def test_bad_input_is_not_gated():
    """Never gate on an exception -- silently losing physics is worse than a stray XML."""
    class _Boom:
        @property
        def shapes(self):
            raise RuntimeError("unreadable")
    assert not _is_first_person_mesh("x/1st_1.nif", _Boom())
    assert not _is_first_person_mesh(None, _Nif("Cuirass_A"))
