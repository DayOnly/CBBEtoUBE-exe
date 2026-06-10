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

"""Guard for the exposed-body-skin gate (open-cleavage breast clip).

An open-cleavage corset / lingerie bakes a slice of the nude body's own skin
(often named 'CBBE'/'3BA') so bare skin shows in the opening. That shape sits
ON the body surface, so `compute_body_blend_skinning` already gives it the
body's graduated weights and it co-moves with the body. The extra
`add_scale_bone_weights` pass would then MAX-propagate redundant breast/butt
weight onto it, so it over-inflates vs the real body under a slider and pokes
through the corset (measured on Eli's Dark-Triss corset: breast-bone fraction
0.12 -> 0.19). `_is_exposed_body_skin_shape` detects such a shape purely by
geometry (verts coincide with the CBBE body) so the caller can skip the pass.

The detector must FIRE on a shape whose verts lie on the body and NOT fire on
draped cloth (which always carries >=~1u of standoff). Measured real-world
separation on the Triss corset: the exposed skin sits 100% within 0.5u of the
body; every cloth shape <=8% within 0.5u.
"""
import numpy as np

from src import nif_convert as nc


def _body_cloud(seed=0):
    """A deterministic pseudo-body point cloud (no Math.random in workflow,
    but tests run in plain Python so a fixed-seed RNG is fine)."""
    rng = np.random.RandomState(seed)
    return rng.uniform(-30.0, 120.0, size=(4000, 3)).astype(np.float64)


def test_detector_fires_on_on_body_skin():
    body = _body_cloud()
    # exposed skin: a subset of body verts nudged by < the coincide dist.
    skin = body[:1200] + 0.1
    assert nc._is_exposed_body_skin_shape(skin, body) is True


def test_detector_ignores_draped_cloth():
    body = _body_cloud()
    # cloth: same footprint but pushed 1.5u off the surface (typical drape).
    cloth = body[:1200] + 1.5
    assert nc._is_exposed_body_skin_shape(cloth, body) is False


def test_detector_ignores_mostly_off_body_shape():
    body = _body_cloud()
    # 80% on-body, 20% far off — below the 0.9 fraction floor -> not skin.
    on = body[:800] + 0.1
    off = body[800:1000] + 10.0
    mixed = np.vstack([on, off])
    assert nc._is_exposed_body_skin_shape(mixed, body) is False


def test_detector_safe_when_no_cbbe_basis():
    # No warp basis available -> safe fallback (current behaviour, no skip).
    assert nc._is_exposed_body_skin_shape(_body_cloud()[:100], None) is False


def test_detector_safe_on_empty():
    body = _body_cloud()
    assert nc._is_exposed_body_skin_shape(np.empty((0, 3)), body) is False


def test_cached_tree_reuses_and_reverifies_identity():
    body = _body_cloud()
    t1 = nc._cached_cbbe_body_tree(body)
    t2 = nc._cached_cbbe_body_tree(body)
    assert t1 is t2                      # same array -> cached tree reused
    other = _body_cloud(seed=1)
    t3 = nc._cached_cbbe_body_tree(other)
    assert t3 is not t1                  # different array -> rebuilt


def test_threshold_constants_sane():
    # Wide margin: skin sits ~0u from the body, tightest cloth ~1u away.
    assert 0.0 < nc.EXPOSED_SKIN_COINCIDE_DIST <= 1.0
    assert 0.5 < nc.EXPOSED_SKIN_COINCIDE_FRAC <= 1.0


# --- _exposed_body_skin_shape_names: which slices trigger body injection ----

_BODY_DIFF = "textures\\actors\\character\\female\\femalebody_1.dds"
_CLOTH_DIFF = "textures\\armor\\corset\\corset_d.dds"


class _Nif:
    def __init__(self, shapes):
        self.shapes = shapes


class _Shape:
    """nif_io.Shape stand-in for the exposed-skin slice detector: verts +
    name + bone_names + textures (read via the `_backing or self` path)."""
    def __init__(self, name, verts, diffuse, nbones=8):
        self.name = name
        self.verts = np.asarray(verts, dtype=np.float64)
        self.bone_names = [f"B{i}" for i in range(nbones)]
        self.textures = {"Diffuse": diffuse}
        self._backing = None


def _body():
    rng = np.random.RandomState(7)
    return rng.uniform(-30.0, 120.0, size=(8000, 3)).astype(np.float64)


def test_exposed_skin_slice_flagged_for_injection():
    body = _body()
    band = body[(body[:, 2] >= 90) & (body[:, 2] <= 110)]   # breast-band slice
    skin = _Shape("CBBE", band[:400] + 0.1, _BODY_DIFF)     # on-body skin slice
    corset = _Shape("triss", band[:400] + 1.5, _CLOTH_DIFF)  # off-body cloth
    decal = _Shape("decal", band[:50] + 0.1, _BODY_DIFF)     # too few verts
    names = nc._exposed_body_skin_shape_names(_Nif([skin, corset, decal]), body)
    assert names == ["CBBE"]


def test_full_inline_body_left_to_classify_shapes():
    # A full-height body skin is handled by classify_shapes' own body path,
    # so the slice detector must NOT also return it (would double-drop).
    body = _body()
    full = _Shape("BodyFull", body[:2000] + 0.1, _BODY_DIFF, nbones=45)
    assert nc._exposed_body_skin_shape_names(_Nif([full]), body) == []


def test_slice_detector_safe_without_basis():
    body = _body()
    skin = _Shape("CBBE", body[:400] + 0.1, _BODY_DIFF)
    assert nc._exposed_body_skin_shape_names(_Nif([skin]), None) == []
