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

r"""#alttex-exact-provenance -- a colour variant's entry goes to the shell its
source index names, read from the source mesh, or nowhere.

THE DEFECT. #alttex-family-strict bound the entries of a renamed name ('fur',
'fur:1', 'fur:2' ...) by rank, judged from the converted NIF's layout alone. A
lost TRAILING shell passes its layout and count checks when the set names only
some shells or the author already had a 'fur:1' the set does not name, and the
rank then puts a colour on a neighbour shell. The rename is a pure function of
the source file, so the reconcile now re-reads the source (the convert step's
own resolution, read-only), replays the converter's own rename on it, and binds
each entry: source 3D index -> the name that shell shipped under -> that name's
index in the converted NIF. A shell the converted NIF lacks drops its entry. No
source, an unreadable one, or one that is not the mesh converted (names or
geometry differ): every name that may be split loses its entries for that NIF.
`CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE=1` binds by the layout as before.
"""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import esp, ube_patcher as up                          # noqa: E402
from src.nif_convert_writer import _dup_shape_rename_plan       # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,      # noqa: E402
                                 pynifly_available, uv_sphere)

EXACT_OFF = "CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE"
OCC_OFF = "CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE"
DSN_OFF = "CBBE2UBE_NO_DUP_SHAPE_NAMES"
STRICT_OFF = "CBBE2UBE_NO_ALTTEX_FAMILY_STRICT"
RED, GREEN, BLUE, TAN = 0x0A000001, 0x0A000002, 0x0A000003, 0x0A000004
SIX = [0x0A000010 + i for i in range(6)]


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    for k in (EXACT_OFF, OCC_OFF, DSN_OFF, STRICT_OFF):
        monkeypatch.delenv(k, raising=False)


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


def _source(names, prints=None):
    """A source as the converter names it: the rename is the converter's own
    pure plan; each shape gets a distinct print unless `prints` is given."""
    plan = _dup_shape_rename_plan(list(names))
    renamed = plan[0] if plan else list(names)
    prints = prints or [_print(10 + i, 20 + i, 0.1 * i) for i in range(len(names))]
    return up._AltTexSource(tuple(names), tuple(renamed), tuple(prints))


def _print(verts, tris, uv):
    """A `_shape_print`: counts and a (verts, 2) UV array."""
    return (verts, tris, np.full((verts, 2), uv, dtype=np.float32))


def _converted(src, keep=None, extra=("BaseShape",)):
    """The converted NIF: the source shapes in `keep` (all by default), under
    their shipped names, then our injected body."""
    keep = range(len(src.names)) if keep is None else keep
    return ([(src.renamed[i], src.prints[i]) for i in keep]
            + [(nm, _print(1, 1, 0.5)) for nm in extra])


def _reindex(entries, src, conv):
    """Run one set through the reconcile's rebuild, bound through `src`."""
    index = {nm: i for i, (nm, _p) in enumerate(conv)}
    return up._reindex_alt_texture_payload(_alt(entries), index,
                                           up._alttex_binding(src, conv))


COAT = ["coat", "fur", "fur", "fur"]                 # shipped fur, fur:1, fur:2


# --- the source says which shell each entry is ------------------------------

def test_a_full_family_gives_each_shell_its_own_colour():
    src = _source(COAT)
    listed = [("fur", BLUE, 3), ("fur", RED, 1), ("coat", TAN, 0),
              ("fur", GREEN, 2)]
    out = _reindex(listed, src, _converted(src))
    assert _colour_at(out) == {0: TAN, 1: RED, 2: GREEN, 3: BLUE}
    assert [nm for nm, _t, _i in _parse(out)] == ["fur", "fur", "coat", "fur"], (
        "the authored name bytes are kept: the engine binds by index")


def test_a_lost_trailing_shell_with_a_partial_set_colours_no_neighbour():
    # The review's case: 'fur:2' failed to copy, and the set recolours the
    # first and the last shell. The layout guess saw 'fur', 'fur:1' and two
    # entries and gave the LAST shell's colour to 'fur:1'.
    src = _source(COAT)
    entries = [("coat", TAN, 0), ("fur", RED, 1), ("fur", BLUE, 3)]
    out = _reindex(entries, src, _converted(src, keep=[0, 1, 2]))
    assert _parse(out) == [("coat", TAN, 0), ("fur", RED, 1)]


