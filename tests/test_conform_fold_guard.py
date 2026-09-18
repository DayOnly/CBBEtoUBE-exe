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

"""#conform-fold-guard -- the clamp that stops `conform` turning the garment
surface inside out.

The guard's whole safety argument is two properties, so both are asserted
directly rather than inferred from a fold count: every vertex moves in the SAME
direction and NO FURTHER than it was asked to, and a triangle the source already
shipped inverted is left exactly as it was.
"""
import numpy as np
import pytest

from src import nif_convert as nc


def _winding(v, t):
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return np.cross(p1 - p0, p2 - p0)


def _flips(cur, out, t):
    """Triangles whose orientation reversed between `cur` and `out`."""
    return int((np.einsum("ij,ij->i", _winding(cur, t), _winding(out, t))
                <= 0.0).sum())


# A strip of two triangles sharing an edge, flat in Z.
STRIP_V = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]])
STRIP_T = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64)


def test_guard_prevents_a_flip_the_raw_displacement_would_cause():
    # Drag vert 0 far past vert 1: unguarded this inverts triangle 0.
    disp = np.zeros((4, 3))
    disp[0] = [3.0, 0.0, 0.0]
    assert _flips(STRIP_V, STRIP_V + disp, STRIP_T) == 1, "test setup is wrong"

    damped = nc._damp_to_avoid_inversion(STRIP_V, disp, STRIP_T)
    assert _flips(STRIP_V, STRIP_V + damped, STRIP_T) == 0


def test_guard_never_moves_a_vertex_further_or_sideways():
    """The safety property: output displacement is the input scaled into [0,1].

    This is what lets us say the guard cannot cause clipping -- it can only
    under-deliver a pull-in, never push out or steer.
    """
    rng = np.random.default_rng(20260802)
    v = rng.normal(size=(60, 3)) * 4.0
    t = np.array([[i, (i + 1) % 60, (i + 2) % 60] for i in range(60)],
                 dtype=np.int64)
    disp = rng.normal(size=(60, 3)) * 3.0

    out = nc._damp_to_avoid_inversion(v, disp, t)

    for i in range(len(v)):
        n_in = np.linalg.norm(disp[i])
        n_out = np.linalg.norm(out[i])
        assert n_out <= n_in + 1e-9, f"vert {i} moved FURTHER"
        if n_in > 1e-9 and n_out > 1e-9:
            cos = float(np.dot(disp[i], out[i]) / (n_in * n_out))
            assert cos > 1 - 1e-9, f"vert {i} changed direction"


def test_untouched_displacement_is_returned_unchanged():
    """A displacement that folds nothing must survive bit-for-bit, so enabling
    the guard is a no-op on the meshes that never had the defect."""
    disp = np.zeros((4, 3))
    disp[0] = [0.0, 0.0, 0.05]
    out = nc._damp_to_avoid_inversion(STRIP_V, disp, STRIP_T)
    assert np.array_equal(out, disp)


def test_a_source_inverted_triangle_is_not_repaired_or_worsened():
    """The reference is the geometry ENTERING the pass. An author's own inverted
    triangle arrives inverted and must leave exactly as inverted -- the guard
    forbids NEW flips, it is not a mesh repair tool."""
    v = STRIP_V.copy()
    t = np.array([[0, 2, 1], [1, 3, 2]], dtype=np.int64)   # tri 0 wound backwards
    disp = np.zeros((4, 3))
    disp[3] = [0.0, 0.0, 0.2]
    out = nc._damp_to_avoid_inversion(v, disp, t)
    assert _flips(v, v + out, t) == 0
    assert np.allclose(out, disp)


def test_degenerate_entry_triangle_is_ignored_not_guessed_at():
    """A zero-area triangle has no reliable normal; it must not pin its
    neighbours' motion to nothing."""
    v = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
                  [2.0, 0.0, 0.0],            # collinear -> zero area
                  [0.0, 1.0, 0.0]])
    t = np.array([[0, 1, 2], [0, 1, 3]], dtype=np.int64)
    disp = np.zeros((4, 3))
    disp[3] = [0.0, 0.3, 0.0]
    out = nc._damp_to_avoid_inversion(v, disp, t)
    assert np.allclose(out[3], disp[3])


@pytest.mark.parametrize("bad_tris", [
    np.zeros((0, 3), dtype=np.int64),
    np.array([[0, 1]], dtype=np.int64),
])
def test_unusable_topology_is_a_no_op(bad_tris):
    disp = np.ones((4, 3))
    out = nc._damp_to_avoid_inversion(STRIP_V, disp, bad_tris)
    assert np.array_equal(out, disp)


def test_ships_off_and_has_an_env_switch():
    """PIPELINE rule 8: a fit-changing feature ships OFF until it has an in-game
    verdict. If this test is what fails when the default is flipped, flip it
    deliberately -- do not delete it."""
    assert nc.CONFORM_FOLD_GUARD is False


def test_gui_exposes_it():
    """A flag the user cannot reach is a flag nobody can test (2026-08-01 flag
    audit: 17 opt-in features were env-only)."""
    from src import gui_settings as gs
    s = gs.by_key()["conform_fold_guard"]
    assert s.default is False
    assert s.env == "CBBE2UBE_CONFORM_FOLD_GUARD"
    assert s.invert is False
    assert s.key in dict(gs.LAYOUT["Armor"])["Fit and clearance"]
