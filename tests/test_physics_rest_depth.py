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

"""Rest-pose depth of simulated cloth, through the node tree and the skin.

The first depth numbers read stored vertices, so a chain whose ROOT NODE the
converter lifted still read at its pre-lift depth. These pin the model on small
fixtures: a translated chain root moves the cloth, a two-bone vertex moves by
its share, a skeleton bone is the actor skeleton's and not the armour's
placeholder copy, the armour root's transform is not used, the sign is
positive inside whatever the winding, the controls can fail, and the lift is
read back from the file under the node it moved."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

import numpy as np                                               # noqa: E402
import pytest                                                    # noqa: E402
from scripts.analysis import physics_cloth_health as pch         # noqa: E402
from scripts.analysis import physics_rest_depth as prd           # noqa: E402

PELVIS = "NPC Pelvis [Pelv]"


def _t(x=0.0, y=0.0, z=0.0):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


class _TB:
    """A pynifly TransformBuf: row-major rotation, translation, scale."""

    def __init__(self, m):
        self.rotation = [list(r) for r in m[:3, :3]]
        self.translation = tuple(m[:3, 3])
        self.scale = 1.0


class _Node:
    def __init__(self, name, local=None, parent=None):
        self.name = name
        self.transform = _TB(local if local is not None else np.eye(4))
        self.parent = parent

    @property
    def global_transform(self):
        m, cur = np.eye(4), self
        while cur is not None:
            m = prd._mat(cur.transform) @ m
            cur = cur.parent
        return _TB(m)


class _Shape:
    def __init__(self, name, verts, weights, stb, textures=None):
        self.name = name
        self.verts = [tuple(v) for v in verts]
        self.tris = []
        self.bone_weights = weights
        self.bone_names = list(weights)
        self._stb = stb
        self.global_to_skin = _TB(np.eye(4))
        self.textures = {"Diffuse": "cloth.dds"} if textures is None else textures

    def get_shape_skin_to_bone(self, bone):
        return _TB(self._stb[bone])


class _Nif:
    def __init__(self, nodes, shapes):
        self.rootNode = nodes[0]
        self.nodes = {n.name: n for n in nodes}
        self.shapes = shapes


# The actor skeleton has its pelvis at z 60. The armour's own copy of the
# pelvis is a placeholder at the origin, as the converter writes it.
SKEL = {pch._key(PELVIS): _t(z=60.0)}
# Bound with the chain root 5u behind the pelvis and the skirt bone 10u below.
STB = {PELVIS: _t(z=-60.0), "Skirt_01": _t(y=5.0, z=-50.0),
       "Belt_01": _t(y=5.0, z=-50.0)}
V = (0.0, -6.0, 50.0)                 # a skirt vertex, stored in world


def _rig(lift=(0.0, 0.0, 0.0), root_at=0.0, on_root=False):
    """Scene Root > pelvis placeholder > Skirt_00 > Skirt_01, and a kinematic
    Belt_01 beside it. `lift` is added to Skirt_00 as #chain-rest-lift does."""
    root = _Node("Scene Root", _t(z=root_at))
    pelvis = _Node(PELVIS, parent=root)
    if on_root:       # a chain hung straight off the armour root, by global
        s00 = _Node("Skirt_00", _t(lift[0], -5.0 + lift[1], 60.0 + lift[2]),
                    parent=root)
    else:
        s00 = _Node("Skirt_00", _t(lift[0], -5.0 + lift[1], lift[2]),
                    parent=pelvis)
    s01 = _Node("Skirt_01", _t(z=-10.0), parent=s00)
    belt = _Node("Belt_01", _t(y=-5.0, z=-10.0), parent=pelvis)
    return [root, pelvis, s00, s01, belt]


def _rest(nodes, weights, moving=("skirt_00", "skirt_01"), verts=(V,)):
    nif = _Nif(nodes, [])
    shape = _Shape("Skirt", verts, weights, STB)
    return prd.shape_rest(prd._nodes(nif), shape, SKEL, set(moving), {})


# ------------------------------------------------- the rest position (frame)

def test_a_translated_chain_root_moves_the_cloth():
    """#chain-rest-lift moves the ROOT node and leaves the skin alone: the
    stored vertex does not move, the drawn one does, by the lift."""
    r = _rest(_rig(lift=(0.0, -1.0, 0.0)), {"Skirt_01": [(0, 1.0)]})
    assert np.allclose(r["bind"][0], V)
    assert np.allclose(r["rest"][0], np.add(V, (0.0, -1.0, 0.0)))
    assert np.allclose(r["offsets"]["skirt_01"], (0.0, -1.0, 0.0))


def test_a_two_bone_vertex_moves_by_its_chain_share():
    """A vertex half on the pelvis, half on the lifted chain, moves half the
    lift. The weights are not normalised in the file on purpose."""
    r = _rest(_rig(lift=(0.0, -1.0, 0.0)),
              {PELVIS: [(0, 0.25)], "Skirt_01": [(0, 0.25)]})
    assert np.allclose(r["rest"][0], np.add(V, (0.0, -0.5, 0.0)))
    assert r["share"][0] == pytest.approx(0.5)


