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
name binds to the k-th shape of that name in the converted NIF.
`CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE=1` keeps one entry per name; with
`CBBE2UBE_NO_DUP_SHAPE_NAMES=1` it is off too.
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

# A coat: shape 0 'coat', then three shells the author all named 'fur'; the
# converted NIF carries them renamed, in the same order, and our injected body
# after them.
CONVERTED = {"coat": 0, "fur": 1, "fur:1": 2, "fur:2": 3, "BaseShape": 4}
RED, GREEN, BLUE, TAN = 0x0A000001, 0x0A000002, 0x0A000003, 0x0A000004


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(DSN_OFF, raising=False)


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


def test_an_authored_colon_name_is_matched_by_name():
    # The author already had a 'fur:1', so the rename skipped it: the second
    # and third 'fur' became 'fur:2' and 'fur:3'.
    nif = {"fur": 0, "fur:1": 1, "fur:2": 2, "fur:3": 3}
    entries = [("fur", RED, 0), ("fur:1", TAN, 1), ("fur", GREEN, 2),
               ("fur", BLUE, 3)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _colour_at(out) == {0: RED, 1: TAN, 2: GREEN, 3: BLUE}


def test_a_repeated_source_index_keeps_one_entry():
    entries = [("fur", RED, 1), ("fur", GREEN, 1), ("fur", BLUE, 2)]
    out = up._reindex_alt_texture_payload(_alt(entries), CONVERTED)
    assert _parse(out) == [("fur", RED, 1), ("fur", BLUE, 2)]


def test_entries_past_the_last_shell_are_dropped():
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
