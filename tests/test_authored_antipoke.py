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
from src import nif_convert_fitgeom as fg

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
    requirement must scale with the growth, not collapse to a tight authored
    value.

    THE SECOND ASSERTION CHANGED 2026-09-05 (`#authored-floor-same-rule`). It
    used to require the relaxed result to equal the UNRELAXED one at high morph
    -- i.e. that the authored floor be exactly inert wherever the body grows.
    That is not a safety property, it is the defect: the bust and belly are the
    only bands with real amplitude, so "inert where the body grows" meant inert
    everywhere it was aimed, and the pack shipped the breast 0.49u further off
    than the author with the flag doing nothing about it.

    The contract now splits the requirement in two: the MORPH-DRIVEN part
    (`ARMOR_TO_SKIN_BUFFER + MORPH_FACTOR * amp`) is non-negotiable and still
    scales with growth, while the STATIC part is the author's to decide. So a
    growing body keeps its headroom and a tight author is still honoured."""
    still = _run([0.05], [0.05], amp=0.0)
    moving = _run([0.05], [0.05], amp=2.0)
    assert moving[0] > still[0]
    # The morph-driven headroom survives in full ...
    assert moving[0] == pytest.approx(
        nc.ARMOR_TO_SKIN_BUFFER + nc.ADAPTIVE_CLEARANCE_MORPH_FACTOR * 2.0,
        abs=1e-6)
    # ... and it is still MONOTONE against the unrelaxed pass, which is the
    # safety argument for touching the last line against skin through steel.
    assert moving[0] <= _run([0.05], [0.05], amp=2.0, authored=False)[0] + 1e-9
    # More growth, more clearance -- the relaxation does not flatten the ramp.
    assert _run([0.05], [0.05], amp=4.0)[0] > moving[0]


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


def test_off_by_default_and_PAIRED_with_the_inflate_floor():
    """See that flag's twin test for why the two may only move together. This
    half is the one that reaches the body-swap path at all, and also the one
    carrying the whole surface cost (+440 folds against the inflate half's +28)
    that sent both defaults back off the day they were promoted."""
    import os
    env = {k: os.environ.get("CBBE2UBE_AUTHORED_" + k.upper())
           for k in ("inflate", "antipoke")}
    if any(v is not None for v in env.values()):
        pytest.skip("an env override is in play: %r" % env)
    assert nc.AUTHORED_ANTIPOKE is False
    assert nc.AUTHORED_INFLATE is nc.AUTHORED_ANTIPOKE


def test_reachable_from_the_gui():
    from src import gui_settings
    assert any(s.env == "CBBE2UBE_AUTHORED_ANTIPOKE"
               for s in gui_settings.SETTINGS)


# --- #authored-keeps-the-bust-floor -------------------------------------------

def test_the_authored_relaxation_may_not_cut_the_bust_ramp():
    """REPORTED IN GAME: nipples through a leather cuirass "by the smallest
    amount" after the authored floors were armed.

    The bust ramp is applied as a FLOOR precisely so later clearance logic "can
    never take the bust below what the fixed path guaranteed" (its own comment).
    The authored relaxation ran one block later and did exactly that.

    At the nipple the relaxation's premise fails: the author fitted over a CBBE
    bust and UBE's protrudes further, so their spacing under-provisions ours by a
    fixed amount. `amp_room` does not cover it -- that reserves headroom for the
    body's MORPH amplitude, and this is a STATIC difference between two bodies.
    """
    import inspect
    src = inspect.getsource(fg.clear_armor_outside_body)
    i = src.index("#authored-antipoke")
    tail = src[i:]
    assert "_bust_req" in tail and "_in_bust" in tail, (
        "the authored relaxation no longer honours the bust ramp -- it can cut "
        "nipple clearance again")
    # The guard must come BEFORE the clamp it protects, or it does nothing.
    assert tail.index("np.maximum(floor, _bust_req") < tail.index(
        "req = np.minimum(req, np.maximum(floor, worst))")


def test_the_bust_floor_is_captured_on_both_branches():
    """Adaptive and fixed both compute the ramp; if only one publishes it, the
    other silently loses the guard and the defect returns on those shapes."""
    import inspect
    src = inspect.getsource(fg.clear_armor_outside_body)
    assert src.count("_bust_req, _in_bust = bust_req, in_bust") == 2


# --- #authored-nipple-exempt --------------------------------------------------

def test_the_tip_is_exempt_from_the_authored_relaxation():
    """The author fitted over a CBBE bust; UBE's protrudes further, so their
    spacing under-provisions ours at the tip by a fixed amount however careful
    they were -- and `amp_room` cannot cover it, because that reserves headroom
    for the body's MORPH amplitude and this is a STATIC difference between two
    bodies.

    Measured over the 36 pack pieces covering the nipple: tip clearance p50
    1.193u (old build) -> 1.085u with the floors armed -> 1.220u with this.
    """
    import inspect
    src = inspect.getsource(fg.clear_armor_outside_body)
    i = src.index("#authored-antipoke")
    tail = src[i:]
    assert "_nipw" in tail, "the anti-poke floor no longer exempts the tip"
    assert "np.where(_nipw > 0.5, req," in tail, (
        "the exemption must keep the UNRELAXED req on the tip")


def test_the_tip_mask_is_calibrated_on_the_body_not_the_shape():
    """A garment covering only the flat chest has a local nipple maximum near
    zero; a fraction of THAT would exempt the whole piece. The existing
    `#nipple-ramp-sharpness` code records the same trap."""
    import inspect
    src = inspect.getsource(nc._nipple_tip_mask)
    assert "nw.max()" in src
    assert "wmax <= 0.2" in src, (
        "a flat panel's noise must not be read as a nipple map")


def test_the_tip_mask_is_a_radius_then_a_topology_spread():
    """Both weaker forms were measured. Nearest-vertex selected 1 garment vert
    of 1700 because a garment is coarser than the body; adding the pass's 6
    nearest body verts reached only 3. And dropping the topology spread is not
    just weaker but DANGEROUS -- bust-band penetration went to 465 against 12,
    because the requirement field becomes spiky rather than gentler."""
    import inspect
    src = inspect.getsource(nc._nipple_tip_mask)
    assert "cKDTree" in src and "radius" in src, "the radius test is gone"
    assert "rings" in src and "mask[t]" in src or "w[t]" in src, (
        "the topology spread is gone -- the triangle covering the nipple has "
        "all three corners outside any sensible radius")


def test_a_ramped_exemption_was_measured_and_rejected():
    """Kept as a note because it is the obvious next idea: a weight ramping from
    1 at the tip to 0 at `radius` scores WORSE on the thing being protected
    (tip clearance p05 0.439u vs 0.454u hard, old build 0.451u)."""
    import inspect
    src = inspect.getsource(nc._nipple_tip_mask)
    assert "MEASURED AND REJECTED" in src


def test_both_floors_exempt_the_tip_or_neither_is_safe():
    """They cost opposite ends of the distribution -- the anti-poke floor takes
    the MEDIAN (1.205 -> 1.107), the inflate floor takes the TAIL (p05 0.466 ->
    0.421). Exempting only one leaves the tip losing one end."""
    import inspect
    infl = inspect.getsource(nc.inflate_armor_outward)
    assert "#authored-nipple-exempt" in infl
    assert "body_nipple" in infl
    assert "AUTHORED_NIPPLE_EXEMPT" in infl


def test_inflate_without_a_nipple_map_is_todays_behaviour():
    """`_fit_shapes_copy` has no nipple map in scope, so the no-map path is real
    and must be exactly the old floor rather than a half-guard."""
    import inspect
    infl = inspect.getsource(nc.inflate_armor_outward)
    assert "_relaxed if _tip is None" in infl


def test_the_exemption_fraction_default():
    """Resolve from the CODE. 0.5 measured best on the population for BOTH the
    bust gap and the surface -- the response is not monotonic in this knob."""
    assert nc.AUTHORED_NIPPLE_EXEMPT == 0.5
