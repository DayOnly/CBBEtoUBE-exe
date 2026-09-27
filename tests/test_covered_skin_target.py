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

"""#covered-skin-target -- the rigid leg-plate match aims each vertex at the skin
it COVERS, not at the six body vertices nearest to it.

THE DEFECT THIS PINS (measured 2026-09-17). A leather greave's rear crotch gusset
sits 0.65u from the inner-thigh skin and also passes over the buttock cleft 4u
further on. The cleft is Pelvis 1.0 and never moves with a leg; the six nearest
body vertices are thigh skin, so the old target gave the gusset 0.444 on one
thigh and a 50-degree swing of that leg carried it 2.5u into the lower buttock
and the top of the inner thigh (869 body vertices lost more than 0.5u of
clearance; the other leg moved nothing). The author's own row was 0.749 on that
thigh: the split was inherited and only half corrected, because the target it
was blended toward was the wrong skin.

The target is now the clearance-weighted mean of the covered skin: the skin the
panel would touch first has the most say. These tests pin the pure helpers, the
reach of the cover map, and the switch. The wiring into the pass is proven by
the exe A/B on the reported mod (it needs the live UBE body), not here.
"""
import importlib

import numpy as np

from src import gui_settings as gs
from src import nif_convert as nc
from src import nif_convert_weights as ncw

LT, RT, PEL = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]"
SWITCH = "CBBE2UBE_NO_COVERED_SKIN_TARGET"


def _rows(n, row):
    return [dict(row) for _ in range(n)]


