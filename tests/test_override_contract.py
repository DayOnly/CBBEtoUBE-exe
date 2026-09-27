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

r"""#override-contract -- a shape copy takes only geometry that is its own.

THE DEFECT. `_copy_shape` documented that an override has one row per source
vert and never checked it. On a fur coat whose six shells share the name 'fur',
one shell's 2915 positions reached a 965-vert shell; pynifly sized the shape
from the override and the UVs from the source, and read past them. The written
coat had four shells of 2915 verts with 1687 triangles indexing the wrong mesh
and non-finite UVs (it differed run to run). Now a wrong-length vert or normal
override, or a triangle index outside the shape, raises ValueError, which every
caller treats as a failed copy. The triangle COUNT stays free: the UBE body's
pubic holes are closed by APPENDING fill triangles. The re-author also declines
an override whose name matches several shapes (the same-size case).
`CBBE2UBE_NO_OVERRIDE_CONTRACT=1` turns both off.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc                                    # noqa: E402
from src import auto_convert as ac                              # noqa: E402
from src import nif_convert_telemetry as tel                    # noqa: E402
from tests.synthetic_nif import (TRIS, VERTS, build_shape_nif,  # noqa: E402
                                 build_skinned_shapes_nif,
                                 pynifly_available, uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib not available")

OFF = "CBBE2UBE_NO_OVERRIDE_CONTRACT"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _src_and_dst(tmp_path):
    pyn = nc._pynifly()
    s = pyn.NifFile(filepath=str(build_shape_nif(tmp_path / "s.nif"))).shapes[0]
    dst = pyn.NifFile()
    dst.initialize("SKYRIMSE", str(tmp_path / "d.nif"))
    return s, dst


@pytest.mark.parametrize("rows", [5, 3])
def test_a_wrong_length_vert_override_is_refused(tmp_path, rows):
    s, dst = _src_and_dst(tmp_path)
    with pytest.raises(ValueError, match="override_verts has"):
        nc._copy_shape(s, dst, override_verts=[(0.0, 0.0, 0.0)] * rows)


def test_a_wrong_length_normal_override_is_refused(tmp_path):
    s, dst = _src_and_dst(tmp_path)
    with pytest.raises(ValueError, match="override_normals has"):
        nc._copy_shape(s, dst, override_normals=[(0.0, 0.0, 1.0)] * 3)


def test_a_triangle_outside_the_shape_is_refused(tmp_path):
    s, dst = _src_and_dst(tmp_path)
    with pytest.raises(ValueError, match="override_tris indexes"):
        nc._copy_shape(s, dst, override_tris=list(TRIS) + [(0, 1, len(VERTS))])


def test_appended_fill_triangles_are_accepted(tmp_path):
    """The pubic-hole fill APPENDS triangles; only the indices are bounded."""
    s, dst = _src_and_dst(tmp_path)
    tris = list(TRIS) + [(0, 2, 1), (1, 3, 2)]
    new = nc._copy_shape(s, dst, override_tris=tris,
                         override_verts=list(VERTS),
                         override_normals=[(0.0, 0.0, 1.0)] * len(VERTS))
    assert len(new.tris) == len(TRIS) + 2


def _open_bottom_body(radius=4.0, centre=(0.0, 0.0, 67.0), rings=8, segs=12):
    """A sphere with its bottom cap removed: ONE boundary loop inside the
    pubic box, which the injection closes with fill triangles."""
    verts, tris, normals = uv_sphere(radius, rings=rings, segs=segs, centre=centre)
    bottom = len(verts) - 1
    tris = [t for t in tris if bottom not in t]
    return verts[:-1], tris, normals[:-1]


def test_the_body_swap_injects_a_body_with_its_fill_triangles(tmp_path):
    body = _open_bottom_body()
    ref = build_skinned_shapes_nif(tmp_path / "ube" / "femalebody_1.nif",
                                   [("BaseShape", *body),
                                    ("VirtualBody", *uv_sphere(3.8, centre=(0.0, 0.0, 67.0)))])
    src = build_skinned_shapes_nif(
        tmp_path / "src" / "cuirass_1.nif",
        [("Cuirass", *uv_sphere(6.0, centre=(0.0, 0.0, 67.0))),
         ("3BA", *uv_sphere(4.0, centre=(0.0, 0.0, 67.0)))])
    out = tmp_path / "out"
    out.mkdir()
    dst = out / src.name
    res = ac._nif_convert_worker((str(src.resolve()), str(dst.resolve()),
                                  str(ref.resolve()), int(nc.BIPED_SLOT32_BIT), None))
    assert res.status == "converted (body-swap)", (res.status, res.reason)
    base = next(s for s in nc._pynifly().NifFile(filepath=str(dst)).shapes
                if s.name == "BaseShape")
    assert len(base.verts) == len(body[0])
    assert len(base.tris) > len(body[1]), "the hole was closed by appended triangles"
    assert int(np.asarray(base.tris).max()) < len(base.verts)


def _three_shapes(tmp_path):
    shifted = [(x + 5.0, y, z) for x, y, z in VERTS]
    far = [(x + 10.0, y, z) for x, y, z in VERTS]
    return build_skinned_shapes_nif(
        tmp_path / "piece.nif",
        [("fur", VERTS, TRIS, [(0.0, 0.0, 1.0)] * 4),
         ("fur", shifted, TRIS, [(0.0, 0.0, 1.0)] * 4),
         ("coat", far, TRIS, [(0.0, 0.0, 1.0)] * 4)])


def _verts(path):
    return [np.asarray(s.verts, dtype=np.float64)
            for s in nc._pynifly().NifFile(filepath=str(path)).shapes]


NEW_FUR = np.asarray(VERTS, dtype=np.float64) + (0.0, 0.0, 3.0)
NEW_COAT = np.asarray(VERTS, dtype=np.float64) + (10.0, 0.0, 3.0)


def test_the_reauthor_declines_a_name_two_shapes_share(tmp_path):
    p = _three_shapes(tmp_path)
    before = _verts(p)
    tel._begin_piece_pass_log()
    assert nc._reauthor_nif_fresh(p, override_verts_by_name={"fur": NEW_FUR,
                                                             "coat": NEW_COAT})
    after = _verts(p)
    assert np.allclose(after[0], before[0]) and np.allclose(after[1], before[1]), (
        "neither 'fur' takes geometry the name cannot say is its own")
    assert np.allclose(after[2], NEW_COAT, atol=1e-4), "the unambiguous one commits"
    assert any("reauthor/ambiguous-shape-name" in f
               for f in tel._piece_pass_failures())


def test_switched_off_both_same_named_shapes_take_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    p = _three_shapes(tmp_path)
    assert nc._reauthor_nif_fresh(p, override_verts_by_name={"fur": NEW_FUR})
    after = _verts(p)
    assert np.allclose(after[0], NEW_FUR, atol=1e-4)
    assert np.allclose(after[1], NEW_FUR, atol=1e-4), "the collapse the guard stops"
