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

"""_smooth_warp_grooves must never move a vert toward the body.

Laplacian smoothing of the CBBE->UBE displacement field flattens whatever the
field peaks over, and over a convex feature it peaks at the apex -- so the pass
pulled the bust apex back onto the skin. A per-pass trace caught it regressing
13 of 42 shapes for a net +1052 exposed verts while improving fit zero times.

The scenario below is that failure in miniature: a garment displaced outward by
a bump, smoothed. test_old_behaviour_pulls_toward_body is a NEGATIVE CONTROL and
is load-bearing -- without it, a synthetic case that happens to produce no
inward motion at all would make every clamp assertion here pass while testing
nothing. The no-op test matters for the same reason in the other direction:
"never moves inward" is trivially satisfied by never moving.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_convert as nc  # noqa: E402

_UP = np.array([0.0, 1.0, 0.0])


def _scene(n=16, span=8.0, amp=1.0):
    """Flat body at y=0; garment 1u above it, bulged outward by a gaussian.

    The bulge is the displacement field: it peaks at the centre exactly the way
    a real garment's does over the bust apex, so smoothing it must flatten the
    centre -- pulling that vert down toward the body.
    """
    a = np.linspace(-span, span, n)
    X, Z = np.meshgrid(a, a)
    x, z = X.ravel(), Z.ravel()
    body = np.stack([x, np.zeros_like(x), z], 1)
    normals = np.tile(_UP, (len(body), 1))
    src = np.stack([x, np.ones_like(x), z], 1)
    bump = amp * np.exp(-(x ** 2 + z ** 2) / 8.0)
    warped = src + np.stack([np.zeros_like(x), bump, np.zeros_like(x)], 1)
    return body, normals, src, warped


def _inward(out, warped):
    """Total motion against the body normal (+y here). 0 == fully one-sided."""
    dy = (np.asarray(out) - np.asarray(warped))[:, 1]
    return float(-dy[dy < 0.0].sum())


def _moved(out, warped):
    d = np.linalg.norm(np.asarray(out) - np.asarray(warped), axis=1)
    return int((d > 1e-6).sum()), float(d.sum())


def test_old_behaviour_pulls_toward_body(monkeypatch):
    """NEGATIVE CONTROL. If the unclamped pass does no harm on this scene, the
    clamp assertions below prove nothing."""
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", False)
    body, nrm, src, warped = _scene()
    out = nc._smooth_warp_grooves(src, warped, body, ube_body_normals=nrm)
    assert _inward(out, warped) > 0.1, (
        "the scene does not reproduce the defect; the clamp tests would be "
        "vacuous")


def test_onesided_never_moves_a_vert_toward_the_body(monkeypatch):
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    body, nrm, src, warped = _scene()
    out = nc._smooth_warp_grooves(src, warped, body, ube_body_normals=nrm)
    assert _inward(out, warped) == pytest.approx(0.0, abs=1e-9)


def test_onesided_is_not_a_no_op(monkeypatch):
    """Clamping to zero motion would also score 'no inward motion'. The pass
    must still smooth -- tangentially and outward."""
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    body, nrm, src, warped = _scene()
    out = nc._smooth_warp_grooves(src, warped, body, ube_body_normals=nrm)
    n, total = _moved(out, warped)
    assert n > 0 and total > 0.05, f"pass was silently disabled: {n} verts moved"


def test_onesided_retains_most_of_the_smoothing(monkeypatch):
    """Only the inward part is cancelled, so the bulk of the motion survives."""
    body, nrm, src, warped = _scene()
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", False)
    _n0, t0 = _moved(nc._smooth_warp_grooves(
        src, warped, body, ube_body_normals=nrm), warped)
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    _n1, t1 = _moved(nc._smooth_warp_grooves(
        src, warped, body, ube_body_normals=nrm), warped)
    assert t1 > 0.25 * t0, f"retained only {100.0 * t1 / t0:.0f}% of smoothing"


def test_onesided_without_supplied_normals(monkeypatch):
    """Falls back to the position difference; still must not push inward."""
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    body, _nrm, src, warped = _scene()
    out = nc._smooth_warp_grooves(src, warped, body)
    assert _inward(out, warped) == pytest.approx(0.0, abs=1e-6)


def test_mismatched_normals_are_ignored_not_indexed(monkeypatch):
    """A wrong-length normals array must fall back, not raise -- the whole
    function is wrapped in `except: return warped`, so an IndexError here would
    disable the pass silently rather than fail loudly."""
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    body, _nrm, src, warped = _scene()
    out = nc._smooth_warp_grooves(src, warped, body,
                                  ube_body_normals=np.tile(_UP, (3, 1)))
    n, _t = _moved(out, warped)
    assert n > 0, "fell into the exception path and returned warped unchanged"
    assert _inward(out, warped) == pytest.approx(0.0, abs=1e-6)


def test_clamp_survives_denormal_normals(monkeypatch):
    """Zero-length normals must not produce NaN geometry."""
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    body, nrm, src, warped = _scene()
    nrm = nrm.copy()
    nrm[::5] = 0.0
    out = nc._smooth_warp_grooves(src, warped, body, ube_body_normals=nrm)
    assert np.isfinite(np.asarray(out)).all()


def test_no_body_is_still_handled(monkeypatch):
    """Without a body there is no inward direction to clamp; must not raise."""
    monkeypatch.setattr(nc, "GROOVE_ONESIDED", True)
    _body, _nrm, src, warped = _scene()
    out = nc._smooth_warp_grooves(src, warped, None)
    assert np.asarray(out).shape == warped.shape
    assert np.isfinite(np.asarray(out)).all()
