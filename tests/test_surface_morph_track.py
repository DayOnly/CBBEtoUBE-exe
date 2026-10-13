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

"""`#surface-morph-track` -- the garment SURFACE follows the body under a morph.

WHY. Body-motion match makes each hugging VERTEX copy its nearest body vertex,
so on a coarse garment the surface between corners is a linear blend of distant
donors. Under a bust-heavy preset the nipple moves ~5u while the corners move
3.4-5.2u, and it comes through between correct vertices (census 10-12: every
piece that poked had 2.6-8u triangles over the tip).

PINNED HERE:
  * DEFAULT ON, resolved in a scrubbed subprocess; CBBE2UBE_NO_* turns it off;
  * INERT without triangles or a region mask (flag on, output unchanged);
  * it FIRES: the barycentric surface tracks the body points it covers better
    than the donor answer -- the quantity the solve minimises;
  * an OPEN-BOUNDARY vertex moves only along the garment normal (an edge that
    slides lets the tip out from under it; one that lifts covers it);
  * a vertex standing off the body (not hugging) is untouched.
"""
import importlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import src.sliderset_gen as sg                 # noqa: E402
from src.osd import OsdFile, OsdMorph          # noqa: E402


def _resolve(expr, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    env.update(extra_env or {})
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        import src.sliderset_gen as sg
        print({expr})
    """)
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _body():
    """Dense plane y=0; a 1u nipple-sized patch around the origin moves +1 in y and
    +0.5 in x -- a SIDEWAYS part too, so a rim that is not tangent-locked shows it."""
    g = np.arange(-4.0, 4.01, 0.25)
    X, Z = np.meshgrid(g, g)
    bv = np.c_[X.ravel(), np.zeros(X.size), Z.ravel()]
    bump = np.linalg.norm(bv[:, [0, 2]], axis=1) < 1.0
    return bv, bump


def _osd(bump):
    return OsdFile(version=1, morphs=[OsdMorph.from_offsets(
        "BaseShapeBump", [(int(i), 0.5, 1.0, 0.0) for i in np.flatnonzero(bump)])])


# A COARSE plate hugging at y=1: a 3x3 vertex grid of 3u spacing, so no vertex
# sits over the bump and the centre vertex is the only interior one.
_G = np.array([(x, 1.0, z) for z in (-3.0, 1.2, 3.0) for x in (-3.0, 1.4, 3.0)])
_T = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4],
               [3, 4, 7], [3, 7, 6], [4, 5, 8], [4, 8, 7]])


def _run(on, tris=True, mask=True, plate=_G):
    importlib.reload(sg)
    bv, bump = _body()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "_SURFACE_MORPH_TRACK", bool(on))
        tri = sg.generate_armor_tri(
            {"Plate": plate.copy()}, bv, _osd(bump), body_shape_name="BaseShape",
            include_body_shapes=False,
            armor_shape_tris={"Plate": _T} if tris else None,
            body_track_mask=np.ones(len(bv), bool) if mask else None)
    d = np.zeros((len(plate), 3))
    for ts in tri.shapes:
        if ts.name == "Plate":
            for m in ts.morphs:
                if m.name == "Bump":
                    for vi, dx, dy, dz in m.offsets:
                        d[int(vi)] = (dx, dy, dz)
    return d, bv, bump


def _surface_residual(d, bv, bump, plate=_G):
    """Sum of squared (surface delta - body delta) over body points under the plate."""
    tot = 0.0
    for a, b, c in _T:
        A, B, C = plate[a], plate[b], plate[c]
        v0, v1 = B - A, C - A
        d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
        den = d00 * d11 - d01 * d01
        for i, P in enumerate(bv):
            v2 = P + np.array([0, 1.0, 0]) - A          # projected to the plate plane
            wb = (d11 * (v2 @ v0) - d01 * (v2 @ v1)) / den
            wc = (d00 * (v2 @ v1) - d01 * (v2 @ v0)) / den
            if wb >= 0 and wc >= 0 and wb + wc <= 1:
                s = (1 - wb - wc) * d[a] + wb * d[b] + wc * d[c]
                tot += float(((s - (np.array([0.5, 1.0, 0]) if bump[i] else 0.0)) ** 2).sum())
    return tot


def test_default_is_on():
    assert _resolve("sg._SURFACE_MORPH_TRACK") == "True"


def test_switch_reads_the_env():
    assert _resolve("sg._SURFACE_MORPH_TRACK",
                    {"CBBE2UBE_NO_SURFACE_MORPH_TRACK": "1"}) == "False"


def test_inert_without_triangles_or_mask():
    off, _, _ = _run(False)
    for kw in ({"tris": False}, {"mask": False}):
        on, _, _ = _run(True, **kw)
        assert np.array_equal(on, off), kw


def test_donor_answer_misses_the_bump():
    """The defect itself: every corner copies a body vert that does not move."""
    off, _, _ = _run(False)
    assert np.abs(off).max() < 1e-9


def test_surface_tracks_the_body_better():
    off, bv, bump = _run(False)
    on, _, _ = _run(True)
    assert np.abs(on - off).max() > 1e-3, "the solve did not run"
    assert _surface_residual(on, bv, bump) < 0.9 * _surface_residual(off, bv, bump)


def test_centre_vertex_rises_with_the_bump():
    """The interior corner over the bump takes an OUTWARD delta."""
    on, _, _ = _run(True)
    assert on[4, 1] > 0.05


def test_open_boundary_moves_only_along_the_normal():
    """Every rim vertex's correction is (all but) parallel to the plate normal (y)."""
    off, _, _ = _run(False)
    on, _, _ = _run(True)
    corr = on - off
    rim = [0, 1, 2, 3, 5, 6, 7, 8]
    # a stiff PENALTY inside the solve, not an exact projection: the tangential
    # part must be a sliver of the normal part on the rim...
    assert np.abs(corr[rim][:, [0, 2]]).max() < 0.01 * np.abs(corr[rim][:, 1]).max()
    # ...while the interior vertex is NOT locked: the body moves sideways too
    assert abs(corr[4, 0]) > 0.01


def test_standoff_vert_is_untouched():
    """A plate standing 12u off the body is drape, not hugging: no solve."""
    far = _G.copy(); far[:, 1] = 12.0
    off, _, _ = _run(False, plate=far)
    on, _, _ = _run(True, plate=far)
    assert np.array_equal(on, off)
