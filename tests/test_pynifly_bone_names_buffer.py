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

"""A shape's bone names are read whole, whatever their total length.

pynifly reads a shape's bone list, joined by newlines, into a 300-byte buffer and
reads again into a bigger one only when the DLL reports MORE than 300. The DLL
writes at most 299 characters plus the terminator, so a list exactly 300 long came
back with its last bone name one character short: `NPC L Calf [LClf` for
`NPC L Calf [LClf]`. The converter wrote that name back out, so the vertices on it
were skinned to a bone no skeleton has. In one pack: a dress's calf (665 verts in
both weights), a cuirass's upper arm (18 verts), and two one-weight grafts
(`R Breast0`, `NPC R RearThig`) whose node then sat in one weight file only.
The fix is the vendored change in `.pynifly/patches/pynifly.py.diff`.
"""
import pytest

from src import nif_convert as nc
from tests.synthetic_nif import pynifly_available, uv_sphere

pytestmark = pytest.mark.skipif(not pynifly_available(), reason="needs pynifly")


def _names_of_total_length(total):
    names, cur = [], 0
    while cur + 11 + 10 < total:
        names.append(f"Bone{len(names):06d}")
        cur += 11
    names.append(("Z" * (total - cur))[:-1] + "Q")
    assert len("\n".join(names)) == total
    return names


def _round_trip(tmp_path, names):
    pyn = nc._pynifly()
    v, t, n = uv_sphere(5.0)
    p = tmp_path / "bones.nif"
    nf = pyn.NifFile()
    nf.initialize("SKYRIMSE", str(p))
    sh = nf.createShapeFromData("S", list(v), list(t), [(0.0, 0.0)] * len(v), list(n))
    sh.skin()
    for b in names:
        sh.add_bone(b)
    idt = pyn.TransformBuf()
    idt.set_identity()
    for b in names:
        sh.set_skin_to_bone_xform(b, idt)
        sh.setShapeWeights(b, [(0, 1.0)])
    nf.save()
    return pyn.NifFile(filepath=str(p)).shapes[0].bone_names


def test_a_bone_list_exactly_300_long_is_read_whole(tmp_path):
    names = _names_of_total_length(300)
    assert _round_trip(tmp_path, names) == names


@pytest.mark.parametrize("total", (299, 301, 600))
def test_bone_lists_around_the_edge_are_read_whole(tmp_path, total):
    names = _names_of_total_length(total)
    assert _round_trip(tmp_path, names) == names


def test_the_reported_calf_bone_keeps_its_bracket(tmp_path):
    """The reported dress: its last bone was `NPC L Calf [LClf]` in a 300-long list."""
    names = _names_of_total_length(300 - len("NPC L Calf [LClf]") + 10)[:-1]
    names.append("NPC L Calf [LClf]")
    joined = len("\n".join(names))
    pad = 300 - joined
    if pad:
        names[0] = names[0] + "x" * pad
    assert len("\n".join(names)) == 300
    assert _round_trip(tmp_path, names)[-1] == "NPC L Calf [LClf]"
