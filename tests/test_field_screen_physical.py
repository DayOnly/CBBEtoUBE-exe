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

"""#field-screen-physical -- the clearance solve's reach must be in WORLD units.

`test_the_fold_driver_no_longer_tracks_tessellation` is the load-bearing one.
Everything else here guards the blast radius: a coarse mesh must be untouched, a
weld seam must not be mistaken for fine tessellation, and the helper must fail
soft. The property under test is that TWO MESHES OF THE SAME SURFACE, differing
only in how finely they are cut, get the same displacement GRADIENT PER WORLD
UNIT -- which is the quantity that folds a triangle. Measuring the reach instead
would pass a solve that spread the field far but still stepped sharply at the
constraint edge, and measuring peak amplitude reports a wider feather as a
narrower one.
"""
import numpy as np
import pytest

from src import nif_convert as nc  # noqa: F401  (registers the module _nc() finds)
from src import nif_convert_fitgeom as fg


def _grid(edge, span=8.0):
    """A flat patch of fixed WORLD size, cut at `edge`. Same surface each time."""
    k = int(round(span / edge)) + 1
    x = np.linspace(-span / 2, span / 2, k)
    X, Y = np.meshgrid(x, x)
    V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
    idx = np.arange(k * k).reshape(k, k)
    t = []
    for i in range(k - 1):
        for j in range(k - 1):
            a, b, c, d = idx[i, j], idx[i, j + 1], idx[i + 1, j], idx[i + 1, j + 1]
            t.append([a, b, c])
            t.append([b, d, c])
    return V, np.asarray(t, np.int64)


def _lift_disc(V, radius=1.0, amount=1.0):
    """The constraint: lift a disc in the middle, leave the rest free."""
    return np.where(np.linalg.norm(V[:, :2], axis=1) <= radius, amount, 0.0)


def _max_gradient_per_unit(V, T, u):
    """Steepest change in displacement per WORLD unit -- the fold driver."""
    e = np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    L = np.linalg.norm(V[e[:, 0]] - V[e[:, 1]], axis=1)
    d = np.abs(u[e[:, 0], 2] - u[e[:, 1], 2])
    return float((d / np.maximum(L, 1e-9)).max())


def _solve(edge, *, on, span=8.0, monkeypatch=None):
    V, T = _grid(edge, span)
    N = np.tile(np.array([0.0, 0.0, 1.0]), (len(V), 1))
    need = _lift_disc(V)
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", on)
    u, stats = fg._solve_clearance_field(V, N, need, T, max_push=3.0)
    assert stats["ok"], "the solve must actually run, or the test proves nothing"
    assert stats["n_moved"] > 0, "0/0 is not a pass -- nothing was displaced"
    return V, T, u, stats


# The reference tessellation the solve's `lam` was tuned at, and a mesh cut eight
# times finer. 8x is enough to make today's behaviour unmistakable (the gradient
# roughly triples) without making the test slow.
COARSE, FINE = 1.0, 0.125


def _tessellation_ratio(on, monkeypatch, need_fn=_lift_disc):
    """Fine-mesh fold driver over coarse-mesh fold driver. 1.0 is invariant."""
    Vc, Tc = _grid(COARSE)
    Vf, Tf = _grid(FINE)
    N_c = np.tile(np.array([0.0, 0.0, 1.0]), (len(Vc), 1))
    N_f = np.tile(np.array([0.0, 0.0, 1.0]), (len(Vf), 1))
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", on)
    u_c, s_c = fg._solve_clearance_field(Vc, N_c, need_fn(Vc), Tc, max_push=3.0)
    u_f, s_f = fg._solve_clearance_field(Vf, N_f, need_fn(Vf), Tf, max_push=3.0)
    assert s_c["ok"] and s_f["ok"] and s_c["n_moved"] and s_f["n_moved"], (
        "both solves must actually run and move something -- 0/0 is not a pass")
    return (_max_gradient_per_unit(Vf, Tf, u_f)
            / _max_gradient_per_unit(Vc, Tc, u_c))


def test_today_the_fold_driver_tracks_tessellation(monkeypatch):
    """The defect itself, pinned. If this ever stops holding, the motivation for
    the fix has gone away and the fix should be re-justified rather than kept."""
    assert _tessellation_ratio(False, monkeypatch) > 5.0


