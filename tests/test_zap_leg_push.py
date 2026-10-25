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

"""#zap-leg-push (audit 2026-10-20 F25).

The zap keeps lower-leg skin for robe hems in motion, and #zap-lean skips the anti-poke that lifted the cloth off it,
so robes and skirts sat inside the UBE leg: legs / hips showed through at bind. After the zap, the cloth is lifted
over the KEPT leg / hip skin by `minimum_push`'s measured, smallest lift.
"""
from types import SimpleNamespace

import numpy as np

from src import body_zap as bz
from src import fit_metrics as fm


def _sheet(n, half, y, z0=30.0):
    """a sheet in the x/z plane at depth y, normals +y, centred on (0, y, z0)"""
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


def _shape(name, V, T, bones=None, flags=0):
    n = len(V)
    bw = bones or {"NPC L Thigh [LThg]": [(i, 1.0) for i in range(n)]}
    return SimpleNamespace(name=name, verts=V, tris=T, flags=flags, normals=np.tile([0.0, 1.0, 0.0], (n, 1)),
                           bone_weights=bw, bone_names=list(bw))


def _piece(monkeypatch, cloth_y=-0.3, z0=30.0):
    BV, BT = _sheet(25, 12.0, 0.0, z0)
    monkeypatch.setattr(bz, "full_body_tris", lambda w: (BT, BV))
    GV, GT = _sheet(19, 9.0, cloth_y, z0)
    body = _shape(bz.BODY_NAME, BV, BT)
    return SimpleNamespace(shapes=[body, _shape("Robe", GV, GT)]), BT, GV


def test_cloth_inside_kept_leg_skin_is_lifted_just_clear(monkeypatch):
    nf, BT, GV = _piece(monkeypatch)
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    assert st["exposed_before"] > 0 and st["exposed_after"] == 0
    lift = out["Robe"][:, 1] - GV[:, 1]
    assert lift.max() <= 0.3 + fm.PUSH_MARGIN + 0.05, "the smallest lift, not an inflate"
    assert np.all(lift >= -1e-9), "outward only"


def test_skin_above_the_hips_is_not_this_pass(monkeypatch):
    nf, BT, _ = _piece(monkeypatch, z0=110.0)                 # a bust-height sheet: #push-tri's band, not this one
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    assert out == {} and st.get("skipped") == "region too small"


def test_deleted_skin_is_not_judged(monkeypatch):
    nf, BT, _ = _piece(monkeypatch)
    out, _ = bz._push_kept_legs(nf, "_1", BT[:0], set(), {})   # the zap kept nothing here
    assert out == {}


def test_colliders_and_hidden_shapes_never_move(monkeypatch):
    nf, BT, GV = _piece(monkeypatch)
    nf.shapes[1].flags = 1
    assert bz._push_kept_legs(nf, "_1", BT, set(), {})[0] == {}
    nf.shapes[1].flags = 0
    assert bz._push_kept_legs(nf, "_1", BT, {"Robe"}, {})[0] == {}


def _dress_over_robe(monkeypatch):
    nf, BT, GV = _piece(monkeypatch)                          # the robe 0.3u inside the skin
    DV, DT = _sheet(19, 9.0, -0.1)                            # a physics dress 0.2u above it, still under the skin
    nf.shapes.append(_shape("Dress", DV, DT, bones={"Dress Bone 01": [(i, 1.0) for i in range(len(DV))]}))
    return nf, BT, GV, DV


def test_a_physics_dress_above_rides_the_lift(monkeypatch):
    # #zap-leg-push-stack: the dress the XML lists as a collider is no cover (the robe still clears the skin) but it
    # rests above the robe, so it is lifted with it and the two keep their order
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_STACK", True)
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_SIM", True)
    nf, BT, GV, DV = _dress_over_robe(monkeypatch)
    out, _ = bz._push_kept_legs(nf, "_1", BT, {"Dress"}, {})
    c = int(np.argmin(np.linalg.norm(GV[:, [0, 2]] - [0.0, 30.0], axis=1)))
    robe, dress = out["Robe"][c, 1] - GV[c, 1], out["Dress"][c, 1] - DV[c, 1]
    assert robe > 0.3 and dress >= robe - 0.05
    assert out["Dress"][c, 1] > out["Robe"][c, 1], "the dress stays above"


def test_dress_stack_off_by_default(monkeypatch):
    nf, BT, GV, DV = _dress_over_robe(monkeypatch)
    out, _ = bz._push_kept_legs(nf, "_1", BT, {"Dress"}, {})
    assert "Robe" in out and "Dress" not in out


def test_simulated_cloth_held_still_by_default(monkeypatch):
    assert not bz.ZAP_LEG_PUSH_SIM and not bz.ZAP_LEG_PUSH_STACK
    nf, BT, GV = _piece(monkeypatch)
    nf.shapes[1].bone_weights = {"Skirt Bone 01": [(i, 1.0) for i in range(len(GV))]}
    nf.shapes[1].bone_names = ["Skirt Bone 01"]
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    assert out == {} and st.get("skipped") == "all simulated"


def test_a_seam_takes_its_largest_lift():
    V = np.array([[0.0, 0, 0], [0.0, 0, 0], [5.0, 0, 0], [5.0, 0, 0]])
    d = np.array([[0, 0.1, 0], [0, 0.4, 0], [0, 0.2, 0], [0, 0.0, 0]])
    w = bz._weld_max_lift(V, d, np.array([False, False, False, True]))
    assert np.allclose(w[0], [0, 0.4, 0]) and np.allclose(w[1], [0, 0.4, 0])
    assert np.allclose(w[2], [0, 0.2, 0]) and np.allclose(w[3], 0.0)


