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

r"""#model-path-codepage, round 2 -- EVERY reader of an armature's model path
reads it through one decoder, in the game's codepage.

THE DEFECT. Round 1 made the archive reader, the alt-texture reconcile and the
postflight read cp1252, but the planner (`_player_armor_mesh_bases`) and about
a dozen coverage and patch readers still decoded MOD2-5 as UTF-8 with errors
ignored: a byte such as 0x92 (a curly apostrophe) was dropped before any
lookup, so an armour whose mesh is named with one was planned under a path
that exists nowhere and never converted. Others read cp1252 with `replace`,
which loses the five bytes cp1252 leaves undefined. All of them now read
through `bsa_strings.model_path_text`; `CBBE2UBE_NO_MODEL_PATH_CODEPAGE=1`
gives each its old decode back.

The postflight's `unconverted-mesh-linked` row (build-failing) reads the same
way, so it can now see a source path with such a byte left beside our
converted NIF -- the armour really wears the unconverted mesh there.
"""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, bsa_strings as bs, esp        # noqa: E402
from src import stale_sweep as ss, ube_patcher as up                # noqa: E402
from tests.test_bsa_seek_read import _write_bsa                     # noqa: E402

OFF = "CBBE2UBE_NO_MODEL_PATH_CODEPAGE"
RAW = b"armor\\elf\x92s\\cuirass_1.nif"      # the armature's bytes
BASE = "armor/elf’s/cuirass"            # the planner's key, cp1252
DROPPED = "armor/elfs/cuirass"                # the old UTF-8 read
DEFAULT_RACE = 0x00000019
BODY = 1 << 2


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, "CBBE2UBE_NO_ARMA_PATH_BYTES"):
        monkeypatch.delenv(k, raising=False)


# --- the one decoder ---------------------------------------------------------------

def test_the_one_codec_gives_back_the_very_bytes():
    for raw in (RAW, b"armor\\x\x81y\\a_1.nif", b"armor\\caf\xe9\\a_1.nif"):
        txt = bs.game_codepage_text(raw)
        assert txt.encode("cp1252", "surrogateescape") == raw
    assert bs.game_codepage_text(b"\x81") == "\udc81", "undefined byte kept"
    assert bs.game_codepage_text(b"\x92") == "’"


def test_every_old_decode_reads_the_game_s_text():
    for legacy in ("utf-8", "cp1252", "latin-1"):
        assert bs.model_path_text(RAW + b"\x00", legacy) == \
            "armor\\elf’s\\cuirass_1.nif"


@pytest.mark.parametrize("legacy,old", [
    ("utf-8", "armor\\elfs\\cuirass_1.nif"),
    ("cp1252", "armor\\elf’s\\cuirass_1.nif"),
    ("latin-1", "armor\\elf\x92s\\cuirass_1.nif"),
], ids=["utf-8", "cp1252", "latin-1"])
def test_switched_off_each_reader_gets_its_old_decode(monkeypatch, legacy, old):
    monkeypatch.setenv(OFF, "1")
    assert bs.model_path_text(RAW + b"\x00", legacy) == old
    # cp1252 with `replace` lost a byte cp1252 leaves undefined.
    if legacy == "cp1252":
        assert bs.model_path_text(b"a\x81\x00", legacy) == "a�"


# --- planned, found in the archive, extracted, slotted -----------------------------

def _arma(fid, model: bytes, slot=BODY):
    payload = (esp.encode_subrecord(b"EDID", b"ElfCuirassAA\x00")
               + esp.encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + esp.encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
               + esp.encode_subrecord(b"MOD3", model + b"\x00"))
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x2C, payload=payload)


