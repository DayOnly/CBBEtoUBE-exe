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

"""#collider-warp-frame: a re-imported collider is warped in WORLD space.

The CBBE -> UBE body delta is a world field. One HDT cuirass stores its
colliders at g2s x0.045 rot 180; warping those STORED verts sampled the delta
near the origin (the feet) and, written back at x22, moved ColSkirt/ColBelt
5.2u rearward -- the skirt was pushed out into the back plate. The warp must
lift by the g2s, warp, and lower back; an identity g2s is unchanged."""
import numpy as np
import pytest

from tests.synthetic_nif import pynifly_available
import src.nif_convert as nc  # noqa: E402
from src import nif_convert_physics as ph  # noqa: E402

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")

pyn = nc._pynifly()


class _Shape:
    def __init__(self, verts, g2s):
        self.verts = [tuple(v) for v in verts]
        self.has_global_to_skin = g2s is not None
        self.global_to_skin = g2s


def _g2s(scale=1.0, rot_z180=False):
    g = pyn.TransformBuf()
    g.set_identity()
    g.scale = scale
    if rot_z180:
        g.rotation = pyn.MATRIX3(pyn.VECTOR3(-1, 0, 0), pyn.VECTOR3(0, -1, 0),
                                 pyn.VECTOR3(0, 0, 1))
    return g


# A body whose delta differs by height: the feet stay put, the hips move back.
_BODY = np.array([[x, y, z] for z in (0.0, 5.0, 60.0, 70.0, 80.0)
                  for x in (-10.0, 10.0) for y in (-10.0, 10.0)])
_DELTA = np.where(_BODY[:, 2:3] > 30.0, [0.0, -1.0, 0.0], [0.0, 0.0, 0.0])
_WORLD = np.array([[0.0, -12.0, 65.0], [8.0, 0.0, 70.0], [-8.0, 5.0, 75.0]])


def test_scaled_collider_moves_like_a_world_collider():
    g = _g2s(1.0 / 22.2455, rot_z180=True)
    stored = nc._verts_world_to_skin(_WORLD, g)
    out = ph._warp_collider_to_ube(_Shape(stored, g), _BODY, _DELTA)
    moved = nc._verts_skin_to_world(out, g) - _WORLD
    # hip-height verts follow the hips' -1u, not the feet's 0u scaled by 22
    assert np.allclose(moved, [[0.0, -1.0, 0.0]] * 3, atol=0.05), moved


def test_identity_g2s_unchanged():
    ref = nc.warp_armor_by_body_delta(_WORLD, _BODY, _DELTA, min_standoff=0.0) \
        if hasattr(nc, "warp_armor_by_body_delta") else None
    out = ph._warp_collider_to_ube(_Shape(_WORLD, None), _BODY, _DELTA)
    if ref is None:
        from src.nif_convert_fitgeom import warp_armor_by_body_delta
        ref = warp_armor_by_body_delta(_WORLD, _BODY, _DELTA, min_standoff=0.0)
    assert np.array_equal(out, ref)