def test_minimum_push_keeps_the_bust_band_by_default():
    BV, BT = _sheet(25, 12.0, 0.0, 30.0)
    GV, GT = _sheet(19, 9.0, -0.3, 30.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(BV), 1)); gn = np.tile([0.0, 1.0, 0.0], (len(GV), 1))
    out, st = fm.minimum_push(GV, GT, gn, BV, BT, bn)
    assert st["skipped"] == "region too small" and np.array_equal(out, GV)


def test_deep_hits_are_not_lifted_toward(monkeypatch):
    # Cloth 1.5u under the skin is other geometry (a hem's far side, an inner layer), not this cloth just inside it.
    nf, BT, GV = _piece(monkeypatch, cloth_y=-1.5)
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    assert out == {}


def test_the_lift_is_capped_for_shallow_poke(monkeypatch):
    nf, BT, GV = _piece(monkeypatch, cloth_y=-0.9)
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    lift = out["Robe"][:, 1] - GV[:, 1]
    assert lift.max() <= bz.ZAP_LEG_PUSH_MAX_DEPTH + 0.1 + 1e-9




def _room(monkeypatch, y, sim=False, flags=0, name="Skirt", lift=0.5):
    """#zap-leg-push-room on its own: the robe (0.3u inside the skin) lifted `lift` along +y under a layer at `y`."""
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_ROOM", 0.2)
    nf, BT, GV = _piece(monkeypatch)
    SV, ST = _sheet(19, 9.0, y)
    bones = {"Skirt Bone 01": [(i, 1.0) for i in range(len(SV))]} if sim else None
    nf.shapes.append(_shape(name, SV, ST, bones=bones, flags=flags))
    d = np.tile([0.0, lift, 0.0], (len(GV), 1))
    body_bones = set(nf.shapes[0].bone_names)
    out, n = bz._room_cap(nf, GV, d, np.zeros(len(GV), bool), set(), {}, body_bones)
    return out[:, 1], n


def test_the_lift_leaves_room_under_a_physics_skirt(monkeypatch):
    # the skirt (simulated, never lifted) 0.4u above the robe: the robe stops ROOM short of it
    lift, n = _room(monkeypatch, 0.1, sim=True)
    assert n > 0 and np.allclose(lift, 0.4 - bz.ZAP_LEG_PUSH_ROOM, atol=1e-6)


def test_a_layer_far_above_or_a_rigid_one_does_not_cap(monkeypatch):
    lift, n = _room(monkeypatch, 2.0, sim=True)
    assert n == 0 and np.allclose(lift, 0.5)
    lift, n = _room(monkeypatch, 0.1)                         # rigid cloth: #zap-leg-push lifts it too
    assert n == 0 and np.allclose(lift, 0.5)


def test_room_under_colliders_and_its_switch(monkeypatch):
    lift, n = _room(monkeypatch, 0.1, flags=1, name="PantsCol")
    assert n > 0 and np.allclose(lift, 0.4 - bz.ZAP_LEG_PUSH_ROOM, atol=1e-6)
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_ROOM_COLLIDERS", False)
    assert _room(monkeypatch, 0.1, flags=1, name="PantsCol")[1] == 0


def test_room_is_opt_in_and_off_by_default(monkeypatch):
    assert bz.ZAP_LEG_PUSH_ROOM == 0.0
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_ROOM", 0.2)
    nf, BT, GV = _piece(monkeypatch)
    SV, ST = _sheet(19, 9.0, 0.1)
    nf.shapes.append(_shape("PantsCol", SV, ST, flags=1))
    capped = bz._push_kept_legs(nf, "_1", BT, set(), {})[1].get("room_capped")
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_ROOM", 0.0)
    st = bz._push_kept_legs(nf, "_1", BT, set(), {})[1]
    assert capped and "room_capped" not in st


def _centre_lift(out, GV):
    c = int(np.argmin(np.linalg.norm(GV[:, [0, 2]] - [0.0, 30.0], axis=1)))
    return out["Robe"][c, 1] - GV[c, 1] if "Robe" in out else 0.0


def test_physics_cloth_is_lifted_like_any_layer(monkeypatch):
    # #zap-leg-push-sim (opt-in): a physics skirt just under the skin is lifted (the layer the skin pokes through)
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_SIM", True)
    nf, BT, GV = _piece(monkeypatch)
    nf.shapes[1].bone_weights = {"Skirt Bone 01": [(i, 1.0) for i in range(len(GV))]}
    nf.shapes[1].bone_names = ["Skirt Bone 01"]
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    assert st["exposed_after"] == 0 and _centre_lift(out, GV) > 0.3


def test_the_skirt_over_a_plate_is_lifted_not_the_plate(monkeypatch):
    # plate 0.6u inside the skin, physics skirt 0.3u inside it: the skirt (the layer just under the skin) clears the
    # poke; the plate keeps its place under it, so the two keep their order
    monkeypatch.setattr(bz, "ZAP_LEG_PUSH_SIM", True)
    nf, BT, GV = _piece(monkeypatch, cloth_y=-0.6)
    SV, ST = _sheet(19, 9.0, -0.3)
    nf.shapes.append(_shape("Skirt", SV, ST, bones={"Skirt Bone 01": [(i, 1.0) for i in range(len(SV))]}))
    out, st = bz._push_kept_legs(nf, "_1", BT, set(), {})
    c = int(np.argmin(np.linalg.norm(SV[:, [0, 2]] - [0.0, 30.0], axis=1)))
    assert "Skirt" in out and out["Skirt"][c, 1] > 0.0
    assert "Robe" not in out or out["Robe"][c, 1] < out["Skirt"][c, 1]
