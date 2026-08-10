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

"""The POSE SET a garment must survive, and the regions to judge it over.

WHY A SET, NOT A POSE. Every offline metric in this project measured the BIND pose,
which is an A-pose nobody stands in. `posed_clip_test.py` closed part of that blind
spot but poses only the THIGHS and CALVES and reports a regression against ONE stride
-- so torso, shoulder and spine motion, and therefore the whole CHEST region, were
still judged at bind only. A garment that fits at bind can clip the moment the actor
breathes, twists, draws a bow, or sprints.

WHAT A POSE IS. `[(bone, axis, degrees), ...]` ordered ROOT -> LEAF, so a knee bend
composes on top of a hip swing. Bone names are XPMSSE skeleton nodes; the hierarchy
comes from the SKELETON nif (an armour NIF's bone list is FLAT, so posing from it
silently moves nothing below the joint).

ANGLES ARE DELIBERATELY MODERATE. These are meant to sit inside the envelope of
ordinary animations -- walking, standing idle sway, drawing a weapon, sitting -- not
to prove a garment can be broken by a contortion. A test that only fails at 90 degrees
of hip flexion tells you nothing about play.
"""
from __future__ import annotations

import numpy as np

LT, RT = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]"
LC, RC = "NPC L Calf [LClf]", "NPC R Calf [RClf]"
SP0, SP1, SP2 = "NPC Spine [Spn0]", "NPC Spine1 [Spn1]", "NPC Spine2 [Spn2]"
LCL, RCL = "NPC L Clavicle [LClv]", "NPC R Clavicle [RClv]"
LUA, RUA = "NPC L UpperArm [LUar]", "NPC R UpperArm [RUar]"
LFA, RFA = "NPC L Forearm [LLar]", "NPC R Forearm [RLar]"
NECK = "NPC Neck [Neck]"

# Axis convention is the one `posed_clip_test.rot_matrix` established by MEASUREMENT
# (x = hip swing fwd/back). Do not re-derive it from intuition.
POSE_SET = {
    # --- reference -----------------------------------------------------------
    "bind": [],

    # --- lower body: the class posed_clip_test already covered ---------------
    "stride": [(LT, 'x', 30.0), (RT, 'x', -20.0)],
    "deep stride": [(LT, 'x', 45.0), (RT, 'x', -25.0)],
    "knee bend": [(LT, 'x', 20.0), (LC, 'x', 35.0)],
    "legs together": [(LT, 'y', -10.0), (RT, 'y', 10.0)],
    "crouch": [(LT, 'x', 40.0), (RT, 'x', 40.0), (LC, 'x', 45.0), (RC, 'x', 45.0)],

    # --- torso: NOT previously tested, and where the chest regions live -------
    # A spine bend/twist moves the ribcage and bust through the garment. This is
    # the gap that left "upper chest" judged at bind only.
    "spine fwd lean": [(SP0, 'x', 12.0), (SP1, 'x', 12.0), (SP2, 'x', 10.0)],
    "spine twist": [(SP0, 'z', 10.0), (SP1, 'z', 12.0), (SP2, 'z', 12.0)],
    "spine side bend": [(SP0, 'y', 10.0), (SP1, 'y', 10.0)],

    # --- shoulders/arms: pull a cuirass's collar and armhole ------------------
    "arms down": [(LCL, 'z', -8.0), (LUA, 'z', -35.0),
                  (RCL, 'z', 8.0), (RUA, 'z', 35.0)],
    "arms forward": [(LUA, 'x', 40.0), (RUA, 'x', 40.0)],
    "arms crossed": [(LUA, 'x', 35.0), (LFA, 'x', 60.0),
                     (RUA, 'x', 35.0), (RFA, 'x', 60.0)],
    "bow draw": [(SP2, 'z', 8.0), (LUA, 'x', 55.0),
                 (RUA, 'z', 25.0), (RFA, 'x', 70.0)],

    # --- a weapon swing, the pose a melee player spends the fight in ----------
    # Reported defect: the bust emerges through the SIDE of a cuirass while
    # swinging. No pose here reproduced that -- `arms forward`/`arms crossed`
    # move both arms symmetrically with no torso twist, and `bow draw` is a
    # braced, near-static stance. A one-handed swing is ASYMMETRIC and pairs an
    # arm sweep with a spine twist, which is what pulls one flank open.
    # The two halves twist in OPPOSITE directions on purpose: the sign
    # convention of a spine 'z' rotation is not documented anywhere, so running
    # both ends of the swing measures the leading flank whichever way it is.
    "swing windup": [(SP0, 'z', -8.0), (SP1, 'z', -10.0), (SP2, 'z', -12.0),
                     (RCL, 'z', -6.0), (RUA, 'z', -30.0), (RUA, 'x', -20.0),
                     (RFA, 'x', 45.0)],
    "swing strike": [(SP0, 'z', 10.0), (SP1, 'z', 12.0), (SP2, 'z', 14.0),
                     (RCL, 'z', -4.0), (RUA, 'x', 35.0), (RUA, 'z', -12.0),
                     (RFA, 'x', 30.0), (LUA, 'x', -15.0)],

    # --- combined: closest to a real frame of animation ----------------------
    "walk + lean": [(SP0, 'x', 8.0), (SP1, 'x', 8.0),
                    (LT, 'x', 25.0), (RT, 'x', -18.0)],
    "sprint": [(SP0, 'x', 18.0), (SP1, 'x', 15.0), (SP2, 'x', 10.0),
               (LT, 'x', 40.0), (RT, 'x', -28.0), (LC, 'x', 30.0),
               (LUA, 'x', 45.0), (RUA, 'x', -35.0)],
}

