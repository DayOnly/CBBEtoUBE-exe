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


def test_a_physics_dress_listed_as_a_collider_is_never_lifted(monkeypatch):
    nf, BT, GV, DV = _dress_over_robe(monkeypatch)
    out, _ = bz._push_kept_legs(nf, "_1", BT, {"Dress"}, {})
    assert "Robe" in out and "Dress" not in out


def test_simulated_cloth_held_still_by_default(monkeypatch):
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
