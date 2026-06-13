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
    from .correspondence import MeshIndex
    from scipy.spatial import cKDTree
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
    # CBBE warped into UBE space (anatomical correspondence): each CBBE vert ->
    # its nearest UBE vert. Identical to the converter's CBBE->UBE body delta
    # (which is the same NN); computed inline so it works for any region.
    _, nn = cKDTree(ubv).query(cbv, k=1)
    cbbe_in_ube = ubv[nn]
    return OverlayCorrespondence(
        ube_verts=ubv, ube_uv=ubuv, ube_tris=ubt,
        cbbe_uv=cbuv, cbbe_tris=cbt,
        cbbe_in_ube_mesh=MeshIndex.build(cbbe_in_ube, cbt))


def build_region_correspondence(region: str, weight: str = "_1"
                                ) -> "OverlayCorrespondence | None":
    cbbe, ube = _find_region_meshes(region, weight)
    prefer = ("BaseShape", "3BA") if region == "body" else ("BaseShape",)
    return build_overlay_correspondence(cbbe, ube, prefer)


def build_body_overlay_correspondence(weight: str = "_1") -> "OverlayCorrespondence | None":
    """Back-compat: the BODY correspondence."""
    return build_region_correspondence("body", weight)


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
    doesn't change, so they're not misaligned), and remapping one through a body
    correspondence would corrupt it. 'hands'/'feet' ARE remapped, each via its
    own region correspondence (UBE hands/feet UV differ from CBBE too). 'head' is
    never remapped (the head mesh is not a UBE body part).

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

def discover_overlays(layout, regions=("body", "hands", "feet")) -> "dict[str, dict]":
    """Find every overlay texture across enabled mods (loose + BSA), in MO2
    priority order so the load-order WINNER is kept per path, bucketed by region.
    Returns {region: {rel_path: source}} where rel_path is `textures/.../x.dds`
    (forward slash, lowercased) and source is ("loose", Path, mod) or ("bsa",
    bsa_path, internal_name, mod). Only the requested regions are kept (head /
    makeup / unlabeled overlays are never collected)."""
    from .bsa_strings import BSAArchive
    mr = _paths.mods_root()
    out: "dict[str, dict]" = {r: {} for r in regions}
    if mr is None:
        return out
    ordered = _paths.enabled_mods_ordered(layout)
    if ordered is None:
        ordered = sorted(d.name for d in mr.iterdir() if d.is_dir())
    seen: set = set()                   # first (highest-priority) source wins
    rel_root = _OVERLAY_ROOT
    want = set(regions)

    def _take(rel, source):
        if rel in seen:
            return
        reg = classify_overlay(rel)
        if reg in want:
            seen.add(rel)
            out[reg][rel] = source

    for mod_name in ordered:
        mod = mr / mod_name
        if not mod.is_dir():
            continue
        ovl_dir = mod / Path(rel_root)
        if ovl_dir.is_dir():
            for f in ovl_dir.rglob("*.dds"):
                _take(f.relative_to(mod).as_posix().lower(),
                      ("loose", f, mod_name))
        for bsa in mod.glob("*.bsa"):
            try:
                names = BSAArchive(bsa, eager=False).list_files(rel_root)
            except Exception:
                continue
            for name in names:
                rel = name.replace("\\", "/").lower()
                if rel.endswith(".dds") and rel.startswith(rel_root):
                    _take(rel, ("bsa", bsa, name, mod_name))
    return out


def convert_overlays(output_dir, layout, *, regions=("body", "hands", "feet"),
                     texconv=None, log=print, limit: int = 0) -> dict:
    """Rebake CBBE/3BA overlays into UBE-UV space for each region, writing a
    loose DDS at the original texture path under `output_dir` (RaceMenu loads it
    via load order; no ESP). Opt-in. Builds one correspondence per region (the
    expensive part, reused across that region's overlays). Returns a stats dict.
    `limit` (>0) caps the TOTAL count for a quick test run."""
    import shutil
    import tempfile
    from .bsa_strings import BSAArchive
    texconv = texconv or find_texconv()
    if texconv is None:
        log("  !! overlay transfer SKIPPED: texconv not found (set "
            "CBBE2UBE_TEXCONV or install it under the MO2 tools/ folder)")
        return {"converted": 0, "reason": "no-texconv"}
    by_region = discover_overlays(layout, regions)
    total = sum(len(v) for v in by_region.values())
    if total == 0:
        log("  overlay transfer: no body/hands/feet overlays found")
        return {"converted": 0, "reason": "none-found"}
    out_root = Path(output_dir)
    work = Path(tempfile.mkdtemp(prefix="ube_overlay_"))
    arc_cache: dict = {}
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
        corr = build_region_correspondence(region)
        if corr is None:
            log(f"  !! overlay transfer: SKIP region '{region}' "
                f"(CBBE/UBE {region} ref not found) -- {len(items)} overlay(s)")
            continue
        log(f"  overlay transfer [{region}]: {len(items)} overlay(s) -> UBE UV ...")
        rn = 0
        for rel, src in items:
            try:
                if src[0] == "loose":
                    src_dds = src[1]
                else:
                    arc = arc_cache.get(src[1])
                    if arc is None:
                        arc = BSAArchive(src[1], eager=False)
                        arc_cache[src[1]] = arc
                    data = arc.read_file(src[2])
                    if not data:
                        raise RuntimeError("BSA extract returned no data")
                    src_dds = work / "src.dds"
                    src_dds.write_bytes(data)
                convert_overlay(src_dds, out_root / rel.replace("/", "\\"),
                                corr, texconv, work / "w")
                rn += 1
                n += 1
                if limit:
                    remaining -= 1
                    if remaining <= 0:
                        break
            except Exception as e:
                failed.append((rel, repr(e)))
        per_region[region] = rn
    shutil.rmtree(work, ignore_errors=True)
    if failed:
        log(f"  !! overlay transfer: {len(failed)} failed (e.g. {failed[0]})")
    log(f"  overlay transfer: {n} overlay(s) written under {out_root} "
        f"({', '.join(f'{r}={c}' for r, c in per_region.items())})")
    return {"converted": n, "failed": failed, "total": total,
            "per_region": per_region}


def convert_body_overlays(output_dir, layout, **kw) -> dict:
    """Back-compat: body overlays only."""
    return convert_overlays(output_dir, layout, regions=("body",), **kw)


def discover_body_overlays(layout) -> "dict[str, tuple]":
    """Back-compat: just the body overlays as {rel: source}."""
    return discover_overlays(layout, regions=("body",)).get("body", {})
