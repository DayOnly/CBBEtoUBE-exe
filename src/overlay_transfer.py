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

"""Align CBBE/3BA body overlays (RaceMenu tattoos / body paints) to the UBE body.

UBE uses a DIFFERENT UV layout than CBBE, so a CBBE-authored overlay texture --
which is painted in CBBE's UV space -- lands on the wrong anatomy when displayed
through UBE's UVs. This module rebakes each overlay into UBE-UV space via the
CBBE<->UBE body correspondence so the design appears at the correct anatomy on
the UBE body. Output is a loose DDS at the SAME texture path the overlay already
uses; RaceMenu loads it via load order (no ESP/INI change).

Method (validated): for each texel of the UBE body's UV, rasterize the UBE mesh
in UV space (barycentric) to its 3D point, project that point onto the CBBE
surface WARPED INTO UBE SPACE (cbbe + the converter's CBBE->UBE delta) and read
the barycentric CBBE UV there, then bilinear-sample the source overlay and write
the texel. The between-island gutter is padded (nearest-covered fill) so no seam
lines appear at island edges. Images go through texconv via an uncompressed TGA
intermediate (no PIL, so it works in the frozen exe).
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import paths as _paths

# RaceMenu/SKEE body overlays live under one of these roots. The standard is
# .../character/overlays/, but some packs ship under .../character/character
# assets/overlays/ (the body/head ASSET tree) and the game loads those too --
# so BOTH must be scanned. An un-scanned overlay is never remapped, so it shows
# in its SOURCE UV on the UBE body = lands on the wrong anatomy (this is what
# made an entire wounds pack misplace). classify_overlay/_overlay_set split on
# "/overlays/" so they're already root-agnostic; only discovery uses these.
_OVERLAY_ROOTS = (
    "textures/actors/character/overlays",
    "textures/actors/character/character assets/overlays",
)
_OVERLAY_ROOT = _OVERLAY_ROOTS[0]      # back-compat (diag scripts, default path)


# ---------- texconv locator -------------------------------------------------

_TEXCONV_CACHE: "list[Path | None]" = []


def find_texconv() -> "Path | None":
    """Locate texconv(.exe). Order: env CBBE2UBE_TEXCONV, then the MO2 instance
    tools/ tree, then PATH. Prefers the x64 build. Cached. Returns None if not
    found (the caller disables the overlay feature with a clear message -- it's
    opt-in anyway).

    SECURITY: deliberately does NOT scan the mods/ tree. A malicious mod could
    plant a Texconv*.exe there and the rglob would execute it (search-path
    hijack -> RCE). texconv lives in the instance tools/ dir or on PATH, never
    inside an installed mod."""
    if _TEXCONV_CACHE:
        return _TEXCONV_CACHE[0]
    found: "Path | None" = None
    env = os.environ.get("CBBE2UBE_TEXCONV")
    if env and Path(env).is_file():
        found = Path(env)
    if found is None:
        roots: list[Path] = []
        mr = _paths.mods_root()
        if mr is not None:
            roots.append(mr.parent / "tools")   # <instance>/tools (trusted)
            # NOT mods/: a malicious mod could plant Texconv*.exe there and the
            # rglob below would execute it (search-path hijack -> RCE).
        for root in roots:
            if found is not None or not root.is_dir():
                break
            cands = sorted(root.rglob("[Tt]exconv*.exe"))
            # prefer x64
            cands.sort(key=lambda p: (0 if "64" in p.name.lower() else 1))
            if cands:
                found = cands[0]
    if found is None:
        from shutil import which
        w = which("texconv") or which("texconvx64")
        if w:
            found = Path(w)
    _TEXCONV_CACHE.append(found)
    return found


def _run_texconv(texconv, args):
    # CREATE_NO_WINDOW (Windows): texconv is a console app; when spawned from the
    # windowed (no-console) GUI exe each call would otherwise flash its own
    # console window -- 2x per overlay, hundreds of times. Output is captured via
    # pipes regardless, so nothing is lost by hiding the window.
    r = subprocess.run([str(texconv)] + [str(a) for a in args],
                       capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(
            f"texconv failed ({r.returncode}): args={args}\n"
            f"{r.stdout.strip()}\n{r.stderr.strip()}")


# ---------- DDS <-> RGBA via uncompressed TGA (no PIL) -----------------------

def _read_tga_rgba(path) -> np.ndarray:
    d = Path(path).read_bytes()
    id_len = d[0]
    cmap_type = d[1]
    if d[2] != 2:
        raise ValueError(f"TGA image type {d[2]} unsupported (need 2)")
    w = struct.unpack_from("<H", d, 12)[0]
    h = struct.unpack_from("<H", d, 14)[0]
    bpp = d[16]
    desc = d[17]
    if bpp not in (24, 32):
        raise ValueError(f"TGA {bpp}bpp unsupported")
    nch = bpp // 8
    cmap_bytes = (struct.unpack_from("<H", d, 5)[0] * (d[7] // 8)) if cmap_type else 0
    off = 18 + id_len + cmap_bytes
    pix = np.frombuffer(d, np.uint8, count=w * h * nch, offset=off).reshape(h, w, nch)
    rgba = (pix[..., [2, 1, 0, 3]].copy() if nch == 4
            else np.dstack([pix[..., [2, 1, 0]], np.full((h, w), 255, np.uint8)]))
    if not (desc & 0x20):                          # bottom-left origin -> flip
        rgba = rgba[::-1].copy()
    return rgba


def _write_tga_rgba(arr: np.ndarray, path):
    h, w = arr.shape[:2]
    header = bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0]) \
        + struct.pack("<HH", w, h) + bytes([32, 0x20])
    bgra = np.ascontiguousarray(arr[..., [2, 1, 0, 3]].astype(np.uint8))
    Path(path).write_bytes(header + bgra.tobytes())


def dds_to_rgba(dds_path, texconv, workdir) -> np.ndarray:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    _run_texconv(texconv, ["-ft", "tga", "-m", "1", "-y", "-o", workdir, dds_path])
    tga = workdir / (Path(dds_path).stem + ".tga")
    try:
        return _read_tga_rgba(tga)          # numpy copy -> file no longer needed
    finally:
        # DELETE the ~16MB intermediate immediately. These have UNIQUE per-source
        # names, so they accumulate (~16MB each) across an 800+ overlay run and
        # filled the TEMP drive -> ENOSPC. Bounded now to ~workers files at once.
        try:
            tga.unlink()
        except OSError:
            pass


def rgba_to_dds(arr: np.ndarray, dds_path, texconv, workdir, fmt="BC3_UNORM"):
    # texconv names its output by the INPUT stem and writes it next to the final
    # DDS (`-o dds_path.parent`) so the trailing .replace is a same-drive atomic
    # rename. The intermediate stem is therefore derived from the DESTINATION
    # (unique within its folder) -- a fixed "_ovl" name collided across threads
    # converting different overlays in the SAME folder (parallel runs lost files).
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    dds_path = Path(dds_path)
    dds_path.parent.mkdir(parents=True, exist_ok=True)
    stem = dds_path.stem + ".__ube_tmp__"
    tga = workdir / (stem + ".tga")
    _write_tga_rgba(arr, tga)
    try:
        _run_texconv(texconv, ["-ft", "dds", "-f", fmt, "-y",
                               "-o", dds_path.parent, tga])
        (dds_path.parent / (stem + ".dds")).replace(dds_path)
    finally:
        # DELETE the ~16MB TGA intermediate now -- it has a UNIQUE per-destination
        # name (so the parallel output dir doesn't collide), which also means it
        # would otherwise accumulate and fill the TEMP drive on a big run.
        try:
            tga.unlink()
        except OSError:
            pass


# ---------- geometry --------------------------------------------------------

@dataclass
class OverlayCorrespondence:
    """Precomputed CBBE<->UBE mapping for one body region, reused across every
    overlay of that region. Build once (it's the expensive part). The per-
    texel UV map (the costly rasterize+project) is overlay-INDEPENDENT, so it's
    cached per texture size in `_map_cache` and reused for every overlay."""
    ube_verts: np.ndarray
    ube_uv: np.ndarray
    ube_tris: np.ndarray
    cbbe_uv: np.ndarray
    cbbe_tris: np.ndarray
    cbbe_in_ube_mesh: object          # correspondence.MeshIndex over warped CBBE
    _map_cache: dict = field(default_factory=dict)   # T -> per-size transfer map
    _lock: object = field(default_factory=threading.Lock)   # guards _map_cache build


def _rasterize_uv_to_3d(uv, verts, tris, T):
    pt = np.zeros((T, T, 3), np.float64)
    cov = np.zeros((T, T), bool)
    px = uv[:, 0] * (T - 1)
    py = uv[:, 1] * (T - 1)
    for tri in tris:
        i0, i1, i2 = tri
        ax, ay, bx, by, cx, cy = px[i0], py[i0], px[i1], py[i1], px[i2], py[i2]
        minx = max(int(np.floor(min(ax, bx, cx))), 0)
        maxx = min(int(np.ceil(max(ax, bx, cx))), T - 1)
        miny = max(int(np.floor(min(ay, by, cy))), 0)
        maxy = min(int(np.ceil(max(ay, by, cy))), T - 1)
        if minx > maxx or miny > maxy:
            continue
        denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(denom) < 1e-12:
            continue
        ys, xs = np.mgrid[miny:maxy + 1, minx:maxx + 1]
        fx = xs + 0.5
        fy = ys + 0.5
        w0 = ((by - cy) * (fx - cx) + (cx - bx) * (fy - cy)) / denom
        w1 = ((cy - ay) * (fx - cx) + (ax - cx) * (fy - cy)) / denom
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
        if not inside.any():
            continue
        p = (w0[..., None] * verts[i0] + w1[..., None] * verts[i1]
             + w2[..., None] * verts[i2])
        pt[ys[inside], xs[inside]] = p[inside]
        cov[ys[inside], xs[inside]] = True
    return pt, cov


def _bilinear_sample(img, u, v):
    H, W = img.shape[:2]
    fx = np.clip(u * (W - 1), 0, W - 1)
    fy = np.clip(v * (H - 1), 0, H - 1)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, W - 1)
    y1 = np.minimum(y0 + 1, H - 1)
    tx = (fx - x0)[:, None]
    ty = (fy - y0)[:, None]
    im = img.astype(np.float64)
    top = im[y0, x0] * (1 - tx) + im[y0, x1] * tx
    bot = im[y1, x0] * (1 - tx) + im[y1, x1] * tx
    return top * (1 - ty) + bot * ty


def _barycentric_uv(pts, tri_idx, verts, tris, uv):
    t = tris[tri_idx]
    a, b, c = verts[t[:, 0]], verts[t[:, 1]], verts[t[:, 2]]
    v0, v1, v2 = b - a, c - a, pts - a
    d00 = np.einsum("ij,ij->i", v0, v0)
    d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1)
    d20 = np.einsum("ij,ij->i", v2, v0)
    d21 = np.einsum("ij,ij->i", v2, v1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-12, 1e-12, den)
    vb = (d11 * d20 - d01 * d21) / den
    wc = (d00 * d21 - d01 * d20) / den
    ua = 1.0 - vb - wc
    return (ua[:, None] * uv[t[:, 0]] + vb[:, None] * uv[t[:, 1]]
            + wc[:, None] * uv[t[:, 2]])


def _uv_map_for_size(corr: OverlayCorrespondence, T: int) -> dict:
    """Compute (or fetch cached) the per-texel transfer map for a T x T overlay.
    This is the EXPENSIVE, overlay-independent step (rasterize UBE UV -> 3D ->
    project to CBBE), so it's cached per size and reused across every overlay of
    that size. Thread-safe: the build is guarded by corr._lock (double-checked)
    so a parallel run's first overlays of a given size don't race.

    Returns a dict with: covered texels (ys,xs); the source CBBE UV (su,sv); the
    gutter-fill indices (fy,fx); the covered mask (cov); and the PRECOMPUTED
    bilinear corner indices (bx0,by0,bx1,by1) + weights (btx,bty) for a T x T
    source -- those depend only on (su,sv,T), so baking them here turns the
    per-overlay sample into 4 uint8 gathers + a float32 lerp (no 134MB float64
    image, no repeated index math)."""
    cached = corr._map_cache.get(T)
    if cached is not None:
        return cached
    with corr._lock:
        cached = corr._map_cache.get(T)
        if cached is not None:                 # built while we waited for the lock
            return cached
        from scipy import ndimage
        from .correspondence import project_to_mesh
        pt, cov = _rasterize_uv_to_3d(corr.ube_uv, corr.ube_verts, corr.ube_tris, T)
        ys, xs = np.where(cov)
        if len(ys):
            proj, tri_idx, _ = project_to_mesh(pt[ys, xs], corr.cbbe_in_ube_mesh, k=8)
            uv = _barycentric_uv(proj, tri_idx, corr.cbbe_in_ube_mesh.verts,
                                 corr.cbbe_tris, corr.cbbe_uv)
            su, sv = uv[:, 0], uv[:, 1]
            fxf = np.clip(su * (T - 1), 0, T - 1)
            fyf = np.clip(sv * (T - 1), 0, T - 1)
            bx0 = np.floor(fxf).astype(np.intp)
            by0 = np.floor(fyf).astype(np.intp)
            bx1 = np.minimum(bx0 + 1, T - 1)
            by1 = np.minimum(by0 + 1, T - 1)
            btx = (fxf - bx0).astype(np.float32)[:, None]
            bty = (fyf - by0).astype(np.float32)[:, None]
        else:
            su = sv = np.zeros(0)
            bx0 = by0 = bx1 = by1 = np.zeros(0, np.intp)
            btx = bty = np.zeros((0, 1), np.float32)
        _, (fy, fx) = ndimage.distance_transform_edt(~cov, return_indices=True)
        m = {"ys": ys, "xs": xs, "su": su, "sv": sv, "fy": fy, "fx": fx,
             "cov": cov, "bx0": bx0, "by0": by0, "bx1": bx1, "by1": by1,
             "btx": btx, "bty": bty}
        corr._map_cache[T] = m
        return m


def transfer_overlay(src_rgba: np.ndarray,
                     corr: OverlayCorrespondence) -> np.ndarray:
    """Rebake `src_rgba` (CBBE-UV overlay) into UBE-UV space. Returns an
    (T,T,4) RGBA uint8 array (T = source size), gutter-padded. Uses the cached
    per-size UV map, so all but the first overlay of a given size is just a
    bilinear sample + pad."""
    T = src_rgba.shape[0]
    m = _uv_map_for_size(corr, T)
    ys, xs, fy, fx, cov = m["ys"], m["xs"], m["fy"], m["fx"], m["cov"]
    out = np.zeros((T, T, 4), np.uint8)
    if len(ys):
        if src_rgba.shape[1] != T:
            # non-square source (rare): the baked indices assume T x T, so fall
            # back to the general sampler using the precomputed CBBE UV.
            out[ys, xs] = np.clip(_bilinear_sample(src_rgba, m["su"], m["sv"]),
                                  0, 255).astype(np.uint8)
        else:
            bx0, by0, bx1, by1 = m["bx0"], m["by0"], m["bx1"], m["by1"]
            btx, bty = m["btx"], m["bty"]
            c00 = src_rgba[by0, bx0].astype(np.float32)
            c01 = src_rgba[by0, bx1].astype(np.float32)
            c10 = src_rgba[by1, bx0].astype(np.float32)
            c11 = src_rgba[by1, bx1].astype(np.float32)
            top = c00 * (1.0 - btx) + c01 * btx
            bot = c10 * (1.0 - btx) + c11 * btx
            out[ys, xs] = np.clip(top * (1.0 - bty) + bot * bty,
                                  0, 255).astype(np.uint8)
    padded = out[fy, fx]          # gutter <- nearest covered texel
    padded[cov] = out[cov]
    return padded


# region -> (CBBE-UV skin filename, !UBE tangent rel path under meshes/).
_REGION_CBBE_FILE = {"hands": "femalehands_1.nif", "feet": "femalefeet_1.nif"}
_REGION_UBE_REL = {
    "hands": Path("meshes", "!UBE", "Hands", "femalehands_tangent_1.nif"),
    "feet": Path("meshes", "!UBE", "Feet", "femalefeet_tangent_1.nif"),
}


def _find_region_meshes(region: str, weight: str = "_1"):
    """(cbbe_path, ube_path) for a region. body -> the converter's body finders;
    hands/feet -> scan the mods root for the CBBE-UV skin (a CBBE/3BA-named,
    non-bodyslide mod's femalehands/femalefeet) + the !UBE tangent."""
    from . import nif_convert as nc
    if region == "body":
        return nc._find_cbbe_base_body(weight), nc._find_ube_femalebody(weight)
    mr = _paths.mods_root()
    if mr is None or region not in _REGION_CBBE_FILE:
        return None, None
    ube_rel = _REGION_UBE_REL[region]
    cbbe_file = _REGION_CBBE_FILE[region]
    try:
        mods = sorted(d for d in mr.iterdir() if d.is_dir())
    except OSError:
        mods = []
    ube = next((m / ube_rel for m in mods if (m / ube_rel).is_file()), None)
    cbbe = None
    for m in mods:
        nm = m.name.lower()
        if "bodyslide" in nm or not any(h in nm for h in ("cbbe", "3ba", "3bbb")):
            continue
        p = m / "meshes" / "actors" / "character" / "character assets" / cbbe_file
        if p.is_file():
            cbbe = p
            break
    return cbbe, ube


def build_overlay_correspondence(cbbe_path, ube_path,
                                 prefer_shapes=("BaseShape", "3BA")
                                 ) -> "OverlayCorrespondence | None":
    """Generic CBBE<->UBE correspondence for one region from explicit meshes.
    Returns None if a mesh is missing/unreadable."""
    if cbbe_path is None or ube_path is None:
        return None
    from . import nif_convert as nc
    from .correspondence import MeshIndex, project_to_mesh
    pyn = nc._pynifly()

    def _load(path):
        nf = pyn.NifFile(filepath=str(path))
        s = next((x for x in nf.shapes if x.name in prefer_shapes), None) \
            or max(nf.shapes, key=lambda x: len(x.verts))
        return (np.asarray(s.verts, np.float64), np.asarray(s.uvs, np.float64),
                np.asarray(s.tris, np.int64))
    try:
        cbv, cbuv, cbt = _load(cbbe_path)
        ubv, ubuv, ubt = _load(ube_path)
    except Exception:
        return None
    # Warp CBBE into UBE space by projecting each CBBE vert onto the UBE
    # SURFACE (a continuous closest-point), NOT snapping to the nearest UBE
    # vertex. Vert-snapping collapsed ~35% of body triangles (a third of CBBE
    # verts shared one UBE vert) -> a degenerate cbbe_in_ube mesh -> the
    # transfer's project_to_mesh then landed on collapsed/folded tris -> wrong
    # CBBE UV -> SMEARED/garbled overlays (worst on thin-line body paint such as
    # tiger-stripe bodypaint). Surface projection preserves CBBE topology:
    # measured body bad-tris 35%->12%, folded 1.33%->0.18%.
    cbbe_in_ube, _, _ = project_to_mesh(cbv, MeshIndex.build(ubv, ubt))
    return OverlayCorrespondence(
        ube_verts=ubv, ube_uv=ubuv, ube_tris=ubt,
        cbbe_uv=cbuv, cbbe_tris=cbt,
        cbbe_in_ube_mesh=MeshIndex.build(cbbe_in_ube, cbt))


def build_region_correspondence(region: str, weight: str = "_1"
                                ) -> "OverlayCorrespondence | None":
    cbbe, ube = _find_region_meshes(region, weight)
    prefer = ("BaseShape", "3BA") if region == "body" else ("BaseShape",)
    return build_overlay_correspondence(cbbe, ube, prefer)


def convert_overlay(src_dds, out_dds, corr, texconv, workdir):
    """Read one overlay DDS, transfer it to UBE UV, write the BC3 result."""
    src = dds_to_rgba(src_dds, texconv, workdir)
    out = transfer_overlay(src, corr)
    rgba_to_dds(out, out_dds, texconv, workdir)


# Face/makeup keywords matched anywhere in the overlay's PATH. These ride the
# HEAD UV, which UBE doesn't change, so they're never remapped. Kept to
# descriptive, low-false-match terms (the folder names sets actually use:
# EyeShadow/EyeLiner/Blush/Lips/Contours/Highlights, "Face", warpaint, ...).
_FACE_KEYWORDS = (
    "face", "head", "makeup", "warpaint", "blush", "lipstick", "lips",
    "eyeliner", "eyeshadow", "mascara", "foundation", "contour", "highlight",
    "eyebrow", "brows", "eyelash",
)

# Body-part names = body paint, but matched as WHOLE words (split on non-letters)
# so "butt" never grabs "butterfly" nor "arm" -> "armor"/"warm". A file with one
# of these is body, which also marks its SET as a body-paint set so the set's
# other unlabeled files resolve to body too (e.g. a set names its files
# "abs"/"boob"/"butt"/"chest", with no "body" keyword anywhere).
_BODY_PART_TOKENS = frozenset({
    "abs", "arm", "arms", "boob", "boobs", "breast", "breasts", "butt",
    "chest", "belly", "stomach", "tummy", "thigh", "thighs", "leg", "legs",
    "hip", "hips", "navel", "glute", "glutes", "torso", "waist", "abdomen",
    "pubic", "groin", "cleavage", "nipple", "nipples", "back", "spine",
    "shoulder", "shoulders", "rib", "ribs", "neck",
})

# How to resolve an UNLABELED ('ambiguous') overlay that has NO slot/body-part
# signal anywhere in its path. The conservative set-level guard (False) sends it
# to body ONLY inside a set that ALSO has a real body/hand/feet overlay (a
# body-paint set); an all-makeup or all-unlabeled set keeps its ambiguous files
# as 'head' (skipped, left as the original CBBE DDS).
#
# This is deliberately conservative because transferring a FACE overlay through
# the BODY correspondence DESTROYS it (measured: a sharp eyeliner/lip overlay
# loses 100% of its placement and most of its ink), whereas skipping a body
# overlay merely leaves it CBBE-aligned. A blanket ambiguous->body (True) would
# corrupt every unlabeled makeup set, so it is NOT used. Sets with no path signal
# at all (e.g. gendered-numeric filenames) are resolved by the user-supplied
# OVERLAY_SET_OVERRIDES instead -- see _set_override / discover_overlays.
_OVERLAY_AMBIGUOUS_TO_BODY = False


# Optional USER-supplied per-set slot overrides for sets the path can't classify
# (e.g. an all-unlabeled BSA set with gendered-numeric filenames). The converter
# ships NONE of these -- the file is the user's, so no third-party set name lives
# in our code. Resolution beats classification, so it also lets a user correct a
# mis-detected set. Format: one `set = body|hands|feet|head|skip` per line ('#'
# comments, blank lines ignored); `set` is the folder under .../overlays/.
# Source: CBBE2UBE_OVERLAY_SLOTS env (a file path), else `overlay_slots.txt`
# beside the mods root. 'head'/'skip' both mean "leave as the original CBBE DDS".
OVERLAY_SLOTS_ENV = "CBBE2UBE_OVERLAY_SLOTS"
_OVERRIDE_REGIONS = frozenset({"body", "hands", "feet", "head", "skip"})
_set_overrides_cache: dict = {}      # resolved-path str -> {set: region}


def _overlay_slots_path() -> "Path | None":
    env = os.environ.get(OVERLAY_SLOTS_ENV)
    if env:
        return Path(env)
    mr = _paths.mods_root()
    return (mr.parent / "overlay_slots.txt") if mr is not None else None


def _load_set_overrides() -> dict:
    """Parse the user's set->slot override file (cached per resolved path, so a
    different mods root or a newly-created file is picked up). Empty if absent."""
    path = _overlay_slots_path()
    key = str(path) if path is not None else ""
    if key in _set_overrides_cache:
        return _set_overrides_cache[key]
    out: dict = {}
    if path is not None and path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip().lower()
            v = v.strip().lower()
            if k and v in _OVERRIDE_REGIONS:
                out[k] = v
    _set_overrides_cache[key] = out
    return out


def _set_override(overlay_set: str) -> "str | None":
    """User-forced region for a set ('skip' -> None == not transferred)."""
    reg = _load_set_overrides().get(overlay_set.lower())
    return None if reg == "skip" else reg


def _overlay_set(rel_path: str) -> str:
    """The set folder directly under .../overlays/ (e.g. 'wnb', 'fms'). Used to
    resolve 'ambiguous' overlays at the set level in discover_overlays."""
    parts = rel_path.replace("\\", "/").lower().split("/overlays/", 1)
    return parts[1].split("/", 1)[0] if len(parts) == 2 and parts[1] else ""


def classify_overlay(rel_path: str) -> str:
    """Classify an overlay by its FULL path (folders + filename), not just the
    filename. Returns 'body' | 'hands' | 'feet' | 'head' | 'ambiguous'.

    Filename-only was too narrow: most sets put the slot in a FOLDER
    (e.g. .../Face/, .../Hand/) or a bare set/paint subfolder name rather than the
    filename, so every body paint not literally named "...body.dds" fell through
    -> not remapped -> the raw CBBE-UV file showed on the UBE body = the design
    landed on the WRONG BODY PART. Only the literal "NN body.dds" form was being
    caught.

    'head' (face/makeup, head UV) is NEVER remapped. 'ambiguous' (no slot marker
    anywhere in the path -- e.g. a bare set/paint subfolder) is resolved at the SET
    level by discover_overlays: a set that ALSO contains body/hand/feet overlays
    is a body-paint set, so its ambiguous files are 'body'; an all-makeup set
    (only face + ambiguous) keeps its ambiguous files as 'head' (skipped). Face
    is tested FIRST so a makeup file inside a body-paint set is never grabbed."""
    p = rel_path.replace("\\", "/").lower()
    tail = p.split("/overlays/", 1)
    p = tail[1] if len(tail) == 2 else p   # ignore the fixed path prefix
    if any(k in p for k in _FACE_KEYWORDS):
        return "head"
    if "hand" in p:
        return "hands"
    if "feet" in p or "foot" in p:
        return "feet"
    if "body" in p:
        return "body"
    raw = "".join(ch if ch.isalpha() else " " for ch in p).split()
    tokens = set(raw)
    # Many sets fuse a gender prefix onto the part ("malechest", "femalehip"),
    # which hides the body-part token from the whole-word match. Peel the
    # leading male/female off so "malechest" also yields "chest" -> body. (Face
    # parts like "femalehead" are already caught above by the 'head' keyword.)
    for t in raw:
        for pre in ("female", "male"):
            if t.startswith(pre) and len(t) > len(pre):
                tokens.add(t[len(pre):])
    if tokens & _BODY_PART_TOKENS:
        return "body"
    return "ambiguous"


# ---------- discovery + orchestration ---------------------------------------

def discover_overlays(layout, regions=("body", "hands", "feet"),
                      skip_mods=(), only_mods=None) -> "dict[str, dict]":
    """Find every overlay texture across enabled mods (loose + BSA), in MO2
    priority order so the load-order WINNER is kept per path, bucketed by region.
    Returns {region: {rel_path: source}} where rel_path is `textures/.../x.dds`
    (forward slash, lowercased) and source is ("loose", Path, mod) or ("bsa",
    bsa_path, internal_name, mod). Only the requested regions are kept; head/
    makeup overlays are skipped, and an UNLABELED overlay becomes body only when
    its set also has body/hand/feet overlays (a body-paint set) -- otherwise it
    stays face/skipped (see classify_overlay + the set-level pass below).

    `skip_mods` (mod folder names, case-insensitive) are NOT scanned -- pass our
    OWN output mod, else a previous run's already-converted UBE-UV overlays (it's
    the highest-priority mod) win as the "source" and get transferred a SECOND
    time -> double-warped / garbled overlays in-game."""
    from .bsa_strings import BSAArchive
    mr = _paths.mods_root()
    out: "dict[str, dict]" = {r: {} for r in regions}
    if mr is None:
        return out
    ordered = _paths.enabled_mods_ordered(layout)
    if ordered is None:
        ordered = sorted(d.name for d in mr.iterdir() if d.is_dir())
    skip_lower = {s.lower() for s in skip_mods}
    only_lower = {s.lower() for s in only_mods} if only_mods else None
    want = set(regions)

    # Script-registered overlays are AUTHORITATIVE and a pack may register a
    # texture at ANY path, not just under _OVERLAY_ROOTS (e.g. a tattoo set under
    # textures/actors/<mod>/). Build the slot map up front so PASS 1 also COLLECTS
    # those custom-path textures -- otherwise they're never discovered, stay
    # CBBE-UV, and land on the wrong anatomy on UBE. Reused for PASS-2 region
    # classification.
    from . import overlay_slots as _oslots
    slot_map = _oslots.build_script_slot_map(layout)
    registered_nonroot = {
        rel for rel in slot_map
        if rel.endswith(".dds")
        and not any(rel.startswith(r) for r in _OVERLAY_ROOTS)
    }
    # Distinct parent dirs of the custom-path overlays -> list only those inside a
    # BSA (cheap) instead of the whole 'textures' tree.
    nonroot_prefixes = sorted({rel.rsplit("/", 1)[0] for rel in registered_nonroot})

    # PASS 1: collect every overlay (highest-priority source wins per rel) with
    # its raw class + set. Track which sets contain a real body/hand/feet slot --
    # those are body-paint sets, so their 'ambiguous' files resolve to body.
    collected: "dict[str, tuple]" = {}     # rel -> (source, raw_class, set)
    sets_with_slot: set = set()

    def _collect(rel, source):
        if rel in collected:
            return                      # first (highest-priority) source wins
        raw = classify_overlay(rel)
        st = _overlay_set(rel)
        collected[rel] = (source, raw, st)
        if raw in ("body", "hands", "feet"):
            sets_with_slot.add(st)

    for mod_name in ordered:
        if only_lower is not None and mod_name.lower() not in only_lower:
            continue
        if mod_name.lower() in skip_lower:
            continue                    # never read our own output as a source
        mod = mr / mod_name
        if not mod.is_dir():
            continue
        for rel_root in _OVERLAY_ROOTS:
            ovl_dir = mod / Path(rel_root)
            if ovl_dir.is_dir():
                for f in ovl_dir.rglob("*.dds"):
                    _collect(f.relative_to(mod).as_posix().lower(),
                             ("loose", f, mod_name))
        # Script-registered overlays at a custom (non-root) LOOSE path.
        for rel in registered_nonroot:
            if rel not in collected and (mod / rel).is_file():
                _collect(rel, ("loose", mod / rel, mod_name))
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False)
            except Exception:
                continue
            for rel_root in _OVERLAY_ROOTS:
                try:
                    names = arc.list_files(rel_root)
                except Exception:
                    continue
                for name in names:
                    rel = name.replace("\\", "/").lower()
                    if rel.endswith(".dds") and rel.startswith(rel_root):
                        _collect(rel, ("bsa", bsa, name, mod_name))
            # Script-registered overlays at a custom (non-root) path INSIDE a BSA
            # (e.g. a tattoo pack's textures/actors/<mod>/). List only those
            # specific dirs, and only while such overlays remain uncollected, so
            # the common case (no custom-path packs) keeps the cheap per-root scan.
            remaining = registered_nonroot - collected.keys()
            if remaining:
                for pref in nonroot_prefixes:
                    try:
                        names = arc.list_files(pref)
                    except Exception:
                        continue
                    for name in names:
                        rel = name.replace("\\", "/").lower()
                        if rel in remaining:
                            _collect(rel, ("bsa", bsa, name, mod_name))

    # PASS 2: resolve each overlay's region the way RaceMenu does. The SCRIPT
    # slot map (AddWarPaint/BodyPaint/HandPaint/FeetPaint registrations) is the
    # AUTHORITATIVE source -- it's exactly how RaceMenu identifies an overlay's
    # slot, no filename guessing. Only when NO script registers a texture do we
    # fall back to the user override and then the keyword classifier.
    #   * A texture RaceMenu registers as 'head'/face is skipped (head UV).
    #   * A texture registered for MULTIPLE slots (e.g. a body paint reused on
    #     the feet slot) is routed to body here; the feet/secondary-slot output
    #     is produced separately (it needs its own path + a script repoint).
    for rel, (source, raw, st) in collected.items():
        slots = slot_map.get(rel)
        if slots:
            reg = _region_from_slots(slots)
        else:
            ov = _set_override(st)
            if ov is not None:
                reg = ov
            elif st.lower() in _load_set_overrides():
                continue                # explicit 'skip' override
            elif raw == "ambiguous":
                reg = "body" if (_OVERLAY_AMBIGUOUS_TO_BODY or st in sets_with_slot) else "head"
            else:
                reg = raw
        if reg in want:
            out[reg][rel] = source
    return out


def _region_from_slots(slots) -> str:
    """Pick the convert region for a texture RaceMenu registers in `slots`.
    A multi-slot texture (body + feet reuse) routes to BODY for now -- it's the
    correct body-UV output at the texture's own path; the feet/secondary-slot
    version is a separate output produced by the multi-slot pass (own UV + a
    script repoint), not by overwriting this path."""
    if "body" in slots:
        return "body"
    for r in ("hands", "feet", "head"):
        if r in slots:
            return r
    return "head"


def _overlay_workers() -> int:
    """Thread count for the overlay transfer. Defaults to the CPU count capped at
    16: the numpy gathers AND the texconv subprocess both release the GIL, so
    threads scale near-linearly (measured ~8x at 8), but each thread can hold a
    texconv subprocess so we cap it. Override with CBBE2UBE_OVERLAY_WORKERS."""
    env = os.environ.get("CBBE2UBE_OVERLAY_WORKERS", "")
    if env.isdigit() and int(env) > 0:
        return min(int(env), 32)
    return max(1, min(os.cpu_count() or 4, 16))


def list_overlay_mods(layout, regions=("body", "hands", "feet"),
                      skip_mods=()) -> "list[str]":
    """Enabled mods (MO2 priority order) that provide at least one convertible
    body/hands/feet overlay -- for a UI to choose WHICH mods' overlays to convert
    (unpicked mods keep their originals, so they still work on non-UBE races)."""
    by_region = discover_overlays(layout, regions, skip_mods=skip_mods)
    seen: "dict[str, int]" = {}         # ordered set (discovery = priority order)
    for region in by_region.values():
        for src in region.values():
            mod = (src[-1] if isinstance(src, (tuple, list)) and len(src) >= 3
                   else None)
            if mod and mod not in seen:
                seen[mod] = 1
    return list(seen)


def convert_overlays(output_dir, layout, *, regions=("body", "hands", "feet"),
                     texconv=None, log=print, limit: int = 0,
                     overlay_mode="replace", skip_male=False,
                     only_mods=None, exclude_mods=None) -> dict:
    """Rebake CBBE/3BA overlays into UBE-UV space for each region, writing a
    loose DDS at the original texture path under `output_dir` (RaceMenu loads it
    via load order; no ESP). Opt-in. Builds one correspondence per region (the
    expensive part, reused across that region's overlays). Overlays within a
    region are transferred in parallel across a thread pool (each is independent;
    output is identical to serial). Returns a stats dict. `limit` (>0) caps the
    TOTAL count for a quick test run. `exclude_mods` names are never read as a
    source (their overlays keep their originals)."""
    import concurrent.futures as cf
    import shutil
    import tempfile
    from .bsa_strings import BSAArchive
    texconv = texconv or find_texconv()
    if texconv is None:
        log("  !! overlay transfer SKIPPED: texconv not found (set "
            "CBBE2UBE_TEXCONV or install it under the MO2 tools/ folder)")
        return {"converted": 0, "reason": "no-texconv"}
    # Exclude our OWN output mod from the source scan: it's the highest-priority
    # mod, so a previous run's already-converted UBE-UV overlays would otherwise
    # win as the "source" and be transferred a SECOND time -> double-warped.
    skip = set(exclude_mods or ())          # user exclusions (e.g. UBE-native)
    _mr = _paths.mods_root()
    if _mr is not None:
        try:
            skip.add(Path(output_dir).resolve().relative_to(_mr.resolve()).parts[0])
        except Exception:
            skip.add(Path(output_dir).name)
    # "Add UBE copy" mode: keep every original overlay and add a `UBE <name>`
    # variant per registered overlay (needs the Papyrus toolchain). Distinct code
    # path -- returns its own stats.
    if str(overlay_mode).lower() == "copy":
        return add_ube_overlay_copies(
            layout, output_dir, texconv, regions=regions, skip_mods=skip,
            only_mods=only_mods, skip_male=skip_male, limit=limit, log=log)
    by_region = discover_overlays(layout, regions, skip_mods=skip,
                                  only_mods=only_mods)
    if skip_male:
        for _r in by_region:
            by_region[_r] = {rel: s for rel, s in by_region[_r].items()
                             if not _is_male_overlay(rel)}
    total = sum(len(v) for v in by_region.values())
    if total == 0:
        log("  overlay transfer: no body/hands/feet overlays found")
        return {"converted": 0, "reason": "none-found"}
    out_root = Path(output_dir)
    work = Path(tempfile.mkdtemp(prefix="ube_overlay_"))
    arc_cache: dict = {}
    arc_lock = threading.Lock()        # BSA cache + read (not proven thread-safe)
    tls = threading.local()            # one scratch workdir per worker thread
    workers = _overlay_workers()

    def _thread_workdir() -> Path:
        w = getattr(tls, "w", None)
        if w is None:
            w = work / f"th{threading.get_ident()}"
            w.mkdir(parents=True, exist_ok=True)
            tls.w = w
        return w

    n = 0
    failed: list = []
    per_region: dict = {}
    remaining = limit
    for region in regions:
        items = list(by_region.get(region, {}).items())
        if not items:
            continue
        if limit:
            if remaining <= 0:
                break
            items = items[:remaining]
            remaining -= len(items)
        corr = build_region_correspondence(region)
        if corr is None:
            log(f"  !! overlay transfer: SKIP region '{region}' "
                f"(CBBE/UBE {region} ref not found) -- {len(items)} overlay(s)")
            continue
        log(f"  overlay transfer [{region}]: {len(items)} overlay(s) -> UBE UV "
            f"(x{workers}) ...")

        def _do(rel_src, _corr=corr):
            rel, src = rel_src
            w = _thread_workdir()
            if src[0] == "loose":
                src_dds = src[1]
            else:
                with arc_lock:
                    arc = arc_cache.get(src[1])
                    if arc is None:
                        arc = BSAArchive(src[1], eager=False)
                        arc_cache[src[1]] = arc
                    data = arc.read_file(src[2])
                if not data:
                    raise RuntimeError("BSA extract returned no data")
                src_dds = w / "src.dds"
                src_dds.write_bytes(data)
            out_dds = out_root / rel.replace("/", "\\")
            # SECURITY: `rel` derives from a mod-controlled (BSA) texture name;
            # refuse `..`/absolute traversal outside the overlay output root.
            if not _paths.is_within_dir(out_root, out_dds):
                raise RuntimeError(f"refusing overlay traversal path: {rel!r}")
            convert_overlay(src_dds, out_dds, _corr, texconv, w)
            return rel

        rn = 0
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_do, it): it[0] for it in items}
            for fut in cf.as_completed(futs):
                try:
                    fut.result()
                    rn += 1
                    n += 1
                except Exception as e:
                    failed.append((futs[fut], repr(e)))
        per_region[region] = rn
    shutil.rmtree(work, ignore_errors=True)
    # Multi-slot feet pass: bake feet-UV variants for body textures reused on the
    # feet slot + repoint AddFeetPaint. The region pass above only produced
    # feet-ONLY overlays; this covers the (common) multi-slot ones. Best-effort --
    # skipped cleanly if the Papyrus toolchain isn't available.
    if "feet" in regions:
        try:
            fn = build_multislot_feet_overlays(layout, out_root, texconv,
                                               skip_mods=skip, log=log)
            if fn:
                per_region["feet"] = per_region.get("feet", 0) + fn
                n += fn
        except Exception as e:
            log(f"  !! multi-slot feet pass failed (skipped): {e!r}")
    if failed:
        log(f"  !! overlay transfer: {len(failed)} failed (e.g. {failed[0]})")
    log(f"  overlay transfer: {n} overlay(s) written under {out_root} "
        f"({', '.join(f'{r}={c}' for r, c in per_region.items())})")
    return {"converted": n, "failed": failed, "total": total,
            "per_region": per_region}


