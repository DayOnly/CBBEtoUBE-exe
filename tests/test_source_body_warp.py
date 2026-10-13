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

"""`#source-body-warp` / `#source-body-tighten` -- a piece authored on ANOTHER body.

WHY. A cuirass a retexture mod ships on the vanilla body (no CBBE build exists)
was warped by the CBBE template's delta as if it had been built on CBBE; its
authored stand-off is to ITS body, and it came out 3.5u off the waist and
~5.5u at the lower-back hem, which an arched idle opens to view.

PINNED HERE:
  * both DEFAULT ON, resolved in a scrubbed subprocess; CBBE2UBE_NO_* turns each off;
  * the warp leaves a CBBE build alone (its body sits on the template);
  * a body off the template is warped from ITSELF: each of its verts lands where
    the template's nearest vert goes;
  * too little torso to judge -> no warp;
  * the tighten pulls a loose torso surface in to the target and no further,
    never moves a vert outward, and leaves verts outside the torso band alone.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import nif_convert as nc                  # noqa: E402


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


def _plane(y, z0=60.0, z1=130.0, step=1.0):
    """A body-like plane at depth y, spanning the torso heights, normal +y."""
    xs = np.arange(-10.0, 10.01, step)
    zs = np.arange(z0, z1 + 0.01, step)
    X, Z = np.meshgrid(xs, zs)
    return np.c_[X.ravel(), np.full(X.size, y), Z.ravel()]


def _grid_tris(nx, nz):
    t = []
    for j in range(nz - 1):
        for i in range(nx - 1):
            a = j * nx + i
            t += [[a, a + 1, a + nx + 1], [a, a + nx + 1, a + nx]]
    return np.asarray(t, dtype=np.int64)


def test_both_default_on():
    assert _resolve("(nc.SOURCE_BODY_WARP, nc.SOURCE_BODY_TIGHTEN)") == "(True, True)"


def test_switches_read_the_env():
    assert _resolve("nc.SOURCE_BODY_WARP",
                    {"CBBE2UBE_NO_SOURCE_BODY_WARP": "1"}) == "False"
    assert _resolve("nc.SOURCE_BODY_TIGHTEN",
                    {"CBBE2UBE_NO_SOURCE_BODY_TIGHTEN": "1"}) == "False"


def test_a_cbbe_build_is_left_alone():
    cbbe = _plane(0.0)
    src = cbbe + np.array([0.0, 0.1, 0.0])          # 0.1u off: a CBBE build
    assert nc._source_body_warp(src, cbbe, np.zeros_like(cbbe)) is None


def test_a_body_off_the_template_is_warped_from_itself():
    """Each source body vert must land where the template's nearest vert goes:
    source + delta == template vert + template delta."""
    cbbe = _plane(0.0)
    delta = np.tile([0.0, 2.0, 0.0], (len(cbbe), 1))   # CBBE -> UBE: +2u out
    src = cbbe + np.array([0.0, 1.0, 0.0])              # vanilla body, 1u off
    res = nc._source_body_warp(src, cbbe, delta)
    assert res is not None
    off, d = res
    assert abs(off - 1.0) < 1e-6
    assert np.allclose(src + d, cbbe + delta)


def test_too_little_torso_to_judge_is_not_warped():
    cbbe = _plane(0.0)
    src = _plane(1.0, z0=20.0, z1=40.0)                # a leg, no torso verts
    assert nc._source_body_warp(src, cbbe, np.zeros_like(cbbe)) is None


def _tighten_case(gap):
    body = _plane(0.0)
    nb = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    xs = np.arange(-6.0, 6.01, 1.0); zs = np.arange(60.0, 130.01, 1.0)
    X, Z = np.meshgrid(xs, zs)
    g = np.c_[X.ravel(), np.full(X.size, gap), Z.ravel()]
    return g, _grid_tris(len(xs), len(zs)), body, nb


def test_loose_torso_is_pulled_to_the_target_and_no_further():
    g, t, body, nb = _tighten_case(3.5)
    res = nc._source_body_tighten(g, t, body, nb)
    assert res is not None
    new, _pull = res
    mid = (g[:, 2] > 80.0) & (g[:, 2] < 108.0)          # well inside the band
    target = float(nc.SOURCE_BODY_TIGHT_TARGET)
    assert np.all(new[mid, 1] >= target - 1e-6)
    assert np.allclose(new[mid, 1], target, atol=0.05)


def test_never_moves_a_vert_outward():
    g, t, body, nb = _tighten_case(3.5)
    new, _ = nc._source_body_tighten(g, t, body, nb)
    assert np.all(new[:, 1] <= g[:, 1] + 1e-6)


def test_outside_the_torso_band_is_left_alone():
    g, t, body, nb = _tighten_case(3.5)
    new, _ = nc._source_body_tighten(g, t, body, nb)
    out = (g[:, 2] < 66.0) | (g[:, 2] > 122.0)
    assert out.any()
    assert np.allclose(new[out], g[out], atol=1e-6)


def test_a_surface_already_within_the_target_is_untouched():
    g, t, body, nb = _tighten_case(1.0)
    assert nc._source_body_tighten(g, t, body, nb) is None


def test_smoothing_never_drags_a_vert_inside_where_it_may_rest():
    """Loose (3.5u) next to tight (1.0u): smoothing the pull field drags the tight
    side in too. No vert may end closer than min(its own start gap, target)."""
    g, t, body, nb = _tighten_case(3.5)
    tight = g[:, 0] < 0.0
    g = g.copy(); g[tight, 1] = 1.0
    new, _ = nc._source_body_tighten(g, t, body, nb)
    floor = np.minimum(g[:, 1], float(nc.SOURCE_BODY_TIGHT_TARGET))
    assert np.all(new[:, 1] >= floor - 1e-6)
