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

"""Validate the surface-penetration metric on geometry whose answer is known.

WHY THESE EXIST. The metric this replaces (nearest garment VERTEX, projected onto the
body normal) was wrong in a way that survived review and justified a feature that had
to be reverted: where cloth hangs away from the body it reported penetration with none.
A metric is not trustworthy because it looks principled -- it is trustworthy when it
gives the known answer on cases built to have one, INCLUDING the case that broke its
predecessor. That is the `test_loose_drape_is_not_penetration` case below."""
import numpy as np
import pytest

from scripts.mesh_penetration import closest_point_on_triangles, surface_penetration


def _sphere(r, n=16, centre=(0.0, 0.0, 0.0)):
    """Triangulated UV sphere with OUTWARD winding and matching vertex normals."""
    u = np.linspace(0, np.pi, n)
    v = np.linspace(0, 2 * np.pi, 2 * n)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    x = np.sin(uu) * np.cos(vv)
    y = np.sin(uu) * np.sin(vv)
    z = np.cos(uu)
    dirs = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1)
    verts = dirs * r + np.asarray(centre, dtype=np.float64)
    tris = []
    cols = 2 * n
    for i in range(n - 1):
        for j in range(cols - 1):
            p = i * cols + j
            tris.append([p, p + cols, p + 1])
            tris.append([p + 1, p + cols, p + cols + 1])
    return verts, np.asarray(tris, dtype=np.int64), dirs


# --- exact closest point ------------------------------------------------------

def test_closest_point_interior_edge_and_vertex():
    a = np.array([[0.0, 0, 0]] * 3)
    b = np.array([[1.0, 0, 0]] * 3)
    c = np.array([[0.0, 1, 0]] * 3)
    pts = np.array([[0.25, 0.25, 5.0],      # over the interior
                    [-3.0, -3.0, 0.0],      # past vertex a
                    [0.5, -2.0, 0.0]])      # past edge ab
    got = closest_point_on_triangles(pts, a, b, c)
    assert np.allclose(got[0], [0.25, 0.25, 0.0])
    assert np.allclose(got[1], [0.0, 0.0, 0.0])
    assert np.allclose(got[2], [0.5, 0.0, 0.0])


# --- the metric ---------------------------------------------------------------

def test_body_inside_garment_reads_negative():
    body, _bt, bn = _sphere(9.0)
    gv, gt, gn = _sphere(10.0)
    signed, dist, covered, agree = surface_penetration(body, gv, gt, gn, contact=2.0)
    assert covered.all(), "a 1.0u gap must be inside the contact gate"
    assert (signed < 0).all(), "body inside the shell must be negative everywhere"
    assert np.allclose(dist, 1.0, atol=0.25)
    assert agree > 0.9


def test_body_outside_garment_reads_positive():
    body, _bt, _bn = _sphere(11.0)
    gv, gt, gn = _sphere(10.0)
    signed, _d, covered, _a = surface_penetration(body, gv, gt, gn, contact=2.0)
    assert covered.all()
    assert (signed > 0).all(), "body outside the shell IS poking through"


def test_loose_drape_is_not_penetration():
    """THE case that killed the old metric. The garment hangs 4u away and does not
    touch the body: nearest-VERTEX projection called this poke-through; a surface
    metric with a contact gate must call it 'not covered' and judge nothing."""
    body, _bt, _bn = _sphere(6.0)
    gv, gt, gn = _sphere(10.0)
    signed, dist, covered, _a = surface_penetration(body, gv, gt, gn, contact=1.5)
    assert dist.min() > 3.5, "sanity: the gap really is ~4u"
    assert not covered.any(), "loose drape must be gated OUT, not scored"
    assert not (covered & (signed > 0)).any()


def test_partial_penetration_is_localised():
    """A dent on one side only: poking verts must be exactly the dented ones, not a
    band smeared around the whole shell."""
    body, _bt, _bn = _sphere(9.0)
    gv, gt, gn = _sphere(10.0)
    side = gv[:, 0] > 6.0                       # push part of the shell inward
    gv = gv.copy()
    gv[side] *= 0.82
    signed, _d, covered, _a = surface_penetration(body, gv, gt, gn, contact=2.0)
    poking = covered & (signed > 0)
    assert poking.any(), "the dent must register"
    assert poking.mean() < 0.35, "and must stay local to the dent"
    assert (body[poking][:, 0] > 0).all(), "every poke is on the dented side"


def test_winding_flip_is_detected_and_corrected():
    """A NIF does not guarantee outward winding. Reversed triangles must not invert
    the verdict -- that would report a perfect fit as total penetration."""
    body, _bt, _bn = _sphere(9.0)
    gv, gt, gn = _sphere(10.0)
    signed_ok, _d, _c, agree_ok = surface_penetration(body, gv, gt, gn, contact=2.0)
    signed_rev, _d2, _c2, agree_rev = surface_penetration(
        body, gv, gt[:, ::-1].copy(), gn, contact=2.0)
    assert (signed_ok < 0).all() and (signed_rev < 0).all()
    assert agree_ok > 0.9 and agree_rev > 0.9


def test_degenerate_triangles_do_not_crash():
    gv = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0]])
    gt = np.array([[0, 1, 2], [0, 1, 3]])           # first is degenerate (collinear)
    signed, dist, covered, _a = surface_penetration(
        np.array([[0.25, 0.25, 1.0]]), gv, gt, None, contact=5.0)
    assert np.isfinite(signed).all() and np.isfinite(dist).all()


