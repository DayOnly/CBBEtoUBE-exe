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

"""#back-morph-residual: the upper back must be charged the morph residual that
the bust already gets, and nothing else may move.

Every shipped clearance pass measures the BIND-POSE reference body. The only
morph-aware one is `#bust-morph-residual`, and it is gated on nipple weight, so
the upper back -- which is where the body grows most on large presets and where
the user sees skin through the shoulder blades -- was charged NOTHING.
`minimum_push`, the other measurement-driven push, is fenced to the front and
sides and never reaches it either.

Measured over 6 golden pieces x 4 presets with the body-occlusion-gated metric,
against a shared OFF baseline:

    back clipping  71.1 -> 8.0 mean,  net -1513 verts,  0 back regressions
    front          48 pairs: 11 worse, 7 better, 30 unchanged,  NET -3 verts

So the front is NOT a trade-off at the default band floor. Counting regressions
alone was the wrong criterion -- it scores +1 the same as +66 and discards every
improvement.

The defaults pinned below are each a MEASURED choice, not a preference; the tests
that pin them say what the alternative scored.
"""
import numpy as np
import pytest

from src import nif_convert as nc

BACK_Y = -6.0
FRONT_Y = 0.0


def _scene(n_back=8, back_z=(96.0, 110.0), with_bust=True):
    """A front wall (normals +Y, inside the bust band) and a back wall (normals
    -Y, inside the back band), each with a garment sheet 0.3u clear of it.

    The back grid must clear `BACK_MIN_VERTS` (24). Only about HALF of it can
    carry a residual -- `_stack_for` bumps alternate verts, and a vert whose own
    nearest body vert is the bumped one sees a NEGATIVE residual, clipped to
    zero. A 5x8 grid yields 20 qualifying verts, which is under the floor: the
    charge is then correctly skipped and every assertion about it passes
    vacuously. 7 columns puts it clear.

    Returns (body_verts, body_normals, cloth, back_mask_on_cloth).
    """
    xs = np.linspace(-4.0, 4.0, 7)
    bz = np.linspace(86.0, 98.0, 5)                 # bust band is (84, 100)
    front = np.array([(x, FRONT_Y, z) for x in xs for z in bz])
    fn = np.tile([0.0, 1.0, 0.0], (len(front), 1))
    kz = np.linspace(back_z[0], back_z[1], n_back)
    back = np.array([(x, BACK_Y, z) for x in xs for z in kz])
    bn = np.tile([0.0, -1.0, 0.0], (len(back), 1))
    if not with_bust:
        front, fn = front[:0], fn[:0]
    bv = np.vstack([front, back]) if len(front) else back
    bnorm = np.vstack([fn, bn]) if len(fn) else bn

    # garment sits 0.3u outside each wall, along that wall's outward normal
    gf = front + np.array([0.0, 0.3, 0.0]) if len(front) else front
    gb = back + np.array([0.0, -0.3, 0.0])
    cloth = np.vstack([gf, gb]) if len(gf) else gb
    mask = np.zeros(len(cloth), bool)
    mask[len(gf):] = True
    return (bv.astype(np.float64), bnorm.astype(np.float64),
            cloth.astype(np.float32), mask)


def _stack_for(bv, bump=1.0):
    """One synthetic slider that carries the back wall OUTWARD (-Y) by `bump`,
    except at every OTHER vertex.

    The residual is `(delta[neighbour] - delta[nearest]) . nrm0` maximised over
    neighbours: zero when a slider merely inflates (both move alike), positive
    when it reshapes. Alternating the bump guarantees some neighbour outgrows the
    vert covering it, which is exactly the geometry the charge exists for.
    """
    d = np.zeros((1, len(bv), 3))
    rear = bv[:, 1] < (BACK_Y + FRONT_Y) / 2.0
    idx = np.flatnonzero(rear)[::2]
    d[0, idx, 1] = -float(bump)
    return d.astype(np.float32)