# ---------- multi-slot FEET pass (Papyrus repoint) --------------------------
# A tattoo registered for BOTH body and feet reuses ONE texture path; the normal
# region pass bakes it for the BODY UV (it routes multi-slot -> body), which is
# wrong on the feet slot (feet UV differs). This pass bakes a FEET-UV variant of
# each such texture to its OWN path under the output mod and recompiles the
# RaceMenuBase script so its AddFeetPaint points there. Feet-ONLY overlays (their
# own dedicated texture) are already converted correctly by the region pass, so
# they are left untouched. Needs the Papyrus compiler; gracefully no-ops without.

_FEET_UV_SUFFIX = "_ubefeet"
_ADD_FEETPAINT_RE = re.compile(
    r'(AddFeetPaint\s*\(\s*"[^"]*"\s*,\s*")([^"]+)(")', re.IGNORECASE)


def _feet_variant_path(dds_path: str) -> str:
    """Insert the feet-UV suffix before the .dds extension, preserving the path's
    slashes (it is rewritten verbatim into the .psc; Windows is case-insensitive
    so a normalized .dds extension is fine)."""
    if dds_path[-4:].lower() == ".dds":
        return dds_path[:-4] + _FEET_UV_SUFFIX + ".dds"
    return dds_path + _FEET_UV_SUFFIX


