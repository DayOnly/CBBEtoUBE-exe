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

"""`_find_hdt_xml_for_armor` fallback (used when a source NIF carries no physics
extra-data). The tiny keyword map (boob/breast/tasset/skirt/tail) misses most
authored config names (Dress, Choker, LongHair, ...). An XML whose stem EXACTLY
matches the NIF stem is the armour's own config regardless of keyword, so it must
win -- else the piece falls back to a GENERATED XML. But a stem MISMATCH must NOT
be grabbed just for sharing a folder. #xml-stem-match
"""
import src.nif_convert as nc  # noqa: E402


def _mk(root, rel_nif, xmls):
    """Create <root>/meshes/<rel_nif> and the given xml rel-paths; return nif path."""
    nif = root / "meshes" / rel_nif
    nif.parent.mkdir(parents=True, exist_ok=True)
    nif.write_bytes(b"x")
    for x in xmls:
        p = root / "meshes" / x
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<system></system>")
    return nif


def test_exact_stem_match_wins_over_missing_keyword(tmp_path):
    nif = _mk(tmp_path, "armor/hair/LongHair_1.nif", ["armor/hair/LongHair.xml"])
    res = nc._find_hdt_xml_for_armor(nif)
    assert res is not None and res.lower().endswith("longhair.xml")


def test_stem_mismatch_in_same_folder_not_grabbed(tmp_path):
    # A lone unrelated XML sharing the folder must NOT be picked (no stem/keyword).
    nif = _mk(tmp_path, "armor/boots/boots_1.nif", ["armor/boots/random.xml"])
    assert nc._find_hdt_xml_for_armor(nif) is None


def test_same_dir_exact_preferred_over_distant_exact(tmp_path):
    nif = _mk(tmp_path, "armor/a/Cuirass_1.nif",
              ["armor/a/Cuirass.xml", "armor/b/Cuirass.xml"])
    res = nc._find_hdt_xml_for_armor(nif)
    assert res is not None and res.lower().replace("\\", "/").endswith("a/cuirass.xml")


def test_keyword_match_still_works(tmp_path):
    # Non-matching stem but a valid keyword pairing (breast XML <-> torso NIF).
    nif = _mk(tmp_path, "armor/x/femaletorso_1.nif", ["armor/x/breastphysics.xml"])
    res = nc._find_hdt_xml_for_armor(nif)
    assert res is not None and res.lower().endswith("breastphysics.xml")
