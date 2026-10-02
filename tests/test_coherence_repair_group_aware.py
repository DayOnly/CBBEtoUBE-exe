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

"""#coherence-repair-seam-groups -- the coherence repair may not re-open a seam.

`#seam-weld-self` closes every source-coincident group (the halves of a
UV/normal seam) after the fit chain. `_repair_coherence_collapse` then runs
AFTER it at both wiring sites and smooths the displacement field per vertex
INDEX. The two halves of a seam have different neighbours, so they received
different smoothed displacements and the seam re-opened. Measured by A/B on
real pieces: with CBBE2UBE_NO_COHERENCE_REPAIR=1 every shape had 0 split seam
groups; with the defaults a gown's `top` had 23 split groups (worst 0.43u), its
`underpants` 44 (0.91u), and a cuirass 142 (1.84u). Census over 94 torso
pieces: 78 shipped with re-opened seams.

The fix is inside the producer, not a second weld: the repair treats each
group as ONE vertex (one neighbour set, one displacement, one membership in
core / region / pinned ring), and hands the same map to
`_hold_repair_outside_body`. `test_a_seam_inside_a_buckled_patch_stays_closed`
is the defect stated directly; `test_the_per_vertex_path_is_what_split_it`
shows the same mesh through the old per-vertex path splits, so the mesh is a
real reproduction and the group handling is what closes it.

The meshes here are a tilted grid strip (so the patch has a third dimension
and takes the smoothing branch; slope 0 takes the rigid strip branch) with a
patch of seeded random displacement in the middle. The gates are pinned to
their defaults so a knob in the runner's environment cannot change what
qualifies. Twins enter with the SAME displacement, as they do in the pipeline
after the weld.
"""
import numpy as np
import pytest

from src import nif_convert as nc
from src import nif_convert_writer as w

TOL = 1e-4


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for name, val in (("COHERENCE_REPAIR", True), ("COHERENCE_MIN_AREA", 4.0),
                      ("COHERENCE_SRC_MIN", 0.70), ("COHERENCE_OUT_MAX", 0.30),
                      ("COHERENCE_ITERS", 12), ("COHERENCE_DILATE", 2),
                      ("COHERENCE_THIN", 3.0), ("COHERENCE_THIN_AREA_SCALE", 1.0),
                      ("COHERENCE_THIN_DROP", 0.30), ("COHERENCE_KINK", True),
                      ("COHERENCE_REPAIR_OUTSIDE_BODY", True)):
        monkeypatch.setattr(nc, name, val)


def grid(nx=24, ny=14, seam_col=None, slope=0.35):
    """A grid strip, unit spacing, z = slope * x. With `seam_col`, that column
    is a UV-style seam: its vertices are duplicated and the triangles on the +x
    side reference the duplicates, so the two halves share a position and
    nothing else. Returns (verts, tris, twin) where twin maps each seam vertex
    to its duplicate."""
    idx, verts = {}, []
    for y in range(ny):
        for x in range(nx):
            idx[(x, y)] = len(verts)
            verts.append((float(x), float(y), slope * x))
    dup = {}
    if seam_col is not None:
        for y in range(ny):
            dup[(seam_col, y)] = len(verts)
            verts.append(verts[idx[(seam_col, y)]])

    def right(x, y):
        return dup.get((x, y), idx[(x, y)])

    tris = []
    for y in range(ny - 1):
        for x in range(nx - 1):
            if x == seam_col:
                a, b = right(x, y), idx[(x + 1, y)]
                c, d = idx[(x + 1, y + 1)], right(x, y + 1)
            else:
                a, b = idx[(x, y)], idx[(x + 1, y)]
                c, d = idx[(x + 1, y + 1)], idx[(x, y + 1)]
            tris.append((a, b, c))
            tris.append((a, c, d))
    twin = {idx[k]: v for k, v in dup.items()}
    return (np.array(verts, dtype=np.float64),
            np.array(tris, dtype=np.int64), twin)