def test_a_skeleton_bone_is_the_actor_skeletons_not_the_armours_copy():
    """The armour's pelvis sits at the origin; in game the chain hangs from
    the SKELETON's pelvis at z 60. Reading the armour's copy would drop the
    whole skirt 60u."""
    r = _rest(_rig(), {"Skirt_01": [(0, 1.0)]})
    assert np.allclose(r["rest"][0], V)


def test_the_armour_roots_own_transform_is_not_used():
    """A chain hung off the armour root hangs off the skeleton's root in
    game; the armour root's transform (here 100u up) goes nowhere."""
    r = _rest(_rig(root_at=100.0, on_root=True), {"Skirt_01": [(0, 1.0)]})
    assert np.allclose(r["rest"][0], V)


def test_bones_below_a_dynamic_bone_move_and_a_kinematic_chain_does_not():
    """Only Skirt_00 has mass; Skirt_01 hangs below it and rides on it. The
    belt chain is declared with no mass and moves with nothing."""
    nodes = prd._nodes(_Nif(_rig(), []))
    moving = prd.moving_bones(nodes, {"skirt_00"}, SKEL)
    assert "skirt_01" in moving and "belt_01" not in moving
    assert pch._key(PELVIS) not in moving


def test_the_census_names_only_bones_with_mass():
    root, _rep = pch.parse_xml_bytes(
        b'<system><bone name="Skirt_00"><mass>1</mass></bone>'
        b'<bone name="Belt_01"><mass>0</mass></bone>'
        b'<per-vertex-shape name="Skirt"/></system>')
    info = pch.read_system(root, {"skirt": ["skirt_01", "belt_01"]})
    assert info["dynamic_bones"] == {"skirt_00"}


# ----------------------------------------------------------- depth and sign

def _sphere(r=10.0, c=(0.0, 0.0, 50.0), n_lat=24, n_lon=48, flip=False):
    verts = [(0.0, 0.0, r)]
    for i in range(1, n_lat):
        th = np.pi * i / n_lat
        for j in range(n_lon):
            ph = 2 * np.pi * j / n_lon
            verts.append((r * np.sin(th) * np.cos(ph),
                          r * np.sin(th) * np.sin(ph), r * np.cos(th)))
    verts.append((0.0, 0.0, -r))
    V_ = np.array(verts) + np.array(c)
    last = len(verts) - 1
    ring = lambda i, j: 1 + (i - 1) * n_lon + (j % n_lon)       # noqa: E731
    tris = []
    for j in range(n_lon):
        tris.append((0, ring(1, j), ring(1, j + 1)))
        tris.append((last, ring(n_lat - 1, j + 1), ring(n_lat - 1, j)))
    for i in range(1, n_lat - 1):
        for j in range(n_lon):
            a, b = ring(i, j), ring(i, j + 1)
            c_, d = ring(i + 1, j), ring(i + 1, j + 1)
            tris += [(a, c_, b), (b, c_, d)]
    T = np.array(tris)
    a, b, c3 = (V_[T[:, k]] - np.array(c) for k in range(3))
    if np.einsum("ij,ij->i", a, np.cross(b, c3)).sum() < 0:
        T = T[:, ::-1]                 # the helper itself winds outward
    if flip:
        T = T[:, ::-1]
    return V_, T


def _body(flip=False, inside=((0.0, 0.0, 50.0),), stored=None):
    v, t = _sphere(flip=flip)
    return prd.Body(v, t, stored=stored, inside=inside)


@pytest.mark.parametrize("flip", [False, True])
def test_depth_is_positive_inside_the_body_whatever_the_winding(flip):
    b = _body(flip=flip)
    d = b.depth([(0.0, 0.0, 59.0), (0.0, 0.0, 61.0), (9.0, 0.0, 50.0)])
    assert d[0] == pytest.approx(1.0, abs=0.05)
    assert d[1] == pytest.approx(-1.0, abs=0.05)
    assert d[2] == pytest.approx(1.0, abs=0.05)


def test_the_control_passes_on_a_sound_body():
    ok, err, thin = _body().control()
    assert ok and err <= prd.CONTROL_TOL and thin == 0.0


def test_the_control_fails_when_an_inside_anchor_reads_outside():
    """The anchors do not use the normals the depth is signed by, so a surface
    whose sign is wrong everywhere cannot pass them."""
    ok, _err, _thin = _body(inside=((0.0, 0.0, 70.0),)).control()
    assert not ok


def test_the_control_fails_when_the_skinned_body_is_not_the_stored_one():
    v, _t = _sphere()
    ok, _err, _thin = _body(stored=v + (0.0, 0.0, 1.0)).control()
    assert not ok


# --------------------------------------------------------- a piece, measured

def _piece(lift=(0.0, 0.0, 0.0), extra=()):
    nodes = _rig(lift=lift)
    shapes = [_Shape("Skirt", [V], {"Skirt_01": [(0, 1.0)]}, STB)]
    shapes += list(extra)
    return _Nif(nodes, shapes), {"dynamic_bones": {"skirt_00"}, "shapes": [
        {"name": "Skirt"}]}


