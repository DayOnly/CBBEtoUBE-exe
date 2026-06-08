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

"""feminize_male_armor_conform: pull male-armor verts IN to hug the female body
in the feminine zones (breast/butt/belly), using only the female body. Must
hug the bust, leave static zones untouched, never push out (no poke-through),
never pull below target_standoff.
"""
import numpy as np
from src import nif_convert


def test_conform_hugs_bust_leaves_static_no_pokethrough():
    # Two body points with +Z outward normals: one BREAST (high morph amp),
    # one STATIC shoulder (zero amp).
    body = np.array([[0.0, 0.0, 0.0],     # breast region
                     [50.0, 0.0, 0.0]],   # static region
                    dtype=np.float64)
    normals = np.array([[0, 0, 1.0], [0, 0, 1.0]], dtype=np.float64)
    amp = np.array([4.0, 0.0], dtype=np.float64)
    # Armor: a flat slab sitting 3u off BOTH regions.
    armor = np.array([[0.0, 0.0, 3.0], [50.0, 0.0, 3.0]], dtype=np.float64)

    out = nif_convert.feminize_male_armor_conform(
        armor, body, normals, amp, target_standoff=0.7, smooth_iters=0)
    breast_clr = out[0, 2]   # clearance == z (normal is +Z, surface at z=0)
    static_clr = out[1, 2]
    assert abs(breast_clr - 0.7) < 1e-6, f"breast must hug to target, got {breast_clr}"
    assert abs(static_clr - 3.0) < 1e-6, f"static zone must be untouched, got {static_clr}"
    # never pull below target (no poke-through), never push out
    assert breast_clr >= 0.7 - 1e-9 and breast_clr <= 3.0 + 1e-9
    print("  test_conform_hugs_bust_leaves_static_no_pokethrough OK")


def test_conform_never_pushes_out_or_below_target():
    body = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    normals = np.array([[0, 0, 1.0]], dtype=np.float64)
    amp = np.array([4.0], dtype=np.float64)
    # Already INSIDE target (0.4u < 0.7 target): must NOT be pushed out.
    inside = np.array([[0.0, 0.0, 0.4]], dtype=np.float64)
    out = nif_convert.feminize_male_armor_conform(
        inside, body, normals, amp, target_standoff=0.7, smooth_iters=0)
    assert abs(out[0, 2] - 0.4) < 1e-6, "must not push an inside vert outward"
    print("  test_conform_never_pushes_out_or_below_target OK")


def test_smoothing_reduces_pull_discontinuity():
    # A strip of armor verts; only the middle one is over a breast (high amp),
    # neighbours are static. Without smoothing the middle vert pulls in alone
    # (big edge jump); with smoothing the pull spreads to neighbours.
    n = 7
    xs = np.linspace(0, 12, n)
    body = np.stack([xs, np.zeros(n), np.zeros(n)], 1)
    normals = np.tile([0, 0, 1.0], (n, 1))
    amp = np.zeros(n); amp[n // 2] = 5.0     # one breast vert
    armor = np.stack([xs, np.zeros(n), np.full(n, 3.0)], 1)
    tris = np.array([[i, i + 1, i] for i in range(n - 1)], dtype=np.int64)

    no_s = nif_convert.feminize_male_armor_conform(
        armor, body, normals, amp, smooth_iters=0)
    sm = nif_convert.feminize_male_armor_conform(
        armor, body, normals, amp, tris=tris, smooth_iters=4)
    # adjacent-vertex z difference (discontinuity) should shrink with smoothing
    jump_no = np.abs(np.diff(no_s[:, 2])).max()
    jump_sm = np.abs(np.diff(sm[:, 2])).max()
    assert jump_sm < jump_no, f"smoothing should reduce the jump: {jump_sm} !< {jump_no}"
    print("  test_smoothing_reduces_pull_discontinuity OK")


def test_disabled_when_no_morph_map():
    body = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    normals = np.array([[0, 0, 1.0]], dtype=np.float64)
    armor = np.array([[0.0, 0.0, 3.0]], dtype=np.float64)
    out = nif_convert.feminize_male_armor_conform(armor, body, normals, None)
    assert np.allclose(out, armor), "no morph map -> no-op"
    print("  test_disabled_when_no_morph_map OK")


test_conform_hugs_bust_leaves_static_no_pokethrough()
test_conform_never_pushes_out_or_below_target()
test_smoothing_reduces_pull_discontinuity()
test_disabled_when_no_morph_map()