def _source_mod(tmp_path) -> Path:
    """A mod whose plugin names the mesh with 0x92 and whose archive holds
    it under the same byte; no loose mesh anywhere."""
    mod = tmp_path / "Elf Armour"
    mod.mkdir()
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[_arma(0x01000800, RAW)])]
            ).save(mod / "ElfArmour.esp")
    _write_bsa(mod / "Elf Armour.bsa", [("meshes\\armor\\elf\x92s",
                                         "cuirass_1.nif", b"MESH", None)])
    return mod


def _plan_and_extract(monkeypatch, tmp_path):
    mod = _source_mod(tmp_path)
    bases = ac._player_armor_mesh_bases(mod, include_candidate_slots=True)
    monkeypatch.setattr(ac, "_BATCH_BSA_INDEX",
                        ac._BsaMeshIndex([mod], tmp_path / "out" / "_bsa_staging"))
    pairs = ac._resolve_armor_meshes(bases, None, None, [])
    slots = up.build_nif_slot_map([mod / "ElfArmour.esp"])
    return bases, pairs, slots


def test_a_path_with_such_a_byte_is_planned_extracted_and_slotted(
        monkeypatch, tmp_path):
    bases, pairs, slots = _plan_and_extract(monkeypatch, tmp_path)
    assert bases == {BASE}
    assert len(pairs) == 1
    src, rel = pairs[0]
    assert Path(src).read_bytes() == b"MESH", "extracted from the archive"
    assert rel.lower() == BASE + "_1.nif"
    # The crash guard's slot lookup finds the body slot under the same key.
    assert ac._make_slot_resolver(slots)(rel) & BODY


def test_switched_off_the_planner_drops_the_byte_and_finds_nothing(
        monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    bases, pairs, slots = _plan_and_extract(monkeypatch, tmp_path)
    assert bases == {DROPPED}
    assert pairs == []


# --- a coverage reader and the stale sweep's reader -----------------------------

def test_the_coverage_readers_see_the_game_s_path():
    payload = _arma(0x01000800, RAW).payload
    assert up._arma_model_paths(payload) == ["armor\\elf’s\\cuirass_1.nif"]


def test_the_sweep_reads_our_ube_paths_losslessly(monkeypatch, tmp_path):
    plugin = tmp_path / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[
                _arma(0x01000800, b"!UBE\\armor\\x\x81y\\a_1.nif")])]
            ).save(plugin)
    assert [m for m, _b in ss._ube_models(plugin)] == ["!UBE\\armor\\x\udc81y\\a_1.nif"]
    monkeypatch.setenv(OFF, "1")
    assert [m for m, _b in ss._ube_models(plugin)] == ["!UBE\\armor\\x�y\\a_1.nif"]


# --- the postflight row that can now fire ---------------------------------------------

def _left_unredirected(tmp_path) -> Path:
    """Our converted NIF exists, but the Combined still names the SOURCE path
    (the redirect missed it)."""
    out = tmp_path / "out"
    nif = out / "meshes" / "!UBE" / "armor" / "elf’s" / "cuirass_1.nif"
    nif.parent.mkdir(parents=True, exist_ok=True)
    nif.write_bytes(b"x")
    plugin = out / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[_arma(0x01000800, RAW)])]
            ).save(plugin)
    return plugin


def _unconverted(warns):
    return [w for w in warns if w.startswith("unconverted-mesh-linked")]


def test_a_source_path_beside_our_converted_nif_fails_the_postflight(tmp_path):
    plugin = _left_unredirected(tmp_path)
    warns = up.validate_patch(plugin, plugin.parent / "meshes")
    assert len(_unconverted(warns)) == 1
    assert "elf’s" in _unconverted(warns)[0]
    res = up.postflight_validate_combined(plugin, plugin.parent / "meshes")
    assert [w.split(":", 1)[0] for _p, w in res["ctd"]] == ["unconverted-mesh-linked"]


def test_switched_off_the_postflight_cannot_see_it(monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    plugin = _left_unredirected(tmp_path)
    assert not _unconverted(up.validate_patch(plugin, plugin.parent / "meshes"))
