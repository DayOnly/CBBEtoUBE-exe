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

"""A part deforms the way its author made it deform (#author-deviation-skin).

Reported in game after the cross-shape, rigid-part and part-pair fixes had all
shipped: on the metal buckles of the stomach belts, "some verts shift based on
movement while others don't, making a distortion during movement". The buckle
agrees with its belt where they touch and rides the right average bone -- and is
still torn apart from the inside, because nothing bounded how far OUR rows
diverge WITHIN a part the author skinned semi-deformably.

`#rigid-part-cap` aimed at the part MEAN, i.e. at zero internal variation, and
was rejected on measurement: it fixed the buckles by freezing the fabric. The
operation under test aims at the AUTHOR'S OWN deviation instead

    row_i := our_part_mean + (author_i - author_part_mean)

which makes a correctly-skinned part a FIXED POINT rather than a casualty. The
two properties that matter are exact, so they are asserted exactly:

  * the part's resulting spread equals the AUTHOR's spread, not zero; and
  * the part's MEAN row is unchanged, so the UBE retarget, the fit chain and
    `#part-pair-align` all survive the rebuild.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402

PELV = "NPC Pelvis [Pelv]"
SPINE = "NPC Spine [Spn0]"
BELLY = "NPC Belly"
LTHIGH = "NPC L Thigh [LThg]"
RTHIGH = "NPC R Thigh [RThg]"
_MAX_INFLUENCES = 4


class FakeShape:
    """Duck-typed pynifly shape with the real write semantics.

    `setShapeWeights` MERGES rather than replaces, and the native buffer holds
    four influences and drops the smallest on overflow
    ([[project_setshapeweights_update_semantics]]). Both are reproduced because
    a pass that ignores either ships rows that do not sum to 1.
    """

    def __init__(self, name, verts, rows, tris=()):
        self.name = name
        self.verts = [tuple(map(float, v)) for v in verts]
        self.tris = list(tris)
        self._rows = [dict(r) for r in rows]
        self.has_global_to_skin = False
        self.global_to_skin = None
        self._stb = {b: object() for b in self.bone_names}
        self.writes = []

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
        self.writes.append((bn, list(pairs)))
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


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------
# A twelve-vertex strip is ONE welded part. `_RIGID_PART_MIN_VERTS` is 8, so a
# shorter one would be skipped as a sliver and every assertion below would pass
# vacuously.
_N = 12
_STRIP = [(0.3 * i, 0.0, 75.0) for i in range(_N)]
_TRIS = [(i, i + 1, i + 2) for i in range(_N - 2)]
# Far from the strip, and SHARED with the second shape. The pass returns early
# when no two shapes have a coincident vertex at all, so without this pair
# nothing under test would ever run.
_FAR = (500.0, 500.0, 500.0)


def _buckle(rows):
    """The strip plus one isolated vertex. The isolated one is its own
    component of size 1 and is never treated as a part."""
    return FakeShape("Buckle", _STRIP + [_FAR], list(rows) + [{PELV: 1.0}],
                     _TRIS)


def _belt(row):
    """Three verts and NO triangles, so it contributes no parts of its own and
    cannot mask what the buckle does. It exists to supply the coincident pair --
    with a row that DISAGREES, so the edge is cut and no cluster forms."""
    return FakeShape("Belt", [_FAR, (501.0, 500.0, 500.0),
                              (502.0, 500.0, 500.0)], [dict(row)] * 3)


def _author_rows():
    """Spread 0.44 -- deliberately ABOVE `_RIGID_PART_GATE` (0.15), so this is a
    part the author meant to deform and the rigid branch correctly walks away
    from it. That is precisely the case `#rigid-part-cap` could not serve."""
    return [{SPINE: 0.80 - 0.02 * i, PELV: 0.20 + 0.02 * i} for i in range(_N)]


def _torn_rows():
    """What the conversion produced: half the buckle nailed to the spine, half
    to the pelvis, spread 2.0 -- the maximum possible. This is "some verts shift
    based on movement while others don't"."""
    return [({SPINE: 1.0} if i < _N // 2 else {PELV: 1.0}) for i in range(_N)]


def _spread(rows):
    w = 0.0
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            w = max(w, sum(abs(a.get(k, 0.0) - b.get(k, 0.0))
                           for k in set(a) | set(b)))
    return w


def _mean(rows):
    m = {}
    for r in rows:
        for b, w in r.items():
            m[b] = m.get(b, 0.0) + w / len(rows)
    return m


def _run(dst_shapes, src_shapes):
    """Drive the pass over in-memory NIFs, UNDOING the fakes on the way out --
    a bare `pytest.MonkeyPatch()` leaks `nc._pynifly` into every later test."""
    saved = []

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return (_FakeNif(dst_shapes) if str(filepath) == "dst.nif"
                    else _FakeNif(src_shapes))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn)
        mp.setattr(nc, "_nif_has_fx_shape", lambda nf: False)
        mp.setattr(nc, "_hdt_collider_shape_names", lambda p, nif=None: set())
        mp.setattr(nc, "_hdt_softbody_shape_names", lambda p, nif=None: set())
        mp.setattr(nc, "_hide_virtual_body", lambda nf: False)
        mp.setattr(nc, "atomic_nif_save", lambda nf, p: saved.append(p))
        nc._match_coincident_cross_shape_skin("dst.nif", src_nif_path="src.nif")
    return saved


def _out(sh):
    return [sh.row(i) for i in range(_N)]


# --------------------------------------------------------------------------
# The defect itself
# --------------------------------------------------------------------------

def test_the_part_ends_up_with_the_AUTHORS_spread_not_with_none():
    """The whole difference from the rejected cap. Aiming at the part mean would
    land this at 0.0 and would take the fabric with it; aiming at the author's
    deviation lands it exactly on 0.44."""
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    got, want = _spread(_out(dst)), _spread(_author_rows())
    assert abs(got - want) < 1e-9, f"spread {got} should equal author {want}"
    assert got > 0.4, "a part the author meant to deform must still deform"


def test_the_parts_MEAN_row_survives_the_rebuild():
    """Our mean carries the UBE retarget, the fit chain and `#part-pair-align`;
    only the variation ABOUT it is the author's. If the mean moved, this pass
    would be silently undoing whichever pass set it."""
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    before, after = _mean(_torn_rows()), _mean(_out(dst))
    for b in set(before) | set(after):
        assert abs(before.get(b, 0.0) - after.get(b, 0.0)) < 1e-9, b


def test_every_rebuilt_row_is_a_legal_skin_row():
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    for i, r in enumerate(_out(dst)):
        assert abs(sum(r.values()) - 1.0) < 1e-6, f"vert {i} sums {sum(r.values())}"
        assert len(r) <= _MAX_INFLUENCES, f"vert {i} has {len(r)} influences"
        assert all(w > 0.0 for w in r.values()), f"vert {i} has a dead influence"


def test_a_bone_the_pass_wrote_is_never_left_empty():
    """A bone present on the shape with an empty weight list drops out of the
    regenerated skin partition palette -> equip CTD. #zeroweight-bone-desync"""
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    live = dst.bone_weights
    for bn, _prs in dst.writes:
        assert live.get(bn), f"{bn} was written and shipped with no weight"


# --------------------------------------------------------------------------
# What it must NOT do -- these are the rejected cap's failure modes
# --------------------------------------------------------------------------

def test_a_part_we_already_skin_MORE_smoothly_than_the_author_is_untouched():
    """It may only remove variation the conversion invented. Measured on the
    reported piece, sub-1u studs sit at 0.030 against the author's 0.098 -- if
    the transplant fired there it would ADD movement nobody asked for."""
    smooth = [{SPINE: 0.70 - 0.002 * i, PELV: 0.30 + 0.002 * i}
              for i in range(_N)]
    dst = _buckle(smooth)
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    assert _out(dst) == [dict(r) for r in smooth]


def test_a_part_within_the_margin_of_its_author_is_left_alone():
    """Rewriting for a difference of noise churns weights for nothing.

    Also the regression guard for a trap this pass fell into once: the author
    spread cached in `_soft_parts` STOPS as soon as it clears the rigid gate, so
    it is a truncated lower bound (0.16 here against a true 0.44). Gating on it
    fired the transplant on parts already within the author's margin. The pass
    must recompute the author's spread in full.
    """
    near = [{SPINE: 0.82 - 0.024 * i, PELV: 0.18 + 0.024 * i}
            for i in range(_N)]
    assert 0.0 < _spread(near) - _spread(_author_rows()) < nc._AUTHOR_DEV_MARGIN
    dst = _buckle(near)
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    assert _out(dst) == [dict(r) for r in near]


def test_the_pass_can_only_ever_REDUCE_a_parts_spread():
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})],
         [_buckle(_author_rows()), _belt({SPINE: 1.0})])
    assert _spread(_out(dst)) <= _spread(_torn_rows()) + 1e-9


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def test_a_part_whose_author_palette_we_barely_share_is_left_alone():
    """The author's deviation is expressed in the author's bones. With almost
    none of them in common the transplant would be rebuilding the part out of a
    fragment, which is worse than leaving the measured defect in place."""
    alien = [{LTHIGH: 0.80 - 0.02 * i, RTHIGH: 0.20 + 0.02 * i}
             for i in range(_N)]
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})], [_buckle(alien), _belt({SPINE: 1.0})])
    assert _out(dst) == _torn_rows()


