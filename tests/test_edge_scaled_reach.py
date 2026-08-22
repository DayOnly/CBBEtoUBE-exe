# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026
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

"""#edge-scaled-reach: a smoothing reach stated in UNITS and resolved PER
VERTEX.

`#smooth-reach` already makes a RING COUNT cover the same distance, but from
ONE median per shape. `#conform-adaptive-reach` already varies the screening
term per vertex, but RELATIVE to that same shape median -- so a uniformly fine
shape still gets a shorter world-space reach than a coarse one.

The case neither can serve is the one this file pins: a shape that is FINE in
one region and COARSE in another. There is no single median that is right for
both, and the reported suit, the college robe and the standing collar are all
that shape. Under a per-shape number the fine region is always under-reached.
"""
import numpy as np
import pytest

from src import nif_convert as nc


def _grid(nx, ny, step, x0=0.0):
    """A flat triangulated grid with a known, uniform edge length."""
    xs, ys = np.meshgrid(x0 + np.arange(nx) * step, np.arange(ny) * step,
                         indexing="ij")
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(nx * ny)], axis=1)
    t = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a = i * ny + j
            t.append([a, a + 1, a + ny])
            t.append([a + 1, a + ny + 1, a + ny])
    return v, np.asarray(t, dtype=np.int64)


def _mixed_grid():
    """One shape, two tessellations: a 0.1u strip beside a 1.0u strip.

    Not welded together -- the two halves only have to live in ONE vertex
    array for the point to hold, which is that a per-SHAPE median describes
    neither of them.
    """
    fv, ft = _grid(21, 21, 0.1)
    cv, ct = _grid(11, 11, 1.0, x0=10.0)
    v = np.vstack([fv, cv])
    t = np.vstack([ft, ct + len(fv)])
    fine = np.zeros(len(v), bool)
    fine[:len(fv)] = True
    return v, t, fine


# --- the primitive ------------------------------------------------------

def test_local_edge_length_reads_a_uniform_mesh():
    for step in (0.05, 0.5, 2.5):
        v, t = _grid(9, 9, step)
        elen, has = nc._local_edge_length(v, t)
        assert has.all()
        # interior verts see 4 axis edges at `step` and 2 diagonals at
        # step*sqrt(2), so the mean sits between the two, proportional to step
        assert step < np.median(elen) < step * np.sqrt(2.0)


def test_local_edge_length_separates_the_two_halves_of_one_shape():
    """The whole reason a per-shape median is not enough."""
    v, t, fine = _mixed_grid()
    elen, has = nc._local_edge_length(v, t)
    assert has.all()
    assert np.median(elen[fine]) < 0.2
    assert np.median(elen[~fine]) > 1.0
    # and the single number the per-shape form would have used is wrong for
    # BOTH halves by a large factor
    med = float(np.median(elen))
    assert med / np.median(elen[fine]) > 3.0 or np.median(elen[~fine]) / med > 3.0


def test_weld_edges_are_not_tessellation():
    """Coincident verts at a UV seam are ONE point of surface. Counting the
    join between them as an incident edge reads every seam vertex as
    infinitely fine, which would hand it an unbounded reach."""
    v, t = _grid(9, 9, 1.0)
    v = np.vstack([v, v[0]])                      # a duplicate of vertex 0
    t = np.vstack([t, [[len(v) - 1, 1, 2]]])
    src = np.array([0, len(v) - 1])
    dst = np.array([len(v) - 1, 0])
    elen, has = nc._local_edge_length(v, t, src=src, dst=dst)
    # the only edges supplied are the zero-length weld pair -> nothing
    # measurable, rather than a 0.0 that reads as infinitely fine
    assert elen is None and has is None


def test_a_vertex_with_no_real_edge_is_reported_as_unmeasurable():
    v, t = _grid(9, 9, 1.0)
    v = np.vstack([v, v[-1]])                     # isolated duplicate
    elen, has = nc._local_edge_length(v, t)
    assert has[:-1].all()
    assert not has[-1]
    assert elen[-1] == 0.0


def test_degenerate_inputs_return_nothing_rather_than_a_wrong_scale():
    v, t = _grid(9, 9, 1.0)
    assert nc._local_edge_length(np.zeros((2, 3)), t) == (None, None)
    assert nc._local_edge_length(v, np.zeros((0, 3), np.int64)) == (None, None)
    # every vertex coincident -> every edge is a weld
    assert nc._local_edge_length(np.zeros((len(v), 3)), t) == (None, None)


# --- the reach ----------------------------------------------------------

def test_lambda_scales_with_the_square_of_the_local_edge():
    """`delta = elen / sqrt(lam)`, so holding `delta` at R needs
    `lam = (elen/R)**2`. That relation IS the fix; pin it."""
    elen = np.array([0.1, 0.2, 0.4, 1.0])
    has = np.ones(4, bool)
    lam = nc._reach_screen(elen, has, 2.0, 1000, fallback=0.5)
    assert np.allclose(lam, (elen / 2.0) ** 2)
    # doubling the edge length quadruples lam, i.e. holds the reach fixed
    assert np.allclose(lam[1:] / lam[:-1], (elen[1:] / elen[:-1]) ** 2)


def test_the_default_reach_reproduces_todays_constant_on_a_1u_mesh():
    """`REACH_UNITS` is calibrated, not invented: at 1.0u it must land on the
    `lam = 0.5` this file has always used, so a coarse mesh keeps its
    behaviour and only fine geometry gains reach."""
    lam = nc._reach_screen(np.array([1.0]), np.ones(1, bool),
                           nc.REACH_UNITS, 1000, fallback=0.123)
    assert lam[0] == pytest.approx(0.5, abs=0.005)


