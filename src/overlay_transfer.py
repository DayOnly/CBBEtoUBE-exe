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
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import paths as _paths

# Overlay paint coverage below this hand-bone-weight is "body". Body overlays
# map to the body UV; we only handle body (+ later hands). Head overlays map to
# the head mesh, which is not a UBE body part -> skipped.
_OVERLAY_ROOT = "textures/actors/character/overlays"


# ---------- texconv locator -------------------------------------------------

_TEXCONV_CACHE: "list[Path | None]" = []


def find_texconv() -> "Path | None":
    """Locate texconv(.exe). Order: env CBBE2UBE_TEXCONV, then the MO2 instance
    tools/ tree, then a bounded scan of the mods root, then PATH. Prefers the
    x64 build. Cached. Returns None if not found (the caller disables the
    overlay feature with a clear message -- it's opt-in anyway)."""
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
            roots.append(mr.parent / "tools")   # <instance>/tools
            roots.append(mr)                     # mods/ (some tool mods ship it)
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
    r = subprocess.run([str(texconv)] + [str(a) for a in args],
                       capture_output=True, text=True)
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
    return _read_tga_rgba(workdir / (Path(dds_path).stem + ".tga"))


def rgba_to_dds(arr: np.ndarray, dds_path, texconv, workdir, fmt="BC3_UNORM"):
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    tga = workdir / "_ovl.tga"
    _write_tga_rgba(arr, tga)
    dds_path = Path(dds_path)
    dds_path.parent.mkdir(parents=True, exist_ok=True)
    _run_texconv(texconv, ["-ft", "dds", "-f", fmt, "-y", "-o", dds_path.parent, tga])
    (dds_path.parent / "_ovl.dds").replace(dds_path)


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
    _map_cache: dict = field(default_factory=dict)   # T -> (ys,xs,su,sv,fy,fx,cov)


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


def _uv_map_for_size(corr: OverlayCorrespondence, T: int):
    """Compute (or fetch cached) the per-texel transfer map for a T x T overlay:
    the covered texels (ys,xs), the source CBBE UV to sample there (su,sv), the
    gutter-fill indices (fy,fx), and the covered mask. This is the EXPENSIVE,
    overlay-independent step (rasterize UBE UV -> 3D -> project to CBBE), so it's
    cached per size and reused across every overlay of that size."""
    cached = corr._map_cache.get(T)
    if cached is not None:
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
    else:
        su = sv = np.zeros(0)
    _, (fy, fx) = ndimage.distance_transform_edt(~cov, return_indices=True)
    m = (ys, xs, su, sv, fy, fx, cov)
    corr._map_cache[T] = m
    return m


def transfer_overlay(src_rgba: np.ndarray,
                     corr: OverlayCorrespondence) -> np.ndarray:
    """Rebake `src_rgba` (CBBE-UV overlay) into UBE-UV space. Returns an
    (T,T,4) RGBA uint8 array (T = source size), gutter-padded. Uses the cached
    per-size UV map, so all but the first overlay of a given size is just a
    bilinear sample + pad."""
    T = src_rgba.shape[0]
    ys, xs, su, sv, fy, fx, cov = _uv_map_for_size(corr, T)
    out = np.zeros((T, T, 4), np.uint8)
    if len(ys):
        out[ys, xs] = np.clip(_bilinear_sample(src_rgba, su, sv),
                              0, 255).astype(np.uint8)
    padded = out[fy, fx]          # gutter <- nearest covered texel
    padded[cov] = out[cov]
    return padded


def build_body_overlay_correspondence(weight: str = "_1") -> "OverlayCorrespondence | None":
    """Build the CBBE<->UBE BODY correspondence from the same body refs the
    armor converter uses. Returns None if a body ref is missing."""
    from . import nif_convert as nc
    from .correspondence import MeshIndex
    from scipy.spatial import cKDTree
    cbbe_path = nc._find_cbbe_base_body(weight)
    ube_path = nc._find_ube_femalebody(weight)
    if cbbe_path is None or ube_path is None:
        return None
    pyn = nc._pynifly()

    def _body(path):
        nf = pyn.NifFile(filepath=str(path))
        s = next((x for x in nf.shapes if x.name in ("BaseShape", "3BA")), None) \
            or max(nf.shapes, key=lambda x: len(x.verts))
        return (np.asarray(s.verts, np.float64), np.asarray(s.uvs, np.float64),
                np.asarray(s.tris, np.int64))
    cbv, cbuv, cbt = _body(cbbe_path)
    ubv, ubuv, ubt = _body(ube_path)
    # CBBE warped into UBE space (anatomical correspondence): reuse the
    # converter's CBBE->UBE delta (NN per CBBE vert). Falls back to a local NN
    # if the cached delta is unavailable.
    cbv_d, delta = nc._cached_cbbe_to_ube_delta(cbbe_path, ube_path)
    if cbv_d is not None and delta is not None and len(cbv_d) == len(cbv):
        cbbe_in_ube = cbv_d + delta
    else:
        _, nn = cKDTree(ubv).query(cbv, k=1)
        cbbe_in_ube = ubv[nn]
    return OverlayCorrespondence(
        ube_verts=ubv, ube_uv=ubuv, ube_tris=ubt,
        cbbe_uv=cbuv, cbbe_tris=cbt,
        cbbe_in_ube_mesh=MeshIndex.build(cbbe_in_ube, cbt))


def convert_overlay(src_dds, out_dds, corr, texconv, workdir):
    """Read one overlay DDS, transfer it to UBE UV, write the BC3 result."""
    src = dds_to_rgba(src_dds, texconv, workdir)
    out = transfer_overlay(src, corr)
    rgba_to_dds(out, out_dds, texconv, workdir)