# Which poses can plausibly affect which region. Judging the bust under a knee bend
# only adds noise, and judging the thigh under a bow draw does the same.
REGION_POSES = {
    "breast":      ["spine fwd lean", "spine twist", "spine side bend",
                    "arms down", "arms forward", "arms crossed", "bow draw", "sprint",
                    "swing windup", "swing strike"],
    # The FLANK of the bust, which every front/rear-facing region above misses.
    # `spine fwd lean` and `walk + lean` are here as DECOMPOSITION, not because a
    # lean is expected to open a flank: `sprint` is a composite (lean + arms +
    # legs) and it was the one pose that REGRESSED under the spine-twist match.
    # Without the isolated lean there is no way to say which component moved, and
    # a composite pose is not an attributable measurement.
    "breast_side": ["spine twist", "spine side bend", "spine fwd lean",
                    "arms down", "arms forward", "arms crossed", "bow draw",
                    "walk + lean", "sprint", "swing windup", "swing strike"],
    "upper_chest": ["spine fwd lean", "spine twist", "spine side bend",
                    "arms down", "arms forward", "arms crossed", "bow draw", "sprint",
                    "swing windup", "swing strike"],
    "belly":       ["spine fwd lean", "spine twist", "spine side bend",
                    "crouch", "walk + lean", "sprint"],
    "butt":        ["stride", "deep stride", "crouch", "walk + lean", "sprint"],
    "lower_back":  ["spine fwd lean", "spine twist", "crouch", "sprint"],
    # Same poses as upper_chest: the shoulder blades are driven by the clavicle,
    # upper arm and Spine2, so an arm pose moves them and a leg pose does not.
    "upper_back":  ["spine fwd lean", "spine twist", "spine side bend",
                    "arms down", "arms forward", "arms crossed", "bow draw", "sprint"],
    "thigh":       ["stride", "deep stride", "knee bend", "legs together",
                    "crouch", "walk + lean", "sprint"],
}

