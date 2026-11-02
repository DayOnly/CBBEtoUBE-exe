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

"""Guard for the layer ride on zapped pieces (`#zap-keep-ride`, `#zap-ride-restore`).

On a zapped piece the warp alone is the authors' fit, but it moves each layer on its own and can squeeze a stacked
waist flat (in-game: corset over the shirt 0.23u in the source, 0.25u by the author, 0.00u ours). The ride gives back
ONLY the squeezed gap there: outward along the body normal, never pulling a layer the warp left looser back in.
"""
from types import SimpleNamespace

import numpy as np

from src import nif_convert as nc
from src import nif_convert_layers as ncl


def _flat_body(n=15):
    g = np.arange(n) - n // 2
    xx, yy = np.meshgrid(g, g)
    v = np.stack([xx.ravel(), yy.ravel(), np.zeros(xx.size)], axis=1).astype(float)
    return v, np.tile([0.0, 0.0, 1.0], (len(v), 1))


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


def _ride(monkeypatch, outer_fin, keep=True, restore=True):
    """Inner layer authored 0.5u off and warped to 1.0u; the outer one authored 0.3u above it."""
    monkeypatch.setattr(nc, "_zap_lean", lambda: True)
    monkeypatch.setattr(nc, "_ZAP_KEEP_RIDE", keep)
    monkeypatch.setattr(nc, "_ZAP_RIDE_RESTORE", restore)
    bv, bn = _flat_body()
    inner = _layer("inner", 0.5, 1.0)
    outer = _layer("outer", 0.8, outer_fin)
    ncl._ride_layers_on_reference([inner, outer], body_verts=bv, body_norms=bn)
    return outer["verts"][:, 2]


def test_a_squeezed_gap_is_given_back(monkeypatch):
    z = _ride(monkeypatch, 1.0)          # the warp laid the outer layer ON the inner one
    assert np.allclose(z, 1.3, atol=1e-3), f"source gap 0.3u not restored: {z.min():.3f}..{z.max():.3f}"


def test_a_looser_warp_is_not_pulled_in(monkeypatch):
    z = _ride(monkeypatch, 1.6)          # the warp kept MORE room than the source: that is the authors' fit
    assert np.allclose(z, 1.6, atol=1e-6), z


def test_the_plain_ride_pulls_it_in(monkeypatch):
    """The control: without the restore the ride re-derives the layer and drags the looser fit back to 1.3u -- so
    the test above measures the restore and nothing else."""
    z = _ride(monkeypatch, 1.6, restore=False)
    assert np.allclose(z, 1.3, atol=1e-3), z


def test_without_keep_ride_a_zapped_piece_is_not_ridden(monkeypatch):
    z = _ride(monkeypatch, 1.0, keep=False)
    assert np.allclose(z, 1.0, atol=1e-9), z


def test_both_are_on_by_default():
    assert nc._ZAP_KEEP_RIDE and nc._ZAP_RIDE_RESTORE
