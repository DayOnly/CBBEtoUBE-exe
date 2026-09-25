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

r"""#dup-shape-names -- shapes that share a name each get their own.

THE DEFECT. A layered fur coat ships six shells all named 'fur' (two sizes).
Dozens of passes key per-shape data by shape name, so the last 'fur' won: the
self-intersection repair handed one shell's positions to all six (a heap read
past the smaller shells' UVs, #override-contract), the stacked-layer motion
and coincident-skin passes died on the size mismatch, and the TRI carried one
'fur' block indexed for a 965-vert shell. The source's shapes are renamed at
load, in the author's order -- the first keeps its name, the k-th becomes
'fur:k' -- and the order never changes (an alternate texture binds by INDEX,
BUG-09). Left as authored and reported: a name the physics XML uses (FSMP binds
by name), and a name the converter reads as a body.
`CBBE2UBE_NO_DUP_SHAPE_NAMES=1` keeps every name as authored.

The conversions go through the batch worker's door, like #batch-door, with no
MO2 layout: spheres, no OSD, so the TRI inputs are read, not a `.tri`.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc                                    # noqa: E402
from src import auto_convert as ac                              # noqa: E402
from src import nif_convert_telemetry as tel                    # noqa: E402
from src import nif_io                                          # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,      # noqa: E402
                                 pynifly_available, uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib not available")

OFF = "CBBE2UBE_NO_DUP_SHAPE_NAMES"
CLOAK_SLOT = 1 << (46 - 30)

FUR_A = uv_sphere(12.0)                       # 86 verts
FUR_B = uv_sphere(13.0, rings=6, segs=8)      # 42 verts
COAT = uv_sphere(14.0, rings=10, segs=12)     # 110 verts
RADII = (12.0, 13.0, 14.0)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


@pytest.fixture
def world(tmp_path):
    body, vbody = uv_sphere(10.0), uv_sphere(9.5)
    ref = build_skinned_shapes_nif(tmp_path / "ube" / "femalebody_1.nif",
                                   [("BaseShape", *body), ("VirtualBody", *vbody)])
    coat = build_skinned_shapes_nif(tmp_path / "src" / "coat.nif",
                                    [("fur", *FUR_A), ("fur", *FUR_B), ("coat", *COAT)])
    out = tmp_path / "out"
    out.mkdir()
    return ref, coat, out


def _convert(src, out, ref, slots=CLOAK_SLOT):
    dst = out / src.name
    res = ac._nif_convert_worker((str(src.resolve()), str(dst.resolve()),
                                  str(ref.resolve()), int(slots), None))
    return res, dst


def _shapes(path):
    return list(nc._pynifly().NifFile(filepath=str(path)).shapes)


def _nearest_radius(shape) -> int:
    """Which source shell (by its radius) this written shape's geometry is."""
    v = np.asarray(shape.verts, dtype=np.float64)
    r = float(np.linalg.norm(v - (0.0, 0.0, 100.0), axis=1).mean())
    return int(np.argmin([abs(r - x) for x in RADII]))


def test_each_shell_keeps_its_own_geometry_under_its_own_name(world):
    ref, coat, out = world
    res, dst = _convert(coat, out, ref)
    assert res.status == "converted (copy)", (res.status, res.reason)
    shapes = _shapes(dst)
    assert [s.name for s in shapes] == ["fur", "fur:1", "coat"], (
        "the first keeps the authored name, the second gets its own; the ORDER "
        "is the author's (an alternate texture binds by index, BUG-09)")
    assert [len(s.verts) for s in shapes] == [86, 42, 110]
    assert [_nearest_radius(s) for s in shapes] == [0, 1, 2], (
        "each shell carries its OWN geometry, not a same-named neighbour's")
    assert "CHANGED BY #dup-shape-names (gave each shape of a shared name its " \
           "own: fur x2)" in res.reason, res.reason
    assert "PASS FAILED" not in res.reason, res.reason


def test_every_shell_gets_its_own_tri_block(world):
    ref, coat, out = world
    _res, dst = _convert(coat, out, ref)
    verts, _body, _ef = nc._collect_tri_inputs(nc._pynifly().NifFile(filepath=str(dst)))
    assert {n: len(v) for n, v in verts.items()} == {"fur": 86, "fur:1": 42,
                                                     "coat": 110}


