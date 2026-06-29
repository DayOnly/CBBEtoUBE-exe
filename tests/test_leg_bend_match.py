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

"""Rigid leg-plate WHOLE-leg conform. A rigid greave / one-piece cuirass-leg keeps the
source's leg skinning, so on UBE it neither bends the knee right (Orcish 91/9 Thigh:
Calf vs body 76/23) nor flexes the front/back thigh (it lacks the body's FrontThigh/
RearThigh/RearCalf detail bones). The pass re-divides the vert's leg mass across the
body's FULL leg distribution (`_leg_deform_match_vert`) and grafts the detail bones
with a bind RE-ANCHORED to the armor's own Thigh/Calf (`_reanchor_stb_mat4`) -- copying
the body's ABSOLUTE STB onto an armor with a zero-translation leg bind exploded the
armor in-game, so the re-anchor's bind-consistency is the safety property tested here."""
import numpy as np
from src import nif_convert as nc

LT = "NPC L Thigh [LThg]"
LC = "NPC L Calf [LClf]"
FRONT_L = "NPC L FrontThigh"
REAR_L = "NPC L RearThigh"
RT = "NPC R Thigh [RThg]"
RC = "NPC R Calf [RClf]"


# ---- _leg_deform_match_vert: per-vert weight redistribution -----------------

def test_knee_rebalances_thigh_calf_to_body_ratio():
    dv = {LT: 0.91, LC: 0.09}
    touched, added = nc._leg_deform_match_vert(dv, {LT: 0.76, LC: 0.23})
    assert touched == {LT, LC}
    assert added == set()
    assert abs(dv[LT] - 0.76 / 0.99) < 1e-6
    assert abs(dv[LT] + dv[LC] - 1.0) < 1e-6        # mass preserved


def test_thigh_grafts_front_detail_bone():
    dv = {LT: 0.99}
    touched, added = nc._leg_deform_match_vert(dv, {LT: 0.91, FRONT_L: 0.09})
    assert added == {FRONT_L}
    assert FRONT_L in touched and LT in touched
    assert abs(dv[FRONT_L] - 0.99 * 0.09) < 1e-6
    assert abs(sum(dv.values()) - 0.99) < 1e-6      # leg mass preserved


def test_preserves_total_and_non_leg_bones():
    dv = {LT: 0.8, LC: 0.1, "NPC Pelvis [Pelv]": 0.1}
    before = sum(dv.values())
    nc._leg_deform_match_vert(dv, {LT: 0.5, LC: 0.3, REAR_L: 0.2})
    assert abs(sum(dv.values()) - before) < 1e-6
    assert dv["NPC Pelvis [Pelv]"] == 0.1
    assert abs(dv[REAR_L] - 0.9 * 0.2) < 1e-6


def test_skips_below_mass_min():
    dv = {LT: 0.05, LC: 0.05, "NPC Spine [Spn0]": 0.9}
    assert nc._leg_deform_match_vert(dv, {LT: 0.5, LC: 0.5}) == (set(), set())
    assert dv[LT] == 0.05


def test_skips_when_nearest_body_vert_is_not_a_leg_vert():
    dv = {LT: 0.9, LC: 0.1}
    assert nc._leg_deform_match_vert(dv, {"NPC Spine [Spn0]": 1.0}) == (set(), set())
    assert dv[LT] == 0.9


def test_idempotent_when_already_matched():
    dv = {LT: 0.6, LC: 0.4}
    assert nc._leg_deform_match_vert(dv, {LT: 0.6, LC: 0.4}) == (set(), set())


def test_right_leg_independent():
    dv = {RT: 0.9, RC: 0.1}
    touched, added = nc._leg_deform_match_vert(dv, {RT: 0.5, RC: 0.5})
    assert touched == {RT, RC} and added == set()
    assert abs(dv[RT] - 0.5) < 1e-6


def test_detail_bone_names_and_anchors_resolve():
    assert FRONT_L in nc._LEG_DETAIL_BONE_NAMES
    # every detail bone is anchored to an existing leg bone of the SAME leg
    for leg in nc._LEG_DEFORM_BONES:
        for b, anc in leg["detail"]:
            assert anc in (leg["thigh"], leg["calf"])


# ---- _reanchor_stb_mat4: the anti-spike bind-consistency property ------------

def _mat(t):
    m = np.eye(4)
    m[:3, 3] = t
    return m


def test_reanchor_preserves_detail_relative_to_anchor():
    # The Orcish failure: armor Thigh STB is zero-translation, body's is offset.
    # Copying the body's absolute detail STB tore verts; the re-anchor must instead
    # keep detail-RELATIVE-to-anchor identical between body and armor frames.
    m_anchor_armor = _mat([0.0, 0.0, 0.0])          # armor's zero-translation Thigh
    m_anchor_body = _mat([-13.5, 2.0, 67.9])        # body's Thigh
    m_detail_body = _mat([-13.5, -3.2, 54.4])       # body's FrontThigh
    out = nc._reanchor_stb_mat4(m_detail_body, m_anchor_body, m_anchor_armor)
    # consistency: out @ inv(anchor_armor) == detail_body @ inv(anchor_body)
    lhs = out @ np.linalg.inv(m_anchor_armor)
    rhs = m_detail_body @ np.linalg.inv(m_anchor_body)
    assert np.allclose(lhs, rhs, atol=1e-9)
    # and the result is anchored near the armor's Thigh (NOT the body's 54.4)
    assert abs(out[2, 3] - (54.4 - 67.9)) < 1e-6    # = -13.5, the relative offset


def test_reanchor_identity_anchors_is_identity_relative():
    # If both anchors are identity, the detail STB transfers unchanged.
    out = nc._reanchor_stb_mat4(_mat([1.0, 2.0, 3.0]), np.eye(4), np.eye(4))
    assert np.allclose(out, _mat([1.0, 2.0, 3.0]), atol=1e-9)


def test_reanchor_degenerate_anchor_returns_none():
    singular = np.zeros((4, 4))                     # non-invertible
    assert nc._reanchor_stb_mat4(_mat([1, 1, 1]), singular, np.eye(4)) is None
