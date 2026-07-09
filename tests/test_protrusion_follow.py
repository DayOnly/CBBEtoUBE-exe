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

"""Protrusion-follow (regional outward morph tracking). A breast/butt is a
PROTRUDING VOLUME: when it inflates, armor covering the surrounding surface at a
stand-off must ride OUT over it or the body pokes through (measured: a plate 4u
off follows a breast slider at ~0.1-0.6x while a hugging fur tracks ~1.0x). The
pointwise nearest-vertex IDW can't capture this because the plate's nearest body
vert under-morphs. `generate_armor_tri(..., body_normals=...)` tops up the
outward displacement to the regional peak. These tests lock the CONTRACT (the
geometric region behaviour is validated on real data by
scripts/verify_protrusion_follow.py):
  * disabled / no normals -> byte-identical to the old pointwise result
  * add-only: never reduces a morph, only pushes outward
  * stand-off gated: hugging verts untouched
  * only outward-expanding sliders get a field (flat/inward sliders no-op)
#protrusion-follow"""
import importlib

import numpy as np

import src.sliderset_gen as sg


class _Morph:
    def __init__(self, name, offsets):
        self.name = name
        self.offsets = offsets


class _Osd:
    def __init__(self, morphs):
        self.morphs = morphs


def _body():
    """Flat +Y plane (y=0) with a 3-vert bump protruding to y=2 at z=4.
    All normals face +Y (outward)."""
    verts, is_bump = [], []
    for z in range(0, 9):
        for x in (-1, 0, 1):
            y = 2.0 if z == 4 else 0.0
            verts.append((float(x), y, float(z)))
            is_bump.append(z == 4)
    verts = np.asarray(verts, dtype=np.float64)
    normals = np.tile(np.array([0.0, 1.0, 0.0]), (len(verts), 1))
    return verts, normals, np.asarray(is_bump)


def _osd(is_bump):
    """'Bump' pushes the bump verts +1 outward; 'Sink' pulls everything inward
    (no outward expansion -> must be ignored by the follow field)."""
    bump = [(i, 0.0, 1.0, 0.0) for i in np.where(is_bump)[0]]
    sink = [(i, 0.0, -0.5, 0.0) for i in range(len(is_bump))]
    return _Osd([_Morph("BaseShapeBump", bump), _Morph("BaseShapeSink", sink)])


def _plate():
    # target: stands off 2u over the FLAT surface beside the bump (its nearest
    # body vert under-morphs -> pointwise leaves it ~static); hug: 0.5u off.
    return {"Plate": np.asarray([
        (0.0, 2.0, 7.0),   # 0 target stand-off
        (0.0, 0.5, 2.0),   # 1 hugging (below gate)
        (0.0, 2.0, 1.0),   # 2 stand-off over far-flat
    ], dtype=np.float64)}


def _delta_map(tri, shape, n):
    """Reconstruct per-vertex (n,3) delta for `shape`'s 'Bump' morph."""
    out = np.zeros((n, 3))
    for ts in tri.shapes:
        if ts.name == shape:
            for m in ts.morphs:
                if m.name == "Bump":
                    for vi, dx, dy, dz in m.offsets:
                        out[int(vi)] = (dx, dy, dz)
    return out


def _gen(body_normals, reach_k=64):
    importlib.reload(sg)
    sg._PF_FIELD_CACHE.clear()
    sg._PF_SELF_CACHE.clear()
    sg._PF_REACH_K = reach_k
    bv, bn, is_bump = _body()
    return sg.generate_armor_tri(
        _plate(), bv, _osd(is_bump), body_shape_name="BaseShape",
        include_body_shapes=False,
        body_normals=(bn if body_normals else None)), bv


def test_no_normals_identical_to_disabled(monkeypatch):
    """body_normals=None must reproduce the old pointwise result exactly, and so
    must the env escape hatch -- the follow path is purely additive/opt-in."""
    tri_none, bv = _gen(body_normals=False)
    d_none = _delta_map(tri_none, "Plate", len(bv))
    monkeypatch.setenv("CBBE2UBE_NO_PROTRUSION_FOLLOW", "1")
    tri_off, _ = _gen(body_normals=True)
    d_off = _delta_map(tri_off, "Plate", len(bv))
    assert np.allclose(d_none, d_off)


def test_standoff_vert_follows_the_protrusion():
    """The stand-off plate vert over the flat beside the bump barely moves
    without follow, but rides OUT (+Y) toward the bump's +1 expansion with it."""
    tri_off, bv = _gen(body_normals=False)
    tri_on, _ = _gen(body_normals=True)
    off = _delta_map(tri_off, "Plate", len(bv))[0]
    on = _delta_map(tri_on, "Plate", len(bv))[0]
    assert np.linalg.norm(on) > np.linalg.norm(off) + 0.4
    assert on[1] > off[1] + 0.4          # the added push is outward (+Y)


def test_add_only_never_reduces():
    """Follow only ADDS outward clearance -- no armor vert's morph shrinks."""
    tri_off, bv = _gen(body_normals=False)
    tri_on, _ = _gen(body_normals=True)
    off = _delta_map(tri_off, "Plate", len(bv))
    on = _delta_map(tri_on, "Plate", len(bv))
    assert np.all(np.linalg.norm(on, axis=1) >= np.linalg.norm(off, axis=1) - 1e-9)


def test_hugging_vert_untouched():
    """A vert below the stand-off gate keeps its pointwise morph (hugging cloth
    already tracks well and must not be disturbed)."""
    tri_off, bv = _gen(body_normals=False)
    tri_on, _ = _gen(body_normals=True)
    assert np.allclose(_delta_map(tri_off, "Plate", len(bv))[1],
                       _delta_map(tri_on, "Plate", len(bv))[1])


def test_inward_slider_builds_no_field():
    """A slider with no outward expansion ('Sink') never gets a follow field, so
    it can't push armor out."""
    bv, bn, is_bump = _body()
    importlib.reload(sg)
    sg._PF_FIELD_CACHE.clear()
    sg._PF_SELF_CACHE.clear()
    fields = sg._pf_follow_fields(bv, bn, _osd(is_bump), "BaseShape",
                                  sg.cKDTree(bv))
    assert "Bump" in fields
    assert "Sink" not in fields


def test_disabled_returns_no_fields(monkeypatch):
    monkeypatch.setattr(sg, "_PROTRUSION_FOLLOW", False)
    bv, bn, is_bump = _body()
    assert sg._pf_follow_fields(bv, bn, _osd(is_bump), "BaseShape",
                                sg.cKDTree(bv)) is None
