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

"""Guard for the layer ride's body floor margin (`#ride-floor-margin`).

The ride re-derives an outer layer from the one beneath it and keeps the
author's offset, which can set a layer down ON the skin wherever its author
built it at or under their own body. `#ride-body-floor` only stopped it going
INTO the body; the margin keeps up to `RIDE_BODY_FLOOR_MARGIN` of the clearance
the fit chain gave it, so the skin does not come through as soon as the body
moves under the cloth (front crotch while walking, from an in-game report).
"""
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

from src import nif_convert as nc
from src import nif_convert_layers as ncl


def _flat_body(n=9, step=1.0):
    g = (np.arange(n) - n // 2) * step
    xx, yy = np.meshgrid(g, g)
    v = np.stack([xx.ravel(), yy.ravel(), np.zeros(xx.size)], axis=1)
    return v, np.tile([0.0, 0.0, 1.0], (len(v), 1))


def _push(cand_z, fitted_z, margin):
    bv, bn = _flat_body()
    cand = np.array([[0.0, 0.0, z] for z in cand_z])
    fitted = np.array([[0.0, 0.0, z] for z in fitted_z])
    push, short = ncl._ride_body_floor_push(cand, fitted, bv, bn, cKDTree(bv), margin)
    return cand + push, short


def test_a_vert_the_fit_held_clear_keeps_the_margin():
    out, short = _push([0.0], [0.9], 0.2)
    assert abs(out[0, 2] - 0.2) < 1e-3, out
    assert short[0] > 0.19


def test_the_margin_never_exceeds_the_fitted_clearance():
    """A vert the fit chain put only 0.1u off is held at 0.1u, not pushed to the
    margin: the floor is a floor on what the fit decided, not a new standoff."""
    out, _ = _push([-0.3], [0.1], 0.2)
    assert abs(out[0, 2] - 0.1) < 1e-3, out


def test_a_vert_the_fit_left_inside_is_not_hauled_out():
    out, short = _push([-0.3, -0.8], [-0.5, -0.5], 0.2)
    assert short[0] == 0.0 and abs(out[0, 2] + 0.3) < 1e-9
    assert abs(out[1, 2] + 0.5) < 1e-3, "never deeper than the fit chain had it"


def test_a_vert_already_clear_of_the_floor_is_untouched():
    out, short = _push([0.6], [0.9], 0.2)
    assert short[0] == 0.0 and out[0, 2] == 0.6


def test_margin_zero_is_the_floor_as_it_was():
    out, _ = _push([-0.3], [0.9], 0.0)
    assert abs(out[0, 2]) < 1e-3


def _layer(name, src_z, fin_z, n=7):
    g = np.arange(n) - n // 2
    xx, yy = np.meshgrid(g, g)
    xy = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(float)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [(a, a + 1, a + n), (a + 1, a + n + 1, a + n)]
    sv = np.column_stack([xy, np.full(len(xy), src_z)])
    fv = np.column_stack([xy, np.full(len(xy), fin_z)])
    src = SimpleNamespace(name=name, verts=sv, tris=np.array(tris),
                          normals=np.tile([0.0, 0.0, 1.0], (len(sv), 1)),
                          textures={"diffuse": "x.dds"}, has_global_to_skin=False,
                          shader=None)
    return {"src": src, "verts": fv.copy()}


def _ride(monkeypatch, margin):
    """Inner layer authored 0.5u off, fitted 0.4u INTO the body; the outer one
    authored 0.1u above it and fitted 0.9u clear. The ride carries the inner
    layer's -0.9u onto it, which would put it inside the body."""
    monkeypatch.setattr(nc, "RIDE_BODY_FLOOR", True)
    monkeypatch.setattr(nc, "RIDE_BODY_FLOOR_MARGIN", margin)
    bv, bn = _flat_body(15)
    inner = _layer("inner", 0.5, -0.4)
    outer = _layer("outer", 0.6, 0.9)
    ncl._ride_layers_on_reference([inner, outer], body_verts=bv, body_norms=bn,
                                  enable_panels=False)
    return outer["verts"][:, 2]


def test_the_ride_keeps_the_margin_off_the_skin(monkeypatch):
    z = _ride(monkeypatch, 0.2)
    assert z.min() > 0.19, f"the ride set the layer down on the skin: min {z.min():.3f}"


def test_the_ride_without_a_margin_lands_on_the_skin(monkeypatch):
    """The control: margin 0 is the floor as it was, and the same stack lands
    on the skin -- so the test above is measuring the margin and nothing else."""
    z = _ride(monkeypatch, 0.0)
    assert z.min() < 0.05 and z.min() > -0.01


def test_the_default_margin_is_on():
    assert nc.RIDE_BODY_FLOOR_MARGIN > 0
