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

"""Headless morph-preview renderer for converted UBE NIFs.

For each NIF we produce a single BMP showing front, side, and back
orthographic silhouettes of all visible shapes. Each vert is colored
by the maximum morph-delta magnitude across every slider in the
BODYTRI's per-shape morph table.

Why this catches morph issues without launching Skyrim:

  * Shapes whose name doesn't appear in the BODYTRI render in dim
    GRAY. They won't move when sliders change. Whether that's a bug
    depends on the slot: a chest / cuirass piece showing up gray
    means body poke-through in game; a metal pauldron / amulet
    chain that doesn't morph is fine. The image flags it; you decide.
  * Shapes covered by the BODYTRI render with a BLUE -> YELLOW ->
    RED ramp; per-vert magnitude is the worst-case ||delta|| across
    sliders. Bright red blobs on tight regions usually mean broken
    correspondence (e.g. a morph delta of 6 units when neighbors are
    at 0.5 — the slider will tear the mesh under that vert).
  * Per-region asymmetry — e.g. only one buttock turning red on the
    "CBBE Big Butt" slider — points at an indexing bug in the TRI
    generator.
  * The warnings list separates *expected* static (no BODYTRI string
    at all — e.g. an amulet) from *broken* static (the BODYTRI
    string is present but the .tri file is missing on disk, or
    parsing failed). The latter is a converter bug.

Implementation notes:

  * Pure numpy + struct. No matplotlib / PIL / Pillow / PyOpenGL.
    Output is 24-bit uncompressed BMP — every Windows image viewer
    opens it natively. Keeping the dep list at just numpy + scipy
    + pynifly matches the rest of the project.
  * Renders verts as 2x2 pixel dots (no triangle rasterizer). For
    QA the point cloud silhouette is enough — we're looking for
    "is this shape in the right place" and "are some verts ten units
    off" type bugs, not photorealism.
  * Skyrim coords: +X = character right, +Y = forward, +Z = up.
    Front view drops Y (camera looks at +Y from -Y); side view
    drops X with +X behind camera, so character faces +screen-right;
    back view mirrors front along X.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .tri import TriFile


# ---------------------------------------------------------------------------
# BMP writer
# ---------------------------------------------------------------------------

def write_bmp(path: str | Path, pixels: np.ndarray) -> None:
    """Write a (H, W, 3) uint8 BGR array as 24-bit uncompressed BMP.

    BMP stores rows bottom-up, padded to 4-byte multiples. We accept
    pixels as conventional top-down (row 0 = top of image, BGR per
    pixel) — write_bmp handles the flip and the padding.
    """
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError(
            f"write_bmp expects (H,W,3) uint8 BGR, got {pixels.shape}")
    H, W = pixels.shape[:2]
    row_bytes = W * 3
    pad = (-row_bytes) & 3
    row_size = row_bytes + pad
    img_size = row_size * H
    file_size = 54 + img_size

    with open(path, "wb") as f:
        # BITMAPFILEHEADER (14 bytes)
        f.write(b"BM")
        f.write(struct.pack("<I", file_size))
        f.write(b"\x00\x00\x00\x00")         # reserved
        f.write(struct.pack("<I", 54))       # pixel data offset
        # BITMAPINFOHEADER (40 bytes)
        f.write(struct.pack("<I", 40))       # header size
        f.write(struct.pack("<i", W))
        f.write(struct.pack("<i", H))        # positive = bottom-up
        f.write(struct.pack("<H", 1))        # planes
        f.write(struct.pack("<H", 24))       # bpp
        f.write(struct.pack("<I", 0))        # BI_RGB no compression
        f.write(struct.pack("<I", img_size))
        f.write(struct.pack("<i", 2835))     # 72 DPI x
        f.write(struct.pack("<i", 2835))     # 72 DPI y
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", 0))
        pad_bytes = b"\x00" * pad if pad else b""
        for y in range(H - 1, -1, -1):
            f.write(pixels[y].tobytes())
            if pad_bytes:
                f.write(pad_bytes)


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

VIEW_FRONT = "front"
VIEW_SIDE = "side"
VIEW_BACK = "back"
VIEWS = (VIEW_FRONT, VIEW_SIDE, VIEW_BACK)


def project_2d(verts: np.ndarray, view: str) -> tuple[np.ndarray, np.ndarray]:
    """Project (N, 3) verts to 2D image coords for the named view.

    Returns (u, v): u = horizontal axis, v = vertical axis. Both still
    in world units; the caller maps them to pixels using the global
    bounding box across all views (so all three panels share scale).
    """
    if view == VIEW_FRONT:
        return verts[:, 0], verts[:, 2]
    if view == VIEW_SIDE:
        return -verts[:, 1], verts[:, 2]
    if view == VIEW_BACK:
        return -verts[:, 0], verts[:, 2]
    raise ValueError(f"unknown view: {view!r}")


def _global_uv_bounds(all_verts: np.ndarray
                      ) -> tuple[float, float, float, float]:
    """Return (umin, umax, vmin, vmax) — the union of u/v ranges across
    front/side/back views. Putting all three panels on the same scale
    makes asymmetric morphs visible at a glance (a left-only red blob
    in front view aligns with the same height in side view).

    For a single set of verts:
      front:  u = X,  side: u = -Y,  back: u = -X
    So:
      u_union_min = min(X.min(), -Y.max(), -X.max())
      u_union_max = max(X.max(), -Y.min(), -X.min())
      v is always Z.
    """
    X = all_verts[:, 0]
    Y = all_verts[:, 1]
    Z = all_verts[:, 2]
    umin = float(min(X.min(), -Y.max(), -X.max()))
    umax = float(max(X.max(), -Y.min(), -X.min()))
    vmin = float(Z.min())
    vmax = float(Z.max())
    return umin, umax, vmin, vmax


# ---------------------------------------------------------------------------
# Color ramp
# ---------------------------------------------------------------------------

def magnitude_to_bgr(mag: np.ndarray, vmax: float) -> np.ndarray:
    """Map per-vert magnitude (N,) to (N, 3) uint8 BGR via blue -> yellow
    -> red ramp. `vmax` defines the full-red point; magnitudes >= vmax
    saturate to pure red.

    0    -> (180, 100, 50)   dim blue
    0.5v -> (50,  220, 220)  yellow
    1.0v -> (50,  50,  255)  red
    """
    if vmax <= 0:
        norm = np.zeros_like(mag, dtype=np.float64)
    else:
        norm = np.clip(mag.astype(np.float64) / vmax, 0.0, 1.0)

    half = norm <= 0.5
    n2 = np.where(half, norm * 2.0, (norm - 0.5) * 2.0)

    bgr = np.empty((len(mag), 3), dtype=np.uint8)
    # Lower half: (180,100,50) -> (50,220,220)
    b_low = 180 + (50 - 180) * n2
    g_low = 100 + (220 - 100) * n2
    r_low = 50  + (220 - 50)  * n2
    # Upper half: (50,220,220) -> (50,50,255)
    b_hi = 50  + (50 - 50) * n2
    g_hi = 220 + (50 - 220) * n2
    r_hi = 220 + (255 - 220) * n2

    bgr[:, 0] = np.clip(np.where(half, b_low, b_hi), 0, 255).astype(np.uint8)
    bgr[:, 1] = np.clip(np.where(half, g_low, g_hi), 0, 255).astype(np.uint8)
    bgr[:, 2] = np.clip(np.where(half, r_low, r_hi), 0, 255).astype(np.uint8)
    return bgr


# ---------------------------------------------------------------------------
# Shape data
# ---------------------------------------------------------------------------

@dataclass
class _PreviewShape:
    name: str
    verts: np.ndarray          # (N, 3) float32
    mag: np.ndarray | None     # (N,) float32, or None when not in TRI


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BG_COLOR = (24, 24, 24)        # near-black background
NO_MORPH_GRAY = (110, 110, 110)
TITLE_COLOR = (210, 210, 210)
HEADER_BG = (40, 40, 40)
PAD_PX = 8                     # panel inner padding
DEFAULT_PANEL_SIZE = (300, 460)  # (W, H) per panel — yields ~1.2 MB BMPs


# ---------------------------------------------------------------------------
# View renderer (vert-cloud scatter with global magnitude sort)
# ---------------------------------------------------------------------------

def _render_view(shapes: list[_PreviewShape], view: str,
                 panel_size: tuple[int, int],
                 bounds: tuple[float, float, float, float],
                 color_vmax: float) -> np.ndarray:
    """Render one viewport. Returns (H, W, 3) uint8 BGR.

    All shapes' verts are gathered first, then drawn in a single
    magnitude-sorted pass so the brightest red pixels always sit
    on top of any cool-band pixels that happen to project to the
    same screen position — regardless of which shape they came from.
    """
    W, H = panel_size
    img = np.full((H, W, 3), BG_COLOR, dtype=np.uint8)

    umin, umax, vmin, vmax = bounds
    span_u = umax - umin
    span_v = vmax - vmin
    if span_u <= 0 or span_v <= 0:
        return img

    # Keep aspect: pick the tighter of the two scales.
    aw = (W - 2 * PAD_PX) / span_u
    ah = (H - 2 * PAD_PX) / span_v
    s = min(aw, ah)
    cx = W * 0.5 - 0.5 * (umin + umax) * s
    cy = H * 0.5 - 0.5 * (vmin + vmax) * s

    # Gather x, y, color, sort-key for every vert across every shape.
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    keys: list[np.ndarray] = []

    for sh in shapes:
        u, v = project_2d(sh.verts, view)
        x = np.rint(u * s + cx).astype(np.int32)
        y = np.rint(H - 1 - (v * s + cy)).astype(np.int32)
        if sh.mag is None:
            color = np.tile(np.array(NO_MORPH_GRAY, dtype=np.uint8),
                            (len(sh.verts), 1))
            # Sort key: gray shapes go to the BOTTOM (drawn first),
            # so any morphed shape that touches the same pixel wins.
            key = np.full(len(sh.verts), -1.0, dtype=np.float64)
        else:
            color = magnitude_to_bgr(sh.mag, color_vmax)
            key = sh.mag.astype(np.float64, copy=False)
        xs.append(x); ys.append(y); cols.append(color); keys.append(key)

    if not xs:
        return img
    X = np.concatenate(xs)
    Y = np.concatenate(ys)
    C = np.concatenate(cols, axis=0)
    K = np.concatenate(keys)

    # Sort ascending so high-magnitude verts paint LAST.
    order = np.argsort(K, kind="stable")
    X = X[order]; Y = Y[order]; C = C[order]

    # Plot main pixel + 3 satellites (2x2 dot). Each pass is
    # vectorized; the global sort order ensures red overwrites blue.
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        x2 = X + dx
        y2 = Y + dy
        m = (x2 >= 0) & (x2 < W) & (y2 >= 0) & (y2 < H)
        img[y2[m], x2[m]] = C[m]
    return img


# ---------------------------------------------------------------------------
# Text glyphs for panel + header labels
# ---------------------------------------------------------------------------

# 5x7 ASCII glyph atlas. Just enough to label panels with
# 'FRONT' / 'SIDE' / 'BACK' and write the NIF filename in the header.
# Format: rows separated by '\n', '#' = pixel on, '.' = off.
_GLYPHS = {
    "A": ".###.\n#...#\n#...#\n#####\n#...#\n#...#\n#...#",
    "B": "####.\n#...#\n#...#\n####.\n#...#\n#...#\n####.",
    "C": ".####\n#....\n#....\n#....\n#....\n#....\n.####",
    "D": "####.\n#...#\n#...#\n#...#\n#...#\n#...#\n####.",
    "E": "#####\n#....\n#....\n####.\n#....\n#....\n#####",
    "F": "#####\n#....\n#....\n####.\n#....\n#....\n#....",
    "G": ".####\n#....\n#....\n#..##\n#...#\n#...#\n.####",
    "H": "#...#\n#...#\n#...#\n#####\n#...#\n#...#\n#...#",
    "I": "#####\n..#..\n..#..\n..#..\n..#..\n..#..\n#####",
    "J": "..###\n...#.\n...#.\n...#.\n...#.\n#..#.\n.##..",
    "K": "#...#\n#..#.\n#.#..\n##...\n#.#..\n#..#.\n#...#",
    "L": "#....\n#....\n#....\n#....\n#....\n#....\n#####",
    "M": "#...#\n##.##\n#.#.#\n#...#\n#...#\n#...#\n#...#",
    "N": "#...#\n##..#\n#.#.#\n#..##\n#...#\n#...#\n#...#",
    "O": ".###.\n#...#\n#...#\n#...#\n#...#\n#...#\n.###.",
    "P": "####.\n#...#\n#...#\n####.\n#....\n#....\n#....",
    "Q": ".###.\n#...#\n#...#\n#...#\n#.#.#\n#..#.\n.##.#",
    "R": "####.\n#...#\n#...#\n####.\n#.#..\n#..#.\n#...#",
    "S": ".####\n#....\n#....\n.###.\n....#\n....#\n####.",
    "T": "#####\n..#..\n..#..\n..#..\n..#..\n..#..\n..#..",
    "U": "#...#\n#...#\n#...#\n#...#\n#...#\n#...#\n.###.",
    "V": "#...#\n#...#\n#...#\n#...#\n#...#\n.#.#.\n..#..",
    "W": "#...#\n#...#\n#...#\n#...#\n#.#.#\n##.##\n#...#",
    "X": "#...#\n#...#\n.#.#.\n..#..\n.#.#.\n#...#\n#...#",
    "Y": "#...#\n#...#\n.#.#.\n..#..\n..#..\n..#..\n..#..",
    "Z": "#####\n....#\n...#.\n..#..\n.#...\n#....\n#####",
    "0": ".###.\n#...#\n#..##\n#.#.#\n##..#\n#...#\n.###.",
    "1": "..#..\n.##..\n..#..\n..#..\n..#..\n..#..\n.###.",
    "2": ".###.\n#...#\n....#\n...#.\n..#..\n.#...\n#####",
    "3": "####.\n....#\n....#\n.###.\n....#\n....#\n####.",
    "4": "...#.\n..##.\n.#.#.\n#..#.\n#####\n...#.\n...#.",
    "5": "#####\n#....\n####.\n....#\n....#\n#...#\n.###.",
    "6": ".###.\n#....\n#....\n####.\n#...#\n#...#\n.###.",
    "7": "#####\n....#\n...#.\n..#..\n.#...\n.#...\n.#...",
    "8": ".###.\n#...#\n#...#\n.###.\n#...#\n#...#\n.###.",
    "9": ".###.\n#...#\n#...#\n.####\n....#\n....#\n.###.",
    " ": ".....\n.....\n.....\n.....\n.....\n.....\n.....",
    ".": ".....\n.....\n.....\n.....\n.....\n..#..\n..#..",
    "_": ".....\n.....\n.....\n.....\n.....\n.....\n#####",
    "-": ".....\n.....\n.....\n#####\n.....\n.....\n.....",
    ":": ".....\n..#..\n..#..\n.....\n..#..\n..#..\n.....",
    "/": "....#\n....#\n...#.\n..#..\n.#...\n#....\n#....",
    "(": "..##.\n.#...\n#....\n#....\n#....\n.#...\n..##.",
    ")": ".##..\n...#.\n....#\n....#\n....#\n...#.\n.##..",
    "=": ".....\n.....\n#####\n.....\n#####\n.....\n.....",
    "!": "..#..\n..#..\n..#..\n..#..\n..#..\n.....\n..#..",
}
_GLYPH_W, _GLYPH_H = 5, 7

# Precompile each glyph into a (7, 5) bool array — one-time cost,
# saves the inner-loop string parse on every char draw.
_GLYPH_ARRAYS: dict[str, np.ndarray] = {
    ch: np.array(
        [[c == "#" for c in row] for row in pat.split("\n")],
        dtype=bool)
    for ch, pat in _GLYPHS.items()
}


def _draw_text(img: np.ndarray, text: str, x: int, y: int,
               color: tuple[int, int, int] = TITLE_COLOR,
               scale: int = 1) -> None:
    """Draw ASCII text at (x, y). Unknown chars draw as space. Text is
    uppercased to match the atlas."""
    H, W = img.shape[:2]
    col = np.array(color, dtype=np.uint8)
    cx = x
    for ch in text.upper():
        glyph = _GLYPH_ARRAYS.get(ch, _GLYPH_ARRAYS[" "])
        for row_i in range(_GLYPH_H):
            row_mask = glyph[row_i]
            py = y + row_i * scale
            if py < 0 or py + scale > H:
                continue
            for col_i in range(_GLYPH_W):
                if not row_mask[col_i]:
                    continue
                px = cx + col_i * scale
                if px < 0 or px + scale > W:
                    continue
                img[py:py + scale, px:px + scale] = col
        cx += (_GLYPH_W + 1) * scale


def _draw_legend(img: np.ndarray, x: int, y: int, w: int, h: int,
                 vmax: float) -> None:
    """Draw the color ramp as a horizontal strip plus '0' / 'vmax'
    labels — explains the per-vert color coding inline so readers
    don't need to remember which end is high."""
    if w < 8 or h < 4:
        return
    H, W = img.shape[:2]
    # Clamp draw region
    x0 = max(0, x); x1 = min(W, x + w)
    y0 = max(0, y); y1 = min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    grad_w = x1 - x0
    mags = np.linspace(0.0, vmax, grad_w, dtype=np.float32)
    row = magnitude_to_bgr(mags, vmax if vmax > 0 else 1.0)
    img[y0:y1, x0:x1] = row[None, :, :]
    # Numeric end-labels just below the strip.
    label_y = y1 + 2
    if label_y + _GLYPH_H + 1 < H:
        _draw_text(img, "0", x0, label_y)
        max_label = f"{vmax:.1f}U"
        # Right-align by approximate width.
        text_w = len(max_label) * (_GLYPH_W + 1)
        _draw_text(img, max_label, x1 - text_w, label_y)


