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

"""`shape_body_offset` must discard a transform that moves a shape OFF the body.

The bug: the function adds a shape's NiAVObject.transform.translation so
phase-2 maths runs in body space. Correct for a shape authored in a shifted
space; WRONG for a skinned shape already in body space, whose transform is inert
at render. A real cuirass carries translation [-40,0,0] with identity
global_to_skin and verts already placed -- the offset moved it 40u sideways and
median bust-skin distance went 2.11u -> 21.09u, so every fit pass was matching a
garment that was not where the body is.

Census, 765 source NIFs / 194 mods: 351 shapes carry a non-zero offset; of those
that are genuinely body-fitted, the ones the offset displaces were 4/4
skinned-with-identity-g2s. But the offset is REQUIRED for others (a stabiliser
shape: 40.26u raw, 5.48u with offset), so it cannot just be removed -- hence a
geometric check rather than a blanket rule.

Both directions are tested. A check that discarded every offset would silently
break the shapes that need it, and that failure looks identical to success in
any test that only covers the harmful case.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from src.nif_convert import shape_body_offset  # noqa: E402


class _Tr:
    def __init__(self, t):
        self.translation = np.asarray(t, dtype=np.float64)


def _shape(verts, translation):
    return SimpleNamespace(verts=np.asarray(verts, dtype=np.float64),
                           transform=_Tr(translation))


def _body():
    """A blob of 'body' verts around the origin-ish torso region."""
    g = np.linspace(-8, 8, 12)
    X, Y, Z = np.meshgrid(g, g * 0.4, np.linspace(88, 104, 12))
    return np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)


def test_no_body_means_unchanged_behaviour():
    """Callers that do not pass a body must be completely unaffected."""
    s = _shape([[0, 0, 96]] * 5, [-40, 0, 0])
    assert np.allclose(shape_body_offset(s), [-40, 0, 0])


def test_harmful_offset_is_discarded():
    """Verts already on the body; the offset would move them 40u away."""
    bv = _body()
    on_body = bv[:40] + np.array([0.0, 1.0, 0.0])   # sitting just off the skin
    s = _shape(on_body, [-40, 0, 0])
    got = shape_body_offset(s, body_verts=bv)
    assert np.allclose(got, 0.0), (
        f"offset that moves the shape off the body must be discarded, got {got}")


def test_needed_offset_is_kept():
    """Verts authored 60u BELOW the body; the offset brings them onto it.

    This is the case a blanket rule would break -- e.g. the stabiliser shape
    measured 40.26u raw and 5.48u with its offset applied.
    """
    bv = _body()
    shifted = bv[:40] - np.array([0.0, 0.0, 60.0])
    s = _shape(shifted, [0, 0, 60])
    got = shape_body_offset(s, body_verts=bv)
    assert np.allclose(got, [0, 0, 60]), (
        f"an offset that brings the shape ONTO the body must be kept, got {got}")


def test_zero_offset_short_circuits():
    bv = _body()
    s = _shape(bv[:40], [0, 0, 0])
    assert np.allclose(shape_body_offset(s, body_verts=bv), 0.0)


def test_a_wash_keeps_existing_behaviour():
    """Within the slack, don't change what the converter already did -- this is
    a correctness guard, not a licence to re-tune every borderline shape."""
    bv = _body()
    s = _shape(bv[:40], [0.0, 0.1, 0.0])
    assert np.allclose(shape_body_offset(s, body_verts=bv), [0.0, 0.1, 0.0])


def test_degenerate_body_is_safe():
    s = _shape([[0, 0, 96]] * 5, [-40, 0, 0])
    for bad in (np.zeros((0, 3)), np.zeros((2, 3)), np.zeros(3)):
        assert np.allclose(shape_body_offset(s, body_verts=bad), [-40, 0, 0]), (
            "a degenerate body must leave the offset alone, not silently zero it")


def test_missing_transform_is_zero():
    s = SimpleNamespace(verts=np.zeros((5, 3)), transform=None)
    assert np.allclose(shape_body_offset(s, body_verts=_body()), 0.0)


def test_unreadable_verts_do_not_raise():
    """A sanity check must never be the thing that fails a conversion."""
    s = SimpleNamespace(verts=None, transform=_Tr([-40, 0, 0]))
    got = shape_body_offset(s, body_verts=_body())
    assert np.allclose(got, [-40, 0, 0])
