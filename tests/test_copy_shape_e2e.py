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

from tests.synthetic_nif import (VERTS, build_shape_nif, build_skinned_shape_nif,
                                  build_effect_shader_nif, build_colored_shape_nif,
                                  copy_shape_into_fresh, pynifly_available)
import src.nif_convert as nc  # noqa: E402

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


def test_copy_shape_fitpath_skinned_resets_transform_keeps_weights(tmp_path):
    # GAP #3 (SKINNED fit path): the engine ignores a skinned shape's NiAVObject
    # transform and the fit verts are body-positioned, so a source translation must
    # be reset to identity (else it flings the mesh off-body / collapses it) -- and
    # the bone weights must survive the copy.
    build_skinned_shape_nif(tmp_path / "src.nif", trans=(0.0, 0.0, 64.0))
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif",
                                override_verts=VERTS)
    assert max(abs(c) for c in out.transform.translation) < 1e-4, \
        f"skinned fit-path left a residual transform: {list(out.transform.translation)}"
    assert out.bone_weights, "bone weights were dropped on the skinned copy"


def test_copy_shape_fitpath_nonskinned_preserves_transform(tmp_path):
    # GATE (NON-skinned fit path): a non-skinned shape's NiAVObject transform IS
    # engine-honored, so the fit path must NOT zero it (would misposition the
    # shape). Confirms the bone_names gate on the gap-#3 reset (review finding F1).
    build_shape_nif(tmp_path / "src.nif", trans=(0.0, 0.0, 64.0))   # no bones
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif",
                                override_verts=VERTS)
    assert abs(out.transform.translation[2] - 64.0) < 1e-4, \
        "non-skinned transform was wrongly zeroed (misposition risk)"


def test_copy_shape_skinned_offset_g2s_preserves_transform(tmp_path):
    # A SKINNED shape with an authored OFFSET global_to_skin and a compensating
    # NiAVObject transform (furexarot SMP armor, e.g. the elven cuirass) on the
    # NON-fit copy path must KEEP its transform: the g2s + transform are a matched
    # pair the engine uses to place the bounding sphere. Zeroing it lands the cull
    # bound ~g2s-offset below the geometry -> the shape is frustum-culled / invisible
    # at angles (the regression this guards). Distinct from the FIT path, where the
    # verts ARE body-positioned and the transform IS reset (test above).
    src = build_skinned_shape_nif(tmp_path / "src.nif", trans=(0.0, 0.0, 120.3),
                                  g2s_trans=(0.0, 0.0, -120.3))
    s = nc._pynifly().NifFile(filepath=str(src)).shapes[0]
    assert s.has_global_to_skin, "test setup: offset g2s not set"
    out = copy_shape_into_fresh(src, tmp_path / "dst.nif")   # NO override_verts (not fit)
    assert abs(out.transform.translation[2] - 120.3) < 1e-3, \
        f"skinned offset-g2s transform was zeroed (cull-bound regression): " \
        f"{list(out.transform.translation)}"


def test_copy_shape_fitpath_offset_g2s_preserves_transform(tmp_path):
    # The FIT path (override_verts) for a SKINNED offset-g2s shape -- the elven
    # cuirass's ACTUAL path (a phase-1 "copy" still fits verts). The fit clause
    # `override_verts is not None and bone_names` fires here, so the offset-g2s gate
    # must SUPPRESS the reset; the non-fit guard above does NOT cover this path.
    # Zeroing the transform drops the cull bound ~g2s below the geometry ->
    # frustum-culled / INVISIBLE LEGS on equip (the regression this guards).
    src = build_skinned_shape_nif(tmp_path / "src.nif", trans=(0.0, 0.0, 120.3),
                                  g2s_trans=(0.0, 0.0, -120.3))
    out = copy_shape_into_fresh(src, tmp_path / "dst.nif", override_verts=VERTS)
    assert abs(out.transform.translation[2] - 120.3) < 1e-3, \
        f"fit-path offset-g2s transform was zeroed (invisible-legs regression): " \
        f"{list(out.transform.translation)}"


