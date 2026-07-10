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

"""Jiggle-overshoot clearance (DEFAULT ON since 2026-07-10): the final anti-poke adds
bounded extra clearance where the body's softbody (breast/butt/belly) jiggle weight is
high, and adds exactly 0 where it's 0 -- so bouncing softbody can't punch through rigid
cloth while tight fit is kept elsewhere.

Every other clearance term measures the body at REST. HDT-SMP throws the breast outward
past that surface, and a rigid cuirass has no idea it's coming: an armor with +1.18u
resting clearance and ratio-1.0 morph tracking still showed skin in-game. This term is
the only one that reasons about the body's MOVING envelope.

It was opt-in and simply never switched on -- and the env var could not have reached the
converter anyway, since MO2 does not inherit them (the same reason UNIFIED_COVERAGE had to
become a sentinel file). Measured on the UBE body: breast +0.14u mean / +0.28u at the
nipple, belly +0.02u, butt +0.01u, back exactly 0.000u. Disable with
CBBE2UBE_NO_JIGGLE_CLEARANCE=1; retune with CBBE2UBE_JIGGLE_CLEARANCE_GAIN / _MAX."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nif_convert import (clear_armor_outside_body, _body_jiggle_weight,
                             JIGGLE_CLEARANCE_GAIN)


def _flat_body(n=40, spacing=1.0):
    """A flat XZ 'body' sheet at y=0 with +Y normals, verts dense enough that
    the anti-poke neighborhood (radius 4) stays populated."""
    xs = np.arange(n) * spacing
    verts = np.stack([xs, np.zeros(n), np.zeros(n)], axis=1)
    normals = np.tile([0.0, 1.0, 0.0], (n, 1))
    return verts, normals


def test_zero_jiggle_map_is_noop_vs_none():
    bv, bn = _flat_body()
    # armor verts hovering at y=0.5 over body verts 10 and 30
    av = np.array([[10.0, 0.5, 0.0], [30.0, 0.5, 0.0]])
    amp = np.zeros(len(bv))          # no morphs anywhere
    out_none = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp)
    out_zero = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp,
                                        jiggle_amplitude=np.zeros(len(bv)))
    assert np.allclose(out_none, out_zero), "all-zero jiggle map must be a no-op"
    print("  test_zero_jiggle_map_is_noop_vs_none OK")


def test_jiggle_zone_gains_clearance_static_zone_does_not():
    bv, bn = _flat_body()
    jig = np.zeros(len(bv))
    jig[8:13] = 1.0                  # full jiggle weight around body vert 10
    amp = np.zeros(len(bv))
    av = np.array([[10.0, 0.5, 0.0],   # over the jiggle zone
                   [30.0, 0.5, 0.0]])  # over a static zone
    base = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp)
    out = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp,
                                   jiggle_amplitude=jig)
    dy_static = out[1, 1] - base[1, 1]
    assert abs(dy_static) < 1e-6, f"static zone must not move: {dy_static}"
    # Push-out-only semantics: the vert ends at the required TOTAL standoff
    # (adaptive_base + jiggle term), regardless of its starting height.
    from src.nif_convert import ADAPTIVE_CLEARANCE_BASE
    want = ADAPTIVE_CLEARANCE_BASE + JIGGLE_CLEARANCE_GAIN
    assert abs(out[0, 1] - want) < 1e-6, \
        f"jiggle zone must end at base+jiggle standoff {want}, got {out[0, 1]}"
    assert out[0, 1] > base[0, 1]      # and it actually moved outward
    print("  test_jiggle_zone_gains_clearance_static_zone_does_not OK")


def test_jiggle_term_is_capped():
    bv, bn = _flat_body()
    jig = np.full(len(bv), 1.0)
    amp = np.zeros(len(bv))
    av = np.array([[20.0, 0.1, 0.0]])
    out = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp,
                                   jiggle_amplitude=jig,
                                   jiggle_gain=5.0, jiggle_cap=0.3)
    base = clear_armor_outside_body(av, bv, bn, morph_amplitude=amp)
    assert abs((out[0, 1] - base[0, 1]) - 0.3) < 1e-6, \
        "jiggle term must clip at jiggle_cap"
    print("  test_jiggle_term_is_capped OK")


class _FakeShape:
    def __init__(self, n, bw):
        self.verts = [(0.0, 0.0, 0.0)] * n
        self.bone_weights = bw


def test_body_jiggle_weight_map():
    bw = {"L Breast02": [(0, 0.9), (1, 0.4)],
          "NPC Belly": [(1, 0.7)],
          "NPC Spine2 [Spn2]": [(2, 1.0)]}     # not a jiggle bone
    m = _body_jiggle_weight(_FakeShape(4, bw))
    assert m is not None
    assert abs(m[0] - 0.9) < 1e-9      # breast weight
    assert abs(m[1] - 0.7) < 1e-9      # max(breast 0.4, belly 0.7)
    assert m[2] == 0.0 and m[3] == 0.0  # spine/unweighted = no jiggle
    # a body with no jiggle bones -> None (pass no-ops)
    assert _body_jiggle_weight(_FakeShape(2, {"NPC Spine": [(0, 1.0)]})) is None
    print("  test_body_jiggle_weight_map OK")


def test_enabled_by_default():
    """It shipped opt-in and was never switched on, so the armor was only ever cleared
    against the body's resting shape. An env var would not have reached the converter."""
    import src.nif_convert as nc
    assert nc.JIGGLE_CLEARANCE_ENABLED is True


def test_disable_switch_exists(monkeypatch):
    import importlib
    import src.nif_convert as nc
    monkeypatch.setenv("CBBE2UBE_NO_JIGGLE_CLEARANCE", "1")
    importlib.reload(nc)
    assert nc.JIGGLE_CLEARANCE_ENABLED is False
    monkeypatch.delenv("CBBE2UBE_NO_JIGGLE_CLEARANCE")
    importlib.reload(nc)
    assert nc.JIGGLE_CLEARANCE_ENABLED is True


def test_gain_and_cap_retune_without_a_rebuild(monkeypatch):
    """A bouncier SMP setup needs a bigger term. Retuning must cost a reconvert, not a
    rebuild -- the clearance is baked into the mesh at convert time."""
    import importlib
    import src.nif_convert as nc
    monkeypatch.setenv("CBBE2UBE_JIGGLE_CLEARANCE_GAIN", "1.4")
    monkeypatch.setenv("CBBE2UBE_JIGGLE_CLEARANCE_MAX", "1.2")
    importlib.reload(nc)
    assert abs(nc.JIGGLE_CLEARANCE_GAIN - 1.4) < 1e-9
    assert abs(nc.JIGGLE_CLEARANCE_MAX - 1.2) < 1e-9
    monkeypatch.delenv("CBBE2UBE_JIGGLE_CLEARANCE_GAIN")
    monkeypatch.delenv("CBBE2UBE_JIGGLE_CLEARANCE_MAX")
    importlib.reload(nc)
    assert abs(nc.JIGGLE_CLEARANCE_GAIN - 0.5) < 1e-9
