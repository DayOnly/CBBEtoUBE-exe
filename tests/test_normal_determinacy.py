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

"""The normal sign guard must rescue undetermined normals WITHOUT inverting
determined ones.

The guard flips a recomputed normal back to the source's sign when the two
disagree by more than 90 degrees. Applied blanketly it stores normals pointing
into the surface at vertices whose geometry says otherwise; applied not at all
it reintroduces the pubic-boundary regression it was written for. Both halves
are asserted here, and each test carries a control that FAILS if the mechanism
under test is not the one doing the work.
"""
import numpy as np
import pytest

import src.nif_convert as nc
from src.nif_convert import (
    NORMAL_DETERMINED_COHERENCE_MIN,
    NORMAL_DETERMINED_FAN_MIN,
    _recompute_vertex_normals,
)


@pytest.fixture(autouse=True)
def _select_determinacy_branch():
    """Every test below exercises the coherence/fan DETERMINACY sign-guard. A
    clearance-field build -- now the default -- force-enables
    NORMAL_SIGN_GUARD_BOUNDARY (see the `CLEARANCE_FIELD_* -> True` line by its
    definition), and that branch SHADOWS the determinacy one. Select the
    determinacy branch explicitly: it is the live fallback whenever clearance-field
    is off, and it is what this module is about. The boundary guard -- the
    production default -- has its own test at the end of the file."""
    saved = nc.NORMAL_SIGN_GUARD_BOUNDARY
    nc.NORMAL_SIGN_GUARD_BOUNDARY = False
    try:
        yield
    finally:
        nc.NORMAL_SIGN_GUARD_BOUNDARY = saved


def _disc(n=8, r=1.0, z=0.0):
    """A closed fan: centre vertex 0 surrounded by n rim verts, all in a
    plane. Vertex 0 has a full agreeing fan, so its normal is determined."""
    verts = [(0.0, 0.0, z)]
    for k in range(n):
        a = 2.0 * np.pi * k / n
        verts.append((r * np.cos(a), r * np.sin(a), z))
    tris = [(0, 1 + k, 1 + (k + 1) % n) for k in range(n)]
    return np.asarray(verts, float), np.asarray(tris, np.int64)


def test_determined_normal_is_not_inverted_by_a_stale_source():
    """A flat closed fan with a source normal pointing the WRONG way: the
    geometry is unambiguous, so the recompute must win."""
    verts, tris = _disc()
    out = _recompute_vertex_normals(verts, tris, source_normals=None)
    assert out[0][2] > 0.9, "unguarded recompute should face +Z"

    stale = np.tile(np.asarray([0.0, 0.0, -1.0]), (len(verts), 1))
    guarded = _recompute_vertex_normals(verts, tris, source_normals=stale)
    assert guarded[0][2] > 0.9, (
        "the centre vertex has a full agreeing fan -- its normal is "
        "determined by the geometry and must not be flipped to a stale sign")


def test_control_the_blanket_guard_would_invert_it():
    """NEGATIVE CONTROL. Without the determinacy term the very same input IS
    inverted. If this fails, the test above proves nothing -- it would pass
    with the fix deleted."""
    verts, tris = _disc()
    stale = np.tile(np.asarray([0.0, 0.0, -1.0]), (len(verts), 1))

    import src.nif_convert as nc
    saved = nc.NORMAL_SIGN_GUARD_DETERMINED
    try:
        nc.NORMAL_SIGN_GUARD_DETERMINED = False
        blanket = _recompute_vertex_normals(verts, tris, source_normals=stale)
    finally:
        nc.NORMAL_SIGN_GUARD_DETERMINED = saved

    assert blanket[0][2] < -0.9, (
        "the blanket guard must invert this vertex -- if it does not, the "
        "determinacy gate is not what makes the other test pass")


def test_undetermined_normal_is_still_rescued():
    """The case the guard exists for: a vertex on ONE triangle. Its fan cannot
    determine the normal, so the source's sign must still win -- this is the
    pubic-boundary-loop regression, and narrowing the guard must not lose it."""
    verts = np.asarray([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                       float)
    tris = np.asarray([(0, 1, 2)], np.int64)
    stale = np.tile(np.asarray([0.0, 0.0, -1.0]), (3, 1))

    out = _recompute_vertex_normals(verts, tris, source_normals=None)
    assert out[0][2] > 0.9, "bare recompute faces +Z for this winding"

    guarded = _recompute_vertex_normals(verts, tris, source_normals=stale)
    assert guarded[0][2] < -0.9, (
        f"a fan of 1 is below the fan-{NORMAL_DETERMINED_FAN_MIN} floor, so "
        "the source sign must still be honoured")


def test_cancelling_fan_is_treated_as_undetermined():
    """A zero-thickness ribbon rim: faces from both sides cancel, so the
    accumulated normal is numerical noise however many triangles there are.
    Coherence, not fan size, is what catches this one."""
    # two coincident opposite-wound discs sharing every vertex
    verts, tris = _disc()
    tris = np.vstack([tris, tris[:, ::-1]])
    stale = np.tile(np.asarray([0.0, 0.0, -1.0]), (len(verts), 1))

    face = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]],
                    verts[tris[:, 2]] - verts[tris[:, 0]])
    acc = np.zeros_like(verts)
    area = np.zeros(len(verts))
    for i in range(3):
        np.add.at(acc, tris[:, i], face)
        np.add.at(area, tris[:, i], np.linalg.norm(face, axis=1))
    coh = np.linalg.norm(acc[0]) / max(area[0], 1e-12)
    assert coh < NORMAL_DETERMINED_COHERENCE_MIN, (
        f"the fixture must actually cancel to exercise the gate (coh={coh})")

    guarded = _recompute_vertex_normals(verts, tris, source_normals=stale)
    assert guarded[0][2] < 0.0, (
        "a cancelling fan is undetermined -- the source sign must win even "
        "though the vertex has plenty of triangles")


def test_boundary_guard_is_the_clearance_field_default(monkeypatch):
    """The PRODUCTION default (clearance-field forces NORMAL_SIGN_GUARD_BOUNDARY
    on): take the AUTHORED source normal at a topology BOUNDARY vertex, and TRUST
    the recompute in the interior -- the f6f811f rim-shard fix. This is a
    different mechanism from the determinacy gate above, so it defeats the autouse
    fixture on purpose and gives the now-default branch its own coverage."""
    monkeypatch.setattr(nc, "NORMAL_SIGN_GUARD_BOUNDARY", True)

    # A single triangle: every vertex is a boundary (each edge in one triangle),
    # so the authored source sign must win even though the recompute is clean.
    verts = np.asarray([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], float)
    tris = np.asarray([(0, 1, 2)], np.int64)
    stale = np.tile(np.asarray([0.0, 0.0, -1.0]), (3, 1))
    at_rim = _recompute_vertex_normals(verts, tris, source_normals=stale)
    assert at_rim[0][2] < -0.9, "a boundary vert takes the authored source sign"

    # An interior vertex with a full agreeing fan is INTERIOR (no boundary edge),
    # so the recompute is trusted and is NOT flipped to the stale source -- the
    # sharp-stud "shard" the boundary form was written to stop.
    dv, dt = _disc()
    interior = _recompute_vertex_normals(
        dv, dt, source_normals=np.tile(np.asarray([0.0, 0.0, -1.0]), (len(dv), 1)))
    assert interior[0][2] > 0.9, (
        "an interior determined fan is trusted, not flipped to a stale source")
