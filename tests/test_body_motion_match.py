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

"""Body-motion match: an armor vert must move exactly as the body surface it
COVERS moves, so its clearance is preserved under every slider -- then the body
can't poke through and the armor can't balloon past it.

The bare IDW average dilutes a hugging vert's motion (measured 0.59-0.81x of the
body on a fitted leather cuirass -> body burst through it in-game). An earlier
attempt topped verts up to the regional PEAK expansion instead and overshot to
2.0-2.2x -> the same cuirass ballooned. Both were measured in-game; ratio 1.0 is
the validated target. Wide averaging is still wanted for DRAPE cloth far off the
body, so the nearest-copy weight eases out between _MATCH_NEAR and _MATCH_FAR.
#body-motion-match"""
import importlib

import numpy as np

import src.sliderset_gen as sg


class _Morph:
    def __init__(self, name, offsets):
        self.name = name
        self.offsets = offsets


class _Osd:
    def __init__(self, morphs):
        self.morphs = morphs


def _body():
    """Dense flat plane at y=0; the row at z==5 moves +1 outward under 'Bump'."""
    verts, moved = [], []
    for z in range(0, 11):
        for x in (-2, -1, 0, 1, 2):
            verts.append((float(x), 0.0, float(z)))
            moved.append(z == 5)
    return np.asarray(verts, np.float64), np.asarray(moved)


def _osd(moved):
    return _Osd([_Morph("BaseShapeBump",
                        [(int(i), 0.0, 1.0, 0.0) for i in np.where(moved)[0]])])


# vert 0 hugs the body (stand-off 1.0); vert 1 hangs far off it (stand-off 12)
_PLATE = np.asarray([(0.0, 1.0, 5.0), (0.0, 12.0, 5.0)], np.float64)


def _deltas(enabled=True):
    importlib.reload(sg)
    if not enabled:
        sg._BODY_MOTION_MATCH = False
    bv, moved = _body()
    tri = sg.generate_armor_tri({"Plate": _PLATE.copy()}, bv, _osd(moved),
                                body_shape_name="BaseShape",
                                include_body_shapes=False)
    out = np.zeros((len(_PLATE), 3))
    for ts in tri.shapes:
        if ts.name == "Plate":
            for m in ts.morphs:
                if m.name == "Bump":
                    for vi, dx, dy, dz in m.offsets:
                        out[int(vi)] = (dx, dy, dz)
    return out


def test_hugging_vert_copies_the_body_exactly():
    """Stand-off <= _MATCH_NEAR -> ratio 1.0: the armor moves exactly as the body
    vertex it covers. This is what stops the body poking through fitted armor."""
    d = _deltas()[0]
    assert np.allclose(d, [0.0, 1.0, 0.0], atol=1e-6)


def test_hugging_vert_is_diluted_without_the_match():
    """Bare IDW under-follows a hugging vert (the measured 0.59-0.81x defect)."""
    d = _deltas(enabled=False)[0]
    assert np.linalg.norm(d) < 0.9


def test_never_overshoots_the_body():
    """Ratio must not exceed 1.0 -- overshoot is what ballooned the cuirass."""
    for d in _deltas():
        assert np.linalg.norm(d) <= 1.0 + 1e-6


def test_far_drape_vert_keeps_the_smoothed_average():
    """Stand-off >= _MATCH_FAR -> unchanged IDW, so a skirt doesn't snap rigidly
    to whichever body vert happens to be nearest."""
    assert np.allclose(_deltas()[1], _deltas(enabled=False)[1], atol=1e-9)


def test_match_weight_ramps_between_near_and_far():
    importlib.reload(sg)
    w = sg._motion_match_weight(np.array([0.0, sg._MATCH_NEAR, sg._MATCH_FAR, 99.0]))
    assert w[0] == 1.0 and w[1] == 1.0        # hugging -> copy the body
    assert w[2] == 0.0 and w[3] == 0.0        # drape   -> smoothed average
    mid = sg._motion_match_weight(
        np.array([(sg._MATCH_NEAR + sg._MATCH_FAR) / 2.0]))[0]
    assert 0.0 < mid < 1.0                    # smooth blend, no seam


def test_disabled_matches_plain_idw(monkeypatch):
    """CBBE2UBE_NO_BODY_MOTION_MATCH=1 reverts to the old pointwise behaviour."""
    monkeypatch.setenv("CBBE2UBE_NO_BODY_MOTION_MATCH", "1")
    importlib.reload(sg)
    assert sg._BODY_MOTION_MATCH is False
