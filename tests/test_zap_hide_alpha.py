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


def test_the_lowering_stops_short_of_kept_skin():
    # #zap-nipple-hide-covered hides a nipple whose ring skin is partly KEPT (a cup edge nearby): the lowered cloth
    # must not come down onto that skin
    BV, BT, apex = _body()
    G, GT = _sheet(25, 6.0, 0.6, bump=0.5)
    skin = np.array([[0.3, 0.75, 0.3]])                           # kept skin just under where the bump would land
    out = bz._flatten_cloth_bump(G, GT, BV, BT, [apex], kept_skin=skin)
    c = int(np.argmin(np.hypot(G[:, 0], G[:, 2])))
    assert out is not None and out[c, 1] < G[c, 1], "still lowered as far as the skin allows"
    near = np.hypot(G[:, 0] - 0.3, G[:, 2] - 0.3) < 0.6
    assert np.all(out[near, 1] >= 0.75 + bz.ZAP_HIDE_KEEP_GAP - 0.05), out[near, 1]


# #zap-nipple-poke. In game 10-22: pointed nipples on coarse plates -- the zap kept nipple skin poking through the
# plate (seen from the side) and #min-push lifted the plate over it; the authors' plates run under the breast's tip.
def _sphere(R, n=48):
    """a closed lat-long sphere of radius R about the origin (outward-wound), its +y pole a vertex"""
    th = np.linspace(0.0, np.pi, n // 2 + 1)[1:-1]; ph = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    T_, P_ = np.meshgrid(th, ph, indexing="ij")
    V = np.c_[R * np.sin(T_.ravel()) * np.cos(P_.ravel()), R * np.cos(T_.ravel()), R * np.sin(T_.ravel()) * np.sin(P_.ravel())]
    V = np.vstack([V, [0.0, R, 0.0], [0.0, -R, 0.0]]); top, bot = len(V) - 2, len(V) - 1
    rows, F = len(th), []
    for i in range(rows - 1):
        for j in range(n):
            a, b, c, d = i * n + j, i * n + (j + 1) % n, (i + 1) * n + j, (i + 1) * n + (j + 1) % n
            F += [[a, b, c], [b, d, c]]
    F += [[top, (j + 1) % n, j] for j in range(n)] + [[bot, (rows - 1) * n + j, (rows - 1) * n + (j + 1) % n] for j in range(n)]
    F = np.asarray(F, np.int64)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    if ((V[F].mean(1)) * fn).sum() < 0:
        F = F[:, [0, 2, 1]]
    return V, F, top


def _cap(R, half=6.0, n=13):
    """a cloth patch on the sphere of radius R over the +y pole"""
    xs = np.linspace(-half, half, n); X, Z = np.meshgrid(xs, xs)
    Y = np.sqrt(np.clip(R * R - X ** 2 - Z ** 2, 0.0, None))
    V = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    _, T = _sheet(n, half, 0.0)
    return V, T


def test_skin_poking_through_cloth_just_under_it_counts_as_covered():
    B, BT, top = _sphere(10.0)
    G, GT = _cap(9.0)                                              # the cloth 1u under the skin at the pole
    out = bz._poking_through(B, BT, [G], [GT], np.array([top]), len(B))
    assert out[top]


def test_cloth_above_the_skin_or_far_under_it_does_not():
    B, BT, top = _sphere(10.0)
    for R in (11.0, 10.0 - bz.ZAP_POKE_DEPTH - 1.0):               # over the skin (seen around it) / far inside
        G, GT = _cap(R)
        assert not bz._poking_through(B, BT, [G], [GT], np.array([top]), len(B))[top], R


def test_cloth_level_with_the_skin_counts():
    B, BT, top = _sphere(10.0)
    G, GT = _cap(10.0 + 0.5 * bz.ZAP_POKE_LEVEL)
    assert bz._poking_through(B, BT, [G], [GT], np.array([top]), len(B))[top]


def test_the_edge_band_does_not_bring_back_skin_that_poked_through():
    V = np.c_[np.arange(6, dtype=float), np.zeros(6), np.zeros(6)]  # verts 1u apart in a row
    vis = np.array([True, False, False, False, False, False])        # vert 0 seen at bind
    de = ~vis
    kept = bz._keep_edge_band(de, vis, V)
    assert not kept[1] and kept[3], "the band keeps the skin next to an opening"
    poked = np.zeros(6, bool); poked[1] = True
    assert bz._keep_edge_band(de, vis, V, poked)[1], "skin that poked through cloth stays deleted"


def test_min_push_leaves_the_zapped_nipple_alone(monkeypatch):
    from src import nif_convert as nc
    B, BT, top = _sphere(10.0)
    B = B + np.array([0.0, 0.0, 100.0])                          # into the push band (z 90-125)
    shape = SimpleNamespace(verts=B, tris=BT)
    monkeypatch.setattr(bz, "_nipple_apexes", lambda body, full: [top])
    monkeypatch.setattr(nc, "_zap_lean", lambda: True)
    reg = nc._min_push_region(B, shape)
    base = nc.fit_metrics.push_region_mask(B)
    near = np.linalg.norm(B - B[top], axis=1) < bz.ZAP_NIPPLE_R
    assert base[near].any() and not reg[near].any()
    assert np.array_equal(reg[~near], base[~near])
    monkeypatch.setattr(nc, "_zap_lean", lambda: False)            # not a zapped piece: the whole band is judged
    assert np.array_equal(nc._min_push_region(B, shape), base)


def test_all_are_on_by_default():
    from src import nif_convert as nc
    assert bz.ZAP_ALPHA and bz.ZAP_NIPPLE_HIDE and bz.ZAP_NIPPLE_POKE and bz.ZAP_NIPPLE_HIDE_COVERED
    assert nc._MIN_PUSH_ZAP_NIPPLE
