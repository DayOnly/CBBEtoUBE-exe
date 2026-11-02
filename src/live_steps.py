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

"""The window's Live steps panel: one small picture per step of each piece.
#live-steps

Two halves, one per process:

  * THE WORKER records. When `CBBE2UBE_LIVE_STEPS` names a folder, every
    fitted shape's verts are kept at three points of the per-shape chain the
    stage hook already marks -- entry (the source, in body space), after the
    warp, and after the last pass -- and once the piece and its body zap are
    done ONE file per piece goes into that folder. Copies only, never a write
    back: the output is the same bytes with the panel on or off. Nothing is
    rendered in a worker; a few array copies and one uncompressed save per
    piece is all the panel costs a conversion.
  * THE WINDOW renders. It polls the folder, reads the newest file, reads the
    written NIF for the last two steps (weights and the zapped body), and
    draws front-view thumbnails in numpy on a background thread, never on the
    Tk main loop.

Steps: Source, Warp (coloured by how far the warp moved each vert), Fit
(coloured by how far the passes after the warp moved it), Weights (each vert
coloured by its strongest bone, read from the written file), Zap (the written
body in skin tone with the cloth laid over it, so deleted skin shows as a hole).

A step is flagged (red border, one-line note) when a pass failed on the piece,
when its verts are not finite, or when a step moved some vert more than
`JUMP_U` units -- the warp and the fit passes move cloth by a few units, so ten
is a jump, not a fit.
"""
from __future__ import annotations

import colorsys
import json
import os
import time
import zlib
from pathlib import Path

import numpy as np

ENV = "CBBE2UBE_LIVE_STEPS"
#: Files left unread in the folder before a worker stops adding more (the
#: window deletes what it reads; a CLI run with the variable set has no reader).
MAX_PENDING = 40
#: Movement of one vert in one step that is a jump, not a fit (units).
JUMP_U = 10.0
#: Triangles kept per piece in a record; a denser piece is thinned by stride.
MAX_TRIS = 60000


def live_dir() -> "str | None":
    """The folder the window reads, or None (panel off)."""
    raw = os.environ.get("CBBE2UBE_LIVE_STEPS", "")  # a folder path, not a flag
    return raw.strip() or None


# ---------------------------------------------------------------- worker side

_SHAPES: "dict[str, dict]" = {}


def begin() -> None:
    """Forget the previous piece. Called once per conversion, before it starts."""
    _SHAPES.clear()


class _ShapeRec:
    """The stage-hook recorder for one shape: entry, warp, and the latest."""

    __slots__ = ("slot",)

    def __init__(self, slot):
        self.slot = slot

    def __call__(self, label, verts) -> None:
        if verts is None:
            return
        try:
            v = np.array(verts, dtype=np.float32)
        except Exception:
            return
        if v.ndim != 2 or v.shape[1] != 3 or len(v) != len(self.slot["entry"]):
            return
        if label == "warp":
            self.slot["warp"] = v
        self.slot["fit"] = v


def shape(name, tris, entry) -> "_ShapeRec | None":
    """A recorder for the stage hook, or None when the panel is off."""
    if live_dir() is None or entry is None:
        return None
    try:
        e = np.array(entry, dtype=np.float32)
        t = np.asarray(tris, dtype=np.int32).reshape(-1, 3)
    except Exception:
        return None
    if e.ndim != 2 or e.shape[1] != 3:
        return None
    slot = {"tris": t, "entry": e, "warp": None, "fit": None}
    _SHAPES[str(name)] = slot
    return _ShapeRec(slot)


def _pending(d: Path) -> int:
    try:
        return sum(1 for e in os.scandir(d) if e.name.endswith(".npz"))
    except OSError:
        return 0


