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

"""#pose-clearance -- the demand must be ZERO where nothing closes in.

That property is the entire difference between this and a uniform outward push. A
uniform push buys poke-through resistance by trading gaps at garment edges; measured,
it made one armour's belly WORSE (3.5% -> 3.9%) while helping the breast (11.0% ->
2.5%). A targeted term has to leave the passing cases alone or it inherits the same
trade.

The predecessor -- a cached per-BODY "pose amplitude" map -- failed because it saw
only one side of a RELATIVE motion: the vertices that lose coverage are the ones the
body deforms least (AUC 0.19-0.47, backwards). `test_demand_sees_a_garment_that_moves`
pins the case it could not see."""
import numpy as np
import pytest

from research import pose_clearance as pc


def _grid(n=12, z0=0.0, z1=10.0, r=5.0, y=0.0):
    """A flat sheet of verts in the xz plane at depth `y` -- stands in for a body
    patch or a garment panel."""
    xs = np.linspace(-r, r, n)
    zs = np.linspace(z0, z1, n)
    xx, zz = np.meshgrid(xs, zs, indexing="ij")
    v = np.stack([xx.ravel(), np.full(xx.size, y), zz.ravel()], axis=1)
    return v.astype(np.float64)


def _w(verts, bone, origin=(0.0, 0.0, 0.0)):
    return {bone: (np.ones(len(verts), dtype=np.float64),
                   np.asarray(origin, dtype=np.float64))}


PARENTS = {"root": None, "A": "root", "B": "A"}


# --- posing primitives --------------------------------------------------------

def test_identity_pose_reproduces_bind_exactly():
    v = _grid()
    out = pc.apply_pose(v, _w(v, "A"), {})
    assert np.abs(out - v).max() == 0.0


def test_pose_carries_descendants():
    """A rotation on a parent must move its children, or nothing below a joint poses
    -- the flat-bone-list trap that silently produces a no-op."""
    kids = pc.descendants(PARENTS, "A")
    assert kids == {"A", "B"}
    acc = pc.build_pose(PARENTS, {"A": np.zeros(3)}, [("A", 'z', 90.0)])
    assert "B" in acc and "A" in acc


def test_build_pose_ignores_bones_the_mesh_lacks():
    assert pc.build_pose(PARENTS, {}, [("A", 'z', 30.0)]) == {}


# --- the demand ---------------------------------------------------------------

