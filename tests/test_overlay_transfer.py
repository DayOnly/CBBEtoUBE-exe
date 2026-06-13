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

"""Unit tests for the body-overlay -> UBE-UV transfer (pure pieces): the
conservative overlay classifier, TGA round-trip, bilinear sampler, UV->3D
rasterizer, and the texconv locator."""
import numpy as np

from src import overlay_transfer as ot


def test_classify_overlay_is_conservative():
    assert ot.classify_overlay("textures/.../00 body.dds") == "body"
    assert ot.classify_overlay("01 hands.dds") == "hands"
    assert ot.classify_overlay("RFeet.dds") == "feet"
    assert ot.classify_overlay("02 head m.dds") == "head"
    assert ot.classify_overlay("warpaint_face.dds") == "head"
    # makeup / unlabeled overlays must NOT be treated as body (don't corrupt)
    assert ot.classify_overlay("blush_01.dds") == "other"
    assert ot.classify_overlay("eyeliner.dds") == "other"
    assert ot.classify_overlay("tribal01.dds") == "other"


def test_tga_roundtrip(tmp_path):
    rng = np.arange(8 * 6 * 4, dtype=np.uint8).reshape(8, 6, 4)
    p = tmp_path / "t.tga"
    ot._write_tga_rgba(rng, p)
    back = ot._read_tga_rgba(p)
    assert back.shape == rng.shape
    assert np.array_equal(back, rng)


def test_bilinear_sample_corners_and_center():
    # 2x2 image: distinct corners; sample exact corners + center
    img = np.array([[[0, 0, 0, 0], [255, 0, 0, 255]],
                    [[0, 255, 0, 255], [0, 0, 255, 255]]], np.uint8)
    u = np.array([0.0, 1.0, 0.0, 1.0, 0.5])
    v = np.array([0.0, 0.0, 1.0, 1.0, 0.5])
    s = ot._bilinear_sample(img, u, v)
    assert np.allclose(s[0], [0, 0, 0, 0])          # top-left
    assert np.allclose(s[1], [255, 0, 0, 255])      # top-right
    assert np.allclose(s[2], [0, 255, 0, 255])      # bottom-left
    assert np.allclose(s[3], [0, 0, 255, 255])      # bottom-right
    assert np.allclose(s[4], np.mean(img.reshape(4, 4), axis=0))  # center = avg


def test_rasterize_uv_to_3d_barycentric():
    # one triangle filling the lower-left half of UV; 3D verts encode position
    uv = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    verts = np.array([[0.0, 0, 0], [10.0, 0, 0], [0.0, 10, 0]])
    tris = np.array([[0, 1, 2]])
    T = 8
    pt, cov = ot._rasterize_uv_to_3d(uv, verts, tris, T)
    assert cov.any()
    # a covered texel near UV (0.1,0.1) -> 3D near (1,1,0)
    ys, xs = np.where(cov)
    # pick the texel closest to (0.5,0.5) in UV
    uvpix = np.stack([xs / (T - 1), ys / (T - 1)], 1)
    i = np.argmin(np.abs(uvpix - 0.3).sum(1))
    # barycentric: 3D x ~ 10*u, y ~ 10*v
    assert abs(pt[ys[i], xs[i], 0] - 10 * uvpix[i, 0]) < 2.0
    assert abs(pt[ys[i], xs[i], 1] - 10 * uvpix[i, 1]) < 2.0


def test_find_texconv_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "texconv.exe"
    fake.write_bytes(b"\x00")
    monkeypatch.setenv("CBBE2UBE_TEXCONV", str(fake))
    ot._TEXCONV_CACHE.clear()
    assert ot.find_texconv() == fake
    ot._TEXCONV_CACHE.clear()
