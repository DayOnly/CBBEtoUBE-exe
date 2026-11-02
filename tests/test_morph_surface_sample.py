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

"""`#morph-surface-sample` -- a hugging garment moves as the skin UNDER it.

WHY. Body-motion match copies the delta of the NEAREST body VERTEX. Where the
body's motion varies across one body edge, neighbouring garment verts latch onto
different body verts and the triangle between them shears: on a fine top, 37%
of the triangles near the bust changed shape > 1.5x beyond the body's own
stretch under a large preset (worst 1%: 11x).

PINNED HERE:
  * DEFAULT ON, resolved in a scrubbed subprocess; CBBE2UBE_NO_* turns it off;
  * the DEFECT reproduces with the switch off: the garment's delta is a step
    function of position over a body whose motion is a smooth ramp;
  * ON, the garment's delta is the body's delta at the closest SURFACE point --
    it follows the ramp to within float noise;
  * INERT without body triangles, and when the triangles index another body.
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


_BV, _BT = _grid(2.0, 8.0, 0.0)     # a COARSE body: 2u between body verts
_PV, _ = _grid(0.25, 6.0, 1.0)     # a fine garment hugging it 1u out


def _ramp(x):
    return 0.3 * x                  # outward motion grows smoothly along x


def _osd():
    f = _ramp(_BV[:, 0])
    idx = np.flatnonzero(np.abs(f) > 1e-9)
    return OsdFile(version=1, morphs=[OsdMorph.from_offsets(
        "BaseShapeRamp", [(int(i), 0.0, float(f[i]), 0.0) for i in idx])])


def _run(on, tris=True, bad_index=False):
    importlib.reload(sg)
    bt = _BT.copy()
    if bad_index:
        bt[0, 0] = len(_BV) + 5
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "_MORPH_SURFACE_SAMPLE", bool(on))
        # this step is judged in isolation, on the closest-surface kernel without
        # layer lockstep (both default ON since 2026-10-17; see test_morph_defaults_1017)
        mp.setattr(sg, "MATCH_IDW_K", 0)
        mp.setattr(sg, "_MORPH_LAYER_LOCKSTEP", False)
        tri = sg.generate_armor_tri(
            {"Plate": _PV.copy()}, _BV, _osd(), body_shape_name="BaseShape",
            include_body_shapes=False, body_tris=bt if tris else None)
    d = np.zeros((len(_PV), 3))
    for ts in tri.shapes:
        if ts.name == "Plate":
            for m in ts.morphs:
                for vi, dx, dy, dz in m.offsets:
                    d[int(vi)] = (dx, dy, dz)
    return d


def test_default_is_on():
    assert _resolve("sg._MORPH_SURFACE_SAMPLE") == "True"


def test_switch_reads_the_env():
    assert _resolve("sg._MORPH_SURFACE_SAMPLE",
                    {"CBBE2UBE_NO_MORPH_SURFACE_SAMPLE": "1"}) == "False"


def test_defect_reproduces_with_the_switch_off():
    """Nearest-vertex copying turns the smooth ramp into steps 2u wide."""
    d = _run(False)[:, 1]
    err = np.abs(d - _ramp(_PV[:, 0]))
    assert err.max() > 0.25


def test_garment_follows_the_skin_under_it():
    d = _run(True)[:, 1]
    assert np.abs(d - _ramp(_PV[:, 0])).max() < 1e-3


def test_inert_without_body_triangles():
    assert np.array_equal(_run(False), _run(True, tris=False))


def test_inert_when_triangles_index_another_body():
    assert np.array_equal(_run(False), _run(True, bad_index=True))
