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

"""The Live steps tab's large view: one step of the newest piece as a lit,
shadowed, slowly turning 3D picture. #live-steps

The window only, never a conversion. Triangles are filled by
native/liveview.dll (z-buffer plus interpolated normal and colour per pixel);
the lighting is numpy on the pixels the piece covers: a key light, a fill, a
rim light so the outline stands out against any theme, a little specular, a
soft overhead shadow on a floor disc, and depth fog toward the background.

The camera stays put and the piece turns about the vertical axis through its
centre, so the lights stay where they are as it turns, like a studio
turntable. The UBE body, as written, stands in grey behind the cloth in every
step after the warp; the Source step has no UBE body to stand in (it is in the
CBBE body's space). The Zap step draws the body as the game does (front faces
only) in skin tone with the cloth over it at 55%: kept skin shows through the
cloth, skin the zap removed is see-through.
"""
from __future__ import annotations

import ctypes
import functools
import sys
from pathlib import Path

import numpy as np

from . import live_steps

BODY_GREY = (0.55, 0.56, 0.60)


@functools.lru_cache(maxsize=1)
def _dll():
    """liveview.dll beside the exe (frozen) or in native/ (source). None when absent:
    then the tab shows the step pictures without the large view."""
    here = Path(__file__).resolve().parent
    for c in (Path(getattr(sys, "_MEIPASS", "")) / "liveview.dll",
              here.parent / "native" / "liveview.dll"):
        if c.is_file():
            try:
                d = ctypes.CDLL(str(c))
            except OSError:
                return None
            f = np.ctypeslib.ndpointer(np.float32, flags="C_CONTIGUOUS")
            i = np.ctypeslib.ndpointer(np.int32, flags="C_CONTIGUOUS")
            d.lv_raster.argtypes = [f, f, f, f, ctypes.c_int, ctypes.c_int, i, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, f, f]
            d.lv_raster.restype = ctypes.c_int
            return d
    return None


def available() -> bool:
    return _dll() is not None


def vnormals(V, T):
    """Area-weighted vertex normals."""
    V = np.asarray(V, np.float32)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, T[:, k], n)
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    ln[ln == 0] = 1
    return (N / ln).astype(np.float32)


def outward_sign(V, T) -> float:
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    return 1.0 if ((V[T].mean(1) - V.mean(0)) * n).sum(1).mean() >= 0 else -1.0


class Camera:
    """A mild perspective camera in front of the piece (+Y), a little above it."""

    def __init__(self, centre, radius, w, h, elev=0.12, fov=0.55):
        self.w, self.h = w, h
        c = np.asarray(centre, np.float32)
        dist = radius / np.tan(fov / 2) * 1.08
        d = np.array([0.0, np.cos(elev), np.sin(elev)], np.float32)
        self.pos = c + d * dist
        f = c - self.pos
        f /= np.linalg.norm(f)
        r = np.cross(f, [0, 0, 1]).astype(np.float32)
        r /= np.linalg.norm(r)
        self.f, self.r, self.u = f, r, np.cross(r, f)
        self.focal = (h / 2) / np.tan(fov / 2)

    def project(self, V):
        Q = np.asarray(V, np.float32) - self.pos
        z = np.maximum(Q @ self.f, 1e-3)
        x = self.w / 2 + (Q @ self.r) / z * self.focal
        y = self.h / 2 - (Q @ self.u) / z * self.focal
        return (np.ascontiguousarray(x, np.float32), np.ascontiguousarray(y, np.float32),
                np.ascontiguousarray(z, np.float32))

    def to_cam(self, N):
        """World normals -> (right, up, toward the camera)."""
        return np.stack([N @ self.r, N @ self.u, -(N @ self.f)], -1).astype(np.float32)


def raster(cam, meshes):
    """[(V, T, N, C)] -> depth (h*w,) and attributes (h*w, 6): camera-space normal, colour."""
    w, h = cam.w, cam.h
    z = np.full(w * h, np.inf, np.float32)
    a = np.zeros(w * h * 6, np.float32)
    dll = _dll()
    for V, T, N, C in meshes:
        if T is None or not len(T):
            continue
        x, y, d = cam.project(V)
        A = np.ascontiguousarray(np.concatenate([cam.to_cam(N), C], 1), np.float32)
        dll.lv_raster(x, y, d, A, 6, len(V), np.ascontiguousarray(T, np.int32), len(T), w, h, z, a)
    return z, a.reshape(-1, 6)


_KEY = np.array([-0.45, 0.55, 0.70], np.float32)
_KEY /= np.linalg.norm(_KEY)
_FILL = np.array([0.6, 0.1, 0.6], np.float32)
_FILL /= np.linalg.norm(_FILL)
_HALF = _KEY + np.array([0, 0, 1], np.float32)
_HALF /= np.linalg.norm(_HALF)
_RIM = np.array([0.85, 0.9, 1.0], np.float32)