def buckle(src, cols, rows, h=2.0, seed=1):
    """A uniform offset everywhere plus seeded noise over the vertices in the
    column/row window: a crumple whose normals scatter. Twins get the same
    displacement (they enter the repair welded)."""
    rng = np.random.default_rng(seed)
    out = src + np.array([2.0, 0.0, 0.5])
    for i, (x, y, _z) in enumerate(src):
        xi, yi = int(round(x)), int(round(y))
        if cols[0] <= xi <= cols[1] and rows[0] <= yi <= rows[1]:
            out[i] += rng.uniform(-h, h, 3) * (1.0, 0.3, 1.0)
    gid, members = w._source_coincident_groups(src, TOL)
    for m in members:
        out[m] = out[m[0]]
    return out


def groups(src):
    _gid, members = w._source_coincident_groups(src, TOL)
    return members


def max_split(members, v):
    if not members:
        return 0.0
    return max(float(np.linalg.norm(v[m][:, None] - v[m][None], axis=2).max())
               for m in members)


def _repair(src, out, tris, **kw):
    fixed, n = w._repair_coherence_collapse(src, out, tris, **kw)
    return np.asarray(fixed, dtype=np.float64), n


# --- the defect ------------------------------------------------------------

def test_a_seam_inside_a_buckled_patch_stays_closed():
    """THE DEFECT. A seam runs through a crumpled patch. The repair must still
    fire, must still move the seam (it is part of the patch), and must leave
    its two halves where the weld put them: on one point."""
    src, tris, twin = grid(seam_col=12)
    out = buckle(src, (7, 16), (4, 9))
    fixed, n = _repair(src, out, tris)
    assert n > 0, "the patch did not qualify -- the test mesh is wrong"
    g = groups(src)
    assert len(g) == 14
    assert max_split(g, fixed) < 1e-9
    # The seam was inside the smoothed region, not merely left alone.
    inside = [a for a in twin if 4 <= src[a, 1] <= 9]
    assert np.abs(fixed[inside] - out[inside]).max() > 1e-3


def test_the_per_vertex_path_is_what_split_it(monkeypatch):
    """The same mesh through the OLD per-vertex path re-opens the seam, so the
    reproduction is real and the group handling is what closes it. Disabling
    the grouping is exactly the pre-change algorithm."""
    monkeypatch.setattr(w, "_source_coincident_groups", lambda *_a, **_k: (None, []))
    src, tris, _twin = grid(seam_col=12)
    out = buckle(src, (7, 16), (4, 9))
    fixed, n = _repair(src, out, tris)
    assert n > 0
    key = np.round(src / TOL).astype(np.int64)
    _u, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    order = np.argsort(inv, kind="stable")
    cuts = np.flatnonzero(np.diff(inv[order])) + 1
    g = [m for m in np.split(order, cuts) if len(m) >= 2]
    assert max_split(g, fixed) > 0.05, "the old path no longer reproduces the defect"


def test_a_thin_strip_moves_rigidly_with_its_seam():
    """The rigid branch (`#coherence-rigid`) sets one displacement for the whole
    core; a seam group half in the core and half not would split there too."""
    src, tris, _twin = grid(seam_col=12, slope=0.0)
    out = buckle(src, (7, 16), (4, 9))
    fixed, n = _repair(src, out, tris)
    assert n > 0
    assert max_split(groups(src), fixed) < 1e-9


