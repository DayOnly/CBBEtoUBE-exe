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

r"""#model-path-codepage, round 3 -- the readers that DECIDE something, one by
one, and the writer that must agree with the one decoder.

THE READERS. Round 2 routed every model-path reader through
`bsa_strings.model_path_text`, but most sites had no test: putting a site's old
UTF-8 read back (which drops a byte such as 0x92, a curly apostrophe) passed
every test. Each row of the table below runs the public step a site lives in,
with a model path holding 0x92, and asks for the outcome that site decides:
  patch-emit    generate_ube_patch's source scan -- whether a UBE armature is
                emitted at all (no converted mesh found -> none);
  planner-male  the planner's male list -- the mesh planned for conversion;
  body-admit    the body coverage pass's admit test (`_arma_models`);
  body-world    the body pass's world-mesh test (`_world_mesh_converted`);
  twin          the twin report (`_ube_twin_slots`) the piece validator trusts;
  hand-slot     fix_spurious_hand_slot -- whether a stray Hands slot is cleared.
Each row also runs with CBBE2UBE_NO_MODEL_PATH_CODEPAGE=1, where the site gets
its old UTF-8 read back and must MISS the path -- so a row that stays green
under a revert is a row that never exercised its site.

THE WRITER. `rebuild_arma_payload` writes a dead female slot's male path "as it
is" (#coverage-female-standin) from the text the one decoder read. With only
CBBE2UBE_NO_ARMA_PATH_BYTES=1 set it wrote that text as strict UTF-8, which
raises on a byte cp1252 leaves undefined (0x81 decodes to a lone surrogate).
Now the male path and a stand-in go back through the decoder's own codec
(#model-path-writer), and no writer raises on such a byte.
"""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, esp, ube_patcher as up         # noqa: E402

OFF = "CBBE2UBE_NO_MODEL_PATH_CODEPAGE"
PB_OFF = "CBBE2UBE_NO_ARMA_PATH_BYTES"
DEFAULT_RACE = 0x00000019
BODY = 1 << 2
HEAD = 1 << 0


def _bits(*slots):
    v = 0
    for s in slots:
        v |= 1 << (s - 30)
    return v


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, PB_OFF):
        monkeypatch.delenv(k, raising=False)


def _raw(name: str) -> bytes:
    """An armature path whose folder holds 0x92."""
    return b"armor\\elf\x92s\\" + name.encode("ascii")


def _key(raw: bytes) -> str:
    """The planner's converted-set key for `raw`: the game's text, forward
    slashes, lower case."""
    return raw.decode("cp1252").replace("\\", "/").lower()


def _payload(models: dict, slots: int, edid: bytes = b"PieceAA") -> bytes:
    p = (esp.encode_subrecord(b"EDID", edid + b"\x00")
         + esp.encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
         + esp.encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE)))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += esp.encode_subrecord(sig, models[sig] + b"\x00")
    return p


def _save(path, masters, groups):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    esp.ESP(header=esp.TES4Header(masters=masters, num_records=0,
                                  next_object_id=0x900, version=1.7, flags=0),
            groups=groups).save(path)
    return Path(path)


def _mod_plugin(path, models: dict, slots: int, with_armo=True):
    """A plugin with one DefaultRace armature (0x800) and, optionally, the
    armour that lists it (0x801)."""
    arma = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                      version_unk=0x2C, payload=_payload(models, slots)
                      + esp.encode_subrecord(b"MODL", struct.pack("<I", DEFAULT_RACE)))
    groups = [esp.Group(label=b"ARMA", records=[arma])]
    if with_armo:
        q = (esp.encode_subrecord(b"EDID", b"Piece\x00")
             + esp.encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
             + esp.encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
             + esp.encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
             + esp.encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0)))
        groups.append(esp.Group(label=b"ARMO", records=[esp.Record(
            sig=b"ARMO", flags=0, formid=0x01000801, timestamp_vc=0,
            version_unk=0x2C, payload=q)]))
    return _save(path, ["Skyrim.esm"], groups)


def _masters(tmp_path):
    return [_save(tmp_path / "Skyrim.esm", [], []),
            _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])]