def test_copy_shape_identity_is_noop(tmp_path):
    # An identity-transform shape must round-trip its verts unchanged (no spurious
    # bake) -- guards against a future change baking when it shouldn't.
    build_shape_nif(tmp_path / "src.nif")
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif")
    ov = [tuple(v) for v in out.verts]
    for i, v in enumerate(VERTS):
        assert all(abs(ov[i][k] - v[k]) < 1e-4 for k in range(3)), (i, ov[i])


def test_copy_shape_preserves_effect_shader(tmp_path):
    # REGRESSION (Daedric cuirass glow): the red glow is a BSEffectShaderProperty overlay.
    # createShapeFromData only makes BSLightingShaderProperty, which would downgrade it
    # (emissive zeroed, greyscale dropped -> renders white). _copy_shape must transplant the
    # effect shader so the glow keeps its COLOUR. By DEFAULT it is STATIC (no animation
    # controller): the controller chain doesn't survive the HDT-inject reload+re-save and
    # crashes the engine on cloth+glow armors (_EFFECT_GLOW_ANIM). The colour still works.
    src = build_effect_shader_nif(tmp_path / "glow_src.nif", controlled_var=8)
    src_shape = nc._pynifly().NifFile(filepath=str(src)).shapes[0]
    assert src_shape.shader_block_name == "BSEffectShaderProperty"   # sanity
    out = copy_shape_into_fresh(src, tmp_path / "glow_out.nif")
    assert out.shader_block_name == "BSEffectShaderProperty", \
        "effect shader was downgraded to lighting shader (glow would render white)"
    assert out.shader.controller is None, \
        "default must be a STATIC glow (no controller) -- the animated chain CTDs on re-save"


def test_copy_shape_effect_shader_animation_optin(tmp_path, monkeypatch):
    # With CBBE2UBE_GLOW_ANIM (opt-in), the full animation controller chain is transplanted
    # (controller -> interpolator -> NiFloatData keys). Safe ONLY on glow armors that are
    # never reload+re-saved (no SMP), so it's not the default.
    monkeypatch.setattr(nc, "_EFFECT_GLOW_ANIM", True)
    src = build_effect_shader_nif(tmp_path / "glow_src.nif", controlled_var=8)
    out = copy_shape_into_fresh(src, tmp_path / "glow_out.nif")
    assert out.shader_block_name == "BSEffectShaderProperty"
    ctrl = out.shader.controller
    assert ctrl is not None, "opt-in animation controller was not transplanted"
    assert ctrl.properties.controlledVariable == 8, "controlled variable lost"
    assert ctrl.properties.targetID == out.shader.id, "controller does not target its shader"
    data = out.file.read_node(id=ctrl.interpolator.properties.dataID)
    assert len(data.keys) == 2, "animation keyframes lost"


def test_copy_shape_preserves_vertex_colors(tmp_path):
    # REGRESSION (Daedric glow rendered solid red instead of faded): the glow's fade
    # is a per-vertex ALPHA gradient (SLSF2_Vertex_Colors). createShapeFromData makes
    # a COLORLESS shape, so _copy_shape must copy the vertex colors -- else the overlay
    # renders opaque/solid (the alpha gradient is lost).
    src = build_colored_shape_nif(tmp_path / "c_src.nif", alphas=[0.0, 0.33, 0.66, 1.0])
    out = copy_shape_into_fresh(src, tmp_path / "c_out.nif")
    cols = out.colors
    assert cols is not None and len(cols) == len(VERTS), "vertex colors were dropped"
    alphas = sorted(round(c[3], 2) for c in cols)
    assert alphas[0] < 0.05 and alphas[-1] > 0.95, alphas   # alpha gradient preserved


def test_copy_shape_lighting_shader_unaffected(tmp_path):
    # The effect-shader branch must not touch ordinary lighting-shader shapes: a
    # normal shape still copies as a BSLightingShaderProperty.
    build_shape_nif(tmp_path / "src.nif")
    out = copy_shape_into_fresh(tmp_path / "src.nif", tmp_path / "dst.nif")
    assert out.shader_block_name == "BSLightingShaderProperty"