def test_only_cloth_vertices_are_measured():
    """A vertex on the pelvis alone deep inside the body is fit, not cloth."""
    static = _Shape("Top", [(0.0, 0.0, 50.0)], {PELVIS: [(0, 1.0)]}, STB)
    nif, row = _piece(extra=[static])
    rec, reason = prd.measure(nif, row, SKEL, _body())
    assert reason is None
    assert [s["name"] for s in rec["shapes"]] == ["Skirt"]
    assert rec["rest"]["n"] == 1


def test_a_hidden_proxy_is_counted_apart_from_visible_cloth():
    proxy = _Shape("Proxy", [(0.0, 0.0, 50.0)], {"Skirt_01": [(0, 1.0)]}, STB,
                   textures={})
    nif, row = _piece(extra=[proxy])
    rec, _r = prd.measure(nif, row, SKEL, _body())
    assert rec["rest"]["n"] == 1 and rec["hidden_rest"]["n"] == 1
    assert rec["hidden_rest"]["over_1.5"] == 1


def test_the_lift_is_read_back_under_the_node_it_moved():
    nif, row = _piece(lift=(0.0, -1.0, 0.0))
    rec, _r = prd.measure(nif, row, SKEL, _body())
    assert list(rec["lifted"]) == ["skirt_00"]
    assert rec["lifted"]["skirt_00"]["lift"] == pytest.approx(1.0)
    # 4u inside at bind, 3u after the lift pulls it 1u back
    assert rec["bind"]["max"] == pytest.approx(4.0, abs=0.05)
    assert rec["rest"]["max"] == pytest.approx(3.0, abs=0.05)
    # The converter's frame check passes once the lift is taken off again.
    assert rec["frame_check"]["ok"] and rec["frame_check"]["checked"] == 1
    assert not rec["frame_refused"] and not rec["disagree"]


# ------------------------------------------- what a lift is, and what is not

def _vest(offsets, lift_top=(0.0, 0.0, 0.0), dynamic=("vest_00",)):
    """Scene Root > pelvis > Vest_00 (no skin) > Vest_01 > ... Each Vest_k is
    bound 10k u below Vest_00; its node rests `offsets[k-1]` further back
    (-y) than that, the node tree disagreeing with the skin by a growing or
    flipping amount. `lift_top` is added to Vest_00, as a lift would be."""
    root = _Node("Scene Root")
    pelvis = _Node(PELVIS, parent=root)
    top = _Node("Vest_00", _t(lift_top[0], -5.0 + lift_top[1], lift_top[2]),
                parent=pelvis)
    nodes, stb, weights, verts = [root, pelvis, top], {PELVIS: _t(z=-60.0)}, {}, []
    prev, parent = 0.0, top
    for k, d in enumerate(offsets, 1):
        n = _Node(f"Vest_{k:02d}", _t(y=-(d - prev), z=-10.0), parent=parent)
        nodes.append(n)
        stb[n.name] = _t(y=5.0, z=-(60.0 - 10.0 * k))
        weights[n.name] = [(k - 1, 1.0)]
        verts.append((0.0, -5.0, 60.0 - 10.0 * k))
        prev, parent = d, n
    shape = _Shape("Vest", verts, weights, stb)
    return _Nif(nodes, [shape]), {"dynamic_bones": set(dynamic),
                                  "shapes": [{"name": "Vest"}]}


def test_an_offset_growing_along_a_chain_is_no_lift():
    """0.8, 1.4, 2.0, 2.4 down one chain is the node tree disagreeing with
    the skin more at every link: a lift moves a whole subtree by one vector.
    The converter's own frame check refuses the file, so it is listed apart."""
    rec, _r = prd.measure(*_vest((0.8, 1.4, 2.0, 2.4)), SKEL, _body())
    assert rec["lifted"] == {}
    assert sorted(rec["disagree"]) == ["vest_01", "vest_02", "vest_03",
                                       "vest_04"]
    assert rec["frame_check"]["worst"] == pytest.approx(2.4)
    assert rec["frame_refused"] and rec["frame_disagrees"]
    assert not rec["far_from_bind"]          # 2.4u: under FRAME_MAX_U alone


def test_a_small_growing_offset_is_disagreement_not_a_lift():
    """The same shape inside the converter's tolerance: the file is ranked,
    and still no link of it is a lift -- not even the last, which alone has
    nothing below it."""
    rec, _r = prd.measure(*_vest((0.1, 0.25, 0.4)), SKEL, _body())
    assert not rec["frame_disagrees"]
    assert rec["lifted"] == {}
    assert sorted(rec["disagree"]) == ["vest_01", "vest_02", "vest_03"]


@pytest.mark.parametrize("amp", [1.0, 0.15])
def test_an_offset_flipping_sign_along_a_chain_is_no_lift(amp):
    rec, _r = prd.measure(*_vest((2.5 * amp, 0.0, -2.0 * amp)), SKEL,
                          _body())
    assert rec["lifted"] == {}
    assert sorted(rec["disagree"]) == ["vest_01", "vest_03"]
    assert rec["frame_refused"] is (amp == 1.0)