def _arma_models(plugin: Path) -> "list[dict]":
    if not plugin.is_file():
        return []
    return [{s: d for s, d in esp.iter_subrecords(r.payload)
             if s in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")}
            for g in esp.ESP.load(plugin).groups if g.label == b"ARMA"
            for r in g.records]


# --- the table: one row per deciding reader ------------------------------------------

def _patch_emit(tmp_path, monkeypatch):
    """generate_ube_patch emits a UBE armature only when the source scan finds
    one of its meshes in the converted set."""
    raw = _raw("cuirass_1.nif")
    _masters(tmp_path)
    src = _mod_plugin(tmp_path / "Elf" / "Elf.esp", {b"MOD3": raw}, BODY)
    out = tmp_path / "Elf UBE patch.esp"
    up.generate_ube_patch(src, out, master_data_dirs=[tmp_path],
                          converted_rel_paths={_key(raw)})
    return [m.get(b"MOD3") for m in _arma_models(out)], \
        [b"!UBE\\" + raw + b"\x00"]


def _planner_male(tmp_path, monkeypatch):
    """A male-only armature's MOD2 is the mesh the planner converts."""
    raw = _raw("cuirass_1.nif")
    mod = tmp_path / "Elf"
    _mod_plugin(mod / "Elf.esp", {b"MOD2": raw}, BODY, with_armo=False)
    return (sorted(ac._player_armor_mesh_bases(mod, include_candidate_slots=True)),
            [_key(raw)[:-len("_1.nif")]])


def _body_pass(tmp_path, models, conv, slots=BODY):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    world = _masters(tmp_path) + [_mod_plugin(tmp_path / "Mod.esp", models, slots)]
    up.generate_modded_body_ube_coverage_patch(
        out, world, converted_rel_paths=conv, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True)
    return [m.get(b"MOD3") for m in _arma_models(out)]


def _body_admit(tmp_path, monkeypatch):
    """The body pass admits a forearms armature (slot 34: deforming, neither
    torso nor hands/feet) only when one of its models (`_arma_models`) was
    converted."""
    raw = _raw("bracers_1.nif")
    return (_body_pass(tmp_path, {b"MOD3": raw}, {_key(raw)}, _bits(34)),
            [b"!UBE\\" + raw + b"\x00"])


def _body_world(tmp_path, monkeypatch):
    """A torso armature is minted only when the female WORLD mesh it draws was
    converted (`_world_mesh_converted`); its first-person mesh (ASCII, also
    converted) admits it either way."""
    m3 = _raw("cuirass_1.nif")
    m5 = b"armor\\elf\\1stcuirass_1.nif"
    got = _body_pass(tmp_path, {b"MOD3": m3, b"MOD5": m5}, {_key(m3), _key(m5)})
    return got, [b"!UBE\\" + m3 + b"\x00"]


def _twin(tmp_path, monkeypatch):
    """The non-body pass reports each slot pointed at another mod's hand-made
    UBE twin (`_ube_twin_slots`); the piece validator whitelists exactly the
    reported path."""
    raw = _raw("circlet_1.nif")
    text = raw.decode("cp1252")
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    world = _masters(tmp_path) + [_mod_plugin(tmp_path / "Mod.esp", {b"MOD3": raw}, HEAD)]
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, world, converted_rel_paths=set(), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True,
        ube_twin_exists=lambda p: "A UBE Patch" if p.lower() == text.lower() else None)
    return [d["path"] for d in st["ube_twin"]], ["!UBE\\" + text]


def _hand_slot(tmp_path, monkeypatch):
    """A converted vambrace armature tagged Hands + Forearms over a handless mesh
    loses its Hands bit -- only when fix_spurious_hand_slot finds the mesh."""
    raw = b"!UBE\\" + _raw("vambraces_1.nif")
    mesh = tmp_path / "meshes" / Path(raw.decode("cp1252").replace("\\", "/"))
    mesh.parent.mkdir(parents=True, exist_ok=True)
    mesh.write_bytes(b"\x00")
    monkeypatch.setattr(up, "_nif_max_hand_weight_fraction",
                        lambda p: 0.0 if Path(p) == mesh else None)
    up._HAND_WEIGHT_FRAC_CACHE.clear()
    arma = esp.Record(sig=b"ARMA", flags=0, formid=0x801, timestamp_vc=0,
                      version_unk=0x2C,
                      payload=_payload({b"MOD3": raw}, _bits(33, 34), b"VambAA"))
    combined = _save(tmp_path / "CBBE_to_UBE_Combined.esp", ["Skyrim.esm"],
                     [esp.Group(label=b"ARMA", records=[arma])])
    up.fix_spurious_hand_slot(combined, tmp_path / "meshes")
    slots = [struct.unpack_from("<I", d)[0]
             for g in esp.ESP.load(combined).groups if g.label == b"ARMA"
             for r in g.records for s, d in esp.iter_subrecords(r.payload)
             if s == b"BOD2"]
    return slots, [_bits(34)]


SITES = {
    "patch-emit": _patch_emit,
    "planner-male": _planner_male,
    "body-admit": _body_admit,
    "body-world": _body_world,
    "twin": _twin,
    "hand-slot": _hand_slot,
}


@pytest.mark.parametrize("site", list(SITES))
def test_each_deciding_reader_finds_a_path_with_such_a_byte(
        site, tmp_path, monkeypatch):
    got, want = SITES[site](tmp_path, monkeypatch)
    assert got == want


