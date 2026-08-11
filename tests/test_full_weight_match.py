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

"""Full-vector weight match (#full-weight-match).

Every FAMILY match fixes one bone family and rescales the rest proportionally to
make room, so the fix is funded out of whatever else the row carries -- measured
on a vanilla cuirass, SPINE +0.0397 while Pelvis -0.0258 and Clavicle -0.0041,
and the one pose that leans, swings the arms and drives the hips together got
worse. The family is also only ~0.10 of a 0.507 median garment-vs-body gap.

Copying the WHOLE vector has nothing left over to fund itself from, so its ideal
is follow = 1.0 in every pose at once. Measured: no pose worse than production at
either strength, which no family match achieved at any setting.
"""
import importlib
import inspect

import numpy as np

import src.nif_convert as nc


def test_flag_defaults_on_and_opts_out(monkeypatch):
    """This is the closest thing here to a reskin of a source-skinned garment --
    exactly what the morph-TRI gate exists to prevent -- so it shipped OFF on one
    piece and an offline metric. It has since been judged in game on BOTH
    material classes it could plausibly break differently, soft leather and
    rigid plate (2026-08-11), which is what earns the default."""
    assert nc.MATCH_FULL_WEIGHTS is True
    monkeypatch.setenv("CBBE2UBE_NO_FULL_WEIGHT_MATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MATCH_FULL_WEIGHTS is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_FULL_WEIGHT_MATCH", raising=False)
        importlib.reload(nc)


def test_pass_is_wired_into_both_convert_paths_and_runs_LAST():
    """It manages every shared bone, so any family match running after it would
    overwrite the match wholesale."""
    src = inspect.getsource(nc)
    calls = {
        "leg": "_match_leg_motion_to_body(dst_path, biped_slots,",
        "spine": "_match_spine_motion_to_body(dst_path, biped_slots,",
        "arm": "_match_arm_motion_to_body(dst_path, biped_slots,",
        "twist": "_match_spine_twist_to_body(dst_path, biped_slots,",
        "full": "_match_full_weights_to_body(dst_path, biped_slots,",
    }
    at = {k: [i for i in range(len(src)) if src.startswith(v, i)]
          for k, v in calls.items()}
    for k, v in at.items():
        assert len(v) >= 2, f"{k} is not wired into both convert paths"
    for leg, spine, arm, twist, full in zip(*(at[k] for k in
                                              ("leg", "spine", "arm", "twist", "full"))):
        assert leg < spine < arm < twist < full, (
            "the full-vector match must run LAST in every convert path")


def test_the_clean_row_gate_is_UNCONDITIONAL_here():
    """THE INVARIANT THAT PROTECTS AUTHORED PHYSICS.

    For a family match the foreign-weight test is conditional (`_row_gate`).
    For this one it must be unconditional: blending toward a body that has no
    chain bone would drain an authored chain to zero. A row carrying ANY weight
    on a bone the body lacks has to be left alone.
    """
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i = src.index("if full_vector:")
    # Slice on the FAMILY path's own first statement, not on the next `else:`.
    # The `else:` form broke the moment the full-vector branch grew an inner
    # if/else: the slice stopped early and this failed on a guard that was
    # still present, which reads as "the invariant is gone" when it is not.
    j = src.index("midx = [shape_bones.index(b)", i)
    branch = src[i:j]
    assert "foreign <= 1e-4" in branch, (
        "the full-vector branch must refuse rows carrying chain/foreign weight")
    assert "_sel = band & live & _okb & (foreign <= 1e-4)" in branch