def test_a_lift_on_a_kinematic_chain_is_read_back():
    """#chain-rest-lift moves a chain root whether or not the XML simulates
    the chain: here only the belt has mass, and the lifted skirt is kinematic."""
    belt = _Shape("Belt", [(0.0, -5.0, 50.0)], {"Belt_01": [(0, 1.0)]}, STB)
    nif, _row = _piece(lift=(0.0, -0.9, 0.0), extra=[belt])
    row = {"dynamic_bones": {"belt_01"}, "shapes": [{"name": "Belt"}]}
    rec, reason = prd.measure(nif, row, SKEL, _body())
    assert reason is None
    assert [s["name"] for s in rec["shapes"]] == ["Belt"]
    assert list(rec["lifted"]) == ["skirt_00"]
    assert rec["lifted"]["skirt_00"]["lift"] == pytest.approx(0.9)
    assert not rec["frame_refused"]


def test_a_lift_is_one_translation_of_the_whole_chain_at_its_root():
    """The whole Vest chain moved by one vector from its root: one lift,
    named at the node the converter lifts, and nothing left disagreeing."""
    rec, _r = prd.measure(*_vest((0.0, 0.0, 0.0), lift_top=(0.0, -1.5, 0.0)),
                          SKEL, _body())
    assert list(rec["lifted"]) == ["vest_00"]
    assert rec["lifted"]["vest_00"]["bones"] == 3
    assert rec["lifted"]["vest_00"]["lift"] == pytest.approx(1.5)
    assert rec["disagree"] == {} and not rec["frame_refused"]


def test_the_frame_check_is_the_converters_tolerance():
    """One link 0.4u off its skin passes the converter's check (0.5u) and is
    ranked, its offset counted as disagreement; 0.6u is refused."""
    near, _r = prd.measure(*_vest((0.0, 0.4, 0.4)), SKEL, _body())
    far, _r = prd.measure(*_vest((0.0, 0.6, 0.6)), SKEL, _body())
    assert near["frame_check"]["ok"] and not near["frame_disagrees"]
    assert list(near["disagree"]) == ["vest_02"] and near["lifted"] == {}
    assert far["frame_refused"] and far["frame_disagrees"]


def test_a_garment_bone_the_skeleton_carries_is_still_checked():
    """A skirt bone that the actor's skeleton happens to carry is placed by
    the skeleton in game, and the converter's check covers it (its name is no
    skeleton bone's): 3u off its skin, the file is refused. A hard skeleton
    bone is not a chain bone and is left to the far-from-bind rule."""
    for name, refused in (("SkirtBone01", True), ("NPC Cape", False)):
        skel = dict(SKEL, **{pch._key(name): _t(y=-8.0, z=50.0)})
        shape = _Shape("Cape", [(0.0, -5.0, 50.0)], {name: [(0, 1.0)]},
                       {name: _t(y=5.0, z=-50.0)})
        row = {"dynamic_bones": {pch._key(name)}, "shapes": [{"name": "Cape"}]}
        rec, _r = prd.measure(_Nif(_rig(), [shape]), row, skel, _body())
        assert rec["frame_refused"] is refused, name
        assert rec["frame_check"]["checked"] == int(refused)
        assert rec["far_from_bind"] and rec["frame_disagrees"]


def test_a_file_the_converter_refuses_carries_no_lift():
    """The converter refuses the whole file or lifts nothing on it: a chain
    that looks lifted on a file whose other chain disagrees past the
    tolerance is no lift."""
    nif, row = _vest((0.0, 0.0, 0.0), lift_top=(0.0, -1.0, 0.0))
    # A second chain: Tail_00 on its skin, Tail_01 resting 1u off its own.
    t00 = _Node("Tail_00", _t(y=-5.0), parent=nif.nodes[PELVIS])
    t01 = _Node("Tail_01", _t(y=-1.0, z=-10.0), parent=t00)
    nif.nodes.update({n.name: n for n in (t00, t01)})
    nif.shapes.append(_Shape(
        "Tail", [(0.0, -5.0, 60.0), (0.0, -5.0, 50.0)],
        {"Tail_00": [(0, 1.0)], "Tail_01": [(1, 1.0)]},
        {"Tail_00": _t(y=5.0, z=-60.0), "Tail_01": _t(y=5.0, z=-50.0)}))
    rec, _r = prd.measure(nif, row, SKEL, _body())
    assert rec["frame_refused"]
    assert rec["lifted"] == {}
    assert {"vest_00", "tail_01"} <= set(rec["disagree"])


def test_the_files_own_global_to_skin_is_not_the_frame_checked():
    """The written file's skeleton nodes are flat, so its global-to-skin is
    not the frame the converter checked in: the check reads the skin in the
    bind frame the depth uses (here from the pelvis), and a sound lifted
    piece passes it."""
    nodes = _rig(lift=(0.0, -1.0, 0.0))
    shape = _Shape("Skirt", [V, (0.0, 0.0, 50.0)],
                   {"Skirt_01": [(0, 1.0)], PELVIS: [(1, 1.0)]}, STB)
    shape.global_to_skin = _TB(_t(z=-60.0))
    row = {"dynamic_bones": {"skirt_00"}, "shapes": [{"name": "Skirt"}]}
    rec, _r = prd.measure(_Nif(nodes, [shape]), row, SKEL, _body())
    assert not rec["frame_refused"] and list(rec["lifted"]) == ["skirt_00"]


