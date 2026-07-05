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

"""Anti-poke refinements: push-field smoothing (feathers, never reopens,
all-zero no-op) and layer-aware clearance floors (req_extra separates stacked
garments; 0.0 = unchanged)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nif_convert import (clear_armor_outside_body, _smooth_push_field,
                             _rank_body_layers, LAYERED_ANTIPOKE_EPSILON)


class _FakeLayer:
    """Duck-typed shape for _rank_body_layers: name/verts/bone_names, no
    transform / global_to_skin (helpers None-safe -> identity)."""
    def __init__(self, name, y, n=16, bones=("NPC Spine [Spn0]",)):
        self.name = name
        self.verts = [(float(i), float(y), 0.0) for i in range(n)]
        self.bone_names = list(bones)
        self.transform = None


def _flat_body(n=40, spacing=1.0):
    xs = np.arange(n) * spacing
    verts = np.stack([xs, np.zeros(n), np.zeros(n)], axis=1)
    normals = np.tile([0.0, 1.0, 0.0], (n, 1))
    return verts, normals


def _strip_tris(n):
    """Tris chaining vert i to i+1, i+2 (a strip) so adjacency is a line."""
    return np.array([[i, i + 1, i + 2] for i in range(n - 2)], dtype=np.int64)


def test_smooth_never_reopens_and_feathers():
    n = 9
    tris = _strip_tris(n)
    needed = np.zeros(n)
    needed[4] = 1.0                      # one spike push
    out = _smooth_push_field(needed.copy(), needed, tris, iters=2)
    assert out[4] >= 1.0 - 1e-12, "spike vert must keep its full push (floor)"
    assert out[3] > 0 and out[5] > 0, "neighbors must be feathered"
    assert out[0] < out[3], "feather decays with distance"
    print("  test_smooth_never_reopens_and_feathers OK")


def test_smooth_all_zero_is_noop():
    n = 9
    tris = _strip_tris(n)
    z = np.zeros(n)
    out = _smooth_push_field(z.copy(), z, tris, iters=3)
    assert np.all(out == 0), "no pokes -> smoothing must change nothing"
    print("  test_smooth_all_zero_is_noop OK")


def test_clear_with_tris_none_matches_legacy():
    bv, bn = _flat_body()
    av = np.array([[10.0, 0.05, 0.0], [30.0, 0.5, 0.0]])
    amp = np.zeros(len(bv))
    a = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp, tris=None)
    b = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp)
    assert np.allclose(a, b), "tris=None must be byte-identical to legacy"
    print("  test_clear_with_tris_none_matches_legacy OK")


def test_req_extra_separates_layers():
    bv, bn = _flat_body()
    amp = np.zeros(len(bv))
    # two 'layers' both starting slightly above the body at the same spot
    inner = np.array([[20.0, 0.05, 0.0]])
    outer = np.array([[20.0, 0.10, 0.0]])
    out_in = clear_armor_outside_body(inner, bv, bn, morph_amplitude=amp,
                                      req_extra=0.0)
    out_out = clear_armor_outside_body(outer, bv, bn, morph_amplitude=amp,
                                       req_extra=0.15)
    gap = out_out[0, 1] - out_in[0, 1]
    assert abs(gap - 0.15) < 1e-6, \
        f"layered floors must separate by req_extra, gap={gap}"
    # and req_extra=0 output is unchanged vs legacy call
    legacy = clear_armor_outside_body(inner, bv, bn, morph_amplitude=amp)
    assert np.allclose(out_in, legacy)
    print("  test_req_extra_separates_layers OK")


def _rank(shapes, **kw):
    bv, _ = _flat_body()
    base = dict(body_names={"BaseShape"}, reskin_skip=set(),
                softbody_names=set(), collider_names=set(),
                ube_bones={"NPC Spine [Spn0]"})
    base.update(kw)
    return _rank_body_layers(shapes, bv, **base)


def test_rank_orders_layers_and_gates():
    shirt = _FakeLayer("Shirt", 0.3)         # innermost
    vest = _FakeLayer("Vest", 2.0)           # outer
    body = _FakeLayer("BaseShape", 0.0)      # excluded: body
    soft = _FakeLayer("Cape", 1.0)           # excluded: softbody
    far = _FakeLayer("Banner", 50.0)         # excluded: far drape
    tiny = _FakeLayer("Gem", 0.5, n=4)       # excluded: micro-shape
    out = _rank([shirt, vest, body, soft, far, tiny],
                softbody_names={"Cape"})
    assert set(out) == {"Shirt", "Vest"}, out
    assert out["Shirt"] == 0.0                             # innermost: unchanged
    assert abs(out["Vest"] - LAYERED_ANTIPOKE_EPSILON) < 1e-12
    print("  test_rank_orders_layers_and_gates OK")


def test_rank_single_layer_is_empty():
    assert _rank([_FakeLayer("Only", 0.5)]) == {}          # <2 eligible -> {}
    print("  test_rank_single_layer_is_empty OK")


def test_rank_deterministic_across_weight_partners():
    # _0/_1 partners have micro-different medians; quantization + name
    # tie-break must give IDENTICAL ranks for both (no self-inflicted
    # weight-slider divergence).
    r0 = _rank([_FakeLayer("A_layer", 0.98), _FakeLayer("B_layer", 1.02)])
    r1 = _rank([_FakeLayer("A_layer", 1.03), _FakeLayer("B_layer", 0.97)])
    assert r0 == r1, (r0, r1)
    print("  test_rank_deterministic_across_weight_partners OK")
