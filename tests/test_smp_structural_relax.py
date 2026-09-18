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

"""Structural bones must not be mistaken for physics rigging.
#smp-structural-relax

`_shape_has_hdt_smp_rigging` counts every bone the injected BaseShape lacks, and
the BaseShape is a BODY -- it carries no hand, finger, pauldron or twist bones.
So a 2-bone pauldron clears the 0.4 ratio by missing both and loses the
clearance pass. Measured over a converted modlist: 26 of 236 gated shapes.

`_smp_rigging_is_structural_only` refines that answer for ONE call site (the
anti-poke). The guards below are the ones that caught real mistakes while this
was being built:

  * XPMSE ships `SkirtFBone01`, so "the skeleton has it" alone is NOT enough to
    call a bone structural -- that version reclassified 26 genuine chain shapes.
  * the shared predicate must stay UNCHANGED. Relaxing it in place also enabled
    the reskin and conform passes on these shapes, and a vanilla robe's pauldron
    clearance moved the WRONG WAY (-0.07u) because of them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402


class _FakeShape:
    """Minimal duck-typed stand-in for a pynifly shape."""
    def __init__(self, bones):
        self.bone_names = bones


# A body BaseShape: spine and pelvis, but no hands, fingers or pauldrons.
BODY = {"NPC Pelvis [Pelv]", "NPC Spine [Spn0]", "NPC Spine1 [Spn1]"}

# Stand-in actor skeleton: the vanilla rig, which DOES carry the bones the body
# mesh lacks -- plus the XPMSE skirt bones, the trap in this whole design.
SKELETON = {
    "npc pelvis [pelv]", "npc spine [spn0]", "npc spine1 [spn1]",
    "npc l hand [lhnd]", "npc r hand [rhnd]",
    "npc l finger00 [lf00]", "npc r finger00 [rf00]",
    "npc l pauldron", "npc r pauldron",
    "npc l upperarmtwist2 [lut2]", "npc r upperarmtwist2 [rut2]",
    "npc l rearthigh", "npc l frontthigh",
    "skirtfbone01", "skirtfbone02", "skirtfbone03",
}


class _Skeleton:
    """Install a known skeleton and ENABLE the opt-in, restoring both after.

    Two things would otherwise make these tests pass for the wrong reason: with
    no skeleton the helper deliberately returns the conservative answer, and the
    flag is DEFAULT OFF (the classification is correct but acting on it measured
    harmful -- see the constant's comment). Both must be forced to exercise the
    logic at all.
    """
    def __enter__(self):
        self._c = nc._SKELETON_BONES_CACHE
        self._n = nc._SKELETON_BONES_NORM_CACHE
        self._f = nc._SMP_STRUCTURAL_RELAX
        nc._SKELETON_BONES_CACHE = set(SKELETON)
        nc._SKELETON_BONES_NORM_CACHE = None
        nc._SMP_STRUCTURAL_RELAX = True
        return self

    def __exit__(self, *exc):
        nc._SKELETON_BONES_CACHE = self._c
        nc._SKELETON_BONES_NORM_CACHE = self._n
        nc._SMP_STRUCTURAL_RELAX = self._f
        return False


def _pauldron():
    """2 bones, both unknown to the body, both ordinary skeleton bones."""
    return _FakeShape(["NPC L Pauldron", "NPC R Pauldron"])


def _hands():
    """Hand/finger armour: high unknown ratio, zero physics."""
    return _FakeShape(["NPC L Hand [LHnd]", "NPC R Hand [RHnd]",
                       "NPC L Finger00 [LF00]", "NPC R Finger00 [RF00]",
                       "NPC Pelvis [Pelv]"])


def _authored_chain():
    """Author-invented chain bones -- in no skeleton, the real thing."""
    return _FakeShape([f"Skirt 1_{i:02d}" for i in range(6)]
                      + ["NPC Pelvis [Pelv]"])


def _xpmse_skirt():
    """Skirt weighted to XPMSE bones: the skeleton HAS them, but they are
    simulated. Must stay gated -- this is the skirt-collapse family."""
    return _FakeShape(["SkirtFBone01", "SkirtFBone02", "SkirtFBone03",
                       "NPC Pelvis [Pelv]"])


def test_relax_defaults_OFF():
    """Measured harmful on convex regions -- see the constant's comment.

    If this ever flips to default-on, it must be because a NEW in-game A/B says
    so, not because the classification looked correct. It did look correct; the
    pauldrons still ended up deeper in the body.
    """
    assert nc._SMP_STRUCTURAL_RELAX is False


def test_disabled_by_default_even_with_a_skeleton():
    """The default path must not depend on the flag being unreachable."""
    prev, prevn = nc._SKELETON_BONES_CACHE, nc._SKELETON_BONES_NORM_CACHE
    nc._SKELETON_BONES_CACHE = set(SKELETON)
    nc._SKELETON_BONES_NORM_CACHE = None
    try:
        assert nc._smp_rigging_is_structural_only(_pauldron(), BODY) is False
    finally:
        nc._SKELETON_BONES_CACHE = prev
        nc._SKELETON_BONES_NORM_CACHE = prevn


def test_evidence_floor_is_the_measured_value():
    # 4 raises collateral to 6/194 real chain shapes; see the constant's comment.
    assert nc._SMP_CHAIN_EVIDENCE_MIN == 3


def test_structural_only_shape_is_recognised():
    """The fix: a pauldron is not a physics chain."""
    with _Skeleton():
        assert nc._smp_rigging_is_structural_only(_pauldron(), BODY) is True
        assert nc._smp_rigging_is_structural_only(_hands(), BODY) is True


def test_shared_predicate_is_UNCHANGED():
    """The relaxation must not leak into the predicate's other seven callers.

    Relaxing it in place is what made a robe's pauldron clearance go backwards:
    the reskin and conform passes switched on too.
    """
    with _Skeleton():
        assert nc._shape_has_hdt_smp_rigging(_pauldron(), BODY) is True
        assert nc._shape_has_hdt_smp_rigging(_hands(), BODY) is True
        assert nc._shape_has_hdt_smp_rigging(_authored_chain(), BODY) is True


def test_authored_chain_is_not_structural():
    """The thing the gate exists for must never be called structural."""
    with _Skeleton():
        assert nc._smp_rigging_is_structural_only(
            _authored_chain(), BODY) is False


def test_xpmse_skirt_bones_still_count_as_physics():
    """Skeleton membership alone must NOT make a bone structural.

    This is the assertion that fails for the naive 'exclude anything the
    skeleton resolves' rule, which reclassified 26 real chain shapes.
    """
    with _Skeleton():
        assert nc._is_physics_evidence_bone("SkirtFBone01") is True
        assert nc._smp_rigging_is_structural_only(_xpmse_skirt(), BODY) is False


def test_jiggle_bones_still_count_as_physics():
    with _Skeleton():
        assert nc._is_physics_evidence_bone("NPC L Butt") is True
        assert nc._is_physics_evidence_bone("NPC L Breast") is True


def test_plain_structural_bones_are_not_evidence():
    with _Skeleton():
        for b in ("NPC L Hand [LHnd]", "NPC R Finger00 [RF00]",
                  "NPC L Pauldron", "NPC L UpperarmTwist2 [LUt2]"):
            assert nc._is_physics_evidence_bone(b) is False, b


def test_no_skeleton_keeps_conservative_answer():
    """With nothing to compare against, guessing is worse than the status quo."""
    prev, prevn = nc._SKELETON_BONES_CACHE, nc._SKELETON_BONES_NORM_CACHE
    nc._SKELETON_BONES_CACHE = set()
    nc._SKELETON_BONES_NORM_CACHE = None
    try:
        assert nc._smp_rigging_is_structural_only(_pauldron(), BODY) is False
    finally:
        nc._SKELETON_BONES_CACHE = prev
        nc._SKELETON_BONES_NORM_CACHE = prevn


def test_flag_off_restores_old_behaviour():
    with _Skeleton():
        nc._SMP_STRUCTURAL_RELAX = False
        try:
            assert nc._smp_rigging_is_structural_only(
                _pauldron(), BODY) is False
        finally:
            nc._SMP_STRUCTURAL_RELAX = True


def test_boneless_shape_is_not_structural():
    with _Skeleton():
        assert nc._smp_rigging_is_structural_only(_FakeShape([]), BODY) is False
