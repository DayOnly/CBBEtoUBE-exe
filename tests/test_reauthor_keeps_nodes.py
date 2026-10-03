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


"""#reauthor-keeps-nodes -- a rebuild may drop a skin binding, never a node (#33).

`_reauthor_nif_fresh` copies every shape through `_install_skin`, which add_bones
only bones that still carry weight, so the NiNodes of everything else vanished
with them. Which files get rebuilt, and which of their bones are empty by then,
is decided per weight file, so `_0` and `_1` of one garment came out with
different NiNode sets. Nodes sit in front of the shapes, so a different count
moves every shape behind them (Dawnguard Heavy `_1` had three root stubs `_0`
had lost, every shape +3). `_keep_unweighted_nodes` re-creates, before any shape
is copied, the flat root-level nodes of the old file that carry no weight.

The stand-ins below are only as deep as the helper reads: `.nodes`, `.shapes`,
`.rootNode`, `add_node`.
"""
import inspect

from src import nif_convert as nc
from src import nif_convert_writer as w


class Node:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.global_transform = ("xf", name)


class Shape:
    def __init__(self, name, weights):
        self.name = name
        self.bone_names = list(weights)
        self.bone_weights = weights


class Old:
    def __init__(self, nodes, shapes, root="Scene Root"):
        self.nodes = {n.name: n for n in nodes}
        self.shapes = shapes
        self.rootNode = self.nodes[root]


class New:
    def __init__(self, have=()):
        self.nodes = {n: object() for n in have}
        self.added = []

    def add_node(self, name, xf, parent=None):
        assert parent is None, "kept nodes are flat"
        self.nodes[name] = object()
        self.added.append((name, xf))


def old_file(extra=(), weights=None):
    root = Node("Scene Root")
    nodes = [root] + [Node(n, root) for n in
                      ("NPC", "NPC COM [COM ]", "NPC Root [Root]", "Bone A", "Bone B")]
    nodes += [Node("Chain 01", nodes[1]), Node("Piece", root), Node("File_0.nif", root)]
    nodes += list(extra)
    w_ = weights if weights is not None else {"Bone A": [(0, 1.0)], "Bone B": [(1, 0.0)]}
    return Old(nodes, [Shape("Piece", w_)])


def kept(old, new=None):
    new = new or New()
    n = w._keep_unweighted_nodes(new, old)
    return n, [a for a, _ in new.added]


def test_the_unweighted_root_stubs_are_kept():
    n, names = kept(old_file())
    assert {"NPC", "NPC COM [COM ]", "NPC Root [Root]"} <= set(names)
    assert n == len(names)


def test_a_bone_that_carries_weight_is_left_to_add_bone():
    _n, names = kept(old_file())
    assert "Bone A" not in names


def test_a_bone_whose_weights_are_all_zero_is_kept_as_a_node():
    # in the skin's bone list but empty: the rebuild drops the binding, the node stays
    _n, names = kept(old_file())
    assert "Bone B" in names


def test_a_node_with_a_real_parent_is_left_to_the_chain_code():
    _n, names = kept(old_file())
    assert "Chain 01" not in names


def test_shapes_the_root_and_file_name_nodes_are_not_kept():
    _n, names = kept(old_file())
    for skip in ("Piece", "Scene Root", "File_0.nif"):
        assert skip not in names


def test_genital_anatomy_nodes_are_not_kept():
    root = Node("Scene Root")
    extra = [Node("NPC Anus Deep2", root), Node("Clitoral1", root)]
    old = old_file(extra=extra)
    old.nodes["NPC Anus Deep2"].parent = old.nodes["Scene Root"]
    old.nodes["Clitoral1"].parent = old.nodes["Scene Root"]
    _n, names = kept(old)
    assert "NPC Anus Deep2" not in names and "Clitoral1" not in names
    assert "NPC" in names


def test_a_node_already_in_the_new_file_is_not_added_twice():
    _n, names = kept(old_file(), New(have=("NPC",)))
    assert "NPC" not in names


def test_the_order_is_deterministic():
    _n, names = kept(old_file())
    assert names == sorted(names)


def test_unreadable_weights_count_as_weighted():
    class Bad:
        name = "Piece"
        bone_names = ["Bone A", "Bone B"]

        @property
        def bone_weights(self):
            raise RuntimeError("no weights")
    old = old_file()
    old.shapes = [Bad()]
    _n, names = kept(old)
    assert "Bone A" not in names and "Bone B" not in names


def test_an_unreadable_old_file_keeps_nothing():
    class Broken:
        @property
        def nodes(self):
            raise RuntimeError("x")
    assert w._keep_unweighted_nodes(New(), Broken()) == 0


def test_the_rebuild_keeps_nodes_before_it_copies_any_shape():
    src = inspect.getsource(w._reauthor_nif_fresh)
    keep = src.index("_keep_unweighted_nodes(new, old)")
    first_copy = src.index("_copy_shape(s, new")
    assert keep < first_copy
    assert "if _nc().REAUTHOR_KEEPS_NODES:" in src


def test_the_flag_defaults_on_and_has_an_off_switch():
    assert nc.REAUTHOR_KEEPS_NODES is True
    src = inspect.getsource(nc)
    assert 'not _flag("CBBE2UBE_NO_REAUTHOR_KEEPS_NODES", False)' in src