# ---------------------------------------------------------------------------
# BODYTRI resolution
# ---------------------------------------------------------------------------

def _resolve_bodytri(nif_path: Path, bodytri_path: str) -> Path | None:
    """Resolve a BODYTRI string (Skyrim-relative under meshes\\) to a
    real path on disk. Walks up from the NIF until it finds a 'meshes'
    ancestor, then joins. Returns None if the file isn't there."""
    norm = bodytri_path.replace("\\", "/").lstrip("/")
    p = nif_path.resolve()
    for parent in [p, *p.parents]:
        if parent.name.lower() == "meshes":
            # BODYTRI strings sometimes include the leading "meshes/" too.
            candidates = [parent / norm]
            if norm.lower().startswith("meshes/"):
                candidates.append(parent.parent / norm)
            for c in candidates:
                if c.is_file():
                    return c
            return None
    # No 'meshes' ancestor — fall back to the NIF's own folder.
    candidate = nif_path.parent / Path(norm).name
    return candidate if candidate.is_file() else None


def _find_bodytri_string(nif) -> str | None:
    """Return the first BODYTRI NiStringExtraData value found on any
    shape. Skyrim itself only reads the first one in the file."""
    for s in nif.shapes:
        try:
            ed_iter = s.extra_data()
        except Exception:
            continue
        for ed in ed_iter:
            if getattr(ed, "name", None) == "BODYTRI":
                return ed.string_data
    return None


