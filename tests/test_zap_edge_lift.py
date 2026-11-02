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

"""#zap-edge-lift and #zap-preset-nipples.

A cloth edge sitting against kept skin is buried by the skin beside it as soon as a slider moves that skin more than
the edge (2026-10-30: 31% of such edge verts at +1 AND -1, whatever the transfer). After the zap it is lifted off the
skin at rest by what the worst single slider needs, capped, and the skin under the lifted cloth is kept.
"""
from types import SimpleNamespace
from pathlib import Path

import numpy as np

from src import body_zap as bz


def _sheet(n, half, y, z0=100.0):
    xs = np.linspace(-half, half, n)
    X, Z = np.meshgrid(xs, xs)
    V = np.stack([X.ravel(), np.full(X.size, float(y)), Z.ravel() + z0], 1)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [[a, a + 1, a + n], [a + 1, a + n + 1, a + n]]
    T = np.asarray(tris, np.int64)
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    if fn[:, 1].mean() < 0:
        T = T[:, [0, 2, 1]]
    return V, T


def _shape(name, V, T, flags=0):
    bw = {"NPC Spine2 [Spn2]": [(i, 1.0) for i in range(len(V))]}
    return SimpleNamespace(name=name, verts=V, tris=T, flags=flags, bone_weights=bw, bone_names=list(bw))


def _piece(monkeypatch, gap=0.05, rise=0.5):
    """a body sheet (y=0, normals +y) with a cloth panel `gap` above it; slider "Swell" lifts the body skin outside the
    panel's edge (|x| > 9) by `rise` and moves nothing under it; the cloth has no row for it."""
    BV, BT = _sheet(25, 12.0, 0.0)
    monkeypatch.setattr(bz, "full_body_tris", lambda w: (BT, BV))
    GV, GT = _sheet(19, 9.0, gap)
    idx = np.flatnonzero(np.abs(BV[:, 0]) > 9.0)
    swell = SimpleNamespace(name="Swell", idx=idx, delta=np.tile([0.0, rise, 0.0], (len(idx), 1)))
    tri = {bz.BODY_NAME: SimpleNamespace(morphs=[swell])}
    nf = SimpleNamespace(shapes=[_shape(bz.BODY_NAME, BV, BT), _shape("Panel", GV, GT)])
    return nf, BT, BV, GV, tri


def _normals_plus_y(monkeypatch):
    monkeypatch.setattr(bz, "_body_normals", lambda BV, T: np.tile([0.0, 1.0, 0.0], (len(BV), 1)))


def test_an_edge_beside_rising_skin_is_lifted_by_what_it_needs(monkeypatch):
    _normals_plus_y(monkeypatch)
    monkeypatch.setattr(bz, "ZAP_EDGE_LIFT_CAP", 1.0)     # under the cap: the lift is what the slider needs
    nf, BT, BV, GV, tri = _piece(monkeypatch)
    over = {}
    moved, keep = bz._edge_lift({"_1": (nf, "x")}, BT, tri, over, set())
    assert moved > 0
    new = over["_1"]["Panel"]
    edge = np.abs(np.abs(GV[:, 0]) - 9.0) < 1e-6
    lift = new[:, 1] - GV[:, 1]
    # need 0.5 (+0.05 tolerance) minus the 0.05 gap it already has
    assert np.allclose(lift[edge], 0.5, atol=1e-6)
    assert np.all(lift >= -1e-12) and np.allclose(new[:, [0, 2]], GV[:, [0, 2]]), "outward only, along the normal"
    centre = np.linalg.norm(GV[:, [0, 2]] - [0.0, 100.0], axis=1) < 5.0
    assert np.allclose(lift[centre], 0.0), "the feather stops inside the panel"
    assert keep is not None and keep[np.abs(BV[:, 0]) > 9.0].any(), "the skin under the lifted edge is kept"


def test_the_lift_is_capped(monkeypatch):
    _normals_plus_y(monkeypatch)
    nf, BT, BV, GV, tri = _piece(monkeypatch, rise=3.0)
    over = {}
    bz._edge_lift({"_1": (nf, "x")}, BT, tri, over, set())
    assert (over["_1"]["Panel"][:, 1] - GV[:, 1]).max() <= bz.ZAP_EDGE_LIFT_CAP + 1e-9