def test_the_switch_defaults_on_and_the_env_turns_it_off(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    importlib.reload(nc)
    assert nc.COVERED_SKIN_TARGET is True
    monkeypatch.setenv(SWITCH, "1")
    importlib.reload(nc)
    assert nc.COVERED_SKIN_TARGET is False
    monkeypatch.delenv(SWITCH, raising=False)
    importlib.reload(nc)
    assert nc.COVERED_SKIN_TARGET is True


def test_a_gusset_over_static_cleft_skin_targets_the_pelvis():
    # eight cleft vertices the panel grazes (clearance 0.05u), four thigh-top
    # vertices it clears by 2u: the skin it would touch first is the pelvis.
    body_w = _rows(8, {PEL: 1.0}) + _rows(4, {RT: 0.4, PEL: 0.6})
    clearance = np.array([0.05] * 8 + [2.0] * 4)
    t = ncw._covered_skin_target(list(range(12)), clearance, body_w, eps=0.5)
    assert t[PEL] > 0.9
    assert t.get(RT, 0.0) < 0.1
    assert abs(sum(t.values()) - 1.0) < 1e-9


def test_a_vertex_over_thigh_skin_keeps_the_thigh():
    # ten thigh vertices at 0.5u and two cleft vertices 3u away: a uniform
    # average would read 0.75 on the thigh; the covered-skin target stays above 0.8.
    body_w = _rows(10, {RT: 0.9, PEL: 0.1}) + _rows(2, {PEL: 1.0})
    clearance = np.array([0.5] * 10 + [3.0] * 2)
    t = ncw._covered_skin_target(list(range(12)), clearance, body_w, eps=0.5)
    assert t[RT] > 0.8


def test_skin_the_vertex_already_sits_inside_counts_as_touching():
    body_w = _rows(1, {PEL: 1.0}) + _rows(1, {RT: 1.0})
    a = ncw._covered_skin_target([0, 1], np.array([-0.5, 0.0]), body_w, eps=0.5)
    b = ncw._covered_skin_target([0, 1], np.array([0.0, 0.0]), body_w, eps=0.5)
    assert abs(a[PEL] - b[PEL]) < 1e-9
    assert abs(a[PEL] - 0.5) < 1e-9


def test_an_empty_cover_has_no_target():
    assert ncw._covered_skin_target([], np.zeros(0), [], eps=0.5) is None


def test_the_map_pairs_each_body_vertex_with_its_nearest_garment_vertex_within_reach():
    Vg = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    Vb = np.array([[0.0, -1.0, 0.0], [10.0, -0.5, 0.0], [50.0, 0.0, 0.0], [1.0, -1.0, 0.0]])
    Nb = np.array([[0.0, 1.0, 0.0]] * 4)
    band = np.array([True, True, True, False])
    cover, clr = ncw._covered_skin_map(Vg, Vb, Nb, band, reach=6.0)
    assert cover == {0: [0], 1: [1]}          # vertex 2 is 40u away; vertex 3 is outside the band
    assert abs(clr[0] - 1.0) < 1e-9
    assert abs(clr[1] - 0.5) < 1e-9
    assert np.isnan(clr[2]) and np.isnan(clr[3])


def test_the_butt_match_given_the_covered_target_removes_one_sided_thigh_weight():
    dv = {LT: 0.14, PEL: 0.112, RT: 0.749}      # the gusset row the author shipped
    mass = sum(dv.values())
    ncw._butt_match_vert(dv, {PEL: 1.0}, strength=1.0, rebalance=True)
    assert dv[PEL] > 0.95 * mass
    assert dv.get(RT, 0.0) < 0.02
    assert dv.get(LT, 0.0) < 0.02
    assert abs(sum(dv.values()) - mass) < 1e-6


def test_the_settings_window_can_turn_it_off():
    s = next(x for x in gs.SETTINGS if x.key == "covered_skin_target")
    assert s.env == SWITCH
    assert s.invert is True
    assert s.default is True


class _FakeShape:
    def __init__(self, name, verts, weights):
        self.name = name
        self.verts = [tuple(v) for v in verts]
        self.bone_weights = weights
        self.written = {}
        self._weights = weights

    def setShapeWeights(self, bone, pairs):
        self.written[bone] = list(pairs)


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def test_the_conform_core_aims_at_the_covered_skin_when_it_has_one(monkeypatch):
    """The fitted-cloth conform is the pass that produced the shipped gusset row
    (0.1 x the author's row + 0.9 x the ONE nearest body vertex reproduces it to
    0.02). A flat patch of skin at y=0: a pelvis-only cleft column with a thigh
    column on each side, and a pelvis-only hip strip further out that carries the
    butt jiggle the conform gates on. One gusset vertex sits 0.58u from a right
    thigh vertex and covers all 24 cluster vertices at 0.5u."""
    from scipy.spatial import cKDTree
    body, bw = [], []
    for z in (59.5, 60.0, 60.5, 61.0):
        for x, row in ((-2.5, {LT: 0.4, PEL: 0.6}), (-1.5, {LT: 0.4, PEL: 0.6}),
                       (-0.5, {PEL: 1.0}), (0.5, {PEL: 1.0}),
                       (1.5, {RT: 0.4, PEL: 0.6}), (2.5, {RT: 0.4, PEL: 0.6})):
            body.append((x, 0.0, z))
            bw.append(dict(row))
        for x in (5.0, 6.0, 7.0, 8.0):
            body.append((x, 0.0, z))
            bw.append({PEL: 1.0})
    Vb = np.asarray(body, float)
    Nb = np.tile(np.array([0.0, 1.0, 0.0]), (len(Vb), 1))
    gv = [(1.2, 0.5, 60.0)]
    for z in (59.5, 60.5):
        for x in (5.0, 6.0, 7.0, 8.0):
            gv.append((x, 0.5, z))
    weights = {RT: [(0, 0.749)], LT: [(0, 0.14)],
               PEL: [(0, 0.112)] + [(i, 0.8) for i in range(1, 9)],
               "NPC L Butt": [(i, 0.2) for i in range(1, 9)]}
    shape = _FakeShape("Greaves", gv, weights)
    nif = _FakeNif([shape])
    ref = (Vb, bw, {LT, RT, PEL, "NPC L Butt"}, cKDTree(Vb))
    monkeypatch.setattr(nc, "_body_conform_ref", lambda weight: ref)
    monkeypatch.setattr(nc, "_body_conform_normals", lambda weight: Nb)
    # the collider, soft-body, physics-XML and skin-frame readers all answer
    # empty / False / None for a fake NIF (checked), so nothing else is patched
    monkeypatch.setattr(nc, "COVERED_SKIN_TARGET", True)
    dirty, n = ncw._conform_weights_core(nif, "fake_1.nif", "_1")
    assert dirty and n >= 1
    r = dict(shape.written.get(RT, []))
    # aimed at the ONE nearest body vertex (right thigh 0.4 / pelvis 0.6) the 0.9
    # blend leaves 0.435 on the right thigh; aimed at the covered skin (8 cleft, 8
    # right-thigh and 8 left-thigh vertices, all 0.5u away) it leaves 0.20.
    assert r.get(0, 0.0) < 0.25


class _FakeSkinShape(_FakeShape):
    """A shape the reskin can read: verts, stored normals, bone names, no transforms."""

    def __init__(self, name, verts, weights, normals=None):
        super().__init__(name, verts, weights)
        self.normals = normals
        self.bone_names = list(weights)

    def get_shape_skin_to_bone(self, bone):
        return None


def _flat_patch():
    body, bw = [], []
    for z in (59.5, 60.0, 60.5, 61.0):
        for x, row in ((-2.5, {LT: 0.4, PEL: 0.6}), (-1.5, {LT: 0.4, PEL: 0.6}),
                       (-0.5, {PEL: 1.0}), (0.5, {PEL: 1.0}),
                       (1.5, {RT: 0.4, PEL: 0.6}), (2.5, {RT: 0.4, PEL: 0.6})):
            body.append((x, 0.0, z))
            bw.append(dict(row))
        for x in (5.0, 6.0, 7.0, 8.0):
            body.append((x, 0.0, z))
            bw.append({PEL: 1.0})
    weights = {}
    for i, row in enumerate(bw):
        for b, w in row.items():
            weights.setdefault(b, []).append((i, w))
    normals = [(0.0, 1.0, 0.0)] * len(body)
    return _FakeSkinShape("BaseShape", body, weights, normals)


def _reskin_gusset_share(flag_on, monkeypatch):
    monkeypatch.setattr(nc, "COVERED_SKIN_TARGET", flag_on)
    body = _flat_patch()
    gv = [(1.2, 0.5, 60.0)]
    for z in (59.5, 60.5):
        for x in (5.0, 6.0, 7.0, 8.0):
            gv.append((x, 0.5, z))
    src = _FakeSkinShape("Greaves", gv, {RT: [(0, 0.749)], LT: [(0, 0.14)],
                                         PEL: [(0, 0.112)] + [(i, 1.0) for i in range(1, 9)]})
    _bones, _xf, weights = ncw.compute_body_blend_skinning(
        np.asarray(gv, float), src, body, near_dist=1.2, far_dist=3.5, k=4)
    return dict(weights.get(RT, [])).get(0, 0.0)


def test_the_reskin_aims_a_covering_vertex_at_the_skin_it_covers(monkeypatch):
    # the four body vertices nearest to the gusset are three right-thigh
    # vertices and one cleft vertex (0.315 on the right thigh by inverse
    # distance); the skin it COVERS is the whole 24-vertex cluster, 0.133.
    assert _reskin_gusset_share(True, monkeypatch) < 0.2


def test_the_switch_off_restores_the_nearest_skin_in_the_reskin(monkeypatch):
    assert _reskin_gusset_share(False, monkeypatch) > 0.25