def flush(result, *, failures=(), zap=None, zap_pending=False) -> "Path | None":
    """Write this piece's record for the window. Never raises: the panel must
    not be able to fail a conversion."""
    try:
        d = live_dir()
        if d is None:
            return None
        status = str(getattr(result, "status", ""))
        if status.startswith("skipped") and not _SHAPES:
            return None
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        if _pending(d) >= MAX_PENDING:
            return None
        names = list(_SHAPES)
        arrays = {}
        budget = MAX_TRIS
        total = sum(len(s["tris"]) for s in _SHAPES.values()) or 1
        for i, n in enumerate(names):
            s = _SHAPES[n]
            t = s["tris"]
            if total > budget:
                t = t[::int(np.ceil(total / budget))]
            arrays[f"t{i}"] = t
            arrays[f"e{i}"] = s["entry"]
            arrays[f"w{i}"] = s["warp"] if s["warp"] is not None else s["entry"]
            arrays[f"f{i}"] = s["fit"] if s["fit"] is not None else arrays[f"w{i}"]
        dst = getattr(result, "dst_path", None)
        meta = {
            "src": str(getattr(result, "src_path", "") or ""),
            "dst": str(dst) if dst else "",
            "status": status,
            "reason": str(getattr(result, "reason", "") or ""),
            "failures": [str(f) for f in failures],
            "zap": zap if isinstance(zap, dict) else None,
            "zap_pending": bool(zap_pending),
            "shapes": names,
            "time": time.time(),
        }
        arrays["meta"] = np.frombuffer(
            json.dumps(meta, default=str).encode("utf-8"), dtype=np.uint8)
        stamp = f"{time.time_ns():020d}-{os.getpid()}"
        tmp = d / f".{stamp}.tmp"
        with open(tmp, "wb") as fh:
            np.savez(fh, **arrays)
        final = d / f"{stamp}.npz"
        os.replace(tmp, final)
        return final
    except Exception:
        return None
    finally:
        _SHAPES.clear()


# ---------------------------------------------------------------- window side

def read_record(path) -> dict:
    """A record file -> {"meta": dict, "shapes": [(name, tris, entry, warp, fit)]}."""
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(bytes(z["meta"]).decode("utf-8"))
        shapes = []
        for i, n in enumerate(meta.get("shapes", [])):
            shapes.append((n, z[f"t{i}"], z[f"e{i}"], z[f"w{i}"], z[f"f{i}"]))
    return {"meta": meta, "shapes": shapes}


BODY_NAMES = ("BaseShape",)
HIDDEN_NAMES = ("VirtualBody",)


def read_written(dst) -> "dict | None":
    """The written NIF's drawn shapes: {"body": [(V, T)], "cloth": [(V, T, bone_idx, bone_names)]}."""
    from . import nif_io
    if not dst or not Path(dst).is_file():
        return None
    nif = nif_io.load_nif(dst)
    out = {"body": [], "cloth": []}
    try:
        for s in nif.shapes:
            if s.name in HIDDEN_NAMES or not len(s.tris):
                continue
            try:
                if int(s._backing.flags) & 1:       # hidden in game
                    continue
            except Exception:
                pass
            V = np.asarray(s.verts, np.float32)
            T = np.asarray(s.tris, np.int32).reshape(-1, 3)
            if s.name in BODY_NAMES:
                out["body"].append((V, T))
                continue
            best = np.full(len(V), -1, np.int32)
            bw = np.zeros(len(V), np.float32)
            names = list(s.bone_weights)
            for bi, bn in enumerate(names):
                pr = s.bone_weights[bn]
                if not len(pr):
                    continue
                idx = pr[:, 0].astype(np.int64)
                w = pr[:, 1].astype(np.float32)
                ok = (idx >= 0) & (idx < len(V))
                idx, w = idx[ok], w[ok]
                win = w > bw[idx]
                bw[idx[win]] = w[win]
                best[idx[win]] = bi
            out["cloth"].append((V, T, best, names))
    finally:
        try:
            nif_io.release_nif(nif._backing)
        except Exception:
            pass
    return out


# The barycentric samples a triangle is splatted with: corners, edge middles,
# centre. Big triangles (several pixels across) get a denser grid.
def _bary(n):
    pts = [(i / n, j / n) for i in range(n + 1) for j in range(n + 1 - i)]
    a = np.array(pts, np.float32)
    return np.column_stack([a[:, 0], a[:, 1], 1.0 - a.sum(1)])


_B_SMALL = _bary(2)
_B_BIG = _bary(8)


