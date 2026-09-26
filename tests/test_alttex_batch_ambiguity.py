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

r"""#alttex-batch-ambiguity -- the reconcile decides once, for the whole merged
plugin, whether a converted NIF's name was shared; and a NIF carrying a name
twice has its source read.

THE DEFECTS. (1) `reconcile_alt_texture_indices_all` reconciled each ESL-split
piece (`<stem>.esp`, `<stem>2.esp`) on its own. A set in one piece repeating
'fur' proves the NIF's 'fur' was shared, but a set in the OTHER piece naming
'fur' once read no source and bound by name: a lost shell's colour landed on
the surviving 'fur'. (2) A converted NIF carrying two literal 'fur' shapes (a
group the rename kept as authored) read no source unless a set repeated the
name, so a set naming 'fur' once put its colour on the LAST 'fur' shape.
`CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY=1` restores #alttex-case-provenance as it
first shipped.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import esp, ube_patcher as up                           # noqa: E402
from tests.synthetic_nif import build_skinned_shapes_nif          # noqa: E402
from tests.test_alttex_exact_provenance import (                 # noqa: E402
    BLUE, GREEN, RED, TAN, _alt, _parse, _resolve_to)
from tests.test_alttex_set_provenance import (                   # noqa: E402
    BODY, COAT, KEY, LOST_CONV, LOST_SET, LOST_SRC, REL, SHELL1, SHELL2,
    SWITCHES, _reconciled, _world, needs_pynifly)

BATCH_OFF = "CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY"
assert BATCH_OFF in SWITCHES


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


MODEL = b"!UBE\\" + REL.encode() + b"\x00"


def _piece(path, fid, *sets):
    """One plugin (one ESL-split piece) with one ARMA on the NIF whose MO2S,
    MO3S, ... carry `sets`."""
    payload = esp.encode_subrecord(b"EDID", b"CoatAA\x00")
    for n, entries in zip((b"2", b"3", b"4", b"5"), sets):
        payload += (esp.encode_subrecord(b"MOD" + n, MODEL)
                    + esp.encode_subrecord(b"MO" + n + b"S", _alt(entries)))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                     version_unk=0x002C, payload=payload)
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(path)


def _sets(path):
    payload = esp.ESP.load(path).groups[0].records[0].payload
    return [_parse(d) for s, d in esp.iter_subrecords(payload)
            if s in (b"MO2S", b"MO3S", b"MO4S", b"MO5S")]


def _two_pieces(tmp_path, first, second):
    """Source coat, fur, fur (shipped coat, fur, fur:1); the converted NIF
    lost 'fur:1'. The merged plugin is split in two pieces, each with one ARMA
    on that NIF."""
    src = build_skinned_shapes_nif(tmp_path / "src" / "coat_1.nif", LOST_SRC)
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             LOST_CONV)
    out = tmp_path / "out"
    _piece(out / "Combined.esp", 0x01000800, first)
    _piece(out / "Combined2.esp", 0x01000801, second)
    return src, out


# --- 1. a repeat in one piece makes a single entry in another ambiguous -----

@needs_pynifly
@pytest.mark.parametrize("found", [True, False])
def test_a_repeat_in_another_piece_makes_a_single_entry_ambiguous(
        monkeypatch, tmp_path, found):
    # Combined.esp repeats 'fur'; Combined2.esp names it once, for the LOST
    # shell 2 (RED). By name RED landed on the surviving 'fur'.
    src, out = _two_pieces(tmp_path, [("fur", BLUE, 1), ("fur", BLUE, 2)],
                           [("fur", RED, 2), ("coat", TAN, 0)])
    seen = _resolve_to(monkeypatch, {KEY: src} if found else {})
    up.reconcile_alt_texture_indices_all(out / "Combined.esp", out / "meshes")
    assert _sets(out / "Combined2.esp") == [[("coat", TAN, 0)]], (
        "the lost shell's RED must not land on the surviving 'fur'")
    assert _sets(out / "Combined.esp") == ([[("fur", BLUE, 1)]] if found
                                           else [[]])
    assert seen == [[KEY]], "the source is looked up once for both pieces"


@needs_pynifly
def test_a_single_entry_in_the_first_piece_is_ambiguous_too(monkeypatch,
                                                            tmp_path):
    # The repeat is in the LATER piece: every piece is scanned first.
    src, out = _two_pieces(tmp_path, [("fur", RED, 2), ("coat", TAN, 0)],
                           [("fur", GREEN, 1), ("fur", BLUE, 2)])
    _resolve_to(monkeypatch, {KEY: src})
    up.reconcile_alt_texture_indices_all(out / "Combined.esp", out / "meshes")
    assert _sets(out / "Combined.esp") == [[("coat", TAN, 0)]]
    assert _sets(out / "Combined2.esp") == [[("fur", GREEN, 1)]]


@needs_pynifly
@pytest.mark.parametrize("found", [True, False])
def test_switched_off_each_piece_is_reconciled_on_its_own(monkeypatch,
                                                          tmp_path, found):
    monkeypatch.setenv(BATCH_OFF, "1")
    src, out = _two_pieces(tmp_path, [("fur", BLUE, 1), ("fur", BLUE, 2)],
                           [("fur", RED, 2), ("coat", TAN, 0)])
    _resolve_to(monkeypatch, {KEY: src} if found else {})
    up.reconcile_alt_texture_indices_all(out / "Combined.esp", out / "meshes")
    assert _sets(out / "Combined2.esp") == [[("fur", RED, 1),
                                             ("coat", TAN, 0)]], (
        "#alttex-case-provenance as first shipped: the known wrong shell")


@needs_pynifly
def test_a_single_piece_reconciles_as_one_plugin_did(monkeypatch, tmp_path):
    # One piece only: the batch view is that plugin's own, so the result is
    # the per-plugin reconcile's, with the switch set or not.
    got = {}
    for off in (False, True):
        if off:
            monkeypatch.setenv(BATCH_OFF, "1")
        base = tmp_path / str(off)
        src, plugin = _world(base, LOST_SRC, LOST_CONV,
                             [("fur", BLUE, 1), ("fur", BLUE, 2)],
                             [("fur", RED, 2), ("coat", TAN, 0)],
                             [("fur", GREEN, 1), ("Coat", TAN, 0)])
        _resolve_to(monkeypatch, {KEY: src})
        assert up.reconcile_alt_texture_indices_all(
            plugin, base / "out" / "meshes") == 1
        got[off] = plugin.read_bytes()
        assert _sets(plugin) == [[("fur", BLUE, 1)], [("coat", TAN, 0)],
                                 [("fur", GREEN, 1), ("Coat", TAN, 0)]]
    assert got[False] == got[True]


@needs_pynifly
def test_every_piece_is_reconciled_and_saved(monkeypatch, tmp_path):
    # Combined.esp's 'coat' entry carries a stale index; Combined2.esp's
    # set repeats 'fur'. Both pieces change and are saved.
    src, out = _two_pieces(tmp_path, [("coat", TAN, 5), ("fur", GREEN, 1)],
                           LOST_SET)
    _piece(out / "Unrelated.esp", 0x01000802, [("fur", RED, 2)])
    before = (out / "Unrelated.esp").read_bytes()
    _resolve_to(monkeypatch, {KEY: src})
    assert up.reconcile_alt_texture_indices_all(out / "Combined.esp",
                                                out / "meshes") == 2
    assert _sets(out / "Combined.esp") == [[("coat", TAN, 0),
                                            ("fur", GREEN, 1)]]
    assert _sets(out / "Combined2.esp") == [[("fur", GREEN, 1),
                                             ("coat", TAN, 0)]]
    assert (out / "Unrelated.esp").read_bytes() == before


@needs_pynifly
def test_a_piece_that_does_not_load_raises_after_the_others_are_saved(
        monkeypatch, tmp_path):
    src, out = _two_pieces(tmp_path, LOST_SET, [("coat", TAN, 0)])
    (out / "Combined2.esp").write_bytes(b"not a plugin")
    _resolve_to(monkeypatch, {KEY: src})
    with pytest.raises(Exception):
        up.reconcile_alt_texture_indices_all(out / "Combined.esp",
                                             out / "meshes")
    assert _sets(out / "Combined.esp") == [[("fur", GREEN, 1),
                                            ("coat", TAN, 0)]]


# --- 2. a NIF carrying a name twice has its source read ---------------------

KEPT_CONV = [("coat", *COAT), ("fur", *SHELL1), ("fur", *SHELL2),
             ("BaseShape", *BODY)]
# 'fur' named once, for shell 1: by name it landed on the LAST 'fur' (2).
ONCE_SET = [("fur", GREEN, 1), ("coat", TAN, 0)]


def _physics_keeps_fur(monkeypatch):
    # The source's physics XML names 'fur': the rename keeps both shells.
    from src import nif_convert_physics as phys
    monkeypatch.setattr(phys, "_read_source_hdt_xml_text",
                        lambda *a, **k: '<per-triangle-shape name="fur">')


def test_literal_duplicate_names_are_the_names_carried_twice():
    assert up._literal_duplicate_names(["fur", "fur", "coat"]) == {"fur"}
    assert up._literal_duplicate_names(["Fur", "fur", "coat"]) == frozenset(), (
        "two spellings are a case variant, not a literal duplicate")
    assert up._literal_duplicate_names(["", "", "coat"]) == frozenset()


@needs_pynifly
@pytest.mark.parametrize("found", [True, False])
def test_a_name_the_nif_carries_twice_reads_the_source(monkeypatch, tmp_path,
                                                       found):
    _physics_keeps_fur(monkeypatch)
    src, plugin = _world(tmp_path, LOST_SRC, KEPT_CONV, ONCE_SET)
    seen = _resolve_to(monkeypatch, {KEY: src} if found else {})
    assert _reconciled(plugin, tmp_path) == (
        [[("fur", GREEN, 1), ("coat", TAN, 0)]] if found
        else [[("coat", TAN, 0)]]), (
        "by name GREEN landed on the last 'fur', the other shell")
    assert seen == [[KEY]]


@needs_pynifly
def test_switched_off_a_name_carried_twice_binds_by_name(monkeypatch,
                                                         tmp_path):
    monkeypatch.setenv(BATCH_OFF, "1")
    _physics_keeps_fur(monkeypatch)
    src, plugin = _world(tmp_path, LOST_SRC, KEPT_CONV, ONCE_SET)
    seen = _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 2),
                                              ("coat", TAN, 0)]]
    assert seen == []
