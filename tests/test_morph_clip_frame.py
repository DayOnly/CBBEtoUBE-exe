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

"""`morph_clip_test._aligned` -- the guard against a FALSE ZERO.

This harness is the only one that can judge clearance under a body morph, so a
silent failure here does not read as broken: it reads as "this garment does not
clip". Two ways it produced exactly that, both real:

  * it aborted on every copy-path piece ("no injected BaseShape"), leaving ~78%
    of the pack unmeasured -- and an unmeasured pack looks like a clean one;
  * once taught to use the UBE template body instead, it re-transformed output
    verts that were ALREADY in world. A cuirass at z 11..119 became z -320..292,
    landed nowhere near the body, and reported 0.00% band coverage with 0.000%
    clip. On a CUIRASS, over the BUST.

`_aligned` exists to make the second impossible. The test that matters is the
one where the naive call is wrong, so that case is asserted directly.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))


@pytest.fixture(scope="module")
def aligned():
    """`_aligned` and `_world`, without importing the module's CLI/layout."""
    src = (_REPO / "scripts" / "analysis" / "morph_clip_test.py").read_text(
        encoding="utf-8")
    start = src.index("def _world(shape):")
    end = src.index("\ndef ", src.index("def _aligned"))
    ns = {"np": np}

    class _NC:
        @staticmethod
        def _shape_global_to_skin(s):
            return getattr(s, "xform", np.zeros(3))

        @staticmethod
        def _verts_skin_to_world(v, off):
            return np.asarray(v, np.float64) + np.asarray(off, np.float64)

    ns["nc"] = _NC
    exec(compile(src[start:end], "morph_clip_test.py<frag>", "exec"), ns)
    assert "_aligned" in ns, "the frame chooser was renamed or removed"
    return ns


class _Shape:
    def __init__(self, verts, xform):
        self.verts = np.asarray(verts, np.float64)
        self.xform = np.asarray(xform, np.float64)


def _body(lo=11.0, hi=114.0, n=40):
    z = np.linspace(lo, hi, n)
    return np.column_stack([np.zeros(n), np.zeros(n), z])


def test_a_world_stored_shape_with_a_stale_transform_is_left_alone(aligned):
    """THE FALSE-ZERO CASE. Verts already on the body must not be transformed.

    This is the real shape of the bug: the transform is non-identity, so a
    "transform when it is not identity" rule applies it -- and the garment
    leaves the body entirely.
    """
    ref = _body()
    garment = _Shape(np.column_stack([np.zeros(20), np.zeros(20),
                                      np.linspace(90.0, 102.0, 20)]),
                     xform=[0.0, 0.0, -331.0])
    out = aligned["_aligned"](garment, ref)
    assert np.allclose(out, garment.verts), (
        "a shape already sitting on the body was re-transformed off it -- this "
        "is the false zero this function exists to prevent")
    naive = aligned["_world"](garment)
    assert not np.allclose(naive, garment.verts), (
        "fixture is inert: the naive call must be WRONG here or this proves "
        "nothing")


def test_a_skin_stored_shape_is_brought_onto_the_body(aligned):
    """The mirror case -- the transform IS needed, and must be applied."""
    ref = _body()
    garment = _Shape(np.column_stack([np.zeros(20), np.zeros(20),
                                      np.linspace(-6.0, 6.0, 20)]),
                     xform=[0.0, 0.0, 96.0])
    out = aligned["_aligned"](garment, ref)
    assert out[:, 2].min() > 80.0, "the shape was left in its skin frame"
    assert np.allclose(out, aligned["_world"](garment))


def test_an_identity_transform_is_a_no_op(aligned):
    ref = _body()
    v = np.column_stack([np.zeros(8), np.zeros(8), np.linspace(90.0, 100.0, 8)])
    out = aligned["_aligned"](_Shape(v, xform=[0.0, 0.0, 0.0]), ref)
    assert np.allclose(out, v)


def test_a_broken_transform_falls_back_rather_than_aborting_the_run(aligned):
    """One unreadable shape must not kill a sweep over hundreds of pieces."""
    class _Bad:
        verts = np.array([[0.0, 0.0, 95.0]])

        @property
        def xform(self):
            raise RuntimeError("no skin data")

    out = aligned["_aligned"](_Bad(), _body())
    assert np.allclose(out, [[0.0, 0.0, 95.0]])