def _repoint_feet_script(text: str, multislot_rels: set) -> str:
    """Rewrite every AddFeetPaint whose (normalized) texture rel is in
    `multislot_rels` to point at its feet-UV variant; feet-only calls are left
    untouched. Returns the edited script text."""
    from . import overlay_slots as _osl

    def _sub(mm):
        if _osl.normalize_script_texpath(mm.group(2)) in multislot_rels:
            return mm.group(1) + _feet_variant_path(mm.group(2)) + mm.group(3)
        return mm.group(0)
    return _ADD_FEETPAINT_RE.sub(_sub, text)


# ---- "Add UBE copy" overlay mode ------------------------------------------
# Overwrite mode (default) writes the converted DDS at the ORIGINAL path so the
# output mod overrides the overlay for EVERY body -- which breaks it on non-UBE
# races. Copy mode instead writes the converted DDS to a `ube/` subfolder and
# inserts a DUPLICATE `AddXPaint("UBE <name>", <ube path>)` into the mod's
# RaceMenu script, so the list shows BOTH the untouched original (correct on
# CBBE) and the UBE-fit copy. Full call captured (name + texture + any trailing
# args) so the inserted line matches the original's signature.
_FULL_PAINT_RX = re.compile(
    r'(Add(?:War|Body|Hand|Feet)Paint\s*\(\s*")([^"]*)("\s*,\s*")([^"]+)("[^)]*\))',
    re.IGNORECASE)


