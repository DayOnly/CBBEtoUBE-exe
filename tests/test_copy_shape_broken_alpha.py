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

"""A source shape whose NiAlphaProperty reference is BROKEN (pynifly's
has_alpha_property getter raises "getNiAlphaProperty called on invalid node")
must still copy -- WITHOUT the alpha property -- instead of failing _copy_shape
and dropping the shape (invisible piece in-game; a modded-gauntlets
class)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.nif_convert as nc
from tests.synthetic_nif import (build_shape_nif, pynifly_available)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")


def test_broken_alpha_read_copies_without_alpha(tmp_path, monkeypatch):
    build_shape_nif(tmp_path / "src.nif")
    pyn = nc._pynifly()
    src_nif = pyn.NifFile(filepath=str(tmp_path / "src.nif"))
    s = src_nif.shapes[0]

    # Break the alpha READ exactly like the real failure: the class property
    # raises a plain Exception (nifly getBlock error) on every access. The
    # setter path (new_shape.has_alpha_property = True) then also fails ->
    # inner try -> copy proceeds WITHOUT alpha. Restore via monkeypatch scope.
    def _boom(self):
        raise Exception(
            "Error calling nifly getBlock: ERROR: getNiAlphaProperty called "
            "on invalid node.")
    monkeypatch.setattr(type(s), "has_alpha_property", property(_boom),
                        raising=False)

    dst = pyn.NifFile()
    dst.initialize("SKYRIMSE", str(tmp_path / "dst.nif"))
    nc._copy_shape(s, dst)          # must NOT raise (was: shape DROPPED)
    dst.save()

    monkeypatch.undo()              # restore before reloading the output
    out = pyn.NifFile(filepath=str(tmp_path / "dst.nif"))
    assert len(out.shapes) == 1, "shape must survive a broken alpha reference"
    assert len(out.shapes[0].verts) == len(s.verts)
    print("  test_broken_alpha_read_copies_without_alpha OK")
