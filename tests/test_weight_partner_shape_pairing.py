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


"""#weight-partner-shape-pairing -- the jiggle sync pairs the same shape at both weights.

`_sync_weight_partner_jiggle` paired `_0` and `_1` shapes by NAME. An author who
names the shapes per weight (`TorsoF_0` / `TorsoF_1`) ships the same mesh under
two names, so every such piece fell out of the sync and a jiggle bone (`NPC
Belly`, 81 to 457 vertices) stayed in `_0` only (#33, mechanism B2). Shapes with
no namesake are now paired by position and vertex count.
"""
import inspect

from src import nif_convert_weights as ww


class S:
    def __init__(self, name, n):
        self.name = name
        self.verts = [(0.0, 0.0, 0.0)] * n


def pair(a, b):
    return ww._pair_weight_partner_shapes(a, b)


def test_shapes_with_the_same_name_pair_by_name():
    assert pair([S("A", 5), S("B", 7)], [S("B", 7), S("A", 5)]) == {"A": "A", "B": "B"}


def test_per_weight_names_pair_by_position_and_vertex_count():
    got = pair([S("TorsoF_0", 100), S("PantsF_0", 40)], [S("TorsoF_1", 100), S("PantsF_1", 40)])
    assert got == {"TorsoF_0": "TorsoF_1", "PantsF_0": "PantsF_1"}


def test_a_different_vertex_count_is_never_paired():
    assert pair([S("TorsoF_0", 100)], [S("TorsoF_1", 101)]) == {}


def test_lists_of_different_length_pair_by_name_only():
    assert pair([S("A_0", 5), S("B_0", 5)], [S("A_1", 5)]) == {}
    assert pair([S("A", 5), S("B_0", 5)], [S("A", 5)]) == {"A": "A"}


def test_a_shape_is_matched_once():
    # "X_0" sits at the index where `_1` has "A", which already has its namesake:
    # position only pairs shapes at the SAME index, and never takes a taken shape.
    got = pair([S("A", 5), S("X_0", 5)], [S("X_1", 5), S("A", 5)])
    assert got == {"A": "A"}
    got = pair([S("X_0", 5), S("A", 5)], [S("X_1", 5), S("A", 5)])
    assert got == {"X_0": "X_1", "A": "A"}
    assert len(set(got.values())) == len(got)


def test_an_empty_shape_is_not_paired_by_position():
    assert pair([S("E_0", 0)], [S("E_1", 0)]) == {}


def test_the_sync_looks_its_partner_up_through_the_pairing():
    src = inspect.getsource(ww._sync_weight_partner_jiggle_loaded)
    assert 'partner = _pair_weight_partner_shapes(nf["0"].shapes, nf["1"].shapes)' in src
    assert 'by["1"].get(partner.get(name))' in src
    assert 'own = name if side == "0" else partner[name]' in src
    assert 's, src = by[side][own], by[other][oth]' in src