def _run(monkeypatch, bv, bnorm, cloth, *, on, stack, call=None, **over):
    monkeypatch.setattr(nc, "BACK_MORPH_RESIDUAL", bool(on))
    monkeypatch.setattr(nc, "_find_ube_body_osd", lambda: "synthetic.osd")
    monkeypatch.setattr(nc, "_cached_body_morph_stack",
                        lambda _p, _n: stack)
    for k, v in over.items():
        monkeypatch.setattr(nc, k, v)
    return np.asarray(nc.conform_to_source_standoff(
        cloth, bv, bnorm, cloth, bv, bnorm, **(call or {})), np.float64)


def _wrap(zs=(96.0, 99.0), nz=6, ntheta=16, r=6.0):
    """A torso cross-section that WRAPS from rear-facing to front-facing at bust
    height, plus its triangulation.

    This is the shoulder geometry the band interaction actually lives on: every
    vert is inside the bust band (84-100), while only the rear-facing ones are
    inside the back band, so triangles STRADDLE the boundary. A flat two-wall
    scene cannot produce a straddling triangle at all.
    """
    th = np.linspace(-np.pi / 2, np.pi / 2, ntheta)
    zz = np.linspace(zs[0], zs[1], nz)
    pos, nrm = [], []
    for z in zz:
        for t in th:
            n = np.array([np.sin(t), -np.cos(t), 0.0])
            pos.append(np.array([r * np.sin(t), -r * np.cos(t), z]))
            nrm.append(n)
    bv = np.asarray(pos, np.float64)
    bn = np.asarray(nrm, np.float64)
    cloth = (bv + 0.3 * bn).astype(np.float32)
    tris = []
    for i in range(nz - 1):
        for j in range(ntheta - 1):
            a = i * ntheta + j
            tris.append((a, a + 1, a + ntheta))
            tris.append((a + 1, a + ntheta + 1, a + ntheta))
    return bv, bn, cloth, np.asarray(tris, np.int64)


def _outward(before, after, mask):
    """Signed movement along each wall's OUTWARD normal (-Y at the back)."""
    return -(after[mask][:, 1] - before[mask][:, 1])


def test_back_charge_pushes_the_back_out_and_leaves_the_front_alone(monkeypatch):
    bv, bnorm, cloth, back = _scene()
    st = _stack_for(bv)
    off = _run(monkeypatch, bv, bnorm, cloth, on=False, stack=st)
    on = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st)

    # CONTROL: the charge must actually have fired. Without this every
    # "unchanged" assertion below passes vacuously on a pass that never ran.
    moved = np.abs(on - off).max(axis=1)
    assert moved.max() > 0.05, "back charge never fired; the rest is vacuous"
    assert int((moved > 1e-9).sum()) >= nc.BACK_MIN_VERTS

    assert np.all(moved[~back] < 1e-9), "the charge moved a front vert"
    assert _outward(cloth, on, back).max() > _outward(cloth, off, back).max()


def test_charge_is_capped_by_back_move_max(monkeypatch):
    """A runaway push is the failure this cap exists for: an uncapped charge put
    3.14u of travel against a 0.5u residual and manufactured 16 upper-chest
    clipping verts from zero.

    THE CAP ONLY BINDS ON AN INTERSECTING GARMENT. `_deficit` is
    `req_back - worst`, and `req_back` is already limited by
    BACK_MORPH_RESIDUAL_MAX, so on a garment standing clear of the body the
    deficit is ~0.5u and BACK_MOVE_MAX (0.8u) never engages -- a version of this
    test using a clear garment PASSED with the cap deleted outright. `worst` has
    no lower bound, so the runaway needs the body INSIDE the cloth, which is
    exactly the bind-pose intersection the constant's comment describes."""
    xs = np.linspace(-4.0, 4.0, 7)
    kz = np.linspace(96.0, 110.0, 8)
    back = np.array([(x, BACK_Y, z) for x in xs for z in kz])
    bv = back.astype(np.float64)
    bnorm = np.tile([0.0, -1.0, 0.0], (len(bv), 1)).astype(np.float64)
    # cloth 2u on the BODY side of the wall: clearance along the normal is -2u
    cloth = (back + np.array([0.0, 2.0, 0.0])).astype(np.float32)
    mask = np.ones(len(cloth), bool)
    st = _stack_for(bv)

    off = _run(monkeypatch, bv, bnorm, cloth, on=False, stack=st)
    on = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st)
    gain = _outward(cloth, on, mask) - _outward(cloth, off, mask)
    assert gain.max() > nc.BACK_MOVE_MAX / 2.0, (
        "control: the intersecting scene did not drive the charge to the cap, "
        "so this asserts nothing about BACK_MOVE_MAX")
    assert gain.max() <= nc.BACK_MOVE_MAX + 1e-6, (
        f"charge exceeded BACK_MOVE_MAX: {gain.max():.3f}u")


