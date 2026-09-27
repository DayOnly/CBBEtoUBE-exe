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

r"""#alttex-dup-occurrence -- a colour variant keeps every same-named shell.

THE DEFECT. #dup-shape-names ships a source whose shells share a name as
'fur', 'fur:1', 'fur:2' ... in the author's order. The source plugin's colour
variant addresses those shells by 3D index, every entry still named 'fur'. Our
own plugin's alternate-texture reconcile matched entries by name and kept one
per name: the variant kept ONE entry, bound to the first shell, and the other
shells lost their colour. Now the k-th entry (by source 3D index) of a split
name binds to the shape named 'name:k' in the converted NIF.
`CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE=1` keeps one entry per name; with
`CBBE2UBE_NO_DUP_SHAPE_NAMES=1` it is off too.

#alttex-family-strict: only a family laid out exactly as the rename lays it
out ('name', 'name:1' .. 'name:n', consecutive, in that NIF order) and a set
naming each of its shells once bind; anything else keeps one entry per name.
`CBBE2UBE_NO_ALTTEX_FAMILY_STRICT=1` binds any 'name' beside 'name:k' by rank.

Both bind by the converted NIF's LAYOUT, which is not exact (a lost trailing
shell passes the checks) and since #alttex-exact-provenance runs only with
`CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE=1`; the default reads the source mesh
(tests/test_alttex_exact_provenance.py). This file pins that switched-off
path, so every test here runs with that switch set.
"""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import esp, ube_patcher as up                          # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,      # noqa: E402
                                 pynifly_available, uv_sphere)

OFF = "CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE"
DSN_OFF = "CBBE2UBE_NO_DUP_SHAPE_NAMES"
STRICT_OFF = "CBBE2UBE_NO_ALTTEX_FAMILY_STRICT"
EXACT_OFF = "CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE"

# A coat: shape 0 'coat', then three shells the author all named 'fur'; the
# converted NIF carries them renamed, in the same order, and our injected body
# after them.
CONVERTED = {"coat": 0, "fur": 1, "fur:1": 2, "fur:2": 3, "BaseShape": 4}
RED, GREEN, BLUE, TAN = 0x0A000001, 0x0A000002, 0x0A000003, 0x0A000004


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(DSN_OFF, raising=False)
    monkeypatch.delenv(STRICT_OFF, raising=False)
    monkeypatch.setenv(EXACT_OFF, "1")        # the layout path (see above)


def _alt(entries) -> bytes:
    """entries: [(name, txst, 3D index)] -> a raw MO?S payload."""
    out = struct.pack("<I", len(entries))
    for name, txst, index in entries:
        nm = name.encode("latin-1")
        out += struct.pack("<I", len(nm)) + nm + struct.pack("<II", txst, index)
    return out


def _parse(data: bytes):
    n = struct.unpack_from("<I", data, 0)[0]
    p = 4
    out = []
    for _ in range(n):
        nl = struct.unpack_from("<I", data, p)[0]; p += 4
        nm = data[p:p + nl].split(b"\x00", 1)[0].decode("latin-1"); p += nl
        tx, ix = struct.unpack_from("<II", data, p); p += 8
        out.append((nm, tx, ix))
    return out


def _colour_at(data: bytes) -> "dict[int, int]":
    """{converted 3D index: TXST} -- what the engine recolours."""
    return {ix: tx for _nm, tx, ix in _parse(data)}


THREE_SHELLS = [("coat", TAN, 0), ("fur", RED, 1), ("fur", GREEN, 2),
                ("fur", BLUE, 3)]


def test_each_same_named_shell_keeps_its_colour_variant():
    out = up._reindex_alt_texture_payload(_alt(THREE_SHELLS), CONVERTED)
    assert _colour_at(out) == {0: TAN, 1: RED, 2: GREEN, 3: BLUE}


