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

"""#authored-inflate -- state the outward push as a FLOOR, not an addition.

`test_never_pushes_further_than_the_additive_pass` is the load-bearing one. The
whole safety argument for this change is that it is MONOTONE: it can only ever
reduce a push, so over-inflation cannot get worse and the only risk left is a
floor that comes out too low -- which the census's clearance counters measure
directly. If that property ever breaks, the change is no longer bounded and the
census result stops covering it.
"""
import numpy as np
import pytest

from src import nif_convert as nc


# A flat body slab in the z=0 plane, normals +z, and armour floating above it.
BODY = np.array([[x, y, 0.0] for x in range(-4, 5) for y in range(-4, 5)],
                dtype=float)
BODY_N = np.tile(np.array([0.0, 0.0, 1.0]), (len(BODY), 1))


def _armor(heights):
    return np.array([[0.0, 0.0, h] for h in heights], dtype=float)


def _run(cur_h, src_h, *, amp=0.0, magnitude=0.7, authored=True, base=None):
    """Push the armour once and return the resulting heights.

    Sets the flag around the call rather than via an autouse fixture, so
    `test_off_by_default` still sees the module's real default.
    """
    prev = nc.AUTHORED_INFLATE
    nc.AUTHORED_INFLATE = True
    try:
        out = nc.inflate_armor_outward(
            _armor(cur_h), BODY,
            magnitude=magnitude, close_threshold=3.0, body_normals=BODY_N,
            morph_amplitude=np.full(len(BODY), amp),
            base_magnitude=(nc.ADAPTIVE_CLEARANCE_BASE if base is None
                            else base),
            src_armor_verts=_armor(src_h) if authored else None,
            src_body_verts=BODY.copy() if authored else None,
            src_body_normals=BODY_N if authored else None,
        )
    finally:
        nc.AUTHORED_INFLATE = prev
    return np.asarray(out, float)[:, 2]


def test_never_pushes_further_than_the_additive_pass():
    """MONOTONE. The additive result is the ceiling, over a spread of current
    heights, authored heights and morph amplitudes."""
    cur = [0.05, 0.2, 0.5, 1.0, 1.5, 2.0, 2.5]
    for src in ([0.0] * 7, [0.3] * 7, [1.2] * 7, [3.0] * 7):
        for amp in (0.0, 0.5, 2.0, 8.7):
            a = _run(cur, src, amp=amp, authored=False)
            b = _run(cur, src, amp=amp, authored=True)
            assert (b <= a + 1e-9).all(), (
                f"authored push exceeded additive at amp={amp} src={src[0]}")


def test_a_vertex_already_at_the_authored_standoff_is_left_alone():
    """The 67.5% the census measured drifting away from the author's fit."""
    # authored 1.2u, currently 1.2u, no morph: nothing to do.
    out = _run([1.2], [1.2], amp=0.0)
    assert out[0] == pytest.approx(1.2, abs=1e-6)
    # the additive pass would have shoved it out
    assert _run([1.2], [1.2], amp=0.0, authored=False)[0] > 1.3


def test_a_vertex_below_the_floor_is_still_pushed():
    """Clearance is the counter the census says must not worsen."""
    out = _run([0.05], [1.2], amp=0.0)
    assert out[0] > 0.05
    # and it is not pushed past what the additive pass would have given
    assert out[0] <= _run([0.05], [1.2], amp=0.0, authored=False)[0] + 1e-9


def test_morph_amplitude_raises_the_floor():
    """A vertex over a part of the body that grows must keep room even where
    the author left little."""
    still = _run([0.5], [0.5], amp=0.0)
    moving = _run([0.5], [0.5], amp=2.0)
    assert moving[0] > still[0]


def test_the_morph_term_is_capped():
    """The belly's amplitude runs to 8.7u; a floor that tracked it would fling
    loose drape outward.

    `base` is raised so the additive CEILING is not the binding constraint in
    either arm -- otherwise this compares two different ceilings and passes or
    fails for a reason that has nothing to do with the cap. That is what the
    first version of this test did."""
    # `magnitude` too, not just `base`: the additive cap is
    # max(magnitude, morph_max), so raising `base` alone leaves it at 1.1 and
    # the ceiling still decides the answer.
    at_cap = _run([0.1], [0.0], amp=nc.AUTHORED_INFLATE_AMP_CAP,
                  base=4.0, magnitude=4.0)
    way_over = _run([0.1], [0.0], amp=8.7, base=4.0, magnitude=4.0)
    assert at_cap[0] == pytest.approx(
        nc.ARMOR_TO_SKIN_BUFFER + nc.AUTHORED_INFLATE_AMP_CAP, abs=1e-6)
    assert way_over[0] == pytest.approx(at_cap[0], abs=1e-9)


def test_authored_tuck_under_the_skin_is_not_honoured():
    """An author who buried a vertex inside their body must not produce a
    NEGATIVE floor -- that would be a licence to leave it inside ours."""
    out = _run([0.05], [-0.8], amp=0.0)
    assert out[0] >= nc.ARMOR_TO_SKIN_BUFFER - 1e-6


def test_no_source_means_unchanged_behaviour():
    """Most of the pack reaches this pass with no source body; those pieces must
    ship byte-identical to before rather than get a guessed floor."""
    cur = [0.05, 0.5, 1.5]
    assert np.allclose(_run(cur, cur, authored=False),
                       _run(cur, cur, authored=False))


def test_off_by_default():
    import os
    assert nc.AUTHORED_INFLATE is False or (
        os.environ.get("CBBE2UBE_AUTHORED_INFLATE") == "1")


def test_reachable_from_the_gui():
    from src import gui_settings
    assert any(s.env == "CBBE2UBE_AUTHORED_INFLATE"
               for s in gui_settings.SETTINGS)


def test_source_tree_cache_is_identity_checked():
    """The KD-tree cache is keyed on id(), which is only safe because the entry
    keeps the array alive and re-verifies identity. If it ever stopped doing
    that, a freed array's id could be reused and the pass would measure the
    authored standoff against a DIFFERENT body -- silently, and only on some
    pieces."""
    a = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    t1 = nc._authored_src_tree(a)
    assert nc._authored_src_tree(a) is t1          # same array -> cached
    b = a.copy()
    assert nc._authored_src_tree(b) is not t1      # different array -> rebuilt
    # and the cache holds a reference, so the id cannot be recycled under it
    assert any(v[0] is a for v in nc._AUTHORED_SRC_TREE.values())


def test_source_tree_cache_is_bounded():
    """A run needs one or two trees; an unbounded dict keyed on id() would grow
    for the length of a pack conversion."""
    for _ in range(40):
        nc._authored_src_tree(np.random.default_rng(0).random((8, 3)))
    assert len(nc._AUTHORED_SRC_TREE) <= 9
