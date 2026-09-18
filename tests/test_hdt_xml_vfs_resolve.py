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

"""Guard for the authored-HDT-XML VFS resolution (#177): the source armor NIF's
physics-XML reference must resolve even when the authored XML ships in a
DIFFERENT mod than the (BodySlide-output) NIF. This is the authored-physics
"pulls to origin" root cause -- the converter couldn't find MDDressA.xml (it
lives in the PHYSICS mod, the NIF lives in the Bodyslide-output mod) so it overwrote the
custom chain physics with a generic XML that doesn't drive the chain."""
from pathlib import Path
from src import nif_convert


REL = "Meshes\\ModAuthor\\MageDress\\XML\\MDDressA.xml"


def _make_layout(tmp: Path):
    """mods/ with a Bodyslide-output mod (ships the NIF) and a separate armor
    mod (ships the authored XML) -- the real physics-mod / BodySlide split."""
    mods = tmp / "mods"
    nif = (mods / "ExampleMod - Bodyslide Output - 3BA" / "meshes"
           / "ModAuthor" / "MageDress" / "MageDress_DressA_1.nif")
    nif.parent.mkdir(parents=True, exist_ok=True)
    nif.write_bytes(b"\x00")
    xml = (mods / "MAGEDRESS - hdt SMP (CBBE 3BA)" / "Meshes"
           / "ModAuthor" / "MageDress" / "XML" / "MDDressA.xml")
    xml.parent.mkdir(parents=True, exist_ok=True)
    xml.write_text("<system/>")
    return mods, nif, xml


def test_xml_in_other_mod_resolved_via_vfs(tmp_path, monkeypatch):
    mods, nif, xml = _make_layout(tmp_path)
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: mods)
    got = nif_convert._resolve_data_rel_in_vfs(REL, nif)
    assert got is not None, "VFS fallback failed to find the authored XML"
    assert got == xml, got


def test_local_xml_preferred_over_vfs(tmp_path, monkeypatch):
    # If the XML sits next to the NIF's own mod root, that copy wins (no VFS).
    mods, nif, xml = _make_layout(tmp_path)
    local = (nif.parents[3] / "Meshes" / "ModAuthor" / "MageDress"
             / "XML" / "MDDressA.xml")
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("<system/>")
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: mods)
    got = nif_convert._resolve_data_rel_in_vfs(REL, nif)
    assert got == local, got            # local copy, not the other-mod one


def test_missing_everywhere_returns_none(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    nif = (mods / "SomeMod" / "meshes" / "a.nif")
    nif.parent.mkdir(parents=True, exist_ok=True)
    nif.write_bytes(b"\x00")
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: mods)
    got = nif_convert._resolve_data_rel_in_vfs(REL, nif)
    assert got is None, got


def test_empty_rel_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: tmp_path)
    assert nif_convert._resolve_data_rel_in_vfs("", tmp_path / "x.nif") is None


def test_vfs_falls_back_to_src_derived_root_when_mods_root_none(tmp_path, monkeypatch):
    # When mods_root() is None in THIS process (a worker that didn't inherit the env),
    # the resolver must still find the other-mod XML by deriving the mods root from
    # the NIF's OWN path (above the mod folder that holds 'meshes'). Without this the
    # source HDT XML silently fails to resolve -> per-triangle colliders undetected ->
    # partitions collapsed -> FSMP equip CTD (the elven Greaves regression).
    mods, nif, xml = _make_layout(tmp_path)
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: None)
    got = nif_convert._resolve_data_rel_in_vfs(REL, nif)
    assert got == xml, f"src-derived mods-root fallback failed: {got}"