def _ube_variant_rel(rel: str) -> str:
    """Forward-slash overlay rel -> the UBE-copy output rel: a `ube/` folder
    before the filename. 'textures/a/b/x.dds' -> 'textures/a/b/ube/x.dds'."""
    i = rel.rfind("/")
    return (rel[:i] + "/ube" + rel[i:]) if i >= 0 else ("ube/" + rel)


def _ube_variant_scriptpath(sp: str) -> str:
    """Script-literal texture path -> the UBE-copy variant, inserting a `UBE`
    folder before the filename and preserving the literal's separator style
    (RaceMenu .psc literals usually double their backslashes)."""
    for sep in ("\\\\", "\\", "/"):
        i = sp.rfind(sep)
        if i >= 0:
            return sp[:i] + sep + "UBE" + sep + sp[i + len(sep):]
    return "UBE\\\\" + sp


def add_ube_paint_lines(text: str, baked_norm_rels) -> "tuple[str, int]":
    """Insert a `UBE <name>` duplicate AddXPaint call after every call whose
    (normalized) texture rel is in `baked_norm_rels`. Line-based so the original
    indentation is kept and multiple calls per line are handled. Returns
    (new_text, inserted_count). Pure -> unit-tested without a compiler."""
    from . import overlay_slots as _osl
    baked = set(baked_norm_rels)
    added = 0
    out_lines: list = []
    for line in text.split("\n"):
        out_lines.append(line)
        inserts: list = []
        for m in _FULL_PAINT_RX.finditer(line):
            if _osl.normalize_script_texpath(m.group(4)) not in baked:
                continue
            indent = line[:len(line) - len(line.lstrip())]
            inserts.append(indent + m.group(1) + "UBE " + m.group(2)
                           + m.group(3) + _ube_variant_scriptpath(m.group(4))
                           + m.group(5))
        out_lines.extend(inserts)
        added += len(inserts)
    return "\n".join(out_lines), added