def test_a_body_named_helper_is_in_the_frame_check_not_the_depth():
    """The converter's frame check reads every shape of the file. A source's
    own body helper keeps its name (an injected-body name) in the written
    file; its genital bone skinned 4u off where the skeleton puts it refuses
    the file, as the converter refused it. It is never measured as cloth."""
    gen = "NPC Genitals01 [Gen01]"
    skel = dict(SKEL, **{pch._key(gen): _t(y=4.0, z=55.0)})

    def helper(gen_y):
        # Framed by the pelvis, which carries most of its weight.
        return _Shape("VirtualBody", [(0.0, 0.0, 55.0), (0.0, 0.0, 60.0)],
                      {PELVIS: [(0, 1.0), (1, 1.0)], gen: [(0, 0.5)]},
                      {PELVIS: _t(z=-60.0), gen: _t(y=gen_y, z=-55.0)})
    nif, row = _piece(lift=(0.0, -1.0, 0.0), extra=[helper(0.0)])
    rec, _r = prd.measure(nif, row, skel, _body())
    assert rec["frame_refused"] and rec["frame_disagrees"]
    assert rec["frame_check"]["worst"] == pytest.approx(4.0)
    assert [s["name"] for s in rec["shapes"]] == ["Skirt"]
    nif, row = _piece(lift=(0.0, -1.0, 0.0), extra=[helper(-4.0)])
    rec, _r = prd.measure(nif, row, skel, _body())
    assert not rec["frame_refused"] and list(rec["lifted"]) == ["skirt_00"]


def test_a_piece_moved_by_bones_alone_is_measured_and_marked():
    nif, row = _piece()
    rec, _r = prd.measure(nif, dict(row, cloth=[], moved=["skirt"]), SKEL,
                          _body())
    assert rec["bone_driven"] and rec["rest"]["n"] == 1
    plain, _r = prd.measure(nif, dict(row, cloth=[{"name": "Skirt"}]), SKEL,
                            _body())
    assert not plain["bone_driven"]


def test_two_sub_chains_lifted_differently_are_two_lifts():
    """One top node carrying two sub-chains the lift moved by different
    vectors: each is named at its own root, not merged under the top."""
    root = _Node("Scene Root")
    pelvis = _Node(PELVIS, parent=root)
    top = _Node("Sk_root", parent=pelvis)
    a0 = _Node("Sk_A_00", _t(y=-5.0, z=-0.5), parent=top)
    a1 = _Node("Sk_A_01", _t(z=-10.0), parent=a0)
    b0 = _Node("Sk_B_00", _t(y=-5.0, z=0.7), parent=top)
    b1 = _Node("Sk_B_01", _t(z=-10.0), parent=b0)
    nodes = prd._nodes(_Nif([root, pelvis, top, a0, a1, b0, b1], []))
    groups = prd.chain_groups(nodes, {"sk_a_01": np.array([0.0, 0.0, -0.5]),
                                      "sk_b_01": np.array([0.0, 0.0, 0.7])},
                              SKEL)
    assert sorted(groups) == ["sk_a_00", "sk_b_00"]
    assert groups["sk_b_00"]["lift"] == pytest.approx(0.7)


def _fan(offs):
    """Scene Root > pelvis > Fan_00 (no skin) > four skinned SIBLINGS, each
    resting offs[k] further back (-y) than its skin binds it."""
    root = _Node("Scene Root")
    pelvis = _Node(PELVIS, parent=root)
    fan = _Node("Fan_00", _t(y=-5.0), parent=pelvis)
    nodes, stb, weights, verts = [root, pelvis, fan], {}, {}, []
    for k, d in enumerate(offs):
        x = 2.0 * k
        n = _Node(f"Fan_0{k + 1}", _t(x=x, y=-d, z=-10.0), parent=fan)
        nodes.append(n)
        stb[n.name] = _t(x=-x, y=5.0, z=-50.0)
        weights[n.name] = [(k, 1.0)]
        verts.append((x, -5.0, 50.0))
    shape = _Shape("Fan", verts, weights, stb)
    return _Nif(nodes, [shape]), {"dynamic_bones": {"fan_00"},
                                  "shapes": [{"name": "Fan"}]}


@pytest.mark.parametrize("offs, n_disagree", [
    ((0.3, 0.0, 0.3, 0.0), 1), ((0.0, 0.3, 0.0, 0.3), 1),
    ((0.3, -0.3, 0.3, -0.3), 2)])