def test_vert_floor_declines_a_piece_with_little_to_gain(monkeypatch):
    """Conditional by construction, like `minimum_push`. A piece with nothing to
    gain must exit having moved ZERO verts -- that is what keeps the cost off the
    majority."""
    bv, bnorm, cloth, _ = _scene(n_back=1)          # 5 in-band verts, under 24
    st = _stack_for(bv)
    off = _run(monkeypatch, bv, bnorm, cloth, on=False, stack=st)
    on = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st)
    assert np.allclose(on, off), "charge fired under the vert floor"

    # CONTROL: the same scene above the floor DOES move, so the assertion above
    # is about the floor and not about a scene that could never charge.
    bv2, bn2, cl2, _ = _scene(n_back=8)
    st2 = _stack_for(bv2)
    a = _run(monkeypatch, bv2, bn2, cl2, on=False, stack=st2)
    b = _run(monkeypatch, bv2, bn2, cl2, on=True, stack=st2)
    assert not np.allclose(a, b)


def test_charge_is_unreachable_above_the_bust_band(monkeypatch):
    """PINS A REAL LIMITATION, not a desired behaviour.

    The whole block lives inside `if np.any(in_bust)`, and `in_bust` is a HEIGHT
    test with no facing test -- `body_z` in (84, 100). So the gate is not "has
    bust coverage": a back-only garment reaching down to z 100 satisfies it from
    its own rear verts, and the charge fires normally. What CANNOT be reached is
    a shape whose coverage sits entirely ABOVE z 100, even though the back band
    runs to 112. Instrumented on the golden set, several shapes report
    `in_bust 0 -> BACK CHARGE UNREACHABLE`.

    Note the interaction with the rejected `BACK_RESIDUAL_Z[0]=102`: that band
    lies wholly above the bust band, so a shape covering only the upper back
    could never be charged at all.

    If this starts failing because the nesting was fixed, that is an IMPROVEMENT
    -- update the test, do not restore the nesting."""
    bv, bnorm, cloth, _ = _scene(n_back=8, back_z=(101.0, 112.0),
                                 with_bust=False)
    st = _stack_for(bv)
    off = _run(monkeypatch, bv, bnorm, cloth, on=False, stack=st)
    on = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st)
    assert np.allclose(on, off)

    # CONTROL: the same back-only scene reaching down to 100 DOES charge, so the
    # assertion above is about the height gate and not about a scene that could
    # never charge for some other reason.
    bv2, bn2, cl2, _ = _scene(n_back=8, back_z=(96.0, 110.0), with_bust=False)
    st2 = _stack_for(bv2)
    a = _run(monkeypatch, bv2, bn2, cl2, on=False, stack=st2)
    b = _run(monkeypatch, bv2, bn2, cl2, on=True, stack=st2)
    assert not np.allclose(a, b)


def test_feather_interior_is_untouched_not_merely_equal(monkeypatch):
    """`move + 1.0*(raised - move)` is ALGEBRAICALLY `raised` and is NOT
    bit-identical to it, and this chain amplifies that: measured, 529 verts moved
    with a median of 0.0010u and a maximum of 0.2546u once the layer and
    anti-poke passes made discrete decisions on the perturbed input. The zone
    interior must take the original expression, not the blend evaluated at 1."""
    bv, bnorm, cloth, _ = _scene(n_back=8, back_z=(99.0, 107.0))
    st = _stack_for(bv)
    flat = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st,
                BACK_RESIDUAL_FEATHER=0.0)
    tiny = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st,
                BACK_RESIDUAL_FEATHER=1e-9, BACK_RESIDUAL_FEATHER_NY=1e-9,
                BACK_RESIDUAL_FEATHER_X=1e-9)
    assert np.array_equal(flat, tiny), (
        "a zero-width feather perturbed the zone interior")