def project(V, frame, w, h):
    """Front view (camera on +Y, looking at the chest): screen x = -X, y = Z."""
    x0, x1, z0, z1 = frame
    s = min(w / max(x1 - x0, 1e-3), h / max(z1 - z0, 1e-3))
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    px = w / 2 - (V[:, 0] - cx) * s
    py = h / 2 - (V[:, 2] - cz) * s
    return px, py, -V[:, 1], s


def raster(meshes, frame, w, h):
    """Splat flat-shaded triangles into a z-buffer.

    `meshes`: [(V, T, rgb)] with rgb an (N,3) per-vert colour 0..1. Returns
    (rgb (h,w,3) float, depth (h,w), mask (h,w))."""
    rgb = np.zeros((h * w, 3), np.float32)
    depth = np.full(h * w, np.inf, np.float32)
    P, C = [], []
    for V, T, col in meshes:
        if not len(T):
            continue
        V = np.asarray(V, np.float32)
        T = np.asarray(T, np.int64)
        T = T[(T >= 0).all(1) & (T < len(V)).all(1)]
        if not len(T):
            continue
        px, py, dz, sc = project(V, frame, w, h)
        a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        n = np.cross(b - a, c - a)
        nn = np.linalg.norm(n, axis=1)
        nn[nn == 0] = 1.0
        shade = 0.5 + 0.5 * np.abs(n[:, 1] / nn)
        fc = (col[T[:, 0]] + col[T[:, 1]] + col[T[:, 2]]) / 3.0 * shade[:, None]
        P2 = np.stack([np.stack([px[T[:, k]], py[T[:, k]], dz[T[:, k]]], 1)
                       for k in range(3)], 1)                    # (t, 3, 3)
        edge = np.max(np.linalg.norm(P2[:, [1, 2, 0], :2] - P2[:, :, :2], axis=2), 1)
        for sel, B in ((edge <= 2.5, _B_SMALL), (edge > 2.5, _B_BIG)):
            if not sel.any():
                continue
            S = np.einsum("kj,tjc->tkc", B, P2[sel]).reshape(-1, 3)
            P.append(S)
            C.append(np.repeat(fc[sel], len(B), axis=0))
    if not P:
        return rgb.reshape(h, w, 3), depth.reshape(h, w), np.zeros((h, w), bool)
    S = np.concatenate(P)
    col = np.concatenate(C)
    ix = np.floor(S[:, 0]).astype(np.int64)
    iy = np.floor(S[:, 1]).astype(np.int64)
    ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h) & np.isfinite(S[:, 2])
    pix = iy[ok] * w + ix[ok]
    dz = S[ok, 2]
    col = col[ok]
    order = np.lexsort((dz, pix))
    pix_s = pix[order]
    first = np.ones(len(pix_s), bool)
    first[1:] = pix_s[1:] != pix_s[:-1]
    win = order[first]
    rgb[pix[win]] = col[win]
    depth[pix[win]] = dz[win]
    rgb, depth = rgb.reshape(h, w, 3), depth.reshape(h, w)
    _fill_pinholes(rgb, depth)
    return rgb, depth, np.isfinite(depth)


def _erode(m, n):
    for _ in range(n):
        m = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
    return m


def _open1(m):
    """Binary opening by one pixel (4-neighbour erode, then dilate)."""
    e = _erode(m, 1)
    return m & (e | np.roll(e, 1, 0) | np.roll(e, -1, 0) | np.roll(e, 1, 1) | np.roll(e, -1, 1))


def _fill_pinholes(rgb, depth) -> None:
    """Close one-pixel gaps the splatting leaves between samples: an empty
    pixel with both horizontal or both vertical neighbours drawn takes their
    average. In place."""
    m = np.isfinite(depth)
    for ax in (1, 0):
        a = np.roll(m, 1, ax) & np.roll(m, -1, ax) & ~m
        if ax == 1:
            a[:, 0] = a[:, -1] = False
        else:
            a[0, :] = a[-1, :] = False
        if not a.any():
            continue
        rgb[a] = (np.roll(rgb, 1, ax)[a] + np.roll(rgb, -1, ax)[a]) / 2
        depth[a] = np.maximum(np.roll(depth, 1, ax)[a], np.roll(depth, -1, ax)[a])
        m = np.isfinite(depth)


