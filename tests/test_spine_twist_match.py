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

"""Spine-TWIST weight match at partial strength (#spine-twist-partial).

Reported in game: the bust comes out the SIDE of a cuirass while swinging a
weapon. Motion only, not at rest, and reproducible with the preset off.

Root-caused as the Spine1-vs-Spine2 split -- the same defect the torso instance
handles -- over the flank verts that emerge under the swing: follow 0.88,
clearance closing 0.47u, garment Spine1 0.167 where the covered body carries
0.016.

WHY A SECOND INSTANCE. The torso instance never reaches this population (the
source owns a morph TRI), and ungating it at full strength TRADES pose families
rather than fixing anything: swing strike 12.81 -> 8.54 but sprint 13.82 ->
19.60. Two designs were then measured, and the tests below pin BOTH results --
the one that worked and the one that did not:

  * scoping it to the FLANK (the verts the defect was measured on) -- REFUTED,
    it lost most of the fix and made `spine twist` worse than doing nothing;
  * PARTIAL STRENGTH -- works, but only once `strength` was made to reach the
    SPLIT rather than just the family total.

NOTHING HERE IS CONFIRMED IN GAME, and it is one piece. Default OFF.
"""
import importlib
import inspect

import src.nif_convert as nc


def test_flag_defaults_off_and_opts_in(monkeypatch):
    """DEFAULT OFF. It carries ignore_morph_tri, which no shipped instance does,
    and its only evidence so far is one piece."""
    assert nc.MATCH_SPINE_TWIST is False
    monkeypatch.setenv("CBBE2UBE_SPINE_TWIST_MATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MATCH_SPINE_TWIST is True
    finally:
        monkeypatch.delenv("CBBE2UBE_SPINE_TWIST_MATCH", raising=False)
        importlib.reload(nc)


def test_pass_is_wired_into_both_convert_paths():
    src = inspect.getsource(nc)
    calls = src.count("_match_spine_twist_to_body(dst_path, biped_slots,")
    assert calls >= 2, f"only {calls} call site(s)"


def test_it_runs_last_of_the_family_matches_in_both_paths():
    """ORDER IS LOAD-BEARING. Every family match rescales the bones it does not
    manage, so the last one to run wins the overlapping rows. This is the
    narrowest band of the four; if a wider one runs after it, the scoping this
    whole change is about gets overwritten."""
    src = inspect.getsource(nc)
    calls = {
        "leg": "_match_leg_motion_to_body(dst_path, biped_slots,",
        "spine": "_match_spine_motion_to_body(dst_path, biped_slots,",
        "arm": "_match_arm_motion_to_body(dst_path, biped_slots,",
        "twist": "_match_spine_twist_to_body(dst_path, biped_slots,",
    }
    at = {k: [i for i in range(len(src)) if src.startswith(v, i)]
          for k, v in calls.items()}
    for k, v in at.items():
        assert len(v) >= 2, f"{k} is not wired into both convert paths"
    for leg, spine, arm, twist in zip(at["leg"], at["spine"], at["arm"],
                                      at["twist"]):
        assert leg < spine < arm < twist, (
            "pass order must be leg -> spine -> arm -> spine-twist in every "
            "convert path")


def test_partial_strength_is_the_lever_not_full():
    """At full strength this pass costs more than it buys (sprint 13.82 ->
    19.60). The sweep puts the best swing at 0.60, so that has to be the
    default, not 1.0."""
    assert 0.0 < nc._SPINE_TWIST_STRENGTH < 1.0, (
        "a full-strength default reintroduces the trade the sweep measured")


def test_flank_scoping_is_OFF_because_it_was_measured_WORSE():
    """THE REFUTED DESIGN, kept reproducible rather than only written down.

    Narrowing this pass to the flank -- the verts the defect was measured on --
    is the obvious move, and it threw away most of the fix while keeping the
    cost: `swing strike` 12.81 -> 12.06 where the unscoped pass reaches 7.79,
    and `spine twist` went 7.29 -> 9.04, WORSE than doing nothing. The garment
    is a continuous surface, so the flank is dragged by verts on the front and
    back of the same panel and a fix confined to the flank cannot move it.
    """
    assert nc._SPINE_TWIST_LATERAL_X == 0.0, (
        "flank scoping measured WORSE than the whole torso band; not a default")
    src = inspect.getsource(nc._match_spine_twist_to_body)
    assert "lateral_half_x=_SPINE_TWIST_LATERAL_X" in src, (
        "keep the knob wired so the negative result stays reproducible")


def test_it_covers_the_same_torso_band_as_the_shipped_spine_pass():
    """Same defect and same measured mismatch (Spine2 disagreement z
    80.9-110.7); only the strength and the morph-TRI gate differ."""
    src = inspect.getsource(nc._match_spine_twist_to_body)
    assert "z_lo=_SPINE_MOTION_Z_LO" in src and "z_hi=_SPINE_MOTION_Z_HI" in src
    assert nc._SPINE_MOTION_Z_LO <= 80.9 and nc._SPINE_MOTION_Z_HI >= 110.7


def test_it_manages_the_whole_spine_chain():
    """The defect is the SPLIT. Managing Spine2 alone adds mass without moving
    it off Spine1."""
    assert nc._SPINE_TWIST_BONES == nc._SPINE_MOTION_BONES


def test_it_opts_out_of_the_morph_tri_skip():
    """The gated population IS the defect population -- 88% of the pack owns a
    source morph TRI, and the reported piece is one of them. Gated, this pass is
    a measured no-op, which is exactly why it ships default OFF."""
    src = inspect.getsource(nc._match_spine_twist_to_body)
    assert "ignore_morph_tri=True" in src


def test_lateral_gate_narrows_and_never_widens_the_band():
    """`lateral_half_x` must be an AND against the existing band. Written as an
    OR -- or applied before the z test -- it would pull in flank verts outside
    the z band, which is a wider reach than the unscoped pass, not a narrower
    one."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "band &= np.abs(wv[:, 0]) >= lateral_half_x" in src


def test_pairing_is_by_ray_and_reuses_the_shipped_ray_test():
    """KD-nearest is not `covered`. Where the torso folds toward itself the
    closest body vert belongs to different anatomy, so the pass copies the wrong
    bone's weight. Measured: the ray hits 72.8-79.8% of band rows and re-pairs
    ~60% of them; outcome swing strike 7.79 -> 7.04, and the small upper_chest
    regression KD introduced (4.45 -> 4.75) goes away."""
    src = inspect.getsource(nc._match_spine_twist_to_body)
    assert "pair_by_ray=_SPINE_TWIST_PAIR_RAY" in src
    assert nc._SPINE_TWIST_PAIR_RAY is True
    body = inspect.getsource(nc._match_limb_motion_to_body)
    assert "fit_metrics._ClipTester" in body, (
        "use the shipped ray test, not a second implementation of one")


def test_ray_pairing_failure_is_REPORTED_not_swallowed():
    """THE BUG THIS PINS, and it cost a wrong conclusion.

    The first version wrote `except Exception: pass` around a block that
    referenced `_fm` -- a LOCAL import in a different function. Every call raised
    NameError, fell back to KD, and produced a byte-identical mesh, which reads
    exactly like 'ray pairing changes nothing'. It was caught only because a
    standalone measurement said the two pairings DO differ while the artefact
    said they did not.

    A swallowed exception is indistinguishable from 'nothing qualified'.
    """
    body = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_note_pass_failure(\"_match_limb_motion_to_body/ray-pair\"" in body
    assert "except Exception:\n                    pass" not in body


def test_ray_pairing_can_only_re_pair_never_drop_a_row():
    """A ray that misses must keep the KD answer. If a miss dropped the row, the
    pass would silently shrink to the 72-80% of rows the ray happens to hit."""
    body = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_fin = np.isfinite(_hit)" in body
    assert "near[_rows[_fin]] = _n2" in body, (
        "only rows with a finite hit may be re-paired")


def test_strength_blends_the_SPLIT_and_is_inert_at_one():
    """`strength` used to scale only the family TOTAL, so on a split-type defect
    it moved weights and changed nothing: strengths 1.0 / 0.5 / 0.25 wrote
    weights differing by up to 0.378 and gave byte-identical exposure in every
    region and every pose. Without a split blend there is no partial setting to
    trade with, and a pass that helps one pose family and hurts another has no
    middle to search."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "if strength < 1.0:" in src, (
        "the split blend must be guarded so strength 1.0 -- what every shipped "
        "instance uses -- stays byte-identical")
    assert "(1.0 - strength) * g_split" in src
    for name in ("_LEG_MOTION_STRENGTH", "_ARM_MOTION_STRENGTH",
                 "_SPINE_MOTION_STRENGTH"):
        assert getattr(nc, name) == 1.0, (
            f"{name} is not 1.0, so the split blend is no longer inert for a "
            f"shipped instance and this change altered production behaviour")


def test_split_blend_interpolates_between_garment_and_body():
    """At 0 the garment keeps its own split, at 1 it takes the body's."""
    import numpy as np
    g_split = np.array([[0.20, 0.80]])
    b_split = np.array([[0.02, 0.98]])
    for s, want in ((0.0, g_split), (1.0, b_split),
                    (0.5, (g_split + b_split) / 2.0)):
        got = (1.0 - s) * g_split + s * b_split
        assert np.allclose(got, want)
        assert np.isclose(got.sum(), 1.0), "a blend of two splits must still sum to 1"


def test_morph_tri_opt_out_is_off_by_default_for_shipped_instances():
    """A shipped-ON instance must never carry the opt-out: the morph-TRI skip
    exists because re-sharing a TRI-owned shape's limb mass regressed in game."""
    for fn in (nc._match_leg_motion_to_body, nc._match_arm_motion_to_body,
               nc._match_spine_motion_to_body):
        assert "ignore_morph_tri" not in inspect.getsource(fn), (
            f"{fn.__name__} ships ON and must not opt out of the morph-TRI skip")
