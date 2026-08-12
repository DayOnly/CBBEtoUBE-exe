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

"""#strap-scale-uniform -- a belt may GROW, it may BEND, it may not CRUMPLE.

Reported in game twice on the same garment. Measured with edge lengths, which is
the frame-free way to separate bending from stretching: the strap's edges ran
0.517x to 1.514x the author's, mean absolute deviation 0.219 against 0.060 for the
chest plate on the same piece, 78% of edges outside 5%.

THE CENTRAL INVARIANT, and the reason this pass is not an isometry solver: the
strap's MEDIAN ratio was 1.025, which is CORRECT -- a belt fitted to a wider body
should be slightly longer. So a UNIFORMLY grown strap must come out untouched even
though it trips the distortion gate. `test_uniform_growth_is_a_NO_OP` is that
test, and it is the one that would catch a rewrite quietly turning this back into
"restore the author's lengths".
"""
import numpy as np
import pytest

from src import nif_convert as nc


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def _strap(n=12, step=2.0, width=1.0):
    """A long thin strip: n segments, two verts across. A belt, essentially.

    Deliberately thin, because that is where every rigid-fit-based test in this
    project degenerates -- a 1-ring here is nearly collinear.
    """
    verts, tris = [], []
    for i in range(n + 1):
        verts += [[i * step, 0.0, 0.0], [i * step, width, 0.0]]
    for i in range(n):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        tris += [[a, c, d], [a, d, b]]
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, np.int64)


def _cube(origin, size):
    o = np.asarray(origin, dtype=np.float64)
    c = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                  [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
                 dtype=np.float64) * size + o
    t = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
                  [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
                  [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0]],
                 dtype=np.int64)
    return c, t


def _split_quad_grid(n, step):
    """One flat surface, but every quad carries its OWN four vertices.

    How a real mesh is authored when the author wants hard edges: corners are
    positionally COINCIDENT across quads but are separate indices, so the triangle
    graph alone reports n*n disconnected pieces.
    """
    verts, tris = [], []
    for i in range(n):
        for j in range(n):
            base = len(verts)
            x0, y0 = i * step, j * step
            verts += [[x0, y0, 0.0], [x0 + step, y0, 0.0],
                      [x0 + step, y0 + step, 0.0], [x0, y0 + step, 0.0]]
            tris += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, np.int64)


def _crumple(v, amount):
    """Squash and stretch alternate segments: pure VARIANCE, median ratio 1.0.

    Column i slides by +/-amount, so the edges along the strap alternate short and
    long while the strap's overall length is unchanged. That is the defect with the
    growth held out, which is what lets the tests below attribute an outcome.
    """
    out = v.copy()
    col = np.arange(len(v)) // 2
    out[:, 0] += amount * np.where(col % 2 == 0, 1.0, -1.0)
    return out


def _dev(sv, ov, t):
    """mean |edge ratio - 1|, the same quantity the pass gates on."""
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    ls = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
    lo = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
    m = ls > 1e-6
    return float(np.abs(lo[m] / ls[m] - 1.0).mean())


@pytest.fixture(autouse=True)
def _pass_on(monkeypatch):
    monkeypatch.setattr(nc, "STRAP_SCALE_UNIFORM", True, raising=False)


# --------------------------------------------------------------------------
# THE INVARIANT: it uniformises, it does not restore
# --------------------------------------------------------------------------
def test_uniform_growth_is_a_NO_OP():
    """A belt on a wider waist is LONGER, and that is not a defect.

    The grown strap trips the distortion gate (mean deviation 0.20 >= 0.15), so
    this is not passing by accident of the gate -- the pass runs and finds every
    edge already at the length its neighbourhood agrees on.
    """
    sv, t = _strap()
    ov = (sv - sv.mean(0)) * 1.20 + sv.mean(0)
    assert _dev(sv, ov, t) >= nc.STRAP_SCALE_MIN_DEV, \
        "the gate must not be what makes this a no-op -- otherwise the test is vacuous"
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    assert moved == 0, "uniform growth must survive untouched"
    assert np.array_equal(np.asarray(got), ov)


def test_a_crumpled_strap_is_uniformised():
    sv, t = _strap()
    ov = _crumple(sv, 0.5)                 # edges alternate 1.0 and 3.0
    before = _dev(sv, ov, t)
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    after = _dev(sv, np.asarray(got), t)
    assert moved > 0
    assert after < 0.6 * before, f"crumple must fall substantially: {before:.3f} -> {after:.3f}"


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS
# --------------------------------------------------------------------------
def test_an_undistorted_shape_is_untouched():
    """And the control can fire: the same strap crumpled DOES get repaired."""
    sv, t = _strap()
    ov = (sv - sv.mean(0)) * 1.02 + sv.mean(0)      # 2% -- below the gate
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    assert moved == 0 and np.array_equal(np.asarray(got), ov)

    _, fired = nc._uniformise_local_scale(sv, _crumple(sv, 0.5), t)
    assert fired > 0, (
        "control is inert -- if the crumpled strap is not repaired either, the "
        "assertion above says nothing about the gate")


