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


"""#canonical-skeleton-nodes -- the node set of an output file is a function of the source (#33).

nifly writes every NiNode in front of the shapes, so one more node in one weight
file moves every mesh behind it. Which skeleton bones a file ends up with is
decided per weight by passes that read that weight's geometry (a jiggle bone
grafted on one weight, a twist bone left on two vertices of one), and no per-file
gate can be made to agree. The node set therefore stops depending on them:
`_canonical_skeleton_nodes` derives it from the SOURCE and the body, and
`_seed_canonical_skeleton_nodes` creates it in the still-empty output.

The stand-ins are only as deep as the helpers read: `.nodes` (name -> node with
`.parent`), `.shapes`, `.rootNode`, `add_node`.
"""
import inspect

import pytest

from src import nif_convert as nc
from src import nif_convert_physics as ph

SPINE = "NPC Spine [Spn0]"
PELVIS = "NPC Pelvis [Pelv]"
PAULDRON = "NPC L Pauldron"
FOREARM = "NPC L Forearm [LLar]"


@pytest.fixture(autouse=True)
def _skeleton(monkeypatch):
    """Whether the actor skeleton is loadable here changes what the predicate
    says (it falls back to a name heuristic without one), so pin it: `NPC ...`
    names are the actor's, `Clitoral1` too (so the genital filter has work to do),
    everything else is a custom bone."""
    monkeypatch.setattr(
        ph, "_actor_can_resolve_bone",
        lambda n: n.startswith("NPC ") or n == "Clitoral1")


class Node:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent


class Shape:
    def __init__(self, name):
        self.name = name


class Src:
    def __init__(self, names, parents=None, shapes=("Armor",), root="Scene Root"):
        parents = parents or {}
        self.nodes = {}
        self.nodes[root] = Node(root)
        for n in names:
            self.nodes[n] = Node(n)
        for child, par in parents.items():
            self.nodes[child] = self.nodes.get(child) or Node(child)
            self.nodes[child].parent = self.nodes[par]
        self.shapes = [Shape(s) for s in shapes]
        self.rootNode = self.nodes[root]


class Dst:
    def __init__(self, have=()):
        self.nodes = {n: object() for n in have}
        self.added = []

    def add_node(self, name, xf, parent=None):
        assert parent is None, "canonical nodes are flat"
        self.nodes[name] = object()
        self.added.append(name)


def canon(src, body=()):
    return ph._canonical_skeleton_nodes(src, body)


def test_the_skeleton_nodes_of_the_source_are_canonical():
    assert canon(Src([SPINE, PELVIS, PAULDRON])) == sorted([SPINE, PELVIS, PAULDRON])


def test_the_bodys_bones_are_canonical_too():
    assert canon(Src([SPINE]), body=[PELVIS]) == sorted([SPINE, PELVIS])


def test_a_custom_chain_node_is_not_a_skeleton_node():
    assert "SkirtFBone01" not in canon(Src([SPINE, "SkirtFBone01"]))


def test_genital_anatomy_is_never_canonical():
    got = canon(Src([SPINE, "NPC Anus Deep2", "Clitoral1"]))
    assert got == [SPINE]


def test_the_root_shapes_and_file_name_nodes_are_not_canonical():
    src = Src([SPINE, "Armor", "Piece_0.nif"], shapes=("Armor",))
    assert canon(src) == [SPINE]


def test_an_ancestor_of_a_custom_chain_is_left_to_the_chain_code():
    # `NPC L Forearm` anchors a custom chain: the chain code builds it with its
    # parent links and skips a node that already exists, so it must not be seeded
    src = Src([SPINE, FOREARM], parents={"ArmChain 01": FOREARM})
    got = canon(src)
    assert FOREARM not in got and SPINE in got


def test_ancestry_is_followed_all_the_way_up():
    src = Src([SPINE, PELVIS, FOREARM],
              parents={FOREARM: PELVIS, "Cape 01": FOREARM})
    got = canon(src)
    assert FOREARM not in got and PELVIS not in got and SPINE in got