def _skyrim_se_install_dirs() -> "list":
    """Skyrim SE install root(s) from the Bethesda 'Installed Path' registry key
    (Windows). This is the REAL Steam install (which ships Papyrus Compiler) --
    needed because a Wabbajack 'Stock Game' copy omits the compiler."""
    dirs = []
    try:
        import winreg
    except Exception:
        return dirs
    for hive, key in (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                val, _ = winreg.QueryValueEx(k, "Installed Path")
            if val:
                dirs.append(Path(val))
        except OSError:
            continue
    return dirs


def find_papyrus_compiler() -> "Path | None":
    """Locate PapyrusCompiler.exe: env CBBE2UBE_PAPYRUS_COMPILER, then the MO2
    gamePath / discovered game Data dirs, then the real Skyrim SE install from
    the registry (a Wabbajack 'Stock Game' copy has no compiler)."""
    env = os.environ.get("CBBE2UBE_PAPYRUS_COMPILER", "")
    if env and Path(env).is_file():
        return Path(env)
    roots = []
    try:
        lay = _paths.discover_layout()
        if lay.game_path:
            roots.append(Path(lay.game_path))
        roots += [Path(d).parent for d in (lay.game_data_dirs or [])]
    except Exception:
        pass
    roots += _skyrim_se_install_dirs()
    for root in roots:
        cand = root / "Papyrus Compiler" / "PapyrusCompiler.exe"
        if cand.is_file():
            return cand
    return None


def _find_skse_source() -> "Path | None":
    """The SKSE Scripts/Source dir (extended Utility/Math that must win over the
    vanilla base). Requires the ARTIFACT, not just a name match -- 'skse' alone
    also hits e.g. an Address Library mod that ships no sources."""
    mr = _paths.mods_root()
    if mr is None:
        return None
    cands = []
    for d in sorted(p for p in mr.iterdir() if p.is_dir()):
        srcdir = d / "Scripts" / "Source"
        if srcdir.is_dir() and any(srcdir.glob("*.psc")):
            cands.append((d.name.lower(), srcdir))
    for nm, srcdir in cands:
        if "script extender" in nm:
            return srcdir
    for nm, srcdir in cands:                 # fallback: carries SKSE Utility/Math
        if (srcdir / "Utility.psc").is_file() or (srcdir / "Math.psc").is_file():
            return srcdir
    return None


def _find_racemenu_bsas() -> "list":
    """BSAs of the RaceMenu mod (they ship racemenubase/nioverride .psc sources).
    Requires the .bsa ARTIFACT -- 'racemenu' alone also hits e.g. a 'Racemenu
    Undress' tweak with no archive. Prefers the shortest matching name (the base
    'RaceMenu' over longer variants)."""
    mr = _paths.mods_root()
    if mr is None:
        return []
    cands = [d for d in sorted(p for p in mr.iterdir() if p.is_dir())
             if "racemenu" in d.name.lower() and list(d.glob("*.bsa"))]
    cands.sort(key=lambda d: len(d.name))
    return list(cands[0].glob("*.bsa")) if cands else []


def _assemble_papyrus_imports(compiler, work):
    """Build the Papyrus -import dir: SKSE-extended base (FIRST so it wins over
    vanilla Utility/Math) + RaceMenu's racemenubase/nioverride, plus the vanilla
    base extracted from the game's Scripts.zip. Returns (src_dir, base_src_dir)
    or (None, None) if the base sources can't be assembled."""
    import zipfile
    import shutil
    from .bsa_strings import BSAArchive
    base = work / "base"
    src = work / "src"
    base.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)
    scripts_zip = Path(compiler).parent.parent / "Data" / "Scripts.zip"
    if not scripts_zip.is_file():
        return None, None
    with zipfile.ZipFile(scripts_zip) as z:
        z.extractall(base)
    flg = next(base.rglob("TESV_Papyrus_Flags.flg"), None)
    if flg is None:
        return None, None
    skse_src = _find_skse_source()
    if skse_src is not None:
        for f in skse_src.glob("*.psc"):
            shutil.copy(f, src)
    for bsa in _find_racemenu_bsas():
        try:
            arc = BSAArchive(bsa, eager=False)
            for n in arc.list_files(""):
                if n.lower().endswith(".psc") and "source" in n.lower():
                    (src / Path(n).name).write_bytes(arc.read_file(n))
        except Exception:
            continue
    return src, flg.parent


