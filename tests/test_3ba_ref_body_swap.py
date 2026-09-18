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

"""Inline-body detection of a "3BA Ref" reference body (#3ba-ref-body).

Mod authors leave a full CBBE BodySlide reference body named "3BA Ref" inside
skimpy armor NIFs; it renders with the ARMOR diffuse, so the texture-gated
heuristic misses it and a whole CBBE body renders on the UBE actor. It must be
detected as an inline body (-> swapped to the UBE body). BUT the smaller
lower-body "3BA Ref" COLLISION PROXY that skirt HDT-SMP XMLs reference must NOT
be flagged, or skirt physics loses its collision shape.
"""
import numpy as np
import pytest

from src import nif_convert as nc


class _Shape:
    def __init__(self, name, nverts, z_lo, z_hi, nbones):
        self.name = name
        rng = np.linspace(z_lo, z_hi, nverts)
        self.verts = [(0.0, 0.0, float(z)) for z in rng]
        self.bone_names = [f"b{i}" for i in range(nbones)]


def test_full_3ba_ref_body_is_inline_body():
    # full CBBE body: ~32k verts, full character height, many bones
    s = _Shape("3BA Ref", 31923, 0.0, 132.0, 72)
    assert nc._looks_like_inline_body(s) is True


def test_skirt_3ba_ref_collision_proxy_is_kept():
    # lower-body collision proxy: ~13k verts, only to ~78u, fewer bones
    s = _Shape("3BA Ref", 13410, 0.0, 78.0, 28)
    assert nc._looks_like_inline_body(s) is False


def test_3ba_ref_reference_spelling_also_caught():
    s = _Shape("3BA Reference", 30000, 0.0, 130.0, 60)
    assert nc._looks_like_inline_body(s) is True


def test_exact_3ba_still_caught_by_name():
    # canonical name path is unaffected (tiny body-part meshes included)
    assert nc._is_inline_body_name("3BA") is True


def test_name_family_helper():
    assert nc._is_3ba_body_family_name("3BA Ref") is True
    assert nc._is_3ba_body_family_name("3ba_anus") is True
    assert nc._is_3ba_body_family_name("ModSkirt1") is False
    assert nc._is_3ba_body_family_name(None) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