def test_the_fix_removes_most_of_the_tessellation_excess(monkeypatch):
    """THE POINT OF THE CHANGE. Same surface, same constraint, eight times the
    resolution -- and the quantity that folds a triangle stops tracking the cut.

    NOT ASSERTED AS RATIO 1.0, AND THAT IS NOT SLACK IN THE FIX. A disc
    constraint is a STEP at its rim, so the exact solution has a kink there and a
    finer mesh legitimately resolves it more steeply; measured, the ratio floors
    at ~2.1 however long the solve is allowed to converge. What the fix has to
    remove is the part that comes from the SCREEN, and
    `test_a_smooth_constraint_was_never_affected` shows the rest is the
    constraint's own shape rather than anything this code does.
    """
    off = _tessellation_ratio(False, monkeypatch)
    on = _tessellation_ratio(True, monkeypatch)
    excess_removed = (off - on) / (off - 1.0)
    assert excess_removed > 0.6, (
        f"ratio {off:.2f} -> {on:.2f} removes only "
        f"{100 * excess_removed:.0f}% of the tessellation-driven excess")


def test_a_smooth_constraint_was_never_affected(monkeypatch):
    """The defect is at the BOUNDARY between constrained and free verts, which is
    exactly where a clearance requirement stops -- and it is the rim the authored
    floor makes more ragged, by relaxing some verts and not their neighbours.

    Where the constraint is smooth and global there is no such boundary, the
    solve was already resolution-invariant, and the fix must leave it that way.
    """
    def cone(V):
        return np.clip(1.0 - np.linalg.norm(V[:, :2], axis=1) / 3.0, 0.0, 1.0)

    assert _tessellation_ratio(False, monkeypatch, cone) == pytest.approx(
        1.0, abs=0.05)
    assert _tessellation_ratio(True, monkeypatch, cone) == pytest.approx(
        1.0, abs=0.05)


def test_a_coarse_mesh_is_left_exactly_as_it_is(monkeypatch):
    """The scale is clamped at 1, so at or above the reference tessellation the
    arithmetic is untouched. This is what holds the blast radius to the class
    being fixed -- without it, every shape in the pack moves."""
    V, T = _grid(COARSE)
    N = np.tile(np.array([0.0, 0.0, 1.0]), (len(V), 1))
    need = _lift_disc(V)
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", False)
    off, _ = fg._solve_clearance_field(V, N, need, T, max_push=3.0)
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    on, _ = fg._solve_clearance_field(V, N, need, T, max_push=3.0)
    assert np.array_equal(off, on), (
        "a mesh at the reference tessellation must be bit-identical with the "
        "flag on")


def test_a_mesh_coarser_than_the_reference_is_also_untouched(monkeypatch):
    V, T = _grid(2.0, span=16.0)
    N = np.tile(np.array([0.0, 0.0, 1.0]), (len(V), 1))
    need = _lift_disc(V, radius=2.0)
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", False)
    off, _ = fg._solve_clearance_field(V, N, need, T, max_push=3.0)
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    on, _ = fg._solve_clearance_field(V, N, need, T, max_push=3.0)
    assert np.array_equal(off, on)


def test_the_flag_is_what_switches_it(monkeypatch):
    """An A/B that cannot abort is not an A/B. The OFF arm must differ."""
    Vf, Tf = _grid(FINE)
    _, _, u_off, _ = _solve(FINE, on=False, monkeypatch=monkeypatch)
    _, _, u_on, _ = _solve(FINE, on=True, monkeypatch=monkeypatch)
    assert not np.array_equal(u_off, u_on)


def test_weld_seam_edges_do_not_count_as_tessellation(monkeypatch):
    """A UV seam is two coincident verts joined by a ~zero-length edge. That is
    one point of SURFACE, not a finely cut one, and averaging it into the local
    edge length would drive the screen to its floor on exactly the seams that
    need the most care."""
    V, T = _grid(COARSE)
    n = len(V)
    src = np.array([0, 1, 0], np.int64)
    dst = np.array([1, 0, 2], np.int64)
    # Vertex 0 sits on a weld (0-1 is zero length) plus one real edge to 2.
    V = V.copy()
    V[1] = V[0]
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    scale = fg._field_screen_scale(V, src, dst, n)
    assert scale is not None
    real = float(np.linalg.norm(V[0] - V[2]))
    assert scale[0] == pytest.approx(min((real / 1.0) ** 2, 1.0), rel=1e-6), (
        "vertex 0's scale must come from its REAL edge alone")