def test_groups_under_one_node_are_all_kept(offs, n_disagree):
    """Siblings that moved differently form several groups rooted at the
    same node. Keyed by root alone, the later one replaced the earlier and
    a disagreement vanished (or two became one); every group is kept, and
    every skinned bone is in one."""
    rec, _r = prd.measure(*_fan(offs), SKEL, _body())
    assert sum(g["bones"] for g in rec["chains"].values()) == 4
    assert {g["root"] for g in rec["chains"].values()} == {"fan_00"}
    assert len(rec["disagree"]) == n_disagree
    assert rec["lifted"] == {} and not rec["frame_refused"]


def test_cloth_moved_further_than_any_lift_is_a_frame_disagreement():
    """A 2u root translation is a capped lift. 3u is past the cap: no lift,
    and the converter's check refuses it. A skeleton-named cloth bone the
    actor's skeleton places 3u off the skin is out of the check's reach
    (the game takes that node from the skeleton), and is listed apart as
    cloth resting further from its bind than any lift moves it."""
    ok, _r = prd.measure(*_piece(lift=(0.0, -2.0, 0.0)), SKEL, _body())
    past, _r = prd.measure(*_piece(lift=(0.0, -3.0, 0.0)), SKEL, _body())
    assert not ok["frame_disagrees"] and list(ok["lifted"]) == ["skirt_00"]
    assert past["frame_disagrees"] and past["lifted"] == {}
    assert past["frame_refused"] and list(past["disagree"]) == ["skirt_00"]
    cape = "NPC Cape"
    skel = dict(SKEL, **{pch._key(cape): _t(y=-8.0, z=50.0)})
    shape = _Shape("Cape", [(0.0, -5.0, 50.0)], {cape: [(0, 1.0)]},
                   {cape: _t(y=5.0, z=-50.0)})
    row = {"dynamic_bones": {pch._key(cape)}, "shapes": [{"name": "Cape"}]}
    far, _r = prd.measure(_Nif(_rig(), [shape]), row, skel, _body())
    assert far["max_move"] == pytest.approx(3.0)
    assert far["frame_check"]["checked"] == 0 and not far["frame_refused"]
    assert far["far_from_bind"] and far["frame_disagrees"]


def test_each_weight_is_measured_against_its_own_body(tmp_path, monkeypatch):
    """The weight-0 piece against the weight-0 body: here only that body
    reaches the vertex."""
    paths = []
    for w in ("_0", "_1"):
        p = tmp_path / f"skirt{w}.nif"
        p.write_bytes(b"\x00" + pch._MARKER + b"\x00")
        paths.append(p)
    nif, row = _piece()
    monkeypatch.setattr(pch, "classify", lambda p, n: (dict(row), None))
    far_v, far_t = _sphere(c=(0.0, 0.0, 90.0))
    bodies = {"_0": _body(), "_1": prd.Body(far_v, far_t)}
    rows, skip = prd.scan(paths, tmp_path, SKEL, bodies,
                          open_nif=lambda p: nif)
    got = {r["weight"]: r["rest"]["over_0.5"] for r in rows}
    assert got == {"_0": 1, "_1": 0} and not skip
    assert prd.weight_of("a/b/skirt_0.nif") == "_0"


def test_the_skeleton_is_the_load_order_winner(tmp_path, monkeypatch):
    """Two mods ship the skeleton; the game loads the higher-priority one,
    which here is NOT the first alphabetically."""
    from src import paths as _p
    rel = prd.SKELETON_PATTERNS[0]
    for mod in ("Alpha Skeleton", "Zeta Skeleton"):
        f = tmp_path / "mods" / mod / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(b"nif")

    class _Lay:
        game_data_dirs = []
    monkeypatch.delenv("CBBE2UBE_SKELETON_NIF", raising=False)
    monkeypatch.setattr(_p, "discover_layout", lambda: _Lay())
    monkeypatch.setattr(_p, "enabled_mods_ordered",
                        lambda lay: ["Zeta Skeleton", "Alpha Skeleton"])
    monkeypatch.setattr(_p, "mods_root", lambda: tmp_path / "mods")
    monkeypatch.setattr(_p, "overwrite_dir", lambda lay: None)
    assert prd.find_skeleton().parent.parent.parent.parent.parent.name == (
        "Zeta Skeleton")


# ------------------------------------------------------------ report + log

def test_a_failed_control_exits_3(capsys):
    rec, _r = prd.measure(*_piece(), SKEL, _body())
    rec.update(path="a/skirt_1.nif", garment="a/skirt", weight="_1")
    rc = prd.report([rec], Counter(), 1, {"_1": _body()},
                    {"_1": (False, 0.0, 0.0, 0.0, False)})
    assert rc == 3
    assert "CONTROL FAILED" in capsys.readouterr().out


def test_nothing_measured_exits_3():
    with pytest.raises(SystemExit) as ex:
        prd.report([], Counter({pch.NO_POINTER: 3}), 3, {"_1": _body()},
                   {"_1": (True, 0.0, 0.0, 0.0, False)})
    assert ex.value.code == 3