def _compile_psc(compiler, psc_path, src, basesrc, out_dir):
    """Compile one .psc to .pex, written ATOMICALLY into out_dir. Compiles into a
    private temp dir first, then atomic_write_bytes the .pex into out_dir, so a
    killed/locked compile never leaves a truncated .pex that CTDs RaceMenu.
    Returns (pex_path|None, log). Treats a non-zero compiler exit as failure
    (a stale .pex left in out_dir can't masquerade as success)."""
    import shutil
    import tempfile
    from .atomic_io import atomic_write_bytes
    flg = next(Path(basesrc).glob("TESV_Papyrus_Flags.flg"), None)
    if flg is None:
        return None, "no flags file"
    shutil.copy(psc_path, src)               # target joins the import dir
    tmp_out = tempfile.mkdtemp(prefix="ube_pex_")
    try:
        r = subprocess.run(
            [str(compiler), Path(psc_path).stem,
             "-import=" + str(src) + ";" + str(basesrc),
             "-output=" + tmp_out, "-flags=" + str(flg)],
            capture_output=True, text=True, cwd=tmp_out,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pex_tmp = Path(tmp_out) / (Path(psc_path).stem + ".pex")
        if r.returncode != 0 or not pex_tmp.is_file():
            return None, (r.stdout + r.stderr)
        final = Path(out_dir) / (Path(psc_path).stem + ".pex")
        atomic_write_bytes(final, pex_tmp.read_bytes())
        return final, (r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)


def _build_overlay_source_map(layout, skip_mods=()):
    """rel_texture (lowered, fwd-slash) -> source rec, to resolve an AddFeetPaint
    path to its real DDS. Mirrors discover_overlays PASS 1 (loose wins, then BSA);
    iterates in LOAD ORDER so `setdefault` keeps the active (winning) source."""
    from .bsa_strings import BSAArchive
    out: dict = {}
    mr = _paths.mods_root()
    if mr is None:
        return out
    skip_lower = {s.lower() for s in skip_mods}
    ordered = _paths.enabled_mods_ordered(layout)
    mod_names = ordered if ordered else sorted(
        d.name for d in mr.iterdir() if d.is_dir())
    for mod_name in mod_names:
        if mod_name.lower() in skip_lower:
            continue
        mod = mr / mod_name
        if not mod.is_dir():
            continue
        for root in _OVERLAY_ROOTS:
            d = mod / Path(root)
            if d.is_dir():
                for f in d.rglob("*.dds"):
                    out.setdefault(f.relative_to(mod).as_posix().lower(),
                                   ("loose", f))
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False)
            except Exception:
                continue
            for root in _OVERLAY_ROOTS:
                try:
                    names = arc.list_files(root)
                except Exception:
                    continue
                for n in names:
                    rel = n.replace("\\", "/").lower()
                    if rel.endswith(".dds") and rel.startswith(root):
                        out.setdefault(rel, ("bsa", arc, n))
    return out


