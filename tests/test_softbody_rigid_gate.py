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

"""Regression: the GENERATED per-vertex soft-body (the fallback physics for
armors with no hand-authored XML) must only fire on free HANGING cloth, never on
a rigid TORSO cuirass. A cuirass turned into cloth1 has no authored chain, so
the whole armour flops / disjoints from the body (the Anequina Armor case: a
single WolfArmor shape carrying both the plate torso and a skirt was wrongly
soft-bodied -> disjointed skirt).

`_shape_is_rigid_torso_armor` splits them by upper-torso rigid-bone weight
(cuirass ~54%, real skirt ~2%). #softbody-rigid-gate
"""
import pytest

from tests.synthetic_nif import build_skinned_shape_nif, pynifly_available
import src.nif_convert as nc  # noqa: E402

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")


def _shape(tmp_path, name, bones):
    p = build_skinned_shape_nif(tmp_path / f"{name}.nif", name=name, bones=bones)
    return nc._pynifly().NifFile(filepath=str(p)).shapes[0]


def test_torso_cuirass_is_flagged_rigid(tmp_path):
    # Weighted to upper-torso rigid bones -> body-fitted plate, not free cloth.
    sh = _shape(tmp_path, "Cuirass",
                ("NPC Spine2 [Spn2]", "NPC L UpperArm [LUar]"))
    assert nc._shape_is_rigid_torso_armor(sh) is True


def test_hanging_skirt_is_not_rigid(tmp_path):
    # Weighted to a skirt chain bone + pelvis -> free hanging cloth.
    sh = _shape(tmp_path, "Skirt",
                ("SkirtFBone03", "NPC Pelvis [Pelv]"))
    assert nc._shape_is_rigid_torso_armor(sh) is False


def test_cape_off_spine_lower_is_not_rigid(tmp_path):
    # A cape hangs off the lower spine / pelvis -- below the rigid-torso keys.
    sh = _shape(tmp_path, "Cape",
                ("NPC Spine [Spn0]", "NPC Pelvis [Pelv]"))
    assert nc._shape_is_rigid_torso_armor(sh) is False


def test_empty_skin_is_not_rigid(tmp_path):
    # No weights -> can't be classified rigid (don't accidentally exclude).
    class _Bare:
        bone_weights = {}
    assert nc._shape_is_rigid_torso_armor(_Bare()) is False