def test_the_lift_log_is_read_by_the_sinks_path(tmp_path):
    """The sink keeps the written file's last four path parts, so a shallow
    piece's starts with `meshes`; a torn line and a renamed temp are
    dropped."""
    log = tmp_path / "standoff_audit.jsonl"
    recs = [
        {"pass_": "chain-rest-lift", "moved": True, "root": "Skirt 1_00",
         "lift": 0.9, "path": "meshes/!UBE/set/cuirass_1.nif"},
        {"pass_": "chain-rest-lift", "skipped": "every bone already clears "
         "the body", "root": "Skirt 2_00",
         "path": "meshes/!UBE/set/cuirass_1.nif"},
        {"pass_": "chain-rest-lift", "moved": True, "root": "X",
         "lift": 2.0, "path": "meshes/!UBE/set/cuirass_1.nif.reauth"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in recs) + "\n{torn",
                   encoding="utf-8")
    got = prd.read_lift_log(log)
    ent = got[prd.log_key("!UBE/set/cuirass_1.nif")]
    assert dict(ent["moved"]) == {"Skirt 1_00": {0.9}}
    assert sum(ent["skipped"].values()) == 1
    assert len(got) == 1


def test_a_refused_piece_is_listed_apart_not_ranked(capsys):
    good, _r = prd.measure(*_piece(lift=(0.0, -1.0, 0.0)), SKEL, _body())
    bad, _r = prd.measure(*_vest((0.8, 1.4, 2.0, 2.4)), SKEL, _body())
    good.update(path="a/skirt_1.nif", garment="a/skirt", weight="_1")
    bad.update(path="b/vest_1.nif", garment="b/vest", weight="_1")
    rc = prd.report([good, bad], Counter(), 2, {"_1": _body()},
                    {"_1": (True, 0.0, 0.0, 0.0, False)})
    out = capsys.readouterr().out
    assert rc == 0
    ranked, apart = out.split("\nFRAME DISAGREEMENT (not ranked")
    assert "a/skirt_1.nif" in ranked and "b/vest_1.nif" not in ranked
    assert "refused" in apart and "b/vest_1.nif" in apart


# ------------------------------------------------------------ inputs + json

def _main_inputs(tmp_path):
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    return meshes


def test_an_unreadable_skeleton_or_body_exits_2(tmp_path, capsys):
    meshes = _main_inputs(tmp_path)
    junk = tmp_path / "junk_1.nif"
    junk.write_bytes(b"garbage")
    assert prd.main([str(meshes), "--body", str(junk),
                     "--skeleton", str(junk)]) == 2
    assert "cannot use the skeleton or body" in capsys.readouterr().out


def test_a_body_the_skeleton_cannot_place_exits_2(tmp_path, monkeypatch,
                                                  capsys):
    """A real NIF whose body shape has no skin the skeleton resolves: the
    loader raises, and the tool says so in one line with exit 2."""
    from tests.synthetic_nif import build_shape_nif, pynifly_available
    if not pynifly_available():
        pytest.skip("pynifly not available")
    meshes = _main_inputs(tmp_path)
    body = build_shape_nif(tmp_path / "body_1.nif")
    monkeypatch.setattr(prd, "load_skeleton", lambda p: dict(SKEL))
    assert prd.main([str(meshes), "--body", str(body),
                     "--skeleton", str(body)]) == 2
    out = capsys.readouterr().out
    assert "ValueError" in out and "Traceback" not in out


def _fallback_skeleton(tmp_path, monkeypatch):
    """A load-order skeleton the lookup COULD fall back to."""
    from src import paths as _p
    f = tmp_path / "mods" / "Skel" / prd.SKELETON_PATTERNS[0]
    f.parent.mkdir(parents=True)
    f.write_bytes(b"nif")

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(_p, "discover_layout", lambda: _Lay())
    monkeypatch.setattr(_p, "enabled_mods_ordered", lambda lay: ["Skel"])
    monkeypatch.setattr(_p, "mods_root", lambda: tmp_path / "mods")
    monkeypatch.setattr(_p, "overwrite_dir", lambda lay: None)


@pytest.mark.parametrize("by_env", [False, True])
def test_a_named_skeleton_that_is_no_file_exits_2(tmp_path, monkeypatch,
                                                  capsys, by_env):
    """A mistyped --skeleton (or CBBE2UBE_SKELETON_NIF) is never replaced by
    the load-order skeleton: exit 2, one line, nothing measured."""
    meshes = _main_inputs(tmp_path)
    _fallback_skeleton(tmp_path, monkeypatch)
    body = tmp_path / "body_1.nif"
    body.write_bytes(b"nif")
    missing = str(tmp_path / "nowhere" / "skeleton_female.nif")
    argv = [str(meshes), "--body", str(body)]
    if by_env:
        monkeypatch.setenv("CBBE2UBE_SKELETON_NIF", missing)
    else:
        monkeypatch.delenv("CBBE2UBE_SKELETON_NIF", raising=False)
        argv += ["--skeleton", missing]
    assert prd.main(argv) == 2
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1 and "names no file" in lines[0]
    assert not lines[0].startswith("skeleton:")      # nothing fell back


