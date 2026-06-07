"""Non-identity shape-transform scale bake (#scalebake).

Some source meshes carry a SCALE (and/or rotation) on the geometry's NiAVObject
transform instead of the verts (e.g. Vigilant Shaokhan @ 0.0729, Pelinal arms @
6.86). The engine ignores that transform for SKINNED meshes, so the converted
shape renders at the wrong scale (flung off-body = invisible/static, or
collapsed). `_copy_shape` now bakes the transform into the verts and adjusts the
skin-to-bone by its inverse (bind-preserving), emitting an identity transform.
This must be a strict NO-OP for the identity transforms normal armor ships with.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402

pyn = nc._pynifly()


class _MockShape:
    def __init__(self, tb):
        self.transform = tb


def _tb(scale=1.0, trans=(0.0, 0.0, 0.0)):
    t = pyn.TransformBuf()
    t.set_identity()
    t.scale = scale
    t.translation = (float(trans[0]), float(trans[1]), float(trans[2]))
    return t


def _full(tb):
    M = np.array(tb.to_matrix()._array, dtype=np.float64)
    if abs(abs(np.linalg.det(M[:3, :3])) - 1.0) < 1e-2:
        M[:3, :3] = M[:3, :3] * float(tb.scale)
    return M


def test_identity_transform_is_noop():
    assert nc._shape_bake_matrix(_MockShape(_tb(1.0))) is None


def test_nonidentity_scale_detected_and_folded():
    M = nc._shape_bake_matrix(_MockShape(_tb(0.0729)))
    assert M is not None
    # scalar scale folded into the 3x3 diagonal
    assert abs(M[0, 0] - 0.0729) < 1e-4
    assert abs(M[2, 2] - 0.0729) < 1e-4


def test_oversize_scale_detected():
    M = nc._shape_bake_matrix(_MockShape(_tb(6.862)))
    assert M is not None
    assert abs(M[1, 1] - 6.862) < 1e-3


def test_bind_preserved_after_bake():
    # Bake T (scale 0.0729) into a far-flung vert; the adjusted skin-to-bone must
    # map the baked vert to the SAME bone-space position as the original.
    bake_T = nc._shape_bake_matrix(_MockShape(_tb(0.0729)))
    stb = _tb(0.5, (1.0, 2.0, 3.0))          # arbitrary skin-to-bone
    stb2 = nc._adjust_skin_to_bone_baked(stb, bake_T)
    v = np.array([10.0, 20.0, 1400.0])        # 13.7x-too-large source vert
    vb = (bake_T @ np.r_[v, 1.0])[:3]         # baked vert
    lhs = (_full(stb2) @ np.r_[vb, 1.0])[:3]  # STB' @ baked
    rhs = (_full(stb) @ np.r_[v, 1.0])[:3]    # STB  @ original
    assert np.allclose(lhs, rhs, atol=1e-2)
    # baked vert is brought down to body scale (z ~ 1400*0.0729 ~ 102)
    assert 80.0 < vb[2] < 120.0


def test_bake_brings_geometry_to_body_scale():
    bake_T = nc._shape_bake_matrix(_MockShape(_tb(0.0729)))
    v = np.array([800.0, 800.0, 1480.0])
    vb = (bake_T @ np.r_[v, 1.0])[:3]
    assert np.linalg.norm(vb) < np.linalg.norm(v) / 5.0  # shrank ~13.7x


# ---- pure-translation bake (#scalebake-translation): the ebony-cuirass case ----
# A skinned shape's non-identity TRANSLATION (identity scale/rotation) is ignored
# by the engine -> mesh renders un-lifted = collapsed to the floor. _shape_bake_
# matrix only fires on scale/rotation, so it must NOT catch this; _shape_bake_
# translation catches it and the verts are lifted (NO skin-to-bone adjust).

def test_shape_bake_matrix_ignores_pure_translation():
    # the gap that caused the collapse: a pure translation slipped through unbaked
    assert nc._shape_bake_matrix(_MockShape(_tb(scale=1.0, trans=(0.0, 0.0, 64.68)))) is None


def test_shape_bake_translation_detects_pure_translation():
    s = _MockShape(_tb(scale=1.0, trans=(0.0, -2.03, 64.68)))
    got = nc._shape_bake_translation(s)
    assert got is not None
    assert abs(got[1] - (-2.03)) < 1e-2 and abs(got[2] - 64.68) < 1e-2


def test_shape_bake_translation_identity_is_none():
    assert nc._shape_bake_translation(_MockShape(_tb())) is None


def test_shape_bake_translation_scale_is_none():
    # a non-identity SCALE is _shape_bake_matrix's job (bind-preserving), not this
    assert nc._shape_bake_translation(
        _MockShape(_tb(scale=0.5, trans=(0.0, 0.0, 64.68)))) is None