def test_feather_reduces_the_charge_and_never_raises_it(monkeypatch):
    """The ramp may only LOWER the charge near the boundary; it must never extend
    the charge past `in_back` nor increase it anywhere."""
    bv, bnorm, cloth, back = _scene(n_back=8, back_z=(96.0, 110.0))
    st = _stack_for(bv)
    off = _run(monkeypatch, bv, bnorm, cloth, on=False, stack=st)
    flat = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st,
                BACK_RESIDUAL_FEATHER=0.0)
    feat = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st,
                BACK_RESIDUAL_FEATHER=4.0, BACK_RESIDUAL_FEATHER_NY=0.2,
                BACK_RESIDUAL_FEATHER_X=3.0)
    g_flat = _outward(cloth, flat, back) - _outward(cloth, off, back)
    g_feat = _outward(cloth, feat, back) - _outward(cloth, off, back)
    assert g_flat.max() > 0.05, "control: the flat arm produced no charge"
    assert np.all(g_feat <= g_flat + 1e-9), "feather RAISED the charge somewhere"
    assert np.all(np.abs(feat[~back] - off[~back]) < 1e-9), (
        "feather reached a vert outside the band")


def test_overlapping_bands_take_the_larger_charge_not_the_sum(monkeypatch):
    """The two bands OVERLAP: `in_bust` is z 84-100 with no facing test, and
    `in_back` is z 95-112 rear-facing, so z 95-100 on the back is in BOTH. The
    code claims such a vert 'takes the larger of the two, with only the back half
    bounded'. Nothing pinned that.

    A nipple-weighted bust requirement is used so the bust charge is large enough
    to be distinguishable from the back one -- otherwise both reduce to the same
    flat 0.3u and the test cannot tell max from sum.

    `BUST_MORPH_RESIDUAL` is switched OFF here to isolate the interaction. Left
    on, the same synthetic slider stack also inflates the BUST requirement, and
    the bust half is uncapped BY DESIGN -- the overlap vert then legitimately
    reaches 1.6u, which looks exactly like a cap failure and is not one.

    This is also the regression test for `req_back = req.copy()`. If the back
    requirement were written into `req` itself, the BUST line below it would
    apply the inflated value UNCAPPED, and the overlap vert would sail past
    BACK_MOVE_MAX."""
    xs = np.linspace(-4.0, 4.0, 7)
    kz = np.linspace(95.5, 100.0, 8)                # inside BOTH bands
    body = np.array([(x, BACK_Y, z) for x in xs for z in kz])
    bv = body.astype(np.float64)
    bnorm = np.tile([0.0, -1.0, 0.0], (len(bv), 1)).astype(np.float64)
    # Start the cloth WELL INSIDE the bust requirement so the bust charge has
    # room to fire. Derived from the constant rather than hardcoded: this was a
    # flat 0.3, comfortably under the old 0.9 ceiling, and when that ceiling
    # moved to 0.3 the cloth started exactly AT the requirement -- the charge
    # measured zero and the control below correctly aborted the test as vacuous.
    cloth = (body + np.array([0.0, -0.3 * nc.CONFORM_BUST_CLEARANCE, 0.0])
             ).astype(np.float32)
    mask = np.ones(len(cloth), bool)
    nip = np.ones(len(bv))                          # full bust requirement
    st = _stack_for(bv)

    off = _run(monkeypatch, bv, bnorm, cloth, on=False, stack=st,
               call={"ube_body_nipple": nip}, BUST_MORPH_RESIDUAL=False)
    on = _run(monkeypatch, bv, bnorm, cloth, on=True, stack=st,
              call={"ube_body_nipple": nip}, BUST_MORPH_RESIDUAL=False)
    g_bust = _outward(cloth, off, mask)
    g_both = _outward(cloth, on, mask)

    assert g_bust.max() > 0.05, (
        "control: the bust charge did not fire, so 'larger of the two' is "
        "vacuous here")
    assert np.all(g_both >= g_bust - 1e-9), "the back charge LOWERED the bust"
    assert g_both.max() <= nc.BACK_MOVE_MAX + 1e-6, (
        f"overlap vert exceeded BACK_MOVE_MAX ({g_both.max():.3f}u) -- the back "
        f"requirement leaked into the uncapped bust push")
    assert g_both.max() < g_bust.max() + nc.BACK_MOVE_MAX - 1e-6, (
        "overlap looks ADDITIVE; it must take the larger of the two")