def test_the_authored_names_are_kept():
    out = up._reindex_alt_texture_payload(_alt(THREE_SHELLS), CONVERTED)
    assert [nm for nm, _t, _i in _parse(out)] == ["coat", "fur", "fur", "fur"]


def test_entries_bind_in_source_index_order_not_set_order():
    listed = [("fur", BLUE, 3), ("fur", RED, 1), ("coat", TAN, 0),
              ("fur", GREEN, 2)]
    out = up._reindex_alt_texture_payload(_alt(listed), CONVERTED)
    assert _colour_at(out) == {0: TAN, 1: RED, 2: GREEN, 3: BLUE}


def test_a_moved_shell_family_follows_the_converted_order():
    # Our body first (a phase-2 build before BUG-09's fix): every shell +1.
    moved = {"BaseShape": 0, "coat": 1, "fur": 2, "fur:1": 3, "fur:2": 4}
    out = up._reindex_alt_texture_payload(_alt(THREE_SHELLS), moved)
    assert _colour_at(out) == {1: TAN, 2: RED, 3: GREEN, 4: BLUE}


def test_a_case_different_entry_joins_its_shells():
    entries = [("FUR", RED, 1), ("Fur", GREEN, 2), ("fur", BLUE, 3)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert _colour_at(out) == {1: RED, 2: GREEN, 3: BLUE}


def _as_before(monkeypatch, entries, nif):
    """What the reconcile made before #alttex-dup-occurrence: one entry per
    name."""
    monkeypatch.setenv(OFF, "1")
    try:
        return _parse(up._reindex_alt_texture_payload(_alt(entries), nif))
    finally:
        monkeypatch.delenv(OFF)


def test_an_authored_colon_name_the_set_names_is_matched_by_name(monkeypatch):
    # The author already had a 'fur:1', so the rename skipped it: the second
    # and third 'fur' became 'fur:2' and 'fur:3' -- 'fur', 'fur:2', 'fur:3'
    # is not the rename's own layout, so 'fur' keeps one entry.
    nif = {"fur": 0, "fur:1": 1, "fur:2": 2, "fur:3": 3}
    entries = [("fur", RED, 0), ("fur:1", TAN, 1), ("fur", GREEN, 2),
               ("fur", BLUE, 3)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _colour_at(out) == {0: RED, 1: TAN}
    assert _parse(out) == _as_before(monkeypatch, entries, nif)


def test_an_authored_colon_name_never_joins_a_family_of_the_same_size():
    # Four 'fur' shells beside the author's 'fur:1': renamed 'fur:2' ..
    # 'fur:4', and 'fur:4' lost in conversion. Counting the author's 'fur:1'
    # the NIF has four 'fur' shapes for the set's four 'fur' entries.
    nif = {"fur": 0, "fur:1": 1, "fur:2": 2, "fur:3": 3}
    entries = [("fur", RED, 0), ("fur:1", TAN, 1), ("fur", GREEN, 2),
               ("fur", BLUE, 3), ("fur", 0x0A000005, 4)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _colour_at(out) == {0: RED, 1: TAN}


def test_a_taken_name_that_looks_consecutive_falls_back():
    # Source shapes 'fur', 'fur:1' (the author's), 'fur': the rename made the
    # second 'fur' 'fur:2'. The NIF reads 'fur', 'fur:1', 'fur:2' -- the
    # rename's layout -- but the set names two 'fur' shells for three shapes,
    # so binding by rank would give the author's 'fur:1' the second colour.
    nif = {"fur": 0, "fur:1": 1, "fur:2": 2}
    entries = [("fur", RED, 0), ("fur", BLUE, 2)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("fur", RED, 0)]


def test_a_set_naming_only_some_shells_falls_back():
    entries = [("fur", RED, 1), ("fur", BLUE, 3)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert _parse(out) == [("fur", RED, 1)]


def test_a_repeated_source_index_keeps_one_entry():
    entries = [("fur", RED, 1), ("fur", GREEN, 1), ("fur", BLUE, 2),
               ("fur", TAN, 3)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert _parse(out) == [("fur", RED, 1), ("fur", BLUE, 2), ("fur", TAN, 3)]


def test_a_set_naming_more_shells_than_the_nif_falls_back():
    entries = [("fur", RED, 1), ("fur", GREEN, 2), ("fur", BLUE, 3),
               ("fur", TAN, 4)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert _parse(out) == [("fur", RED, 1)]


# --- #alttex-family-strict: only the rename's own layout binds -------------

SIX = [0x0A000010 + i for i in range(6)]
SIX_SHELLS = [("coat", TAN, 0)] + [("fur", SIX[i], 1 + i) for i in range(6)]
FULL_COAT = {"coat": 0, "fur": 1, "fur:1": 2, "fur:2": 3, "fur:3": 4,
             "fur:4": 5, "fur:5": 6}
# 'fur:2' failed to copy; the partial NIF shipped without it.
DROPPED_MIDDLE = {"coat": 0, "fur": 1, "fur:1": 2, "fur:3": 3, "fur:4": 4,
                  "fur:5": 5}


def test_a_consecutive_family_gives_each_shell_its_own_colour():
    out = up._reindex_alt_texture_payload(_alt(SIX_SHELLS), FULL_COAT)
    assert _colour_at(out) == {0: TAN, **{1 + i: SIX[i] for i in range(6)}}


def test_a_dropped_middle_shell_falls_back_without_shifting_colours(monkeypatch):
    out = up._reindex_alt_texture_payload(_alt(SIX_SHELLS), DROPPED_MIDDLE)
    assert _colour_at(out) == {0: TAN, 1: SIX[0]}
    assert _parse(out) == _as_before(monkeypatch, SIX_SHELLS, DROPPED_MIDDLE)


def test_a_gap_falls_back_even_when_the_set_names_as_many_shells():
    # The set recolours the first five of the six shells, and the NIF lost
    # 'fur:2': five entries for five shapes, yet the third entry is the lost
    # shell's and 'fur:3' is not in the set at all.
    entries = [("fur", SIX[i], 1 + i) for i in range(5)]
    out = up._reindex_alt_texture_payload(_alt(entries), DROPPED_MIDDLE)
    assert _parse(out) == [("fur", SIX[0], 1)]


@pytest.mark.parametrize("entries", [
    [("x", RED, 1)],
    [("x", RED, 0), ("x", GREEN, 1)],
], ids=["one-entry", "two-entries"])
def test_an_authored_colon_name_before_its_base_is_unchanged(monkeypatch,
                                                            entries):
    # The rename keeps the first shape's name, so 'x:1' before 'x' is the
    # author's own 'x:1', not a renamed shell.
    nif = {"x:1": 0, "x": 1}
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("x", RED, 1)]
    assert _parse(out) == _as_before(monkeypatch, entries, nif)


def test_a_family_out_of_suffix_order_falls_back():
    nif = {"fur": 0, "fur:2": 1, "fur:1": 2}
    entries = [("fur", RED, 0), ("fur", GREEN, 1), ("fur", BLUE, 2)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("fur", RED, 0)]


def test_a_zero_suffix_is_not_a_rename():
    nif = {"fur": 0, "fur:0": 1, "fur:1": 2}
    entries = [("fur", RED, 0), ("fur", GREEN, 1), ("fur", BLUE, 2)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("fur", RED, 0)]


def test_strict_switched_off_binds_by_rank_as_before(monkeypatch):
    monkeypatch.setenv(STRICT_OFF, "1")
    out = up._reindex_alt_texture_payload(_alt(SIX_SHELLS), DROPPED_MIDDLE)
    assert _colour_at(out) == {0: TAN, **{1 + i: SIX[i] for i in range(5)}}
    out = up._reindex_alt_texture_payload(_alt([("x", RED, 1)]),
                                          {"x:1": 0, "x": 1})
    assert _parse(out) == [("x", RED, 0)]


def test_strict_switched_off_entries_past_the_last_shell_are_dropped(monkeypatch):
    monkeypatch.setenv(STRICT_OFF, "1")
    entries = [("fur", RED, 1), ("fur", GREEN, 2), ("fur", BLUE, 3),
               ("fur", TAN, 4)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert _colour_at(out) == {1: RED, 2: GREEN, 3: BLUE}


def test_a_name_the_nif_lacks_is_dropped_as_before():
    entries = THREE_SHELLS + [("gone", TAN, 5)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert "gone" not in {nm for nm, _t, _i in _parse(out)}
    assert _colour_at(out) == {0: TAN, 1: RED, 2: GREEN, 3: BLUE}


def test_unique_names_are_unchanged():
    nif = {"BaseShape": 0, "Hood": 1, "Skirt": 2}
    entries = [("hood", RED, 0), ("Skirt", GREEN, 1), ("Skirt", BLUE, 1)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("hood", RED, 1), ("Skirt", GREEN, 2)]


def test_two_cases_of_one_name_in_the_nif_are_not_split():
    # 'Fur' and 'fur' are two authored shapes: a lower-cased entry could mean
    # either, so the name keeps today's one-entry match.
    nif = {"Fur": 0, "fur": 1, "fur:1": 2}
    entries = [("fur", RED, 1), ("fur", GREEN, 2)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("fur", RED, 0)]


@pytest.mark.parametrize("suffix", ["01", "x", ""])
def test_a_colon_name_that_is_not_a_rename_does_not_split(suffix):
    nif = {"fur": 0, f"fur:{suffix}": 1}
    entries = [("fur", RED, 0), ("fur", GREEN, 1)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("fur", RED, 0)]


def test_switched_off_the_set_keeps_one_entry(monkeypatch):
    monkeypatch.setenv(OFF, "1")
    out = up._reindex_alt_texture_payload(_alt(THREE_SHELLS), CONVERTED)
    assert _parse(out) == [("coat", TAN, 0), ("fur", RED, 1)]


def test_with_the_rename_off_the_set_keeps_one_entry(monkeypatch):
    monkeypatch.setenv(DSN_OFF, "1")
    out = up._reindex_alt_texture_payload(_alt(THREE_SHELLS), CONVERTED)
    assert _parse(out) == [("coat", TAN, 0), ("fur", RED, 1)]


# --- the whole reconcile, on a written plugin and a written NIF -----------

@pytest.mark.skipif(not pynifly_available(),
                    reason="pynifly native lib not available")
def test_the_reconcile_rewrites_our_plugin_per_shell(tmp_path):
    rel = r"!UBE\Clothes\Coat\coat_1.nif"
    shapes = []
    for i, name in enumerate(("coat", "fur", "fur:1", "fur:2", "BaseShape")):
        v, t, n = uv_sphere(10.0 + i, rings=4, segs=6)
        shapes.append((name, v, t, n))
    build_skinned_shapes_nif(tmp_path / "meshes" / rel, shapes)
    # The source set as the patcher copies it: the SOURCE's indices (its own
    # body at 1, dropped by the conversion, so the shells sat at 2..4), every
    # shell entry named 'fur'.
    payload = (esp.encode_subrecord(b"EDID", b"CoatAA\x00")
               + esp.encode_subrecord(b"MOD3", rel.encode("latin-1") + b"\x00")
               + esp.encode_subrecord(b"MO3S", _alt(
                   [("fur", BLUE, 4), ("fur", GREEN, 3), ("fur", RED, 2),
                    ("coat", TAN, 0)])))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    plugin = tmp_path / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(plugin)

    assert up.reconcile_alt_texture_indices(plugin, tmp_path / "meshes") == 1
    got = esp.ESP.load(plugin).groups[0].records[0].payload
    mo3s = next(d for s, d in esp.iter_subrecords(got) if s == b"MO3S")
    assert _colour_at(mo3s) == {0: TAN, 1: RED, 2: GREEN, 3: BLUE}
