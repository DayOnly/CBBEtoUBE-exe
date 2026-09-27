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

r"""#sweep-sidecar-base -- a morph `.tri` or physics `.xml` moves with the
weight base the converter named it from.

THE DEFECT. The converter names a piece's `.tri` and `.xml` after the mesh stem
with ONE `_0`/`_1` taken off: `x_1_0.nif`/`x_1_1.nif` get `x_1.tri` and
`x_1.xml`. The stale-output sweep read the sidecar's stem back as a mesh and
took ANOTHER suffix off, so it filed `x_1.xml` under the base `x` (of `x_0.nif`
/ `x_1.nif`). A folder that holds both bases -- sources ship that -- then moved
the live `x_1_*` piece's physics XML and morph TRI along with a stale `x`, and
left a stale `x_1`'s sidecars behind in `meshes\!UBE`.

THE RULE. A sidecar `y.tri`/`y.xml` belongs to the base of `y_1.nif`: `y`.
`CBBE2UBE_NO_SWEEP_SIDECAR_BASE=1` restores the old reading.
"""
import pytest

from src import stale_sweep as ss
from tests.test_stale_output_sweep import IRON, World, _finish, _sweep, _vanilla
# The autouse fixture of the sweep's own tests: clean switches and caches.
from tests.test_stale_output_sweep import _clean  # noqa: F401

SWITCH = "CBBE2UBE_NO_SWEEP_SIDECAR_BASE"
X = "clothes/outfit/robe"          # x_0.nif / x_1.nif, x.tri, x.xml
X1 = "clothes/outfit/robe_1"       # x_1_0.nif / x_1_1.nif, x_1.tri, x_1.xml


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)


@pytest.fixture
def both(tmp_path):
    """One folder with both bases, each with its own morph and physics files."""
    w = World(tmp_path)
    w.mesh(IRON, "_0.nif", "_1.nif", ".tri")
    w.mesh(X, "_0.nif", "_1.nif", ".tri", ".xml")
    w.mesh(X1, "_0.nif", "_1.nif", ".tri", ".xml")
    return w


def _names(inv, base):
    return sorted(p.name for p in inv.bases.get(base, []))


def test_each_base_holds_the_sidecars_named_from_it(both):
    inv = ss.inventory(both.out)
    assert _names(inv, X) == ["robe.tri", "robe.xml", "robe_0.nif", "robe_1.nif"]
    assert _names(inv, X1) == ["robe_1.tri", "robe_1.xml", "robe_1_0.nif",
                               "robe_1_1.nif"]


@pytest.mark.parametrize("rel,base", [
    ("clothes/outfit/robe_1.xml", X1),
    ("clothes/outfit/robe_1.tri", X1),
    ("clothes/outfit/robe.xml", X),
    ("Clothes\\Outfit\\Robe.TRI", X),
    ("armor/odd/gloves.nif.tri", "armor/odd/gloves.nif"),
    ("clothes/outfit/robe_1_1.nif", X1),
    ("clothes/outfit/robe_1.nif", X),
])
def test_a_sidecar_keys_to_the_mesh_stem_one_suffix_longer(rel, base):
    assert ss.base_key(rel) == base


def test_moving_a_stale_base_leaves_the_live_bases_physics_and_morphs(both):
    """`x` is stale (its source is gone); `x_1` is converted this run. Only
    `x`'s files move: `x_1_*.nif` still finds `x_1.xml` and `x_1.tri`."""
    both.record({X: "Old Mod", X1: "vanilla", IRON: "vanilla"})
    results = [_vanilla(both, claims=(IRON, X1))]
    _sweep(both, results)
    for s in ("_1.xml", "_1.tri", "_1_0.nif", "_1_1.nif"):
        assert both.has(X, s), s
    for s in ("_0.nif", "_1.nif", ".tri", ".xml"):
        assert not both.has(X, s) and both.moved(X, s).is_file(), s
    _finish(both, results)
    assert both.has(X, "_1.xml") and both.has(X, "_1.tri")


def test_moving_a_stale_double_suffix_base_takes_its_own_sidecars(both):
    """The reverse: `x_1` is stale; its `.tri`/`.xml` go with it, `x`'s stay."""
    both.record({X1: "Old Mod", X: "vanilla", IRON: "vanilla"})
    results = [_vanilla(both, claims=(IRON, X))]
    _sweep(both, results)
    for s in ("_1.xml", "_1.tri", "_1_0.nif", "_1_1.nif"):
        assert not both.has(X, s) and both.moved(X, s).is_file(), s
    for s in ("_0.nif", "_1.nif", ".tri", ".xml"):
        assert both.has(X, s), s


def test_switched_off_the_sidecar_loses_a_second_suffix(both, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    assert ss.base_key("clothes/outfit/robe_1.xml") == X
    inv = ss.inventory(both.out)
    assert "robe_1.xml" in _names(inv, X) and "robe_1.tri" in _names(inv, X)
    both.record({X: "Old Mod", X1: "vanilla", IRON: "vanilla"})
    _sweep(both, [_vanilla(both, claims=(IRON, X1))])
    assert not both.has(X, "_1.xml") and not both.has(X, "_1.tri")
