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

"""`#nipple-bridge` -- a fine garment passes OVER the nipple instead of tracing it.

WHY. A garment hugging the body at a near-constant clearance reproduces the
nipple bump, and the clearance requirement peaks there. The reported top drew
the nipple fully (outline 1.01; the author's 0.79, tucked 0.17u in at the tip).

PINNED HERE:
  * DEFAULT ON (scrubbed subprocess); CBBE2UBE_NO_* turns it off;
  * the bump in a tracing garment is FLATTENED over the core;
  * the apex keeps at least the floor (measured demand + margin) of clearance;
  * nothing outside the bridge radius moves;
  * a COARSE garment (too few verts over the nipple) is left alone;
  * a `skip_mask` vertex never moves.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import nif_convert as nc                  # noqa: E402
from src import nif_convert_fitgeom as fg          # noqa: E402


def _resolve(expr):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from src import nif_convert as nc
        print({expr})
    """)
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


BUMP = 0.44


CURV = 0.0     # base curvature of the breast; the sag test sets it


def _body():
    """Plane y=0 (both sides of x=0 mirrored) with a nipple bump at x=+-6, on a
    convex base of curvature CURV (0 = flat)."""
    g = np.arange(-12.0, 12.01, 0.2)
    X, Z = np.meshgrid(g, g)
    x, z = X.ravel(), Z.ravel()
    r2 = (np.abs(x) - 6.0) ** 2 + z ** 2
    yb = BUMP * np.exp(-r2 / (2 * 0.35 ** 2))
    y = yb - CURV * r2
    bv = np.c_[x, y, z]
    gx = -(np.abs(x) - 6.0) * np.sign(x) / 0.35 ** 2 * yb - 2 * CURV * (np.abs(x) - 6.0) * np.sign(x)
    gz = -z / 0.35 ** 2 * yb - 2 * CURV * z
    bn = np.c_[-gx, np.ones(len(bv)), -gz]
    bn /= np.linalg.norm(bn, axis=1, keepdims=True)
    nip = np.exp(-r2 / (2 * 1.0 ** 2))
    return bv, bn, nip


def _garment(step=0.3, clear=1.0):
    """A garment tracing the body at constant clearance (the defect)."""
    bv, bn, _ = _body()
    sel = np.flatnonzero((np.abs(np.abs(bv[:, 0]) - 6.0) < 6.0) & (np.abs(bv[:, 2]) < 6.0))
    keep = sel[(np.round(bv[sel, 0] / step) * step == np.round(bv[sel, 0], 6))]
    # simple subsample on a coarser grid
    gx = np.round(bv[sel, 0] / step) * step
    gz = np.round(bv[sel, 2] / step) * step
    m = np.isclose(bv[sel, 0], gx, atol=1e-6) & np.isclose(bv[sel, 2], gz, atol=1e-6)
    idx = sel[m] if m.any() else keep
    return bv[idx] + bn[idx] * clear


def _bridge(V, skip=None, demand_stack=None):
    bv, bn, nip = _body()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fg, "_cached_body_morph_stack", lambda *a, **k: demand_stack)
        mp.setattr(fg, "_find_ube_body_osd", lambda *a, **k: None)
        return fg._nipple_bridge(V, bv, bn, nip, skip_mask=skip)


def _apex_gap(V):
    """Garment height straight over the right apex (x=6, z=0) minus the apex."""
    d = np.hypot(V[:, 0] - 6.0, V[:, 2])
    near = d < 0.25
    return float(V[near, 1].mean() - BUMP)


def _bump(V):
    """Garment height over the apex minus its height 1.8u away, right side."""
    d = np.hypot(V[:, 0] - 6.0, V[:, 2])
    return float(V[d < 0.25, 1].mean() - V[(d > 1.7) & (d < 1.9), 1].mean())


def test_default_is_on():
    assert _resolve("nc.NIPPLE_BRIDGE") == "True"


def test_tracing_garment_has_the_defect():
    assert _bump(_garment()) > 0.3


def test_bump_is_flattened():
    V = _garment()
    out = _bridge(V)
    assert out is not None
    assert _bump(out) < 0.5 * _bump(V)


def test_never_raises_a_vertex():
    """LOWER ONLY: a garment already closer than the floor is left where it is
    (raising a core kicked a coarse plate's triangles up and the late coherence
    repair collapsed it into the body)."""
    for clear in (0.05, 1.0):
        V = _garment(clear=clear)
        out = _bridge(V)
        if out is not None:
            assert np.all(out[:, 1] <= V[:, 1] + 1e-9)


