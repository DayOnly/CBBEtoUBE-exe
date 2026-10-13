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

"""#rear-surface-req: the rear floor must hold the garment SURFACE off the skin,
not just its vertices.

`REAR_STANDOFF` is a per-vertex floor. On a convex buttock a coarse garment face
sags between its corners, so every corner stood its 1.0u while the face itself
touched the skin -- measured on a guard cuirass as a rear gap p1 of 0.07u, and
reported in game as "body shows through at the rear". Over 36 pieces from
source the surface rule took skin pokes in motion 315 -> 152 with creases and
stretch flat.

The scene is a rear-facing half cylinder at butt height under a garment whose
corners stand exactly the vertex floor but whose faces sag well inside it.
"""
import numpy as np
import pytest

from src import nif_convert_fitgeom as fg

R = 8.0


def _scene(step_deg=50.0, n=5, zs=(56.0, 61.0, 66.0, 71.0)):
    """Dense body + coarse garment. Angles are measured around -Y (the rear),
    so theta 0 faces straight back. Returns body verts/normals, garment verts,
    garment tris and the garment grid shape (rows = z, cols = theta)."""
    bt = np.radians(np.linspace(-100.0, 100.0, 81))
    bz = np.linspace(50.0, 76.0, 53)
    T, Z = np.meshgrid(bt, bz)
    nrm = np.stack([np.sin(T), -np.cos(T), np.zeros_like(T)], -1).reshape(-1, 3)
    bv = nrm * R + np.stack([np.zeros_like(Z), np.zeros_like(Z), Z], -1).reshape(-1, 3)
    half = (n - 1) / 2.0
    gt = np.radians((np.arange(n) - half) * step_deg)
    G = []
    for z in zs:
        for t in gt:
            G.append(((R + fg.REAR_STANDOFF) * np.sin(t),
                      -(R + fg.REAR_STANDOFF) * np.cos(t), z))
    G = np.asarray(G, np.float64)
    tris = []
    for r in range(len(zs) - 1):
        for c in range(n - 1):
            a, b = r * n + c, r * n + c + 1
            d, e = (r + 1) * n + c, (r + 1) * n + c + 1
            tris += [(a, b, e), (a, e, d)]
    return bv, nrm, G.astype(np.float32), np.asarray(tris, np.int64), (len(zs), n)


def _surface_gap(G, tris, bv, bn, sel):
    """Distance from each selected body point along its normal to the garment
    surface (ray / triangle). inf where the ray misses."""
    out = np.full(int(sel.sum()), np.inf)
    P, D = bv[sel], bn[sel]
    for a, b, c in tris:
        A, B, C = G[a], G[b], G[c]
        e1, e2 = B - A, C - A
        h = np.cross(D, e2)
        det = h @ e1
        ok = np.abs(det) > 1e-12
        f = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = P - A
        u = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        v = f * np.einsum("ij,ij->i", D, q)
        t = f * (q @ e2)
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
        out = np.where(hit, np.minimum(out, t), out)
    return out


def _run(monkeypatch, on, **kw):
    bv, bn, G, tris, shape = _scene(**kw)
    monkeypatch.setattr(fg, "REAR_SURFACE_REQ", bool(on))
    out = np.asarray(fg.clear_armor_outside_body(G, bv, bn, tris=tris), np.float64)
    return bv, bn, G.astype(np.float64), out, tris, shape


def _interior_body(bv):
    """Body points well inside the rear zone (full z and normal weight) and
    under the garment's interior columns."""
    th = np.degrees(np.arctan2(bv[:, 0], -bv[:, 1]))
    return (np.abs(th) <= 45.0) & (bv[:, 2] >= 60.0) & (bv[:, 2] <= 68.0)


def test_rear_floor_lifts_a_sagging_surface_off_the_skin(monkeypatch):
    bv, bn, _G, off, tris, _ = _run(monkeypatch, on=False)
    _, _, _, on, _, _ = _run(monkeypatch, on=True)
    sel = _interior_body(bv)
    g_off = _surface_gap(off, tris, bv, bn, sel)
    g_on = _surface_gap(on, tris, bv, bn, sel)
    assert np.isfinite(g_off).all() and np.isfinite(g_on).all()
    # CONTROL: the vertex floor alone must leave the faces sagging, or the
    # scene does not contain the defect.
    assert g_off.min() < fg.REAR_SURFACE_CLEAR - 0.15, (
        f"control: the vertex floor already clears the surface ({g_off.min():.3f}u)")
    assert g_on.min() >= fg.REAR_SURFACE_CLEAR - 0.05, (
        f"surface still sags to {g_on.min():.3f}u over the rear")


def test_rear_surface_rule_only_pushes_out(monkeypatch):
    _bv, _bn, _G, off, _, _ = _run(monkeypatch, on=False)
    _, _, _, on, _, _ = _run(monkeypatch, on=True)
    rad = on[:, :2] / np.linalg.norm(on[:, :2], axis=1, keepdims=True)
    d = np.einsum("ij,ij->i", (on - off)[:, :2], rad)
    assert d.min() >= -1e-5
    assert np.linalg.norm(on - off, axis=1).max() <= fg.REAR_SURFACE_MAX_PUSH + 1e-5


def test_defaults_are_the_measured_configuration():
    assert fg.REAR_SURFACE_REQ is True
    assert fg.REAR_SURFACE_CLEAR == pytest.approx(0.5)
    assert fg.REAR_SURFACE_MAX_PUSH == pytest.approx(1.0)
