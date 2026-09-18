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

"""pynifly's `extra_data()` cannot see a BODYTRI behind an unbuildable block. #extra-data-walk-blind

`extra_data()` walks `get_extra_data(target_index=i)` and stops at the first
block pynifly cannot build, so every later block on that shape is invisible. The
three rollback guards (`_all_extra` in nif_convert_bust.py and twice in
nif_convert_physics.py) snapshot extra data through it and check
`pre_extra <= post_extra`: a BODYTRI hidden that way is missing from both sides,
so its loss would pass. `_census_common.bodytri_of` enumerates by index over
`extraDataCount` and does see it.

The guards are deliberately NOT switched to the index reader (a stricter
invariant could roll back healthy pieces, because the write-time re-author
collapses tags), and the acceptance population has no such shape: 0 blind of
its BODYTRI shapes (plan D-09, measured 2026-09-15). So this pins the divergence
itself, on a synthetic NIF, where no population can: whoever changes either
reader, or pynifly, sees at once which one is blind.
"""
import struct
from pathlib import Path

import pytest

from src import hh_offset
from tests.synthetic_nif import TRIS, VERTS, nc, pynifly_available

pytestmark = pytest.mark.skipif(not pynifly_available(), reason="pynifly native lib not loadable")

TRI_PATH = "meshes\\armor\\synthetic\\body.tri"


def _two_string_blocks(path: Path) -> Path:
    """One shape carrying HIDDEN then BODYTRI, both NiStringExtraData."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    sh = nif.createShapeFromData("Body", VERTS, TRIS, [(0.0, 0.0)] * len(VERTS),
                                 [(0.0, 0.0, 1.0)] * len(VERTS))
    pyn.NiStringExtraData.New(nif, name="HIDDEN", string_value="x", parent=sh)
    pyn.NiStringExtraData.New(nif, name="BODYTRI", string_value=TRI_PATH, parent=sh)
    nif.save()
    return path


def _retype_to_unbuildable(path: Path, name: bytes = b"HIDDEN") -> None:
    """Turn the named NiStringExtraData into a NiFloatExtraData, a type pynifly
    does not build. Both bodies are 8 bytes (a name index, then a string index or
    a float), so only the block's type index changes."""
    data = path.read_bytes()
    p = hh_offset._parse(data)
    assert hh_offset._serialize(p) == data, "the fixture NIF does not round-trip"
    name_idx = p["strings"].index(name)
    string_t = p["block_types"].index(b"NiStringExtraData")
    hits = [k for k, (t, body) in enumerate(zip(p["bti"], p["blocks"]))
            if t == string_t and struct.unpack_from("<i", body)[0] == name_idx]
    assert len(hits) == 1 and len(p["blocks"][hits[0]]) == 8, (hits, "block layout changed")
    if b"NiFloatExtraData" not in p["block_types"]:
        p["block_types"].append(b"NiFloatExtraData")
    p["bti"][hits[0]] = p["block_types"].index(b"NiFloatExtraData")
    path.write_bytes(hh_offset._serialize(p))


def _readers(path: Path):
    from scripts.analysis._census_common import bodytri_of
    pyn = nc._pynifly()
    shape = pyn.NifFile(filepath=str(path)).shapes[0]
    walked = [getattr(ed, "name", None) for ed in shape.extra_data()]
    return walked, bodytri_of(shape), int(shape.properties.extraDataCount)


def test_both_readers_agree_when_every_block_is_buildable(tmp_path):
    """Control: without the retype, the fixture is an ordinary shape and the two
    readers see the same thing -- so the divergence below is the hidden block's."""
    walked, bodytri, count = _readers(_two_string_blocks(tmp_path / "plain.nif"))
    assert count == 2
    assert walked == ["HIDDEN", "BODYTRI"]
    assert bodytri == [TRI_PATH]


def test_extra_data_walk_misses_a_bodytri_that_bodytri_of_reads(tmp_path):
    path = _two_string_blocks(tmp_path / "blind.nif")
    _retype_to_unbuildable(path)
    walked, bodytri, count = _readers(path)
    assert count == 2, "the shape still carries both blocks"
    assert walked == [], f"extra_data() is expected to stop at the unbuildable block, got {walked}"
    assert bodytri == [TRI_PATH], f"bodytri_of must still find the BODYTRI, got {bodytri}"
