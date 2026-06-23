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

"""Overlay-band morph-sync must MERGE, not REPLACE.

A garment caught by the size gate but only PARTLY overlapping an under-layer
(a shirt whose sleeves sit over undersleeves but whose chest has no cloth
under-layer) must keep its own chest/breast morphs. The old code replaced the
shape's ENTIRE morph set with the (arm-only) under-layer sync, silently dropping
the chest conform -> "armor doesn't conform" in-game (LotD Museum shirt: 125
source morphs collapsed to 19 arm-only)."""
import numpy as np

from src.osd import OsdFile, OsdMorph
from src.sliderset_gen import generate_armor_tri


def test_overlay_band_sync_preserves_non_overlapping_morphs():
    xs = np.arange(-8, 9, 2.0)
    zs = np.arange(70, 112, 2.0)
    body = np.array([(x, 0.0, z) for z in zs for x in xs], dtype=np.float64)

    def morph(name, zlo, zhi, dy):
        return OsdMorph(name="BaseShape" + name,
                        offsets=[(i, 0.0, dy, 0.0)
                                 for i, (x, y, z) in enumerate(body) if zlo <= z <= zhi])

    osd = OsdFile(version=1, morphs=[morph("ChestSlider", 88, 98, 1.5),
                                     morph("ArmSlider", 75, 85, 1.5)])

    # Shirt: chest verts (Y=1, near body chest, NO cloth under-layer) +
    # sleeve verts (Y=3, sitting outside the undersleeve at the arm region).
    chest = np.array([(x, 1.0, z) for z in np.arange(88, 99, 2.0) for x in xs])
    sleeve = np.array([(x, 3.0, z) for z in np.arange(75, 86, 2.0) for x in xs])
    shirt = np.vstack([chest, sleeve])
    # Undersleeve: at the arm region, closer to the body (Y=2) -> the shirt's
    # sleeve sits outside it; it carries ONLY the arm slider.
    under = np.array([(x, 2.0, z) for z in np.arange(75, 86, 2.0) for x in xs])
    # Big shape so the shirt falls UNDER the 0.40*maxv "band" gate (far away,
    # overlaps nothing).
    pants = np.array([(x, 30.0, z) for z in zs for x in xs] * 2)

    tri = generate_armor_tri({"Shirt": shirt, "Undersleeve": under, "Pants": pants},
                             body, osd, body_shape_name="BaseShape",
                             include_body_shapes=False)
    res = {s.name: {m.name for m in s.morphs} for s in tri.shapes}

    # The fix: the shirt KEEPS its chest morph (the old replace-logic dropped it,
    # leaving only the under-layer's arm slider). This is the regression.
    assert "ChestSlider" in res["Shirt"], res["Shirt"]
    # It still follows the arm slider at the synced sleeve region.
    assert "ArmSlider" in res["Shirt"], res["Shirt"]
    # The chest morph must actually cover the chest verts (first 54 = chest band),
    # i.e. it wasn't reduced to a stray sleeve-only remnant.
    shirt_shape = next(s for s in tri.shapes if s.name == "Shirt")
    chest_morph = next(m for m in shirt_shape.morphs if m.name == "ChestSlider")
    chest_verts_morphed = sum(1 for o in chest_morph.offsets if o[0] < 54)
    assert chest_verts_morphed >= 20, (chest_verts_morphed, len(chest_morph.offsets))