def test_a_bone_outside_the_shapes_palette_is_never_written():
    """Weighting a vertex to a bone the shape cannot bind skins it to the origin
    ([[project_shape_weight_accessor]]). The author here leans on a bone the
    destination shape does not carry at all."""
    src_rows = [{SPINE: 0.60 - 0.02 * i, PELV: 0.20 + 0.02 * i, BELLY: 0.20}
                for i in range(_N)]
    dst = _buckle(_torn_rows())
    _run([dst, _belt({PELV: 1.0})], [_buckle(src_rows), _belt({SPINE: 1.0})])
    pal = {PELV, SPINE}
    for i, r in enumerate(_out(dst)):
        assert set(r) <= pal, f"vert {i} was given {set(r) - pal}"


def test_the_cross_shape_agreement_still_gets_the_LAST_word():
    """Ordering guard. The cross-shape fix is the in-game-verified one this is
    built on top of; a transplant applied AFTER cluster unification would pull
    touching verts apart again and undo it."""
    shared = (0.0, 0.0, 75.0)          # vertex 0 of the buckle
    dst = _buckle(_torn_rows())
    other = FakeShape("Strap", [shared, (600.0, 0.0, 0.0), (601.0, 0.0, 0.0)],
                      [{PELV: 0.9, SPINE: 0.1}, {PELV: 1.0}, {PELV: 1.0}])
    agree = {SPINE: 0.80, PELV: 0.20}   # what the author gave the buckle there
    src_other = FakeShape("Strap", [shared, (600.0, 0.0, 0.0),
                                    (601.0, 0.0, 0.0)],
                          [dict(agree), {PELV: 1.0}, {PELV: 1.0}])
    _run([dst, other], [_buckle(_author_rows()), src_other])
    assert dst.row(0) == other.row(0), (
        "coincident verts must still end up identically skinned")


