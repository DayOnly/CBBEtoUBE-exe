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

"""AUDIT A8 GUARD -- keep the converter's own clip test inside thick geometry.

There are THREE implementations of "is the garment behind the skin":

    mesh_penetration.clipping_report   body-occlusion gate  YES
    standoff_audit.ClipTester          body-occlusion gate  YES
    fit_metrics._ClipTester            body-occlusion gate  NO   <-- this one

The third is the one INSIDE the converter: it drives `minimum_push` (moves verts)
and `ChainGuard.exposed` (rolls a shape back). Without the gate it counts garment
seen ACROSS a thin gap as a poke-through -- measured elsewhere as 162 of 257
flagged hip verts, 63%, being the skirt seen across the gap between the legs.

Today that is INERT, and measurably so: over six shipped pieces, bust-band
clipping with the gate on vs off differed by 0.0000 on every one. It is inert
only because `push_region_mask` happens to sit on the FRONT TORSO, which is thick
enough that the inward ray never escapes the body.

That makes it a trap rather than a bug, and this test is the tripwire. Widening
the push region to reach the hip, rear or inner thigh would silently put the
converter's own decisions on the metric this project has already declared wrong
there -- with nothing to catch it, because the two gated implementations are used
only by scripts. If this test fails, do not just move the bound: give
`fit_metrics._ClipTester` the body-occlusion gate first. See CONVERTER_AUDIT
2026-08-01 A8.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.fit_metrics as fm                    # noqa: E402
from src.body_zones import BUTT_Z               # noqa: E402


def test_push_region_excludes_rear_skin():
    """`y > PUSH_Y_MIN` is what keeps the un-gated tester off the rear, where a
    ray can leave the body and hit the far side of a skirt."""
    assert fm.PUSH_Y_MIN >= -2.0, (
        "the push region has been extended around the back; the converter's "
        "clip test has no body-occlusion gate -- see AUDIT A8")


def test_push_region_does_not_reach_the_butt_band():
    """The butt/hip is the measured home of the across-the-gap artifact."""
    assert fm.PUSH_Z_LO > BUTT_Z[1], (
        f"push region z starts at {fm.PUSH_Z_LO}, at or below the butt band "
        f"{BUTT_Z}; the un-gated clip test would now judge thin geometry "
        f"-- see AUDIT A8")


def test_the_gate_free_tester_is_still_the_one_the_converter_uses():
    """If `_ClipTester` ever grows the gate, this guard can be retired -- but it
    must be retired deliberately, not silently."""
    import inspect
    src = inspect.getsource(fm._ClipTester)
    assert "body_occlusion" not in src, (
        "fit_metrics._ClipTester now has a body-occlusion gate -- good. "
        "Delete this guard file and AUDIT A8's action item.")