def test_a_lost_trailing_shell_beside_an_authored_colon_name():
    # Source 'fur', the author's 'fur:1', 'fur', 'fur': shipped 'fur',
    # 'fur:1', 'fur:2', 'fur:3', and 'fur:3' was lost. The layout guess saw
    # 'fur', 'fur:1', 'fur:2' and three 'fur' entries and moved each colour
    # onto the author's 'fur:1' and on down.
    src = _source(["fur", "fur:1", "fur", "fur"])
    assert src.renamed == ("fur", "fur:1", "fur:2", "fur:3")
    entries = [("fur", RED, 0), ("fur", GREEN, 2), ("fur", BLUE, 3)]
    out = _reindex(entries, src, _converted(src, keep=[0, 1, 2]))
    assert _parse(out) == [("fur", RED, 0), ("fur", GREEN, 2)]


def test_a_dropped_middle_shell_drops_only_its_own_colour():
    src = _source(["coat"] + ["fur"] * 6)
    entries = [("coat", TAN, 0)] + [("fur", SIX[i], 1 + i) for i in range(6)]
    conv = _converted(src, keep=[0, 1, 2, 4, 5, 6])  # 'fur:2' lost
    out = _reindex(entries, src, conv)
    assert _colour_at(out) == {0: TAN, 1: SIX[0], 2: SIX[1], 3: SIX[3],
                               4: SIX[4], 5: SIX[5]}


def test_a_taken_authored_name_keeps_its_own_entry():
    # The rename skipped the author's 'fur:1': the second 'fur' shipped as
    # 'fur:2'. Each entry reaches its own shape, the author's by name.
    src = _source(["fur", "fur:1", "fur"])
    assert src.renamed == ("fur", "fur:1", "fur:2")
    conv = _converted(src)
    out = _reindex([("fur", RED, 0), ("fur:1", TAN, 1), ("fur", BLUE, 2)],
                   src, conv)
    assert _colour_at(out) == {0: RED, 1: TAN, 2: BLUE}
    out = _reindex([("fur", RED, 0), ("fur", BLUE, 2)], src, conv)
    assert _colour_at(out) == {0: RED, 2: BLUE}


def test_a_partial_set_colours_exactly_the_shells_it_names():
    src = _source(COAT)
    out = _reindex([("fur", RED, 1), ("fur", BLUE, 3)], src, _converted(src))
    assert _colour_at(out) == {1: RED, 3: BLUE}


def test_a_moved_family_follows_the_converted_order():
    # The source's own body sat between the coat and the fur; the conversion
    # dropped it and put ours last.
    src = _source(["coat", "3BA", "fur", "fur", "fur"])
    conv = _converted(src, keep=[0, 2, 3, 4])
    out = _reindex([("coat", TAN, 0), ("fur", RED, 2), ("fur", GREEN, 3),
                    ("fur", BLUE, 4)], src, conv)
    assert _colour_at(out) == {0: TAN, 1: RED, 2: GREEN, 3: BLUE}


def test_a_shape_named_like_a_split_name_in_other_case_keeps_its_colour():
    # The author's own 'Fur' beside two 'fur' shells: the rename splits only
    # the exact-case group, and every entry still reaches the shape its index
    # names, whatever case the entry spells.
    src = _source(["Fur", "fur", "fur"])
    assert src.renamed == ("Fur", "fur", "fur:1")
    out = _reindex([("FUR", BLUE, 2), ("Fur", GREEN, 0), ("fur", RED, 1)],
                   src, _converted(src))
    assert _colour_at(out) == {0: GREEN, 1: RED, 2: BLUE}


def test_an_entry_whose_index_is_another_names_shell_is_dropped():
    # 'fur' at the index of the second 'belt': the name and the index
    # disagree, so which shell the author meant is unknown.
    src = _source(["fur", "fur", "belt", "belt"])
    out = _reindex([("fur", RED, 3), ("belt", GREEN, 2)], src, _converted(src))
    assert _parse(out) == [("belt", GREEN, 2)]


def test_a_repeated_source_index_keeps_the_first_listed():
    src = _source(COAT)
    out = _reindex([("fur", RED, 2), ("fur", GREEN, 2), ("fur", BLUE, 3)],
                   src, _converted(src))
    assert _parse(out) == [("fur", RED, 2), ("fur", BLUE, 3)]


def test_a_source_the_rename_left_alone_matches_by_name():
    # The author's own 'fur' and 'fur:1', each once: nothing was renamed.
    src = _source(["fur", "fur:1"])
    out = _reindex([("fur:1", GREEN, 1), ("fur", RED, 0), ("fur", BLUE, 0)],
                   src, _converted(src))
    assert _parse(out) == [("fur:1", GREEN, 1), ("fur", RED, 0)]


