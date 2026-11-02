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

"""Guards for `#push-tri` (min-push lifts the covering triangle) and `#coherence-hold-surface`.

A zapped breastplate whose source sat inside the bust kept its nipple skin (the zap saw it) and it showed through:
min-push lifted the 3 nearest cloth verts into thin tents that passed its own along-the-normal test while the surface
between them stayed under the skin, and the coherence repair, which runs last, smoothed the lift back flat.
"""
import numpy as np

from src import fit_metrics as fm
from src import nif_convert as nc
from src import nif_convert_writer as ncw


def _grid(n, half, y, z_center=95.0):
    xs = np.linspace(-half, half, n)
    zs = np.linspace(z_center - half, z_center + half, n)
    X, Z = np.meshgrid(xs, zs)
    V = np.stack([X.ravel(), np.full(X.size, y), Z.ravel()], 1).astype(float)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c
            tris += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    return V, np.asarray(tris, np.int64), np.tile([0.0, 1.0, 0.0], (len(V), 1))


def _bulge(V, amp=0.8, r=3.0):
    """skin with a bump at the centre (a nipple under a coarse plate)"""
    d = np.hypot(V[:, 0], V[:, 2] - 95.0)
    V = V.copy(); V[:, 1] += amp * np.clip(1.0 - d / r, 0.0, None)
    return V


def _exposed(gV, gT, bV, bN):
    """skin with cloth below it and none above it (the strict poke test)"""
    idx = np.flatnonzero((np.abs(bV[:, 0]) < 4.0) & (np.abs(bV[:, 2] - 95.0) < 4.0))
    up = fm._ClipTester(gV, gT, tmax=6.0).cast(bV[idx] + 1e-3 * bN[idx], bN[idx])
    dn = fm._ClipTester(gV, gT, tmax=3.0).cast(bV[idx] - 1e-3 * bN[idx], -bN[idx])
    return int((~np.isfinite(up) & np.isfinite(dn)).sum())


def _scene():
    bV, bT, bN = _grid(41, 6.0, 0.0)
    bV = _bulge(bV)
    gV, gT, gN = _grid(5, 10.0, 0.3)          # a COARSE plate 0.3u over the flat skin, 5u triangles
    return bV, bT, bN, gV, gT, gN


def test_the_bump_pokes_through_the_coarse_plate():
    bV, bT, bN, gV, gT, gN = _scene()
    assert _exposed(gV, gT, bV, bN) > 0


def test_triangle_lift_clears_the_surface(monkeypatch):
    monkeypatch.setattr(fm, "PUSH_TRI", True)
    bV, bT, bN, gV, gT, gN = _scene()
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    assert st["moved"] > 0 and _exposed(out, gT, bV, bN) == 0, st


def test_clean_geometry_still_moves_nothing(monkeypatch):
    monkeypatch.setattr(fm, "PUSH_TRI", True)
    bV, bT, bN = _grid(41, 6.0, 0.0)
    gV, gT, gN = _grid(5, 10.0, 1.0)
    out, st = fm.minimum_push(gV, gT, gN, bV, bT, bN)
    assert st["moved"] == 0 and np.array_equal(out, gV)


def test_the_hold_undoes_a_repair_that_re_exposes_skin():
    bV, bT, bN = _grid(41, 6.0, 0.0); bV = _bulge(bV)
    gV, gT, _ = _grid(5, 10.0, 0.3)
    before = gV.copy(); before[:, 1] = 1.2                        # the plate after min-push: clear of the bump
    after = before.copy(); after[12, 1] = 0.3                      # the repair sags the centre corner back
    assert _exposed(after, gT, bV, bN) > 0
    held = ncw._hold_repair_surface(before, after, gT, bV, bN)
    assert _exposed(held, gT, bV, bN) == 0
    untouched = np.ones(len(gV), bool); untouched[12] = False
    assert np.array_equal(held[untouched], after[untouched]), "only verts the repair moved may be restored"


def test_both_are_on_by_default():
    assert fm.PUSH_TRI and nc.COHERENCE_HOLD_SURFACE and fm.PUSH_RIM_INTERIOR and fm.PUSH_RIM_MARGIN == 2.0
