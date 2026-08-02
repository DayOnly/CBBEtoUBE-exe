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

"""One clipping percentage conflates two unrelated defects. Split it by depth.

The bands separate defects by the SIZE of correction they need, which differs by
more than an order of magnitude: one hip band is 67% of verts under 0.2u (median
0.123u) and 18% over 1u (up to 4.02u). No single push budget is right for both.

The sub-0.2u band is NOT z-fighting, though it was introduced on that theory.
The zoom test falsified it the same day: the user reports that clipping equally
visible with the camera against it. 0.2u is ~3mm, orders of magnitude above
depth-buffer precision -- the theory never held numerically either. Treat the
band as small-but-real penetration a clearance pass can fix, not as cosmetic.

The load-bearing test is `test_the_split_actually_separates`: a classifier that
answers "coincident" for everything would satisfy any single-band assertion, so
the same report has to put a shallow patch and a deep patch in DIFFERENT bands
before any of the individual cases mean anything.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "analysis"))
from mesh_penetration import (clipping_report, ray_exposure,     # noqa: E402
                              ray_first_hit, CLIP_COINCIDENT, CLIP_BURIED)

BANDS = ("clip_coincident_pct", "clip_shallow_pct", "clip_buried_pct")


def _panel(y, x0=-8.0, x1=8.0, z0=-8.0, z1=8.0):
    """A plane at depth `y`, spanning the given x/z box."""
    v = np.array([[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]], float)
    return v, np.array([[0, 1, 2], [0, 2, 3]], np.int64)


def _body_slab():
    """Near wall at y=0 (scored, normals +y), far wall at y=-4."""
    near, nt = _panel(0.0, -6.0, 6.0, -6.0, 6.0)
    far, ft = _panel(-4.0, -6.0, 6.0, -6.0, 6.0)
    verts = np.vstack([near, far])
    tris = np.vstack([nt, ft + len(near)])
    normals = np.zeros_like(verts)
    normals[:len(near)] = (0.0, 1.0, 0.0)
    normals[len(near):] = (0.0, -1.0, 0.0)
    mask = np.zeros(len(verts), bool)
    mask[:len(near)] = True
    return verts, tris, normals, mask


def _report(*garments, **kw):
    bv, bt, bn, mask = _body_slab()
    return clipping_report(bv, bt, bn, list(garments), mask=mask,
                           tmax=10.0, **kw)


def test_the_split_actually_separates():
    """THE control. Two patches at 0.1u and 2.0u, in ONE report, must land in
    two different bands. Without this, every assertion below is satisfied by a
    classifier that returns a constant."""
    r = _report(_panel(-0.1, x0=-8.0, x1=0.0),     # covers the x=-6 corners
                _panel(-2.0, x0=0.0, x1=8.0))      # covers the x=+6 corners
    assert r["clipping_pct"] > 99.0, r
    assert np.isclose(r["clip_coincident_pct"], 50.0), r
    assert np.isclose(r["clip_buried_pct"], 50.0), r
    assert r["clip_shallow_pct"] == 0.0, r
    assert r["clip_coincident_verts"] == 2 and r["clip_buried_verts"] == 2, r


def test_each_band_on_its_own():
    coin = _report(_panel(-0.1))
    shal = _report(_panel(-0.5))
    bur = _report(_panel(-2.0))
    assert coin["clip_coincident_pct"] > 99.0 and coin["clip_buried_pct"] == 0.0
    assert shal["clip_shallow_pct"] > 99.0 and shal["clip_coincident_pct"] == 0.0
    assert bur["clip_buried_pct"] > 99.0 and bur["clip_shallow_pct"] == 0.0


def test_the_bands_partition_the_clipping_number():
    """They must sum to `clipping_pct`, not merely correlate with it. A split
    that loses or double-counts area lets a fix claim ground it never took."""
    r = _report(_panel(-0.1, x0=-8.0, x1=0.0), _panel(-2.0, x0=0.0, x1=8.0))
    assert np.isclose(sum(r[k] for k in BANDS), r["clipping_pct"])
    assert (r["clip_coincident_verts"] + r["clip_shallow_verts"]
            + r["clip_buried_verts"]) == r["clip_verts"]


def test_edges_are_half_open_and_named():
    """Exactly at 0.2u is shallow, exactly at 1.0u is buried -- so a vert can
    never fall in two bands or none."""
    assert CLIP_COINCIDENT == 0.2 and CLIP_BURIED == 1.0
    assert _report(_panel(-CLIP_COINCIDENT))["clip_shallow_pct"] > 99.0
    assert _report(_panel(-CLIP_BURIED))["clip_buried_pct"] > 99.0


def test_the_keys_exist_even_with_nothing_to_report():
    """A missing key forces every consumer to guess whether the band was empty
    or the build predates the split, and one of those guesses is wrong."""
    clean = _report(_panel(4.0))            # garment OUTSIDE, nothing clips
    assert clean["clipping_pct"] < 1.0, clean
    for k in (*BANDS, "clip_depth_median", "clip_depth_p90", "clip_depth_max"):
        assert k in clean, k
    assert clean["clip_depth_median"] is None
    bv, bt, bn, mask = _body_slab()
    empty = clipping_report(bv, bt, bn, [_panel(-1.0)],
                            mask=np.zeros(len(bv), bool), tmax=10.0)
    for k in BANDS:
        assert k in empty, k


def test_depths_are_reported_too():
    r = _report(_panel(-2.0))
    assert np.isclose(r["clip_depth_median"], 2.0)
    assert np.isclose(r["clip_depth_max"], 2.0)


def test_the_split_works_with_the_occlusion_gate_off():
    """The opt-out reproduces pre-2026-08-01 numbers and must not lose the
    bands with them -- otherwise every historical comparison silently drops
    back to the single conflated figure."""
    r = _report(_panel(-0.1, x0=-8.0, x1=0.0), _panel(-2.0, x0=0.0, x1=8.0),
                body_occlusion=False)
    assert np.isclose(r["clip_coincident_pct"], 50.0), r
    assert np.isclose(r["clip_buried_pct"], 50.0), r


def test_the_fast_tester_agrees_with_the_reference_where_the_gate_FIRES():
    """`standoff_audit.ClipTester` is the tester every A/B script actually
    calls, and it had no body-occlusion gate while `clipping_report` did --
    measured 100.0% against 0.0% on a garment past the far wall. `selftest` ran
    on the bust band, where the body is thick and the gate rejects nothing, so
    a whole release compared two different metrics.

    This case is chosen so the gate DOES fire. A control that only exercises
    the region where two implementations trivially agree is not a control.
    """
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.analysis import standoff_audit as sa

    bv, bt, bn, mask = _body_slab()
    for label, g in (("beyond the far wall", _panel(-6.0)),   # gate fires
                     ("genuinely under the skin", _panel(-0.5))):
        ref = clipping_report(bv, bt, bn, [g], mask=mask, tmax=10.0)
        got = sa.ClipTester(g[0], g[1], tmax=10.0).report(
            bv, bt, bn, np.flatnonzero(mask), sa.vert_areas(bv, bt))
        for k in ("clipping_pct", *BANDS):
            assert abs(ref[k] - got[k]) < 1e-6, (label, k, ref[k], got[k])
    # and the gate must genuinely have fired, or the pair agreed on nothing
    assert clipping_report(bv, bt, bn, [_panel(-6.0)], mask=mask, tmax=10.0,
                           body_occlusion=False)["clipping_pct"] > 99.0


def test_first_hit_and_exposure_agree_so_the_swap_changed_nothing():
    """`clipping_report` now derives the un-gated `in_hit` from the DISTANCE
    rather than the bool, to get depth. The two test the same predicate over
    the same bounds; assert that rather than trusting the reading."""
    gv, gt = _panel(-1.0)
    o = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 5.0], [99.0, 0.0, 0.0]])
    d = np.tile([0.0, -1.0, 0.0], (3, 1))
    assert np.array_equal(
        ~ray_exposure(o, d, gv, gt, tmax=10.0),
        np.isfinite(ray_first_hit(o, d, gv, gt, tmax=10.0)))