def test_apex_keeps_the_floor():
    """No morph data -> demand 0 -> floor = margin: the bridge lowers the bump
    but stops with the apex still at least the margin clear."""
    V = _garment(clear=0.3)
    out = _bridge(V)
    assert out is not None
    assert _apex_gap(out) >= float(nc.NIPPLE_BRIDGE_MARGIN) - 0.02


def test_measured_demand_raises_the_floor():
    """A nipple that grows 0.8u under a slider: the bridge stops lowering at
    demand + margin, well above the bare margin it would stop at otherwise."""
    bv, _, _ = _body()
    d = np.zeros((1, len(bv), 3))
    d[0, np.hypot(np.abs(bv[:, 0]) - 6.0, bv[:, 2]) < 0.5, 1] = 0.8   # nipple grows 0.8u
    out = _bridge(_garment(clear=1.2), demand_stack=d)
    assert out is not None
    assert _apex_gap(out) >= 0.8 + float(nc.NIPPLE_BRIDGE_MARGIN) - 0.05


def test_nothing_outside_the_bridge_moves():
    V = _garment()
    out = _bridge(V)
    far = np.hypot(np.abs(V[:, 0]) - 6.0, V[:, 2]) > float(nc.NIPPLE_BRIDGE_R_IN) + 0.01
    assert np.array_equal(out[far], V[far])


def test_coarse_garment_is_left_alone():
    assert _bridge(_garment(step=3.0)) is None


def test_skip_mask_is_respected():
    V = _garment()
    skip = np.hypot(V[:, 0] - 6.0, V[:, 2]) < 1.0
    out = _bridge(V, skip=skip)
    assert np.array_equal(out[skip], V[skip])


def _grid_garment(step, clear, off=(0.0, 0.0)):
    """A triangulated garment grid tracing the body `clear` above it, so the
    SURFACE (not just the vertices) can be checked."""
    bv, bn, _ = _body()
    tree = __import__("scipy.spatial", fromlist=["cKDTree"]).cKDTree(bv[:, [0, 2]])
    xs = np.arange(0.0, 12.0 + 1e-9, step) + off[0]
    zs = np.arange(-6.0, 6.0 + 1e-9, step) + off[1]
    V, idx = [], {}
    for j, z in enumerate(zs):
        for i, x in enumerate(xs):
            _, k = tree.query([x, z])
            idx[(i, j)] = len(V)
            # a TRANSLATED copy traces the nipple exactly, like the reported
            # garments; an offset along the normals smooths a narrow bump away
            # and would (rightly) not be bridged at all
            V.append(bv[k] + np.array([0.0, clear, 0.0]))
    T = []
    for j in range(len(zs) - 1):
        for i in range(len(xs) - 1):
            a, b = idx[(i, j)], idx[(i + 1, j)]
            c, d = idx[(i + 1, j + 1)], idx[(i, j + 1)]
            T += [[a, b, c], [a, c, d]]
    return np.asarray(V), np.asarray(T, dtype=np.int64)


def _bridge_t(V, T):
    bv, bn, nip = _body()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fg, "_cached_body_morph_stack", lambda *a, **k: None)
        mp.setattr(fg, "_find_ube_body_osd", lambda *a, **k: None)
        return fg._nipple_bridge(V, bv, bn, nip, tris=T)


def test_garment_that_does_not_trace_is_left_alone():
    """Offset along the normals of a narrow nipple, the garment's own bump is
    a fraction of the body's: there is no outline, so no bridge."""
    bv, bn, _ = _body()
    V, T = _grid_garment(0.6, 1.0, off=(0.13, 0.17))
    tree = __import__("scipy.spatial", fromlist=["cKDTree"]).cKDTree(bv)
    _, k = tree.query(V - np.array([0.0, 1.0, 0.0]))
    V2 = bv[k] + bn[k] * 1.0                         # normal offset: smoothed
    assert _bridge_t(V2, T) is None


def test_coarse_triangulated_garment_is_not_bridged():
    """Median core edge above NIPPLE_BRIDGE_MAX_EDGE: left alone, whatever its
    vertex count -- its flat triangles would sag between bridged corners."""
    V, T = _grid_garment(2.0, 1.0, off=(0.3, 0.2))
    assert _bridge_t(V, T) is None


