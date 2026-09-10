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

"""#proxy-encloses-chain: a collision proxy must not CONTAIN its own chain.

Confirmed in game 2026-08-23: a generated `SkirtCol` holding 11 of a skirt's 71
chain nodes gave an intermittent, character-relative spike and a skirt that
behaved wrongly throughout. A node inside its own collider starts the solver in
violation on frame one.

The guard must DISCRIMINATE, not merely disable: half the pack's proxies are
well-formed and those must still be built. Both directions are asserted here,
because a gate that always fires is a kill switch wearing a gate's name.
"""
import numpy as np

from src import nif_convert as nc


def _box(cx=0.0, cy=0.0, cz=0.0, r=1.0):
    """Closed axis-aligned cube centred at (cx,cy,cz), outward winding."""
    v = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                  [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], float)
    v = v * r + np.array([cx, cy, cz], float)
    t = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                  [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6],
                  [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]], np.int64)
    return v, t


def test_a_node_inside_the_hull_is_reported():
    v, t = _box(r=5.0)
    got = nc._proxy_encloses_chain_nodes(
        v, t, {"Skirt 01": np.array([0.0, 0.0, 0.0]),
               "Skirt 02": np.array([1.0, -2.0, 0.5])})
    assert sorted(got) == ["Skirt 01", "Skirt 02"]


def test_nodes_outside_the_hull_are_not_reported():
    """THE POSITIVE CONTROL. Half of the pack's proxies are well-formed; a guard
    that flagged those would delete a pass that works."""
    v, t = _box(r=1.0)
    got = nc._proxy_encloses_chain_nodes(
        v, t, {"Skirt 01": np.array([50.0, 0.0, 0.0]),
               "Skirt 02": np.array([0.0, 9.0, 0.0])})
    assert got == []


def test_it_separates_a_mixed_set():
    v, t = _box(r=2.0)
    got = nc._proxy_encloses_chain_nodes(
        v, t, {"in": np.array([0.0, 0.0, 0.0]),
               "out": np.array([0.0, 0.0, 40.0])})
    assert got == ["in"]


def test_degenerate_input_is_not_an_enclosure():
    """A proxy that cannot be tested must not be reported as enclosing --
    that would decline every piece whose hull failed to build."""
    v, t = _box()
    assert nc._proxy_encloses_chain_nodes(v, t, {}) == []
    assert nc._proxy_encloses_chain_nodes(v[:2], t, {"a": np.zeros(3)}) == []
    assert nc._proxy_encloses_chain_nodes(v, t[:1], {"a": np.zeros(3)}) == []


def test_the_guard_defaults_ON():
    """It prevents a defect confirmed in game, so it is not opt-in."""
    assert nc.PROXY_ENCLOSE_GUARD is True