def test_a_source_over_the_cap_gets_no_canonical_set(monkeypatch):
    monkeypatch.setattr(nc, "_CANONICAL_NODE_CAP", 2)
    assert canon(Src([SPINE, PELVIS, PAULDRON])) == []
    monkeypatch.setattr(nc, "_CANONICAL_NODE_CAP", 3)
    assert len(canon(Src([SPINE, PELVIS, PAULDRON]))) == 3


def test_a_cap_of_zero_means_no_cap(monkeypatch):
    monkeypatch.setattr(nc, "_CANONICAL_NODE_CAP", 0)
    assert len(canon(Src([SPINE, PELVIS, PAULDRON]))) == 3


def test_the_order_is_deterministic():
    names = canon(Src([PAULDRON, SPINE, PELVIS]))
    assert names == sorted(names)


def test_an_unreadable_source_gives_an_empty_set():
    class Broken:
        @property
        def nodes(self):
            raise RuntimeError("x")
    assert ph._canonical_skeleton_nodes(Broken()) == []


def test_the_seed_creates_missing_nodes_and_skips_existing_ones():
    dst = Dst(have=[SPINE])
    n = ph._seed_canonical_skeleton_nodes(dst, Src([SPINE, PELVIS, PAULDRON]))
    assert dst.added == sorted([PELVIS, PAULDRON]) and n == 2


def test_the_flag_switches_the_seed_off(monkeypatch):
    monkeypatch.setattr(nc, "CANONICAL_SKELETON_NODES", False)
    dst = Dst()
    assert ph._seed_canonical_skeleton_nodes(dst, Src([SPINE])) == 0 and not dst.added


def test_a_failing_seed_is_reported_not_raised():
    class Boom(Dst):
        def add_node(self, *a, **k):
            raise RuntimeError("no")
    assert ph._seed_canonical_skeleton_nodes(Boom(), Src([SPINE])) == 0


def test_both_first_write_sites_seed_the_set():
    p2 = inspect.getsource(nc.convert_nif_phase2)
    assert "_seed_canonical_skeleton_nodes(\n            dst_nif, src_nif," in p2
    p1 = inspect.getsource(nc.convert_nif)
    assert "_seed_canonical_skeleton_nodes(\n                    dst_nif_for_fit, src_nif_for_fit," in p1


def test_the_seed_comes_after_the_chain_anchors_in_phase_two():
    src = inspect.getsource(nc.convert_nif_phase2)
    assert src.index("_seed_flat_chain_anchors(dst_nif, src_nif)") < src.index(
        "_seed_canonical_skeleton_nodes(")


def test_defaults():
    assert nc.CANONICAL_SKELETON_NODES is True
    assert nc._CANONICAL_NODE_CAP == 100
    assert 'not _flag("CBBE2UBE_NO_CANONICAL_SKELETON_NODES", False)' in inspect.getsource(nc)


def test_the_real_pynifly_seed_creates_flat_skeleton_nodes():
    pyn = nc._pynifly()
    src = pyn.NifFile()
    src.initialize("SKYRIMSE", "src_canon_test.nif")
    src.createShapeFromData("Armor", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)],
                            [(0, 0), (1, 0), (0, 1)], [(0, 0, 1)] * 3)
    xf = pyn.TransformBuf()
    xf.set_identity()
    for n in (SPINE, PAULDRON):
        src.add_node(n, xf, parent=None)
    dst = pyn.NifFile()
    dst.initialize("SKYRIMSE", "dst_canon_test.nif")
    made = ph._seed_canonical_skeleton_nodes(dst, src, body_bones=[PELVIS])
    assert made == 3
    assert {SPINE, PAULDRON, PELVIS} <= set(dst.nodes)
    assert ph._seed_canonical_skeleton_nodes(dst, src, body_bones=[PELVIS]) == 0