def test_a_seam_on_a_thin_strips_edge_is_shared_not_dragged():
    """A seam group on the EDGE of a rigid strip (one half in the strip, the
    other in the panel next to it) is a smoothing node shared by both sides,
    not part of the rigid move. It still stays closed, and it lands between
    the strip's one displacement and the panel's field rather than on the
    strip's, so the panel's edge row is not dragged the whole way."""
    # buckled columns 7..11, seam at 12: the left halves' triangles turn, the
    # right halves' (12' .. 13) do not, so the group straddles the core edge.
    src, tris, twin = grid(seam_col=12, slope=0.0)
    out = buckle(src, (7, 11), (4, 9))
    fixed, n = _repair(src, out, tris)
    assert n > 0
    assert max_split(groups(src), fixed) < 1e-9
    disp = fixed - src
    row = {int(round(src[i, 0])): i for i in range(len(src))
           if int(round(src[i, 1])) == 6 and i not in twin.values()}
    strip = disp[[row[x] for x in range(7, 12)]]
    assert np.allclose(strip, strip[0], atol=1e-9), "the strip is not rigid"
    seam_d = disp[row[12]]
    panel_d = disp[row[14]]
    assert np.linalg.norm(seam_d - strip[0]) > 1e-6, "the seam was dragged with the strip"
    # Between the two, on the axis the strip actually moved along.
    ax = int(np.argmax(np.abs(strip[0] - panel_d)))
    lo, hi = sorted((strip[0][ax], panel_d[ax]))
    assert lo - 1e-9 <= seam_d[ax] <= hi + 1e-9


# --- the region and the pinned ring ---------------------------------------

def test_a_seam_on_the_pinned_ring_stays_closed():
    """The seam sits on the LAST dilation ring: reached from one side only,
    pinned on that side, and its twin outside the region altogether. The mean
    restore shifts the whole region, so the old path split it by exactly that
    shift. Both halves must now be pinned together, i.e. move by the same
    vector as the rest of the ring."""
    # buckled columns 7..11: core 6..12, ring one 5 / 13, pinned ring 4 / 14
    src, tris, twin = grid(seam_col=14)
    out = buckle(src, (7, 11), (4, 9))
    fixed, n = _repair(src, out, tris)
    assert n == 1
    assert max_split(groups(src), fixed) < 1e-9
    shift = fixed - out
    row6 = {(int(round(x)), int(round(y))): i for i, (x, y, _z) in enumerate(src)
            if int(round(y)) == 6 and i not in twin.values()}
    left_pin = row6[(4, 6)]
    seam = row6[(14, 6)]
    assert seam in twin
    assert np.linalg.norm(shift[left_pin]) > 1e-9, "ring did not take the mean restore"
    assert np.allclose(shift[seam], shift[left_pin], atol=1e-9)
    assert np.allclose(shift[twin[seam]], shift[left_pin], atol=1e-9)


def test_a_seam_bounding_the_patch_is_repaired_on_both_sides():
    """A seam splits the turned triangles into two components, so the patch is
    two patches. Each is repaired; the seam between them stays one line."""
    src, tris, _twin = grid(seam_col=12)
    out = buckle(src, (7, 16), (4, 9))
    _fixed, n = _repair(src, out, tris)
    assert n >= 2


# --- no coincident vertices: unchanged -------------------------------------

def test_a_mesh_without_coincident_vertices_is_bit_identical(monkeypatch):
    """No twins, no change: the group machinery must not touch the arithmetic.
    Compared against the same function with grouping disabled, which is the
    pre-change per-vertex path (measured bit-identical to the v1.5 code on
    these meshes and on a random 120x80 strip at implementation time)."""
    src, tris, twin = grid()
    assert not twin
    out = buckle(src, (7, 16), (4, 9))
    want, n_want = _repair(src, out, tris)
    monkeypatch.setattr(w, "_source_coincident_groups", lambda *_a, **_k: (None, []))
    got, n_got = _repair(src, out, tris)
    assert n_want == n_got > 0
    assert np.array_equal(want, got)
    assert not np.array_equal(want, out)       # something was repaired


def test_a_flat_strip_without_coincident_vertices_is_bit_identical(monkeypatch):
    src, tris, _twin = grid(slope=0.0)
    out = buckle(src, (7, 16), (4, 9))
    want, n_want = _repair(src, out, tris)
    monkeypatch.setattr(w, "_source_coincident_groups", lambda *_a, **_k: (None, []))
    got, n_got = _repair(src, out, tris)
    assert n_want == n_got > 0
    assert np.array_equal(want, got)


# --- the grouping is the weld's own ----------------------------------------

