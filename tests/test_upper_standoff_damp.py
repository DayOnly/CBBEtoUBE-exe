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

"""Guard for the upper-body standoff-damp in warp_armor_by_body_delta (#178):
rigid standoff geometry in the UPPER body (a collar) gets LESS warp so it stops
shearing, while body-fitted cloth, and ALL lower-body drape (skirts), keep the
full warp."""
import numpy as np
from src.nif_convert import warp_armor_by_body_delta


def _body_column():
    # body verts along Z at the origin; uniform +5 X delta (CBBE->UBE shift)
    z = np.linspace(0.0, 120.0, 121)
    bv = np.stack([np.zeros_like(z), np.zeros_like(z), z], axis=1)
    delta = np.tile(np.array([5.0, 0.0, 0.0]), (len(bv), 1))
    return bv.astype(np.float64), delta.astype(np.float64)


def _warp(a, damp):
    bv, delta = _body_column()
    return np.asarray(warp_armor_by_body_delta(
        np.asarray([a], np.float64), bv, delta,
        min_standoff=0.0, upper_damp_max=damp), np.float64)[0]


def test_upper_standoff_collar_is_damped():
    a = [8.0, 0.0, 115.0]                       # high Z + high standoff = collar
    full = _warp(a, 0.0)                          # damp off
    damped = _warp(a, 0.6)                         # damp on (default)
    disp_full = np.linalg.norm(full - a)
    disp_damped = np.linalg.norm(damped - a)
    assert disp_damped < disp_full - 0.5, (disp_full, disp_damped)  # clearly reduced
    assert disp_damped > 0.1                       # but not fully removed


def test_lower_body_skirt_untouched():
    a = [8.0, 0.0, 50.0]                          # high standoff but LOW Z (skirt)
    full = _warp(a, 0.0)
    damped = _warp(a, 0.6)
    assert np.allclose(full, damped, atol=1e-6)    # Z-gate closed -> identical


def test_body_fitted_upper_untouched():
    a = [1.0, 0.0, 115.0]                          # upper Z but LOW standoff (fitted)
    full = _warp(a, 0.0)
    damped = _warp(a, 0.6)
    assert np.allclose(full, damped, atol=1e-6)    # standoff-gate closed -> identical
