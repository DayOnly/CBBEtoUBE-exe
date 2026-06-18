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