def test_a_MIS_SCALED_shape_is_refused(monkeypatch):
    """21x the author's size is not crumpled, it is wrong. Uniformising it would
    produce an evenly ballooned shape: better by this metric, no less broken.

    Mis-scaled AND crumpled, deliberately. A uniformly oversized strap is a no-op
    through the uniformity mechanism itself, so testing the cap with one proves
    nothing about the cap -- the first draft of this test did exactly that and
    passed with the cap removed.
    """
    sv, t = _strap()
    big = (sv - sv.mean(0)) * 3.0 + sv.mean(0)
    ov = _crumple(big, 0.5)                 # oversized AND uneven
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    assert moved == 0, "median growth past the cap must be refused outright"
    assert np.array_equal(np.asarray(got), ov)

    # ...and the control fires: raise the cap and this same input IS repaired,
    # which is what makes the refusal attributable to the cap.
    monkeypatch.setattr(nc, "STRAP_SCALE_MAX_GROWTH", 100.0, raising=False)
    _, fired = nc._uniformise_local_scale(sv, ov, t)
    assert fired > 0, (
        "control is inert -- with the cap lifted this input must be uniformised, "
        "or the refusal above is not the cap's doing")


def test_small_fittings_are_LEFT_ALONE():
    """A stud is not a continuous surface, so this pass has no business on it.

    Tried, and measured worse: letting it reach the fittings took the reported
    buckle's edge deviation from 0.364 to 0.454 and a chest plate's from 0.060 to
    0.090. A cluster of separate small objects has a scale field that legitimately
    VARIES between them, and smoothing pushes each toward a consensus that belongs
    to its neighbours rather than to it.
    """
    strap, ts = _strap()
    cube, tc = _cube((60.0, 0.0, 0.0), 3.0)         # a stud, well clear of the strap
    sv = np.vstack([strap, cube])
    t = np.vstack([ts, tc + len(strap)])
    ov = sv.copy()
    ov[:len(strap)] = _crumple(strap, 0.5)
    ov[len(strap):] = _crumple(cube, 0.4)           # distort the stud too

    got, moved = nc._uniformise_local_scale(sv, ov, t)
    got = np.asarray(got)
    assert moved > 0, "the strap must still be repaired"
    assert np.array_equal(got[len(strap):], ov[len(strap):]), \
        "a small welded component is a fitting and must not be touched here"


def test_a_fitting_is_told_apart_from_a_strap_by_ONE_threshold():
    """One definition of "a part", so nothing can drift from anything else."""
    strap, ts = _strap()
    cube, tc = _cube((60.0, 0.0, 0.0), 3.0)
    sv = np.vstack([strap, cube])
    t = np.vstack([ts, tc + len(strap)])
    mask = nc._small_element_mask(sv, t)
    assert mask is not None
    assert mask[len(strap):].all(), "the 5.2u stud is a small element"
    assert not mask[:len(strap)].any(), "the 24u strap is not"


def test_a_shading_split_surface_WELDS_into_one_object(monkeypatch):
    """A topological component is NOT an object, and everything here depends on it.

    Authors split verts at hard edges, so one surface arrives as many topological
    pieces. Censused: a stocking reads as 865 components on 3459 verts. Without
    the weld, `_small_element_mask` would call every one of those fragments a
    "fitting" and this pass would refuse to repair any real garment.
    """
    src, tris = _split_quad_grid(5, 2.0)            # one 10u surface, 25 quads
    lab, ncomp = nc._welded_components(src, tris)
    assert lab is not None
    assert ncomp == 1, f"one surface must weld to one object, got {ncomp}"
    assert not nc._small_element_mask(src, tris).any(), \
        "and a 14u welded surface is not a fitting"

    # ...and the control can fire: without the weld it fragments per quad.
    monkeypatch.setattr(nc, "SMALL_ELEMENT_WELD", 0.0, raising=False)
    _, unwelded = nc._welded_components(src, tris)
    assert unwelded == 25, (
        f"control is inert -- unwelded this must fragment, got {unwelded}")


# --------------------------------------------------------------------------
# it must never be able to break a file
# --------------------------------------------------------------------------
def test_it_does_not_walk_the_shape_off_the_body():
    """The anchor exists so satisfying edge lengths cannot translate the shape."""
    sv, t = _strap()
    ov = _crumple(sv, 0.5)
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    got = np.asarray(got)
    assert moved > 0
    drift = float(np.linalg.norm(got.mean(0) - ov.mean(0)))
    assert drift < 0.05, f"centroid drifted {drift:.3f}u"
    assert float(np.linalg.norm(got - ov, axis=1).max()) < 1.0, \
        "no vertex may be relocated further than the crumple it is undoing"


def test_flag_off_is_a_noop(monkeypatch):
    monkeypatch.setattr(nc, "STRAP_SCALE_UNIFORM", False, raising=False)
    sv, t = _strap()
    ov = _crumple(sv, 0.5)
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    assert moved == 0 and np.array_equal(np.asarray(got), ov)


@pytest.mark.parametrize("bad", [np.empty((0, 3)), np.zeros((5, 3))])
def test_malformed_input_returns_the_input_unchanged(bad):
    sv, t = _strap()
    got, moved = nc._uniformise_local_scale(sv, bad, t)
    assert moved == 0 and np.asarray(got).shape == np.asarray(bad).shape


def test_vertex_count_and_order_are_preserved():
    sv, t = _strap()
    ov = _crumple(sv, 0.5)
    got, moved = nc._uniformise_local_scale(sv, ov, t)
    assert moved > 0 and np.asarray(got).shape == ov.shape
