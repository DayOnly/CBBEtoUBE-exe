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

"""Guard for the overlay-band lift (c991d5b) — the per-shape warp can SCRAMBLE a
multi-layer garment's stacking order: a thin band that sits OUTSIDE another cloth
in the SOURCE sinks below it after the warp, so the under-layer pokes through the
band ("the belt clips the corset"). The pass classifies order from the SOURCE
(CBBE) clearance and lifts a sunk band back outside its under-layer, so it is
immune to the warp's scrambling.

REGRESSION this also guards (2026-06-10): the new algorithm REQUIRES
`cbbe_body_verts` and reads each job's `src` shape; the phase-2 call site was
omitting `cbbe_body_verts`, silently no-opping the lift for every body-swap
outfit (exactly the multi-layer mashups it exists for)."""
import numpy as np
from src import nif_convert as nc


class _Src:
    """Stand-in for a pynifly source shape — only `.verts` is read."""
    def __init__(self, verts):
        self.verts = verts


def _patch(y_src, y_warp, x_range, z_range, n):
    """A cloth patch: SOURCE verts at clearance `y_src`, WARP verts at `y_warp`
    (body plane at Y=0, +Y outward). Same topology/count in both frames."""
    xs = np.linspace(*x_range, n)
    zs = np.linspace(*z_range, n)
    src = np.array([[x, y_src, z] for x in xs for z in zs], dtype=np.float64)
    warp = np.array([[x, y_warp, z] for x in xs for z in zs], dtype=np.float64)
    return src, warp


def _job(src_v, warp_v):
    return {"override_skin": {"weights": {"NPC Spine": []}},
            "src": _Src(src_v), "verts": warp_v, "verts_modified": False}


def _body_plane(n=24):
    xs = np.linspace(-10, 10, n)
    zs = np.linspace(70, 96, n)
    bv = np.array([[x, 0.0, z] for x in xs for z in zs], dtype=np.float64)
    bn = np.tile(np.array([0.0, 1.0, 0.0]), (len(bv), 1))
    return bv, bn


def test_sunk_overlay_band_lifted_above_under_layer():
    bv, bn = _body_plane()
    # Under-layer B: LARGE, sits at clearance 1.0 in both source and warp.
    b_src, b_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)   # 400 verts
    # Overlay band A: SMALL; in the SOURCE it sits OUTSIDE B (1.5), but the warp
    # SANK it to B's depth (1.0) -> the pass must lift it back outside B.
    a_src, a_warp = _patch(1.5, 1.0, (-4, 4), (78, 90), 10)   # 100 verts
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)

    pushed = nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn)

    assert pushed > 0, "the sunk overlay band must be lifted"
    a_y = float(np.median(np.asarray(A["verts"])[:, 1]))
    b_y = float(np.median(np.asarray(B["verts"])[:, 1]))
    assert B["verts_modified"] is False, "large under-layer must NOT move"
    assert a_y > b_y + 0.1, f"band must end up outside under-layer: A={a_y} B={b_y}"


def test_noop_without_cbbe_body():
    # No body reference at all -> safe no-op (first-line guard).
    bv, bn = _body_plane()
    a_src, a_warp = _patch(1.5, 1.0, (-4, 4), (78, 90), 10)
    b_src, b_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)
    assert nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=None) == 0
    assert A["verts_modified"] is False


def test_noop_without_source_normals():
    # The ordered lift needs SIGNED source-body normals to rank layers; the
    # unsigned fallback can invert big shapes, so with no source normals the
    # aggressive multi-layer lift must NOT run (gated to reliable ordering).
    bv, bn = _body_plane()
    a_src, a_warp = _patch(1.5, 1.0, (-4, 4), (78, 90), 10)
    b_src, b_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)
    assert nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv) == 0
    assert A["verts_modified"] is False


def test_noop_single_layer():
    bv, bn = _body_plane()
    a_src, a_warp = _patch(1.0, 1.0, (-4, 4), (80, 90), 10)
    A = _job(a_src, a_warp)
    assert nc._separate_abdomen_layered_cloth_depth(
        [A], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn) == 0
    assert A["verts_modified"] is False
