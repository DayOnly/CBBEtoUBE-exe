"""End-to-end refit orchestration.

Public entry points:
  refit_nif(armor_path, out_path, refs)         - one NIF
  refit_pair(weight_0_path, weight_1_path, ...) - _0/_1 pair using both refs
  iter_armor_pairs(root)                        - batch walker over a folder
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from . import nif_io
from .correspondence import MeshIndex, compute_deformation
from .weights import transfer_weights


@dataclass
class References:
    """Pre-built mesh indices + raw weight data for CBBE and UBE bodies, at a
    single weight slider value (0 or 1). One References instance is required
    per weight variant being processed.
    """
    cbbe: MeshIndex
    ube: MeshIndex
    ube_bone_weights: dict[str, np.ndarray]   # raw sparse weights of UBE body
    ube_verts: np.ndarray                     # plain (V, 3) UBE body verts

    @classmethod
    def load(cls, cbbe_path: Path, ube_path: Path) -> "References":
        cbbe_nif = nif_io.load_nif(cbbe_path)
        ube_nif = nif_io.load_nif(ube_path)
        cbbe_body = _largest_shape(cbbe_nif)
        ube_body = _largest_shape(ube_nif)
        return cls(
            cbbe=MeshIndex.build(cbbe_body.verts, cbbe_body.tris),
            ube=MeshIndex.build(ube_body.verts, ube_body.tris),
            ube_bone_weights=ube_body.bone_weights,
            ube_verts=ube_body.verts.astype(np.float64),
        )


def _largest_shape(nif: nif_io.Nif) -> nif_io.Shape:
    """Body NIFs may contain hands/feet shapes alongside the body — pick the
    one with the most verts as the body proper."""
    return max(nif.shapes, key=lambda s: len(s.verts))


def _looks_skinned(shape: nif_io.Shape) -> bool:
    return bool(shape.bone_weights) and shape.verts.size > 0


def refit_nif(armor_path: Path, out_path: Path, refs: References) -> None:
    """Refit a single armor NIF against the given reference pair.

    Shapes without bone weights (e.g. world models / dropped items) are passed
    through unchanged: they don't ride the body so they shouldn't be reshaped.
    """
    armor = nif_io.load_nif(armor_path)
    for shape in armor.shapes:
        if not _looks_skinned(shape):
            continue
        displacement = compute_deformation(shape.verts, refs.cbbe, refs.ube)
        new_verts = (shape.verts.astype(np.float64) + displacement).astype(np.float32)
        shape.verts = new_verts

        new_weights = transfer_weights(
            deformed_verts=new_verts,
            tgt_ref_verts=refs.ube_verts,
            tgt_ref_bone_weights=refs.ube_bone_weights,
        )
        shape.bone_weights = new_weights

        if shape.normals.size:
            shape.normals = _recompute_normals(new_verts, shape.tris)

    nif_io.save_nif(armor, out_path)


def _recompute_normals(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    verts64 = verts.astype(np.float64)
    a = verts64[tris[:, 0]]
    b = verts64[tris[:, 1]]
    c = verts64[tris[:, 2]]
    face_n = np.cross(b - a, c - a)
    vert_n = np.zeros_like(verts64)
    for k in range(3):
        np.add.at(vert_n, tris[:, k], face_n)
    norms = np.linalg.norm(vert_n, axis=1, keepdims=True)
    vert_n = vert_n / np.where(norms > 0, norms, 1.0)
    return vert_n.astype(np.float32)


def refit_pair(
    weight0: Path,
    weight1: Path,
    out_dir: Path,
    refs_w0: References,
    refs_w1: References,
) -> None:
    refit_nif(weight0, out_dir / weight0.name, refs_w0)
    refit_nif(weight1, out_dir / weight1.name, refs_w1)


def iter_armor_pairs(root: Path) -> Iterable[tuple[Path, Path | None]]:
    """Yield (_0.nif, _1.nif) pairs under root. Unpaired NIFs come back with
    None on the second slot.
    """
    by_stem: dict[str, dict[str, Path]] = {}
    for p in root.rglob("*.nif"):
        name = p.stem
        if name.endswith("_0"):
            base = name[:-2]
            by_stem.setdefault(base, {})["0"] = p
        elif name.endswith("_1"):
            base = name[:-2]
            by_stem.setdefault(base, {})["1"] = p
        else:
            by_stem.setdefault(name, {})["solo"] = p

    for base, slots in by_stem.items():
        if "0" in slots or "1" in slots:
            yield slots.get("0"), slots.get("1")
        else:
            yield slots["solo"], None