def test_switched_off_the_names_stay_as_authored(world, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    ref, coat, out = world
    res, dst = _convert(coat, out, ref)
    shapes = _shapes(dst)
    assert [s.name for s in shapes] == ["fur", "fur", "coat"]
    # #override-contract still keeps each shell's own vert count.
    assert [len(s.verts) for s in shapes] == [86, 42, 110]
    assert "#dup-shape-names" not in res.reason
    verts, _b, _e = nc._collect_tri_inputs(nc._pynifly().NifFile(filepath=str(dst)))
    assert sorted(verts) == ["coat", "fur"], "one TRI block for two shells"


def test_the_body_swap_path_renames_the_same_way(world, tmp_path):
    ref, _coat, out = world
    cuirass = build_skinned_shapes_nif(
        tmp_path / "src" / "cuirass_1.nif",
        [("fur", *FUR_A), ("fur", *FUR_B), ("3BA", *uv_sphere(10.0))])
    res, dst = _convert(cuirass, out, ref, slots=nc.BIPED_SLOT32_BIT)
    assert res.status == "converted (body-swap)", (res.status, res.reason)
    shapes = _shapes(dst)
    assert [s.name for s in shapes] == ["fur", "fur:1", "BaseShape", "VirtualBody"]
    assert [len(s.verts) for s in shapes[:2]] == [86, 42]


def test_every_reread_of_the_source_gets_the_same_names(world):
    _ref, coat, _out = world
    nf = nc._open_source_nif(coat)
    assert [s.name for s in nf.shapes] == ["fur", "fur:1", "coat"]
    assert len(nf.shape_dict["fur"].verts) == 86, (
        "pynifly's by-name lookup points at the shape that kept the name")
    wrapped = nif_io.load_nif(coat)
    assert nc._uniquify_source_shape_names(wrapped, coat) == 1
    assert [s.name for s in wrapped.shapes] == ["fur", "fur:1", "coat"]
    assert [s._backing.name for s in wrapped.shapes] == ["fur", "fur:1", "coat"]


def _xml(monkeypatch, text):
    from src import nif_convert_physics as phys
    monkeypatch.setattr(phys, "_read_source_hdt_xml_text",
                        lambda *_a, **_k: text)


def test_a_name_the_physics_xml_uses_is_kept_and_reported(world, monkeypatch):
    _ref, coat, _out = world
    _xml(monkeypatch, '<system><per-vertex-shape name="Fur"/></system>')
    tel._begin_piece_pass_log()
    nf = nc._pynifly().NifFile(filepath=str(coat))
    assert nc._uniquify_source_shape_names(nf, coat, report=True) == 0
    assert [s.name for s in nf.shapes] == ["fur", "fur", "coat"]
    fails = tel._piece_pass_failures()
    assert any("dup-shape-names/kept" in f and "fur: xml" in f for f in fails), fails
    assert not any("#dup-shape-names" in e for e in tel._piece_pass_effects())


def test_an_xml_that_names_other_shapes_does_not_stop_the_rename(world, monkeypatch):
    _ref, coat, _out = world
    _xml(monkeypatch, '<system><per-vertex-shape name="cape"/></system>')
    nf = nc._pynifly().NifFile(filepath=str(coat))
    assert nc._uniquify_source_shape_names(nf, coat) == 1
    assert [s.name for s in nf.shapes] == ["fur", "fur:1", "coat"]


def test_the_plan_skips_a_taken_name_and_keeps_the_order():
    new, renamed, kept = nc._dup_shape_rename_plan(["fur", "fur:1", "fur", "coat", "fur"])
    assert new == ["fur", "fur:1", "fur:2", "coat", "fur:3"]
    assert renamed == {"fur": 3} and kept == {}


def test_the_plan_leaves_unique_names_alone():
    assert nc._dup_shape_rename_plan(["a", "b", "", ""]) is None


def test_an_unreadable_declared_xml_keeps_the_names():
    new, renamed, kept = nc._dup_shape_rename_plan(["fur", "fur"], xml_unread=True)
    assert new == ["fur", "fur"] and renamed == {} and kept == {"fur": "xml-unread"}


def test_a_body_name_is_never_renamed():
    nf_names = ["3BA", "3BA", "BaseShape", "BaseShape", "cloth", "cloth"]
    reserved = lambda n: (nc._is_inline_body_name(n) or n in nc.UBE_BODY_INJECT_NAMES)
    new, renamed, kept = nc._dup_shape_rename_plan(nf_names, reserved=reserved)
    assert new == ["3BA", "3BA", "BaseShape", "BaseShape", "cloth", "cloth:1"]
    assert kept == {"3BA": "body", "BaseShape": "body"} and renamed == {"cloth": 2}


def test_the_converter_reserves_its_body_names(tmp_path):
    body = uv_sphere(10.0)
    src = build_skinned_shapes_nif(tmp_path / "b.nif",
                                   [("3BA Ref", *body), ("3BA Ref", *body),
                                    ("VirtualBody", *body), ("VirtualBody", *body)])
    nf = nc._pynifly().NifFile(filepath=str(src))
    assert nc._uniquify_source_shape_names(nf, src) == 0
    assert [s.name for s in nf.shapes] == ["3BA Ref", "3BA Ref",
                                           "VirtualBody", "VirtualBody"]
