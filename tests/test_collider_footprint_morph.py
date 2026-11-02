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

"""#collider-footprint-morph (audit 2026-10-20 F14).

A coarse SMP collider copied the motion of the ONE skin point nearest each of its verts, so skin bulging between its
verts grew through its faces under a preset. A collider vert now moves out at least as far as any skin it stands for.
"""
import numpy as np

from src import sliderset_gen as sg
from src.osd import OsdFile, OsdMorph


def _grid(n, step, y):
    xs = np.arange(n) * step
    X, Z = np.meshgrid(xs, xs)
    V = np.stack([X.ravel(), np.full(X.size, float(y)), Z.ravel() + 40.0], 1)
    T = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            T += [[a, a + 1, a + n], [a + 1, a + n + 1, a + n]]
    T = np.asarray(T, np.int64)
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    if fn[:, 1].mean() < 0:
        T = T[:, [0, 2, 1]]
    return V, T


def _scene():
    BV, _ = _grid(17, 1.0, 0.0)                 # fine skin, 1u spacing
    CV, CT = _grid(5, 4.0, 1.0)                 # coarse collider 1u off it, 4u spacing
    bump = np.flatnonzero((np.abs(BV[:, 0] - 6.0) < 0.1) & (np.abs(BV[:, 2] - 46.0) < 0.1))
    d = np.zeros_like(BV); d[bump, 1] = 1.0     # skin bulges between collider verts
    return BV, CV, CT, bump, d


def test_a_collider_vert_moves_with_the_bulge_it_stands_for():
    BV, CV, CT, bump, d = _scene()
    from scipy.spatial import cKDTree
    nearest = cKDTree(BV).query(CV)[1]
    assert np.allclose(d[nearest], 0.0), "the bulge is at no collider vert's nearest skin point"
    prep = sg._collider_footprint_prep(CV, CT, BV, nearest)
    out = sg._collider_footprint_apply(d[nearest], prep, d)
    assert out[:, 1].max() == 1.0
    owner = cKDTree(CV).query(BV[bump])[1]
    assert np.all(out[owner, 1] == 1.0)


def test_never_less_outward_than_before():
    BV, CV, CT, bump, d = _scene()
    from scipy.spatial import cKDTree
    nearest = cKDTree(BV).query(CV)[1]
    prep = sg._collider_footprint_prep(CV, CT, BV, nearest)
    own = np.zeros_like(CV); own[:, 1] = 2.0
    assert np.array_equal(sg._collider_footprint_apply(own, prep, d), own)


def test_outward_follows_the_body_side_even_with_inverted_winding():
    BV, CV, CT, bump, d = _scene()
    from scipy.spatial import cKDTree
    nearest = cKDTree(BV).query(CV)[1]
    _, vn = sg._collider_footprint_prep(CV, CT[:, [0, 2, 1]], BV, nearest)
    assert np.all(vn[:, 1] > 0.9)


def _tri(colliders, monkeypatch=None):
    BV, CV, CT, bump, d = _scene()
    osd = OsdFile(version=1, morphs=[OsdMorph("BaseShapeBulge", bump, d[bump])])
    tri = sg.generate_armor_tri({"Proxy": CV}, BV, osd, include_body_shapes=False,
                                collider_tris={"Proxy": CT} if colliders else None)
    sh = next((s for s in tri.shapes if s.name == "Proxy"), None)        # no motion at all -> no table
    m = next((m for m in sh.morphs if m.name == "Bulge"), None) if sh is not None else None
    full = np.zeros_like(CV)
    if m is not None:
        full[np.asarray(m.idx)] = np.asarray(m.delta)
    return full


def test_generate_armor_tri_applies_it_to_colliders_only():
    assert _tri(True)[:, 1].max() > 0.99
    assert _tri(False)[:, 1].max() < 0.99


def test_off_switch(monkeypatch):
    monkeypatch.setattr(sg, "_COLLIDER_FOOTPRINT_MORPH", False)
    assert _tri(True)[:, 1].max() < 0.99


