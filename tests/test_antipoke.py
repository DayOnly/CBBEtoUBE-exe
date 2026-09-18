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

"""Guard for clear_armor_outside_body -- the FINAL anti-poke pass (#175).
Push-OUT only, measured against the (injected) body; nipple-aware in the bust
band; leaves already-clear and far-from-body verts alone."""
import numpy as np
from src.nif_convert import clear_armor_outside_body


def _flat_body(n=21, span=6.0, z=0.0):
    xs = np.linspace(-span, span, n)
    gx, gy = np.meshgrid(xs, xs)
    v = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)], axis=1)
    nrm = np.tile(np.array([0.0, 0.0, 1.0]), (len(v), 1))
    return v.astype(float), nrm.astype(float)


def test_pushes_tight_vert_out_to_flat_clear():
    bv, bn = _flat_body()
    cloth = np.array([[0.0, 0.0, 0.05]])              # basically touching -> pokes
    out = clear_armor_outside_body(cloth, bv, bn, flat_clear=0.6)
    assert out[0, 2] >= 0.6 - 1e-3, out               # pushed out to flat_clear


def test_leaves_already_clear_vert():
    bv, bn = _flat_body()
    cloth = np.array([[0.0, 0.0, 1.5]])               # already clear of flat_clear
    out = clear_armor_outside_body(cloth, bv, bn, flat_clear=0.6)
    assert abs(out[0, 2] - 1.5) < 1e-3                # untouched (push-out only)


def test_push_out_only_never_pulls_in():
    bv, bn = _flat_body()
    cloth = np.array([[0.0, 0.0, 3.0]])
    out = clear_armor_outside_body(cloth, bv, bn, flat_clear=0.6)
    assert out[0, 2] >= 3.0 - 1e-3                    # never reeled in


def test_nipple_band_gets_more_clearance_than_flat():
    bv, bn = _flat_body(z=96.0)                        # body in the bust Z-band
    nip = np.zeros(len(bv))
    ci = int(np.argmin(np.linalg.norm(bv[:, :2], axis=1)))
    nip[ci] = 0.7                                      # mark a nipple vert
    cloth = np.array([[0.0, 0.0, 96.1]])               # tight over the nipple
    out = clear_armor_outside_body(cloth, bv, bn, body_nipple=nip,
                                   flat_clear=0.6, bust_clear=1.0, nipple_gain=1.5)
    assert out[0, 2] - 96.0 >= 0.9, out                # ramps toward bust_clear
    out_flat = clear_armor_outside_body(cloth, bv, bn,
                                        flat_clear=0.6, bust_clear=1.0)  # no nipple wt
    assert out_flat[0, 2] - 96.0 < 0.8, out_flat       # flat band stays close


def test_far_drape_untouched():
    bv, bn = _flat_body()
    cloth = np.array([[0.0, 0.0, 20.0]])               # free-hanging, far from body
    out = clear_armor_outside_body(cloth, bv, bn, max_body_dist=10.0)
    assert abs(out[0, 2] - 20.0) < 1e-3                # not bulged out


# ---- adaptive (morph-aware) clearance: the "armor floats off the breasts" fix --
from src.nif_convert import (ADAPTIVE_CLEARANCE_BASE as _BASE,
                             ADAPTIVE_CLEARANCE_MORPH_MAX as _CAP)


def test_adaptive_static_zone_hugs_body():
    # A STATIC zone (zero morph amplitude) must drop to the small z-fight floor,
    # NOT the old fixed flat_clear -> armor hugs the body instead of floating.
    bv, bn = _flat_body()
    amp = np.zeros(len(bv))
    cloth = np.array([[0.0, 0.0, 0.05]])               # tight/poking
    out = clear_armor_outside_body(cloth, bv, bn, flat_clear=0.8,
                                   morph_amplitude=amp)
    assert out[0, 2] <= _BASE + 0.05, out              # ~adaptive_base (0.25)
    assert out[0, 2] < 0.8 - 0.2, "must be far tighter than the old flat_clear"


def test_adaptive_morph_zone_keeps_clearance():
    # A high-morph zone (breast/belly) still gets the big standoff, capped.
    bv, bn = _flat_body()
    amp = np.full(len(bv), 9.0)                         # large outward growth
    cloth = np.array([[0.0, 0.0, 0.05]])
    out = clear_armor_outside_body(cloth, bv, bn, morph_amplitude=amp)
    assert abs(out[0, 2] - _CAP) < 0.05, out           # ramps up to the cap


def test_adaptive_uses_worst_neighbour_amplitude():
    # A high-morph body vert in the neighbourhood drives the clearance even when
    # the NEAREST body vert is flat (the #175 nipple-tip case).
    bv, bn = _flat_body()
    amp = np.zeros(len(bv))
    hot = int(np.argmin(np.linalg.norm(bv[:, :2] - np.array([0.6, 0.0]), axis=1)))
    amp[hot] = 9.0                                      # a nearby (not nearest) hot vert
    cloth = np.array([[0.0, 0.0, 0.05]])               # nearest body vert is flat (amp 0)
    out = clear_armor_outside_body(cloth, bv, bn, radius=4.0, morph_amplitude=amp)
    assert out[0, 2] > _BASE + 0.3, out                # the hot neighbour lifted req


def test_adaptive_absent_falls_back_to_legacy_flat_clear():
    # No morph map -> legacy fixed flat_clear behaviour (unchanged for old callers).
    bv, bn = _flat_body()
    cloth = np.array([[0.0, 0.0, 0.05]])
    out = clear_armor_outside_body(cloth, bv, bn, flat_clear=0.8)
    assert out[0, 2] >= 0.8 - 1e-3, out                # still pushed to flat_clear
