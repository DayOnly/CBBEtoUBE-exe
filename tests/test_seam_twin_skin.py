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

"""Seam twins of ONE shape share one skin row (#seam-twin-skin).

Reported in game on a torso: the seam under the arm separates at the back half of
the armpit when the arm moves. The positions of the two sides of the seam were
already joined at rest; their WEIGHTS were not. In the source every such pair is
skinned alike (a seam is one point of the garment cut open for a UV island), and
after conversion 198 of 1346 torso pairs differed, up to 0.66, because each side
is paired to the body by its own normal.

The cross-shape pass skipped every pair inside one shape. These pin the rule that
joins the pairs that were ONE point in the source, and only those.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402
import src.nif_convert_weights as nw  # noqa: E402
from tests.test_coincident_skin_match import (  # noqa: E402
    BELLY, LTHIGH, PELV, SPINE, SPINE1, _run, _shape)

SEAM = (0.0, 0.0, 96.0)


def _torso(rows, dst_pos, src_pos, src_rows):
    dst = [_shape("Torso", dst_pos, rows)]
    src = [_shape("Torso", src_pos, src_rows)]
    return dst, src


def _alike():
    # the author's row for both sides of the seam
    return {SPINE: 0.494, SPINE1: 0.126, PELV: 0.380}


def test_the_two_sides_of_a_seam_end_up_with_one_row():
    # the reported armpit pair: side one Spine-heavy, side two split with an arm bone
    a = {SPINE: 0.873, SPINE1: 0.009, LTHIGH: 0.118}
    b = {SPINE: 0.217, SPINE1: 0.404, PELV: 0.379}
    dst, src = _torso([a, b], [SEAM, SEAM], [SEAM, SEAM], [_alike(), _alike()])
    n, saved = _run(dst, src)
    assert n == 2 and saved == ["dst.nif"]
    assert dst[0].row(0) == dst[0].row(1)
    assert abs(sum(dst[0].row(0).values()) - 1.0) < 1e-6


def test_the_unified_row_is_the_mean_of_the_two_sides():
    a = {SPINE: 0.8, PELV: 0.2}
    b = {SPINE: 0.4, PELV: 0.6}
    dst, src = _torso([a, b], [SEAM, SEAM], [SEAM, SEAM], [_alike(), _alike()])
    _run(dst, src)
    row = dst[0].row(0)
    assert abs(row[SPINE] - 0.6) < 1e-3 and abs(row[PELV] - 0.4) < 1e-3


def test_twins_are_joined_by_where_they_were_in_the_source_not_in_the_output():
    # the output halves are a whole unit apart (no weld ran); the source ones
    # coincided, so they are still one point
    a = {SPINE: 0.9, PELV: 0.1}
    b = {SPINE: 0.3, PELV: 0.7}
    dst, src = _torso([a, b], [SEAM, (1.0, 0.0, 96.0)], [SEAM, SEAM],
                      [_alike(), _alike()])
    _run(dst, src)
    assert dst[0].row(0) == dst[0].row(1)


def test_a_seam_the_author_skinned_apart_is_left_apart():
    # the author's own rows differ across this edge by far more than the gate:
    # a deliberate skin boundary, not a cut seam
    a, b = {SPINE: 1.0}, {PELV: 1.0}
    dst, src = _torso([a, b], [SEAM, SEAM], [SEAM, SEAM], [a, b])
    n, saved = _run(dst, src)
    assert dst[0].row(0) == {SPINE: 1.0} and dst[0].row(1) == {PELV: 1.0}


def test_vertices_that_were_only_near_each_other_in_the_source_are_not_joined():
    # 0.01 apart in the source: neighbouring vertices, not twins. They must keep
    # their own rows however the output placed them.
    a = {SPINE: 0.9, PELV: 0.1}
    b = {SPINE: 0.3, PELV: 0.7}
    dst, src = _torso([a, b], [SEAM, SEAM], [SEAM, (0.0, 0.0, 96.01)],
                      [_alike(), _alike()])
    _run(dst, src)
    assert dst[0].row(0) == a and dst[0].row(1) == b


def test_a_piece_with_a_single_shape_is_handled():
    # the pass used to return at once unless two shapes were in play
    a = {SPINE: 0.9, PELV: 0.1}
    b = {SPINE: 0.3, PELV: 0.7}
    dst, src = _torso([a, b], [SEAM, SEAM], [SEAM, SEAM], [_alike(), _alike()])
    assert len(dst) == 1
    n, _ = _run(dst, src)
    assert n == 2


def test_a_third_vertex_at_the_seam_joins_the_same_row():
    # three UV islands meet at one corner: three twins, one row
    rows = [{SPINE: 0.9, PELV: 0.1}, {SPINE: 0.3, PELV: 0.7},
            {SPINE: 0.6, PELV: 0.4}]
    dst, src = _torso(rows, [SEAM] * 3, [SEAM] * 3, [_alike()] * 3)
    _run(dst, src)
    assert dst[0].row(0) == dst[0].row(1) == dst[0].row(2)


def test_a_row_that_would_hold_more_than_four_bones_is_capped_and_sums_to_one():
    a = {SPINE: 0.4, SPINE1: 0.3, PELV: 0.2, LTHIGH: 0.1}
    b = {BELLY: 0.4, SPINE: 0.3, PELV: 0.2, SPINE1: 0.1}
    carriers = {BELLY: 0.25, SPINE: 0.25, SPINE1: 0.25, LTHIGH: 0.25}
    dst = [_shape("Torso", [SEAM, SEAM], [a, b], filler=[carriers, carriers])]
    src = [_shape("Torso", [SEAM, SEAM], [_alike(), _alike()])]
    _run(dst, src)
    row = dst[0].row(0)
    assert len(row) <= 4 and abs(sum(row.values()) - 1.0) < 1e-6
    assert row == dst[0].row(1)


def test_the_switch_leaves_the_twins_alone(monkeypatch):
    monkeypatch.setattr(nc, "SEAM_TWIN_SKIN", False)
    a = {SPINE: 0.9, PELV: 0.1}
    b = {SPINE: 0.3, PELV: 0.7}
    dst, src = _torso([a, b], [SEAM, SEAM], [SEAM, SEAM], [_alike(), _alike()])
    n, saved = _run(dst, src)
    assert n == 0 and not saved
    assert dst[0].row(0) == a and dst[0].row(1) == b


def test_the_switch_is_on_by_default():
    assert nc.SEAM_TWIN_SKIN is True


def test_the_twin_pairs_of_a_source_shape_are_listed_once_and_in_order():
    verts = [(0, 0, 0), (5, 5, 5), (0, 0, 0), (0, 0, 1e-5), (9, 9, 9)]
    assert nw._source_twin_pairs(verts) == {(0, 2), (0, 3), (2, 3)}


def test_vertices_farther_apart_than_the_twin_distance_are_not_twins():
    assert nw._source_twin_pairs([(0, 0, 0), (0, 0, 0.01)]) == set()
    assert nw._source_twin_pairs([(0, 0, 0)]) == set()


def test_other_vertices_of_the_shape_keep_their_rows():
    a = {SPINE: 0.9, PELV: 0.1}
    b = {SPINE: 0.3, PELV: 0.7}
    far = {SPINE: 0.2, PELV: 0.8}
    dst, src = _torso([a, b, far], [SEAM, SEAM, (30.0, 0.0, 96.0)],
                      [SEAM, SEAM, (30.0, 0.0, 96.0)],
                      [_alike(), _alike(), {PELV: 1.0}])
    _run(dst, src)
    assert dst[0].row(2) == far
    assert np.allclose(dst[0].verts[2], (30.0, 0.0, 96.0))


def test_twins_in_a_later_shape_are_found_at_the_right_vertex_indices():
    # the pass numbers every vertex of every shape in one run; a wrong offset
    # joins the wrong vertices, or none
    a = {SPINE: 0.9, PELV: 0.1}
    b = {SPINE: 0.3, PELV: 0.7}
    other_pos = [(40.0, 0.0, 10.0), (41.0, 0.0, 10.0), (42.0, 0.0, 10.0)]
    other_rows = [{PELV: 1.0}, {PELV: 1.0}, {PELV: 1.0}]
    dst = [_shape("Pants", other_pos, other_rows),
           _shape("Torso", [SEAM, SEAM], [a, b])]
    src = [_shape("Pants", other_pos, other_rows),
           _shape("Torso", [SEAM, SEAM], [_alike(), _alike()])]
    _run(dst, src)
    assert dst[1].row(0) == dst[1].row(1)
    assert all(dst[0].row(i) == {PELV: 1.0} for i in range(3))
