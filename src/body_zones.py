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

r"""Where the anatomical zones actually are on the UBE body, in body-space Z.

ONE definition, imported everywhere. Four diagnostic scripts had each re-derived a
"breast band" by eye and four of them were wrong -- they read z 100-108, which is the
UPPER CHEST. Measured there, a cuirass reports +0.98u clearance and zero poke-through
while the real breast is 8% penetrated at rest. A band error doesn't produce a wrong
answer, it produces a CLEAN answer to the wrong question, which is far harder to catch.

The body stands with feet at z~11 and the neck stump near z~114 (a ~103-unit span), so
these are absolute body-space values, not fractions of height.

BREAST_Z = (90, 102), apex ~95-96. Established two independent ways:
  * the body's front-most (+Y) vertex sits at z=95, and front protrusion falls from
    8.2 at z 93-99 to 5.7 by z=100
  * the strongest vertices of the breast morphs (BreastsBigger, BreastsTBD) are at z=96
`clear_armor_outside_body`'s own `bust_z=(84, 100)` agrees; it was right all along.

Front/back matters as much as height: a z-band alone spans the BACK too. Gating the bust
clearance floor on `bust_z` without a front-facing normal test pushed a fur cuirass's back
from 0.74u to 1.02u. Use the FRONT/REAR helpers, or the body's outward normal.

See memory `project_bust_clearance_floor` and CLIPPING_LOG entry 0d.
"""
from __future__ import annotations

# (z_lo, z_hi) half-open in the scripts that slice with >= / <
BREAST_Z = (90.0, 102.0)
BREAST_APEX_Z = 95.5          # front-most vertex z=95; peak morph vertex z=96
BELLY_Z = (78.0, 92.0)
BUTT_Z = (62.0, 80.0)         # rear-facing
UPPER_CHEST_Z = (102.0, 112.0)   # NOT the breast -- named so nobody reaches for it again

# Torso half-width: beyond this you are on the arm / side, not the front surface.
TORSO_HALF_X = 16.0
# |y| past which a vertex is unambiguously front (+) or rear (-) facing.
FRONT_Y = 2.0
REAR_Y = -2.0


def _band(verts, zlo, zhi):
    import numpy as np
    z = np.asarray(verts)[:, 2]
    return (z >= zlo) & (z < zhi)


def breast_mask(verts):
    """Front-facing breast surface. Excludes the back, the sides and the upper chest."""
    import numpy as np
    v = np.asarray(verts)
    return (_band(v, *BREAST_Z) & (np.abs(v[:, 0]) < TORSO_HALF_X) & (v[:, 1] > FRONT_Y))


def belly_mask(verts):
    import numpy as np
    v = np.asarray(verts)
    return (_band(v, *BELLY_Z) & (np.abs(v[:, 0]) < 14.0) & (v[:, 1] > 1.0))


def butt_mask(verts):
    import numpy as np
    v = np.asarray(verts)
    return (_band(v, *BUTT_Z) & (np.abs(v[:, 0]) < TORSO_HALF_X) & (v[:, 1] < REAR_Y))


def back_mask(verts):
    """Rear torso at breast height -- the control surface. The bust clearance floor
    must never move this; if it does, the front/back gate leaked."""
    import numpy as np
    v = np.asarray(verts)
    return (_band(v, BREAST_Z[0], 110.0) & (np.abs(v[:, 0]) < TORSO_HALF_X)
            & (v[:, 1] < REAR_Y))
