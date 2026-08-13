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

"""The fold detector must fire on a folded surface, stay silent on a clean
one, and REFUSE to judge a vertex whose normal is undetermined.

That last one is not a nicety. Gating on coherence alone -- without the fan
term -- scores a vertex belonging to ONE triangle as perfectly coherent,
because there is no second face to disagree with it. That blind spot counted
every isolated tab and ribbon rim as a defect and overstated the class by 103
pieces (979 of 1656 vs the real 876) before it was caught.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "scripts" / "analysis"))
from fold_census import COHERENCE_MIN, FAN_MIN, score  # noqa: E402


def _disc(n=10, r=1.0):
    """Centre vertex 0 with a closed, planar fan of n triangles."""
    verts = [(0.0, 0.0, 0.0)]
    for k in range(n):
        a = 2.0 * np.pi * k / n
        verts.append((r * np.cos(a), r * np.sin(a), 0.0))
    tris = [(0, 1 + k, 1 + (k + 1) % n) for k in range(n)]
    return np.asarray(verts, float), np.asarray(tris, np.int64)


def test_flat_fan_is_not_folded():
    v, t = _disc()
    folded, inverted, undetermined = score(v, t)
    assert not folded.any(), "a flat disc contains no fold"
    assert not undetermined[0], "a closed planar fan is determined"


def test_folded_vertex_is_detected():
    """Drive the centre vertex sideways PAST its own rim. The triangles on the
    far side then wind backwards while the near ones do not, so the vertex's
    faces point in opposing directions and the surface passes through itself.

    Note the shape that is NOT a fold: pulling the centre along the normal
    instead makes a cone, however steep. A cone's faces still agree, and the
    detector must stay silent on it -- that was this fixture's first version
    and the detector was right to reject it.
    """
    v, t = _disc()
    v[0, 0] = 3.0            # outside the rim: some faces invert
    folded, _, _ = score(v, t)
    assert folded[0], "the centre vertex has been dragged through its own rim"


def test_control_the_same_mesh_unfolded_is_clean():
    """NEGATIVE CONTROL for the test above. Displace the same vertex the same
    way but keep it INSIDE the rim. If this fails, that test proves nothing --
    it would pass on any displaced mesh."""
    v, t = _disc()
    v[0, 0] = 0.2            # still inside: no face inverts
    folded, _, _ = score(v, t)
    assert not folded[0], (
        "an off-centre but interior vertex must NOT read as a fold, or the "
        "detector fires on ordinary asymmetry and its counts are noise")


def test_lone_triangle_is_excluded_not_judged():
    """The blind spot. A vertex on ONE triangle has no fan to disagree with
    itself, so its normal is undetermined and it must not be judged."""
    v = np.asarray([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], float)
    t = np.asarray([(0, 1, 2)], np.int64)
    normals = np.tile(np.asarray([0.0, 0.0, -1.0]), (3, 1))   # contradicts
    folded, inverted, undetermined = score(v, t, normals)
    assert undetermined.all(), "a fan of 1 cannot determine a normal"
    assert not inverted.any(), (
        "an undetermined vertex must never be counted as inverted -- this is "
        "the miscount that overstated the class by 103 pieces")


def test_control_coherence_alone_would_have_judged_it():
    """NEGATIVE CONTROL for the gate itself: show that the FAN term is what
    excludes the lone triangle. Coherence alone scores it 1.0 -- perfectly
    coherent -- so a coherence-only gate would have judged and miscounted it.
    """
    v = np.asarray([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], float)
    t = np.asarray([(0, 1, 2)], np.int64)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    area = np.linalg.norm(fn, axis=1)
    coherence = float(np.linalg.norm(fn[0]) / area[0])
    assert coherence >= COHERENCE_MIN, (
        f"a lone triangle scores {coherence} on coherence -- it passes the "
        f"coherence gate, so only the fan >= {FAN_MIN} term can exclude it")


def test_inverted_normal_on_a_determined_vertex_is_counted():
    """The other half: where the fan IS determined, a normal contradicting it
    must be reported. Otherwise the detector is silent on the real defect."""
    v, t = _disc()
    normals = np.tile(np.asarray([0.0, 0.0, -1.0]), (len(v), 1))
    folded, inverted, undetermined = score(v, t, normals)
    assert not undetermined[0]
    assert inverted[0], (
        "the disc faces +Z; a stored -Z normal on its determined centre "
        "vertex is inverted and must be counted")