def test_the_helper_is_off_by_default_and_fails_soft(monkeypatch):
    """Returning None puts the caller back on exactly today's arithmetic, which
    is what makes a failure here a no-op rather than a silent loss of
    clearance."""
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", False)
    V, T = _grid(COARSE)
    e = np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    assert fg._field_screen_scale(V, e[:, 0], e[:, 1], len(V)) is None
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    # Every edge degenerate -> nothing to measure -> today's behaviour.
    flat = np.zeros((len(V), 3))
    assert fg._field_screen_scale(flat, e[:, 0], e[:, 1], len(V)) is None


def test_the_scale_is_bounded_to_one(monkeypatch):
    """It may only ever LENGTHEN the reach. A scale above 1 would tighten the
    screen on a coarse mesh -- a change with no measured defect behind it."""
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    V, T = _grid(3.0, span=12.0)
    e = np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    scale = fg._field_screen_scale(V, e[:, 0], e[:, 1], len(V))
    assert scale is not None
    assert float(scale.max()) <= 1.0


def test_iterations_are_raised_but_capped(monkeypatch):
    """A weaker screen decays over more edges, and Jacobi carries a field about
    sqrt(iters) edges -- so without more rings the longer reach never
    materialises. Capped, because the ratio grows as 1/edge^2."""
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    _, _, _, st_fine = _solve(FINE, on=True, monkeypatch=monkeypatch)
    _, _, _, st_coarse = _solve(COARSE, on=True, monkeypatch=monkeypatch)
    assert st_fine["iters"] > st_coarse["iters"]
    assert st_fine["iters"] <= fg._FIELD_ITERS_MAX


def test_a_piece_smaller_than_the_feather_has_its_screen_held_back(monkeypatch):
    """#field-reach-fits-the-piece. Asking for a physical reach is right only
    where there is somewhere to spread the displacement TO. On a piece smaller
    than the reach the solve couples the whole part, the constraint still pins
    part of it, and the two fight -- measured as +15% folds on the 28 shapes in
    the pack whose surface is smaller than the reference reach.

    Reverting such a piece EXACTLY is what makes the guard safe: the fix can
    degrade to today's behaviour but never below it.
    """
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    V, T = _grid(0.125, span=2.0)   # sqrt(area) = 2.0u, under the 3.46u reach
    src, dst, deg, _live = nc._welded_edges(V, T, 1e-3)
    guarded = fg._field_screen_scale(V, src, dst, len(V), deg=deg, lam=0.5,
                                     tris=T)
    unguarded = fg._field_screen_scale(V, src, dst, len(V))
    assert guarded is not None and unguarded is not None
    # The MEDIAN, not the min: the floor scales with a vertex's degree, so the
    # four corners of a grid patch (degree 2) have the loosest floor of all and
    # would hide the effect on the whole interior.
    assert float(np.median(guarded)) > float(np.median(unguarded)), (
        "the guard must RAISE the screen back on a piece this small")


def _decay_units(scale, V, src, dst, deg, lam=0.5):
    """The screened solve's decay length in WORLD units: sqrt(deg/(lam*scale))*h."""
    L = np.linalg.norm(V[src] - V[dst], axis=1)
    real = L > 1e-6
    acc = np.zeros(len(V))
    cnt = np.zeros(len(V))
    np.add.at(acc, src[real], L[real])
    np.add.at(cnt, src[real], 1.0)
    h = np.where(cnt > 0, acc / np.maximum(cnt, 1e-9), 1.0)
    return np.sqrt(np.clip(deg, 1.0, None) / (lam * scale)) * h


def test_the_reach_never_outgrows_the_piece(monkeypatch):
    """The property the guard states, checked directly rather than through a
    proxy: the decay length must stay within the surface's own size."""
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    for span in (2.0, 4.0, 20.0):
        V, T = _grid(0.125, span=span)
        src, dst, deg, _live = nc._welded_edges(V, T, 1e-3)
        scale = fg._field_screen_scale(V, src, dst, len(V), deg=deg, lam=0.5,
                                       tris=T)
        area = float(np.linalg.norm(
            np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]),
            axis=1).sum() * 0.5)
        reach = _decay_units(scale, V, src, dst, deg)
        assert float(reach.max()) <= area ** 0.5 * 1.02, (
            f"span {span}: reach {float(reach.max()):.2f}u over a surface of "
            f"sqrt(area) {area ** 0.5:.2f}u")