def test_only_outward_room_is_added_the_own_sideways_motion_stays():
    BV, CV, CT, bump, d = _scene()
    d = d.copy(); d[bump, 0] = 5.0                  # the bulge also slides sideways
    from scipy.spatial import cKDTree
    nearest = cKDTree(BV).query(CV)[1]
    prep = sg._collider_footprint_prep(CV, CT, BV, nearest)
    own = np.zeros_like(CV); own[:, 0] = 0.25
    out = sg._collider_footprint_apply(own, prep, d)
    assert np.allclose(out[:, 0], 0.25), "no neighbour's sideways motion"
    assert out[:, 1].max() == 1.0


def test_skin_facing_away_gives_no_room():
    # The far leg's skin (facing away from the collider vert) is not skin it stands for.
    BV, CV, CT, bump, d = _scene()
    from scipy.spatial import cKDTree
    nearest = cKDTree(BV).query(CV)[1]
    facing = np.tile([0.0, 1.0, 0.0], (len(BV), 1))
    away = -facing
    zero = np.zeros_like(CV)
    assert sg._collider_footprint_apply(zero, sg._collider_footprint_prep(CV, CT, BV, nearest, facing), d)[:, 1].max() == 1.0
    assert sg._collider_footprint_apply(zero, sg._collider_footprint_prep(CV, CT, BV, nearest, away), d)[:, 1].max() == 0.0


def test_a_collider_under_the_skin_still_grows_outward():
    # A body-shaped proxy 1u UNDER the skin: "away from the skin" is inward; the body's own normal is the truth.
    BV, _ = _grid(17, 1.0, 0.0)
    CV, CT = _grid(5, 4.0, -1.0)
    from scipy.spatial import cKDTree
    nearest = cKDTree(BV).query(CV)[1]
    _, vn = sg._collider_footprint_prep(CV, CT, BV, nearest, np.tile([0.0, 1.0, 0.0], (len(BV), 1)))
    assert np.all(vn[:, 1] > 0.9)


def test_only_hidden_proxies_take_the_collider_rule(monkeypatch):
    from types import SimpleNamespace
    from src import nif_convert_writer as nwr
    from src import nif_convert_trigen as tg
    monkeypatch.setattr(nwr, "_hdt_collider_shape_names", lambda *a, **k: {"robes", "SkirtCol"})
    V = np.zeros((3, 3)); T = [[0, 1, 2]]
    nif = SimpleNamespace(filepath="x.nif", shapes=[SimpleNamespace(name="robes", flags=14, verts=V, tris=T),
                                                    SimpleNamespace(name="SkirtCol", flags=15, verts=V, tris=T)])
    got = tg._collider_tri_inputs(nif, {"robes": V, "SkirtCol": V}, xml_owner="src.nif")
    assert set(got) == {"SkirtCol"}, "a visible cloth the XML also collides with keeps cloth morphs"


def _morphs(tri, name):
    sh = next((s for s in tri.shapes if s.name == name), None)
    return {m.name: (np.asarray(m.idx).tolist(), np.round(np.asarray(m.delta), 9).tolist())
            for m in (sh.morphs if sh is not None else [])}


def test_the_cloth_over_a_collider_morphs_exactly_as_without_the_rule(monkeypatch):
    # The room goes on after the layer lockstep / stack: the robe over the proxy neither rises over the added room
    # (zap holes on mage robes) nor loses the proxy as the layer beneath it (torso poke under Too Big), 2026-10-21.
    BV, CV, CT, bump, d = _scene()
    osd = OsdFile(version=1, morphs=[OsdMorph("BaseShapeBulge", bump, d[bump])])
    def run():
        return sg.generate_armor_tri({"Proxy": CV, "Robe": CV + [0, 1.0, 0]}, BV, osd, include_body_shapes=False,
                                     armor_shape_tris={"Proxy": CT, "Robe": CT}, collider_tris={"Proxy": CT})
    on = run()
    monkeypatch.setattr(sg, "_COLLIDER_FOOTPRINT_MORPH", False)
    off = run()
    assert _morphs(on, "Robe") == _morphs(off, "Robe")
    assert _morphs(on, "Proxy") != _morphs(off, "Proxy"), "the proxy itself still takes the room"
