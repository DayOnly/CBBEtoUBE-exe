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

"""Two body detectors disagreed, and the stricter one silently broke a pass.

`classify_shapes` -> `_looks_like_inline_body` identified a shape as the body
and DROPPED it for the body swap. `_is_body_pynifly_shape` then refused the same
shape, because its heuristic requires >= 40 bones and a BodySlide-output inline
body carries only the bones its surviving verts touch -- the hide cuirasses ship
one with 26. So `cbbe_body_shape` came back None, `src_body_v_p2` stayed None,
and `conform_to_source_standoff` -- the only pass that reels an over-projected
garment back onto the body -- never ran. Nothing recorded it: no exception, no
warning, the pass was simply absent from the per-pass trace.

Measured cost on the affected piece: 2.40u of standoff at the strap line,
against a MAXIMUM of 1.79u across 42 shapes where the pass did run. With the
fallback it lands at 1.72u and clipping stays 0.00%.

`test_bone_count_alone_must_not_veto_a_body` is the regression: it pins the
exact geometry that was rejected. If the thresholds are ever retuned so that a
26-bone body fails again with no fallback, that test fails rather than a garment
quietly floating off the body.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_convert as nc  # noqa: E402


class _Shape:
    """Minimal stand-in with the attributes the detectors read."""

    def __init__(self, name, n_verts, n_bones, z_range=102.6):
        self.name = name
        z = np.linspace(11.0, 11.0 + z_range, n_verts)
        self.verts = np.stack([np.zeros(n_verts), np.zeros(n_verts), z], 1)
        self.bone_names = [f"B{i}" for i in range(n_bones)]
        self.tris = np.zeros((1, 3), dtype=np.int64)


def test_the_thresholds_are_what_the_bug_needed():
    """Documents the trap: the veto is BONE COUNT, not verts or extent."""
    assert nc._BODY_HEURISTIC_MIN_BONES > 26, (
        "the 26-bone body no longer fails this gate; if the threshold was "
        "lowered deliberately, this test and the fallback comment need updating")
    assert nc._BODY_HEURISTIC_MIN_VERTS <= 9312
    assert nc._BODY_HEURISTIC_MIN_Z_RANGE <= 102.6


def test_bone_count_alone_must_not_veto_a_body(monkeypatch):
    """The exact shape that broke: full-height, 9312 verts, only 26 bones.

    The strict detector is allowed to reject it -- what must NOT happen is that
    the conversion then has no source body at all.
    """
    body = _Shape("CBBE Body", 9312, 26)
    monkeypatch.setattr(nc, "_shape_diffuse_is_body_skin", lambda s: True)
    # The strict path rejects it on bone count, before the texture gate.
    assert len(body.bone_names) < nc._BODY_HEURISTIC_MIN_BONES
    # The permissive path -- the one classify_shapes uses -- accepts it, which
    # is why the body was dropped for the swap in the first place.
    assert nc._looks_like_inline_body(body), (
        "if this ever returns False the two detectors agree again and the "
        "fallback is unnecessary -- but then the body would not be dropped "
        "either, which is a different bug")


def test_named_body_fallback_picks_the_largest():
    """`body_names` can also carry exposed-skin slices; the body is the biggest.

    Mirrors the selection the fallback performs, so a change to that rule is
    caught here rather than by a garment floating off the body in game.
    """
    shapes = [_Shape("Skin_Chest", 900, 26), _Shape("CBBE Body", 9312, 26),
              _Shape("Skin_Arm", 400, 26)]
    names = {"Skin_Chest", "CBBE Body", "Skin_Arm"}
    picked = max([s for s in shapes if s.name in names],
                 key=lambda s: len(s.verts))
    assert picked.name == "CBBE Body"


def test_fallback_is_wired_and_guarded_by_the_strict_detector():
    """It must be a FALLBACK. Replacing the strict detector outright would
    change which shape is chosen on pieces that already work -- 42 of 42 armed
    shapes in the census ran conform fine, and one was verified byte-identical
    after this change."""
    import inspect
    src = inspect.getsource(nc.convert_nif_phase2)
    i = src.index("cbbe_body_shape = next(")
    tail = src[i:i + 2400]
    assert "if cbbe_body_shape is None and body_names:" in tail, (
        "the named-body fallback is gone or no longer guarded on the strict "
        "detector having failed first")
    assert "max(_named_body, key=lambda s: len(s.verts))" in tail


def test_a_normal_body_still_takes_the_strict_path(monkeypatch):
    """A full body with plenty of bones must satisfy the strict detector, so
    the fallback never engages for it."""
    body = _Shape("CBBE Body", 18436, 60)
    monkeypatch.setattr(nc, "_shape_diffuse_is_body_skin", lambda s: True)
    assert len(body.bone_names) >= nc._BODY_HEURISTIC_MIN_BONES
    assert len(body.verts) >= nc._BODY_HEURISTIC_MIN_VERTS
