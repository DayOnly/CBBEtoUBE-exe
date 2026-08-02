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

"""#unified-offset -- the four clearance passes as ONE solved target.

The four passes are three different OPERATORS (additive push, absolute target,
floor) and an additive push followed by an absolute target is simply overwritten
-- measured as `inflate` cancelled by `conform` on 12 of 32 shapes. Restating
them as constraints on the final offset removes the conflict by construction.

These tests pin the four properties the reformulation is FOR. The headline one
is `test_an_additive_push_can_be_cancelled_but_a_floor_cannot`: it reproduces
the real bug in miniature and shows the new form is immune.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import offset_field as of  # noqa: E402


def _flat_body(n=25, spacing=1.0):
    """A flat sheet at y=0 with +y normals — offsets are just the y value."""
    xs, zs = np.meshgrid(np.arange(n) * spacing, np.arange(n) * spacing)
    B = np.column_stack([xs.ravel(), np.zeros(xs.size), zs.ravel()])
    N = np.tile([0.0, 1.0, 0.0], (len(B), 1))
    return B, N


# ------------------------------------------------------------------ the solve

def test_no_opinion_means_no_movement():
    """A vert nobody asserts anything about must not move. Without this the
    unified pass would touch every vert in the mesh."""
    o = np.array([0.5, 1.0, 2.0])
    assert np.allclose(of.solve(o), o)


def test_floor_raises_only_what_is_below_it():
    o = np.array([0.1, 1.0, 3.0])
    assert np.allclose(of.solve(o, floor=1.0), [1.0, 1.0, 3.0])


def test_ceiling_lowers_only_what_is_above_it():
    o = np.array([0.1, 1.0, 3.0])
    assert np.allclose(of.solve(o, ceiling=2.0), [0.1, 1.0, 2.0])


def test_the_floor_beats_the_ceiling_when_they_disagree():
    """Deliberate and asymmetric: skin through steel gets reported, a garment
    slightly too far off the body is cosmetic."""
    assert np.allclose(of.solve(np.array([0.0]), floor=2.0, ceiling=1.0), [2.0])


def test_an_absolute_target_overrides_the_current_offset():
    """This is `conform`: aim at the authored drape wherever the vert is now."""
    o = np.array([0.2, 3.0])
    assert np.allclose(of.solve(o, target=np.array([1.0, 1.0])), [1.0, 1.0])


def test_THE_BUG_an_additive_push_is_cancelled_but_a_floor_is_not():
    """The measured defect, in miniature.

    OLD: inflate adds 0.5 to a vert at 0.1, then conform sets it to the source
    offset 0.15 -- the inflation is gone, which is the -1.06..-5.36 cancellation
    the survival trace found on 12 of 32 shapes.
    NEW: the same intent expressed as a FLOOR survives the same target.
    """
    start, inflate_add, source_target, clearance_floor = 0.1, 0.5, 0.15, 0.6
    old = source_target                      # absolute target wins outright
    assert old < start + inflate_add, "the additive push is overwritten"

    new = of.solve(np.array([start]), target=np.array([source_target]),
                   floor=clearance_floor)[0]
    assert new == clearance_floor
    assert new > old, "restating the push as a floor is what makes it survive"


# -------------------------------------------------------------- the pin (P4)

def test_a_pinned_vert_never_moves():
    B, N = _flat_body()
    V = np.array([[2.0, 0.05, 2.0], [3.0, 0.05, 3.0]])
    off, j, _d = of.current_offset(V, B, N)
    out = of.apply(V, N, j, off, np.array([2.0, 2.0]),
                   weight=np.array([0.0, 1.0]), feather_iters=0)
    assert np.allclose(out[0], V[0]), "pinned vert moved"
    assert out[1][1] > V[1][1] + 1.0, "free vert did not move"


def test_the_feather_cannot_leak_motion_into_a_pinned_vert():
    """Smoothing after the pin would drift a chain off its bones."""
    B, N = _flat_body()
    V = np.array([[float(i), 0.05, 0.0] for i in range(6)])
    tris = np.array([[i, i + 1, i + 2] for i in range(4)], np.int64)
    off, j, _d = of.current_offset(V, B, N)
    w = np.ones(len(V))
    w[0] = 0.0
    out = of.apply(V, N, j, off, np.full(len(V), 3.0), weight=w,
                   tris=tris, feather_iters=4)
    assert np.allclose(out[0], V[0]), "feather leaked into the pinned vert"


# ------------------------------------------------------------ budget + feather

def test_one_budget_caps_the_move():
    B, N = _flat_body()
    V = np.array([[2.0, 0.0, 2.0]])
    off, j, _d = of.current_offset(V, B, N)
    out = of.apply(V, N, j, off, np.array([9.0]), max_move=1.5,
                   feather_iters=0)
    assert np.isclose(out[0][1], 1.5), out


def test_the_feather_does_not_reopen_a_cleared_vert():
    """Smoothing the MOVE, not the positions, is what makes this safe: every
    neighbour's move already satisfies the same solve, so averaging cannot pull
    a vert back under its floor by more than its neighbours are above it."""
    B, N = _flat_body()
    V = np.array([[float(i), 0.0, 0.0] for i in range(7)])
    tris = np.array([[i, i + 1, i + 2] for i in range(5)], np.int64)
    off, j, _d = of.current_offset(V, B, N)
    tgt = of.solve(off, floor=1.0)
    out = of.apply(V, N, j, off, tgt, tris=tris, feather_iters=4)
    assert out[:, 1].min() > 0.6, out[:, 1]


def test_feather_is_a_noop_without_topology():
    m = np.array([1.0, 0.0, 3.0])
    assert np.allclose(of.feather(m, None, 4), m)


# ---------------------------------------------------------------- the offset

def test_current_offset_reads_signed_distance_along_the_body_normal():
    B, N = _flat_body()
    V = np.array([[2.0, 1.25, 2.0], [3.0, -0.5, 3.0]])
    off, _j, _d = of.current_offset(V, B, N)
    assert np.allclose(off, [1.25, -0.5]), off


def test_empty_input_is_survivable():
    B, N = _flat_body()
    off, j, _d = of.current_offset(np.zeros((0, 3)), B, N)
    assert len(off) == 0 and len(j) == 0