def test_unique_names_need_no_source():
    nif = {"BaseShape": 0, "Hood": 1, "Skirt": 2}
    entries = [("hood", RED, 0), ("Skirt", GREEN, 1), ("Skirt", BLUE, 1)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif)
    assert _parse(out) == [("hood", RED, 1), ("Skirt", GREEN, 2)]


# --- no source that matches: drop, never guess --------------------------------

def test_without_a_source_every_split_name_loses_its_entries():
    # The layout guess and the one-entry-per-name rule would both keep the
    # FIRST-listed 'fur' entry on the first shell -- here the third shell's
    # colour. Dropped instead.
    nif = {"coat": 0, "fur": 1, "fur:1": 2, "fur:2": 3, "BaseShape": 4}
    entries = [("coat", TAN, 0), ("fur", BLUE, 3), ("fur", RED, 1),
               ("fur:1", GREEN, 2)]
    out = up._reindex_alt_texture_payload(_alt(entries), nif, None)
    assert _parse(out) == [("coat", TAN, 0)]


def test_a_source_with_other_names_is_not_the_mesh_converted():
    # A two-shell source for a NIF converted from a three-shell one.
    conv = _converted(_source(COAT))
    assert up._alttex_binding(_source(["coat", "fur", "fur"]), conv) is None


def test_a_source_with_other_geometry_is_not_the_mesh_converted():
    src = _source(COAT)
    conv = _converted(src)
    nv, nt, uv = src.prints[3]
    for other, what in (((nv + 1, nt, np.resize(uv, (nv + 1, 2))), "verts"),
                        ((nv, nt + 1, uv), "tris"),
                        ((nv, nt, uv + 0.01), "uvs")):
        moved = src._replace(prints=src.prints[:3] + (other,))
        assert up._alttex_binding(moved, conv) is None, what
    assert up._alttex_binding(src, conv) is not None


def test_half_float_uvs_are_the_same_shell():
    # The writer may store UVs as half floats: MEASURED up to 2.4e-4 off.
    src = _source(COAT)
    nv, nt, uv = src.prints[3]
    written = (nv, nt, (uv + 2.4e-4).astype(np.float32))
    conv = _converted(src)[:3] + [("fur:2", written)]
    assert up._alttex_binding(src, conv) is not None


def test_a_shell_name_the_converted_nif_carries_twice_is_not_bound():
    src = _source(COAT)
    conv = _converted(src) + [("fur:1", src.prints[2])]
    assert up._alttex_binding(src, conv) is None


def test_no_source_at_all_is_no_binding():
    assert up._alttex_binding(None, _converted(_source(COAT))) is None


@pytest.mark.parametrize("model, key", [
    (r"!UBE\Clothes\Coat\coat_1.nif", "clothes/coat/coat_1.nif"),
    (r"!ube/clothes/coat/coat_1.nif", "clothes/coat/coat_1.nif"),
    (r"Clothes\Coat\coat_1.nif", None),
])
def test_the_source_key_is_the_converted_path_less_our_prefix(model, key):
    assert up._alttex_source_rel(model) == key


# --- the switches -------------------------------------------------------------

REVIEW_CASE = [("coat", TAN, 0), ("fur", RED, 1), ("fur", BLUE, 3)]
REVIEW_NIF = {"coat": 0, "fur": 1, "fur:1": 2, "BaseShape": 3}


def test_switched_off_binds_by_the_layout_as_the_parent(monkeypatch):
    monkeypatch.setenv(EXACT_OFF, "1")
    out = up._reindex_alt_texture_payload(_alt(REVIEW_CASE), REVIEW_NIF)
    assert _colour_at(out) == {0: TAN, 1: RED, 2: BLUE}, (
        "the layout path's known wrong shell -- the reason this is the default")


def test_the_occurrence_switch_still_keeps_one_entry_per_name(monkeypatch):
    monkeypatch.setenv(OCC_OFF, "1")
    src = _source(COAT)
    out = _reindex(REVIEW_CASE, src, _converted(src, keep=[0, 1, 2]))
    assert _parse(out) == [("coat", TAN, 0), ("fur", RED, 1)]
    out = up._reindex_alt_texture_payload(_alt(REVIEW_CASE), REVIEW_NIF, None)
    assert _parse(out) == [("coat", TAN, 0), ("fur", RED, 1)]