def classify_overlay(rel_path: str) -> str:
    """Classify an overlay texture by filename -> 'body' | 'hands' | 'feet' |
    'head' | 'other'. CONSERVATIVE: only an overlay whose name explicitly says
    "body" is treated as a body overlay to remap. Everything else (makeup, face
    paint, skin features, warpaints, and any unlabeled overlay) is left ALONE --
    most overlays in a load order are face/makeup using the HEAD UV (which UBE
    doesn't change, so they're not misaligned), and remapping one through the
    BODY correspondence would corrupt it. 'hands'/'feet' are body-region but use
    their OWN UV (a separate transfer, not yet built) so they're skipped too.

    This catches the standard RaceMenu body-paint convention ("NN body.dds",
    Community Overlays, etc.). Body-paint mods that name files without "body"
    won't be picked up -- a deliberate trade (miss-some > corrupt-makeup); the
    keyword set can be widened once we can tell them apart reliably."""
    name = Path(rel_path).name.lower()
    if "body" in name:
        return "body"
    if "hand" in name:
        return "hands"
    if "feet" in name or "foot" in name:
        return "feet"
    if "head" in name or "face" in name:
        return "head"
    return "other"


# ---------- discovery + orchestration ---------------------------------------

def discover_body_overlays(layout) -> "dict[str, tuple]":
    """Find every BODY overlay texture across enabled mods (loose + BSA), in
    MO2 priority order so the load-order WINNER is kept per path. Returns
    {rel_path: source} where rel_path is `textures/.../x.dds` (forward slash,
    lowercased) and source is ("loose", Path, mod) or ("bsa", bsa_path,
    internal_name, mod)."""
    from .bsa_strings import BSAArchive
    mr = _paths.mods_root()
    if mr is None:
        return {}
    ordered = _paths.enabled_mods_ordered(layout)
    if ordered is None:
        ordered = sorted(d.name for d in mr.iterdir() if d.is_dir())
    found: "dict[str, tuple]" = {}      # first (highest-priority) source wins
    rel_root = _OVERLAY_ROOT            # textures/actors/character/overlays
    for mod_name in ordered:
        mod = mr / mod_name
        if not mod.is_dir():
            continue
        ovl_dir = mod / Path(rel_root)
        if ovl_dir.is_dir():
            for f in ovl_dir.rglob("*.dds"):
                rel = f.relative_to(mod).as_posix().lower()
                if rel not in found and classify_overlay(rel) == "body":
                    found[rel] = ("loose", f, mod_name)
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False)   # header only, no whole-file read
                names = arc.list_files(rel_root)
            except Exception:
                continue
            for name in names:
                rel = name.replace("\\", "/").lower()
                if (rel.endswith(".dds") and rel.startswith(rel_root)
                        and rel not in found
                        and classify_overlay(rel) == "body"):
                    found[rel] = ("bsa", bsa, name, mod_name)
    return found


def convert_body_overlays(output_dir, layout, *, texconv=None, log=print,
                          limit: int = 0) -> dict:
    """Discover all body overlays and rebake each into UBE-UV space, writing a
    loose DDS at the original texture path under `output_dir` (RaceMenu loads it
    via load order; no ESP). Opt-in -- gated by the caller's toggle. Returns a
    stats dict. `limit` (>0) caps the count for a quick test run."""
    import tempfile
    from .bsa_strings import BSAArchive
    texconv = texconv or find_texconv()
    if texconv is None:
        log("  !! overlay transfer SKIPPED: texconv not found (set "
            "CBBE2UBE_TEXCONV or install it under the MO2 tools/ folder)")
        return {"converted": 0, "reason": "no-texconv"}
    corr = build_body_overlay_correspondence()
    if corr is None:
        log("  !! overlay transfer SKIPPED: CBBE base or UBE body ref not found")
        return {"converted": 0, "reason": "no-body-ref"}
    overlays = discover_body_overlays(layout)
    if not overlays:
        log("  overlay transfer: no body overlays found in the load order")
        return {"converted": 0, "reason": "none-found"}
    out_root = Path(output_dir)
    work = Path(tempfile.mkdtemp(prefix="ube_overlay_"))
    n = 0
    failed: list = []
    arc_cache: dict = {}                            # bsa path -> BSAArchive (lazy)
    items = list(overlays.items())
    if limit:
        items = items[:limit]
    log(f"  overlay transfer: {len(items)} body overlay(s) "
        f"(of {len(overlays)} found) -> UBE UV ...")
    for rel, src in items:
        try:
            if src[0] == "loose":
                src_dds = src[1]
            else:                                  # bsa -> extract to temp
                arc = arc_cache.get(src[1])
                if arc is None:
                    arc = BSAArchive(src[1], eager=False)
                    arc_cache[src[1]] = arc
                data = arc.read_file(src[2])
                if not data:
                    raise RuntimeError("BSA extract returned no data")
                src_dds = work / "src.dds"
                src_dds.write_bytes(data)
            out_dds = out_root / rel.replace("/", "\\")
            convert_overlay(src_dds, out_dds, corr, texconv, work / "w")
            n += 1
        except Exception as e:
            failed.append((rel, repr(e)))
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    if failed:
        log(f"  !! overlay transfer: {len(failed)} failed (e.g. {failed[0]})")
    log(f"  overlay transfer: {n} overlay(s) written under {out_root}")
    return {"converted": n, "failed": failed, "total": len(overlays)}