def ramp(d, top=4.0):
    """Movement in units -> colour: still = steel blue, `top` and over = red."""
    t = np.clip(np.asarray(d, np.float32) / top, 0.0, 1.0)
    stops = np.array([[0.40, 0.52, 0.72], [0.30, 0.75, 0.55],
                      [0.95, 0.85, 0.30], [0.90, 0.25, 0.20]], np.float32)
    x = t * (len(stops) - 1)
    i = np.minimum(x.astype(np.int64), len(stops) - 2)
    f = (x - i)[:, None]
    return stops[i] * (1 - f) + stops[i + 1] * f


def bone_colour(name: str):
    """A stable colour per bone name (golden-ratio hue from a CRC)."""
    h = (zlib.crc32(name.encode("utf-8", "replace")) * 0.618033988749895) % 1.0
    return colorsys.hsv_to_rgb(h, 0.55, 0.92)


SOURCE_RGB = (0.62, 0.66, 0.72)
SKIN_RGB = (0.86, 0.72, 0.60)
BODY_RGB = (0.50, 0.50, 0.50)
UNWEIGHTED_RGB = (1.0, 0.0, 1.0)
CLOTH_RGB = (0.55, 0.60, 0.70)
HOLE_RGB = (0.16, 0.31, 0.90)
#: Hole pixels in view (no cloth in front) before the Zap step is flagged.
HOLE_PX = 12


def _front_faces(V, T):
    """The triangles facing a camera on +Y, with the winding read off the mesh
    itself (outward normals point away from the centre on average)."""
    V = np.asarray(V, np.float32)
    T = np.asarray(T, np.int64)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    c = V[T].mean(1)
    sign = 1.0 if ((c - V.mean(0)) * n).sum(1).mean() >= 0 else -1.0
    return T[n[:, 1] * sign > 0]


def _flat(n, rgb):
    return np.tile(np.asarray(rgb, np.float32), (n, 1))


def _frame(point_sets, pad=0.06):
    pts = [p for p in point_sets if p is not None and len(p)]
    if not pts:
        return None
    P = np.concatenate([np.asarray(p, np.float32) for p in pts])
    P = P[np.isfinite(P).all(1)]
    if not len(P):
        return None
    x0, z0 = np.percentile(P[:, 0], 0.5), np.percentile(P[:, 2], 0.5)
    x1, z1 = np.percentile(P[:, 0], 99.5), np.percentile(P[:, 2], 99.5)
    px, pz = max(x1 - x0, 4.0) * pad, max(z1 - z0, 4.0) * pad
    return (x0 - px, x1 + px, z0 - pz, z1 + pz)


