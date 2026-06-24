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

"""Build tiny REAL NIFs in-memory via pynifly for end-to-end converter tests.

The suite is otherwise pure-logic / ESP-level and mocks pynifly, so the
read->transform->write boundary (scale/translation bake, _copy_shape) has no
output-level coverage -- a regression there breaks every converted mesh yet
passes all unit tests. These fixtures are deterministic (fixed geometry, no
external assets) and run in milliseconds.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402

# A 4-vertex tetra: enough for vert/tri/transform assertions, trivially small.
VERTS = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
TRIS = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]


def pynifly_available() -> bool:
    """True if the pynifly native lib loads (else e2e tests skip)."""
    try:
        nc._pynifly()
        return True
    except Exception:
        return False


def build_shape_nif(path, *, name="TestShape", scale=1.0,
                    trans=(0.0, 0.0, 0.0), verts=None, tris=None):
    """Write a SKYRIMSE NIF with ONE shape carrying the given NiAVObject transform
    (scale + translation). Returns `path`. Reload with `pyn().NifFile(filepath=...)`."""
    pyn = nc._pynifly()
    verts = list(VERTS if verts is None else verts)
    tris = list(TRIS if tris is None else tris)
    uvs = [(0.0, 0.0)] * len(verts)
    normals = [(0.0, 0.0, 1.0)] * len(verts)
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    sh = nif.createShapeFromData(name, verts, tris, uvs, normals)
    tb = pyn.TransformBuf()
    tb.set_identity()
    tb.scale = scale
    tb.translation = tuple(trans)
    sh.transform = tb
    nif.save()
    return path


def build_effect_shader_nif(path, *, name="GlowShape", controlled_var=8):
    """Write a SKYRIMSE NIF whose ONE shape carries a BSEffectShaderProperty -- the
    additive glow/decal shader Daedric armor's red glow uses -- WITH a float-controller
    animation chain (controller -> interpolator -> NiFloatData + 2 keys), as that glow
    has. Exercises the converter's effect-shader + controller transplant. Returns
    `path`."""
    pyn = nc._pynifly()
    verts = list(VERTS)
    tris = list(TRIS)
    uvs = [(0.0, 0.0)] * len(verts)
    normals = [(0.0, 0.0, 1.0)] * len(verts)
    none_id = pyn.NODEID_NONE
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    sh = nif.createShapeFromData(name, verts, tris, uvs, normals)
    tb = pyn.TransformBuf()
    tb.set_identity()
    sh.transform = tb
    # Animation chain (built bottom-up; the effect shader is created last so it can
    # reference the controller, whose targetID predicts the shader's id -- ids are
    # sequential and NifFile.save() remaps refs).
    dbuf = pyn.NiFloatData.getbuf()
    try:
        dbuf.keys.interpolation = pyn.NiKeyType.QUADRATIC_KEY
    except Exception:
        pass
    data = nif.add_block(None, dbuf, parent=None)

    class _K:
        def __init__(self, t, v):
            self.time = t
            self.value = v
            self.forward = 0.0
            self.backward = 0.0

    data.keys_add(_K(0.0, 1.0))
    data.keys_add(_K(2.0, 0.0))
    ibuf = pyn.NiFloatInterpolator.getbuf()
    ibuf.dataID = data.id
    interp = nif.add_block(None, ibuf, parent=None)
    cbuf = pyn.BSEffectShaderPropertyFloatController.getbuf()
    cbuf.interpolatorID = interp.id
    cbuf.targetID = interp.id + 2          # the effect shader, created next
    cbuf.nextControllerID = none_id
    cbuf.controlledVariable = controlled_var
    cbuf.flags = 72
    cbuf.frequency = 1.0
    cbuf.stopTime = 2.0
    ctrl = nif.add_block(None, cbuf, parent=None)
    ebuf = pyn.BSEffectShaderProperty.getbuf()
    ebuf.controllerID = ctrl.id
    eff = nif.add_block("", ebuf, parent=sh)
    sh.properties.shaderPropertyID = eff.id
    sh._shader = None
    nif.save()
    return path


def build_colored_shape_nif(path, *, name="ColorShape", alphas=None):
    """Write a SKYRIMSE NIF whose ONE shape carries per-vertex RGBA (white RGB, the
    given per-vertex alphas -- an alpha gradient, as the Daedric glow's fade is).
    Returns `path`."""
    pyn = nc._pynifly()
    verts = list(VERTS)
    tris = list(TRIS)
    uvs = [(0.0, 0.0)] * len(verts)
    normals = [(0.0, 0.0, 1.0)] * len(verts)
    if alphas is None:
        alphas = [i / (len(verts) - 1) for i in range(len(verts))]
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    sh = nif.createShapeFromData(name, verts, tris, uvs, normals)
    tb = pyn.TransformBuf()
    tb.set_identity()
    sh.transform = tb
    sh.set_colors([(1.0, 1.0, 1.0, float(a)) for a in alphas])
    nif.save()
    return path


_DEFAULT_BONES = ("NPC Spine [Spn0]", "NPC L Thigh [LThg]")


def build_skinned_shape_nif(path, *, name="SkinShape", scale=1.0,
                            trans=(0.0, 0.0, 0.0), bones=_DEFAULT_BONES):
    """Like build_shape_nif but SKINNED, so the shape round-trips as a skinned mesh
    (bone weights persist). Requires the pynifly sequence skin() -> add_bone (ALL
    first) -> set_skin_to_bone_xform -> setShapeWeights. Verts are split evenly
    across the bones (each weight 1.0). Returns `path`."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    uvs = [(0.0, 0.0)] * len(VERTS)
    normals = [(0.0, 0.0, 1.0)] * len(VERTS)
    sh = nif.createShapeFromData(name, VERTS, TRIS, uvs, normals)
    tb = pyn.TransformBuf()
    tb.set_identity()
    tb.scale = scale
    tb.translation = tuple(trans)
    sh.transform = tb
    sh.skin()
    for bn in bones:
        sh.add_bone(bn)
    idt = pyn.TransformBuf()
    idt.set_identity()
    for bn in bones:
        try:
            sh.set_skin_to_bone_xform(bn, idt)
        except Exception:
            pass
    half = len(VERTS) // 2
    sh.setShapeWeights(bones[0], [(i, 1.0) for i in range(half)])
    sh.setShapeWeights(bones[1], [(i, 1.0) for i in range(half, len(VERTS))])
    nif.save()
    return path


def copy_shape_into_fresh(src_path, dst_path, override_verts=None):
    """Reload src, run the real `_copy_shape` of its first shape into a fresh NIF,
    save + reload, and return the resulting shape (final on-disk bytes).

    Pass `override_verts` to exercise the FIT path (body-positioned verts), as the
    real body-fit conversion does."""
    pyn = nc._pynifly()
    s = pyn.NifFile(filepath=str(src_path)).shapes[0]
    dst = pyn.NifFile()
    dst.initialize("SKYRIMSE", str(dst_path))
    if override_verts is None:
        nc._copy_shape(s, dst)
    else:
        nc._copy_shape(s, dst, override_verts=list(override_verts))
    dst.save()
    return pyn.NifFile(filepath=str(dst_path)).shapes[0]
