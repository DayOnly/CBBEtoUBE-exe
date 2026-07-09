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

"""Regression: soft-body / HDT-rigged cloth is skipped by the main anti-poke
(`clear_armor_outside_body`) because moving every vert disturbs the sim, so the
larger UBE breast/butt pokes straight through it. `_inflate_cloth_over_bust_butt`
nudges ONLY the breast + butt bands outward to cover, body-PRESERVING. Confirmed
in-game (Ancient Falmer cuirass breast). #softcloth-inflate
"""
import numpy as np

import src.nif_convert as nc  # noqa: E402


def _patch(z, y, n=5, span=2.0):
    """A small x-spread patch of verts at height z, depth y (front +y)."""
    xs = np.linspace(-span, span, n)
    return np.array([[float(x), float(y), float(z)] for x in xs])


def test_breast_poke_pushed_out():
    # Body breast verts (front, z in the bust band) with outward +y normals; the
    # cloth sits 1u INSIDE them -> must be pushed out to sit ~bust_clear proud.
    body = _patch(105.0, 6.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    cloth = _patch(105.0, 5.0)
    out = np.asarray(nc._inflate_cloth_over_bust_butt(
        cloth, body, bn, tris=None, bust_clear=1.3), dtype=float)
    assert len(out) == len(cloth)                      # returns cloth only
    assert out[:, 1].mean() > cloth[:, 1].mean() + 1.5  # pushed clear of the body


def test_no_poke_leaves_cloth_untouched():
    # Cloth already well outside the body -> nothing to push.
    body = _patch(105.0, 6.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    cloth = _patch(105.0, 10.0)
    out = np.asarray(nc._inflate_cloth_over_bust_butt(cloth, body, bn), dtype=float)
    assert np.allclose(out, cloth, atol=1e-4)


def test_out_of_band_untouched():
    # A poke at z=60 (below the butt band, not the breast) is outside both bands.
    body = _patch(60.0, 6.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    cloth = _patch(60.0, 5.0)
    out = np.asarray(nc._inflate_cloth_over_bust_butt(cloth, body, bn), dtype=float)
    assert np.allclose(out, cloth, atol=1e-4)


def test_butt_poke_pushed_out():
    # Back-facing body verts in the butt band -> cloth pushed out along -y.
    body = _patch(84.0, -6.0)
    bn = np.tile([0.0, -1.0, 0.0], (len(body), 1))
    cloth = _patch(84.0, -5.0)
    out = np.asarray(nc._inflate_cloth_over_bust_butt(
        cloth, body, bn, butt_clear=1.1), dtype=float)
    assert out[:, 1].mean() < cloth[:, 1].mean() - 1.3  # pushed outward (-y)