def shade(a):
    """Lit colour for (n, 6) interpolated attributes."""
    n = a[:, :3]
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1
    n = n / ln
    n = np.where(n[:, 2:3] < 0, -n, n)             # cloth is two-sided: face the camera
    col = a[:, 3:6]
    diff = np.clip(n @ _KEY, 0, 1)[:, None]
    fill = np.clip(n @ _FILL, 0, 1)[:, None]
    rim = (1 - np.clip(n[:, 2], 0, 1))[:, None] ** 3
    spec = np.clip(n @ _HALF, 0, 1)[:, None] ** 40
    out = col * (0.28 + 0.62 * diff + 0.18 * fill) + 0.35 * rim * _RIM + 0.12 * spec
    return np.clip(out, 0, 1)


def _blur(m, r):
    for ax in (0, 1):
        pad = [(r + 1, r) if i == ax else (0, 0) for i in range(2)]
        c = np.cumsum(np.pad(m, pad), axis=ax)
        n = c.shape[ax]
        m = (np.take(c, range(2 * r + 1, n), axis=ax)
             - np.take(c, range(0, n - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return m


class Scene:
    """One step of one piece, ready to be drawn at any turn of the turntable.

    `cloth` and `body`: [(V, T, rgb)] with world-space verts and a per-vert colour."""

    SHADOW_GRID = 160

    def __init__(self, cloth, body=(), zap=False):
        self.cloth = [(np.asarray(V, np.float32), np.asarray(T, np.int32),
                       vnormals(V, np.asarray(T, np.int64)), np.asarray(C, np.float32))
                      for V, T, C in cloth if len(T)]
        self.body = [(np.asarray(V, np.float32), np.asarray(T, np.int32),
                      vnormals(V, np.asarray(T, np.int64)), np.asarray(C, np.float32),
                      outward_sign(np.asarray(V, np.float32), np.asarray(T, np.int64)))
                     for V, T, C in body if len(T)]
        self.zap = zap
        own = [m[0] for m in self.cloth] or [m[0] for m in self.body]
        P = np.concatenate(own) if own else np.zeros((1, 3), np.float32)
        P = P[np.isfinite(P).all(1)]
        if not len(P):
            P = np.zeros((1, 3), np.float32)
        lo, hi = np.percentile(P, 0.5, 0), np.percentile(P, 99.5, 0)
        self.centre = ((lo + hi) / 2).astype(np.float32)
        self.radius = max(float(np.linalg.norm(hi - lo) / 2) * 0.92, 5.0)
        allP = np.concatenate([m[0] for m in self.cloth] + [m[0] for m in self.body] + [P])
        allP = allP[np.isfinite(allP).all(1)]
        self.floor = float(allP[:, 2].min())
        # the overhead shadow, in the piece's own floor coordinates
        S = self.SHADOW_GRID
        self.sh_ext = float(max(hi[0] - lo[0], hi[1] - lo[1]) * 0.9 + 10)
        self.sh_c = self.centre[:2].copy()
        mask = np.zeros((S, S), np.float32)
        for m in self.cloth + self.body:
            V = m[0][np.isfinite(m[0]).all(1)]
            g = np.clip((((V[:, :2] - self.sh_c) / (2 * self.sh_ext) + 0.5) * S).astype(int), 0, S - 1)
            mask[g[:, 1], g[:, 0]] = 1
        self.shadow = np.clip(_blur(_blur(mask, 3) * 4, 6), 0, 1).astype(np.float32) * 0.55
        self._bkey = None

    def _base(self, w, h, bg):
        """What does not turn: background, the floor disc, each pixel's floor point."""
        key = (w, h, tuple(np.round(bg, 4)))
        if self._bkey == key:
            return self._b
        cam = Camera(self.centre, self.radius, w, h)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dark = float(bg.mean()) < 0.5
        lift = 0.06 * np.exp(-(((xx - w / 2) / (0.7 * w)) ** 2 + ((yy - h * 0.45) / (0.7 * h)) ** 2))
        img = np.clip(bg + lift[..., None] * (1.0 if dark else -0.6), 0, 1)
        ray = ((xx[..., None] - w / 2) / cam.focal * cam.r
               - (yy[..., None] - h / 2) / cam.focal * cam.u + cam.f)
        rz = np.where(np.abs(ray[..., 2]) < 1e-6, -1e-6, ray[..., 2])
        t = (self.floor - cam.pos[2]) / rz
        ok = t > 0
        hit = (cam.pos[:2] + ray[..., :2] * t[..., None]) - self.sh_c
        r2 = (hit ** 2).sum(-1) / (self.radius * 1.3) ** 2
        disc = np.where(ok, np.exp(-r2 * 1.5), 0) * (0.10 if dark else 0.06)
        img = np.clip(img + disc[..., None] * (1.0 if dark else -1.0), 0, 1).astype(np.float32)
        fl = np.flatnonzero(ok & (np.abs(hit) < self.sh_ext).all(-1))
        self._b = (cam, img.reshape(-1, 3), fl, hit.reshape(-1, 2)[fl])
        self._bkey = key
        return self._b

    def _turned(self, theta):
        """The meshes turned by -theta about the vertical axis through the centre:
        the picture of the camera going round by +theta with the lights kept."""
        c, s = np.cos(-theta), np.sin(-theta)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float32)
        o = self.centre

        def rot(m):
            return ((m[0] - o) @ R.T + o, m[1], m[2] @ R.T, m[3])
        return [rot(m) for m in self.cloth], [rot(m) + (m[4],) for m in self.body]

    def render(self, theta, w, h, bg):
        """One frame -> (h, w, 3) uint8."""
        bg = np.asarray(bg, np.float32)
        cam, base, fl, hit = self._base(w, h, bg)
        cloth, body = self._turned(theta)
        img = base.copy()
        c, s = np.cos(theta), np.sin(theta)
        hx, hy = hit[:, 0] * c - hit[:, 1] * s, hit[:, 0] * s + hit[:, 1] * c
        S = self.SHADOW_GRID
        gx = ((hx / (2 * self.sh_ext) + 0.5) * S).astype(np.int64).clip(0, S - 1)
        gy = ((hy / (2 * self.sh_ext) + 0.5) * S).astype(np.int64).clip(0, S - 1)
        img[fl] *= (1 - self.shadow[gy, gx])[:, None]
        if not self.zap:
            z, a = raster(cam, [m[:4] for m in body] + cloth)
            px = np.flatnonzero(np.isfinite(z))
            col = shade(a[px])
            zz = z[px]
        else:
            front = []
            for V, T, N, C, sgn in body:
                fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]) * sgn
                front.append((V, T[((V[T].mean(1) - cam.pos) * fn).sum(1) < 0], N, C))
            zb, ab = raster(cam, front)
            zc, ac = raster(cam, cloth)
            mb, mc = np.isfinite(zb), np.isfinite(zc)
            px = np.flatnonzero(mb | mc)
            col = img[px].copy()
            b = mb[px]
            col[b] = shade(ab[px[b]])
            o = mc[px] & (~b | (zc[px] < zb[px]))
            col[o] = col[o] * 0.45 + shade(ac[px[o]]) * 0.55
            zz = np.where(mc[px], zc[px], zb[px])
        if len(px):
            z0, z1 = float(zz.min()), float(zz.max())
            fog = np.clip((zz - z0) / max(z1 - z0, 1e-3), 0, 1)[:, None] * 0.22
            img[px] = col * (1 - fog) + bg * fog
        return (np.clip(img, 0, 1).reshape(h, w, 3) * 255).astype(np.uint8)


