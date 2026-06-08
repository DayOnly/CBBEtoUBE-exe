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

"""Guard for repair_collapsed_tris: un-pinch triangles the converter's vertex
ops collapsed to zero area (the 'mangled fabric' symptom), WITHOUT disturbing
geometry that was already degenerate in the source (folded seams)."""
import numpy as np
from src.nif_convert import repair_collapsed_tris


def _area(v, t):
    a = v[t[:, 0]]; b = v[t[:, 1]]; c = v[t[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def test_collapsed_tri_is_restored():
    # source: a healthy right triangle
    src = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    tris = np.array([[0, 1, 2]])
    # converted: the ops pinched all 3 verts onto ~one point (zero area)
    cur = np.array([[5.0, 5.0, 5.0], [5.01, 5.0, 5.0], [5.0, 5.01, 5.0]])
    out, n = repair_collapsed_tris(cur, src, tris)
    assert n == 1
    assert _area(out, tris)[0] > 0.1                 # area restored
    # centroid (fitted location) preserved
    assert np.allclose(out.mean(0), cur.mean(0), atol=1e-6)


def test_source_degenerate_tri_left_alone():
    # source ALSO degenerate (a folded seam) -> must NOT be "repaired"
    src = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])  # collinear
    tris = np.array([[0, 1, 2]])
    cur = np.array([[5.0, 5.0, 5.0], [5.0, 5.0, 5.0], [5.0, 5.0, 5.0]])
    out, n = repair_collapsed_tris(cur, src, tris)
    assert n == 0
    assert np.allclose(out, cur)                     # untouched


def test_healthy_tri_untouched():
    src = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    tris = np.array([[0, 1, 2]])
    cur = src + np.array([2.0, 0.0, 0.0])            # translated but healthy
    out, n = repair_collapsed_tris(cur, src, tris)
    assert n == 0
    assert np.allclose(out, cur)


def test_huge_collapse_capped():
    # a vert whose restore would jump it across the mesh (> max_fix) is skipped
    src = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [0.0, 50.0, 0.0]])
    tris = np.array([[0, 1, 2]])
    cur = np.array([[0.0, 0.0, 0.0], [0.01, 0.0, 0.0], [0.0, 0.01, 0.0]])
    out, n = repair_collapsed_tris(cur, src, tris, max_fix=3.0)
    assert n == 1                                    # counted as collapsed
    # no vert moved more than max_fix
    assert np.all(np.linalg.norm(out - cur, axis=1) <= 3.0 + 1e-6)
