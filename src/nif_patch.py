"""Binary in-place patcher for Skyrim SE NIF files.

Why this exists:
  pynifly can READ shape verts but exposes no API to write them back to an
  existing shape — its only writeback path is `createNifShapeFromData`, which
  builds a new shape from scratch and would force us to re-derive every other
  shape property (skin, partitions, materials, alpha, shader, etc.). That's a
  lot of code, with many places to silently break things.

  Instead this module patches the binary file directly. It does NOT need to
  parse NIF headers, block tables, or vertex_desc bitfields. It uses a simpler
  trick: pynifly already told us each shape's exact vertex positions when we
  loaded the file, so we search the binary for those float32 byte patterns to
  locate each shape's vertex data in the file. Found → patch positions in
  place. Everything else (skin, shader, partitions, animations, physics
  metadata) stays byte-identical.

The only thing we DON'T update is the bounding sphere on each shape. Skyrim
re-derives shape bounds on the fly for many operations and the inline bound
is mainly used for culling — it has plenty of slack for sub-2-unit
displacements. If a refit shape disappears at certain camera angles, that's
the smoking gun and we'd add bounding-sphere patching here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable

import numpy as np


# Skyrim SE BSTriShape vertex strides we expect to see. Each shape's per-vertex
# block is one of these depending on which attributes the shape carries.
# 16 = position only (rare)
# 32 = position + uv + normal/tangent packed (static armor pieces)
# 40 = + vertex color
# 48 = + bone indices/weights (skinned)
# 56 = + extra (eye data, etc.)
_KNOWN_STRIDES = (16, 32, 40, 48, 56)


@dataclass
class VertexBlockLocation:
    """Where a shape's vertex positions live in the file binary."""
    shape_name: str
    file_offset: int           # byte offset of first vertex's position in file
    stride: int                # bytes between successive vertex positions
    vertex_count: int


def locate_shape_in_file(
    file_bytes: bytes | bytearray | memoryview,
    expected_verts: list[tuple[float, float, float]],
    *,
    shape_name: str = "<unknown>",
) -> VertexBlockLocation | None:
    """Find where a shape's vertex positions live in the NIF binary.

    The trick: we already know what the positions are (pynifly gave them to
    us). We search the file for the byte pattern of the first vertex's xyz,
    then validate it's the start of the block by checking that the SECOND
    vertex appears at a plausible Skyrim SE stride (16/32/40/48/56 bytes).

    Returns None if the shape can't be located (degenerate verts, partial
    overlap, etc).
    """
    if len(expected_verts) < 2:
        return None

    sig1 = struct.pack("<fff", *expected_verts[0])
    sig2 = struct.pack("<fff", *expected_verts[1])
    sig3 = struct.pack("<fff", *expected_verts[2]) if len(expected_verts) >= 3 else None

    haystack = bytes(file_bytes) if not isinstance(file_bytes, bytes) else file_bytes
    pos = 0
    while True:
        i = haystack.find(sig1, pos)
        if i < 0:
            return None

        for stride in _KNOWN_STRIDES:
            j = i + stride
            if j + 12 > len(haystack):
                continue
            if haystack[j:j + 12] != sig2:
                continue
            if sig3 is not None:
                k = i + 2 * stride
                if k + 12 > len(haystack) or haystack[k:k + 12] != sig3:
                    continue
            return VertexBlockLocation(
                shape_name=shape_name,
                file_offset=i,
                stride=stride,
                vertex_count=len(expected_verts),
            )

        pos = i + 1


def patch_verts(
    file_bytes: bytes | bytearray,
    location: VertexBlockLocation,
    new_verts: np.ndarray | list[tuple[float, float, float]],
) -> bytearray:
    """Patch positions in place. Returns a mutable bytearray copy."""
    if isinstance(file_bytes, bytes):
        out = bytearray(file_bytes)
    else:
        out = bytearray(file_bytes)  # defensive copy

    verts = np.asarray(new_verts, dtype=np.float32)
    if len(verts) != location.vertex_count:
        raise ValueError(
            f"shape '{location.shape_name}': new_verts has {len(verts)} entries, "
            f"expected {location.vertex_count}"
        )

    base = location.file_offset
    stride = location.stride
    for i, (x, y, z) in enumerate(verts):
        off = base + i * stride
        out[off:off + 12] = struct.pack("<fff", float(x), float(y), float(z))
    return out


def patch_nif_shapes(
    src_path,
    dst_path,
    shapes_to_patch: Iterable[tuple[str, np.ndarray]],
    *,
    locator_loader,
) -> dict[str, VertexBlockLocation | None]:
    """Open a source NIF, patch the named shapes with new verts, write to dst.

    shapes_to_patch: iterable of (shape_name, new_verts_array).
    locator_loader: callable(src_path) -> dict[shape_name -> list-of-(x,y,z)]
        of CURRENT (pre-patch) verts. Used to find each shape's offset in the
        file. Inject the loader to keep this module's dependencies minimal.

    Returns: dict of shape_name -> VertexBlockLocation (None if not located).
    """
    from pathlib import Path
    src = Path(src_path)
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    current_verts = locator_loader(src)
    with open(src, "rb") as f:
        data = bytearray(f.read())

    located: dict[str, VertexBlockLocation | None] = {}
    for shape_name, new_verts in shapes_to_patch:
        old = current_verts.get(shape_name)
        if old is None or len(old) == 0:
            located[shape_name] = None
            continue
        loc = locate_shape_in_file(data, old, shape_name=shape_name)
        located[shape_name] = loc
        if loc is None:
            continue
        data = patch_verts(data, loc, new_verts)

    with open(dst, "wb") as f:
        f.write(data)
    return located
