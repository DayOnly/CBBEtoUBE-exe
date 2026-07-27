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

"""Ray-based skin-exposure metric -- synthetic cases with known answers.

This metric replaced two that gave WRONG answers on real meshes:

  * nearest-vertex distance -- reports how far the closest armour VERTEX is. It
    cannot say inside vs outside, and it never sees the surface between vertices.
    Every "bust clearance" number produced before this was a distance, not evidence
    of coverage.
  * signed distance via the nearest triangle's normal -- flips sign unpredictably on
    a shell with front AND inner faces. It reported 135/1110 nipple verts "outside"
    a cuirass whose surface actually sits 2.4u IN FRONT of the nipple; the ray test
    says 0/1110, and an independent y-extent check agrees with the ray test.

Validated on real geometry before use: body regions the armour does not cover
(lower legs, shins) return 100% exposed; regions it does cover (mid-chest, belly,
upper back) return 0%. A metric that only ever returns 0 would look identical to
success, which is why the positive control matters.
"""
import numpy as np

from scripts.verify_skin_exposure import ray_blocked


def _quad(cx, cy, cz, size=10.0, axis="z"):
    """Two triangles forming a square plate centred at (cx,cy,cz)."""
    h = size / 2.0
    if axis == "z":                       # plate lies in the XY plane
        v = np.array([[cx - h, cy - h, cz], [cx + h, cy - h, cz],
                      [cx + h, cy + h, cz], [cx - h, cy + h, cz]], float)
    else:                                 # plate lies in the XZ plane (faces +Y)
        v = np.array([[cx - h, cy, cz - h], [cx + h, cy, cz - h],
                      [cx + h, cy, cz + h], [cx - h, cy, cz + h]], float)
    return v, np.array([[0, 1, 2], [0, 2, 3]], np.int32)


def test_ray_straight_into_a_plate_is_blocked():
    V, t = _quad(0, 0, 5.0)
    o = np.array([[0.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, 1.0]])
    assert ray_blocked(o, d, V, t)[0]


def test_ray_pointing_away_from_the_plate_escapes():
    """Direction matters: skin facing away from the armour is not covered by it."""
    V, t = _quad(0, 0, 5.0)
    o = np.array([[0.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, -1.0]])
    assert not ray_blocked(o, d, V, t)[0]


def test_ray_missing_the_plate_edge_escapes():
    """The plate spans x in [-5, 5]; a ray at x=20 must miss it."""
    V, t = _quad(0, 0, 5.0)
    o = np.array([[20.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, 1.0]])
    assert not ray_blocked(o, d, V, t)[0]


def test_plate_beyond_tmax_does_not_count():
    """A distant plate is not 'covering' the skin -- guards a runaway tmax."""
    V, t = _quad(0, 0, 500.0)
    o = np.array([[0.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, 1.0]])
    assert not ray_blocked(o, d, V, t, tmax=25.0)[0]


def test_plate_behind_the_origin_does_not_count():
    """t must be positive: geometry behind the skin does not cover it."""
    V, t = _quad(0, 0, -5.0)
    o = np.array([[0.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, 1.0]])
    assert not ray_blocked(o, d, V, t)[0]


def test_a_ray_through_a_gap_between_plates_escapes():
    """THE case both replaced metrics got wrong: vertices near the skin on either
    side, nothing in between. A nearest-VERTEX metric reports a small gap and calls
    it covered; the ray goes straight through."""
    V1, t1 = _quad(-8.0, 0, 5.0, size=6.0)
    V2, t2 = _quad(+8.0, 0, 5.0, size=6.0)
    V = np.vstack([V1, V2])
    t = np.vstack([t1, t2 + len(V1)])
    o = np.array([[0.0, 0.0, 0.0]])       # centred in the gap
    d = np.array([[0.0, 0.0, 1.0]])
    assert not ray_blocked(o, d, V, t)[0]
    # ...and a ray aimed at a plate is still blocked, so the mesh itself is sane
    assert ray_blocked(np.array([[-8.0, 0.0, 0.0]]), d, V, t)[0]


def test_double_sided_shell_does_not_confuse_it():
    """A cuirass has a front AND an inner face. This is exactly what broke the
    signed-normal metric: a point between the two faces is near both, and whichever
    sample is nearest decided the sign. A ray is unambiguous -- it is blocked."""
    front, tf = _quad(0, 2.0, 0, size=10.0, axis="y")
    inner, ti = _quad(0, 1.0, 0, size=10.0, axis="y")
    V = np.vstack([front, inner])
    t = np.vstack([tf, ti + len(front)])
    o = np.array([[0.0, 0.0, 0.0]])       # skin behind both faces
    d = np.array([[0.0, 1.0, 0.0]])
    assert ray_blocked(o, d, V, t)[0], "skin under a two-layer shell is covered"


def test_batch_shape_and_independence():
    V, t = _quad(0, 0, 5.0)
    o = np.array([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]])
    got = ray_blocked(o, d, V, t)
    assert got.shape == (3,)
    assert list(got) == [True, False, False]


def test_degenerate_triangle_does_not_crash_or_block():
    V = np.array([[0.0, 0.0, 5.0], [0.0, 0.0, 5.0], [0.0, 0.0, 5.0]])
    t = np.array([[0, 1, 2]], np.int32)
    o = np.array([[0.0, 0.0, 0.0]])
    d = np.array([[0.0, 0.0, 1.0]])
    assert not ray_blocked(o, d, V, t)[0]
