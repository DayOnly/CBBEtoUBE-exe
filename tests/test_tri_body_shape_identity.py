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

"""The injected UBE body is identified by TOPOLOGY, not by its name.

A mod may name one of its own armour shapes `BaseShape`, and 4 NIFs in the pack
do -- two of them a 3618-vertex cuirass part. Split by name alone, that shape
was pulled out of the armour set and handed to `generate_armor_tri` as a body
shape, which embeds the UBE body's OSD morphs VERBATIM: offsets indexing to
29297 on a 3618-vertex mesh. That is two of the five out-of-bounds entries the
pack census reports, and it is a DIFFERENT defect from #tri-variant-collision
(which owns the other three).

`generate_armor_tri` cannot catch it downstream: its `#osd-bounds` filter bounds
the verbatim offsets against the body REFERENCE's vert count, on the assumption
its comment states -- "body_verts shares the injected BaseShape's topology".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc  # noqa: E402
from src.nif_convert_trigen import _collect_tri_inputs  # noqa: E402
from tests.synthetic_nif import (  # noqa: E402
    TRIS, VERTS, build_shape_nif, pynifly_available)

pytestmark = pytest.mark.skipif(
    not pynifly_available(), reason="pynifly native lib not available")


def _nif(tmp_path, name, verts):
    p = tmp_path / (name + ".nif")
    build_shape_nif(p, name=name, verts=verts, tris=TRIS)
    return nc._pynifly().NifFile(filepath=str(p))


def test_an_authored_shape_named_baseshape_is_armour_not_the_body(tmp_path):
    """The defect. A 4-vert `BaseShape` against a 29298-vert body reference is
    an armour shape that happens to share the name -- it must get its OWN
    propagated morphs, never the body's verbatim ones."""
    nif = _nif(tmp_path, "BaseShape", VERTS)
    armor, body, _ef = _collect_tri_inputs(nif, 29298)
    assert body == set(), (
        "a 4-vertex shape cannot carry a 29298-vertex body's OSD offsets")
    assert "BaseShape" in armor
    assert len(armor["BaseShape"]) == len(VERTS)


def test_the_real_injected_body_is_still_the_body(tmp_path):
    """Matching topology -> it IS the injected body, embedded verbatim."""
    nif = _nif(tmp_path, "BaseShape", VERTS)
    armor, body, _ef = _collect_tri_inputs(nif, len(VERTS))
    assert body == {"BaseShape"}
    assert "BaseShape" not in armor


def test_without_a_body_count_the_split_is_unchanged(tmp_path):
    """An unmeasured caller keeps the name-only behaviour rather than guessing.
    """
    nif = _nif(tmp_path, "BaseShape", VERTS)
    armor, body, _ef = _collect_tri_inputs(nif)
    assert body == {"BaseShape"}
    assert "BaseShape" not in armor


def test_the_collision_proxy_is_never_armour(tmp_path):
    """`VirtualBody` is the SMP proxy we generate ourselves. Its topology is
    per-piece by construction, so a topology check would demote EVERY one of
    them into the armour set and start morphing a collision shape. It must stay
    out of both -- `generate_armor_tri` only ever emits a verbatim body TriShape
    for `BaseShape`, so naming it in the body set is inert."""
    nif = _nif(tmp_path, "VirtualBody", VERTS)
    armor, body, _ef = _collect_tri_inputs(nif, 29298)
    assert armor == {}, "a collision proxy must not be given body morphs"
    assert body == {"VirtualBody"}
