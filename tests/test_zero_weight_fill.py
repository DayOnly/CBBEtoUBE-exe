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

"""Regression guard for _fill_zero_weight_verts (#zeroweight).

Verts with no bone weight skin to the origin -> a spike to (0,0,0). The fix gives
each zero-weight vert the weights of its nearest weighted vert. Seen on guard-armor
reskins (qwib) and decoration/1st-person shapes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402


def _wsum(weights, n):
    ws = [0.0] * n
    for _bn, pairs in weights.items():
        for i, w in pairs:
            ws[int(i)] += float(w)
    return ws


def test_fills_zero_weight_vert_from_nearest():
    # vert 0 + 1 weighted; vert 2 (closest to vert 1) carries NO weight
    verts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (11.0, 0.0, 0.0)]
    weights = {"BoneA": [(0, 1.0)], "BoneB": [(1, 1.0)]}
    out = nc._fill_zero_weight_verts(weights, verts)
    ws = _wsum(out, 3)
    assert ws[2] > 0.9                                  # vert 2 now weighted
    assert any(i == 2 for i, _w in out.get("BoneB", []))  # borrowed nearest (BoneB)


def test_noop_when_all_weighted():
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    weights = {"BoneA": [(0, 1.0), (1, 1.0)]}
    out = nc._fill_zero_weight_verts(weights, verts)
    assert out is weights                               # unchanged (no-op)