def test_groups_are_the_seam_welds_own_rule():
    """Every group this reports is one the weld collapses, and nothing else is."""
    rng = np.random.default_rng(3)
    src = rng.uniform(-10, 10, (40, 3))
    src[5] = src[2]
    src[17] = src[2]                      # a triple
    src[30] = src[31]                     # a pair
    out = src + rng.uniform(-1, 1, (40, 3))
    gid, members = w._source_coincident_groups(src, TOL)
    assert sorted(sorted(int(x) for x in m) for m in members) == [[2, 5, 17], [30, 31]]
    grouped = {int(x) for m in members for x in m}
    assert {i for i in range(40) if gid[i] >= 0} == grouped
    assert all(gid[m[0]] == gid[x] for m in members for x in m)
    _welded, moved = nc._weld_source_coincident_verts(src, out, tol=TOL)
    assert moved == 5


def test_a_mesh_without_twins_reports_none():
    src = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    gid, members = w._source_coincident_groups(src, TOL)
    assert gid is None and members == []


# --- the clearance hold cannot split a group either -------------------------

_XS = np.linspace(-4.0, 4.0, 9)
BODY = np.array([[x, y, 0.0] for x in _XS for y in _XS], dtype=float)
BODY_N = np.tile(np.array([0.0, 0.0, 1.0]), (len(BODY), 1))


def test_the_hold_moves_a_group_together():
    """Two halves that entered split (one clear of the body, one already
    inside) and were brought together by the repair: per vertex, only the
    clear one is held at the skin and the seam re-opens by that hold. With the
    map, both take the larger hold."""
    before = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -0.3]])
    after = np.array([[0.0, 0.0, -0.25], [0.0, 0.0, -0.25]])
    per_vertex = np.asarray(w._hold_repair_outside_body(before, after, BODY, BODY_N))
    assert per_vertex[:, 2] == pytest.approx([0.0, -0.25], abs=1e-9)
    grouped = np.asarray(w._hold_repair_outside_body(
        before, after, BODY, BODY_N, groups=[np.array([0, 1])]))
    assert grouped[:, 2] == pytest.approx([0.0, 0.0], abs=1e-9)


def test_the_hold_still_leaves_an_unmoved_member_alone():
    """The one-sided contract: a member the repair did not move is not this
    guard's business, group or no group."""
    before = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 0.4]])
    after = np.array([[0.0, 0.0, -0.25], [0.0, 0.0, 0.4]])
    out = np.asarray(w._hold_repair_outside_body(
        before, after, BODY, BODY_N, groups=[np.array([0, 1])]))
    assert out[:, 2] == pytest.approx([0.0, 0.4], abs=1e-9)


def test_the_hold_is_unchanged_for_coincident_members():
    before = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [3.0, 0.0, 1.0]])
    after = np.array([[0.0, 0.0, -0.25], [0.0, 0.0, -0.25], [3.0, 0.0, 0.7]])
    plain = np.asarray(w._hold_repair_outside_body(before, after, BODY, BODY_N))
    grouped = np.asarray(w._hold_repair_outside_body(
        before, after, BODY, BODY_N, groups=[np.array([0, 1])]))
    assert np.array_equal(plain, grouped)


def test_the_repair_hands_the_hold_its_groups(monkeypatch):
    """The behavioural tests above pass just as well if the repair never passes
    the map on. Pin the hand-off: a mesh with seams sends its groups, a mesh
    without sends none."""
    seen = []

    def spy(before, after, bv, bn, *, groups=None):
        seen.append(groups)
        return after

    monkeypatch.setattr(w, "_hold_repair_outside_body", spy)
    src, tris, _twin = grid(seam_col=12)
    _repair(src, buckle(src, (7, 16), (4, 9)), tris, body_verts=BODY, body_normals=BODY_N)
    src2, tris2, _twin2 = grid()
    _repair(src2, buckle(src2, (7, 16), (4, 9)), tris2, body_verts=BODY, body_normals=BODY_N)
    assert len(seen) == 2
    assert seen[0] is not None and len(seen[0]) == 14
    assert seen[1] is None