# --- the resolver: the convert step's own, read-only --------------------------

class _Archives:
    """Stands in for the batch's archive index: lookup-only reads."""
    data = {"armor/bsa/cuirass_1.nif": b"ARCHIVED"}

    def __init__(self, dirs, staging, **kw):
        assert staging is None, "the reconcile never extracts"

    def read_bytes(self, key):
        return self.data.get(key)


def _fake_layout(monkeypatch, tmp_path, vfs):
    from src import auto_convert as ac, paths

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(paths, "mods_root", lambda: tmp_path / "mods")
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda lay: ["A", "B"])
    monkeypatch.setitem(ac._BATCH_MESH_INDEX,
                        str(tmp_path / "mods").lower(), vfs)
    monkeypatch.setattr(ac, "_BsaMeshIndex", _Archives)


def test_the_source_is_the_batch_vfs_winner_then_the_archive_copy(
        monkeypatch, tmp_path):
    loose = tmp_path / "mods" / "A" / "meshes" / "armor" / "loose_1.nif"
    _fake_layout(monkeypatch, tmp_path, {"armor/loose_1.nif": loose})
    output = tmp_path / "out"
    staged = output / "_bsa_staging" / "meshes" / "armor" / "bsa" / "cuirass_1.nif"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"ARCHIVED")
    keys = ["armor/loose_1.nif", "armor/bsa/cuirass_1.nif", "armor/none_1.nif"]
    assert up._alttex_source_paths(output / "meshes", keys) == {
        "armor/loose_1.nif": loose, "armor/bsa/cuirass_1.nif": staged}


def test_a_stale_archive_copy_is_not_the_source(monkeypatch, tmp_path):
    _fake_layout(monkeypatch, tmp_path, {})
    output = tmp_path / "out"
    staged = output / "_bsa_staging" / "meshes" / "armor" / "bsa" / "cuirass_1.nif"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"AN OLDER EXTRACT")
    assert up._alttex_source_paths(output / "meshes",
                                   ["armor/bsa/cuirass_1.nif"]) == {}


# --- the whole reconcile, on a written plugin and written NIFs ----------------

REL = r"Clothes\Coat\coat_1.nif"
SPHERES = {"coat": uv_sphere(14.0, rings=10, segs=12),
           "fur": uv_sphere(12.0), "fur:1": uv_sphere(13.0, rings=6, segs=8),
           "fur:2": uv_sphere(12.5, rings=5, segs=7),
           "BaseShape": uv_sphere(10.0)}


def _world(tmp_path, src_shapes, conv_names, entries):
    """A source NIF, the NIF converted from it under our prefix, and our
    plugin carrying the source's colour set."""
    src = build_skinned_shapes_nif(tmp_path / "src" / "coat_1.nif", src_shapes)
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             [(nm, *SPHERES[nm]) for nm in conv_names])
    payload = (esp.encode_subrecord(b"EDID", b"CoatAA\x00")
               + esp.encode_subrecord(b"MOD3", b"!UBE\\" + REL.encode() + b"\x00")
               + esp.encode_subrecord(b"MO3S", _alt(entries)))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    plugin = tmp_path / "out" / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(plugin)
    return src, plugin


def _reconciled(plugin, tmp_path):
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    got = esp.ESP.load(plugin).groups[0].records[0].payload
    return next(d for s, d in esp.iter_subrecords(got) if s == b"MO3S")


# Source: coat, its own body, three 'fur' shells; converted: the body
# dropped, the shells renamed, ours last.
SRC_SHAPES = [("coat", *SPHERES["coat"]), ("3BA", *SPHERES["BaseShape"]),
              ("fur", *SPHERES["fur"]), ("fur", *SPHERES["fur:1"]),
              ("fur", *SPHERES["fur:2"])]
CONV_NAMES = ["coat", "fur", "fur:1", "fur:2", "BaseShape"]
SET = [("fur", BLUE, 4), ("fur", GREEN, 3), ("fur", RED, 2), ("coat", TAN, 0)]


def _resolve_to(monkeypatch, mapping):
    seen = []

    def fake(meshes_root, keys):
        seen.append(sorted(keys))
        return {k: v for k, v in mapping.items() if k in keys}
    monkeypatch.setattr(up, "_alttex_source_paths", fake)
    return seen


@pytest.mark.skipif(not pynifly_available(),
                    reason="pynifly native lib not available")
