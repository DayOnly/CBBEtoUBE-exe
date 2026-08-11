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

"""#full-weight-limb-boundary -- the full-vector match must not cross the armpit.

REPORTED IN GAME on a multi-layer cuirass: "stretched parts that link to the
arms when they shouldn't". `_match_limb_motion_to_body(full_vector=True)` copies
the weight row of the NEAREST body vertex, and nearest is not covered: in the
A-pose the upper arm hangs right beside the chest, so chest-plate geometry near
the armpit pairs to the ARM and takes the arm's whole row with it.

Measured on that piece before the gate: 310 units of true arm-family weight
moved onto `chest_plate`, concentrated at |x| 8-12 and z 90-100 -- the front of
the chest at bust height, where the arms are at |x| > 16 -- with the worst verts
reaching **0.986**, i.e. chest plate almost entirely following the arm.

After the gate, same piece: total arm gain **-11.4** (none added) and the worst
vert bounded at 0.474, which is the threshold doing exactly what it says.

WHY NOT THE EXISTING SHOULDER GATE. That one is a z>=103 band added for the same
family of defect above the shoulder. This one is a whole band lower, so no height
threshold separates them: the property is not how high the vert is, it is WHICH
LIMB the matched body point belongs to.
"""
import inspect

import numpy as np

from src import nif_convert as nc


# ------------------------------------------------------- the family predicate

def test_clavicle_is_not_arm_for_this_gate():
    """Deliberate: a chest plate legitimately follows the clavicle, so gating on
    it would refuse matches that are correct. The measured defect is UpperArm."""
    assert not nc._is_arm_hand_bone("NPC L Clavicle [LClv]")
    for b in ("NPC L UpperArm [LUar]", "NPC R Forearm [RLar]",
              "NPC L Hand [LHnd]", "NPC R Finger00 [RF00]"):
        assert nc._is_arm_hand_bone(b), b


def test_the_threshold_is_a_share_not_a_distance():
    """A share in [0,1]: 'the matched body vertex is mostly arm'. 0 disables."""
    assert 0.0 < nc._FULL_WEIGHT_LIMB_MAX <= 1.0


# --------------------------------------------------------------- the gate rule

def _gate(body_arm, garment_arm, thresh=None):
    """The predicate as the pass applies it: admit the match unless the BODY
    sample is arm-dominated while the GARMENT vert is not."""
    t = nc._FULL_WEIGHT_LIMB_MAX if thresh is None else thresh
    body_arm = np.asarray(body_arm, float)
    garment_arm = np.asarray(garment_arm, float)
    return (body_arm <= t) | (garment_arm > t)


def test_chest_plate_paired_to_the_arm_is_refused():
    """The reported case: garment vert carries almost no arm weight, the body
    vertex it matched is the arm. Refusing keeps the author's weights."""
    assert not _gate([0.99], [0.02])[0]


def test_sleeve_geometry_still_matches_the_arm():
    """The gate must not break the pass for actual sleeves -- a vert that IS arm
    geometry should still adopt the arm's row, which is the whole point of a
    full-vector match on a sleeve."""
    assert _gate([0.99], [0.90])[0]


def test_torso_matched_to_torso_is_untouched():
    """The bust win lives here: bust cloth pairs to torso body verts, which carry
    no arm weight, so the gate must be invisible to it."""
    assert _gate([0.00], [0.00])[0]
    assert _gate([0.10], [0.00])[0]


def test_zero_disables_the_gate():
    """The escape hatch has to actually restore the old behaviour, or a bisect
    against it proves nothing."""
    assert _gate([0.99], [0.02], thresh=0.0)[0] is np.True_ or _gate(
        [0.99], [0.02], thresh=0.0)[0]


# ------------------------------------------------------- it is wired in, live

def test_the_gate_is_applied_to_the_full_vector_selection():
    """A gate computed and not ANDed into `_sel` is the failure mode this
    project keeps hitting -- computed, reported, and silently discarded."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_limb_ok" in src, "the limb gate is gone"
    assert "& _limb_ok" in src, \
        "the limb gate is computed but not applied to the row selection"
    assert "_is_arm_hand_bone(_b)" in src, \
        "the gate no longer uses the project's arm-family predicate"


def test_the_gate_only_touches_the_full_vector_path():
    """The per-family matches (leg/spine/arm/spine-twist) are a different
    operation and were not implicated; the gate must not silently change them."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    head, _, tail = src.partition("if full_vector:")
    assert "_limb_ok" not in head, \
        "the limb gate leaked into the shared pre-amble / family path"