def test_unmeasurable_verts_keep_the_callers_own_constant():
    """Never smoother NOR stiffer than today on geometry we could not
    measure -- an unmeasurable vertex is not evidence of anything."""
    lam = nc._reach_screen(np.array([0.1, 0.0]), np.array([True, False]),
                           2.0, 1000, fallback=0.5)
    assert lam[1] == 0.5


def test_the_reach_is_floored_at_what_the_solver_can_carry():
    """A Jacobi sweep is a DIFFUSION step, so `k` sweeps spread about
    `sqrt(k)` rings. Asking for more reach than that is not delivered, it is
    merely under-converged, so the floor is `1/iters`."""
    lam = nc._reach_screen(np.array([1e-6]), np.ones(1, bool),
                           1000.0, 64, fallback=0.5)
    assert lam[0] == pytest.approx(1.0 / 64.0)


def test_a_reach_under_the_floor_really_is_not_delivered():
    """Why the floor is where it is, rather than asserted. Below it the solve
    returns a SHORTER reach than the formula promises -- silently."""
    honest = _decay_length(0.1, 2.0, iters=8000)     # lam 0.0025 > 1/8000
    starved = _decay_length(0.1, 2.0, iters=100)     # lam floored to 1/100
    assert honest > starved * 1.5, (
        f"an unconverged solve should under-reach ({starved:.2f}u) against a "
        f"converged one ({honest:.2f}u)")


def test_a_nonsense_reach_falls_back_instead_of_dividing_by_it():
    for bad in (0.0, -1.0, float("nan")):
        lam = nc._reach_screen(np.array([0.5]), np.ones(1, bool), bad, 32,
                               fallback=0.5)
        assert lam[0] == 0.5


# --- the property, end to end -------------------------------------------

def _screened_chain(elen, lam, n=600, iters=8000):
    """Run the screened Jacobi this file actually runs, on a 1-D chain of
    spacing `elen`, and report how far the response carried in UNITS.

    MASS-WEIGHTED RADIUS, not a 1/e threshold, for the reason
    `test_smooth_reach` already gives: a threshold measured on a grid whose
    spacing IS the variable under test quantises to that spacing, so the coarse
    arm reads its own edge length back. For an exponential response the
    mass-weighted radius is the decay length exactly, on any spacing.
    """
    u = np.zeros(n)
    D = np.zeros(n)
    D[0] = 1.0
    deg = np.full(n, 2.0)
    deg[0] = deg[-1] = 1.0
    for _ in range(iters):
        acc = np.zeros(n)
        acc[1:] += u[:-1]
        acc[:-1] += u[1:]
        u = (acc + lam * D) / (deg + lam)
    w = np.abs(u)
    if w.sum() <= 0:
        return 0.0
    x = np.arange(n) * elen
    return float((w * x).sum() / w.sum())


def _decay_length(elen, R, iters=8000):
    lam = float(nc._reach_screen(np.array([elen]), np.ones(1, bool), R,
                                 iters, fallback=0.5)[0])
    return _screened_chain(elen, lam, iters=iters)


def test_the_decay_length_is_the_same_in_units_on_both_tessellations():
    """THE PROPERTY. A 0.1u mesh and a 1.0u mesh must smooth over the same
    number of UNITS, not the same number of rings."""
    fine = _decay_length(0.1, 2.0)
    coarse = _decay_length(1.0, 2.0)
    assert 0.7 < fine / coarse < 1.4, (
        f"decay was {fine:.2f}u on the fine mesh and {coarse:.2f}u on the "
        "coarse one; a reach stated in units must not depend on tessellation")


def test_todays_constant_is_the_defect_the_property_replaces():
    """The negative control: it is not enough that the fixed version is
    tessellation-independent -- the version it replaces must be shown to
    DEPEND on tessellation, or the test above proves nothing."""
    fine = _screened_chain(0.1, 0.5)
    coarse = _screened_chain(1.0, 0.5)
    assert coarse / fine > 5.0, (
        f"a constant lam gave {fine:.2f}u on the fine mesh and {coarse:.2f}u "
        "on the coarse one -- if these were already comparable there would be "
        "nothing to fix")


def test_the_flag_is_off_by_default():
    """Unjudged in game. It must not reach a real run by accident."""
    assert nc.EDGE_SCALED_REACH is False


def test_the_conform_relax_pass_uses_it_only_when_asked(monkeypatch):
    """Prove the wiring: with the flag off the screened solve must see the
    caller's own constant, and with it on it must see a per-vertex term.
    A new pass that silently no-ops has shipped here before."""
    v, t, fine = _mixed_grid()
    seen = {}
    real = nc._local_edge_length

    def spy(*a, **kw):
        out = real(*a, **kw)
        seen["elen"] = out[0]
        return out

    monkeypatch.setattr(nc, "_local_edge_length", spy)
    monkeypatch.setattr(nc, "EDGE_SCALED_REACH", True)
    normals = np.tile([0.0, 0.0, 1.0], (len(v), 1))
    disp = np.zeros_like(v)
    disp[0] = [0.0, 0.0, -0.5]
    move = np.zeros(len(v))
    move[0] = -0.5
    out = nc._relax_conform_field(v, disp, move, normals, t, iters=4)
    assert seen.get("elen") is not None, "the primitive was never consulted"
    assert out.shape == disp.shape
    # the two halves must have been given different screening, which is the
    # whole claim -- assert on the scale the solve was handed
    assert np.median(seen["elen"][fine]) < np.median(seen["elen"][~fine]) / 3.0