@pytest.mark.parametrize("site", list(SITES))
def test_switched_off_each_reader_drops_the_byte_and_misses_it(
        site, tmp_path, monkeypatch):
    """The control: each row really runs its site's decode."""
    monkeypatch.setenv(OFF, "1")
    got, want = SITES[site](tmp_path, monkeypatch)
    assert got != want


# --- the writer ------------------------------------------------------------------------

M2 = b"armor\\x\x81y\\m_0.nif"          # 0x81: a byte cp1252 leaves undefined


def _dead_female_as_is(mod2: bytes, mod3: bytes, standin=None) -> dict:
    """MOD2 not converted, MOD3 dead, a non-body piece: the dead female slot
    draws the male path as it is (or `standin`, when given)."""
    payload = (esp.encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
               + esp.encode_subrecord(b"MOD2", mod2 + b"\x00")
               + esp.encode_subrecord(b"MOD3", mod3 + b"\x00"))
    log: list = []
    out = up.rebuild_arma_payload(
        payload, new_primary_rnam=DEFAULT_RACE, new_additional_race_fids=[],
        converted_nif_exists=lambda p: False, keep_named_female=True,
        declined_log=log, female_mesh_exists=lambda p: False,
        female_standin=lambda s, m: standin,
        dead_female_male_as_is=lambda m: True)
    got = {s: d for s, d in esp.iter_subrecords(out) if s in (b"MOD2", b"MOD3")}
    got["log"] = [sorted(d) for d in log]
    return got


@pytest.mark.parametrize("mod2", [M2, b"armor\\elf\x92s\\m_0.nif"],
                         ids=["undefined-0x81", "apostrophe-0x92"])
def test_the_male_path_as_it_is_keeps_its_bytes_with_arma_path_bytes_off(
        monkeypatch, mod2):
    """The exact failing input: MOD2 = MOD3, the female slot dead, the male path
    drawn as it is, only CBBE2UBE_NO_ARMA_PATH_BYTES=1 set. It raised
    UnicodeEncodeError on 0x81; the male path is now the source's bytes."""
    monkeypatch.setenv(PB_OFF, "1")
    got = _dead_female_as_is(mod2, mod2)
    assert got[b"MOD3"] == mod2 + b"\x00"
    assert got["log"] == [["male_as_is", "orig", "slot"]]


def test_at_defaults_the_male_path_as_it_is_keeps_its_bytes():
    assert _dead_female_as_is(M2, M2)[b"MOD3"] == M2 + b"\x00"


def test_with_the_old_reads_the_male_path_as_it_is_still_keeps_its_bytes(
        monkeypatch):
    """Only CBBE2UBE_NO_MODEL_PATH_CODEPAGE=1: the lookups read the male path
    with cp1252 `replace` (0x81 lost), but #arma-path-bytes still writes the
    bytes the source had, not that text."""
    monkeypatch.setenv(OFF, "1")
    assert _dead_female_as_is(M2, M2)[b"MOD3"] == M2 + b"\x00"


def test_with_both_switches_set_the_male_path_is_written_as_before(monkeypatch):
    """Both switches set: the old cp1252 `replace` read, written as UTF-8 -- the
    parent's bytes."""
    monkeypatch.setenv(PB_OFF, "1")
    monkeypatch.setenv(OFF, "1")
    got = _dead_female_as_is(M2, M2)
    assert got[b"MOD3"] == b"armor\\x\xef\xbf\xbdy\\m_0.nif\x00"
    assert got[b"MOD2"] == b"armor\\xy\\m_0.nif\x00"   # the UTF-8 round trip
    got = _dead_female_as_is(b"armor\\elf\x92s\\m_0.nif", b"armor\\elf\x92s\\m_0.nif")
    assert got[b"MOD3"] == b"armor\\elf\xe2\x80\x99s\\m_0.nif\x00"


def test_a_stand_in_read_by_the_one_decoder_is_written_back(monkeypatch):
    """A stand-in comes from the vanilla armatures, read by the one decoder; it
    is written through the same codec with #arma-path-bytes off too."""
    monkeypatch.setenv(PB_OFF, "1")
    got = _dead_female_as_is(M2, M2, standin="armor\\x\udc81y\\elf’s_0.nif")
    assert got[b"MOD3"] == b"!UBE\\armor\\x\x81y\\elf\x92s_0.nif\x00"
    assert got["log"] == [["orig", "slot", "standin"]]


def test_the_utf8_writer_gives_an_undefined_byte_back_and_nothing_else_moves():
    assert up._model_path_zstring("a\\x\udc81y.nif", False) == b"a\\x\x81y.nif\x00"
    assert up._model_path_zstring("a\\elf’s.nif", False) == \
        b"a\\elf\xe2\x80\x99s.nif\x00", "any other text as before"
    assert up._model_path_zstring("a\\elf’s.nif", True) == b"a\\elf\x92s.nif\x00"
