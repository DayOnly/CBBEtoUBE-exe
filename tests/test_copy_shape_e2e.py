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

"""End-to-end guard for the NIF read->transform->write boundary (`_copy_shape`).

The engine IGNORES a skinned shape's NiAVObject transform, so the converter must
BAKE any non-identity scale/translation into the verts and ship an identity
transform -- otherwise the shape renders at the wrong size / flung off-body /
collapsed (the project_scale_bake_vigilant 'flung-off / breast-to-floor' class).
The pure-logic tests (test_scale_bake) only exercise the matrix helpers on mocks;
these build a REAL NIF, run the REAL `_copy_shape`, reload, and assert the bake
actually landed in the on-disk verts. Skips cleanly if pynifly isn't available.
"""
import numpy as np
import pytest

from tests.synthetic_nif import (VERTS, build_shape_nif, copy_shape_into_fresh,
                                  pynifly_available)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")


def test_copy_shape_bakes_scale_into_verts(tmp_path):
    # A source shape with transform scale 0.5 must come out with the scale baked
    # into the verts and an IDENTITY transform (engine ignores the transform).
    build_shape_nif(tmp_path / "src.nif", scale=0.5)
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif")
    assert abs(out.transform.scale - 1.0) < 1e-4, "transform not reset to identity"
    ov = [tuple(v) for v in out.verts]
    for i, v in enumerate(VERTS):
        assert all(abs(ov[i][k] - 0.5 * v[k]) < 1e-4 for k in range(3)), (i, ov[i])


def test_copy_shape_lifts_translation_into_verts(tmp_path):
    # A source shape with a +64 z translation must be lifted into the verts and
    # the transform reset (the documented breast-to-floor / offset cause).
    build_shape_nif(tmp_path / "src.nif", trans=(0.0, 0.0, 64.0))
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif")
    assert max(abs(c) for c in out.transform.translation) < 1e-4, "translation kept"
    ov = [tuple(v) for v in out.verts]
    for i, v in enumerate(VERTS):
        assert abs(ov[i][2] - (v[2] + 64.0)) < 1e-4, (i, ov[i])


def test_copy_shape_preserves_geometry(tmp_path):
    # Shape set + vert/tri counts preserved; no NaN/inf verts introduced.
    build_shape_nif(tmp_path / "src.nif")
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif")
    assert len(out.verts) == len(VERTS)
    assert len(out.tris) == len(VERTS)   # 4 verts, 4 tris in the tetra
    assert np.isfinite(np.asarray(out.verts, dtype=np.float64)).all()


def test_copy_shape_fitpath_resets_transform_to_identity(tmp_path):
    # GAP #3: on the FIT path (override_verts = body-positioned verts) a source
    # translation must NOT survive in the output transform. The verts are already
    # in final space and the engine ignores the NiAVObject transform for skinned
    # meshes, so a leftover transform reads as a transform-bake regression. The
    # output must carry an identity transform with the fitted verts used as-is.
    build_shape_nif(tmp_path / "src.nif", trans=(0.0, 0.0, 64.0))
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif",
                                override_verts=VERTS)
    assert abs(out.transform.scale - 1.0) < 1e-4
    assert max(abs(c) for c in out.transform.translation) < 1e-4, \
        f"fit-path left a residual transform: {list(out.transform.translation)}"
    ov = [tuple(v) for v in out.verts]
    for i, v in enumerate(VERTS):       # fitted verts used as-is (not lifted)
        assert all(abs(ov[i][k] - v[k]) < 1e-4 for k in range(3)), (i, ov[i])


def test_copy_shape_identity_is_noop(tmp_path):
    # An identity-transform shape must round-trip its verts unchanged (no spurious
    # bake) -- guards against a future change baking when it shouldn't.
    build_shape_nif(tmp_path / "src.nif")
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif")
    ov = [tuple(v) for v in out.verts]
    for i, v in enumerate(VERTS):
        assert all(abs(ov[i][k] - v[k]) < 1e-4 for k in range(3)), (i, ov[i])
