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


def test_cloth_moved_further_than_any_lift_is_a_frame_disagreement():
    ok, _r = prd.measure(*_piece(lift=(0.0, -2.0, 0.0)), SKEL, _body())
    far, _r = prd.measure(*_piece(lift=(0.0, -3.0, 0.0)), SKEL, _body())
    assert not ok["frame_disagrees"]
    assert far["frame_disagrees"]


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