def test_a_piece_large_enough_still_gets_the_fix(monkeypatch):
    """The guard must not swallow the change on the pieces it was built for --
    otherwise it would be indistinguishable from reverting the fix."""
    Vf, Tf = _grid(FINE)
    _, _, u_off, _ = _solve(FINE, on=False, monkeypatch=monkeypatch)
    _, _, u_on, _ = _solve(FINE, on=True, monkeypatch=monkeypatch)
    assert not np.array_equal(u_off, u_on)


def test_the_area_is_read_PER_SHAPE_not_per_island(monkeypatch):
    """A REJECTED ALTERNATIVE, pinned so it is not re-derived.

    "A component is not an object" argues a vertex should be measured against
    its own island rather than its shape, and the shapes agree it would matter
    (60% carry more than one island, 27% would score differently). It was built
    and measured over the whole population, and it LOST: identical bust gap and
    penetration on both paths, worse surface without the floors (+331 folds,
    +117 inverted) and a trade with them (-159 folds for +59 inverted, the side
    the author ships at zero), at 74% more cost in this helper.

    So the damage this guard stops is a property of the SHAPE being small, not
    of a small island inside a large one -- a stud on a boot has the boot's
    solve around it. This test fails if someone reintroduces the per-island
    reading without re-measuring.
    """
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    big_V, big_T = _grid(0.125, span=20.0)      # sqrt(area) 20u, has room
    small_V, small_T = _grid(0.125, span=1.5)   # sqrt(area) 1.5u, does not
    small_V = small_V + np.array([100.0, 0.0, 0.0])   # far away: its own island
    V = np.vstack([big_V, small_V])
    T = np.vstack([big_T, small_T + len(big_V)])
    src, dst, deg, _live = nc._welded_edges(V, T, 1e-3)
    scale = fg._field_screen_scale(V, src, dst, len(V), deg=deg, lam=0.5, tris=T)
    assert scale is not None
    big = float(np.median(scale[:len(big_V)]))
    small = float(np.median(scale[len(big_V):]))
    # Same tessellation, one shape: the shape-wide reading scores them the same.
    # The per-island reading separated them ~10.7x.
    assert small == pytest.approx(big, rel=1e-6), (
        f"area is being read per island again: small {small:.5f} vs big "
        f"{big:.5f} -- re-measure the population before keeping that")


def test_the_guard_does_not_bind_on_a_piece_with_room(monkeypatch):
    """It must be inert wherever the reach already fits, or it would be
    indistinguishable from tuning the screen down globally."""
    monkeypatch.setattr(fg, "FIELD_SCREEN_PHYSICAL", True)
    V, T = _grid(0.125, span=20.0)      # sqrt(area) 20u, far above the reach
    src, dst, deg, _live = nc._welded_edges(V, T, 1e-3)
    guarded = fg._field_screen_scale(V, src, dst, len(V), deg=deg, lam=0.5,
                                     tris=T)
    unguarded = fg._field_screen_scale(V, src, dst, len(V))
    assert np.array_equal(guarded, unguarded)


def test_the_default_is_on():
    """Resolve the default from the CODE. A flag default written anywhere else
    is a dated claim.

    This test used to assert the OPPOSITE and pass. It read
    `fg._flag("CBBE2UBE_FIELD_SCREEN_PHYSICAL", False) is False` while the
    constant has been bound to the KILL switch
    (`not _flag("CBBE2UBE_NO_FIELD_SCREEN_PHYSICAL", False)`) since a22f094, so
    the positive name is read nowhere -- the assertion only proved that an unset
    env with default False returns False.

    Read the CONSTANT, in a subprocess with every `CBBE2UBE_*` stripped.
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from scripts.analysis import flag_surface
    got = flag_surface.resolved_values(
        ["FIELD_SCREEN_PHYSICAL"], "nif_convert_fitgeom")
    assert got["FIELD_SCREEN_PHYSICAL"] is True
