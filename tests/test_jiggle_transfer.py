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

"""Pure-logic guard for the body-jiggle graft (_jiggle_transfer_vert).

A fitted garment that hugs the butt/belly but carries none of the body's jiggle
weight stays rigid while the body jiggles -> the body pokes through (close-to-body
clip when moving). The graft adds a closeness-scaled share of the body vert's
jiggle weight and renormalizes. The on-disk pass (_transfer_body_jiggle_to_fitted)
needs the real UBE body, so these pin the decision math in isolation."""
import src.nif_convert as nc

THIGH = "NPC L Thigh [LThg]"
BUTT = "NPC L Butt"


def test_graft_adds_jiggle_and_renormalizes():
    new, added = nc._jiggle_transfer_vert({THIGH: 1.0}, {BUTT: 0.5}, 1.0, 0.7)
    assert added == {BUTT}
    assert BUTT in new
    assert abs(sum(new.values()) - 1.0) < 1e-6     # still a partition of unity
    assert new[THIGH] > new[BUTT]                   # thigh stays dominant
    # the graft is a MEANINGFUL share (target = 0.5*0.7 = 0.35), not a token amount
    assert abs(new[BUTT] - 0.35) < 1e-6
    assert abs(new[THIGH] - 0.65) < 1e-6


def test_graft_caps_so_jiggle_never_dominates():
    # two strong jiggle bones would target > 1; the graft is capped at 0.95 total
    # so the vert keeps some of its own leg weight.
    new, added = nc._jiggle_transfer_vert(
        {THIGH: 1.0}, {BUTT: 0.9, "NPC Belly": 0.9}, 1.0, 0.85)
    assert added == {BUTT, "NPC Belly"}
    assert abs(sum(new.values()) - 1.0) < 1e-6
    assert new.get(THIGH, 0.0) >= 0.04             # leg weight not fully evicted


def test_no_body_jiggle_is_noop():
    assert nc._jiggle_transfer_vert({THIGH: 1.0}, {}, 1.0, 0.7) == (None, set())


def test_far_vert_is_noop():
    # closeness 0 (vert at the proximity edge) -> nothing grafted
    assert nc._jiggle_transfer_vert({THIGH: 1.0}, {BUTT: 0.5}, 0.0, 0.7) == (None, set())


def test_closeness_scales_graft_amount():
    near, _ = nc._jiggle_transfer_vert({THIGH: 1.0}, {BUTT: 0.5}, 1.0, 0.7)
    far, _ = nc._jiggle_transfer_vert({THIGH: 1.0}, {BUTT: 0.5}, 0.3, 0.7)
    assert near[BUTT] > far[BUTT]                   # closer to the body -> more jiggle


def test_existing_jiggle_bone_is_noop():
    # the graft only ADDS missing jiggle (reinforcing an existing bone is the
    # conform pass's job) -> a vert that already has the bone is left untouched.
    assert nc._jiggle_transfer_vert(
        {THIGH: 0.6, BUTT: 0.4}, {BUTT: 0.5}, 1.0, 0.7) == (None, set())


def test_negligible_weight_is_noop():
    # a near-zero body jiggle weight produces no meaningful graft
    assert nc._jiggle_transfer_vert({THIGH: 1.0}, {BUTT: 1e-5}, 1.0, 0.7) == (None, set())