def test_an_edge_with_room_or_beside_deleted_skin_stays(monkeypatch):
    _normals_plus_y(monkeypatch)
    nf, BT, BV, GV, tri = _piece(monkeypatch, gap=0.8)              # 0.8u of room covers a 0.5u rise
    assert bz._edge_lift({"_1": (nf, "x")}, BT, tri, {}, set())[0] == 0
    nf, BT, BV, GV, tri = _piece(monkeypatch)
    assert bz._edge_lift({"_1": (nf, "x")}, BT[:0], tri, {}, set())[0] == 0   # the zap kept no skin here


def test_colliders_and_hidden_shapes_never_move(monkeypatch):
    _normals_plus_y(monkeypatch)
    nf, BT, BV, GV, tri = _piece(monkeypatch)
    assert bz._edge_lift({"_1": (nf, "x")}, BT, tri, {}, {"Panel"})[0] == 0
    nf.shapes[1].flags = 1
    assert bz._edge_lift({"_1": (nf, "x")}, BT, tri, {}, set())[0] == 0


def test_seam_duplicates_are_not_an_edge():
    V, T = _sheet(5, 2.0, 0.0)
    # split the sheet along x = 0 with duplicated verts: the seam is not an open edge
    left = V[:, 0] < 1e-9
    V2 = np.concatenate([V, V[left & (np.abs(V[:, 0]) < 1e-9)]])
    edge, _ = bz._open_edge_verts(V2, T)
    inner = (np.abs(V2[:, 0]) < 1.9) & (np.abs(V2[:, 2] - 100.0) < 1.9)
    assert not edge[:len(V)][inner[:len(V)]].any()


def test_both_options_are_on_by_default():
    assert bz.ZAP_EDGE_LIFT and bz.ZAP_NIPPLE_OPAQUE and bz.ZAP_EDGE_LIFT_CAP == 0.2


def test_preset_nipples_is_removed():
    # 2026-10-30: judging the presets with their nipple sliders drew a nipple outline through opaque armour in game
    assert not hasattr(bz, "ZAP_PRESET_NIPPLES")
    assert "CBBE2UBE_NO_ZAP_PRESET_NIPPLES" not in Path(bz.__file__).read_text(encoding="utf-8")


def test_rest_clear_lifts_a_face_the_skin_pokes_through_and_nothing_else(monkeypatch):
    """#zap-rest-clear: a panel whose middle dips 0.4u under the skin (the skin pokes through its face at rest) has
    the dip lifted out over the skin by the poke + margin; the rest of the panel and its open edges stay put."""
    _normals_plus_y(monkeypatch)
    BV, BT = _sheet(25, 12.0, 0.0)
    GV, GT = _sheet(19, 9.0, 0.3)
    dip = (np.abs(GV[:, 0]) < 2.5) & (np.abs(GV[:, 2] - 100.0) < 2.5)
    GV = GV.copy(); GV[dip, 1] = -0.4
    nf = SimpleNamespace(shapes=[_shape(bz.BODY_NAME, BV, BT), _shape("Panel", GV, GT)])
    out = bz._rest_clear(nf, nf.shapes[0], BT, set(), {"NPC Spine2 [Spn2]"})
    assert set(out) == {"Panel"}
    lift = out["Panel"][:, 1] - GV[:, 1]
    assert np.allclose(out["Panel"][:, [0, 2]], GV[:, [0, 2]]), "along the body normal only"
    assert (out["Panel"][dip, 1] > 0.0).all(), "the dip now clears the skin"
    assert lift.max() <= 0.4 + bz.ZAP_REST_CLEAR_M + 1e-9, "no more than the poke + margin"
    edge, _ = bz._open_edge_verts(GV, GT)
    assert np.allclose(lift[edge], 0.0), "open edges stay put"


def test_rest_clear_leaves_cloth_that_clears_alone(monkeypatch):
    _normals_plus_y(monkeypatch)
    BV, BT = _sheet(25, 12.0, 0.0)
    GV, GT = _sheet(19, 9.0, 0.3)
    nf = SimpleNamespace(shapes=[_shape(bz.BODY_NAME, BV, BT), _shape("Panel", GV, GT)])
    assert bz._rest_clear(nf, nf.shapes[0], BT, set(), {"NPC Spine2 [Spn2]"}) == {}
    assert bz.ZAP_REST_CLEAR
