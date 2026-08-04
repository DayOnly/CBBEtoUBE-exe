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

"""#seam-weld-self -- vertices coincident in the SOURCE must stay coincident.

A UV/normal seam is several vertices at one position with different normals.
The fit passes push each along its own normal, so the seam opens into a gap and
you see the unlit interior through it. Measured on a real cuirass: 237 of 739
seam groups split, worst 3.3u, as a mirrored pair at the rear waist.
"""
import numpy as np

from src import nif_convert as nc


def test_a_split_seam_is_closed():
    src = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0],   # one seam pair
                    [5.0, 0.0, 0.0]])
    out = np.array([[-1.5, 0.0, 0.0], [1.5, 0.0, 0.0],  # pushed 3u apart
                    [5.0, 0.0, 0.0]])
    fixed, moved = nc._weld_source_coincident_verts(src, out)
    assert moved == 2
    assert np.allclose(fixed[0], fixed[1]), "seam still open"
    assert np.allclose(fixed[0], [0.0, 0.0, 0.0]), "should land on the centroid"


def test_non_seam_vertices_are_never_touched():
    src = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
    out = np.array([[-1.5, 0.0, 0.0], [1.5, 0.0, 0.0], [7.3, 1.1, -2.0]])
    fixed, _ = nc._weld_source_coincident_verts(src, out)
    assert np.array_equal(fixed[2], out[2])


def test_an_intact_seam_is_a_no_op():
    """A seam the passes moved TOGETHER is already correct and must survive
    bit-for-bit -- the weld may close gaps, not relocate healthy geometry."""
    src = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
    out = np.array([[2.0, 1.0, 0.0], [2.0, 1.0, 0.0], [6.0, 0.0, 0.0]])
    fixed, moved = nc._weld_source_coincident_verts(src, out)
    assert moved == 0
    assert np.array_equal(fixed, out)


def test_a_mesh_with_no_seams_is_a_no_op():
    src = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    out = src + 0.5
    fixed, moved = nc._weld_source_coincident_verts(src, out)
    assert moved == 0 and np.array_equal(fixed, out)


def test_groups_larger_than_two_all_collapse():
    """Real seams routinely carry 3 or 4 duplicates (the cuirass's worst had 4)."""
    src = np.zeros((4, 3))
    out = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0],
                    [0.0, 3.0, 0.0], [0.0, 0.0, 3.0]])
    fixed, moved = nc._weld_source_coincident_verts(src, out)
    assert moved == 4
    assert np.allclose(fixed, fixed[0])
    assert np.allclose(fixed[0], out.mean(0))


def test_mismatched_lengths_are_refused_not_guessed():
    src = np.zeros((3, 3))
    out = np.zeros((2, 3))
    fixed, moved = nc._weld_source_coincident_verts(src, out)
    assert moved == 0 and fixed.shape == out.shape


def test_ships_on_with_a_kill_switch():
    assert nc.SEAM_WELD_SELF is True