def _iter_feetpaint_scripts(layout, skip_mods=()):
    """Yield (psc_name, text) for each loose/BSA .psc that calls AddFeetPaint, in
    LOAD ORDER and DE-DUPED by script name -- only the load-order WINNER of a
    given name is processed, else a second mod's same-named RaceMenuBase would
    silently overwrite the first's recompiled .pex."""
    from .bsa_strings import BSAArchive
    mr = _paths.mods_root()
    if mr is None:
        return
    skip_lower = {s.lower() for s in skip_mods}
    seen: set = set()
    ordered = _paths.enabled_mods_ordered(layout)
    mod_names = ordered if ordered else sorted(
        d.name for d in mr.iterdir() if d.is_dir())
    for mod_name in mod_names:
        if mod_name.lower() in skip_lower:
            continue
        mod = mr / mod_name
        if not mod.is_dir():
            continue
        for f in mod.rglob("*.psc"):
            nm = f.name.lower()
            if nm in seen:
                continue
            try:
                t = f.read_text("utf-8", "replace")
            except OSError:
                continue
            seen.add(nm)                 # this name's load-order winner
            if "AddFeetPaint" in t:
                yield f.name, t
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False)
                names = arc.list_files("")
            except Exception:
                continue
            for n in names:
                if not n.lower().endswith(".psc"):
                    continue
                base = n.rsplit("/", 1)[-1]
                if base.lower() in seen:
                    continue
                try:
                    d = arc.read_file(n)
                except Exception:
                    continue
                seen.add(base.lower())
                t = (d.decode("utf-8", "replace")
                     if isinstance(d, (bytes, bytearray)) else str(d))
                if "AddFeetPaint" in t:
                    yield base, t


def build_multislot_feet_overlays(layout, out_root, texconv, *, skip_mods=(),
                                  limit: int = 0, log=print) -> int:
    """Bake feet-UV variants for body-textures reused on the feet slot and
    recompile their scripts to point AddFeetPaint at them. Returns the number of
    feet-UV textures written. Best-effort: no-ops (0) if the Papyrus toolchain or
    feet correspondence is unavailable. See the section header for the why."""
    import tempfile
    import shutil
    from . import overlay_slots as _osl
    compiler = find_papyrus_compiler()
    if compiler is None:
        log("  multi-slot feet pass SKIPPED: PapyrusCompiler.exe not found "
            "(set CBBE2UBE_PAPYRUS_COMPILER)")
        return 0
    feet_corr = build_region_correspondence("feet")
    if feet_corr is None:
        log("  multi-slot feet pass SKIPPED: feet CBBE/UBE ref not found")
        return 0
    slot_map = _osl.build_script_slot_map(layout)
    work = Path(tempfile.mkdtemp(prefix="ube_feet_"))
    try:
        src, basesrc = _assemble_papyrus_imports(compiler, work)
        if src is None:
            log("  multi-slot feet pass SKIPPED: Papyrus base (Scripts.zip) "
                "not found")
            return 0
        srcmap = _build_overlay_source_map(layout, skip_mods=skip_mods)
        twork = work / "tw"
        twork.mkdir()
        pex_out = Path(out_root) / "Scripts"
        pex_out.mkdir(parents=True, exist_ok=True)
        n_tex = n_compiled = n_scripts = 0
        for name, text in _iter_feetpaint_scripts(layout, skip_mods=skip_mods):
            repoint = {}     # normalized rel -> feet-UV rel
            for m in _ADD_FEETPAINT_RE.finditer(text):
                rel = _osl.normalize_script_texpath(m.group(2))
                if "body" not in (slot_map.get(rel) or set()):
                    continue                 # feet-only: region pass handles it
                repoint[rel] = _feet_variant_path(rel)
            if not repoint:
                continue
            baked: set = set()   # only repoint paints whose feet-UV DDS wrote
            for rel, newrel in repoint.items():
                srcrec = srcmap.get(rel)
                if not srcrec:
                    log(f"  !! feet pass: source DDS not found: {rel}")
                    continue
                if srcrec[0] == "loose":
                    src_dds = srcrec[1]
                else:
                    src_dds = twork / "s.dds"
                    src_dds.write_bytes(srcrec[1].read_file(srcrec[2]))
                outp = Path(out_root) / newrel.replace("/", "\\")
                try:
                    rgba = dds_to_rgba(src_dds, texconv, twork)
                    rgba_to_dds(transfer_overlay(rgba, feet_corr), outp,
                                texconv, twork)
                    n_tex += 1
                    baked.add(rel)
                except Exception as e:
                    log(f"  !! feet pass: transfer failed for {rel}: {e!r}")
            if not baked:
                continue     # nothing baked -> don't repoint to a missing DDS
            # Repoint ONLY paints whose feet-UV texture actually wrote, so the
            # recompiled script never points AddFeetPaint at a non-existent file.
            edited = _repoint_feet_script(text, baked)
            epsc = twork / name
            # \n only -- write_text would re-translate and double the source CRs;
            # the compiler rejects bare \r.
            epsc.write_bytes(edited.replace("\r\n", "\n").replace("\r", "\n")
                             .encode("utf-8"))
            pex, clog = _compile_psc(compiler, epsc, src, basesrc, pex_out)
            n_scripts += 1
            if pex:
                n_compiled += 1
            else:
                log(f"  !! feet pass: compile FAILED for {name}: {clog[-400:]}")
            if limit and n_scripts >= limit:
                break
        log(f"  multi-slot feet pass: {n_tex} feet-UV texture(s) from "
            f"{n_compiled}/{n_scripts} recompiled script(s)")
        return n_tex
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _is_male_overlay(rel: str) -> bool:
    """True if an overlay path looks MALE (a 'male' marker that is NOT part of
    'female'). Converting male overlays to the (female) UBE UV does not work, so
    they can be skipped."""
    low = rel.replace("\\", "/").lower()
    low = low.split("/overlays/", 1)[-1]        # ignore the fixed path prefix
    return "male" in low.replace("female", "")


