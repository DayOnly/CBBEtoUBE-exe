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

"""Guard for the body-normal inflation fix (a gold-corset crushed-foil bug).

`inflate_armor_outward` used to derive its push direction from
(armor_vert - nearest_body_vert) normalized. For armor that HUGS the body
(a tight gold corset) that offset vector is near-zero, so normalizing it
amplifies floating-point noise into a random per-vert direction -> a uniform
inflation crumples the surface. Pushing along the body's smooth vertex normal
instead keeps a contiguous surface smooth. These tests pin that behaviour."""
import numpy as np
from src import nif_convert as nc


def _sphere(n_theta=40, n_phi=40, r=10.0):
    """A sphere's verts + outward unit normals (normal == position / r)."""
    th = np.linspace(0.15, np.pi - 0.15, n_theta)
    ph = np.linspace(0, 2 * np.pi, n_phi, endpoint=False)
    pts = []
    for t in th:
        for p in ph:
            pts.append([r * np.sin(t) * np.cos(p),
                        r * np.sin(t) * np.sin(p),
                        r * np.cos(t)])
    v = np.asarray(pts, np.float64)
    nrm = v / np.linalg.norm(v, axis=1, keepdims=True)
    return v, nrm


def _roughness(v):
    from scipy.spatial import cKDTree
    t = cKDTree(v)
    _, nn = t.query(v, k=7)
    neigh = v[nn[:, 1:]].mean(axis=1)
    return float(np.percentile(np.linalg.norm(v - neigh, axis=1), 95))


def test_body_normal_inflation_keeps_hugging_surface_smooth():
    bv, bn = _sphere()
    # Armor = the body surface plus a TINY, direction-varying gap (a rigid
    # piece hugging the body). The gap vector is sub-unit and its direction
    # wobbles vert-to-vert, exactly the regime where normalizing
    # (armor - nearest_body) amplifies noise. Deterministic (no RNG).
    i = np.arange(len(bv))
    wobble = 1e-3 * np.stack(
        [np.sin(i * 1.0), np.cos(i * 1.3), np.sin(i * 0.7)], axis=1)
    armor = bv + wobble
    r0 = _roughness(armor)

    noisy = nc.inflate_armor_outward(armor, bv, magnitude=0.7,
                                     close_threshold=2.0, body_normals=None)
    smooth = nc.inflate_armor_outward(armor, bv, magnitude=0.7,
                                      close_threshold=2.0, body_normals=bn)

    rn, rs = _roughness(noisy), _roughness(smooth)
    # The old (no-normal) path crumples a hugging surface badly...
    assert rn > r0 + 0.2, f"expected noisy path to crumple: r0={r0:.3f} rn={rn:.3f}"
    # ...the body-normal path stays essentially as smooth as the input
    # (the ~0.28 floor here is the sphere sampling's own spatial roughness).
    assert rs < r0 + 0.05, f"normal path not smooth: r0={r0:.3f} rs={rs:.3f}"
    assert rn > rs + 0.3, f"normal path not much smoother: rn={rn:.3f} rs={rs:.3f}"


def test_body_normal_inflation_still_pushes_outward():
    bv, bn = _sphere(r=10.0)
    armor = bv.copy()                      # on the surface (radius 10)
    out = nc.inflate_armor_outward(armor, bv, magnitude=0.5,
                                   close_threshold=2.0, body_normals=bn)
    # every vert moved outward ~0.5u (radius grew), none collapsed inward
    radii = np.linalg.norm(out, axis=1)
    assert radii.min() > 10.0, "inflation must push verts outward"
    assert abs(np.median(radii) - 10.5) < 0.1, "expected ~0.5u outward push"


def test_inflate_empty_input_returns_cleanly():
    # Zero-vert armor (or empty body) must return cleanly rather than raise on
    # cKDTree / idxs.max() -- the shape would otherwise silently lose clearance.
    body = np.zeros((3, 3), dtype=np.float64)
    out = nc.inflate_armor_outward(np.zeros((0, 3), dtype=np.float64), body)
    assert out.shape[0] == 0                     # empty armor -> empty out

    armor = np.array([[1.0, 0.0, 0.0]], dtype=np.float64)
    out2 = nc.inflate_armor_outward(armor, np.zeros((0, 3), dtype=np.float64))
    assert np.allclose(out2, armor)              # empty body -> armor unchanged
