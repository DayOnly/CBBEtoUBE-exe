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

"""Cross-shape layer passes must not depend on which skin frame a shape is
stored in (#abdomen-order-world-frame, #layer-order-surface).

Shapes in one NIF can carry DIFFERENT global-to-skin transforms. Measured on a
guard cuirass: the strap's frame sat 31u off the plates', the abdomen order
pass compared the raw verts, paired the strap with the wrong plate verts and
lifted the plate 1.8u straight through it. Phase-2 jobs hold SKIN-frame verts
for such a shape; phase-1 jobs carry `g2s` and hold WORLD verts.

The invariant pinned here: the same world-space garment, stored once with
identity frames and once with each shape in its own offset frame, comes out of
the pass at the SAME world positions.
"""
import numpy as np
from src import nif_convert as nc


class _G2S:
    def __init__(self, t, scale=1.0):
        self.translation = list(t)
        self.scale = scale
        self.rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


class _Src:
    def __init__(self, verts, g2s=None, tris=None, normals=None, name="s"):
        self.verts = verts
        self.has_global_to_skin = g2s is not None
        self.global_to_skin = g2s
        self.tris = tris if tris is not None else np.zeros((0, 3), np.int64)
        self.normals = normals
        self.name = name
        self.textures = {"Diffuse": "x.dds"}


def _grid(y, x_range, z_range, n):
    xs = np.linspace(*x_range, n)
    zs = np.linspace(*z_range, n)
    v = np.array([[x, y, z] for x in xs for z in zs], dtype=np.float64)
    tris = []
    for i in range(n - 1):
        for k in range(n - 1):
            a = i * n + k
            tris += [[a, a + 1, a + n], [a + 1, a + n + 1, a + n]]  # +Y winding
    return v, np.asarray(tris, np.int64)


def _body_plane(n=24):
    xs = np.linspace(-10, 10, n)
    zs = np.linspace(70, 96, n)
    bv = np.array([[x, 0.0, z] for x in xs for z in zs], dtype=np.float64)
    bn = np.tile(np.array([0.0, 1.0, 0.0]), (len(bv), 1))
    return bv, bn


def _to_skin(v, g):
    return nc._verts_world_to_skin(v, g) if g is not None else v


def _to_world(v, g):
    return nc._verts_skin_to_world(np.asarray(v, np.float64), g) if g is not None else np.asarray(v, np.float64)


def _abdomen_run(ga, gb, gbody):
    bv, bn = _body_plane()
    b_src, _t = _grid(1.0, (-8, 8), (72, 94), 20)
    a_src, _t = _grid(1.5, (-4, 4), (78, 90), 10)
    a_warp = a_src.copy(); a_warp[:, 1] = 1.0          # the warp sank the band
    b_warp = b_src.copy()
    A = {"override_skin": {"weights": {"NPC Spine": []}},
         "src": _Src(_to_skin(a_src, ga), ga), "verts": _to_skin(a_warp, ga),
         "verts_modified": False}
    B = {"override_skin": {"weights": {"NPC Spine": []}},
         "src": _Src(_to_skin(b_src, gb), gb), "verts": _to_skin(b_warp, gb),
         "verts_modified": False}
    nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=_to_skin(bv, gbody), source_body_normals=bn,
        source_body_g2s=gbody)
    return _to_world(A["verts"], ga), _to_world(B["verts"], gb)


def test_abdomen_pass_is_frame_invariant():
    ref_a, ref_b = _abdomen_run(None, None, None)
    assert float(np.median(ref_a[:, 1])) > float(np.median(ref_b[:, 1])) + 0.1, \
        "control: the sunk band must be lifted in the identity frame"
    # Strap and plate in frames 31u apart, body in the plate's frame -- the
    # measured guard-cuirass layout.
    ga, gb = _G2S((0.0, 0.9, -89.5)), _G2S((0.0, 1.5, -120.3))
    got_a, got_b = _abdomen_run(ga, gb, gb)
    assert np.abs(got_a - ref_a).max() < 1e-6
    assert np.abs(got_b - ref_b).max() < 1e-6


