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

"""`#nipple-morph-envelope` -- a COARSE garment's triangle over the nipple
rises at least as far as the nipple grows.

WHY. A garment too coarse to trace a nipple spans it with triangles whose
corners sit up to an edge length from the apex; each corner copies motion from
the skin near IT, so all of them rise less than the nipple and the flat
triangle between them is overtaken. Measured on a coarse shirt under a preset
stacking nipple sliders at 200%: tip +0.05u -> -0.03u once corners sampled the
skin under them.

PINNED HERE:
  * DEFAULT ON, resolved in a scrubbed subprocess; CBBE2UBE_NO_* turns it off;
  * the DEFECT reproduces with the switch off: the surface over the apex rises
    well short of the nipple's growth;
  * ON, it rises at least as far as the nipple grows;
  * a breast-SHAPE slider and a FINE garment are untouched;
  * it only ever ADDS outward motion.
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


def _grid(step, half, y, off=0.0):
    g = np.arange(-half, half + 1e-6, step) + off
    X, Z = np.meshgrid(g, g)
    v = np.c_[X.ravel(), np.full(X.size, y), Z.ravel()]
    n = len(g)
    t = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            t += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    return v, np.asarray(t, dtype=np.int64)


_BV, _BT = _grid(0.2, 8.0, 0.0)
_R_B = np.linalg.norm(_BV[:, [0, 2]], axis=1)
_BV[:, 1] = 0.44 * np.exp(-_R_B ** 2 / (2 * 0.4 ** 2))
_APEX = int(np.argmin(_R_B))
_GROW = 0.6 * np.exp(-_R_B ** 2 / (2 * 0.4 ** 2))       # the nipple grows outward
_COARSE = _grid(2.0, 6.0, 1.2, off=0.7)                # no corner over the apex
_FINE = _grid(0.5, 6.0, 1.2, off=0.1)


def _osd():
    breast = 2.0 * np.exp(-_R_B ** 2 / (2 * 5.0 ** 2))
    ms = []
    for name, f in (("Grow", _GROW), ("Breast", breast)):
        idx = np.flatnonzero(np.abs(f) > 1e-9)
        ms.append(OsdMorph.from_offsets(
            "BaseShape" + name, [(int(i), 0.0, float(f[i]), 0.0) for i in idx]))
    return OsdFile(version=1, morphs=ms)


def _run(on, plate=_COARSE):
    importlib.reload(sg)
    pv, pt = plate
    nb = [(_APEX, np.array([0.0, 1.0, 0.0]), sg._vertex_areas(_BV, _BT))]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "_NIPPLE_MORPH_ENVELOPE", bool(on))
        # this step is judged in isolation, on the closest-surface kernel without
        # layer lockstep (both default ON since 2026-10-17; see test_morph_defaults_1017)
        mp.setattr(sg, "MATCH_IDW_K", 0)
        mp.setattr(sg, "_MORPH_LAYER_LOCKSTEP", False)
        tri = sg.generate_armor_tri(
            {"Plate": pv.copy()}, _BV, _osd(), body_shape_name="BaseShape",
            include_body_shapes=False, nipple_bridge=nb,
            armor_shape_tris={"Plate": pt})
    out = {}
    for ts in tri.shapes:
        if ts.name == "Plate":
            for m in ts.morphs:
                d = np.zeros((len(pv), 3))
                for vi, dx, dy, dz in m.offsets:
                    d[int(vi)] = (dx, dy, dz)
                out[m.name] = d
    return out


def _rise_over_apex(dy, plate=_COARSE):
    """The plate SURFACE's outward motion straight over the apex."""
    pv, pt = plate
    for a, b, c in pt:
        A, B, C = pv[a][[0, 2]], pv[b][[0, 2]], pv[c][[0, 2]]
        v0, v1, v2 = B - A, C - A, -A
        den = v0[0] * v1[1] - v0[1] * v1[0]
        wb = (v2[0] * v1[1] - v2[1] * v1[0]) / den
        wc = (v0[0] * v2[1] - v0[1] * v2[0]) / den
        if wb >= 0 and wc >= 0 and wb + wc <= 1:
            return float((1 - wb - wc) * dy[a] + wb * dy[b] + wc * dy[c])
    raise AssertionError("apex not under the plate")


def test_default_is_on():
    assert _resolve("sg._NIPPLE_MORPH_ENVELOPE") == "True"


def test_switch_reads_the_env():
    assert _resolve("sg._NIPPLE_MORPH_ENVELOPE",
                    {"CBBE2UBE_NO_NIPPLE_MORPH_ENVELOPE": "1"}) == "False"


def test_defect_reproduces_with_the_switch_off():
    d = _run(False).get("Grow", np.zeros((len(_COARSE[0]), 3)))[:, 1]
    assert _rise_over_apex(d) < 0.45          # the nipple grows 0.6


def test_surface_over_the_apex_keeps_up_with_the_nipple():
    d = _run(True)["Grow"][:, 1]
    assert _rise_over_apex(d) >= 0.6 - 1e-6


def test_only_adds_outward_motion():
    off = _run(False).get("Grow", np.zeros((len(_COARSE[0]), 3)))
    on = _run(True)["Grow"]
    assert np.all(on[:, 1] >= off[:, 1] - 1e-9)
    assert np.allclose(on[:, [0, 2]], off[:, [0, 2]])


def test_breast_shape_slider_is_untouched():
    assert np.array_equal(_run(False)["Breast"], _run(True)["Breast"])


def test_fine_garment_is_untouched():
    a, b = _run(False, _FINE), _run(True, _FINE)
    assert a.keys() == b.keys()
    for k in a:
        assert np.array_equal(a[k], b[k]), k


def test_corners_whose_triangles_miss_the_nipple_are_not_lifted():
    """Only triangles that COVER the nipple need its growth. A ball one edge
    wide lifted every corner near the apex (tip-patch stand-off +0.21u median
    under a mild preset over the 44-piece set)."""
    pv, pt = _COARSE
    off = _run(False).get("Grow", np.zeros((len(pv), 3)))[:, 1]
    on = _run(True)["Grow"][:, 1]
    covers = np.zeros(len(pv), dtype=bool)
    for tri in pt:
        x, z = pv[tri, 0], pv[tri, 2]
        v0 = (x[1] - x[0], z[1] - z[0]); v1 = (x[2] - x[0], z[2] - z[0])
        den = v0[0] * v1[1] - v0[1] * v1[0]
        qx, qz = -x[0], -z[0]
        wb = (qx * v1[1] - qz * v1[0]) / den
        wc = (v0[0] * qz - v0[1] * qx) / den
        # any triangle reaching within the nipple's 1.2u (3 sigma) gets lifted
        cen = np.hypot(x.mean(), z.mean())
        if (wb >= -0.6 and wc >= -0.6 and wb + wc <= 1.6) or cen < 1.2:
            covers[tri] = True
    far = ~covers & (np.hypot(pv[:, 0], pv[:, 2]) < 4.0)
    assert far.any()
    assert np.allclose(on[far], off[far], atol=1e-6)
