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

"""#hdt-xml-absent -- a pointer to a physics file nobody ships is an outcome, not a
failed pass.

In one full run all 12 `hdt_xml_unresolved` pass failures were pieces whose author
left a pointer to ANOTHER mod's physics file (a boot pointing at a dress XML from a
mod not installed). The game loads no physics for those pieces either. They read
"PASS FAILED" in the summary and "SMP dead -- extra-data dropped by a rebuild?" in
the validator, which sent readers hunting for a converter bug.
"""
from pathlib import Path

from src import nif_convert as nc
from src import nif_convert_physics as ph
from tests import _converter_sources as _cs  # patch on every module that binds a name

PTR = "meshes\\Other Mod\\dress.xml"


class _Ed:
    name = "HDT Skinned Mesh Physics Object"

    def __init__(self, s):
        self.string_data = s


class _Root:
    def __init__(self, ptr):
        self._ptr = ptr

    def extra_data(self):
        return [_Ed(self._ptr)] if self._ptr else []


class _Nif:
    def __init__(self, ptr=PTR):
        self.rootNode = _Root(ptr)


def _world(monkeypatch, *, order=True, found=None):
    _cs.patch(monkeypatch, "_load_order_dirs", lambda: [Path("x")] if order else None)
    _cs.patch(monkeypatch, "_resolve_data_rel_in_vfs", lambda rel, src: found)
    _cs.patch(monkeypatch, "_load_order_file", lambda norm: found)


def test_a_pointer_no_folder_ships_is_absent(monkeypatch):
    _world(monkeypatch)
    assert ph._hdt_xml_pointer_absent(Path("boots_1.nif"), nif=_Nif()) == PTR


def test_a_pointer_that_resolves_is_not_absent(monkeypatch):
    _world(monkeypatch, found=Path("D:/mods/Other Mod/meshes/dress.xml"))
    assert ph._hdt_xml_pointer_absent(Path("boots_1.nif"), nif=_Nif()) is None


def test_without_a_load_order_nobody_can_say_it_is_absent(monkeypatch):
    _world(monkeypatch, order=False)
    assert ph._hdt_xml_pointer_absent(Path("boots_1.nif"), nif=_Nif()) is None


def test_a_piece_without_a_pointer_is_not_absent(monkeypatch):
    _world(monkeypatch)
    assert ph._hdt_xml_pointer_absent(Path("boots_1.nif"), nif=_Nif(None)) is None


def _unresolved(monkeypatch, absent):
    nc._begin_piece_pass_log()
    _cs.patch(monkeypatch, "_read_source_hdt_xml_disk", lambda p, nif=None: None)
    _cs.patch(monkeypatch, "_nif_declares_hdt_xml", lambda p, nif=None: True)
    _cs.patch(monkeypatch, "_hdt_xml_pointer_absent", lambda p, nif=None: absent)
    monkeypatch.setattr(nc, "_PIECE_HDT_XML_TEXT", None)
    got = ph._read_source_hdt_xml_text_uncached(Path("boots_1.nif"), nif=_Nif(),
                                                stem_scan=False)
    return got, nc._piece_pass_failures(), nc._piece_pass_effects()


def test_an_absent_file_is_an_effect_not_a_failure(monkeypatch):
    got, fails, effects = _unresolved(monkeypatch, PTR)
    assert got is None                                    # protections still fail closed
    assert not fails
    assert any("#hdt-xml-absent" in e and "dress.xml" in e for e in effects)


def test_a_file_that_exists_but_did_not_resolve_is_still_a_failure(monkeypatch):
    got, fails, effects = _unresolved(monkeypatch, None)
    assert got is None
    assert any("hdt_xml_unresolved" in f for f in fails)
    assert not any("#hdt-xml-absent" in e for e in effects)


def test_a_file_only_the_meshs_own_mod_ships_is_not_absent(monkeypatch):
    """Found by the fallback lookup (the mesh's own mod) though not by the load
    order: it exists, so it is not absent."""
    _world(monkeypatch)
    _cs.patch(monkeypatch, "_resolve_data_rel_in_vfs",
              lambda rel, src: Path("D:/mods/Own/meshes/dress.xml"))
    assert ph._hdt_xml_pointer_absent(Path("boots_1.nif"), nif=_Nif()) is None
