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

"""`_ball_mean` -- gap-aware follow's smoothing without building every ball.

The list form (`vals[query_ball_point(V, r)].mean()` per vert) built 676 million
pairs on a dense worn prop and set a full reconvert's finish time. All-0 and
all-1 balls are exact by a nearest-neighbour test; mixed balls are summed in
chunks (same members, another summation order)."""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_convert_weights as nw      # noqa: E402


def _list_mean(V, vals, r):
    return np.array([vals[x].mean() for x in cKDTree(V).query_ball_point(V, r)])


def _case(seed):
    rng = np.random.default_rng(seed)
    V = rng.uniform(0, 20, (5000, 3))
    vals = np.where(V[:, 0] < 8, 1.0, np.where(V[:, 0] > 12, 0.0, (V[:, 0] - 8) / 4))
    vals[rng.random(len(V)) < 0.01] = 0.37
    return V, vals


def test_matches_the_list_mean():
    for seed in range(3):
        V, vals = _case(seed)
        assert np.abs(nw._ball_mean(V, vals, 3.0) - _list_mean(V, vals, 3.0)).max() < 1e-12


def test_uniform_balls_are_exact():
    V, vals = _case(9)
    ref = _list_mean(V, vals, 3.0)
    out = nw._ball_mean(V, vals, 3.0)
    for v in (0.0, 1.0):
        uni = ref == v
        assert uni.any()
        assert np.array_equal(out[uni], ref[uni])


def test_a_neighbour_exactly_at_the_radius_counts():
    """A one-valued vert exactly r from a zero: the zero's ball holds it, so its
    mean is not 0 -- the uniform shortcut must not claim it."""
    V = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]])
    vals = np.array([0.0, 1.0])
    ref = _list_mean(V, vals, 3.0)
    assert np.array_equal(nw._ball_mean(V, vals, 3.0), ref)
