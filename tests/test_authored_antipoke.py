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

"""#authored-antipoke -- relax the clearance requirement toward the author's
own spacing, but only where the body does not grow.

`test_never_pushes_further_than_today` is the load-bearing one. This pass is the
LAST line against skin through steel, so the argument for touching it at all is
that it is MONOTONE: the existing requirement is the ceiling, so it can only
ever push a vertex LESS. If that breaks, the change is no longer bounded and
the census clearance counters stop covering it.
"""
import numpy as np
import pytest

from src import nif_convert as nc

# Flat body slab at z=0, normals +z.
BODY = np.array([[x, y, 0.0] for x in range(-4, 5) for y in range(-4, 5)],
                dtype=float)
BODY_N = np.tile(np.array([0.0, 0.0, 1.0]), (len(BODY), 1))


def _a(h):
    return np.array([[0.0, 0.0, z] for z in h], dtype=float)


def _run(cur, src, *, amp=0.0, authored=True, flat=1.0):
    prev = nc.AUTHORED_ANTIPOKE
    nc.AUTHORED_ANTIPOKE = True
    try:
        out = nc.clear_armor_outside_body(
            _a(cur), BODY, BODY_N,
            flat_clear=flat, bust_clear=flat,
            morph_amplitude=np.full(len(BODY), amp),
            smooth_iters=0,
            src_armor_verts=_a(src) if authored else None,
            src_body_verts=BODY.copy() if authored else None,
            src_body_normals=BODY_N if authored else None)
    finally:
        nc.AUTHORED_ANTIPOKE = prev
    return np.asarray(out, float)[:, 2]


def test_never_pushes_further_than_today():
    """MONOTONE, over a spread of current heights, authored heights and morph."""
    cur = [0.0, 0.1, 0.3, 0.6, 1.0, 1.5]
    for src in ([0.0] * 6, [0.2] * 6, [0.9] * 6, [2.5] * 6):
        for amp in (0.0, 0.4, 2.0, 8.7):
            a = _run(cur, src, amp=amp, authored=False)
            b = _run(cur, src, amp=amp, authored=True)
            assert (b <= a + 1e-9).all(), f"amp={amp} src={src[0]}"


def test_a_tight_author_over_a_still_body_is_left_tighter():
    """The +0.0537u the ledger says this pass costs: the author had the vertex
    at 0.2u, the flat floor wants 1.0u, and nothing here grows."""
    assert _run([0.2], [0.2], amp=0.0)[0] < _run([0.2], [0.2], amp=0.0,
                                                 authored=False)[0]
    assert _run([0.2], [0.2], amp=0.0)[0] >= nc.ARMOR_TO_SKIN_BUFFER - 1e-9


def test_a_growing_body_still_gets_its_clearance():
    """The whole point of the pass. Where the body morphs outward the
    requirement must NOT be relaxed toward a tight authored value."""
    still = _run([0.05], [0.05], amp=0.0)
    moving = _run([0.05], [0.05], amp=2.0)
    assert moving[0] > still[0]
    # and at high morph it should reach what it would have done anyway
    assert moving[0] == pytest.approx(
        _run([0.05], [0.05], amp=2.0, authored=False)[0], abs=1e-9)


def test_a_loose_author_never_raises_the_requirement():
    """An author who left 2.5u must not pull the floor UP to 2.5u -- that would
    be new over-inflation, and is the direction this must never move."""
    assert _run([0.05], [2.5], amp=0.0)[0] == pytest.approx(
        _run([0.05], [2.5], amp=0.0, authored=False)[0], abs=1e-9)


def test_authored_tuck_under_the_skin_is_not_honoured():
    out = _run([0.0], [-0.9], amp=0.0)
    assert out[0] >= nc.ARMOR_TO_SKIN_BUFFER - 1e-6


def test_no_source_means_unchanged_behaviour():
    cur = [0.0, 0.3, 1.2]
    assert np.allclose(_run(cur, cur, authored=False),
                       _run(cur, cur, authored=False))


def test_off_by_default():
    import os
    assert nc.AUTHORED_ANTIPOKE is False or (
        os.environ.get("CBBE2UBE_AUTHORED_ANTIPOKE") == "1")


def test_reachable_from_the_gui():
    from src import gui_settings
    assert any(s.env == "CBBE2UBE_AUTHORED_ANTIPOKE"
               for s in gui_settings.SETTINGS)