def test_back_requirement_never_reaches_the_bust_surface_pass(monkeypatch):
    """`_surface_deficit` evaluates the requirement per garment TRIANGLE and
    scatters the result to all three corners. On the shoulder the garment wraps
    rear-to-front, so a triangle owning ONE back-band vertex also owns front
    corners -- and if it read a requirement inflated by the back charge it would
    push them out. That is not hypothetical: sharing the array cost
    `hide-collider` 15-17 upper-chest clipping verts FROM ZERO on every preset,
    at 3.14u of travel that capping the back push did not budge.

    Runs with BUST_SURFACE_REQ ON, on wrapping geometry where straddling
    triangles actually exist, and asserts the front-facing corners are untouched.
    """
    bv, bn, cloth, tris = _wrap()
    st = _stack_for(bv)
    front = bn[:, 1] > nc.BACK_RESIDUAL_NY          # outside the back band

    kw = {"tris": tris, "ube_body_nipple": np.zeros(len(bv))}
    off = _run(monkeypatch, bv, bn, cloth, on=False, stack=st, call=kw,
               BUST_SURFACE_REQ=True)
    on = _run(monkeypatch, bv, bn, cloth, on=True, stack=st, call=kw,
              BUST_SURFACE_REQ=True)

    # CONTROL: the charge must have reached the rear of this scene, or the
    # assertion about the front is about a pass that never ran.
    assert np.abs(on[~front] - off[~front]).max() > 0.05, (
        "control: the back charge never fired on the wrapping scene")
    assert front.sum() >= 4, "control: no front-facing verts in the scene"
    assert np.abs(on[front] - off[front]).max() < 1e-9, (
        "the back requirement reached a front-facing vert through a straddling "
        "triangle")


def test_defaults_are_the_measured_configuration():
    """Each of these lost a same-run A/B. Reverting one silently re-ships a
    configuration that was already rejected with numbers."""
    # Band floor. 102 was chosen on Body3F ALONE and is the bad config: on the
    # golden set it scored back 16.1 (vs 8.0) and net front +254 verts (vs -3),
    # with robes-thalmor upper chest 53 -> 119 on Punk UBE.
    assert nc.BACK_RESIDUAL_Z[0] == 95.0
    assert nc.BACK_RESIDUAL_Z[1] == 112.0
    # Feathering the band edge: back 8.0 -> 10.8, front 11 -> 12 regressions.
    assert nc.BACK_RESIDUAL_FEATHER == 0.0
    # Bounding the edit: travel 4.03u -> 1.67u but front 11 -> 18 regressions.
    assert nc.BACK_BOUND_EDIT is False
    assert nc.BACK_MIN_VERTS == 24
    # The cap was measured at 0.8, and it must STAY 0.8. It used to be written
    # as `BUST_FLAT_CLEARANCE + BACK_MORPH_RESIDUAL_MAX`, which quietly made a
    # BACK number follow a BUST knob: retuning the bust floor to 0.12 for a
    # chest defect dragged this to 0.62 with nothing re-measured, and then broke
    # the overlap test outright when the bust ceiling went back to 0.9. The back
    # now carries its own base, so this pins the measured value AND the fact
    # that a bust retune can no longer reach it.
    assert nc.BACK_MOVE_MAX == pytest.approx(
        nc.BACK_MOVE_BASE_CLEARANCE + nc.BACK_MORPH_RESIDUAL_MAX)
    assert nc.BACK_MOVE_MAX == pytest.approx(0.8)


def test_diagnostics_are_off_by_default_and_do_not_print():
    """The dumps are diagnostic scaffolding; a shipping run must not pay for them
    or write files. `BACK_DEBUG_LOG` (a log PATH, still live) exists because
    `golden_output._convert` runs the worker under `redirect_stdout`, which
    silently discards anything a pass prints."""
    assert nc.BACK_DUMP_DISP == ""
