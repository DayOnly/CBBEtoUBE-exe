"""The shared frame chooser must get BOTH directions right.

A tool that always transforms throws 84 shipped shapes off the body; a tool
that never transforms strands 403 of them below the floor. Only proximity to
the body gets both, so both directions are asserted here -- a test that pinned
one would pass for either broken rule.
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts" / "analysis"))

import standoff_audit as sa  # noqa: E402


def _body(n=60):
    """A stand-in torso: a vertical line of verts at human height."""
    z = np.linspace(11.0, 114.0, n)
    return cKDTree(np.column_stack([np.zeros(n), np.zeros(n), z]))


def _bust(n=20, dz=0.0):
    return np.column_stack([np.zeros(n), np.zeros(n),
                            np.linspace(90.0, 102.0, n) + dz])


def test_a_world_stored_shape_with_a_stale_transform_keeps_its_raw_verts():
    """THE FALSE-ZERO CASE: raw is already on the body, world is 120u off."""
    raw = _bust()
    world = raw + np.array([0.0, 0.0, -120.34])   # the measured median throw
    got, which = sa.pick_frame(raw, world, _body())
    assert which == "raw"
    assert np.allclose(got, raw)


def test_a_skin_stored_shape_is_transformed():
    """THE OTHER DIRECTION: 403 of 534 shapes genuinely need the call. A rule
    that merely stopped transforming would strand every one of them."""
    world = _bust()
    raw = world - np.array([0.0, 0.0, 120.34])    # off-body, as stored
    got, which = sa.pick_frame(raw, world, _body())
    assert which == "world"
    assert np.allclose(got, world)


def test_agreement_is_not_ambiguity():
    """Identical frames must yield verts, not an exclusion: reading agreement
    as ambiguity once cut a 20-shape sample to 3."""
    raw = _bust()
    got, which = sa.pick_frame(raw, raw.copy(), _body())
    assert which == "agree"
    assert np.allclose(got, raw)


def test_the_fixture_is_not_inert():
    """If the rejected frame were also on the body, the two tests above would
    pass for a chooser that simply returned its first argument."""
    raw = _bust()
    world = raw + np.array([0.0, 0.0, -120.34])
    t = _body()
    d_raw = float(np.median(t.query(raw)[0]))
    d_world = float(np.median(t.query(world)[0]))
    assert d_raw < 1.0
    assert d_world > 20.0 and d_world > 20.0 * max(d_raw, 1e-6)


@pytest.mark.parametrize("delta", [0.0, 0.1, 0.24])
def test_frames_within_the_agree_band_are_not_split(delta):
    raw = _bust()
    got, which = sa.pick_frame(raw, _bust(dz=delta), _body())
    assert which == "agree"
    assert np.allclose(got, raw)
