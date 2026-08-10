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

"""#clearance-differential: adaptive clearance must pay for what the body
RESHAPES, not for how far it GROWS.

`clear_armor_outside_body` drops ANTIPOKE_FLAT_CLEAR whenever an amplitude map
exists and uses `BASE + FACTOR * amp`, where amp is a vertex's own outward
growth. Clipping is caused by the differential -- how much a neighbour outgrows
the point covering it -- because a garment vert carries the delta of the body
point it hugs. Measured on the UBE body, amp vs differential need:

    zone            amp p50   adaptive grants   differential asks (p90)
    back band          0.02             0.26u                    0.385u  SHORT
    rear 80-95         0.23             0.31u                    0.354u  SHORT
    breast             3.69             1.05u                    0.569u  covers

Pearson r between amp and need: +0.088 on the back, -0.413 on the breast. The
proxy is uncorrelated where it matters and negatively correlated on the front;
the front is protected by accident, not by design.
"""
import numpy as np
import pytest

from src import nif_convert as nc


def _wall(nx=7, nz=7, y=0.0):
    """A flat wall facing +Y, with its garment sheet 0.3u clear of it."""
    xs = np.linspace(-3.0, 3.0, nx)
    zs = np.linspace(88.0, 96.0, nz)
    bv = np.array([(x, y, z) for x in xs for z in zs], dtype=np.float64)
    bn = np.tile([0.0, 1.0, 0.0], (len(bv), 1)).astype(np.float64)
    cloth = (bv + np.array([0.0, 0.3, 0.0])).astype(np.float32)
    return bv, bn, cloth


def _stack_alternating(nv, bump=1.0):
    """One slider that carries every OTHER vert outward: a RESHAPE."""
    d = np.zeros((1, nv, 3))
    d[0, ::2, 1] = float(bump)
    return d.astype(np.float32)


def _stack_uniform(nv, vec=(0.4, 1.3, -0.7)):
    """One slider that translates the whole body: growth with no reshape."""
    return np.tile(np.asarray(vec, np.float32)[None, None, :], (1, nv, 1))


def _diff(monkeypatch, bv, bn, stack, path="synthetic.osd"):
    monkeypatch.setattr(nc, "_cached_body_morph_stack", lambda _p, _n: stack)
    nc._BODY_MORPH_DIFF_CACHE.clear()
    return nc._cached_body_morph_differential(path, bv, bn)


def test_uniform_slider_charges_nothing(monkeypatch):
    """The defining property. A slider that merely translates or inflates
    uniformly must charge ZERO: the deltas cancel and a hugging garment follows
    it for free. If this is non-zero the metric is measuring size, not reshape,
    and every zone is charged for whole-body growth it does not need."""
    bv, bn, _ = _wall()
    d = _diff(monkeypatch, bv, bn, _stack_uniform(len(bv)))
    assert d is not None
    assert float(np.abs(d).max()) < 1e-9

    # CONTROL: the same machinery DOES report a reshape, so the zero above is a
    # property of the slider and not of a function that always returns zero.
    r = _diff(monkeypatch, bv, bn, _stack_alternating(len(bv)))
    assert float(r.max()) > 0.5


def test_differential_is_positive_where_a_neighbour_outgrows_the_vert(monkeypatch):
    bv, bn, _ = _wall()
    d = _diff(monkeypatch, bv, bn, _stack_alternating(len(bv), bump=0.75))
    # every un-bumped vert has a bumped neighbour within the radius
    assert float(d.max()) == pytest.approx(0.75, abs=1e-6)
    assert float(d.min()) == 0.0            # a bumped vert is charged nothing


def test_cache_keys_on_vertex_count_not_path_alone(monkeypatch):
    """Regression for the shape of bug the sibling cache still has:
    `_BODY_MORPH_STACK_CACHE` keys on the OSD path ALONE while its value depends
    on `n_verts`, so a first call with a smaller count poisons every later one.
    """
    big_bv, big_bn, _ = _wall(nx=7, nz=7)
    small_bv, small_bn, _ = _wall(nx=4, nz=4)
    monkeypatch.setattr(nc, "_cached_body_morph_stack",
                        lambda _p, n: _stack_alternating(n, bump=0.6))
    nc._BODY_MORPH_DIFF_CACHE.clear()
    small = nc._cached_body_morph_differential("same.osd", small_bv, small_bn)
    big = nc._cached_body_morph_differential("same.osd", big_bv, big_bn)
    assert len(small) == len(small_bv)
    assert len(big) == len(big_bv), (
        "the small-body call poisoned the cache for the large body")
    assert float(big.max()) > 0.0


def _clear(bv, bn, cloth, *, amp, diff):
    return np.asarray(nc.clear_armor_outside_body(
        cloth, bv, bn, morph_amplitude=amp, morph_differential=diff),
        np.float64)


def test_differential_raises_clearance_where_amp_says_static(monkeypatch):
    """The back case: amp ~ 0 so adaptive grants only the 0.25u base, while the
    differential asks for more. This is the whole point of the change."""
    bv, bn, cloth = _wall()
    amp = np.zeros(len(bv))                       # body does not GROW here
    d = _diff(monkeypatch, bv, bn, _stack_alternating(len(bv), bump=0.4))
    assert float(d.max()) > 0.3, "control: no differential to charge"

    off = _clear(bv, bn, cloth, amp=amp, diff=None)
    on = _clear(bv, bn, cloth, amp=amp, diff=d)
    gain = (on - off)[:, 1]
    assert gain.max() > 0.1, "differential did not raise clearance"
    assert gain.min() >= -1e-9, "differential LOWERED clearance somewhere"


def test_it_is_a_monotone_floor_and_never_takes_room_away(monkeypatch):
    """Applied with np.maximum so a zone already served by the amp ramp keeps
    what it has. The breast is the case that matters: amp p50 3.69 grants 1.05u
    against a differential of 0.569u, so it must not move at all."""
    bv, bn, cloth = _wall()
    amp = np.full(len(bv), 6.0)                   # amp ramp saturates the cap
    d = _diff(monkeypatch, bv, bn, _stack_alternating(len(bv), bump=0.4))
    off = _clear(bv, bn, cloth, amp=amp, diff=None)
    on = _clear(bv, bn, cloth, amp=amp, diff=d)
    assert np.allclose(on, off), (
        "a zone the amp ramp already serves was disturbed")

    # CONTROL: with a LOW amp the same differential does move the result, so the
    # equality above is the floor being inert and not the arrays being ignored.
    lo = np.zeros(len(bv))
    assert not np.allclose(_clear(bv, bn, cloth, amp=lo, diff=d),
                           _clear(bv, bn, cloth, amp=lo, diff=None))


def test_flag_is_on_by_default():
    """ON since 2026-08-08, with #back-morph-residual. Golden six x 4 presets,
    96 piece/preset/region pairs, both enabled: 5 worse, 49 better, 42 unchanged,
    worst single regression +3 verts. Either alone is worse -- the charge alone
    leaves the upper chest untouched, and the differential alone regresses
    hide-collider's upper chest 0 -> 16 on every preset.

    `clear_armor_outside_body` still accepts the array unconditionally; the flag
    gates the CALLER, so the pass stays testable without touching global state."""
    assert nc.CLEARANCE_DIFFERENTIAL is True
    assert nc.BACK_MORPH_RESIDUAL is True
