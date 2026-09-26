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

"""Thin numpy-friendly wrapper over pynifly.

pynifly is not on PyPI; it ships alongside BadDog's Blender plugin. We add
PYNIFLY_PATH to sys.path before importing so users can keep the DLL+module
outside the project tree.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def _ensure_pynifly_on_path() -> None:
    # Default search path: <project_root>/.pynifly/ — where we extracted
    # BadDog's PyNifly Blender plugin (pyn/ package + NiflyDLL.dll).
    project_root = Path(__file__).resolve().parent.parent
    default_path = project_root / ".pynifly"
    p = os.environ.get("PYNIFLY_PATH") or str(default_path)
    if p and p not in sys.path:
        sys.path.insert(0, p)


_ensure_pynifly_on_path()

try:
    from pyn import pynifly  # type: ignore
except ImportError as e:  # pragma: no cover - environment-specific
    pynifly = None  # type: ignore
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


# #nif-library-one-search (2026-09-26): the import above looks ONLY in
# PYNIFLY_PATH when that variable is set, while nif_convert._pynifly() -- which
# the conversion itself uses -- looks in the repo's `.pynifly/`. A source run
# with a PYNIFLY_PATH that no longer holds the library therefore converted
# (nif_convert found it) while every read through this module failed with
# "'NoneType' object has no attribute 'NifFile'": the zeroed-body check called
# the game's bodies "unreadable" and the run fell back to finding bodies by
# name (no CBBE body at all, a UBE body picked by folder name), the Reference
# bodies dialog listed every body as unreadable, and the zeroed garment source
# switched off. `library()` retries ONCE the other search -- the same one
# nif_convert._pynifly() makes -- so both halves read NIFs with the same
# library. Frozen builds bundle `pyn` and never reach the retry.
# CBBE2UBE_NO_NIF_LIBRARY_RETRY=1 keeps the one import above.
from .envflags import flag as _flag  # noqa: E402

NIF_LIBRARY_RETRY = not _flag("CBBE2UBE_NO_NIF_LIBRARY_RETRY", default=False)
_RETRIED = False
_RETRY_LOCK = threading.Lock()


def library():
    """The pynifly module this module reads NIFs with, or None when it cannot
    be imported (`import_error()` then says why). The retry is made once, under
    a lock, and marked done only when it has finished, so a second thread's
    first read waits for it instead of failing."""
    global pynifly, _IMPORT_ERROR, _RETRIED
    if pynifly is not None or _RETRIED or not NIF_LIBRARY_RETRY:
        return pynifly
    with _RETRY_LOCK:
        if pynifly is not None or _RETRIED:
            return pynifly
        if not getattr(sys, "frozen", False):
            pn = str(Path(__file__).resolve().parent.parent / ".pynifly")
            if pn not in sys.path:
                sys.path.insert(0, pn)
        try:
            from pyn import pynifly as mod  # type: ignore
        except ImportError as e:
            _IMPORT_ERROR = e
            _RETRIED = True
            return None
        pynifly = mod
        _IMPORT_ERROR = None
        _RETRIED = True
        return mod


def import_error() -> str:
    """Why `library()` is None, as one line ("" when it is loaded)."""
    e = _IMPORT_ERROR
    return f"{type(e).__name__}: {e}" if e is not None else ""


def _require_pynifly() -> None:
    if library() is None:
        raise RuntimeError(
            "pynifly is not importable. Install it from "
            "https://github.com/BadDogSkyrim/PyNifly and either drop it into "
            "site-packages or set PYNIFLY_PATH to the folder containing "
            "pynifly.py + nifly.dll."
        ) from _IMPORT_ERROR


@dataclass
class Shape:
    """A single NiShape's data as numpy arrays, read-only: nothing here writes
    a NIF (the converter writes through its own re-author path and
    atomic_nif_save). `_backing` keeps the underlying pynifly object.
    """
    name: str
    verts: np.ndarray            # (N, 3) float32
    normals: np.ndarray          # (N, 3) float32 — may be empty
    uvs: np.ndarray              # (N, 2) float32 — may be empty
    tris: np.ndarray             # (T, 3) int32
    bone_names: list[str]
    # bone_weights[bone_name] -> array of (vert_idx, weight) pairs as float64
    # for precision during transfer. Sparse: only nonzero entries are present.
    bone_weights: dict[str, np.ndarray] = field(default_factory=dict)
    _backing: object | None = None  # underlying pynifly NiShape


@dataclass
class Nif:
    path: Path
    shapes: list[Shape]
    _backing: object | None = None  # underlying pynifly NifFile


def open_nif_retry(path_str: str, attempts: int = 5, base_delay: float = 0.08):
    """Open a pynifly NifFile, retrying TRANSIENT failures with backoff.

    Under many parallel workers, opening a large source NIF can transiently fail
    (Windows file-share / handle contention, an AV scan holding the file) even
    though the file is a perfectly valid NIF -- observed as "Could not open ...
    as nif" on 3-4MB meshes at 23 workers, on files that open fine in isolation.
    Dropping the mesh on the first blip is wrong; retry a few times, then re-raise
    the last error so a GENUINELY bad file still fails loudly. Backoff: 0.08, 0.16,
    0.32, 0.64s (total ~1.2s worst case)."""
    import time
    last: "Exception | None" = None
    for i in range(max(1, attempts)):
        try:
            return library().NifFile(filepath=path_str)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 - transient IO; retried below
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last  # type: ignore[misc]


def load_nif(path: str | os.PathLike) -> Nif:
    _require_pynifly()
    path = Path(path)
    nf = open_nif_retry(str(path))
    shapes: list[Shape] = []
    for raw in nf.shapes:
        verts = np.asarray(raw.verts, dtype=np.float32)
        tris = np.asarray(raw.tris, dtype=np.int32)
        normals = np.asarray(getattr(raw, "normals", None) or [], dtype=np.float32)
        uvs = np.asarray(getattr(raw, "uvs", None) or [], dtype=np.float32)

        bone_weights: dict[str, np.ndarray] = {}
        bone_names = list(getattr(raw, "bone_names", []) or [])
        for bn in bone_names:
            pairs = raw.bone_weights.get(bn, []) if hasattr(raw, "bone_weights") else []
            if pairs:
                bone_weights[bn] = np.asarray(pairs, dtype=np.float64)

        shapes.append(Shape(
            name=raw.name,
            verts=verts,
            normals=normals,
            uvs=uvs,
            tris=tris,
            bone_names=bone_names,
            bone_weights=bone_weights,
            _backing=raw,
        ))
    return Nif(path=path, shapes=shapes, _backing=nf)


def release_nif(nf) -> None:
    """Break the reference cycles a loaded pynifly NifFile carries, so
    reference counting frees it -- and its native handle, through
    NifFile.__del__ -- the moment the caller drops it.

    Every block object holds `file` (the NifFile) and `_parent`; the file
    holds `_shapes`, `_nodes`, `_shape_dict`, `node_ids`, and lazily the root
    node and the reference skeleton. Those cycles hand every loaded file to the cyclic
    collector, whose FULL pass is rare in a large heap. MEASURED 2026-09-17,
    loading a `_0`/`_1` pair the way the postflight passes do and dropping
    it: 35.8 MB per pair retained under reference counting, 35.0 after a
    generation-0 collection, 34.9 after generation 1, freed only by a full
    collection (0.44 s in the batch parent, which holds a million objects).
    In that parent the two postflight passes climbed 32 and 23 MB per pair,
    1.44 -> 3.57 GB on a 76-NIF mod, and would have climbed without bound on
    a large pack. With this release: 2.9 MB per pair before the containers
    were cleared too.

    The caller must be done with the file and its shapes: after this call
    `nf.shapes` is empty. The names are the vendored library's own (.pynifly
    is pinned); tests/test_postflight_releases_nif_pairs.py fails if a
    future library keeps a link this does not cut. #postflight-release"""
    d = getattr(nf, "__dict__", None)
    if d is None:
        return
    blocks = list(d.get("_shapes") or [])
    blocks += list((d.get("_nodes") or {}).values())
    blocks += list((d.get("_shape_dict") or {}).values())
    for k, v in list(d.items()):
        vd = getattr(v, "__dict__", None)
        if vd is None or k in ("_shapes", "_nodes", "_shape_dict", "node_ids"):
            continue
        if "file" in vd:                 # a block object held directly (`_root`)
            blocks.append(v)
            d[k] = None
        elif "_shapes" in vd and v is not nf:   # another NifFile (the reference skeleton)
            release_nif(v)
            d[k] = None
    for o in blocks:
        od = getattr(o, "__dict__", None)
        if od is None:
            continue
        for a in ("file", "_parent"):
            if a in od:
                od[a] = None
    for a, empty in (("_shapes", []), ("_nodes", {}), ("_shape_dict", {}), ("node_ids", {})):
        if a in d:
            d[a] = empty
