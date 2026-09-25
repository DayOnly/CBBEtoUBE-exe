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

r"""#alttex-set-provenance -- the two holes #alttex-exact-provenance left.

1. The source was read only when the converted NIF showed 'name:k' beside
   'name'. A family that lost EVERY renamed shell (a two-shell 'fur' that lost
   'fur:1') looks unrenamed, so one entry per name put the set's first-listed
   entry -- possibly the lost shell's colour -- on the surviving shell. A set
   that names one shape name more than once now reads the source too.
2. A group the rename kept as authored (physics XML or body name) kept one
   entry per name, bound to the LAST shape of the name. With the source at
   hand each entry now binds by its source index to the converted shape whose
   print (counts + UVs) is that shell's, or the name's entries are dropped.
`CBBE2UBE_NO_ALTTEX_SET_PROVENANCE=1` restores #alttex-exact-provenance as
it first shipped.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import esp, ube_patcher as up                          # noqa: E402
from src.nif_convert_writer import _dup_shape_rename_plan       # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,      # noqa: E402
                                 pynifly_available, uv_sphere)
from tests.test_alttex_exact_provenance import (                # noqa: E402
    BLUE, GREEN, RED, TAN, _alt, _colour_at, _converted, _parse, _print,
    _resolve_to)

SET_OFF = "CBBE2UBE_NO_ALTTEX_SET_PROVENANCE"
SWITCHES = (SET_OFF, "CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE",
            "CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE", "CBBE2UBE_NO_DUP_SHAPE_NAMES",
            "CBBE2UBE_NO_ALTTEX_FAMILY_STRICT",
            "CBBE2UBE_NO_ALTTEX_CASE_PROVENANCE")
needs_pynifly = pytest.mark.skipif(not pynifly_available(),
                                   reason="pynifly native lib not available")


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


REL = r"Clothes\Coat\coat_1.nif"
KEY = "clothes/coat/coat_1.nif"
COAT = uv_sphere(14.0, rings=10, segs=12)
SHELL1 = uv_sphere(12.0)
SHELL2 = uv_sphere(13.0, rings=6, segs=8)
BODY = uv_sphere(10.0, rings=9, segs=10)


def _world(tmp_path, src_shapes, conv_shapes, *sets):
    """A source NIF, the NIF converted from it under our prefix, and our
    plugin: one ARMA whose MO2S, MO3S, ... carry `sets`, all on that NIF."""
    src = build_skinned_shapes_nif(tmp_path / "src" / "coat_1.nif", src_shapes)
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             conv_shapes)
    model = b"!UBE\\" + REL.encode() + b"\x00"
    payload = esp.encode_subrecord(b"EDID", b"CoatAA\x00")
    for n, entries in zip((b"2", b"3", b"4", b"5"), sets):
        payload += (esp.encode_subrecord(b"MOD" + n, model)
                    + esp.encode_subrecord(b"MO" + n + b"S", _alt(entries)))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    plugin = tmp_path / "out" / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(plugin)
    return src, plugin


def _reconciled(plugin, tmp_path):
    """Every MO?S set of the ARMA after the reconcile, in subrecord order."""
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    got = esp.ESP.load(plugin).groups[0].records[0].payload
    return [_parse(d) for s, d in esp.iter_subrecords(got)
            if s in (b"MO2S", b"MO3S", b"MO4S", b"MO5S")]


# --- 1. every renamed shell lost: the set's repeat reads the source -----------

# Source coat, fur, fur ships coat, fur, fur:1; 'fur:1' failed to copy, so the
# converted NIF shows no 'name:k'. The set lists the lost shell's entry first.
LOST_SRC = [("coat", *COAT), ("fur", *SHELL1), ("fur", *SHELL2)]
LOST_CONV = [("coat", *COAT), ("fur", *SHELL1), ("BaseShape", *BODY)]
LOST_SET = [("fur", RED, 2), ("fur", GREEN, 1), ("coat", TAN, 0)]


@needs_pynifly
def test_every_renamed_shell_lost_drops_the_lost_shells_colour(
        monkeypatch, tmp_path):
    src, plugin = _world(tmp_path, LOST_SRC, LOST_CONV, LOST_SET)
    seen = _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 1),
                                              ("coat", TAN, 0)]], (
        "the lost shell's RED must not land on the surviving shell")
    assert seen == [[KEY]]


@needs_pynifly
def test_every_renamed_shell_lost_beside_an_authored_case_variant(
        monkeypatch, tmp_path):
    # Source 'Fur' (the author's), 'fur', 'fur' ships 'Fur', 'fur', 'fur:1';
    # 'fur:1' lost. One entry per name put the lost shell's RED on 'Fur'.
    src, plugin = _world(
        tmp_path, [("Fur", *COAT), ("fur", *SHELL1), ("fur", *SHELL2)],
        [("Fur", *COAT), ("fur", *SHELL1), ("BaseShape", *BODY)],
        [("fur", RED, 2), ("Fur", TAN, 0), ("fur", GREEN, 1)])
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("Fur", TAN, 0),
                                              ("fur", GREEN, 1)]]


@needs_pynifly
def test_a_repeated_name_without_a_source_loses_its_entries(
        monkeypatch, tmp_path, capsys):
    _src, plugin = _world(tmp_path, LOST_SRC, LOST_CONV, LOST_SET)
    _resolve_to(monkeypatch, {})
    assert _reconciled(plugin, tmp_path) == [[("coat", TAN, 0)]]
    assert ("!! alt-texture reconcile: 1 converted NIF(s) with same-named "
            "layers whose source mesh could not be read" in capsys.readouterr().err)


@needs_pynifly
@pytest.mark.parametrize("found", [True, False])
def test_another_sets_repeat_makes_a_single_entry_ambiguous(
        monkeypatch, tmp_path, found):
    # The MO3S set names 'fur' once, for the lost shell. The MO2S set's repeat
    # proves the NIF's 'fur' was shared, so that one entry is bound through the
    # source (and dropped: its shell was lost) or, with no source, dropped.
    src, plugin = _world(tmp_path, LOST_SRC, LOST_CONV,
                         [("fur", BLUE, 1), ("fur", BLUE, 2)],
                         [("fur", RED, 2), ("coat", TAN, 0)])
    _resolve_to(monkeypatch, {KEY: src} if found else {})
    got = _reconciled(plugin, tmp_path)
    assert got[1] == [("coat", TAN, 0)]
    assert got[0] == ([("fur", BLUE, 1)] if found else [])


@needs_pynifly
def test_a_repeat_in_a_later_armature_still_reads_the_source(monkeypatch,
                                                             tmp_path):
    # Two armatures on one NIF. The FIRST names 'fur' once; only the SECOND's
    # set repeats it. Every set is scanned for repeats before any NIF is
    # loaded, so the NIF is still read with its source: loaded while only the
    # first armature was seen, it would be cached without, and one entry per
    # name would put the lost shell's RED on the surviving 'fur' in both.
    src = build_skinned_shapes_nif(tmp_path / "src" / "coat_1.nif", LOST_SRC)
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             LOST_CONV)
    model = b"!UBE\\" + REL.encode() + b"\x00"
    recs = []
    for fid, entries in ((0x01000800, [("fur", RED, 2), ("coat", TAN, 0)]),
                         (0x01000801, [("fur", RED, 2), ("fur", GREEN, 1)])):
        payload = (esp.encode_subrecord(b"EDID", b"CoatAA\x00")
                   + esp.encode_subrecord(b"MOD2", model)
                   + esp.encode_subrecord(b"MO2S", _alt(entries)))
        recs.append(esp.Record(sig=b"ARMA", flags=0, formid=fid,
                               timestamp_vc=0, version_unk=0x002C,
                               payload=payload))
    plugin = tmp_path / "out" / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=recs)]).save(plugin)
    seen = _resolve_to(monkeypatch, {KEY: src})
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    got = [[_parse(d) for s, d in esp.iter_subrecords(r.payload) if s == b"MO2S"]
           for r in esp.ESP.load(plugin).groups[0].records]
    assert got == [[[("coat", TAN, 0)]], [[("fur", GREEN, 1)]]], (
        "the lost shell's RED must not land on the surviving 'fur'")
    assert seen == [[KEY]]


@needs_pynifly
def test_without_a_source_a_name_the_nif_carries_twice_is_dropped(
        monkeypatch, tmp_path):
    # A kept 'fur' group (two literal shapes) beside a renamed 'belt' pair;
    # the set names 'fur' once. No source: which 'fur' it meant is unknown.
    _src, plugin = _world(
        tmp_path, [("coat", *COAT)],
        [("coat", *COAT), ("fur", *SHELL1), ("fur", *SHELL2),
         ("belt", *SHELL1), ("belt:1", *SHELL2)],
        [("coat", TAN, 0), ("fur", GREEN, 1), ("belt", BLUE, 3)])
    _resolve_to(monkeypatch, {})
    assert _reconciled(plugin, tmp_path) == [[("coat", TAN, 0)]]


@needs_pynifly
def test_a_set_naming_each_name_once_reads_no_source_and_is_unchanged(
        monkeypatch, tmp_path, capsys):
    src, plugin = _world(tmp_path, LOST_SRC, LOST_CONV,
                         [("fur", GREEN, 1), ("Coat", TAN, 0)])
    seen = _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 1),
                                              ("Coat", TAN, 0)]]
    assert seen == [], "a set without a repeat is matched by name, as before"
    assert "alt-texture reconcile" not in capsys.readouterr().err


@needs_pynifly
def test_switched_off_the_lost_family_binds_as_the_parent(monkeypatch, tmp_path):
    monkeypatch.setenv(SET_OFF, "1")
    src, plugin = _world(tmp_path, LOST_SRC, LOST_CONV, LOST_SET)
    seen = _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", RED, 1),
                                              ("coat", TAN, 0)]], (
        "#alttex-exact-provenance as first shipped: the known wrong shell")
    assert seen == []


def test_a_sets_repeated_names_are_case_insensitive():
    assert up._repeated_entry_names(_alt(
        [("Fur", RED, 0), ("fur", GREEN, 1), ("coat", TAN, 2)])) == {"fur"}
    assert up._repeated_entry_names(_alt(
        [("fur", RED, 0), ("coat", TAN, 2)])) == frozenset()
    assert up._repeated_entry_names(b"\x05\x00") == frozenset()


# --- 2. a group the rename kept as authored binds by its shells' prints -------

P1, P2, PC, PB = (_print(10, 20, 0.1), _print(11, 21, 0.2),
                  _print(30, 40, 0.3), _print(1, 1, 0.5))


def _kept_source(names, prints):
    """A source whose 'fur' group the physics XML names: kept as authored."""
    renamed = _dup_shape_rename_plan(list(names), xml_names={"fur"})[0]
    assert tuple(renamed) == tuple(names)
    return up._AltTexSource(tuple(names), tuple(renamed), tuple(prints))


def _rebuild(entries, src, conv):
    index = {nm: i for i, (nm, _p) in enumerate(conv)}
    return up._reindex_alt_texture_payload(_alt(entries), index,
                                           up._alttex_binding(src, conv))


KEPT = _kept_source(["coat", "fur", "fur"], [PC, P1, P2])
KEPT_SET = [("fur", GREEN, 1), ("fur", RED, 2), ("coat", TAN, 0)]


def test_a_kept_group_binds_each_entry_to_its_shells_print():
    out = _rebuild(KEPT_SET, KEPT, _converted(KEPT))
    assert _colour_at(out) == {0: TAN, 1: GREEN, 2: RED}, (
        "one entry per name put GREEN on the LAST 'fur' shape")


def test_a_kept_groups_colour_follows_the_print_not_the_order():
    conv = [("coat", PC), ("fur", P2), ("fur", P1), ("BaseShape", PB)]
    assert _colour_at(_rebuild(KEPT_SET, KEPT, conv)) == {0: TAN, 1: RED,
                                                          2: GREEN}


def test_a_kept_groups_lost_shell_drops_only_its_own_colour():
    conv = [("coat", PC), ("fur", P1), ("BaseShape", PB)]
    assert _parse(_rebuild(KEPT_SET, KEPT, conv)) == [("fur", GREEN, 1),
                                                      ("coat", TAN, 0)]


def test_a_kept_group_of_identical_shells_is_dropped():
    same = _kept_source(["coat", "fur", "fur"], [PC, P1, P1])
    assert _parse(_rebuild(KEPT_SET, same, _converted(same))) == [
        ("coat", TAN, 0)], "two shells with one print cannot be told apart"


def test_a_kept_shape_matching_no_source_shell_drops_the_name():
    conv = [("coat", PC), ("fur", P1), ("fur", _print(12, 22, 0.9)),
            ("BaseShape", PB)]
    assert _parse(_rebuild(KEPT_SET, KEPT, conv)) == [("coat", TAN, 0)]


def test_a_repeated_index_of_a_kept_shell_keeps_the_first_listed():
    entries = [("fur", GREEN, 1), ("fur", BLUE, 1), ("fur", RED, 2)]
    assert _parse(_rebuild(entries, KEPT, _converted(KEPT))) == [
        ("fur", GREEN, 1), ("fur", RED, 2)]


@needs_pynifly
def test_the_reconcile_binds_a_physics_kept_group_by_print(monkeypatch,
                                                           tmp_path):
    # The source's physics XML names 'fur', so the rename keeps both shells
    # as 'fur'; the converted NIF carries two literal 'fur' shapes.
    from src import nif_convert_physics as phys
    monkeypatch.setattr(phys, "_read_source_hdt_xml_text",
                        lambda *a, **k: '<per-triangle-shape name="fur">')
    src, plugin = _world(tmp_path, LOST_SRC,
                         [("coat", *COAT), ("fur", *SHELL1), ("fur", *SHELL2),
                          ("BaseShape", *BODY)], KEPT_SET)
    _resolve_to(monkeypatch, {KEY: src})
    [got] = _reconciled(plugin, tmp_path)
    assert {ix: tx for _nm, tx, ix in got} == {0: TAN, 1: GREEN, 2: RED}
    monkeypatch.setenv(SET_OFF, "1")
    src, plugin = _world(tmp_path, LOST_SRC,
                         [("coat", *COAT), ("fur", *SHELL1), ("fur", *SHELL2),
                          ("BaseShape", *BODY)], KEPT_SET)
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 2),
                                              ("coat", TAN, 0)]], (
        "switched off: one entry per name, on the last 'fur'")


# --- the resolver: a key the batch index lacks is looked up loose -------------

@pytest.mark.parametrize("off", [False, True])
def test_a_key_the_batch_index_lacks_is_found_loose(monkeypatch, tmp_path, off):
    from src import auto_convert as ac, discovery, paths

    class _Lay:
        game_data_dirs = []

    class _NoArchives:
        def __init__(self, dirs, staging):
            pass

        def read_bytes(self, key):
            return None
    loose = tmp_path / "mods" / "Own" / "meshes" / "armor" / "own_1.nif"
    asked = []

    def index(mods_root, order, target_keys=None, skip_mods=()):
        asked.append(sorted(target_keys))
        return {"armor/own_1.nif": loose}
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(paths, "mods_root", lambda: tmp_path / "mods")
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda lay: ["A", "Own"])
    monkeypatch.setitem(ac._BATCH_MESH_INDEX, str(tmp_path / "mods").lower(),
                        {"armor/listed_1.nif": tmp_path / "listed_1.nif"})
    monkeypatch.setattr(ac, "_BsaMeshIndex", _NoArchives)
    monkeypatch.setattr(discovery, "build_mesh_index", index)
    if off:
        monkeypatch.setenv(SET_OFF, "1")
    got = up._alttex_source_paths(tmp_path / "out" / "meshes",
                                  ["armor/listed_1.nif", "armor/own_1.nif"])
    assert got["armor/listed_1.nif"] == tmp_path / "listed_1.nif"
    if off:
        assert "armor/own_1.nif" not in got and asked == []
    else:
        assert got["armor/own_1.nif"] == loose
        assert asked == [["armor/own_1.nif"]], "only the keys it lacks"