def test_no_demand_when_nothing_closes_in():
    """THE property. Body and garment rigidly share one bone, so every pose moves
    them together and the clearance never changes."""
    body = _grid(y=0.0)
    arm = _grid(y=2.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    d = pc.pose_clearance_demand(arm, _w(arm, "A"), body, _w(body, "A"), bn,
                                 PARENTS, {"A": np.zeros(3)},
                                 poses={"twist": [("A", 'z', 20.0)]})
    assert d.max() == pytest.approx(0.0, abs=1e-9)


def test_demand_sees_a_garment_that_moves_away_from_a_still_body():
    """The case the per-BODY amplitude map was blind to: the body does not deform at
    all, the GARMENT swings, and the clearance between them collapses."""
    body = _grid(y=0.0)
    arm = _grid(y=2.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    # body pinned to an unposed bone; garment rides the bone that rotates
    d = pc.pose_clearance_demand(arm, _w(arm, "A"), body, _w(body, "root"), bn,
                                 PARENTS, {"A": np.array([0.0, 0.0, 5.0])},
                                 poses={"swing": [("A", 'x', 25.0)]}, cap=99.0)
    assert d.max() > 0.5, "a garment swinging off a still body must register demand"


def test_demand_is_capped():
    body = _grid(y=0.0)
    arm = _grid(y=2.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    d = pc.pose_clearance_demand(arm, _w(arm, "A"), body, _w(body, "root"), bn,
                                 PARENTS, {"A": np.array([0.0, 0.0, 5.0])},
                                 poses={"swing": [("A", 'x', 60.0)]}, cap=0.25)
    assert d.max() <= 0.25 + 1e-9


def test_gain_scales_the_demand():
    body = _grid(y=0.0)
    arm = _grid(y=2.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    kw = dict(parents=PARENTS, origins={"A": np.array([0.0, 0.0, 5.0])},
              poses={"swing": [("A", 'x', 25.0)]}, cap=99.0)
    full = pc.pose_clearance_demand(arm, _w(arm, "A"), body, _w(body, "root"), bn,
                                    gain=1.0, **kw)
    half = pc.pose_clearance_demand(arm, _w(arm, "A"), body, _w(body, "root"), bn,
                                    gain=0.5, **kw)
    assert half.max() == pytest.approx(full.max() * 0.5, rel=1e-6)


def test_demand_is_never_negative():
    """A pose that moves the body AWAY must not produce a negative requirement --
    that would pull the garment IN and invent a new clip."""
    body = _grid(y=0.0)
    arm = _grid(y=2.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    d = pc.pose_clearance_demand(arm, _w(arm, "root"), body, _w(body, "A"), bn,
                                 PARENTS, {"A": np.array([0.0, 0.0, 5.0])},
                                 poses={"away": [("A", 'x', -25.0)]}, cap=99.0)
    assert (d >= 0.0).all()


def test_empty_inputs_are_safe():
    e = np.zeros((0, 3))
    d = pc.pose_clearance_demand(e, {}, _grid(), _w(_grid(), "A"),
                                 np.tile([0.0, 1.0, 0.0], (144, 1)),
                                 PARENTS, {"A": np.zeros(3)})
    assert d.shape == (0,)


def test_default_is_off():
    """Clearance changes on this project have a history of trading one flaw for
    another; this ships off until it is calibrated against the pose census."""
    assert pc.POSE_CLEARANCE_ENABLED is False


# --- the exposure-driven demand ------------------------------------------------
#
# The clearance-deficit signal above is kept only for its posing primitives: it moved
# 35-74% of a garment, which is the bagginess a uniform push is rejected for. Demand
# derived from EXPOSURE is sparse by construction -- measured 0.9-8.9% of a garment --
# because only body verts that are covered at bind and exposed under a pose ask for
# anything at all.

def _panel(n=14, y=2.0):
    xs = np.linspace(-6, 6, n)
    zs = np.linspace(0, 12, n)
    xx, zz = np.meshgrid(xs, zs, indexing="ij")
    v = np.stack([xx.ravel(), np.full(xx.size, y), zz.ravel()], axis=1)
    t = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            t += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
    return v.astype(np.float64), np.asarray(t, dtype=np.int64)


def test_exposure_demand_is_zero_when_nothing_is_exposed():
    """A garment that covers the body in every pose must ask for NOTHING -- the
    property that separates this from a uniform push."""
    body = _grid(y=0.0)
    arm, at = _panel(y=2.0)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    d = pc.exposure_demand(arm, _w(arm, "A"), at, body, _w(body, "A"), bn,
                           PARENTS, {"A": np.zeros(3)},
                           poses={"twist": [("A", 'z', 15.0)]}, cap=2.0)
    assert d.max() == pytest.approx(0.0, abs=1e-9)


def test_exposure_demand_is_zero_for_skin_bare_at_bind():
    """Skin already exposed at BIND is bare by DESIGN -- a bikini asks for nothing.
    Only coverage LOST to a pose counts, so a garment that never covered the body
    generates no demand no matter how it moves."""
    body = _grid(y=0.0)
    arm, at = _panel(y=2.0)
    arm = arm + np.array([0.0, 0.0, 60.0])         # panel far above the body
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    d = pc.exposure_demand(arm, _w(arm, "A"), at, body, _w(body, "root"), bn,
                           PARENTS, {"A": np.array([0.0, 0.0, 5.0])},
                           poses={"swing": [("A", 'x', 30.0)]}, cap=2.0)
    assert d.shape == (len(arm),)
    assert d.max() == pytest.approx(0.0, abs=1e-9)


def test_exposure_demand_respects_the_cap():
    body = _grid(y=0.0)
    arm, at = _panel(y=0.4)
    bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    d = pc.exposure_demand(arm, _w(arm, "A"), at, body, _w(body, "root"), bn,
                           PARENTS, {"A": np.array([0.0, 0.0, 6.0])},
                           poses={"swing": [("A", 'x', 40.0)]}, cap=0.2)
    assert d.max() <= 0.2 + 1e-9


def test_rays_escape_controls():
    """Positive controls, both directions -- a metric that reports 'no problem' is
    indistinguishable from one that cannot see the problem."""
    arm, at = _panel(y=2.0)
    o = np.zeros((20, 3))
    o[:, 2] = np.linspace(1, 11, 20)
    out = np.tile([0.0, 1.0, 0.0], (20, 1))
    assert not pc.rays_escape(o, out, arm, at).any(), "panel in front must block"
    assert pc.rays_escape(o, -out, arm, at).all(), "facing away must escape"
