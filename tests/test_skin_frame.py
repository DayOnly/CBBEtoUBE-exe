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

"""Shape skin-frame reconciliation (the 'ebony breaking at the shoulders' fix).

A shape with an offset global_to_skin stores its verts far from world position;
the fit must run in WORLD frame and transform back to skin for output. Identity
g2s must be a strict no-op.
"""
import numpy as np
from src import nif_convert


class _G2S:
    """Minimal global_to_skin stand-in: rotation (3x3), translation (3,), scale."""
    def __init__(self, translation=(0, 0, 0), rotation=None, scale=1.0):
        self.translation = list(translation)
        self.rotation = rotation or [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        self.scale = scale


def test_identity_g2s_is_noop():
    g = _G2S()
    assert nif_convert._g2s_is_identity(g)
    v = np.array([[1.0, 2.0, 3.0], [-4.0, 5.0, -6.0]])
    w = nif_convert._verts_skin_to_world(v, g)
    s = nif_convert._verts_world_to_skin(v, g)
    assert w is v and s is v, "identity g2s must return the SAME array (no-op)"
    print("  test_identity_g2s_is_noop OK")


def test_offset_translation_roundtrip():
    # Ebony case: pure -64.7u Z translation, no rotation.
    g = _G2S(translation=(0.0, 2.0, -64.7))
    assert not nif_convert._g2s_is_identity(g)
    skin = np.array([[-9.8, -0.5, 50.0], [3.0, 1.0, -58.0]])
    world = nif_convert._verts_skin_to_world(skin, g)
    # skin -> world == skin - translation (identity rotation, scale 1)
    assert np.allclose(world, skin - np.array([0.0, 2.0, -64.7])), world
    # world z should land in the torso band, not the cuirass-local negative band
    assert world[0, 2] > 110, "shoulder vert must map to upper-body world z"
    back = nif_convert._verts_world_to_skin(world, g)
    assert np.allclose(back, skin, atol=1e-9), "round-trip must recover skin verts"
    print("  test_offset_translation_roundtrip OK")


def test_rotated_scaled_roundtrip():
    # 90deg about Z + scale, nonzero translation -> round-trip must still hold.
    c, s = 0.0, 1.0
    R = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    g = _G2S(translation=(5.0, -3.0, 10.0), rotation=R, scale=1.25)
    assert not nif_convert._g2s_is_identity(g)
    skin = np.array([[1.0, 2.0, 3.0], [-7.0, 4.0, 9.0], [0.0, 0.0, 0.0]])
    world = nif_convert._verts_skin_to_world(skin, g)
    back = nif_convert._verts_world_to_skin(world, g)
    assert np.allclose(back, skin, atol=1e-6), "rotated+scaled round-trip failed"
    print("  test_rotated_scaled_roundtrip OK")


test_identity_g2s_is_noop()
test_offset_translation_roundtrip()
test_rotated_scaled_roundtrip()
