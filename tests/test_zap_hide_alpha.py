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

"""Guards for `#zap-alpha` (see-through cover hides no skin) and `#zap-nipple-hide` (no bulge over a hidden nipple).

In game: a strap sleeve, transparent between its straps, had the upper-arm skin behind it deleted (the arms missing);
an opaque top kept the source sculpt's nipple point over skin the zap had already removed.
"""
from types import SimpleNamespace

import numpy as np

from src import body_zap as bz


def _sheet(n, half, y, bump=0.0, r_bump=1.5):
    """a sheet in the x/z plane at depth y facing +y, centred on (0, y, 0); optional bump at the centre"""
    xs = np.linspace(-half, half, n)
    X, Z = np.meshgrid(xs, xs)
    V = np.stack([X.ravel(), np.full(X.size, float(y)), Z.ravel()], 1)
    if bump:
        d = np.hypot(V[:, 0], V[:, 2])
        V[:, 1] += bump * np.clip(1.0 - d / r_bump, 0.0, None)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    return V, np.asarray(tris, np.int64)


# ---------------------------------------------------------------- #zap-alpha
def _strap(flags=0x200, alpha=True):
    V, T = _sheet(3, 1.0, 0.0)
    uv = np.c_[(V[:, 0] + 1.0) / 2.0, (V[:, 2] + 1.0) / 2.0]
    ap = SimpleNamespace(properties=SimpleNamespace(flags=flags, threshold=128)) if alpha else None
    return SimpleNamespace(alpha_property=ap, textures={"Diffuse": "textures/x.dds"}, uvs=uv), T


def _half_transparent(monkeypatch):
    A = np.zeros((64, 64), np.uint8); A[:, 32:] = 255          # left half see-through, right half opaque
    monkeypatch.setattr(bz, "_texture_alpha", lambda rel, dst: A)


def test_only_fully_opaque_triangles_cover(monkeypatch):
    _half_transparent(monkeypatch)
    s, T = _strap()
    m = bz._opaque_tris(s, T, "x")
    uv = np.asarray(s.uvs)[T]
    assert m is not None and m.any() and not m.all()
    assert np.all(uv[m][..., 0] >= 0.5 - 1e-9), "a covering triangle may not reach into the see-through half"


def test_a_blended_shape_is_judged_too(monkeypatch):
    _half_transparent(monkeypatch)
    s, T = _strap(flags=1)
    m = bz._opaque_tris(s, T, "x")
    assert m is not None and not m.all()


def test_no_alpha_or_unreadable_texture_stays_cover(monkeypatch):
    s, T = _strap(alpha=False)
    assert bz._opaque_tris(s, T, "x") is None
    monkeypatch.setattr(bz, "_texture_alpha", lambda rel, dst: None)
    s, T = _strap()
    assert bz._opaque_tris(s, T, "x") is None


# ---------------------------------------------------------------- #zap-nipple-hide
def _body():
    V, T = _sheet(41, 8.0, 0.0)
    apex = int(np.argmin(np.hypot(V[:, 0], V[:, 2])))
    return V, T, apex


def test_a_fine_cloth_bump_is_lowered_onto_its_ring():
    BV, BT, apex = _body()
    G, GT = _sheet(25, 6.0, 0.6, bump=0.5)                        # 0.5u edges, a 0.5u point over the apex
    out = bz._flatten_cloth_bump(G, GT, BV, BT, [apex])
    assert out is not None
    c = int(np.argmin(np.hypot(G[:, 0], G[:, 2])))
    assert out[c, 1] < 0.65 and G[c, 1] > 1.0, (G[c, 1], out[c, 1])
    assert np.all(out[:, 1] <= G[:, 1] + 1e-9), "lower only"
    far = np.hypot(G[:, 0], G[:, 2]) > bz.ZAP_HIDE_CORE_R
    assert np.allclose(out[far], G[far]), "the ring and beyond are untouched"


def test_coarse_cloth_is_left_alone():
    BV, BT, apex = _body()
    G, GT = _sheet(5, 6.0, 0.6, bump=0.5, r_bump=4.0)             # 3u edges cannot draw a nipple
    assert bz._flatten_cloth_bump(G, GT, BV, BT, [apex]) is None


def test_both_are_on_by_default():
    assert bz.ZAP_ALPHA and bz.ZAP_NIPPLE_HIDE
