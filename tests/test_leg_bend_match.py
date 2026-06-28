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

"""_leg_bend_rebalance_vert: the per-vert math behind the rigid leg-plate knee-bend
conform. A rigid greave / one-piece cuirass-leg under-weighted to the Calf at the
knee LAGS the body's knee bend so the body knee pokes through (Orcish heavy: measured
armor 91/9 Thigh:Calf vs body 76/23). The pass re-divides ONLY the (Thigh+Calf) mass
the vert already carries by the nearest body vert's ratio -- never moving a vert,
adding a bone, or changing the vert's total weight."""
from src import nif_convert as nc

LT = "NPC L Thigh [LThg]"
LC = "NPC L Calf [LClf]"
RT = "NPC R Thigh [RThg]"
RC = "NPC R Calf [RClf]"


def test_rebalances_knee_split_to_body_ratio():
    # The Orcish case: armor 91/9, body 76/23 -> armor takes the body's split.
    dv = {LT: 0.91, LC: 0.09}
    touched = nc._leg_bend_rebalance_vert(dv, {LT: 0.76, LC: 0.23})
    assert touched == {LT, LC}
    r = 0.76 / (0.76 + 0.23)
    assert abs(dv[LT] - r) < 1e-6            # mass (1.0) * body ratio
    assert abs(dv[LT] + dv[LC] - 1.0) < 1e-6  # mass preserved


def test_preserves_total_and_other_bones():
    # Total weight and any non-leg bone (e.g. pelvis) are untouched: only the
    # Thigh/Calf split changes, so the skin-partition palette is unaffected.
    dv = {LT: 0.8, LC: 0.1, "NPC Pelvis [Pelv]": 0.1}
    before = sum(dv.values())
    nc._leg_bend_rebalance_vert(dv, {LT: 0.5, LC: 0.5})
    assert abs(sum(dv.values()) - before) < 1e-6
    assert dv["NPC Pelvis [Pelv]"] == 0.1
    assert abs(dv[LT] - dv[LC]) < 1e-6        # 50/50 now (mass 0.9 split evenly)


def test_skips_below_mass_min():
    # A vert with only a trace of leg weight (mostly spine) is not a leg-bend vert.
    dv = {LT: 0.05, LC: 0.05, "NPC Spine [Spn0]": 0.9}   # leg mass 0.10 < 0.15
    assert nc._leg_bend_rebalance_vert(dv, {LT: 0.5, LC: 0.5}) == set()
    assert dv[LT] == 0.05


def test_skips_when_nearest_body_vert_is_not_a_leg_vert():
    # If the nearest body vert is torso/arm (no thigh+calf), don't fabricate a ratio.
    dv = {LT: 0.9, LC: 0.1}
    assert nc._leg_bend_rebalance_vert(dv, {"NPC Spine [Spn0]": 1.0}) == set()
    assert dv[LT] == 0.9


def test_idempotent_when_already_matched():
    dv = {LT: 0.6, LC: 0.4}
    assert nc._leg_bend_rebalance_vert(dv, {LT: 0.6, LC: 0.4}) == set()


def test_right_leg_pair_independent():
    dv = {RT: 0.9, RC: 0.1}
    touched = nc._leg_bend_rebalance_vert(dv, {RT: 0.5, RC: 0.5})
    assert touched == {RT, RC}
    assert abs(dv[RT] - 0.5) < 1e-6
