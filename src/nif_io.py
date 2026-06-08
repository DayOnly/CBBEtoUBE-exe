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


def _require_pynifly() -> None:
    if pynifly is None:
        raise RuntimeError(
            "pynifly is not importable. Install it from "
            "https://github.com/BadDogSkyrim/PyNifly and either drop it into "
            "site-packages or set PYNIFLY_PATH to the folder containing "
            "pynifly.py + nifly.dll."
        ) from _IMPORT_ERROR


@dataclass
class Shape:
    """A single NiShape's data as numpy arrays. Backed by the underlying
    pynifly object so writes round-trip through save_nif().
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


def load_nif(path: str | os.PathLike) -> Nif:
    _require_pynifly()
    path = Path(path)
    nf = pynifly.NifFile(filepath=str(path))  # type: ignore[attr-defined]
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


def save_nif(nif: Nif, out_path: str | os.PathLike) -> None:
    """Push numpy state back into the backing pynifly objects and write."""
    _require_pynifly()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if nif._backing is None:
        raise RuntimeError("Nif has no backing pynifly object; was it constructed via load_nif()?")

    for s in nif.shapes:
        raw = s._backing
        if raw is None:
            continue
        raw.set_verts([tuple(v) for v in s.verts.tolist()])
        if s.normals.size:
            raw.set_normals([tuple(n) for n in s.normals.tolist()])
        # Bone weight write-back: clear+set per bone.
        if hasattr(raw, "set_bone_weights"):
            for bn, pairs in s.bone_weights.items():
                raw.set_bone_weights(bn, [(int(i), float(w)) for i, w in pairs.tolist()])

    nif._backing.save(str(out_path))
