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
                    "arms down", "arms forward", "arms crossed", "bow draw", "sprint"],
    "upper_chest": ["spine fwd lean", "spine twist", "spine side bend",
                    "arms down", "arms forward", "arms crossed", "bow draw", "sprint"],
    "belly":       ["spine fwd lean", "spine twist", "spine side bend",
                    "crouch", "walk + lean", "sprint"],
    "butt":        ["stride", "deep stride", "crouch", "walk + lean", "sprint"],
    "lower_back":  ["spine fwd lean", "spine twist", "crouch", "sprint"],
    "thigh":       ["stride", "deep stride", "knee bend", "legs together",
                    "crouch", "walk + lean", "sprint"],
}

# Body regions, same bands the penetration census uses. front = +Y on the UBE body;
# breast z90-102 (apex ~95), z99-112 is UPPER CHEST -- measuring the bust in the
# upper-chest band hides the defect entirely.
REGIONS = (
    ("breast",      lambda z, ny: (z >= 90) & (z <= 102) & (ny > 0.3)),
    ("upper_chest", lambda z, ny: (z >= 99) & (z <= 112) & (ny > 0.3)),
    ("belly",       lambda z, ny: (z >= 75) & (z < 90) & (ny > 0.3)),
    ("butt",        lambda z, ny: (z >= 55) & (z <= 75) & (ny < -0.3)),
    ("lower_back",  lambda z, ny: (z > 75) & (z <= 95) & (ny < -0.3)),
    ("thigh",       lambda z, ny: (z >= 35) & (z < 55)),
)

# Exclusions established by MEASUREMENT in posed_clip_test.visible_skin, not taste:
#   |x| > ARM_X   -> the arms, bare by design on most garments;
#   |x| < MID_X   -> the midline crevice (crotch / gluteal cleft), where rays escape
#                    downward between the legs in EVERY pose including bind and bury
#                    the real signal under a constant few-hundred-vert background.
ARM_X = 20.0
MID_X = 2.5
