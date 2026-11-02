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

"""`#panel-rigid-tip-guard` -- re-rigidifying a plate may not settle it onto the nipple.

WHY. `_rigidify_within_clearance` bisects a per-panel strength under a floor
tested at garment vertices and centroid/edge-midpoint samples, each against the
nearest body vertex's tangent plane. Over a coarse plate none of those samples
sits over a 0.44u nipple rising between them. Stage dump of a reported cuirass:
anti-poke left the nipple core +0.21u clear, this pass shipped it at -0.044u.

PINNED HERE:
  * DEFAULT ON (scrubbed subprocess); CBBE2UBE_NO_* turns it off;
  * the DEFECT reproduces with the guard off -- the plate flattens onto the tip;
  * ON, the tip ends no deeper than min(its clearance before the pass, 0);
  * ON with no nipple map it is byte-identical to OFF (copy path, body-less);
  * a plate whose rigid target clears the tip is not held back at all.
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


def _resolve(expr, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    env.update(extra_env or {})
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


def _body(height=0.8):
    """Dense plane y=0 with a nipple bump at the origin; nipple weight peaks there."""
    g = np.arange(-6.0, 6.01, 0.2)
    X, Z = np.meshgrid(g, g)
    r2 = X.ravel() ** 2 + Z.ravel() ** 2
    bv = np.c_[X.ravel(), height * np.exp(-r2 / (2 * 0.4 ** 2)), Z.ravel()]
    # analytic normals of y = f(x, z)
    fx = -X.ravel() / 0.4 ** 2 * bv[:, 1]
    fz = -Z.ravel() / 0.4 ** 2 * bv[:, 1]
    bn = np.c_[-fx, np.ones(len(bv)), -fz]
    bn /= np.linalg.norm(bn, axis=1, keepdims=True)
    nip = np.exp(-r2 / (2 * 0.5 ** 2))
    return bv, bn, nip


def _plate(lift):
    """A coarse 5x5 plate (2u spacing), offset so no vertex, centroid or edge
    midpoint sits over the origin. `src` is flat at y=0.5; `cur` is src with the
    region over the bump lifted by `lift` (what the anti-poke leaves)."""
    g = np.arange(-4.0, 4.01, 2.0)
    src = np.array([(x + 0.7, 0.5, z + 0.45) for z in g for x in g])
    t = []
    for j in range(4):
        for i in range(4):
            a = j * 5 + i
            t += [[a, a + 1, a + 6], [a, a + 6, a + 5]]
    t = np.asarray(t, dtype=np.int64)
    cur = src.copy()
    r = np.linalg.norm(cur[:, [0, 2]], axis=1)
    cur[:, 1] += lift * np.clip(1.0 - r / 3.0, 0.0, None)
    return src, cur, t


def _tip_clearance(V, t, height=0.8):
    """Plate surface height over the apex minus the apex height (ray straight up)."""
    O = np.array([0.0, 0.0, 0.0])
    for a, b, c in t:
        A, B, C = V[a], V[b], V[c]
        v0, v1, v2 = (B - A)[[0, 2]], (C - A)[[0, 2]], (O - A)[[0, 2]]
        den = v0[0] * v1[1] - v0[1] * v1[0]
        wb = (v2[0] * v1[1] - v2[1] * v1[0]) / den
        wc = (v0[0] * v2[1] - v0[1] * v2[0]) / den
        if wb >= 0 and wc >= 0 and wb + wc <= 1:
            return float((1 - wb - wc) * A[1] + wb * B[1] + wc * C[1] - height)
    raise AssertionError("apex not under the plate")


def _run(on, nipple=True, lift=1.2):
    bv, bn, nip = _body()
    src, cur, t = _plate(lift)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "PANEL_RIGID_TIP_GUARD", bool(on))
        out, touched, _s = fg._rigidify_within_clearance(
            src, cur, t, bv, bn, 1.0, min_verts=3,
            body_nipple=nip if nipple else None)
    return np.asarray(out), cur, t


def test_default_is_on():
    assert _resolve("nc.PANEL_RIGID_TIP_GUARD") == "True"


def test_defect_reproduces_with_the_guard_off():
    out, cur, t = _run(False)
    assert _tip_clearance(cur, t) > 0.0          # anti-poke left it clear
    assert _tip_clearance(out, t) < 0.0          # the plate settled onto the tip


def test_guard_keeps_the_tip_out():
    out, cur, t = _run(True)
    assert _tip_clearance(out, t) >= -1e-3


def test_no_nipple_map_is_identical_to_off():
    a, _, _ = _run(False)
    b, _, _ = _run(True, nipple=False)
    assert np.array_equal(a, b)


def test_clear_rigid_target_is_not_held_back():
    """Lift the whole plate so even the rigid target clears the tip: the guard
    must not change the answer."""
    bv, bn, nip = _body()
    src, cur, t = _plate(0.6)
    src[:, 1] += 1.0
    cur[:, 1] += 1.0
    res = []
    for on in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(nc, "PANEL_RIGID_TIP_GUARD", on)
            res.append(np.asarray(fg._rigidify_within_clearance(
                src, cur, t, bv, bn, 1.0, min_verts=3, body_nipple=nip)[0]))
    assert np.allclose(res[0], res[1])


def test_a_surface_behind_the_tip_does_not_block_the_panel():
    """One welded panel with a FRONT plate over the tip and a BACK sheet behind
    the body (a dress seen through the torso). The rigidify moves the back sheet
    a little deeper; that sheet covers nothing, so the guard must not refuse the
    panel for it. Pairing every surface within 4u did, and left a dress's
    anti-poke emboss in place (outline 0.86 -> 1.20)."""
    bv, bn, nip = _body()
    # a TWO-SIDED torso: the back is a plane at y=-2.5 facing -y, so the back
    # sheet below sits OUTSIDE it (as a dress back does) and only the tip
    # pairing can object to moving it
    g = np.arange(-6.0, 6.01, 0.4)
    X, Z = np.meshgrid(g, g)
    back = np.c_[X.ravel(), np.full(X.size, -2.5), Z.ravel()]
    bv = np.vstack([bv, back])
    bn = np.vstack([bn, np.tile([0.0, -1.0, 0.0], (len(back), 1))])
    nip = np.r_[nip, np.zeros(len(back))]
    src_f, cur_f, t_f = _plate(0.3)
    src_f[:, 1] += 0.5                            # plate at 1.0 over a 0.8 nipple:
    cur_f[:, 1] += 0.5                            # the rigid target stays clear
    src_b = src_f.copy(); src_b[:, 1] = -3.0
    cur_b = src_b.copy(); cur_b[:, 1] = -2.8 + 0.05 * np.sin(cur_b[:, 0])
    n = len(src_f)
    src = np.vstack([src_f, src_b]); cur = np.vstack([cur_f, cur_b])
    # weld front to back along the x-max edge with a strip of triangles
    edge = [j * 5 + 4 for j in range(5)]
    strip = []
    for a, b in zip(edge[:-1], edge[1:]):
        strip += [[a, b, b + n], [a, b + n, a + n]]
    t = np.vstack([t_f, t_f[:, ::-1] + n, np.asarray(strip)])
    res = []
    for on in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(nc, "PANEL_RIGID_TIP_GUARD", on)
            res.append(np.asarray(fg._rigidify_within_clearance(
                src, cur, t, bv, bn, 1.0, min_verts=3, body_nipple=nip)[0]))
    assert _tip_clearance(res[0][:n], t_f) >= 0.0     # the front stays clear anyway
    assert np.allclose(res[0], res[1])


def test_an_edge_on_strap_beside_the_tip_does_not_block_the_panel():
    """A strap hanging EDGE-ON beside the nipple (its plane contains the tip's
    normal), welded to the plate. Projected onto the strap's PLANE the nipple
    points land inside it at ~0 clearance; along the tip's own normal (a ray)
    they never meet it. Pairing by plane projection read the strap as covering
    the nipple at 0.05u and froze a whole top's panel (its covering surface was
    1.27u out)."""
    bv, bn, nip = _body()
    src_f, cur_f, t_f = _plate(0.3)
    src_f[:, 1] += 0.5
    cur_f[:, 1] += 0.5
    n = len(src_f)
    # top edge: two plate verts on the x=0.7 column; bottom edge hangs to y=0.2
    top = [j * 5 + 2 for j in (1, 2)]                  # (0.7, *, -1.55), (0.7, *, 0.45)
    bot_src = src_f[top].copy(); bot_src[:, 1] = 0.2
    bot_cur = cur_f[top].copy(); bot_cur[:, 1] = 0.25  # the rigidify pulls it 0.05 down
    src = np.vstack([src_f, bot_src]); cur = np.vstack([cur_f, bot_cur])
    strap = np.array([[top[0], top[1], n + 1], [top[0], n + 1, n]])
    t = np.vstack([t_f, strap])
    res = []
    for on in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(nc, "PANEL_RIGID_TIP_GUARD", on)
            res.append(np.asarray(fg._rigidify_within_clearance(
                src, cur, t, bv, bn, 1.0, min_verts=3, body_nipple=nip)[0]))
    assert _tip_clearance(res[0][:n], t_f) >= 0.0
    assert np.allclose(res[0], res[1])


def test_a_surface_only_behind_the_tip_does_not_block_the_panel():
    """Nothing covers the tip from the FRONT (the panel's front plate sits off to
    one side); only its back sheet lies behind the tip, ~3.6u deep. Pairing that
    sheet would hold it no-deeper and refuse the panel for a surface that covers
    nothing -- the case the `c0 > -1` filter exists for."""
    bv, bn, nip = _body()
    g = np.arange(-6.0, 6.01, 0.4)
    X, Z = np.meshgrid(g, g)
    back = np.c_[X.ravel(), np.full(X.size, -2.5), Z.ravel()]
    bv = np.vstack([bv, back])
    bn = np.vstack([bn, np.tile([0.0, -1.0, 0.0], (len(back), 1))])
    nip = np.r_[nip, np.zeros(len(back))]
    src_f, cur_f, t_f = _plate(0.0)
    src_f[:, 0] += 12.0; cur_f[:, 0] += 12.0       # front plate well off the tip
    src_f[:, 1] += 0.5; cur_f[:, 1] += 0.5
    src_b, _, _ = _plate(0.0)
    src_b[:, 1] = -3.0                              # the back sheet, behind the tip
    cur_b = src_b.copy(); cur_b[:, 1] = -2.8 + 0.05 * np.sin(cur_b[:, 0])
    n = len(src_f)
    src = np.vstack([src_f, src_b]); cur = np.vstack([cur_f, cur_b])
    edge = [j * 5 + 4 for j in range(5)]
    strip = []
    for a, b in zip(edge[:-1], edge[1:]):
        strip += [[a, b, b + n], [a, b + n, a + n]]
    t = np.vstack([t_f, t_f[:, ::-1] + n, np.asarray(strip)])
    res = []
    for on in (False, True):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(nc, "PANEL_RIGID_TIP_GUARD", on)
            res.append(np.asarray(fg._rigidify_within_clearance(
                src, cur, t, bv, bn, 1.0, min_verts=3, body_nipple=nip)[0]))
    assert not np.allclose(res[0], cur)                # the rigidify did act
    assert np.allclose(res[0], res[1])
