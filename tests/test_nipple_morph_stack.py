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

"""`#nipple-morph-stack` -- over a nipple, an outer garment layer rises at
least as far as the layer under it.

WHY. The bridge (fine garments: low-passed) and the envelope (coarse ones:
lifted) shape each layer's nipple-local morph ON ITS OWN. A coarse inner layer
under a fine outer one then rises past it and comes through under a preset
that drives the nipple sliders -- measured on a two-layer leather cuirass: bust
crossings 7 -> 119 verts (mild preset), 25 -> 237 (large); 14 / 37 with this.

PINNED HERE:
  * DEFAULT ON, resolved in a scrubbed subprocess; CBBE2UBE_NO_* turns it off;
  * the DEFECT reproduces with the switch off: somewhere over the nipple the
    outer layer's surface rises well short of the inner layer's;
  * ON, at every point over the nipple the outer surface rises at least as far;
  * "on top" is decided point by point: layers that already INTERLEAVE at
    rest keep their order everywhere, and a layer that is BELOW is never lifted
    (lifting a cuirass to its pauldron put it through the pauldron);
  * the floors pass twice, so a lift carries up a three-layer stack whatever
    order the floors are listed in;
  * a breast-SHAPE slider and a single-layer garment are byte-identical, and
    nothing is ever lowered or slid sideways.
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
from tests.test_nipple_morph_envelope import (  # noqa: E402
    _APEX, _BT, _BV, _COARSE, _grid, _osd)


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


_OUTER = _grid(0.5, 6.0, 1.6, off=0.1)          # fine: the bridge low-passes it


def _interleaved():
    """The coarse inner plate with one corner near the apex poking 0.1u ABOVE
    the outer face at rest."""
    v, t = _COARSE[0].copy(), _COARSE[1]
    k = int(np.argmin(np.linalg.norm(v[:, [0, 2]], axis=1)))
    v[k, 1] = 1.7
    return v, t


def _run(on, shapes, spread=0.0):
    """The floors are tested on their own (spread 0); the spread has its own tests."""
    importlib.reload(sg)
    nb = [(_APEX, np.array([0.0, 1.0, 0.0]), sg._vertex_areas(_BV, _BT))]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "_NIPPLE_MORPH_STACK", bool(on))
        # this step is judged in isolation, on the closest-surface kernel without
        # layer lockstep (both default ON since 2026-10-17; see test_morph_defaults_1017)
        mp.setattr(sg, "MATCH_IDW_K", 0)
        mp.setattr(sg, "_MORPH_LAYER_LOCKSTEP", False)
        mp.setattr(sg, "NIPPLE_MORPH_STACK_SPREAD", float(spread))
        tri = sg.generate_armor_tri(
            {n: v.copy() for n, (v, _t) in shapes.items()}, _BV, _osd(),
            body_shape_name="BaseShape", include_body_shapes=False,
            nipple_bridge=nb, armor_shape_tris={n: t for n, (_v, t) in shapes.items()})
    out = {}
    for ts in tri.shapes:
        n = len(shapes[ts.name][0])
        for m in ts.morphs:
            d = np.zeros((n, 3))
            for vi, dx, dy, dz in m.offsets:
                d[int(vi)] = (dx, dy, dz)
            out[(ts.name, m.name)] = d
    return out


def _rise_at(plate, dy, p):
    pv, pt = plate
    for a, b, c in pt:
        A, B, C = pv[a][[0, 2]], pv[b][[0, 2]], pv[c][[0, 2]]
        v0, v1, v2 = B - A, C - A, p - A
        den = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(den) < 1e-12:
            continue
        wb = (v2[0] * v1[1] - v2[1] * v1[0]) / den
        wc = (v0[0] * v2[1] - v0[1] * v2[0]) / den
        if wb >= -1e-9 and wc >= -1e-9 and wb + wc <= 1 + 1e-9:
            return float((1 - wb - wc) * dy[a] + wb * dy[b] + wc * dy[c])
    return None


_POINTS = [np.array([x, z]) for x in np.linspace(-3, 3, 25) for z in np.linspace(-3, 3, 25)
           if x * x + z * z < 9.0]


def _worst_shortfall(out, inner):
    """Largest amount, over points within 3u of the apex, by which the OUTER
    surface rises less than the INNER one under the nipple slider."""
    di, do = out[("Inner", "Grow")][:, 1], out[("Outer", "Grow")][:, 1]
    worst = 0.0
    for p in _POINTS:
        ri, ro = _rise_at(inner, di, p), _rise_at(_OUTER, do, p)
        if ri is not None and ro is not None:
            worst = max(worst, ri - ro)
    return worst


def test_default_is_on():
    assert _resolve("sg._NIPPLE_MORPH_STACK") == "True"


def test_switch_reads_the_env():
    assert _resolve("sg._NIPPLE_MORPH_STACK",
                    {"CBBE2UBE_NO_NIPPLE_MORPH_STACK": "1"}) == "False"


def test_off_the_outer_layer_is_overtaken():
    """The defect: the lifted coarse layer rises past the low-passed fine one."""
    out = _run(False, {"Inner": _COARSE, "Outer": _OUTER})
    assert _worst_shortfall(out, _COARSE) > 0.05


def test_on_the_outer_layer_rises_at_least_as_far():
    out = _run(True, {"Inner": _COARSE, "Outer": _OUTER})
    assert _worst_shortfall(out, _COARSE) <= 1e-6


_OUTER_APART = _grid(0.5, 6.0, 2.0, off=0.1)   # 0.8u over the coarse plate: not touching


def test_a_layer_clearly_apart_clears_the_ridges_under_it():
    """Beyond TOUCH only the floors hold the upper layer: the vert, triangle and
    EDGE rules. The lifted coarse layer peaks along its edges, and a flat fine
    face spanning such a ridge dips below it unless every crossing is held."""
    out = _run(True, {"Inner": _COARSE, "Outer": _OUTER_APART})
    di, do = out[("Inner", "Grow")][:, 1], out[("Outer", "Grow")][:, 1]
    worst = 0.0
    for p in _POINTS:
        ri, ro = _rise_at(_COARSE, di, p), _rise_at(_OUTER_APART, do, p)
        if ri is not None and ro is not None:
            worst = max(worst, ri - ro)
    off = _run(False, {"Inner": _COARSE, "Outer": _OUTER_APART})
    d0 = max(((_rise_at(_COARSE, off[("Inner", "Grow")][:, 1], p) or 0.0)
              - (_rise_at(_OUTER_APART, off[("Outer", "Grow")][:, 1], p) or 0.0))
             for p in _POINTS)
    assert d0 > 0.05                                       # the case is armed
    assert worst <= 1e-6


def _flips(out, inner):
    """Points within 3u of the apex where two layers SEPARATED at rest (by more
    than 0.05u) swap order once the nipple slider is applied. A point on the
    line where they already intersect at rest is left out: that line moving a
    little is not a new crossing (the population metric uses the same 0.05u)."""
    di, do = out[("Inner", "Grow")][:, 1], out[("Outer", "Grow")][:, 1]
    hi0, ho0 = inner[0][:, 1], _OUTER[0][:, 1]
    n = 0
    for p in _POINTS:
        a0, b0 = _rise_at(inner, hi0, p), _rise_at(_OUTER, ho0, p)
        ri, ro = _rise_at(inner, di, p), _rise_at(_OUTER, do, p)
        if None in (a0, b0, ri, ro) or abs(b0 - a0) < 0.05:
            continue
        n += (np.sign(b0 - a0) != np.sign((b0 + ro) - (a0 + ri)))
    return n


def test_interleaved_layers_keep_their_order_everywhere():
    inner = _interleaved()
    off = _run(False, {"Inner": inner, "Outer": _OUTER})
    on = _run(True, {"Inner": inner, "Outer": _OUTER})
    assert _flips(off, inner) > 0                           # the case is armed
    assert _flips(on, inner) == 0


def test_a_layer_below_is_never_lifted():
    """0.4u under the outer layer, the inner one is below everywhere: whatever
    the outer does, the inner keeps its own motion."""
    off = _run(False, {"Inner": _COARSE, "Outer": _OUTER})
    on = _run(True, {"Inner": _COARSE, "Outer": _OUTER})
    assert np.array_equal(on[("Inner", "Grow")], off[("Inner", "Grow")])


def test_a_lift_carries_up_a_stack_listed_out_of_order():
    """Three one-vert layers, the top floor listed before the middle one: the
    middle is lifted by the bottom, and the second pass carries that to the top."""
    up = np.array([[0.0, 1.0, 0.0]])
    one = (np.array([0]), np.array([[0, 0, 0]]), np.array([[1.0, 0.0, 0.0]]), up)
    prep = [("Top", one[0], "Mid", *one[1:]), ("Mid", one[0], "Bottom", *one[1:])]
    props = {"Bottom": np.array([[0.0, 1.0, 0.0]]), "Mid": np.zeros((1, 3)),
             "Top": np.zeros((1, 3))}
    out = sg._nipple_morph_stack_apply(props, prep)
    assert out["Mid"][0, 1] == pytest.approx(1.0)
    assert out["Top"][0, 1] == pytest.approx(1.0)


def test_a_breast_shape_slider_is_untouched():
    off = _run(False, {"Inner": _COARSE, "Outer": _OUTER})
    on = _run(True, {"Inner": _COARSE, "Outer": _OUTER})
    for k in (("Inner", "Breast"), ("Outer", "Breast")):
        assert np.array_equal(off[k], on[k])


def test_a_single_layer_is_untouched():
    off = _run(False, {"Outer": _OUTER})
    on = _run(True, {"Outer": _OUTER})
    assert off.keys() == on.keys()
    for k in off:
        assert np.array_equal(off[k], on[k])


def test_it_never_lowers_or_slides_anything():
    off = _run(False, {"Inner": _COARSE, "Outer": _OUTER})
    on = _run(True, {"Inner": _COARSE, "Outer": _OUTER})
    for k in (("Inner", "Grow"), ("Outer", "Grow")):
        d = on[k] - off[k]
        assert (d[:, 1] >= -1e-12).all() and np.abs(d[:, [0, 2]]).max() < 1e-12


# ---- the lift is spread over the upper layer (#nipple-morph-stack spread)

def _spread_case():
    """A flat 9x9 grid layer with two floored verts needing different lifts."""
    g = np.arange(0.0, 4.01, 0.5)
    X, Z = np.meshgrid(g, g)
    av = np.c_[X.ravel(), np.zeros(X.size), Z.ravel()]
    a, b = 40, 42                                # 1.0u apart in the middle row
    up = np.array([[0.0, 1.0, 0.0]])
    prep = [("Top", np.array([a, b]), "Under", np.array([[0, 0, 0], [1, 1, 1]]),
             np.array([[1.0, 0, 0], [1.0, 0, 0]]), np.repeat(up, 2, 0))]
    props = {"Top": np.zeros((len(av), 3)), "Under": np.array([[0, 0.6, 0], [0, 0.3, 0]])}
    return av, prep, props, a, b


def test_spread_keeps_every_floored_vert_at_its_lift():
    av, prep, props, a, b = _spread_case()
    sp = sg._nipple_morph_stack_spread_prep({"Top": av}, prep, radius=1.5)
    out = sg._nipple_morph_stack_apply({k: v.copy() for k, v in props.items()}, prep, spread=sp)
    assert out["Top"][a, 1] == pytest.approx(0.6)
    assert out["Top"][b, 1] == pytest.approx(0.3)   # its own lift beats a's spread (0.185)


def test_spread_rounds_the_lift_and_takes_the_largest():
    """Neighbours rise less than the vert they surround, falling off with
    distance; where two spreads overlap the larger one wins, never their sum."""
    av, prep, props, a, b = _spread_case()
    sp = sg._nipple_morph_stack_spread_prep({"Top": av}, prep, radius=1.5)
    out = sg._nipple_morph_stack_apply({k: v.copy() for k, v in props.items()}, prep, spread=sp)
    y = out["Top"][:, 1]
    near = int(np.argmin(np.linalg.norm(av - av[a] - [0, 0, 0.5], axis=1)))
    assert 0.0 < y[near] < y[a]
    assert y.max() == pytest.approx(0.6)
    far = np.linalg.norm(av - av[a], axis=1) > 1.5 + 1e-9
    far &= np.linalg.norm(av - av[b], axis=1) > 1.5 + 1e-9
    assert np.all(y[far] == 0.0)


def test_no_spread_lifts_only_the_floored_verts():
    av, prep, props, a, b = _spread_case()
    out = sg._nipple_morph_stack_apply({k: v.copy() for k, v in props.items()}, prep)
    y = out["Top"][:, 1]
    assert set(np.flatnonzero(y > 0)) == {a, b}
    assert sg._nipple_morph_stack_spread_prep({"Top": av}, prep, radius=0.0) == {}