def test_a_body_with_no_triangles_exits_2(tmp_path, monkeypatch, capsys):
    """Vertices but no surface: the controls would index an empty triangle
    list. It is refused on load, in one line, never with a traceback."""
    meshes = _main_inputs(tmp_path)
    body_p = tmp_path / "body_1.nif"
    body_p.write_bytes(b"nif")
    v, _t = _sphere()
    monkeypatch.setattr(prd, "load_skeleton", lambda p: dict(SKEL))
    monkeypatch.setattr(prd.Body, "load",
                        classmethod(lambda cls, p, s: cls(v, [])))
    assert prd.main([str(meshes), "--body", str(body_p), "--skeleton",
                     str(body_p)]) == 2
    out = capsys.readouterr().out
    assert "no triangles" in out and len(out.strip().splitlines()) == 2


def _stale(tmp_path):
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"status": "ok", "rows": [{"path": "old.nif"}]}),
                   encoding="utf-8")
    return out


@pytest.mark.parametrize("case", ["meshes", "skeleton", "body", "usage"])
def test_every_exit_2_records_its_reason_over_a_stale_json(
        tmp_path, monkeypatch, case):
    """An earlier run's rows at the --json path never survive a run that
    refused its inputs: the file says "input error", why, and no rows."""
    meshes = _main_inputs(tmp_path)
    out = _stale(tmp_path)
    junk = tmp_path / "junk_1.nif"
    junk.write_bytes(b"garbage")
    monkeypatch.delenv("CBBE2UBE_SKELETON_NIF", raising=False)
    argv = {"meshes": [str(tmp_path / "nope")],
            "skeleton": [str(meshes), "--body", str(junk), "--skeleton",
                         str(tmp_path / "missing.nif")],
            "body": [str(meshes), "--body", str(junk), "--skeleton",
                     str(junk)],
            "usage": [str(meshes), "--top", "many"]}[case]
    if case == "usage":
        with pytest.raises(SystemExit) as ex:
            prd.main(argv + ["--json", str(out)])
        assert ex.value.code == 2
    else:
        assert prd.main(argv + ["--json", str(out)]) == 2
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["status"] == prd.STATUS_INPUT and got["reason"]
    assert got["rows"] == []


@pytest.mark.parametrize("usage", [False, True])
def test_a_json_path_that_cannot_be_written_exits_2_with_one_line(
        tmp_path, capsys, usage):
    """A --json in a folder that does not exist is an input error like any
    other: one line and exit 2 (argparse's own exit 2 for a usage error),
    never a traceback."""
    meshes = _main_inputs(tmp_path)
    out = tmp_path / "no_such_dir" / "out.json"
    if usage:
        with pytest.raises(SystemExit) as ex:
            prd.main([str(meshes), "--top", "many", "--json", str(out)])
        assert ex.value.code == 2
    else:
        assert prd.main([str(tmp_path / "nope"), "--json", str(out)]) == 2
        assert "cannot write --json" in capsys.readouterr().out
    assert not out.exists()


@pytest.mark.parametrize("where", ["scan", "report"])
def test_a_run_that_crashes_leaves_incomplete_not_an_old_result(
        tmp_path, monkeypatch, where):
    meshes = _main_inputs(tmp_path)
    out = _stale(tmp_path)
    body_p = tmp_path / "body_1.nif"
    body_p.write_bytes(b"nif")
    monkeypatch.setattr(prd, "load_skeleton", lambda p: dict(SKEL))
    monkeypatch.setattr(prd.Body, "load",
                        classmethod(lambda cls, p, s: _body()))

    def _boom(*a, **k):
        raise RuntimeError("crash")
    monkeypatch.setattr(prd, where, _boom)
    if where == "report":
        monkeypatch.setattr(prd, "scan", lambda *a, **k: ([], Counter()))
    with pytest.raises(RuntimeError):
        prd.main([str(meshes), "--body", str(body_p), "--skeleton",
                  str(body_p), "--json", str(out)])
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["status"] == prd.STATUS_INCOMPLETE and got["rows"] == []


@pytest.mark.parametrize("sound", [True, False])
def test_the_json_carries_depths_only_when_the_controls_pass(
        tmp_path, monkeypatch, sound):
    meshes = _main_inputs(tmp_path)
    body_p = tmp_path / "body_1.nif"
    body_p.write_bytes(b"nif")
    rec, _r = prd.measure(*_piece(), SKEL, _body())
    rec.update(path="a/skirt_1.nif", garment="a/skirt", weight="_1")
    body = _body() if sound else _body(inside=((0.0, 0.0, 70.0),))
    monkeypatch.setattr(prd, "load_skeleton", lambda p: dict(SKEL))
    monkeypatch.setattr(prd.Body, "load", classmethod(lambda cls, p, s: body))
    monkeypatch.setattr(prd, "scan", lambda *a, **k: ([rec], Counter()))
    out = tmp_path / "out.json"
    rc = prd.main([str(meshes), "--body", str(body_p), "--skeleton",
                   str(body_p), "--json", str(out)])
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["controls"]["_1"]["ok"] is sound
    if sound:
        assert rc == 0 and got["status"] == "ok" and len(got["rows"]) == 1
    else:
        assert rc == 3 and got["status"] == "controls FAILED"
        assert got["rows"] == []
