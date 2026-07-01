"""Unit tests for the effect-overlay ride, cross-plate seam weld, and seam
skin-match passes (src/nif_convert.py).

These passes operate on the pass-1 `shape_jobs` list, accessing only a small
surface of each source shape, so we drive them with lightweight duck-typed
mock shapes rather than building full NIFs.
"""
import types

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import nif_convert as nc  # noqa: E402

_EFFECT_BUFTYPE = getattr(
    nc._pynifly().PynBufferTypes, "BSEffectShaderPropertyBufType", 33)


class MockShape:
    def __init__(self, name, verts, *, effect=False, textures=None,
                 bone_names=None, bone_weights=None, xforms=None):
        self.name = name
        self.verts = np.asarray(verts, dtype=np.float64)
        self.textures = {"Diffuse": "x.dds"} if textures is None else textures
        self.has_global_to_skin = False       # -> identity g2s in the passes
        self.bone_names = list(bone_names or [])
        self.bone_weights = bone_weights or {}
        self._xforms = xforms or {}
        self.shader = (types.SimpleNamespace(
            properties=types.SimpleNamespace(bufType=_EFFECT_BUFTYPE))
            if effect else None)

    def get_shape_skin_to_bone(self, bn):
        return self._xforms.get(bn)


def _job(shape, final=None, override_skin=None):
    return {
        "src": shape,
        "verts": np.asarray(shape.verts if final is None else final,
                            dtype=np.float64),
        "verts_modified": final is not None,
        "override_skin": override_skin,
    }


# --------------------------- seam weld -------------------------------------

def test_seam_weld_closes_coincident_cross_plate_gap():
    # Two plates sharing a coincident seam vert (index 0). Their finals drift
    # apart; the weld pulls both to the centroid.
    a = MockShape("A", [[0, 0, 0], [10, 0, 0]])
    b = MockShape("B", [[0, 0, 0], [-10, 0, 0]])
    ja = _job(a, final=[[0, 0, 0.3], [10, 0, 0.3]])
    jb = _job(b, final=[[0, 0, -0.3], [-10, 0, -0.3]])
    n = nc._weld_cross_shape_seams([ja, jb])
    assert n == 2
    # seam verts (index 0 of each) welded to the same point (z-centroid 0)
    np.testing.assert_allclose(ja["verts"][0], jb["verts"][0], atol=1e-9)
    np.testing.assert_allclose(ja["verts"][0], [0, 0, 0], atol=1e-9)
    # non-seam verts untouched
    np.testing.assert_allclose(ja["verts"][1], [10, 0, 0.3], atol=1e-9)


def test_seam_weld_ignores_offset_layers():
    # A layered garment: B sits 0.2u ABOVE A (NOT coincident in source). The
    # weld must NOT touch them -- that would collapse an intentional layer.
    a = MockShape("A", [[0, 0, 0], [10, 0, 0]])
    b = MockShape("B", [[0, 0, 0.2], [10, 0, 0.2]])   # 0.2 > tol 0.05
    ja = _job(a, final=[[0, 0, 0.0], [10, 0, 0.0]])
    jb = _job(b, final=[[0, 0, 0.5], [10, 0, 0.5]])
    n = nc._weld_cross_shape_seams([ja, jb])
    assert n == 0
    np.testing.assert_allclose(jb["verts"][0], [0, 0, 0.5], atol=1e-9)


def test_seam_weld_skips_effect_overlays():
    # An effect-shader glow coincident with a plate must not be welded as a
    # plate (it rides the plate instead, a separate pass).
    plate = MockShape("plate", [[0, 0, 0]])
    glow = MockShape("glow", [[0, 0, 0]], effect=True)
    jp = _job(plate, final=[[0, 0, 0.3]])
    jg = _job(glow, final=[[0, 0, -0.3]])
    n = nc._weld_cross_shape_seams([jp, jg])
    assert n == 0  # only one non-effect plate -> nothing to weld


# ----------------------- seam skin-match -----------------------------------

