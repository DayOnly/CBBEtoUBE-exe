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

"""A part on both legs is never shifted as a whole (#part-pair-bilateral).

`#part-pair-align` moves two adjacent parts bodily toward their joint MEAN row.
For a buckle on a belt that is the right operation. For a pair of trousers it is
not: the trousers' mean averages two legs that swing in opposite directions, so
one additive offset lands on both. Measured on a leather suit: a thigh-strap
buckle on the RIGHT leg put R Thigh 0.018 on every LEFT-leg vertex of the
trousers, and a sprint stride then pulled the forward knee's cloth through the
skin.

The fixture below is chosen so the OLD rule provably fires -- the guard-off test
is the positive control, without which the guard-on test would pass vacuously.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402
from src import nif_convert_weights as ncw  # noqa: E402
from tests import _converter_sources as _cs  # noqa: E402

PELV = "NPC Pelvis [Pelv]"
LTH = "NPC L Thigh [LThg]"
RTH = "NPC R Thigh [RThg]"
_MAX_INFLUENCES = 4


class FakeShape:
    """Duck-typed pynifly shape with the real write semantics: setShapeWeights
    MERGES, and the native row holds four influences (the smallest is dropped)."""

    def __init__(self, name, verts, rows, tris=()):
        self.name = name
        self.verts = [tuple(map(float, v)) for v in verts]
        self.tris = list(tris)
        self._rows = [dict(r) for r in rows]
        self.has_global_to_skin = False
        self.global_to_skin = None
        self._stb = {b: object() for b in self.bone_names}

    @property
    def bone_names(self):
        out = []
        for r in self._rows:
            for b in r:
                if b not in out:
                    out.append(b)
        return sorted(out)

    @property
    def bone_weights(self):
        out = {}
        for i, r in enumerate(self._rows):
            for b, w in r.items():
                out.setdefault(b, []).append((i, w))
        return out

    def get_shape_skin_to_bone(self, bn):
        return self._stb.get(bn)

    def set_skin_to_bone_xform(self, bn, st):
        self._stb[bn] = st

    def setShapeWeights(self, bn, pairs):
        for i, w in pairs:
            i = int(i)
            if float(w) <= 1e-4:
                self._rows[i].pop(bn, None)
                continue
            self._rows[i][bn] = float(w)
            if len(self._rows[i]) > _MAX_INFLUENCES:
                drop = min(self._rows[i], key=lambda b: (self._rows[i][b], b))
                self._rows[i].pop(drop)

    def row(self, i):
        return dict(self._rows[i])


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


# One WELDED part spanning both legs: a left strip, a pelvis bridge across the
# crotch, a right strip, joined by a triangle strip along the path. 29 verts, so
# `_RIGID_PART_MIN_VERTS` (8) cannot drop it as a sliver.
_LEFT = [(-5.0, 0.0, 40.0 + i) for i in range(10)]
_BRIDGE = [(-4.0 + i, 0.0, 50.0) for i in range(9)]
_RIGHT = [(5.0, 0.0, 49.0 - i) for i in range(10)]
_PATH = _LEFT + _BRIDGE + _RIGHT
_N_LEFT = len(_LEFT)
_TROUSER_TRIS = [(i, i + 1, i + 2) for i in range(len(_PATH) - 2)]
# A ten-vert buckle 0.5u off the RIGHT leg: adjacent (<= `_PART_PAIR_NEAR`, 1u)
# and never coincident (> `_COINCIDENT_SKIN_TOL`, 0.15u) with the trousers.
_BUCKLE = [(5.5, 0.0, 44.0 + 0.1 * i) for i in range(10)]
_BUCKLE_TRIS = [(i, i + 1, i + 2) for i in range(len(_BUCKLE) - 2)]
# The pass returns early unless two shapes share a coincident vertex somewhere,
# so both shapes carry one far from everything else, with agreeing rows.
_FAR = (500.0, 500.0, 500.0)


def _trouser_rows():
    return ([{LTH: 1.0}] * len(_LEFT) + [{PELV: 1.0}] * len(_BRIDGE)
            + [{RTH: 1.0}] * len(_RIGHT) + [{PELV: 1.0}])


def _trousers(rows):
    return FakeShape("Trousers", _PATH + [_FAR], rows, _TROUSER_TRIS)


def _buckle(row):
    return FakeShape("Buckle", _BUCKLE + [_FAR], [dict(row)] * len(_BUCKLE) + [{PELV: 1.0}],
                     _BUCKLE_TRIS)


def _run(dst_shapes, src_shapes, guard):
    """Drive the real pass over in-memory NIFs, undoing every fake on the way out."""
    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return (_FakeNif(dst_shapes) if str(filepath) == "dst.nif"
                    else _FakeNif(src_shapes))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn)
        mp.setattr(nc, "_nif_has_fx_shape", lambda nf: False)
        mp.setattr(nc, "PART_PAIR_BILATERAL_GUARD", guard)
        _cs.patch(mp, "_hdt_collider_shape_names", lambda p, nif=None: set())
        _cs.patch(mp, "_hdt_softbody_shape_names", lambda p, nif=None: set())
        mp.setattr(nc, "_hide_virtual_body", lambda nf: False)
        _cs.patch(mp, "atomic_nif_save", lambda nf, p: None)
        nc._match_coincident_cross_shape_skin("dst.nif", src_nif_path="src.nif")


def _left_leg_rthigh(trousers):
    return max(trousers.row(i).get(RTH, 0.0) for i in range(_N_LEFT))


# --------------------------------------------------------------------------
# The defect and the guard
# --------------------------------------------------------------------------

def test_OLD_rule_puts_the_right_thigh_on_the_left_leg():
    """POSITIVE CONTROL. With the guard off the whole-part shift fires on this
    fixture and the far leg picks up the buckle's bone -- the measured defect.
    If this ever reads 0, the fixture no longer exercises part-pair at all."""
    dst = [_trousers(_trouser_rows()), _buckle({RTH: 1.0})]
    src = [_trousers(_trouser_rows()), _buckle({RTH: 0.6, PELV: 0.4})]
    _run(dst, src, guard=False)
    assert _left_leg_rthigh(dst[0]) > 0.01


def test_a_part_on_both_legs_is_never_shifted_as_a_whole():
    """The same fixture with the guard on: the trousers keep every row, and the
    left leg carries no right-thigh weight at all."""
    dst = [_trousers(_trouser_rows()), _buckle({RTH: 1.0})]
    src = [_trousers(_trouser_rows()), _buckle({RTH: 0.6, PELV: 0.4})]
    _run(dst, src, guard=True)
    assert _left_leg_rthigh(dst[0]) == 0.0
    for i, want in enumerate(_trouser_rows()[:len(_PATH)]):
        assert dst[0].row(i) == pytest.approx(want)


def test_the_one_sided_partner_still_moves_toward_the_rows_it_touches():
    """The guard must not simply switch the pair off. A buckle that disagrees with
    the trousers where they touch is still pulled toward THOSE rows -- the right
    leg's -- while the trousers stay put. The buckle carries a little R Thigh so
    the bone is in its palette (the pass never writes outside it). Arithmetic:
    local L1 1.8 against the author's 0.8, s = 0.5 * (1 - 0.9 / 1.8) = 0.25, so
    R Thigh 0.1 + 0.25 * 0.9 = 0.325."""
    dst = [_trousers(_trouser_rows()), _buckle({PELV: 0.9, RTH: 0.1})]
    src = [_trousers(_trouser_rows()), _buckle({RTH: 0.6, PELV: 0.4})]
    _run(dst, src, guard=True)
    for i in range(len(_BUCKLE)):
        assert dst[1].row(i).get(RTH, 0.0) == pytest.approx(0.325, abs=1e-6)
    for i, want in enumerate(_trouser_rows()[:len(_PATH)]):
        assert dst[0].row(i) == pytest.approx(want)


def test_two_two_sided_parts_are_not_paired():
    """Neither of two two-sided parts has a mean worth moving toward. An
    overskirt layer 0.5u outside the trousers, on both legs, whose right leg we
    skin half to the pelvis where the author did not: the old rule pulls BOTH
    parts bodily toward their joint mean (the trousers' left leg picks up
    pelvis), and treating either as the 'one-sided' partner moves a two-sided
    part as a whole all the same. With the guard, neither part changes a row."""
    over_src = _trouser_rows()
    over_dst = ([{LTH: 1.0}] * len(_LEFT) + [{PELV: 1.0}] * len(_BRIDGE)
                + [{RTH: 0.5, PELV: 0.5}] * len(_RIGHT) + [{PELV: 1.0}])

    def over(rows):
        return FakeShape("Overskirt", [(x, 0.5, z) for (x, _y, z) in _PATH] + [_FAR],
                         rows, _TROUSER_TRIS)

    # positive control: without the guard the trousers' left leg moves
    dst = [_trousers(_trouser_rows()), over(over_dst)]
    _run(dst, [_trousers(_trouser_rows()), over(over_src)], guard=False)
    assert any(dst[0].row(i) != pytest.approx({LTH: 1.0}) for i in range(_N_LEFT))
    # the guard: nothing on either part moves
    dst = [_trousers(_trouser_rows()), over(over_dst)]
    _run(dst, [_trousers(_trouser_rows()), over(over_src)], guard=True)
    for i, want in enumerate(_trouser_rows()[:len(_PATH)]):
        assert dst[0].row(i) == pytest.approx(want)
    for i, want in enumerate(over_dst[:len(_PATH)]):
        assert dst[1].row(i) == pytest.approx(want)


# --------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name, side", [
    ("NPC L Thigh [LThg]", "L"), ("NPC R Calf [RClf]", "R"),
    ("L Breast01", "L"), ("NPC R UpperArm [RUar]", "R"),
    ("NPC Pelvis [Pelv]", None), ("NPC LB Anus2", None),
    ("NPC Spine1 [Spn1]", None), ("", None)])
def test_bone_side(name, side):
    assert ncw._bone_side(name) == side


def test_trousers_are_two_sided_and_a_one_leg_buckle_is_not():
    rows = _trouser_rows()
    assert ncw._part_is_bilateral(rows, range(len(_PATH)), 0.10) is True
    buckle = [{RTH: 1.0}] * 10
    assert ncw._part_is_bilateral(buckle, range(10), 0.10) is False
    belt = [{PELV: 1.0}] * 10
    assert ncw._part_is_bilateral(belt, range(10), 0.10) is False


def test_the_share_threshold_is_a_real_boundary():
    """29 verts at 0.10 need ceil(2.9) = 3 on each side: 3 left passes, 2 fails.
    Literal counts, not ones derived from the knob under test."""
    def rows(n_left):
        return [{LTH: 1.0}] * n_left + [{RTH: 1.0}] * 10 + [{PELV: 1.0}] * (19 - n_left)
    assert ncw._part_is_bilateral(rows(3), range(29), 0.10) is True
    assert ncw._part_is_bilateral(rows(2), range(29), 0.10) is False


def test_a_vertex_counts_toward_a_side_only_above_half():
    """0.5 exactly is not 'mostly left': a seam vert split evenly between the two
    thighs belongs to neither side."""
    split = [{LTH: 0.5, RTH: 0.5}] * 20
    assert ncw._part_is_bilateral(split, range(20), 0.10) is False


def test_exactly_half_on_a_side_does_not_make_the_part_two_sided():
    """The boundary on EACH side, where `>` and `>=` disagree: one side is held
    fully, the other only at exactly 0.5 (the rest on the pelvis). Not two-sided
    either way round -- a `>= 0.5` on either side would call it two-sided."""
    half_left = [{RTH: 1.0}] * 10 + [{LTH: 0.5, PELV: 0.5}] * 10 + [{PELV: 1.0}] * 9
    half_right = [{LTH: 1.0}] * 10 + [{RTH: 0.5, PELV: 0.5}] * 10 + [{PELV: 1.0}] * 9
    assert ncw._part_is_bilateral(half_left, range(29), 0.10) is False
    assert ncw._part_is_bilateral(half_right, range(29), 0.10) is False


# --------------------------------------------------------------------------
# Flag and knobs
# --------------------------------------------------------------------------

def test_flag_defaults_ON_and_the_kill_switch_is_honoured(monkeypatch):
    assert nc.PART_PAIR_BILATERAL_GUARD is True
    monkeypatch.setenv("CBBE2UBE_NO_PART_PAIR_BILATERAL_GUARD", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.PART_PAIR_BILATERAL_GUARD is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_PART_PAIR_BILATERAL_GUARD", raising=False)
        importlib.reload(nc)


def test_knob_defaults():
    assert nc._PART_PAIR_BILATERAL_FRAC == 0.10
    assert nc._PART_PAIR_LOCAL == 2.0