# Body regions, same bands the penetration census uses. front = +Y on the UBE body;
# breast z90-102 (apex ~95), z99-112 is UPPER CHEST -- measuring the bust in the
# upper-chest band hides the defect entirely.
#
# A selector is `sel(z, ny, nx=None)`. `nx` is optional so the six front/rear
# regions -- and every existing caller that passes two arguments -- are unchanged;
# a LATERAL region needs it and REFUSES when it is absent rather than quietly
# selecting the wrong skin.
def _breast_side(z, ny, nx=None):
    """The FLANK of the bust: outward-facing sideways, at breast height.

    THE BLIND SPOT THIS PINS, and it is the same shape as the `upper_back` one.
    Every other torso region gates on `ny` -- front-facing or rear-facing -- so a
    vertex whose normal points SIDEWAYS (|ny| small, |nx| large) belonged to no
    region at all. Measured on the UBE body at breast height: 1275 lateral-facing
    verts, 1985 verts in no existing region. A user reported the bust emerging
    through the SIDE of a cuirass under a weapon swing and every region here read
    clean, because none of them was looking at the side.

    The z ceiling is 103, NOT the breast band's 102: the flank is continuous with
    the armhole above it and `SIDE_Z` in src.body_zones already uses (92, 103).
    """
    if nx is None:
        raise ValueError(
            "breast_side is a LATERAL region and needs the normal's x component; "
            "the caller passed only (z, ny). Selecting on ny alone here would "
            "return front-facing skin under a lateral region's name.")
    return (z >= 90) & (z <= 103) & (np.abs(nx) > 0.5) & (np.abs(ny) <= 0.3)


REGIONS = (
    ("breast",      lambda z, ny, nx=None: (z >= 90) & (z <= 102) & (ny > 0.3)),
    ("breast_side", _breast_side),
    ("upper_chest", lambda z, ny, nx=None: (z >= 99) & (z <= 112) & (ny > 0.3)),
    ("belly",       lambda z, ny, nx=None: (z >= 75) & (z < 90) & (ny > 0.3)),
    ("butt",        lambda z, ny, nx=None: (z >= 55) & (z <= 75) & (ny < -0.3)),
    ("lower_back",  lambda z, ny, nx=None: (z > 75) & (z <= 95) & (ny < -0.3)),
    # THE SHOULDER BLADES, which nothing here could see: lower_back stops at z95 and
    # upper_chest is z99-112 FRONT-facing, so the rear above z95 was judged in no
    # region at all.
    # It starts at 95, NOT at upper_chest's 99. Mirroring the front band exactly was
    # the tidier story, but it left rear z95-99 in no region -- and an unjudged strip
    # between two regions is exactly where a defect sits unseen. Contiguity with
    # lower_back's ceiling beats symmetry with the front.
    ("upper_back",  lambda z, ny, nx=None: (z > 95) & (z <= 112) & (ny < -0.3)),
    ("thigh",       lambda z, ny, nx=None: (z >= 35) & (z < 55)),
)

# Exclusions established by MEASUREMENT in posed_clip_test.visible_skin, not taste:
#   |x| > ARM_X   -> the arms, bare by design on most garments;
#   |x| < MID_X   -> the midline crevice (crotch / gluteal cleft), where rays escape
#                    downward between the legs in EVERY pose including bind and bury
#                    the real signal under a constant few-hundred-vert background.
ARM_X = 20.0
MID_X = 2.5

# `ARM_X` DOES NOT SEPARATE THE ARM FROM THE FLANK, and a lateral region is where
# that stops being survivable. Measured on the UBE body over z90-103, mean weight
# on Clavicle/UpperArm/Forearm/Hand per |x| shell:
#
#     |x|  8-12   2102 verts   arm weight 0.220   <- the flank
#     |x| 12-16    640 verts   arm weight 0.975   <- the ARM, INSIDE the cut
#     |x| 16-20    738 verts   arm weight 1.000   <- the ARM, INSIDE the cut
#     |x| 20-24    252 verts   arm weight 1.000
#
# In the bind A-pose the upper arm hangs beside the chest, so a pure |x| cutoff
# keeps 1378 pure-arm verts and would still have to reach past |x|=12 to admit the
# flank at all. The front/rear regions survive this because bare arm skin is
# uncovered at bind and the covered-at-bind baseline drops it; a lateral region has
# no such luck -- the arm is exactly what sits lateral to the bust.
# So LATERAL regions exclude the arm by SKIN WEIGHT instead.
ARM_BONE_KEYS = ("Clavicle", "UpperArm", "Upperarm", "Forearm", "Hand")
ARM_WEIGHT_MAX = 0.5
WEIGHT_ARM_EXCLUDED = frozenset({"breast_side"})