def test_seam_skin_match_unifies_weights_incl_source_skinned_member():
    # A: reskinned (has override_skin). B: source-skinned (override_skin None,
    # carries its own bone_names/weights). After weld+match the shared seam vert
    # (index 0) must have IDENTICAL weights on both plates.
    a = MockShape("A", [[0, 0, 0], [10, 0, 0]])
    b = MockShape(
        "B", [[0, 0, 0], [-10, 0, 0]],
        bone_names=["NPC Spine2 [Spn2]", "NPC Pelvis [Pelv]"],
        bone_weights={"NPC Spine2 [Spn2]": [(0, 0.8), (1, 1.0)],
                      "NPC Pelvis [Pelv]": [(0, 0.2)]},
        xforms={"NPC Spine2 [Spn2]": object(), "NPC Pelvis [Pelv]": object()},
    )
    osk_a = {
        "bones": ["NPC Spine [Spn0]", "NPC Spine2 [Spn2]"],
        "xforms": {"NPC Spine [Spn0]": object(),
                   "NPC Spine2 [Spn2]": object()},
        "weights": {"NPC Spine [Spn0]": [(0, 0.6), (1, 1.0)],
                    "NPC Spine2 [Spn2]": [(0, 0.4)]},
    }
    ja = _job(a, final=[[0, 0, 0.3], [10, 0, 0.3]], override_skin=osk_a)
    jb = _job(b, final=[[0, 0, -0.3], [-10, 0, -0.3]])   # override_skin None
    nc._weld_cross_shape_seams([ja, jb])

    def vert0_weights(osk):
        out = {}
        for bn, pairs in osk["weights"].items():
            for vi, w in pairs:
                if int(vi) == 0:
                    out[bn] = out.get(bn, 0.0) + float(w)
        return out

    # B got a synthesized override_skin
    assert jb["override_skin"] is not None
    wa, wb = vert0_weights(ja["override_skin"]), vert0_weights(jb["override_skin"])
    assert set(wa) == set(wb)
    for bn in wa:
        assert wa[bn] == pytest.approx(wb[bn], abs=1e-9)
    assert sum(wa.values()) == pytest.approx(1.0, abs=1e-6)
    assert len(wa) <= 4  # engine per-vertex bone cap


# --------------------------- glow ride -------------------------------------

def test_glow_ride_follows_plate_and_preserves_offset():
    # Plate moves +z by 1.0; the glow decal sits 0.03u off it and rides along.
    plate = MockShape("plate", [[0, 0, 0], [5, 0, 0]])
    glow = MockShape("glow", [[0, 0, 0.03], [5, 0, 0.03]], effect=True)
    jp = _job(plate, final=[[0, 0, 1.0], [5, 0, 1.0]])
    jg = _job(glow)  # unwarped -> verts == source
    n = nc._ride_effect_overlays_on_plate([jp, jg])
    assert n == 2
    # glow_final = plate_final[nearest] + (glow_src - plate_src[nearest])
    np.testing.assert_allclose(jg["verts"][0], [0, 0, 1.03], atol=1e-6)
    np.testing.assert_allclose(jg["verts"][1], [5, 0, 1.03], atol=1e-6)


def test_glow_ride_skips_far_verts():
    # A glow vert with no plate within ride_max keeps its own warp.
    plate = MockShape("plate", [[0, 0, 0]])
    glow = MockShape("glow", [[0, 0, 0.03], [0, 0, 50.0]], effect=True)
    jp = _job(plate, final=[[0, 0, 1.0]])
    jg = _job(glow, final=[[0, 0, 0.03], [0, 0, 50.0]])
    n = nc._ride_effect_overlays_on_plate([jp, jg], ride_max=2.0)
    assert n == 1  # only the near vert rode
    np.testing.assert_allclose(jg["verts"][0], [0, 0, 1.03], atol=1e-6)
    np.testing.assert_allclose(jg["verts"][1], [0, 0, 50.0], atol=1e-9)