def render_piece(record, written, w=112, h=150) -> "list[dict]":
    """One thumbnail per step: [{"step", "rgb", "mask", "note", "bad"}].

    `rgb`/`mask` are composed onto the theme's background when shown, so a
    theme change needs no re-render."""
    meta = record["meta"]
    shapes = record["shapes"]
    cloth = (written or {}).get("cloth", [])
    body = (written or {}).get("body", [])
    frame = _frame([s[2] for s in shapes] + [s[4] for s in shapes]
                   + [c[0] for c in cloth])
    failed = list(meta.get("failures") or [])
    status = str(meta.get("status", ""))
    out = []

    def cell(step, meshes, note, bad=False, layers=None):
        if frame is None or (not meshes and not layers):
            out.append({"step": step, "rgb": None, "mask": None,
                        "note": note or "nothing to draw", "bad": bad})
            return
        if layers is not None:
            rgb, mask = layers
        else:
            rgb, _d, mask = raster(meshes, frame, w, h)
        out.append({"step": step, "rgb": rgb, "mask": mask, "note": note, "bad": bad})

    def moved(a, b):
        d = np.linalg.norm(np.asarray(b, np.float32) - np.asarray(a, np.float32), axis=1)
        return d

    # Source
    bad = any(not np.isfinite(s[2]).all() for s in shapes)
    cell("Source", [(s[2], s[1], _flat(len(s[2]), SOURCE_RGB)) for s in shapes],
         f"{len(shapes)} fitted shape(s)" if shapes else "no fitted shapes", bad)
    # Warp and Fit, coloured by what that step moved
    for step, a_i, b_i in (("Warp", 2, 3), ("Fit", 3, 4)):
        meshes, mx, finite = [], 0.0, True
        for s in shapes:
            d = moved(s[a_i], s[b_i])
            finite &= bool(np.isfinite(s[b_i]).all())
            d = np.nan_to_num(d, nan=99.0, posinf=99.0)
            if len(d):
                mx = max(mx, float(d.max()))
            meshes.append((s[b_i], s[1], ramp(d)))
        jump = mx > JUMP_U
        note = (f"moved up to {mx:.1f}u" + (" - JUMP" if jump else "")) if shapes else ""
        cell(step, meshes, note if finite else "verts not finite", jump or not finite)
    # Weights, from the written file
    if written is None:
        cell("Weights", [], "not written" if not status.startswith("converted")
             else "written file not readable", status.startswith("error"))
    else:
        meshes, unweighted = [], 0
        for V, T in body:
            meshes.append((V, T, _flat(len(V), BODY_RGB)))
        for V, T, best, names in cloth:
            pal = np.array([bone_colour(n) for n in names] or [(0, 0, 0)], np.float32)
            col = np.where((best >= 0)[:, None], pal[np.maximum(best, 0)],
                           np.asarray(UNWEIGHTED_RGB, np.float32))
            unweighted += int((best < 0).sum())
            meshes.append((V, T, col))
        cell("Weights", meshes,
             f"{unweighted} unweighted vert(s)" if unweighted else "colour = strongest bone",
             unweighted > 0)
    # Zap: the written body in skin tone, the cloth laid over it at 45%
    zap = meta.get("zap")
    if meta.get("zap_pending"):
        znote = "zap runs after the other weight"
    elif zap:
        znote = f"{zap.get('deleted_tris', 0)} body tris removed"
    elif written is not None and not body:
        znote = "no body in this piece"
    else:
        znote = "zap off or nothing hidden"
    if written is None:
        cell("Zap", [], znote)
    else:
        # The body as the game draws it, front faces only, so skin the zap
        # deleted shows as a hole instead of the body's far side. Under cloth a
        # hole is the zap working (cloth tone); with no cloth in front it is a
        # hole a camera sees (blue, as the zap judge draws it).
        skin = [(V, T, _flat(len(V), SKIN_RGB)) for V, T in body]
        rf, _df, mf = raster([(V, _front_faces(V, T), c) for V, T, c in skin], frame, w, h)
        _ra, _da, ma = raster(skin, frame, w, h)
        rc, _dc, mc = raster([(V, T, _flat(len(V), CLOTH_RGB)) for V, T, _b, _n in cloth],
                            frame, w, h)
        # Kept skin is skin; skin the zap removed from under cloth is the cloth's
        # tone; a hole with no cloth in front is blue. Openings the body has
        # anyway (wrists, neck, ankles) sit on the body's outline, so a hole is
        # taken only two pixels inside it, opened by one pixel against splat
        # noise.
        hole = _open1(_erode(ma, 2) & ~mf)
        rgb = rf.copy()
        rgb[mc & ~mf] = rc[mc & ~mf] * 0.8
        seen = hole & ~mc
        rgb[seen] = HOLE_RGB
        n_seen = int(seen.sum())
        if n_seen and body:
            znote += f", {n_seen} px hole in view"
        cell("Zap", None, znote, bad=n_seen > HOLE_PX, layers=(rgb, mf | mc | seen))
    if failed or status.startswith("error"):
        out[-1]["bad"] = True
        out[-1]["note"] = (failed[0] if failed else meta.get("reason", "error"))[:80]
    return out


def compose(thumb, bg_hex: str):
    """A thumbnail on the theme's background -> binary PPM bytes for Tk."""
    rgb, mask = thumb["rgb"], thumb["mask"]
    bg = np.array([int(bg_hex[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255.0
    if rgb is None:
        return None
    h, w = mask.shape
    img = np.where(mask[..., None], rgb, bg[None, None, :])
    data = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    return b"P6 %d %d 255\n" % (w, h) + data.tobytes()


def piece_title(meta) -> str:
    p = Path(meta.get("dst") or meta.get("src") or "?")
    return f"{p.parent.name}/{p.name}" if p.parent.name else p.name
