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


"""#reskin-author-sliver-floor -- a sliver of an author bone is not a skin bone (#33).

`compute_body_blend_skinning` scales the author's weights by (1 - blend), so a
row just past `near_dist` keeps a sliver of every author bone: 0.004 on a skirt
chain bone, 0.007 on an upper-arm twist, measured. The only floor afterwards was
1e-4, so the sliver became a skin bone, and whether a row lands a hair either
side of the edge is decided per weight FILE: the thin file had `SkirtFBone01` on
0 vertices and the heavy file on 6, and its NiNode with it. Author-bone weights
under the floor on a row the BODY dominates (blend >= 0.5) are dropped; rows the
author still owns are not touched.

Geometry: a flat body at z=0 (normals +z, one bone), garment vertices straight
above grid points at chosen heights, near_dist=1 and far_dist=3, so the blend at
height h is 1 - (h - 1) / 2 and the author keeps (h - 1) / 2 of its weight.
"""
import numpy as np
import pytest

from src import nif_convert as nc
from src import nif_convert_weights as ww

N = 6


class Body:
    def __init__(self, minor=0.0):
        xs, ys = np.meshgrid(np.arange(N, dtype=float), np.arange(N, dtype=float))
        self.verts = np.c_[xs.ravel(), ys.ravel(), np.zeros(N * N)]
        self.normals = np.tile([0.0, 0.0, 1.0], (N * N, 1))
        self.bone_names = ["BodyA"]
        self.bone_weights = {"BodyA": [(i, 1.0 - minor) for i in range(N * N)]}
        if minor:
            self.bone_names.append("BodyB")
            self.bone_weights["BodyB"] = [(i, minor) for i in range(N * N)]

    def get_shape_skin_to_bone(self, _b):
        return None


class Armor:
    def __init__(self, weights):
        self.bone_weights = weights
        self.bone_names = list(weights)

    def get_shape_skin_to_bone(self, _b):
        return None


# (x, y, height) -> blend: 0.996, 0.5, 0.005 (author keeps 0.995), 0.0
VERTS = np.array([[2.0, 2.0, 1.008], [1.0, 1.0, 2.0], [4.0, 4.0, 2.99], [3.0, 3.0, 3.5]])


def run(weights, floor, minor=0.0):
    nc_floor = nc._RESKIN_AUTHOR_SLIVER_FLOOR
    try:
        nc._RESKIN_AUTHOR_SLIVER_FLOOR = floor
        nc.COVERED_SKIN_TARGET, keep = False, nc.COVERED_SKIN_TARGET
        try:
            _b, _x, wbb = ww.compute_body_blend_skinning(
                VERTS, Armor(weights), Body(minor), near_dist=1.0, far_dist=3.0, k=3)
        finally:
            nc.COVERED_SKIN_TARGET = keep
    finally:
        nc._RESKIN_AUTHOR_SLIVER_FLOOR = nc_floor
    return wbb


def rows(wbb, bone):
    return {i for i, w in wbb.get(bone, []) if w > 0.0}


CHAIN = {"Chain": [(0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)]}


def test_the_sliver_exists_without_the_floor():
    # the fixture reproduces the defect: row 0 keeps ~0.4% of the chain bone
    wbb = run(CHAIN, 0.0)
    assert 0 in rows(wbb, "Chain")
    w0 = dict(wbb["Chain"])[0]
    assert 0.001 < w0 < 0.01


def test_a_sliver_on_a_body_dominated_row_is_dropped():
    assert 0 not in rows(run(CHAIN, 0.02), "Chain")


def test_a_row_the_author_still_owns_keeps_its_weight():
    got = rows(run(CHAIN, 0.02), "Chain")
    assert {1, 2, 3} <= got


def test_the_rows_stay_normalised_after_the_drop():
    wbb = run(CHAIN, 0.02)
    tot = np.zeros(len(VERTS))
    for pairs in wbb.values():
        for i, w in pairs:
            tot[i] += w
    assert np.allclose(tot, 1.0, atol=1e-6)


def test_a_light_secondary_weight_the_author_owns_is_not_touched():
    # row 3 is far from the body (blend 0): its 1.5% second bone is authored skin
    weights = {"Chain": [(3, 0.985)], "Chain2": [(3, 0.015)]}
    got = rows(run(weights, 0.02), "Chain2")
    assert 3 in got


def test_a_light_body_bone_is_not_an_author_sliver():
    # the body's own minor bone lands ~0.01 on the body-dominated row 0: it is
    # body-propagated weight, not an author sliver, and the floor leaves it alone
    wbb = run(CHAIN, 0.02, minor=0.01)
    assert 0 in rows(wbb, "BodyB")


def test_zero_switches_it_off():
    assert 0 in rows(run(CHAIN, 0.0), "Chain")


def test_the_default_floor_is_two_percent():
    assert nc._RESKIN_AUTHOR_SLIVER_FLOOR == pytest.approx(0.02)
