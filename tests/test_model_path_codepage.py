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

r"""#model-path-codepage -- archive names and the model paths that name them
are read in one codepage, the game's (cp1252).

THE DEFECT. #arma-path-bytes reads MOD2-5 as cp1252, but the archive reader
decoded folder and file names as latin-1, and the alt-texture reconcile and the
postflight missing-mesh check read MOD2-5 as latin-1. For a byte in 0x80-0x9F
(a curly apostrophe is 0x92) the two give different text, so an archive-only
mesh was "absent" to the coverage step, and our own converted NIF at such a
path was "absent" to the reconcile (colour indices left stale) and the
postflight (a false missing-nif line). `CBBE2UBE_NO_MODEL_PATH_CODEPAGE=1`
reads latin-1 again.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, bsa_strings as bs, esp        # noqa: E402
from src import ube_patcher as up                                   # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,         # noqa: E402
                                 pynifly_available, uv_sphere)
from tests.test_alttex_exact_provenance import RED, TAN, _alt, _parse  # noqa: E402
from tests.test_bsa_seek_read import _write_bsa                     # noqa: E402

OFF = "CBBE2UBE_NO_MODEL_PATH_CODEPAGE"
needs_pynifly = pytest.mark.skipif(not pynifly_available(),
                                   reason="pynifly native lib not available")

# The archive and the armature hold the same byte, 0x92, as the game sees it.
RAW = b"armor\\elf\x92s\\cuirass_1.nif"
KEY = "armor/elf’s/cuirass_1.nif"          # its cp1252 reading, lowercased
OLD_KEY = "armor/elf\x92s/cuirass_1.nif"         # its latin-1 reading


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_ARMA_PATH_BYTES", raising=False)


def _archive(root: Path, folder_byte: str = "\x92") -> Path:
    """A mod folder whose archive holds meshes\\armor\\elf<byte>s\\cuirass_1.nif.
    The writer encodes names latin-1, so '\\x92' is the byte 0x92."""
    mod = root / "Mod"
    _write_bsa(mod / "Mod.bsa", [(f"meshes\\armor\\elf{folder_byte}s",
                                  "cuirass_1.nif", b"MESH", None)])
    return mod


def _armature_key(raw: bytes) -> str:
    """The key the coverage step asks the archive index about for a path."""
    return up._model_path_str(raw, True).replace("\\", "/").lower()


# --- the archive reader --------------------------------------------------------

def test_an_archive_name_reads_as_the_game_reads_it(tmp_path):
    arch = bs.BSAArchive(_archive(tmp_path) / "Mod.bsa")
    assert arch.list_files() == ["meshes/" + KEY]
    assert arch.read_file("meshes/" + KEY) == b"MESH"


def test_the_armature_path_finds_the_archived_mesh(tmp_path):
    idx = ac._BsaMeshIndex([_archive(tmp_path)], None)
    assert _armature_key(RAW) == KEY
    assert idx.contains(KEY)
    assert idx.read_bytes(KEY) == b"MESH"


def test_a_byte_cp1252_leaves_undefined_still_matches(tmp_path):
    """0x81 has no cp1252 character: both sides keep it as the same escape."""
    idx = ac._BsaMeshIndex([_archive(tmp_path, "\x81")], None)
    key = _armature_key(b"armor\\elf\x81s\\cuirass_1.nif")
    assert idx.contains(key) and idx.read_bytes(key) == b"MESH"


def test_switched_off_the_names_read_latin1_again(monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    arch = bs.BSAArchive(_archive(tmp_path) / "Mod.bsa")
    assert arch.list_files() == ["meshes/" + OLD_KEY]
    idx = ac._BsaMeshIndex([tmp_path / "Mod"], None)
    assert not idx.contains(KEY) and idx.contains(OLD_KEY)


# --- the ARMA readers of our own plugin ------------------------------------------

def test_a_path_is_read_back_as_arma_path_bytes_wrote_it(monkeypatch):
    assert up._model_path_read(RAW + b"\x00") == "armor\\elf’s\\cuirass_1.nif"
    # With #arma-path-bytes off the paths were written UTF-8: read as UTF-8.
    monkeypatch.setenv("CBBE2UBE_NO_ARMA_PATH_BYTES", "1")
    utf8 = "armor\\elf’s\\cuirass_1.nif".encode("utf-8") + b"\x00"
    assert up._model_path_read(utf8) == "armor\\elf’s\\cuirass_1.nif"


def _plugin(out: Path, sig: bytes, extra: bytes = b"") -> Path:
    payload = (esp.encode_subrecord(b"EDID", b"ElfAA\x00")
               + esp.encode_subrecord(sig, b"!UBE\\" + RAW + b"\x00") + extra)
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                     version_unk=0x002C, payload=payload)
    plugin = out / "Combined.esp"
    out.mkdir(parents=True, exist_ok=True)
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(plugin)
    return plugin


def _our_nif(out: Path) -> Path:
    p = out / "meshes" / "!UBE" / "armor" / "elf’s" / "cuirass_1.nif"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _missing(warns):
    return [w for w in warns if w.startswith("missing-nif")]


def test_the_postflight_finds_our_nif_at_such_a_path(tmp_path):
    out = tmp_path / "out"
    _our_nif(out)
    plugin = _plugin(out, b"MOD3")
    assert not _missing(up.validate_patch(plugin, out / "meshes"))


def test_switched_off_the_postflight_misses_it(monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    out = tmp_path / "out"
    _our_nif(out)
    plugin = _plugin(out, b"MOD3")
    assert _missing(up.validate_patch(plugin, out / "meshes"))


def _reconcile_setup(monkeypatch, tmp_path):
    out = tmp_path / "out"
    nif = out / "meshes" / "!UBE" / "armor" / "elf’s" / "cuirass_1.nif"
    build_skinned_shapes_nif(nif, [(nm, *uv_sphere(10.0 + i))
                                   for i, nm in enumerate(["hood", "Skirt"])])
    # The game's copy is never the answer here: our own NIF is on disk.
    monkeypatch.setattr(up, "_loaded_mesh_lookup",
                        lambda meshes_root: (lambda model: None))
    plugin = _plugin(out, b"MOD3", esp.encode_subrecord(
        b"MO3S", _alt([("Skirt", RED, 7), ("hood", TAN, 3)])))
    return out, plugin


def _mo3s(plugin: Path):
    payload = esp.ESP.load(plugin).groups[0].records[0].payload
    return _parse(next(d for s, d in esp.iter_subrecords(payload) if s == b"MO3S"))


@needs_pynifly
def test_the_reconcile_finds_our_nif_at_such_a_path(monkeypatch, tmp_path):
    out, plugin = _reconcile_setup(monkeypatch, tmp_path)
    assert up.reconcile_alt_texture_indices(plugin, out / "meshes") == 1
    assert _mo3s(plugin) == [("Skirt", RED, 1), ("hood", TAN, 0)]


@needs_pynifly
def test_switched_off_the_reconcile_misses_it(monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    out, plugin = _reconcile_setup(monkeypatch, tmp_path)
    before = plugin.read_bytes()
    assert up.reconcile_alt_texture_indices(plugin, out / "meshes") == 0
    assert plugin.read_bytes() == before