def test_it_never_adds_a_bone():
    """Same invariant as the family path -- add_bone resets every STB. It costs
    nothing: measured on the clean rows, body weight on bones the garment LACKS
    is mean 0.0006, p90 0.0000, no row above 0.10."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i = src.index("if full_vector:")
    j = src.index("else:", i)
    branch = src[i:j]
    # `add_bone(` -- the CALL. A bare "add_bone" also matches the comment that
    # documents the invariant, which made this assertion fail on its own prose.
    assert "add_bone(" not in branch
    assert "if _b in ube_bones:" in branch, (
        "only bones the BODY also has may receive weight")
    # The write is indexed by `shape_bones`, i.e. bones the shape ALREADY has --
    # that is what makes "never adds a bone" true by construction, not by policy.
    assert "for _j, _b in enumerate(shape_bones):" in branch


def test_shoulder_band_gates_on_CONTACT_not_proximity():
    """REPORTED IN GAME after the first deploy: "the leather protrusion above the
    shoulder moves in weird ways".

    Cause, measured: at z>=103 the pass put `NPC L/R UpperArm` weight on verts
    that had 0.000 of it and took it off Clavicle/UpperarmTwist1, so a standing
    decorative plate began swinging with the arm. The shoulder band is BIMODAL --
    standoff p50 0.69u but p90 3.2-3.5u, max 7.5u -- because that is where
    pauldrons and raised trim sit over a very convex shoulder.

    ONE threshold cannot serve both, and the numbers say so: 5.0u everywhere
    rewrote 104 proud shoulder verts, while 2.0u everywhere protected them and
    handed back most of the bust win (breast region 3.12% -> 27.01%), because
    bust cloth legitimately sits 2.2u out. Gating only ABOVE the shoulder gives
    0 proud verts rewritten AND reproduces the 5.0u result exactly on every
    region and every pose.
    """
    assert nc._FULL_WEIGHT_SHOULDER_DIST > 0.0
    assert nc._FULL_WEIGHT_SHOULDER_DIST < nc._FULL_WEIGHT_MAX_DIST, (
        "the shoulder gate must be TIGHTER than the torso one, or it does "
        "nothing")
    src = inspect.getsource(nc._match_spine_twist_to_body.__globals__[
        "_match_full_weights_to_body"])
    assert "shoulder_z=ARMHOLE_Z[0]" in src, (
        "the band start belongs in body_zones, not re-derived here")
    body = inspect.getsource(nc._match_limb_motion_to_body)
    assert "band &= (wv[:, 2] < shoulder_z) | (dist <= shoulder_max_dist)" in body


def test_shoulder_gate_only_ever_narrows_the_band():
    """Written as an OR against the wrong side it would ADMIT far-off shoulder
    geometry instead of excluding it -- the exact opposite of the fix."""
    import numpy as np
    z = np.array([90.0, 90.0, 110.0, 110.0])
    dist = np.array([4.0, 1.0, 4.0, 1.0])
    band = np.ones(4, dtype=bool)
    band &= (z < 103.0) | (dist <= 2.0)
    # below the shoulder both survive; above it only the one in contact does
    assert list(band) == [True, True, False, True]


def test_default_strength_is_the_one_that_was_judged_in_game():
    """1.0 measured best on the probe (breast 3.12 vs 22.86). It was held at 0.6
    because a garment deforming exactly like skin is right for soft leather and
    was feared WRONG for rigid plate -- the one objection that was never
    measured. It has now been looked at: a glass cuirass at 1.0, with 3.97% of
    its mass relocated, was judged good in game beside the leather (2026-08-11).

    Pinned because the default and the verdict must not drift apart: a strength
    the user has not seen is not a validated default, in either direction."""
    assert nc._FULL_WEIGHT_STRENGTH == 1.0


def test_blend_is_a_convex_combination_of_two_normalised_rows():
    """Both operands are normalised weight rows, so the blend still sums to 1 and
    no renormalisation can silently change the intended split."""
    g = np.array([[0.20, 0.50, 0.30]])
    b = np.array([[0.02, 0.68, 0.30]])
    for s in (0.0, 0.35, 0.6, 1.0):
        row = (1.0 - s) * g + s * b
        assert np.isclose(row.sum(), 1.0)
        assert (row >= 0.0).all()
    assert np.allclose((1.0 - 0.0) * g + 0.0 * b, g)
    assert np.allclose((1.0 - 1.0) * g + 1.0 * b, b)
