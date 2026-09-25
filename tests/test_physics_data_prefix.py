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

"""#physics-data-prefix: a physics-XML pointer written as "Data\\meshes\\..."
resolves with its ONE leading Data segment removed.

Some authors write the NIF's `HDT Skinned Mesh Physics Object` string relative
to the game folder. No mod folder holds a "data" folder, so the resolver missed
it, the piece failed CLOSED (`hdt_xml_unresolved`) and shipped with no physics.
The raw rel is still tried first, every escape check still applies to the
stripped rel, and nothing is read out of an archive."""
from pathlib import Path

import pytest

from src import nif_convert


PREFIXED = "Data\\meshes\\ModAuthor\\Cloak\\cloak.xml"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_NO_PHYSICS_DATA_PREFIX", raising=False)
    nif_convert._VFS_DATA_REL_MEMO.clear()
    yield
    nif_convert._VFS_DATA_REL_MEMO.clear()


def _layout(tmp: Path, monkeypatch):
    """mods/ with a BodySlide-output mod shipping the NIF and a separate
    physics mod shipping the authored XML under meshes/ (no data/ folder)."""
    mods = tmp / "mods"
    nif = mods / "Cloak - Bodyslide Output" / "meshes" / "ModAuthor" / "Cloak" / "cloak_1.nif"
    nif.parent.mkdir(parents=True, exist_ok=True)
    nif.write_bytes(b"\x00")
    xml = mods / "Cloak - hdt SMP" / "meshes" / "ModAuthor" / "Cloak" / "cloak.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    xml.write_text("<system/>")
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: mods)
    return mods, nif, xml


def test_a_data_prefixed_pointer_resolves(tmp_path, monkeypatch):
    _mods, nif, xml = _layout(tmp_path, monkeypatch)
    assert nif_convert._resolve_data_rel_in_vfs(PREFIXED, nif) == xml


def test_lowercase_forward_slash_prefix_resolves(tmp_path, monkeypatch):
    _mods, nif, xml = _layout(tmp_path, monkeypatch)
    got = nif_convert._resolve_data_rel_in_vfs("data/Meshes/ModAuthor/Cloak/cloak.xml", nif)
    assert got is not None and got.samefile(xml)


def test_the_raw_rel_is_still_preferred_when_it_exists(tmp_path, monkeypatch):
    """A mod packaged as <mod>/data/meshes/... keeps the file it really ships."""
    mods, nif, _xml = _layout(tmp_path, monkeypatch)
    raw = mods / "Misplaced Mod" / "Data" / "meshes" / "ModAuthor" / "Cloak" / "cloak.xml"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("<system/>")
    assert nif_convert._resolve_data_rel_in_vfs(PREFIXED, nif) == raw


def test_only_one_data_segment_is_removed(tmp_path, monkeypatch):
    _mods, nif, _xml = _layout(tmp_path, monkeypatch)
    got = nif_convert._resolve_data_rel_in_vfs(
        "Data\\Data\\meshes\\ModAuthor\\Cloak\\cloak.xml", nif)
    assert got is None


def test_a_pointer_without_the_prefix_is_not_rewritten(tmp_path, monkeypatch):
    """Only a leading Data segment is stripped, never any other first folder."""
    _mods, nif, _xml = _layout(tmp_path, monkeypatch)
    got = nif_convert._resolve_data_rel_in_vfs(
        "Other\\meshes\\ModAuthor\\Cloak\\cloak.xml", nif)
    assert got is None


@pytest.mark.parametrize("rel", [
    "Data\\..\\..\\secret.xml",
    "Data\\meshes\\..\\..\\..\\secret.xml",
    "Data\\C:\\Windows\\secret.xml",
    "\\\\server\\share\\Data\\meshes\\x.xml",
])
def test_an_escape_is_still_refused(tmp_path, monkeypatch, rel):
    mods, nif, _xml = _layout(tmp_path, monkeypatch)
    # Bait at every place the escape could land, so a missed check would HIT.
    (tmp_path / "secret.xml").write_text("<system/>")
    (mods / "secret.xml").write_text("<system/>")
    assert nif_convert._resolve_data_rel_in_vfs(rel, nif) is None


def test_a_traversal_under_the_prefix_is_still_refused(tmp_path, monkeypatch):
    """'Data\\..\\..\\x' climbs out of the mod folder into the mods root."""
    mods, nif, _xml = _layout(tmp_path, monkeypatch)
    (mods / "secret.xml").write_text("<system/>")
    assert nif_convert._resolve_data_rel_in_vfs("Data\\..\\..\\secret.xml", nif) is None


def test_a_drive_letter_exposed_by_the_strip_is_refused(tmp_path, monkeypatch):
    """"Data\\<drive>:\\..." passes the first check (its head is "Data"); the strip
    exposes the drive, and pathlib would then discard the mod root entirely."""
    _mods, nif, _xml = _layout(tmp_path, monkeypatch)
    target = tmp_path / "outside" / "secret.xml"
    target.parent.mkdir(parents=True)
    target.write_text("<system/>")
    rel = "Data\\" + str(target)
    assert nif_convert._resolve_data_rel_in_vfs(rel, nif) is None


def test_a_file_only_inside_another_mods_archive_is_not_fetched(tmp_path, monkeypatch):
    """Resolution reads loose files only: an archive that holds the path is
    never opened, so a pointer into another mod's tree stays unresolved."""
    mods, nif, _xml = _layout(tmp_path, monkeypatch)
    rel = "Data\\meshes\\OtherAuthor\\Dress\\dressa.xml"
    bsa = mods / "Other Mod" / "Other Mod.bsa"
    bsa.parent.mkdir(parents=True, exist_ok=True)
    bsa.write_bytes(b"BSA\x00meshes\\otherauthor\\dress\\dressa.xml\x00<system/>")
    assert nif_convert._resolve_data_rel_in_vfs(rel, nif) is None


def test_switched_off_the_prefixed_pointer_misses_as_before(tmp_path, monkeypatch):
    _mods, nif, xml = _layout(tmp_path, monkeypatch)
    monkeypatch.setenv("CBBE2UBE_NO_PHYSICS_DATA_PREFIX", "1")
    assert nif_convert._resolve_data_rel_in_vfs(PREFIXED, nif) is None
    # an ordinary pointer is untouched by the switch
    assert nif_convert._resolve_data_rel_in_vfs(
        "meshes\\ModAuthor\\Cloak\\cloak.xml", nif) == xml


def test_the_source_nif_pointer_route_gets_the_xml(tmp_path, monkeypatch):
    """End to end on the reader every physics pass uses: the NIF's own
    extra-data string "Data\\meshes\\..." now yields the authored XML path."""
    _mods, nif, xml = _layout(tmp_path, monkeypatch)

    class _ED:
        name = "HDT Skinned Mesh Physics Object"
        string_data = PREFIXED

    class _Root:
        def extra_data(self):
            return [_ED()]

    class _Nif:
        rootNode = _Root()

    from src import nif_convert_physics as ncp
    assert ncp._read_source_hdt_xml_disk(nif, nif=_Nif()) == xml
    monkeypatch.setenv("CBBE2UBE_NO_PHYSICS_DATA_PREFIX", "1")
    assert ncp._read_source_hdt_xml_disk(nif, nif=_Nif()) is None