def _gather_shapes_and_morphs(nif, tri: TriFile | None) -> list[_PreviewShape]:
    """Per visible armor shape, compute per-vert max ||delta|| across
    every slider in the TRI's matching shape entry. Verts not present
    in any morph offset list get magnitude 0 (no movement)."""
    tri_by_name = {sh.name: sh for sh in (tri.shapes if tri else [])}
    out: list[_PreviewShape] = []
    for s in nif.shapes:
        # Skip pynifly UBE placeholder shapes and the body itself —
        # we're rendering ARMOR pieces.
        if s.name in ("VirtualBody", "VirtualGround", "BaseShape"):
            continue
        verts = np.asarray(s.verts, dtype=np.float32)
        if len(verts) == 0:
            continue
        tri_sh = tri_by_name.get(s.name)
        if tri_sh is None:
            out.append(_PreviewShape(s.name, verts, None))
            continue
        mag = np.zeros(len(verts), dtype=np.float32)
        for morph in tri_sh.morphs:
            if not morph.offsets:
                continue
            offsets_arr = np.asarray(morph.offsets, dtype=np.float32)
            # offsets stored as (idx, dx, dy, dz). idx column is float
            # but always an integral value after parsing.
            idx_arr = offsets_arr[:, 0].astype(np.int32)
            d_arr = offsets_arr[:, 1:4]
            m = np.linalg.norm(d_arr, axis=1)
            mask = (idx_arr >= 0) & (idx_arr < len(verts))
            if mask.any():
                np.maximum.at(mag, idx_arr[mask], m[mask])
        out.append(_PreviewShape(s.name, verts, mag))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_morph_preview(nif_path: str | Path,
                          out_path: str | Path,
                          panel_size: tuple[int, int] = DEFAULT_PANEL_SIZE,
                          vmax: float | None = None) -> dict:
    """Render a 3-panel (front/side/back) BMP of `nif_path`'s armor
    shapes, coloring each vert by max morph-delta magnitude.

    Returns a stats dict suitable for inclusion in a conversion report:

      {
        "shapes_total":       int,    # visible armor shapes in NIF
        "shapes_no_morph":    int,    # rendered gray
        "shapes_with_morph":  int,    # rendered with color ramp
        "max_delta":          float,  # worst displacement, in world units
        "vmax_used":          float,  # value mapped to pure red in image
        "bodytri_string":     str | None,
        "bodytri_resolved":   bool,
        "out_path":           str,
        "warnings":           [str, ...],
      }

    The warnings list distinguishes:
      * "BODYTRI 'X' not resolvable on disk" — converter bug, the NIF
        references a TRI that doesn't exist next to it.
      * "BODYTRI parse error: ..." — TRI file is malformed.
      * "no BODYTRI string" is NOT a warning — many shape types
        (amulets, weapons) are static by design.
    """
    # Lazy import: pynifly is the heaviest import in this project
    # and we don't want preview_module-level imports to drag it in.
    import sys
    HERE = Path(__file__).resolve().parent.parent
    pyn_dir = HERE / ".pynifly"
    if str(pyn_dir) not in sys.path:
        sys.path.insert(0, str(pyn_dir))
    from pyn import pynifly

    nif_path = Path(nif_path)
    out_path = Path(out_path)
    warnings: list[str] = []

    nf = pynifly.NifFile(filepath=str(nif_path))

    bodytri_str = _find_bodytri_string(nf)
    tri: TriFile | None = None
    bodytri_resolved = False
    if bodytri_str:
        tri_disk = _resolve_bodytri(nif_path, bodytri_str)
        if tri_disk is None:
            warnings.append(
                f"BODYTRI {bodytri_str!r} not resolvable on disk")
        else:
            try:
                tri = TriFile.load(tri_disk)
                bodytri_resolved = True
            except Exception as e:
                warnings.append(f"BODYTRI parse error: {e}")

    shapes = _gather_shapes_and_morphs(nf, tri)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Special case: nothing to render — write a blank image and bail.
    if not shapes:
        warnings.append("no visible shapes — nothing to render")
        blank = np.full((panel_size[1] + 28, panel_size[0] * 3, 3),
                         BG_COLOR, dtype=np.uint8)
        write_bmp(out_path, blank)
        return {
            "shapes_total": 0,
            "shapes_no_morph": 0,
            "shapes_with_morph": 0,
            "max_delta": 0.0,
            "vmax_used": 0.0,
            "bodytri_string": bodytri_str,
            "bodytri_resolved": bodytri_resolved,
            "out_path": str(out_path),
            "warnings": warnings,
        }

    all_v = np.vstack([s.verts for s in shapes])
    bounds = _global_uv_bounds(all_v)

    # Pick the color vmax. Default: the actual peak ||delta|| in this
    # NIF, with a 0.5u floor so unmorphed NIFs render flat-blue (not
    # full red from numerical noise). The pure-red point is the
    # *biggest delta we measured* in this NIF — see module docstring.
    if vmax is None:
        max_mag = 0.0
        for s in shapes:
            if s.mag is not None and s.mag.size:
                max_mag = max(max_mag, float(s.mag.max()))
        vmax = max(max_mag, 0.5)

    # Render three panels and concatenate horizontally.
    panels: list[np.ndarray] = []
    for view in VIEWS:
        panel = _render_view(shapes, view, panel_size, bounds, vmax)
        _draw_text(panel, view.upper(), PAD_PX, PAD_PX, TITLE_COLOR)
        panels.append(panel)
    img = np.concatenate(panels, axis=1)

    # Header bar across the top: filename + legend + a status note.
    header_h = 28
    header = np.full((header_h, img.shape[1], 3), HEADER_BG, dtype=np.uint8)
    label = nif_path.name
    if not bodytri_resolved:
        if bodytri_str:
            label += "   (BODYTRI MISSING)"
        else:
            label += "   (no BODYTRI)"
    _draw_text(header, label, 4, 4)
    # Legend strip in the right third of the header.
    legend_w = max(40, img.shape[1] // 4)
    legend_x = img.shape[1] - legend_w - 4
    _draw_legend(header, legend_x, 4, legend_w, 8, vmax)
    img = np.concatenate([header, img], axis=0)

    write_bmp(out_path, img)

    shapes_no_morph = sum(1 for s in shapes if s.mag is None)
    max_delta = 0.0
    for s in shapes:
        if s.mag is not None and s.mag.size:
            max_delta = max(max_delta, float(s.mag.max()))

    return {
        "shapes_total": len(shapes),
        "shapes_no_morph": shapes_no_morph,
        "shapes_with_morph": len(shapes) - shapes_no_morph,
        "max_delta": max_delta,
        "vmax_used": float(vmax),
        "bodytri_string": bodytri_str,
        "bodytri_resolved": bodytri_resolved,
        "out_path": str(out_path),
        "warnings": warnings,
    }


def render_all_previews(output_mod_root: str | Path,
                         previews_root: str | Path | None = None
                         ) -> list[dict]:
    """Render previews for every NIF under `<output_mod_root>/meshes`.

    Default destination is a SIBLING folder next to the mod root,
    named `<mod_name> - Previews`. Sibling layout keeps the BMPs
    outside MO2's deployable tree — writing them under
    `<output_mod_root>/` would push 100+ MB of QA art into the game's
    overwrite folder. Pass `previews_root` to override.
    """
    root = Path(output_mod_root)
    meshes_root = root / "meshes"
    if not meshes_root.is_dir():
        return []
    if previews_root is None:
        previews_root = root.parent / f"{root.name} - Previews"
    else:
        previews_root = Path(previews_root)
    results: list[dict] = []
    for nif_path in sorted(meshes_root.rglob("*.nif")):
        rel = nif_path.relative_to(meshes_root)
        out = previews_root / rel.with_suffix(".bmp")
        try:
            res = render_morph_preview(nif_path, out)
            res["nif"] = str(rel)
            results.append(res)
        except Exception as e:
            results.append({
                "nif": str(rel),
                "error": str(e),
                "out_path": str(out),
            })
    return results