def _order_run(ga, gb, monkeypatch):
    monkeypatch.setattr(nc, "LAYER_ORDER_REPAIR_ENABLED", True)
    monkeypatch.setattr(nc, "LAYER_ORDER_SURFACE", True)
    b_src, bt = _grid(1.0, (-8, 8), (72, 94), 17)
    a_src, at = _grid(1.4, (-4, 4), (78, 90), 9)
    a_out = a_src.copy(); a_out[:, 1] = 0.6           # swallowed by B
    up = np.tile(np.array([0.0, 1.0, 0.0]), (1, 1))
    A = {"src": _Src(_to_skin(a_src, ga), ga, at, np.repeat(up, len(a_src), 0), "A"),
         "verts": _to_skin(a_out, ga), "verts_modified": False}
    B = {"src": _Src(_to_skin(b_src, gb), gb, bt, np.repeat(up, len(b_src), 0), "B"),
         "verts": _to_skin(b_src, gb), "verts_modified": False}
    n = nc._repair_layer_order([A, B])
    return n, _to_world(A["verts"], ga), _to_world(B["verts"], gb)


def test_layer_order_repair_is_frame_invariant(monkeypatch):
    n0, ref_a, ref_b = _order_run(None, None, monkeypatch)
    assert n0 > 0, "control: the swallowed layer must be repaired"
    assert float(np.median(ref_a[:, 1])) > 1.0, "A must come back outside B"
    n1, got_a, got_b = _order_run(_G2S((0.0, 0.9, -89.5)),
                                  _G2S((0.0, 1.5, -120.3)), monkeypatch)
    assert n1 == n0
    assert np.abs(got_a - ref_a).max() < 1e-6
    assert np.abs(got_b - ref_b).max() < 1e-6


def test_layer_order_surface_ignores_rim_slide(monkeypatch):
    """A vert BESIDE a layer's rim (offset along the surface, not over the
    face) carries no side; it must not be read as swallowed."""
    monkeypatch.setattr(nc, "LAYER_ORDER_REPAIR_ENABLED", True)
    monkeypatch.setattr(nc, "LAYER_ORDER_SURFACE", True)
    b_src, bt = _grid(1.0, (-4, 4), (78, 90), 9)
    # A sits just past B's x = +4 rim, in B's plane, and drifts slightly lower.
    a_src, at = _grid(1.0, (4.3, 6.3), (78, 90), 5)
    a_out = a_src.copy(); a_out[:, 1] = 0.9
    up = np.tile(np.array([0.0, 1.0, 0.0]), (1, 1))
    A = {"src": _Src(a_src, None, at, np.repeat(up, len(a_src), 0), "A"),
         "verts": a_out.copy(), "verts_modified": False}
    B = {"src": _Src(b_src, None, bt, np.repeat(up, len(b_src), 0), "B"),
         "verts": b_src.copy(), "verts_modified": False}
    assert nc._repair_layer_order([A, B]) == 0


def _radial_run(a_x, monkeypatch, radial=True):
    """Under-layer B at clearance 1.0; band A authored at 1.5 and sunk to 1.0
    by the warp. Both carry triangles, so the radial gate can judge."""
    monkeypatch.setattr(nc, "ABDOMEN_ORDER_RADIAL", radial)
    bv, bn = _body_plane()
    b_src, bt = _grid(1.0, (-8, -1), (72, 94), 15)
    a_src, at = _grid(1.5, a_x, (78, 90), 10)
    a_warp = a_src.copy(); a_warp[:, 1] = 1.0
    A = {"override_skin": {"weights": {"NPC Spine": []}},
         "src": _Src(a_src, None, at), "verts": a_warp.copy(), "verts_modified": False}
    B = {"override_skin": {"weights": {"NPC Spine": []}},
         "src": _Src(b_src, None, bt), "verts": b_src.copy(), "verts_modified": False}
    nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn)
    return float(np.max(np.asarray(A["verts"])[:, 1]))


def test_abdomen_pass_does_not_lift_over_a_lateral_neighbour(monkeypatch):
    """A band BESIDE the under-layer (2u off its edge, bare skin beneath) is
    'further from the body' than it, but nothing is under it to clear: it must
    stay where the fit put it (#abdomen-order-radial)."""
    assert _radial_run((1.0, 8.0), monkeypatch, radial=False) > 1.05, \
        "control: without the gate the lateral neighbour IS lifted"
    assert _radial_run((1.0, 8.0), monkeypatch) < 1.0 + 1e-6


def test_abdomen_pass_still_lifts_a_band_over_its_under_layer(monkeypatch):
    assert _radial_run((-6.0, -3.0), monkeypatch) > 1.05