def test_the_reconcile_binds_through_the_source(monkeypatch, tmp_path, capsys):
    src, plugin = _world(tmp_path, SRC_SHAPES, CONV_NAMES, SET)
    seen = _resolve_to(monkeypatch, {"clothes/coat/coat_1.nif": src})
    assert _colour_at(_reconciled(plugin, tmp_path)) == {0: TAN, 1: RED,
                                                         2: GREEN, 3: BLUE}
    assert seen == [["clothes/coat/coat_1.nif"]]
    err = capsys.readouterr().err
    assert "1 converted NIF(s) with same-named layers bound through their source" in err
    assert "!!" not in err


@pytest.mark.skipif(not pynifly_available(),
                    reason="pynifly native lib not available")
def test_the_reconcile_drops_the_lost_trailing_shell(monkeypatch, tmp_path):
    src, plugin = _world(tmp_path, SRC_SHAPES,
                         ["coat", "fur", "fur:1", "BaseShape"],
                         [("fur", RED, 2), ("fur", BLUE, 4), ("coat", TAN, 0)])
    _resolve_to(monkeypatch, {"clothes/coat/coat_1.nif": src})
    assert _parse(_reconciled(plugin, tmp_path)) == [("fur", RED, 1),
                                                     ("coat", TAN, 0)]


@pytest.mark.skipif(not pynifly_available(),
                    reason="pynifly native lib not available")
@pytest.mark.parametrize("how", ["not-found", "unreadable", "other-mesh"])
def test_the_reconcile_falls_back_and_reports_it(monkeypatch, tmp_path, capsys,
                                                 how):
    src, plugin = _world(tmp_path, SRC_SHAPES, CONV_NAMES, SET)
    if how == "unreadable":
        src.write_bytes(b"not a nif")
    elif how == "other-mesh":            # a shell of another size
        build_skinned_shapes_nif(src, SRC_SHAPES[:4]
                                 + [("fur", *uv_sphere(12.5, rings=4, segs=6))])
    _resolve_to(monkeypatch, {} if how == "not-found"
                else {"clothes/coat/coat_1.nif": src})
    assert _parse(_reconciled(plugin, tmp_path)) == [("coat", TAN, 0)]
    err = capsys.readouterr().err
    assert ("!! alt-texture reconcile: 1 converted NIF(s) with same-named layers "
            "whose source mesh could not be read or is not the mesh converted"
            in err)


@pytest.mark.skipif(not pynifly_available(),
                    reason="pynifly native lib not available")
def test_the_reconcile_matches_a_real_conversion(monkeypatch, tmp_path):
    # The prints the reconcile compares are what the converter keeps: convert
    # a two-shell source for real and bind through it.
    from src import auto_convert as ac
    body, vbody = uv_sphere(10.0), uv_sphere(9.5)
    ref = build_skinned_shapes_nif(tmp_path / "ube" / "femalebody_1.nif",
                                   [("BaseShape", *body), ("VirtualBody", *vbody)])
    src = build_skinned_shapes_nif(
        tmp_path / "src" / "coat_1.nif",
        [("fur", *uv_sphere(12.0)), ("fur", *uv_sphere(13.0, rings=6, segs=8)),
         ("coat", *uv_sphere(14.0, rings=10, segs=12))])
    dst = tmp_path / "out" / "meshes" / "!UBE" / REL
    dst.parent.mkdir(parents=True)
    res = ac._nif_convert_worker((str(src.resolve()), str(dst.resolve()),
                                  str(ref.resolve()), 1 << (46 - 30), None))
    assert res.status == "converted (copy)", (res.status, res.reason)
    from src import nif_io
    conv = [(s.name, up._shape_print(s)) for s in nif_io.load_nif(dst).shapes]
    binding = up._alttex_binding(up._read_alttex_source(src), conv)
    assert binding is not None and binding.index == {"fur": 0, "fur:1": 1}
    payload = (esp.encode_subrecord(b"MOD3", b"!UBE\\" + REL.encode() + b"\x00")
               + esp.encode_subrecord(b"MO3S", _alt(
                   [("coat", TAN, 2), ("fur", GREEN, 1), ("fur", RED, 0)])))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800,
                     timestamp_vc=0, version_unk=0x002C, payload=payload)
    plugin = tmp_path / "out" / "Combined.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(plugin)
    _resolve_to(monkeypatch, {"clothes/coat/coat_1.nif": src})
    assert _colour_at(_reconciled(plugin, tmp_path)) == {0: RED, 1: GREEN,
                                                         2: TAN}
