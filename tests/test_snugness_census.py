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

"""The snugness census's FRAME CHOOSER -- the part that produced void numbers.

Two different frame mistakes made this measurement worthless on 2026-08-23
before it ever produced a usable number, and both were silent:

  1. applying one rule to source AND output ("always transform") reported
     `authored 0.00` for every shape and a garment 1973u from the body;
  2. excluding shapes whose two candidate frames AGREE -- which is most shapes,
     because most transforms are identity -- cut a 20-shape sample to 3.

Neither raised anything. They produced tabulated, confident nonsense. So the
chooser gets pinned here rather than trusted, including the case that a naive
"the margin must be decisive" guard gets wrong.
"""
import re
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts" / "analysis"))

# The REAL shared rule, not a stand-in. `pick_frame` here now delegates to
# `standoff_audit.pick_frame`, so these tests judge the composed behaviour --
# this file's wrapper plus the rule it calls. Substituting a fake would let the
# wrapper pass while the rule it delegates to was broken.
import standoff_audit as sa  # noqa: E402


_SRC = (_REPO / "scripts" / "analysis" / "snugness_census.py").read_text(
    encoding="utf-8")


def _load():
    """Namespace with the census's CONSTANTS and `pick_frame`, nothing else.

    The module resolves an MO2 layout and parses argv at import, so importing it
    inside pytest would abort or eat pytest's own arguments. Slice out the two
    pieces under test instead -- and assert we found them, because an empty
    namespace would make every test below a KeyError rather than a pass, and a
    silently-renamed constant should fail loudly here.
    """
    ns = {"np": np, "sa": sa}
    consts = re.findall(r"^([A-Z][A-Z_]*) = ([0-9.]+)\s", _SRC, re.M)
    assert len(consts) >= 4, f"expected the census constants, found {consts}"
    for name, val in consts:
        ns[name] = float(val) if "." in val else int(val)
    start = _SRC.index("def pick_frame")
    # Slice to whatever function FOLLOWS it -- naming the next one made an
    # empty slice the day the file was reordered, and an empty slice is a
    # KeyError in every test rather than a visible failure here.
    nxt = _SRC.index("\ndef ", start + 1)
    body = _SRC[start:nxt]
    exec(compile(body, "snugness_census.py<pick_frame>", "exec"), ns)
    assert "pick_frame" in ns
    return ns


HEAD = _load()


class _Shape:
    """Minimal stand-in: `pick_frame` only ever reads `.verts`."""

    def __init__(self, verts):
        self.verts = np.asarray(verts, dtype=np.float64)


@pytest.fixture()
def chooser():
    """A FRESH namespace per test -- `_install` rebinds `nc` inside it."""
    return _load()


def _tree(points):
    from scipy.spatial import cKDTree
    return cKDTree(np.asarray(points, dtype=np.float64))


def _install(ns, offset):
    """Make `_verts_skin_to_world` add `offset`; identity when offset is 0."""
    class _NC:
        @staticmethod
        def _shape_global_to_skin(_s):
            return offset

        @staticmethod
        def _verts_skin_to_world(v, off):
            return np.asarray(v, dtype=np.float64) + np.asarray(off, float)

    ns["nc"] = _NC


def test_agreeing_frames_are_kept_not_excluded(chooser):
    """THE BUG THAT CUT 20 SHAPES TO 3.

    An identity transform makes both candidate frames the SAME points. That is
    agreement, not ambiguity -- excluding it throws away most of the pack.
    """
    _install(chooser, np.zeros(3))
    tree = _tree([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    verts, which, margin = chooser["pick_frame"](
        _Shape([[0.0, 0.0, 2.0], [1.0, 0.0, 2.0]]), tree)
    assert which == "agree"
    assert margin == float("inf"), "an agreeing shape must never be excluded"
    assert np.allclose(verts, [[0.0, 0.0, 2.0], [1.0, 0.0, 2.0]])


def test_a_material_disagreement_picks_the_frame_nearer_the_body(chooser):
    """When the frames really differ, the BODY decides -- not a hard-coded rule.

    Here the raw verts sit far away and the transform brings them home, so the
    chooser must return `world`. The mirror case is covered below; a rule like
    "source needs the transform, output does not" gets one of the two wrong.
    """
    _install(chooser, np.array([0.0, 0.0, -100.0]))
    tree = _tree([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    _v, which, margin = chooser["pick_frame"](
        _Shape([[0.0, 0.0, 101.0], [1.0, 0.0, 101.0]]), tree)
    assert which == "world"
    assert margin > chooser["AMBIGUOUS"], "a real disagreement must be decisive"


def test_the_mirror_case_picks_raw(chooser):
    """Same chooser, opposite answer -- proving it is evidence and not a rule."""
    _install(chooser, np.array([0.0, 0.0, 100.0]))
    tree = _tree([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    _v, which, margin = chooser["pick_frame"](
        _Shape([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]), tree)
    assert which == "raw"
    assert margin > chooser["AMBIGUOUS"]


def test_an_undecidable_shape_is_excluded_rather_than_guessed(chooser):
    """Frames that differ MATERIALLY but not decisively are a coin flip.

    `AGREE_U` must not swallow this case: the two frames are 2u apart, well past
    agreement, but neither is clearly nearer, so the margin stays under the bar
    and the caller drops the shape and counts it.
    """
    # raw lands 3.0u out, world 1.5u out: 1.5u apart (well past AGREE_U) but
    # only a 2.0x margin, under the 3.0x bar. Neither frame is clearly right.
    _install(chooser, np.array([0.0, 0.0, -1.5]))
    tree = _tree([[0.0, 0.0, 0.0]])
    _v, _which, margin = chooser["pick_frame"](_Shape([[0.0, 0.0, 3.0]]), tree)
    assert 1.0 < margin < chooser["AMBIGUOUS"]
    assert margin < chooser["AMBIGUOUS"], (
        "a coin-flip frame must fail the decisiveness bar so the caller can "
        "exclude and COUNT it")


def test_a_broken_transform_falls_back_to_raw_rather_than_dying(chooser):
    """One unreadable shape must not abort a 1500-file census."""
    class _NC:
        @staticmethod
        def _shape_global_to_skin(_s):
            raise RuntimeError("no skin data")

        @staticmethod
        def _verts_skin_to_world(v, off):  # pragma: no cover - not reached
            raise AssertionError("should not be called")

    chooser["nc"] = _NC
    verts, which, margin = chooser["pick_frame"](
        _Shape([[0.0, 0.0, 1.0]]), _tree([[0.0, 0.0, 0.0]]))
    assert which == "raw" and margin == float("inf")
    assert np.allclose(verts, [[0.0, 0.0, 1.0]])


def test_thresholds_are_ordered_so_neither_swallows_the_other():
    """`AGREE_U` is a DISTANCE and `AMBIGUOUS` is a RATIO.

    They guard different failure modes and swapping them (or setting AGREE_U so
    wide it absorbs real disagreements) silently restores one of the two bugs
    this file exists for.
    """
    assert 0.0 < HEAD["AGREE_U"] < 1.0
    assert HEAD["AMBIGUOUS"] > 1.0
    assert HEAD["MIN_SCORED"] >= 1, "a census with no floor cannot report 0/0"
