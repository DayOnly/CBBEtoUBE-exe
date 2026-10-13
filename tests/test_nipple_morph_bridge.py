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

"""`#nipple-morph-bridge` -- a garment over the nipple carries no detail finer
than its own stand-off.

WHY. A garment vert copies the delta of its NEAREST body vert. From a plate
1.4u off the skin that is the apex for every plate vert within ~1.5u of it, so
an inverted-nipple / puffy-areola slider (apex in, rim out, both inside 1.5u)
sank a 1.5u disc of the plate and raised a rim just outside it -- a circle on a
reported top under a preset that sets the slider to 0.5.

PINNED HERE:
  * DEFAULT ON, resolved in a scrubbed subprocess; CBBE2UBE_NO_* turns it off;
  * the DEFECT reproduces with the switch off: a sunken disc with a raised rim;
  * ON, the plate's delta is smooth over the nipple (no disc, no rim);
  * a breast-SHAPE slider (moves the body well away from the apex) is followed
    exactly -- averaging it down put a nipple through a top under a large preset;
  * a COARSE garment (median edge over the nipple > NIPPLE_MORPH_MAX_EDGE) keeps
    the plain transfer -- it cannot trace a nipple, and averaging its growth away
    put two coarse pieces through under a large preset;
  * INERT without the nipple inputs or without triangles.
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


def _grid(step, half, y):
    g = np.arange(-half, half + 1e-6, step)
    X, Z = np.meshgrid(g, g)
    v = np.c_[X.ravel(), np.full(X.size, y), Z.ravel()]
    n = len(g)
    t = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            t += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    return v, np.asarray(t, dtype=np.int64)


_BV, _BT = _grid(0.2, 8.0, 0.0)                 # body: plane y=0 ...
_R_B = np.linalg.norm(_BV[:, [0, 2]], axis=1)
_BV[:, 1] = 0.8 * np.exp(-_R_B ** 2 / (2 * 0.6 ** 2))   # ... with a raised nipple
_PV, _PT = _grid(0.5, 7.0, 2.2)                 # plate 1.4u off the apex
_R_P = np.linalg.norm(_PV[:, [0, 2]], axis=1)
_APEX = int(np.argmin(_R_B))


def _osd():
    """`Puffy`: apex in -0.36, areola rim out +0.27, nothing past 1.5u (the
    measured slider). `Breast`: a broad outward swell (breast SHAPE)."""
    puffy = np.where(_R_B < 0.5, -0.36, np.where(_R_B < 1.5, 0.27, 0.0))
    breast = 3.0 * np.exp(-_R_B ** 2 / (2 * 4.0 ** 2))
    ms = []
    for name, f in (("Puffy", puffy), ("Breast", breast)):
        idx = np.flatnonzero(np.abs(f) > 1e-9)
        ms.append(OsdMorph.from_offsets(
            "BaseShape" + name, [(int(i), 0.0, float(f[i]), 0.0) for i in idx]))
    return OsdFile(version=1, morphs=ms)


def _run(on, inputs=True, plate=None, tris=True):
    importlib.reload(sg)
    pv, pt = plate if plate is not None else (_PV, _PT)
    nb = [(_APEX, np.array([0.0, 1.0, 0.0]), sg._vertex_areas(_BV, _BT))]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "_NIPPLE_MORPH_BRIDGE", bool(on))
        tri = sg.generate_armor_tri(
            {"Plate": pv.copy()}, _BV, _osd(), body_shape_name="BaseShape",
            include_body_shapes=False, nipple_bridge=nb if inputs else None,
            armor_shape_tris={"Plate": pt} if tris else None)
    out = {}
    for ts in tri.shapes:
        if ts.name == "Plate":
            for m in ts.morphs:
                d = np.zeros((len(pv), 3))
                for vi, dx, dy, dz in m.offsets:
                    d[int(vi)] = (dx, dy, dz)
                out[m.name] = d
    return out


def _rim_step(dy):
    """Largest outward-minus-inward swing of the plate's delta within 3u."""
    near = _R_P < 3.0
    return float(dy[near].max() - dy[near].min())


def test_default_is_on():
    assert _resolve("sg._NIPPLE_MORPH_BRIDGE") == "True"


def test_switch_reads_the_env():
    assert _resolve("sg._NIPPLE_MORPH_BRIDGE",
                    {"CBBE2UBE_NO_NIPPLE_MORPH_BRIDGE": "1"}) == "False"


def test_defect_reproduces_with_the_switch_off():
    """The plate copies the APEX over a disc far wider than the 0.5u dip (the
    rim never reaches it), and the disc ends in a step: a circle."""
    d = _run(False)["Puffy"][:, 1]
    assert d[_R_P < 1.5].max() < -0.3
    assert _rim_step(d) > 0.3


def test_plate_is_smooth_over_the_nipple():
    d = _run(True).get("Puffy", np.zeros((len(_PV), 3)))[:, 1]
    assert _rim_step(d) < 0.1


def test_breast_shape_slider_is_followed_exactly():
    off = _run(False)["Breast"]
    on = _run(True)["Breast"]
    assert np.array_equal(on, off)


def test_inert_without_the_nipple_inputs():
    off = _run(False)
    on = _run(True, inputs=False)
    assert off.keys() == on.keys()
    for k in off:
        assert np.array_equal(off[k], on[k]), k


def _equal_runs(a, b):
    assert a.keys() == b.keys()
    for k in a:
        assert np.array_equal(a[k], b[k]), k


def test_coarse_garment_keeps_the_plain_transfer():
    coarse = _grid(2.0, 7.0, 2.2)
    _equal_runs(_run(False, plate=coarse), _run(True, plate=coarse))


def test_inert_without_triangles():
    _equal_runs(_run(False), _run(True, tris=False))