def scenes_for(record, written) -> "dict[str, Scene]":
    """The five steps of one piece as Scenes; a step with nothing to draw is left out."""
    out = {}
    shapes = record["shapes"]
    body, skin = [], []
    for V, T in (written or {}).get("body", []):
        body.append((V, T, live_steps._flat(len(V), BODY_GREY)))
        skin.append((V, T, live_steps._flat(len(V), live_steps.SKIN_RGB)))

    def moved(a, b):
        d = np.linalg.norm(np.asarray(b, np.float32) - np.asarray(a, np.float32), axis=1)
        return np.nan_to_num(d, nan=99.0, posinf=99.0)
    if shapes:
        out["Source"] = Scene([(s[2], s[1], live_steps._flat(len(s[2]), live_steps.SOURCE_RGB))
                               for s in shapes])
        out["Warp"] = Scene([(s[3], s[1], live_steps.ramp(moved(s[2], s[3]))) for s in shapes], body)
        out["Fit"] = Scene([(s[4], s[1], live_steps.ramp(moved(s[3], s[4]))) for s in shapes], body)
    cloth = (written or {}).get("cloth", [])
    if written is not None:
        wc = []
        for V, T, best, names in cloth:
            pal = np.array([live_steps.bone_colour(n) for n in names] or [(0, 0, 0)], np.float32)
            wc.append((V, T, np.where((best >= 0)[:, None], pal[np.maximum(best, 0)],
                                      np.asarray(live_steps.UNWEIGHTED_RGB, np.float32))))
        if wc or body:
            out["Weights"] = Scene(wc, body)
        if cloth or skin:
            out["Zap"] = Scene([(V, T, live_steps._flat(len(V), live_steps.CLOTH_RGB))
                                for V, T, _b, _n in cloth], skin, zap=True)
    return out
