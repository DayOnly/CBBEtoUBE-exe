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

"""_leg_deform_match_vert: the per-vert math behind the rigid leg-plate WHOLE-leg
conform. A rigid greave / one-piece cuirass-leg keeps the source's leg skinning, so
on UBE it neither bends the knee right (Orcish armor 91/9 Thigh:Calf vs body 76/23)
nor flexes the front/back thigh (the body uses FrontThigh/RearThigh detail bones the
plate lacks). The pass re-divides ONLY the vert's (Thigh+Calf) leg mass across the
nearest body vert's FULL leg distribution, returning the detail bones the caller must
graft -- never moving a vert or changing the vert's total weight."""
from src import nif_convert as nc

LT = "NPC L Thigh [LThg]"
LC = "NPC L Calf [LClf]"
FRONT_L = "NPC L FrontThigh"
REAR_L = "NPC L RearThigh"
RT = "NPC R Thigh [RThg]"
RC = "NPC R Calf [RClf]"


def test_knee_rebalances_thigh_calf_to_body_ratio():
    # No detail bones at the knee -> behaves like the old Thigh:Calf rebalance.
    dv = {LT: 0.91, LC: 0.09}
    touched, added = nc._leg_deform_match_vert(dv, {LT: 0.76, LC: 0.23})
    assert touched == {LT, LC}
    assert added == set()
    assert abs(dv[LT] - 0.76 / 0.99) < 1e-6        # mass(1.0) * body share
    assert abs(dv[LT] + dv[LC] - 1.0) < 1e-6        # mass preserved


def test_thigh_grafts_front_detail_bone():
    # The thigh case: body uses FrontThigh; the armor (Thigh only) must take it.
    dv = {LT: 0.99}
    touched, added = nc._leg_deform_match_vert(dv, {LT: 0.91, FRONT_L: 0.09})
    assert added == {FRONT_L}                        # caller must bind it
    assert FRONT_L in touched and LT in touched
    assert abs(dv[FRONT_L] - 0.99 * 0.09) < 1e-6
    assert abs(sum(dv.values()) - 0.99) < 1e-6       # leg mass preserved


def test_preserves_total_and_non_leg_bones():
    dv = {LT: 0.8, LC: 0.1, "NPC Pelvis [Pelv]": 0.1}
    before = sum(dv.values())
    nc._leg_deform_match_vert(dv, {LT: 0.5, LC: 0.3, REAR_L: 0.2})
    assert abs(sum(dv.values()) - before) < 1e-6     # total weight unchanged
    assert dv["NPC Pelvis [Pelv]"] == 0.1            # non-leg bone untouched
    # the 0.9 leg mass spread over the body's 0.5/0.3/0.2 distribution
    assert abs(dv[REAR_L] - 0.9 * 0.2) < 1e-6


def test_skips_below_mass_min():
    dv = {LT: 0.05, LC: 0.05, "NPC Spine [Spn0]": 0.9}   # leg mass 0.10 < 0.15
    touched, added = nc._leg_deform_match_vert(dv, {LT: 0.5, LC: 0.5})
    assert (touched, added) == (set(), set())
    assert dv[LT] == 0.05


def test_skips_when_nearest_body_vert_is_not_a_leg_vert():
    dv = {LT: 0.9, LC: 0.1}
    touched, added = nc._leg_deform_match_vert(dv, {"NPC Spine [Spn0]": 1.0})
    assert (touched, added) == (set(), set())
    assert dv[LT] == 0.9


def test_idempotent_when_already_matched():
    dv = {LT: 0.6, LC: 0.4}
    assert nc._leg_deform_match_vert(dv, {LT: 0.6, LC: 0.4}) == (set(), set())


def test_right_leg_independent():
    dv = {RT: 0.9, RC: 0.1}
    touched, added = nc._leg_deform_match_vert(dv, {RT: 0.5, RC: 0.5})
    assert touched == {RT, RC} and added == set()
    assert abs(dv[RT] - 0.5) < 1e-6


def test_detail_bone_names_resolve():
    # The graft set must reference real UBE leg-detail bones (typos -> silent no-op).
    assert FRONT_L in nc._LEG_DETAIL_BONE_NAMES
    assert REAR_L in nc._LEG_DETAIL_BONE_NAMES
    assert all("Thigh" in b or "Calf" in b for b in nc._LEG_DETAIL_BONE_NAMES)
