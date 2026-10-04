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

"""#coherence-repair-write-hold -- the write-time repair keeps the body hold.

On a body-swap piece the coherence repair runs twice: in the chain (with the
body, so `_hold_repair_outside_body` clamps it) and again inside `_copy_shape`
at write time, which used to get NO body and so no hold. That second run is the
last thing to touch a vertex. Traced on one cuirass: the chain's run was held to
0.000 clearance, then the write-time run moved 129 more vertices with no hold
and one ended 2.03u under the skin. The seam-group closure makes the repair's
patches larger, so the gap grew from 40 moved vertices to 129.

`_copy_shape` now takes `repair_body=(verts, normals)` in the shape's own frame
and hands it to the repair. The phase-2 job carries it with the per-shape
offset subtracted, because the chain works in the offset frame and the verts
are written back out of it.
"""
import inspect

import numpy as np

from src import nif_convert as nc
from src import nif_convert_writer as w


def test_copy_shape_takes_a_repair_body():
    sig = inspect.signature(w._copy_shape)
    assert "repair_body" in sig.parameters
    assert sig.parameters["repair_body"].default is None


def test_the_write_time_repair_is_given_the_body():
    src = inspect.getsource(w._copy_shape)
    assert "body_verts=_rb[0], body_normals=_rb[1]" in src
    # no body (the phase-1 copy path) is the old call, not an error
    assert "repair_body if repair_body is not None else (None, None)" in src


def test_the_job_carries_the_body_in_the_shapes_own_frame():
    src = inspect.getsource(nc._fit_shapes_swap)
    assert '"repair_body"' in src
    # the chain's offset frame is subtracted back off, as for the verts
    assert "np.asarray(body_verts_for_p2, dtype=np.float64)" in src
    assert "- _off_p2, body_norms_for_p2)" in src


def test_the_copy_loop_hands_the_job_body_to_the_writer():
    src = inspect.getsource(nc.convert_nif_phase2)
    assert 'repair_body=j.get("repair_body")' in src


def test_the_hold_clamps_a_repair_that_pulls_a_vert_under_the_skin():
    # the behaviour the plumbing exists for, through the repair's own body
    # argument: a flat body at z=0 facing +z, a sheet 0.4 above it, and a
    # buckled patch the smoothing pulls toward the body.
    n = 14
    xs, ys = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    body_v = np.c_[xs.ravel(), ys.ravel(), np.zeros(n * n)]
    body_n = np.tile([0.0, 0.0, 1.0], (n * n, 1))
    before = body_v + [0.0, 0.0, 0.4]
    after = before.copy()
    after[n * 6 + 6, 2] = -1.5            # one vert driven well under the skin
    held = w._hold_repair_outside_body(before, after, body_v, body_n)
    assert held[n * 6 + 6, 2] >= -1e-9
    unheld = w._hold_repair_outside_body(before, after, None, None)
    assert unheld[n * 6 + 6, 2] == -1.5