def _iter_paint_scripts(layout, skip_mods=(), only_mods=None):
    """Yield (mod_name, psc_name, text) for each loose/BSA .psc registering ANY
    AddXPaint overlay, in load order, de-duped by script name (load-order winner
    only). `only_mods` (case-insensitive names) limits to those mods."""
    from .bsa_strings import BSAArchive
    from . import overlay_slots as _osl
    mr = _paths.mods_root()
    if mr is None:
        return
    skip_lower = {s.lower() for s in skip_mods}
    only_lower = {s.lower() for s in only_mods} if only_mods else None
    seen: set = set()
    ordered = _paths.enabled_mods_ordered(layout)
    mod_names = ordered if ordered else sorted(
        d.name for d in mr.iterdir() if d.is_dir())
    for mod_name in mod_names:
        if mod_name.lower() in skip_lower:
            continue
        if only_lower is not None and mod_name.lower() not in only_lower:
            continue
        mod = mr / mod_name
        if not mod.is_dir():
            continue
        for f in mod.rglob("*.psc"):
            nm = f.name.lower()
            if nm in seen:
                continue
            try:
                t = f.read_text("utf-8", "replace")
            except OSError:
                continue
            seen.add(nm)
            if _osl._script_has_paint(t):
                yield mod_name, f.name, t
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False)
                names = arc.list_files("")
            except Exception:
                continue
            for n in names:
                if not n.lower().endswith(".psc"):
                    continue
                base = n.rsplit("/", 1)[-1]
                if base.lower() in seen:
                    continue
                try:
                    d = arc.read_file(n)
                except Exception:
                    continue
                seen.add(base.lower())
                t = (d.decode("utf-8", "replace")
                     if isinstance(d, (bytes, bytearray)) else str(d))
                if _osl._script_has_paint(t):
                    yield mod_name, base, t


def add_ube_overlay_copies(layout, out_root, texconv, *,
                           regions=("body", "hands", "feet"),
                           skip_mods=(), only_mods=None, skip_male=False,
                           limit: int = 0, log=print) -> dict:
    """'Add UBE copy' overlay mode: for each registered overlay, bake a UBE-UV
    variant to a `ube/` path AND splice `AddXPaint("UBE <name>", <ube path>)` into
    its RaceMenu script, then recompile -- so the list shows BOTH the untouched
    original (correct on non-UBE bodies) and the UBE-fit copy. Requires the Papyrus
    toolchain; no-ops (returns reason) if texconv/compiler/base are missing."""
    import shutil
    import tempfile
    from . import overlay_slots as _osl
    texconv = texconv or find_texconv()
    if texconv is None:
        log("  overlay UBE-copy SKIPPED: texconv not found (set CBBE2UBE_TEXCONV)")
        return {"copies": 0, "reason": "no-texconv"}
    compiler = find_papyrus_compiler()
    if compiler is None:
        log("  overlay UBE-copy SKIPPED: PapyrusCompiler.exe not found "
            "(set CBBE2UBE_PAPYRUS_COMPILER)")
        return {"copies": 0, "reason": "no-compiler"}
    corr_cache: dict = {}

    def _corr(region):
        if region not in corr_cache:
            corr_cache[region] = build_region_correspondence(region)
        return corr_cache[region]

    work = Path(tempfile.mkdtemp(prefix="ube_ovcopy_"))
    try:
        src, basesrc = _assemble_papyrus_imports(compiler, work)
        if src is None:
            log("  overlay UBE-copy SKIPPED: Papyrus base (Scripts.zip) not found")
            return {"copies": 0, "reason": "no-papyrus-base"}
        srcmap = _build_overlay_source_map(layout, skip_mods=skip_mods)
        twork = work / "tw"
        twork.mkdir()
        pex_out = Path(out_root) / "Scripts"
        pex_out.mkdir(parents=True, exist_ok=True)
        n_tex = n_scripts = n_compiled = 0
        remaining = limit
        for mod_name, name, text in _iter_paint_scripts(
                layout, skip_mods=skip_mods, only_mods=only_mods):
            baked: set = set()      # normalized rels a UBE variant was written for
            for slot, rel in _osl.iter_paint_calls(text):
                if slot not in regions:         # head/warpaint -> never remapped
                    continue
                if skip_male and _is_male_overlay(rel):
                    continue
                if limit and remaining <= 0:
                    break
                srcrec = srcmap.get(rel)
                if not srcrec:
                    continue
                corr = _corr(slot)
                if corr is None:
                    continue
                if srcrec[0] == "loose":
                    src_dds = srcrec[1]
                else:
                    src_dds = twork / "s.dds"
                    try:
                        src_dds.write_bytes(srcrec[1].read_file(srcrec[2]))
                    except Exception:
                        continue
                outp = Path(out_root) / _ube_variant_rel(rel).replace("/", "\\")
                if not _paths.is_within_dir(Path(out_root), outp):
                    continue
                try:
                    rgba = dds_to_rgba(src_dds, texconv, twork)
                    rgba_to_dds(transfer_overlay(rgba, corr), outp, texconv, twork)
                    baked.add(rel)
                    n_tex += 1
                    if limit:
                        remaining -= 1
                except Exception as e:
                    log(f"  !! overlay UBE-copy: transfer failed for {rel}: {e!r}")
            if not baked:
                continue
            edited, added = add_ube_paint_lines(text, baked)
            if not added:
                continue
            epsc = twork / name
            epsc.write_bytes(edited.replace("\r\n", "\n").replace("\r", "\n")
                             .encode("utf-8"))
            pex, clog = _compile_psc(compiler, epsc, src, basesrc, pex_out)
            n_scripts += 1
            if pex:
                n_compiled += 1
            else:
                log(f"  !! overlay UBE-copy: compile FAILED for {name}: "
                    f"{(clog or '')[-300:]}")
        log(f"  overlay UBE-copy: {n_tex} UBE texture(s) added from "
            f"{n_compiled}/{n_scripts} recompiled script(s)")
        return {"copies": n_tex, "scripts": n_scripts, "compiled": n_compiled}
    finally:
        shutil.rmtree(work, ignore_errors=True)
