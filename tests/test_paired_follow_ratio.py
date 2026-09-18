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

"""The two readings of the over-follow number, on data whose answer is known. #paired-follow

The claim ("the garment over-follows the body 1.25-3.7x") and its refutation
("paired, it is 1.00") are the same measurement taken two ways: mean-over-band
against nearest-vertex. These tests pin what each one does, on synthetic bands
where the true answer is arithmetic, so the reading over an arm can be trusted to
separate the two.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

pytest.importorskip("scipy")
from scripts.analysis.paired_follow_ratio import ratios  # noqa: E402


def _band(v):
    """Everything in these fixtures is in the band."""
    return np.ones(len(v), bool)


def _grid(n=40, z=95.0, off=0.0):
    xs = np.linspace(-10, 10, n)
    return np.column_stack([xs, np.full(n, off), np.full(n, z)])


def test_a_garment_that_tracks_its_body_point_reads_one():
    body = _grid()
    garm = _grid(off=2.0)                  # sits 2u off, same points
    d = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    r = ratios(body, d, garm, d.copy(), _band)
    assert r["paired"] == pytest.approx(1.0)
    assert r["unpaired"] == pytest.approx(1.0)


def test_a_garment_that_moves_twice_as_far_reads_two():
    body = _grid()
    garm = _grid(off=2.0)
    d = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    r = ratios(body, d, garm, d * 2.0, _band)
    assert r["paired"] == pytest.approx(2.0)


def test_the_two_readings_disagree_when_the_bands_cover_different_motion():
    """THE POINT OF THE ITEM. Every garment vertex tracks the body point under it
    exactly -- paired 1.0 -- but the garment covers only the half of the band
    that moves most, so the band-mean reading calls it over-follow."""
    body = _grid(n=40)
    body_d = np.zeros((40, 3))
    body_d[:20, 1] = 0.5                    # half the band barely moves
    body_d[20:, 1] = 2.0
    garm = body[20:].copy() + np.array([0.0, 2.0, 0.0])   # covers the moving half only
    garm_d = body_d[20:].copy()
    r = ratios(body, body_d, garm, garm_d, _band)
    assert r["paired"] == pytest.approx(1.0), r
    assert r["unpaired"] > 1.4, r           # 2.0 / mean(0.5, 2.0) = 1.6
    assert r["unpaired"] / r["paired"] > 1.4


def test_a_body_that_does_not_move_yields_no_ratio():
    """Control: dividing by a body that stands still is how a harness invents a
    number. It returns nothing instead."""
    body = _grid()
    garm = _grid(off=2.0)
    still = np.zeros((len(body), 3))
    moving = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    assert ratios(body, still, garm, moving, _band) is None


def test_the_band_mask_decides_which_vertices_count():
    """The mask is the measurement's population. Read the whole shape instead and
    the number answers a different question."""
    body = np.vstack([_grid(n=20, z=95.0), _grid(n=20, z=60.0)])
    body_d = np.zeros((40, 3))
    body_d[:20, 1] = 2.0                    # the band moves
    body_d[20:, 1] = 1.0                    # below it moves half as much
    garm = body.copy() + np.array([0.0, 2.0, 0.0])
    garm_d = np.tile([0.0, 2.0, 0.0], (40, 1))

    def band(v):
        return np.asarray(v)[:, 2] > 80.0

    inside = ratios(body, body_d, garm, garm_d, band)
    assert inside["paired"] == pytest.approx(1.0) and inside["garment_band"] == 20
    everything = ratios(body, body_d, garm, garm_d, _band)
    assert everything["paired"] > 1.4, "off-band vertices change the answer"


def test_a_band_that_only_twitches_yields_no_ratio():
    """A per-vertex floor is not enough: every pair can clear it while the band
    as a whole moves 0.02u, which is noise to divide by."""
    body = _grid()
    garm = _grid(off=2.0)
    twitch = np.tile([0.0, 0.02, 0.0], (len(body), 1))
    assert ratios(body, twitch, garm, twitch * 3.0, _band) is None


def test_one_wild_vertex_does_not_move_the_median():
    body = _grid(n=21)
    garm = body.copy() + np.array([0.0, 2.0, 0.0])
    body_d = np.tile([0.0, 1.0, 0.0], (21, 1))
    garm_d = body_d.copy()
    garm_d[0, 1] = 50.0                     # one runaway vertex
    r = ratios(body, body_d, garm, garm_d, _band)
    assert r["paired"] == pytest.approx(1.0), "a mean would read ~3.3 here"


def test_a_garment_over_the_still_part_of_a_moving_band_yields_no_ratio():
    """The band moves, so the band-level floor passes -- but every vertex this
    garment covers stands still, so there is nothing to divide by. Reporting the
    median of an empty set is how a harness returns a number for no data."""
    body = np.vstack([_grid(n=20, z=95.0), _grid(n=20, z=95.0, off=8.0)])
    body_d = np.zeros((40, 3))
    body_d[:20, 1] = 2.0                    # only the first cluster moves
    garm = body[20:].copy() + np.array([0.0, 2.0, 0.0])   # covers the still one
    garm_d = np.tile([0.0, 2.0, 0.0], (20, 1))
    assert ratios(body, body_d, garm, garm_d, _band) is None


def test_vertices_whose_partner_barely_moves_are_dropped_not_divided_by():
    body = _grid(n=40)
    body_d = np.zeros((40, 3))
    body_d[:20, 1] = 0.001                  # noise, not motion
    body_d[20:, 1] = 2.0
    garm = body.copy() + np.array([0.0, 2.0, 0.0])
    garm_d = np.tile([0.0, 2.0, 0.0], (40, 1))
    r = ratios(body, body_d, garm, garm_d, _band)
    assert r["n_pairs"] == 20 and r["dropped"] == pytest.approx(0.5)
    assert r["paired"] == pytest.approx(1.0), "the noise pairs would have read 2000x"