def test_no_usable_triangles_returns_nothing_covered():
    gv = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]])
    gt = np.array([[0, 1, 2]])                      # only a degenerate triangle
    _s, dist, covered, _a = surface_penetration(
        np.array([[0.0, 5.0, 0.0]]), gv, gt, None)
    assert not covered.any() and np.isinf(dist).all()


# --- the SOUND metric: ray exposure -------------------------------------------
#
# `surface_penetration`'s SIGN is known-bad (METRICS.md: shell has two faces, the
# nearest one decides the sign, so it is arbitrary inside a cup). These cover the
# replacement, with positive controls -- a metric that reports "no problem" is
# indistinguishable from one that cannot see the problem.

def test_ray_exposure_fully_enclosed_body_is_not_exposed():
    """POSITIVE CONTROL, the direction that matters: a body entirely inside a closed
    shell must read 0% exposed. This is the case the signed-normal metric got wrong."""
    from scripts.mesh_penetration import ray_exposure
    body, _bt, bn = _sphere(9.0)
    gv, gt, _gn = _sphere(10.0)
    exposed = ray_exposure(body, bn, gv, gt)
    assert exposed.mean() == 0.0, "an enclosed body cannot be exposed"


def test_ray_exposure_uncovered_body_is_fully_exposed():
    """The other control: no garment in the way -> 100% exposed."""
    from scripts.mesh_penetration import ray_exposure
    body, _bt, bn = _sphere(9.0)
    gv, gt, _gn = _sphere(2.0)                  # tiny shell deep inside, blocks nothing
    assert ray_exposure(body, bn, gv, gt).mean() == 1.0


def test_ray_exposure_localises_a_hole():
    """A garment with one side removed: exposure must be confined to that side."""
    from scripts.mesh_penetration import ray_exposure
    body, _bt, bn = _sphere(9.0)
    gv, gt, _gn = _sphere(10.0)
    keep = ~((gv[gt].mean(axis=1))[:, 0] > 4.0)      # delete the +x cap
    exposed = ray_exposure(body, bn, gv, gt[keep])
    assert 0.05 < exposed.mean() < 0.5, f"partial hole, got {exposed.mean():.2f}"
    assert body[exposed][:, 0].min() > 0, "exposure must be on the removed side only"


def test_ray_exposure_ignores_a_garment_that_hangs_away_but_still_covers():
    """Loose drape still BLOCKS the ray, so it is covered -- the distinction the
    nearest-vertex metric could not make. A 4u gap is not exposure."""
    from scripts.mesh_penetration import ray_exposure
    body, _bt, bn = _sphere(6.0)
    gv, gt, _gn = _sphere(10.0)
    assert ray_exposure(body, bn, gv, gt).mean() == 0.0


def test_ray_exposure_handles_empty_geometry():
    from scripts.mesh_penetration import ray_exposure
    import numpy as _np
    body, _bt, bn = _sphere(9.0)
    assert ray_exposure(body, bn, _np.zeros((0, 3)), _np.zeros((0, 3))).all()


# --- exposure is COVERAGE, not a defect ---------------------------------------
#
# 187 armors flagged >5% "exposed and garment within 2u" at the upper chest, and only
# SIX had a real poke-through. At a neckline the garment IS within 2u -- just below
# the rim. Without this split, a population signal is mostly garment design.

def _octahedron():
    """A genuinely CLOSED mesh. `_sphere` above leaves its UV seam unstitched, so it
    is open and cannot serve as the closed control -- which is what these two tests
    caught the first time they ran."""
    v = np.array([[1.0, 0, 0], [-1, 0, 0], [0, 1, 0],
                  [0, -1, 0], [0, 0, 1], [0, 0, -1]])
    t = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4],
                  [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    return v, t


def test_boundary_points_finds_an_open_rim():
    from scripts.mesh_penetration import boundary_points
    v, t = _octahedron()
    rim = boundary_points(v, t[:-1])                # drop one face -> a 3-edge hole
    assert len(rim) == 3, f"one missing face leaves a triangular rim, got {len(rim)}"


def test_closed_mesh_has_no_boundary():
    from scripts.mesh_penetration import boundary_points
    v, t = _octahedron()
    assert len(boundary_points(v, t)) == 0, "a closed shell has no open rim"


def test_classify_splits_poke_from_neckline_and_bare_skin():
    from scripts.mesh_penetration import classify_exposure
    exposed = np.array([True, True, True, False])
    surf = np.array([0.5, 0.5, 9.0, 0.5])           # near, near, far, near
    rim = np.array([9.0, 1.0, 9.0, 9.0])            # deep, at-rim, deep, deep
    poke, neck, uncov = classify_exposure(exposed, surf, rim)
    assert poke.tolist() == [True, False, False, False], "garment around it = defect"
    assert neck.tolist() == [False, True, False, False], "at the rim = neckline"
    assert uncov.tolist() == [False, False, True, False], "no garment = bare by design"


def test_rim_distance_alone_would_misclassify_a_bare_body():
    """THE bug this split fixes. Rim distance is large BOTH deep inside coverage and
    completely outside the garment, so on its own it scored a towel at 100% poke."""
    from scripts.mesh_penetration import classify_exposure
    exposed = np.array([True] * 3)
    far_from_rim = np.array([50.0] * 3)             # identical on the naive test
    surf = np.array([0.4, 20.0, 30.0])              # only the first is covered
    poke, _neck, uncov = classify_exposure(exposed, surf, far_from_rim)
    assert poke.sum() == 1 and uncov.sum() == 2