# --------------------------------------------------------------------------
# Reachability -- a pass nobody can switch off is as bad as one nobody can
# switch on ([[feedback_deployed_build_runs_at_defaults]])
# --------------------------------------------------------------------------

def test_the_flag_defaults_ON_and_the_kill_switch_is_honoured(monkeypatch):
    import importlib
    monkeypatch.delenv("CBBE2UBE_NO_AUTHOR_DEVIATION_SKIN", raising=False)
    assert importlib.reload(nc).AUTHOR_DEVIATION_SKIN is True
    monkeypatch.setenv("CBBE2UBE_NO_AUTHOR_DEVIATION_SKIN", "1")
    assert importlib.reload(nc).AUTHOR_DEVIATION_SKIN is False
    monkeypatch.delenv("CBBE2UBE_NO_AUTHOR_DEVIATION_SKIN", raising=False)
    importlib.reload(nc)


def test_it_is_reachable_from_a_real_run():
    from src import gui_settings as gs
    s = next(x for x in gs.SETTINGS if x.key == "author_deviation_skin")
    assert s.default is True and s.invert is True
    assert s.env == "CBBE2UBE_NO_AUTHOR_DEVIATION_SKIN"


def test_the_gates_are_conservative():
    assert 0.0 < nc._AUTHOR_DEV_MARGIN <= 0.5
    assert 0.0 < nc._AUTHOR_DEV_MIN_PALETTE <= 1.0