def test_floor_holds_on_the_triangle_surface(monkeypatch):
    """A convex breast under a 0.9u grid (fine enough to be bridged, coarse
    enough that its flat triangles SAG below the smooth target), lowered toward
    its smooth surface: the bridge may not leave the SURFACE over the body core
    any closer than the floor allows -- judged on triangles, not vertices, and
    never worse than it found it."""
    monkeypatch.setattr(sys.modules[__name__], "CURV", 0.15)
    V, T = _grid_garment(0.9, 0.6, off=(0.45, 0.45))
    out = _bridge_t(V, T)
    assert out is not None
    assert np.any(out[:, 1] < V[:, 1] - 1e-3), "nothing was lowered"
    bv, _, _ = _body()
    core = np.flatnonzero(np.hypot(bv[:, 0] - 6.0, bv[:, 2]) < 1.0)
    nA = np.array([0.0, 1.0, 0.0])
    m = float(nc.NIPPLE_BRIDGE_MARGIN)
    msk = np.hypot(V[:, 0] - 6.0, V[:, 2]) < 2.5
    before = fg._bridge_surface_shortfall(V, T, msk, bv[core], nA, m)
    after = fg._bridge_surface_shortfall(out, T, msk, bv[core], nA, m)
    assert after <= max(before, 2e-3)


def test_inner_wall_of_a_thick_garment_is_not_moved():
    """A double-walled garment (outer wall 1.0u off, inner wall 0.4u off, same
    grid): only the OUTER wall is bridged. Fitting one quadric through both
    walls scattered the normals and the late coherence repair then collapsed
    the patch into the body on a reported cuirass."""
    Vo, To = _grid_garment(0.6, 1.0, off=(0.13, 0.17))
    Vi, Ti = _grid_garment(0.6, 0.4, off=(0.13, 0.17))
    V = np.vstack([Vo, Vi])
    T = np.vstack([To, Ti[:, ::-1] + len(Vo)])      # inner wall faces the body
    out = _bridge_t(V, T)
    assert out is not None
    assert np.array_equal(out[len(Vo):], Vi)        # inner wall untouched
    assert not np.array_equal(out[:len(Vo)], Vo)    # outer wall bridged


def test_edge_gate_refuses_a_garment_coarser_than_the_limit(monkeypatch):
    """A tracing garment passes every other gate; with the edge limit set
    below its edge length it must be refused (and bridged at the default)."""
    V, T = _grid_garment(0.6, 1.0, off=(0.13, 0.17))
    assert _bridge_t(V, T) is not None
    monkeypatch.setattr(nc, "NIPPLE_BRIDGE_MAX_EDGE", 0.3)
    assert _bridge_t(V, T) is None


def _disc_garment():
    """A translated copy 0.3u off (it traces the nipple) carrying a raised DISC
    of 0.25u out to 1.5u -- what the clearance push leaves over the nipple map --
    so the floor lift is positive and the shape of that lift decides the look."""
    V, T = _grid_garment(0.3, 0.3, off=(0.15, 0.15))
    r = np.hypot(V[:, 0] - 6.0, V[:, 2])
    t = np.clip((2.0 - r) / 0.5, 0.0, 1.0)
    V = V.copy()
    V[:, 1] += 0.25 * t * t * (3.0 - 2.0 * t)
    return V, T, r


def _disc_band_height(dome_r):
    V, T, r = _disc_garment()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "NIPPLE_BRIDGE_DOME_R", dome_r)
        out = _bridge_t(V, T)
    assert out is not None
    band = (r >= 1.0) & (r < 1.4) & (np.abs(V[:, 0]) > 3.0)
    # height over the flat base (the copy sits 0.3u up; the bump is ~0 here)
    return float(np.median(out[band, 1] - 0.3)), out, T, V


def test_uniform_lift_leaves_the_disc_standing():
    """The defect: one lift over the whole core keeps the 1-1.4u band raised,
    so the disc's edge survives the bridge."""
    h, _, _, _ = _disc_band_height(0.0)
    assert h > 0.15


def test_dome_lift_takes_the_disc_down():
    h, _, _, _ = _disc_band_height(1.0)
    assert h < 0.05


def test_dome_lift_still_holds_the_floor_on_the_surface():
    _, out, T, V = _disc_band_height(1.0)
    bv, _, _ = _body()
    core = np.flatnonzero(np.hypot(bv[:, 0] - 6.0, bv[:, 2]) < 1.0)
    msk = np.hypot(V[:, 0] - 6.0, V[:, 2]) < 2.5
    short = fg._bridge_surface_shortfall(out, T, msk, bv[core],
                                         np.array([0.0, 1.0, 0.0]),
                                         float(nc.NIPPLE_BRIDGE_MARGIN))
    assert short <= 2e-3
